/* Self-authored read-only metadata probe, without an Apple SDK.
 * ABI references (not an exact guest-source equivalence claim):
 * https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/dyld_priv.h
 * https://github.com/apple-oss-distributions/dyld/blob/main/include/dlfcn.h
 * No dlopen, target initializer, code dump, or memory write is performed.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned long usize;
typedef long isize;
extern int printf(const char *, ...) __attribute__((format(printf, 1, 2)));
extern int getpid(void);
extern void *dlsym(void *, const char *);

typedef _Bool (*cache_uuid_fn)(u8 *);
typedef const void *(*cache_range_fn)(usize *);
typedef _Bool (*immutable_fn)(const void *, usize);
typedef _Bool (*image_uuid_fn)(const void *, u8 *);
typedef isize (*image_slide_fn)(const void *);
typedef u32 (*image_count_fn)(void);
typedef const void *(*image_header_fn)(u32);

_Static_assert(sizeof(void *) == 8 && sizeof(usize) == 8 && sizeof(isize) == 8, "LP64 ABI");
_Static_assert(sizeof(u32) == 4 && sizeof(u64) == 8, "Integer ABI");

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int parse_uuid(const char *s, u8 out[16])
{
    unsigned n = 0;
    while (n < 37 && s[n]) ++n;
    if (n != 32 && n != 36) return 0;
    unsigned pos = 0;
    for (unsigned i = 0; i < 16; ++i) {
        if (n == 36 && (i == 4 || i == 6 || i == 8 || i == 10))
            if (s[pos++] != '-') return 0;
        int hi = hex_digit(s[pos++]), lo = hex_digit(s[pos++]);
        if (hi < 0 || lo < 0) return 0;
        out[i] = (u8)((hi << 4) | lo);
    }
    return s[pos] == 0;
}

static int parse_offset(const char *s, u64 *out)
{
    if (s[0] != '0' || s[1] != 'x' || !s[2]) return 0;
    u64 value = 0;
    unsigned n = 0;
    for (s += 2; *s; ++s) {
        int v = hex_digit(*s);
        if (++n > 16 || v < 0) return 0;
        value = (value << 4) | (unsigned)v;
    }
    *out = value;
    return 1;
}

static int same_uuid(const u8 a[16], const u8 b[16])
{
    for (unsigned i = 0; i < 16; ++i) if (a[i] != b[i]) return 0;
    return 1;
}

static void uuid_text(const u8 u[16], char out[37])
{
    static const char hex[] = "0123456789abcdef";
    unsigned pos = 0;
    for (unsigned i = 0; i < 16; ++i) {
        if (i == 4 || i == 6 || i == 8 || i == 10) out[pos++] = '-';
        out[pos++] = hex[u[i] >> 4];
        out[pos++] = hex[u[i] & 15];
    }
    out[pos] = 0;
}

static u32 read32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

static int unavailable(const char *reason, int code)
{
    printf("{\"schema\":1,\"probe_version\":1,\"state\":\"unavailable\","
           "\"reason\":\"%s\",\"pid\":%d,\"target_dlopen_performed\":false}\n", reason, getpid());
    return code;
}

static int target_loaded(image_count_fn count, image_header_fn header,
                         image_uuid_fn uuid, const u8 target[16], u32 *total)
{
    *total = count();
    if (*total > 16384) return -1;
    int found = 0;
    for (u32 i = 0; i < *total; ++i) {
        const void *h = header(i);
        u8 id[16];
        if (h && uuid(h, id) && same_uuid(id, target)) ++found;
    }
    return found;
}

int main(int argc, char **argv)
{
    u8 expected_cache[16], expected_image[16], cache_id[16], image_id[16];
    u64 offset;
    if (argc != 4 || !parse_uuid(argv[1], expected_cache) ||
            !parse_uuid(argv[2], expected_image) || !parse_offset(argv[3], &offset))
        return unavailable("expected_cache_uuid_image_uuid_hex_offset", 2);
    /* Darwin RTLD_DEFAULT, resolved only against already loaded libraries. */
    void *scope = (void *)(isize)-2;
    cache_uuid_fn cache_uuid = (cache_uuid_fn)dlsym(scope, "_dyld_get_shared_cache_uuid");
    cache_range_fn cache_range = (cache_range_fn)dlsym(scope, "_dyld_get_shared_cache_range");
    immutable_fn immutable = (immutable_fn)dlsym(scope, "_dyld_is_memory_immutable");
    image_uuid_fn image_uuid = (image_uuid_fn)dlsym(scope, "_dyld_get_image_uuid");
    image_slide_fn image_slide = (image_slide_fn)dlsym(scope, "_dyld_get_image_slide");
    image_count_fn image_count = (image_count_fn)dlsym(scope, "_dyld_image_count");
    image_header_fn image_header = (image_header_fn)dlsym(scope, "_dyld_get_image_header");
    if (!cache_uuid || !cache_range || !immutable || !image_uuid ||
            !image_slide || !image_count || !image_header)
        return unavailable("required_dyld_api_unavailable", 3);
    u32 before, after;
    int loaded_before = target_loaded(image_count, image_header, image_uuid, expected_image, &before);
    if (loaded_before != 0) return unavailable("target_already_loaded_or_invalid_image_count", 4);
    usize size = 0;
    const u8 *base = cache_range(&size);
    if (!base || !cache_uuid(cache_id) || !same_uuid(cache_id, expected_cache))
        return unavailable("current_cache_uuid_mismatch_or_absent", 5);
    if (size < 32 || offset > (u64)size - 32 || offset > ~(u64)0 - (u64)(usize)base)
        return unavailable("image_offset_outside_current_cache", 6);
    const u8 *image = base + offset;
    if (!immutable(image, 32)) return unavailable("image_header_not_immutable", 7);
    if (read32(image) != 0xfeedfacfU || read32(image + 4) != 0x0100000cU ||
            read32(image + 12) != 6)
        return unavailable("not_arm64_macho_dylib", 8);
    u32 ncmds = read32(image + 16), commands = read32(image + 20);
    if (!ncmds || ncmds > 4096 || commands > 65536 || commands < ncmds * 8 ||
            (u64)commands + 32 > (u64)size - offset || !immutable(image, (usize)commands + 32))
        return unavailable("load_commands_outside_immutable_bound", 9);
    u32 at = 32, uuid_offset = 0;
    for (u32 i = 0; i < ncmds; ++i) {
        if (at > commands + 32 - 8) return unavailable("truncated_load_command", 10);
        u32 type = read32(image + at), len = read32(image + at + 4);
        if (len < 8 || (len & 7) || len > commands + 32 - at)
            return unavailable("invalid_load_command_size", 10);
        if (type == 0x1b) {
            if (uuid_offset || len != 24) return unavailable("ambiguous_or_invalid_lc_uuid", 10);
            uuid_offset = at + 8;
        }
        at += len;
    }
    if (at != commands + 32 || !uuid_offset ||
            !same_uuid(image + uuid_offset, expected_image) ||
            !image_uuid(image, image_id) || !same_uuid(image_id, expected_image))
        return unavailable("image_uuid_mismatch_or_malformed_macho", 11);
    isize slide = image_slide(image);
    int loaded_after = target_loaded(image_count, image_header, image_uuid, expected_image, &after);
    usize size_after = 0;
    u8 cache_after[16];
    if (loaded_after != 0 || cache_range(&size_after) != base || size_after != size ||
            !cache_uuid(cache_after) || !same_uuid(cache_after, cache_id))
        return unavailable("mapping_or_loaded_images_changed", 12);
    char cache_text[37], image_text[37];
    uuid_text(cache_id, cache_text); uuid_text(image_id, image_text);
    printf("{\"schema\":1,\"probe_version\":1,\"state\":\"verified\",\"pid\":%d,"
           "\"cache_uuid\":\"%s\",\"image_uuid\":\"%s\",\"cache_base\":\"0x%llx\","
           "\"cache_size\":%llu,\"image_base\":\"0x%llx\",\"image_cache_offset\":\"0x%llx\","
           "\"image_slide\":%lld,\"lc_uuid_payload_image_offset\":%u,"
           "\"images_before\":%u,\"images_after\":%u,\"target_loaded_before\":false,"
           "\"target_loaded_after\":false,\"target_dlopen_performed\":false,"
           "\"scope\":\"Current probe process shared-cache metadata only; not a cross-process runtime guard\"}\n",
           getpid(), cache_text, image_text, (u64)(usize)base, (u64)size,
           (u64)(usize)image, offset, (long long)slide, uuid_offset, before, after);
    return 0;
}

/*
 * Self-authored arm64 macOS probe, compiled without an Apple SDK.
 * Public ABI declarations only; no Apple implementation or headers are copied.
 * ABI references:
 * https://github.com/apple-oss-distributions/xnu/blob/main/bsd/arm/_types.h
 * https://github.com/apple-oss-distributions/Libc/blob/main/include/_time.h
 * https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/mach/mach_time.h
 * https://github.com/apple-oss-distributions/dyld/blob/main/include/dlfcn.h
 * Descriptor/query reference (not an exact binary-version equivalence claim):
 * https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/graphics/cocoa/WebMAudioUtilitiesCocoa.mm
 * Codec order: WebKit 47fa426c169f32f8fc3dedd2c28bd04af887713d,
 * Source/WebKit/GPUProcess/GPUConnectionToWebProcess.cpp:370-385.
 * VT return ABI: Apple VTIsHardwareDecodeSupported DocC Objective-C declaration
 * returns Boolean; apple-oss-distributions/CF/CFBase.h defines unsigned char.
 * AV1/Vorbis FourCCs appear in that WebKit revision; VP9 agrees with FFmpeg's
 * libavcodec/videotoolbox.c public-API compatibility constant.
 */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned long darwin_clock_t;
struct timebase { u32 numer, denom; };
struct audio_description { u32 type, subtype, manufacturer, flags, flags_mask; };
typedef void *(*find_next_fn)(void *, const struct audio_description *);
typedef unsigned char (*hardware_decode_fn)(u32);

extern int printf(const char *, ...) __attribute__((format(printf, 1, 2)));
extern int fflush(void *);
extern long write(int, const void *, unsigned long);
extern int getpid(void);
extern long time(long *);
extern unsigned int sleep(unsigned int);
extern darwin_clock_t clock(void);
extern u64 mach_absolute_time(void);
extern int mach_timebase_info(struct timebase *);
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern char *dlerror(void);

_Static_assert(sizeof(void *) == 8 && sizeof(unsigned long) == 8, "Darwin LP64");
_Static_assert(sizeof(u32) == 4 && sizeof(u64) == 8, "Darwin integer ABI");
_Static_assert(sizeof(struct audio_description) == 20, "AudioComponentDescription");
_Static_assert(sizeof(struct timebase) == 8, "mach_timebase_info_data_t");

static struct timebase tb;
static int pid;

static int equal(const char *a, const char *b)
{
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

static void marker(const char *phase)
{
    printf("{\"phase\":\"%s\",\"pid\":%d,\"epoch_seconds\":%ld,"
           "\"mach_ticks\":%llu}\n", phase, pid, time((void *)0), mach_absolute_time());
    fflush((void *)0);
}

static int failure(const char *phase, int code)
{
    marker(phase);
    const char *detail = dlerror();
    if (detail) {
        unsigned long n = 0;
        while (detail[n]) ++n;
        write(2, detail, n);
        write(2, "\n", 1);
    }
    return code;
}

static void duration(const char *phase, u64 wall_start, darwin_clock_t cpu_start,
                     u64 wall_end, darwin_clock_t cpu_end, int result,
                     const char *result_key)
{
    u64 ticks = wall_end - wall_start;
    u64 ns = (ticks / tb.denom) * tb.numer
        + ((ticks % tb.denom) * tb.numer) / tb.denom;
    int valid = cpu_start != (darwin_clock_t)-1
        && cpu_end != (darwin_clock_t)-1 && cpu_end >= cpu_start;
    printf("{\"phase\":\"%s\",\"pid\":%d,\"epoch_seconds\":%ld,"
           "\"mach_start\":%llu,\"mach_end\":%llu,\"wall_ns\":%llu,"
           "\"cpu_valid\":%s,\"cpu_ticks\":%lu,\"cpu_ticks_per_second\":1000000,"
           "\"%s\":%s}\n",
           phase, pid, time((void *)0), wall_start, wall_end, ns,
           valid ? "true" : "false", valid ? cpu_end - cpu_start : 0UL,
           result_key, result < 0 ? "null" : result ? "true" : "false");
    fflush((void *)0);
}

static void sleep_complete(unsigned int seconds)
{
    while (seconds) seconds = sleep(seconds);
}

static void query_codecs(find_next_fn find_next, hardware_decode_fn hardware_decode)
{
    static const char *const phases[2][4] = {
        { "first_vp9", "first_av1", "first_opus", "first_vorbis" },
        { "repeat_vp9", "repeat_av1", "repeat_opus", "repeat_vorbis" }
    };
    struct measured { u64 start, end; darwin_clock_t cpu_start, cpu_end; int result; };
    struct measured rows[4];
    for (int pass = 0; pass < 2; ++pass) {
        marker(pass ? "repeat_codecs_start" : "first_codecs_start");
        u64 start = mach_absolute_time();
        darwin_clock_t cpu_start = clock();
        /* No reporting/flush between these four calls: keep their source order. */
        for (int item = 0; item < 4; ++item) {
            rows[item].start = mach_absolute_time();
            rows[item].cpu_start = clock();
            if (item < 2) {
                u32 codec = item ? 0x61763031U : 0x76703039U; /* av01 / vp09 */
                rows[item].result = hardware_decode(codec) != 0;
            } else {
                const struct audio_description desc = {
                    0x61646563U, item == 2 ? 0x6f707573U : 0x766f7262U,
                    0x6170706cU, 2U, 0U /* adec, opus/vorb, appl, SandboxSafe */
                };
                rows[item].result = find_next((void *)0, &desc) != (void *)0;
            }
            rows[item].cpu_end = clock();
            rows[item].end = mach_absolute_time();
        }
        darwin_clock_t cpu_end = clock();
        u64 end = mach_absolute_time();
        for (int item = 0; item < 4; ++item)
            duration(phases[pass][item], rows[item].start, rows[item].cpu_start,
                     rows[item].end, rows[item].cpu_end, rows[item].result,
                     item < 2 ? "hardware_decode_supported" : "component_found");
        duration(pass ? "repeat_codecs_total" : "first_codecs_total",
                 start, cpu_start, end, cpu_end, -1, "result");
    }
}

int main(int argc, char **argv)
{
    int load_only = argc == 2 && equal(argv[1], "load-only");
    int codecs = argc == 2 && equal(argv[1], "codecs");
    if (!(argc == 1 || load_only || codecs || (argc == 2 && equal(argv[1], "query"))))
        return 2;
    pid = getpid();
    if (mach_timebase_info(&tb) != 0 || !tb.numer || !tb.denom)
        return failure("timebase_failed", 3);
    printf("{\"phase\":\"START\",\"pid\":%d,\"mode\":\"%s\","
           "\"timebase_numer\":%u,\"timebase_denom\":%u,\"probe_version\":2,"
           "\"ready_wait_seconds\":15,\"hold_seconds\":60}\n",
           pid, load_only ? "load-only" : codecs ? "codecs" : "query", tb.numer, tb.denom);
    fflush((void *)0);
    marker("load_start");
    u64 wall_start = mach_absolute_time();
    darwin_clock_t cpu_start = clock();
    /* Darwin RTLD_LAZY | RTLD_LOCAL; load only the guest's installed framework. */
    void *library = dlopen("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox", 0x1 | 0x4);
    darwin_clock_t cpu_end = clock();
    u64 wall_end = mach_absolute_time();
    duration("framework_load", wall_start, cpu_start, wall_end, cpu_end, -1, "component_found");
    if (!library) return failure("dlopen_failed", 4);
    find_next_fn find_next = (find_next_fn)dlsym(library, "AudioComponentFindNext");
    if (!find_next) return failure("dlsym_failed", 5);
    hardware_decode_fn hardware_decode = (hardware_decode_fn)0;
    if (codecs) {
        marker("videotoolbox_load_start");
        wall_start = mach_absolute_time();
        cpu_start = clock();
        void *video = dlopen("/System/Library/Frameworks/VideoToolbox.framework/VideoToolbox", 0x1 | 0x4);
        cpu_end = clock();
        wall_end = mach_absolute_time();
        duration("videotoolbox_load", wall_start, cpu_start, wall_end, cpu_end, -1, "result");
        if (!video) return failure("videotoolbox_dlopen_failed", 6);
        hardware_decode = (hardware_decode_fn)dlsym(video, "VTIsHardwareDecodeSupported");
        if (!hardware_decode) return failure("videotoolbox_dlsym_failed", 7);
        printf("{\"phase\":\"codec_sequence\",\"order\":[\"vp09\",\"av01\",\"opus\",\"vorb\"],"
               "\"scope\":\"public queries only; no enableMediaPlayback or Safari sandbox\"}\n");
    }
    printf("{\"phase\":\"descriptor\",\"type\":\"adec\",\"subtype\":\"opus\","
           "\"manufacturer\":\"appl\",\"flags\":2,\"flags_mask\":0}\n");
    marker("READY");
    sleep_complete(15);
    if (load_only) {
        marker("query_skipped");
    } else if (codecs) {
        query_codecs(find_next, hardware_decode);
    } else {
        const struct audio_description desc = {
            0x61646563U, 0x6f707573U, 0x6170706cU, 2U, 0U
        };
        for (int index = 0; index < 2; ++index) {
            marker(index ? "repeat_query_start" : "first_query_start");
            wall_start = mach_absolute_time();
            cpu_start = clock();
            void *component = find_next((void *)0, &desc);
            cpu_end = clock();
            wall_end = mach_absolute_time();
            duration(index ? "repeat_query" : "first_query",
                     wall_start, cpu_start, wall_end, cpu_end, component != (void *)0, "component_found");
        }
    }
    marker("HOLD");
    sleep_complete(60);
    marker("DONE");
    return 0;
}

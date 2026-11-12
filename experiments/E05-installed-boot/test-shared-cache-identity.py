#!/usr/bin/env python3
"""Host ABI fixtures for the read-only probe; no macOS acceptance claim."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CACHE_UUID = '00112233-4455-6677-8899-aabbccddeeff'
IMAGE_UUID = '10213243-5465-7687-98a9-bacbdcedfe0f'

SHIM = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
static unsigned char cache[8192];
static unsigned char cache_id[16] = {0,17,34,51,68,85,102,119,136,153,170,187,204,221,238,255};
static unsigned char image_id[16] = {16,33,50,67,84,101,118,135,152,169,186,203,220,237,254,15};
static const char *which;
static unsigned count_calls, range_calls;
static int is(const char *s) { return !strcmp(which,s); }
static void put32(unsigned char *p, uint32_t x) { memcpy(p,&x,4); }
static bool cache_uuid(unsigned char *out) {
    memcpy(out,cache_id,16); if (is("wrong_cache")) out[0]^=1; return true;
}
static const void *cache_range(unsigned long *size) {
    *size=sizeof(cache); ++range_calls;
    if (is("no_cache")) return 0;
    return cache + ((is("cache_changed") && range_calls>1) ? 16 : 0);
}
static bool immutable(const void *p, unsigned long n) {
    if (is("mutable_header")) return false;
    if (is("mutable_commands") && n>32) return false;
    uintptr_t at=(uintptr_t)p, start=(uintptr_t)cache;
    return at>=start && at-start<=sizeof(cache) && n<=sizeof(cache)-(at-start);
}
static bool image_uuid(const void *p, unsigned char *out) {
    if (p!=cache+4096) abort();
    memcpy(out,cache+4096+40,16);
    if (is("api_uuid_mismatch")) out[0]^=1;
    return true;
}
static long image_slide(const void *p) { if (p!=cache+4096) abort(); return 16384; }
static unsigned image_count(void) {
    ++count_calls;
    if (is("too_many_images")) return 16385;
    return is("already_loaded") || (is("loaded_after") && count_calls>1);
}
static const void *image_header(unsigned i) { if (i) abort(); return cache+4096; }
void *probe_dlsym(void *scope,const char *name) {
    if ((intptr_t)scope!=-2) abort();
    if (is("missing_api") && !strcmp(name,"_dyld_is_memory_immutable")) return 0;
    if (!strcmp(name,"_dyld_get_shared_cache_uuid")) return (void *)cache_uuid;
    if (!strcmp(name,"_dyld_get_shared_cache_range")) return (void *)cache_range;
    if (!strcmp(name,"_dyld_is_memory_immutable")) return (void *)immutable;
    if (!strcmp(name,"_dyld_get_image_uuid")) return (void *)image_uuid;
    if (!strcmp(name,"_dyld_get_image_slide")) return (void *)image_slide;
    if (!strcmp(name,"_dyld_image_count")) return (void *)image_count;
    if (!strcmp(name,"_dyld_get_image_header")) return (void *)image_header;
    abort();
}
extern int identity_main(int,char **);
int main(int argc,char **argv) {
    which=getenv("PROBE_FIXTURE"); if (!which) which="valid";
    unsigned char *p=cache+4096;
    put32(p,0xfeedfacf); put32(p+4,is("wrong_cpu")?0x01000007:0x0100000c);
    put32(p+12,6); put32(p+16,1); put32(p+20,24);
    put32(p+32,0x1b); put32(p+36,24); memcpy(p+40,image_id,16);
    if (is("wrong_image")) p[40]^=1;
    if (is("bad_command")) put32(p+36,0);
    if (is("huge_commands")) put32(p+20,65544);
    if (is("duplicate_uuid")) {
        put32(p+16,2); put32(p+20,48); memcpy(p+56,p+32,24);
    }
    return identity_main(argc,argv);
}
'''


class IdentityProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='shared-cache-identity-test-')
        cls.out = Path(cls.temp.name)
        shim = cls.out / 'abi.c'
        shim.write_text(SHIM)
        source = ROOT / 'experiments/E05-installed-boot/guest-diag/shared-cache-identity.c'
        flags = ['clang', '-std=c11', '-O1', '-fsanitize=undefined', '-fno-sanitize-recover=all',
                 '-Wall', '-Wextra', '-Werror']
        subprocess.run(flags + ['-Dmain=identity_main', '-Ddlsym=probe_dlsym', '-c', str(source),
                               '-o', str(cls.out / 'probe.o')], check=True, capture_output=True)
        cls.binary = cls.out / 'probe'
        subprocess.run(flags + [str(shim), str(cls.out / 'probe.o'), '-o', str(cls.binary)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_probe(self, case='valid', args=None):
        result = subprocess.run([str(self.binary)] + (args if args is not None else
            [CACHE_UUID, IMAGE_UUID, '0x1000']), env={**os.environ, 'PROBE_FIXTURE': case},
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def test_current_cache_and_image_identity(self):
        rc, result = self.run_probe()
        self.assertEqual(rc, 0)
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(result['cache_uuid'], CACHE_UUID)
        self.assertEqual(result['image_uuid'], IMAGE_UUID)
        self.assertEqual(int(result['image_base'], 16)-int(result['cache_base'], 16), 4096)
        self.assertEqual(result['lc_uuid_payload_image_offset'], 40)
        self.assertEqual(result['image_slide'], 16384)
        for key in ('target_loaded_before', 'target_loaded_after', 'target_dlopen_performed'):
            self.assertFalse(result[key])

    def test_uuid_and_mapping_mismatches_are_unavailable(self):
        for case, code in [('wrong_cache', 5), ('wrong_image', 11), ('api_uuid_mismatch', 11),
                           ('no_cache', 5), ('cache_changed', 12)]:
            with self.subTest(case=case):
                rc, result = self.run_probe(case)
                self.assertEqual(rc, code)
                self.assertEqual(result['state'], 'unavailable')

    def test_loaded_target_and_missing_api_are_rejected(self):
        for case, code in [('already_loaded', 4), ('loaded_after', 12), ('too_many_images', 4),
                           ('missing_api', 3)]:
            with self.subTest(case=case):
                rc, result = self.run_probe(case)
                self.assertEqual(rc, code)
                self.assertEqual(result['state'], 'unavailable')

    def test_header_and_commands_need_immutable_bounded_storage(self):
        for case, code in [('mutable_header', 7), ('mutable_commands', 9), ('wrong_cpu', 8),
                           ('bad_command', 10), ('huge_commands', 9), ('duplicate_uuid', 10)]:
            with self.subTest(case=case):
                rc, result = self.run_probe(case)
                self.assertEqual(rc, code)
                self.assertEqual(result['state'], 'unavailable')

    def test_arguments_and_unsigned_offset_bounds(self):
        for args, code in [([], 2), ([CACHE_UUID, 'bad', '0x1000'], 2),
                           ([CACHE_UUID, IMAGE_UUID, '1000'], 2),
                           ([CACHE_UUID, IMAGE_UUID, '0x10000000000000000'], 2),
                           ([CACHE_UUID, IMAGE_UUID, '0xffffffffffffffff'], 6),
                           ([CACHE_UUID, IMAGE_UUID, '0x1ff0'], 6)]:
            with self.subTest(args=args):
                rc, result = self.run_probe(args=args)
                self.assertEqual(rc, code)
                self.assertEqual(result['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main(verbosity=2)

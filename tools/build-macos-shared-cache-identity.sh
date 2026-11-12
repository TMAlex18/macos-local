#!/usr/bin/env bash
# Build only our diagnostic source; the text stub contains import names, no SDK.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXP="$PROJECT_ROOT/experiments/E05-installed-boot"
OUT="$PROJECT_ROOT/build/macos-shared-cache-identity"
CLANG="${CLANG:-/usr/bin/clang}"
LD64="${LD64:-/usr/bin/ld64.lld}"
mkdir -p "$OUT"
"$CLANG" --target=arm64-apple-macos11.0 -std=c11 -O2 -Wall -Wextra -Werror \
    -ffreestanding -fno-builtin -fno-stack-protector \
    -c "$EXP/guest-diag/shared-cache-identity.c" -o "$OUT/shared-cache-identity.o"
"$LD64" -arch arm64 -platform_version macos 11.0 11.0 -e _main \
    -L "$EXP/macos-stubs" -lSystem -adhoc_codesign \
    "$OUT/shared-cache-identity.o" -o "$OUT/shared-cache-identity"
llvm-objdump --macho --private-headers "$OUT/shared-cache-identity" > "$OUT/load-commands.txt"
llvm-objdump --macho --dylibs-used --bind --lazy-bind "$OUT/shared-cache-identity" > "$OUT/imports.txt"
file "$OUT/shared-cache-identity" > "$OUT/file.txt"
python3 - "$PROJECT_ROOT" "$CLANG" "$LD64" <<'PY'
from datetime import datetime, timezone
import hashlib, json, os, struct, subprocess, sys
from pathlib import Path
root=Path(sys.argv[1]); out=root/'build/macos-shared-cache-identity'
exp=root/'experiments/E05-installed-boot'; binary=out/'shared-cache-identity'
data=binary.read_bytes()
header=struct.unpack_from('<8I',data)
assert header[:4]==(0xfeedfacf,0x100000c,0,2), 'Expected arm64 MH_EXECUTE'
offset=32; dylibs=[]; dylinker=None; signature=None; entry=None
for _ in range(header[4]):
    cmd,size=struct.unpack_from('<II',data,offset)
    assert size>=8 and offset+size<=32+header[5], 'Malformed load command'
    if cmd in (0xc,0xe):
        name=struct.unpack_from('<I',data,offset+8)[0]
        value=data[offset+name:offset+size].split(b'\0',1)[0].decode()
        if cmd==0xc:dylibs.append(value)
        else:dylinker=value
    elif cmd==0x80000028:
        entry=struct.unpack_from('<Q',data,offset+8)[0]
    elif cmd==0x1d:
        signature=struct.unpack_from('<II',data,offset+8)
    offset+=size
assert dylibs==['/usr/lib/libSystem.B.dylib'], dylibs
assert dylinker=='/usr/lib/dyld' and entry and entry<len(data)
assert signature is not None, 'Missing LC_CODE_SIGNATURE'
sig_offset,sig_size=signature
magic,length,count=struct.unpack_from('>III',data,sig_offset)
assert magic==0xfade0cc0 and length<=sig_size
directory=None
for index in range(count):
    slot,relative=struct.unpack_from('>II',data,sig_offset+12+8*index)
    if slot==0:directory=sig_offset+relative
assert directory is not None
fields=struct.unpack_from('>9I4B',data,directory)
magic,length,version,flags,hash_offset,ident_offset,special,slots,limit,hash_size,hash_type,platform,page_bits=fields
assert magic==0xfade0c02 and flags&2 and hash_type==2 and hash_size==32
assert limit==sig_offset and slots==(limit+(1<<page_bits)-1)//(1<<page_bits)
for index in range(slots):
    page=data[index*(1<<page_bits):min((index+1)*(1<<page_bits),limit)]
    actual=data[directory+hash_offset+index*hash_size:directory+hash_offset+(index+1)*hash_size]
    assert hashlib.sha256(page).digest()==actual, f'CodeDirectory page {index}'
undefined=subprocess.check_output(['llvm-nm','--undefined-only',str(binary)],text=True)
symbols={line.split()[-1] for line in undefined.splitlines()}
allowed={'_printf','_getpid','_dlsym','dyld_stub_binder'}
assert symbols<=allowed and {'_printf','_dlsym','_getpid'}<=symbols, symbols
digest=hashlib.sha256(data).hexdigest()
(out/'shared-cache-identity.sha256').write_text(digest+'  shared-cache-identity\n')
served=[]
for name in ('shared-cache-identity','shared-cache-identity.sha256'):
    link=exp/'guest-diag'/name
    target=os.path.relpath(out/name,link.parent)
    if os.path.lexists(link):
        assert link.is_symlink() and os.readlink(link)==target, 'Unexpected served artifact'
    else:link.symlink_to(target)
    assert link.read_bytes()==(out/name).read_bytes()
    served.append(str(link.relative_to(root)))
report={'built_at_utc':datetime.now(timezone.utc).isoformat(),
    'source':str((exp/'guest-diag/shared-cache-identity.c').relative_to(root)),
    'source_sha256':hashlib.sha256((exp/'guest-diag/shared-cache-identity.c').read_bytes()).hexdigest(),
    'stub_sha256':hashlib.sha256((exp/'macos-stubs/libSystem.tbd').read_bytes()).hexdigest(),
    'binary':str(binary.relative_to(root)),'binary_sha256':digest,'binary_bytes':len(data),
    'clang':subprocess.check_output([sys.argv[2],'--version'],text=True).splitlines()[0],
    'linker':subprocess.check_output([sys.argv[3],'--version'],text=True).strip(),
    'target':'arm64-apple-macos11.0','probe_version':1,
    'arguments':['cache_uuid','image_uuid','image_cache_offset_hex'],
    'loads_target_library':False,
    'abi_references':['https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/dyld_priv.h',
                      'https://github.com/apple-oss-distributions/dyld/blob/main/include/dlfcn.h'],
    'sdk_used':False,'sdk_version_load_command':'11.0 (linker minimum, no SDK installed)',
    'load_dylibs':dylibs,'dynamic_linker':dylinker,'entry_file_offset':entry,
    'undefined_symbols':sorted(symbols),'adhoc_code_directory_sha256_pages_verified':slots,
    'code_signature_flags':flags,'served_links':served,'guest_executed':False,
    'scope':'Cross-build, Mach-O imports/entry and mathematical ad-hoc page-hash validation only; macOS loader/signature acceptance and dyld metadata ABI behavior require a guest run.'}
(out/'build.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'binary':str(binary),'sha256':digest,'static_validation':'passed','guest_executed':False}))
PY

#!/bin/sh
# Usage: sh app-suite-probe.sh TAG [hold_gui_seconds]
# Exercises stock macOS arm64e GUI applications and userland frameworks:
#   1. Calculator.app, TextEdit.app, System Information.app (launch, GUI registration, clean quit)
#   2. JavaScriptCore (jsc) sieve, Float64 matrix multiply, and BigInt modular exponentiation
#   3. CoreGraphics / ImageIO (sips) BMP -> PNG lossless encode and 2x downsample
#   4. SQLite3 WAL mode, ACID transaction + rollback, recursive CTE, window function, VACUUM INTO
#   5. Property List (plutil) XML -> binary1 (bplist00) -> JSON round-trip
#   6. Archive/compression & SHA-256 digest (gzip + shasum + sw_vers + uname)
# Host validation is performed independently by tools/validate-app-suite.py.
set -u
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
hold_gui=${2:-12}
case "$tag" in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
case "$hold_gui" in ''|*[!0-9]*) exit 2;; esac

d=/tmp/$tag
mkdir "$d" || exit 2

cleanup_apps() {
    for app in "Calculator" "TextEdit" "System Information"; do
        osascript -e "quit app \"$app\"" >/dev/null 2>&1 || true
    done
    for proc in "Calculator" "TextEdit" "System Information"; do
        for _ in $(seq 30); do
            pgrep -x "$proc" >/dev/null 2>&1 || break
            sleep 1
        done
    done
}
trap 'cleanup_apps' 0
trap 'cleanup_apps; exit 143' TERM INT

finish() {
    rc=$1
    echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$d/summary.txt"
    echo "collector_exit=$rc" >> "$d/summary.txt"
    tar -C /tmp -czf "/tmp/$tag.tgz" "$tag" || exit 1
    curl -fsS --connect-timeout 5 -m 60 -T "/tmp/$tag.tgz" "$H/up/$tag.tgz" || exit 1
    echo "uploaded $tag.tgz"
    exit "$rc"
}

{
    echo "schema=1"
    echo "fixture=macos-app-and-framework-suite-v1"
    echo "tag=$tag"
    echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "hold_gui_seconds=$hold_gui"
} > "$d/summary.txt"

sw_vers > "$d/sw_vers.txt" 2>&1
uname -a > "$d/uname.txt" 2>&1

# --- 1. JavaScriptCore (jsc) native arm64e execution ---
jsc_bin=""
for cand in \
    "/System/Library/Frameworks/JavaScriptCore.framework/Helpers/jsc" \
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc" \
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"; do
    if [ -x "$cand" ]; then
        jsc_bin=$cand
        break
    fi
done
echo "jsc_bin=$jsc_bin" >> "$d/summary.txt"
if [ -n "$jsc_bin" ]; then
    cat > "$d/jsc-work.js" << 'EOF'
const N = 100000;
const composite = new Uint8Array(N + 1);
let primeCount = 0;
let primeSum = 0;
for (let p = 2; p <= N; p++) {
    if (!composite[p]) {
        primeCount++;
        primeSum += p;
        if (p * p <= N) {
            for (let m = p * p; m <= N; m += p) composite[m] = 1;
        }
    }
}
const K = 16;
const A = new Float64Array(K * K);
const B = new Float64Array(K * K);
const C = new Float64Array(K * K);
for (let i = 0; i < K; i++) {
    for (let j = 0; j < K; j++) {
        A[i * K + j] = (i * 3 + j * 5 - 11) * 0.25;
        B[i * K + j] = (i * 7 - j * 2 + 3) * 0.5;
    }
}
let matTrace = 0.0;
let matFrobSq = 0.0;
for (let i = 0; i < K; i++) {
    for (let j = 0; j < K; j++) {
        let acc = 0.0;
        for (let t = 0; t < K; t++) acc += A[i * K + t] * B[t * K + j];
        C[i * K + j] = acc;
        if (i === j) matTrace += acc;
        matFrobSq += acc * acc;
    }
}
function modPow(base, exp, mod) {
    let result = 1n;
    let b = base % mod;
    let e = exp;
    while (e > 0n) {
        if (e & 1n) result = (result * b) % mod;
        b = (b * b) % mod;
        e >>= 1n;
    }
    return result;
}
const modExp = modPow(3n, 4096n, 1000000007n).toString();
print(JSON.stringify({
    prime_limit: N,
    prime_count: primeCount,
    prime_sum: primeSum,
    mat_dim: K,
    mat_trace: matTrace,
    mat_frob_sq: matFrobSq,
    bigint_modexp: modExp
}));
EOF
    "$jsc_bin" "$d/jsc-work.js" > "$d/jsc-out.json" 2> "$d/jsc-err.txt"
    echo "jsc_exit=$?" >> "$d/summary.txt"
else
    echo "jsc_exit=127" >> "$d/summary.txt"
fi

# --- 2. CoreGraphics / ImageIO (sips) lossless BMP -> PNG & 2x resize ---
LC_ALL=C awk '
BEGIN {
    b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    nbuf = 0
    W = 64; H = 48; img = W * H * 3; fsz = 54 + img
    hdr[0]=66; hdr[1]=77
    hdr[2]=fsz%256; hdr[3]=int(fsz/256)%256; hdr[4]=0; hdr[5]=0
    for (i=6; i<=9; i++) hdr[i]=0
    hdr[10]=54; hdr[11]=0; hdr[12]=0; hdr[13]=0
    hdr[14]=40; hdr[15]=0; hdr[16]=0; hdr[17]=0
    hdr[18]=W;  hdr[19]=0; hdr[20]=0; hdr[21]=0
    hdr[22]=H;  hdr[23]=0; hdr[24]=0; hdr[25]=0
    hdr[26]=1;  hdr[27]=0; hdr[28]=24; hdr[29]=0
    for (i=30; i<=33; i++) hdr[i]=0
    hdr[34]=img%256; hdr[35]=int(img/256)%256; hdr[36]=0; hdr[37]=0
    for (i=38; i<=53; i++) hdr[i]=0
    for (i=0; i<54; i++) emit(hdr[i])
    for (y=0; y<H; y++) {
        for (x=0; x<W; x++) {
            if (y < 24 && x < 32)      { r=220; g=40;  b=60  }
            else if (y < 24 && x >= 32){ r=40;  g=180; b=80  }
            else if (y >= 24 && x < 32){ r=50;  g=90;  b=230 }
            else                       { r=240; g=200; b=30  }
            emit(b); emit(g); emit(r)
        }
    }
    printf "\n"
}
function emit(v,   n) {
    buf[nbuf++] = v
    if (nbuf == 3) {
        n = buf[0] * 65536 + buf[1] * 256 + buf[2]
        printf "%s%s%s%s", \
            substr(b64, int(n / 262144) + 1, 1), \
            substr(b64, (int(n / 4096) % 64) + 1, 1), \
            substr(b64, (int(n / 64) % 64) + 1, 1), \
            substr(b64, (n % 64) + 1, 1)
        nbuf = 0
    }
}
' > "$d/input.bmp.b64"
if /usr/bin/base64 -d -i "$d/input.bmp.b64" -o "$d/input.bmp" 2>/dev/null; then
    :
else
    /usr/bin/openssl base64 -d -A -in "$d/input.bmp.b64" -out "$d/input.bmp"
fi
rm -f "$d/input.bmp.b64"
/usr/bin/sips -s format png "$d/input.bmp" --out "$d/out.png" > "$d/sips-encode.txt" 2>&1
echo "sips_encode_exit=$?" >> "$d/summary.txt"
/usr/bin/sips -z 24 32 "$d/out.png" --out "$d/resized.png" > "$d/sips-resize.txt" 2>&1
echo "sips_resize_exit=$?" >> "$d/summary.txt"
/usr/bin/sips -g pixelWidth -g pixelHeight -g format "$d/out.png" "$d/resized.png" > "$d/sips-info.txt" 2>&1

# --- 3. SQLite3 WAL ACID + CTE + Window functions ---
/usr/bin/sqlite3 "$d/work.db" > "$d/sqlite-out.txt" 2> "$d/sqlite-err.txt" << 'EOF'
PRAGMA journal_mode=WAL;
CREATE TABLE ledger (
    id INTEGER PRIMARY KEY,
    bucket TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0)
);
CREATE INDEX idx_ledger_bucket ON ledger(bucket);
BEGIN IMMEDIATE;
WITH RECURSIVE seq(n) AS (
    SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 250
)
INSERT INTO ledger(id, bucket, amount)
SELECT n, CASE (n % 4) WHEN 0 THEN 'alpha' WHEN 1 THEN 'beta' WHEN 2 THEN 'gamma' ELSE 'delta' END,
       (n * 17) % 103
FROM seq;
COMMIT;
BEGIN IMMEDIATE;
INSERT INTO ledger(id, bucket, amount) VALUES (999, 'rolled_back', 12345);
ROLLBACK;
SELECT 'count=' || COUNT(*) || ',sum=' || SUM(amount) FROM ledger;
SELECT bucket || ':' || COUNT(*) || ':' || SUM(amount) || ':' || MAX(running)
FROM (
    SELECT bucket, amount, SUM(amount) OVER (PARTITION BY bucket ORDER BY id) AS running
    FROM ledger
)
GROUP BY bucket ORDER BY bucket;
PRAGMA integrity_check;
EOF
echo "sqlite_work_exit=$?" >> "$d/summary.txt"
/usr/bin/sqlite3 "$d/work.db" "VACUUM INTO '$d/compact.db';" >> "$d/sqlite-err.txt" 2>&1
echo "sqlite_vacuum_exit=$?" >> "$d/summary.txt"
rm -f "$d/work.db" "$d/work.db-wal" "$d/work.db-shm"

# --- 4. Property List (plutil) XML -> binary1 -> JSON round-trip ---
cat > "$d/input.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>suite</key>
    <string>macos-app-and-framework-suite-v1</string>
    <key>counts</key>
    <array>
        <integer>16384</integer>
        <integer>65536</integer>
        <integer>-42</integer>
    </array>
    <key>exact_half</key>
    <real>0.625</real>
    <key>flags</key>
    <dict>
        <key>arm64e</key>
        <true/>
        <key>rosetta</key>
        <false/>
    </dict>
</dict>
</plist>
EOF
cp "$d/input.plist" "$d/data.bplist"
/usr/bin/plutil -convert binary1 "$d/data.bplist" > "$d/plutil.txt" 2>&1
echo "plutil_binary_exit=$?" >> "$d/summary.txt"
/usr/bin/plutil -lint "$d/data.bplist" >> "$d/plutil.txt" 2>&1
echo "plutil_lint_exit=$?" >> "$d/summary.txt"
/usr/bin/plutil -convert json -o "$d/data-plutil.json" "$d/data.bplist" >> "$d/plutil.txt" 2>&1
echo "plutil_json_exit=$?" >> "$d/summary.txt"

# --- 5. Archive/compression & SHA-256 digest ---
LC_ALL=C awk 'BEGIN { for (i = 0; i < 1024; i++) printf "line-%04d-det-%08x\n", i, (i * 2654435761) % 4294967296 }' > "$d/payload.txt"
shasum -a 256 "$d/payload.txt" | awk '{print $1}' > "$d/payload.sha256"
/usr/bin/gzip -9 -c "$d/payload.txt" > "$d/payload.txt.gz"
/usr/bin/gzip -dc "$d/payload.txt.gz" > "$d/payload.roundtrip.txt"
shasum -a 256 "$d/payload.roundtrip.txt" | awk '{print $1}' > "$d/payload.roundtrip.sha256"
rm -f "$d/payload.txt" "$d/payload.roundtrip.txt"

# --- 6. Stock macOS Cocoa/SwiftUI GUI applications ---
cat > "$d/sample-doc.txt" << 'EOF'
macOS 27.0.1 (26A434) native arm64e TextEdit + Calculator + System Information verification.
EOF

{
    for proc in "Calculator" "TextEdit" "System Information"; do
        pgrep -x "$proc" >/dev/null 2>&1
        echo "before:$proc:pgrep_exit=$?"
    done
} > "$d/gui-lifecycle.txt"

open -a "/System/Applications/Calculator.app" > "$d/open-calc.txt" 2>&1
echo "open_calc_exit=$?" >> "$d/summary.txt"
open -a "/System/Applications/TextEdit.app" "$d/sample-doc.txt" > "$d/open-textedit.txt" 2>&1
echo "open_textedit_exit=$?" >> "$d/summary.txt"
open -a "/System/Applications/Utilities/System Information.app" > "$d/open-sysinfo.txt" 2>&1
echo "open_sysinfo_exit=$?" >> "$d/summary.txt"

gui_ready=false
for _ in $(seq 45); do
    if pgrep -x "Calculator" >/dev/null 2>&1 && \
       pgrep -x "TextEdit" >/dev/null 2>&1 && \
       pgrep -x "System Information" >/dev/null 2>&1; then
        gui_ready=true
        break
    fi
    sleep 1
done
echo "gui_all_processes_live=$gui_ready" >> "$d/summary.txt"

/usr/bin/lsappinfo list > "$d/lsappinfo-live.txt" 2>&1 || true
for proc in "Calculator" "TextEdit" "System Information"; do
    pid=$(pgrep -x "$proc" | head -n 1)
    echo "live:$proc:pid=${pid:-none}" >> "$d/gui-lifecycle.txt"
done

echo "gui_ready_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$d/gui-ready.txt"
curl -fsS --connect-timeout 5 -m 15 -T "$d/gui-ready.txt" "$H/up/$tag-gui-ready.txt" >/dev/null 2>&1 || true

if [ "$hold_gui" -gt 0 ]; then
    sleep "$hold_gui"
fi

cleanup_apps
for proc in "Calculator" "TextEdit" "System Information"; do
    pgrep -x "$proc" >/dev/null 2>&1
    echo "after:$proc:pgrep_exit=$?" >> "$d/gui-lifecycle.txt"
done

finish 0

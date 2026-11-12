#!/usr/bin/env python3
"""普通自给NIST AES128 ECB密钥：真实VMApple DMA / FLAG / SPI50验证。"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from run import ROOT, compile_case

BINARY = ROOT / "build/qemu-bdif-usb/qemu-system-aarch64"
NIST = "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38a.pdf"
KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
PLAINTEXT = "6bc1bee22e409f96e93d7e117393172a"
CIPHERTEXT = "3ad77bb40d7a3660a89ecaf32466ef97"
JOBS = [(0x72000100, 0x72004100, 0), (0x110000100, 0x110004100, 0x10001)]
TRACE_EVENTS = ("aes_write", "aes_cmd_key_select_new", "aes_cmd_data", "aes_cmd_flag", "aes_fifo_process")


def fields(stdout, name):
    return [int(v, 16) for v in re.findall(r"^" + name + r"=(0x[0-9a-f]+)$", stdout, re.M)]


def assess(stdout, stderr, trace, code, timeout, negative):
    jobs = JOBS[:1] if negative else JOBS
    expected_fifo = [0x10100000] + [int.from_bytes(KEY[i:i + 4], "little") for i in range(0, 16, 4)]
    for i, (src, dst, high) in enumerate(jobs, 1):
        expected_fifo += [0x50000010, high, src & 0xffffffff, dst & 0xffffffff, 0x88000000 | i]
    fifo = [int(v, 16) for v in re.findall(r"aes_write offset=0x200 val=0x([0-9a-f]+)", trace)]
    successful = [int(v) for v in re.findall(r"aes_fifo_process cmd=(\d+) success=1", trace)]
    marker = ("E06 AES SPI50 MASKED DATA FLAG READY WITHOUT ISR EXPECTED" if negative else
              "E06 AES128 USER KEY LOW HIGH DMA IRQ ACK REARM PASS")
    actual_ciphertext = re.findall(r"^AES_CIPHERTEXT=([0-9a-f]{32})$", stdout, re.M)
    passed = (not timeout and code == (1 if negative else 0) and marker in stdout and "FAIL:" not in stdout and
              "fatal" not in stderr.lower() and fifo == expected_fifo and
              successful == ([1, 5, 8] if negative else [1, 5, 8, 5, 8]) and
              fields(stdout, "AES_JOB") == list(range(1, len(jobs) + 1)) and
              fields(stdout, "AES_SRC_PA") == [j[0] for j in jobs] and
              fields(stdout, "AES_DST_PA") == [j[1] for j in jobs] and
              fields(stdout, "AES_ADDR_WORD") == [j[2] for j in jobs] and
              actual_ciphertext == [CIPHERTEXT] * len(jobs) and
              stdout.count("AES_INPUT_OUTPUT_16K_GUARDS_PASS") == len(jobs))
    if negative:
        notification_verified = (fields(stdout, "AES_IRQ_COUNT") == [0] and
                                 not fields(stdout, "AES_ISR_INTID") and
                                 fields(stdout, "AES_NEGATIVE_IRQ_STATUS") == [32] and
                                 fields(stdout, "AES_NEGATIVE_FLAG_INFO") == [1] and
                                 bool(fields(stdout, "AES_NEGATIVE_GIC_PENDING") and
                                      fields(stdout, "AES_NEGATIVE_GIC_PENDING")[0] & (1 << 18)) and
                                 "AES_JOB_ISR_AND_DATA_PASS" not in stdout and
                                 "E06 AES128 USER KEY LOW HIGH DMA IRQ ACK REARM PASS" not in stdout)
    else:
        notification_verified = (fields(stdout, "AES_ISR_INTID") == [50, 50] and
                                 fields(stdout, "AES_ISR_STATUS") == [32, 32] and
                                 fields(stdout, "AES_ISR_FLAG") == [1, 2] and
                                 fields(stdout, "AES_IRQ_COUNT") == [1, 2] and
                                 fields(stdout, "AES_FINAL_IRQ_STATUS") == [0] and
                                 stdout.count("AES_JOB_ISR_AND_DATA_PASS") == 2)
    return dict(passed=bool(passed and notification_verified),
                guest_exit=code, expected_exit=1 if negative else 0, host_timeout=timeout,
                missing_irq_expected=negative, notification_expectation_verified=notification_verified,
                actual_ciphertext=actual_ciphertext,
                dma=[dict(src_pa=hex(s), dst_pa=hex(d), address_word=hex(h)) for s, d, h in jobs],
                actual_iar=fields(stdout, "AES_ISR_INTID"), irq_count=fields(stdout, "AES_IRQ_COUNT"),
                flag_info=fields(stdout, "AES_ISR_FLAG"), irq_status=fields(stdout, "AES_ISR_STATUS"),
                fifo_words=[hex(v) for v in fifo], fifo_expected_sequence_verified=fifo == expected_fifo,
                successful_fifo_commands=successful,
                full_input_and_output_page_checks=len(actual_ciphertext),
                input_page_bytes=16384, output_page_bytes=16384, data_bytes_per_job=16)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    run = ROOT / "runs/e06-aes-user-key" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run.mkdir(parents=True, exist_ok=False)
    record = json.loads((ROOT / "runs/qemu-bdif-usb-build.json").read_text())
    with BINARY.open("rb") as f:
        binary_hash = hashlib.file_digest(f, "sha256").hexdigest()
    if record["binary"] != str(BINARY.relative_to(ROOT)) or record["binary_sha256"] != binary_hash:
        raise SystemExit("当前bdif binary与既有build record不一致，未启动VM")
    cases = []
    print("run:", run.relative_to(ROOT), flush=True)
    for name, defines, negative in (("aes-user-key-positive", [], False),
                                    ("aes-user-key-spi-masked", ["-DBLOCK_AES_SPI=1"], True)):
        image = compile_case("aes", name, defines)
        with tempfile.TemporaryDirectory(prefix="aes-disks-", dir=image.parent) as disk_dir:
            aux, root_disk = Path(disk_dir) / "aux.raw", Path(disk_dir) / "root.raw"
            for path, size in ((aux, 4 << 20), (root_disk, 32 << 20)):
                with path.open("wb") as f: f.truncate(size)
            cmd = [str(BINARY), "-object", "memory-backend-memfd,id=ram,size=4G,share=on",
                   "-machine", "vmapple,memory-backend=ram,uuid=1,x-ctrr-model=on,x-pac-software-model=on",
                   "-cpu", "max,pauth-qarma5=on,cntfrq=24000000,x-cntfrq-exact=on",
                   "-m", "4G", "-smp", "2", "-accel", "tcg", "-nodefaults", "-nic", "none",
                   "-display", "none", "-nographic", "-monitor", "none", "-serial", "stdio",
                   "-semihosting-config", "enable=on,target=native", "-bios", str(image),
                   "-drive", f"file={aux},format=raw,if=pflash,index=0",
                   "-drive", f"file={root_disk},format=raw,if=pflash,index=1"]
            for event in TRACE_EVENTS: cmd += ["-trace", "enable=" + event]
            timeout = False
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                   env=dict(os.environ, REIMS_VGPU_WINDOW="0", REIMS_VGPU_LOG="warn"))
                stdout, stderr, code = p.stdout, p.stderr, p.returncode
            except subprocess.TimeoutExpired as exc:
                timeout = True; code = None
                stdout = (exc.stdout or b"").decode(errors="replace")
                stderr = (exc.stderr or b"").decode(errors="replace")
        log_path = run / (name + ".log")
        log_path.write_text(stdout + "\nSTDERR:\n" + stderr)
        # The pinned binary uses QEMU log trace backend: events are on stderr,
        # and -trace file does not redirect that backend. Keep one raw log.
        row = assess(stdout, stderr, stderr, code, timeout, negative)
        row.update(case=name, log=str(log_path.relative_to(ROOT)), trace_log=str(log_path.relative_to(ROOT)),
                   trace_backend="log/stderr",
                   firmware=str(image.relative_to(ROOT)), firmware_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                   command=cmd)
        cases.append(row)
        print(name + ": " + ("PASS" if row["passed"] else "FAIL"), flush=True)
        print(stdout, flush=True)
        if not row["passed"]: break
    report = dict(time_utc=datetime.now(timezone.utc).isoformat(),
                  scope="自制EL1 guest、普通自给公开AES128 ECB key/select0/context0；不是Apple guest或受保护密钥验证",
                  binary=str(BINARY.relative_to(ROOT)), binary_sha256=binary_hash,
                  source_pin=record["base_commit"], build_record="runs/qemu-bdif-usb-build.json",
                  cipher_backend=record.get("cipher_backend"), device_profile=record.get("device_profile"),
                  platform=dict(ram_base="0x70000000", ram_end_exclusive="0x170000000", memory="4G",
                                guest_counter_hz=24000000, configured_vcpus=2, active_probe_vcpus=1,
                                guest_allocation_alignment=16384, mmu_enabled=False,
                                ctrr_pac_models_configured=True, protected_semantics_exercised=False),
                  vector=dict(source=NIST, section="F.1.1 ECB-AES128.Encrypt, block1; printed page24 / PDF page31",
                              public_test_key=KEY.hex(), plaintext=PLAINTEXT, ciphertext=CIPHERTEXT),
                  device=dict(mmio="0x30220000", fifo_offset="0x200", key_selector=0, key_context=0,
                              mode="ECB", key_bits=128, dma_operation="encrypt", flag_irq_status_bit=5,
                              gic_intid=50, trigger="level", ack="W1C IRQ status bit5 before EOImode0"),
                  oracle="客体独立匹配NIST密文并逐字节检查每项16KiB输入/输出页；正例仅实际IAR50/FLAG1、2后接受完成；屏蔽SPI负例须数据/FLAG/GIC pending就绪而无ISR，退出1；host timeout永不通过",
                  cases=cases, passed=len(cases) == 2 and all(c["passed"] for c in cases))
    path = run / "results.json"; path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("result:", path.relative_to(ROOT))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

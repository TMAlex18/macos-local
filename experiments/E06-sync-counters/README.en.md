> **Language:** **English** | [简体中文](README.md)

# E06-sync-counters — TCG BQL and Exclusive-Section Source Counter Checks

Validates `0033-tcg-sync-source-counters.patch` with a self-authored dual-vCPU AArch64 bare-metal guest (no Apple code or network):

- Default-off (`x-sync-stats` omitted): `x-sync-stats-json` reports `{"enabled":false}`;
- With `-cpu max,x-sync-stats=on` under dual-vCPU MTTCG: exact loop counts for `CNTVCT_EL0` reads (37), `CNTP_CVAL_EL0` writes (13), `SVC`+`ERET` round-trips (19 traps / 38 `ERET` hook BQL locks), `TLBI VAE1IS` (11 `excl_tlbi_page`), `TLBI RVAE1IS` (3 `excl_tlbi_range`), `TLBI VMALLE1IS` (5 `excl_tlbi_all`), PL011 MMIO writes (5 `bql_mmio`), and peer-vCPU ordinary queued work match expected counts;
- `system_reset` zeroes all scalar and per-register counters.

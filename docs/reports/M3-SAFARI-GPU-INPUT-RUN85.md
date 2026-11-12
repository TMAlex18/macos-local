# M3 Intel native application and input validation — Run85

**Run:** `m3-intel-native-app-control-20261104-85`  
**Started:** 2026-11-04 17:12:42 UTC  
**Finished:** 2026-11-04 19:47:51 UTC, 9,309.531 seconds total.  
**Current work:** [STATUS.md](../STATUS.md); the promoted PAC candidate is undergoing same-binary off/on application validation.

## Established results

The same BGRA8 candidate binary used by Run83 boots the installed normal OS and displays login and the desktop through the Intel native window. The profile remains 8 vCPUs, QARMA5/TCG, USB input, user NAT, exact half blend and a requested 30 Hz diagnostic refresh. This is a functional profile, not an FPS result.

Guest command execution and networking now have independent evidence. After modifier-only recovery, explicit Terminal focus and paced public input, `pwd` returned `/Users/macos`. The guest then fetched and executed `network.sh`, printed its results and uploaded the text to the host.

| Check | Result | Evidence under the run directory |
|---|---|---|
| Actual command execution | `pwd` and new output observed | `native-pwd-proof.png` |
| Guest → host HTTP | 200, exit 0, endpoint 10.0.2.2 | `guest-uploads/n85.txt` |
| DNS + public HTTPS | IANA request 200, certificate verification 0, curl exit 0 | same upload; `native-network-running.png` |
| Guest → host upload | PUT accepted, file contents match visible output | `guest-uploads/n85.txt` |
| Input queue diagnostic consumer | `echo drain85` produced `drain85`; 26 fresh drain observations, no full/timeout, original switches restored | `native-drain-proof.png`, `ui-actions.jsonl` |

The default route selected `en1`. A first query against assumed `en0` returned no address/lease and was retained in `d85.txt`; it was not classified as a network failure. The route-selected follow-up in `d85route.txt` returned address 10.0.2.15, DHCP ACK, server/router 10.0.2.2, DNS 10.0.2.3 and a 86400-second lease. This establishes the tested user-NAT configuration, not all protocols.

## Input handling correction

QMP acceptance alone did not reliably identify the visible effect during earlier attempts: commands were absent while menus/panels appeared. Source inspection found a concrete helper hazard: the HID keyboard has 16 raw-scancode slots and can drop an event without a QMP error, while the old recovery operation sent 63 keyups (71 scancodes) in one burst, with critical modifiers late in the list.

The helper now releases modifiers first and paces individual recovery events. Its opt-in public-keyboard mode waits on fresh payload-free queue drain events, stops on overflow/timeout without replaying accepted input, and restores trace switches. The 31 offline checks and actual `echo drain85` consumer passed. The earlier host pointer-keepalive wrapper was separately found invalid: it invoked nonexistent `move` rather than `pointer`, and all 180 logged invocations exited 2. It is not counted as effective; the guest caffeinate process had independent command and UI evidence. This does not retrospectively prove overflow caused every earlier UI failure, nor does a queue-empty event by itself prove application execution. Secret input is excluded from this trace mode.

## Safari first-page failure with working networking

`s85local-prepare.txt` confirms ordinary Safari quit and the exact Run65 fixture SHA-256, `d4bda62b4ac464fe07877d7091a970fea94e0d7bb84919de675c253c544c7033`. The fixture contains only a heading and opaque RGB blocks, with no external resources.

The new launch initially showed a white page and later the repeated-problem UI. The WebKit log confirms all 185 bytes arrived and the page committed/finished loading; those events do not prove successful visible paint. See `native-safari-local-start.png`, `native-safari-local-end.png` and `guest-uploads/s85local.tgz`.

Times below are UTC; the log inside the archive is in UTC−08:00.

| UTC | Observation | `s85local/webkit-stream.log` lines |
|---|---|---|
| 17:48:40.084 | WebContent 1182 launch completed; reported duration 16.481856 s | 12–13 |
| 17:49:21.913 | GPU 1207 launch completed; reported duration 27.299961 s; initialization began | 179–187 |
| 17:49:31.278 → 33.655 | WebContent judged unresponsive, then responsive again | 415–416, 458–459 |
| 17:49:34.531 | GPU 1207 began creating its WebProcess connection | 475 |
| 17:49:37.529 | Safari requested GPU 1207 termination as Unresponsive | 541–546 |
| 17:49:41.115 | Replacement GPU 1262 completed launch in 1.728615 s | 652–670 |
| 17:49:44.139 | GPU 1262 judged unresponsive about 3.024 s later | 745–751 |
| 17:49:44.167 | GPU 1262 logged connection creation after the termination decision | 755 |

The evidence supports the same initialization/watchdog interaction seen in Run69. Proxy termination is not an exact OS-process death timestamp: GPU 1262 remained visible in later samples. This capture does not establish the full Run69 three-exit/crash-limit sequence.

A later reload produced a separate “Cannot open file” error for the file URL. `s85warm.tgz` records sandbox-extension creation failure and resource error -3001; its later finish/paint events belong to alternate error HTML, not the original fixture.

The HTTP control in `s85http.tgz` reused GPU 1314, already alive for about 20 minutes. It received HTTP 200 / 185 B and completed the page; `native-http-result.png` independently displays the heading and RGB blocks. All three 33000-pixel interiors match their expected primary color in `rgb-verification.json`. WebContent briefly became unresponsive and recovered, without GPU termination. This is warm GPU success, not a first-start fix.

The same HTTP content after confirmed ordinary Safari quit (`s85coldhttp.tgz`) used a new Safari 2139 and GPU 2178→2216→2244→2257→2267→2279. The first three GPU Unresponsive decisions occurred at 18:22:01.102, 06.895 and 23.768 UTC; `webkit-stream.log:1229–1231` explicitly records the 30-second crash limit and termination of all WebContent processes. HTTP 200 / 185 B had arrived at 18:22:02.228. The capture has no successful commit/finish/paint; a later screenshot, `native-fresh-http-result.png`, shows eventual recovery after the recorded restart/reload sequence. OS/disk caches were not cleared.

Warm remote navigation also succeeded: `s85remote.tgz` reused GPU 2998 and recorded no GPU unresponsive termination. Actual loadRequest→finish-load was about 37.1 seconds, with transient WebContent unresponsiveness that recovered. `native-remote-result.png` shows the IANA logo, article and multilingual table. Neither this time nor WebKit's internal paint timing is physical input-to-photon latency.

## Stack sampling is now available

`guest-uploads/s85stack.tgz` records successful ordinary-user `sample` of GPU 1314. The report is dated 18:05:39.762 UTC; the process launched at 17:50:52.680 UTC. Main-thread Mach-message receive and RemoteRenderingBackend semaphore waits are consistent with an idle service. There is no evidence of pending work or a driver-fence deadlock in that later one-second sample.

The explicitly instrumented `s85init.tgz` provides two valid initialization-related reports. GPU 2472 includes `InitializeGPUProcess`, logging/thread attributes and memory-pressure-handler setup. All 58 main-thread samples of GPU 2511 follow `createGPUConnectionToWebProcess → isOpusDecoderAvailable → registerOpusDecoderIfNeeded → AudioComponentFindNext → retrieveComponents → mergeServerRegistrations → NSKeyedUnarchiver`. The sampled work includes changing allocation/decoding leaves, not a proven permanently held lock. GPU 2511's report Date is about 10.4 seconds after Safari's termination decision; it proves that the still-alive process continued this work, not that all 58 samples occurred before the three-second deadline. GPU 2564 produced no sample text and logged a symbolicator failure despite exit 0, so it is not counted as a valid stack.

For comparison only, upstream WebKit starts a responsiveness timer before requesting GPU connection creation and stops it on the reply ([GPUProcessProxy.cpp at 47fa426](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebKit/UIProcess/GPU/GPUProcessProxy.cpp#L537)); its connection constructor queries codec capabilities including Opus before sending initialization information ([GPUConnectionToWebProcess.cpp](https://github.com/WebKit/WebKit/blob/47fa426c169f32f8fc3dedd2c28bd04af887713d/Source/WebKit/GPUProcess/GPUConnectionToWebProcess.cpp#L370)). This control-flow comparison has not been matched to the Apple binary and is not a substitute for the actual stack/time evidence.

## Measured CPU hotspot and candidate

The 120-second host profile aligned with `s85perf` contains 61,969 user-state samples, with no lost samples. Period-weighted PAC helpers account for 42.363% of all QEMU samples and 42.607% of the eight vCPU samples; translation/code generation is 2.359%, and 11.102% remains unresolved anonymous JIT. During the three-GPU failure interval, vCPU PAC remains 42.662%. These are whole-VM costs in the selected intervals, not the CPU cost of one GPU process or AudioComponent function. See `host-perf-summary.json` and `host-perf-symbols.json`.

This justified a default-off, bounded per-vCPU ComputePAC memo candidate, recorded in patch 0022. Full inputs, both key halves and algorithm are compared; PAC insertion and authentication remain unchanged. Independent oracle checks, cached bad-signature rejection and 2/4/8-vCPU checks passed. A separately rebuilt main binary also passed both cache states through the functional integration harness. Candidate microbenchmarks and their concurrency limits are in the candidate record; actual OS benefit remains for the Run86/87 comparison.

## Host NVIDIA dependency

Run84 stopped before QEMU launch because installed NVIDIA user libraries were 595.99.02 while the loaded module remained 595.91.07 after an unattended update. Matching official packages were extracted under `build/nvidia-runtime-595.91.07`, without changing global packages or reloading the module. NVML, the actual FirmwareSession GPU preflight, 256-value compute readback and 64×64 offscreen rendering all passed under the local library path. These are host-runtime results, not a new NVIDIA guest validation. See runtime evidence.

## Lifecycle and remaining work

The Apple-menu shutdown encountered Terminal's prompt for the temporary caffeinate process. Selecting its ordinary Terminate action allowed shutdown to finish. QMP recorded `guest=true/reason=guest-shutdown`, no host quit request, exit 0 and reaping. Raw `observation_completed=true` with `passed=false` preserves the unavailable-final-capture limitation. Full filesystem cleanliness remains unproven.

Next, compare the same promoted binary with PAC memo off/on using the same native profile and fresh Safari/HTTP consumer, preserving background-load and observation-window differences. First-load stability, complete filesystem cleanliness, broad Metal coverage and performance targets remain open.

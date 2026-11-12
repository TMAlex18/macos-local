# VirtIO sound: used-ring layout and HAL TX boundaries

Run118 establishes a concrete completion-visibility failure. The device and
the actual guest control queue use different physical locations for the used
ring. QEMU has published one completion at its configured location, while the
driver's translated internal used ring still has index0, equal to its private
last-used index. The fixed `handleEvent` condition therefore cannot dispatch
that completion through the observed queue path. Run119 then verifies the
predicted512-entry allocation/layout match and restores registration and
playback startup in one real boot; complete PCM fidelity remains false.

This finding comes from source-guarded translation of the actual queue,
sound object and backing descriptor. It is stronger than an inference from
an empty audio trace or current interrupt registers. It does not establish
historical interrupt delivery, the exact waiting-thread stack, allocator
reservation size or the explanation of every earlier boot.

## Empty-query success did not restore registration

Run117 used the 0026 binary `a03e9f…1c526`. Its inventory had
`AppleVirtIOSound !registered/!matched`, an empty CoreAudio device list and
`afplay` exit1 with `AudioQueueStart failed (-66680)`. No WAV existed, so PCM
comparison was unavailable, not a failed bit-accuracy test. The current
completed control buffer showed an empty JACK_INFO query answered NOT_SUPP.

0027 corrected valid empty JACK_INFO/CHMAP_INFO replies and short-response
used lengths. Forty actual indirect qtest queries and existing PCM consumers
passed on `1b41b3…278c1`. Run118 then supplies the real Apple consumer:

- `bdif-block-io.log:4517` records the successful empty-query branch for
  `JACK_INFO(start=0,count=0,size=24,total=0)`.
- The current response is `S_OK`, with used index1, head0 and length4.
- The 16:58:54–16:59:02 UTC inventory still shows an unregistered/unmatched
  driver and no CoreAudio device. No `afplay` or fresh Safari trial was run.

Thus the protocol repair executed, but success of that reply alone was
insufficient to complete registration in Run118. The old NOT_SUPP response
cannot be retained as the established sole cause.

## Actual mapping distinguishes the two used rings

The fixed AppleVirtIO source uses different expressions for the internal
virtual used pointer and the advertised physical used address:

| Consumer | Expression |
|---|---|
| Driver's internal used pointer | `align_up(availVA + 2*n + 6, 4096)` |
| Advertised physical used address | `align_up(availPA + 2, 4096)` |

For this actual 64-entry queue, available-ring placement is 4080 bytes into
a 4 KiB alignment region. The extra 132 bytes in the internal expression
cross an additional alignment boundary. The resulting physical locations
differ by **4096 bytes**.

| Observed field | Device used location | Driver internal used location |
|---|---:|---:|
| Used index | 1 | 0 |
| First used head | 0 | 0 |
| First used length | 4 | 0 |

The driver's private `lastUsedIdx` is0. Its fixed callback guard is
`internal_used.idx != lastUsedIdx`; the observed values make that condition
false. This proves present completion invisibility through the configured
consumer path. It does not claim that an interrupt handler was directly
observed executing at the inspection instant.

The translation used actual kernel-pmap runtime attributes and verified
code/UUID and slide guards. Five independent checks tie the result to the
same real object chain: runtime code to its exact physical guard,
descriptor/available addresses to QMP, sound's control-queue link back to the
identified queue, common provider identity, and backing-descriptor class and
backing pointer. Descriptor, available and internal-used addresses share one
verified 16 KiB leaf mapping; no physical-contiguity assumption is needed.

The runtime pointer mask is **47 bits**. A prior fixed-low48 masking rule was
invalid because PAC metadata affects bit47 in this instance. An earlier
boot-arguments-derived slide also failed code and UUID guards. Both rejected
observation assumptions remain in the private evidence; neither is evidence
that the target object was absent. Stable rereads were performed, without
claiming an atomic snapshot of the entire running guest.

## Allocation and interrupt boundaries

The actual backing descriptor declares capacity4614 bytes, requested
alignment1 and options`0x13`. Its internal used ring starts at offset5136,
**522 bytes beyond the declared capacity**. The software pagewalk establishes
the address being read. It does not measure the allocator's rounded reserved
extent or prove adjacent-object corruption. Numeric allocation options alone
also do not establish physical contiguity.

The queue/common-config and QMP views agree on size64 and enabled state.
MSI-X is enabled and unmasked with no pending bit in the retained read;
selected GIC interrupts are enabled and edge-triggered. These current,
non-atomic snapshots do not establish historical IRQ delivery. They do not
remove the separately established used-address mismatch: even a handler
invocation against the observed queue fields would fail its index guard.

## Matched HAL TX completion and producer boundaries (static)

The official27.0.1/26A434 ordinary-OS image contains
`/System/Library/Audio/Plug-Ins/HAL/AppleVirtIOSound.driver/Contents/MacOS/AppleVirtIOSound`,
bundle260. The321792-byte fat file has SHA256
`0479d38fe38f0898ca5c54d93774210ca5cdcd269d8b1aec76ffcb0c86413915`;
the108800-byte arm64e slice has SHA256
`3363781614e1e844040f1f45d016a0ca77e854ad605be7fc1f548f05937dab36`
and UUID`49F7C365-DA15-30BA-A9CA-E8DC7FE72BF6`. The bounded source contract and
function-window hashes remain in
`private/apple-sound-static-26A434/source-contract.json` under
`userland_hal_feedback`; raw Apple bytes are not reproduced here.

The selector8 TX wrapper routes completion through full-period and tail blocks.
Both blocks **ignore the incoming numeric result and latency values**. They
advance the completed cursor using the byte count captured for the submitted
period or tail, then permit retired-ring reclamation. The other located ARM
slice has the same numeric-value behavior. For this pinned output consumer,
`latency_bytes` crosses the kernel boundary but does not drive TX accounting
or the separate zero-timestamp arithmetic. Its numeric userland use is therefore
known to be absent in these blocks, rather than an unresolved use hypothesis.

Callback **arrival timing** still controls completion/cursor progress and
buffer reclamation. Static numeric-value independence does not exclude
producer/consumer scheduling effects. No live loaded-plugin UUID or actual
branch/timing trace was bound in this static task, and it is not a broad
HAL/CoreAudio audit.

`writeMixBlock` has a concrete static pre-TX rejection condition. It computes
requested bytes from frame count and ring bytes per frame, and returns0
without copying or advancing the write cursor when either condition holds:

- requested bytes exceed total ring capacity;
- requested bytes exceed `capacity + u32(completed_cursor - write_cursor)`.

This is an explicit source-level candidate for missing producer writes.
**No Run119–124 execution of that rejection branch, or causal link to a PCM
defect, has been observed.** The completed callback path and this producer
check must not be collapsed into a proven root cause.

The fixed ring configuration uses sample rate`F`, bytes per frame`B` and
`getpagesize()` value`P`:

```text
period_bytes = P * ceil(floor(F * B / 12) / P)
capacity     = 12 * period_bytes
```

The12period count is hardcoded; capacity/period are copied into SET_PARAMS.
For48kHz F32 stereo and16KiB pages, this predicts period32768B and capacity
393216B =49152frames ≈1.024s. These are **conditional static predictions**,
not measured Run124 ring parameters. In existing PCM traces,
`CONTROL.accepted=32768` is `S_OK` (`0x8000`), **not a recorded period size**;
the trace does not capture `buffer_bytes` or `period_bytes`.

The small plugin does not itself read the standard CoreAudio buffer-frame-size
or range properties. Their existence/writability in the unexamined
`ASDAudioDevice` base class is unresolved; even a writable property has not
been shown to change this ring formula or increase TX headroom. It might
instead affect request size or callback cadence. Further HAL extraction and
runtime probing are deferred while the known lifecycle race is repaired.

## Earlier boots and the next controlled profile

| Evidence | Established boundary |
|---|---|
| Run105 and later positive inventories | Guest audio registration has worked in earlier boots. |
| Run109 | Earlier single-stream initialization failed before the used-ring repair; it does not establish duplex necessity. |
| Runs108/114 | Complete reference PCM transfers exist in retained evidence. |
| Run116 | Audio registers, but phase discontinuities already occur at TX pop. |
| Run117 | Audio does not register; current empty-query response is NOT_SUPP. No direct used-address mapping was captured. |
| Run118 | Empty-query branch succeeds with S_OK; audio still does not register; actual used-address mismatch is verified. |
| Run119 | Actual512 layout matches and completion indices advance; registration/CoreAudio/afplay succeed, but full-reference PCM still fails. |

The fixed zero-count JACK path waits on transaction completion and checks
transaction `IOReturn`; it does not parse the raw sound response status in
that path, and it ignores the `copyResults` boolean. Completion and wake with
NOT_SUPP are therefore statically compatible with continued initialization.
This explains a compatibility possibility, not the exact execution history
of successful boots. The current finding also does not attribute Run117's
failure to 0026 or explain Run116's waveform discontinuities.

The next candidate is an optional **standard queue-size512 profile**, keeping
default64. Retained runtime evidence verifies no system mapper and allocator
flags9. Under that observed branch, the fixed allocator source sends a512-entry
capacity of16390 bytes (over16 KiB) through page-backed VM allocation, with an
expected rounded data extent of32768 bytes. A page-aligned base makes both
used formulas produce offset12288. These are source-supported expectations. Run119 later verifies the aligned
backing, declared capacity and used offset below; the allocator-rounded
reserved extent itself remains unmeasured.

0028 now implements the standard property with default64 and positive
power-of-two sizes up to1024; the installed runner exposes only explicit64/512
and requires the matching patch hash. Omitting the option preserves the old
arguments. Binary`8f0b58480ea310c04d0ed33f86aa3cba01d52c162a16f2cfc41db20a1ed57022`
passed actual default64/explicit64/512 device tests. In the512 profile, all
four advertised/selected queues,1027 control completions across all512 heads,
two ring-slot wraps, EVENT_IDX514 notifications/513 suppressions, used lengths,
canaries and production-argument4096B PCM with complete trace passed. The0027
query regression and installed preflight also passed.

Actual device validation
is separate from the real Apple allocator. Run119 explicitly selected512
from118 guest-shutdown storage, and the actual checks below now establish
layout/initialization progress beyond requested metadata. This remains
one observed boot, not a general queue-size fix: at2048 entries the two expressions differ
by4096 bytes even with a page-aligned base. No guest instruction patch or
physical-address rewrite is used.

## Run119 confirms the layout and initialization path

The17:40:04.661088 UTC source-guarded observation verifies actual queue0
size512, BMD capacity16390/alignment request1/options`0x13`/flags9, no system
mapper, and both backing VA and PA aligned to16 KiB. The internal used offset
is12288 and its translated physical location equals the device's used address.
The declared buffer now covers the used ring. Available, device-used,
internal-used and private-last-used indices all equal3, following the initial
JACK_INFO, PCM_INFO and CHMAP_INFO requests. Six independent code/object/address
oracles and stable queue-identity checks pass. The observation is bounded and
non-atomic; it does not prove historical interrupt delivery or allocated
reservation beyond declared capacity. Raw object and Apple-code material
remain private.

At17:43:13–17:43:36, AppleVirtIOSound is registered/matched, coreaudiod270 has
its user client, and CoreAudio exposes default48 kHz stereo output and the
configured input/system device. Plain `afplay`764 exits0. These actual consumer
results confirm the predicted layout/initialization path in this boot; they
do not establish physical input/output or waveform fidelity.

The complete96,000-frame PCM fixture still fails. The136,704-frame WAV has a
main fade-anchored extent of91,896 frames, two512-frame zero blocks and13
reference joins; after4,872 silent frames there is another512-frame block
(511 nonzero frames). All34 TX packets/134 accepted writes preserve these
contents from the earliest pop through populate/accepted and the entire
converted WAV. The selected main mapping uses6,004 omitted/876 reused reference
frames plus1,024 zeros, yielding a main extent4,104 frames shorter; periodicity
makes this a nonunique source history. Final END is valid and the original
2,227,936-byte trace prefix is unchanged. Registration restoration therefore
does not resolve the remaining PCM producer/timing/feedback question.

The ordinary first Safari GPU also still fails, while late exact RGB and
guest shutdown pass. A same-binary/queue512 runner-only vCPU-affinity0–7
control is running as Run120. The retained17:49:44.782399 placement snapshot shows all
last processors in the P-core set and allowed masks0–19. It does not establish
first-GPU execution on an E-core or a scheduler cause. No affinity was changed
during Run119; no global governor, nice or host-setting change is proposed.

The runner validation
now passes one actual diskless8CPU checksum consumer and five targeted offline
checks on the unchanged8f0b binary. Run120 has started with explicit
`--vcpu-affinity 0-7`: all eight startup vCPU masks are verified before initial
continuation, while29 other existing QEMU threads and controller masks remain
0–19. Startup verification is limited to those existing threads. The later
18:16:20 post-login snapshot now observes27 newly created Reims/metal threads
with masks0–7;24 surviving original non-vCPU threads and7 other new threads
still have0–19. No affinity was changed after startup. Renderer-thread mask
inheritance is therefore part of the actual Run120 effect: this is **not a
pure vCPU-only runtime comparison**. Actual residency, all future thread masks
and audio/Safari benefits remain separate. The
post-login evidence
retains individual thread identities and masks.

See actual layout,
Run119 application record
and PCM analysis.

## Run120 repeat preserves initialization but not PCM fidelity

The second actual512 layout again matches, with registered/default audio and
afplay0. Full-reference PCM still fails: a95,248-frame main extent has two
selected phase jumps272/480, net752 frames shorter. The137,216-frame WAV has
only outer silence around three exact reference regions, with no replacement
zero blocks or extra tail fragment. All34 packets/134 writes preserve those
contents from TX pop through the complete converted WAV, and END/prefix
verification passes. This is a different observed defect than119, not a
proved improvement attributable to affinity.

Run120's first Safari GPU still fails, despite two retained watchdog requests
and later173,400 exact RGB pixels. Its post-login snapshot proves renderer
threads also inherited masks0–7; it cannot isolate only vCPU placement. Guest
shutdown completed normally, while controller1 reflects missing final capture.
The Run120 application record
and [comparison report](M3-PAC-MEMO-APP-COMPARISON.md) retain the detailed PCM,
Safari, timing and inheritance limits. Run121 now uses an independent observer-policy
control with affinity omitted and the same binary/queue512; its initial
normal-proof stop branch is verified. It later completed two distinct full
PCM transfers while its first Safari GPU and late error page still failed.

## Run121 adds two complete PCM transfers; a single-stream profile is next

Run121 retains actual512 layout and registered/default audio. With automatic
boot-only capture stopped after5captures, one explicit manual pre-login layout
scan and no application-phase RAM scanning, both the first pre-Safari voice
and a post-Safari warm repeat contain the full96,000-frame reference at every
PCM boundary and in their finalized WAVs. The first voice has51packets/204writes
and the repeat33/129; both have zero outer samples. The final two-generation
trace has normal END, unchanged first/repeat prefixes and unchanged frozen
WAVs. Existing108/114 positives and119/120 failures remain valid; these results
do not prove observer causality or universal PCM fidelity. Safari still fails
its first GPU and displays a late error page without RGB.

The next standard runtime profile uses one output stream for the WAV backend,
while generic default2 remains unchanged. The earlier Run109 single-stream
failure occurred before the used-ring fix; it is not proof that the driver
requires duplex enumeration. Source WAV input capacity remains zero, so no
physical microphone claim follows from the two-stream inventory or the new
profile. Real single-stream layout, registration, PCM and Safari still require
independent consumer validation.

The runtime stream-count validation
now passes default2/explicit2/explicit1 actual QOM/config/direction checks,
invalid stream1 INFO/START rejection in the one-stream case, and131072B PCM
with complete trace in each512-queue profile. Five targeted offline checks
pass; omission preserves the old default2 arguments. The binary remains8f0b,
with no new QEMU patch or build. Native voice-list inspection was unavailable
because ptrace attachment was rejected; the WAV backend's zero input-voice
capacity is source evidence, not a claimed native-list observation.

Run122
has now started from121 guest-shutdown storage without an unclean override,
requesting one stream, queue512, boot-only and omitted affinity. The initial
normal proof stops RAM after5captures. Stream-count metadata remains a
configured expectation until actual QOM/guest validation; audio/application
benefit is still pending.

Run121 application
and separate repeat analysis
retain exact content and warm-history boundaries.

## Run122 verifies a real single-output consumer

The standard one-stream profile now works in an actual Apple boot: QOM count1,
guest`[0,1,0]`, queue512 layout MATCH, registered stream1, coreaudiod271 and
CoreAudio default48 kHz stereo output without input fields. Afplay0 follows.
This directly separates current single-output support from Run109's earlier
failure before the ring-layout repair. Physical input remains unsupported
and unclaimed.

Full PCM still fails: a94,136-frame main extent has two selected phase jumps
1384/480 and net1864 frames shorter, already at TX pop. All34packets/134writes
preserve that stream through the full137,216-frame WAV, with only outer silence;
the final prefix is unchanged and one160B END record closes the single-stream
trace. All five Safari GPUs fail, and the late frame is an error page.
Constructor-only shortening is offset by a later constructor start and must
not be reported as overall8-second startup improvement. Guest shutdown succeeds.

The Run122 application record
and [application/timing report](M3-PAC-MEMO-APP-COMPARISON.md) retain these limits.
The next candidate expands exact PAC memo capacity to16384, with explicitly
larger fixed arrays and clear costs; it is not an audio-layout change or an
established performance improvement. Run123 subsequently retained correct
single-output initialization but failed fullPCM/firstGPU despite higher lookup
hit ratio; its same-binary1024 follow-up is detailed in the comparison report.

## Related Run126 network-control observation: proof stops at device addresses

Run126 has a current host GET200 but guest curl7 and no default route. Its
retained en0 image is inactive without IPv4, but the MAC differs from the
configured VirtIO target, so this is not target-interface-state evidence.
No inventory helper, identity probe or target Safari ran.
The371-byte read in network state
shows CTRL queue2 size64, used at avail+16B versus a134B expected available
ring, hence physical overlap. One completed RX_ALLMULTI has usedlen1/ACK0.
RX/TX256 queues have4096B gaps and no corresponding physical overlap.

This is not yet the Run118 level of evidence: the network driver's internal
used VA was **not translated**, and ACK0/current ring state do not prove guest
consumption or historical IRQ delivery. The pinned26A434 network allocation
path reads the transport's announced u16queue size without clamping it to64
and uses the common AppleVirtIOQueue allocator, supporting a512-capacity test.
It does not establish Run126's actual internal mapping or a unique cause.

0032 now adds the optional standard `ctrl_queue_size` property with default64;
RX/TX256, feature/ACK handling and guest physical addresses are not rewritten.
Actual core checks
cover default/64/512 ACKs, wrap/SG/canaries,60B bidirectional RX/TX packets and
MQ rebuilds; vhost coverage is only advertisement/linkdown handshake. These
checks and targeted host/argv validation do not prove a macOS fix.

Run127's early device read selectsCTRL512, with4096B avail→used gaps and
no physical overlap in all three rings; RX/TX remains256. That initial CTRL
index0 snapshot does not prove connectivity. Later actual guest GET/PUT and
inventory succeed; target en1 is matched to the configured MAC and has active
IPv4/default routing, while the later CTRLused/last-avail reaches13. This is
current HTTP-chain recovery under the new profile, not a unique causal proof
or a full network test. Run127 subsequently completed its first matched
Safari GPU/client initialization4.081s after the UI request under the enabled
finite30s diagnostic, with no GPU watchdog but three WebProcess-unresponsive
notices. This is not original3s performance or both-site validation. Guest internal used mapping remains untranslated,
and the latest reused/header-only descriptor supplies no reliable ACK value.
The 0032 host check index
keeps5runner/2argv methods plus one focused metadata recheck distinct from
actual guest traffic. See the Run127 application record.
The Run126 application record
marks the target trial unavailable, not a failed timeout guard.

## Run118 lifecycle

`R118_KEEP` PID743 was terminated and waited before the17:25:17.450483 UTC
native capture, which contains the termination and `R118_KEEP_DONE` marker.
Guest-origin shutdown occurred at17:28:04.036371 UTC, followed by finalization
at17:28:05.058656. QEMU and the guest-files child exited0/reaped; no host quit
was requested and temporary aliases were removed. Controller service1/failed
reflects unavailable final capture after natural guest exit, with `errors=[]`.
Filesystem cleanliness remains unproved.

The existing trace decoder verifies a176-byte header-plus-normal-END trace,
with no PCM records. That closes the diagnostic trace; it does not create a
playback result. The application record
keeps audio initialization, unattempted playback/Safari and successful guest
shutdown separate.

## Evidence locators

The public derived record is Run118 initialization analysis.
It links the inventory, successful-query event, current response, MSI-X/GIC
snapshots and queue configuration. Raw Apple bytes, kernel-object images and
concrete addresses remain under `private/` in
`private/apple-sound-static-26A434/source-contract.json` and
`private/installed-boot/m3-sound-empty-info-memo1024-20261107-118/sound-queue-state/verified-mapping.json`.

Related retained records: Run117 audio analysis,
0027 actual validation,
Run116 PCM analysis
and the [application comparisons](M3-PAC-MEMO-APP-COMPARISON.md).

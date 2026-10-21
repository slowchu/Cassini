# Offline audio delivery add-on

Use this add-on to investigate pauses or stuttering in recorded audio. It
finds quiet sections and, when you provide both the generated audio and
playback timing logs, helps distinguish silence in the source from a gap in
delivery. Quiet audio alone cannot establish a playback fault. The main
analysis works on existing files; optional Windows tools can collect new
recordings. Use it when you need more detail than the core silence check.

This GPL-3.0-only add-on extracts the qualified Grandia audio arithmetic into
a game-independent process adapter. It imports no Grandia checkout, launcher,
live process observer or game data. Its optional NumPy dependency must already
be available to the configured Python; nothing is installed automatically.

The core invokes `adapter.py --request REQUEST.json --out FRESH_DIRECTORY`.
The JSON request contains `command: "audio-gaps"`, `arguments`, `profile` and
an absolute `profile_path`. The reply names `audio-delivery.json` using the
version1 adapter envelope. Successful analysis and valid scoring are separate:
packet gaps can produce a completed report with `scoring_valid: false`.

`arguments.input` is the existing process-loopback WAV. `reference` is the
generated PCM WAV, not an assumed waveform-aligned copy. Both must contain
uncompressed signed16 PCM at the profile's sample rate and channel count.
Without `delivery`, the tool reports sustained near-zero intervals at the
declared amplitude thresholds and explicitly leaves delivery timing unknown.

For delivery analysis, `delivery` names a manifest with schema
`saturn-kit-audio-delivery-v1`. These fields are exact file pins (`path`,
`bytes`, `sha256`), resolved relative to that manifest:

- `playback`: must identify `arguments.input`.
- `packets`: CSV with `file_frame`, `frames`, `flags`, `qpc_100ns`; the original
  recorder's additional numeric columns are retained and accepted.
- `capture_metadata`: JSON with target-process-tree scope, sample rate, frame
  and packet totals, discontinuity/timestamp-error totals and QPC frequency.
- `generated`: required for counter-aligned analysis; must identify `reference`.
- `supply`: optional original supply-observer CSV. `arguments.events` may
  supply this file explicitly; if both are given they must name the same file.
- `supply_metadata`: optional observer JSON with complete/pass status,
  frequency, observation count and an optional exact sample-file pin. Process
  identity must agree when capture or observer metadata declares a PID.

Every referenced file must be inside the profile's input roots. Inputs are
pinned before analysis and rechecked afterwards. Output is exclusive and
below its private output root. The profile's `audio` settings are:

```json
{
  "rate": 44100,
  "channels": 2,
  "thresholds_s16": [2, 16],
  "minimum_run_ms": 5,
  "secondary_minimum_run_ms": 20,
  "windows": {"scene": [1200, 1800]}
}
```

Those window numbers are synthetic examples, not Grandia checkpoints. CLI
`threshold_ms` overrides the primary minimum duration. Thresholds test maximum
absolute sample amplitude across all channels, including correct signed-minimum
handling. The minimum duration rounds upward to whole sample frames.

The supply CSV retains the existing bracketed `qpc_before/after`,
`samples_before/after`, `vblank_before/after`, `underruns_before/after`,
`dropped_before/after`, `vtime_before/after`, `marker_pointer_before/after`,
`marker`, and `same_values_across_read` fields. Declared VBlank windows select
the inner QPC/sample boundaries; non-atomic counter reads produce lower/upper
bounds rather than fabricated exact times. Without supply observations, a
profile may declare `qpc_windows_100ns`, or the complete delivery interval is
reported with generated/playback alignment unknown.

Packet timestamps use the recorder's100ns unit. Supply QPC uses the separately
declared positive frequency. Rational packet mapping is retained, including
fractional packet ends. Missing file frames, timestamp gaps and flagged packets
invalidate intersecting scoring windows. Missing time is never filled; quiet
intervals crossing a scored boundary are listed separately without clipping.

Near-zero playback, generated silence, counter changes and packet integrity
remain separate observations. Their coexistence does not prove causation or an
audible underrun. The report cannot prove physical device/headphone delivery,
complete gameplay/save equivalence or waveform alignment. The offline adapter
starts no capture process; optional acquisition needs a separate invocation.

`audio_math.py` retains the donor packet/quiet/counter algorithms with sample
rate and channel parameters. `adapter.py` replaces frozen Grandia paths,
scenario windows and historical receipt/hash constants with the admitted
profile/manifest. Exact donor paths, hashes and changes are recorded in
`provenance.json`; the complete GPLv3 text accompanies this directory.

## Optional Windows acquisition sources

`windows_process_audio.cpp` retains the existing bounded WASAPI process-loopback
capture implementation. Only its hard-coded Grandia path policy and CLI were
adapted to an explicit allowed image and private output root. It still captures
only the target process tree, has no desktop fallback, verifies the PID image,
rejects self-targeting, uses exclusive new files, and emits packet timestamps
and flags without filling missing time. Acquisition remains 44100Hz stereo16;
the analysis adapter can accept another declared signed16 rate, but this helper
does not claim configurable capture formats.

An existing supported x64 Windows C++ toolchain and SDK must supply
`audioclientactivationparams.h`, `ActivateAudioInterfaceAsync`, and the process
loopback interfaces. The existing build recipe used LLVM-MinGW plus Windows
SDK10.0.26100.0 headers (not bundled):

```text
clang++ -std=c++17 -O2 -Wall -Wextra -municode -static -idirafter SDK_UM_HEADERS windows_process_audio.cpp -o FRESH_TOOL.exe -lole32 -lmmdevapi -luuid
```

This is an explicit build instruction, not an automatic build step. The adapted
source passes one explicit compile with existing tools and synthetic guards;
live acquisition remains unqualified. Interface or
OS support failure is reported rather than falling back to whole-desktop audio.
No claim of support on older Windows versions follows from having the headers.

The helper's explicit interface is:

```text
FRESH_TOOL.exe PID EXPECTED_IMAGE SECONDS PRIVATE_ROOT FRESH_OUTPUT_PREFIX
FRESH_TOOL.exe --self-check PRIVATE_ROOT FRESH_SYNTHETIC_PREFIX
```

The self-check creates only a tiny synthetic WAV and checks duration, image,
output and self-target guards; it activates no audio interface. This check
passed on the adapted C++ source. The output root must already
exist; the prefix's resolved parent must stay inside it.

`windows_capture.py` is the optional profile driver. Its default creates only a
pinned plan. It executes the already built helper only with `--execute`, uses a
bounded watchdog, and never stops the game process. Set profile `audio_capture`
to exact `binary` and `helper` pins inside its input roots, then invoke:

```text
python -B windows_capture.py --profile PRIVATE_PROFILE --pid OWNED_PID --seconds 30 --out FRESH_PRIVATE_CHILD
```

An executing invocation additionally supplies `--execute`. This driver requires
explicit ownership of the target; the profile is not permission to attach to an
arbitrary process.

`windows_audio_events.py` retains the original four-counter observer, exact raw
symbol checks, finite interval/deadline, short read-only metadata reads and event
brackets. `windows_host.py` contains its existing x64 PE and process-read helpers.
They do not suspend the target, write memory, call SDL, or read guest RAM. The
required native binary must retain the configured raw audio-counter/VBlank/host
marker symbols; stripped or incompatible builds fail. No emulator build is
assumed to expose those native symbols.

Its per-game `audio_events` profile names exact `binary`, `nm` and `host_helper`
pins, an explicit boolean `recovery_pad` counter interpretation, optional exact
`symbols` mapping and `marker_labels`. The target, symbolizer and local helper
are checked against those pins. The CLI is:

```text
python -B windows_audio_events.py --profile PRIVATE_PROFILE --pid OWNED_PID --first 100 --last 500 --out FRESH_PRIVATE_CHILD
python -B windows_audio_events.py --self-check
```

The Python self-check uses only synthetic PE/symbol/counter objects and never
opens a process. Live observation is not run as part of kit tests. The
four-counter event CSV is not the six-counter generated-supply CSV: it cannot
by itself establish generated sample alignment. The retained supply-observation
contract described above remains necessary for that analysis.

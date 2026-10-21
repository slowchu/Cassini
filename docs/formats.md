<!-- SPDX-License-Identifier: MIT -->
# Neutral formats, version 1

All manifests are UTF-8 JSON objects. `schema` is an exact version discriminator;
unknown versions fail rather than being interpreted as a compatible dump.
Addresses and decoded register values are integers in JSON. A pin is
`{"path":"relative-or-absolute", "bytes":123, "sha256":"64 hex digits"}`.
Referenced inputs must remain inside the selected profile's allowed roots.
For the current Python executable, generate its pin from the package root:

```text
python -B -c "import json,sys; from saturn_kit.api import pin; print(json.dumps(pin(sys.executable)))"
```

On Windows Store Python this hashes the executable Windows actually loaded,
because the app execution alias cannot be opened as a normal file. Use the
returned path in the profile and include its parent in input_roots. File pins
and data-path containment checks remain mandatory. This addresses the
[Store alias behavior documented by CPython](https://github.com/python/cpython/issues/104336).

The profile output_root must lie inside input_roots; validation fails at
startup otherwise. This admits generated reports/images for later comparisons.
Audio threshold precedence is explicit argument, then the optional audio
profile's minimum_run_ms, then its5ms default. The MIT exact-zero fallback
without an audio add-on uses20ms. Front ends do not inject an override.

## Captures and dumps

`saturn-kit-capture-v1` and `saturn-kit-dump-v1` share this envelope:

```json
{
  "schema": "saturn-kit-capture-v1",
  "game": "example-game",
  "provenance": {"origin":"native", "debug_derived":false, "matched_moment":null},
  "state": {"vdp1":{}, "vdp2":{}},
  "images": {},
  "buffers": {},
  "limitations": []
}
```

`state` holds represented decoded fields; absent information is not fabricated.
Images map unique names to pins for lossless PNGs. Buffers map component names
to pins for opaque raw data, optionally with `byte_order`, `element_bits`,
`width`, `height`, `role` and `phase` metadata. The neutral core verifies buffer
identities but does not invent chip decoding for them. Dump producers or a
game's optional decoder supply the state. Raw legacy dumps use the optional
profile adapter and are not silently assumed to have this schema.

Relative referenced paths resolve against the manifest location. Inspection
verifies each reference, copies PNGs into the fresh result folder, retains raw
buffer references and writes `saturn-kit-inspection-v1`. Physical DISPLAY,
current DRAW and an interpolated frame are different roles; a static
reconstruction cannot prove a past displayed frame. Preserve original capture
identity, scheduler/phase, unavailable fields and debug derivation.

## Results

`saturn-kit-result-v1` is the compact CLI/MCP response with `command`, `output`,
`report` and optional image paths. It embeds no raw memories or game images.
The report is private local evidence, not a safe public export.

`saturn-kit-diff-v1` lists every represented changed/added/missing state field
using escaped JSON Pointer paths. It compares every image's pixels and full
extents; different extents count added/missing pixels. A supplied premise JSON
is retained with its identity, and `conformance_claim` remains false because
the kit cannot verify gameplay equivalence from file names or dimensions.

`saturn-kit-hw-report-v1` recognizes the terminal-count records `reg`, `cpu-reg`,
`area`, `cpu-area`, `call`, `cpu-hle-entry` and `watch`. It indexes the
complete key preceding the final unsigned decimal count. Repeated keys within
one file or across input files are rejected: use one final snapshot or separate
reports per run. Other log lines contribute event-prefix frequencies only.
Global and per-CPU counters overlap. No CPU time,
per-frame distribution or optimization benefit is inferred from counts.

Save metadata, PCM-silence candidates, planned jobs and failures have
separate `saturn-kit-saves-v1`, `saturn-kit-audio-gaps-v1`,
`saturn-kit-job-plan-v1` and `saturn-kit-failure-v1` envelopes. Their reports
explicitly mark unproved checksum/delivery/execution claims.

## Optional execution and delivery

An adapter request is `{command, arguments, profile, profile_path}`. The
profile is the launch-selected snapshot; profile_path is only the relative
path base. All arguments identifying inputs are resolved by the core before
launch. Optional adapters emit a fresh report through a versioned envelope;
the core refuses a report outside its fresh result folder.

Control returns `saturn-kit-control-result-v1`, pointing to
`saturn-kit-control-session-v1`: executed/pass status, bounded frame proof,
actual commands, source pins, output pins, wall-cap result and owned-process
closure. Original recipes use `saturn-kit-original-recipe-v1` with a required
boolean debug_derived and finite steps `{command,args}`. Allowed commands are
pad/run/capture/save_state/status; see the control add-on example for exact
argument shapes. State export is evidence, never permission to import it.

Audio delivery manifests use `saturn-kit-audio-delivery-v1`, containing pins
for playback, packets, capture_metadata and optional generated/supply/
supply_metadata. Counter alignment requires generated PCM identity. Its
analysis report is `saturn-kit-audio-delivery-analysis-v1`; scoring validity,
generated quiet, playback quiet and delivery evidence are separate fields.
See the audio add-on for CSV/time units and uncertainty rules.

## Name compatibility

Cassini 0.2.1 keeps `saturn_kit`, the version-1 `saturn-kit-*` schemas and
`saturn-*` skill names. `cassini.py` is the preferred launcher; `saturn-kit.py`
remains an alias. A future naming migration must accept existing formats and
provide a deprecation period before removing these entry points. Renaming a
schema field without changing its meaning would needlessly break saved reports.

Explicit saves `to: native|ymir` uses the optional save adapter. Outputs and
the retained BUP envelope are pinned in saves-v1; unsupported opaque metadata
normalization and absent checksum/progress validation remain explicit.

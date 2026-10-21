# Optional live control add-on

Use this add-on when you want to repeat a recorded route in a native player
or collect captures from a compatible research build of Ymir. It starts a
separate test process with copies of your save and emulator settings, runs a
limited sequence of inputs, then closes it. It produces a plan by default;
running the plan requires explicit permission through --execute. You do not
need this add-on to inspect files you already captured.

This GPL-3.0-only add-on runs without a Grandia checkout. It includes the
qualified filesystem bridge client as an exact source copy and a new finite
controller. Python 3.11 or later is required. Pillow is needed only to export
PNG from Ymir's original PPM capture; nothing is installed automatically.

`ymir_bridge_client.py` is also a standalone acknowledged bridge client. Its
CLI takes `--root`, an optional session/generation guard, a command and JSON
arguments. It retains unique request files and never automatically resends a
timed-out command. The full protocol client is available for agents; authority
to use a particular command remains the caller's responsibility. The packaged
live controller deliberately permits only pads, bounded runs, status, capture,
state **export**, and a final pad release. It never imports a state or writes RAM.

Run `adapter.py --request REQUEST.json --out FRESH`. The request contains
`command` (`replay` or `capture-original`), `arguments`, `profile` (the trusted
startup snapshot) and `profile_path` (only the base for relative paths). The
profile file itself is never opened by the controller. `execute` defaults to
false and produces a validated plan. True executes the selected pinned player.
The result is `saturn-kit-control-result-v1` with a private session-report path;
it does not embed game data in stdout. Raw captures, saves, logs and exported
states stay in the fresh private output folder.

Every file pin has `path`, `bytes`, and `sha256`. Input/data paths must fall
inside the profile's `input_roots`; output must be a new child below
`output_root`. Relative paths/pins resolve against `profile_path`'s parent.
Executables are separately pinned trusted profile settings. All source inputs
are rechecked after execution; saves are copied before a process starts. Use
immutable save-library seeds labelled `disposable_seed: true`, never a live
owner save. There is no software installation, shell command, automatic retry
or background service.

`profile.example.json` shows the complete supported shape. Its zero-size/zero
hash rows and placeholder bridge identity are intentionally unusable; replace
them from your own admitted tools and private inputs. It supplies no game data.

The profile's `control` object has the pinned `python`/`adapter` used by the
core, optional `source_pins`, an outer `timeout_seconds` (default 300, at most
600), and these operation settings:

- `native`: `player` pin, `arguments` list, optional `disc` pin, `files` pins,
  optional `save` pin plus `disposable_seed: true`, `capture_frames` (at most
  64 sorted unique integers), and `timeout_seconds`. `replay` arguments contain
  `recording` and a positive `frames` bound up to 1,000,000. Ordinary startup
  flags are restricted to cue, clock, scale, multitap, task addresses, headless,
  unpaced, strict-unseeded and SMC logging off. The controller appends isolated
  save/settings/output paths, `--input @FILE`, `--vblanks`, fast replay, shots
  and dumps. Successful execution requires exit zero, the runtime's exact
  VBlank-limit completion marker and all requested shot/dump files.
- `original`: `player` pin, `arguments` list, optional `disc` and `media` pins,
  `launch_files` pins, `files` entries (`pin`, relative `target`, optional
  `template: true`), `save` (`pin`, relative `target`, `disposable_seed: true`),
  exact `bridge_build_identity` object, `frame_limit`, `timeout_seconds`, and
  `request_timeout_seconds` (at most 130). The research bridge build is required;
  stock Ymir is not assumed compatible. A CUE's FILE dependencies must be
  individually pinned in native `files` or original `media`.

Complete launch-argument tokens `{profile}`, `{session}` and `{disc}` expand
to private paths. An optional first argument can name a separately pinned
launcher script, useful for synthetic protocol fixtures. No arbitrary switches
are accepted. Original launch requires explicit `--profile`, `--automation`
and `--paused`; optional switches are `--disc`, `--debug`, and `--fast-forward`.
Original profile templates may also use `{save}`. Profile files normally
include Ymir.toml, BIOS and SMPC seed state at their normal relative locations.
None of these private files are distributed in the kit.

Original Ymir.toml must select the new internal-save copy, disable per-game
internal backup names, leave every PathOverride empty, and have no cartridge
with empty cartridge image paths. Any nonempty IPL/CD-ROM override must point
to a copied profile file. The audited Ymir settings/profile contract places
settings, RTC/SMPC persistence, savestates, dumps and screenshots under the
explicit fresh profile in this configuration. `--user` and aliases that could
override that selection are refused.

An original recipe is external JSON:

```json
{"schema":"saturn-kit-original-recipe-v1","debug_derived":false,
 "provenance":{"origin":"your immutable recording or save"},
 "steps":[{"command":"pad","args":{"port":1,"buttons":["Start"]}},
          {"command":"run","args":{"frames":30}},
          {"command":"capture","args":{"name":"checkpoint"}},
          {"command":"save_state","args":{"name":"checkpoint"}}]}
```

There are at most 1,000 steps, 36,000 frames per run, and the profile's total
frame bound. All replies must match the acknowledged session/build/generation;
runs must complete exactly their requested frames. Recipe-derived provenance
is retained. A debug-derived result alone is never a normal-play expectation
or a confirmed game bug. Native and original frame numbers are separate clocks.

Both players have a wall watchdog. Windows uses an owned kill-on-close job;
POSIX uses an owned process group. Cleanup touches only that child tree. The
outer adapter timeout must exceed the child cap by at least 10 seconds.
Original capture ends by closing the owned process after the recipe; this is
recorded separately from a natural exit. This extraction has synthetic process
and protocol tests; new live-game/other-game integration requires its own check.
Windows job cleanup also applies if the adapter exits abruptly. On POSIX an
external SIGKILL of the adapter can bypass its watchdog/finally cleanup; do not
treat the untested POSIX path as qualified crash-containment behavior.

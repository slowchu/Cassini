<!-- SPDX-License-Identifier: MIT -->
# Cassini

Cassini is a toolkit for looking inside Sega Saturn games. It reads what an
emulator or a recompiled port can save (video memory, captures, hardware logs,
saves and audio recordings) and turns it into images, reports and
side-by-side comparisons that you, or a coding agent, can browse and check.

You don't need to be building a recompiled game. With an ordinary copy of the
[Ymir](https://github.com/StrikerX3/Ymir) emulator you can already pull out
every texture a scene uses.

## What it can do

| Task | Works with just an emulator? |
| --- | --- |
| **View and export textures:** every texture the sprite and polygon chip (VDP1) is using, with its memory address, saved as PNGs with a page to browse them. | **Yes.** Ymir's *Dump all memory*, or any emulator that can save video memory. |
| **Find silence in audio:** stretches of exact digital silence in a recording. | **Yes.** Any 16-bit PCM WAV. |
| **Read and copy saves:** list the entries in a save file and copy them to the native player's format or Ymir's. | **Yes, for `.BUP` files.** Copying into Ymir needs a converter you build. Raw backup-RAM images aren't read. |
| **Compare two frames:** every changed drawing command or video setting, plus a pixel-by-pixel image difference. | **Not yet with stock emulators.** It needs the native player's dumps, the Ymir research bridge, or an emulator that exports Cassini's [neutral capture format](docs/formats.md). |
| **Summarize hardware access:** which chip registers and memory areas a play session touches, and how often. | **No.** It reads the hardware log a native recompiled player writes. |
| **Replay a recorded route** in a native player, collecting screenshots and dumps. | **No.** It needs a native recompiled player. |
| **Capture the original game** in Ymir by script, to compare against a port. | **No.** It needs a research build of Ymir with the automation bridge. |
| **Work with a coding agent,** as an MCP server or a Claude Code plugin. | **Yes.** |

## Before you start

You need Python 3.11 or later with [Pillow](https://pypi.org/project/Pillow/)
10 or later. Detailed audio analysis also needs NumPy. Cassini never installs
anything, downloads anything or changes your game, emulator or saves. It only
reads its inputs and writes each result into a new folder.

## Try it without a game

From this folder, run:

```text
python -B examples/demo.py --out ../cassini-demo
```

This makes two small made-up captures, compares their drawing commands and
pixels, and summarizes a made-up hardware log, all inside the new
`../cassini-demo` folder:

| Before | After | Pixel difference |
| --- | --- | --- |
| ![Synthetic rectangle before movement](examples/before.png) | ![Synthetic rectangle after movement](examples/after.png) | ![Synthetic whole-image pixel difference](examples/diff.png) |

```text
State: /vdp1/commands/0/x changed from 32 to 48
Image: 1024 different pixels; no missing or added pixels
Hardware log: 2 counter keys; no CPU-time inference
```

The shapes are hand-made: the demo doesn't emulate the Saturn or run a real
game. Open `demo-summary.json` in the output folder to see every result.

## Export textures from Ymir

1. In Ymir, pause on the scene you want.
2. Choose **Debug > Dump all memory**.
3. For exact colours, also choose **Debug > Open memory viewer**, pick the
   **VDP2 registers** region and press **Dump**. Without this file Cassini
   assumes the most common settings and says so in its report.

Both go to Ymir's dumps folder; **Settings > General** shows where it is. Then
make a working folder with a `profile.json` like this, using forward slashes
and your own dumps folder:

```json
{
  "schema": "saturn-kit-profile-v1",
  "game": "my-game",
  "input_roots": [".", "C:/Users/you/AppData/Roaming/StrikerX3/Ymir/dumps"],
  "output_root": "outputs"
}
```

From the `addons/gpl-renderer` folder, run this with full paths and a new
output name each time:

```text
python -B -m saturn_graphics --profile C:/cassini-work/profile.json textures --ymir-dumps C:/Users/you/AppData/Roaming/StrikerX3/Ymir/dumps --out C:/cassini-work/outputs/first-scene
```

Open `index.html` in the output folder to browse the textures. Each one also
has a PNG, its raw bytes and an entry in `textures.json` with its address,
size, colour mode and the drawing commands that use it. A texture's file name
comes from its contents, so it stays the same wherever the game loads it,
which is what a texture-replacement pack would match on. The images are the
game's own graphics: keep them private.

Other emulators work too if they can save VDP1 VRAM and colour RAM, and ideally
VDP2's registers. See
[View and export textures from an emulator](addons/gpl-renderer/README.md#view-and-export-textures-from-an-emulator).

## Profiles

Every command takes a profile: a small JSON file naming the folders Cassini
may read (`input_roots`) and where it writes (`output_root`, which must be
inside one of the input roots). Relative paths in a profile start from the
profile's own folder, and every run writes into a new folder below
`output_root`. Keep filled profiles, captures and results outside this folder.

[`profiles/neutral.example.json`](profiles/neutral.example.json) is the
smallest working profile. [`profiles/grandia.example.json`](profiles/grandia.example.json)
shows how a full game integration pins its Python, add-ons and tools.

## Commands

Run `python -B cassini.py --help` for the full list. The same operations are
available as MCP tools.

| Command | What it does |
| --- | --- |
| `inspect` | Reads a capture or dump and reports its drawing commands and video settings. Raw dumps and Ymir states need the [renderer add-on](addons/gpl-renderer/README.md). |
| `diff` | Compares two inspections: every changed, added or missing field, and every pixel. |
| `hw-report` | Counts register and memory-area accesses in hardware logs. Counts are not CPU time. |
| `saves` | Lists the entries in a `.BUP` or native save, or copies them into a new file with `--to`. |
| `audio-gaps` | Finds digital silence in a WAV, or analyses playback delivery with the audio add-on. |
| `replay` | Plans a limited replay in a native player. It runs only with `--execute`. |
| `capture-original` | Plans a scripted capture through the Ymir research bridge. It runs only with `--execute`. |

For example, with the demo's profile:

```text
python -B cassini.py inspect --profile ../cassini-demo/profile.json --input inputs/before.json --out results/another-inspection
```

## Optional add-ons

The MIT core handles Cassini's neutral captures, hardware logs, `.BUP` saves
and WAV files. The add-ons, under the GPL, read other formats or start other
programs:

- [Graphics (`gpl-renderer`)](addons/gpl-renderer/README.md): decodes raw video
  memory, renders separate screen layers and exports textures.
- [Live control (`gpl-control`)](addons/gpl-control/README.md): replays
  recorded routes in a native player or a Ymir research build.
- [Audio delivery (`gpl-audio`)](addons/gpl-audio/README.md): separates silence
  in the game's own audio from gaps in playback, with optional Windows
  recording tools.
- [Save transport (`gpl-saves`)](addons/gpl-saves/README.md): converts saves
  between `.BUP`, native and Ymir formats.

## For emulator developers

If your emulator writes the [neutral capture format](docs/formats.md) (decoded
VDP1 and VDP2 state plus lossless PNGs), Cassini's `inspect` and `diff` work on
it with no add-on. That lets you compare your output with another emulator's,
or with a recompiled port, field by field and pixel by pixel.

## Connect an agent

Point an MCP client at the server, using absolute paths:

```text
python -B /path/to/Cassini/cassini.py mcp --profile /path/to/private/profile.json
```

The server keeps the profile it started with; restart it to change games or
permissions. This folder is also a Claude Code plugin: load it with
`--plugin-dir` pointing here. Its `.mcp.json` reads your profile's path from
the `CASSINI_PROFILE` environment variable. [AGENTS.md](AGENTS.md) describes
the same workflows for Codex and other agents, and `skills/` holds short
step-by-step recipes. No global agent settings are changed.

## What the results can and can't tell you

- A difference report covers every recorded field and every pixel, but two
  captures with the same scene name or frame number aren't necessarily the same
  moment of gameplay.
- The frame being drawn, the frame on screen and a rebuilt picture are
  different things, and reports keep them apart.
- Hardware access counts show what was touched, not how long it took.
- Silence in a recording doesn't prove the audio dropped out: the game may have
  been silent on purpose.
- Copying a save doesn't check that it is valid for the game.

## Safety

- Inputs are only read, never changed. The core commands also check that an
  input didn't change while they were reading it.
- Each run writes into a new folder; existing results are never overwritten.
- Replay and capture only produce a plan unless you add `--execute`. Then
  they run a pinned player on copies of your save and emulator settings, with
  frame and time limits, and close only the processes they started. They never
  write into game memory or load a save state.
- Keep game data, BIOS files, saves, states, captures and recordings private.
  None are included here.

## Licences and checks

The core and its workflows are MIT. The add-ons are GPL and keep their
original authors' licences and notices. Game data, BIOS files, saves, captures
and compiled players or emulators are not included. See
[licensing](docs/licensing.md), [inventory](docs/inventory.md) and
[architecture](docs/architecture.md), and review the GPL source obligations
before redistributing an add-on.

To run the made-up-data tests and check every shipped file against its
recorded licence and hash:

```text
python -B -m unittest discover -s tests -v
python -B check_provenance.py
```

## Upgrading from saturn-kit

Cassini was called saturn-kit. The `saturn_kit` module, `saturn-kit-*` schemas,
`saturn-*` skill names and `saturn-kit.py` launcher still work. Plugin
configurations must rename `SATURN_KIT_PROFILE` to `CASSINI_PROFILE` and
restart. Pinned source and program hashes in a profile must match your
installed copy.

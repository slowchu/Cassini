<!-- SPDX-License-Identifier: MIT -->
# Cassini

A reusable Saturn recomp agent kit, named after the Cassini space probe.

Python CLI, typed stdio MCP server and five workflows for Saturn recomp
investigations. Version0.2.1 packages the existing graphics inspector, Ymir bridge
client, finite replay/capture controller, save transport and audio delivery
analysis into optional source add-ons. Running them needs no Grandia
reconstruction checkout. Grandia is the first validated profile; another
game's real integration remains unqualified.

The MIT core handles neutral JSON/PNG inspection, structured/pixel diffs and
hardware/guest reports. GPL add-ons retain existing qualified algorithms and
production renderer sources, communicating through process/JSON boundaries.
Binaries, game data and private profiles are not shipped.

Use Python3.11+ and an existing Pillow installation. Optional audio analysis
also needs existing NumPy. Nothing automatically installs packages, enables
plugins, configures clients or builds utilities.

```text
python -B C:/Cassini/cassini.py --help
python -B C:/Cassini/cassini.py inspect --profile PROFILE --input CAPTURE --out FRESH
python -B C:/Cassini/cassini.py diff --profile PROFILE --before INSPECTION_A --after INSPECTION_B --out FRESH
python -B C:/Cassini/cassini.py hw-report --profile PROFILE --inputs CLOSED_HW_LOG CLOSED_GUEST_LOG --out FRESH
python -B C:/Cassini/cassini.py replay --profile PROFILE --recording RECORDING --frames 600 --out FRESH
python -B C:/Cassini/cassini.py capture-original --profile PROFILE --recipe RECIPE_JSON --out FRESH
python -B C:/Cassini/cassini.py saves --profile PROFILE --input BUP_FILE --to native --out FRESH
python -B C:/Cassini/cassini.py audio-gaps --profile PROFILE --input PLAYBACK_WAV --reference GENERATED_WAV --delivery DELIVERY_JSON --out FRESH
python -B C:/Cassini/cassini.py mcp --profile PROFILE
```

Replay/capture default to plans. Explicit `--execute` runs the pinned control
adapter with finite frame/wall bounds and disposable save/profile copies.
Never configure an active save as its seed. The controller owns and closes
its child processes and refuses RAM writes/state import. One native and one
original Grandia 600-frame run passed through the delivered MCP server, with
capture/export and process closure. This verifies the bounded mechanism;
longer routes and other games require their own checks.

`PROFILE` is trusted local JSON with `schema: saturn-kit-profile-v1`, a
`game` identity, allowed `input_roots`, and an `output_root`. Relative
paths resolve against its directory. Every result is a new child below the
output root; existing folders are refused. Declare output_root inside an
input_roots entry so generated reports/images can be read for later comparisons;
profiles that omit this are refused at startup. Keep filled profiles, raw dumps,
media, saves, recordings and outputs outside this package.

The profile supplies exact source/executable pins and permissions.
`allow_external_tools: true` explicitly enables those local tools; captures
cannot supply commands. MCP retains its launch-selected profile in memory and
refuses file changes or a different per-call profile. Restart deliberately to
change its game or permissions.

See [profiles](profiles/grandia.example.json), [inventory](docs/inventory.md),
[formats](docs/formats.md), [architecture](docs/architecture.md),
[licensing](docs/licensing.md) and each add-on README for its contract.
Offline rendering uses a pinned production harness; original states need a
compatible pinned reader and paired capture. Explicit build recipes are
provided; external Ymir sources remain necessary for its utilities.

This directory is also a Claude Code plugin (`--plugin-dir C:/Cassini`).
Its `.mcp.json` requires an existing Python and the private
`CASSINI_PROFILE` environment variable. Existing plugin configurations using
`SATURN_KIT_PROFILE` must rename that variable to `CASSINI_PROFILE`; the old
variable is no longer read. Update existing MCP client launch paths/server
keys to Cassini and restart the client. `AGENTS.md` gives equivalent
recipes for other agents. No global configuration is edited.

Run `python -B -m unittest discover -s tests -v` from the package. Shipped
fixtures are generated synthetically. Actual Grandia captures/results stay
private. Diffs cover whole images and represented fields; they do not prove
matched gameplay. Save transport does not infer progress or validate game
checksums. Quiet audio alone does not prove a delivery fault.

`python -B check_provenance.py` verifies every shipped file against
`provenance.json`, including licences and donor identities. Check exact
grants and corresponding-source obligations before redistributing add-ons.

Cassini keeps the existing `saturn_kit` Python module, `saturn-kit-*` data
schemas and `saturn-*` skill names for compatibility. `saturn-kit.py` remains
an alias for `cassini.py`. Copyright and third-party notices retain their
original names. Filled profiles must reference the current folder and pins.

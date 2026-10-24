# Optional GPL Saturn graphics tools

Use this add-on to investigate a graphics problem in a saved Saturn capture.
It shows the drawing commands and video settings, renders separate layers,
and compares the results so you can see where an image changed. You need it
for supported native dumps or Ymir states; comparing already-exported neutral
captures and PNGs works with the core tools. Rendering needs a compatible
runtime, and Ymir states also need a compatible reader.

## View and export textures from an emulator

`textures` finds every texture VDP1's command list uses and shows its VRAM
address, size and colour mode. It saves each texture as a PNG with its raw
bytes, and writes `index.html`, a self-contained page showing them all. It needs
no recompiled game, renderer build or reader: an emulator's saved video memory
is enough.

With Ymir, pause on the scene, then choose **Debug > Dump all memory**. For
exact colours, also choose **Debug > Open memory viewer**, pick the
**VDP2 registers** region and press **Dump**. Both go to Ymir's dumps folder,
shown in Settings > General. Without the registers file, common settings are
assumed and the report says so. Then, from this add-on directory, with
Python 3.11+ and Pillow:

```text
python -B -m saturn_graphics --profile ABS_PROFILE textures --ymir-dumps ABS_YMIR_DUMPS_FOLDER --out ABS_FRESH_OUTPUT
```

From another emulator, save VDP1 VRAM (512 KiB), colour RAM (4 KiB) and, if it
can, VDP2's registers (512 bytes). Add `--swap-bytes` if it saves 16-bit words
little-endian:

```text
python -B -m saturn_graphics --profile ABS_PROFILE textures --vdp1-vram ABS_VRAM --cram ABS_CRAM [--vdp2-registers ABS_REGISTERS] [--swap-bytes] --out ABS_FRESH_OUTPUT
```

Native dumps and the other inspection inputs (`--native`, `--original-fixture`,
`--ymir-state`) work too. The profile only names the folders inputs may come
from and where outputs go:

```json
{"schema": "saturn-kit-profile-v1", "game": "my-game", "input_roots": ["ABS_YMIR_DUMPS_FOLDER"], "output_root": "ABS_OUTPUT_FOLDER"}
```

Each PNG is named by two hashes: the texture's bytes, then the colours it was
drawn with. A texture keeps its name wherever the game loads it in VRAM, in any
capture, which is what a texture-replacement pack would look it up by. Colours
follow the production renderer's VDP1 texel and VDP2 sprite rules, without
Gouraud shading, colour calculation, priority or flips. When a texture's
current colours are all transparent, the page shows its texel values in grey
instead. VDP2 background cells and bitmaps are not exported yet. The images are
the game's own graphics: keep them private.

## Inside this add-on

This add-on contains the qualified Python inspector, its retained VDP1 decoder,
Saturn register map, original-state reader source and complete small production
compositor source closure. It lives in `addons/gpl-renderer`; the old
`addons/gpl-grandia` forwarding path has been removed. Update profile pins when
updating the kit; the core verifies `graphics.source_pins` before launching
the adapter. No Grandia reconstruction checkout or game build profile is
required.

The MIT core calls `adapter.py --request ABS_JSON --out ABS_FRESH_DIRECTORY`.
Requests contain `command`, `arguments`, `profile` and optionally `profile_path`.
Relative profile paths resolve against that explicit profile file, never cwd.
Inspection returns a pinned `neutral_capture` in `saturn-kit-capture-v1` format.
The original backend report is retained separately; backend `diff` still accepts
its own raw inspection reports. Inputs/outputs stay in configured roots, existing
outputs are preserved, and utility execution uses explicit pinned receipts.

`graphics` needs pinned `python`, `adapter`, `source_pins`, `harness` and, for an
original state, `reader_build`. Pin all shipped Python modules and selected
production sources. No `reconstruction` setting is used. See `examples/` for
neutral utility receipts. Paths in a receipt may be receipt-relative. To reuse
an admitted utility, preserve the original receipt and add a portable receipt
recording its size/hash, exact unchanged executable and matching shipped source
identities. This does not qualify another executable automatically.

No executable, game code, BIOS, disc, asset, save, state, RAM or captured image is
shipped. `production/reader/named_dump.hpp` is generic tool source generated from
public Ymir declarations; it contains no snapshot/game values. It is not generated
guest game code. Per-file origins, donor identities and modifications are in
`PROVENANCE.json`. Preserve `LICENSE`, the MIT notices and the Mednafen
GPL-2.0-or-later source-header grants. The combined add-on is GPL-3.0-only; the
MIT core retains its licence.

From this add-on directory, with an existing Python and Pillow:

```text
python -B -m saturn_graphics --profile ABS_PROFILE inspect --native ABS_DUMP --harness ABS_RENDERER_RECEIPT --out ABS_FRESH_OUTPUT
python -B -m saturn_graphics --profile ABS_PROFILE inspect --ymir-state ABS_STATE --paired-capture ABS_CAPTURE_JSON --reader-build ABS_READER_RECEIPT --harness ABS_RENDERER_RECEIPT --out ABS_FRESH_OUTPUT
python -B -m saturn_graphics --profile ABS_PROFILE inspect --native ABS_DUMP --decode-only --out ABS_FRESH_OUTPUT
```

Building is optional and explicit. Inspection never builds or installs:

```text
python -B -m saturn_graphics --profile ABS_PROFILE build-harness --compiler ABS_EXISTING_CLANG_OR_GXX --out ABS_FRESH_BUILD
python -B -m saturn_graphics --profile ABS_PROFILE build-harness --runtime ABS_COMPATIBLE_RUNTIME --compiler ABS_EXISTING_CLANG_OR_GXX --out ABS_FRESH_BUILD
python -B -m saturn_graphics --profile ABS_PROFILE build-reader --compiler ABS_EXISTING_CLANG_OR_GXX --include ABS_YMIR_CORE_INCLUDE --include ABS_YMIR_SDL_SRC --include ABS_COMPATIBLE_HEADER_LIBRARY_INCLUDE --include ABS_XXHASH_INCLUDE --out ABS_FRESH_READER_BUILD
```

The default renderer uses the bundled 14-file non-system closure. `--runtime`
selects a caller's compatible saturn-recomp runtime directory while retaining
the two bundled compositor seams. The builder pins the 14 expected source
files before compilation, records the actual compiler dependency closure and
rejects any added or missing dependency. It uses C++20 and
`SATURN_VDP1_GPL=1`; no Saturn core/guest is constructed. See
[the exact runtime contract](docs/runtime-compatibility.md) before selecting an
external runtime; this is a private production API, not an arbitrary-emulator
interface. The reader additionally
needs compatible public Ymir archive13 savestate/serializer headers and fmt,
cereal, nlohmann/json and xxHash headers. Repeat `--include` for each explicit
header root. Those public headers and an existing C++ compiler remain external;
build receipts pin their actual dependency closure. Other Ymir archive versions
are not interchangeable. A build alone is not format/render qualification.

Legacy DRAW bundles and physical DISPLAY sidecars retain unknown-register,
interpolation, raster-history and OS-readback limits. Rendering is a static
diagnostic. A screenshot alone cannot supply missing machine state. A supplied
matched-moment premise is never inferred from filenames or dimensions.

Synthetic checks (mocked compiler processes; no actual build, guest or renderer):

```text
python -B -m unittest discover -s tests -v
```

`SATURN_KIT_TEST_TEMP` selects a writable test-only temporary parent if necessary.
These checks do not qualify another runtime, serializer version or game.

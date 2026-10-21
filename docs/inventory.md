# Tool inventory and extraction status

Version0.2, checked against Grandia's retained tools on2026-10-21. All
generalisable source below is extracted; external compiled utilities and
game-specific inputs remain explicit dependencies. No game data is bundled.

| Capability | Extracted destination | Retained dependency / qualification limit |
| --- | --- | --- |
| Graphics inspector | addons/gpl-grandia: command/register/layer decoders, original/native readers, production compositor closure and generic state-reader source | Pinned renderer/reader utilities; explicit build recipes. Original archive13 and paired capture required. Other game/serializer versions need qualification. |
| Ymir research bridge | addons/gpl-control/ymir_bridge_client.py, exact existing client | Compatible research emulator with exact build identity; stock Ymir unsupported. |
| Fast replay/capture | addons/gpl-control/adapter.py | Pinned native player/media, recording, disposable seed, finite bounds and route/profile parameters. Synthetic flow/cleanup and one 600-frame Grandia run pass. |
| Original controller/capture | same control adapter, controller-only recipe | New profile/backup copy and matching research bridge. Debug derivation is explicit; export is allowed, import/RAM writes refused. |
| Save transport | addons/gpl-saves | Exact existing native-to-Ymir C++ converter source and BUP-to-native function. External pinned Ymir API converter for backup image output; no installation or story/checksum inference. |
| Hardware/guest logs | saturn_kit/api.py | New MIT text parser. Full keys retained; repeated cumulative rows refused; global/per-CPU overlap is not added together. |
| Audio comparison | addons/gpl-audio | Extracted packet mapping, generated/playback near-zero intervals, supply uncertainty and boundary rules; existing NumPy required. |
| Windows audio acquisition | same audio add-on | Profile-bound process-loopback helper, event observer and explicit-execute capture wrapper. Existing compiler/Windows APIs and exact symbols required. Compile/synthetic guards pass; live acquisition is unqualified. |

Grandia paths, source/executable pins, BIOS/disc/save inputs, route steps,
clock/settings, audio windows and symbols belong in a private game profile.
None are frozen as generic kit constants. The source profile example is inert.
A profile identity is not proof that another game or route works.

Production rendering is preserved, not rewritten. DRAW, physical DISPLAY,
interpolated presentation and OS readback remain different observations.
Unsupported renderer features and missing original getters stay explicit.
A finite command-walk guard means incomplete traversal, not a live timing
measurement. Matched filenames/frame numbers do not prove matched state.

Exact donor paths/hashes and adaptations appear in add-on provenance records
and the root per-file manifest. Reconstruction tools without a permissive
file grant are conservatively GPL3; saturn-recomp MIT and Mednafen GPL2+
exceptions retain their own notices. MIT core has no copied GPL decoder.

No game/runtime/translator changes, automatic builds/installs, expectation
updates, active-save writes or third-party uploads are performed by analysis.
Acquisition is opt-in and separately qualified; offline retained-data checks
do not silently qualify live use.

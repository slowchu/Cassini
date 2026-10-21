# Cassini

Use the kit for read-only inspection of existing Saturn recomp captures and
logs. Select a game profile; keep its local paths and pins outside generic
recipes. Write each result to a fresh private directory. The MCP tools and
CLI share one dispatcher; a successful call is not proof that two scenes are
matched.

Start graphics work with `inspect`, then `diff`; extend the admitted adapter
when a required field is missing. Do not write a temporary decoder or use a
replacement renderer. Treat DRAW, physical DISPLAY, interpolated presentation
and OS readback as separate observations. Keep unknown registers unknown and
flag incomplete command walks.

Use `hw_report` for access counts and recognized guest-log messages. Counts
are not CPU time. Preserve source/input identities and matching premises in
reports. Do not count mirrored aggregate/per-CPU records twice or treat equal
frame numbers as equivalent game state.

For a concrete investigation, read the relevant short recipe:

- [Graphics bug investigation](skills/saturn-graphics-investigation/SKILL.md)
- [Native versus original](skills/saturn-native-original/SKILL.md)
- [Feature-demand report](skills/saturn-feature-demand/SKILL.md)
- [Reproduction from recordings](skills/saturn-recording-reproduction/SKILL.md)
- [Debug-derived findings](skills/saturn-debug-derived/SKILL.md)

Replay/original capture default to plans; explicit execute uses the pinned
optional controller with finite bounds and disposable profiles/saves. Never
start a competing guest while the owner plays. Check owned-process closure
and functional endpoints separately. Save conversion needs an explicit target
and fresh output; no save installation or checksum/progress inference. Audio
delivery analysis needs pinned packets/metadata and, for generated alignment,
supply observations; quiet PCM alone is not a delivery fault. Do not report
planned or synthetic-only jobs as actual gameplay qualification. No tool
installs software, changes game/runtime code, imports state or replaces
active saves/expectations.

Keep raw game/BIOS/media, saves, states, images and audio private. The kit's
tests use synthetic fixtures only. Existing GPL tools belong to the optional
add-on boundary; preserve their notices and consult the per-file provenance
manifest before sharing the package.

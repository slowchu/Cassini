---
name: saturn-graphics-investigation
description: Inspect an existing Saturn recomp graphics dump or original capture and explain a reported visual fault using the maintained agent kit.
license: MIT
---

Select the game profile and identify the capture's producer, scene and visible
symptom. Use the kit's `inspect` MCP tool with `profile`, `input`, `kind` and a
fresh private `out`; original states also need `paired_capture`. Inspect the
returned text/JSON and the composite, physical framebuffer and layer PNGs.

Check DRAW versus physical DISPLAY and any interpolation/history limits before
reasoning about a missing object. Current VRAM is not the previous executed
list. Preserve unknown registers and incomplete traversal as limitations.
Use the admitted production compositor; extend its maintained adapter rather
than writing another decoder or renderer.

For a reference pair, use `diff` on the two inspection manifests. Record which
inputs, scene/state and presentation phase match. Investigate command/layer
changes alongside the full pixel difference. A static reconstruction is not
automatically what the owner saw, and a pixel difference is not automatically
a game bug.

Report the observed defect, the supporting fields/images and the smallest next
check that would distinguish the remaining explanations. Keep raw outputs
private. This workflow does not authorize runtime changes or new guests.

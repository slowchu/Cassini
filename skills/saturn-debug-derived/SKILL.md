---
name: saturn-debug-derived
description: Investigate Saturn game-debug-menu findings while preserving debug-derived provenance and separating them from normal-play evidence.
license: MIT
---

Check the game's profile and owner's authorized debug entry method first.
Grandia permits its existing guarded debug-flag enable in disposable copies,
then its own map/scenario/battle controls. It prohibits tool-written RAM
beyond that flag. This permission is game-specific, not a default for another
Saturn title.

Record the debug controls, starting save, resulting map/story/party state and
all tool-written bytes, if any. Label every resulting run, capture and in-game
save `debug-derived`. A map jump can leave unrelated early-story flags active;
visual arrival is not proof that a late event has been reproduced.

Use `inspect` and `diff` for retained graphics evidence. A debug-derived
finding requires the same debug steps in Ymir, or reproduction in normal
play/from a genuine save, before confirming a Grandia bug. Record which
condition was actually met and compare the behavior. If the original shows
the same symptom, report original behavior rather than a native-port defect.
Keep a source-supported suspicion unconfirmed when the target event was not
reached.

Keep derived saves in a separate provenance-labelled library; never replace
the owner's progress. Debug-derived states do not become frozen expectations
for normal play. Controller recipes may use the game's own controls only
under the project's debug policy; the kit never enables debug flags, writes
RAM or imports a state.

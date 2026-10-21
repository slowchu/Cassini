# Native project license and component notices

The Grandia reconstruction project and combined native program are licensed
under GNU GPL version 3 only (SPDX: GPL-3.0-only). The full text accompanies this
notice. Existing separately licensed components retain their original grants
and notices. The owner's free, noncommercial project intention adds no
restriction to recipients' GPL rights.

The private Saturn recompilation toolkit retains its MIT LICENSE and
THIRD_PARTY.md. Its retained SaturnKit rotation-background component is MIT,
Copyright (c) 2026 Samuele Voltan; the complete adjacent
SATURNKIT-RBG0-LICENSE.txt remains required.

The selected compatible emulator source set is Ymir v0.3.3, commit
54fead6a0001d3e4b6741a8d095ee8342266c3dc, GPL-3.0-only; and the five verified
Mednafen 1.32.1 Saturn VDP1 source files under GPL-2.0-or-later, used under
GPL version 3. Mednafen's vdp1.cpp is Copyright (C) 2015-2021 Mednafen Team;
vdp1_common.h, vdp1_sprite.cpp, vdp1_poly.cpp and vdp1_line.cpp are Copyright
(C) 2015-2020 Mednafen Team. Preserve their full notices with any adaptations.
Additional files require their own provenance and license review before use.

This records source compatibility, not a claim that those emulator sources have
already been ported or that the optional rasterizer has been enabled. Each
actual port must record precise paths, revision, dependencies and notices.
Corresponding-source obligations apply to distributed GPL binaries.

Game data, BIOS, saves, extracted assets and disc-derived generated guest code
or binaries are excluded. Players supply their own disc and BIOS. No game or
BIOS copyright is granted by this project.

Upstream sources: https://github.com/ymir-emu/Ymir/tree/54fead6a0001d3e4b6741a8d095ee8342266c3dc
and https://mednafen.github.io/releases/ (verified mednafen-1.32.1.tar.xz).

Session2 additionally selects only self-contained extended-color mixer branches,
related dispatcher conditions and internal constants from Mednafen 1.32.1
src/ss/vdp2_render.cpp, SHA-256 3871b2f6e7ba7dc80f134cc1de438b5d0a062bdb12d019a935b3c2f8d7ebf8a4.
Copyright (C) 2016-2019 Mednafen Team, GPL-2.0-or-later, used under GPL3.
The release archive SHA-256 is de7eb94ab66212ae7758376524368a8ab208234b33796625ca630547dbc83832.
Its full notice must accompany each actual adaptation. No vdp2_common.h, ss.h
or generic Mednafen framework header is included by this narrow selection.
This selects compatible source; actual port and renderer verification remain
separately reviewable.

Actual session2 adaptation: runtime/src/video/mednafen_excc.inc implements
the audited CPP extended-color and line-color mixer branch semantics, retaining
its full Mednafen copyright, GPL-2.0-or-later grant and warranty notice.
runtime/src/video/vdp2.cpp supplies pixel-format and raw color-calculation
metadata and line insertion. The existing MIT saturnkit_rbg0.inc adds bounded
coefficient line-color metadata; its full adjacent MIT notice stays unchanged.
No Mednafen common or framework header was copied. Exact donor commit: 1336a80fd5b6c514b42346112d02aff7bd308c6b.
GPL renderer code is present; only the optional GPL VDP1 rasterizer remains off.
Offline mixer/captured-input checks do not establish live gameplay correctness.

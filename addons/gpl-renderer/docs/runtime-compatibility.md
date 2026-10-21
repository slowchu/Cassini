# Compatible production runtime contract

`build-harness --runtime ABS_RUNTIME` selects existing production source for an
explicit offline build. Omit it to select the frozen runtime shipped here. It
does not modify the selected runtime, start a game or build implicitly during
inspection. The selected directory is resolved to its canonical path and stored
in the build receipt. The two inspection translation units always come from
this add-on; they include the selected `vdp1.cpp` and `vdp2.cpp` directly.

The runtime root must contain these 12 files:

```text
include/saturn/sh2.h
include/saturn/sh2_time.h
include/saturn/sh2_work.h
src/machine/saturn.h
src/machine/state.h
src/video/vdp1.cpp
src/video/vdp2.cpp
src/video/png.cpp
src/video/video.h
src/video/vdp1_raster_gpl.inc
src/video/mednafen_excc.inc
src/video/saturnkit_rbg0.inc
```

Together with `production/vdp1_bridge.cpp` and `production/compositor.cpp`, this
is the expected 14-file non-system compilation closure. An unavailable required
file fails before creating the output folder. The selected files and compiler
are pinned before compiling; actual compiler-reported paths and hashes are
preserved in the receipt. An additional or missing dependency rejects the build,
and the failed receipt and logs remain available. Compiler/system-library headers
are outside the non-system dependency list produced by `-MMD`.

The bundled seam sources are the complete, exact API specification. Their
important dependencies include:

| Area | Required compatible interface |
| --- | --- |
| Shared types | `SaturnConfig`, `SH2Context`, `Frame` with width, height and row-major `0x00RRGGBB` pixels; declarations used by the seam's fixed host stubs. |
| VDP1 storage | `g_vdp1_vram[0x80000]`, register words through `g_reg`, two physical framebuffer banks accessed through `g_fb`, and the draw/target/interpolation state used by `inspect_load_vdp1`. |
| VDP1 helpers | `TVMR`, `LOPR`, `vw`, `tw`, `draw`, `fb_width`, `fb_height`; `RecFrame` and `g_rec_cur.draws[].cmds[].addr` for recording the production command order; `g_cfg.interp`, `g_sys_x`, `g_sys_y`. |
| VDP2 storage | Big-endian byte arrays `g_vdp2_vram[0x80000]`, `g_vdp2_cram[0x1000]`, `g_vdp2_regs[0x200]`. |
| VDP2 helpers/types | `Nbg`, `Rbg`, `RotParam`, `Pix`, `S_DOT`, `reg`, `cram_mode`, `setup_nbg`, `setup_rbg`, `window_line`, `sprite_pixel`, `vdp1_dot`, `vdp2_lines`, `vdp2_compose` and `write_png`; all fields read by the two seam sources must retain their meaning. |

The loader supplies VDP1 VRAM (524288 bytes), framebuffer (262144), VDP1 registers
(32), VDP2 VRAM (524288), CRAM (4096) and VDP2 registers (512). The seam preserves
the existing physical-bank, captured-word and static-field interpretation. It
recomposes with parity `-1` and does not reconstruct past draws, field latches,
interpolated buffers or an OS-visible frame. Layer exports and decoded production
fields retain the existing unsupported-feature and unknown-getter limits.

The build uses an existing GCC/Clang-compatible C++20 driver, the selected
runtime's `include` and `src/{video,machine,host,sound}` include directories, and
`SATURN_VDP1_GPL=1`. No new decoder, rasterizer, colour mixer or emulator adapter
is substituted. Another emulator's renderer or a changed private API is not
compatible merely because it models Saturn hardware. Supporting it requires an
explicit seam/dependency update and checks against captured inputs.

Retain the selected runtime's source grants and notices; the bundled licence
does not grant rights to another source tree. Compilation only establishes that
the seam links. Before adopting its receipt, run the compositor's control
self-check and the relevant retained-input whole-image/layer regressions. A
successful build is not game or renderer qualification.

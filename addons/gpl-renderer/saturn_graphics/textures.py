# SPDX-License-Identifier: GPL-3.0-only
"""Export each VDP1 texture the current command list uses: PNG, raw bytes, addresses and a local viewer page.

Texels follow the production texel() in vdp1.cpp and colours its sprite_pixel() in vdp2.cpp, without
priority, colour calculation, Gouraud shading or the draw-time flips."""
import base64
import hashlib
import html
import json

from . import decode, legacy_vdp1 as legacy
from .inputs import SIZES
from .policy import pin, private

MODES = {0: "4bpp colour bank", 1: "4bpp lookup table", 2: "6bpp colour bank (64)",
         3: "7bpp colour bank (128)", 4: "8bpp colour bank (256)", 5: "16bpp RGB"}
BANK_MASKS = {2: 0x3F, 3: 0x7F, 4: 0xFF}
# Draw-code masks of the sixteen sprite types, as vdp2.cpp's kTypes.
DC_MASKS = (0x7FF, 0x7FF, 0x7FF, 0x7FF, 0x3FF, 0x7FF, 0x3FF, 0x1FF, 0x7F, 0x3F, 0x3F, 0x3F, 0xFF, 0xFF, 0xFF, 0xFF)
SETTING_REGISTERS = {"RAMCTL": 0x0E, "SPCTL": 0xE0, "SDCTL": 0xE2, "CRAOFB": 0xE6}
# Raw memories from another emulator may come without VDP2's registers: 2048-colour CRAM, type 0, mixed RGB.
ASSUMED_REGISTERS = {0x0E: 0x1000, 0xE0: 0x0020}
CLEAR, SHADOW = (0, 0, 0, 0), (0, 0, 0, 128)
LIMITS = [
    "Colours use this capture's VDP2 sprite settings and colour RAM. On screen, Gouraud shading, colour calculation, shadows and priority can still change them.",
    "Textures are as stored in VRAM; each use's flips are listed, not applied. End-code texels are shown transparent and do not cut their line short.",
    "The command list is the one in current VDP1 VRAM, walked from address 0; it need not be the list that drew the displayed picture.",
    "Only VDP1 textures are exported. VDP2 background cells and bitmaps are not.",
    "Exported images are the game's own graphics: keep them private."]


def raw_memories(vram_path, cram_path, registers_path=None, swap_bytes=False):
    """Video memories saved by any emulator: VDP1 VRAM, colour RAM and, if available, VDP2's registers."""
    def read(path, name):
        data = private(path).read_bytes()
        if len(data) != SIZES[name]:
            raise ValueError(f"{path.name} must be exactly {SIZES[name]} bytes")
        return bytes(data[i ^ 1] for i in range(len(data))) if swap_bytes else data
    vram, cram = read(vram_path, "vdp1_vram"), read(cram_path, "cram")
    inputs = [pin(vram_path), pin(cram_path)]
    if registers_path:
        regs, known = read(registers_path, "vdp2_registers"), set(range(0, 512, 2))
        inputs.append(pin(registers_path))
    else:
        regs, known = bytearray(SIZES["vdp2_registers"]), set()
        for offset, value in ASSUMED_REGISTERS.items():
            regs[offset:offset + 2] = value.to_bytes(2, "big")
    provenance = {"kind": "raw-video-memories", "inputs": inputs, "byte_swapped": swap_bytes}
    return vram, cram, bytes(regs), known, provenance, ["Raw memories: the moment they were saved is as supplied, not checked."]


def ymir_dumps(directory, registers_path=None):
    """Ymir's Debugger > Dump all memory folder, plus the memory viewer's dump of the VDP2 registers region."""
    folder = private(directory)
    if registers_path is None:
        found = sorted(folder.glob("*_VDP2_registers_05F80000_512B.bin"))
        if len(found) > 1:
            raise ValueError("Several saved VDP2 register files; choose one with --vdp2-registers")
        registers_path = found[0] if found else None
    vram_path = folder / "vdp1-vram.bin"
    memories = raw_memories(vram_path, folder / "vdp2-cram.bin", registers_path)
    memories[4]["kind"] = "ymir-memory-dump"
    limits = memories[5]
    if registers_path and abs(private(registers_path).stat().st_mtime - vram_path.stat().st_mtime) > 60:
        limits.append("The VDP2 register file was saved more than a minute apart from the memory dump; colours may not match that moment.")
    return memories


def settings(regs, known):
    reg = lambda off: int.from_bytes(regs[off:off + 2], "big")
    spctl = reg(0xE0)
    return dict(cram_mode=reg(0x0E) >> 12 & 3, sprite_type=spctl & 15, sprite_window=bool(spctl >> 4 & 1),
                mixed_rgb=bool(spctl >> 5 & 1), sprite_colour_offset=(reg(0xE6) >> 4 & 7) << 8,
                transparent_shadows=bool(reg(0xE2) >> 8 & 1),
                captured_registers={name: off in known for name, off in SETTING_REGISTERS.items()})


def rgb555(v):
    return ((v & 31) << 3, (v >> 5 & 31) << 3, (v >> 10 & 31) << 3, 255)


def cram_colour(cram, mode, i):
    if mode == 2:
        a = (i & 0x3FF) * 4
        return (cram[a + 3], cram[a + 2], cram[a + 1], 255)
    a = (i & (0x7FF if mode == 1 else 0x3FF)) * 2
    return rgb555(cram[a] << 8 | cram[a + 1])


def colour(dot, s, cram):
    """The sprite layer's colour for one VDP1 dot, ignoring its priority."""
    if dot == 0:
        return CLEAR
    if s["mixed_rgb"] and dot & 0x8000:
        return rgb555(dot)
    kind = s["sprite_type"]
    mask = DC_MASKS[kind]
    d = dot & 0xFF if kind >= 8 else dot
    dc = d & mask
    msb_shadow = 2 <= kind <= 7 and not s["sprite_window"] and d & 0x8000
    if dc == mask - 1:
        return SHADOW
    if msb_shadow and not d & 0x7FFF:
        return SHADOW if s["transparent_shadows"] else CLEAR
    if dc == 0:
        return CLEAR
    return cram_colour(cram, s["cram_mode"], dc + s["sprite_colour_offset"])


def dot_of(mode, raw, colr, lut):
    if mode == 0: return colr & 0xFFF0 | raw
    if mode == 1: return lut[raw]
    if mode <= 4: return colr & ~BANK_MASKS[mode] & 0xFFFF | raw & BANK_MASKS[mode]
    return raw


def raw_values(mode, data):
    if mode <= 1:
        for b in data:
            yield b >> 4
            yield b & 15
    elif mode <= 4:
        yield from data
    else:
        for i in range(0, len(data), 2):
            yield data[i] << 8 | data[i + 1]


def image(mode, data, colr, lut, spd, ecd, s, cram):
    """RGBA bytes and texel counts, as the production texel() decides drawn, transparent and end codes."""
    end_code = 15 if mode <= 1 else 0xFF if mode <= 4 else 0x7FFF
    pixels, counts = bytearray(), dict(transparent=0, end_code=0, shadow=0)
    for raw in raw_values(mode, data):
        if raw == end_code and not ecd:
            value = CLEAR
            counts["end_code"] += 1
        elif raw == 0 and not spd:
            value = CLEAR
        else:
            value = colour(dot_of(mode, raw, colr, lut), s, cram)
        counts["transparent"] += value == CLEAR
        counts["shadow"] += value == SHADOW
        pixels += bytes(value)
    return bytes(pixels), counts


def colour_id(mode, data, colr, lut, spd, ecd, s, cram):
    """Identity of everything besides the texel bytes that decides the exported colours."""
    flags = bytes((mode, spd, ecd, s["mixed_rgb"]))
    if mode == 5:
        dots = sorted(set(raw_values(5, data)))
        table = b"".join(bytes(colour(d, s, cram)) for d in dots if not (s["mixed_rgb"] and d & 0x8000))
    else:
        table = b"".join(bytes(colour(dot_of(mode, raw, colr, lut), s, cram)) for raw in range(16 if mode <= 1 else 256))
    return hashlib.sha256(b"vdp1-colours-v1" + flags + table).hexdigest()


def collect(vram, cram, s):
    """One entry per distinct texture/colour context, with every command that uses it."""
    listing = decode.commands(vram)
    entries, skipped = {}, []
    for row in listing["commands"]:
        if not row.get("textured"):
            continue
        mode, width, height = row["color_mode"], row["width"], row["height"]
        use = dict(command_address=row["address"], draw_index=row["draw_index"], type_name=row["type_name"],
                   hflip=row["hflip"], vflip=row["vflip"], gouraud=row["gouraud_enabled"], calculation=row["calculation"])
        if mode not in MODES or not width or not height:
            skipped.append(dict(use, reason="reserved colour mode" if mode not in MODES else "zero-size texture",
                                colour_mode=mode, width=width, height=height))
            continue
        address = row["texture_address"] & ~15 if mode == 5 else row["texture_address"]
        size = width * height // 2 if mode <= 1 else width * height if mode <= 4 else width * height * 2
        colr = row["colour_word"]
        lut = [legacy.word(vram, row["lut_address"] + 2 * i) for i in range(16)] if mode == 1 else None
        key = (address, width, height, mode, colr if mode in (0, 2, 3, 4) else None, tuple(lut or ()), row["spd"], row["ecd"])
        if key not in entries:
            data = legacy.wrapped(vram, address, size)
            pixels, counts = image(mode, data, colr, lut, row["spd"], row["ecd"], s, cram)
            texture = hashlib.sha256(b"vdp1-texture-v1" + bytes((mode,)) + width.to_bytes(2, "big") + height.to_bytes(2, "big") + data).hexdigest()
            entries[key] = dict(texture_id=texture, colour_id=colour_id(mode, data, colr, lut, row["spd"], row["ecd"], s, cram),
                address=address, bytes=size, width=width, height=height, colour_mode=mode, colour_mode_name=MODES[mode],
                colour_bank=row.get("color_bank"), lut_address=row.get("lut_address") if mode == 1 else None, lut_words=lut,
                spd=row["spd"], ecd=row["ecd"], texel_counts=counts, uses=[], data=data, pixels=pixels)
        entries[key]["uses"].append(use)
    return listing, sorted(entries.values(), key=lambda e: (e["address"], e["colour_id"])), skipped


def export(vram, cram, regs, known, provenance, limits, out):
    """Write textures/, raw/, textures.json, textures.txt and index.html into the fresh directory out."""
    from PIL import Image
    s = settings(regs, known)
    listing, entries, skipped = collect(vram, cram, s)
    (out / "textures").mkdir()
    (out / "raw").mkdir()
    rows = []
    for e in entries:
        name = e["texture_id"][:16] + "-" + e["colour_id"][:8]
        e["file"], e["raw_file"] = f"textures/{name}.png", f"raw/{e['texture_id'][:16]}.bin"
        Image.frombytes("RGBA", (e["width"], e["height"]), e.pop("pixels")).save(out / e["file"])
        e["index_file"] = None
        if e["texel_counts"]["transparent"] == e["width"] * e["height"] and e["colour_mode"] < 5:
            # Its current colours draw nothing; a grey view of the texel values still shows its shape.
            (out / "indices").mkdir(exist_ok=True)
            e["index_file"] = f"indices/{e['texture_id'][:16]}.png"
            top = 15 if e["colour_mode"] <= 1 else 255
            grey = bytes(v * 255 // top for v in raw_values(e["colour_mode"], e["data"]))
            alpha = bytes(0 if v == 0 else 255 for v in raw_values(e["colour_mode"], e["data"]))
            Image.merge("LA", (Image.frombytes("L", (e["width"], e["height"]), grey),
                               Image.frombytes("L", (e["width"], e["height"]), alpha))).save(out / e["index_file"])
        raw = out / e["raw_file"]
        if not raw.exists():
            raw.write_bytes(e["data"])
        e["image_sha256"] = hashlib.sha256((out / e["file"]).read_bytes()).hexdigest()
        del e["data"]
        rows.append(e)
    missing = [name for name, captured in s["captured_registers"].items() if not captured]
    walk_end = listing["summary"]["termination"]["reason"]
    limits = [*limits, *LIMITS,
              *([f"VDP2 registers not captured: {', '.join(missing)}. Colours use assumed values for them."] if missing else []),
              *([f"Command walk: {len(listing['legacy_continuation_gaps'])} CALL/RET continuation gaps; textures past them may be missed."]
                if listing["legacy_continuation_gaps"] else []),
              *(["The command walk ended by " + ("looping back on itself" if walk_end == "cycle" else "reaching its step limit") +
                 ", not at an end command; the list may have been mid-update, so some textures may be missing."]
                if walk_end != "end" else [])]
    summary = dict(commands=listing["summary"]["commands"], textured_commands=listing["summary"]["textured_commands"],
                   textures=len(rows), distinct_texture_bytes=len({e["texture_id"] for e in rows}), skipped=len(skipped),
                   walk_end=listing["summary"]["termination"])
    report = dict(schema="cassini-vdp1-textures-v1", provenance=provenance, summary=summary, settings=s,
                  textures=rows, skipped=skipped, limits=limits)
    (out / "textures.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "textures.txt").write_text(text(report), encoding="utf-8")
    (out / "index.html").write_text(page(report, out), encoding="utf-8")
    return report


def describe(e):
    detail = f"bank 0x{e['colour_bank']:04X}" if e["colour_bank"] is not None else (
        f"table at 0x{e['lut_address']:05X}" if e["lut_address"] is not None else "direct colour")
    return f"{e['colour_mode_name']}, {detail}"


def text(report):
    s = report["summary"]
    lines = [f"VDP1 textures: {s['textures']} exported from {s['textured_commands']} textured commands "
             f"({s['commands']} commands); {s['skipped']} skipped", "",
             "Address  Size     Uses  Colours                                   File"]
    for e in report["textures"]:
        lines.append(f"0x{e['address']:05X}  {e['width']:>3}x{e['height']:<3}  {len(e['uses']):>4}  {describe(e):<40}  {e['file']}" +
                     (f"  (colours all transparent; grey view {e['index_file']})" if e["index_file"] else ""))
    for k in report["skipped"]:
        lines.append(f"skipped command 0x{k['command_address']:05X}: {k['reason']}")
    return "\n".join(lines + ["", "Limits:", *report["limits"]]) + "\n"


def page(report, out):
    """One self-contained page: images are embedded, so it still works when moved or shared."""
    s, cards = report["summary"], []
    for e in report["textures"]:
        scale = max(1, min(8, 160 // max(e["width"], e["height"])))
        uses = ", ".join(f"#{u['draw_index']}" + ("↔" if u["hflip"] else "") + ("↕" if u["vflip"] else "") for u in e["uses"])
        shown = e["index_file"] or e["file"]
        source = "data:image/png;base64," + base64.b64encode((out / shown).read_bytes()).decode("ascii")
        note = "<br><i>Its current colours are all transparent; showing texel values in grey.</i>" if e["index_file"] else ""
        cards.append(f"""<figure><a href="{shown}"><img src="{source}" width="{e['width'] * scale}" height="{e['height'] * scale}" alt="Texture at 0x{e['address']:05X}"></a>
<figcaption><b>0x{e['address']:05X}</b> · {e['width']}×{e['height']}<br>{html.escape(describe(e))}{note}<br>
Drawn by {len(e['uses'])}: {html.escape(uses)}<br><span class="id">{e['texture_id'][:16]}-{e['colour_id'][:8]}</span> · <a href="{e['raw_file']}">raw</a></figcaption></figure>""")
    limits = "".join(f"<li>{html.escape(item)}</li>" for item in report["limits"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VDP1 Textures</title>
<style>
:root {{ --bg: #f6f6f4; --card: #fff; --ink: #1d1d1b; --muted: #66665f; --line: #deded8; --check: #e4e4e0; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #161615; --card: #20201e; --ink: #ececea; --muted: #a2a29b; --line: #34342f; --check: #2c2c29; }} }}
body {{ margin: 0; padding: 24px 16px; background: var(--bg); color: var(--ink); font: 14px/1.45 system-ui, sans-serif; }}
main {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
p, li {{ color: var(--muted); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin: 20px 0; }}
figure {{ margin: 0; padding: 12px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; }}
figure > a {{ display: flex; justify-content: center; align-items: center; min-height: 96px; border-radius: 4px;
  background: conic-gradient(var(--check) 25%, transparent 0 50%, var(--check) 0 75%, transparent 0) 0 0 / 16px 16px; }}
img {{ image-rendering: pixelated; max-width: 100%; height: auto; }}
figcaption {{ margin-top: 8px; font-size: 13px; overflow-wrap: anywhere; }}
.id {{ font-family: ui-monospace, monospace; font-size: 12px; color: var(--muted); }}
a {{ color: inherit; }}
</style></head><body><main>
<h1>VDP1 textures</h1>
<p>{s['textures']} textures from {s['textured_commands']} textured commands. Addresses are VDP1 VRAM offsets.
Draw numbers follow the command list; ↔ and ↕ mark uses drawn flipped.</p>
<div class="grid">
{chr(10).join(cards)}
</div>
<h2>Limits</h2><ul>{limits}</ul>
</main></body></html>
"""

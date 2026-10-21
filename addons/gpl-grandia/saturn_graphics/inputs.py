# SPDX-License-Identifier: GPL-3.0-only
"""Adapt existing dump layouts and frozen original reader exports, without a new serializer."""
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import zlib

from . import hardware
from .policy import fresh_output, pin, private, read_json, utility, verify

SIZES = {"vdp1_vram": 524288, "framebuffer": 262144, "vdp1_registers": 32,
         "vdp2_vram": 524288, "cram": 4096, "vdp2_registers": 512}
ALIASES = {"PNCN0": "PNCNA", "PNCN1": "PNCNB", "PNCN2": "PNCNC", "PNCN3": "PNCND",
           "SCXN2": "SCXIN2", "SCYN2": "SCYIN2", "SCXN3": "SCXIN3", "SCYN3": "SCYIN3", "VCNT": "VCNTLatch"}












def register_maps():
    return hardware.VDP1, hardware.VDP2, pin(Path(hardware.__file__).resolve())


def pack_getters(values, names, size, aliases=None):
    packed = bytearray(size)
    represented = set()
    for offset, name in names.items():
        key = (aliases or {}).get(name, name)
        if name == "-" or key not in values:
            continue
        v = values[key]
        if type(v) is not int or not 0 <= v <= 65535 or offset % 2 or not 0 <= offset < size:
            raise ValueError(f"Invalid saved register getter: {key}")
        packed[offset:offset + 2] = v.to_bytes(2, "big")
        represented.add(offset)
    return bytes(packed), represented


@dataclass
class Snapshot:
    parts: dict
    known1: set
    known2: set
    provenance: dict
    limits: list = field(default_factory=list)
    reference: Path | None = None
    legacy_draw: bytes | None = None

    def validate(self):
        if set(self.parts) != set(SIZES):
            raise ValueError("Incomplete video components")
        for name, size in SIZES.items():
            if len(self.parts[name]) != size:
                raise ValueError(f"Wrong {name} size: expected {size}")
        if 0 not in self.known1:
            raise ValueError("TVMR is not captured; framebuffer interpretation is unknown")
        return self

    def export(self, directory):
        for name, data in self.parts.items():
            (directory / (name + ".bin")).write_bytes(data)
        if self.legacy_draw is not None:
            (directory / "legacy-draw-framebuffer.bin").write_bytes(self.legacy_draw)


def display_bundle(path, legacy, parts):
    """Admit only self-consistent physical-bank metadata; never infer IFB or host presentation."""
    manifest_path = path.with_suffix(".graphics.json")
    display_path = path.with_suffix(".display.bin")
    if not manifest_path.exists():
        return None, display_path.exists()
    if private(manifest_path).parent != path.parent:
        raise ValueError("Display manifest escapes capture directory")
    if manifest_path.stat().st_size > 65536:
        raise ValueError("Display manifest exceeds bounded metadata size")
    def unique(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("Duplicate display manifest field")
            result[k] = v
        return result
    m = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if m.get("schema") != "native-graphics-display-v1" or m.get("complete") is not True or m.get("phase") != "precompose":
        raise ValueError("Unsupported/incomplete display bundle")
    match = re.fullmatch(r"dump-(0|[1-9][0-9]*)\.bin", path.name)
    if not match or type(m.get("vblank")) is not int or m["vblank"] != int(match[1]):
        raise ValueError("Display bundle VBlank/name mismatch")
    def integer(name, maximum):
        if type(m.get(name)) is not int or not 0 <= m[name] <= maximum:
            raise ValueError(f"Invalid display metadata: {name}")
    for name in ("vblank", "raster_ns", "virtual_ns", "absolute_line", "draws", "frame_changes", "draw_end_ns"):
        integer(name, (1 << 64) - 1)
    for name in ("draw_bank", "display_bank", "completed_parity"):
        integer(name, 1)
    integer("pending_out_phase", 2)
    for name in ("tvmr", "fbcr", "tvmd"):
        integer(name, 65535)
    if m["tvmr"] & 7 not in (0, 1, 3):
        raise ValueError("Unsupported physical display TVMR mode")
    for name in ("drawing", "change_pending", "erase_pending", "erase_field", "vbe_due", "erase_at_out", "interp_configured", "host_readback"):
        if type(m.get(name)) is not bool:
            raise ValueError(f"Invalid display boolean: {name}")
    if m["draw_bank"] ^ m["display_bank"] != 1 or m["host_readback"]:
        raise ValueError("Invalid physical-bank/host-readback claim")
    source = "physical_display_not_guaranteed_sampled" if m["interp_configured"] else "physical_display"
    if m.get("sprite_source") != source:
        raise ValueError("Display source/interpolation mismatch")
    word = lambda data, offset: int.from_bytes(data[offset:offset + 2], "big")
    if (m["tvmr"], m["fbcr"], m["tvmd"]) != (word(parts["vdp1_registers"], 0), word(parts["vdp1_registers"], 2), word(parts["vdp2_registers"], 0)):
        raise ValueError("Display metadata/register mismatch")
    def payload(key, expected_name, expected_size, known=None):
        row = m.get(key)
        if not isinstance(row, dict) or row.get("file") != expected_name or type(row.get("bytes")) is not int or row["bytes"] != expected_size:
            raise ValueError("Invalid display payload identity")
        target = private(path.parent / expected_name)
        if target.parent != path.parent or target.stat().st_size != expected_size:
            raise ValueError("Display payload path/size mismatch")
        raw = known if known is not None else target.read_bytes()
        if not isinstance(row.get("crc32"), str) or not re.fullmatch(r"[0-9A-Fa-f]{8}", row["crc32"]) or zlib.crc32(raw) != int(row["crc32"], 16):
            raise ValueError("Display payload CRC mismatch")
        return raw, pin(target)
    _, legacy_pin = payload("legacy", path.name, sum(SIZES.values()), legacy)
    display, display_pin = payload("display", display_path.name, 262144)
    mode = m["tvmr"] & 7
    expected = (1024 if mode == 1 else 512, 512 if mode == 3 else 256, 8 if mode & 1 else 16)
    row = m["display"]
    if any(type(row.get(k)) is not int for k in ("width", "height", "bits_per_pixel")) or tuple(row[k] for k in ("width", "height", "bits_per_pixel")) != expected or row.get("byte_order") != "big":
        raise ValueError("Display packing mismatch")
    shot = m.get("shot")
    if not isinstance(shot, dict) or type(shot.get("requested")) is not bool or shot.get("file") != f"shot-{m['vblank']}.png" or shot.get("status") != "request_only_not_verified":
        raise ValueError("Invalid requested-shot metadata")
    reference = None
    observed_shot = {"requested": shot["requested"], "exists": False, "identity": None,
                     "pairing": "Filename/request only; producer did not verify the later shot or OS presentation."}
    if shot["requested"] and (path.parent / shot["file"]).exists():
        reference = private(path.parent / shot["file"])
        if reference.parent != path.parent:
            raise ValueError("Requested shot escapes capture directory")
        from PIL import Image
        with Image.open(reference) as image:
            if image.format != "PNG":
                raise ValueError("Requested shot is not PNG")
            image.verify()
        observed_shot.update(exists=True, identity=pin(reference))
    return {"metadata": m, "manifest": pin(manifest_path), "legacy": legacy_pin, "display": display_pin,
            "pixels": display, "reference": reference, "observed_shot": observed_shot}, False


def native_dump(path):
    p = private(path)
    if p.stat().st_size != sum(SIZES.values()):
        raise ValueError(f"Native --dump requires exactly {sum(SIZES.values())} bytes")
    data = p.read_bytes()
    parts, pos = {}, 0
    for name, size in SIZES.items():
        parts[name] = data[pos:pos + size]
        pos += size
    bundle, orphan = display_bundle(p, data, parts)
    if bundle:
        draw = parts["framebuffer"]
        parts["framebuffer"] = bundle.pop("pixels")
        reference = bundle.pop("reference")
        limits = ["Physical displayed bank plus current VRAM/registers: static reconstruction does not establish last executed commands, latches or DD retained rows.",
                  "Shot requested/exists/current-file identity are separate facts; no OS/window readback or temporal pairing is inferred."]
        if bundle["metadata"]["interp_configured"]:
            limits.append("Interpolation configured: physical display is NOT guaranteed the sampled source. No IFB was exported; this is a physical-bank reconstruction only.")
        return Snapshot(parts, set(range(0, 32, 2)), set(range(0, 512, 2)),
            {"kind": "native-display-bundle", "input": pin(p), "framebuffer_role": bundle["metadata"]["sprite_source"],
             "legacy_draw": {"bytes": len(draw), "sha256": hashlib.sha256(draw).hexdigest(), "role": "captured legacy DRAW"},
             "display_bundle": bundle}, limits, reference, draw).validate()
    return Snapshot(parts, set(range(0, 32, 2)), set(range(0, 512, 2)),
                    {"kind": "native-dump", "input": pin(p), "framebuffer_role": "captured DRAW only",
                     "display_bundle_status": "incomplete_display_bundle" if orphan else "absent"},
                    ["DRAW-only static supplied-buffer reconstruction; displayed bank, last executed command list and raster history are absent.",
                     *( ["Orphan display sidecar: incomplete_display_bundle; no displayed-source claim is admitted."] if orphan else [])]).validate()


def original_fixture(path):
    manifest = read_json(private(path))
    if manifest.get("schema") not in {"saved-original-isolated-renderer-fixture-v1", "isolated-frozen-menu-renderer-fixture-v1", "saturn-kit-original-fixture-v1"}:
        raise ValueError("Unsupported original fixture schema")
    rows = {Path(r["path"]).name: r for r in manifest["private_fixture_files"]}
    def raw(name):
        return private(verify(rows[name])).read_bytes()
    maps1, maps2, map_pin = register_maps()
    # Use the same inspected name/offset mapping as the retained packing, never infer unknown getters.
    if manifest.get("register_map", {}).get("sha256") != map_pin["sha256"]:
        raise ValueError("Original fixture register map differs from current inspected map")
    metadata = json.loads(raw("renderer-input-private.json"))
    regs1, known1 = pack_getters(metadata["original_regs1"], maps1, 32)
    known2 = {r["offset"] for r in manifest["packed_vdp2_words"]}
    display = manifest["selected_display_framebuffer"]
    if type(display) is not int or display not in (0, 1):
        raise ValueError("Invalid captured display framebuffer selector")
    parts = {"vdp1_vram": raw("vdp1-vram.bin"), "framebuffer": raw(f"sprite-fb{display}.bin"),
             "vdp1_registers": regs1, "vdp2_vram": raw("vdp2-vram.bin"), "cram": raw("cram.bin"),
             "vdp2_registers": raw("vdp2-registers-saved-fields.bin")}
    ref = manifest.get("original_reference_frame")
    return Snapshot(parts, known1, known2,
        {"kind": "original-export", "manifest": pin(path), "display_fb": display, "register_map": map_pin,
         "verified_inputs": [rows[n] for n in ("vdp1-vram.bin", "vdp2-vram.bin", "cram.bin", f"sprite-fb{display}.bin", "vdp2-registers-saved-fields.bin", "renderer-input-private.json")]},
        ["Original saved getter gaps are unknown; zeros in the isolated compositor vector are declared synthetic, never captured values.",
         "Saved display bank and current VRAM/getters do not establish prior-frame latches or captured-frame temporal equivalence.",
         *manifest.get("limits", [])], private(verify(ref)) if ref else None).validate()


def original_state(snapshot, build_receipt, capture_path, directory):
    """Invoke only the retained validated version13 executable; never compile or create a Saturn core."""
    source, capture_path = private(snapshot), private(capture_path)
    build = utility(build_receipt, "saturn-kit-original-reader-v1")
    if build.get("archive_version_required") != 13:
        raise ValueError("A compatible frozen version13 reader is required")
    exe = verify(build["executable"])
    library = Path(build["library_directory"])
    manifest = read_json(source.with_suffix(source.suffix + ".json"))
    capture = read_json(capture_path)
    if manifest.get("format") != "Ymir cereal portable binary" or manifest.get("size") != source.stat().st_size:
        raise ValueError("Original snapshot manifest mismatch")
    reader_dir = directory / "reader"
    reader_dir.mkdir()
    values_path = reader_dir / "original-state.json"
    env = os.environ.copy()
    env["PATH"] = str(library) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([str(exe), str(source), str(values_path)], capture_output=True, timeout=60, env=env)
    (reader_dir / "reader.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError("Retained original reader rejected state; preserved reader.log")
    values = read_json(values_path)
    if (values.get("schema") != "ymir-original-state-values-v1" or values.get("roundtrip_byte_identical") is not True or
        values.get("archive_version") != 13 or
        values.get("snapshot_xxh128") != manifest.get("xxh128") or
        values["scheduler"]["currCount"] != capture["scheduler_cycle"] or
        manifest["source_session_id"] != capture["session_id"]):
        raise ValueError("Original reader/capture/manifest boundary mismatch")
    vdp = values["vdp"]
    maps1, maps2, map_pin = register_maps()
    regs1, known1 = pack_getters(vdp["regs1"], maps1, 32)
    regs2, known2 = pack_getters(vdp["regs2"], maps2, 512, ALIASES)
    display = vdp["displayFB"]
    if type(display) is not int or display not in (0, 1):
        raise ValueError("Invalid original display framebuffer selector")
    def array(v, size):
        if len(v) != size or any(type(x) is not int or not 0 <= x <= 255 for x in v):
            raise ValueError("Invalid original video array")
        return bytes(v)
    parts = {"vdp1_vram": array(vdp["VRAM1"],524288), "framebuffer": array(vdp["spriteFB"][display],262144),
             "vdp1_registers": regs1, "vdp2_vram": array(vdp["VRAM2"],524288),
             "cram": array(vdp["CRAM"],4096), "vdp2_registers": regs2}
    return Snapshot(parts, known1, known2, {"kind": "original-state", "snapshot": pin(source),
        "reader_build": pin(build_receipt), "reader": pin(exe), "paired_capture": pin(capture_path), "register_map": map_pin,
        "display_fb": display}, ["Current saved arrays/getters; unknown register words remain unknown. Capture/Save renderer history can differ."]).validate()

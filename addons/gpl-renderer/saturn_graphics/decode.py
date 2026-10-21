# SPDX-License-Identifier: GPL-3.0-only
"""Expose the maintained VDP1 decoder; add annotations without another command walker."""
import hashlib
import importlib.util
import sys
from collections import Counter

from pathlib import Path
from . import legacy_vdp1 as legacy
from .inputs import pin, register_maps

TYPES = {0: "normal-sprite", 1: "scaled-sprite", 2: "distorted-sprite", 3: "distorted-sprite-alias",
         4: "polygon", 5: "polyline", 6: "line", 7: "polyline-alias", 8: "user-clip",
         9: "system-clip", 10: "local-coordinates", 11: "user-clip-alias"}


def signed(v, bits):
    return (v & ((1 << bits) - 1)) - ((v & (1 << (bits - 1))) << 1)


def walk_with_fetches(data):
    """Observe only the existing walker's own word fetches; its traversal remains unchanged."""
    fetches, control = [], True
    original_word = legacy.word
    def observed(buf, address):
        nonlocal control
        value = original_word(buf, address)
        if sys._getframe(1).f_code is legacy.walk.__code__:
            if control:
                fetches.append({"address": address & (legacy.VRAM - 1), "ctrl": value})
            else:
                fetches[-1]["link"] = value * 8 & (legacy.VRAM - 1)
            control = not control
        return value
    legacy.word = observed
    try:
        commands, refs, end = legacy.walk(data)
    finally:
        legacy.word = original_word
    active_return, gaps = False, []
    for index, row in enumerate(fetches):
        ctrl = row["ctrl"]
        row.update(index=index, end=bool(ctrl & 0x8000), skip=bool(ctrl & 0x4000),
                   jump=(ctrl >> 12) & 7, type=ctrl & 15)
        if row["end"]:
            continue
        action = row["jump"] & 3
        if action == 2:
            if active_return:
                gaps.append({"index": index, "reason": "nested CALL: retained walker and production outstanding-return behavior differ"})
            active_return = True
        elif action == 3:
            if not active_return:
                gaps.append({"index": index, "reason": "unmatched RET: retained walker and production sequential fallback differ"})
            active_return = False
    return commands, refs, end, fetches, gaps


def commands(data, actual_order=None, production_terminated=True):
    rows, refs, end, fetches, gaps = walk_with_fetches(data)
    if actual_order is not None:
        rows = [legacy.decode(data, a)[0] for a in actual_order]
    refs = Counter()
    local, system, user = None, None, None
    for index, row in enumerate(rows):
        a, kind = row["address"], row["type"]
        word = lambda o: legacy.word(data, a + o)
        pm, colr = word(4), word(6)
        row.update(draw_index=index, type_name=TYPES.get(kind, "reserved"),
                   link_address=word(2) * 8 & 0x7FFFF, colour_word=colr, size_word=word(10),
                   zoom_point=(word(0) >> 8) & 15,
                   user_clipping_enabled=bool(pm & 0x400), user_clipping_outside=bool(pm & 0x200),
                   pre_clipping_disabled=bool(pm & 0x800), gouraud_enabled=bool(pm & 4),
                   clip_state_before={"local": local, "system": system, "user": user})
        if kind in (8, 11):
            user = [[word(12) & 0x3FF, word(14) & 0x1FF], [word(20) & 0x3FF, word(22) & 0x1FF]]
        elif kind == 9:
            system = [word(20) & 0x3FF, word(22) & 0x1FF]
        elif kind == 10:
            local = [signed(word(12), 11), signed(word(14), 11)]
        if kind < 8 and local is not None:
            row["vertices_with_local_offset"] = [[x + local[0], y + local[1]] for x, y in row["vertices"]]
        if kind < 8 and pm & 4:
            address = word(28) * 8
            values = [legacy.word(data, address + i * 2) for i in range(4)]
            row["gouraud"] = {"address": address, "rgb5_neutral16": [[v & 31, v >> 5 & 31, v >> 10 & 31] for v in values],
                              "sha256": legacy.sha(legacy.wrapped(data, address, 8))}
        if kind < 4 and (pm >> 3 & 7) == 1:
            row["command_setup_lut_address"] = colr * 8 & 0x7FFFF
            row["production_lut_address"] = (colr & ~3) * 8 & 0x7FFFF
            row["lut_address_interpretations_equal"] = row["production_lut_address"] == row["lut_address"]
            row["legacy_lut_address"] = row["lut_address"]
            row["lut_address"] = row["production_lut_address"]
            row["lut_rgb_entries"] = sum(bool(legacy.word(data, row["lut_address"] + i * 2) & 0x8000) for i in range(16))
        for kind_ref, address, size in legacy.decode(data, a)[1]:
            if kind_ref == "lut": address = row["lut_address"]
            refs[kind_ref, address, size] += 1
        if row.get("gouraud"):
            refs["gouraud", row["gouraud"]["address"], 8] += 1
    summary = legacy.summary(rows, end if actual_order is None else {"reason": "production-end" if production_terminated else "production-limit"})
    return {"decoder": pin(Path(legacy.__file__).resolve()), "commands": rows,
            "summary": summary, "control_fetches": fetches, "control_fetches_source": "legacy diagnostic, incomplete where continuation gaps are listed",
            "legacy_continuation_gaps": gaps, "draw_order_source": "production draw record" if actual_order is not None else "retained qualified walker",
            "primary_order_complete": bool(production_terminated) if actual_order is not None else not gaps and end["reason"] == "end",
            "order_limit": "Current-VRAM production order only; not last executed/displayed history. Decode-only legacy order is non-authoritative when continuation gaps exist.",
            "referenced_regions": [{"kind": k, "address": a, "bytes": n, "references": count,
                "sha256": legacy.sha(legacy.wrapped(data, a, n))} for (k, a, n), count in sorted(refs.items())],
            "initial_clipping_premise": "unknown until set in this current list; no last-draw/history claim"}


def registers(data, known, names):
    return {f"{offset:03X}:{names.get(offset, 'unmapped')}":
            {"captured": offset in known, "value": int.from_bytes(data[offset:offset + 2], "big") if offset in known else None}
            for offset in range(0, len(data), 2)}


def inspect_snapshot(snapshot, production=None):
    names1, names2, map_pin = register_maps()
    data = snapshot.parts
    r1 = lambda a: int.from_bytes(data["vdp1_registers"][a:a + 2], "big") if a in snapshot.known1 else None
    tvmr, fbcr = r1(0), r1(2)
    if production:
        production["captured_register_dependencies"] = {
            "unknown_vdp1_offsets": [a for a in range(0, 32, 2) if a not in snapshot.known1],
            "unknown_vdp2_offsets": [a for a in range(0, 512, 2) if a not in snapshot.known2],
            "condition": "Derived production fields are conditional on the declared isolated vector; unknown getter placeholders are synthetic, not captured zero values."}
    word1 = lambda a, fn: None if r1(a) is None else fn(r1(a))
    return {"schema": "grandia-graphics-inspection-v1", "provenance": snapshot.provenance,
        "limits": snapshot.limits, "register_map": map_pin,
        "components": {name: {"bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()} for name, v in data.items()},
        "vdp1": {"register_words": registers(data["vdp1_registers"], snapshot.known1, names1),
            "register_fields": {"TVMR": {"mode": tvmr & 7, "vblank_erase": bool(tvmr & 8)},
                "FBCR": None if fbcr is None else {"change_mode": fbcr & 3, "DIL": bool(fbcr & 4), "DIE": bool(fbcr & 8), "EOS": bool(fbcr & 16)},
                "PTMR": word1(4, lambda v: {"plot_trigger_mode": v & 3}),
                "EWDR": r1(6),
                "EWLR": word1(8, lambda v: {"x_left": ((v >> 9) & 63) * 8, "y_top": v & 511}),
                "EWRR": word1(10, lambda v: {"x_right_exclusive": ((v >> 9) & 127) * 8, "y_bottom_inclusive": v & 511}),
                "EDSR": word1(16, lambda v: {"previous_end": bool(v & 1), "current_end": bool(v & 2)}),
                "LOPR": word1(18, lambda v: {"command_address": v * 8}),
                "COPR": word1(20, lambda v: {"command_address": v * 8}), "MODR": r1(22)},
            **commands(data["vdp1_vram"], production.get("command_addresses") if production else None,
                       production.get("command_order_terminated", False) if production else True)},
        "vdp2": {"register_words": registers(data["vdp2_registers"], snapshot.known2, names2),
            "unknown_word_offsets": [a for a in range(0, 512, 2) if a not in snapshot.known2],
            "production": production},
        "capture_observation": snapshot.provenance.get("display_bundle"),
        "render_interpretation": "static supplied-buffer reconstruction of " + snapshot.provenance.get("framebuffer_role", "saved display bank") +
            "; compositor RGB, isolated layers and any requested captured shot are distinct; interpolation/history premises remain explicit"}


def differences(a, b, path=""):
    """Every field, with no ownership masks or ignored values."""
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for k in sorted(a.keys() | b.keys()):
            name = f"{path}/{k}"
            if k not in a: result.append({"path": name, "kind": "added", "new": b[k]})
            elif k not in b: result.append({"path": name, "kind": "missing", "old": a[k]})
            else: result.extend(differences(a[k], b[k], name))
        return result
    if isinstance(a, list) and isinstance(b, list):
        return differences({str(i): v for i, v in enumerate(a)}, {str(i): v for i, v in enumerate(b)}, path)
    return [] if a == b else [{"path": path, "kind": "changed", "old": a, "new": b}]


def compare_reports(a, b):
    def indexed(rows):
        counts, result = {}, {}
        for r in rows:
            address = r["address"]; n = counts.get(address, 0); counts[address] = n + 1
            result[f"{address:05X}:{n}"] = r
        return result
    pa, pb = a["vdp2"]["production"], b["vdp2"]["production"]
    artifacts = {"images", "harness_build"}
    semantic = lambda p: {k: v for k, v in p.items() if k not in artifacts} if p else p
    provenance = lambda p: {k: v for k, v in p.items() if k in artifacts} if p else p
    return {"schema": "grandia-graphics-field-diff-v1", "comparison_status": "diagnostic; scene/phase equivalence is not inferred",
        "command_match_key": "address plus occurrence; draw_index and every decoded field remain compared",
        "commands": differences(indexed(a["vdp1"]["commands"]), indexed(b["vdp1"]["commands"])),
        "vdp1_registers": differences(a["vdp1"]["register_words"], b["vdp1"]["register_words"]),
        "vdp2_registers": differences(a["vdp2"]["register_words"], b["vdp2"]["register_words"]),
        "layers_and_rotation": differences(semantic(pa), semantic(pb)),
        "production_artifact_provenance": differences(provenance(pa), provenance(pb)),
        "components": differences(a["components"], b["components"]),
        "capture_observation": differences(a.get("capture_observation"), b.get("capture_observation")),
        "premise_gaps": [*a["limits"], *b["limits"]]}

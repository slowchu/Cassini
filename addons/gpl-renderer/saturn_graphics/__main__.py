# SPDX-License-Identifier: GPL-3.0-only
"""python -B -m saturn_graphics --help"""
import argparse
from pathlib import Path

from .policy import configure
from .decode import compare_reports, inspect_snapshot
from .inputs import fresh_output, native_dump, original_fixture, original_state, pin, private, read_json
from .offline import build, pixel_diff, render, save


ADDRESS_FIELDS = {"address", "link_address", "texture_address", "lut_address", "color_bank",
                  "legacy_lut_address", "production_lut_address", "command_setup_lut_address",
                  "command_address", "command_terminal_address", "plane_addresses", "bitmap_address",
                  "line_table_address", "cell_table_address", "rotation_table_base"}


def text_values(value, field=""):
    """Format a text-only copy, including address old/new values in structured diffs."""
    if isinstance(value, dict):
        parts = value.get("path", "").split("/")
        changed_field = parts[-2] if len(parts) > 1 and parts[-1].isdigit() else parts[-1]
        return {key: text_values(item, changed_field if key in {"old", "new"} else key)
                for key, item in value.items()}
    if isinstance(value, list):
        return [text_values(item, field) for item in value]
    return f"0x{value:X}" if field in ADDRESS_FIELDS and type(value) is int else value


def text_report(report):
    v1, v2 = report["vdp1"], report["vdp2"]
    lines = ["Graphics inspector: static captured inputs", "", str(report["provenance"]),
             f"VDP1 {v1['summary']['commands']} non-skipped commands; order source: {v1['draw_order_source']}"]
    for row in v1["commands"]:
        shown = text_values(row)
        lines.append(f"  {row['draw_index']:4} {shown['address']} {row['type_name']} jump={row['jump']} link={shown['link_address']} "
            f"vertices={row['vertices']} size={row.get('width')}x{row.get('height')} tex={shown.get('texture_address')} "
            f"colour={row.get('color_mode')} lut={shown.get('lut_address')} bank={shown.get('color_bank')} "
            f"lut_setup={shown.get('command_setup_lut_address')} "
            f"gouraud={shown.get('gouraud', {}).get('address')} "
            f"mesh={row.get('mesh')} clip={row['user_clipping_enabled']}/{row['user_clipping_outside']} calc={row.get('calculation')}")
    for name, values in v1["register_words"].items():
        lines.append(f"VDP1 0x{name} = {values['value'] if values['captured'] else 'UNKNOWN'}")
    import json
    lines += ["", "VDP1 decoded register fields", json.dumps(text_values(v1["register_fields"]), indent=2)]
    for name, values in v2["register_words"].items():
        lines.append(f"VDP2 0x{name} = {values['value'] if values['captured'] else 'UNKNOWN'}")
    if v2["production"]:
        for name in ("global", "normal_layers", "rotation", "windows", "unavailable", "production_warnings", "view_policy"):
            lines += ["", "VDP2 " + name, json.dumps(text_values(v2["production"].get(name)), indent=2)]
    else: lines.append("VDP2 structured decode/render pending explicit offline harness; key captured words above remain available.")
    return "\n".join(lines + ["", "Limits:", *report["limits"], v1["order_limit"]]) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    sub = parser.add_subparsers(dest="job", required=True)
    b = sub.add_parser("build-harness", help="explicit offline compile only; never automatic")
    b.add_argument("--out", required=True, type=Path); b.add_argument("--compiler", required=True, type=Path)
    b.add_argument("--runtime", type=Path, help="compatible saturn-recomp runtime root; default: bundled frozen renderer")
    b = sub.add_parser("build-reader", help="explicit reader compile against compatible public Ymir/header dependencies")
    b.add_argument("--out", required=True, type=Path); b.add_argument("--compiler", required=True, type=Path)
    b.add_argument("--include", required=True, action="append", type=Path)
    p = sub.add_parser("inspect", help="read one native dump or retained original state/export")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--native", type=Path); src.add_argument("--original-fixture", type=Path); src.add_argument("--ymir-state", type=Path)
    p.add_argument("--reader-build", type=Path); p.add_argument("--paired-capture", type=Path)
    p.add_argument("--out", type=Path, required=True)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--harness", type=Path); modes.add_argument("--decode-only", action="store_true")
    p = sub.add_parser("diff", help="every represented field plus every rendered pixel")
    p.add_argument("--before", required=True, type=Path); p.add_argument("--after", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path); p.add_argument("--premise", type=Path)
    args = parser.parse_args()
    configure(args.profile)
    if args.job == "build-harness":
        print(build(args.out, args.compiler, args.runtime)); return 0
    if args.job == "build-reader":
        from .build_reader import build as build_reader
        print(build_reader(args.out, args.compiler, args.include)); return 0
    out = fresh_output(args.out)
    try:
        if args.job == "inspect":
            if args.native: snapshot = native_dump(args.native)
            elif args.original_fixture: snapshot = original_fixture(args.original_fixture)
            else:
                if not args.reader_build or not args.paired_capture:
                    raise ValueError("Original state requires its frozen reader build and paired capture")
                snapshot = original_state(args.ymir_state, args.reader_build, args.paired_capture, out)
            production = render(snapshot, args.harness, out) if args.harness else None
            report = inspect_snapshot(snapshot, production)
            if production and snapshot.reference:
                report["captured_image_vs_static_reconstruction"] = pixel_diff(snapshot.reference, out / "render/composite.png", out / "capture-vs-static.png")
            save(out / "inspection.json", report)
            (out / "inspection.txt").write_text(text_report(report), encoding="utf-8")
        else:
            a, b = read_json(private(args.before)), read_json(private(args.after))
            report = compare_reports(a, b)
            report.update(before=pin(args.before), after=pin(args.after),
                          supplied_premise=read_json(private(args.premise)) if args.premise else None,
                          premise_pin=pin(args.premise) if args.premise else None, pixels={})
            ai, bi = (r["vdp2"]["production"]["images"] if r["vdp2"]["production"] else {} for r in (a, b))
            for name in sorted(ai.keys() | bi.keys()):
                if name not in ai or name not in bi:
                    report["pixels"][name] = {"kind": "added" if name in bi else "missing"}
                else:
                    from .inputs import verify
                    report["pixels"][name] = pixel_diff(verify(ai[name]), verify(bi[name]), out / ("diff-" + name))
            save(out / "diff.json", report)
            (out / "diff.txt").write_text("Graphics diff: diagnostic only; matched-moment premise is external.\n" +
                "\n".join(f"{name}: {value}" for name, value in text_values(report).items()), encoding="utf-8")
        print(out); return 0
    except Exception as exc:
        save(out / "failure.json", {"passed": False, "error": str(exc), "outputs_preserved": True})
        raise


if __name__ == "__main__":
    raise SystemExit(main())

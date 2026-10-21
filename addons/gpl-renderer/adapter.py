# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Saturn recomp agent kit contributors
"""Optional process adapter for the bundled, extracted graphics inspector.

Qualified general decoder/renderer sources are bundled; no executable or game
data is included. External prebuilt utility identities come from a per-game
profile. The MIT core uses JSON and subprocesses only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ADDON = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def absolute(value):
    require(isinstance(value, str) and bool(value), "Expected an absolute path string")
    path = Path(value)
    require(path.is_absolute(), "Relative paths are not accepted")
    return path.resolve()


def inside(value, roots, *, file=True):
    path = absolute(value)
    require(any(path.is_relative_to(root) for root in roots), "Path is outside the configured roots")
    if file:
        require(path.is_file(), "Required input is not a regular file")
    return path


def pin(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def verify(row):
    require(isinstance(row, dict), "Expected a file identity")
    path = absolute(row.get("path"))
    require(path.is_file(), "Pinned backend file is missing")
    require(type(row.get("bytes")) is int and row["bytes"] >= 0, "Invalid pinned byte count")
    require(isinstance(row.get("sha256"), str) and len(row["sha256"]) == 64, "Invalid SHA-256 pin")
    require(pin(path) == row, "Pinned file changed: " + str(path))
    return path


def read_json(path, limit=128 * 1024 * 1024):
    require(path.stat().st_size <= limit, "JSON input exceeds its size limit")
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def resolved_profile(request):
    """Resolve portable configuration against its explicit profile file, never cwd."""
    profile = json.loads(json.dumps(request["profile"]))
    base = absolute(request["profile_path"]).parent if "profile_path" in request else None
    def path(value):
        selected = Path(value)
        if not selected.is_absolute():
            require(base is not None, "Relative configuration requires profile_path")
            selected = base / selected
        return str(selected.resolve())
    profile["input_roots"] = [path(value) for value in profile["input_roots"]]
    profile["output_root"] = path(profile["output_root"])
    graphics = profile["graphics"]
    for key in ("adapter", "python", "harness", "reader_build"):
        if key in graphics:
            graphics[key]["path"] = path(graphics[key]["path"])
    for row in graphics["source_pins"]:
        row["path"] = path(row["path"])
    return profile


def command(request, output):
    require(isinstance(request, dict) and {"command", "arguments", "profile"} <= set(request) <= {"command", "arguments", "profile", "profile_path"},
            "Expected command, arguments, and profile")
    job, args, profile = request["command"], request["arguments"], request["profile"]
    require(job in {"inspect", "diff"} and isinstance(args, dict), "Unsupported graphics operation")
    require(isinstance(profile, dict) and profile.get("schema") == "saturn-kit-profile-v1", "Unsupported profile")
    roots = [absolute(value) for value in profile["input_roots"]]
    require(bool(roots), "At least one input root is required")
    out_root = absolute(profile["output_root"])
    out = inside(output, [out_root], file=False)
    require(out != out_root, "Choose a fresh child of the configured output root")
    require(not out.exists(), "Adapter outputs already exist; preserve them and select a fresh folder")
    backend = out / "result"

    graphics = profile["graphics"]
    python = verify(graphics["python"])
    source_pins = graphics["source_pins"]
    require(isinstance(source_pins, list) and bool(source_pins), "Backend source pins are required")
    sources = [verify(row) for row in source_pins]
    require(ADDON / "saturn_graphics/__main__.py" in sources,
            "The configured inspector entry point must be pinned")
    timeout = graphics.get("timeout_seconds", 120)
    require(type(timeout) is int and 1 <= timeout <= 300, "Invalid bounded backend timeout")
    argv = [str(python), "-B", "-E", "-m", "saturn_graphics", "--profile", str(out / "backend-profile.json"), job]
    inputs = []
    if job == "inspect":
        require(set(args) <= {"input", "kind", "paired_capture"} and "input" in args, "Unsupported inspect arguments")
        source = inside(args["input"], roots)
        kind = args.get("kind", "auto")
        if kind == "auto":
            kind = "ymir-state" if source.suffix.lower() == ".ystate" else "original-fixture" if source.suffix.lower() == ".json" else "native"
        require(kind in {"native", "ymir-state", "original-fixture"}, "Unsupported graphics input kind")
        argv += ["--" + kind, str(source)]
        inputs.append(pin(source))
        harness = verify(graphics["harness"])
        argv += ["--harness", str(harness)]
        if kind == "ymir-state":
            require(bool(args.get("paired_capture")), "Original state requires a paired capture manifest")
            paired = inside(args["paired_capture"], roots)
            argv += ["--reader-build", str(verify(graphics["reader_build"])), "--paired-capture", str(paired)]
            inputs.append(pin(paired))
        else:
            require("paired_capture" not in args, "paired_capture only applies to an original state")
        result_kind, filename = "inspection", "inspection.json"
    else:
        require(set(args) <= {"before", "after", "premise"} and {"before", "after"} <= set(args), "Unsupported diff arguments")
        for name in ("before", "after", "premise"):
            if name in args:
                value = inside(args[name], roots)
                argv += ["--" + name, str(value)]
                inputs.append(pin(value))
        result_kind, filename = "diff", "diff.json"
    argv += ["--out", str(backend)]
    identities = [graphics["python"], *source_pins, *inputs]
    if job == "inspect":
        identities.append(graphics["harness"])
        if kind == "ymir-state":
            identities.append(graphics["reader_build"])
    return argv, out, backend / filename, result_kind, identities, timeout


def run(request, output):
    request = dict(request, profile=resolved_profile(request))
    argv, out, report_path, kind, identities, timeout = command(request, output)
    out.mkdir(parents=True, exist_ok=False)
    with (out / "backend-profile.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(request["profile"], stream, indent=2)
        stream.write("\n")
    env = os.environ.copy()
    # The bundled backend uses its explicit profile, with no inherited path policy.
    for name in ("PYTHONPATH", "PYTHONHOME", "GRANDIA_PROJECT_PATHS"):
        env.pop(name, None)
    invocation = {"schema": "saturn-kit-backend-invocation-v1", "command": argv,
                  "cwd": str(ADDON), "passed": False,
                  "offline_only": True, "builds": 0, "guest_runs": 0}
    try:
        with (out / "backend.stdout.log").open("xb") as stdout, (out / "backend.stderr.log").open("xb") as stderr:
            completed = subprocess.run(argv, cwd=invocation["cwd"], env=env,
                                       stdout=stdout, stderr=stderr, timeout=timeout, shell=False)
        invocation["returncode"] = completed.returncode
        require(completed.returncode == 0, "Qualified inspector failed; preserved backend logs")
        for row in identities:
            verify(row)
        report = read_json(report_path)
        images = {}
        if kind == "inspection":
            require(report.get("schema") == "grandia-graphics-inspection-v1", "Unexpected backend inspection schema")
            production = report["vdp2"]["production"]
            images.update(production["images"] if production else {})
            observed = report.get("captured_image_vs_static_reconstruction")
            if observed:
                images["capture-vs-static.png"] = observed["diff_image"]
            limits = report.get("limits", [])
        else:
            require(report.get("schema") == "grandia-graphics-field-diff-v1", "Unexpected backend diff schema")
            images.update({"diff-" + name: value["diff_image"] for name, value in report["pixels"].items() if "diff_image" in value})
            limits = report.get("premise_gaps", [])
        for row in images.values():
            require(verify(row).is_relative_to(out), "An output image escapes the fresh output folder")
        result = {"schema": "saturn-kit-adapter-result-v1", "backend": "bundled-saturn-graphics",
                  "kind": kind, "report": str(report_path), "report_pin": pin(report_path), "images": images,
                  "limitations": [*limits, "Static diagnostic only; matching scene, history and initial state are external premises."],
                  "invocation": str(out / "backend-invocation.json")}
        if kind == "inspection":
            # Backend-specific translation stays in this optional GPL add-on.
            fields = ("register_words", "register_fields", "commands", "summary", "control_fetches",
                      "legacy_continuation_gaps", "draw_order_source", "primary_order_complete", "order_limit")
            state = {"vdp1": {key: report["vdp1"][key] for key in fields if key in report["vdp1"]},
                     "vdp2": dict(report["vdp2"])}
            if production:
                state["vdp2"]["production"] = {key: value for key, value in production.items() if key != "images"}
            neutral = {"schema": "saturn-kit-capture-v1", "game": request["profile"]["game"],
                       "provenance": report["provenance"], "state": state,
                       "images": production["images"] if production else {}, "limitations": result["limitations"]}
            neutral_path = out / "neutral-capture.json"
            with neutral_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(neutral, stream, indent=2)
                stream.write("\n")
            result["neutral_capture"] = pin(neutral_path)
        invocation["passed"] = True
        return result
    except Exception as error:
        invocation["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        with (out / "backend-invocation.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(invocation, stream, indent=2)
            stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Absolute private JSON request")
    parser.add_argument("--out", required=True, help="Absolute fresh output folder created by the core")
    args = parser.parse_args()
    try:
        request_path = absolute(args.request)
        request = read_json(request_path, 1024 * 1024)
        print(json.dumps(run(request, args.out), separators=(",", ":")))
        return 0
    except Exception as error:
        print(json.dumps({"schema": "saturn-kit-adapter-error-v1", "error": type(error).__name__ + ": " + str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())

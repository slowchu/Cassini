# SPDX-License-Identifier: GPL-3.0-only
"""Explicitly build/run only the private offline production compositor harness."""
import json
import os
from pathlib import Path
import shlex
import subprocess

from .inputs import fresh_output, pin, private, read_json, utility, verify

PACKAGE = Path(__file__).resolve().parents[1] / "production"
RENDERER_FILES = ["src/video/" + n for n in ("vdp1.cpp", "vdp2.cpp", "png.cpp", "video.h", "saturnkit_rbg0.inc",
    "mednafen_excc.inc", "vdp1_raster_gpl.inc")] + ["src/machine/saturn.h", "src/machine/state.h",
    "include/saturn/sh2.h", "include/saturn/sh2_work.h", "include/saturn/sh2_time.h"]


def save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def dependency_paths(path):
    text = Path(path).read_text().replace("\\\n", "")
    if ": " not in text:
        raise ValueError("Malformed compiler dependency record")
    rows = shlex.split(text.split(": ", 1)[1])
    if not rows:
        raise ValueError("Empty compiler dependency closure")
    return [Path(p).resolve() for p in rows]


def build(output, compiler, runtime=None):
    """Explicit compile of a compatible production runtime; never invoked by inspect."""
    compiler = Path(compiler).resolve()
    source = Path(runtime).resolve() if runtime is not None else PACKAGE / "runtime"
    if not source.is_dir():
        raise ValueError("A compatible runtime directory is required")
    sources = [PACKAGE / "vdp1_bridge.cpp", PACKAGE / "compositor.cpp", source / "src/video/png.cpp"]
    dependencies = [source / name for name in RENDERER_FILES] + sources[:2]
    identities = [pin(p) for p in dependencies]
    compiler_identity = pin(compiler)
    directory = fresh_output(output)
    receipt = {"schema": "saturn-kit-offline-renderer-v1", "passed": False, "commands": [],
               "compiler": compiler_identity, "library_directory": str(compiler.parent), "source_files": identities,
               "runtime_selection": "external" if runtime is not None else "bundled", "runtime_root": str(source),
               "expected_dependency_files": identities}
    try:
        include = [source / "include", *[source / "src" / name for name in ("video", "machine", "host", "sound")]]
        env = os.environ.copy()
        env["PATH"] = str(compiler.parent) + os.pathsep + env.get("PATH", "")
        objects = []
        for i, src in enumerate(sources):
            obj = directory / (str(i) + ".o")
            objects.append(obj)
            command = [str(compiler), "-std=c++20", "-O2", "-DSATURN_VDP1_GPL=1", "-MMD", "-MF", str(directory / (str(i) + ".d")),
                       *[value for path in include for value in ("-I", str(path))], "-c", str(src), "-o", str(obj)]
            receipt["commands"].append(command)
            save(directory / "build.json", receipt)
            result = subprocess.run(command, env=env, capture_output=True, timeout=90)
            (directory / (str(i) + "-compile.log")).write_bytes(result.stdout + result.stderr)
            if result.returncode:
                raise ValueError("Explicit offline compile failed; preserved log")
        exe = directory / ("graphics-inspector.exe" if os.name == "nt" else "graphics-inspector")
        command = [str(compiler), *map(str, objects), "-o", str(exe)]
        receipt["commands"].append(command)
        result = subprocess.run(command, env=env, capture_output=True, timeout=60)
        (directory / "link.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError("Explicit offline link failed; preserved log")
        actual = {path for dep in directory.glob("*.d") for path in dependency_paths(dep)}
        expected = {path.resolve() for path in dependencies}
        receipt["actual_dependency_paths"] = [str(path) for path in sorted(actual)]
        receipt["actual_dependency_files"] = [pin(path) for path in sorted(actual)]
        if actual != expected:
            raise ValueError("Compiler dependency closure differs from the selected source closure")
        for row in identities:
            verify(row)
        receipt.update(passed=True, executable=pin(exe), dependency_files=[pin(path) for path in sorted(directory.glob("*.d"))])
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        save(directory / "build.json", receipt)
    return directory / "build.json"


def render(snapshot, build_path, directory):
    receipt = utility(build_path, "saturn-kit-offline-renderer-v1")
    if receipt.get("passed") is not True:
        raise ValueError("A completed explicit offline renderer is required")
    exe = verify(receipt["executable"])
    inputs = directory / "components"; inputs.mkdir(); snapshot.export(inputs)
    output = directory / "render"
    env = os.environ.copy(); env["PATH"] = str(Path(receipt["library_directory"])) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([str(exe), str(inputs), str(output)], capture_output=True, env=env, timeout=30)
    (directory / "harness.log").write_bytes(result.stdout + result.stderr)
    if result.returncode: raise ValueError("Production offline harness refused input; preserved harness.log")
    from PIL import Image
    expected = {"composite.png", "vdp1-framebuffer.png", "vdp1-layer.png", "NBG0.png", "NBG1.png", "NBG2.png", "NBG3.png", "RBG0.png"}
    if {p.name for p in output.glob("*.png")} != expected:
        raise ValueError("Missing production render output")
    for p in output.glob("*.png"):
        with Image.open(p) as im: im.verify()
    production = read_json(output / "production.json")
    production["production_warnings"] = [line for line in (result.stdout + result.stderr).decode(errors="replace").splitlines() if "not done" in line]
    production["harness_build"] = pin(build_path)
    production["images"] = {p.name: pin(p) for p in sorted(output.glob("*.png"))}
    production["view_policy"] = {"composite": "unchanged captured vector; whole static reconstruction, parity=-1",
        "framebuffer": "physical supplied bank decoded by production sprite_pixel; nondots/shadows/priority-zero are black; no windows/blending",
        "isolated_layers": "captured backscreen, windows and calculation retained; other layers/sprite priorities omitted in temporary vectors; not additive planes",
        "configuration_decode": "production setup helpers also decode disabled layer configuration with enable temporarily set; captured enable restored in report",
        "unknown_register_words": "synthetic zeros exist only in compositor input; report values remain unknown"}
    return production


def pixel_diff(before, after, output):
    from PIL import Image
    with Image.open(private(before)) as a, Image.open(private(after)) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
        w, h = max(a.width, b.width), max(a.height, b.height)
        result = Image.new("RGB", (w, h)); different = missing = added = 0
        ap, bp, rp = a.load(), b.load(), result.load()
        for y in range(h):
            for x in range(w):
                have_a, have_b = x < a.width and y < a.height, x < b.width and y < b.height
                if have_a and have_b:
                    va, vb = ap[x, y], bp[x, y]
                    different += va != vb
                    rp[x, y] = tuple(abs(u - v) for u, v in zip(va, vb))
                elif have_a: missing += 1; rp[x, y] = (255, 0, 255)
                elif have_b: added += 1; rp[x, y] = (0, 255, 255)
        result.save(output)
        return {"before": pin(before), "after": pin(after), "before_size": list(a.size), "after_size": list(b.size),
            "different_overlap_pixels": different, "missing_pixels": missing, "added_pixels": added,
            "equal": a.size == b.size and not different, "diff_image": pin(output),
            "policy": "all pixels, no crop, shift, rescale, colour normalization or masks"}


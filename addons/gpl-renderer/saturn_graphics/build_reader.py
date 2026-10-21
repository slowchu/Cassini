# SPDX-License-Identifier: GPL-3.0-only
"""Explicit, optional build of the retained generic Ymir archive13 reader."""
import os
from pathlib import Path
import subprocess

from .offline import dependency_paths, save
from .policy import fresh_output, pin, verify


def build(output, compiler, includes):
    directory = fresh_output(output)
    compiler = Path(compiler).resolve()
    includes = [Path(value).resolve() for value in includes]
    if not includes or any(not value.is_dir() for value in includes):
        raise ValueError("Explicit compatible Ymir/header include directories are required")
    source = Path(__file__).resolve().parents[1] / "production/reader/read_state.cpp"
    fixed = [pin(source), pin(source.with_name("named_dump.hpp"))]
    exe = directory / ("read_state.exe" if os.name == "nt" else "read_state")
    dep = directory / "reader.d"
    command = [str(compiler), "-std=c++20", "-O1", "-DWIN32_LEAN_AND_MEAN", "-DNOMINMAX", "-MMD", "-MF", str(dep),
               *[value for path in includes for value in ("-I", str(path))], str(source), "-o", str(exe)]
    receipt = {"schema": "saturn-kit-original-reader-v1", "passed": False, "archive_version_required": 13,
               "compiler": pin(compiler), "library_directory": str(compiler.parent), "command": command,
               "source_files": fixed, "format_qualification": "Build alone is not archive qualification; use retained state/manifest/paired-capture checks."}
    try:
        env = os.environ.copy()
        env["PATH"] = str(compiler.parent) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(command, capture_output=True, env=env, timeout=120)
        (directory / "build.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError("Explicit reader build failed; preserved build.log")
        paths = dependency_paths(dep)
        allowed = [source.parent, *includes]
        if any(not any(path.is_relative_to(root) for root in allowed) for path in paths):
            raise ValueError("Reader includes escape explicitly selected header roots")
        for row in fixed:
            verify(row)
        receipt.update(passed=True, executable=pin(exe), source_files=[pin(path) for path in sorted(set(paths))], dependency_file=pin(dep))
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        save(directory / "build.json", receipt)
    return directory / "build.json"

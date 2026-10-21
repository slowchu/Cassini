# SPDX-License-Identifier: GPL-3.0-only
"""Profile path/identity policy; no game checkout or historical-path resolver."""
import hashlib
import json
from pathlib import Path

_profile = None


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("JSON exceeds128MiB")
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def absolute(value, base=None):
    p = Path(value)
    if not p.is_absolute():
        if base is None:
            raise ValueError("Explicit absolute paths are required")
        p = base / p
    return p.resolve()


def configure(path):
    global _profile
    path = absolute(path)
    profile = read_json(path)
    if profile.get("schema") != "saturn-kit-profile-v1" or not isinstance(profile.get("input_roots"), list) or not profile["input_roots"]:
        raise ValueError("An explicit saturn-kit profile is required")
    profile["input_roots"] = [absolute(value, path.parent) for value in profile["input_roots"]]
    profile["output_root"] = absolute(profile["output_root"], path.parent)
    for key in ("harness", "reader_build"):
        if key in profile.get("graphics", {}):
            row = profile["graphics"][key]
            row["path"] = str(absolute(row["path"], path.parent))
    _profile = profile


def private(path, *, exists=True, output=False):
    if _profile is None:
        raise ValueError("Configure explicit input/output roots before using the backend")
    p = absolute(path)
    roots = [_profile["output_root"]] if output else [*_profile["input_roots"], _profile["output_root"]]
    if not any(p.is_relative_to(root) for root in roots):
        raise ValueError("Path escapes the configured private roots")
    if exists and not p.exists():
        raise ValueError("Missing configured input: " + str(p))
    return p


def fresh_output(path):
    p = private(path, exists=False, output=True)
    if p == _profile["output_root"] or p.exists():
        raise ValueError("Select a fresh child of the output root; preserve existing outputs")
    p.mkdir(parents=True)
    return p


def pin(path):
    p = absolute(path)
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return dict(path=str(p), bytes=p.stat().st_size, sha256=digest.hexdigest())


def verify(row):
    if not isinstance(row, dict) or type(row.get("bytes", row.get("size"))) is not int:
        raise ValueError("A complete file identity is required")
    p = absolute(row["path"])
    value = pin(p)
    if value["bytes"] != row.get("bytes", row.get("size")) or value["sha256"] != row.get("sha256"):
        raise ValueError("File identity changed: " + str(p))
    return p


def utility(path, schema):
    """Utilities come only from an explicitly pinned profile receipt, never a capture."""
    if _profile is None:
        raise ValueError("Missing profile")
    rows = _profile.get("graphics", {})
    key = "harness" if schema == "saturn-kit-offline-renderer-v1" else "reader_build"
    expected = rows.get(key)
    if not isinstance(expected, dict) or verify(expected) != absolute(path):
        raise ValueError("Utility receipt is not the pinned profile choice")
    value = read_json(path)
    if value.get("schema") != schema:
        raise ValueError("Unsupported neutral utility receipt")
    base = absolute(path).parent
    value["executable"]["path"] = str(absolute(value["executable"]["path"], base))
    verify(value["executable"])
    for row in value.get("source_files", []) + value.get("library_files", []):
        row["path"] = str(absolute(row["path"], base))
        verify(row)
    library = absolute(value["library_directory"], base)
    if not library.is_dir():
        raise ValueError("Missing explicit utility library directory")
    value["library_directory"] = str(library)
    return value

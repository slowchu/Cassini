# SPDX-License-Identifier: GPL-3.0-only
"""Compatibility entry point; renderer sources live only in ../gpl-renderer.

Profiles still need current adapter/source pins after this package update.
See ../gpl-renderer/LICENSE and PROVENANCE.json for grants and source origins.
"""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "gpl-renderer/adapter.py"), run_name="__main__")

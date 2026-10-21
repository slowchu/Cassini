# SPDX-License-Identifier: MIT
"""Compatibility launcher; the preferred entry point is C:/Cassini/cassini.py."""
import sys
sys.dont_write_bytecode = True
from saturn_kit.__main__ import main
raise SystemExit(main())

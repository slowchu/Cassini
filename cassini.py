# SPDX-License-Identifier: MIT
"""Cassini CLI: python -B C:/Cassini/cassini.py --help."""
import sys
sys.dont_write_bytecode = True
from saturn_kit.__main__ import main
raise SystemExit(main())

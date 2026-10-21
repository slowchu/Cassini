# SPDX-License-Identifier: MIT
"""Keep this bootstrap compatible with older Python so failures stay readable."""
import sys


def check_runtime():
    requirement = 'Cassini needs Python 3.11+ and Pillow.'
    if sys.version_info < (3, 11):
        raise SystemExit(requirement + ' Found Python %s.%s; select a Python 3.11+ interpreter.' % sys.version_info[:2])
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit(requirement + ' Pillow is unavailable in this interpreter. See README.md for setup.') from None

# SPDX-License-Identifier: MIT
"""Interpreter diagnostics and synthetic Windows alias errors; no game data."""
from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from saturn_kit import api, startup


class StartupTests(unittest.TestCase):
    def test_old_python_is_explained_before_pillow_import(self):
        with patch.object(sys, 'version_info', (3, 10, 11)):
            with self.assertRaisesRegex(SystemExit, 'Cassini needs Python 3.11\\+ and Pillow.*Found Python 3.10'):
                startup.check_runtime()

    def test_missing_pillow_has_clean_stderr_on_every_entrypoint(self):
        root = Path(__file__).resolve().parents[1]
        entries = [['cassini.py'], ['saturn-kit.py'], ['-m', 'saturn_kit'], ['-m', 'saturn_kit.mcp_server']]
        for entry in entries:
            with self.subTest(entry=entry):
                result = subprocess.run([sys.executable, '-B', '-S', *entry, '--help'], cwd=root,
                                        capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertIn('Cassini needs Python 3.11+ and Pillow.', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_store_alias_pins_the_actual_running_image(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'real-python.exe'
            binary.write_bytes(b'synthetic interpreter identity')
            alias = Path(folder) / 'store-alias.exe'
            original_resolve = Path.resolve
            error = OSError('Synthetic inaccessible Store alias'); error.winerror = 1920
            def resolve(path, *args, **kwargs):
                if path == alias:
                    raise error
                return original_resolve(path, *args, **kwargs)
            with patch.object(sys, 'executable', str(alias)), patch.object(sys, 'platform', 'win32'), \
                    patch.object(Path, 'resolve', resolve), patch.object(api, '_windows_executable', return_value=binary):
                actual = api.pin(sys.executable)
                self.assertEqual(Path(actual['path']), original_resolve(binary, strict=True))
                self.assertEqual(actual['sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())
                self.assertEqual(actual['bytes'], binary.stat().st_size)

    def test_data_paths_and_other_errors_still_fail_closed(self):
        for code, name in [(1920, 'unreadable-capture.bin'), (5, sys.executable)]:
            error = OSError('Synthetic access failure'); error.winerror = code
            with self.subTest(code=code), patch.object(Path, 'resolve', side_effect=error), \
                    patch.object(api, '_windows_executable') as fallback:
                with self.assertRaises(OSError):
                    api.pin(name)
                fallback.assert_not_called()


if __name__ == '__main__':
    unittest.main()

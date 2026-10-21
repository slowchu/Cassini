# SPDX-License-Identifier: MIT
"""Verify the exact distributable file set, per-file licences, origins and pins."""
from pathlib import Path
import hashlib
import json


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))
    assert manifest['schema'] == 'saturn-kit-provenance-v1'
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*')
              if p.is_file() and p.relative_to(root).parts[0] != '.git'}
    rows = manifest['files']
    assert len({row['path'] for row in rows}) == len(rows)
    assert actual == {row['path'] for row in rows}, 'Unaccounted or missing package file'
    for row in rows:
        path = root / row['path']
        assert path.resolve().is_relative_to(root) and not path.is_symlink(), 'Escaped/symlink file'
        assert row['license'] in {'MIT', 'GPL-3.0-only', 'GPL-2.0-or-later', 'MIT AND GPL-3.0-only'} and row['origin'], row['path']
        if not row['path'].startswith('addons/'):
            assert row['license'] == 'MIT', 'GPL code must remain outside the MIT core'
        if row.get('sources'):
            assert all(source.get('path') and len(source.get('sha256', '')) == 64 and source.get('license') for source in row['sources'])
        if row['path'] == 'provenance.json':
            assert row['sha256'] is None and row['origin'] == 'Generated per-file provenance manifest (self not hash-pinned)'
        else:
            data = path.read_bytes()
            assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'], 'File pin differs: ' + row['path']
    print(json.dumps(dict(passed=True, files=len(rows), game_data_included=False, core_license='MIT', optional_addon_license='GPL-3.0-only')))


if __name__ == '__main__':
    main()

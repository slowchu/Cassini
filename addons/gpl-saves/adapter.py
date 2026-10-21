# SPDX-License-Identifier: GPL-3.0-only
"""Save envelope transport; optional Ymir API converter never opens active saves."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from saturn_kit.api import Profile, pin, read_json, require, write_json


def native_record(bup):
    # Qualified community-save-audit native_record; only error wording is generic.
    require(len(bup) >= 64 and bup[:4] == b"Vmem", "Not a complete Vmem header")
    date, size = struct.unpack_from(">II", bup, 40)
    require(size == len(bup) - 64, "Payload length must close exactly")
    require(b"\0" in bup[16:28] and b"\0" in bup[28:39], "Native metadata must terminate")
    return bup[16:40] + struct.pack("<II", date, size) + bup[64:]


def records(data):
    result, at, names = [], 0, set()
    while at < len(data):
        require(len(data) - at >= 32, 'Truncated native header')
        header = data[at:at+32]
        require(b'\0' in header[:12] and b'\0' in header[12:23], 'Native metadata must terminate')
        name = header[:12].split(b'\0')[0]
        require(name and name not in names, 'Empty or duplicate native filename')
        names.add(name)
        date, size = struct.unpack_from('<II', header, 24)
        require(size > 0 and at + 32 + size <= len(data), 'Truncated native payload')
        payload = data[at+32:at+32+size]
        result.append(dict(offset=at, filename_bytes_hex=header[:12].hex(), comment_bytes_hex=header[12:23].hex(),
            language=header[23], date_minutes_since_1980=date, payload_bytes=size,
            payload_sha256=hashlib.sha256(payload).hexdigest()))
        at += 32 + size
    require(result, 'Empty save input')
    return result


def execute(request, out):
    require(request.get('command') == 'saves', 'Unsupported command')
    # Use the MCP launch-selected snapshot; profile_path supplies only its base directory.
    profile = Profile.__new__(Profile)
    profile.filename = Path(request['profile_path']).absolute()
    profile.data = request['profile']
    profile.roots = [profile.resolve(x) for x in profile.data['input_roots']]
    profile.output = profile.resolve(profile.data['output_root'])
    require(profile.data.get('allow_external_tools') is True, 'External tools disabled')
    out = profile.fresh(str(out))
    args = request['arguments']; source = profile.input(args['input']); before = pin(source)
    require(before['bytes'] <= 16*1024*1024, 'Save exceeds 16 MiB limit')
    raw = source.read_bytes(); fmt = args.get('format', 'auto')
    if fmt == 'auto':
        require(raw[:4] == b'Vmem', 'Auto detects BUP only; explicitly select native for flat records')
        fmt = 'bup'
    require(fmt in {'bup', 'native'}, 'Unsupported save format')
    native = native_record(raw) if fmt == 'bup' else raw
    rows = records(native); target = args.get('to'); outputs = {}
    require(target in {None, 'native', 'ymir'}, 'Unsupported conversion target')
    if target:
        native_path = out/'save.native'
        with native_path.open('xb') as stream: stream.write(native)
        outputs['native'] = pin(native_path)
        if fmt == 'bup':
            # Preserve the original envelope for exact reversal; never fabricate its opaque fields.
            with (out/'source-envelope.bin').open('xb') as stream: stream.write(raw[:64])
            outputs['source_envelope'] = pin(out/'source-envelope.bin')
    if target == 'ymir':
        converter = profile.verified(profile.data['saves']['converter'])
        image = out/'backup-ram.bin'
        result = subprocess.run([str(converter), str(native_path), str(image)], capture_output=True,
                                text=True, encoding='utf-8', timeout=120)
        (out/'converter.stdout.txt').write_text(result.stdout, encoding='utf-8')
        (out/'converter.stderr.txt').write_text(result.stderr, encoding='utf-8')
        require(result.returncode == 0 and 'metadata_and_payload_exact=true' in result.stdout, 'Ymir API converter failed')
        require(image.stat().st_size == 32768 and image.read_bytes()[:16] == b'BackUpRam Format', 'Invalid Ymir backup image')
        outputs['ymir'] = pin(image)
    require(pin(source) == before, 'Save source changed during conversion')
    report = dict(schema='saturn-kit-saves-v1', input=before, format=fmt, records=rows, converted=bool(target),
        target=target, outputs=outputs, source_modified=False, story_progress_inferred=False,
        checksum_validated=False, envelope_to_native_metadata_exact=True,
        ymir_metadata_policy='Ymir API normalizes bytes after first metadata NUL; payload and supported metadata are checked, original envelope is preserved separately.',
        limitations=['No save installation, story inference, game-checksum validation or CPU/RAM state import. Raw backup images and SSF files are not auto-detected.'])
    report_path = out/'saves.json'; write_json(report_path, report)
    return dict(schema='saturn-kit-adapter-result-v1', kind='saves', report=str(report_path), records=len(rows), converted=bool(target))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--request', required=True); p.add_argument('--out', required=True)
    a = p.parse_args()
    try: print(json.dumps(execute(read_json(a.request), Path(a.out))))
    except Exception as error:
        print(str(error), file=sys.stderr); raise SystemExit(1)

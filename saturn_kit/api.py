# SPDX-License-Identifier: MIT
"""CLI/MCP operations. Inputs are immutable; every result gets a fresh directory."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import wave

SCHEMA = 'saturn-kit-inspection-v1'
MAX_JSON = 64 * 1024 * 1024


def require(test, message):
    if not test:
        raise ValueError(message)


def read_json(path):
    p = Path(path)
    require(p.stat().st_size <= MAX_JSON, 'JSON exceeds 64 MiB limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(p.read_text(encoding='utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def _windows_executable():
    import ctypes
    function = ctypes.WinDLL('kernel32', use_last_error=True).GetModuleFileNameW
    function.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    function.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(None, buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def python_executable():
    """Return the running interpreter's hashable executable, including Store Python."""
    try:
        return Path(sys.executable).resolve(strict=True)
    except OSError as error:
        if sys.platform != 'win32' or getattr(error, 'winerror', None) != 1920:
            raise
        # A Store app execution alias is launchable but cannot be opened as a file.
        return _windows_executable().resolve(strict=True)


def pin(path):
    same_python = os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(sys.executable))
    p = python_executable() if same_python else Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(path=str(p), bytes=p.stat().st_size, sha256=digest.hexdigest())


class Profile:
    def __init__(self, filename):
        self.filename = Path(filename).resolve(strict=True)
        self.data = read_json(self.filename)
        require(self.data.get('schema') == 'saturn-kit-profile-v1', 'Unsupported profile schema')
        roots = self.data.get('input_roots')
        require(isinstance(roots, list) and roots and all(isinstance(x, str) for x in roots), 'input_roots required')
        self.roots = [self.resolve(x) for x in roots]
        self.output = self.resolve(self.data['output_root'])
        require(any(self.output.is_relative_to(root) for root in self.roots),
                'output_root must lie inside input_roots so produced reports and images can be read and compared')
        require(isinstance(self.data.get('game'), str), 'Profile game required')

    def resolve(self, name):
        require(isinstance(name, str) and bool(name), 'Path must be a nonempty string')
        p = Path(name)
        return (p if p.is_absolute() else self.filename.parent / p).resolve()

    def input(self, name):
        p = self.resolve(name)
        require(any(p.is_relative_to(root) for root in self.roots), 'Input outside profile input_roots')
        require(p.is_file(), 'Input is not a file: ' + str(p))
        return p

    def verified(self, row, base=None):
        require(isinstance(row, dict) and isinstance(row.get('path'), str), 'Pinned file required')
        name = Path(row['path'])
        if base is not None and not name.is_absolute():
            name = base / name
        p = self.input(str(name))
        actual = pin(p)
        require(type(row.get('bytes')) is int and row['bytes'] == actual['bytes'] and
                row.get('sha256') == actual['sha256'], 'File pin mismatch: ' + str(p))
        return p

    def fresh(self, name):
        p = self.resolve(name)
        require(p != self.output and p.is_relative_to(self.output), 'Output must be below profile output_root')
        require(not p.exists(), 'Output already exists; select a fresh directory')
        p.mkdir(parents=True, exist_ok=False)
        return p


def image_paths(profile, manifest, base):
    images = manifest.get('images', {})
    require(isinstance(images, dict), 'images must be an object')
    require(all(re.fullmatch(r'[A-Za-z0-9_.-]+', key) and key not in {'.', '..'} for key in images), 'Invalid image name')
    names = [key if key.endswith('.png') else key + '.png' for key in images]
    require(len({name.casefold() for name in names}) == len(names), 'Image names collide on a case-insensitive filesystem')
    devices = {'CON', 'PRN', 'AUX', 'NUL'} | {prefix + str(n) for prefix in ('COM', 'LPT') for n in range(1, 10)}
    require(all(name.split('.')[0].upper() not in devices and not name.endswith(('.', ' ')) for name in names), 'Reserved image filename')
    return {key: profile.verified(value, base) for key, value in images.items()}


def copy_images(profile, manifest, base, out):
    import shutil
    images = {}
    for key, source in image_paths(profile, manifest, base).items():
        require(source.suffix.lower() == '.png', 'Neutral images must be PNG')
        target = out / (key if key.endswith('.png') else key + '.png')
        expected = pin(source)
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        actual = pin(target)
        require((actual['bytes'], actual['sha256']) == (expected['bytes'], expected['sha256']) and
                pin(source) == expected, 'Image changed during copy')
        images[key] = actual
    return images


def external_inspect(profile, args, out):
    graphics = profile.data.get('graphics', {})
    require(profile.data.get('allow_external_tools') is True, 'External GPL adapter not enabled in this profile')
    adapter = graphics.get('adapter')
    require(isinstance(adapter, dict), 'Pinned graphics adapter required')
    # Executables are explicit trusted local profile settings, never capture-supplied commands.
    adapter_path = profile.verified(adapter)
    python_pin = graphics['python']
    python_path = profile.verified(python_pin)
    request = out / 'adapter-request.json'
    forwarded = {key: value for key, value in args.items() if key in {'input', 'kind', 'paired_capture'}}
    if forwarded.get('paired_capture'):
        forwarded['paired_capture'] = str(profile.input(forwarded['paired_capture']))
    write_json(request, dict(command='inspect', arguments=forwarded, profile=profile.data, profile_path=str(profile.filename)))
    command = [str(python_path), '-B', '-E', str(adapter_path), '--request', str(request), '--out', str(out / 'backend')]
    timeout = graphics.get('timeout_seconds', 120)
    require(type(timeout) is int and 1 <= timeout <= 300, 'Invalid adapter timeout')
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=timeout + 30)
    (out / 'adapter.stdout.txt').write_text(result.stdout, encoding='utf-8')
    (out / 'adapter.stderr.txt').write_text(result.stderr, encoding='utf-8')
    require(result.returncode == 0, 'Graphics adapter failed; see adapter.stderr.txt')
    response = json.loads(result.stdout)
    require(response.get('schema') == 'saturn-kit-adapter-result-v1' and response.get('kind') == 'inspection', 'Unexpected adapter response')
    source = profile.verified(response['neutral_capture'])
    require(source.is_relative_to(out), 'Adapter report escaped fresh result directory')
    manifest = read_json(source)
    require(manifest.get('schema') == 'saturn-kit-capture-v1', 'Adapter must emit a neutral capture-v1 manifest')
    return manifest, source.parent


def external_operation(profile, args, out, section, command):
    require(profile.data.get('allow_external_tools') is True, 'External GPL adapter not enabled in this profile')
    settings = profile.data.get(section, {})
    require(isinstance(settings, dict) and 'adapter' in settings and 'python' in settings, 'Pinned ' + section + ' adapter required')
    adapter, python = profile.verified(settings['adapter']), profile.verified(settings['python'])
    for row in settings.get('source_pins', []):
        profile.verified(row)
    paths = {'input', 'reference', 'delivery', 'events', 'recording', 'recipe'}
    forwarded = {key: (str(profile.input(value)) if key in paths else value)
                 for key, value in args.items() if key not in {'profile', 'out'}}
    inputs = {key: pin(value) for key, value in forwarded.items() if key in paths}
    request = out / 'adapter-request.json'
    write_json(request, dict(command=command, arguments=forwarded, profile=profile.data, profile_path=str(profile.filename)))
    timeout = settings.get('timeout_seconds', 600 if section == 'control' else 300)
    maximum = 600 if section == 'control' else 3600
    require(type(timeout) is int and 1 <= timeout <= maximum, 'Invalid adapter timeout')
    result = subprocess.run([str(python), '-B', '-E', str(adapter), '--request', str(request), '--out', str(out/'backend')],
                            capture_output=True, text=True, encoding='utf-8', timeout=timeout + 60)
    (out/'adapter.stdout.txt').write_text(result.stdout, encoding='utf-8')
    (out/'adapter.stderr.txt').write_text(result.stderr, encoding='utf-8')
    require(result.returncode == 0, section + ' adapter failed; see adapter.stderr.txt')
    response = read_json(out/'adapter.stdout.txt')
    expected = 'saturn-kit-control-result-v1' if section == 'control' else 'saturn-kit-adapter-result-v1'
    require(response.get('schema') == expected, 'Unexpected adapter response')
    report_path = profile.input(response['report'])
    require(report_path.is_relative_to(out), 'Adapter report escaped fresh result directory')
    report = read_json(report_path)
    require(isinstance(report, dict) and isinstance(report.get('schema'), str), 'Adapter report schema required')
    require(all(pin(value['path']) == value for value in inputs.values()), 'Adapter input changed')
    return dict(report=str(report_path), backend=section,
                **{key: response[key] for key in ('executed', 'passed', 'owned_process_closed', 'converted', 'records', 'candidates', 'scoring_valid') if key in response})


def inspect(profile, args, out):
    source = profile.input(args['input']); before = pin(source)
    manifest = read_json(source) if source.suffix.lower() == '.json' else None
    if isinstance(manifest, dict) and manifest.get('schema') in {'saturn-kit-capture-v1', 'saturn-kit-dump-v1', SCHEMA}:
        require(isinstance(manifest.get('state'), dict) and isinstance(manifest.get('provenance'), dict), 'Capture state/provenance required')
        base = source.parent
    else:
        manifest, base = external_inspect(profile, dict(args, input=str(source)), out)
    require(manifest.get('game') == profile.data['game'], 'Capture game differs from selected profile')
    require(isinstance(manifest.get('state'), dict) and isinstance(manifest.get('provenance'), dict), 'Capture state/provenance required')
    images = copy_images(profile, manifest, base, out)
    buffers = manifest.get('buffers', {})
    require(isinstance(buffers, dict), 'buffers must be an object')
    buffers = {key: dict(row, path=str(profile.verified(row, base))) for key, row in buffers.items()}
    report = dict(schema=SCHEMA, game=profile.data['game'], input=before,
                  provenance=manifest['provenance'], state=manifest['state'], images=images, buffers=buffers,
                  limitations=manifest.get('limitations', []), source_modified=False)
    require(pin(source) == before, 'Inspection input changed')
    write_json(out / 'inspection.json', report)
    return dict(report=str(out / 'inspection.json'), images={key: row['path'] for key, row in images.items()},
                limitations=report['limitations'])


def differences(before, after, path=''):
    if type(before) is not type(after):
        return [dict(path=path, kind='changed', old=before, new=after)]
    result = []
    if isinstance(before, dict):
        for key in sorted(before.keys() | after.keys()):
            child = path + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in before: result.append(dict(path=child, kind='added', new=after[key]))
            elif key not in after: result.append(dict(path=child, kind='missing', old=before[key]))
            else: result.extend(differences(before[key], after[key], child))
    elif isinstance(before, list):
        for i in range(max(len(before), len(after))):
            child = path + '/' + str(i)
            if i >= len(before): result.append(dict(path=child, kind='added', new=after[i]))
            elif i >= len(after): result.append(dict(path=child, kind='missing', old=before[i]))
            else: result.extend(differences(before[i], after[i], child))
    elif before != after:
        result.append(dict(path=path, kind='changed', old=before, new=after))
    return result


def pixel_diff(before, after, destination):
    from PIL import Image, ImageChops
    with Image.open(before) as ai, Image.open(after) as bi:
        require(ai.format == bi.format == 'PNG', 'Image inputs must be PNG')
        a, b = ai.convert('RGBA'), bi.convert('RGBA')
        width, height = min(a.width, b.width), min(a.height, b.height)
        changed = sum(x != y for x, y in zip(a.crop((0, 0, width, height)).getdata(), b.crop((0, 0, width, height)).getdata()))
        aw, bw = Image.new('RGBA', (max(a.width, b.width), max(a.height, b.height))), Image.new('RGBA', (max(a.width, b.width), max(a.height, b.height)))
        aw.paste(a, (0, 0)); bw.paste(b, (0, 0))
        diff = ImageChops.difference(aw, bw)
        # Show alpha-only differences, and mark non-overlap even if transparent.
        pixels = diff.load()
        for y in range(diff.height):
            for x in range(diff.width):
                if x >= width or y >= height:
                    if (x < a.width and y < a.height) or (x < b.width and y < b.height): pixels[x, y] = (255, 0, 255, 255)
                elif pixels[x, y][3]: pixels[x, y] = (255, 255, 0, 255)
        diff.convert('RGB').save(destination)
        missing, added = a.width * a.height - width * height, b.width * b.height - width * height
        return dict(equal=changed == missing == added == 0, before_size=list(a.size), after_size=list(b.size),
                    different_overlap_pixels=changed, missing_pixels=missing, added_pixels=added, image=pin(destination))


def diff(profile, args, out):
    ap, bp = profile.input(args['before']), profile.input(args['after'])
    before_pins = pin(ap), pin(bp)
    a, b = read_json(ap), read_json(bp)
    require(a.get('schema') == b.get('schema') == SCHEMA, 'Diff requires neutral inspection-v1 reports')
    require(a['game'] == b['game'] == profile.data['game'], 'Different game profiles')
    delta = differences(a['state'], b['state'])
    ai, bi = image_paths(profile, a, ap.parent), image_paths(profile, b, bp.parent)
    images = {}
    for key in sorted(ai.keys() | bi.keys()):
        if key not in ai: images[key] = dict(kind='added', input=pin(bi[key]))
        elif key not in bi: images[key] = dict(kind='missing', input=pin(ai[key]))
        else: images[key] = pixel_diff(ai[key], bi[key], out / ('diff-' + key + '.png'))
    premise_path = profile.input(args['premise']) if args.get('premise') else None
    require((pin(ap), pin(bp)) == before_pins, 'Inspection report changed during comparison')
    image_paths(profile, a, ap.parent); image_paths(profile, b, bp.parent)
    report = dict(schema='saturn-kit-diff-v1', before=pin(ap), after=pin(bp), fields=delta, images=images,
                  supplied_premise=read_json(premise_path) if premise_path else None,
                  premise=pin(premise_path) if premise_path else None,
                  conformance_claim=False, policy='All represented state fields and complete PNG extents; no alignment/crop/mask. A supplied premise is not verified by the kit.')
    write_json(out / 'diff.json', report)
    return dict(report=str(out / 'diff.json'), different_fields=len(delta), images={key: row['image']['path'] for key, row in images.items() if 'image' in row}, conformance_claim=False)


def hw_report(profile, args, out):
    inputs = args['inputs']; require(isinstance(inputs, list) and inputs, 'At least one log required')
    totals, events, rows = Counter(), Counter(), []
    prefixes = {'cpu-reg', 'reg', 'cpu-area', 'area', 'call', 'cpu-hle-entry', 'watch'}
    for name in inputs:
        source = profile.input(name); before = pin(source)
        matched, unmatched = 0, 0
        with source.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                line = line.strip()
                if not line: continue
                parts = line.rsplit(None, 1)
                if line.split(None, 1)[0] in prefixes and len(parts) == 2 and re.fullmatch(r'[0-9]+', parts[1]):
                    key, count = parts[0], int(parts[1])
                    require(key not in totals, 'Duplicate hardware counter key; use one final snapshot or separate reports per run: ' + key)
                    totals[key] = count; matched += 1
                else:
                    # Guest lines are event frequencies, never fabricated hardware access counts.
                    events[line.split(None, 1)[0]] += 1; unmatched += 1
        require(pin(source) == before, 'Log changed while reporting; select a closed recording')
        rows.append(dict(input=before, counted_rows=matched, event_only_rows=unmatched))
    report = dict(schema='saturn-kit-hw-report-v1', inputs=rows,
        counters=[dict(key=key, count=count) for key, count in sorted(totals.items(), key=lambda x: (-x[1], x[0]))],
        event_prefix_frequencies=dict(events), policy='Recognized saturn-recomp-hw-v1 records are indexed by complete key. Repeated keys, within/across inputs, are rejected to prevent double-counting cumulative snapshots. Other rows are event frequencies only. Global and per-CPU records overlap.',
        host_cpu_time_inferred=False, per_frame_inferred=False)
    write_json(out / 'hw-report.json', report)
    (out / 'hw-report.txt').write_text('Hardware counts (global/per-CPU rows overlap; no CPU-time inference)\n' +
        '\n'.join(str(row['count']) + ' ' + row['key'] for row in report['counters']) + '\n', encoding='utf-8')
    return dict(report=str(out / 'hw-report.json'), counter_keys=len(totals), counted_rows=sum(x['counted_rows'] for x in rows), event_only_rows=sum(x['event_only_rows'] for x in rows), host_cpu_time_inferred=False)


def saves(profile, args, out):
    if args.get('to') or profile.data.get('saves'):
        return external_operation(profile, args, out, 'saves', 'saves')
    p = profile.input(args['input']); before = pin(p)
    require(before['bytes'] <= 16 * 1024 * 1024, 'Save exceeds 16 MiB limit')
    data = p.read_bytes(); fmt = args.get('format', 'auto')
    if fmt == 'auto':
        require(data[:4] == b'Vmem', 'Auto detects BUP only; use --format native for flat records. Raw backup-RAM images are unsupported.')
        fmt = 'bup'
    require(fmt in {'bup', 'native'}, 'Unknown save format')
    records, offset = [], 0
    while offset < len(data):
        header = 64 if fmt == 'bup' else 32
        require(len(data) - offset >= header, 'Truncated save header')
        if fmt == 'bup':
            require(offset == 0 and data[:4] == b'Vmem', 'Invalid BUP envelope')
            size = int.from_bytes(data[44:48], 'big'); start = 16
        else:
            size = int.from_bytes(data[offset+28:offset+32], 'little'); start = offset
        require(size > 0 and offset + header + size <= len(data), 'Invalid save length')
        records.append(dict(offset=offset, filename=data[start:start+11].split(b'\0')[0].decode('ascii', 'replace'), payload_bytes=size))
        offset += header + size
        if fmt == 'bup': require(offset == len(data), 'Trailing BUP bytes')
    require(records, 'Empty save input')
    require(pin(p) == before, 'Save changed while reading; select a closed copy')
    write_json(out / 'saves.json', dict(schema='saturn-kit-saves-v1', input=before, format=fmt, records=records, experimental=True, converted=False, checksum_validated=False, story_progress_inferred=False))
    return dict(report=str(out / 'saves.json'), records=len(records), experimental=True, converted=False)


def audio_gaps(profile, args, out):
    if args.get('delivery') or args.get('events') or profile.data.get('audio'):
        return external_operation(profile, args, out, 'audio', 'audio-gaps')
    p = profile.input(args['input']); before = pin(p); threshold = args.get('threshold_ms', 20)
    require(type(threshold) in (int, float) and math.isfinite(threshold) and threshold > 0, 'Positive finite threshold required')
    silent, first, total = [], None, 0
    with wave.open(str(p), 'rb') as stream:
        require(stream.getsampwidth() == 2 and stream.getcomptype() == 'NONE', 'Only uncompressed 16-bit PCM WAV supported')
        channels, rate = stream.getnchannels(), stream.getframerate()
        require(channels > 0 and rate > 0, 'Invalid audio format')
        frame_bytes = channels * 2; minimum = math.ceil(threshold * rate / 1000)
        while True:
            block = stream.readframes(65536)
            if not block: break
            require(len(block) % frame_bytes == 0, 'Truncated PCM frame')
            for pos in range(0, len(block), frame_bytes):
                zero = not any(block[pos:pos+frame_bytes])
                if zero and first is None: first = total
                elif not zero and first is not None:
                    if total-first >= minimum: silent.append(dict(start_frame=first, frames=total-first))
                    first = None
                total += 1
        if first is not None and total-first >= minimum: silent.append(dict(start_frame=first, frames=total-first))
    require(pin(p) == before, 'Audio changed during analysis')
    report = dict(schema='saturn-kit-audio-gaps-v1', input=before, channels=channels, sample_rate=rate,
                  frames=total, threshold_ms=threshold, exact_digital_silence=silent,
                  experimental=True, audible_dropout_proven=False, policy='Digital silence candidates only; intended silence and device delivery are unknown.')
    if args.get('reference'):
        reference = profile.input(args['reference']); report['reference'] = pin(reference)
        report['whole_file_equal'] = report['reference']['sha256'] == report['input']['sha256']
        report['alignment_inferred'] = False
    write_json(out / 'audio-gaps.json', report)
    return dict(report=str(out / 'audio-gaps.json'), candidates=len(silent), experimental=True, audible_dropout_proven=False)


def plan(profile, args, out, command):
    require(type(args.get('execute', False)) is bool, 'execute must be boolean')
    if args.get('execute', False):
        require(profile.data.get('control'), 'Live execution is unavailable without a pinned control adapter')
        return external_operation(profile, args, out, 'control', command)
    source = profile.input(args['recording' if command == 'replay' else 'recipe'])
    if command == 'replay': require(type(args['frames']) is int and 0 < args['frames'] <= 1000000, 'Invalid frame bound')
    else: require(isinstance(read_json(source), dict), 'Capture recipe must be a JSON object')
    write_json(out / 'plan.json', dict(schema='saturn-kit-job-plan-v1', command=command, input=pin(source),
        game=profile.data['game'], frame_bound=args.get('frames'), executed=False, status='PLANNING-ONLY',
        limitation='Planning only. Pass execute=true with a reviewed pinned control profile to launch an isolated job.'))
    return dict(report=str(out / 'plan.json'), executed=False, status='PLANNING-ONLY')


OPERATIONS = {'inspect': inspect, 'diff': diff, 'hw-report': hw_report, 'saves': saves, 'audio-gaps': audio_gaps}


def run(command, arguments, *, trusted_profile=None):
    require(command in set(OPERATIONS) | {'replay', 'capture-original'}, 'Unknown command')
    require(isinstance(arguments, dict), 'Arguments must be an object')
    require(trusted_profile is None or isinstance(trusted_profile, Profile), 'Invalid trusted profile')
    # MCP supplies its launch-time snapshot so a file edit cannot race a reload.
    profile = trusted_profile if trusted_profile is not None else Profile(arguments['profile'])
    out = profile.fresh(arguments['out'])
    try:
        result = OPERATIONS[command](profile, arguments, out) if command in OPERATIONS else plan(profile, arguments, out, command)
        return dict(schema='saturn-kit-result-v1', command=command, output=str(out), **result)
    except Exception as error:
        write_json(out / 'failure.json', dict(schema='saturn-kit-failure-v1', command=command, error=str(error)))
        raise

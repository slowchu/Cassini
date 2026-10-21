# SPDX-License-Identifier: MIT
"""Generate synthetic captures and exercise Cassini's real offline CLI.

No game, emulator, original capture or production renderer is used. The supplied
output must be a new directory outside the kit; existing results are preserved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
from saturn_kit.startup import check_runtime
check_runtime()


def write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def pin(path):
    return dict(path=str(path), bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def draw(path, x):
    from PIL import Image, ImageDraw, PngImagePlugin
    image = Image.new('RGB', (160, 96), (11, 17, 28))
    drawing = ImageDraw.Draw(image)
    for gx in range(0, 160, 8):
        drawing.line((gx, 0, gx, 95), fill=(24, 36, 52))
    for gy in range(0, 96, 8):
        drawing.line((0, gy, 159, gy), fill=(24, 36, 52))
    drawing.rectangle((x, 32, x + 31, 63), fill=(43, 219, 155))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Source', 'Cassini synthetic example; no game or production renderer')
    image.save(path, pnginfo=metadata)


def run(destination):
    destination = Path(destination).expanduser().resolve()
    if destination == KIT or destination.is_relative_to(KIT):
        raise ValueError('Choose an output directory outside the Cassini source kit')
    if destination.exists():
        raise ValueError('Output already exists; choose a fresh directory name')
    from PIL import Image
    destination.mkdir(parents=True, exist_ok=False)
    inputs = destination / 'inputs'; inputs.mkdir()
    results = destination / 'results'
    captures = {}
    for name, x in (('before', 32), ('after', 48)):
        image = inputs / (name + '.png')
        draw(image, x)
        captures[name] = inputs / (name + '.json')
        image_pin = pin(image); image_pin['path'] = image.name
        write(captures[name], dict(schema='saturn-kit-capture-v1', game='cassini-synthetic-demo',
            provenance=dict(origin='synthetic', debug_derived=False, matched_moment=False,
                            producer='examples/demo.py', production_renderer_used=False),
            state=dict(vdp1=dict(commands=[dict(type='synthetic-rectangle', x=x, y=32, width=32, height=32)]),
                       vdp2=dict(layers=[dict(name='synthetic-background', enabled=True)])),
            images=dict(composite=image_pin), buffers={},
            limitations=['Hand-generated shapes and declared example fields; no Saturn command encoding, game, or production renderer.']))
    profile = destination / 'profile.json'
    write(profile, dict(schema='saturn-kit-profile-v1', game='cassini-synthetic-demo',
                        input_roots=['.'], output_root='results', allow_external_tools=False))
    premise = inputs / 'premise.json'
    write(premise, dict(synthetic_demo=True, complete_renderer_inputs_matched=False,
                        gameplay_equivalence=False, production_renderer_used=False,
                        note='Known declared state change in hand-generated images.'))
    hardware = inputs / 'hardware.log'
    with hardware.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write('reg R 2 05D00000 12\nreg W 2 05F80000 3\nsynthetic-event example-only\n')
    commands = []
    def cli(command, *arguments):
        argv = [sys.executable, '-B', '-E', str(KIT / 'cassini.py'), command,
                '--profile', str(profile), *arguments]
        process = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=30)
        commands.append(dict(argv=argv, returncode=process.returncode,
                             stdout=process.stdout, stderr=process.stderr))
        write(destination / ('command-' + str(len(commands)) + '.json'), commands[-1])
        if process.returncode:
            raise RuntimeError('Cassini analysis failed; see retained command-' + str(len(commands)) + '.json')
        return json.loads(process.stdout)
    inspected = {}
    for name in ('before', 'after'):
        inspected[name] = cli('inspect', '--input', str(captures[name]), '--out', str(results / name))
    compared = cli('diff', '--before', inspected['before']['report'], '--after', inspected['after']['report'],
                   '--premise', str(premise), '--out', str(results / 'comparison'))
    counted = cli('hw-report', '--inputs', str(hardware), '--out', str(results / 'hardware'))
    diff = json.loads(Path(compared['report']).read_text(encoding='utf-8'))
    log = json.loads(Path(counted['report']).read_text(encoding='utf-8'))
    expected = [dict(path='/vdp1/commands/0/x', kind='changed', old=32, new=48)]
    pixels = diff['images']['composite']
    if diff['fields'] != expected or pixels['different_overlap_pixels'] != 1024 or pixels['missing_pixels'] or pixels['added_pixels']:
        raise RuntimeError('Synthetic example differs from its declared one-field/1024-pixel result')
    if len(log['counters']) != 2 or log['host_cpu_time_inferred'] or diff['conformance_claim']:
        raise RuntimeError('Synthetic example count or evidence limit differs')
    with Image.open(pixels['image']['path']) as image:
        if image.size != (160, 96):
            raise RuntimeError('Unexpected synthetic diff dimensions')
    summary = dict(schema='cassini-synthetic-demo-v1', synthetic=True, game_data_used=False,
                   production_renderer_used=False, output=str(destination),
                   changed_fields=diff['fields'], different_pixels=pixels['different_overlap_pixels'],
                   hardware_counter_keys=len(log['counters']), conformance_claim=False,
                   reports=dict(before=inspected['before']['report'], after=inspected['after']['report'],
                                diff=compared['report'], hardware=counted['report']),
                   images=dict(before=pin(inputs / 'before.png'), after=pin(inputs / 'after.png'),
                               diff=pixels['image']))
    write(destination / 'demo-summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='Fresh directory outside the source kit')
    args = parser.parse_args()
    try:
        summary = run(args.out)
        print('State: /vdp1/commands/0/x changed from 32 to 48')
        print('Image: 1024 different pixels; no missing or added pixels')
        print('Hardware log: 2 counter keys; no CPU-time inference')
        print('Synthetic example; no game or production renderer used.')
        print('Reports: ' + str(Path(summary['output']) / 'demo-summary.json'))
        return 0
    except (ValueError, OSError, RuntimeError, ImportError, subprocess.TimeoutExpired) as error:
        print('Demo failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

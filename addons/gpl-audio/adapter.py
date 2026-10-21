# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Saturn recomp agent kit contributors
"""Offline process-loopback delivery analysis; no capture or guest is started."""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from audio_math import Packets, TICKS, pcm, quiet_runs, playback_runs, read_supply, supply_interval


def need(value, message):
    if not value:
        raise ValueError(message)


def pin(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return dict(path=str(path), bytes=path.stat().st_size, sha256=digest.hexdigest())


def read_json(path):
    need(path.stat().st_size <= 16 * 1024 * 1024, 'JSON exceeds 16 MiB')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


class Inputs:
    def __init__(self, request):
        profile = request['profile']
        need(profile.get('schema') == 'saturn-kit-profile-v1', 'Unsupported profile')
        self.base = Path(request.get('profile_path', '.')).resolve().parent
        self.roots = [self.resolve(value) for value in profile['input_roots']]
        need(self.roots, 'Input roots required')
        self.output = self.resolve(profile['output_root'])
        self.before = {}
        self.maximum = profile.get('audio', {}).get('maximum_audio_bytes', 256 * 1024 * 1024)
        need(type(self.maximum) is int and 1 <= self.maximum <= 1024 * 1024 * 1024, 'Invalid audio byte bound')

    def resolve(self, value, base=None):
        need(isinstance(value, str) and value, 'Path string required')
        path = Path(value)
        return (path if path.is_absolute() else (base or self.base) / path).resolve()

    def file(self, value, base=None):
        path = self.resolve(value, base)
        need(any(path.is_relative_to(root) for root in self.roots), 'Input leaves profile roots')
        need(path.is_file(), 'Input must be a regular file')
        need(path.stat().st_size <= self.maximum, 'Input exceeds configured byte bound')
        actual = pin(path)
        if path in self.before:
            need(actual == self.before[path], 'Input changed during analysis')
        self.before[path] = actual
        return path

    def verified(self, row, base):
        need(isinstance(row, dict), 'Pinned delivery artifact required')
        path = self.file(row['path'], base)
        need(type(row.get('bytes')) is int and row['bytes'] == self.before[path]['bytes'] and
             row.get('sha256') == self.before[path]['sha256'], 'Delivery artifact pin differs')
        return path

    def finish(self):
        need(all(pin(path) == row for path, row in self.before.items()), 'Input changed during analysis')


def settings(profile, args):
    spec = profile.get('audio', {})
    rate, channels = spec.get('rate', 44100), spec.get('channels', 2)
    need(type(rate) is int and 1 <= rate <= 384000 and type(channels) is int and 1 <= channels <= 8,
         'Invalid sample rate/channel count')
    thresholds = spec.get('thresholds_s16', [2, 16])
    need(isinstance(thresholds, list) and 1 <= len(thresholds) <= 16 and
         all(type(t) is int and 0 <= t <= 32767 for t in thresholds) and
         thresholds == sorted(set(thresholds)), 'Invalid ordered signed16 thresholds')
    minimum = args.get('threshold_ms', spec.get('minimum_run_ms', 5))
    secondary = spec.get('secondary_minimum_run_ms', max(20, minimum))
    need(type(minimum) in (int, float) and math.isfinite(minimum) and 0 < minimum <= 600000,
         'Invalid quiet interval duration')
    need(type(secondary) in (int, float) and math.isfinite(secondary) and minimum <= secondary <= 600000,
         'Secondary duration must be finite and at least the primary duration')
    return dict(rate=rate, channels=channels, thresholds=thresholds, minimum_ms=minimum,
                minimum_frames=math.ceil(minimum * rate / 1000),
                secondary_ms=secondary, secondary_frames=math.ceil(secondary * rate / 1000))


def packet_rows(path):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        need(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)) and
             {'file_frame', 'frames', 'flags', 'qpc_100ns'} <= set(reader.fieldnames), 'Invalid packet CSV header')
        rows = []
        for row in reader:
            need(None not in row and all(value is not None for value in row.values()), 'Partial packet CSV row')
            values = {key: int(value) for key, value in row.items()}
            need(all(value >= 0 for value in values.values()), 'Negative packet field')
            rows.append(values)
    return rows


def checked_supply(path):
    # Keep the donor reader/arithmetic, but reject malformed boolean spellings
    # before its deliberate CSV conversion of True/False to bool.
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        need(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), 'Duplicate supply CSV header')
        for row in reader:
            need(None not in row and None not in row.values() and
                 row.get('same_values_across_read') in {'True', 'False'}, 'Malformed supply row/boolean')
    return read_supply(path)


def quiet_summary(runs, rate):
    return [dict(start_frame=a, end_frame=b, milliseconds=(b-a) * 1000 / rate) for a, b in runs]


def score_window(name, lo, hi, packets, playback_quiet, generated_quiet, cfg, *, generated_bounds=None,
                 supply=None, vblank=None, events=None):
    need(lo < hi, 'Window must have positive duration')
    quality = packets.quality(lo, hi)
    result = dict(qpc_inner_window_100ns=[float(lo), float(hi)],
                  packet_quality=quality, scoring_valid=quality['passed'],
                  playback_nearzero_results={str(t): playback_runs(runs, packets, lo, hi)
                                            for t, runs in playback_quiet.items()},
                  secondary_nearzero_results={str(t): playback_runs(
                      [(a,b) for a,b in runs if b-a >= cfg['secondary_frames']], packets, lo, hi)
                      for t, runs in playback_quiet.items()},
                  generated_alignment_known=generated_bounds is not None)
    if generated_bounds is not None:
        g0, g1 = generated_bounds
        result.update(generated_frame_inner_window=[g0,g1], supply_bounds=supply,
                      actual_observed_vblank_bounds=vblank, pad_event_brackets=events,
                      pad_counter_bounds=[supply['underruns_delta_lower'], supply['underruns_delta_upper']],
                      dropped_counter_bounds=[supply['dropped_delta_lower'], supply['dropped_delta_upper']],
                      generated_sustained_nearzero_runs={str(t): quiet_summary(
                          [(a,b) for a,b in runs if g0 <= a and b <= g1], cfg['rate'])
                          for t,runs in generated_quiet.items()},
                      generated_boundary_runs_omitted_without_clipping={str(t): quiet_summary(
                          [(a,b) for a,b in runs if a < g1 and b > g0 and not(g0 <= a and b <= g1)], cfg['rate'])
                          for t,runs in generated_quiet.items()})
    return result


def analyze(request, output):
    need(request.get('command') == 'audio-gaps' and isinstance(request.get('arguments'), dict), 'Unsupported audio job')
    args, profile = request['arguments'], request['profile']
    cfg = settings(profile, args)
    inputs = Inputs(request)
    out = inputs.resolve(str(output))
    need(out != inputs.output and out.is_relative_to(inputs.output) and not out.exists(), 'Fresh private output required')
    playback_path = inputs.file(args['input'])
    generated_path = inputs.file(args['reference']) if args.get('reference') else None
    audio = pcm(playback_path, cfg['rate'], cfg['channels'])
    generated = pcm(generated_path, cfg['rate'], cfg['channels']) if generated_path else None
    need(len(audio)>0 and (generated is None or len(generated)>0), 'Empty PCM input')
    quiet = {t: quiet_runs(audio,t,cfg['minimum_frames']) for t in cfg['thresholds']}
    gquiet = {t: quiet_runs(generated,t,cfg['minimum_frames']) for t in cfg['thresholds']} if generated is not None else {}
    result = dict(schema='saturn-kit-audio-delivery-analysis-v1', settings=cfg,
                  playback=inputs.before[playback_path], playback_frames=len(audio),
                  generated=inputs.before.get(generated_path), generated_frames=len(generated) if generated is not None else None,
                  whole_playback_nearzero_runs={str(t):quiet_summary(runs,cfg['rate']) for t,runs in quiet.items()},
                  whole_generated_nearzero_runs={str(t):quiet_summary(runs,cfg['rate']) for t,runs in gquiet.items()},
                  windows={}, delivery_evidence=False, waveform_alignment_inferred=False,
                  audible_dropout_proven=False, missing_time_filled=False, boundary_runs_clipped=False,
                  limits=['Near-zero PCM can be intentional silence; it does not prove a delivery underrun.',
                          'No waveform alignment or physical device/headphone observation is inferred.',
                          'Packet flags/gaps invalidate affected scoring windows; no missing time is filled.',
                          'Supply counter reads are non-atomic and retain interval uncertainty.',
                          'Capture/source identity is checked, not complete gameplay/save equivalence.'])
    if args.get('delivery'):
        delivery_path = inputs.file(args['delivery'])
        declaration = read_json(delivery_path)
        need(declaration.get('schema') == 'saturn-kit-audio-delivery-v1', 'Unsupported delivery manifest')
        need(inputs.verified(declaration['playback'],delivery_path.parent)==playback_path, 'Manifest playback identity differs')
        if declaration.get('generated'):
            need(generated_path is not None and inputs.verified(declaration['generated'],delivery_path.parent)==generated_path,
                 'Manifest generated PCM identity differs')
        packets_path = inputs.verified(declaration['packets'], delivery_path.parent)
        capture_path = inputs.verified(declaration['capture_metadata'], delivery_path.parent)
        metadata = read_json(capture_path)
        rows = packet_rows(packets_path)
        packets = Packets(rows, len(audio), cfg['rate'])
        frequency = metadata.get('qpc_frequency')
        need(type(frequency) is int and frequency > 0, 'Positive integer QPC frequency required')
        need(metadata.get('include_only_process_tree') is True or metadata.get('scope') == 'target-process-tree',
             'Delivery capture must identify target-process-tree scope')
        need(metadata.get('rate') == cfg['rate'] and metadata.get('frames') == len(audio) and
             metadata.get('packets') == len(rows), 'Capture/WAV/packet totals differ')
        need(metadata.get('discontinuity_packets') == sum(bool(r['flags'] & 1) for r in rows) and
             metadata.get('timestamp_error_packets') == sum(bool(r['flags'] & 4) for r in rows), 'Capture flag totals differ')
        expected_frequency = profile.get('audio', {}).get('qpc_frequency', frequency)
        need(type(expected_frequency) is int and expected_frequency == frequency, 'Profile/capture QPC frequency differs')
        result.update(delivery_evidence=True, global_packet_issues=packets.issues,
                      capture_flags_summary={str(f):sum(r['flags']==f for r in rows) for f in sorted({r['flags'] for r in rows})},
                      capture_metadata=inputs.before[capture_path], packet_csv=inputs.before[packets_path])
        supply_path = inputs.verified(declaration['supply'], delivery_path.parent) if declaration.get('supply') else None
        if args.get('events'):
            supplied_events = inputs.file(args['events'])
            need(supply_path is None or supplied_events == supply_path, 'Explicit events differ from manifest supply')
            supply_path = supplied_events
        if supply_path:
            need(generated is not None, 'Generated PCM reference required for supply alignment')
            need(declaration.get('generated') is not None, 'Supply alignment requires a pinned generated PCM artifact')
            observations = checked_supply(supply_path)
            if declaration.get('supply_metadata'):
                observer_path = inputs.verified(declaration['supply_metadata'], delivery_path.parent)
                observer = read_json(observer_path)
                need(observer.get('passed') is True and observer.get('complete') is True and
                     observer.get('qpc_frequency') == frequency and observer.get('observations') == len(observations),
                     'Incomplete/inconsistent supply observer metadata')
                if metadata.get('pid') is not None or observer.get('pid') is not None:
                    need(type(metadata.get('pid')) is int and metadata['pid']>0 and observer.get('pid')==metadata['pid'],
                         'Capture and supply process identity differs')
                if observer.get('samples'):
                    need(inputs.verified(observer['samples'], observer_path.parent) == supply_path, 'Observer sample identity differs')
                result['supply_metadata'] = inputs.before[observer_path]
            windows = profile.get('audio', {}).get('windows')
            need(isinstance(windows,dict) and bool(windows), 'Declared VBlank windows required for supply alignment')
            for name, bounds in windows.items():
                need(isinstance(name,str) and isinstance(bounds,list) and len(bounds)==2 and
                     all(type(v) is int and v>=0 for v in bounds) and bounds[0]<bounds[1], 'Invalid VBlank window')
                first,last = bounds
                selected = [r for r in observations if r['vblank_before'] >= first and r['vblank_after'] <= last]
                need(len(selected)>=2, 'Insufficient observations inside declared VBlank window: '+name)
                a,b = selected[0],selected[-1]
                lo,hi = Fraction(a['qpc_after']*TICKS,frequency),Fraction(b['qpc_before']*TICKS,frequency)
                g0,g1 = a['samples_after'],b['samples_before']
                need(0<=g0<g1<=len(generated), 'Observed generated bounds leave actual PCM')
                bounds = supply_interval(a,b,frequency)
                events = [dict(delta=n['underruns_after']-o['underruns_after'],qpc_lo=o['qpc_before'],qpc_hi=n['qpc_after'],
                               vb_lo=o['vblank_before'],vb_hi=n['vblank_after']) for o,n in zip(observations,observations[1:])
                          if n['underruns_after']>o['underruns_after'] and o['vblank_before']>=first and n['vblank_after']<=last]
                result['windows'][name] = score_window(name,lo,hi,packets,quiet,gquiet,cfg,
                    generated_bounds=(g0,g1),supply=bounds,vblank=[a['vblank_before'],b['vblank_after']],events=events)
                result['windows'][name]['vblank']=[first,last]
            result['supply_csv'] = inputs.before[supply_path]
        else:
            windows = profile.get('audio',{}).get('qpc_windows_100ns')
            default_whole = windows is None
            if default_whole:
                windows = {'whole_delivery':[Fraction(rows[0]['qpc_100ns']),packets.end(rows[-1])]}
            need(isinstance(windows,dict) and windows, 'Invalid QPC window declarations')
            for name,bounds in windows.items():
                need(isinstance(bounds,list) and len(bounds)==2 and
                     all((default_whole and isinstance(v,Fraction)) or
                         (type(v) in (int,float) and math.isfinite(v)) for v in bounds),
                     'Invalid QPC window')
                result['windows'][name] = score_window(name,Fraction(bounds[0]),Fraction(bounds[1]),packets,quiet,gquiet,cfg)
            result['limits'].append('No supply observations: generated PCM has no established mapping to playback time.')
        result['passed'] = all(row['scoring_valid'] for row in result['windows'].values())
    else:
        need(not args.get('events'), 'Events need delivery packet/capture evidence')
        result.update(passed=True, scoring_valid=None)
        result['limits'].append('No delivery manifest: quiet intervals only; continuity and generated/playback alignment are unknown.')
    inputs.finish()
    result['inputs'] = list(inputs.before.values())
    out.mkdir(parents=True,exist_ok=False)
    report = out/'audio-delivery.json'
    write_json(report,result)
    return dict(schema='saturn-kit-adapter-result-v1',kind='audio_delivery',report=str(report),
                analysis_completed=True,scoring_valid=result['passed'] if result['delivery_evidence'] else None,
                delivery_evidence=result['delivery_evidence'],audible_dropout_proven=False,images={})


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--request',required=True,type=Path)
    cli.add_argument('--out',required=True,type=Path)
    args=cli.parse_args()
    try:
        result=analyze(read_json(args.request),args.out)
        print(json.dumps(result,allow_nan=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as error:
        print(json.dumps(dict(error=type(error).__name__,message=str(error))),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())

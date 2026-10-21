# SPDX-License-Identifier: GPL-3.0-only
# Extracted/adapted from the Grandia reconstruction tools; see provenance.json.
"""Qualified packet, PCM and counter arithmetic with parameterized sample rate.

Only intake parameters and local imports changed. No waveform alignment, missing
packet filling, clipped interval scoring or inferred audible fault is added.
"""
from bisect import bisect_left, bisect_right
import csv
from fractions import Fraction
import wave
import numpy as np
TICKS = 10000000
SIZES = {name: None for name in ('vtime','samples','vblank','underruns','dropped','marker_pointer')}

def need(value, message):
    if not value:
        raise ValueError(message)

def pcm(path, rate=44100, channels=2):
    with wave.open(str(path), 'rb') as stream:
        need((stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) ==
             (channels, 2, rate, 'NONE'), 'expected configured signed16 PCM WAV')
        frames = stream.getnframes()
        raw = stream.readframes(frames)
    need(len(raw) == frames * channels * 2, 'truncated PCM data')
    return np.frombuffer(raw, dtype='<i2').reshape(-1, channels)


class Packets:
    def __init__(self, rows, total_frames, rate=44100):
        self.rate = rate
        need(rows and rows[0]['file_frame'] == 0 and
             all(x['frames'] > 0 and min(x['file_frame'], x['qpc_100ns'], x['flags']) >= 0 for x in rows),
             'invalid packet coverage')
        self.rows, self.starts = rows, [x['file_frame'] for x in rows]
        need(self.starts == sorted(set(self.starts)) and
             rows[-1]['file_frame'] + rows[-1]['frames'] == total_frames, 'packet/file coverage differs')
        self.issues = []
        for i, (a, b) in enumerate(zip(rows, rows[1:])):
            need(b['qpc_100ns'] > a['qpc_100ns'], 'packet timestamps reversed')
            frame_gap = b['file_frame'] - a['file_frame'] - a['frames']
            need(frame_gap >= 0, 'overlapping packet file ranges')
            gap = Fraction((b['qpc_100ns'] - a['qpc_100ns']) * self.rate - a['frames'] * TICKS, self.rate)
            if frame_gap or gap:
                self.issues.append({'packet_before': i, 'packet_after': i + 1, 'file_frame_gap': frame_gap,
                                    'timestamp_gap_100ns': float(gap),
                                    'qpc_previous_end': float(self.end(a)), 'qpc_next_start': b['qpc_100ns']})

    def end(self, row):
        return Fraction(row['qpc_100ns'] * self.rate + row['frames'] * TICKS, self.rate)

    def at(self, frame, *, end=False):
        i = (bisect_left(self.starts, frame) if end else bisect_right(self.starts, frame)) - 1
        need(0 <= i < len(self.rows), 'audio boundary is outside real packet coverage')
        row = self.rows[i]
        need(row['file_frame'] <= frame <= row['file_frame'] + row['frames'], 'audio boundary lies in unmapped file gap')
        return Fraction(row['qpc_100ns'] * self.rate + (frame - row['file_frame']) * TICKS, self.rate)

    def quality(self, lo, hi):
        selected = [r for r in self.rows if r['qpc_100ns'] < hi and self.end(r) > lo]
        need(selected, 'no actual playback packets in requested observed window')
        coverage = selected[0]['qpc_100ns'] <= lo and self.end(selected[-1]) >= hi
        issues = [x for x in self.issues if min(x['qpc_previous_end'], x['qpc_next_start']) < hi
                  and max(x['qpc_previous_end'], x['qpc_next_start']) > lo]
        flags = [dict(file_frame=r['file_frame'], frames=r['frames'], flags=r['flags'], qpc_100ns=r['qpc_100ns'])
                 for r in selected if r['flags']]
        full = [r for r in selected if r['qpc_100ns'] >= lo and self.end(r) <= hi]
        return dict(passed=coverage and not issues and not flags, complete_outer_coverage=bool(coverage),
                    packet_issues=issues, flagged_packets=flags, intersecting_packets=len(selected),
                    complete_packets=len(full), complete_packet_frames=sum(r['frames'] for r in full),
                    issues_outside_window=len(self.issues) - len(issues), missing_time_filled=False)


def quiet_runs(audio, threshold, minimum_frames):
    amplitude = np.max(np.abs(audio.astype(np.int32)), axis=1)
    changes = np.diff(np.r_[False, amplitude <= threshold, False].astype(np.int8))
    starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a >= minimum_frames]


def playback_runs(runs, packets, lo, hi):
    inside, boundary = [], []
    for a, b in runs:
        start, end = packets.at(a), packets.at(b, end=True)
        item = dict(start_frame=a, end_frame=b, milliseconds=(b - a) * 1000 / packets.rate,
                    qpc_start=float(start), qpc_end=float(end))
        if lo <= start and end <= hi:
            inside.append(item)
        elif start < hi and end > lo:
            boundary.append(item)
    durations = [x['milliseconds'] for x in inside]
    return dict(holes=len(inside), total_ms=sum(durations), minimum_ms=min(durations, default=0),
                maximum_ms=max(durations, default=0), intervals=inside,
                boundary_runs_omitted_without_clipping=boundary)


def read_supply(path):
    with path.open(newline='', encoding='utf-8') as stream:
        rows = []
        for raw in csv.DictReader(stream):
            row = {k: v if k == 'marker' else v == 'True' if k == 'same_values_across_read' else int(v)
                   for k, v in raw.items()}
            validate_row(row, rows[-1] if rows else None)
            rows.append(row)
    need(rows, 'empty supply CSV')
    return rows


def validate_row(row, previous=None):
    required = {n + suffix for n in SIZES for suffix in ("_before", "_after")}
    required |= {"qpc_before", "qpc_after", "marker", "same_values_across_read"}
    if not required <= row.keys() or any(type(row[n]) is not int for n in required - {"marker", "same_values_across_read"}):
        raise ValueError("partial or noninteger supply observation")
    if row["qpc_after"] < row["qpc_before"]:
        raise ValueError("QPC reversed within observation")
    for name in SIZES.keys() - {"marker_pointer"}:
        before, after = row[name + "_before"], row[name + "_after"]
        if before < 0 or after < before:
            raise ValueError("native counter reversed or became negative")
        if previous and before < previous[name + "_after"]:
            raise ValueError("native counter reversed across observations")
    if previous and row["qpc_before"] <= previous["qpc_after"]:
        raise ValueError("QPC observations overlap or do not increase")


def supply_interval(a, b, frequency):
    """Outer bounds retain uncertainty from independently read native counters."""
    validate_row(b, a)
    dt_lo = (b["qpc_before"] - a["qpc_after"]) / frequency
    dt_hi = (b["qpc_after"] - a["qpc_before"]) / frequency
    lo = b["samples_before"] - a["samples_after"]
    hi = b["samples_after"] - a["samples_before"]
    return {"qpc_lower": a["qpc_before"], "qpc_upper": b["qpc_after"],
            "wall_seconds_lower": dt_lo, "wall_seconds_upper": dt_hi,
            "generated_frames_lower": lo, "generated_frames_upper": hi,
            "generated_frames_per_wall_second_lower": lo / dt_hi,
            "generated_frames_per_wall_second_upper": hi / dt_lo,
            "vblank_lower": a["vblank_before"], "vblank_upper": b["vblank_after"],
            "raw_vtime_lower": a["vtime_before"], "raw_vtime_upper": b["vtime_after"],
            "underruns_delta_lower": b["underruns_before"] - a["underruns_after"],
            "underruns_delta_upper": b["underruns_after"] - a["underruns_before"],
            "dropped_delta_lower": b["dropped_before"] - a["dropped_after"],
            "dropped_delta_upper": b["dropped_after"] - a["dropped_before"]}


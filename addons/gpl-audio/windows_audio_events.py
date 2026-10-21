# SPDX-License-Identifier: GPL-3.0-only
# Profile/path adaptation of tools/windows_audio_events.py; see provenance.json.
"""Bracket changes in an owned native process's existing audio counters, read-only."""
import argparse
import csv
import ctypes
import json
import math
from pathlib import Path
import random
import subprocess
import time

from windows_host import LABELS, Target, pe_layout, pin, section_for

HOST_OBSERVER_SHA = None  # Exact local helper pin comes from the explicit per-game profile.
SYMBOLS = {"_ZL11g_underruns": ("pad_attempts", 4), "_ZL9g_dropped": ("dropped_chunks", 4),
           "_ZL9g_vblanks": ("vblank", 8), "g_host_call": ("marker", 8)}


def symbol_rvas(text, layout):
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[2] not in SYMBOLS or parts[1] not in "bBdD":
            continue
        raw = parts[2]; name, size = SYMBOLS[raw]
        rva = int(parts[0], 16) - layout["preferred_base"]
        if name in found or rva % size or not section_for(layout, rva, size, writable=True):
            raise ValueError("duplicate, unaligned or out-of-image exact audio symbol")
        found[name] = rva
    if found.keys() != {name for name, _ in SYMBOLS.values()}:
        raise ValueError("four exact raw audio/marker/VBlank symbols required")
    spans = sorted((rva, rva + (8 if name in ("marker", "vblank") else 4)) for name, rva in found.items())
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError("audio metadata symbols overlap")
    return found


class QPC:
    def __init__(self):
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.QueryPerformanceCounter.argtypes = self.k.QueryPerformanceFrequency.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
        value = ctypes.c_longlong()
        Target.check(self.k.QueryPerformanceFrequency(ctypes.byref(value)))
        self.frequency = value.value
        if self.frequency <= 0:
            raise ValueError("invalid QPC frequency")

    def now(self):
        value = ctypes.c_longlong()
        Target.check(self.k.QueryPerformanceCounter(ctypes.byref(value)))
        return value.value

    def bridge(self):
        before = self.now(); utc_ns = time.time_ns(); after = self.now()
        return {"qpc_before": before, "utc_ns": utc_ns, "qpc_after": after}


def snapshot(target, symbols, clock):
    begin = clock.now()
    def scalar(name):
        size = 8 if name in ("vblank", "marker") else 4
        return int.from_bytes(target.read(target.base + symbols[name], size), "little", signed=size == 4)
    vb = scalar("vblank"); before = [scalar("pad_attempts"), scalar("dropped_chunks")]
    pointer = scalar("marker")
    section = section_for(target.layout, pointer - target.base)
    marker = "<invalid-marker-pointer>"
    if section and not section["writable"]:
        value = target.read(pointer, min(64, target.base + section["end"] - pointer)).split(b"\0", 1)
        if len(value) == 2:
            label = value[0].decode("ascii", errors="replace")
            marker = (label or "<unmarked>") if label in LABELS else "<unknown-marker>"
    again = scalar("marker")
    after = [scalar("pad_attempts"), scalar("dropped_chunks")]; end_vb = scalar("vblank")
    end = clock.now()
    if end < begin or end_vb < vb or min(before + after) < 0 or any(a < b for a, b in zip(after, before)):
        raise ValueError("clock or native counters moved backwards/negative")
    if pointer != again:
        marker = "<unstable-marker>"
    return {"qpc_before": begin, "qpc_after": end, "vblank_before": vb, "vblank_after": end_vb,
            "pad_before": before[0], "pad_after": after[0], "drop_before": before[1], "drop_after": after[1],
            "counters_stable": before == after, "marker": marker}


class Ledger:
    def __init__(self, first, last, frequency, recovery_pad=True):
        if type(recovery_pad) is not bool:
            raise ValueError("recovery_pad must be an explicit boolean")
        self.audio_kind = "pad-attempt" if recovery_pad else "input-queue-zero"
        self.first, self.last, self.frequency = first, last, frequency
        self.previous = self.initial = None
        self.events, self.observations, self.unstable = [], 0, 0
        self.max_gap_ticks = self.max_read_ticks = 0
        self.last_end = None

    def add(self, row):
        if self.last_end is not None and row["qpc_after"] <= self.last_end:
            raise ValueError("QPC observation ends must increase")
        if self.last_end is not None:
            self.max_gap_ticks = max(self.max_gap_ticks, row["qpc_before"] - self.last_end)
        self.last_end = row["qpc_after"]; self.observations += 1
        self.max_read_ticks = max(self.max_read_ticks, row["qpc_after"] - row["qpc_before"])
        if not row["counters_stable"]:
            self.unstable += 1
            return
        if self.initial is None:
            self.initial = row
        old = self.previous
        if old:
            if row["vblank_before"] < old["vblank_before"]:
                raise ValueError("VBlank counters moved backwards across observations")
            for kind, key in ((self.audio_kind, "pad_after"), ("dropped-chunk", "drop_after")):
                delta = row[key] - old[key]
                if delta < 0:
                    raise ValueError("audio counters moved backwards across observations")
                if not delta:
                    continue
                if len(self.events) >= 100000:
                    raise ValueError("bounded audio event ledger exhausted")
                lo, hi = old["vblank_before"], row["vblank_after"]
                self.events.append({"kind": kind, "count": delta, "skipped_individual_event_times": delta - 1,
                    "counter_before": old[key], "counter_after": row[key],
                    "qpc_lower": old["qpc_before"], "qpc_upper": row["qpc_after"],
                    "vblank_lower": lo, "vblank_upper": hi, "exact_vblank_observed": lo if lo == hi else None,
                    "fully_within_requested_interval": self.first <= lo and hi < self.last,
                    "requested_boundary_overlap": hi >= self.first and lo < self.last,
                    "marker_before": old["marker"], "marker_after": row["marker"],
                    "bracket_ms": (row["qpc_after"] - old["qpc_before"]) * 1000 / self.frequency})
        self.previous = row

    def summary(self):
        counts = lambda rows: {kind: sum(e["count"] for e in rows if e["kind"] == kind)
                               for kind in (self.audio_kind, "dropped-chunk")}
        inside = [e for e in self.events if e["fully_within_requested_interval"]]
        uncertain = [e for e in self.events if e["requested_boundary_overlap"] and not e["fully_within_requested_interval"]]
        return {"observations": self.observations, "unstable_counter_observations": self.unstable,
                "initial_stable": self.initial, "final_stable": self.previous,
                "observed_change_counts": counts(self.events), "counts_fully_inside_interval": counts(inside),
                "counts_uncertain_at_interval_boundary": counts(uncertain), "event_brackets": self.events,
                "skipped_individual_event_times": sum(e["skipped_individual_event_times"] for e in self.events),
                "max_inter_observation_gap_ms": self.max_gap_ticks * 1000 / self.frequency,
                "max_remote_read_span_ms": self.max_read_ticks * 1000 / self.frequency}


def observe(pid, binary, first, last, out_dir, expected_sha256, *, nm_path, deadline_seconds=600, recovery_pad=True, policy=None):
    if type(recovery_pad) is not bool:
        raise ValueError("recovery_pad must be an explicit boolean")
    if not isinstance(policy, dict):
        raise ValueError("explicit audio-events profile policy required")
    binary, out_dir, nm_path = (Path(p).resolve() for p in (binary, out_dir, nm_path))
    if not (isinstance(pid, int) and 0 < pid <= 0xFFFFFFFF and isinstance(first, int) and isinstance(last, int)
            and 0 <= first < last and math.isfinite(deadline_seconds) and 0 < deadline_seconds <= 600):
        raise ValueError("invalid explicit PID, VBlank interval or finite deadline")
    private_root = Path(policy["output_root"]).resolve()
    if out_dir == private_root or not out_dir.is_relative_to(private_root) or out_dir.exists():
        raise ValueError("fresh child of the profile output root required")
    if pin(binary) != policy["binary"] or pin(nm_path) != policy["nm"]:
        raise ValueError("binary or symbolizer differs from profile pin")
    source = Path(__file__).resolve().with_name("windows_host.py")
    if pin(source) != policy["host_helper"]:
        raise ValueError("read-only target helper changed")
    helper_sha = policy["host_helper"]["sha256"]
    binary_pin, nm_pin = pin(binary), pin(nm_path)
    if binary_pin["sha256"] != expected_sha256:
        raise ValueError("binary differs from caller's immutable SHA pin")
    layout = pe_layout(binary)
    command = [str(nm_path), "--defined-only", "--numeric-sort", str(binary)]  # Exact raw local symbols, no demangling.
    symbols = symbol_rvas(subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout, layout)
    out_dir.mkdir(parents=True, exist_ok=False)
    csv_path, report_path = out_dir / "audio-event-samples.csv", out_dir / "audio-events.json"
    if csv_path.exists() or report_path.exists():
        raise ValueError("preserve observer outputs; choose a fresh directory")
    target, clock, ledger, reason, error = None, QPC(), None, "deadline", None
    bridge_start = clock.bridge(); started = time.perf_counter(); deadline = started + deadline_seconds
    ledger = Ledger(first, last, clock.frequency, recovery_pad); rng = random.Random(20261014)
    try:
        target = Target(pid, binary, layout)
        with csv_path.open("x", newline="", encoding="utf-8") as stream:
            writer = None
            while time.perf_counter() < deadline:
                if target.exited():
                    reason = "process-exited"; break
                row = snapshot(target, symbols, clock)
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader()
                writer.writerow(row); ledger.add(row)
                if row["vblank_after"] >= last:
                    reason = "end-vblank-reached"; break
                time.sleep(rng.uniform(.001, .003))
    except (OSError, ValueError) as exc:
        reason, error = "observation-error", str(exc)
    finally:
        if target:
            target.close()
    unchanged = pin(binary) == binary_pin and pin(nm_path) == nm_pin and pin(source)["sha256"] == helper_sha
    result = ledger.summary()
    complete = (reason == "end-vblank-reached" and ledger.initial is not None and
                ledger.initial["vblank_before"] <= first and ledger.previous["vblank_after"] >= last)
    result.update(schema="saturn-kit-audio-counter-event-observer-v1", passed=complete and unchanged,
        recovery_pad=recovery_pad, audio_counter_event_kind=ledger.audio_kind,
        pid=pid, binary=binary_pin, symbolizer=nm_pin, helper_source=pin(source), symbol_command=command,
        symbols_rva=symbols, exact_raw_symbols=SYMBOLS, first_vblank=first, end_vblank_exclusive=last,
        stop_reason=reason, error=error, requested_sleep_ms=[1, 3], deadline_seconds=deadline_seconds,
        observer_seconds=time.perf_counter() - started, qpc_frequency=clock.frequency,
        clock_bridge_start=bridge_start, clock_bridge_end=clock.bridge(), immutable_files_unchanged=unchanged,
        samples=pin(csv_path) if csv_path.exists() else None, read_only=True,
        definition=("Counter increases bracket attempted 50ms padding after input-queue-zero/query failure, or dropped guest chunks. "
                    if recovery_pad else "Counter increases bracket input-queue-zero observations at guest PCM push, or dropped guest chunks; the counter does not establish whether the selected policy inserts a pad. ") +
                   "No SDL calls, remote writes, suspension or guest RAM/register reads.",
        limits=[("Pad attempt is not proof that SDL accepted the pad or that a physical device ran empty; queued API -1 also reaches the baseline zero branch."
                 if recovery_pad else "Input queue zero is not a physical device underrun, proof of failed guest delivery, or evidence for/against a pad; bind its semantics to the caller-pinned runtime source."),
                "Event QPC times are bounded by remote reads, not midpoint estimates. QPC/frequency maps to process-loopback qpc_100ns; UTC bridges permit independent alignment checks.",
                "Counts that jump retain multiplicity but lose individual event times; nonstable reads widen the next bracket. Exact VBlank requires equal outer bounds.",
                "Initial counter values occurred before the first stable read and have no inferred event times. Between-read ABA and marker transitions are not fully observable.",
                "Requested1-3ms sleeps are not guaranteed sampling intervals; actual maximum gap/read span and instrumentation overhead are reported.",
                "Generated sample totals and SDL queued-before/after/API results are unavailable from these four counters; generated WAV remains a separate record."])
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2); stream.write("\n")
    return result


def self_check():
    pe = {"preferred_base": 0x140000000, "sections": [
        {"start": 0x1000, "end": 0x1100, "readable": True, "writable": True},
        {"start": 0x1100, "end": 0x2000, "readable": True, "writable": False}]}
    raw = "140001000 d _ZL11g_underruns\n140001004 d _ZL9g_dropped\n140001008 d _ZL9g_vblanks\n140001010 D g_host_call"
    symbols = symbol_rvas(raw, pe)
    for bad in (raw.replace("_ZL11g_underruns", "g_underruns"), raw + "\n140001020 d _ZL11g_underruns",
                raw.replace("140001004", "140001001"), raw.replace("140001010", "140003000"),
                raw.replace("140001004", "140001000")):
        try:
            symbol_rvas(bad, pe)
        except ValueError:
            continue
        raise AssertionError("invalid exact symbol set accepted")
    class Clock:
        value = 0
        def now(self):
            self.value += 10; return self.value
    class Fake:
        base, layout = 0, pe
        def __init__(self, unstable=False, invalid=False):
            self.reads, self.count_reads, self.unstable, self.invalid = [], 0, unstable, invalid
        def read(self, address, size):
            self.reads.append((address, size))
            if address == 0x1000:
                self.count_reads += 1; return int(self.unstable and self.count_reads > 1).to_bytes(4, "little")
            if address == 0x1004:
                return b"\0" * 4
            if address == 0x1008:
                return (10).to_bytes(8, "little")
            if address == 0x1010:
                return (0xDEADBEEF if self.invalid else 0x1100).to_bytes(8, "little")
            assert address == 0x1100 and size == 64
            return b"SDL_RenderPresent\0".ljust(64, b"\0")
    good = Fake(); assert snapshot(good, symbols, Clock())["counters_stable"]
    assert not snapshot(Fake(unstable=True), symbols, Clock())["counters_stable"]
    bad = Fake(invalid=True); assert snapshot(bad, symbols, Clock())["marker"] == "<invalid-marker-pointer>"
    assert all(a != 0xDEADBEEF for a, _ in bad.reads)
    def row(t, vb, pad, drop=0, stable=True):
        return {"qpc_before": t, "qpc_after": t + 1, "vblank_before": vb, "vblank_after": vb,
                "pad_before": pad, "pad_after": pad, "drop_before": drop, "drop_after": drop,
                "counters_stable": stable, "marker": "<unmarked>"}
    ledger = Ledger(10, 13, 1000)
    for r in (row(0, 9, 5), row(2, 10, 5), row(4, 10, 6), row(6, 11, 7, stable=False),
              row(9, 12, 9, 2), row(12, 13, 10, 2)):
        ledger.add(r)
    result = ledger.summary()
    assert result["observed_change_counts"] == {"pad-attempt": 5, "dropped-chunk": 2}
    assert result["counts_fully_inside_interval"] == {"pad-attempt": 4, "dropped-chunk": 2}
    assert result["counts_uncertain_at_interval_boundary"]["pad-attempt"] == 1
    assert result["skipped_individual_event_times"] == 3 and ledger.events[0]["exact_vblank_observed"] == 10
    assert ledger.events[1]["qpc_lower"] == 4 and ledger.events[1]["qpc_upper"] == 10
    for invalid in (row(12, 13, 11), row(14, 14, 0)):
        try:
            ledger.add(invalid)
        except ValueError:
            continue
        raise AssertionError("duplicate time/decreased counters accepted")
    assert Ledger(0, 1, 1000).summary()["observed_change_counts"]["pad-attempt"] == 0
    no_pad = Ledger(10, 13, 1000, recovery_pad=False)
    no_pad.add(row(0, 10, 2)); no_pad.add(row(4, 10, 5, 1))
    summary = no_pad.summary()
    assert summary["observed_change_counts"] == {"input-queue-zero": 3, "dropped-chunk": 1}
    assert no_pad.events[0]["kind"] == "input-queue-zero" and summary["skipped_individual_event_times"] == 2
    assert no_pad.events[0]["qpc_lower"] == 0 and no_pad.events[0]["qpc_upper"] == 5
    for invalid_policy in (None, 0, 1, "false", [], {}):
        for operation in (lambda: Ledger(0, 1, 1000, invalid_policy),
                          lambda: observe(1, "unused", 0, 1, "unused", "unused", nm_path="unused", recovery_pad=invalid_policy)):
            try:
                operation()
            except ValueError:
                continue
            raise AssertionError("nonboolean recovery policy accepted")
    print("PASS raw symbols/PE bounds, scalar-only reads, unstable/invalid markers, exact brackets/jumps/boundaries, backwards guards, explicit policy/negative guards and empty ledger; no process opened")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--first", type=int)
    parser.add_argument("--last", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--deadline-seconds", type=float, default=600)
    args=parser.parse_args()
    if args.self_check:
        self_check(); return 0
    if any(v is None for v in (args.profile,args.pid,args.first,args.last,args.out)):
        parser.error("explicit profile, owned PID, interval and fresh output are required")
    profile=json.loads(args.profile.read_text(encoding="utf-8-sig"))
    if profile.get("schema")!="saturn-kit-profile-v1":
        raise ValueError("unsupported game profile")
    policy=dict(profile["audio_events"])
    def configured(value):
        p=Path(value)
        return (p if p.is_absolute() else args.profile.resolve().parent/p).resolve()
    for key in ("binary","nm","host_helper"):
        row=dict(policy[key]); row["path"]=str(configured(row["path"])); policy[key]=row
    policy["output_root"]=str(configured(profile["output_root"]))
    raw=policy.get("symbols",SYMBOLS)
    if not isinstance(raw,dict) or {tuple(v) for v in raw.values()}!=set(SYMBOLS.values()):
        raise ValueError("exact four counter roles and widths required")
    global_names=globals()
    global_names["SYMBOLS"]={key:tuple(value) for key,value in raw.items()}
    labels=policy.get("marker_labels")
    if labels is not None:
        if not isinstance(labels,list) or not all(isinstance(v,str) and len(v)<=63 for v in labels):
            raise ValueError("bounded marker labels required")
        global_names["LABELS"]=set(labels)
    recovery_pad=policy.get("recovery_pad")
    if type(recovery_pad) is not bool:
        raise ValueError("profile must declare recovery_pad counter semantics")
    result=observe(args.pid,policy["binary"]["path"],args.first,args.last,args.out,
        policy["binary"]["sha256"],nm_path=policy["nm"]["path"],
        deadline_seconds=args.deadline_seconds,recovery_pad=recovery_pad,policy=policy)
    print(json.dumps({key:result[key] for key in ("passed","stop_reason","observations","observed_change_counts")}))
    return 0 if result["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())

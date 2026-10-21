# SPDX-License-Identifier: GPL-3.0-only
"""Finite, isolated native replay and Ymir capture. No RAM write or state import."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib

import ymir_bridge_client as bridge


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    require(path.stat().st_size <= 16 * 1024 * 1024, "JSON exceeds 16 MiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=True, allow_nan=False, indent=2)
        stream.write("\n")


def pin(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def integer(value, low, high, label):
    require(type(value) is int and low <= value <= high, "Invalid " + label)
    return value


class Inputs:
    def __init__(self, request):
        self.profile = request["profile"]
        require(self.profile.get("schema") == "saturn-kit-profile-v1", "Unsupported profile")
        filename = Path(request["profile_path"])
        require(filename.is_absolute(), "Absolute profile_path required")
        self.base = filename.parent.resolve()
        self.roots = [self.path(value) for value in self.profile["input_roots"]]
        require(self.roots, "Input roots required")
        self.output = self.path(self.profile["output_root"])
        self.sources = {}

    def path(self, value):
        require(isinstance(value, str) and value and "\0" not in value, "Nonempty path required")
        path = Path(value)
        return (path if path.is_absolute() else self.base / path).resolve()

    def input(self, value):
        path = self.path(value)
        require(any(path.is_relative_to(root) for root in self.roots), "Input escaped input_roots")
        require(path.is_file(), "Input file missing")
        self.sources[str(path)] = pin(path)
        return path

    def checked(self, row, data=False):
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}, "Exact file pin required")
        path = self.input(row["path"]) if data else self.path(row["path"])
        actual = pin(path)
        require(type(row["bytes"]) is int and actual["bytes"] == row["bytes"] and actual["sha256"] == row["sha256"], "Pinned input changed")
        self.sources[str(path)] = actual
        return path

    def unchanged(self):
        for value in self.sources.values():
            require(pin(Path(value["path"])) == value, "Immutable source changed: " + value["path"])


def cue_dependencies(inputs, disc, known):
    if disc is None or disc.suffix.lower() != ".cue": return
    require(disc.stat().st_size <= 1024 * 1024, "Cue sheet exceeds 1 MiB")
    for line in disc.read_text(encoding="utf-8-sig").splitlines():
        if re.match(r"\s*FILE\s", line, re.I):
            match = re.fullmatch(r'\s*FILE\s+(?:"([^"\r\n]+)"|(\S+))\s+\S+\s*', line, re.I)
            require(match is not None, "Unsupported cue FILE line")
            value = match.group(1) or match.group(2)
            path = (disc.parent / value).resolve()
            require(path in known, "Every cue FILE dependency must be separately pinned")


def copy_new(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(block)
    expected, actual = pin(source), pin(destination)
    require((expected["bytes"], expected["sha256"]) == (actual["bytes"], actual["sha256"]), "Copy identity mismatch")


def relative_target(value):
    require(isinstance(value, str) and value and "\\" not in value, "Portable relative target required")
    path = Path(value)
    require(not path.is_absolute() and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in {".", ".."} for part in path.parts), "Invalid profile target")
    return path


class OwnedProcess:
    """Own one process group, or one Windows job with kill-on-close semantics."""
    def __init__(self, argv, cwd, logfile):
        self.job = None
        self.lock = threading.Lock()
        options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class Basic(ctypes.Structure):
                _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                            ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
            class IO(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in ("ro", "wo", "oo", "rb", "wb", "ob")]
            class Extended(ctypes.Structure):
                _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                            ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.job = self.kernel.CreateJobObjectW(None, None)
            require(bool(self.job), "Cannot create owned process job")
            limits = Extended(); limits.basic.flags = 0x2000
            if not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.kernel.CloseHandle(self.job); self.job = None
                raise OSError("Cannot configure owned process job")
            startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = subprocess.SW_HIDE
            options["startupinfo"] = startup
        try:
            self.process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT, **options)
            if self.job and not self.kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
                self.process.terminate(); self.process.wait(timeout=5)
                raise OSError("Cannot assign owned process to cleanup job")
        except BaseException:
            if self.job: self.kernel.CloseHandle(self.job); self.job = None
            raise

    def stop(self):
        with self.lock:
            if self.job:
                self.kernel.CloseHandle(self.job); self.job = None
            elif os.name != "nt":
                try: os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
            elif self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try: os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                else: self.process.kill()
                self.process.wait(timeout=5)
            if os.name != "nt":
                # The parent may exit before a descendant that ignored TERM.
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass


def template_arguments(values, replacements, extra_files, base):
    require(isinstance(values, list) and all(isinstance(value, str) and value and "\0" not in value for value in values), "Fixed argument list required")
    result = []
    for value in values:
        if value.startswith("{") and value.endswith("}"):
            require(value[1:-1] in replacements, "Unknown launch template token")
            replacement = replacements[value[1:-1]]
            require(replacement is not None, "Missing launch template input")
            value = str(replacement)
        require("{" not in value and "}" not in value, "Only complete argument template tokens are supported")
        result.append(value)
    if result and not result[0].startswith("--"):
        prefix = Path(result[0]); prefix = (prefix if prefix.is_absolute() else base / prefix).resolve()
        require(prefix in extra_files, "A launcher prefix must be an explicitly pinned file")
        result[0] = str(prefix)
    return result


def native(inputs, settings, args, out, report):
    frames = integer(args["frames"], 1, 1000000, "frame limit")
    recording = inputs.input(args["recording"])
    require(recording.stat().st_size <= 16 * 1024 * 1024, "Recording exceeds 16 MiB")
    player = inputs.checked(settings["player"])
    files = [inputs.checked(row, True) for row in settings.get("files", [])]
    disc = inputs.checked(settings["disc"], True) if settings.get("disc") else None
    cue_dependencies(inputs, disc, files)
    save = out / "working-backup.bin"
    if settings.get("save"):
        require(settings.get("disposable_seed") is True, "Only an explicitly labelled disposable save seed is allowed")
        copy_new(inputs.checked(settings["save"], True), save)
    shots = settings.get("capture_frames", [])
    require(isinstance(shots, list) and len(shots) <= 64 and shots == sorted(set(shots)), "Sorted unique capture_frames required")
    for frame in shots: integer(frame, 1, frames, "capture frame")
    base = template_arguments(settings.get("arguments", []), {"disc": disc}, files, inputs.base)
    # Fixed profiles may select ordinary startup options, but cannot override isolation or inject writes/state.
    pairs = {"--cue", "--clock", "--scale", "--multitap", "--tasks"}
    switches = {"--headless", "--unpaced", "--strict-unseeded", "--smc-log=off"}
    index = 1 if base and not base[0].startswith("--") else 0
    while index < len(base):
        flag = base[index]
        require(flag in pairs | switches, "Unsupported native startup option: " + flag)
        index += 2 if flag in pairs else 1
        require(index <= len(base), "Missing native startup option value")
        if flag == "--cue": require(disc is not None and base[index-1] == str(disc), "Native cue must match its pinned disc")
    run_dir = out / "native"; run_dir.mkdir()
    argv = [str(player), *base, "--input", "@" + str(recording), "--vblanks", str(frames),
            "--fast-replay-until", str(frames), "--out", str(run_dir), "--save", str(save) if save.exists() else "-", "--settings", "-"]
    if shots: argv += ["--shot", ",".join(map(str, shots)), "--dump", ",".join(map(str, shots))]
    report.update(argv=argv, requested_frames=frames, captures_requested=shots, working_save=str(save) if save.exists() else None)
    return argv, run_dir, frames


def original(inputs, settings, args, out, report):
    recipe_path = inputs.input(args["recipe"])
    recipe = read_json(recipe_path)
    require(isinstance(recipe, dict) and set(recipe) <= {"schema", "steps", "debug_derived", "provenance"} and recipe.get("schema") == "saturn-kit-original-recipe-v1", "Unsupported original recipe")
    steps = recipe.get("steps")
    require(isinstance(steps, list) and 0 < len(steps) <= 1000, "Finite recipe steps required")
    require(type(recipe.get("debug_derived")) is bool, "Recipe must label debug-derived provenance")
    allowed = {"pad", "run", "capture", "save_state", "status"}
    frames, names = 0, set()
    for step in steps:
        require(isinstance(step, dict) and step.get("command") in allowed, "Only controller/run/capture/export/status recipe operations are allowed")
        require(set(step) <= {"command", "args"} and isinstance(step.get("args", {}), dict), "Invalid recipe step")
        op, value = step["command"], step.get("args", {})
        if op == "run":
            require(set(value) == {"frames"}, "run requires only frames")
            frames += integer(value["frames"], 1, 36000, "step frames")
        elif op == "pad":
            require(set(value) <= {"port", "buttons"} and "buttons" in value, "pad requires buttons")
            integer(value.get("port", 1), 1, 2, "pad port")
            require(isinstance(value["buttons"], list) and len(value["buttons"]) == len(set(value["buttons"])) and all(button in {"Up", "Down", "Left", "Right", "Start", "A", "B", "C", "X", "Y", "Z", "L", "R"} for button in value["buttons"]), "Invalid pad buttons")
        elif op in {"capture", "save_state"}:
            require(set(value) == {"name"} and isinstance(value["name"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["name"]), "Safe output basename required")
            key = (op, value["name"].casefold()); require(key not in names, "Duplicate output name"); names.add(key)
        else: require(not value, "status takes no arguments")
    integer(settings.get("frame_limit", 36000), 1, 1000000, "original frame limit")
    require(frames <= settings.get("frame_limit", 36000), "Recipe exceeds profile frame limit")
    player = inputs.checked(settings["player"])
    disc = inputs.checked(settings["disc"], True) if settings.get("disc") else None
    extra = [inputs.checked(row, True) for row in settings.get("launch_files", [])]
    media = [inputs.checked(row, True) for row in settings.get("media", [])]
    cue_dependencies(inputs, disc, media)
    profile = out / "profile"; profile.mkdir()
    session = out / "session"
    save_config = settings["save"]
    require(save_config.get("disposable_seed") is True, "A disposable original save seed is required")
    save = profile / relative_target(save_config["target"])
    copy_new(inputs.checked(save_config["pin"], True), save)
    replacements = {"profile": profile, "save": save, "session": session, "disc": disc}
    targets = {str(save.relative_to(profile)).casefold()}
    for entry in settings["files"]:
        require(isinstance(entry, dict) and set(entry) <= {"pin", "target", "template"}, "Invalid original profile file")
        target = relative_target(entry["target"]); key = str(target).casefold()
        require(key not in targets, "Duplicate profile target"); targets.add(key)
        source = inputs.checked(entry["pin"], True)
        destination = profile / target
        if entry.get("template", False):
            require(entry["template"] is True and source.stat().st_size <= 1024 * 1024, "Invalid profile template")
            text = source.read_text(encoding="utf-8-sig")
            for token in re.findall(r"\{([A-Za-z_]+)\}", text):
                require(token in replacements and replacements[token] is not None, "Unknown profile template token")
                text = text.replace("{" + token + "}", Path(replacements[token]).as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8", newline="\n") as stream: stream.write(text)
        else: copy_new(source, destination)
    config = tomllib.loads((profile / "Ymir.toml").read_text(encoding="utf-8"))
    require(Path(config["System"]["InternalBackupRAMImagePath"]).resolve() == save and config["System"].get("InternalBackupRAMPerGame") is False, "Ymir save must point only to the fresh disposable copy")
    require(all(not value for value in config.get("General", {}).get("PathOverrides", {}).values()), "Ymir PathOverrides must be empty")
    cartridge = config.get("Cartridge", {})
    require(cartridge.get("Type", "None") == "None" and not cartridge.get("BackupRAM", {}).get("ImagePath", "")
            and not cartridge.get("ROM", {}).get("ImagePath", ""), "Cartridge must be absent with empty image paths")
    for name in (config.get("System", {}).get("IPL", {}).get("Path", ""), config.get("CDBlock", {}).get("ROMPath", "")):
        if name:
            path = Path(name)
            require(path.is_absolute() and path.resolve().is_relative_to(profile) and path.is_file(), "ROM override must reference a copied profile file")
    arguments = template_arguments(settings["arguments"], replacements, extra, inputs.base)
    require(arguments.count("--profile") == arguments.count("--automation") == arguments.count("--paused") == 1, "Explicit private profile, bridge session and paused start required")
    require(arguments[arguments.index("--profile") + 1] == str(profile) and arguments[arguments.index("--automation") + 1] == str(session), "Original launch escaped isolated profile/session")
    index = 1 if arguments and not arguments[0].startswith("--") else 0
    while index < len(arguments):
        flag = arguments[index]
        require(flag in {"--profile", "--automation", "--disc", "--paused", "--debug", "--fast-forward"}, "Unsupported original launch option: " + flag)
        index += 2 if flag in {"--profile", "--automation", "--disc"} else 1
        require(index <= len(arguments), "Missing original launch argument")
        if flag == "--disc": require(disc is not None and arguments[index-1] == str(disc), "Original disc must match its pin")
    require(isinstance(settings.get("bridge_build_identity"), dict) and settings["bridge_build_identity"], "Exact bridge build identity required")
    argv = [str(player), *arguments]
    report.update(argv=argv, requested_frames=frames, debug_derived=recipe["debug_derived"], recipe_provenance=recipe.get("provenance"), working_save=str(save))
    return argv, profile, session, steps


def execute(request, output):
    require(isinstance(request, dict) and set(request) == {"command", "arguments", "profile", "profile_path"}, "Exact control request required")
    job, args = request["command"], request["arguments"]
    require(job in {"replay", "capture-original"} and isinstance(args, dict), "Unsupported control operation")
    require(type(args.get("execute", False)) is bool, "execute must be boolean")
    expected = {"recording", "frames", "execute"} if job == "replay" else {"recipe", "execute"}
    require(set(args) <= expected, "Unknown control argument")
    inputs = Inputs(request)
    out = inputs.path(output)
    require(out != inputs.output and out.is_relative_to(inputs.output) and not out.exists(), "Fresh child output directory required")
    out.mkdir(parents=True, exist_ok=False)
    settings = inputs.profile["control"]["native" if job == "replay" else "original"]
    timeout = integer(settings.get("timeout_seconds", 180), 1, 600, "wall timeout")
    outer_timeout = integer(inputs.profile["control"].get("timeout_seconds", 300), 1, 600, "outer adapter timeout")
    require(timeout + 10 <= outer_timeout, "Outer adapter timeout must leave at least 10 seconds beyond the child wall cap")
    report = {"schema": "saturn-kit-control-session-v1", "command": job, "executed": False, "passed": False,
              "state_imported": False, "guest_ram_written": False, "owned_process_closed": True, "records": [], "wall_cap_seconds": timeout}
    child = timer = None
    timed_out = threading.Event()
    started = time.monotonic()
    failure = None
    try:
        if job == "replay": argv, cwd, frames = native(inputs, settings, args, out, report)
        else: argv, cwd, session, steps = original(inputs, settings, args, out, report)
        inputs.unchanged()
        if args.get("execute", False):
            with (out / "host.log").open("xb") as log:
                child = OwnedProcess(argv, cwd, log)
                report.update(executed=True, owned_process_closed=False, pid=child.process.pid)
                def expire(): timed_out.set(); child.stop()
                timer = threading.Timer(timeout, expire); timer.daemon = True; timer.start()
                deadline = time.monotonic() + timeout
                if job == "replay":
                    child.process.wait(timeout=timeout + 5)
                    require(not timed_out.is_set(), "Owned replay exceeded wall cap")
                    require(child.process.returncode == 0, "Native player failed")
                    stopped = re.findall(r"stopped \(VBlank limit\) after ([0-9]+) VBlanks", (out / "host.log").read_text(encoding="utf-8", errors="replace"))
                    require(stopped and int(stopped[-1]) == frames, "Native completion frame proof missing")
                    for frame in report["captures_requested"]:
                        require((cwd / f"shot-{frame}.png").is_file() and (cwd / f"dump-{frame}.bin").is_file(), "Requested native capture missing")
                    report.update(completed_frames=frames, process_exit_code=child.process.returncode)
                else:
                    while not (session / "session.json").is_file():
                        require(child.process.poll() is None and time.monotonic() < deadline, "Original bridge did not start within wall cap")
                        time.sleep(.02)
                    client = bridge.Client(session)
                    require(client.session["build_identity"] == settings["bridge_build_identity"], "Original bridge build identity mismatch")
                    request_cap = settings.get("request_timeout_seconds", 130)
                    require(type(request_cap) in (int, float) and math.isfinite(request_cap) and 0 < request_cap <= 130, "Invalid bridge request timeout")
                    def call(command, value):
                        remaining = deadline - time.monotonic()
                        require(remaining > 0 and child.process.poll() is None, "Owned original process stopped or wall cap exhausted")
                        response = client.call(command, value, timeout=min(request_cap, remaining))
                        report["records"].append({"command": command, "arguments": value, "response": response})
                        return response["result"]
                    status = call("status", {})
                    require(status["frame_counter"] == 0, "Original must begin paused at frame zero")
                    frame = 0
                    for step in steps:
                        op, value = step["command"], dict(step.get("args", {}))
                        if op == "run":
                            requested = value["frames"]
                            value["timeout_ms"] = min(120000, max(1, int((deadline-time.monotonic()) * 1000)))
                        if op == "pad": value.setdefault("port", 1)
                        result = call(op, value)
                        if op == "run":
                            require(result["frames_completed"] == requested and result["start_frame"] == frame and result["end_frame"] == frame + requested, "Original run stopped before requested completion")
                            frame += requested
                        if op in {"capture", "save_state"}:
                            returned = Path(result["output_directory" if op == "capture" else "output_path"]).resolve()
                            require(returned.is_relative_to((session / "output").resolve()), "Bridge output escaped owned session")
                            require(returned.is_dir() if op == "capture" else returned.is_file(), "Bridge output is missing")
                            if op == "capture" and (returned / "frame.ppm").is_file():
                                from PIL import Image
                                with Image.open(returned / "frame.ppm") as source:
                                    require(source.format == "PPM", "Original picture must be PPM")
                                    source.load()
                                    with (returned / "frame.png").open("xb") as png: source.save(png, format="PNG")
                    call("release", {})
                    report.update(completed_frames=frame, bridge_session=client.session)
            report["passed"] = True
        else:
            report.update(passed=True, status="PLANNING-ONLY")
    except Exception as error:
        failure = error
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        if timer: timer.cancel()
        if child:
            try: child.stop()
            except Exception as error:
                failure = error; report["passed"] = False; report["error"] = {"type": type(error).__name__, "message": str(error)}
            report.update(owned_process_closed=child.process.poll() is not None, process_exit_code=child.process.returncode)
        try: inputs.unchanged()
        except Exception as error:
            failure = error; report["passed"] = False; report["error"] = {"type": type(error).__name__, "message": str(error)}
        report.update(wall_seconds=time.monotonic()-started, wall_cap_hit=timed_out.is_set(), immutable_sources=list(inputs.sources.values()))
        report["artifacts"] = [pin(path) for path in sorted(out.rglob("*")) if path.is_file() and not path.is_symlink()]
        write_json(out / "control-session.json", report)
    if failure: raise failure
    return {"schema": "saturn-kit-control-result-v1", "command": job, "report": str(out / "control-session.json"),
            "executed": report["executed"], "passed": report["passed"], "owned_process_closed": report["owned_process_closed"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(read_json(args.request), str(args.out))))
        return 0
    except Exception as error:
        print(json.dumps({"error": {"type": type(error).__name__, "message": str(error)}}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: MIT
"""Optional GPL controller tests: synthetic executables, recipes and bridge data only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ADDON = Path(__file__).resolve().parents[1] / "addons/gpl-control"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = load("ymir_bridge_client", ADDON / "ymir_bridge_client.py")
control = load("saturn_kit_test_control", ADDON / "adapter.py")

FIXTURE = r'''
import json, os, pathlib, struct, subprocess, sys, time, zlib
args=sys.argv[1:]
def value(flag): return args[args.index(flag)+1]
def png(path):
    def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\0\0\0'))+chunk(b'IEND',b''))
if MODE=='hang':
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    pathlib.Path('descendant-pid.txt').write_text(str(child.pid))
    time.sleep(30)
if '--vblanks' in args:
    if MODE=='fail': raise SystemExit(7)
    out=pathlib.Path(value('--out')); frames=int(value('--vblanks'))
    if '--shot' in args:
        for n in value('--shot').split(','):
            png(out/('shot-'+n+'.png')); (out/('dump-'+n+'.bin')).write_bytes(b'SYNTHETIC-DUMP')
    assert value('--settings')=='-' and value('--input').startswith('@')
    pathlib.Path('argv.json').write_text(json.dumps(args))
    print('stopped (VBlank limit) after %d VBlanks'%frames,flush=True)
    raise SystemExit(0)
root=pathlib.Path(value('--automation')); root.mkdir()
for name in ['requests','responses','output']: (root/name).mkdir()
created=time.time_ns()//1000000
identity={'synthetic':True} if MODE!='wrong-build' else {'synthetic':False}
session={'schema':1,'session_id':'synthetic-session','created_unix_ms':created,'build_identity':identity}
(root/'session.json').write_text(json.dumps(session))
sequence=frame=0
while True:
    for path in list((root/'requests').glob('*.json')):
        request=json.loads(path.read_text()); path.unlink()
        with (root/'received.jsonl').open('a') as receipt: receipt.write(json.dumps({'id':request['id'],'command':request['command']})+'\n')
        if MODE=='no-response': continue
        op=request['command']; a=request['args']; sequence+=1
        if op=='run':
            start=frame; frame+=a['frames']; result={'frames_completed':a['frames'],'start_frame':start,'end_frame':frame}
        elif op=='capture':
            directory=root/'output'/a['name']; directory.mkdir(); png(directory/'frame.png')
            (directory/'capture.json').write_text(json.dumps({'frame_counter':frame,'synthetic':True}))
            result={'output_directory':str(directory),'capture':{'frame_counter':frame}}
        elif op=='save_state':
            output=root/'output'/(a['name']+'.ystate'); output.write_bytes(b'SYNTHETIC-STATE')
            result={'output_path':str(output)}
        else: result={'frame_counter':frame}
        response={'schema':1,'session_id':session['session_id'],'id':request['id'],'completed_unix_ms':max(time.time_ns()//1000000,request['created_unix_ms']),'build_identity':identity,'generation':0,'operation_seq':sequence,'ok':True,'result':result}
        target=root/'responses'/(request['id']+'.json'); temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(response)); os.replace(temporary,target)
    time.sleep(.005)
'''


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.outputs = self.root / "outputs"
        self.seed = self.root / "seed.bin"; self.seed.write_bytes(b"SYNTHETIC-SAVE-SEED")
        self.recording = self.root / "recording.txt"; self.recording.write_text("0:START,1:")
        self.script = self.root / "fixture.py"
        self.script.write_text("MODE='ok'\n" + FIXTURE)
        self.template = self.root / "Ymir.toml.in"
        self.template.write_text("[System]\nInternalBackupRAMImagePath = '{save}'\nInternalBackupRAMPerGame = false\n[General.PathOverrides]\n")
        self.native = {"player": control.pin(Path(sys.executable).resolve()), "arguments": [str(self.script)],
            "files": [control.pin(self.script)], "save": control.pin(self.seed), "disposable_seed": True,
            "capture_frames": [2], "timeout_seconds": 5}
        self.original = {"player": control.pin(Path(sys.executable).resolve()), "arguments": [str(self.script),
            "--profile", "{profile}", "--automation", "{session}", "--paused"], "launch_files": [control.pin(self.script)],
            "files": [{"pin": control.pin(self.template), "target": "Ymir.toml", "template": True}],
            "save": {"pin": control.pin(self.seed), "target": "bup-int.bin", "disposable_seed": True},
            "bridge_build_identity": {"synthetic": True}, "timeout_seconds": 5, "request_timeout_seconds": .3}
        self.profile = {"schema": "saturn-kit-profile-v1", "game": "synthetic", "input_roots": [str(self.root)],
            "output_root": str(self.outputs), "control": {"native": self.native, "original": self.original}}
        self.recipe = self.root / "recipe.json"
        control.write_json(self.recipe, {"schema": "saturn-kit-original-recipe-v1", "debug_derived": False,
            "steps": [{"command": "pad", "args": {"buttons": ["Start"]}}, {"command": "run", "args": {"frames": 2}},
                      {"command": "capture", "args": {"name": "frame"}}, {"command": "save_state", "args": {"name": "state"}}]})

    def mode(self, value):
        self.script.write_text("MODE=" + repr(value) + "\n" + FIXTURE)
        self.native["files"] = self.original["launch_files"] = [control.pin(self.script)]

    def request(self, command, **arguments):
        args = {"execute": True, **arguments}
        args.update({"recording": str(self.recording), "frames": 2} if command == "replay" else {"recipe": str(self.recipe)})
        return {"command": command, "arguments": args, "profile": self.profile, "profile_path": str(self.root / "private-profile.json")}

    def run_adapter(self, command, name="run", **arguments):
        request = self.root / (name + "-request.json")
        control.write_json(request, self.request(command, **arguments))
        result = subprocess.run([sys.executable, "-B", "-E", str(ADDON / "adapter.py"), "--request", str(request), "--out", str(self.outputs/name)],
            capture_output=True, text=True, timeout=15)
        return result, self.outputs/name

    def test_native_subprocess_replay_captures_and_seed_preserved(self):
        before = control.pin(self.seed)
        process, output = self.run_adapter("replay")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout); self.assertTrue(result["executed"])
        report = control.read_json(Path(result["report"]))
        self.assertEqual(report["completed_frames"], 2)
        self.assertTrue(report["owned_process_closed"])
        self.assertEqual(control.pin(self.seed), before)
        self.assertEqual((output/"working-backup.bin").read_bytes(), self.seed.read_bytes())
        self.assertTrue((output/"native/shot-2.png").is_file())
        self.assertTrue((output/"native/dump-2.bin").is_file())

    def test_original_subprocess_runs_real_bridge_protocol(self):
        before = control.pin(self.seed)
        process, output = self.run_adapter("capture-original")
        self.assertEqual(process.returncode, 0, process.stderr)
        report = control.read_json(output/"control-session.json")
        self.assertEqual(report["completed_frames"], 2)
        self.assertEqual([record["command"] for record in report["records"]], ["status", "pad", "run", "capture", "save_state", "release"])
        self.assertTrue(report["owned_process_closed"])
        self.assertTrue((output/"session/output/state.ystate").is_file())
        self.assertFalse(report["state_imported"])
        self.assertFalse(report["guest_ram_written"])
        self.assertEqual(control.pin(self.seed), before)

    def test_default_plan_does_not_launch(self):
        request = self.request("replay"); request["arguments"].pop("execute")
        result = control.execute(request, str(self.outputs/"plan"))
        self.assertFalse(result["executed"])
        self.assertFalse((self.outputs/"plan/host.log").exists())

    def test_native_failure_preserves_receipt_and_closes_child(self):
        self.mode("fail")
        process, output = self.run_adapter("replay")
        self.assertNotEqual(process.returncode, 0)
        report = control.read_json(output/"control-session.json")
        self.assertFalse(report["passed"]); self.assertTrue(report["owned_process_closed"])
        self.assertEqual(report["process_exit_code"], 7)

    def test_timeout_closes_owned_process_tree(self):
        self.mode("hang"); self.native["timeout_seconds"] = 1
        process, output = self.run_adapter("replay")
        self.assertNotEqual(process.returncode, 0)
        report = control.read_json(output/"control-session.json")
        self.assertTrue(report["wall_cap_hit"]); self.assertTrue(report["owned_process_closed"])
        self.assertLess(report["wall_seconds"], 8)
        pid = int((output/"native/descendant-pid.txt").read_text())
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32"); kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                code = ctypes.c_ulong(); kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.GetExitCodeProcess(handle, ctypes.byref(code)); kernel.CloseHandle(handle)
                self.assertNotEqual(code.value, 259)
        else:
            try: os.kill(pid, 0)
            except ProcessLookupError: pass
            else: self.assertEqual(Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[0], 'Z')

    def test_wrong_bridge_identity_fails_before_commands(self):
        self.mode("wrong-build")
        process, output = self.run_adapter("capture-original")
        self.assertNotEqual(process.returncode, 0)
        report = control.read_json(output/"control-session.json")
        self.assertEqual(report["records"], [])
        self.assertTrue(report["owned_process_closed"])

    def test_bridge_timeout_never_resends(self):
        self.mode("no-response")
        process, output = self.run_adapter("capture-original")
        self.assertNotEqual(process.returncode, 0)
        report = control.read_json(output/"control-session.json")
        self.assertEqual(report["error"]["type"], "BridgeTimeout")
        self.assertEqual(len(list((output/"session/requests").glob("*.json"))), 0)
        self.assertEqual([json.loads(row)["command"] for row in (output/"session/received.jsonl").read_text().splitlines()], ["status"])
        self.assertTrue(report["owned_process_closed"])

    def test_forbidden_recipe_write_rejected_before_launch(self):
        data = control.read_json(self.recipe)
        data["steps"] = [{"command": "poke_high_ram", "args": {}}]
        self.recipe.write_text(json.dumps(data))
        process, output = self.run_adapter("capture-original")
        self.assertNotEqual(process.returncode, 0)
        report = control.read_json(output/"control-session.json")
        self.assertFalse(report["executed"])

    def test_external_cartridge_save_and_user_profile_rejected(self):
        for name, addition, argument in [("cartridge", "\n[Cartridge]\nType='None'\n[Cartridge.BackupRAM]\nImagePath='external-owner.bin'\n", None),
                                          ("user", "", "--user")]:
            self.template.write_text("[System]\nInternalBackupRAMImagePath = '{save}'\nInternalBackupRAMPerGame = false\n[General.PathOverrides]\n" + addition)
            self.original["files"][0]["pin"] = control.pin(self.template)
            if argument: self.original["arguments"].append(argument)
            process, output = self.run_adapter("capture-original", name=name)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(control.read_json(output/"control-session.json")["executed"])

    def test_native_ram_write_option_rejected(self):
        self.native["arguments"] += ["--write", "1:06000000=ff"]
        process, output = self.run_adapter("replay")
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(control.read_json(output/"control-session.json")["executed"])

    def test_outer_timeout_must_allow_owned_cleanup(self):
        self.profile["control"]["timeout_seconds"] = 5
        with self.assertRaisesRegex(ValueError, "at least 10 seconds"):
            control.execute(self.request("replay"), str(self.outputs/"tight"))

    def test_cue_media_must_be_separately_pinned(self):
        cue = self.root/"game.cue"; cue.write_text('FILE "track.bin" BINARY\n TRACK 01 MODE1/2352\n')
        (self.root/"track.bin").write_bytes(b"SYNTHETIC-MEDIA")
        self.native["disc"] = control.pin(cue)
        with self.assertRaisesRegex(ValueError, "separately pinned"):
            control.execute(self.request("replay", execute=False), str(self.outputs/"missing-media"))
        self.native["files"].append(control.pin(self.root/"track.bin"))
        self.assertTrue(control.execute(self.request("replay", execute=False), str(self.outputs/"pinned-media"))["passed"])

    def test_pins_and_output_scope_are_enforced(self):
        self.seed.write_bytes(b"CHANGED")
        process, output = self.run_adapter("replay")
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(control.read_json(output/"control-session.json")["executed"])
        with self.assertRaisesRegex(ValueError, "Fresh child"):
            control.execute(self.request("replay"), str(self.root/"outside"))

    def test_relative_profile_pins_do_not_read_profile_file(self):
        self.profile["input_roots"] = ["."]; self.profile["output_root"] = "outputs"
        self.native["files"][0]["path"] = "fixture.py"; self.native["arguments"] = ["fixture.py"]
        self.native["save"]["path"] = "seed.bin"
        request = self.request("replay", execute=False); request["arguments"]["recording"] = "recording.txt"
        self.assertTrue(control.execute(request, "outputs/relative")["passed"])

    def test_client_timeout_retains_one_request(self):
        root = self.root/"client"; (root/"requests").mkdir(parents=True); (root/"responses").mkdir()
        control.write_json(root/"session.json", {"schema": 1, "session_id": "synthetic", "created_unix_ms": time.time_ns()//1000000, "build_identity": {"synthetic": True}})
        client = bridge.Client(root)
        with self.assertRaises(bridge.BridgeTimeout): client.call("status", timeout=.03)
        self.assertEqual(len(list((root/"requests").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()

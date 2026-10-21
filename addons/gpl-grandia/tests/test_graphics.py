# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic fixtures only; no game, renderer, reader or compiler process."""
import json
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import shlex
import subprocess

ADDON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON))
from saturn_graphics import build_reader, decode, inputs, legacy_vdp1, offline, policy


class ExtractedGraphicsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="saturn-graphics-test-", dir=os.environ.get("SATURN_KIT_TEST_TEMP"))
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "input"
        self.source.mkdir()
        self.output = self.base / "output"
        self.output.mkdir()
        self.profile = self.base / "profile.json"
        self.profile.write_text(json.dumps(dict(schema="saturn-kit-profile-v1", game="synthetic", input_roots=["input"], output_root="output")), encoding="utf-8")
        policy.configure(str(self.profile))

    def tearDown(self):
        parent = Path(os.environ.get("SATURN_KIT_TEST_TEMP", tempfile.gettempdir())).resolve()
        self.assertTrue(self.base.is_relative_to(parent) and self.base.name.startswith("saturn-graphics-test-"))
        self.temp.cleanup()

    def test_retained_walker_selfcheck(self):
        self.assertEqual(legacy_vdp1.selfcheck(), {"synthetic_checks": 6, "passed": True})

    def test_native_layout_and_unknown_free_registers(self):
        data = bytearray(sum(inputs.SIZES.values()))
        data[:2] = b"\x80\x00"
        path = self.source / "synthetic.bin"
        path.write_bytes(data)
        snapshot = inputs.native_dump(path)
        report = decode.inspect_snapshot(snapshot)
        self.assertEqual(report["vdp1"]["summary"]["commands"], 0)
        self.assertEqual(report["components"]["framebuffer"]["bytes"], 262144)
        self.assertEqual(report["vdp2"]["unknown_word_offsets"], [])
        self.assertEqual(report["provenance"]["framebuffer_role"], "captured DRAW only")

    def test_wrong_native_size(self):
        path = self.source / "wrong.bin"
        path.write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            inputs.native_dump(path)

    def test_orphan_display_remains_explicit(self):
        data = bytearray(sum(inputs.SIZES.values()))
        data[:2] = b"\x80\x00"
        path = self.source / "synthetic.bin"
        path.write_bytes(data)
        path.with_suffix(".display.bin").write_bytes(bytes(262144))
        snapshot = inputs.native_dump(path)
        self.assertEqual(snapshot.provenance["display_bundle_status"], "incomplete_display_bundle")

    def test_profile_roots_and_fresh_outputs(self):
        path = policy.fresh_output(self.output / "one")
        self.assertTrue(path.is_dir())
        with self.assertRaises(ValueError):
            policy.fresh_output(path)
        with self.assertRaises(ValueError):
            policy.fresh_output(self.source / "not-output")
        with self.assertRaises(ValueError):
            policy.private(self.base / "escape", exists=False)

    def test_unknown_original_getters(self):
        packed, represented = inputs.pack_getters({"TVMR": 1}, {0: "TVMR", 2: "FBCR"}, 32)
        self.assertEqual(packed[:4], b"\x00\x01\x00\x00")
        self.assertEqual(represented, {0})
        report = decode.registers(packed, represented, {0: "TVMR", 2: "FBCR"})
        self.assertIsNone(report["002:FBCR"]["value"])

    def test_register_map_preserved(self):
        a, b, identity = inputs.register_maps()
        self.assertEqual((a[2], b[0]), ("FBCR", "TVMD"))
        self.assertEqual(identity["sha256"], "c3eb2a951b209c73da8fda150bd7c31ff375215df067511a5686a6467398a61f")

    def test_relative_utility_receipt(self):
        binary = self.base / "synthetic.exe"
        binary.write_bytes(b"not-executable")
        row = policy.pin(binary)
        row["path"] = "synthetic.exe"
        receipt = self.base / "utility.json"
        receipt.write_text(json.dumps(dict(schema="saturn-kit-offline-renderer-v1", passed=True, executable=row, library_directory=".")), encoding="utf-8")
        profile = json.loads(self.profile.read_text())
        profile["graphics"] = dict(harness=policy.pin(receipt))
        self.profile.write_text(json.dumps(profile), encoding="utf-8")
        policy.configure(str(self.profile))
        result = policy.utility(receipt, "saturn-kit-offline-renderer-v1")
        self.assertEqual(result["executable"]["path"], str(binary))
        binary.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            policy.utility(receipt, "saturn-kit-offline-renderer-v1")

    def test_duplicate_json_rejected(self):
        path = self.source / "duplicate.json"
        path.write_text('{"x":1,"x":2}', encoding="utf-8")
        with self.assertRaises(ValueError):
            policy.read_json(path)

    def test_explicit_harness_builder_mocked(self):
        compiler = self.base / "synthetic-compiler"
        compiler.write_bytes(b"not-executable")
        runtime = ADDON / "production/runtime"
        closure = [runtime / name for name in offline.RENDERER_FILES] + [ADDON / "production/vdp1_bridge.cpp", ADDON / "production/compositor.cpp"]
        calls = []
        def fake(argv, **kwargs):
            calls.append(argv)
            Path(argv[argv.index("-o") + 1]).write_bytes(b"mock-build-output")
            if "-MF" in argv:
                Path(argv[argv.index("-MF") + 1]).write_text("o: " + " ".join(shlex.quote(path.as_posix()) for path in closure))
            return subprocess.CompletedProcess(argv, 0, stdout=b"mock", stderr=b"")
        with patch.object(offline.subprocess, "run", fake):
            receipt = offline.build(self.output / "harness", compiler)
        value = policy.read_json(receipt)
        self.assertTrue(value["passed"])
        self.assertEqual(len(calls), 4)
        self.assertEqual(value["schema"], "saturn-kit-offline-renderer-v1")
        self.assertEqual(len(value["source_files"]), 14)

    def test_explicit_reader_builder_mocked(self):
        compiler = self.base / "synthetic-compiler"
        compiler.write_bytes(b"not-executable")
        header = self.source / "public-header.hpp"
        header.write_text("// synthetic public header")
        source = ADDON / "production/reader/read_state.cpp"
        closure = [source, source.with_name("named_dump.hpp"), header]
        def fake(argv, **kwargs):
            Path(argv[argv.index("-o") + 1]).write_bytes(b"mock-reader")
            Path(argv[argv.index("-MF") + 1]).write_text("o: " + " ".join(shlex.quote(path.as_posix()) for path in closure))
            return subprocess.CompletedProcess(argv, 0, stdout=b"mock", stderr=b"")
        with patch.object(build_reader.subprocess, "run", fake):
            receipt = build_reader.build(self.output / "reader", compiler, [self.source])
        value = policy.read_json(receipt)
        self.assertTrue(value["passed"])
        self.assertEqual(value["archive_version_required"], 13)
        self.assertEqual(len(value["source_files"]), 3)

    def test_adapter_has_no_reconstruction_dependency(self):
        spec = importlib.util.spec_from_file_location("synthetic_graphics_adapter", ADDON / "adapter.py")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        python = self.base / "synthetic-python"
        python.write_bytes(b"not-executable")
        harness = self.base / "harness.json"
        harness.write_text("{}")
        native = self.source / "synthetic.bin"
        native.write_bytes(b"synthetic-only")
        entry = policy.pin(ADDON / "saturn_graphics/__main__.py")
        entry["path"] = os.path.relpath(entry["path"], self.base)
        profile = dict(schema="saturn-kit-profile-v1", game="synthetic", input_roots=["input"], output_root="output",
                       graphics=dict(python=policy.pin(python), harness=policy.pin(harness), source_pins=[entry]))
        request = dict(command="inspect", arguments=dict(input=str(native)), profile=profile, profile_path=str(self.profile))
        calls = []
        def fake(argv, **kwargs):
            calls.append((argv, kwargs["cwd"]))
            out = Path(argv[argv.index("--out") + 1])
            out.mkdir()
            (out / "inspection.json").write_text(json.dumps(dict(schema="grandia-graphics-inspection-v1",
                provenance=dict(kind="synthetic"), limits=["synthetic"], vdp1=dict(summary=dict(commands=0)),
                vdp2=dict(production=dict(images={}, normal_layers=[])))))
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(adapter.subprocess, "run", fake):
            result = adapter.run(request, str(self.output / "adapter"))
        capture = policy.read_json(policy.verify(result["neutral_capture"]))
        self.assertEqual(capture["schema"], "saturn-kit-capture-v1")
        self.assertEqual(capture["state"]["vdp1"]["summary"]["commands"], 0)
        self.assertNotIn("images", capture["state"]["vdp2"]["production"])
        self.assertEqual(calls[0][1], str(ADDON))
        self.assertIn("saturn_graphics", calls[0][0])


if __name__ == "__main__":
    unittest.main()

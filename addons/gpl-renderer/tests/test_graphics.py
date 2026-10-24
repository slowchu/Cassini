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
import contextlib
import io

ADDON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON))
from saturn_graphics import build_reader, decode, inputs, legacy_vdp1, offline, policy, textures


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
        self.assertEqual(value["runtime_selection"], "bundled")
        self.assertEqual(value["expected_dependency_files"], value["source_files"])
        self.assertEqual(len(value["actual_dependency_files"]), 14)

    def synthetic_runtime(self):
        runtime = self.source / "compatible-runtime"
        for name in offline.RENDERER_FILES:
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("// synthetic runtime source: " + name)
        return runtime

    def mocked_compiler(self, closure):
        def fake(argv, **kwargs):
            Path(argv[argv.index("-o") + 1]).write_bytes(b"mock-build-output")
            if "-MF" in argv:
                Path(argv[argv.index("-MF") + 1]).write_text("o: " + " ".join(shlex.quote(path.as_posix()) for path in closure))
            return subprocess.CompletedProcess(argv, 0, stdout=b"mock", stderr=b"")
        return fake

    def test_external_runtime_source_pins_mocked(self):
        runtime = self.synthetic_runtime()
        compiler = self.base / "synthetic-compiler"
        compiler.write_bytes(b"not-executable")
        closure = [runtime / name for name in offline.RENDERER_FILES] + [ADDON / "production/vdp1_bridge.cpp", ADDON / "production/compositor.cpp"]
        with patch.object(offline.subprocess, "run", self.mocked_compiler(closure)):
            receipt = offline.build(self.output / "external", compiler, runtime / ".." / runtime.name)
        value = policy.read_json(receipt)
        self.assertTrue(value["passed"])
        self.assertEqual(value["runtime_selection"], "external")
        self.assertEqual(value["runtime_root"], str(runtime.resolve()))
        self.assertEqual({row["path"] for row in value["source_files"]}, {str(path.resolve()) for path in closure})
        self.assertEqual({row["sha256"] for row in value["expected_dependency_files"]}, {policy.pin(path)["sha256"] for path in closure})
        self.assertEqual(len(value["actual_dependency_files"]), 14)

    def test_runtime_closure_mismatch_is_preserved(self):
        runtime = self.synthetic_runtime()
        compiler = self.base / "synthetic-compiler"
        compiler.write_bytes(b"not-executable")
        extra = runtime / "unexpected.hpp"
        extra.write_text("// synthetic unexpected compiler dependency")
        closure = [runtime / name for name in offline.RENDERER_FILES] + [ADDON / "production/vdp1_bridge.cpp", ADDON / "production/compositor.cpp", extra]
        with patch.object(offline.subprocess, "run", self.mocked_compiler(closure)):
            with self.assertRaisesRegex(ValueError, "closure differs"):
                offline.build(self.output / "mismatch", compiler, runtime)
        value = policy.read_json(self.output / "mismatch/build.json")
        self.assertFalse(value["passed"])
        self.assertEqual(len(value["expected_dependency_files"]), 14)
        self.assertEqual(len(value["actual_dependency_files"]), 15)
        self.assertNotIn("executable", value)

    def test_missing_runtime_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, "compatible runtime"):
            offline.build(self.output / "missing", self.base / "unused-compiler", self.source / "missing")
        self.assertFalse((self.output / "missing").exists())

    def test_incomplete_runtime_rejected_before_output(self):
        with self.assertRaises(FileNotFoundError):
            offline.build(self.output / "incomplete", self.base / "unused-compiler", self.source)
        self.assertFalse((self.output / "incomplete").exists())

    def test_build_harness_cli_runtime_forwarded(self):
        from saturn_graphics import __main__ as cli
        runtime = self.source / "selected-runtime"
        compiler = self.base / "selected-compiler"
        destination = self.output / "selected-build"
        argv = ["saturn_graphics", "--profile", str(self.profile), "build-harness", "--runtime", str(runtime),
                "--compiler", str(compiler), "--out", str(destination)]
        with patch.object(sys, "argv", argv), patch.object(cli, "build", return_value=destination / "build.json") as build:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 0)
        build.assert_called_once_with(destination, compiler, runtime)

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


class TextureExportTests(unittest.TestCase):
    setUp, tearDown = ExtractedGraphicsTests.setUp, ExtractedGraphicsTests.tearDown

    @staticmethod
    def put(buffer, address, value):
        buffer[address:address + 2] = value.to_bytes(2, "big")

    def memories(self):
        """Sprites in each texture mode over 2048-colour CRAM, sprite type 0, mixed RGB."""
        vram, cram, regs = bytearray(inputs.SIZES["vdp1_vram"]), bytearray(4096), bytearray(512)
        self.put(regs, 0x0E, 0x1000); self.put(regs, 0xE0, 0x0020)
        self.put(cram, 0x11 * 2, 0x001F); self.put(cram, 0x103 * 2, 0x7C00); self.put(cram, 3 * 2, 0x03E0)
        def sprite(slot, mode, colr, address, width, height, pmod=0):
            base = slot * 32
            self.put(vram, base + 4, mode << 3 | pmod); self.put(vram, base + 6, colr)
            self.put(vram, base + 8, address // 8); self.put(vram, base + 10, (width // 8) << 8 | height)
        sprite(0, 0, 0x0010, 0x2000, 8, 1)                          # 4bpp bank: 0, 1, F (end code), 1...
        vram[0x2000:0x2004] = bytes((0x01, 0xF1, 0x11, 0x11))
        sprite(1, 1, 0x1000 // 8, 0x2100, 8, 1)                     # lookup table: RGB red, CRAM 3
        vram[0x2100:0x2104] = bytes((0x12, 0x12, 0x12, 0x12))
        self.put(vram, 0x1002, 0x801F); self.put(vram, 0x1004, 0x0003)
        sprite(2, 4, 0x0100, 0x2200, 8, 1)                          # 8bpp bank: CRAM 0x103
        vram[0x2200:0x2208] = bytes(8 * [3])
        sprite(3, 5, 0, 0x2300, 8, 1)                               # RGB: green, transparent, end code, shadow code
        for i, v in enumerate((0x83E0, 0, 0x7FFF, 0x07FE, 0x83E0, 0x83E0, 0x83E0, 0x83E0)):
            self.put(vram, 0x2300 + 2 * i, v)
        sprite(4, 0, 0x0010, 0x2000, 8, 1)                          # same texture and colours again
        sprite(5, 1, 0x1100 // 8, 0x2400, 8, 1)                     # its table is all zero
        vram[0x2400:0x2404] = bytes((0x12, 0x30, 0x00, 0x45))
        self.put(vram, 6 * 32, 0x8000)
        return bytes(vram), bytes(cram), bytes(regs)

    def export(self, name):
        vram, cram, regs = self.memories()
        out = policy.fresh_output(self.output / name)
        return textures.export(vram, cram, regs, set(range(0, 512, 2)), {"kind": "synthetic"}, [], out), out

    def pixels(self, out, entry):
        from PIL import Image
        with Image.open(out / entry["file"]) as image:
            data = image.convert("RGBA").tobytes()
        return [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]

    def test_each_mode_coloured_as_production(self):
        report, out = self.export("modes")
        found = {e["address"]: e for e in report["textures"]}
        self.assertEqual(report["summary"], dict(commands=6, textured_commands=6, textures=5, distinct_texture_bytes=5,
                                                 skipped=0, walk_end=report["summary"]["walk_end"]))
        red, clear = (248, 0, 0, 255), (0, 0, 0, 0)
        bank = found[0x2000]
        self.assertEqual(self.pixels(out, bank)[:4], [clear, red, clear, red])
        self.assertEqual(bank["texel_counts"]["end_code"], 1)
        self.assertEqual([u["command_address"] for u in bank["uses"]], [0, 128])
        self.assertEqual(self.pixels(out, found[0x2100])[:2], [red, (0, 248, 0, 255)])
        self.assertEqual(set(self.pixels(out, found[0x2200])), {(0, 0, 248, 255)})
        self.assertEqual(self.pixels(out, found[0x2300])[:4], [(0, 248, 0, 255), clear, clear, (0, 0, 0, 128)])
        self.assertIsNone(bank["index_file"])
        self.assertEqual(found[0x2400]["index_file"], f"indices/{found[0x2400]['texture_id'][:16]}.png")
        for name in ("textures.json", "textures.txt", "index.html", found[0x2400]["index_file"]):
            self.assertTrue((out / name).is_file())
        self.assertIn("data:image/png;base64,", (out / "index.html").read_text(encoding="utf-8"))

    def test_ids_are_stable_and_separate_bytes_from_colours(self):
        first, _ = self.export("first")
        second, _ = self.export("second")
        self.assertEqual([(e["texture_id"], e["colour_id"]) for e in first["textures"]],
                         [(e["texture_id"], e["colour_id"]) for e in second["textures"]])
        self.assertEqual(len({e["colour_id"] for e in first["textures"]}), 5)

    def test_raw_and_ymir_memories(self):
        vram, cram, regs = self.memories()
        swap = lambda data: bytes(data[i ^ 1] for i in range(len(data)))
        (self.source / "vram.bin").write_bytes(swap(vram)); (self.source / "cram.bin").write_bytes(swap(cram))
        loaded = textures.raw_memories(self.source / "vram.bin", self.source / "cram.bin", swap_bytes=True)
        self.assertEqual(loaded[:3], (vram, cram, loaded[2]))
        self.assertEqual(loaded[3], set())
        self.assertEqual(textures.settings(loaded[2], loaded[3])["captured_registers"]["SPCTL"], False)
        folder = self.source / "ymir"
        folder.mkdir()
        (folder / "vdp1-vram.bin").write_bytes(vram); (folder / "vdp2-cram.bin").write_bytes(cram)
        (folder / "T-0000G_VDP2_registers_05F80000_512B.bin").write_bytes(regs)
        loaded = textures.ymir_dumps(folder)
        self.assertEqual(loaded[:4], (vram, cram, regs, set(range(0, 512, 2))))
        self.assertEqual(loaded[4]["kind"], "ymir-memory-dump")
        (folder / "T-0001G_VDP2_registers_05F80000_512B.bin").write_bytes(regs)
        with self.assertRaises(ValueError):
            textures.ymir_dumps(folder)
        (folder / "vdp2-cram.bin").write_bytes(b"short")
        with self.assertRaises(ValueError):
            textures.ymir_dumps(folder, folder / "T-0000G_VDP2_registers_05F80000_512B.bin")

    def test_textures_cli_from_ymir_folder(self):
        from saturn_graphics import __main__ as cli
        vram, cram, regs = self.memories()
        folder = self.source / "dumps"
        folder.mkdir()
        (folder / "vdp1-vram.bin").write_bytes(vram); (folder / "vdp2-cram.bin").write_bytes(cram)
        argv = ["saturn_graphics", "--profile", str(self.profile), "textures", "--ymir-dumps", str(folder), "--out", str(self.output / "cli")]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(cli.main(), 0)
        self.assertIn("5 textures", printed.getvalue())
        report = policy.read_json(self.output / "cli/textures.json")
        self.assertIn("VDP2 registers not captured: RAMCTL, SPCTL, SDCTL, CRAOFB. Colours use assumed values for them.", report["limits"])


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""Protocol-only checks use mocked dispatchers and synthetic JSON, never game data."""

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from saturn_kit import mcp_server as mcp

PROFILE = "synthetic-profile.json"


def request(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def initialize(server, version=mcp.PROTOCOL_VERSION):
    response = server.handle(request("initialize", {
        "protocolVersion": version, "capabilities": {},
        "clientInfo": {"name": "synthetic-test", "version": "1"},
    }))
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return response


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.profile_path = Path(PROFILE).resolve()
        self.profile_pin = {"path": str(self.profile_path), "bytes": 1, "sha256": "0" * 64}
        identity = patch.object(mcp, "_profile_identity", side_effect=lambda filename: (
            Path(filename).resolve(), dict(self.profile_pin)))
        identity.start()
        self.addCleanup(identity.stop)
        current_pin = patch.object(mcp, "pin", side_effect=lambda filename: dict(self.profile_pin))
        self.current_pin = current_pin.start()
        self.addCleanup(current_pin.stop)
        self.profile_object = object()
        profile = patch.object(mcp, "Profile", return_value=self.profile_object)
        self.profile_constructor = profile.start()
        self.addCleanup(profile.stop)

    def test_lifecycle_and_version_negotiation(self):
        server = mcp.Server(PROFILE)
        self.assertEqual(server.handle(request("tools/list"))["error"]["code"], -32002)
        self.assertEqual(server.handle(request("ping"))["result"], {})
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        response = initialize(server, "2099-01-01")
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(server.handle(request("initialize"))["error"]["code"], -32600)
        tools = server.handle(request("tools/list"))["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {
            "inspect", "diff", "replay", "capture_original", "hw_report", "saves", "audio_gaps",
        })
        self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in tools))

    def test_initialized_notification_is_required(self):
        server = mcp.Server(PROFILE)
        server.handle(request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        }))
        self.assertEqual(server.handle(request("tools/list"))["error"]["code"], -32002)

    def test_method_and_envelope_errors_do_not_dispatch(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        cases = [
            ([], -32600),
            ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, -32600),
            ({"jsonrpc": "2.0", "id": True, "method": "ping"}, -32600),
            ({"jsonrpc": "2.0", "id": None, "method": "ping"}, -32600),
            (request("not/a/method"), -32601),
            (request("tools/call", {"name": "shell", "arguments": {}}), -32602),
            (request("tools/call", {"name": [], "arguments": {}}), -32602),
            (request("tools/list", {"cursor": "invented"}), -32602),
            (request("ping", {"_meta": "not-an-object"}), -32602),
        ]
        with patch.object(mcp, "run") as dispatch:
            for message, code in cases:
                with self.subTest(message=message):
                    self.assertEqual(server.handle(message)["error"]["code"], code)
            dispatch.assert_not_called()

    def test_strict_tool_validation(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        common = {"profile": "profile.json", "out": "fresh"}
        cases = [
            ("inspect", {**common, "input": "capture", "command": "ignored"}),
            ("inspect", {**common, "input": "capture", "kind": "anything"}),
            ("inspect", {**common, "input": "capture", "paired_capture": None}),
            ("inspect", {**common, "input": " "}),
            ("inspect", {**common, "input": "bad\0path"}),
            ("diff", {**common, "before": "a", "after": "b", "premise": {}}),
            ("replay", {**common, "recording": "r", "frames": True}),
            ("replay", {**common, "recording": "r", "frames": 0}),
            ("replay", {**common, "recording": "r", "frames": 1.5}),
            ("capture_original", {**common, "recipe": "r", "execute": 1}),
            ("hw_report", {**common, "inputs": []}),
            ("hw_report", {**common, "inputs": ["a", 1]}),
            ("saves", {**common, "input": "save", "format": "exe"}),
            ("audio_gaps", {**common, "input": "log", "threshold_ms": False}),
            ("audio_gaps", {**common, "input": "log", "threshold_ms": float("inf")}),
            ("audio_gaps", {**common, "input": "log", "threshold_ms": 0}),
            ("inspect", {"input": "capture"}),
            ("inspect", []),
        ]
        with patch.object(mcp, "run") as dispatch:
            for name, arguments in cases:
                with self.subTest(name=name, arguments=arguments):
                    response = server.handle(request("tools/call", {"name": name, "arguments": arguments}))
                    self.assertEqual(response["error"]["code"], -32602)
            dispatch.assert_not_called()

    def test_dispatch_names_defaults_and_structured_results(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        common = {"profile": "synthetic-profile.json", "out": "fresh-output"}
        cases = [
            ("inspect", {"input": "capture"}, {"kind": "auto"}),
            ("diff", {"before": "a", "after": "b"}, {}),
            ("replay", {"recording": "r", "frames": 10}, {"execute": False}),
            ("capture_original", {"recipe": "r"}, {"execute": False}),
            ("hw_report", {"inputs": ["one.log", "two.log"]}, {}),
            ("saves", {"input": "save"}, {"format": "auto"}),
            ("audio_gaps", {"input": "log"}, {}),
        ]
        for name, arguments, defaults in cases:
            with self.subTest(name=name), patch.object(mcp, "run", return_value={"report": "fresh-output/report.json"}) as dispatch:
                response = server.handle(request("tools/call", {"name": name, "arguments": {**common, **arguments}}))
                dispatch.assert_called_once_with(name.replace("_", "-"), {
                    **common, **arguments, **defaults, "profile": str(self.profile_path)},
                    trusted_profile=self.profile_object)
                result = response["result"]
                self.assertFalse(result["isError"])
                self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])

    def test_dispatcher_errors_are_tool_errors(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        call = request("tools/call", {"name": "inspect", "arguments": {
            "profile": PROFILE, "out": "new", "input": "missing",
        }})
        for exception in (FileNotFoundError("missing input"), ValueError("stale profile")):
            with self.subTest(exception=exception), patch.object(mcp, "run", side_effect=exception):
                result = server.handle(call)["result"]
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["error"]["type"], type(exception).__name__)
        with patch.object(mcp, "run", return_value=[]):
            self.assertTrue(server.handle(call)["result"]["isError"])

    def test_notifications_never_execute_tools_or_reply(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        with patch.object(mcp, "run") as dispatch:
            for method in ("ping", "unknown", "notifications/cancelled", "tools/call"):
                self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": method}))
            dispatch.assert_not_called()

    def test_stdio_framing_parse_errors_and_stdout_isolation(self):
        lines = [
            "not json",
            "[]",
            json.dumps(request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "synthetic", "version": "1"}})),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(request("tools/call", {"name": "inspect", "arguments": {
                "profile": PROFILE, "out": "fresh", "input": "synthetic"}}, request_id="résumé")),
            '{"jsonrpc":"2.0","id":5,"method":"ping","params":{"bad":NaN}}',
            json.dumps(request("tools/call", {"name": "inspect", "arguments": {
                "profile": PROFILE, "out": "fresh", "input": "missing"}}, request_id=6)),
        ]
        reader = io.StringIO("\n".join(lines) + "\n")
        writer = io.StringIO()
        stderr = io.StringIO()

        def noisy_dispatch(command, arguments, *, trusted_profile):
            self.assertIs(trusted_profile, self.profile_object)
            print("synthetic backend diagnostic")
            if arguments["input"] == "missing":
                raise FileNotFoundError("synthetic missing input")
            return {"report": "fresh/résumé.json"}

        with patch.object(mcp, "run", side_effect=noisy_dispatch), patch("sys.stderr", stderr):
            mcp.serve(reader, writer, PROFILE)
        responses = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(len(responses), 6)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["error"]["code"], -32600)
        self.assertEqual(responses[3]["id"], "résumé")
        self.assertEqual(responses[3]["result"]["structuredContent"]["report"], "fresh/résumé.json")
        self.assertEqual(responses[4]["error"]["code"], -32700)
        self.assertTrue(responses[5]["result"]["isError"])
        self.assertIn("synthetic backend diagnostic", stderr.getvalue())
        self.assertNotIn("synthetic backend diagnostic", writer.getvalue())

    def test_invalid_utf8_is_a_parse_error_and_stream_continues(self):
        reader = io.BytesIO(b'\xff\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
        writer = io.StringIO()
        mcp.serve(reader, writer, PROFILE)
        responses = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["result"], {})

    def test_tool_cannot_choose_another_profile(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        self.current_pin.reset_mock()
        call = request("tools/call", {"name": "inspect", "arguments": {
            "profile": "another-profile.json", "out": "fresh", "input": "capture"}})
        with patch.object(mcp, "run") as dispatch:
            result = server.handle(call)["result"]
            self.assertTrue(result["isError"])
            self.assertIn("launch-selected", result["structuredContent"]["error"]["message"])
            dispatch.assert_not_called()
            self.current_pin.assert_not_called()

    def test_changed_profile_pin_blocks_backend(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        self.profile_pin["sha256"] = "1" * 64
        call = request("tools/call", {"name": "inspect", "arguments": {
            "profile": PROFILE, "out": "fresh", "input": "capture"}})
        with patch.object(mcp, "run") as dispatch:
            result = server.handle(call)["result"]
            self.assertTrue(result["isError"])
            self.assertIn("changed after server startup", result["structuredContent"]["error"]["message"])
            dispatch.assert_not_called()

    def test_unchanged_launch_profile_is_accepted(self):
        server = mcp.Server(PROFILE)
        initialize(server)
        call = request("tools/call", {"name": "inspect", "arguments": {
            "profile": str(self.profile_path), "out": "fresh", "input": "capture"}})
        with patch.object(mcp, "run", return_value={"report": "fresh/report.json"}) as dispatch:
            self.assertFalse(server.handle(call)["result"]["isError"])
            dispatch.assert_called_once_with("inspect", {
                "profile": str(self.profile_path), "out": "fresh", "input": "capture", "kind": "auto"},
                trusted_profile=self.profile_object)
            self.assertIs(dispatch.call_args.kwargs["trusted_profile"], server.profile)
            self.profile_constructor.assert_called_once_with(str(self.profile_path))

    def test_profile_changed_during_startup_is_rejected(self):
        def changed_profile(filename):
            self.profile_pin["sha256"] = "2" * 64
            return self.profile_object

        self.profile_constructor.side_effect = changed_profile
        with self.assertRaisesRegex(ValueError, "changed during server startup"):
            mcp.Server(PROFILE)


if __name__ == "__main__":
    unittest.main()

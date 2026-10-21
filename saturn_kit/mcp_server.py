# SPDX-License-Identifier: MIT
"""Small stdio MCP adapter; the CLI API owns all filesystem and tool work."""

from __future__ import annotations

import contextlib
import copy
import json
import math
from pathlib import Path
import sys

from .api import Profile, pin, run


PROTOCOL_VERSION = "2025-06-18"


def _string(**extra: object) -> dict:
    return {"type": "string", "minLength": 1, **extra}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {"profile": _string(), "out": _string(), **properties},
            "required": ["profile", "out", *required],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


TOOLS = [
    _tool("inspect", "Inspect an existing capture into a fresh private output folder.", {
        "input": _string(),
        "kind": _string(enum=["auto", "native", "ymir-state", "original-fixture"], default="auto"),
        "paired_capture": _string(),
    }, ["input"]),
    _tool("diff", "Compare two inspections; premise is an optional comparison-premise JSON path.", {
        "before": _string(), "after": _string(), "premise": _string(),
    }, ["before", "after"]),
    _tool("replay", "Plan by default; execute=true runs a bounded isolated replay through the pinned profile adapter.", {
        "recording": _string(), "frames": {"type": "integer", "minimum": 1},
        "execute": {"type": "boolean", "default": False},
    }, ["recording", "frames"]),
    _tool("capture_original", "Plan by default; execute=true runs a finite recipe in an isolated pinned original-emulator profile.", {
        "recipe": _string(), "execute": {"type": "boolean", "default": False},
    }, ["recipe"]),
    _tool("hw_report", "Aggregate existing hardware and guest logs into JSON and text reports.", {
        "inputs": {"type": "array", "items": _string(), "minItems": 1},
    }, ["inputs"]),
    _tool("saves", "Read save metadata by default; optional to converts a copy into fresh output, never an active save.", {
        "input": _string(), "format": _string(enum=["auto", "bup", "native"], default="auto"),
        "to": _string(enum=["native", "ymir"]),
    }, ["input"]),
    _tool("audio_gaps", "Find digital-silence candidates in existing PCM audio; this does not prove delivery underruns.", {
        "input": _string(), "threshold_ms": {"type": "number", "exclusiveMinimum": 0},
        "reference": _string(),
        "delivery": _string(), "events": _string(),
    }, ["input"]),
]
TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


class ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _validate_arguments(name: str, arguments: object) -> dict:
    """Enforce the advertised small schemas before the dispatcher sees input."""
    schema = TOOLS_BY_NAME[name]["inputSchema"]
    if not isinstance(arguments, dict):
        raise ProtocolError(-32602, "Tool arguments must be an object")
    if set(arguments) - schema["properties"].keys():
        raise ProtocolError(-32602, "Unknown tool argument")
    if set(schema["required"]) - arguments.keys():
        raise ProtocolError(-32602, "Missing required tool argument")
    validated = dict(arguments)
    for key, rule in schema["properties"].items():
        if key not in validated:
            if "default" in rule:
                validated[key] = rule["default"]
            continue
        value = validated[key]
        kind = rule["type"]
        valid = (
            (kind == "string" and isinstance(value, str) and bool(value.strip()) and "\0" not in value)
            or (kind == "boolean" and type(value) is bool)
            or (kind == "integer" and type(value) is int and value >= rule.get("minimum", 0))
            or (kind == "number" and type(value) in (int, float) and (type(value) is int or math.isfinite(value))
                and value > rule.get("exclusiveMinimum", -math.inf))
            or (kind == "array" and isinstance(value, list) and bool(value)
                and all(isinstance(item, str) and item.strip() and "\0" not in item for item in value))
        )
        if not valid or ("enum" in rule and value not in rule["enum"]):
            raise ProtocolError(-32602, f"Invalid tool argument: {key}")
    return validated


def _object_params(params: object, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(params, dict) or set(params) - allowed or required - params.keys():
        raise ProtocolError(-32602, "Invalid method parameters")
    if "_meta" in params and not isinstance(params["_meta"], dict):
        raise ProtocolError(-32602, "Method _meta must be an object")
    return params


class Server:
    def __init__(self, profile_path: str) -> None:
        self.profile_path, self.profile_pin = _profile_identity(profile_path)
        self.profile = Profile(str(self.profile_path))
        if pin(self.profile_path) != self.profile_pin:
            raise ValueError("Trusted profile changed during server startup")
        self.initialized = False
        self.ready = False

    def handle(self, message: object) -> dict | None:
        request_id = None
        notification = False
        try:
            if not isinstance(message, dict):
                raise ProtocolError(-32600, "Request must be a JSON object")
            request_id = message.get("id")
            notification = "id" not in message
            if (message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str)
                    or (not notification and (type(request_id) not in (str, int)))):
                request_id = None
                raise ProtocolError(-32600, "Invalid JSON-RPC request")
            if set(message) - {"jsonrpc", "id", "method", "params"}:
                raise ProtocolError(-32600, "Unknown JSON-RPC request field")
            method = message["method"]
            params = message.get("params", {})
            if notification:
                if method == "notifications/initialized" and self.initialized:
                    _object_params(params, {"_meta"})
                    self.ready = True
                return None
            result = self._request(method, params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except ProtocolError as error:
            if notification and isinstance(message, dict) and message.get("jsonrpc") == "2.0" and isinstance(message.get("method"), str):
                return None
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": error.code, "message": str(error)}}

    def _request(self, method: str, params: object) -> dict:
        if method == "ping":
            _object_params(params, {"_meta"})
            return {}
        if method == "initialize":
            if self.initialized:
                raise ProtocolError(-32600, "Server is already initialized")
            params = _object_params(params, {"protocolVersion", "capabilities", "clientInfo", "_meta"},
                                    {"protocolVersion", "capabilities", "clientInfo"})
            client = params["clientInfo"]
            if (not isinstance(params["protocolVersion"], str) or not params["protocolVersion"]
                    or not isinstance(params["capabilities"], dict) or not isinstance(client, dict)
                    or not isinstance(client.get("name"), str) or not client["name"]
                    or not isinstance(client.get("version"), str) or not client["version"]):
                raise ProtocolError(-32602, "Invalid initialize parameters")
            self.initialized = True
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cassini", "version": "0.2.1"},
                "instructions": "Use the fixed launch-selected profile. Sources are read-only; outputs require a fresh folder. Private evidence stays local.",
            }
        if method not in {"tools/list", "tools/call"}:
            raise ProtocolError(-32601, "Method not found")
        if not self.ready:
            raise ProtocolError(-32002, "Initialize and send notifications/initialized first")
        if method == "tools/list":
            params = _object_params(params, {"cursor", "_meta"})
            if "cursor" in params:
                raise ProtocolError(-32602, "This server has no pagination cursor")
            return {"tools": copy.deepcopy(TOOLS)}
        params = _object_params(params, {"name", "arguments", "_meta"}, {"name"})
        name = params["name"]
        if not isinstance(name, str) or name not in TOOLS_BY_NAME:
            raise ProtocolError(-32602, "Unknown tool")
        arguments = _validate_arguments(name, params.get("arguments", {}))
        try:
            profile_path = Path(arguments["profile"]).resolve()
            if profile_path != self.profile_path:
                raise ValueError("Tool profile differs from the trusted launch-selected profile")
            if pin(self.profile_path) != self.profile_pin:
                raise ValueError("Trusted profile changed after server startup; restart with the reviewed profile")
            arguments["profile"] = str(self.profile_path)
            with contextlib.redirect_stdout(sys.stderr):
                result = run(name.replace("_", "-"), arguments, trusted_profile=self.profile)
            if not isinstance(result, dict):
                raise TypeError("Dispatcher must return an object")
            serialized = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            return {"content": [{"type": "text", "text": serialized}], "structuredContent": result, "isError": False}
        except Exception as error:
            result = {"error": {"type": type(error).__name__, "message": str(error)}}
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "structuredContent": result, "isError": True}


def _profile_identity(filename: str) -> tuple[Path, dict]:
    path = Path(filename).resolve(strict=True)
    return path, pin(path)


def serve(reader, writer, profile_path: str) -> None:
    """Newline-delimited UTF-8 JSON; no game payloads are embedded by this adapter."""
    server = Server(profile_path)
    for line in reader:
        try:
            text = line.decode("utf-8") if isinstance(line, bytes) else line
            message = json.loads(text, parse_constant=_reject_nonfinite)
        except (ValueError, UnicodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = server.handle(message)
        if response is not None:
            writer.write(json.dumps(response, allow_nan=False, separators=(",", ":")) + "\n")
            writer.flush()


def _reject_nonfinite(value: str):
    raise ValueError(f"Non-JSON number: {value}")


def main(profile_path: str | None = None) -> None:
    if profile_path is None:
        import argparse
        parser = argparse.ArgumentParser(description="Saturn agent kit MCP with one trusted launch-selected profile")
        parser.add_argument("--profile", required=True)
        profile_path = parser.parse_args().profile
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    serve(getattr(sys.stdin, "buffer", sys.stdin), sys.stdout, profile_path)


if __name__ == "__main__":
    main()

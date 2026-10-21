"""Acknowledged filesystem client for the separate D03 diagnostic Ymir build.

This does not launch an emulator, access process memory, or retry mutations.
Timed-out requests remain on disk: timeout is not cancellation or rollback.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import time
import uuid


class BridgeError(RuntimeError):
    """Transport, session, or protocol integrity failure."""


class BridgeTimeout(BridgeError):
    """The response deadline elapsed; the request can still have effects."""


class CommandRejected(BridgeError):
    def __init__(self, response: dict):
        self.response = response
        error = response["error"]
        super().__init__(f"{error['code']}: {error['message']}")


@dataclass(frozen=True)
class Pending:
    request_id: str
    session_id: str
    request_path: Path
    response_path: Path
    published_mtime_ns: int
    created_unix_ms: int
    command: str
    expected_generation: int | None


def _json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise BridgeError(f"Cannot read complete JSON object {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise BridgeError(f"JSON object required: {path}")
    return result


def _publish(path: Path, value: dict) -> int:
    """Publish a complete new file atomically without replacing an old file."""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as out:
            json.dump(value, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        # Both names are in one directory/volume. NTFS supports hard links.
        # Unlike replace(), link() fails if the final request already exists.
        published_mtime_ns = temporary.stat().st_mtime_ns
        os.link(temporary, path)
        return published_mtime_ns
    except OSError as exc:
        raise BridgeError(f"Atomic new-file publication failed for {path}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


class Client:
    def __init__(self, root: Path | str, expected_session: str | None = None):
        self.root = Path(root).resolve(strict=True)
        self.session_path = self.root / "session.json"
        self.session = self._session()
        self.session_id = self.session["session_id"]
        self.generation = None
        self.seen_operations: dict[int, str] = {}
        if expected_session is not None and self.session_id != expected_session:
            raise BridgeError("Wrong session: explicit expected-session guard failed")
        for name in ("requests", "responses"):
            path = self.root / name
            if not path.is_dir() or path.is_symlink():
                raise BridgeError(f"Expected bridge-owned ordinary directory: {path}")

    def _session(self) -> dict:
        session = _json(self.session_path)
        if type(session.get("schema")) is not int or session["schema"] != 1:
            raise BridgeError("Unsupported session protocol schema")
        if not isinstance(session.get("session_id"), str) or not session["session_id"]:
            raise BridgeError("Missing session identity")
        if not session.get("build_identity"):
            raise BridgeError("Missing bridge build identity")
        created = session.get("created_unix_ms")
        if isinstance(created, bool) or not isinstance(created, int) or created <= 0:
            raise BridgeError("Missing session creation timestamp")
        return session

    def _same_session(self) -> None:
        current = self._session()
        for key in ("schema", "session_id", "build_identity", "created_unix_ms"):
            if current.get(key) != self.session.get(key):
                raise BridgeError(f"Bridge session changed ({key}); reconnect explicitly")

    def submit(self, command: str, args: dict | None = None,
               *, generation: int | None = None) -> Pending:
        self._same_session()
        if not isinstance(command, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", command):
            raise BridgeError("Invalid command name")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise BridgeError("Command args must be an object")
        if command != "status":
            if generation is None and self.generation is None:
                raise BridgeError("Read status before submitting a non-status asynchronous request")
            if generation is None:
                generation = self.generation
            if type(generation) is not int or generation < 0:
                raise BridgeError("Non-status requests require a nonnegative state generation")
        request_id = uuid.uuid4().hex
        request = self.root / "requests" / (request_id + ".json")
        response = self.root / "responses" / (request_id + ".json")
        if request.exists() or response.exists():
            raise BridgeError("Request ID already has retained evidence; refusing reuse")
        created = time.time_ns() // 1_000_000
        value = {"schema": 1, "session_id": self.session_id,
                 "id": request_id, "command": command,
                 "args": args, "created_unix_ms": created}
        if command != "status":
            value["generation"] = generation
        published_mtime_ns = _publish(request, value)
        return Pending(request_id, self.session_id, request, response,
                       published_mtime_ns, created, command,
                       generation if command != "status" else None)

    def wait(self, pending: Pending, timeout: float = 130.0,
             *, raise_rejection: bool = True) -> dict:
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 600:
            raise BridgeError("Client timeout must be greater than zero and at most 600 seconds")
        if pending.session_id != self.session_id:
            raise BridgeError("Pending request belongs to another session")
        deadline = time.monotonic() + timeout
        while True:
            self._same_session()
            if pending.response_path.exists():
                # A pre-existing/stale result can never acknowledge this request.
                stat = pending.response_path.stat()
                if stat.st_mtime_ns < max(pending.published_mtime_ns,
                                          self.session["created_unix_ms"] * 1_000_000):
                    raise BridgeError("Stale response timestamp predates request or session")
                response = _json(pending.response_path)
                if type(response.get("schema")) is not int or response["schema"] != 1:
                    raise BridgeError("Unsupported response protocol schema")
                if response.get("session_id") != self.session_id:
                    raise BridgeError("Response belongs to wrong session")
                if response.get("id") != pending.request_id:
                    raise BridgeError("Response ID does not acknowledge this request")
                completed = response.get("completed_unix_ms")
                if type(completed) is not int or completed < max(
                        pending.created_unix_ms, self.session["created_unix_ms"]):
                    raise BridgeError("Missing/stale response completion timestamp")
                if response.get("build_identity") != self.session["build_identity"]:
                    raise BridgeError("Response build identity differs from session")
                generation = response.get("generation")
                sequence = response.get("operation_seq")
                if type(generation) is not int or generation < 0:
                    raise BridgeError("Response lacks a nonnegative state generation")
                if type(sequence) is not int or sequence < 1:
                    raise BridgeError("Response lacks a positive operation sequence")
                if sequence in self.seen_operations and self.seen_operations[sequence] != pending.request_id:
                    raise BridgeError("Operation sequence reused for another request")
                if type(response.get("ok")) is not bool:
                    raise BridgeError("Response lacks boolean completion status")
                if response["ok"]:
                    if "result" not in response or "error" in response:
                        raise BridgeError("Successful response requires result and no error")
                    if pending.command != "status":
                        expected = pending.expected_generation
                        if pending.command in ("load_state", "import_state"):
                            expected += 1
                        if generation != expected:
                            raise BridgeError(
                                f"Successful {pending.command} response generation {generation} "
                                f"does not match required generation {expected}")
                else:
                    error = response.get("error")
                    if not isinstance(error, dict) or not isinstance(error.get("code"), str) \
                            or not isinstance(error.get("message"), str):
                        raise BridgeError("Rejected response requires an explicit code/message")
                # Commit client bookkeeping only after the full response passes
                # validation. A rejected stale request may report the server's
                # newer generation; an older delayed reply never rolls us back.
                self.seen_operations[sequence] = pending.request_id
                self.generation = max(generation, self.generation or 0)
                if not response["ok"] and raise_rejection:
                    raise CommandRejected(response)
                return response
            if time.monotonic() >= deadline:
                raise BridgeTimeout(
                    f"No completed response for {pending.request_id}; command may still have effects. "
                    "No automatic retry was sent. Inspect status/cancel using a fresh request.")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def call(self, command: str, args: dict | None = None, timeout: float = 130.0,
             *, raise_rejection: bool = True, generation: int | None = None) -> dict:
        if command != "status" and generation is None and self.generation is None:
            self.call("status", timeout=timeout)
        return self.wait(self.submit(command, args, generation=generation), timeout,
                         raise_rejection=raise_rejection)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Fresh bridge session root")
    parser.add_argument("--expected-session", help="Require this exact session identity")
    parser.add_argument("--timeout", type=float, default=130.0, help="Response timeout in seconds")
    parser.add_argument("--generation", type=int, help="Explicit state-generation guard")
    parser.add_argument("--args", default="{}", help="Command argument JSON object")
    parser.add_argument("--out", type=Path, help="Optional new receipt path; never overwritten")
    parser.add_argument("command")
    args = parser.parse_args()
    try:
        if args.out and args.out.exists():
            raise BridgeError(f"Receipt already exists: {args.out}")
        arguments = json.loads(args.args)
        client = Client(args.root, args.expected_session)
        response = client.call(args.command, arguments, args.timeout, raise_rejection=False,
                               generation=args.generation)
        receipt = {"client_schema": 1, "bridge_root": str(client.root),
                   "observed_unix_ms": time.time_ns() // 1_000_000,
                   "session": client.session, "response": response}
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            _publish(args.out, receipt)
        print(json.dumps(receipt, indent=2))
        return 0 if response["ok"] else 2
    except (BridgeError, OSError, ValueError) as exc:
        print(json.dumps({"client_error": type(exc).__name__, "message": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: GPL-3.0-only
# Extracted from Grandia tools/windows_host_wait.py; see provenance.json.
"""Read-only x64 PE/process metadata helper; no process is opened at import."""
import ctypes
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import struct
import time
LABELS = {"", "SDL_RenderPresent", "SDL_PollEvent", "SDL_Delay", "SDL_DelayPrecise",
          "SDL_GetAudioStreamQueued", "SDL_PutAudioStreamData"}

def pin(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": Path(path).stat().st_size, "sha256": digest.hexdigest()}


def pe_layout(path):
    with Path(path).open("rb") as stream:
        def read_at(offset, size):
            stream.seek(offset)
            value = stream.read(size)
            if len(value) != size:
                raise ValueError("truncated PE headers")
            return value
        dos = read_at(0, 64)
        offset = struct.unpack_from("<I", dos, 60)[0]
        if dos[:2] != b"MZ" or offset > 16 * 1024 * 1024:
            raise ValueError("expected bounded PE headers")
        header = read_at(offset, 24)
        machine, count = struct.unpack_from("<HH", header, 4)
        optional_size = struct.unpack_from("<H", header, 20)[0]
        if header[:4] != b"PE\0\0" or machine != 0x8664 or not 1 <= count <= 128 or optional_size < 112:
            raise ValueError("expected x86-64 PE executable")
        optional = read_at(offset + 24, optional_size)
        if struct.unpack_from("<H", optional)[0] != 0x20B:
            raise ValueError("expected PE32+ layout")
        base = struct.unpack_from("<Q", optional, 24)[0]
        size = struct.unpack_from("<I", optional, 56)[0]
        sections = []
        for i in range(count):
            section = read_at(offset + 24 + optional_size + i * 40, 40)
            virtual_size, rva, raw_size = struct.unpack_from("<III", section, 8)
            flags = struct.unpack_from("<I", section, 36)[0]
            end = rva + max(virtual_size, raw_size)
            if end > size:
                raise ValueError("section exceeds PE image")
            sections.append({"start": rva, "end": end, "readable": bool(flags & 0x40000000),
                             "writable": bool(flags & 0x80000000)})
        return {"preferred_base": base, "image_size": size, "sections": sections}


def section_for(layout, rva, size=1, writable=False):
    return next((s for s in layout["sections"] if s["readable"] and (not writable or s["writable"])
                 and s["start"] <= rva and rva + size <= s["end"]), None)


class Target:
    def __init__(self, pid, binary, layout, hwnd=None):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ValueError("64-bit observer required")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle = None
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.GetProcessId.argtypes = [W.HANDLE]; self.k.GetProcessId.restype = W.DWORD
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
        self.k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; self.k.WaitForSingleObject.restype = W.DWORD
        self.k.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.k.K32EnumProcessModulesEx.argtypes = [W.HANDLE, ctypes.POINTER(W.HMODULE), W.DWORD, ctypes.POINTER(W.DWORD), W.DWORD]
        class Module(ctypes.Structure):
            _fields_ = [("base", ctypes.c_void_p), ("size", W.DWORD), ("entry", ctypes.c_void_p)]
        class FT(ctypes.Structure):
            _fields_ = [("low", W.DWORD), ("high", W.DWORD)]
        self.FT = FT
        self.k.K32GetModuleInformation.argtypes = [W.HANDLE, W.HMODULE, ctypes.POINTER(Module), W.DWORD]
        self.k.GetSystemTimes.argtypes = [ctypes.POINTER(FT)] * 3
        self.pid, self.hwnd, self.layout = pid, hwnd, layout
        try:
            self.handle = self.k.OpenProcess(0x0400 | 0x0010 | 0x00100000, False, pid)  # query, VM_READ, synchronize
            self.check(bool(self.handle))
            name, size = ctypes.create_unicode_buffer(32768), W.DWORD(32768)
            self.check(self.k.GetProcessId(self.handle) == pid)
            self.check(self.k.QueryFullProcessImageNameW(self.handle, 0, name, ctypes.byref(size)))
            if Path(name.value).resolve() != binary:
                raise ValueError("PID image does not equal the pinned owned binary")
            module, needed, info = W.HMODULE(), W.DWORD(), Module()
            self.check(self.k.K32EnumProcessModulesEx(self.handle, ctypes.byref(module), ctypes.sizeof(module), ctypes.byref(needed), 2))
            self.check(needed.value >= ctypes.sizeof(module))
            self.check(self.k.K32GetModuleInformation(self.handle, module, ctypes.byref(info), ctypes.sizeof(info)))
            if info.size != layout["image_size"]:
                raise ValueError("loaded module size does not match pinned PE")
            self.base = info.base
            if self.exited():
                raise ValueError("owned process already exited")
            if hwnd is not None:
                self.u = ctypes.WinDLL("user32", use_last_error=True)
                self.u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
                self.u.IsIconic.argtypes = self.u.IsWindowVisible.argtypes = [W.HWND]
                if self.window() == ("gone", "gone"):
                    raise ValueError("HWND does not belong to the pinned process")
        except BaseException:
            self.close(); raise

    @staticmethod
    def check(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def exited(self):
        status = self.k.WaitForSingleObject(self.handle, 0)
        if status not in (0, 258):
            self.check(False)
        return status == 0

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle); self.handle = None

    def read(self, address, count):
        if not 1 <= count <= 64:
            raise ValueError("remote read exceeds short metadata bound")
        buffer, read = ctypes.create_string_buffer(count), ctypes.c_size_t()
        self.check(self.k.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buffer, count, ctypes.byref(read)))
        if read.value != count:
            raise ValueError("short remote metadata read")
        return buffer.raw

    def window(self):
        if self.hwnd is None:
            return "", ""
        owner = W.DWORD()
        if not self.u.GetWindowThreadProcessId(self.hwnd, ctypes.byref(owner)) or owner.value != self.pid:
            return "gone", "gone"  # Never query a window now belonging to another process.
        return bool(self.u.IsIconic(self.hwnd)), bool(self.u.IsWindowVisible(self.hwnd))

    def snapshot(self, symbols, mode):
        start = time.perf_counter_ns()
        def u64(name):
            return int.from_bytes(self.read(self.base + symbols[name], 8), "little")
        before, pointer = u64("g_vblanks"), u64("g_host_call")
        section = section_for(self.layout, pointer - self.base)
        marker = "<invalid-marker-pointer>"
        if section:
            text = self.read(pointer, min(64, self.base + section["end"] - pointer)).split(b"\0", 1)
            if len(text) == 2:
                label = text[0].decode("ascii", errors="replace")
                marker = (label or "<unmarked>") if label in LABELS else "<unknown-marker>"
        after_pointer, after = u64("g_host_call"), u64("g_vblanks")
        if after < before:
            raise ValueError("VBlank counter moved backwards")
        if pointer != after_pointer:
            marker = "<unstable-read>"
        idle, kernel, user = self.FT(), self.FT(), self.FT()
        self.check(self.k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)))
        iconic, visible = self.window()
        matches = "" if mode is None else (iconic is True if mode == "minimized" else iconic is False and visible is True)
        end = time.perf_counter_ns()
        result = {"utc_ns": time.time_ns(), "monotonic_ns": (start + end) // 2, "read_span_ns": end - start,
                  "vblank_before": before, "vblank_after": after, "marker": marker,
                  "iconic": iconic, "visible": visible, "window_matches": matches}
        result.update({name: (value.high << 32) | value.low for name, value in (("idle", idle), ("kernel", kernel), ("user", user))})
        return result


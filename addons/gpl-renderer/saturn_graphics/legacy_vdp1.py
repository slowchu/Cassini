# SPDX-License-Identifier: GPL-3.0-only
"""Qualified decoder functions extracted unchanged; game/path CLI omitted."""
import hashlib
from collections import Counter
VRAM = 0x80000

def sha(data):
    return hashlib.sha256(data).hexdigest()


def wrapped(data, address, size):
    if len(data) != VRAM or not 0 <= size <= VRAM:
        raise ValueError("Invalid VDP1 region size")
    address &= VRAM - 1
    end = address + size
    return data[address:end] if end <= VRAM else data[address:] + data[:end - VRAM]


def word(data, address):
    return int.from_bytes(wrapped(data, address & ~1, 2), "big")


def decode(data, address):
    ctrl, pm, colr, size = (word(data, address + o) for o in (0, 4, 6, 10))
    kind, mode = ctrl & 15, pm >> 3 & 7
    textured = kind < 4
    width, height = (size >> 8 & 63) * 8, size & 255
    src = word(data, address + 8) * 8
    result = dict(address=address, type=kind, jump=ctrl >> 12 & 7,
                  command_sha256=sha(wrapped(data, address, 32)))
    refs = []
    if kind < 8:
        result.update(color_mode=mode, spd=bool(pm & 64), ecd=bool(pm & 128), mesh=bool(pm & 256),
                      calculation=pm & 7, msb_on=bool(pm & 32768), textured=textured)
    if textured:
        result.update(texture_address=src, width=width, height=height, hflip=bool(ctrl & 16),
                      vflip=bool(ctrl & 32), color_bank=None)
        length = (width * height // 2 if mode <= 1 else width * height if mode <= 4 else
                  width * height * 2 if mode == 5 else None)
        if length is not None:
            refs.append(("texture", src & ~15 if mode == 5 else src, length))
        else:
            result["unsupported_texture_mode"] = True
        if mode == 1:
            lut = (colr & ~3) * 8
            result.update(lut_address=lut, lut_low_bits=colr & 3,
                          lut_rgb_entries=sum(bool(word(data, lut + 2 * i) & 32768) for i in range(16)))
            refs.append(("lut", lut, 32))
        elif mode in (0, 2, 3, 4):
            result["color_bank"] = colr & {0: 0xFFF0, 2: 0xFFC0, 3: 0xFF80, 4: 0xFF00}[mode]
    result["vertices"] = [[((word(data, address + o) & 8191) ^ 4096) - 4096,
                            ((word(data, address + o + 2) & 8191) ^ 4096) - 4096] for o in (12, 16, 20, 24)]
    return result, refs


def walk(data):
    address = ret = 0
    commands, references, visited = [], Counter(), set()
    for steps in range(20000):
        if (address, ret) in visited:
            return commands, references, dict(reason="cycle", address=address, steps=steps)
        visited.add((address, ret))
        ctrl = word(data, address)
        if ctrl & 32768:
            return commands, references, dict(reason="end", address=address, steps=steps)
        jump = ctrl >> 12 & 7
        if not jump & 4:
            command, refs = decode(data, address)
            commands.append(command)
            references.update(refs)
        link = word(data, address + 2) * 8 & (VRAM - 1)
        action = jump & 3
        if action == 0: address += 32
        elif action == 1: address = link
        elif action == 2: ret, address = address + 32, link
        else: address = ret
        address &= VRAM - 1
    return commands, references, dict(reason="limit", address=address, steps=20000)


def summary(commands, termination):
    sprites = [c for c in commands if c.get("textured")]
    return dict(commands=len(commands), draw_commands=sum(c["type"] < 8 for c in commands),
                textured_commands=len(sprites), types=dict(Counter(c["type"] for c in commands)),
                textured_modes=dict(Counter(c["color_mode"] for c in sprites)), termination=termination,
                textured_spd_true=sum(c["spd"] for c in sprites), textured_spd_false=sum(not c["spd"] for c in sprites),
                textured_ecd_true=sum(c["ecd"] for c in sprites), mesh=sum(c.get("mesh", False) for c in commands),
                rgb_luts=sum(c.get("lut_rgb_entries") == 16 for c in sprites),
                unaligned_luts=sum(bool(c.get("lut_low_bits")) for c in sprites))


def selfcheck():
    data = bytearray(VRAM)
    def put(a, value): data[a:a + 2] = value.to_bytes(2, "big")
    put(10, 0x0303); put(32, 0x8000)
    commands, refs, end = walk(bytes(data))
    assert len(commands) == 1 and refs["texture", 0, 36] == 1 and end["reason"] == "end"
    put(0, 0x2009); put(2, 12); put(32, 0x4008); put(64, 0x8000); put(96, 0x3009)
    commands, _, end = walk(bytes(data))
    assert [c["address"] for c in commands] == [0, 96] and end["address"] == 64
    put(0, 0x6009); put(32, 0x8000)
    assert [c["address"] for c in walk(bytes(data))[0]] == [96]
    put(0, 0x1009); put(2, 0)
    assert walk(bytes(data))[2]["reason"] == "cycle"
    data[-1] = 0xA5; data[0] = 0x5A
    assert wrapped(bytes(data), VRAM - 1, 3) == bytes((0xA5, 0x5A, data[1]))
    put(0, 0xC000)
    assert walk(bytes(data))[0] == []
    return {"synthetic_checks": 6, "passed": True}

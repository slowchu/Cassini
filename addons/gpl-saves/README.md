# Optional save transport (GPL-3.0-only)

Use this add-on to copy supported Saturn save entries between BUP, native
player and Ymir formats, for example when testing the same progress in two
players. It writes new files and leaves the originals alone. Ymir output
requires a separately built converter. It does not check whether a save is
valid for a particular game or repair its progress; keep your normal saves
backed up and use copies for testing.

`saves` reads BUP/native file-entry metadata. Explicit `--to native` writes a fresh native copy; `--to ymir` also imports that copy through the unchanged qualified Ymir backup API converter. Inputs remain unchanged. No game-specific checksum or progress claim is made.

Configure `profile.saves` with pinned `adapter`, `python`, and optionally `converter` files. Set `allow_external_tools: true`. The converter is an explicit external tool, never installed or built automatically.

Build `native_to_ymir.cpp` against your pinned Ymir source and its bundled mio headers using an existing C++23 compiler:

```text
clang++ -std=c++23 -O2 -ffunction-sections -fdata-sections -Wl,--gc-sections -I YMIR/libs/ymir-core/include -I YMIR/vendor/mio/include native_to_ymir.cpp YMIR/libs/ymir-core/src/ymir/sys/backup_ram.cpp -o native-to-ymir
```

Pin the resulting executable in the profile. This optional source dependency is Ymir, not a Grandia checkout. Preserve Ymir's GPL and mio's MIT notices if redistributing their sources/binaries.

Vmem BUP envelope fields and payload are transported exactly to native records. The opaque original 64-byte envelope is preserved separately. Ymir stores strings up to their first NUL and normalizes unused suffix bytes; conversion is not an exact whole-envelope round trip. Multiple native records are supported; duplicate names, malformed lengths and unterminated metadata are refused. Raw backup images/SSF files require an appropriate external reader and are never guessed from size.

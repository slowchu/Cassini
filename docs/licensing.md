# Licence and publication boundary

New core, tests, schemas, docs and skills are MIT. Imported/adapted algorithms
remain in optional process add-ons, outside the MIT module tree. Every shipped
file has an origin, licence and hash in the root provenance manifest. Each
add-on also documents donor identities and adaptations.

The combined GPL add-ons retain their GPL3 notices. Individual renderer source
grants, including MIT components and Mednafen GPL-2.0-or-later code, remain
explicit per file; packaging never silently relicenses them as MIT.
Ymir bridge/backup/state utilities retain GPL provenance. Full donor notices
are supplied. A process boundary does not settle every distribution question:
preserve corresponding source and compatible grants when redistributing.

Source and explicitly synthetic example PNGs are shipped. Existing compiled renderer/reader/converter and
research-emulator binaries are pinned private dependencies, never copied
into the kit. Explicit build recipes require existing compilers and external
Ymir headers where stated. No automatic download or installation occurs.

Fixtures and the README example images are generated from synthetic logic in
examples/demo.py, with no game inputs. Never add game bytes,
screenshots/crops, saves, states, BIOS, audio, extracted assets or disc-derived
generated guest code. Generic generated dump-helper code based on public Ymir
structure declarations is tool source, separately identified in provenance,
and contains no captured values. Filled profiles and result paths remain
private and need review before sharing.

Run check_provenance.py against the root manifest. Review new donor grants
before adding files; do not classify unknown code from a repository-wide
label alone. See NOTICE and add-on provenance records for the exact boundary.

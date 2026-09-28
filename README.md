# demoparser-fix (build-only branch)

Patched copy of [LaihoE/demoparser](https://github.com/LaihoE/demoparser) 0.42.0 (MIT, see demoparser/LICENSE).
Fix: CS2 entity handles were masked with `0x7FF` (2047), so players whose pawn entity index is > 2047 (big
workshop maps) had no positions. Changed to `0x3FFF`; see `demoparser/entity-handle-fix.patch`.
The workflow builds a Windows / Python 3.13 wheel into `wheels/`. Delete this branch to undo.

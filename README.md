# gr-soarr

GNU Radio out-of-tree module implementing the ground-station side of a
CCSDS Telecommand (TC) uplink chain for ARIS's SOARR mission.

See [CONTEXT.md](CONTEXT.md) for what this is,
[claude/architecture.md](claude/architecture.md) for how it works, and
[claude/development.md](claude/development.md) for build/install/test
setup.

## Quick start

```powershell
conda activate radioconda
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
cmake --install build --config Release
pytest .\python\soarr\ -q
```

Full setup details, prerequisites, and troubleshooting:
[claude/development.md](claude/development.md).

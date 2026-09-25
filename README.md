# gr-soarr

GNU Radio out-of-tree module implementing the ground-station side of a
CCSDS Telecommand (TC) uplink chain for ARIS's SOARR mission.

See [docs/architecture.md](docs/architecture.md) for how it works, and
[docs/development.md](docs/development.md) for build/install/test
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
[docs/development.md](docs/development.md).

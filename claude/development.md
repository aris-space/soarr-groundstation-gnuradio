# Development

Build, install, and test setup for gr-soarr.

## Prerequisites

- Visual Studio 2022 (Community or Build Tools)
- `radioconda` conda environment
- GNU Radio 3.10.12 in `radioconda`
- Matching Boost development packages in `radioconda`

## CMake configuration (Windows / radioconda)

The workspace uses these settings in
[`.vscode/settings.json`](../.vscode/settings.json):

| Setting | Value |
|---|---|
| `cmake.sourceDirectory` | `C:/ARIS/soarr-groundstation-gnuradio` |
| `cmake.buildDirectory` | `C:/ARIS/soarr-groundstation-gnuradio/build` |
| `cmake.generator` | `Visual Studio 17 2022` |
| `cmake.platform` | `x64` |
| `CMAKE_PREFIX_PATH` | `C:/Users/yanni/anaconda3/envs/radioconda/Library` |
| `Gnuradio_DIR` | `C:/Users/yanni/anaconda3/envs/radioconda/Library/lib/cmake/gnuradio` |
| `MPIR_INCLUDE_DIR` | `C:/Users/yanni/anaconda3/envs/radioconda/Library/include` |
| `MPIR_LIBRARY` | `C:/Users/yanni/anaconda3/envs/radioconda/Library/lib/mpir.lib` |
| `MPIRXX_LIBRARY` | `C:/Users/yanni/anaconda3/envs/radioconda/Library/lib/mpirxx_static.lib` |

If GNU Radio configure fails on Boost headers, install the matching
development package in the same env:

```powershell
conda activate radioconda
conda install -c conda-forge libboost-devel=1.88.0
```

## Configure, build, install (Windows)

From the repository root:

```powershell
conda activate radioconda
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
cmake --install build --config Release
```

## Running tests

```powershell
conda activate radioconda
pytest .\python\soarr\ -q
```

**Known conflict, needs reconciling:** `.vscode/settings.json` currently
configures Python testing via `unittest` discovery
(`python.testing.unittestEnabled: true`, `pytestEnabled: false`,
`unittestArgs: ["-p", "qa_*.py"]`), while
[`pytest.ini`](../pytest.ini) configures pytest-style discovery
(`python_files = qa_*.py *_test.py test_*.py`) — the same convention the
command above and this project's actual test workflow both use. VS Code's
built-in Test Explorer will not match the documented `pytest` workflow
until this is reconciled. Use the command-line `pytest` invocation above;
don't rely on the Test Explorer's results.

## The shadow-install problem

On Windows/conda, a previous `cmake --install` can leave a real, copied
install of the module at
`<radioconda-env>/Lib/site-packages/gnuradio/soarr`. Because that
directory sits on `sys.path`, it **shadows** the in-repo workspace source
(`python/soarr`) — Python silently keeps importing the old installed copy,
and edits to the repo appear to have no effect.

`tools/ensure_gnuradio_soarr_dev.py` fixes this:

1. Deletes the shadowing `<site-packages>/gnuradio/soarr` directory, if
   present (guarded — only deletes if the directory actually looks like a
   gr-soarr install, e.g. contains `cltuFramer.py`/`bchEncoder.py`).
2. Writes `<site-packages>/gnuradio_soarr_workspace.pth`, pointing at
   `<repo>/python`, so the workspace source is what `sys.path` resolves
   `gnuradio.soarr` to.
3. Verifies `gnuradio.soarr.cltuDeframer` actually resolves to the
   workspace, not a stale copy.

Run it (inside the target conda env) after any `cmake --install` if you
notice stale behavior:

```powershell
conda activate radioconda
python tools\ensure_gnuradio_soarr_dev.py --yes   # or --dry-run to preview
# PowerShell wrapper, equivalent (the -Python flag must be given explicitly,
# otherwise --yes silently binds to it instead of being forwarded):
.\tools\ensure_gnuradio_soarr_dev.ps1 -Python python --yes
```

## WSL/Linux alternative: `install_gr_soarr.sh`

`tools/install_gr_soarr.sh` is a clean-room rebuild script for WSL/Linux:
it wipes `build/` (and any stale in-source CMake artifacts) before
configuring, runs configure/build/install, verifies the resulting import
origin, then cleans `build/` again. Use it instead of the manual CMake
sequence above when you want a guaranteed-clean rebuild rather than
reusing a possibly-stale `build/` directory.

It defaults `MODULE_DIR` (the repo location) to `$HOME/hslu/library/gr-soarr`
— override with `--module-dir` if your clone lives elsewhere:

```bash
./tools/install_gr_soarr.sh --module-dir /path/to/soarr-groundstation-gnuradio
```

Other options: `--prefix <path>` (default `$CONDA_PREFIX` or `/usr/local`),
`--python <exe>` (default `python3`), `--config <name>` (default
`Release`), `--sudo-install`.

## GRC workflow

The 20 blocks are exposed to GNU Radio Companion via
`grc/soarr_*.block.yml`. There is currently no example `.grc` flowgraph in
this repo demonstrating the full TX or RX chain (see
[architecture.md](architecture.md)'s Known Gaps) —
`examples/dbClient_example.yaml` is a YAML *data* file for `dbClient`'s
type=1 config mode, not a flowgraph.

## Dependencies

From [`requirements.txt`](../requirements.txt):

- `construct` — bitstream parsing (used in `ccsdsReader`, `ccsdsReceiver`,
  `sdlsHeader`, `encapsulationHeader`)
- `PyYAML` — `dbClient`'s YAML config mode
- `pycryptodome` — SDLS encryption/authentication
- `pytest` — test framework

GNU Radio itself is not installed via this file — install it through
`radioconda`/conda instead.

## Coding standards

Naming, message-handler error handling, and docstring/PMT-shape rules for
writing or modifying blocks: [coding-standards.md](coding-standards.md).

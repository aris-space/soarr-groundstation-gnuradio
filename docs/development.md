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
| `cmake.sourceDirectory` | `${workspaceFolder}` |
| `cmake.buildDirectory` | `${workspaceFolder}/build` |
| `cmake.generator` | `Visual Studio 17 2022` |
| `cmake.platform` | `x64` |
| `CMAKE_PREFIX_PATH` | `${env:CONDA_PREFIX}/Library` |
| `Gnuradio_DIR` | `${env:CONDA_PREFIX}/Library/lib/cmake/gnuradio` |
| `MPIR_INCLUDE_DIR` | `${env:CONDA_PREFIX}/Library/include` |
| `MPIR_LIBRARY` | `${env:CONDA_PREFIX}/Library/lib/mpir.lib` |
| `MPIRXX_LIBRARY` | `${env:CONDA_PREFIX}/Library/lib/mpirxx_static.lib` |
| `CMAKE_INSTALL_PREFIX` | `${env:CONDA_PREFIX}/Library` |
| `GR_PYTHON_DIR` | `${env:CONDA_PREFIX}/Lib/site-packages` |

Conda on Windows keeps GNU Radio under `Library`, which is also where GNU
Radio Companion looks for block definitions
(`Library/share/gnuradio/grc/blocks`), while Python packages live in the
env's `Lib/site-packages`. The install prefix and `GR_PYTHON_DIR` follow
that split; with the env root as prefix instead, the block definitions
would land in a folder GRC never reads.

`CONDA_PREFIX` is read from the environment VS Code was started in, so
launch it from the activated env (`conda activate radioconda`, then
`code .`); otherwise the paths resolve empty and configure fails to find
GNU Radio.

If GNU Radio configure fails on Boost headers, install the matching
development package in the same env:

```powershell
conda activate radioconda
conda install -c conda-forge libboost-devel=1.88.0
```

## Configure, build, install (Windows)

`tools/install_gr_soarr.ps1` is a one-shot clean rebuild script — the
Windows/PowerShell counterpart to `install_gr_soarr.sh` below. It derives
`CMAKE_PREFIX_PATH`/`Gnuradio_DIR`/`MPIR_*` from `$env:CONDA_PREFIX`
instead of hardcoding a path, wipes `build/` before configuring so a stale
cache never blocks reconfiguration, configures/builds/installs, and checks
that `gnuradio.soarr` imports from the install location. Unlike the Linux
script it does **not** delete `build/` afterward — regenerating a Visual
Studio solution is expensive, and it can be reopened or rebuilt
incrementally.

```powershell
conda activate radioconda
.\tools\install_gr_soarr.ps1
```

Options: `-ModuleDir <path>` (default: this script's own repo root),
`-Prefix <path>` (default `$env:CONDA_PREFIX\Library`), `-Python <exe>` (default
the env's own `python.exe`), `-Config <name>` (default `Release`),
`-Generator <name>` (default `Visual Studio 17 2022`), `-Platform <arch>`
(default `x64`), `-PipInstall` (also runs `pip install -r
requirements.txt`, off by default).

Equivalent manual sequence, if you'd rather run each step yourself:

```powershell
conda activate radioconda
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 `
    -DCMAKE_INSTALL_PREFIX="$env:CONDA_PREFIX\Library" `
    -DGR_PYTHON_DIR="$env:CONDA_PREFIX\Lib\site-packages"
cmake --build build --config Release
cmake --install build --config Release
```

GNU Radio Companion always uses the **installed** copy: after changing a
block, re-run the install script and restart GRC.

## Running tests

```powershell
conda activate radioconda
pytest .\python\soarr\ -q
```

Tests always run against the checkout's `python/soarr/`, not an installed
copy: [`conftest.py`](../conftest.py) registers that folder as
`gnuradio.soarr` before any test is collected, so no install or
environment change is needed to test an edit.

`.vscode/settings.json` configures Python testing via `pytest`
(`python.testing.pytestEnabled: true`, `pytestArgs: ["python/soarr"]`),
matching [`pytest.ini`](../pytest.ini)'s discovery rules
(`python_files = qa_*.py *_test.py test_*.py`) and the command above —
VS Code's Test Explorer and the command-line invocation agree.
`cmake.ctest.testExplorerIntegrationEnabled: false` keeps CMake Tools'
CTest tree out of the same Testing panel, so it shows pytest only.

## WSL/Linux one-shot rebuild: `install_gr_soarr.sh`

`tools/install_gr_soarr.sh` is the WSL/Linux counterpart to
`install_gr_soarr.ps1` above (a separate script, not a shared one, since
MSVC's Visual Studio generator needs native PowerShell rather than
bash-over-WSL): it wipes `build-linux/` (and any stale in-source CMake
artifacts) before configuring, runs configure/build/install, verifies the
resulting import origin, then cleans `build-linux/` again (unlike the
Windows script — regenerating a Makefile/Ninja build here is cheap, so
there's no reason to keep it around). It uses `build-linux/` rather than
`build/` so running it on the same checkout as the Windows build never
deletes the Visual Studio build.

It defaults `MODULE_DIR` (the repo location) to its own repo root, resolved
from the script's own location — works with no arguments for a normal
clone:

```bash
./tools/install_gr_soarr.sh
```

Override with `--module-dir <path>` if you're running it against a
different checkout. Other options: `--prefix <path>` (default
`$CONDA_PREFIX` or `/usr/local`), `--python <exe>` (default `python3`),
`--config <name>` (default `Release`), `--sudo-install`, `--pip-install`
(also runs `pip install -r requirements.txt`, off by default).

### Installing from WSL into a Windows checkout

WSL can't use the Windows radioconda. Install GNU Radio inside WSL
instead, together with the Python dependencies (Ubuntu 24.04 blocks
`pip install` into the system Python, so take them from `apt` too):

```bash
sudo apt install gnuradio gnuradio-dev cmake g++ \
    python3-construct python3-yaml python3-pycryptodome python3-pytest
```

With no conda env active, the script
installs into `/usr/local`, which the system `python3` and GNU Radio
Companion already search. Add `--sudo-install` unless you run as root.

Run the script straight from the Windows checkout rather than copying it —
it finds the repo from its own location, so WSL always installs the code
you're editing. A shell alias in `~/.bashrc` makes this one command:

```bash
alias gr-soarr-install='/mnt/c/<path-to-repo>/tools/install_gr_soarr.sh'
```

[`.gitattributes`](../.gitattributes) keeps `*.sh` checked out with LF
line endings even on Windows, so bash can run the script from `/mnt/c`.

**Network USRPs under WSL drop packets.** Streaming from a network USRP
(N200/N210) into WSL2 loses sample packets — UHD prints `D` (dropped
packet / RX sequence error). Measured with UHD's `benchmark_rate` at
400 kS/s over the same link: 574 sequence errors and ~2.2 % of samples
lost in 60 s under WSL2 (mirrored networking), none natively on Windows.
Run USRP flowgraphs natively on Windows (radioconda includes UHD), or pass
a USB network adapter straight into WSL with `usbipd-win` so the Linux
driver handles it instead of the Windows network stack.

## GRC workflow

The 23 blocks are exposed to GNU Radio Companion via
`grc/soarr_*.block.yml`. Example flowgraphs for the full TX and RX chain —
software only and over a USRP — are in [`examples/`](../examples/); see
its README.

## Dependencies

```powershell
conda activate radioconda
pip install -r requirements.txt
```

From [`requirements.txt`](../requirements.txt):

- `construct` — bitstream parsing (used in `ccsds_reader`, `ccsds_receiver`,
  `sdls_header`, `encapsulation_header`)
- `PyYAML` — `db_client`'s YAML config mode
- `pycryptodome` — SDLS encryption/authentication
- `numpy` — used directly by `acquisition_idle_sequencer`, `ccsds_receiver`,
  `cltu_deframer`, `data_creator` (also a GNU Radio dependency, so normally
  already present via `radioconda`)
- `pytest` — test framework

GNU Radio itself is not installed via this file — install it through
`radioconda`/conda instead.

## Commit message hook

Commit messages follow the Conventional Commits structure documented in
[coding-standards.md](coding-standards.md#commit-messages), enforced by a
`commit-msg` hook at `tools/git-hooks/commit-msg`. One-time setup per clone:

```
git config core.hooksPath tools/git-hooks
```

## Coding standards

Naming, message-handler error handling, docstring/PMT-shape, and commit
message rules for writing or modifying blocks:
[coding-standards.md](coding-standards.md).

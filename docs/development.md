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
Windows/PowerShell counterpart to `install_gr_soarr.sh` below (a separate
script, not a wrapper around it, since MSVC's Visual Studio generator needs
native PowerShell rather than bash-over-WSL). It derives
`CMAKE_PREFIX_PATH`/`Gnuradio_DIR`/`MPIR_*` from `$env:CONDA_PREFIX`
instead of hardcoding a path, wipes `build/` before configuring so a stale
cache never blocks reconfiguration, configures/builds/installs, then runs
`ensure_gnuradio_soarr_dev.py --yes` (unless `-SkipLink`) to fix the
shadow-install and workspace-link problems described below. Unlike the
Linux script it does **not** delete `build/` afterward — regenerating a
Visual Studio solution is expensive, and the usual Windows workflow is to
reopen/incrementally rebuild it.

```powershell
conda activate radioconda
.\tools\install_gr_soarr.ps1
```

Options: `-ModuleDir <path>` (default: this script's own repo root),
`-Prefix <path>` (default `$env:CONDA_PREFIX`), `-Python <exe>` (default
`python`), `-Config <name>` (default `Release`), `-Generator <name>`
(default `Visual Studio 17 2022`), `-Platform <arch>` (default `x64`),
`-PipInstall` (also runs `pip install -r requirements.txt`, off by
default), `-SkipLink` (skip the `ensure_gnuradio_soarr_dev.py` step, just
verify the plain installed import).

Equivalent manual sequence, if you'd rather run each step yourself:

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

`.vscode/settings.json` configures Python testing via `pytest`
(`python.testing.pytestEnabled: true`, `pytestArgs: ["python/soarr"]`),
matching [`pytest.ini`](../pytest.ini)'s discovery rules
(`python_files = qa_*.py *_test.py test_*.py`) and the command above —
VS Code's Test Explorer and the command-line invocation agree.
`cmake.ctest.testExplorerIntegrationEnabled: false` keeps CMake Tools'
CTest tree out of the same Testing panel, so it shows pytest only.

## Making `gnuradio.soarr` resolve to the workspace

Two separate problems can each stop `import gnuradio.soarr` from resolving
to your repo checkout, and `tools/ensure_gnuradio_soarr_dev.py` fixes both:

**The shadow-install problem.** On Windows/conda, a previous
`cmake --install` can leave a real, copied install of the module at
`<radioconda-env>/Lib/site-packages/gnuradio/soarr`. Because that
directory sits on `sys.path`, it **shadows** the in-repo workspace source
(`python/soarr`) — Python silently keeps importing the old installed copy,
and edits to the repo appear to have no effect.

**The missing workspace link problem.** Separately,
`import gnuradio.soarr` can't resolve to the workspace *at all* unless
`python/gnuradio/soarr` exists as a live link to `python/soarr`:
`gnuradio` resolves to `python/gnuradio/` (a real package with a tracked
`__init__.py`), and Python only looks for the `soarr` submodule inside
that same directory — not elsewhere on `sys.path`, even with the `.pth`
file below in place. This link isn't tracked by git (see `.gitignore`),
and on a Windows account without the symlink privilege, a plain `ln -s`
silently falls back to a one-time, non-live copy that goes stale the next
time a file under `python/soarr/` changes.

`tools/ensure_gnuradio_soarr_dev.py` fixes both:

1. Deletes the shadowing `<site-packages>/gnuradio/soarr` directory, if
   present (guarded — only deletes if the directory actually looks like a
   gr-soarr install, e.g. contains `cltu_framer.py`/`bch_encoder.py`, or
   their pre-rename `cltuFramer.py`/`bchEncoder.py` equivalents).
2. Writes `<site-packages>/gnuradio_soarr_workspace.pth`, pointing at
   `<repo>/python`, so the workspace source is what `sys.path` resolves
   `gnuradio.soarr` to.
3. (Re)creates `python/gnuradio/soarr` as a real NTFS junction to
   `python/soarr` on Windows (no special privilege needed, unlike a
   symlink), or a plain symlink on Linux/macOS. Safe against a stale
   pre-existing copy from an earlier failed `ln -s` — that gets removed
   and replaced, not merged into.
4. Verifies `gnuradio.soarr.cltu_deframer` actually resolves to the
   workspace, not a stale copy.

`tools/install_gr_soarr.ps1` above already runs this after installing. Run
it manually (inside the target conda env) if you used the manual CMake
sequence instead, or after any `cmake --install` if you notice stale
behavior:

```powershell
conda activate radioconda
python tools\ensure_gnuradio_soarr_dev.py --yes   # or --dry-run to preview
# PowerShell wrapper, equivalent:
.\tools\ensure_gnuradio_soarr_dev.ps1 --yes
```

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

## GRC workflow

The 20 blocks are exposed to GNU Radio Companion via
`grc/soarr_*.block.yml`. There is currently no example `.grc` flowgraph in
this repo demonstrating the full TX or RX chain (see
[architecture.md](architecture.md)'s Known Gaps) —
`examples/db_client_example.yaml` is a YAML *data* file for `db_client`'s
type=1 config mode, not a flowgraph.

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

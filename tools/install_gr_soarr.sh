#!/usr/bin/env bash
set -euo pipefail

# WSL/Linux install helper. The module root is the repo root itself, so
# source dir == repo dir.
#
# Build directory is always:
#   <repo>/build-linux
# (separate from <repo>/build, which the Windows/Visual Studio build uses,
# since this script deletes its build directory before and after building)

# Default MODULE_DIR to this script's own repo root (tools/.. ), mirroring
# ensure_gnuradio_soarr_dev.py's _repo_root() — works out of the box for any
# clone, not just the original author's machine. Override with --module-dir.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_MODULE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODULE_DIR="${MODULE_DIR:-$DEFAULT_MODULE_DIR}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG="Release"
SUDO_INSTALL=0
PIP_INSTALL=0
PREFIX="${CONDA_PREFIX:-/usr/local}"

resolve_paths() {
    SRC_DIR="${MODULE_DIR}"
    REPO_DIR="${MODULE_DIR}"
    BUILD_DIR="${REPO_DIR}/build-linux"
}

resolve_paths

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --module-dir <path>   Repo/gr-soarr source dir (default: this script's own repo root)
  --prefix <path>       CMAKE_INSTALL_PREFIX (default: CONDA_PREFIX or /usr/local)
  --python <exe>        Python executable for CMake (default: python3)
  --config <name>       Build config (default: Release)
  --sudo-install        Run install step with sudo
  --pip-install         Also run 'pip install -r requirements.txt' (off by default)
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --module-dir)
            MODULE_DIR="$2"
            resolve_paths
            shift 2
            ;;
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        --python)
            PYTHON_BIN="$2"
            shift 2
            ;;
        --config)
            CONFIG="$2"
            shift 2
            ;;
        --sudo-install)
            SUDO_INSTALL=1
            shift
            ;;
        --pip-install)
            PIP_INSTALL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ ! -d "${SRC_DIR}" ]]; then
    echo "ERROR: source directory not found: ${SRC_DIR}"
    exit 1
fi

if [[ ! -f "${SRC_DIR}/CMakeLists.txt" ]]; then
    echo "ERROR: ${SRC_DIR} does not look like a gr-soarr source directory"
    exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "ERROR: python executable not found: ${PYTHON_BIN}"
    exit 1
fi

if ! command -v cmake >/dev/null 2>&1; then
    echo "ERROR: cmake not found on PATH"
    exit 1
fi

if [[ ${PIP_INSTALL} -eq 1 ]]; then
    echo "Installing Python requirements: ${SRC_DIR}/requirements.txt"
    # The assignment is the `if` condition itself (not a preceding statement)
    # so a failing pip doesn't trip `set -e` before PIP_STATUS is captured.
    # Also checks pip's own output text for the PEP 668
    # "externally-managed-environment" failure, since some pip/Debian builds
    # report it while still exiting 0.
    if PIP_OUTPUT="$("${PYTHON_BIN}" -m pip install -r "${SRC_DIR}/requirements.txt" 2>&1)"; then
        PIP_STATUS=0
    else
        PIP_STATUS=$?
    fi
    echo "${PIP_OUTPUT}"
    if [[ ${PIP_STATUS} -ne 0 ]] || echo "${PIP_OUTPUT}" | grep -q "externally-managed-environment"; then
        echo "ERROR: pip install failed." >&2
        echo "On Debian/Ubuntu (PEP 668 'externally-managed-environment'), a system" >&2
        echo "${PYTHON_BIN} usually refuses direct pip installs. This script won't" >&2
        echo "override that automatically. Either:" >&2
        echo "  - use a virtualenv and re-run with --python <venv-python>, or" >&2
        echo "  - manually run: ${PYTHON_BIN} -m pip install -r ${SRC_DIR}/requirements.txt --break-system-packages" >&2
        exit 1
    fi
fi

echo "--- gr-soarr install ---"
echo "Repo:   ${REPO_DIR}"
echo "Source: ${SRC_DIR}"
echo "Build:  ${BUILD_DIR}"
echo "Prefix: ${PREFIX}"
echo "Python: ${PYTHON_BIN}"

echo "Removing existing build directory before configure: ${BUILD_DIR}"
rm -rf "${BUILD_DIR}"

# Clean up stale in-source artifacts if present.
if [[ -f "${SRC_DIR}/CMakeCache.txt" || -d "${SRC_DIR}/CMakeFiles" ]]; then
    echo "Removing stale in-source CMake artifacts from ${SRC_DIR}"
    rm -f "${SRC_DIR}/CMakeCache.txt"
    rm -rf "${SRC_DIR}/CMakeFiles"
fi

mkdir -p "${BUILD_DIR}"

echo "Configuring CMake"
cmake \
    -S "${SRC_DIR}" \
    -B "${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE="${CONFIG}" \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DPYTHON_EXECUTABLE="$(command -v "${PYTHON_BIN}")"

echo "Building"
cmake --build "${BUILD_DIR}" --config "${CONFIG}" -j "$(nproc 2>/dev/null || echo 4)"

echo "Installing"
if [[ ${SUDO_INSTALL} -eq 1 ]]; then
    sudo cmake --install "${BUILD_DIR}" --config "${CONFIG}"
else
    cmake --install "${BUILD_DIR}" --config "${CONFIG}"
fi

echo "Verifying import origin"
"${PYTHON_BIN}" - <<'PY'
import importlib.util
import pathlib
import sys

spec = importlib.util.find_spec("gnuradio.soarr.bch_decoder")
if spec is None or not spec.origin:
    print("ERROR: gnuradio.soarr.bch_decoder not found after install", file=sys.stderr)
    raise SystemExit(1)

origin = pathlib.Path(spec.origin).resolve()
print(f"bch_decoder origin: {origin}")
PY

echo "Cleaning build directory after install: ${BUILD_DIR}"
rm -rf "${BUILD_DIR}"

echo "Done. If imports still point to old code, run:"
echo "  ${PYTHON_BIN} ${REPO_DIR}/tools/ensure_gnuradio_soarr_dev.py --yes"

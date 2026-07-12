<#
.SYNOPSIS
    Windows/conda one-shot clean rebuild for gr-soarr. PowerShell counterpart
    to install_gr_soarr.sh (that one is WSL/Linux-only - MSVC/CMake's
    Visual Studio generator needs native PowerShell, not bash-over-WSL).

.DESCRIPTION
    Wipes build/ (and any stale in-source CMake artifacts) before
    configuring, so a stale cache never blocks reconfiguration. Derives all
    CMake settings (CMAKE_PREFIX_PATH, Gnuradio_DIR, MPIR_*) from
    $env:CONDA_PREFIX rather than hardcoding a path - works for any conda
    env name/username, not just this machine's .vscode/settings.json values.
    Configures, builds, installs, then (unless -SkipLink) runs
    ensure_gnuradio_soarr_dev.py --yes, since on Windows/conda a
    cmake --install alone leaves two problems unresolved: a shadowing
    site-packages copy, and python/gnuradio/soarr not existing as a link to
    python/soarr (see claude/development.md).

    Unlike install_gr_soarr.sh, this script does NOT delete build/
    afterward: regenerating a Visual Studio solution is expensive, and
    Windows dev workflows generally want to reopen/incrementally rebuild it.

.PARAMETER Prefix
    CMAKE_INSTALL_PREFIX. Defaults to $env:CONDA_PREFIX. If you override
    this to somewhere other than the active conda env, be aware the
    ensure_gnuradio_soarr_dev.py step that runs afterward still targets
    the active env's site-packages (derived from -Python/$env:CONDA_PREFIX,
    not from -Prefix) - the shadow-install cleanup and workspace-link fixup
    won't see anything installed at a custom -Prefix. Use -SkipLink in
    that case and verify the install manually instead.

.EXAMPLE
    conda activate radioconda
    .\tools\install_gr_soarr.ps1

.EXAMPLE
    .\tools\install_gr_soarr.ps1 -PipInstall -Config Debug
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$ModuleDir,
    [string]$Prefix,
    [string]$Python,
    [string]$Config = "Release",
    [string]$Generator = "Visual Studio 17 2022",
    [string]$Platform = "x64",
    [switch]$PipInstall,
    [switch]$SkipLink
)

$ErrorActionPreference = "Stop"

function Exit-WithError {
    param([string]$Message)
    # -ErrorAction Continue: Write-Error would otherwise inherit the
    # script-wide $ErrorActionPreference = "Stop" and become a terminating
    # error itself, making the `exit 1` below dead code (verified: a
    # statement placed after Exit-WithError never runs either way, but
    # relying on that rather than on `exit 1` is fragile - e.g. a future
    # try/catch around a call to this function would swallow it).
    Write-Error $Message -ErrorAction Continue
    exit 1
}

# Default ModuleDir to this script's own repo root (tools\..), mirroring
# install_gr_soarr.sh's SCRIPT_DIR/DEFAULT_MODULE_DIR - works out of the box
# for any clone, not just the original author's machine.
if (-not $ModuleDir) {
    $ModuleDir = Split-Path -Parent $PSScriptRoot
}
$ModuleDir = (Resolve-Path $ModuleDir).Path
$BuildDir = Join-Path $ModuleDir "build"

if (-not (Test-Path (Join-Path $ModuleDir "CMakeLists.txt"))) {
    Exit-WithError "$ModuleDir does not look like a gr-soarr source directory"
}

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    Exit-WithError "cmake not found on PATH"
}

if (-not $env:CONDA_PREFIX) {
    Exit-WithError (
        "CONDA_PREFIX is not set. Activate your target conda env first, " +
        "e.g.:`n  conda activate radioconda"
    )
}
$LibDir = Join-Path $env:CONDA_PREFIX "Library"
if (-not $Prefix) {
    $Prefix = $env:CONDA_PREFIX
}

# Resolve Python from $env:CONDA_PREFIX directly rather than trusting a bare
# "python" on PATH: on this machine, setting CONDA_PREFIX alone (without a
# real `conda activate`, which also prepends the env to PATH) leaves PATH
# pointing at a *different* env's python - verified empirically, and it's
# exactly the kind of mismatch that silently reproduces the shadow-install
# bug this script exists to fix (e.g. importing without the target env's
# `pmt` bindings). The same resolved path is reused for every later
# invocation (pip, ensure_gnuradio_soarr_dev.py) instead of re-resolving
# "python" via PATH each time.
if (-not $Python) {
    $CandidatePython = Join-Path $env:CONDA_PREFIX "python.exe"
    if (Test-Path $CandidatePython) {
        $Python = $CandidatePython
    } else {
        Write-Warning (
            "$CandidatePython not found; falling back to 'python' on PATH, " +
            "which may not be `$env:CONDA_PREFIX's ($env:CONDA_PREFIX) interpreter."
        )
        $Python = "python"
    }
}
if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    Exit-WithError "Python executable not found: $Python"
}
$PythonExe = (Get-Command $Python).Source

$GnuradioDir = Join-Path $LibDir "lib\cmake\gnuradio"
$MpirInclude = Join-Path $LibDir "include"
$MpirLib = Join-Path $LibDir "lib\mpir.lib"
$MpirxxLib = Join-Path $LibDir "lib\mpirxx_static.lib"

if (-not (Test-Path $GnuradioDir)) {
    Exit-WithError (
        "GNU Radio CMake config not found under: $GnuradioDir`n" +
        "Is `$env:CONDA_PREFIX ($env:CONDA_PREFIX) really the env with GNU Radio installed? " +
        "See claude/development.md's Prerequisites/CMake configuration sections."
    )
}

if ($PipInstall) {
    Write-Host "Installing Python requirements: $ModuleDir\requirements.txt"
    & $PythonExe -m pip install -r (Join-Path $ModuleDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "pip install failed (exit code $LASTEXITCODE)."
    }
}

Write-Host "--- gr-soarr install ---"
Write-Host "Repo:      $ModuleDir"
Write-Host "Build:     $BuildDir"
Write-Host "Prefix:    $Prefix"
Write-Host "Python:    $PythonExe"
Write-Host "Generator: $Generator ($Platform)"

if (Test-Path $BuildDir) {
    Write-Host "Removing existing build directory before configure: $BuildDir"
    Remove-Item -Recurse -Force $BuildDir
}

# Clean up stale in-source artifacts if present (mirrors install_gr_soarr.sh).
$InSourceCache = Join-Path $ModuleDir "CMakeCache.txt"
$InSourceFiles = Join-Path $ModuleDir "CMakeFiles"
if ((Test-Path $InSourceCache) -or (Test-Path $InSourceFiles)) {
    Write-Host "Removing stale in-source CMake artifacts from $ModuleDir"
    Remove-Item -Force -ErrorAction SilentlyContinue $InSourceCache
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $InSourceFiles
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Host "Configuring CMake"
cmake -S $ModuleDir -B $BuildDir -G $Generator -A $Platform `
    "-DCMAKE_BUILD_TYPE=$Config" `
    "-DCMAKE_INSTALL_PREFIX=$Prefix" `
    "-DCMAKE_PREFIX_PATH=$LibDir" `
    "-DGnuradio_DIR=$GnuradioDir" `
    "-DMPIR_INCLUDE_DIR=$MpirInclude" `
    "-DMPIR_LIBRARY=$MpirLib" `
    "-DMPIRXX_LIBRARY=$MpirxxLib" `
    "-DPYTHON_EXECUTABLE=$PythonExe"
if ($LASTEXITCODE -ne 0) { Exit-WithError "CMake configure failed (exit code $LASTEXITCODE)." }

Write-Host "Building"
cmake --build $BuildDir --config $Config
if ($LASTEXITCODE -ne 0) { Exit-WithError "Build failed (exit code $LASTEXITCODE)." }

Write-Host "Installing"
cmake --install $BuildDir --config $Config
if ($LASTEXITCODE -ne 0) { Exit-WithError "Install failed (exit code $LASTEXITCODE)." }

if (-not $SkipLink) {
    Write-Host "Fixing shadow-install / workspace link (ensure_gnuradio_soarr_dev.py --yes)"
    & $PythonExe (Join-Path $PSScriptRoot "ensure_gnuradio_soarr_dev.py") --yes
    if ($LASTEXITCODE -ne 0) { Exit-WithError "ensure_gnuradio_soarr_dev.py failed (exit code $LASTEXITCODE)." }
} else {
    Write-Host "Skipping ensure_gnuradio_soarr_dev.py (-SkipLink); verifying import as installed"
    $code = @'
import importlib.util
import pathlib
import sys

spec = importlib.util.find_spec("gnuradio.soarr.bch_decoder")
if spec is None or not spec.origin:
    print("ERROR: gnuradio.soarr.bch_decoder not found after install", file=sys.stderr)
    raise SystemExit(1)

origin = pathlib.Path(spec.origin).resolve()
print(f"bch_decoder origin: {origin}")
'@
    # Written to a temp file rather than passed via `-c $code` directly:
    # PowerShell's argument marshalling to a native executable mangles
    # embedded double-quotes in a multi-line string (verified - `&
    # python -c $multilineCode` silently strips the quotes, breaking the
    # Python syntax). A real file sidesteps that entirely.
    $tempScript = New-TemporaryFile
    $tempScript = Rename-Item -Path $tempScript -NewName ($tempScript.Name + ".py") -PassThru
    try {
        Set-Content -Path $tempScript -Value $code -Encoding utf8
        & $PythonExe $tempScript
        if ($LASTEXITCODE -ne 0) { Exit-WithError "Import verification failed (exit code $LASTEXITCODE)." }
    } finally {
        Remove-Item -Path $tempScript -ErrorAction SilentlyContinue
    }
}

Write-Host "Done."

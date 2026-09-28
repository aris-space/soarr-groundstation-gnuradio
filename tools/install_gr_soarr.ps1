<#
.SYNOPSIS
    Clean configure, build and install of gr-soarr into the active conda
    environment (Windows). The Linux/WSL counterpart is install_gr_soarr.sh.

.DESCRIPTION
    Deletes build/ and any stale in-source CMake files, configures with the
    GNU Radio paths derived from $env:CONDA_PREFIX, builds, installs, and
    checks that gnuradio.soarr imports from the install location.

    build/ is kept afterwards: regenerating a Visual Studio solution is slow,
    and it can be reopened or rebuilt incrementally.

.PARAMETER ModuleDir
    Repository root. Defaults to the repository this script is in.

.PARAMETER Prefix
    CMAKE_INSTALL_PREFIX. Defaults to $env:CONDA_PREFIX.

.PARAMETER Python
    Python interpreter for CMake and the import check. Defaults to the one
    in $env:CONDA_PREFIX.

.PARAMETER PipInstall
    Also install requirements.txt with pip before building.

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
    [switch]$PipInstall
)

$ErrorActionPreference = "Stop"

function Exit-WithError {
    param([string]$Message)
    # -ErrorAction Continue keeps Write-Error from terminating before exit 1.
    Write-Error $Message -ErrorAction Continue
    exit 1
}

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
    Exit-WithError "CONDA_PREFIX is not set. Activate your GNU Radio conda env first, e.g.:`n  conda activate radioconda"
}

$LibDir = Join-Path $env:CONDA_PREFIX "Library"
if (-not $Prefix) {
    $Prefix = $env:CONDA_PREFIX
}

# Use the env's own interpreter rather than whichever "python" is first on PATH.
if (-not $Python) {
    $Python = Join-Path $env:CONDA_PREFIX "python.exe"
}
if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    Exit-WithError "Python executable not found: $Python"
}
$PythonExe = (Get-Command $Python).Source

$GnuradioDir = Join-Path $LibDir "lib\cmake\gnuradio"
if (-not (Test-Path $GnuradioDir)) {
    Exit-WithError "GNU Radio CMake config not found under $GnuradioDir. Is $env:CONDA_PREFIX the env with GNU Radio installed?"
}

if ($PipInstall) {
    Write-Host "Installing Python requirements"
    & $PythonExe -m pip install -r (Join-Path $ModuleDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Exit-WithError "pip install failed (exit code $LASTEXITCODE)." }
}

Write-Host "--- gr-soarr install ---"
Write-Host "Repo:      $ModuleDir"
Write-Host "Build:     $BuildDir"
Write-Host "Prefix:    $Prefix"
Write-Host "Python:    $PythonExe"
Write-Host "Generator: $Generator ($Platform)"

if (Test-Path $BuildDir) {
    Write-Host "Removing existing build directory: $BuildDir"
    Remove-Item -Recurse -Force $BuildDir
}
foreach ($stale in @("CMakeCache.txt", "CMakeFiles")) {
    $path = Join-Path $ModuleDir $stale
    if (Test-Path $path) {
        Write-Host "Removing stale in-source CMake artifact: $path"
        Remove-Item -Recurse -Force $path
    }
}

Write-Host "Configuring"
cmake -S $ModuleDir -B $BuildDir -G $Generator -A $Platform `
    "-DCMAKE_BUILD_TYPE=$Config" `
    "-DCMAKE_INSTALL_PREFIX=$Prefix" `
    "-DCMAKE_PREFIX_PATH=$LibDir" `
    "-DGnuradio_DIR=$GnuradioDir" `
    "-DMPIR_INCLUDE_DIR=$(Join-Path $LibDir 'include')" `
    "-DMPIR_LIBRARY=$(Join-Path $LibDir 'lib\mpir.lib')" `
    "-DMPIRXX_LIBRARY=$(Join-Path $LibDir 'lib\mpirxx_static.lib')" `
    "-DPYTHON_EXECUTABLE=$PythonExe"
if ($LASTEXITCODE -ne 0) { Exit-WithError "CMake configure failed (exit code $LASTEXITCODE)." }

Write-Host "Building"
cmake --build $BuildDir --config $Config
if ($LASTEXITCODE -ne 0) { Exit-WithError "Build failed (exit code $LASTEXITCODE)." }

Write-Host "Installing"
cmake --install $BuildDir --config $Config
if ($LASTEXITCODE -ne 0) { Exit-WithError "Install failed (exit code $LASTEXITCODE)." }

Write-Host "Verifying import"
& $PythonExe -c "import gnuradio.soarr as m; print('gnuradio.soarr imported from', m.__file__)"
if ($LASTEXITCODE -ne 0) { Exit-WithError "gnuradio.soarr cannot be imported after install." }

Write-Host "Done. Restart GNU Radio Companion to load the updated blocks."

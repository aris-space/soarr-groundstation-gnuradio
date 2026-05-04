param(
    [string]$Python = "python",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

# Runs the Python helper inside whatever environment $Python points to.
& $Python "$(Join-Path $PSScriptRoot 'ensure_gnuradio_sage_dev.py')" @Args

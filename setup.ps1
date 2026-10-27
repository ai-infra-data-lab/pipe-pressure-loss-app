$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or later is required.' }
}
& '.\.venv\Scripts\python.exe' -m pip --version
if ($LASTEXITCODE -ne 0) {
    & '.\.venv\Scripts\python.exe' -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Cannot initialize pip. Install a standard Python distribution and recreate .venv.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Double-click start.bat.'

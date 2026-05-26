# setup.ps1
# Bootstrap: fetch the optimusPy client and install dependencies (Windows / PowerShell).
$ErrorActionPreference = "Stop"

Write-Host "==> Cloning optimusPy to obtain optimusdb_client.py ..." -ForegroundColor Cyan
if (Test-Path .\.optimuspy) { Remove-Item -Recurse -Force .\.optimuspy }
git clone --depth 1 https://github.com/georgeGeorgakakos/optimusPy.git .optimuspy

Write-Host "==> Copying client into project root ..." -ForegroundColor Cyan
Copy-Item .\.optimuspy\optimusdb_client.py .\optimusdb_client.py -Force

Write-Host "==> Installing dependencies ..." -ForegroundColor Cyan
pip install -r requirements.txt

Write-Host "==> Done. Try: python tms_demo.py health" -ForegroundColor Green

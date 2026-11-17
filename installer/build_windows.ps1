<#
Builds the Windows installer. Run on Windows with: Python 3.12+, Inno Setup 6 (iscc on PATH), Node is NOT needed.
  powershell -File installer\build_windows.ps1 -Version 0.1.0 [-Gpu] [-Sign -CertFile cert.pfx] [-AllowUnconfirmed] [-AegpDll path\to\VoxyEasyAnimator.aex]
NOT RUN IN DEVELOPMENT (no Windows machine available). The same steps were run on Linux for the service part only.
#>
param(
  [string]$Version = "0.1.0",
  [switch]$Gpu,                 # bundle onnxruntime-gpu (NVIDIA fast mode)
  [switch]$Sign, [string]$CertFile, [string]$CertPassword,
  [switch]$AllowUnconfirmed,    # test builds only: the list of supported After Effects versions is not confirmed yet
  [switch]$UnsignedPanel,       # sets PlayerDebugMode so an unsigned panel loads (not for public release)
  [string]$AegpDll              # the AEGP .aex built from ae_host/aegp with the Adobe SDK
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\.."
Set-Location $root
$cfg = Get-Content installer\supported_ae_versions.json -Raw | ConvertFrom-Json
if (-not $cfg.confirmed -and -not $AllowUnconfirmed) { throw "The supported After Effects versions are not confirmed (installer\supported_ae_versions.json). Ask the project owner, or pass -AllowUnconfirmed for a test build." }

New-Item -ItemType Directory -Force build | Out-Null
python -m venv build\venv
& build\venv\Scripts\python -m pip install --upgrade pip
& build\venv\Scripts\python -m pip install -r service\requirements-dev.txt
if ($Gpu) { & build\venv\Scripts\python -m pip uninstall -y onnxruntime; & build\venv\Scripts\python -m pip install onnxruntime-gpu==1.29.0 }

# 1. the service, frozen (no separate Python for the user)
Push-Location service
& ..\build\venv\Scripts\pyinstaller voxy_service.spec --noconfirm --distpath ..\build --workpath ..\build\work
Pop-Location
Remove-Item -Recurse -Force build\service -ErrorAction SilentlyContinue
Rename-Item build\voxy-service build\service

# 2. checks the build the same way the installer will
$out = & build\service\voxy-service.exe --selftest --data-dir build\selftest
if ($LASTEXITCODE -ne 0) { throw "The packaged service failed its self-test: $out" }

# 3. models, third-party tools
if (Test-Path models) { Copy-Item -Recurse -Force models build\models }
& powershell -File installer\fetch_third_party.ps1
# 4. the AE plug-in (built separately with the Adobe SDK)
if ($AegpDll) { New-Item -ItemType Directory -Force build\plugin | Out-Null; Copy-Item $AegpDll build\plugin\VoxyEasyAnimator.aex }
# 5. help page, license page, installer include
& build\venv\Scripts\python installer\build_help.py
& build\venv\Scripts\python installer\make_license_bundle.py
& build\venv\Scripts\python installer\gen_supported_inc.py

$defs = @("/DAppVersion=$Version")
if ($UnsignedPanel) { $defs += "/DUNSIGNED_PANEL" }
if ($Sign) {
  if (-not $CertFile) { throw "-Sign needs -CertFile" }
  $defs += "/DSIGN"; $defs += "/Ssigntool=signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f `"$CertFile`" /p `"$CertPassword`" `$f"
} else { Write-Warning "Building UNSIGNED. Windows will show a SmartScreen warning; docs\INSTALL.md explains it to users." }
iscc @defs installer\VoxyEasyAnimator.iss
Write-Host "Installer: build\installer\VoxyEasyAnimator-Setup-$Version.exe"

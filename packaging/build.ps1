# Compila o Alflor Studio para Windows: .exe (PyInstaller, pasta única) + zip com instalador.
# Uso:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root '.venv\Scripts\python.exe'
$version = & $py -c "import sys; sys.path.insert(0, r'$root'); import ptz_studio; print(ptz_studio.__version__)"
$build = Join-Path $root 'build'
$dist = Join-Path $root 'dist'
Write-Host "== Alflor Studio $version"

# 1) recursos: ponte PTZ compilada, modelos de IA, FFmpeg, ícone
$env:ALFLOR_BRIDGE = (& $py -c "import sys; sys.path.insert(0, r'$root'); from ptz_studio.bridge import build_bridge; print(build_bridge())").Trim()
& $py -c "import sys; sys.path.insert(0, r'$root'); from ptz_studio import models; r = models.download_all(); print('modelos:', r or 'todos presentes')"
$env:ALFLOR_MODELS = (& $py -c "import sys; sys.path.insert(0, r'$root'); from ptz_studio import models; print(models.USER_DIR)").Trim()
$env:ALFLOR_FFMPEG = (& $py -c "import sys; sys.path.insert(0, r'$root'); from ptz_studio.media import find_ffmpeg; print(find_ffmpeg() or '')").Trim()
if (-not $env:ALFLOR_FFMPEG) { throw 'FFmpeg não encontrado (winget install Gyan.FFmpeg)' }
New-Item -ItemType Directory -Force $build | Out-Null
$env:ALFLOR_ICON = Join-Path $build 'alflor_studio.ico'
& $py (Join-Path $PSScriptRoot 'make_icon.py') $env:ALFLOR_ICON | Out-Null
Write-Host "ponte: $env:ALFLOR_BRIDGE"
Write-Host "modelos: $env:ALFLOR_MODELS"
Write-Host "ffmpeg: $env:ALFLOR_FFMPEG"

# 2) PyInstaller
& $py -m PyInstaller --noconfirm --clean --log-level WARN --distpath $dist --workpath (Join-Path $build 'pyinstaller') (Join-Path $PSScriptRoot 'alflor_studio.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou ($LASTEXITCODE)" }
$app = Join-Path $dist 'Alflor Studio'
if (-not (Test-Path (Join-Path $app 'Alflor Studio.exe'))) { throw 'Alflor Studio.exe não foi gerado' }

# 3) pacote: pasta da app + instalador + leia-me → zip
$stage = Join-Path $build "AlflorStudio-$version-win64"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
robocopy $app (Join-Path $stage 'Alflor Studio') /E /NFL /NDL /NJH /NJS /NP | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'installer\*') $stage -Force
Copy-Item (Join-Path $root 'LICENSE') $stage -Force
$zip = Join-Path $dist "AlflorStudio-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
& $py -c "import shutil, sys; shutil.make_archive(sys.argv[1][:-4], 'zip', sys.argv[2])" $zip $stage
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
$appSize = [math]::Round((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "== pronto: $zip ($size MB; aplicação instalada ocupa $appSize MB)"

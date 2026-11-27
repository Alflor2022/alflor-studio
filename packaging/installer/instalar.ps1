# Instalador do Alflor Studio: copia a aplicação para %LOCALAPPDATA%\Programs\Alflor Studio
# e cria atalhos no Ambiente de Trabalho e no Menu Iniciar. Não precisa de administrador.
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'Alflor Studio'
$dst = Join-Path $env:LOCALAPPDATA 'Programs\Alflor Studio'
if (-not (Test-Path (Join-Path $src 'Alflor Studio.exe'))) {
    Write-Host 'Pasta «Alflor Studio» não encontrada junto ao instalador. Extraia o zip completo primeiro.' -ForegroundColor Red
    exit 1
}
$running = Get-Process -Name 'Alflor Studio' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host 'O Alflor Studio está aberto. Feche-o e volte a executar o instalador.' -ForegroundColor Yellow
    exit 1
}
Write-Host "A instalar em $dst ..."
New-Item -ItemType Directory -Force $dst | Out-Null
robocopy $src $dst /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host "Falha a copiar ficheiros (robocopy $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
Copy-Item (Join-Path $PSScriptRoot 'desinstalar.ps1') $dst -Force

$shell = New-Object -ComObject WScript.Shell
$exe = Join-Path $dst 'Alflor Studio.exe'
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Alflor Studio.lnk'),
                   (Join-Path ([Environment]::GetFolderPath('Programs')) 'Alflor Studio.lnk'))) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.WorkingDirectory = $dst
    $s.IconLocation = "$exe,0"
    $s.Description = 'Alflor Studio - controlo PTZ, chroma, tracking e mesa de realização'
    $s.Save()
}
# Entrada em "Aplicações instaladas" (por utilizador) para poder desinstalar pelas Definições
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AlflorStudio'
New-Item -Path $key -Force | Out-Null
$ver = (Get-Item $exe).VersionInfo.ProductVersion
Set-ItemProperty $key -Name DisplayName -Value 'Alflor Studio'
Set-ItemProperty $key -Name Publisher -Value 'Alflor2022'
Set-ItemProperty $key -Name DisplayIcon -Value $exe
Set-ItemProperty $key -Name InstallLocation -Value $dst
Set-ItemProperty $key -Name UninstallString -Value "powershell -NoProfile -ExecutionPolicy Bypass -File `"$dst\desinstalar.ps1`""
Set-ItemProperty $key -Name NoModify -Value 1 -Type DWord
Write-Host 'Instalado. Atalhos criados no Ambiente de Trabalho e no Menu Iniciar.' -ForegroundColor Green
Start-Process $exe

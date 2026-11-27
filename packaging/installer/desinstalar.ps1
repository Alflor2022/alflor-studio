# Desinstala o Alflor Studio deste utilizador. As definições (%APPDATA%\AlflorStudio) e as
# fotos/vídeos gravados NÃO são apagados.
$dst = Join-Path $env:LOCALAPPDATA 'Programs\Alflor Studio'
Get-Process -Name 'Alflor Studio' -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
Start-Sleep 2
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Alflor Studio.lnk'),
                   (Join-Path ([Environment]::GetFolderPath('Programs')) 'Alflor Studio.lnk'))) {
    Remove-Item $lnk -ErrorAction SilentlyContinue
}
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AlflorStudio' -Recurse -ErrorAction SilentlyContinue
# a pasta é apagada depois de este script terminar (o próprio script está lá dentro)
Start-Process cmd -ArgumentList '/c', "timeout /t 2 /nobreak >nul & rmdir /s /q `"$dst`"" -WindowStyle Hidden
Write-Host 'Alflor Studio desinstalado. As suas definições e gravações foram mantidas.'

# Удаление артефактов сборки (закройте IDE, эмулятор, uvicorn перед запуском).
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

@(".dart_tool", "build") | ForEach-Object {
  if (Test-Path $_) { Remove-Item $_ -Recurse -Force }
}
Get-ChildItem -Path $root -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

$gradle = Join-Path $root "android\.gradle"
if (Test-Path $gradle) { Remove-Item $gradle -Recurse -Force }
$appBuild = Join-Path $root "android\app\build"
if (Test-Path $appBuild) { Remove-Item $appBuild -Recurse -Force }

Write-Host "Готово. Папку backend\.venv удалите вручную после остановки Python (или: deactivate, закрыть терминал, затем удалить)."

# Creates (or removes) a "Process Radar" shortcut that starts pythonw.exe + process_radar.py directly.
#   powershell -NoProfile -ExecutionPolicy Bypass -File shortcut.ps1 -Folder Programs|Startup|Desktop [-Remove]
#   powershell -NoProfile -ExecutionPolicy Bypass -File shortcut.ps1 -Dir "D:\My Desktop"
# An existing shortcut with the same name is never overwritten: if it is ours it is kept, otherwise the
# script stops with exit code 2 so you can decide. It never points at a .venv (the folder is synced).
param(
    [ValidateSet('Programs', 'Startup', 'Desktop')][string]$Folder = 'Programs',
    [string]$Dir = '',
    [switch]$Remove,
    [string]$Pythonw = ''
)
$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$script = Join-Path $appDir 'process_radar.py'
$base = if ($Dir) { $Dir } else { [Environment]::GetFolderPath($Folder) }
if (-not (Test-Path -LiteralPath $base)) { throw "Folder not found: $base" }
$lnk = Join-Path $base 'Process Radar.lnk'
$sh = New-Object -ComObject WScript.Shell
if ($Remove) {
    if (Test-Path -LiteralPath $lnk) {
        if ($sh.CreateShortcut($lnk).Arguments -like "*process_radar.py*") { Remove-Item -LiteralPath $lnk; Write-Host "Removed $lnk" }
        else { Write-Host "Not removed (not a Process Radar shortcut): $lnk"; exit 2 }
    }
    exit 0
}
function Test-Py([string]$exe) {
    if (-not $exe -or -not (Test-Path -LiteralPath $exe) -or $exe -like '*\.venv\*' -or $exe -like '*\WindowsApps\*') { return $false }
    & $exe -c "import PySide6.QtWidgets, psutil" *> $null
    return ($LASTEXITCODE -eq 0)
}
if (-not $Pythonw) {
    $cands = @()
    try { $cands += (& py -3 -c "import sys;print(sys.executable)" 2>$null).Trim() } catch { }
    foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) { $cands += $c.Source }
    foreach ($c in $cands) {
        if (Test-Py $c) { $Pythonw = Join-Path (Split-Path -Parent $c) 'pythonw.exe'; break }
    }
}
if (-not $Pythonw -or -not (Test-Path -LiteralPath $Pythonw)) {
    throw "No Python with PySide6 + psutil found. Run: py -m pip install --user psutil PySide6-Essentials"
}
if ($Pythonw -like '*\.venv\*') { throw "Refusing to use a .venv Python (synced folder): $Pythonw" }
if (Test-Path -LiteralPath $lnk) {
    $cur = $sh.CreateShortcut($lnk)
    if ($cur.Arguments -like "*process_radar.py*") { Write-Host "Already exists (kept): $lnk -> $($cur.TargetPath) $($cur.Arguments)"; exit 0 }
    Write-Host "A different shortcut named 'Process Radar' already exists, NOT overwritten: $lnk -> $($cur.TargetPath) $($cur.Arguments)"
    exit 2
}
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $Pythonw
$s.Arguments = '"' + $script + '"'
$s.WorkingDirectory = $appDir
$s.IconLocation = (Join-Path $appDir 'icon.ico') + ',0'
$s.Description = 'Process Radar - see what is running in the background'
$s.Save()
$chk = $sh.CreateShortcut($lnk)
Write-Host "Created $lnk -> $($chk.TargetPath) $($chk.Arguments) (icon $($chk.IconLocation))"

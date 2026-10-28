# Creates (or removes) a "Process Radar" shortcut that starts pythonw.exe + process_radar.py directly.
#   powershell -NoProfile -ExecutionPolicy Bypass -File shortcut.ps1 -Folder Programs|Startup|Desktop [-Remove]
#   powershell -NoProfile -ExecutionPolicy Bypass -File shortcut.ps1 -Dir "G:\My Drive\GoogleDrive\0 Desktop"
# An existing Process Radar shortcut is kept when it is up to date and repaired when it points at an old
# folder (e.g. after the app was moved); any other shortcut with that name is never overwritten (exit 2).
# It never points at a .venv (the folder is synced).
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
    $cands = @('C:\Python314\python.exe')
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
$verb = 'Created'
if (Test-Path -LiteralPath $lnk) {
    $cur = $sh.CreateShortcut($lnk)
    if ($cur.Arguments -notlike "*process_radar.py*") {
        Write-Host "A different shortcut named 'Process Radar' already exists, NOT overwritten: $lnk -> $($cur.TargetPath) $($cur.Arguments)"
        exit 2
    }
    $want = '"' + $script + '"'
    if ($cur.Arguments -eq $want -and $cur.TargetPath -eq $Pythonw -and $cur.WorkingDirectory -eq $appDir) {
        Write-Host "Up to date (kept): $lnk -> $($cur.TargetPath) $($cur.Arguments)"
        exit 0
    }
    # ours, but pointing at an old location (the folder was moved) or another Python: update it in place
    Write-Host "Updating (was: $($cur.TargetPath) $($cur.Arguments))"
    $verb = 'Updated'
}
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $Pythonw
$s.Arguments = '"' + $script + '"'
$s.WorkingDirectory = $appDir
$s.IconLocation = (Join-Path $appDir 'icon.ico') + ',0'
$s.Description = 'Process Radar - see what is running in the background'
$s.Save()
$chk = $sh.CreateShortcut($lnk)
Write-Host "$verb $lnk -> $($chk.TargetPath) $($chk.Arguments) (icon $($chk.IconLocation))"

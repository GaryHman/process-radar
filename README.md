# Process Radar

See at a glance what is running in the background on this PC – Ollama, ComfyUI, LM Studio, llama.cpp
servers, Docker, Jupyter, Python/Node servers and anything else without a window – with ports, CPU,
RAM, GPU memory (VRAM) and uptime.

## Start

* **Start menu** → *Process Radar*, or the **Process Radar** shortcut on your desktop
  (`G:\My Drive\GoogleDrive\0 Desktop`).
* Or double-click `run.bat` in this folder (it finds a Python with PySide6 + psutil and starts the app
  without a console; if none is found it shows one clear message box listing the Pythons it checked
  and writes the reason to `%APPDATA%\ProcessRadar\launcher.log`).
* **Ctrl+Alt+P** shows / hides the window from anywhere (change it under ⚙ Settings).
  Closing the window keeps the app in the tray (green radar icon); *Quit* is in the tray menu.
  Starting it a second time (shortcut, `run.bat`, hotkey) just brings the open window to the front –
  no second copy, no error message.

> **US-International keyboard:** Windows treats AltGr as Ctrl+Alt, so a global Ctrl+Alt+P also
> catches **AltGr+P** (which normally types **ö**). If you type ö, pick another combination in
> ⚙ Settings (for example Ctrl+Alt+Shift+P or Win+Alt+P). The same caveat applies to Ctrl+Alt+T/K/H
> of the other widgets.

## What you see

| Part | What it shows |
|---|---|
| GPU bar | VRAM used / total / free and GPU load of the NVIDIA card (`nvidia-smi`). |
| **Known services** | A card per service with a green **RUNNING** or grey **STOPPED** badge, PIDs, clickable ports (opens `http://localhost:port`), RAM and VRAM. Ollama also shows which models are loaded (from `http://localhost:11434/api/ps`). Click a card to show only its processes in the list; click it again to clear. |
| **Background apps** | Every process that listens on a TCP port, is a Python/Node/Java/… runtime or a server/daemon, or has no window but uses ≥ 2 % CPU or ≥ 300 MB RAM. Windows system processes (svchost, System, dwm, …) and normal windowed apps are hidden unless **Show all processes** is ticked. |

Columns: Name, Label (friendly name, e.g. *ComfyUI*, *Python – main.py*), PID, Command line (short;
hover for the full one), Ports (click to open in the browser), CPU % (of the whole PC, like Task
Manager), RAM, VRAM, Uptime. Click a column header to sort. Type in the filter box (Ctrl+F) to
search name, label, PID, port or command line. The list refreshes every 3 s (⏸ Pause / ⟳ Refresh / F5);
while the window is hidden nothing is measured.

Known services: Ollama (11434), ComfyUI (python `main.py` in a ComfyUI folder – the real listening
port is shown, 8188 by default), LM Studio (1234), llama.cpp server / koboldcpp, SD WebUI / Forge /
Fooocus (7860), Text Generation WebUI, Open WebUI, Jupyter (8888), opencode, OpenClaw, Docker, WSL,
Locally Uncensored, plus *Python (all)* and *Node.js (all)*.

**"via …" on a card** means another app started that service, not you. Example: opening Locally
Uncensored also starts Ollama (`ollama serve`), ComfyUI (`main.py … --enable-cors-header
http://tauri.localhost`), its llama.cpp servers and some Node.js helpers – those cards then say
*via Locally Uncensored*. Hover a card or a row's name for the parent app and its PID. A runtime
(node, python) only counts as opencode / OpenClaw / Open WebUI when the *program it runs* is that app,
not when the name merely appears in a folder path.

**Stopping is safe for the app itself:** Process Radar never stops itself or the process that started
it (a `py.exe` launcher or terminal – stopping those could take Process Radar down too), checks that a
PID still belongs to the same process before killing it (Windows reuses PIDs quickly), and stops
processes in the background so the window keeps responding.

### Row actions (buttons under the list or right-click)

* **Select several rows** with **Ctrl+click**, **Shift+click** or **Ctrl+A**. The selection stays put
  through the 3-second auto-refresh; processes that exit simply drop out of it. The status area shows
  *N selected*.
* **Stop process… / Stop N processes…** (or the **Delete** key, or right-click → *Stop selected*) opens
  one confirmation listing every selected process (name, label, PID, RAM). *Also stop child processes
  (kill tree)* is ticked when any of them has children. Windows-system and Shadow PC processes are
  flagged in red; Process Radar itself is left out automatically. Each process is asked to close
  first and is killed if it is still running after 3 seconds. Afterwards a summary lists every PID:
  *stopped*, *already gone*, *access denied* (runs as administrator / SYSTEM) or *still running*.
* **Ctrl+click a service card** (Ollama, ComfyUI, …) to stop that whole service (all its PIDs) through
  the same confirmation. A plain click only filters the list. *Python (all)* / *Node.js (all)* cannot be
  stopped this way.
* **Open folder** (one row) – the script's folder for Python/Node processes (e.g. the ComfyUI folder),
  otherwise the program's folder.
* **Copy command line(s)**, **Copy PID(s)**, **Open in browser** (first port, one row).

### Microsoft Defender exclusions

* **Add to Defender exclusions…** (toolbar button, right-click on rows, or right-click a service card) opens a
  dialog listing each unique executable of the selection (3× `python.exe` = 1 entry). Tick *Exclude* and
  optionally *Also exclude its folder*. Default is a **process exclusion** with the full path
  (`Add-MpPreference -ExclusionProcess`), which is more precise than a bare name.
* Generic interpreters (python, node, powershell, cmd, …) get a yellow warning and the folder option
  ticked by default – a process exclusion for them covers every script they run.
* Applying launches **one** elevated PowerShell (UAC). Cancel the UAC prompt cleanly; Tamper Protection /
  group policy / third-party antivirus are reported in the result summary.
* **🛡 Manage exclusions…** (Settings, or the tray menu) loads the current list (also needs admin) and lets
  you remove selected ones. Rows whose exe is in the last-known list show a small blue shield.

### VRAM per process

On Windows the NVIDIA driver runs in WDDM mode, where `nvidia-smi --query-compute-apps` reports
`[N/A]` for each process. Process Radar then reads the Windows performance counter
*GPU Process Memory → Dedicated Usage* (the same number Task Manager shows as "Dedicated GPU memory").
The status bar says which source is used.

## Command line

```
python process_radar.py --report        text snapshot of services + background apps, no window
python process_radar.py --report --all  include every process
python process_radar.py --screenshot C:\temp\radar.png   save a picture of the window after the first refresh
```

## Files

| File | Purpose |
|---|---|
| `process_radar.py` | The app (PySide6 window, tray, hotkey, single instance). |
| `radar_core.py` | Pure logic: service detection, filters, labels, formatting, nvidia-smi/PDH/Ollama parsing. |
| `radar_collect.py` | Live data: psutil, listening ports, visible windows, nvidia-smi, GPU counters, stop/kill tree. |
| `win_hotkeys.py` | Global hotkeys via Win32 `RegisterHotKey` (same design as the other widgets). |
| `run.bat` | Launcher without console; validates PySide6 + psutil; logs to `%APPDATA%\ProcessRadar\launcher.log`. |
| `shortcut.ps1` | Creates a shortcut: `-Folder Programs|Startup|Desktop` or `-Dir <folder>`; repairs its own shortcut after the folder moved; never overwrites a foreign shortcut. |
| `uninstall.bat` | Removes the shortcuts on this PC. |
| `icon.ico` | Green radar icon. |
| `tests\` | `python -m pytest -q -p no:cacheprovider tests` |
| `tools\bulk_demo.py` | Safe demo: starts its own dummy processes, selects + bulk-stops only those, saves a screenshot. |
| `tools\defender_demo.py` | Renders the Defender exclusions dialog (never elevates / never changes Defender). |

Settings (hotkey, refresh interval, *Show all*, sort column, window position) are in
`%APPDATA%\ProcessRadar\settings.json`, the log in `%APPDATA%\ProcessRadar\radar.log`. Any unexpected
error is written to `radar.log` (the app keeps running); if Qt itself ever crashes, the Python stack of
every thread is written to `crash.log` in the same folder.

## Other PC

`install-widgets.bat` in the opencode-state folder installs Process Radar together with the other widgets
(`pip install --user -r requirements.txt` into the real Python and a Start-menu shortcut that points at
that Python's `pythonw.exe`, never at a `.venv`).

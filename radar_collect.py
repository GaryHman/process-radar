"""Process Radar - live data collection (psutil, nvidia-smi, Windows GPU counters, Ollama API).

Collector.snapshot() returns a radar_core.Snapshot. Keep one Collector alive and call it from one
thread: it remembers psutil.Process objects so CPU % is measured between two refreshes.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.dont_write_bytecode = True

import psutil

import radar_core as rc

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ----------------------------------------------------------------------------- Windows helpers
def visible_window_pids() -> set[int]:
    """PIDs owning at least one visible, non-tool top-level window (Windows only)."""
    if os.name != "nt":
        return set()
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pids: set[int] = set()
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowLongW.restype = ctypes.c_long
    GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x80

    def cb(hwnd, _lp):
        try:
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                if not (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    pids.add(pid.value)
        except Exception:  # noqa: BLE001
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return pids


def pdh_gpu_dedicated() -> dict[int, int] | None:
    """{pid: dedicated VRAM bytes} from the Windows 'GPU Process Memory' counters (no admin needed).
    None when the counters are not available."""
    if os.name != "nt":
        return None
    from ctypes import wintypes
    pdh = ctypes.WinDLL("pdh")
    PDH_FMT_LARGE, PDH_MORE_DATA = 0x00000400, 0x800007D2

    class _Val(ctypes.Union):
        _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double),
                    ("largeValue", ctypes.c_longlong), ("AnsiStringValue", ctypes.c_char_p),
                    ("WideStringValue", ctypes.c_wchar_p)]

    class FMT_VALUE(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("u", _Val)]

    class ITEM(ctypes.Structure):
        _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", FMT_VALUE)]

    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return None
    try:
        if pdh.PdhAddEnglishCounterW(query, r"\GPU Process Memory(*)\Dedicated Usage", None,
                                     ctypes.byref(counter)) != 0:
            return None
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        r = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None)
        if (r & 0xFFFFFFFF) != PDH_MORE_DATA or size.value == 0:
            return {}
        buf = (ctypes.c_byte * size.value)()
        r = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf)
        if r != 0:
            return None
        arr = ctypes.cast(buf, ctypes.POINTER(ITEM))
        items = [(arr[i].szName, arr[i].FmtValue.u.largeValue) for i in range(count.value)
                 if arr[i].FmtValue.CStatus in (0, 1)]
        return rc.parse_pdh_items(items)
    finally:
        pdh.PdhCloseQuery(query)


def find_nvidia_smi() -> str | None:
    p = shutil.which("nvidia-smi")
    if p:
        return p
    for c in (r"C:\Windows\System32\nvidia-smi.exe",
              r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.exists(c):
            return c
    return None


def run_quiet(argv: list[str], timeout: float = 4.0) -> str:
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
    return r.stdout if r.returncode == 0 else ""


# ----------------------------------------------------------------------------- collector
class Collector:
    def __init__(self, gpu: bool = True, ollama: bool = True):
        self.gpu = gpu
        self.ollama = ollama
        self.ncpu = psutil.cpu_count() or 1
        self._live: dict[int, psutil.Process] = {}
        self._static: dict[tuple[int, float], tuple] = {}
        self._smi = find_nvidia_smi() if gpu else None

    def _static_info(self, proc: psutil.Process, key) -> tuple:
        if key in self._static:
            return self._static[key]
        exe = cmd = cwd = user = None
        try:
            with proc.oneshot():
                for attr in ("exe", "cmdline", "cwd", "username"):
                    try:
                        val = getattr(proc, attr)()
                    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                        val = None
                    if attr == "exe":
                        exe = val
                    elif attr == "cmdline":
                        cmd = val
                    elif attr == "cwd":
                        cwd = val
                    else:
                        user = val
        except psutil.NoSuchProcess:
            raise
        info = (exe or "", list(cmd or []), cwd or "", user or "")
        self._static[key] = info
        return info

    def snapshot(self) -> rc.Snapshot:
        t0 = time.time()
        errors: list[str] = []
        listen: dict[int, list] = {}
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN and c.pid and c.laddr:
                    listen.setdefault(c.pid, []).append((c.laddr.ip, c.laddr.port))
        except (psutil.AccessDenied, OSError) as e:
            errors.append(f"could not read listening ports: {e}")
        try:
            win = visible_window_pids()
        except Exception as e:  # noqa: BLE001
            win = set()
            errors.append(f"window list failed: {e}")

        procs: dict[int, rc.ProcInfo] = {}
        seen_live: dict[int, psutil.Process] = {}
        keys = set()
        for proc in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
            try:
                pid = proc.info["pid"]
                ctime = proc.info.get("create_time") or 0.0
                old = self._live.get(pid)
                if old is not None and getattr(old, "_radar_ctime", None) == ctime:
                    proc = old
                else:
                    proc._radar_ctime = ctime
                    proc.cpu_percent(None)  # prime
                seen_live[pid] = proc
                key = (pid, ctime)
                keys.add(key)
                exe, cmd, cwd, user = self._static_info(proc, key)
                try:
                    cpu = proc.cpu_percent(None) / self.ncpu
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    cpu = 0.0
                try:
                    rss = proc.memory_info().rss
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    rss = 0
                procs[pid] = rc.ProcInfo(pid=pid, name=proc.info.get("name") or "?",
                                         ppid=proc.info.get("ppid") or 0, exe=exe, cmdline=cmd, cwd=cwd,
                                         username=user, create_time=ctime, cpu=cpu, rss=rss,
                                         listen=sorted(set(listen.get(pid, []))), has_window=pid in win)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                continue
        self._live = seen_live
        for k in list(self._static):
            if k not in keys:
                del self._static[k]

        gpus, vsrc = [], ""
        if self.gpu:
            vram: dict[int, int] = {}
            if self._smi:
                try:
                    gpus = rc.parse_smi_gpus(run_quiet([self._smi, "--query-gpu=name,memory.used,memory.total,"
                                                        "memory.free,utilization.gpu",
                                                        "--format=csv,noheader,nounits"]))
                    vram = rc.parse_smi_apps(run_quiet([self._smi, "--query-compute-apps=pid,process_name,"
                                                        "used_memory", "--format=csv,noheader,nounits"]))
                    if vram:
                        vsrc = "nvidia-smi"
                except (OSError, subprocess.SubprocessError) as e:
                    errors.append(f"nvidia-smi failed: {e}")
            if not vram:
                try:
                    pd = pdh_gpu_dedicated()
                except Exception as e:  # noqa: BLE001
                    pd = None
                    errors.append(f"Windows GPU counters failed: {e}")
                if pd is not None:
                    vram, vsrc = pd, "Windows GPU counters (dedicated usage)"
            if vsrc:
                for pid, p in procs.items():
                    p.vram = vram.get(pid, 0)

        models = None
        if self.ollama:
            ol = [p for p in procs.values() if rc.SERVICE_BY_KEY["ollama"].match(p) and p.ports]
            if ol:
                port = 11434 if 11434 in ol[0].ports else ol[0].ports[0]
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ps", timeout=0.6) as r:
                        models = rc.parse_ollama_ps(r.read().decode("utf-8", "replace"))
                except Exception as e:  # noqa: BLE001
                    errors.append(f"Ollama API did not answer: {e}")

        return rc.Snapshot(procs=procs, gpus=gpus, vram_source=vsrc, ollama_models=models,
                           taken_at=time.time(), duration_ms=int((time.time() - t0) * 1000), errors=errors)


# ----------------------------------------------------------------------------- stopping
def stop_process(pid: int, tree: bool, timeout: float = 4.0) -> tuple[list[int], list[str]]:
    """Terminate pid (and its children when tree=True); kill what is still alive after timeout.
    Returns (stopped_pids, error_messages)."""
    if pid == os.getpid():
        return [], ["Process Radar will not stop itself - use Quit in its tray menu."]
    if pid in self_and_ancestors():
        return [], ["Process Radar was started from this process; stopping it could close Process Radar too."]
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return [], [f"PID {pid} is no longer running."]
    targets = []
    if tree:
        try:
            targets = [c for c in root.children(recursive=True) if c.pid != os.getpid()]
        except psutil.Error:
            targets = []
    targets.append(root)
    errors, asked = [], []
    for p in targets:
        try:
            p.terminate()
            asked.append(p)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            errors.append(f"Access denied for {p.pid} – it runs as another user or as administrator.")
    gone, alive = psutil.wait_procs(asked, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error as e:
            errors.append(f"Could not kill {p.pid}: {e}")
    gone2, alive2 = psutil.wait_procs(alive, timeout=2)
    for p in alive2:
        errors.append(f"PID {p.pid} is still running.")
    return sorted(p.pid for p in list(gone) + list(gone2)), errors


def self_and_ancestors() -> set[int]:
    """Process Radar's own PID plus the processes that started it (py.exe launchers, terminals, ...).
    Stopping one of those can take Process Radar down with it (py.exe kills its child through a job object)."""
    out = {os.getpid()}
    try:
        cur = psutil.Process()
        for _ in range(16):
            par = cur.parent()
            if par is None or par.pid in out or par.pid in (0, 4):
                break
            try:
                if par.create_time() > cur.create_time() + 1:   # recycled parent PID
                    break
            except psutil.Error:
                break
            out.add(par.pid)
            cur = par
    except psutil.Error:
        pass
    return out


def stop_many(pids, tree: bool, timeout: float = 3.0, protect=None, expected=None) -> dict[int, str]:
    """Bulk stop. Terminates every target (children first when tree=True), waits up to `timeout`
    seconds, then kills whatever is still alive. Returns {pid: state} with state one of
    stopped / gone / denied / alive / refused (see radar_core.stop_summary).

    protect:  PIDs never touched (Process Radar itself and its parents are always protected).
    expected: {pid: create_time} from the snapshot the user looked at. If a PID now belongs to a
              *different* process (Windows recycles PIDs quickly) it is reported as gone, not killed.
    Never raises: every psutil error is turned into a state."""
    protect = set(protect or ()) | self_and_ancestors()
    expected = expected or {}
    results: dict[int, str] = {}
    targets: dict[int, psutil.Process] = {}
    for pid in pids:
        if pid in protect:
            results[pid] = "refused"
            continue
        try:
            root = psutil.Process(pid)
            want = expected.get(pid)
            if want and abs(root.create_time() - want) > 1.0:
                results[pid] = "gone"           # the process the user saw has exited; PID reused
                continue
        except psutil.NoSuchProcess:
            results[pid] = "gone"
            continue
        except psutil.AccessDenied:
            results[pid] = "denied"
            continue
        except psutil.Error:
            results[pid] = "gone"
            continue
        if tree:
            try:
                for c in root.children(recursive=True):
                    if c.pid not in protect and c.pid not in results:
                        targets.setdefault(c.pid, c)
            except psutil.Error:
                pass
        targets[pid] = root
    asked = []
    for pid, p in targets.items():
        try:
            p.terminate()
            asked.append(p)
        except psutil.NoSuchProcess:
            results[pid] = "gone"
        except psutil.AccessDenied:
            results[pid] = "denied"
        except psutil.Error:
            results[pid] = "gone"
    try:
        gone, alive = psutil.wait_procs(asked, timeout=timeout)
    except psutil.Error:
        gone, alive = [], list(asked)
    for p in gone:
        results[p.pid] = "stopped"
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            results[p.pid] = "denied"
        except psutil.Error:
            pass
    try:
        gone2, alive2 = psutil.wait_procs([p for p in alive if p.pid not in results], timeout=2)
    except psutil.Error:
        gone2, alive2 = [], [p for p in alive if p.pid not in results]
    for p in gone2:
        results[p.pid] = "stopped"
    for p in alive2:
        results[p.pid] = "alive" if p.is_running() else "stopped"
    return results


# ----------------------------------------------------------------------------- elevated PowerShell
ERROR_CANCELLED = 1223


def launch_elevated_powershell(script_path: str, hwnd: int = 0):
    """Start ONE elevated Windows PowerShell running script_path (UAC prompt).
    Returns (status, process_handle): status 'started', 'cancelled' (UAC declined) or 'error: ...'."""
    if os.name != "nt":
        return "error: only available on Windows", None
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SW_HIDE = 0x40, 0x100, 0
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    ps = os.path.join(windir, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.hwnd = hwnd or None
    info.lpVerb = "runas"
    info.lpFile = ps if os.path.exists(ps) else "powershell.exe"
    info.lpParameters = f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script_path}"'
    info.nShow = SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        return ("cancelled" if err == ERROR_CANCELLED else f"error: Windows error {err}"), None
    return "started", info.hProcess


def wait_process_handle(handle, timeout_ms: int) -> int | None:
    """Wait for a process handle from launch_elevated_powershell; returns its exit code, or None on timeout."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    r = k32.WaitForSingleObject(handle, timeout_ms)
    if r != 0:
        return None
    code = wintypes.DWORD()
    k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return code.value

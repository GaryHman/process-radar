"""Process Radar - pure logic (no Qt, no psutil): classification, filtering, formatting, parsing.

Everything here works on plain ProcInfo objects so it can be unit-tested anywhere.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable

sys.dont_write_bytecode = True

APP_TITLE = "Process Radar"
DEFAULT_HOTKEYS = {"toggle": "Ctrl+Alt+P", "show": "", "hide": ""}
DEFAULT_SETTINGS = {"hotkeys": dict(DEFAULT_HOTKEYS), "refresh_ms": 3000, "show_all": False,
                    "geometry": "", "tray_hint_shown": False}
NOTABLE_CPU = 2.0                      # % of the whole machine
NOTABLE_RSS = 300 * 1024 * 1024        # bytes


# ----------------------------------------------------------------------------- data
@dataclass
class ProcInfo:
    pid: int
    name: str
    ppid: int = 0
    exe: str = ""
    cmdline: list = field(default_factory=list)
    cwd: str = ""
    username: str = ""
    create_time: float = 0.0
    cpu: float = 0.0                   # percent of the whole machine (0-100)
    rss: int = 0                       # bytes
    listen: list = field(default_factory=list)   # [(addr, port)]
    vram: int | None = None            # bytes of dedicated GPU memory, None = unknown
    has_window: bool = False

    @property
    def ports(self) -> list[int]:
        return sorted({p for _a, p in self.listen})

    @property
    def base(self) -> str:
        return exe_base(self.name)

    @property
    def cmd_text(self) -> str:
        return join_cmdline(self.cmdline) if self.cmdline else (self.exe or "")


@dataclass
class GpuInfo:
    name: str
    used: int          # bytes
    total: int
    free: int
    util: int | None = None


@dataclass
class Snapshot:
    procs: dict                        # pid -> ProcInfo
    gpus: list = field(default_factory=list)
    vram_source: str = ""              # "nvidia-smi", "Windows GPU counters" or ""
    ollama_models: list | None = None  # [{"name", "size", "size_vram"}] or None = not asked
    taken_at: float = 0.0
    duration_ms: int = 0
    errors: list = field(default_factory=list)


# ----------------------------------------------------------------------------- helpers
def exe_base(name: str) -> str:
    n = (name or "").strip().lower()
    return n[:-4] if n.endswith(".exe") else n


def join_cmdline(parts: Iterable[str]) -> str:
    out = []
    for a in parts:
        a = str(a)
        out.append(f'"{a}"' if (" " in a or not a) and not a.startswith('"') else a)
    return " ".join(out)


def shorten(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    keep = limit - 1
    head = keep * 2 // 3
    return text[:head] + "…" + text[-(keep - head):]


def short_cmdline(p: ProcInfo, limit: int = 90) -> str:
    """Command line with the program reduced to its file name (the full one is in the tooltip)."""
    if not p.cmdline:
        return shorten(p.exe, limit)
    first = os.path.basename(str(p.cmdline[0]).strip('"').replace("\\", "/")) or str(p.cmdline[0])
    rest = []
    for a in p.cmdline[1:]:
        a = str(a)
        if len(a) > 40 and ("\\" in a or "/" in a):
            a = "…" + re.split(r"[\\/]", a.rstrip("\\/"))[-1]
        rest.append(a)
    return shorten(join_cmdline([first] + rest), limit)


def format_bytes(n: int | None) -> str:
    if n is None:
        return "–"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            if unit in ("B", "KB"):
                return f"{n:.0f} {unit}"
            return f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_uptime(seconds: float) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def url_for(addr: str, port: int) -> str:
    a = (addr or "").strip()
    if a in ("", "0.0.0.0", "::", "127.0.0.1", "::1", "*"):
        host = "localhost"
    elif ":" in a:
        host = f"[{a}]"
    else:
        host = a
    return f"http://{host}:{port}"


def urls_for(p: ProcInfo) -> list[tuple[int, str]]:
    """One URL per port; a loopback/any binding wins over a specific address."""
    best: dict[int, str] = {}
    for addr, port in sorted(p.listen, key=lambda t: (t[1], t[0])):
        u = url_for(addr, port)
        if port not in best or "localhost" in u:
            best[port] = u
    return sorted(best.items())


def cmd_lower(p: ProcInfo) -> str:
    return " ".join(str(a) for a in p.cmdline).lower().replace("/", "\\")


def script_of(p: ProcInfo) -> str:
    """The script/module a python/node process runs ('main.py', '-m uvicorn', ...)."""
    args = [str(a) for a in p.cmdline[1:]]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-m" and i + 1 < len(args):
            return "-m " + args[i + 1]
        if a == "-c":
            return "-c …"
        if a.startswith("-"):
            i += 1
            continue
        return a
    return ""


def _pathmod(*paths: str):
    import ntpath
    import posixpath
    return ntpath if any(("\\" in x) or re.match(r"^[A-Za-z]:", x or "") for x in paths) else posixpath


def folder_for(p: ProcInfo) -> str:
    """Folder to open: where the script lives for python/node, otherwise the program folder."""
    if p.base in RUNTIMES:
        s = script_of(p)
        if s and not s.startswith("-"):
            pm = _pathmod(s, p.cwd)
            path = s if pm.isabs(s) or re.match(r"^[A-Za-z]:[\\/]", s) else (pm.join(p.cwd, s) if p.cwd else "")
            if path:
                d = pm.dirname(path)
                if d:
                    return d
        if p.cwd:
            return p.cwd
    if p.exe:
        return _pathmod(p.exe).dirname(p.exe)
    return p.cwd


# ----------------------------------------------------------------------------- classification
RUNTIMES = {"python", "pythonw", "python3", "py", "pyw", "node", "deno", "bun", "java", "javaw",
            "ruby", "php", "php-cgi", "perl", "uvicorn", "gunicorn", "dotnet", "go"}
RUNTIME_LABEL = {"python": "Python", "pythonw": "Python", "python3": "Python", "py": "Python",
                 "pyw": "Python", "node": "Node.js", "deno": "Deno", "bun": "Bun", "java": "Java",
                 "javaw": "Java", "ruby": "Ruby", "php": "PHP", "php-cgi": "PHP", "perl": "Perl",
                 "uvicorn": "Python", "gunicorn": "Python", "dotnet": ".NET", "go": "Go"}

NOISE = {n.lower() for n in """
system idle registry secure system smss csrss wininit winlogon services lsass lsaiso svchost
fontdrvhost dwm sihost taskhostw ctfmon runtimebroker conhost dllhost searchhost searchindexer
searchprotocolhost searchfilterhost startmenuexperiencehost shellexperiencehost textinputhost
securityhealthservice securityhealthsystray msmpeng nissrv mpdefendercoreservice smartscreen spoolsv
wmiprvse audiodg memory compression wudfhost unsecapp moUsocoreworker tiworker trustedinstaller
backgroundtaskhost applicationframehost systemsettings useroobebroker lockapp comppkgsrv
phoneexperiencehost widgets widgetservice crossdeviceresume shellhost sgrmbroker wlanext
dashost lsm gamebarpresencewriter searchapp cortana msedgewebview2 explorer userinit
openconsole windowsterminal vmcompute vmms dllhst3g sppsvc sppextcomobj msiexec wmiapsrv
igfxem igfxcuiservice nvcontainer nvdisplay.container nvsphelper64 rtkauduservice64 mousocoreworker
aggregatorhost memcompression wsearch smss.exe usocoreworker dwmhost wermgr werfault taskmgr
""".split()} | {"memory compression", "secure system", "system idle process"}


def is_noise(p: ProcInfo) -> bool:
    if p.pid in (0, 4):
        return True
    if p.base in NOISE or (p.name or "").lower() in NOISE:
        return True
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    exe = (p.exe or "").lower()
    if exe and exe.startswith(windir.lower().rstrip("\\") + "\\") and p.base not in RUNTIMES:
        return True
    return False


def _name_in(*names: str) -> Callable[[ProcInfo], bool]:
    s = {exe_base(n) for n in names}
    return lambda p: p.base in s


def _py_with(*needles: str, script: tuple = ()) -> Callable[[ProcInfo], bool]:
    def m(p: ProcInfo) -> bool:
        if RUNTIME_LABEL.get(p.base) != "Python":
            return False
        c = cmd_lower(p) + " " + (p.cwd or "").lower() + " " + (p.exe or "").lower()
        if script:
            sc = os.path.basename(script_of(p).replace("\\", "/")).lower()
            if sc not in script:
                return False
        return any(n in c for n in needles)
    return m


def _any(*fs: Callable[[ProcInfo], bool]) -> Callable[[ProcInfo], bool]:
    return lambda p: any(f(p) for f in fs)


def _cmd_has(*needles: str) -> Callable[[ProcInfo], bool]:
    return lambda p: any(n in (cmd_lower(p) + " " + (p.exe or "").lower()) for n in needles)


# A runtime process (node / bun / python) only belongs to a service when the *program it runs* is that
# service - not when the service name merely appears somewhere in its command line (a folder called
# "opencode-state" or "openclaw-workspace" must not turn every script in it into opencode / OpenClaw).
def _runs(p: ProcInfo, runtimes: tuple, names: tuple = (), pkgs: tuple = ()) -> bool:
    """True if runtime process p runs a script/module whose file name starts with one of `names`, or whose
    path contains one of the package folders in `pkgs` (e.g. '\\node_modules\\opencode')."""
    if RUNTIME_LABEL.get(p.base) not in runtimes:
        return False
    sc = script_of(p).lower().replace("/", "\\")
    if sc.startswith("-m "):
        mod = sc[3:].strip()
        return any(mod == n or mod.startswith(n + ".") for n in names)
    base = sc.rsplit("\\", 1)[-1]
    return any(base.startswith(n) for n in names) or any(k in sc for k in pkgs)


@dataclass
class ServiceDef:
    key: str
    label: str
    hint: str
    default_port: int | None
    match: Callable[[ProcInfo], bool]
    group: bool = False


SERVICES: list[ServiceDef] = [
    ServiceDef("ollama", "Ollama", "Local LLM server", 11434,
               _name_in("ollama", "ollama app", "ollama_llama_server")),
    ServiceDef("comfyui", "ComfyUI", "Stable Diffusion node UI", 8188,
               _any(_py_with("comfy", script=("main.py",)), _name_in("comfyui"))),
    ServiceDef("lmstudio", "LM Studio", "Local LLM app / server", 1234,
               _any(_name_in("lm studio", "lms", "lmstudio"), _cmd_has("\\lm studio\\", "lm-studio"))),
    ServiceDef("llamacpp", "llama.cpp server", "llama-server / koboldcpp", 8080,
               lambda p: p.base.endswith("llama-server") or p.base in ("llama-server", "koboldcpp",
                                                                       "llamafile")),
    ServiceDef("sdwebui", "SD WebUI / Forge", "AUTOMATIC1111, Forge, Fooocus", 7860,
               _any(_py_with("stable-diffusion", "webui", "forge", "fooocus",
                             script=("launch.py", "webui.py", "entry_with_update.py")),
                    _name_in("stable-diffusion-webui"))),
    ServiceDef("textgen", "Text Generation WebUI", "oobabooga", 7860,
               _py_with("text-generation-webui", "text_generation_webui", script=("server.py", "one_click.py"))),
    ServiceDef("openwebui", "Open WebUI", "Chat UI for Ollama", 8080,
               lambda p: p.base in ("open-webui", "open_webui")
               or _runs(p, ("Python",), names=("open-webui", "open_webui"))),
    ServiceDef("jupyter", "Jupyter", "Notebook / Lab server", 8888,
               lambda p: bool(re.search(r"jupyter(-lab|-notebook|-server|lab|\s+lab|\s+notebook)|"
                                        r"ipykernel_launcher|jupyterlab", cmd_lower(p)))
               or p.base in ("jupyter", "jupyter-lab", "jupyter-notebook")),
    ServiceDef("opencode", "opencode", "AI coding agent server", None,
               lambda p: p.base.startswith("opencode") or _runs(p, ("Node.js", "Bun"), names=("opencode",),
                                                    pkgs=("\\node_modules\\opencode", "\\opencode-ai\\", "\\.opencode\\bin\\"))),
    ServiceDef("openclaw", "OpenClaw", "Agent gateway", 18789,
               lambda p: "openclaw" in p.base or _runs(p, ("Node.js", "Bun", "Python"), names=("openclaw",),
                                                    pkgs=("\\node_modules\\openclaw",))),
    ServiceDef("docker", "Docker", "Docker Desktop / engine", None,
               _name_in("docker desktop", "com.docker.backend", "com.docker.build", "dockerd",
                        "com.docker.service", "docker")),
    ServiceDef("wsl", "WSL", "Windows Subsystem for Linux", None,
               _name_in("vmmemwsl", "wslservice", "wslhost", "wslrelay")),
    ServiceDef("locallyuncensored", "Locally Uncensored", "Local AI app (+ its llama server)", None,
               lambda p: p.base.startswith("locally-uncensored") or p.base.startswith("lu-")),
    ServiceDef("python", "Python (all)", "Every python.exe / pythonw.exe", None,
               lambda p: RUNTIME_LABEL.get(p.base) == "Python", group=True),
    ServiceDef("node", "Node.js (all)", "Every node.exe", None,
               lambda p: p.base == "node", group=True),
]
SERVICE_BY_KEY = {s.key: s for s in SERVICES}

FRIENDLY = {
    "tailscaled": "Tailscale", "tailscale-ipn": "Tailscale", "googledrivefs": "Google Drive",
    "hasplms": "Sentinel licence manager", "onedrive": "OneDrive", "dropbox": "Dropbox",
    "steam": "Steam", "steamwebhelper": "Steam", "discord": "Discord", "spotify": "Spotify",
    "code": "VS Code", "cursor": "Cursor", "msedge": "Microsoft Edge", "chrome": "Google Chrome",
    "firefox": "Firefox", "grok bot": "Grok Bot", "postgres": "PostgreSQL", "mysqld": "MySQL",
    "redis-server": "Redis", "mongod": "MongoDB", "nginx": "nginx", "httpd": "Apache",
    "caddy": "Caddy", "ngrok": "ngrok", "cloudflared": "Cloudflare Tunnel", "sunshine": "Sunshine",
    "parsecd": "Parsec", "anydesk": "AnyDesk", "teamviewer": "TeamViewer", "nvidia share": "NVIDIA overlay",
    "nvcontainer": "NVIDIA container", "rustdesk": "RustDesk", "syncthing": "Syncthing",
}


def service_of(p: ProcInfo) -> ServiceDef | None:
    """The specific (non-group) known service this process belongs to."""
    for s in SERVICES:
        if not s.group and s.match(p):
            return s
    return None


def friendly_label(p: ProcInfo, self_pid: int | None = None) -> str:
    if self_pid is not None and p.pid == self_pid:
        return "Process Radar (this app)"
    s = service_of(p)
    if s:
        if s.key == "ollama" and "runner" in cmd_lower(p):
            return "Ollama (model runner)"
        return s.label
    rt = RUNTIME_LABEL.get(p.base)
    if rt:
        sc = script_of(p)
        if sc:
            sc = sc if sc.startswith("-") else os.path.basename(sc.replace("\\", "/"))
            return f"{rt} – {sc}"
        return rt
    if p.base in FRIENDLY:
        return FRIENDLY[p.base]
    if p.base.startswith("shadow"):
        return "Shadow PC service"
    if is_noise(p):
        return "Windows system"
    return ""


_SERVERISH = re.compile(r"(server|daemon|serve$|svc$|-svc|gateway|worker|agent$|llama|tunnel)")


def why_listed(p: ProcInfo, window_tree: bool = False) -> str | None:
    """Reason a process belongs in the default 'background apps' list, or None.

    window_tree: True if this process or one of its ancestors owns a visible window.
    """
    if is_noise(p):
        return None
    if service_of(p):
        return "known service"
    if p.listen:
        return "listening on a network port"
    if p.base in RUNTIMES:
        return f"{RUNTIME_LABEL.get(p.base, p.base)} runtime"
    args = [str(a).lower() for a in p.cmdline[1:3]]
    if _SERVERISH.search(p.base) or any(a in ("serve", "server", "daemon", "--service") for a in args):
        return "server / daemon"
    if not p.has_window and not window_tree and (p.cpu >= NOTABLE_CPU or p.rss >= NOTABLE_RSS):
        return "no window, but using CPU/RAM"
    return None


def window_tree_pids(procs: dict) -> set[int]:
    """PIDs that own a visible window or descend (max 6 levels) from one that does."""
    out = set()
    for pid, p in procs.items():
        cur, depth, seen = p, 0, set()
        while cur is not None and depth < 7 and cur.pid not in seen:
            seen.add(cur.pid)
            if cur.has_window:
                out.add(pid)
                break
            cur = procs.get(cur.ppid) if cur.ppid != cur.pid else None
            depth += 1
    return out


def background_rows(snap: Snapshot, show_all: bool = False) -> list[tuple[ProcInfo, str]]:
    wt = window_tree_pids(snap.procs)
    rows = []
    for p in snap.procs.values():
        why = why_listed(p, p.pid in wt)
        if why or show_all:
            rows.append((p, why or ("Windows system" if is_noise(p) else "has a window / idle")))
    rows.sort(key=lambda r: (-(r[0].cpu or 0), r[0].name.lower()))
    return rows


def descendants(pid: int, procs: dict) -> list[int]:
    kids: dict[int, list[int]] = {}
    for p in procs.values():
        if p.ppid != p.pid:
            kids.setdefault(p.ppid, []).append(p.pid)
    out, stack, seen = [], list(kids.get(pid, [])), {pid}
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
        stack.extend(kids.get(c, []))
    return out


# ----------------------------------------------------------------------------- services
@dataclass
class ServiceStatus:
    key: str
    label: str
    hint: str
    running: bool
    pids: list
    ports: list                        # [(port, url)]
    vram: int | None
    rss: int
    cpu: float
    uptime: float | None
    detail: str = ""
    group: bool = False
    launcher: str = ""                 # "Locally Uncensored" when that app started these processes
    launcher_pid: int | None = None

    def line(self) -> str:
        state = "RUNNING" if self.running else "stopped"
        ports = ",".join(str(p) for p, _u in self.ports) or "-"
        pids = ",".join(str(p) for p in self.pids[:6]) + ("…" if len(self.pids) > 6 else "")
        vram = format_bytes(self.vram) if self.vram is not None else "n/a"
        return (f"{self.label:<22} {state:<8} pids={pids or '-':<18} ports={ports:<12} "
                f"vram={vram:<9} ram={format_bytes(self.rss) if self.running else '-':<9} {self.detail}"
                + (f"  (via {self.launcher})" if self.launcher else "")).rstrip()


def service_status(snap: Snapshot, now: float) -> list[ServiceStatus]:
    out = []
    owner = {pid: service_of(p) for pid, p in snap.procs.items()}     # classify every process once
    for s in SERVICES:
        procs = [p for p in snap.procs.values() if (s.match(p) if s.group else owner[p.pid] is s)]
        ports: dict[int, str] = {}
        for p in procs:
            for port, url in urls_for(p):
                ports.setdefault(port, url)
        vr = [p.vram for p in procs if p.vram is not None]
        st = ServiceStatus(
            key=s.key, label=s.label, hint=s.hint, running=bool(procs), group=s.group,
            pids=sorted(p.pid for p in procs), ports=sorted(ports.items()),
            vram=sum(vr) if vr else (0 if procs and snap.vram_source else None),
            rss=sum(p.rss for p in procs), cpu=sum(p.cpu for p in procs),
            uptime=(now - min(p.create_time for p in procs)) if procs and any(p.create_time for p in procs) else None)
        if s.group:
            st.detail = f"{len(procs)} running" if procs else "none running"
        elif s.key == "ollama" and procs:
            st.detail = ollama_detail(snap.ollama_models)
        elif s.key == "comfyui" and procs and not ports:
            guess = cmdline_port(procs[0]) or s.default_port
            st.detail = f"not listening yet (expects port {guess})"
        if procs and not s.group:
            st.launcher, st.launcher_pid = service_launcher(procs, snap.procs, s.key)
        out.append(st)
    return out


# Processes that only pass a launch along (shells, consoles, launchers) - look through them.
_PASS_THROUGH = {"cmd", "conhost", "powershell", "pwsh", "openconsole", "windowsterminal", "wt", "py", "pyw",
                 "bash", "sh", "wsl", "rundll32", "uv", "uvx", "npx"}
# Reaching one of these means "started by you / by Windows", not by another app.
_USER_ROOTS = {"explorer", "services", "svchost", "wininit", "winlogon", "userinit", "taskeng", "taskhostw",
               "sihost", "runtimebroker", "startmenuexperiencehost"}


def launcher_of(p: ProcInfo, procs: dict, own_key: str | None = None, max_depth: int = 8):
    """The app that started p, as (label, pid), or ("", None) when it was started from Explorer/Windows.

    Walks the parent chain; a parent only counts if it started before the child (protects against a
    recycled parent PID). Shells/consoles/launchers are looked through; plain runtimes (node, python)
    without a label are skipped too, so 'cmd -> node -> cmd -> locally-uncensored' gives Locally Uncensored."""
    cur, seen = p, {p.pid}
    for _ in range(max_depth):
        par = procs.get(cur.ppid)
        if par is None or par.pid in seen or par.pid in (0, 4):
            return "", None
        if par.create_time and cur.create_time and par.create_time > cur.create_time + 1:
            return "", None                      # PID was reused: the real parent is gone
        seen.add(par.pid)
        if par.base in _USER_ROOTS:
            return "", None
        svc = service_of(par)
        if svc is not None:
            if svc.key != own_key:
                return svc.label, par.pid
        elif par.base in FRIENDLY:
            return FRIENDLY[par.base], par.pid
        elif par.base not in _PASS_THROUGH and par.base not in RUNTIMES and not is_noise(par):
            return (par.name[:-4] if par.name.lower().endswith(".exe") else par.name), par.pid
        cur = par
    return "", None


def service_launcher(service_procs: list, procs: dict, own_key: str | None = None):
    """Launcher shared by a service's root processes (those whose parent is not part of the service)."""
    own = {p.pid for p in service_procs}
    for p in sorted(service_procs, key=lambda q: q.create_time or 0):
        if p.ppid in own:
            continue
        label, pid = launcher_of(p, procs, own_key)
        if label:
            return label, pid
    return "", None


def cmdline_port(p: ProcInfo) -> int | None:
    args = [str(a) for a in p.cmdline]
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args) and args[i + 1].isdigit():
            return int(args[i + 1])
        m = re.match(r"--port=(\d+)$", a)
        if m:
            return int(m.group(1))
    return None


def ollama_detail(models) -> str:
    if models is None:
        return ""
    if not models:
        return "no model loaded"
    parts = []
    for m in models[:3]:
        v = m.get("size_vram")
        parts.append(f"{m.get('name', '?')}" + (f" ({format_bytes(v)} VRAM)" if v else ""))
    return "loaded: " + ", ".join(parts) + (" …" if len(models) > 3 else "")


def parse_ollama_ps(text: str) -> list[dict]:
    data = json.loads(text or "{}")
    return [{"name": m.get("name") or m.get("model", "?"), "size": int(m.get("size") or 0),
             "size_vram": int(m.get("size_vram") or 0)} for m in data.get("models") or []]


# ----------------------------------------------------------------------------- GPU parsing
def _num(s: str) -> int | None:
    s = (s or "").strip()
    return int(float(s)) if re.fullmatch(r"\d+(\.\d+)?", s) else None


def parse_smi_gpus(text: str) -> list[GpuInfo]:
    """nvidia-smi --query-gpu=name,memory.used,memory.total,memory.free,utilization.gpu
    --format=csv,noheader,nounits"""
    out = []
    for line in (text or "").splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 4 or _num(parts[2]) is None:
            continue
        mib = 1024 * 1024
        out.append(GpuInfo(name=parts[0], used=(_num(parts[1]) or 0) * mib, total=_num(parts[2]) * mib,
                           free=(_num(parts[3]) or 0) * mib, util=_num(parts[4]) if len(parts) > 4 else None))
    return out


def parse_smi_apps(text: str) -> dict[int, int]:
    """nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits.
    On Windows (WDDM) used_memory is '[N/A]' - those rows are skipped."""
    out: dict[int, int] = {}
    for line in (text or "").splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        mem = _num(parts[-1])
        if mem is None:
            continue
        pid = int(parts[0])
        out[pid] = out.get(pid, 0) + mem * 1024 * 1024
    return out


_PDH_INST = re.compile(r"^pid_(\d+)_luid_", re.I)


def parse_pdh_items(items: Iterable[tuple[str, int]]) -> dict[int, int]:
    """Windows '\\GPU Process Memory(*)\\Dedicated Usage' instances -> {pid: bytes} (summed)."""
    out: dict[int, int] = {}
    for name, value in items:
        m = _PDH_INST.match(name or "")
        if m and value is not None:
            pid = int(m.group(1))
            out[pid] = out.get(pid, 0) + int(value)
    return out


# ----------------------------------------------------------------------------- report
def report_text(snap: Snapshot, now: float, show_all: bool = False) -> str:
    lines = [f"{APP_TITLE} - snapshot of {len(snap.procs)} processes in {snap.duration_ms} ms",
             f"VRAM per process from: {snap.vram_source or 'unavailable'}"]
    for g in snap.gpus:
        lines.append(f"GPU: {g.name}  used {format_bytes(g.used)} / {format_bytes(g.total)}  "
                     f"free {format_bytes(g.free)}  util {g.util if g.util is not None else '?'}%")
    if not snap.gpus:
        lines.append("GPU: no NVIDIA GPU information (nvidia-smi not found or failed)")
    lines.append("")
    lines.append("== Known services ==")
    for st in service_status(snap, now):
        lines.append(st.line())
    lines.append("")
    rows = background_rows(snap, show_all)
    lines.append(f"== Background apps ({len(rows)}) ==")
    lines.append(f"{'name':<24} {'label':<28} {'pid':>6} {'ports':<14} {'cpu%':>5} {'ram':>9} "
                 f"{'vram':>9} {'uptime':>8}  command")
    for p, _why in rows:
        lines.append(f"{shorten(p.name, 24):<24} {shorten(friendly_label(p), 28):<28} {p.pid:>6} "
                     f"{shorten(','.join(map(str, p.ports)) or '-', 14):<14} {p.cpu:>5.1f} "
                     f"{format_bytes(p.rss):>9} {format_bytes(p.vram) if p.vram is not None else '-':>9} "
                     f"{format_uptime(now - p.create_time) if p.create_time else '-':>8}  "
                     f"{short_cmdline(p, 70)}")
    if snap.errors:
        lines.append("")
        lines.extend("note: " + e for e in snap.errors)
    return "\n".join(lines)


def split_cmdline(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=False)
    except ValueError:
        return text.split()


# ----------------------------------------------------------------------------- multi-select + bulk stop
def preserve_selection(selected: Iterable[int], current: Iterable[int]) -> list[int]:
    """Keep the previously selected PIDs (in their order) that still exist after a refresh."""
    cur = set(current)
    out, seen = [], set()
    for pid in selected:
        if pid in cur and pid not in seen:
            out.append(pid)
            seen.add(pid)
    return out


def is_shadow(p: ProcInfo) -> bool:
    exe = (p.exe or "").lower()
    # note: Meneer's Windows user is called "Shadow", so C:\Users\Shadow\... must NOT count
    return p.base.startswith("shadow") or "\\program files\\shadow\\" in exe or "\\blade group\\" in exe


@dataclass
class StopItem:
    pid: int
    name: str
    label: str
    rss: int
    children: list
    warning: str = ""


@dataclass
class StopPlan:
    items: list                        # [StopItem] that will be stopped
    excluded_self: bool = False        # Process Radar itself was in the selection and was removed
    excluded_parents: list = field(default_factory=list)  # processes that started Process Radar (left out)
    missing: list = field(default_factory=list)   # PIDs no longer in the snapshot
    warnings: list = field(default_factory=list)  # inline warning lines for the dialog

    @property
    def any_children(self) -> bool:
        return any(i.children for i in self.items)

    @property
    def pids(self) -> list[int]:
        return [i.pid for i in self.items]

    @property
    def total_rss(self) -> int:
        return sum(i.rss for i in self.items)


def ancestors(pid: int, procs: dict, max_depth: int = 16) -> list[int]:
    """Parent chain of pid (nearest first); stops at a recycled parent PID (parent younger than child)."""
    out, cur = [], procs.get(pid)
    while cur is not None and len(out) < max_depth:
        par = procs.get(cur.ppid)
        if par is None or par.pid in out or par.pid == cur.pid or par.pid in (0, 4):
            break
        if par.create_time and cur.create_time and par.create_time > cur.create_time + 1:
            break
        out.append(par.pid)
        cur = par
    return out


def stop_plan(pids: Iterable[int], procs: dict, self_pid: int | None = None) -> StopPlan:
    """What a (bulk) stop of these PIDs would do: removes Process Radar itself and the processes that
    started it (stopping a py.exe launcher or a terminal can take Process Radar down with it), collects
    children (not already selected) and flags Windows-system / Shadow processes."""
    plan = StopPlan(items=[])
    wanted = []
    parents = set(ancestors(self_pid, procs)) if self_pid is not None else set()
    for pid in pids:
        if pid in wanted:
            continue
        if self_pid is not None and pid == self_pid:
            plan.excluded_self = True
            continue
        if pid in parents and pid in procs:
            plan.excluded_parents.append(pid)
            continue
        if pid not in procs:
            plan.missing.append(pid)
            continue
        wanted.append(pid)
    sel = set(wanted)
    for pid in wanted:
        p = procs[pid]
        kids = [k for k in descendants(pid, procs) if k not in sel and k != self_pid and k not in parents]
        warn = ""
        if is_noise(p):
            warn = "Windows system process"
        elif is_shadow(p):
            warn = "Shadow PC service"
        plan.items.append(StopItem(pid=pid, name=p.name, label=friendly_label(p, self_pid), rss=p.rss,
                                   children=kids, warning=warn))
    if plan.excluded_self:
        plan.warnings.append("Process Radar itself is in the selection – it was left out (use Quit in the tray menu).")
    if plan.excluded_parents:
        names = ", ".join(f"{procs[p].name} ({p})" for p in plan.excluded_parents)
        plan.warnings.append(f"Left out {names}: Process Radar was started from it, so stopping it could close "
                             "Process Radar too. Quit Process Radar first, then stop it with Task Manager.")
    sysn = [i for i in plan.items if i.warning == "Windows system process"]
    shad = [i for i in plan.items if i.warning == "Shadow PC service"]
    if sysn:
        plan.warnings.append(f"{len(sysn)} Windows system process(es) selected – stopping them can make Windows "
                             "unstable.")
    if shad:
        plan.warnings.append(f"{len(shad)} Shadow PC service(s) selected – stopping them can drop your Shadow "
                             "connection.")
    if plan.missing:
        plan.warnings.append(f"{len(plan.missing)} selected process(es) already exited.")
    return plan


STOP_STATES = ("stopped", "gone", "denied", "alive", "refused")
STOP_TEXT = {"stopped": "stopped", "gone": "already gone", "denied": "access denied",
             "alive": "still running", "refused": "skipped"}


def stop_summary(results: dict, names: dict | None = None) -> tuple[str, dict]:
    """results {pid: state} -> (text, counts). States: stopped / gone / denied / alive / refused."""
    names = names or {}
    counts = {s: 0 for s in STOP_STATES}
    for st in results.values():
        counts[st] = counts.get(st, 0) + 1
    head = ", ".join(f"{counts[s]} {STOP_TEXT[s]}" for s in STOP_STATES if counts.get(s))
    lines = [head or "nothing to do"]
    for pid in sorted(results, key=lambda p: (STOP_STATES.index(results[p]) if results[p] in STOP_STATES else 9, p)):
        nm = names.get(pid, "")
        lines.append(f"  {pid:>6}  {nm:<28} {STOP_TEXT.get(results[pid], results[pid])}")
    return "\n".join(lines), counts


# ----------------------------------------------------------------------------- Microsoft Defender exclusions
INTERPRETERS = {"python", "pythonw", "python3", "py", "pyw", "node", "deno", "bun", "powershell",
                "pwsh", "cmd", "wscript", "cscript", "ruby", "perl", "php", "java", "javaw", "dotnet",
                "conhost", "wsl", "bash", "sh"}
EXCL_READ_DENIED = "Must be an administrator to view exclusions"


@dataclass
class DefenderTarget:
    exe: str                           # full path to the executable
    procs: list                        # [(pid, name)] using this exe
    is_interpreter: bool = False

    @property
    def name(self) -> str:
        return _basename(self.exe) if self.exe else "?"

    @property
    def folder(self) -> str:
        return _pathmod(self.exe).dirname(self.exe) if self.exe else ""

    @property
    def warning(self) -> str:
        if self.is_interpreter:
            return (f"{self.name} is a generic interpreter: a process exclusion here tells Defender to skip "
                    "every script it runs. Prefer 'Also exclude its folder' for a specific app folder.")
        return ""


def _basename(path: str) -> str:
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _is_interpreter(name: str) -> bool:
    b = exe_base(name)
    if b in INTERPRETERS:
        return True
    # versioned names: python3.13, python3.12-64, node18, …
    return any(b.startswith(p) and (len(b) == len(p) or b[len(p)].isdigit() or b[len(p)] in ".-_")
               for p in ("python", "pythonw", "python3", "node", "pwsh", "powershell"))


def defender_targets(procs: Iterable[ProcInfo]) -> list[DefenderTarget]:
    """Unique executables of the given processes (deduped by exe path, case-insensitive on Windows).
    Processes without a readable exe path are skipped."""
    by_exe: dict[str, DefenderTarget] = {}
    for p in procs:
        exe = (p.exe or "").strip()
        if not exe:
            continue
        key = exe.lower()
        t = by_exe.get(key)
        if t is None:
            t = DefenderTarget(exe=exe, procs=[], is_interpreter=_is_interpreter(_basename(exe)))
            by_exe[key] = t
        t.procs.append((p.pid, p.name))
    return sorted(by_exe.values(), key=lambda t: t.name.lower())


def exe_paths_of_service(snap: "Snapshot", key: str) -> list[DefenderTarget]:
    s = SERVICE_BY_KEY.get(key)
    if not s:
        return []
    procs = [p for p in snap.procs.values() if (s.match(p) if s.group else service_of(p) is s)]
    if not s.group:   # include helpers it started (e.g. Locally Uncensored -> its llama servers + ComfyUI)
        have = {p.pid for p in procs}
        for p in list(procs):
            for k in descendants(p.pid, snap.procs):
                c = snap.procs.get(k)
                if c is not None and k not in have and not is_noise(c):
                    have.add(k)
                    procs.append(c)
    return defender_targets(procs)


_PS_QUOTES = "'\u2018\u2019\u201a\u201b"   # PowerShell treats all of these as single quotes


def ps_single_quote(value: str) -> str:
    """Quote a string as a PowerShell single-quoted literal. Every quote character PowerShell accepts
    (' ‘ ’ ‚ ‛) is doubled, so paths with apostrophes, spaces, $ or backticks stay literal."""
    v = str(value)
    if any(c in v for c in "\r\n\x00"):
        raise ValueError("path contains a line break or NUL character")
    return "'" + "".join(c * 2 if c in _PS_QUOTES else c for c in v) + "'"


@dataclass
class ExclusionChange:
    process_paths: list                # full exe paths -> -ExclusionProcess
    folder_paths: list                 # folders -> -ExclusionPath

    @property
    def is_empty(self) -> bool:
        return not self.process_paths and not self.folder_paths


def selection_to_change(targets: Iterable[DefenderTarget], chosen_exes: set, folder_exes: set) -> ExclusionChange:
    """Build the change from the dialog's checkboxes. chosen_exes = exes to add as process exclusions;
    folder_exes = exes whose folder should also be added as a path exclusion."""
    proc, folders = [], []
    seen_f = set()
    for t in targets:
        if t.exe in chosen_exes:
            proc.append(t.exe)
        if t.exe in folder_exes and t.folder and t.folder.lower() not in seen_f:
            seen_f.add(t.folder.lower())
            folders.append(t.folder)
    return ExclusionChange(process_paths=proc, folder_paths=folders)


def _ps_header(result_path: str) -> list[str]:
    return [
        "$ErrorActionPreference = 'Stop'",
        "$result = [ordered]@{ ok = $false; action = ''; added = @(); removed = @(); errors = @();",
        "    exclusionProcess = @(); exclusionPath = @(); tamper = $null; defender = $null; elevated = $false }",
        f"$resultPath = {ps_single_quote(result_path)}",
        "function Save { param($r) ($r | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath $resultPath -Encoding UTF8 }",
        "try {",
        "    try {",
        "        $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()",
        "        $result.elevated = $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
        "    } catch { }",
        "    try {",
        "        $st = Get-MpComputerStatus",
        "        $result.tamper = [bool]$st.IsTamperProtected",
        "        $result.defender = [ordered]@{ service = [bool]$st.AMServiceEnabled; antivirus = [bool]$st.AntivirusEnabled;",
        "            realtime = [bool]$st.RealTimeProtectionEnabled; mode = [string]$st.AMRunningMode }",
        "    } catch { $result.errors += ('Microsoft Defender is not available: ' + $_.Exception.Message) }",
    ]


def _ps_footer() -> list[str]:
    return [
        "    try {",
        "        $p = Get-MpPreference",
        "        $result.exclusionProcess = @($p.ExclusionProcess)",
        "        $result.exclusionPath = @($p.ExclusionPath)",
        "    } catch { $result.errors += \"read-back failed: $($_.Exception.Message)\" }",
        "    if ($result.errors.Count -eq 0) { $result.ok = $true }",
        "} catch {",
        "    $result.errors += $_.Exception.Message",
        "}",
        "Save $result",
    ]


def build_apply_script(change: ExclusionChange, result_path: str) -> str:
    """PowerShell that adds the chosen exclusions, then writes a JSON result file."""
    lines = _ps_header(result_path)
    lines.append("    $result.action = 'add'")
    for exe in change.process_paths:
        q = ps_single_quote(exe)
        lines += [f"    try {{ Add-MpPreference -ExclusionProcess {q}; $result.added += @{{ type='process'; value={q} }} }}",
                  f"    catch {{ $result.errors += ('process ' + {q} + ' : ' + $_.Exception.Message) }}"]
    for folder in change.folder_paths:
        q = ps_single_quote(folder)
        lines += [f"    try {{ Add-MpPreference -ExclusionPath {q}; $result.added += @{{ type='path'; value={q} }} }}",
                  f"    catch {{ $result.errors += ('path ' + {q} + ' : ' + $_.Exception.Message) }}"]
    lines += _ps_footer()
    return "\r\n".join(lines) + "\r\n"


def build_remove_script(process_paths: Iterable[str], folder_paths: Iterable[str], result_path: str) -> str:
    lines = _ps_header(result_path)
    lines.append("    $result.action = 'remove'")
    for exe in process_paths:
        q = ps_single_quote(exe)
        lines += [f"    try {{ Remove-MpPreference -ExclusionProcess {q}; $result.removed += @{{ type='process'; value={q} }} }}",
                  f"    catch {{ $result.errors += ('process ' + {q} + ' : ' + $_.Exception.Message) }}"]
    for folder in folder_paths:
        q = ps_single_quote(folder)
        lines += [f"    try {{ Remove-MpPreference -ExclusionPath {q}; $result.removed += @{{ type='path'; value={q} }} }}",
                  f"    catch {{ $result.errors += ('path ' + {q} + ' : ' + $_.Exception.Message) }}"]
    lines += _ps_footer()
    return "\r\n".join(lines) + "\r\n"


def build_read_script(result_path: str) -> str:
    """PowerShell that only reads the current exclusion lists into the JSON result file."""
    lines = _ps_header(result_path)
    lines.append("    $result.action = 'read'")
    lines += _ps_footer()
    return "\r\n".join(lines) + "\r\n"


@dataclass
class DefenderResult:
    ok: bool
    action: str
    added: list
    removed: list
    errors: list
    exclusion_process: list
    exclusion_path: list
    tamper: object = None
    raw: str = ""
    defender: dict | None = None
    elevated: bool = False
    not_applied: list = field(default_factory=list)

    @property
    def defender_problem(self) -> str:
        d = self.defender
        if any("defender is not available" in str(e).lower() for e in self.errors):
            return "Microsoft Defender is not available on this PC (disabled or replaced by another antivirus)."
        if not d:
            return ""
        mode = str(d.get("mode") or "")
        if not d.get("service") or not d.get("antivirus"):
            return "Microsoft Defender Antivirus is turned off – exclusions have no effect."
        if "passive" in mode.lower() or "sxs" in mode.lower():
            return (f"Microsoft Defender runs in '{mode}' mode (another antivirus is active) – add the "
                    "exclusion in that antivirus too.")
        return ""

    @property
    def denied(self) -> bool:
        return any(EXCL_READ_DENIED.lower() in str(e).lower() or "must be an administrator" in str(e).lower()
                   for e in self.errors)


def parse_defender_result(text: str) -> DefenderResult:
    text = (text or "").lstrip("\ufeff")
    data = json.loads(text or "{}")

    def _list(key):
        v = data.get(key)
        if v is None:
            return []
        return [v] if isinstance(v, (str, dict)) else list(v)

    def items(key):
        out = []
        for it in _list(key):
            if isinstance(it, dict):
                out.append((it.get("type", ""), it.get("value", "")))
            else:
                out.append(("", it))
        return out

    def strs(key):
        return [str(x) for x in _list(key) if x]

    procs, paths, errors = strs("exclusionProcess"), strs("exclusionPath"), strs("errors")
    if any(EXCL_READ_DENIED.lower() in str(x).lower() for x in procs + paths):
        procs, paths = [], []
        errors.append(EXCL_READ_DENIED)
    res = DefenderResult(
        ok=bool(data.get("ok")) and not errors, action=data.get("action", ""), added=items("added"),
        removed=items("removed"), errors=errors, exclusion_process=procs, exclusion_path=paths,
        tamper=data.get("tamper"), raw=text)
    res.defender = data.get("defender") or None
    res.elevated = bool(data.get("elevated"))
    # an "added" item that is not in the read-back list was silently ignored (policy / Tamper Protection)
    if res.action == "add" and not res.denied:
        have_p = {x.lower() for x in procs}
        have_d = {x.lower().rstrip("\\") for x in paths}
        res.not_applied = [(t, v) for t, v in res.added
                           if (v.lower() not in have_p if t == "process" else v.lower().rstrip("\\") not in have_d)]
    elif res.action == "remove" and not res.denied:
        have = {x.lower() for x in procs} | {x.lower().rstrip("\\") for x in paths}
        res.not_applied = [(t, v) for t, v in res.removed if v.lower().rstrip("\\") in have]
    if res.not_applied:
        res.ok = False
    return res


def summarize_defender(res: DefenderResult) -> str:
    verb = {"add": "Added", "remove": "Removed", "read": "Read"}.get(res.action, "Changed")
    lines = []
    changed = res.added if res.action == "add" else res.removed
    if changed:
        lines.append(f"{verb} {len(changed)} exclusion(s):")
        for typ, val in changed:
            lines.append(f"  [{typ or 'process'}] {val}")
    elif res.action in ("add", "remove"):
        lines.append("No exclusions were changed.")
    if res.not_applied:
        lines.append("")
        lines.append("Windows accepted the command but these are NOT in the exclusion list afterwards "
                     "(blocked by Tamper Protection or a group policy?):" if res.action == "add" else
                     "These are still in the exclusion list afterwards (set by a group policy?):")
        lines += [f"  [{t}] {v}" for t, v in res.not_applied]
    if res.defender_problem:
        lines.append("")
        lines.append(res.defender_problem)
    if res.errors:
        lines.append("")
        if res.denied:
            lines.append("Access denied: viewing or changing Defender exclusions needs administrator rights "
                         "(and Tamper Protection or a group policy can also block it).")
        for e in res.errors:
            if EXCL_READ_DENIED.lower() not in str(e).lower():
                lines.append("Error: " + str(e))
    return "\n".join(lines) or "Nothing to do."


def exe_in_exclusions(exe: str, process_list: Iterable[str], path_list: Iterable[str]) -> bool:
    """True if this exe is covered by a process exclusion (exact path or bare name) or sits inside an
    excluded folder path."""
    if not exe:
        return False
    e = exe.replace("/", "\\").lower()
    base = _basename(e)
    for pr in process_list or []:
        p = str(pr).replace("/", "\\").lower()
        if p == e or p == base:
            return True
    for pa in path_list or []:
        d = str(pa).replace("/", "\\").lower().rstrip("\\")
        if d and (e == d or e.startswith(d + "\\")):
            return True
    return False

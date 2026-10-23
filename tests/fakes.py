"""A snapshot that looks like a typical AI-workstation PC on 22 Oct 2026 (from Get-CimInstance / netstat / PDH)."""
import time

import radar_core as rc

NOW = 1_800_000_000.0
MB = 1024 * 1024


def P(pid, name, cmd=(), exe="", ppid=1, listen=(), vram=None, cpu=0.0, rss=50 * MB, win=False, cwd="", age=600):
    return rc.ProcInfo(pid=pid, name=name, ppid=ppid, exe=exe, cmdline=list(cmd), cwd=cwd, create_time=NOW - age,
                       cpu=cpu, rss=rss, listen=list(listen), vram=vram, has_window=win)


COMFY = r"C:\Users\Alex\Documents\ComfyUI_windows_portable_nvidia"


def demo_snapshot(vram=True):
    procs = [
        P(4, "System", listen=[("10.0.5.10", 139), ("::", 445)]),
        P(1316, "svchost.exe", exe=r"C:\Windows\System32\svchost.exe", listen=[("0.0.0.0", 135)]),
        P(10648, "explorer.exe", exe=r"C:\Windows\explorer.exe", win=True, rss=300 * MB),
        P(14672, "ollama.exe", ["ollama", "serve"], exe=r"C:\Users\Alex\AppData\Local\Programs\Ollama\ollama.exe",
          listen=[("::", 11434)], vram=0, age=7200),
        P(15000, "ollama.exe", [r"C:\...\ollama.exe", "runner", "--model", "x", "--port", "50123"], ppid=14672,
          listen=[("127.0.0.1", 50123)], vram=5200 * MB),
        P(8184, "locally-uncensored.exe", [r"C:\Program Files\Locally Uncensored\locally-uncensored.exe"],
          exe=r"C:\Program Files\Locally Uncensored\locally-uncensored.exe", win=True),
        P(8304, "python.exe", [COMFY + r"\python_embeded\python.exe", "main.py", "--listen", "127.0.0.1", "--port",
                               "8188", "--enable-cors-header", "http://tauri.localhost"],
          exe=COMFY + r"\python_embeded\python.exe", ppid=8184, cwd=COMFY + r"\ComfyUI",
          listen=[("127.0.0.1", 8188)], vram=172154880, rss=900 * MB, cpu=0.4),
        P(7564, "conhost.exe", ["conhost.exe"], exe=r"C:\Windows\System32\conhost.exe", ppid=8304),
        P(5848, "lu-llama-server.exe", exe=r"C:\Program Files\Locally Uncensored\lu-llama-server.exe", ppid=8184,
          listen=[("127.0.0.1", 8128)]),
        P(9784, "opencode-cli.exe", [r"C:\Users\Alex\AppData\Roaming\ai.opencode.desktop\cli\2.1.1-x64\opencode-cli.exe",
                                     "serve", "--service"], listen=[("0.0.0.0", 4096)]),
        P(9196, "node.exe", [r"C:\Program Files\nodejs\node.exe", r"C:\Program Files\nodejs\node_modules\npm\bin\npx-cli.js",
                             "--yes"], exe=r"C:\Program Files\nodejs\node.exe"),
        P(6840, "tailscaled.exe", listen=[("100.78.48.89", 42619)]),
        P(10336, "ShadowStreamer.exe", listen=[("::", 7010), ("0.0.0.0", 7016), ("::", 7016)]),
        P(18472, "msedge.exe", ["msedge.exe"], win=True, rss=400 * MB),
        P(18500, "msedge.exe", ["msedge.exe", "--type=renderer"], ppid=18472, rss=500 * MB, cpu=3.0),
        P(30000, "BigIdle.exe", ["BigIdle.exe"], exe=r"D:\Tools\BigIdle.exe", rss=800 * MB),
        P(30001, "SmallIdle.exe", ["SmallIdle.exe"], exe=r"D:\Tools\SmallIdle.exe", rss=20 * MB),
    ]
    snap = rc.Snapshot(procs={p.pid: p for p in procs}, taken_at=NOW, duration_ms=300,
                       vram_source="Windows GPU counters (dedicated usage)" if vram else "",
                       gpus=[rc.GpuInfo("NVIDIA RTX A4500", 1776 * MB, 19190 * MB, 17210 * MB, 8)],
                       ollama_models=[{"name": "qwen3:8b", "size": 6000 * MB, "size_vram": 5200 * MB}])
    if not vram:
        for p in procs:
            p.vram = None
    return snap


class FakeCollector:
    def __init__(self, snap=None):
        self.snap = snap or demo_snapshot()
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        self.snap.taken_at = time.time()
        return self.snap

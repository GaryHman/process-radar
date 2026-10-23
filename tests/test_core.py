import json

import pytest

import radar_core as rc
from fakes import COMFY, MB, NOW, P, demo_snapshot


def statuses(snap=None):
    return {s.key: s for s in rc.service_status(snap or demo_snapshot(), NOW)}


def test_ollama_running_with_port_vram_and_models():
    st = statuses()["ollama"]
    assert st.running and st.pids == [14672, 15000]
    assert [p for p, _ in st.ports] == [11434, 50123]
    assert dict(st.ports)[11434] == "http://localhost:11434"
    assert st.vram == 5200 * MB
    assert st.detail.startswith("loaded: qwen3:8b")


def test_comfyui_detected_with_actual_port():
    st = statuses()["comfyui"]
    assert st.running and st.pids == [8304]
    assert st.ports == [(8188, "http://localhost:8188")]
    assert st.vram == 172154880


def test_comfyui_on_other_port_reads_listening_port():
    snap = demo_snapshot()
    snap.procs[8304].listen = [("127.0.0.1", 8200)]
    assert statuses(snap)["comfyui"].ports == [(8200, "http://localhost:8200")]


def test_comfyui_not_listening_yet_mentions_cmdline_port():
    snap = demo_snapshot()
    snap.procs[8304].listen = []
    assert "8188" in statuses(snap)["comfyui"].detail


def test_stopped_services_have_badge_data():
    snap = demo_snapshot()
    for pid in (8304, 7564):
        del snap.procs[pid]
    st = statuses(snap)
    assert not st["comfyui"].running and st["comfyui"].pids == []
    assert not st["lmstudio"].running and not st["docker"].running and not st["jupyter"].running


def test_other_known_services():
    st = statuses()
    assert st["opencode"].running and st["opencode"].ports == [(4096, "http://localhost:4096")]
    assert st["llamacpp"].running and st["llamacpp"].pids == [5848]
    assert st["locallyuncensored"].pids == [8184]
    assert st["python"].running and st["python"].detail == "1 running"
    assert st["node"].pids == [9196]


@pytest.mark.parametrize("proc,key", [
    (P(1, "LM Studio.exe", exe=r"C:\Users\x\AppData\Local\Programs\LM Studio\LM Studio.exe"), "lmstudio"),
    (P(1, "python.exe", ["python", "-m", "jupyterlab"]), "jupyter"),
    (P(1, "python.exe", ["python", r"C:\x\Scripts\jupyter-lab-script.py"]), "jupyter"),
    (P(1, "python.exe", ["python", "launch.py"], cwd=r"C:\ai\stable-diffusion-webui"), "sdwebui"),
    (P(1, "python.exe", ["python", "webui.py"], cwd=r"C:\ai\webui_forge"), "sdwebui"),
    (P(1, "Docker Desktop.exe"), "docker"),
    (P(1, "com.docker.backend.exe"), "docker"),
    (P(1, "vmmemWSL"), "wsl"),
    (P(1, "node.exe", ["node", r"C:\Users\x\AppData\Roaming\npm\node_modules\openclaw\dist\index.js", "gateway"]), "openclaw"),
    (P(1, "ollama app.exe"), "ollama"),
    (P(1, "llama-server.exe"), "llamacpp"),
    (P(1, "open-webui.exe", ["open-webui", "serve"]), "openwebui"),
    (P(1, "ComfyUI.exe"), "comfyui"),
])
def test_service_matchers(proc, key):
    assert rc.service_of(proc).key == key


def test_python_main_py_outside_comfy_is_not_comfyui():
    assert rc.service_of(P(1, "python.exe", ["python", "main.py"], cwd=r"C:\projects\bot")) is None


def test_noise_and_listing_rules():
    snap = demo_snapshot()
    rows = {p.pid: why for p, why in rc.background_rows(snap)}
    # system noise is hidden
    assert 4 not in rows and 1316 not in rows and 7564 not in rows and 10648 not in rows
    # known + ports + runtime + server
    assert rows[14672] == "known service" and rows[8304] == "known service"
    assert rows[6840] == "listening on a network port"
    assert rows[9196] == "known service" or rows[9196] == "Node.js runtime"
    assert rows[30000] == "no window, but using CPU/RAM"
    # windowed apps and their helper children are not "background"; small idle ones neither
    assert 18472 not in rows and 18500 not in rows and 30001 not in rows
    allrows = {p.pid for p, _ in rc.background_rows(snap, show_all=True)}
    assert allrows == set(snap.procs)


def test_friendly_labels():
    snap = demo_snapshot()
    lab = {pid: rc.friendly_label(p) for pid, p in snap.procs.items()}
    assert lab[14672] == "Ollama" and lab[15000] == "Ollama (model runner)"
    assert lab[8304] == "ComfyUI"
    assert lab[9196] == "Node.js – npx-cli.js"
    assert lab[6840] == "Tailscale" and lab[10336] == "Shadow PC service"
    assert lab[1316] == "Windows system"
    assert rc.friendly_label(P(1, "python.exe", ["python", "-m", "http.server"])) == "Python – -m http.server"
    assert rc.friendly_label(snap.procs[8304], self_pid=8304) == "Process Radar (this app)"


def test_descendants_for_kill_tree():
    snap = demo_snapshot()
    assert sorted(rc.descendants(8184, snap.procs)) == [5848, 7564, 8304]
    assert rc.descendants(8304, snap.procs) == [7564]
    assert rc.descendants(30001, snap.procs) == []


def test_folder_for():
    snap = demo_snapshot()
    assert rc.folder_for(snap.procs[8304]) == COMFY + r"\ComfyUI"
    assert rc.folder_for(snap.procs[5848]).endswith("Locally Uncensored")
    p = P(1, "python.exe", ["python", r"C:\bots\run\bot.py"], exe=r"C:\Python314\python.exe")
    assert rc.folder_for(p).replace("\\", "/").endswith("bots/run")


def test_formatting():
    assert rc.format_bytes(None) == "–"
    assert rc.format_bytes(512) == "512 B"
    assert rc.format_bytes(172154880) == "164 MB"
    assert rc.format_bytes(19190 * MB) == "18.7 GB"
    assert rc.format_uptime(5) == "5s" and rc.format_uptime(125) == "2m 05s"
    assert rc.format_uptime(3 * 3600 + 120) == "3h 02m" and rc.format_uptime(2 * 86400 + 3600) == "2d 1h"
    s = rc.shorten("x" * 300, 50)
    assert len(s) == 50 and "…" in s


def test_short_cmdline_hides_long_paths():
    p = demo_snapshot().procs[8304]
    short = rc.short_cmdline(p, 200)
    assert short.startswith("python.exe main.py --listen 127.0.0.1 --port 8188")
    assert "ComfyUI_windows_portable" not in short
    assert "ComfyUI_windows_portable" in p.cmd_text


def test_urls():
    assert rc.url_for("0.0.0.0", 80) == "http://localhost:80"
    assert rc.url_for("::", 11434) == "http://localhost:11434"
    assert rc.url_for("100.78.48.89", 42619) == "http://100.78.48.89:42619"
    assert rc.url_for("fd7a::1", 5) == "http://[fd7a::1]:5"
    p = P(1, "x", listen=[("10.0.0.2", 7016), ("0.0.0.0", 7016)])
    assert rc.urls_for(p) == [(7016, "http://localhost:7016")]


def test_parse_nvidia_smi():
    g = rc.parse_smi_gpus("NVIDIA RTX A4500, 1776, 19190, 17210, 8\n")
    assert g[0].name == "NVIDIA RTX A4500" and g[0].used == 1776 * MB and g[0].total == 19190 * MB and g[0].util == 8
    assert rc.parse_smi_gpus("") == []
    wddm = "1672, [Insufficient Permissions], [N/A]\n8304, C:\\x\\python.exe, [N/A]\n"
    assert rc.parse_smi_apps(wddm) == {}
    tcc = "8304, python.exe, 5120\n8304, python.exe, 100\n99, x, 1\n"
    assert rc.parse_smi_apps(tcc) == {8304: 5220 * MB, 99: MB}


def test_parse_pdh_items():
    items = [("pid_8304_luid_0x00000000_0x000084af_phys_0", 172154880),
             ("pid_8304_luid_0x00000000_0x000099af_phys_0", 10),
             ("pid_4_luid_0x00000000_0x000084af_phys_0", 4227072), ("_Total", 5), ("junk", None)]
    assert rc.parse_pdh_items(items) == {8304: 172154890, 4: 4227072}


def test_parse_ollama_ps():
    text = json.dumps({"models": [{"name": "qwen3:8b", "model": "qwen3:8b", "size": 6, "size_vram": 5}]})
    assert rc.parse_ollama_ps(text) == [{"name": "qwen3:8b", "size": 6, "size_vram": 5}]
    assert rc.parse_ollama_ps('{"models": []}') == []
    assert rc.ollama_detail([]) == "no model loaded" and rc.ollama_detail(None) == ""


def test_vram_unknown_when_no_source():
    st = statuses(demo_snapshot(vram=False))
    assert st["comfyui"].vram is None and st["ollama"].vram is None


def test_report_text_contains_services():
    t = rc.report_text(demo_snapshot(), NOW)
    assert "Ollama" in t and "RUNNING" in t and "ComfyUI" in t and "8188" in t and "RTX A4500" in t

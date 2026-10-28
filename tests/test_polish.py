"""Fixes from 23 Oct 2026: second launch, crashes while stopping, service attribution, false positives."""
import gc
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

import radar_core as rc
from fakes import MB, NOW, P, meneer_snapshot

ROOT = Path(__file__).resolve().parent.parent
LU = r"C:\Program Files\Locally Uncensored"
COMFY = r"C:\Users\Shadow\Documents\ComfyUI_windows_portable_nvidia"


# ----------------------------------------------------------------------------- 3. "is it seeing apples for pears?"
def lu_tree():
    """Exactly what Meneer's PC ran at 15:11 on 23 Oct 2026: Locally Uncensored started all of these."""
    procs = [
        P(10248, "explorer.exe", exe=r"C:\Windows\explorer.exe", win=True, age=20000),
        P(11908, "locally-uncensored.exe", [LU + r"\locally-uncensored.exe"], exe=LU + r"\locally-uncensored.exe",
          ppid=10248, win=True, age=70),
        P(12588, "ollama.exe", ["ollama", "serve"], ppid=11908, listen=[("127.0.0.1", 11434)], age=68),
        P(5160, "python.exe", [COMFY + r"\python_embeded\python.exe", "main.py", "--listen", "127.0.0.1", "--port",
                               "8188", "--enable-cors-header", "http://tauri.localhost"], ppid=11908,
          cwd=COMFY + r"\ComfyUI", age=65),
        P(21984, "lu-llama-server.exe", [LU + r"\lu-llama-server.exe", "-m", "x.gguf", "--port", "8128"],
          exe=LU + r"\lu-llama-server.exe", ppid=11908, listen=[("127.0.0.1", 8128)], age=57),
        P(9116, "cmd.exe", ["cmd.exe", "/c", "npx"], exe=r"C:\Windows\System32\cmd.exe", ppid=11908, age=66),
        P(13632, "node.exe", [r"C:\Program Files\nodejs\node.exe", "npx-cli.js", "--yes", "rea", "mcp"],
          ppid=9116, age=66),
        P(6400, "cmd.exe", ["cmd.exe", "/c", "rea"], exe=r"C:\Windows\System32\cmd.exe", ppid=13632, age=60),
        P(19028, "node.exe", ["node", r"C:\Users\Shadow\AppData\Local\npm-cache\_npx\x\rea.mjs", "mcp"],
          ppid=6400, age=60),
        P(9932, "opencode-cli.exe", [r"C:\Users\Shadow\AppData\Roaming\ai.opencode.desktop\cli\opencode-cli.exe",
                                     "serve"], listen=[("0.0.0.0", 4096)], ppid=777, age=13000),
    ]
    return rc.Snapshot(procs={p.pid: p for p in procs}, taken_at=NOW)


def test_services_started_by_locally_uncensored_are_attributed():
    snap = lu_tree()
    st = {s.key: s for s in rc.service_status(snap, NOW)}
    for key in ("ollama", "comfyui", "llamacpp"):
        assert st[key].running and st[key].launcher == "Locally Uncensored" and st[key].launcher_pid == 11908, key
    assert st["locallyuncensored"].running and st["locallyuncensored"].launcher == ""   # started from Explorer
    assert st["opencode"].running and st["opencode"].launcher == ""                     # unrelated, own parent
    assert not st["lmstudio"].running
    assert "(via Locally Uncensored)" in st["comfyui"].line()
    # node MCP plugin: cmd -> node -> cmd chain is looked through
    assert rc.launcher_of(snap.procs[19028], snap.procs) == ("Locally Uncensored", 11908)


def test_launcher_ignores_recycled_parent_pid():
    snap = lu_tree()
    snap.procs[11908].create_time = snap.procs[5160].create_time + 60     # "parent" started after the child
    assert rc.launcher_of(snap.procs[5160], snap.procs) == ("", None)


def test_launcher_names_an_unknown_app_and_skips_runtimes():
    procs = {p.pid: p for p in [
        P(1, "explorer.exe", win=True, age=9000),
        P(2, "Code.exe", ppid=1, age=5000),
        P(3, "python.exe", ["python", "server.py"], ppid=2, age=100),
        P(40, "MyLauncher.exe", ppid=1, age=500),
        P(5, "pythonw.exe", ["pythonw", "x.py"], ppid=40, age=400),
        P(6, "python.exe", ["python", "main.py"], cwd=r"D:\ComfyUI", ppid=5, age=300),
    ]}
    assert rc.launcher_of(procs[3], procs) == ("VS Code", 2)
    assert rc.launcher_of(procs[6], procs) == ("MyLauncher", 40)       # the plain python in between is skipped


@pytest.mark.parametrize("proc,expected", [
    # real services still match
    (P(1, "node.exe", ["node", r"C:\Users\A\AppData\Roaming\npm\\node_modules\openclaw\openclaw.mjs", "gateway"]),
     "openclaw"),
    (P(2, "opencode-cli.exe", ["opencode-cli.exe", "serve"]), "opencode"),
    (P(3, "node.exe", ["node", r"C:\Users\A\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode"]), "opencode"),
    (P(4, "python.exe", ["python", "-m", "open_webui", "serve"]), "openwebui"),
    (P(5, "open-webui.exe", ["open-webui", "serve"]), "openwebui"),
    # false positives before 23 Oct 2026: the name only appears in a folder / argument
    (P(6, "node.exe", ["node", r"C:\Users\Shadow\opencode-state\Grokbot stuff\tools\sync.js"]), None),
    (P(7, "python.exe", ["python", r"C:\Users\Shadow\opencode-state\openclaw-setup\check.py"]), None),
    (P(8, "Code.exe", ["Code.exe", r"C:\Users\Shadow\opencode-state\openclaw-workspace"]), None),
    (P(9, "chrome.exe", ["chrome.exe", "http://localhost:8080/open-webui/docs"]), None),
    (P(10, "pythonw.exe", ["pythonw", r"C:\Users\Shadow\opencode-state\Grokbot stuff\Software\ProcessRadar\process_radar.py"]),
     None),
])
def test_service_matching_has_no_folder_name_false_positives(proc, expected):
    s = rc.service_of(proc)
    assert (s.key if s else None) == expected


def test_card_shows_via_launcher(qapp, tmpenv):
    import process_radar as pr
    snap = lu_tree()
    st = {s.key: s for s in rc.service_status(snap, NOW)}
    card = pr.ServiceCard("comfyui", "ComfyUI", "x")
    card.update_status(st["comfyui"])
    assert card.detail.text().startswith("via Locally Uncensored")
    assert "Started by: Locally Uncensored (PID 11908)" in card.toolTip()
    card2 = pr.ServiceCard("opencode", "opencode", "x")
    card2.update_status(st["opencode"])
    assert card2.detail.text().startswith("PID 9932") and "Started by" not in card2.toolTip()


# ----------------------------------------------------------------------------- 2. closing while stopping
def test_stop_plan_leaves_out_processes_that_started_process_radar():
    procs = {p.pid: p for p in [
        P(1, "explorer.exe", win=True, age=9000),
        P(50, "WindowsTerminal.exe", ppid=1, age=5000),
        P(60, "py.exe", ["py", "process_radar.py"], ppid=50, age=100),
        P(70, "pythonw.exe", ["pythonw", "process_radar.py"], ppid=60, age=99),   # Process Radar
        P(80, "node.exe", ["node", "x.js"], ppid=50, age=50),
    ]}
    plan = rc.stop_plan([60, 50, 80, 70], procs, self_pid=70)
    assert plan.pids == [80] and plan.excluded_self and sorted(plan.excluded_parents) == [50, 60]
    assert any("Process Radar was started from it" in w for w in plan.warnings)
    # stopping the explorer tree never lists Process Radar or its parents as children
    plan = rc.stop_plan([1], procs, self_pid=70)
    assert plan.pids == [] and plan.excluded_parents == [1]


def test_self_and_ancestors_contains_parent():
    import radar_collect as rcol
    me = rcol.self_and_ancestors()
    assert os.getpid() in me and os.getppid() in me


def test_stop_many_does_not_kill_a_recycled_pid():
    import radar_collect as rcol
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ct = psutil.Process(p.pid).create_time()
        res = rcol.stop_many([p.pid], tree=False, timeout=2, expected={p.pid: ct - 100})   # "a different process"
        assert res == {p.pid: "gone"} and p.poll() is None
        res = rcol.stop_many([p.pid], tree=False, timeout=3, expected={p.pid: ct})
        assert res == {p.pid: "stopped"}
    finally:
        if p.poll() is None:
            p.kill()


def test_stop_many_never_raises(monkeypatch):
    import radar_collect as rcol

    class Boom:
        pid = 999999

        def __init__(self, pid):
            raise psutil.Error("weird")
    monkeypatch.setattr(rcol.psutil, "Process", Boom)
    monkeypatch.setattr(rcol, "self_and_ancestors", lambda: {os.getpid()})
    assert rcol.stop_many([999999], tree=True) == {999999: "gone"}


def test_exception_in_a_slot_is_logged_and_app_keeps_running(qapp, tmpenv, monkeypatch):
    import logging
    import process_radar as pr
    from PySide6.QtCore import QObject, Signal
    records = []
    h = logging.Handler()
    h.emit = records.append
    pr.log.addHandler(h)
    old = sys.excepthook
    try:
        pr.install_crash_handlers(pr.data_dir())
        assert (pr.data_dir() / "crash.log").exists()

        class Src(QObject):
            sig = Signal()
        src = Src()

        def bad():
            raise psutil.NoSuchProcess(1234)
        src.sig.connect(bad)
        src.sig.emit()                 # PySide reports slot errors through sys.excepthook
        qapp.processEvents()
        assert any("Unhandled error" in r.getMessage() for r in records)
        # a thread error is logged too
        import threading
        t = threading.Thread(target=bad)
        t.start(); t.join()
        assert any("Unhandled error in thread" in r.getMessage() for r in records)
    finally:
        pr.log.removeHandler(h)
        sys.excepthook = old
        import faulthandler
        faulthandler.disable()


def test_gc_runs_on_gui_thread_only(qapp, tmpenv):
    import process_radar as pr
    from fakes import FakeCollector
    app = pr.RadarApp(qapp, pr.Settings(pr.data_dir() / "s.json"), collector_factory=FakeCollector, tray=False)
    try:
        app.collect_garbage_on_gui_thread(50)
        assert not gc.isenabled() and app.gc_timer.isActive()
    finally:
        app.shutdown()
        app.win.deleteLater()
    assert gc.isenabled()


def test_stop_dialog_is_deleted_on_gui_thread(qapp, tmpenv, monkeypatch):
    """PySide6 6.12 hands a dialog back to Python after exec(); it must be deleted by Qt on the GUI thread."""
    import process_radar as pr
    from PySide6.QtWidgets import QDialog
    from shiboken6 import Shiboken
    from fakes import FakeCollector
    app = pr.RadarApp(qapp, pr.Settings(pr.data_dir() / "s.json"), collector_factory=FakeCollector, tray=False)
    try:
        w = app.win
        w.on_snapshot(meneer_snapshot())
        made = []
        monkeypatch.setattr(pr.StopDialog, "exec", lambda self: (made.append(self), QDialog.Rejected)[1])
        w.stop_pids([30000])
        assert made
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not Shiboken.isValid(made[0])
    finally:
        app.shutdown()
        app.win.deleteLater()


# ----------------------------------------------------------------------------- 1. second launch
def test_server_name_depends_on_data_folder(tmp_path, monkeypatch):
    import process_radar as pr
    monkeypatch.setenv("PR_DATA_DIR", str(tmp_path / "a"))
    a = pr.server_name()
    monkeypatch.setenv("PR_DATA_DIR", str(tmp_path / "b"))
    assert pr.server_name() != a and a.startswith("ProcessRadar-")


def test_notify_gives_up_quietly_without_server(qapp, tmpenv):
    import process_radar as pr
    t0 = time.time()
    assert pr.notify_running_instance(attempts=2, wait_ms=50) is False
    assert time.time() - t0 < 3


def test_second_launch_shows_running_window_without_dialog(qapp, tmp_path):
    """Hold the lock + pipe like a running instance, start process_radar.py again: it must just send
    'show' and exit 0 - no message box, no second window."""
    import process_radar as pr
    from PySide6.QtCore import QLockFile
    from PySide6.QtNetwork import QLocalServer
    data = tmp_path / "data"
    data.mkdir()
    old = os.environ.get("PR_DATA_DIR")
    os.environ["PR_DATA_DIR"] = str(data)
    try:
        lock = QLockFile(str(data / "app.lock"))
        assert lock.tryLock(100)
        server = QLocalServer()
        QLocalServer.removeServer(pr.server_name())
        assert server.listen(pr.server_name())
        got = []

        def on_conn():
            while server.hasPendingConnections():
                c = server.nextPendingConnection()
                c.readyRead.connect(lambda c=c: got.append(bytes(c.readAll())))
        server.newConnection.connect(on_conn)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PR_DATA_DIR=str(data))
        proc = subprocess.Popen([sys.executable, str(ROOT / "process_radar.py")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        end = time.time() + 20
        while proc.poll() is None and time.time() < end:
            qapp.processEvents()
            time.sleep(0.02)
        for _ in range(20):
            qapp.processEvents()
            time.sleep(0.01)
        out = proc.stdout.read().decode("utf-8", "replace")
        assert proc.returncode == 0, out
        assert got and got[0] == b"show"
        assert "Already running - asked the running instance" in (data / "radar.log").read_text(encoding="utf-8")
        server.close()
        lock.unlock()
    finally:
        if old is None:
            os.environ.pop("PR_DATA_DIR", None)
        else:
            os.environ["PR_DATA_DIR"] = old


@pytest.mark.skipif(os.name != "nt", reason="run.bat is Windows only")
def test_run_bat_finds_python_while_radar_log_is_held_open(tmp_path):
    """The bug Meneer saw: with the app running (radar.log open), run.bat rejected every Python."""
    import logging.handlers
    data = tmp_path / "data"          # same folder the app would use (PR_DATA_DIR), never the real one
    data.mkdir()
    h = logging.handlers.RotatingFileHandler(data / "radar.log", encoding="utf-8")
    h.emit(logging.LogRecord("x", logging.INFO, "", 0, "app is running", None, None))   # opens + holds the file
    try:
        env = dict(os.environ, PR_DATA_DIR=str(data), PR_LAUNCH_DRYRUN="1")
        r = subprocess.run(["cmd", "/c", str(ROOT / "run.bat")], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        py = [l for l in r.stdout.splitlines() if l.startswith("PYTHON=")][0]
        assert py != "PYTHON=none" and py.lower().endswith("pythonw.exe"), r.stdout
        tried = [l for l in r.stdout.splitlines() if l.startswith("TRIED=")][0][6:].strip("|").split("|")
        assert len(tried) == len({t.lower() for t in tried}), tried          # no duplicates
        lg = data / "launcher.log"
        assert "skipped" not in (lg.read_text() if lg.exists() else "")     # the working Python was not rejected
    finally:
        h.close()

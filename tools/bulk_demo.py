"""Proof / demo for bulk stop, safe to run next to a live Process Radar.

Spawns its own dummy python sleepers (each with a child), opens a hidden Process Radar window
(WA_DontShowOnScreen, separate settings folder, no tray, no hotkeys), selects the dummies, renders the
window + the confirmation dialog to a PNG, then bulk-stops ONLY the dummies and prints the summary.

    python tools\\bulk_demo.py C:\\temp\\screenshot-bulk.png
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PR_DATA_DIR"] = tempfile.mkdtemp(prefix="radar-demo-")

import psutil  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import process_radar as pr  # noqa: E402
import radar_collect as rcol  # noqa: E402
import radar_core as rc  # noqa: E402

MARK = "radar_dummy_sleeper"


def spawn(n):
    flags = 0x08000000 if os.name == "nt" else 0
    child = f"import time; {MARK}=1; time.sleep(300)"
    code = (f"import subprocess,sys,time; {MARK}=1; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}], creationflags={flags}); time.sleep(300)")
    return [subprocess.Popen([sys.executable, "-c", code], creationflags=flags) for _ in range(n)]


def pump(app, secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main(out):
    parents = spawn(4)
    time.sleep(1.5)
    dummy = {p.pid for p in parents}
    for p in parents:
        dummy |= {c.pid for c in psutil.Process(p.pid).children(recursive=True)}
    print("spawned dummy PIDs (parents):", [p.pid for p in parents], " all:", sorted(dummy))
    qapp = QApplication.instance() or QApplication([])
    settings = pr.Settings(pr.data_dir() / "settings.json")
    app = pr.RadarApp(qapp, settings, tray=False)
    w = app.win
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1320, 820)
    app.show()
    for _ in range(300):
        pump(qapp, 0.05)
        if w.snap is not None and dummy <= set(w.snap.procs):
            break
    w.request_refresh(force=True)          # second sample so CPU % is real
    pump(qapp, 3.5)
    w.select_pids([p.pid for p in parents])
    w.table.setFocus()
    w.request_refresh(force=True)          # prove the selection survives an auto-refresh
    pump(qapp, 3.5)
    sel = w.selected_pids()
    print("selected after refresh:", sel)
    assert set(sel) == {p.pid for p in parents}, "selection lost!"
    rows = [w.proxy.mapFromSource(w.model.index(i, 0)).row() for i, r in enumerate(w.model.rows) if r[0].pid in dummy]
    if rows:
        w.table.scrollTo(w.proxy.index(min(rows), 0), w.table.ScrollHint.PositionAtCenter)
    pump(qapp, 0.3)
    win_pm = w.grab()
    plan = rc.stop_plan(sel, w.snap.procs, os.getpid())
    dlg = pr.StopDialog(plan, w)
    dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
    dlg.show()
    pump(qapp, 0.5)
    dlg_pm = dlg.grab()
    # compose: window, dimmed, with the dialog centred on top (like the real modal dialog)
    win_pm.setDevicePixelRatio(1.0)       # paint in device pixels (HiDPI screens)
    dlg_pm.setDevicePixelRatio(1.0)
    canvas = QPixmap(win_pm.size())
    canvas.setDevicePixelRatio(1.0)
    p = QPainter(canvas)
    p.drawPixmap(0, 0, win_pm)
    p.fillRect(canvas.rect(), QColor(0, 0, 0, 90))
    x = (canvas.width() - dlg_pm.width()) // 2
    y = (canvas.height() - dlg_pm.height()) // 2
    p.fillRect(x - 1, y - 1, dlg_pm.width() + 2, dlg_pm.height() + 2, QColor("#22c55e"))
    p.drawPixmap(x, y, dlg_pm)
    p.end()
    canvas.save(out)
    win_pm.save(out.replace(".png", "-window.png"))
    print("screenshot:", out, canvas.width(), "x", canvas.height())
    print("dialog rows:", [(it.name, it.pid, rc.format_bytes(it.rss), len(it.children)) for it in plan.items],
          "kill tree checked:", dlg.tree.isChecked())
    dlg.close()
    # the real bulk stop (only our dummies)
    assert set(plan.pids) <= dummy
    t0 = time.time()
    res = rcol.stop_many(plan.pids, tree=dlg.tree.isChecked(), timeout=3.0)
    names = {pid: "python.exe (dummy" + (" parent)" if pid in plan.pids else " child)") for pid in res}
    text, counts = rc.stop_summary(res, names)
    print(f"bulk stop took {time.time() - t0:.2f}s")
    print(text)
    left = [pid for pid in dummy if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE]
    print("dummies still alive:", left)
    # second stop of the same PIDs -> already gone
    print(rc.stop_summary(rcol.stop_many(plan.pids[:2], tree=False, timeout=1))[0])
    app.shutdown()
    for pp in parents:
        pp.kill()
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "screenshot-bulk.png"))

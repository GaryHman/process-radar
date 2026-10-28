"""Render the Defender exclusions dialog (hidden) for a screenshot. Never elevates / never changes Defender.

    python tools\\defender_demo.py C:\\temp\\screenshot-defender.png
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PR_DATA_DIR"] = tempfile.mkdtemp(prefix="radar-def-demo-")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import defender_ui as defui  # noqa: E402
import process_radar as pr  # noqa: E402
import radar_core as rc  # noqa: E402


def pump(app, secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main(out):
    qapp = QApplication.instance() or QApplication([])
    settings = pr.Settings(pr.data_dir() / "settings.json")
    app = pr.RadarApp(qapp, settings, tray=False)
    w = app.win
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1320, 820)
    # seed the cache so a few rows show the shield
    app.show()
    for _ in range(200):
        pump(qapp, 0.05)
        if w.snap is not None:
            break
    w.request_refresh(force=True)
    pump(qapp, 2.5)
    # Prefer a Windows-looking selection so the screenshot matches Meneer's PC. On Linux (box) inject fakes.
    from tests.fakes import meneer_snapshot
    fake = meneer_snapshot()
    pick = [fake.procs[k] for k in (14672, 8304, 9196, 8184, 5848, 9784) if k in fake.procs]
    # also include a couple of live ones so the background table is not empty of selection
    live = [p for p in w.snap.procs.values() if p.exe][:2]
    targets = rc.defender_targets(pick)
    # show the fake procs in the table too (so shields / selection look right)
    for p in pick:
        w.snap.procs[p.pid] = p
    w.on_snapshot(w.snap)
    pump(qapp, 0.3)
    # mark ollama (if present) as already excluded so the shield shows
    for t in targets:
        if "ollama" in t.name.lower():
            app.def_cache.processes = [t.exe]
            app.def_cache.updated = time.time()
            break
    w.model.excluded = app.def_cache.covers
    w.select_pids([pid for t in targets for pid, _ in t.procs][:4])
    w.request_refresh(force=True)
    pump(qapp, 2.0)
    win_pm = w.grab()
    dlg = defui.AddExclusionsDialog(targets, w, title="Add to Defender exclusions")
    dlg.setStyleSheet(w.styleSheet())
    dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
    # force a wide grab so the Path column can show drive + folders + filename
    dlg.resize(max(dlg.width(), int(win_pm.width() * 0.88)), max(dlg.height(), 500))
    dlg.table.setColumnWidth(2, max(dlg.table.columnWidth(2), int(dlg.width() * 0.55)))
    dlg.show()
    pump(qapp, 0.4)
    dlg_pm = dlg.grab()
    win_pm.setDevicePixelRatio(1.0)
    dlg_pm.setDevicePixelRatio(1.0)
    canvas = QPixmap(win_pm.size())
    canvas.setDevicePixelRatio(1.0)
    p = QPainter(canvas)
    p.drawPixmap(0, 0, win_pm)
    p.fillRect(canvas.rect(), QColor(0, 0, 0, 100))
    x = (canvas.width() - dlg_pm.width()) // 2
    y = (canvas.height() - dlg_pm.height()) // 2
    p.fillRect(x - 1, y - 1, dlg_pm.width() + 2, dlg_pm.height() + 2, QColor("#3b82f6"))
    p.drawPixmap(x, y, dlg_pm)
    p.end()
    canvas.save(out)
    print("screenshot:", out, canvas.width(), "x", canvas.height())
    print("targets:", [(t.name, t.exe, t.is_interpreter, len(t.procs)) for t in targets])
    print("change:", dlg.change())
    # also emit the generated script for dry-run proof (no elevation)
    script = rc.build_apply_script(dlg.change(), str(Path(os.environ["PR_DATA_DIR"]) / "defender-result.json"))
    script_path = Path(out).with_suffix(".ps1")
    script_path.write_text(script, encoding="utf-8")
    print("script:", script_path)
    print("--- script preview ---")
    print(script)
    dlg.close()
    app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "screenshot-defender.png"))

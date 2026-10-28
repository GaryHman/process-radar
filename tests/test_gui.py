import time

from PySide6.QtCore import Qt

from fakes import FakeCollector, meneer_snapshot


def make_app(qapp, tmpenv):
    import process_radar as pr
    settings = pr.Settings(pr.data_dir() / "settings.json")
    app = pr.RadarApp(qapp, settings, collector_factory=FakeCollector, tray=False)
    return pr, app


def pump(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_window_shows_services_and_rows(qapp, tmpenv):
    pr, app = make_app(qapp, tmpenv)
    try:
        app.show()
        assert pump(qapp, lambda: app.win.snap is not None)
        w = app.win
        assert w.cards["ollama"].badge.text() == "RUNNING"
        assert w.cards["comfyui"].badge.text() == "RUNNING"
        assert w.cards["lmstudio"].badge.text() == "STOPPED"
        assert "11434" in w.cards["ollama"].links.text()
        assert "RTX A4500" in w.gpu_label.text() and "18.7 GB" in w.gpu_bar.format()
        pids = {w.proxy.index(r, 0).data(pr.PID_ROLE) for r in range(w.proxy.rowCount())}
        assert {14672, 8304, 5848, 9784, 30000} <= pids and 1316 not in pids
        # search
        w.search.setText("comfy")
        pids = {w.proxy.index(r, 0).data(pr.PID_ROLE) for r in range(w.proxy.rowCount())}
        assert pids == {8304}
        w.search.setText("11434")
        assert w.proxy.rowCount() == 1
        w.search.clear()
        # show all
        w.show_all.setChecked(True)
        assert w.proxy.rowCount() == len(w.snap.procs)
        w.show_all.setChecked(False)
        # card filter
        w.on_card_clicked("ollama")
        pids = {w.proxy.index(r, 0).data(pr.PID_ROLE) for r in range(w.proxy.rowCount())}
        assert pids == {14672, 15000}
        w.on_card_clicked("ollama")
        assert w.proxy.rowCount() > 2
        # sort by RAM descending -> ComfyUI (900 MB) first among listed
        w.table.sortByColumn(pr.C_RAM, Qt.DescendingOrder)
        assert w.proxy.index(0, 0).data(pr.PID_ROLE) == 8304
        # selection survives a refresh
        w.select_pid(8304)
        w.on_snapshot(meneer_snapshot())
        assert w.selected_pid() == 8304
        assert w.stop_btn.isEnabled() and "8304" in w.stop_btn.text() and w.web_btn.isEnabled()
        # tooltips: full command line on hover
        src_row = [i for i, r in enumerate(w.model.rows) if r[0].pid == 8304][0]
        tip = w.model.index(src_row, pr.C_CMD).data(Qt.ToolTipRole)
        assert "ComfyUI_windows_portable_nvidia" in tip
        # pause stops auto refresh
        w.pause_btn.setChecked(True)
        calls = app.worker._collector.calls
        w.request_refresh()
        qapp.processEvents()
        assert app.worker._collector.calls == calls
    finally:
        app.shutdown()
        app.win.deleteLater()


def test_settings_persist(qapp, tmpenv):
    import process_radar as pr
    s = pr.Settings(pr.data_dir() / "settings.json")
    assert s["hotkeys"]["toggle"] == "Ctrl+Alt+P" and s["refresh_ms"] == 3000
    s["show_all"] = True
    s.save()
    assert pr.Settings(pr.data_dir() / "settings.json")["show_all"] is True


def test_icon_draws(qapp):
    import process_radar as pr
    assert not pr.draw_icon_pixmap(32).isNull() and not pr.app_icon().isNull()


def test_card_needs_real_click(qapp, tmpenv):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    pr, app = make_app(qapp, tmpenv)
    try:
        app.show()
        assert pump(qapp, lambda: app.win.snap is not None)
        card = app.win.cards["comfyui"]
        # a stray release (no press on the card) must not filter
        ev = QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10), Qt.LeftButton,
                         Qt.NoButton, Qt.NoModifier)
        card.mouseReleaseEvent(ev)
        assert app.win.selected_service is None
        QTest.mouseClick(card, Qt.LeftButton)
        assert app.win.selected_service == "comfyui" and app.win.proxy.rowCount() == 1
        QTest.mouseClick(card, Qt.LeftButton)
        assert app.win.selected_service is None
    finally:
        app.shutdown()
        app.win.deleteLater()


def _keys(w, pr):
    return set(w.selected_keys())


def test_multiselect_survives_refresh(qapp, tmpenv):
    import copy
    from PySide6.QtTest import QTest
    import radar_core as rc
    pr, app = make_app(qapp, tmpenv)
    try:
        app.show()
        assert pump(qapp, lambda: app.win.snap is not None)
        w = app.win
        w.table.sortByColumn(pr.C_RAM, Qt.DescendingOrder)
        w.select_pids([8304, 14672, 30000])
        assert set(w.selected_pids()) == {8304, 14672, 30000}
        assert w.stop_btn.text() == "■  Stop 3 processes…" and "3 selected" in w.count_label.text()
        assert not w.open_btn.isEnabled() and w.copy_btn.isEnabled()
        # refresh 1: same processes, new numbers (re-sort happens)
        snap = meneer_snapshot()
        snap.procs[30000].rss = 5000 * 1024 * 1024
        w.on_snapshot(snap)
        assert set(w.selected_pids()) == {8304, 14672, 30000}
        # refresh 2: 30000 exited, a new process appeared, another unselected one exited
        snap2 = meneer_snapshot()
        del snap2.procs[30000]
        del snap2.procs[6840]
        snap2.procs[40000] = copy.copy(snap2.procs[9196])
        snap2.procs[40000].pid = 40000
        w.on_snapshot(snap2)
        assert set(w.selected_pids()) == {8304, 14672}
        assert w.stop_btn.text() == "■  Stop 2 processes…"
        # refresh 3: PID 14672 reused by a different process (new start time) -> dropped
        snap3 = meneer_snapshot()
        del snap3.procs[30000]
        snap3.procs[14672].create_time += 99
        w.on_snapshot(snap3)
        assert w.selected_pids() == [8304]
        assert w.stop_btn.text().startswith("■  Stop python.exe (8304)")
        # selection also survives a search filter that hides and re-shows nothing selected
        w.select_pids([8304, 9196])
        w.on_snapshot(meneer_snapshot())
        assert set(w.selected_pids()) == {8304, 9196}
        # Ctrl+A selects every visible row
        w.table.setFocus()
        QTest.keyClick(w.table, Qt.Key_A, Qt.ControlModifier)
        assert len(w.selected_pids()) == w.proxy.rowCount() > 5
    finally:
        app.shutdown()
        app.win.deleteLater()


def test_bulk_stop_flow(qapp, tmpenv, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    import radar_collect as rcol
    pr, app = make_app(qapp, tmpenv)
    try:
        app.show()
        assert pump(qapp, lambda: app.win.snap is not None)
        w = app.win
        seen = {}

        def fake_exec(dlg):
            seen["dlg"] = dlg
            seen["rows"] = [dlg.table.item(r, 2).text() for r in range(dlg.table.rowCount())]
            seen["tree"] = dlg.tree.isChecked()
            seen["warn"] = [l.text() for l in dlg.findChildren(pr.QLabel) if "⚠" in l.text()]
            return QDialog.Accepted
        monkeypatch.setattr(pr.StopDialog, "exec", fake_exec)
        calls = []

        def fake_stop_many(pids, tree, timeout=3.0, protect=None, expected=None):
            calls.append((list(pids), tree, dict(expected or {})))
            return {pids[0]: "stopped", pids[1]: "denied", 7564: "stopped"}
        monkeypatch.setattr(rcol, "stop_many", fake_stop_many)
        monkeypatch.setattr(pr.os, "getpid", lambda: 30000)   # pretend BigIdle is Process Radar itself
        w.select_pids([8304, 10336, 30000])
        w.table.setFocus()
        QTest.keyClick(w.table, Qt.Key_Delete)                 # Delete key -> stop selected
        assert set(seen["rows"]) == {"8304", "10336"}           # Process Radar itself left out
        assert seen["tree"] is True                            # ComfyUI has a child
        assert any("Process Radar itself" in t for t in seen["warn"])
        assert any("Shadow" in t for t in seen["warn"])
        # the stop runs on a background thread; the result arrives back on the GUI thread
        assert pump(qapp, lambda: getattr(w, "last_stop_box", None) is not None)
        assert calls and set(calls[0][0]) == {8304, 10336} and calls[0][1] is True
        assert calls[0][2] == {8304: w.snap.procs[8304].create_time, 10336: w.snap.procs[10336].create_time}
        assert not w.stop_job.busy
        box = w.last_stop_box
        assert "2 stopped, 1 access denied" in box.text()
        assert "access denied" in box.informativeText() and "child of 8304" in box.informativeText()
        box.close()
    finally:
        app.shutdown()
        app.win.deleteLater()


def test_ctrl_click_card_stops_service(qapp, tmpenv, monkeypatch):
    from PySide6.QtTest import QTest
    pr, app = make_app(qapp, tmpenv)
    try:
        app.show()
        assert pump(qapp, lambda: app.win.snap is not None)
        w = app.win
        got = []
        monkeypatch.setattr(w, "stop_pids", lambda pids, title="": got.append((list(pids), title)))
        # signal was connected to the bound method before patching -> call through stop_service
        monkeypatch.setattr(w.cards["ollama"], "stop_requested", w.cards["ollama"].stop_requested)
        w.cards["ollama"].stop_requested.disconnect()
        w.cards["ollama"].stop_requested.connect(w.stop_service)
        QTest.mouseClick(w.cards["ollama"], Qt.LeftButton, Qt.ControlModifier)
        assert got == [([14672, 15000], "Stop Ollama?")]
        assert w.selected_service is None                      # Ctrl+click does not filter
        QTest.mouseClick(w.cards["python"], Qt.LeftButton, Qt.ControlModifier)   # group card: just filters
        assert len(got) == 1 and w.selected_service == "python"
    finally:
        app.shutdown()
        app.win.deleteLater()


def test_stop_dialog_real_widget(qapp):
    import process_radar as pr
    import radar_core as rc
    snap = meneer_snapshot()
    dlg = pr.StopDialog(rc.stop_plan([8304, 1316], snap.procs, self_pid=None))
    assert dlg.table.rowCount() == 2 and dlg.tree.isChecked()
    assert dlg.stop_btn.text() == "Stop 2" and dlg.windowTitle() == "Stop processes?"
    one = pr.StopDialog(rc.stop_plan([30000], snap.procs))
    assert one.stop_btn.text() == "Stop" and not one.tree.isChecked()



def test_defender_add_dialog_and_apply_flow(qapp, tmpenv, monkeypatch):
    from PySide6.QtWidgets import QDialog
    import defender_ui as defui
    import radar_core as rc
    import json as _json
    pr, app = make_app(qapp, tmpenv)
    try:
        app.show()
        assert pump(qapp, lambda: app.win.snap is not None)
        w = app.win
        targets = rc.defender_targets([w.snap.procs[8304], w.snap.procs[14672], w.snap.procs[9196]])
        assert {t.name.lower() for t in targets} == {"python.exe", "ollama.exe", "node.exe"}
        dlg = defui.AddExclusionsDialog(targets, w)
        py_i = next(i for i, t in enumerate(targets) if t.is_interpreter and "python" in t.name.lower())
        assert dlg.folder_cbs[py_i].isChecked() and dlg.proc_cbs[py_i].isChecked()
        ol_i = next(i for i, t in enumerate(targets) if t.name.lower() == "ollama.exe")
        assert not dlg.folder_cbs[ol_i].isChecked()
        change = dlg.change()
        assert any(p.lower().endswith("python.exe") for p in change.process_paths)
        assert change.folder_paths  # interpreter folder ticked

        # shield indicator from cache
        app.def_cache.processes = [w.snap.procs[14672].exe]
        app.def_cache.updated = 1
        w.model.excluded = app.def_cache.covers
        src = next(i for i, r in enumerate(w.model.rows) if r[0].pid == 14672)
        assert w.model.index(src, pr.C_NAME).data(Qt.DecorationRole) is not None
        assert "Excluded from Microsoft Defender" in w.model.index(src, pr.C_NAME).data(Qt.ToolTipRole)

        # apply flow with mocked dialog + elevated job
        monkeypatch.setattr(defui.AddExclusionsDialog, "exec", lambda self: QDialog.Accepted)
        monkeypatch.setattr(defui.AddExclusionsDialog, "change",
                            lambda self: rc.ExclusionChange([w.snap.procs[14672].exe], []))
        ran = []

        class J:
            busy = False
            result = app.def_job.result

            class Sig:
                def __init__(self):
                    self.slots = []
                def connect(self, fn):
                    self.slots.append(fn)
                def disconnect(self, fn):
                    if fn in self.slots:
                        self.slots.remove(fn)

            def __init__(self):
                self.done = J.Sig()
                self.status = J.Sig()

            def run(self, script, hwnd=0, timeout_s=90):
                ran.append(script)
                res = rc.parse_defender_result(_json.dumps({
                    "ok": True, "action": "add", "elevated": True,
                    "added": [{"type": "process", "value": w.snap.procs[14672].exe}],
                    "removed": [], "errors": [],
                    "exclusionProcess": [w.snap.procs[14672].exe, r"C:\other.exe"],
                    "exclusionPath": [], "tamper": True,
                    "defender": {"service": True, "antivirus": True, "mode": "Normal"},
                }))
                for fn in list(self.done.slots):
                    fn(res)
                return True

        app.def_job = J()
        monkeypatch.setattr(pr.QMessageBox, "exec", lambda self: 0)
        monkeypatch.setattr(pr.QMessageBox, "information", lambda *a, **k: 0)
        w.select_pids([14672])
        w.act_defender()
        assert ran and "Add-MpPreference -ExclusionProcess" in ran[0]
        assert app.def_cache.covers(w.snap.procs[14672].exe)
        assert r"C:\other.exe" in app.def_cache.processes
    finally:
        app.shutdown()
        app.win.deleteLater()


def test_defender_manager_remove(qapp, tmpenv, monkeypatch):
    import defender_ui as defui
    import radar_core as rc
    import json as _json
    pr, app = make_app(qapp, tmpenv)
    try:
        app.def_cache.processes = [r"C:\a.exe", r"C:\b.exe"]
        app.def_cache.paths = [r"D:\Tools"]
        app.def_cache.updated = 1
        app.def_cache.save()
        dlg = defui.ManageExclusionsDialog(app.def_cache, app.def_job, app.win)
        assert dlg.table.rowCount() == 3
        ran = []

        class J:
            busy = False
            result = app.def_job.result

            class Sig:
                def __init__(self):
                    self.slots = []
                def connect(self, fn):
                    self.slots.append(fn)
                def disconnect(self, fn):
                    if fn in self.slots:
                        self.slots.remove(fn)

            def __init__(self):
                self.done = J.Sig()
                self.status = J.Sig()

            def run(self, script, hwnd=0, timeout_s=90):
                ran.append(script)
                res = rc.parse_defender_result(_json.dumps({
                    "ok": True, "action": "remove", "elevated": True,
                    "added": [], "removed": [{"type": "process", "value": r"C:\a.exe"}],
                    "errors": [], "exclusionProcess": [r"C:\b.exe"], "exclusionPath": [r"D:\Tools"],
                    "tamper": True, "defender": {"service": True, "antivirus": True, "mode": "Normal"},
                }))
                for fn in list(self.done.slots):
                    fn(res)
                return True

        dlg.job = J()
        dlg.table.selectRow(0)
        monkeypatch.setattr(pr.QMessageBox, "question", lambda *a, **k: pr.QMessageBox.Yes)
        monkeypatch.setattr(pr.QMessageBox, "exec", lambda self: 0)
        monkeypatch.setattr(pr.QMessageBox, "information", lambda *a, **k: 0)
        dlg.remove_selected()
        assert ran and "Remove-MpPreference -ExclusionProcess" in ran[0]
        assert app.def_cache.processes == [r"C:\b.exe"] and dlg.table.rowCount() == 2
        dlg.close()
    finally:
        app.shutdown()
        app.win.deleteLater()


def test_defender_dialog_shows_full_path_with_elide_middle(qapp, tmpenv):
    """Name is bold/short; Path holds the full exe; view elides in the middle so drive+filename stay."""
    from PySide6.QtCore import Qt
    import defender_ui as defui
    import radar_core as rc
    pr, app = make_app(qapp, tmpenv)
    try:
        app.show()
        assert pump(qapp, lambda: app.win.snap is not None)
        targets = rc.defender_targets([app.win.snap.procs[8304], app.win.snap.procs[14672]])
        dlg = defui.AddExclusionsDialog(targets, app.win)
        assert dlg.table.columnCount() == 5
        assert [dlg.table.horizontalHeaderItem(c).text() for c in range(5)] == [
            "Exclude", "Name", "Path", "Also exclude folder", "Used by"]
        assert dlg.table.textElideMode() == Qt.ElideMiddle
        for r, tg in enumerate(targets):
            assert dlg.table.item(r, 1).text() == tg.name
            assert dlg.table.item(r, 1).font().bold()
            assert dlg.table.item(r, 2).text() == tg.exe          # full path, never "C:..."
            assert dlg.table.item(r, 2).toolTip().startswith(tg.exe)
            assert "ComfyUI" in dlg.table.item(r, 2).text() or "Ollama" in dlg.table.item(r, 2).text() \
                or "ollama" in dlg.table.item(r, 2).text().lower() or "python" in dlg.table.item(r, 2).text().lower()
        # Manage list also elides in the middle and keeps the full value in the tooltip
        app.def_cache.processes = [r"C:\Users\Shadow\AppData\Local\Programs\Ollama\ollama.exe"]
        app.def_cache.updated = 1
        mgr = defui.ManageExclusionsDialog(app.def_cache, app.def_job, app.win)
        assert mgr.table.textElideMode() == Qt.ElideMiddle
        assert mgr.table.item(0, 1).text().endswith("ollama.exe")
        assert mgr.table.item(0, 1).toolTip().endswith("ollama.exe")
        # main table
        assert app.win.table.textElideMode() == Qt.ElideMiddle
        dlg.close(); mgr.close()
    finally:
        app.shutdown()
        app.win.deleteLater()

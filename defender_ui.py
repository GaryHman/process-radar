"""Process Radar - Microsoft Defender exclusion dialogs and elevated PowerShell runner.

Keep UI English. Pure logic lives in radar_core (script generation, parsing); this module only
handles the Qt dialogs and the elevated-helper dance.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import logging
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

import radar_collect as rcol
import radar_core as rc

log = logging.getLogger("processradar")
C_MUTED, C_AMBER, C_RED, C_LINK = "#8b98a5", "#fbbf24", "#f87171", "#60a5fa"


# ----------------------------------------------------------------------------- cache
class ExclusionCache:
    """Last-known Defender exclusion lists (written after every successful elevated call)."""

    def __init__(self, path: Path):
        self.path = path
        self.processes: list[str] = []
        self.paths: list[str] = []
        self.updated: float = 0.0
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.processes = list(data.get("processes") or [])
            self.paths = list(data.get("paths") or [])
            self.updated = float(data.get("updated") or 0)
        except (OSError, ValueError, TypeError):
            self.processes, self.paths, self.updated = [], [], 0.0

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"processes": self.processes, "paths": self.paths,
                                   "updated": self.updated}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def update_from(self, res: rc.DefenderResult) -> None:
        """Refresh the cache from a result whose exclusion lists were readable."""
        if res.denied or res.action not in ("add", "remove", "read"):
            return
        # even an empty list is a successful read
        self.processes = list(res.exclusion_process)
        self.paths = list(res.exclusion_path)
        self.updated = time.time()
        self.save()

    def covers(self, exe: str) -> bool:
        return rc.exe_in_exclusions(exe, self.processes, self.paths)

    def age_text(self) -> str:
        if not self.updated:
            return "never loaded"
        return "loaded " + time.strftime("%Y-%m-%d %H:%M", time.localtime(self.updated))


# ----------------------------------------------------------------------------- elevated runner
class ElevatedJob(QObject):
    """Write a .ps1, launch ONE elevated PowerShell, wait for the result JSON, emit done."""
    done = Signal(object)              # DefenderResult | None (None = cancelled / timed out)
    status = Signal(str)

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        self.script = data_dir / "defender-apply.ps1"
        self.result = data_dir / "defender-result.json"
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._handle = None
        self._deadline = 0.0
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def run(self, script_text: str, hwnd: int = 0, timeout_s: float = 90.0) -> bool:
        if self._busy:
            return False
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.result.unlink(missing_ok=True)
        except OSError:
            pass
        self.script.write_text(script_text, encoding="utf-8-sig", newline="\r\n")
        self.status.emit("Waiting for the Windows UAC prompt…")
        status, handle = rcol.launch_elevated_powershell(str(self.script), hwnd)
        if status == "cancelled":
            self.status.emit("UAC declined")
            self.done.emit(None)
            return True
        if status != "started":
            self.status.emit(status)
            self.done.emit(rc.DefenderResult(ok=False, action="", added=[], removed=[],
                                             errors=[status], exclusion_process=[], exclusion_path=[]))
            return True
        self._handle = handle
        self._deadline = time.time() + timeout_s
        self._busy = True
        self.status.emit("Elevated PowerShell is running…")
        self._timer.start()
        return True

    def _tick(self) -> None:
        if self.result.exists() and self.result.stat().st_size > 2:
            try:
                text = self.result.read_text(encoding="utf-8-sig")
                res = rc.parse_defender_result(text)
            except (OSError, ValueError) as e:
                res = rc.DefenderResult(ok=False, action="", added=[], removed=[],
                                        errors=[f"could not read result: {e}"],
                                        exclusion_process=[], exclusion_path=[])
            self._finish(res)
            return
        code = rcol.wait_process_handle(self._handle, 0) if self._handle else None
        if code is not None:
            self._handle = None
            # process ended; give it a moment for the file to flush
            if time.time() > self._deadline - 88:   # already waited a bit
                pass
        if time.time() > self._deadline:
            self._finish(rc.DefenderResult(ok=False, action="", added=[], removed=[],
                                           errors=["Timed out waiting for the elevated PowerShell."],
                                           exclusion_process=[], exclusion_path=[]))

    def _finish(self, res) -> None:
        self._timer.stop()
        if self._handle:
            rcol.wait_process_handle(self._handle, 0)
            self._handle = None
        self._busy = False
        self.done.emit(res)


# ----------------------------------------------------------------------------- add dialog
class AddExclusionsDialog(QDialog):
    """Pick which unique executables of the selection to exclude, and how."""

    def __init__(self, targets: list, parent=None, title: str = ""):
        super().__init__(parent)
        self.targets = list(targets)
        self.setWindowTitle(title or "Add to Defender exclusions")
        # wide enough that drive + filename stay readable; cap at ~90% of the screen
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        ag = scr.availableGeometry() if scr else None
        aw = ag.width() if ag else 1400
        # prefer a wide dialog so long Windows paths stay readable (ElideMiddle needs room)
        want = min(max(1100, int(aw * 0.92)), max(aw - 24, 1100))
        self.resize(want, 520)
        self.setMinimumWidth(min(1000, max(aw - 80, 720)))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "<b>Add selected executables to Microsoft Defender exclusions</b><br>"
            "<span style='color:#8b98a5'>Windows Security → Virus &amp; threat protection → Exclusions. "
            "Requires administrator approval (one UAC prompt).</span>"))
        note = QLabel(
            "<b>Process exclusion</b> – Defender does not scan files opened by this program "
            f"(<code>Add-MpPreference -ExclusionProcess</code>).<br>"
            "<b>Also exclude its folder</b> – Defender does not scan anything inside the program's folder "
            f"(<code>-ExclusionPath</code>). More useful for interpreters like python.exe.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{C_MUTED}; font-size:9pt;")
        lay.addWidget(note)

        t = self.table = QTableWidget(len(targets), 5)
        t.setHorizontalHeaderLabels(["Exclude", "Name", "Path", "Also exclude folder", "Used by"])
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setShowGrid(False)
        t.setTextElideMode(Qt.ElideMiddle)          # keep drive + filename visible
        t.setWordWrap(False)
        self.proc_cbs, self.folder_cbs = [], []
        bold = QFont(); bold.setBold(True)
        for r, tg in enumerate(targets):
            cb = QCheckBox()
            cb.setChecked(True)
            t.setCellWidget(r, 0, self._center(cb))
            self.proc_cbs.append(cb)
            name = QTableWidgetItem(tg.name)
            name.setFont(bold)
            name.setToolTip(tg.exe)
            if tg.is_interpreter:
                name.setForeground(QColor(C_AMBER))
            t.setItem(r, 1, name)
            path = QTableWidgetItem(tg.exe)
            tip = tg.exe + (("\n\n⚠ " + tg.warning) if tg.warning else "")
            path.setToolTip(tip)
            path.setForeground(QColor(C_AMBER if tg.is_interpreter else C_MUTED))
            t.setItem(r, 2, path)
            fb = QCheckBox("folder")
            fb.setChecked(tg.is_interpreter)          # interpreters: prefer the folder option
            fb.setToolTip(tg.folder or "(no folder)")
            t.setCellWidget(r, 3, self._center(fb))
            self.folder_cbs.append(fb)
            used = ", ".join(f"{n} ({pid})" for pid, n in tg.procs[:4])
            if len(tg.procs) > 4:
                used += f" +{len(tg.procs) - 4}"
            t.setItem(r, 4, QTableWidgetItem(used))
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)   # bold filename
        hh.setSectionResizeMode(2, QHeaderView.Stretch)            # full path, ElideMiddle
        hh.setMinimumSectionSize(64)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        # give Path most of the width up front (Stretch alone can leave it tiny before show)
        other = 70 + 160 + 140 + 160   # Exclude + Name + folder + Used by (approx)
        hh.resizeSection(2, max(560, self.width() - other - 48))
        t.setMinimumHeight(min(320, 40 + 28 * max(1, len(targets))))
        lay.addWidget(t)

        if any(tg.is_interpreter for tg in targets):
            w = QLabel("⚠ One or more selected executables are generic interpreters (python, node, …). "
                       "A process exclusion for them covers every script they run – the folder option is safer.")
            w.setWordWrap(True)
            w.setStyleSheet(f"color:{C_AMBER};")
            lay.addWidget(w)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{C_MUTED};")
        lay.addWidget(self.status)

        bb = QDialogButtonBox()
        self.apply_btn = bb.addButton("Add (admin)…", QDialogButtonBox.AcceptRole)
        self.apply_btn.setObjectName("danger")
        cancel = bb.addButton(QDialogButtonBox.Cancel)
        cancel.setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _center(w: QWidget) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(8, 0, 8, 0)
        h.addStretch(1)
        h.addWidget(w)
        h.addStretch(1)
        return wrap

    def change(self) -> rc.ExclusionChange:
        chosen = {self.targets[i].exe for i, cb in enumerate(self.proc_cbs) if cb.isChecked()}
        folders = {self.targets[i].exe for i, cb in enumerate(self.folder_cbs) if cb.isChecked()}
        return rc.selection_to_change(self.targets, chosen, folders)


# ----------------------------------------------------------------------------- manager dialog
class ManageExclusionsDialog(QDialog):
    """View / remove current Defender exclusions (Load requires admin)."""

    def __init__(self, cache: ExclusionCache, job: ElevatedJob, parent=None):
        super().__init__(parent)
        self.cache = cache
        self.job = job
        self.setWindowTitle("Defender exclusions")
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        aw = scr.availableGeometry().width() if scr else 1400
        self.resize(min(max(880, int(aw * 0.85)), aw - 40), 520)
        self.setMinimumSize(720, 480)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.info = QLabel("")
        self.info.setWordWrap(True)
        top.addWidget(self.info, 1)
        self.load_btn = QPushButton("Load (admin)…")
        self.load_btn.setToolTip("Ask Windows for the current exclusion list (one UAC prompt)")
        self.load_btn.clicked.connect(self.load)
        top.addWidget(self.load_btn)
        lay.addLayout(top)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Type", "Value", "Remove"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{C_MUTED};")
        lay.addWidget(self.status)

        bot = QHBoxLayout()
        self.remove_btn = QPushButton("Remove selected (admin)…")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self.remove_selected)
        bot.addWidget(self.remove_btn)
        bot.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bot.addWidget(close)
        lay.addLayout(bot)

        self._fill()

    def _fill(self) -> None:
        rows = [("process", v) for v in self.cache.processes] + [("path", v) for v in self.cache.paths]
        self.table.setRowCount(len(rows))
        for r, (typ, val) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(typ))
            val_item = QTableWidgetItem(val)
            val_item.setToolTip(val)                  # full path always, even when ElideMiddle shortens it
            self.table.setItem(r, 1, val_item)
            self.table.setItem(r, 2, QTableWidgetItem(""))
            self.table.item(r, 0).setData(Qt.UserRole, typ)
            self.table.item(r, 1).setData(Qt.UserRole, val)
        n = len(rows)
        self.info.setText(f"<b>{n} exclusion(s)</b> in the last-known list ({self.cache.age_text()}). "
                          "Click Load to refresh from Windows.")
        self.remove_btn.setEnabled(n > 0)

    def _run(self, script: str) -> None:
        if self.job.busy:
            return
        self.load_btn.setEnabled(False)
        self.remove_btn.setEnabled(False)
        self.status.setText("Waiting for the Windows UAC prompt…")
        self.job.done.connect(self._on_done)
        self.job.status.connect(self.status.setText)
        if not self.job.run(script, hwnd=int(self.winId())):
            self._unlock()

    def load(self) -> None:
        self._run(rc.build_read_script(str(self.job.result)))

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Defender exclusions", "Select one or more exclusions to remove.")
            return
        procs, paths = [], []
        for r in rows:
            typ = self.table.item(r, 0).data(Qt.UserRole)
            val = self.table.item(r, 1).data(Qt.UserRole)
            (procs if typ == "process" else paths).append(val)
        if QMessageBox.question(self, "Remove exclusions?",
                                f"Remove {len(rows)} exclusion(s) from Microsoft Defender?\n\n"
                                + "\n".join((procs + paths)[:8])
                                + ("\n…" if len(rows) > 8 else ""),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._run(rc.build_remove_script(procs, paths, str(self.job.result)))

    def _unlock(self) -> None:
        self.load_btn.setEnabled(True)
        self.remove_btn.setEnabled(True)
        try:
            self.job.done.disconnect(self._on_done)
        except RuntimeError:
            pass

    def _on_done(self, res) -> None:
        self._unlock()
        if res is None:
            self.status.setText("Cancelled – no changes.")
            return
        self.cache.update_from(res)
        self._fill()
        if res.action == "read" and not res.errors:
            self.status.setText(
                f"Loaded {len(res.exclusion_process)} process + {len(res.exclusion_path)} path exclusion(s).")
            return
        text = rc.summarize_defender(res)
        self.status.setText(text.splitlines()[0] if text else "")
        box = QMessageBox(self)
        box.setWindowTitle("Defender exclusions")
        box.setIcon(QMessageBox.Information if res.ok else QMessageBox.Warning)
        box.setText(text.splitlines()[0] if text else "Done.")
        rest = "\n".join(text.splitlines()[1:])
        if rest:
            box.setInformativeText("<pre style='font-family:Consolas,monospace'>"
                                   + rest.replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
        box.exec()

"""Process Radar - see at a glance what is running in the background on this PC.

Known services (Ollama, ComfyUI, LM Studio, ...) with Running/Stopped badges, a GPU summary and a
sortable list of background processes (ports, CPU, RAM, VRAM, uptime) with Open folder / Copy
command line / Stop actions. Global hotkey Ctrl+Alt+P shows/hides the window.

    pythonw process_radar.py               normal start (no console)
    python  process_radar.py --report      print a text snapshot and exit (no window)
    python  process_radar.py --screenshot out.png   save a picture of the window after the first refresh
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import faulthandler
import gc
import getpass
import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
_APP_NAME = "Process Radar"


def data_dir() -> Path:
    d = Path(os.environ.get("PR_DATA_DIR") or Path(os.environ.get("APPDATA", str(Path.home()))) / "ProcessRadar")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _startup_failure(exc: BaseException) -> None:
    """pythonw has no console: log the reason and show a message box instead of exiting silently."""
    import ctypes
    import datetime
    msg = (f"{_APP_NAME} could not start because a required Python package is missing or broken:\n\n"
           f"{exc}\n\nPython used: {sys.executable}\n\n"
           "Install it with:  py -m pip install --user psutil PySide6-Essentials")
    try:
        with open(data_dir() / "radar.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} ERROR Startup failed: {exc!r} "
                    f"(python: {sys.executable})\n")
    except OSError:
        pass
    if sys.stderr is not None:
        sys.stderr.write(msg + "\n")
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, msg, _APP_NAME, 0x10)
        except Exception:  # noqa: BLE001
            pass
    sys.exit(1)


try:
    import radar_core as rc
    import radar_collect as rcol
except ImportError as _exc:  # pragma: no cover - only on a broken install
    _startup_failure(_exc)


def _report(show_all: bool) -> int:
    col = rcol.Collector()
    col.snapshot()
    time.sleep(1.0)
    snap = col.snapshot()
    text = rc.report_text(snap, time.time(), show_all)
    sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
    return 0


if __name__ == "__main__" and "--report" in sys.argv:
    sys.exit(_report("--all" in sys.argv))

try:
    from PySide6.QtCore import (QAbstractTableModel, QItemSelection, QItemSelectionModel, QLockFile,
                                QModelIndex, QObject, QPoint, QRectF,
                                QSortFilterProxyModel, Qt, QThread, QTimer, QUrl, Signal, Slot)
    from PySide6.QtGui import (QAction, QBrush, QColor, QCursor, QDesktopServices, QFont, QGuiApplication,
                               QIcon, QPainter, QPen, QPixmap, QConicalGradient)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox,
                                   QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                                   QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                                   QSpinBox, QSystemTrayIcon, QTableView, QTableWidget, QTableWidgetItem,
                                   QVBoxLayout, QWidget)

    import win_hotkeys as hk
    import defender_ui as defui
except ImportError as _exc:  # pragma: no cover
    _startup_failure(_exc)

log = logging.getLogger("processradar")
APP_TITLE = rc.APP_TITLE

C_BG, C_PANEL, C_PANEL2, C_BORDER = "#0d1117", "#151b23", "#1b2430", "#263241"
C_TEXT, C_MUTED, C_GREEN, C_GREEN_D = "#e6edf3", "#8b98a5", "#22c55e", "#15803d"
C_RED, C_LINK, C_AMBER = "#f87171", "#60a5fa", "#fbbf24"

QSS = f"""
QWidget {{ background:{C_BG}; color:{C_TEXT}; font-family:'Segoe UI'; font-size:10pt; }}
QToolTip {{ background:{C_PANEL2}; color:{C_TEXT}; border:1px solid {C_BORDER}; padding:4px; }}
QFrame#card {{ background:{C_PANEL}; border:1px solid {C_BORDER}; border-radius:10px; }}
QFrame#card[running="true"] {{ border:1px solid {C_GREEN_D}; background:#122019; }}
QFrame#card[selected="true"] {{ border:2px solid {C_LINK}; }}
QFrame#card QLabel {{ background:transparent; }}
QLabel#title {{ font-size:15pt; font-weight:600; }}
QLabel#section {{ color:{C_MUTED}; font-size:9pt; font-weight:600; letter-spacing:1px; }}
QLabel#muted {{ color:{C_MUTED}; font-size:9pt; }}
QFrame#card QLabel#badgeOn, QLabel#badgeOn {{ background:{C_GREEN}; color:#052e16; border-radius:8px; padding:1px 8px; font-size:8pt; font-weight:700; }}
QFrame#card QLabel#badgeOff, QLabel#badgeOff {{ background:#2b3440; color:#9aa7b4; border-radius:8px; padding:1px 8px; font-size:8pt; font-weight:700; }}
QLineEdit, QSpinBox, QKeySequenceEdit {{ background:{C_PANEL}; border:1px solid {C_BORDER}; border-radius:6px; padding:5px 8px; }}
QLineEdit:focus {{ border:1px solid {C_GREEN}; }}
QPushButton {{ background:{C_PANEL2}; border:1px solid {C_BORDER}; border-radius:6px; padding:5px 12px; }}
QPushButton:hover {{ border:1px solid {C_GREEN}; }}
QPushButton:disabled {{ color:#4b5563; border:1px solid #1f2937; }}
QPushButton#danger {{ color:{C_RED}; }}
QPushButton#danger:hover {{ border:1px solid {C_RED}; }}
QPushButton:checked {{ background:#3b2f0b; border:1px solid {C_AMBER}; color:{C_AMBER}; }}
QCheckBox {{ background:transparent; }}
QTableView {{ background:{C_PANEL}; alternate-background-color:#18202a; border:1px solid {C_BORDER};
             border-radius:8px; gridline-color:transparent; selection-background-color:#1f3b2b;
             selection-color:{C_TEXT}; }}
QTableView::item:selected, QTableView::item:selected:!active {{ background:#1f3b2b; color:{C_TEXT}; }}
QHeaderView::section {{ background:{C_PANEL2}; color:{C_MUTED}; border:none; border-bottom:1px solid {C_BORDER};
                        padding:6px; font-weight:600; }}
QProgressBar {{ background:{C_PANEL}; border:1px solid {C_BORDER}; border-radius:7px; text-align:center;
                color:{C_TEXT}; height:18px; font-size:9pt; }}
QProgressBar::chunk {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {C_GREEN_D}, stop:1 {C_GREEN});
                       border-radius:6px; }}
QMenu {{ background:{C_PANEL2}; border:1px solid {C_BORDER}; }}
QMenu::item {{ padding:5px 18px; }}
QMenu::item:selected {{ background:#1f3b2b; }}
QScrollBar:vertical {{ background:{C_PANEL}; width:10px; }}
QScrollBar::handle:vertical {{ background:{C_BORDER}; border-radius:5px; min-height:30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
QStatusBar {{ color:{C_MUTED}; }}
QGroupBox {{ border:1px solid {C_BORDER}; border-radius:8px; margin-top:14px; padding-top:8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left:10px; color:{C_MUTED}; }}
"""


# ----------------------------------------------------------------------------- helpers
def setup_logging(directory: Path) -> None:
    handler = RotatingFileHandler(directory / "radar.log", maxBytes=512_000, backupCount=2, encoding="utf-8",
                                  delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    if sys.stderr is not None:
        log.addHandler(logging.StreamHandler())


_crash_file = None


def install_crash_handlers(directory: Path) -> None:
    """Never let a bug close the app silently.

    * Python exceptions in Qt slots, timers and threads are logged (with traceback) and the app keeps
      running - pythonw has no console, so without this they would vanish or end the process.
    * A native crash (inside Qt) writes the Python stack of every thread to crash.log via faulthandler,
      so the cause can be found afterwards."""
    global _crash_file

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error("Unhandled error (Process Radar keeps running)", exc_info=(exc_type, exc, tb))
        win = getattr(hook, "window", None)
        if win is not None:
            try:
                win.statusBar().showMessage(f"Something went wrong: {exc} (details in radar.log)", 10000)
            except Exception:  # noqa: BLE001 - the window may be gone already
                pass

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log.error("Unhandled error in thread %s", getattr(args.thread, "name", "?"),
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook
    try:
        _crash_file = open(directory / "crash.log", "a", encoding="utf-8")
        _crash_file.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} started\n")
        _crash_file.flush()
        faulthandler.enable(_crash_file, all_threads=True)
    except OSError:
        pass
    install_crash_handlers.hook = hook


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(json.dumps(rc.DEFAULT_SETTINGS))
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        return self.data.get(k, rc.DEFAULT_SETTINGS.get(k))

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def draw_icon_pixmap(size: int) -> QPixmap:
    """Dark rounded square with a green radar: rings, sweep and two blips."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0b1f14"))
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 13 * s, 13 * s)
    c = 32 * s
    grad = QConicalGradient(c, c, 90)
    grad.setColorAt(0.0, QColor(34, 197, 94, 230))
    grad.setColorAt(0.18, QColor(34, 197, 94, 40))
    grad.setColorAt(0.19, QColor(34, 197, 94, 0))
    grad.setColorAt(1.0, QColor(34, 197, 94, 0))
    p.setBrush(QBrush(grad))
    p.drawEllipse(QRectF(c - 25 * s, c - 25 * s, 50 * s, 50 * s))
    pen = QPen(QColor("#22c55e"))
    pen.setWidthF(max(1.0, 2.6 * s))
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for r in (25, 16.5, 8):
        p.drawEllipse(QRectF(c - r * s, c - r * s, 2 * r * s, 2 * r * s))
    pen.setWidthF(max(1.0, 3.2 * s))
    pen.setColor(QColor("#86efac"))
    p.setPen(pen)
    p.drawLine(int(c), int(c), int(c), int(c - 25 * s))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#bbf7d0"))
    p.drawEllipse(QRectF(c + 9 * s, c - 15 * s, 6.5 * s, 6.5 * s))
    p.setBrush(QColor("#4ade80"))
    p.drawEllipse(QRectF(c - 17 * s, c + 7 * s, 5 * s, 5 * s))
    p.end()
    return pm


def draw_shield_pixmap(size: int) -> QPixmap:
    """Small blue shield with a white tick: 'excluded from Microsoft Defender'."""
    from PySide6.QtGui import QPainterPath
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 16.0
    path = QPainterPath()
    path.moveTo(8 * s, 1 * s)
    path.lineTo(14 * s, 3.2 * s)
    path.cubicTo(14 * s, 9 * s, 11.5 * s, 13 * s, 8 * s, 15 * s)
    path.cubicTo(4.5 * s, 13 * s, 2 * s, 9 * s, 2 * s, 3.2 * s)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#3b82f6"))
    p.drawPath(path)
    pen = QPen(QColor("white"))
    pen.setWidthF(max(1.0, 1.7 * s))
    p.setPen(pen)
    p.drawLine(int(5 * s), int(8 * s), int(7.3 * s), int(10.3 * s))
    p.drawLine(int(7.3 * s), int(10.3 * s), int(11 * s), int(5.8 * s))
    p.end()
    return pm


def app_icon() -> QIcon:
    ico = APP_DIR / "icon.ico"
    if ico.exists():
        icon = QIcon(str(ico))
        if not icon.isNull():
            return icon
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(draw_icon_pixmap(sz))
    return icon


def open_folder(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    return True


# ----------------------------------------------------------------------------- data thread
class Worker(QObject):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, collector_factory):
        super().__init__()
        self._factory = collector_factory
        self._collector = None

    @Slot()
    def collect(self):
        try:
            if self._collector is None:
                self._collector = self._factory()
            self.ready.emit(self._collector.snapshot())
        except Exception as e:  # noqa: BLE001
            log.exception("collect failed")
            self.failed.emit(str(e))


class StopJob(QObject):
    """Stops processes on a background thread, so the window stays responsive while programs are asked
    to close (up to ~5 s) - a frozen window is what Windows offers to 'close the program' for."""
    finished = Signal(object, object, object)    # results {pid: state}, names {pid: str}, error str | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False

    def start(self, pids, tree: bool, names: dict, expected: dict) -> bool:
        if self.busy:
            return False
        self.busy = True

        def run():
            res, err = {}, None
            try:
                res = rcol.stop_many(pids, tree, timeout=3.0, expected=expected)
            except Exception as e:  # noqa: BLE001 - never let a stop take the app down
                log.exception("stop failed")
                err = str(e)
            self.finished.emit(res, names, err)

        threading.Thread(target=run, name="stop-processes", daemon=True).start()
        return True


# ----------------------------------------------------------------------------- table model
COLS = ["Name", "Label", "PID", "Command line", "Ports", "CPU %", "RAM", "VRAM", "Uptime"]
C_NAME, C_LABEL, C_PID, C_CMD, C_PORTS, C_CPU, C_RAM, C_VRAM, C_UP = range(len(COLS))
SORT_ROLE = Qt.UserRole + 1
PID_ROLE = Qt.UserRole + 2
SEARCH_ROLE = Qt.UserRole + 3
KEY_ROLE = Qt.UserRole + 4          # (pid, create_time): identifies a process even if its PID is reused


class ProcModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []           # [(ProcInfo, why, label)]
        self.now = time.time()
        self.has_vram = False
        self.excluded = lambda exe: False          # set by the window from the Defender cache
        self.launchers: dict = {}                  # pid -> (label, pid) of the app that started it
        self._shield = None
        self._link_font = QFont()
        self._link_font.setUnderline(True)

    def set_rows(self, rows, now: float, has_vram: bool, procs: dict | None = None) -> None:
        """Incremental update (remove exited, update in place, append new) so the view's selection and
        current row survive every auto-refresh."""
        self.now, self.has_vram = now, has_vram
        if procs is not None:
            own = {r[0].pid: (rc.service_of(r[0]).key if rc.service_of(r[0]) else None) for r in rows}
            self.launchers = {pid: rc.launcher_of(r[0], procs, own[pid]) for pid, r in
                              ((r[0].pid, r) for r in rows)}
        new_by_key = {(r[0].pid, r[0].create_time): r for r in rows}
        for i in range(len(self.rows) - 1, -1, -1):
            p = self.rows[i][0]
            if (p.pid, p.create_time) not in new_by_key:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.rows[i]
                self.endRemoveRows()
        existing = set()
        for i, r in enumerate(self.rows):
            k = (r[0].pid, r[0].create_time)
            self.rows[i] = new_by_key[k]
            existing.add(k)
        add = [r for r in rows if (r[0].pid, r[0].create_time) not in existing]
        if add:
            n = len(self.rows)
            self.beginInsertRows(QModelIndex(), n, n + len(add) - 1)
            self.rows.extend(add)
            self.endInsertRows()
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section]
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole and section == C_VRAM:
            return "Dedicated GPU memory per process"
        return None

    def proc(self, row: int):
        return self.rows[row][0]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        p, why, label = self.rows[index.row()]
        col = index.column()
        if role == PID_ROLE:
            return p.pid
        if role == KEY_ROLE:
            return (p.pid, p.create_time)
        if role == SEARCH_ROLE:
            extra = " defender excluded" if self.excluded(p.exe) else ""
            return " ".join([p.name, label, str(p.pid), p.cmd_text, " ".join(map(str, p.ports))]).lower() + extra
        if role == Qt.DecorationRole and col == C_NAME and self.excluded(p.exe):
            if self._shield is None:
                self._shield = QIcon(draw_shield_pixmap(16))
            return self._shield
        if role == Qt.DisplayRole:
            if col == C_NAME:
                return p.name
            if col == C_LABEL:
                return label
            if col == C_PID:
                return str(p.pid)
            if col == C_CMD:
                # keep drive/start + filename/end; the view uses ElideMiddle for any leftover overflow
                return rc.short_cmdline(p, 160)
            if col == C_PORTS:
                ports = p.ports
                return ", ".join(map(str, ports[:4])) + (" …" if len(ports) > 4 else "")
            if col == C_CPU:
                return f"{p.cpu:.1f}"
            if col == C_RAM:
                return rc.format_bytes(p.rss)
            if col == C_VRAM:
                return rc.format_bytes(p.vram) if (p.vram and self.has_vram) else ("0" if self.has_vram else "–")
            if col == C_UP:
                return rc.format_uptime(self.now - p.create_time) if p.create_time else "–"
        if role == SORT_ROLE:
            return {C_NAME: p.name.lower(), C_LABEL: label.lower(), C_PID: p.pid, C_CMD: p.cmd_text.lower(),
                    C_PORTS: p.ports[0] if p.ports else 1 << 30, C_CPU: p.cpu, C_RAM: p.rss,
                    C_VRAM: p.vram or 0, C_UP: -(p.create_time or 0)}[col]
        if role == Qt.ToolTipRole:
            if col == C_CMD:
                return p.cmd_text or "(command line not readable – process runs as another user)"
            if col == C_NAME:
                tip = f"{p.exe or p.name}\nListed because: {why}\nUser: {p.username or '?'}"
                via, via_pid = self.launchers.get(p.pid, ("", None))
                if via:
                    tip += f"\nStarted by: {via} (PID {via_pid})"
                if self.excluded(p.exe):
                    tip += "\n🛡 Excluded from Microsoft Defender scanning (last known exclusion list)"
                return tip
            if col == C_PORTS and p.listen:
                return "Click to open in your browser:\n" + "\n".join(u for _p, u in rc.urls_for(p))
            if col == C_UP and p.create_time:
                return "Started " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.create_time))
        if role == Qt.ForegroundRole:
            if col == C_PORTS and p.ports:
                return QColor(C_LINK)
            if col in (C_PID, C_UP, C_CMD):
                return QColor(C_MUTED)
            if col == C_LABEL and label:
                return QColor("#86efac")
        if role == Qt.FontRole and col == C_PORTS and p.ports:
            return self._link_font
        if role == Qt.TextAlignmentRole and col in (C_PID, C_CPU, C_RAM, C_VRAM, C_UP):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.needle = ""
        self.pid_filter: set | None = None
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_needle(self, text: str) -> None:
        self._refilter(lambda: setattr(self, "needle", (text or "").strip().lower()))

    def set_pid_filter(self, pids) -> None:
        self._refilter(lambda: setattr(self, "pid_filter", set(pids) if pids is not None else None))

    def _refilter(self, change) -> None:
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            change()
            self.endFilterChange()
        else:
            change()
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        idx = self.sourceModel().index(row, 0, parent)
        if self.pid_filter is not None and idx.data(PID_ROLE) not in self.pid_filter:
            return False
        if not self.needle:
            return True
        hay = idx.data(SEARCH_ROLE) or ""
        return all(w in hay for w in self.needle.split())


# ----------------------------------------------------------------------------- widgets
class ServiceCard(QFrame):
    clicked = Signal(str)
    stop_requested = Signal(str)       # Ctrl+click: stop the whole service
    menu_requested = Signal(str, QPoint)

    def __init__(self, key: str, label: str, hint: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("card")
        self.setProperty("running", False)
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(210)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(3)
        top = QHBoxLayout()
        self.name = QLabel(label)
        self.name.setStyleSheet("font-weight:600; font-size:10.5pt;")
        self.badge = QLabel("STOPPED")
        self.badge.setObjectName("badgeOff")
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.badge)
        lay.addLayout(top)
        self.detail = QLabel(hint)
        self.detail.setObjectName("muted")
        self.hint = hint
        lay.addWidget(self.detail)
        self.links = QLabel("")
        self.links.setTextFormat(Qt.RichText)
        self.links.setOpenExternalLinks(True)
        self.links.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.links.setObjectName("muted")
        lay.addWidget(self.links)

    def update_status(self, st: "rc.ServiceStatus") -> None:
        on = st.running
        self.badge.setText("RUNNING" if on else ("STOPPED" if not st.group else "NONE"))
        self.badge.setObjectName("badgeOn" if on else "badgeOff")
        self.setProperty("running", on)
        detail = st.detail or (self.hint if not on else "")
        if on and not st.detail:
            bits = [] if st.launcher else [f"PID {', '.join(map(str, st.pids[:3]))}{'…' if len(st.pids) > 3 else ''}"]
            if st.uptime is not None:
                bits.append("up " + rc.format_uptime(st.uptime))
            detail = " · ".join(bits)
        if on and st.launcher:
            detail = f"via {st.launcher}" + (" · " + detail if detail else "")
        self.detail.setText(rc.shorten(detail, 48))
        self.detail.setToolTip(detail + (f"\nStarted by {st.launcher} (PID {st.launcher_pid}), not by you directly."
                                         if on and st.launcher else ""))
        if on:
            ports = " ".join(f'<a style="color:{C_LINK}" href="{u}">:{p}</a>' for p, u in st.ports[:4])
            if len(st.ports) > 4:
                ports += " …"
            res = f"RAM {rc.format_bytes(st.rss)}"
            if st.vram is not None:
                res += f" · VRAM {rc.format_bytes(st.vram)}"
            self.links.setText((ports + " &nbsp;" if ports else '<span>no port</span> &nbsp;') +
                               f'<span style="color:{C_MUTED}">{res}</span>')
        else:
            self.links.setText(f'<span style="color:#4b5563">default port {self._port_hint(st.key)}</span>')
        tip = f"{st.label}: {st.hint}\nPIDs: {', '.join(map(str, st.pids)) or '–'}"
        if on and st.launcher:
            tip += f"\nStarted by: {st.launcher} (PID {st.launcher_pid})"
        tip += "\nClick to show only these processes below."
        if on and not st.group:
            tip += "\nCtrl+click to stop the whole service. Right-click for more (Defender exclusions)."

        self.setToolTip(tip)
        self.style().unpolish(self)
        self.style().polish(self)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    @staticmethod
    def _port_hint(key: str) -> str:
        d = rc.SERVICE_BY_KEY[key].default_port
        return str(d) if d else "–"

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def contextMenuEvent(self, e):
        self.menu_requested.emit(self.key, e.globalPos())

    def mousePressEvent(self, e):
        self._pressed = e.button() == Qt.LeftButton
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        # only a real click (press + release inside the card) toggles the filter
        if e.button() == Qt.LeftButton and getattr(self, "_pressed", False) and self.rect().contains(e.position().toPoint()):
            if e.modifiers() & Qt.ControlModifier and not rc.SERVICE_BY_KEY[self.key].group:
                self.stop_requested.emit(self.key)
            else:
                self.clicked.emit(self.key)
        self._pressed = False
        super().mouseReleaseEvent(e)


class SettingsDialog(QDialog):
    manage_defender = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} – Settings")
        lay = QVBoxLayout(self)
        self.hot = hk.HotkeyGroup(settings["hotkeys"] or {},
                                  "Click a field and press a combination such as Ctrl+Alt+P. Leave empty for "
                                  "no shortcut. On the US-International layout Ctrl+Alt+P is also AltGr+P "
                                  "(types ö) – pick another combination if you need that character.")
        lay.addWidget(self.hot)
        form = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setSuffix(" s")
        self.interval.setValue(max(1, int(settings["refresh_ms"] or 3000) // 1000))
        form.addRow("Auto-refresh every:", self.interval)
        lay.addLayout(form)
        def_row = QHBoxLayout()
        def_row.addWidget(QLabel("Microsoft Defender:"))
        btn = QPushButton("🛡  Manage exclusions…")
        btn.setToolTip("View and remove Defender exclusions (Load needs admin)")
        btn.clicked.connect(self.manage_defender.emit)
        def_row.addWidget(btn)
        def_row.addStretch(1)
        lay.addLayout(def_row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class StopDialog(QDialog):
    """One confirmation for one or many processes."""

    def __init__(self, plan: "rc.StopPlan", parent=None, title: str = ""):
        super().__init__(parent)
        n = len(plan.items)
        self.setWindowTitle(title or ("Stop processes?" if n > 1 else "Stop process?"))
        self.setStyleSheet(QSS)
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>Stop {n} process{'es' if n != 1 else ''}</b> "
                      f"<span style='color:{C_MUTED}'>(RAM {rc.format_bytes(plan.total_rss)} in total)</span>"
                      "<br>Unsaved work in these programs will be lost.")
        lay.addWidget(head)
        t = self.table = QTableWidget(n, 5)
        t.setHorizontalHeaderLabels(["Name", "Label", "PID", "RAM", "Note"])
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setShowGrid(False)
        for r, it in enumerate(plan.items):
            note = it.warning
            if it.children:
                note = (note + " · " if note else "") + f"{len(it.children)} child process{'es' if len(it.children) != 1 else ''}"
            vals = [it.name, it.label, str(it.pid), rc.format_bytes(it.rss), note]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c in (2, 3):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 4 and it.warning:
                    cell.setForeground(QColor(C_RED))
                t.setItem(r, c, cell)
        hh = t.horizontalHeader()
        for c in range(4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        t.setMinimumHeight(min(360, 34 + 26 * n))
        lay.addWidget(t)
        for w in plan.warnings:
            lab = QLabel("⚠  " + w)
            lab.setWordWrap(True)
            lab.setStyleSheet(f"color:{C_RED if ('system' in w or 'Shadow' in w) else C_AMBER};")
            lay.addWidget(lab)
        self.tree = QCheckBox("Also stop child processes (kill tree)")
        self.tree.setChecked(plan.any_children)
        kids = sum(len(i.children) for i in plan.items)
        self.tree.setToolTip(f"{kids} child process(es) of the selected processes" if kids else "No child processes")
        lay.addWidget(self.tree)
        hint = QLabel("Each process is asked to close first; anything still running after 3 seconds is killed.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        bb = QDialogButtonBox()
        self.stop_btn = bb.addButton(f"Stop {n}" if n > 1 else "Stop", QDialogButtonBox.AcceptRole)
        self.stop_btn.setObjectName("danger")
        cancel = bb.addButton(QDialogButtonBox.Cancel)
        cancel.setDefault(True)
        cancel.setFocus()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# ----------------------------------------------------------------------------- main window
class RadarWindow(QMainWindow):
    refresh_requested = Signal()
    snapshot_shown = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.snap = None
        self.selected_service: str | None = None
        self.paused = False
        self.busy = False
        self.statuses: dict = {}
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1320, 860)
        self.setStyleSheet(QSS)

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(16, 14, 16, 10)
        v.setSpacing(10)

        # header
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(draw_icon_pixmap(34))
        title = QLabel(APP_TITLE)
        title.setObjectName("title")
        head.addWidget(ic)
        head.addWidget(title)
        head.addSpacing(18)
        self.gpu_label = QLabel("GPU")
        self.gpu_label.setObjectName("muted")
        self.gpu_bar = QProgressBar()
        self.gpu_bar.setMinimumWidth(380)
        self.gpu_bar.setRange(0, 1000)
        self.gpu_bar.setValue(0)
        self.gpu_bar.setFormat("reading GPU…")
        head.addWidget(self.gpu_label)
        head.addWidget(self.gpu_bar, 1)
        head.addSpacing(12)
        self.pause_btn = QPushButton("⏸  Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setToolTip("Pause / resume the automatic refresh")
        self.pause_btn.toggled.connect(self.set_paused)
        self.refresh_btn = QPushButton("⟳  Refresh")
        self.refresh_btn.clicked.connect(lambda: self.request_refresh(force=True))
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("Settings (hotkey, refresh interval)")
        head.addWidget(self.pause_btn)
        head.addWidget(self.refresh_btn)
        head.addWidget(self.settings_btn)
        v.addLayout(head)

        # known services
        sec = QLabel("KNOWN SERVICES")
        sec.setObjectName("section")
        v.addWidget(sec)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.cards: dict[str, ServiceCard] = {}
        ncol = 5
        for i, s in enumerate(rc.SERVICES):
            card = ServiceCard(s.key, s.label, s.hint)
            card.clicked.connect(self.on_card_clicked)
            card.stop_requested.connect(self.stop_service)
            card.menu_requested.connect(self.on_card_menu)
            self.cards[s.key] = card
            grid.addWidget(card, i // ncol, i % ncol)
        v.addLayout(grid)

        # background apps
        row = QHBoxLayout()
        sec2 = QLabel("BACKGROUND APPS")
        sec2.setObjectName("section")
        row.addWidget(sec2)
        row.addSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name, label, PID, port or command line…   (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.on_search)
        row.addWidget(self.search, 1)
        self.show_all = QCheckBox("Show all processes")
        self.show_all.setToolTip("Also list Windows system processes and normal windowed apps")
        self.show_all.setChecked(bool(settings["show_all"]))
        self.show_all.toggled.connect(self.on_show_all)
        row.addWidget(self.show_all)
        v.addLayout(row)

        self.model = ProcModel(self)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)
        t = self.table = QTableView()
        t.setModel(self.proxy)
        t.setSortingEnabled(True)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Ctrl+click, Shift+click, Ctrl+A
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(26)
        t.setShowGrid(False)
        t.setWordWrap(False)
        t.setTextElideMode(Qt.ElideMiddle)   # command line / long paths: keep start + end
        t.setMouseTracking(True)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(self.on_context_menu)
        t.clicked.connect(self.on_cell_clicked)
        t.entered.connect(self.on_cell_entered)
        h = t.horizontalHeader()
        h.setHighlightSections(False)
        for c, w in ((C_NAME, 170), (C_LABEL, 190), (C_PID, 64), (C_PORTS, 110), (C_CPU, 64), (C_RAM, 84),
                     (C_VRAM, 84), (C_UP, 80)):
            t.setColumnWidth(c, w)
        h.setSectionResizeMode(C_CMD, QHeaderView.Stretch)
        sc, so = settings.data.get("sort", [C_NAME, 0])
        t.sortByColumn(int(sc), Qt.SortOrder(int(so)))
        h.sortIndicatorChanged.connect(self.on_sort_changed)
        t.selectionModel().selectionChanged.connect(lambda *_: (self.update_actions(), self.update_counts()))
        del_act = QAction("Stop selected", t)
        del_act.setShortcut(Qt.Key_Delete)
        del_act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        del_act.triggered.connect(self.act_stop)
        t.addAction(del_act)
        v.addWidget(t, 1)

        # actions
        act = QHBoxLayout()
        self.open_btn = QPushButton("📂  Open folder")
        self.open_btn.clicked.connect(self.act_open_folder)
        self.copy_btn = QPushButton("⧉  Copy command line")
        self.copy_btn.clicked.connect(self.act_copy_cmd)
        self.web_btn = QPushButton("🌐  Open in browser")
        self.web_btn.clicked.connect(self.act_open_web)
        self.def_btn = QPushButton("🛡  Add to Defender exclusions…")
        self.def_btn.setToolTip("Add the selected executables to Microsoft Defender exclusions (needs admin)")
        self.def_btn.clicked.connect(self.act_defender)
        self.stop_btn = QPushButton("■  Stop process…")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.clicked.connect(self.act_stop)
        for b in (self.open_btn, self.copy_btn, self.web_btn, self.def_btn, self.stop_btn):
            act.addWidget(b)
        act.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        act.addWidget(self.count_label)
        v.addLayout(act)
        self.statusBar().setSizeGripEnabled(True)
        self.update_actions()

        QAction_find = QAction(self)
        QAction_find.setShortcut("Ctrl+F")
        QAction_find.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(QAction_find)
        refresh_act = QAction(self)
        refresh_act.setShortcut("F5")
        refresh_act.triggered.connect(lambda: self.request_refresh(force=True))
        self.addAction(refresh_act)

        geo = settings["geometry"]
        restored = False
        if geo:
            try:
                restored = bool(self.restoreGeometry(bytes.fromhex(geo)))
            except ValueError:
                restored = False
        if not restored:
            scr = QGuiApplication.primaryScreen()
            if scr is not None:
                av = scr.availableGeometry()
                w, h = min(1320, int(av.width() * 0.9)), min(860, int(av.height() * 0.88))
                self.setGeometry(av.x() + (av.width() - w) // 2, av.y() + (av.height() - h) // 2, w, h)

    # ---- data
    def request_refresh(self, force: bool = False) -> None:
        if self.busy or (self.paused and not force) or (not self.isVisible() and not force):
            return
        self.busy = True
        self.refresh_requested.emit()

    def on_failed(self, msg: str) -> None:
        self.busy = False
        self.statusBar().showMessage(f"Refresh failed: {msg}")

    def on_snapshot(self, snap) -> None:
        self.busy = False
        self.snap = snap
        now = time.time()
        sts = rc.service_status(snap, now)
        self.statuses = {s.key: s for s in sts}
        for st in sts:
            self.cards[st.key].update_status(st)
        self.update_gpu(snap)
        self.fill_table()
        if self.selected_service:
            self.proxy.set_pid_filter(self.statuses[self.selected_service].pids)
        self.update_counts()
        self.snapshot_shown.emit()

    def fill_table(self) -> None:
        if not self.snap:
            return
        before = self.selected_keys()
        cur = self.table.currentIndex().data(KEY_ROLE) if self.table.currentIndex().isValid() else None
        vs = self.table.verticalScrollBar().value()
        me = os.getpid()
        rows = [(p, why, rc.friendly_label(p, me)) for p, why in rc.background_rows(self.snap, self.show_all.isChecked())]
        self.model.set_rows(rows, self.snap.taken_at or time.time(), bool(self.snap.vram_source), self.snap.procs)
        # the incremental model update keeps the selection; re-apply it if a view change dropped rows
        keep = rc.preserve_selection(before, self.visible_keys())
        if set(self.selected_keys()) != set(keep):
            self.select_keys(keep, current=cur)
        self.table.verticalScrollBar().setValue(vs)
        self.update_actions()

    def update_gpu(self, snap) -> None:
        if not snap.gpus:
            self.gpu_label.setText("GPU")
            self.gpu_bar.setValue(0)
            self.gpu_bar.setFormat("no NVIDIA GPU info (nvidia-smi not found)")
            return
        g = snap.gpus[0]
        self.gpu_label.setText(g.name.replace("NVIDIA ", ""))
        self.gpu_bar.setValue(int(1000 * g.used / g.total) if g.total else 0)
        util = f"   ·   load {g.util}%" if g.util is not None else ""
        self.gpu_bar.setFormat(f"VRAM {rc.format_bytes(g.used)} used / {rc.format_bytes(g.total)}   ·   "
                               f"{rc.format_bytes(g.free)} free{util}")
        extra = "\n".join(f"{x.name}: {rc.format_bytes(x.used)} / {rc.format_bytes(x.total)}" for x in snap.gpus)
        self.gpu_bar.setToolTip(extra + f"\nPer-process VRAM from: {snap.vram_source or 'unavailable'}")

    def update_counts(self) -> None:
        if not self.snap:
            return
        shown, total = self.proxy.rowCount(), len(self.snap.procs)
        filt = ""
        if self.selected_service:
            filt = f"  ·  filtered to {self.statuses[self.selected_service].label} (click the card again to clear)"
        nsel = len(self.selected_keys())
        selt = f"{nsel} selected  ·  " if nsel else ""
        self.count_label.setText(f"{selt}{shown} shown of {total} processes{filt}")
        state = "PAUSED" if self.paused else f"auto-refresh every {int(self.settings['refresh_ms']) // 1000}s"
        self.statusBar().showMessage(
            f"Updated {time.strftime('%H:%M:%S', time.localtime(self.snap.taken_at))} in {self.snap.duration_ms} ms"
            f"  ·  {state}  ·  VRAM per process: {self.snap.vram_source or 'unavailable'}"
            + (f"  ·  {self.snap.errors[0]}" if self.snap.errors else ""))

    # ---- selection helpers
    def selected_keys(self) -> list:
        rows = sorted(self.table.selectionModel().selectedRows(), key=lambda i: i.row())
        return [i.data(KEY_ROLE) for i in rows]

    def selected_pids(self) -> list[int]:
        return [k[0] for k in self.selected_keys()]

    def visible_keys(self) -> list:
        return [self.proxy.index(r, 0).data(KEY_ROLE) for r in range(self.proxy.rowCount())]

    def selected_procs(self) -> list:
        if not self.snap:
            return []
        return [self.snap.procs[pid] for pid in self.selected_pids() if pid in self.snap.procs]

    def selected_pid(self):
        pids = self.selected_pids()
        return pids[0] if pids else None

    def selected_proc(self):
        procs = self.selected_procs()
        return procs[0] if procs else None

    def select_keys(self, keys, current=None) -> None:
        want = set(keys)
        sm = self.table.selectionModel()
        sel = QItemSelection()
        cur_ix = None
        last = self.proxy.columnCount() - 1
        for r in range(self.proxy.rowCount()):
            ix = self.proxy.index(r, 0)
            k = ix.data(KEY_ROLE)
            if k in want:
                sel.select(ix, self.proxy.index(r, last))
            if current is not None and k == current:
                cur_ix = ix
        sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        if cur_ix is not None:
            sm.setCurrentIndex(cur_ix, QItemSelectionModel.NoUpdate)

    def select_pids(self, pids) -> None:
        want = set(pids)
        self.select_keys([k for k in self.visible_keys() if k[0] in want])

    def select_pid(self, pid: int) -> None:
        self.select_pids([pid])

    def update_actions(self) -> None:
        procs = self.selected_procs()
        n = len(procs)
        p = procs[0] if procs else None
        self.copy_btn.setEnabled(n >= 1)
        job = getattr(self, "stop_job", None)
        self.stop_btn.setEnabled(n >= 1 and not (job is not None and job.busy))
        self.def_btn.setEnabled(n >= 1)
        self.open_btn.setEnabled(n == 1)
        self.web_btn.setEnabled(bool(n == 1 and p.listen))
        self.copy_btn.setText("⧉  Copy command lines" if n > 1 else "⧉  Copy command line")
        if n > 1:
            self.stop_btn.setText(f"■  Stop {n} processes…")
        elif p:
            self.stop_btn.setText(f"■  Stop {rc.shorten(p.name, 22)} ({p.pid})…")
        else:
            self.stop_btn.setText("■  Stop process…")

    # ---- UI events
    def on_search(self, text: str) -> None:
        self.proxy.set_needle(text)
        self.update_counts()

    def on_show_all(self, on: bool) -> None:
        self.settings["show_all"] = on
        self.settings.save()
        self.fill_table()
        self.update_counts()

    def on_sort_changed(self, col, order) -> None:
        self.settings["sort"] = [int(col), int(order.value if hasattr(order, "value") else order)]
        self.settings.save()

    def on_card_clicked(self, key: str) -> None:
        if self.selected_service == key or key not in self.statuses:
            self.selected_service = None
            self.proxy.set_pid_filter(None)
        else:
            self.selected_service = key
            pids = self.statuses[key].pids
            if pids and not self.show_all.isChecked():
                listed = {r[0].pid for r in self.model.rows}
                if not set(pids) <= listed:
                    self.show_all.setChecked(True)
            self.proxy.set_pid_filter(pids)
        for k, c in self.cards.items():
            c.set_selected(k == self.selected_service)
        self.update_counts()

    def on_cell_entered(self, index) -> None:
        src = self.proxy.mapToSource(index)
        if src.column() == C_PORTS and self.model.proc(src.row()).listen:
            self.table.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.table.viewport().unsetCursor()

    def on_cell_clicked(self, index) -> None:
        src = self.proxy.mapToSource(index)
        if src.column() != C_PORTS:
            return
        p = self.model.proc(src.row())
        urls = rc.urls_for(p)
        if len(urls) == 1:
            QDesktopServices.openUrl(QUrl(urls[0][1]))
        elif urls:
            m = QMenu(self)
            for _port, u in urls:
                m.addAction(u, lambda u=u: QDesktopServices.openUrl(QUrl(u)))
            m.exec(QCursor.pos())

    def on_context_menu(self, pos: QPoint) -> None:
        ix = self.table.indexAt(pos)
        if not ix.isValid():
            return
        if not self.table.selectionModel().isRowSelected(ix.row(), QModelIndex()):
            self.table.selectRow(ix.row())       # right-click outside the selection selects that row
        procs = self.selected_procs()
        if not procs:
            return
        n = len(procs)
        p = procs[0]
        m = QMenu(self)
        if n == 1:
            for _port, u in rc.urls_for(p)[:8]:
                m.addAction(f"Open {u}", lambda u=u: QDesktopServices.openUrl(QUrl(u)))
            if p.listen:
                m.addSeparator()
        stop_txt = f"Stop {n} selected processes…" if n > 1 else f"Stop {p.name} ({p.pid})…"
        m.addAction(stop_txt + "\tDel", self.act_stop)
        m.addSeparator()
        a = m.addAction("Open folder", self.act_open_folder)
        a.setEnabled(n == 1)
        m.addAction("Copy command lines" if n > 1 else "Copy command line", self.act_copy_cmd)
        m.addAction("Copy PIDs" if n > 1 else "Copy PID", self.act_copy_pids)
        m.addSeparator()
        m.addAction("🛡  Add to Defender exclusions…", self.act_defender)
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ---- actions
    def act_open_folder(self) -> None:
        p = self.selected_proc()
        if not p:
            return
        folder = rc.folder_for(p)
        if not open_folder(folder):
            QMessageBox.information(self, APP_TITLE, f"The folder of {p.name} ({p.pid}) is not readable "
                                    "(it probably runs as another user or as administrator).")

    def act_copy_cmd(self) -> None:
        procs = self.selected_procs()
        if len(procs) == 1:
            p = procs[0]
            QGuiApplication.clipboard().setText(p.cmd_text)
            self.statusBar().showMessage(f"Copied the command line of {p.name} ({p.pid})", 4000)
        elif procs:
            QGuiApplication.clipboard().setText("\n".join(p.cmd_text for p in procs))
            self.statusBar().showMessage(f"Copied {len(procs)} command lines", 4000)

    def act_copy_pids(self) -> None:
        pids = self.selected_pids()
        if pids:
            QGuiApplication.clipboard().setText(", ".join(map(str, pids)))
            self.statusBar().showMessage(f"Copied {len(pids)} PID(s)", 4000)

    def act_open_web(self) -> None:
        p = self.selected_proc()
        if p and p.listen:
            QDesktopServices.openUrl(QUrl(rc.urls_for(p)[0][1]))

    def act_stop(self) -> None:
        self.stop_pids(self.selected_pids())

    def stop_service(self, key: str) -> None:
        st = self.statuses.get(key)
        if st and st.pids:
            self.stop_pids(st.pids, title=f"Stop {st.label}?")

    def stop_pids(self, pids, title: str = "") -> None:
        if not self.snap or not pids:
            return
        job = getattr(self, "stop_job", None)
        if job is not None and job.busy:
            self.statusBar().showMessage("Still stopping the previous selection…", 4000)
            return
        plan = rc.stop_plan(pids, self.snap.procs, os.getpid())
        if not plan.items:
            QMessageBox.information(self, APP_TITLE, "\n".join(plan.warnings) or "Nothing to stop.")
            return
        dlg = StopDialog(plan, self, title)
        accepted = dlg.exec() == QDialog.Accepted
        tree = dlg.tree.isChecked()
        dlg.deleteLater()                  # destroyed by Qt on this (GUI) thread, never by Python's GC
        if not accepted:
            return
        procs = self.snap.procs
        names = {i.pid: f"{i.name} {('– ' + i.label) if i.label else ''}".strip() for i in plan.items}
        for i in plan.items:
            for k in i.children:
                if k in procs:
                    names.setdefault(k, f"{procs[k].name} (child of {i.pid})")
        expected = {pid: procs[pid].create_time for pid in plan.pids if pid in procs and procs[pid].create_time}
        if job is None:
            job = self.stop_job = StopJob(self)
            job.finished.connect(self._on_stop_done)
        n = len(plan.pids)
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage(f"Stopping {n} process{'es' if n != 1 else ''}…")
        job.start(plan.pids, tree, names, expected)

    def _on_stop_done(self, results, names, error) -> None:
        self.stop_job.busy = False
        self.update_actions()
        if error:
            log.error("Stop failed: %s", error)
            self.statusBar().showMessage(f"Stop failed: {error}", 8000)
            return
        text, counts = rc.stop_summary(results, names)
        log.info("Bulk stop -> %s", results)
        box = QMessageBox(self)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setWindowTitle("Stop result")
        box.setIcon(QMessageBox.Information if not (counts["denied"] or counts["alive"]) else QMessageBox.Warning)
        head, _, rest = text.partition("\n")
        box.setText(f"<b>{head}</b>")
        box.setInformativeText("<pre style='font-family:Consolas,monospace'>" + rest.replace("&", "&amp;")
                               .replace("<", "&lt;") + "</pre>")
        self.last_stop_box = box               # for tests; Qt deletes the box when it is closed
        box.open()
        self.statusBar().showMessage(f"Stop: {head}", 8000)
        self.request_refresh(force=True)

    def on_card_menu(self, key: str, pos) -> None:
        st = self.statuses.get(key)
        m = QMenu(self)
        m.addAction("Show only these processes", lambda: self.on_card_clicked(key)
                    if self.selected_service != key else None)
        if st and st.running and not st.group:
            m.addAction(f"Stop {st.label}…", lambda: self.stop_service(key))
        m.addSeparator()
        a = m.addAction("🛡  Add to Defender exclusions…")
        a.setEnabled(bool(st and st.running))
        a.triggered.connect(lambda: self.act_defender_service(key))
        m.exec(pos)

    def act_defender(self) -> None:
        self._defender_for(self.selected_procs())

    def act_defender_service(self, key: str) -> None:
        if not self.snap:
            return
        targets = rc.exe_paths_of_service(self.snap, key)
        label = rc.SERVICE_BY_KEY[key].label if key in rc.SERVICE_BY_KEY else key
        self._defender_for_targets(targets, title=f"Add {label} to Defender exclusions")

    def _defender_for(self, procs) -> None:
        self._defender_for_targets(rc.defender_targets(procs))

    def _defender_for_targets(self, targets, title: str = "") -> None:
        if not targets:
            QMessageBox.information(self, APP_TITLE,
                                    "None of the selected processes have a readable executable path "
                                    "(they probably run as another user or as administrator).")
            return
        app = getattr(self, "radar_app", None)
        if app is None or app.def_job.busy:
            return
        dlg = defui.AddExclusionsDialog(targets, self, title=title)
        dlg.setStyleSheet(self.styleSheet())
        if dlg.exec() != QDialog.Accepted:
            return
        change = dlg.change()
        if change.is_empty:
            QMessageBox.information(self, APP_TITLE, "Nothing selected to exclude.")
            return
        self.statusBar().showMessage("Waiting for the Windows UAC prompt…")
        app.def_job.done.connect(self._on_defender_done)
        app.def_job.status.connect(self.statusBar().showMessage)
        if not app.def_job.run(rc.build_apply_script(change, str(app.def_job.result)), hwnd=int(self.winId())):
            try:
                app.def_job.done.disconnect(self._on_defender_done)
            except RuntimeError:
                pass

    def _on_defender_done(self, res) -> None:
        app = getattr(self, "radar_app", None)
        if app is None:
            return
        try:
            app.def_job.done.disconnect(self._on_defender_done)
        except RuntimeError:
            pass
        if res is None:
            QMessageBox.information(self, APP_TITLE, "Cancelled – no Defender exclusions were changed.")
            return
        app.def_cache.update_from(res)
        self.model.excluded = app.def_cache.covers
        self.model.dataChanged.emit(self.model.index(0, 0),
                                    self.model.index(max(0, self.model.rowCount() - 1), 0))
        text = rc.summarize_defender(res)
        box = QMessageBox(self)
        box.setWindowTitle("Defender exclusions")
        box.setIcon(QMessageBox.Information if res.ok else QMessageBox.Warning)
        head, _, rest = (text + "\n").partition("\n")
        box.setText(f"<b>{head}</b>")
        if rest.strip():
            box.setInformativeText("<pre style='font-family:Consolas,monospace'>"
                                   + rest.replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
        box.exec()
        self.statusBar().showMessage(head, 8000)

    def open_defender_manager(self) -> None:
        app = getattr(self, "radar_app", None)
        if app is None:
            return
        dlg = defui.ManageExclusionsDialog(app.def_cache, app.def_job, self)
        dlg.setStyleSheet(self.styleSheet())
        dlg.exec()
        self.model.excluded = app.def_cache.covers
        if self.model.rowCount():
            self.model.dataChanged.emit(self.model.index(0, 0),
                                        self.model.index(self.model.rowCount() - 1, 0))

    def set_paused(self, on: bool) -> None:
        self.paused = on
        self.pause_btn.setText("▶  Resume" if on else "⏸  Pause")
        self.update_counts()
        if not on:
            self.request_refresh(force=True)

    # ---- window behaviour
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.search.text():
                self.search.clear()
            else:
                self.hide()
            return
        super().keyPressEvent(e)

    def save_geometry(self) -> None:
        self.settings["geometry"] = bytes(self.saveGeometry()).hex()
        self.settings.save()


class RadarApp(QObject):
    def __init__(self, qapp: QApplication, settings: Settings, collector_factory=None, tray: bool = True):
        super().__init__()
        self.qapp = qapp
        self.settings = settings
        self.def_cache = defui.ExclusionCache(data_dir() / "defender-exclusions.json")
        self.def_job = defui.ElevatedJob(data_dir(), self)
        self.win = RadarWindow(settings)
        self.win.radar_app = self
        self.win.model.excluded = self.def_cache.covers
        self.win.closeEvent = self._on_close
        self.win.settings_btn.clicked.connect(self.open_settings)
        self.thread = QThread()
        self.worker = Worker(collector_factory or rcol.Collector)
        self.worker.moveToThread(self.thread)
        self.win.refresh_requested.connect(self.worker.collect)
        self.worker.ready.connect(self.win.on_snapshot)
        self.worker.failed.connect(self.win.on_failed)
        self.thread.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.win.request_refresh)
        self.apply_interval()
        self.hotkeys = hk.HotkeyManager(self)
        self.hotkeys.triggered.connect(self.on_hotkey)
        self.tray = None
        if tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip(APP_TITLE)
            m = QMenu()
            m.addAction("Show / hide", self.toggle)
            m.addAction("Refresh now", lambda: self.win.request_refresh(force=True))
            m.addAction("Settings…", self.open_settings)
            m.addAction("🛡  Defender exclusions…", self.win.open_defender_manager)
            m.addSeparator()
            m.addAction("Quit Process Radar", self.quit)
            self._tray_menu = m
            self.tray.setContextMenu(m)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()
        self.qapp.aboutToQuit.connect(self.shutdown)

    def start(self) -> None:
        errors = self.hotkeys.apply(self.settings["hotkeys"] or rc.DEFAULT_HOTKEYS)
        if errors:
            log.warning("Hotkeys not registered: %s", errors)
            self.notify("Some hotkeys could not be registered:\n" + hk.describe_errors(errors))
        log.info("Hotkeys active: %s", self.hotkeys.active)
        self.show()

    def apply_interval(self) -> None:
        self.timer.start(max(1000, int(self.settings["refresh_ms"] or 3000)))

    def notify(self, msg: str) -> None:
        if self.tray:
            self.tray.showMessage(APP_TITLE, msg, QSystemTrayIcon.Information, 6000)

    def show(self) -> None:
        if self.win.isMinimized():
            self.win.showNormal()
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()
        if os.name == "nt":
            bring_to_front(int(self.win.winId()))
        self.win.request_refresh(force=True)

    def collect_garbage_on_gui_thread(self, interval_ms: int = 5000) -> None:
        """Python's cyclic GC normally runs on whatever thread happens to allocate - often the data
        thread. If it frees a Qt object there, Qt is left with a dangling timer and crashes later
        (seen on 23 Oct 2026: access violation in Qt6Core while delivering a timer event). So the
        automatic GC is switched off and run here, on the GUI thread, every few seconds instead."""
        gc.disable()
        self.gc_timer = QTimer(self)
        self.gc_timer.timeout.connect(lambda: gc.collect())
        self.gc_timer.start(interval_ms)

    def hide(self) -> None:
        self.win.save_geometry()
        self.win.hide()

    def toggle(self) -> None:
        if self.win.isVisible() and not self.win.isMinimized() and self.win.isActiveWindow():
            self.hide()
        else:
            self.show()

    def on_hotkey(self, action: str) -> None:
        {"toggle": self.toggle, "show": self.show, "hide": self.hide}.get(action, lambda: None)()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle()

    def _on_close(self, e) -> None:
        if self.tray is None:
            e.accept()
            self.quit()
            return
        e.ignore()
        self.hide()
        if not self.settings["tray_hint_shown"]:
            self.settings["tray_hint_shown"] = True
            self.settings.save()
            hot = self.hotkeys.active.get("toggle", "")
            self.notify("Process Radar keeps running in the tray." + (f" Press {hot} to show it again." if hot else ""))

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.win)
        dlg.setStyleSheet(QSS)
        dlg.manage_defender.connect(lambda: (dlg.accept(), self.win.open_defender_manager()))
        if dlg.exec() != QDialog.Accepted:
            return
        self.settings["hotkeys"] = dlg.hot.values()
        self.settings["refresh_ms"] = dlg.interval.value() * 1000
        self.settings.save()
        self.apply_interval()
        errors = self.hotkeys.apply(self.settings["hotkeys"])
        if errors:
            QMessageBox.warning(self.win, APP_TITLE, "Some hotkeys could not be registered:\n\n" +
                                hk.describe_errors(errors))
        self.win.update_counts()

    def quit(self) -> None:
        self.win.save_geometry()
        self.qapp.quit()

    def shutdown(self) -> None:
        self.timer.stop()
        if getattr(self, "gc_timer", None) is not None:
            self.gc_timer.stop()
            gc.enable()
        self.hotkeys.unregister_all()
        if self.tray:
            self.tray.hide()
        self.thread.quit()
        self.thread.wait(5000)


def bring_to_front(hwnd: int) -> bool:
    """Really activate our window. Windows only lets the app that has the focus hand it over; Qt's
    activateWindow() alone just flashes the taskbar button. Joining the input queue of the current
    foreground window for a moment is the standard, documented way around that."""
    try:
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        for fn in (u32.IsIconic, u32.ShowWindow, u32.BringWindowToTop, u32.SetForegroundWindow):
            fn.argtypes = [wintypes.HWND] + ([ctypes.c_int] if fn is u32.ShowWindow else [])
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)                  # SW_RESTORE
        fg = u32.GetForegroundWindow()
        if fg == hwnd:
            return True
        me = k32.GetCurrentThreadId()
        other = u32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(other and other != me and u32.AttachThreadInput(me, other, True))
        try:
            u32.BringWindowToTop(hwnd)
            ok = bool(u32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                u32.AttachThreadInput(me, other, False)
        return ok
    except Exception:  # noqa: BLE001 - cosmetic; never fail a show() because of this
        return False


def server_name() -> str:
    """One instance per Windows user *and* settings folder (a test copy with --data-dir never talks to,
    or removes the pipe of, the real app)."""
    tag = hashlib.sha1(str(data_dir().resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return f"ProcessRadar-{getpass.getuser()}-{tag}"


def notify_running_instance(attempts: int = 6, wait_ms: int = 500) -> bool:
    """Ask the running instance to show its window. Retries for ~3 s: the first instance may still be
    starting up (lock taken, pipe not listening yet)."""
    if os.name == "nt":
        try:   # let the running instance take the foreground (Windows blocks that for background apps)
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)    # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass
    for i in range(attempts):
        sock = QLocalSocket()
        sock.connectToServer(server_name())
        if sock.waitForConnected(wait_ms):
            sock.write(b"show")
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            return True
        if i + 1 < attempts:
            time.sleep(wait_ms / 1000.0)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=APP_TITLE)
    ap.add_argument("--data-dir", help="override the settings folder (default %%APPDATA%%\\ProcessRadar)")
    ap.add_argument("--screenshot", help="save a PNG of the window after the first refresh")
    ap.add_argument("--quit-after-screenshot", action="store_true")
    args = ap.parse_args(argv)
    if args.data_dir:
        os.environ["PR_DATA_DIR"] = args.data_dir
    directory = data_dir()
    setup_logging(directory)
    install_crash_handlers(directory)

    QApplication.setQuitOnLastWindowClosed(False)
    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName(APP_TITLE)
    qapp.setWindowIcon(app_icon())
    qapp.setFont(QFont("Segoe UI", 10))
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Angels.ProcessRadar")
        except Exception:  # noqa: BLE001
            pass

    lock = QLockFile(str(directory / "app.lock"))
    lock.setStaleLockTime(0)               # a lock is only stale if its process is gone (QLockFile checks)
    if not lock.tryLock(200):
        if notify_running_instance():
            log.info("Already running - asked the running instance to show its window")
            return 0
        lock.removeStaleLockFile()
        if not lock.tryLock(200):
            # Another instance holds the lock but did not answer (still starting, or busy). Starting a
            # second copy or showing an error would both be wrong: log it and leave quietly.
            log.warning("Already running, but the running instance did not answer - not starting twice")
            return 0
    try:
        QLocalServer.removeServer(server_name())
        server = QLocalServer()
        server.listen(server_name())
        settings = Settings(directory / "settings.json")
        app = RadarApp(qapp, settings)
        app.collect_garbage_on_gui_thread()
        install_crash_handlers.hook.window = app.win

        def on_conn():
            while server.hasPendingConnections():
                c = server.nextPendingConnection()
                c.readyRead.connect(lambda c=c: (c.readAll(), app.show()))
        server.newConnection.connect(on_conn)

        if args.screenshot:
            def shot(_snap=None):
                if getattr(shot, "done", False):
                    return
                shot.done = True
                def grab():
                    app.win.grab().save(args.screenshot)
                    log.info("Screenshot saved to %s", args.screenshot)
                    if args.quit_after_screenshot:
                        app.quit()
                QTimer.singleShot(4500, grab)   # wait for a second refresh so CPU % is real
            app.win.snapshot_shown.connect(shot)

        log.info("Process Radar started (python %s, data folder %s)", sys.executable, directory)
        app.start()
        rc_ = qapp.exec()
        server.close()
        log.info("Process Radar stopped")
        return rc_
    finally:
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())

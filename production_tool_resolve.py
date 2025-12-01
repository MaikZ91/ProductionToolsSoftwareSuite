#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stage-Toolbox (Dark Minimal Theme) – Pro UI (Report & QA) + Workflow-Kacheln
- 2-Spalten-GUI mit Cards, Batch-Validierung
- Felder: Operator/Bediener, Bemerkungen (in PDF-Bericht)
- Kalibrierwerte in GUI + Bericht
- Ein Run-Ordner für alles (Messung + Dauertest)
- QA-Grenzen: Messung (10 µm), Dauertest (8 µm) → OK/FAIL, Live-Anzeige im Dauertest
- PDF-Bericht: Deckblatt (Meta, Kalibrierung, Bemerkungen, QA), danach alle Plots
- Live-Plot speichern, Summary-PDF
- Neue Stage testen (Reset), Ordner öffnen
- Kamera-Auswahl (4 IDs): Autofocus, Kollimator Resolve 1, Resolve 2, MacSEQ
- Dauer-Auswahl für Dauertest (Presets + Benutzerdefiniert), Default 15 h
- NEU: Workflow-Kacheln oben (Stage-Bild, Autofocus-Bild)
- NEU: Autofocus-Reiter mit Exposure-Regler (µs/ms) je Zielkamera
- NEU: Alignment zeigt dx, dy, dist zusätzlich in physikalischen Einheiten (µm / mm)
"""

# --- Snap/GIO Modul-Konflikte entschärfen (vor allen anderen Imports) ---
import os as _os
_os.environ.pop("GIO_MODULE_DIR", None)

import datetime
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

# Ensure relocated modules are importable (./Hardware, ./Algorithmen)
_BASE_DIR = pathlib.Path(__file__).resolve().parent
for _sub in ("Hardware", "Algorithmen"):
    _cand = _BASE_DIR / _sub
    if _cand.exists():
        sys.path.insert(0, str(_cand))

import cv2
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib import image as mpimg

from PySide6.QtCore    import QObject, QThread, Signal, Qt, QTimer, QSize, QRegularExpression, QEvent
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QLineEdit, QTextEdit,
    QProgressBar, QMessageBox, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QSizePolicy, QSpacerItem, QComboBox, QToolButton,
    QStackedWidget, QSlider, QDoubleSpinBox, QDialog, QListWidget,
    QScrollArea
)
from PySide6.QtGui     import (
    QPixmap, QPalette, QColor, QFont, QShortcut, QKeySequence,
    QRegularExpressionValidator, QIcon, QImage, QPainter, QPen
)

from ie_Framework.Algorithm.laser_spot_detection import LaserSpotDetector
from ie_Framework.Hardware.Camera.ids_camera import IdsCam
import datenbank as db
import gitterschieber as gs
from z_trieb import ZTriebWidget
import stage_control as resolve_stage


# ========================== DATENBANK / INFRA ==========================
BASE_DIR = _BASE_DIR
DASHBOARD_WIDGET_CLS, _DASHBOARD_IMPORT_ERROR = (None, None)

# Dashboard resolver (lokal statt aus db-Modul, da dort keine Hilfsfunktion mehr existiert)
def _resolve_dashboard_widget(base_dir: pathlib.Path):
    try:
        sys.path.insert(0, str(base_dir))
        import dashboard  # type: ignore
        cls = getattr(dashboard, "Dashboard", None)
        return cls, None
    except Exception as exc:  # noqa: BLE001
        return None, exc
    finally:
        try:
            sys.path.remove(str(base_dir))
        except Exception:
            pass

DASHBOARD_WIDGET_CLS, _DASHBOARD_IMPORT_ERROR = _resolve_dashboard_widget(BASE_DIR)

matplotlib.use("qtagg")

# ========================== QA LIMITS (in µm) ==========================
MEAS_MAX_UM = 10.0   # Max. |Delta| in Messung
# =======================================================================

# ================================================================
# THEME
# ================================================================
ACCENT   = "#ff2740"
BG       = "#0b0b0f"
BG_ELEV  = "#121218"
FG       = "#e8e8ea"
FG_MUTED = "#9ea0a6"
BORDER   = "#222230"
HOVER    = "#1b1b26"

plt.rcParams.update({
    "figure.facecolor": BG_ELEV,
    "axes.facecolor": BG,
    "savefig.facecolor": BG_ELEV,
    "axes.edgecolor": FG_MUTED,
    "axes.labelcolor": FG,
    "axes.titleweight": "semibold",
    "text.color": FG,
    "xtick.color": FG_MUTED,
    "ytick.color": FG_MUTED,
    "grid.color": "#2a2a3a",
    "grid.linestyle": "-",
    "grid.linewidth": 0.6,
    "axes.grid": True,
    "font.size": 10.5,
    "font.sans-serif": ["Inter", "Segoe UI", "DejaVu Sans", "Arial"],
    "legend.facecolor": BG_ELEV,
    "legend.edgecolor": BORDER,
})

QT_STYLESHEET = f"""
* {{
  background: transparent;
  color: {FG};
  font-family: Inter, "Segoe UI", Arial;
}}
QWidget {{ background-color: {BG}; }}
QLabel {{ color: {FG}; font-size: 13px; }}

QLineEdit, QTextEdit {{
  background-color: {BG_ELEV};
  color: {FG};
  padding: 10px 12px;
  border: 1px solid {BORDER};
  border-radius: 10px;
  selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}

QPushButton {{
  background-color: {BG_ELEV};
  color: {FG};
  padding: 10px 14px;
  border: 1px solid {BORDER};
  border-radius: 12px;
  font-weight: 600;
  min-height: 40px;
}}
QPushButton:hover {{ background-color: {HOVER}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: {ACCENT}; color: #0b0b0f; border-color: {ACCENT}; }}
QPushButton:disabled {{ background: #14141b; color: {FG_MUTED}; border-color: {BORDER}; }}

QProgressBar {{
  background: {BG_ELEV};
  color: {FG};
  border: 1px solid {BORDER};
  border-radius: 10px;
  text-align: center;
  height: 20px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 8px; }}

QFrame#Card {{
  background-color: {BG_ELEV};
  border: 1px solid {BORDER};
  border-radius: 16px;
}}
QLabel#CardTitle {{
  color: {FG};
  font-weight: 700;
  font-size: 14px;
}}

QLabel#Chip {{
  background-color: {HOVER};
  color: {FG};
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid {BORDER};
  font-size: 12px;
}}
"""

def apply_dark_theme(app: QApplication):
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(FG))
    pal.setColor(QPalette.Base, QColor(BG))
    pal.setColor(QPalette.AlternateBase, QColor(BG_ELEV))
    pal.setColor(QPalette.ToolTipBase, QColor(BG_ELEV))
    pal.setColor(QPalette.ToolTipText, QColor(FG))
    pal.setColor(QPalette.Text, QColor(FG))
    pal.setColor(QPalette.Button, QColor(BG_ELEV))
    pal.setColor(QPalette.ButtonText, QColor(FG))
    pal.setColor(QPalette.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#0b0b0f"))
    app.setPalette(pal)
    app.setStyleSheet(QT_STYLESHEET)

# matplotlib savefig facecolor fix
_def_savefig = plt.savefig
def savefig_dark(path, fig=None, **kw):
    if fig is None: fig = plt.gcf()
    kw.setdefault("facecolor", fig.get_facecolor())
    kw.setdefault("edgecolor", "none")
    _def_savefig(path, **kw)
plt.savefig = savefig_dark

def style_ax(ax):
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_color(BORDER); spine.set_linewidth(0.8)
    ax.grid(True)
    ax.tick_params(colors=FG_MUTED, labelsize=10)

# ================================================================
# Reusable Card
# ================================================================
class Card(QFrame):
    def __init__(self, title: str = "", right_widget: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self); lay.setContentsMargins(18,16,18,16); lay.setSpacing(12)
        header = QHBoxLayout(); header.setSpacing(8)
        self.title = QLabel(title); self.title.setObjectName("CardTitle")
        header.addWidget(self.title)
        header.addStretch(1)
        if right_widget: header.addWidget(right_widget, 0, Qt.AlignRight)
        lay.addLayout(header)
        self.body = QVBoxLayout(); self.body.setSpacing(10)
        lay.addLayout(self.body)

# ================================================================
# Workflow-Kacheln (Icons sicher laden)
# ================================================================
def _safe_icon(path: str) -> QIcon:
    try:
        if os.path.exists(path):
            return QIcon(path)
    except Exception:
        pass
    return QIcon()

def make_tile(text: str, icon_path: str, clicked_cb):
    btn = QToolButton()
    btn.setIcon(_safe_icon(icon_path))
    btn.setIconSize(QSize(72, 72))
    btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    btn.setText(text)
    btn.setMinimumSize(130, 100)
    btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    btn.setAutoRaise(False)
    btn.setStyleSheet("""
        QToolButton {
            background-color: %s;
            border: 1px solid %s;
            border-radius: 14px;
            padding: 10px;
            font-weight: 600;
        }
        QToolButton:hover { background-color: %s; border-color: %s; }
    """ % (BG_ELEV, BORDER, HOVER, ACCENT))
    btn.clicked.connect(clicked_cb)
    return btn


class LiveCamEmbed(QWidget):
    """Einfacher Live-Kameraview für BGR/Mono Frames mit Frame-Provider."""
    def __init__(self, frame_provider, *, interval_ms: int = 200, start_immediately: bool = True, parent=None):
        super().__init__(parent)
        self._frame_provider = frame_provider
        self._last_frame = None
        self._interval_ms = interval_ms
        self._autostart = bool(start_immediately)
        self.label = QLabel("Kein Bild")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMinimumHeight(320)
        self.status = QLabel("")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if self._autostart and self.isVisible():
            self.start()

    def start(self):
        if not self._timer.isActive():
            self._timer.start(self._interval_ms)

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self._autostart:
            self.start()

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

    def last_frame(self):
        return self._last_frame

    def _tick(self):
        try:
            frame = self._frame_provider()
        except Exception as exc:
            self.status.setText(f"Kein Frame: {exc}")
            return
        if frame is None:
            self.status.setText("Kein Frame erhalten.")
            return
        self._last_frame = frame
        try:
            qimg = self._to_qimage(frame)
            pm = QPixmap.fromImage(qimg)
            pm = pm.scaled(self.label.width(), self.label.height(), Qt.KeepAspectRatio)
            self.label.setPixmap(pm)
            self.status.setText("Livebild aktualisiert.")
        except Exception as exc:
            self.status.setText(f"Anzeige-Fehler: {exc}")

    def _to_qimage(self, frame):
        if frame.ndim == 2:
            arr = frame
            if arr.dtype != np.uint8:
                arr = np.clip(arr.astype(np.float32) / float(arr.max() or 1) * 255.0, 0, 255).astype(np.uint8)
            h, w = arr.shape
            return QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
        if frame.ndim == 3 and frame.shape[2] == 3:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            return QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        raise ValueError("Unsupported frame shape.")


def _ensure_gray8(frame: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Return (gray_frame_uint8, width, height) for any supported input frame."""
    if frame.ndim == 3 and frame.shape[2] == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if frame.dtype != np.uint8:
        max_val = float(frame.max() or 1.0)
        frame = np.clip(frame.astype(np.float32) / max_val * 255.0, 0, 255).astype(np.uint8)
    gray = np.ascontiguousarray(frame)
    h, w = gray.shape
    return gray, w, h


def _simulate_dummy_centroid(tick: int, width: int, height: int) -> tuple[int, int]:
    """Pseudo-random moving centroid for dummy mode (mirrors old detector logic)."""
    cx = int(width / 2 + np.sin(tick / 12.0) * (width * 0.25))
    cy = int(height / 2 + np.cos(tick / 15.0) * (height * 0.25))
    return cx, cy


def paint_laser_overlay(
    frame: np.ndarray,
    detector: LaserSpotDetector,
    *,
    accent_color: str = ACCENT,
    ref_point: tuple[int, int] | None = None,
    is_dummy: bool = False,
    simulate_fn=None,
) -> tuple[QImage, tuple[int, int]]:
    """Draw laser overlays for a frame and return the QImage plus centroid."""
    gray, width, height = _ensure_gray8(frame)
    frame_bytes = gray.tobytes()
    if is_dummy:
        if simulate_fn is not None:
            cx, cy = simulate_fn(width, height)
        else:
            cx, cy = (width // 2, height // 2)
    else:
        cx, cy = detector.detect_laser_spot(gray)
    ref = ref_point
    qimg = QImage(frame_bytes, width, height, width, QImage.Format_Grayscale8).copy()
    if qimg.format() != QImage.Format_ARGB32:
        qimg = qimg.convertToFormat(QImage.Format_ARGB32)
    painter = QPainter(qimg)
    try:
        pen_cam = QPen(QColor(accent_color))
        pen_cam.setWidth(3)
        pen_cam.setStyle(Qt.DashLine)
        painter.setPen(pen_cam)
        cx0 = width // 2
        cy0 = height // 2
        painter.drawLine(cx0, 0, cx0, height)
        painter.drawLine(0, cy0, width, cy0)
    except Exception:
        pass
    try:
        pen_l = QPen(QColor(accent_color))
        pen_l.setWidth(3)
        pen_l.setCapStyle(Qt.RoundCap)
        painter.setPen(pen_l)
        size = max(6, min(width, height) // 20)
        painter.drawLine(max(0, cx - size), cy, min(width, cx + size), cy)
        painter.drawLine(cx, max(0, cy - size), cx, min(height, cy + size))
    except Exception:
        pass
    if ref is not None:
        try:
            rx, ry = ref
            pen_r = QPen(QColor("#ffd60a"))
            pen_r.setWidth(2)
            painter.setPen(pen_r)
            rsize = max(6, min(width, height) // 30)
            painter.drawEllipse(
                int(rx - rsize // 2),
                int(ry - rsize // 2),
                int(rsize),
                int(rsize),
            )
            pen_line = QPen(QColor("#ffd60a"))
            pen_line.setStyle(Qt.DashLine)
            painter.setPen(pen_line)
            painter.drawLine(rx, ry, cx, cy)
        except Exception:
            pass
    try:
        painter.end()
    except Exception:
        pass
    return qimg, (cx, cy)


class LiveLaserController(QObject):
    """Simple live controller that grabs frames and emits overlays for laser centering."""

    frameReady = Signal(QImage)
    centerChanged = Signal(int, int)

    def __init__(self, device_index: int, detector: LaserSpotDetector, parent=None):
        super().__init__(parent)
        self.device_index = device_index
        self.detector = detector
        self.cam: IdsCam | None = None
        self.is_dummy = False
        self._sim_tick = 0
        self._ref_point: tuple[int, int] | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._init_camera()

    def _init_camera(self):
        try:
            self.cam = IdsCam(index=self.device_index, set_min_exposure=False)
            self.is_dummy = bool(getattr(self.cam, "_dummy", False))
        except Exception as exc:
            self.cam = None
            self.is_dummy = True
            print(f"[WARN] Kamera konnte nicht initialisiert werden: {exc}")

    def start(self, interval_ms: int = 120):
        if not self._timer.isActive():
            self._timer.start(interval_ms)

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()

    def shutdown(self):
        self.stop()
        try:
            if self.cam is not None:
                self.cam.shutdown()
        except Exception:
            pass

    def _tick(self):
        if self.cam is None:
            return
        try:
            frame = self.cam.aquise_frame()
            if frame is None:
                return
            qimg, (cx, cy) = paint_laser_overlay(
                frame,
                self.detector,
                ref_point=self._ref_point,
                is_dummy=self.is_dummy,
                simulate_fn=self._next_dummy_centroid if self.is_dummy else None,
            )
            self.frameReady.emit(qimg)
            self.centerChanged.emit(int(cx), int(cy))
        except Exception as exc:
            print(f"[WARN] Live-Frame fehlgeschlagen: {exc}")

    # ---- Camera controls -------------------------------------------------
    def set_exposure_us(self, exposure_us: int):
        if self.cam is None:
            return
        try:
            self.cam.set_exposure_us(int(exposure_us))
        except Exception as exc:
            print(f"[WARN] Exposure setzen fehlgeschlagen: {exc}")

    def get_exposure_limits_us(self) -> tuple[float, float, float]:
        if self.cam is None:
            return 2000.0, 50.0, 200000.0
        try:
            return self.cam.get_exposure_limits_us()
        except Exception as exc:
            print(f"[WARN] Exposure-Limits nicht lesbar: {exc}")
            return 2000.0, 50.0, 200000.0

    def _next_dummy_centroid(self, width: int, height: int) -> tuple[int, int]:
        """Advance dummy centroid tick and return simulated center."""
        cx, cy = _simulate_dummy_centroid(self._sim_tick, width, height)
        self._sim_tick += 1
        return cx, cy

    # ---- Reference point handling ---------------------------------------
    def set_reference_point(self, x: int | None, y: int | None):
        if x is None or y is None:
            self._ref_point = None
        else:
            self._ref_point = (int(x), int(y))

    def clear_reference_point(self):
        self._ref_point = None

    def get_reference_point(self) -> tuple[int, int] | None:
        return self._ref_point


class CameraWindow(QWidget):
    """Einfaches Kamerafenster mit Laser-Overlay (GUI jetzt im production_tool)."""

    closed = Signal()

    def __init__(
        self,
        parent,
        batch: str,
        device_index: int,
        label: str,
        spot_detector: LaserSpotDetector,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{label} – Charge {batch}")
        self.setMinimumSize(640, 520)
        self.is_closed = False
        self.detector = spot_detector
        self._last_center: tuple[int, int] | None = None

        self.live = LiveLaserController(device_index, detector=spot_detector, parent=self)
        self.live.frameReady.connect(self._update_image)
        self.live.centerChanged.connect(self._on_center_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.imgLabel = QLabel("Kein Frame")
        self.imgLabel.setAlignment(Qt.AlignCenter)
        self.imgLabel.setMinimumHeight(360)
        layout.addWidget(self.imgLabel)

        self.lblCenter = QLabel("Center: –")
        self.lblRef = QLabel("Referenz: –")
        info_row = QHBoxLayout()
        info_row.addWidget(self.lblCenter)
        info_row.addWidget(self.lblRef)
        info_row.addStretch(1)
        layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        self.btnSetRef = QPushButton("Center als Referenz")
        self.btnClearRef = QPushButton("Referenz löschen")
        self.btnShowRef = QPushButton("Ref anzeigen")
        btn_row.addWidget(self.btnSetRef)
        btn_row.addWidget(self.btnClearRef)
        btn_row.addWidget(self.btnShowRef)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.btnSetRef.clicked.connect(self._apply_ref_from_center)
        self.btnClearRef.clicked.connect(self._clear_ref)
        self.btnShowRef.clicked.connect(self._show_ref_status)

    def _apply_ref_from_center(self):
        if self._last_center is None:
            return
        self.live.set_reference_point(*self._last_center)
        self.lblRef.setText(f"Referenz: {self._last_center[0]}, {self._last_center[1]}")

    def _clear_ref(self):
        self.live.clear_reference_point()
        self.lblRef.setText("Referenz: –")

    def _show_ref_status(self):
        ref = self.live.get_reference_point()
        if ref is None:
            self.lblRef.setText("Referenz: –")
        else:
            self.lblRef.setText(f"Referenz: {ref[0]}, {ref[1]}")

    def _update_image(self, qimg: QImage):
        try:
            pm = QPixmap.fromImage(qimg)
            pm = pm.scaled(self.imgLabel.width(), self.imgLabel.height(), Qt.KeepAspectRatio)
            self.imgLabel.setPixmap(pm)
        except Exception as exc:
            self.imgLabel.setText(f"Anzeige-Fehler: {exc}")

    def _on_center_changed(self, x: int, y: int):
        self._last_center = (x, y)
        self.lblCenter.setText(f"Center: {x}, {y}")

    def showEvent(self, event):
        try:
            self.live.start()
        except Exception:
            pass
        super().showEvent(event)

    def hideEvent(self, event):
        try:
            self.live.stop()
        except Exception:
            pass
        super().hideEvent(event)

    def closeEvent(self, event):
        self.is_closed = True
        try:
            self.live.shutdown()
        except Exception:
            pass
        try:
            self.closed.emit()
        except Exception:
            pass
        super().closeEvent(event)

# ================================================================
# Plot Widget
# ================================================================
class LivePlot(FigureCanvas):
    def __init__(self, parent=None, batch: str = "NoBatch"):
        fig = Figure(figsize=(7.2, 4.2), dpi=110, facecolor=BG_ELEV)
        super().__init__(fig)
        self.setParent(parent)
        self.ax = fig.add_subplot(111)
        self.batch = resolve_stage.sanitize_batch(batch)
        self.mode = "Dauertest"
        self._apply_titles()
        style_ax(self.ax)
        self.line_ex, = self.ax.plot([], [], label="Error X")
        self.line_ey, = self.ax.plot([], [], label="Error Y")
        leg = self.ax.legend(); leg.get_frame().set_linewidth(0.6); leg.get_frame().set_edgecolor(BORDER)
        self.t,self.ex,self.ey = [],[],[]

    def _apply_titles(self):
        self.ax.set_title(f"{self.mode} – Positionsfehler · Charge: {self.batch}", fontweight="semibold")
        self.ax.set_xlabel("Zeit [min]"); self.ax.set_ylabel("Fehler [m]")

    def set_batch(self, batch: str):
        self.batch = resolve_stage.sanitize_batch(batch); self._apply_titles(); self.draw_idle()

    def set_mode(self, mode: str):
        self.mode = mode
        self._apply_titles()
        self.draw_idle()

    def reset(self):
        self.t.clear(); self.ex.clear(); self.ey.clear()
        self.line_ex.set_data([], [])
        self.line_ey.set_data([], [])
        self.ax.relim(); self.ax.autoscale_view(); self.draw_idle()

    def add_data(self, data):
        ex_m = float(data.get("ex", 0.0))
        ey_m = float(data.get("ey", 0.0))
        self.t.append(data["t"]); self.ex.append(ex_m); self.ey.append(ey_m)
        self.line_ex.set_data(self.t,self.ex); self.line_ey.set_data(self.t,self.ey)
        self.ax.relim(); self.ax.autoscale_view(); self.draw_idle()

# ========================== GUI =====================================
# ================================================================
# GUI
# ================================================================
class StageGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.sc=resolve_stage.StageController()
        self.plot=None
        self._cam_windows=[]
        self._gitterschieber_windows=[]
        self._batch="NoBatch"; self._dauer_running=False
        self._duration_sec = 15 * 3600  # Default 15h
        self._run_outdir: pathlib.Path | None = None
        self._last_outdir: pathlib.Path | None = None
        self._meas_max_um = None
        self._dur_max_um  = None
        self._calib_vals  = {"X": None, "Y": None}
        self._combined_total: int | None = None
        self._combined_small: int = 0
        self._combined_large: int = 0
        self._warned_camera_sim = False
        self.statusBar: QLabel | None = None
        self._pending_status: str | None = None
        self._af_cam = None

        # Neu: Zielkamera für Exposure-UI (wird nur für Fallback genutzt)
        self._expo_target_idx = 0

        self._build_ui(); self._wire_shortcuts()
        sim_msgs = []
        if getattr(resolve_stage.pmac, "is_simulated", False):
            sim_msgs.append("Stage-Steuerung im Simulationsmodus (keine PMAC-Verbindung).")
        if sim_msgs:
            self._set_status(" ".join(sim_msgs))

    # ---------- UI ----------
    def _build_ui(self):
        self.setWindowTitle("Stage-Toolbox")
        self._apply_initial_size()
        root = QVBoxLayout(self); root.setContentsMargins(18,18,18,12); root.setSpacing(14)

        def add_back_btn(container_layout):
            """Helper: adds a 'Zurück zum Workflow' button to the given layout."""
            btn = QPushButton("Zurück zum Workflow")
            btn.setMinimumHeight(32)
            btn.clicked.connect(self._show_stage_workflow)
            row = QHBoxLayout()
            row.addWidget(btn)
            row.addStretch(1)
            container_layout.addLayout(row)

        # Header
        header = QHBoxLayout(); header.setSpacing(10)
        title = QLabel("Stage-Toolbox"); f = QFont("Inter", 18, QFont.Bold); title.setFont(f)
        header.addWidget(title); header.addStretch(1)
        self.chipBatch = QLabel("Charge: NoBatch"); self.chipBatch.setObjectName("Chip")
        header.addWidget(self.chipBatch)
        # Seriennummer-Suche (sucht im Stage-Teststand-Datenordner nach Dateien/Ordnern)
        self.edSearchSN = QLineEdit(); self.edSearchSN.setPlaceholderText("Seriennummer suchen…")
        self.edSearchSN.setFixedWidth(220)
        header.addWidget(self.edSearchSN)
        self.btnFindSN = QPushButton("Find SN")
        self.btnFindSN.setMinimumHeight(28)
        self.btnFindSN.clicked.connect(lambda: self._on_search_sn())
        self.edSearchSN.returnPressed.connect(lambda: self.btnFindSN.click())
        # Live search: update as the user types (debounced)
        self.edSearchSN.textChanged.connect(self._on_search_sn_live)
        # allow forwarding arrow/enter to popup via eventFilter
        self.edSearchSN.installEventFilter(self)
        header.addWidget(self.btnFindSN)

        self.btnLiveViewTab = QPushButton("LIVE VIEW")
        self.btnLiveViewTab.setMinimumHeight(32)
        self.btnLiveViewTab.clicked.connect(self._open_live_view)
        header.addWidget(self.btnLiveViewTab)

        self.btnWorkflowHome = QPushButton("Workflow")
        self.btnWorkflowHome.setMinimumHeight(32)
        self.btnWorkflowHome.clicked.connect(self._show_stage_workflow)
        header.addWidget(self.btnWorkflowHome)

        # Debounce timer for live search (singleShot)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)  # ms
        self._search_timer.timeout.connect(self._perform_search_sn)

        # Popup list for live results
        self._sn_popup = QListWidget(self)
        popup_flags = Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        self._sn_popup.setWindowFlags(popup_flags)
        self._sn_popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._sn_popup.setFocusPolicy(Qt.StrongFocus)
        self._sn_popup.setUniformItemSizes(True)
        self._sn_popup.setSelectionMode(QListWidget.SingleSelection)
        self._sn_popup.itemClicked.connect(lambda it: self._open_selected_sn(it))
        self._sn_popup.itemDoubleClicked.connect(lambda it: self._open_selected_sn(it))
        root.addLayout(header)

        assets_dir = pathlib.Path(__file__).resolve().parent / "assets"
        stage_img = str((assets_dir / "stage_tile.png").resolve())
        af_img    = str((assets_dir / "autofocus_tile.png").resolve())
        laser_img = str((assets_dir / "laserscan_tile.png").resolve())

        # --- WORKFLOW (Kacheln) ---
        # Seitenumschaltung (in ScrollArea, damit Vollbild sauber aussieht)
        self.stack = QStackedWidget()
        self.contentScroll = QScrollArea()
        self.contentScroll.setWidgetResizable(True)
        self.contentScroll.setFrameShape(QFrame.NoFrame)
        self.contentScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.contentScroll.setWidget(self.stack)
        root.addWidget(self.contentScroll, 1)

        # ===================== Stage Seite =====================
        self.stagePage = QWidget()
        stageLayout = QVBoxLayout(self.stagePage)
        stageLayout.setContentsMargins(0,0,0,0); stageLayout.setSpacing(14)

        self.workflowCard = Card("Workflow")
        stageLayout.addWidget(self.workflowCard)
        tiles = QHBoxLayout(); tiles.setSpacing(12)
        self.btnStageTile = make_tile("Stage", stage_img, self._show_stage_workflow)
        self.btnAutofocusTile = make_tile("Autofocus", af_img, self._open_autofocus_workflow)
        self.btnLaserTile = make_tile("Laserscan Modul", laser_img, self._open_laserscan_workflow)
        self.btnZTriebTile = make_tile("Z-Trieb", laser_img, self._open_ztrieb_workflow)
        self.btnGitterschieberTile = make_tile("Gitterschieber", stage_img, self._open_gitterschieber)
        tiles.addWidget(self.btnStageTile)
        tiles.addWidget(self.btnAutofocusTile)
        tiles.addWidget(self.btnLaserTile)
        tiles.addWidget(self.btnZTriebTile)
        tiles.addWidget(self.btnGitterschieberTile)
        tiles.addStretch(1)
        self.workflowCard.body.addLayout(tiles)

        # Hauptgrid
        grid = QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(14)
        stageLayout.addLayout(grid, 1)

        # Left: Controls + Meta
        self.cardBatch = Card("Meta")
        grid.addWidget(self.cardBatch, 0, 0)

        # Operator
        opLay = QHBoxLayout()
        self.edOperator = QLineEdit(); self.edOperator.setPlaceholderText("Bediener: z. B. M. Zschach")
        opLay.addWidget(QLabel("Operator")); opLay.addWidget(self.edOperator)
        self.cardBatch.body.addLayout(opLay)

        # Batch
        self.edBatch = QLineEdit(); self.edBatch.setPlaceholderText("Chargennummer, z. B. B2025-10-30-01")
        regex = QRegularExpression(r"^[A-Za-z0-9._-]{0,64}$")
        self.edBatch.setValidator(QRegularExpressionValidator(regex))
        hb = QHBoxLayout()
        hb.addWidget(QLabel("Charge")); hb.addWidget(self.edBatch)
        self.cardBatch.body.addLayout(hb)

        # Bemerkungen
        self.txtNotes = QTextEdit(); self.txtNotes.setPlaceholderText("Bemerkungen zum Lauf…")
        self.txtNotes.setFixedHeight(100)
        self.cardBatch.body.addWidget(QLabel("Bemerkungen"))
        self.cardBatch.body.addWidget(self.txtNotes)

        # Actions
        self.cardActions = Card("Aktionen")
        grid.addWidget(self.cardActions, 1, 0)

        self.btnStart = QPushButton("▶  Test starten  (Ctrl+R)"); self.btnStart.clicked.connect(self._start_test)
        self.btnDauer = QPushButton("⏱️  Dauertest starten  (Ctrl+D)"); self.btnDauer.clicked.connect(self._toggle_dauertest)
        self.btnOpenFolder = QPushButton("📂 Ordner öffnen"); self.btnOpenFolder.setEnabled(False); self.btnOpenFolder.clicked.connect(self._open_folder)
        self.btnKleberoboter = QPushButton("Datenbank Senden"); self.btnKleberoboter.clicked.connect(self._trigger_kleberoboter)

        for b in (self.btnStart, self.btnDauer, self.btnOpenFolder, self.btnKleberoboter):
            b.setMinimumHeight(36)

        # Dauertest-Button + Dauer-Dropdown nebeneinander
        dauerRow = QHBoxLayout(); dauerRow.setSpacing(8)
        dauerRow.addWidget(self.btnDauer, 1)
        self.comboDur = QComboBox()
        self._dur_presets = [
            ("15 h (Standard)", 15*3600),
            ("1 h", 1*3600),
            ("4 h", 4*3600),
            ("8 h", 8*3600),
            ("24 h", 24*3600),
            ("30 min", 30*60),
            ("10 min", 10*60),
        ]
        for label, seconds in self._dur_presets:
            self.comboDur.addItem(label, seconds)
        self.comboDur.setFixedWidth(150)
        self.comboDur.currentIndexChanged.connect(self._on_duration_mode_changed)
        dauerRow.addWidget(self.comboDur, 0, Qt.AlignRight)

        self.cardActions.body.addWidget(self.btnStart)
        self.cardActions.body.addLayout(dauerRow)
        self.cardActions.body.addWidget(self.btnOpenFolder)
        self.cardActions.body.addWidget(self.btnKleberoboter)

        # Right: Status + Plot + QA
        self.cardStatus = Card("Status / Fortschritt")
        grid.addWidget(self.cardStatus, 0, 1)
        self.lblPhase = QLabel("—")
        self.pbar = QProgressBar(); self._reset_progress()
        self.cardStatus.body.addWidget(self.lblPhase)
        self.cardStatus.body.addWidget(self.pbar)

        # Kalibrierdaten
        self.cardStatus.body.addItem(QSpacerItem(0,6, QSizePolicy.Minimum, QSizePolicy.Fixed))
        self.lblCalibTitle = QLabel("Kalibrierung"); self.lblCalibTitle.setObjectName("CardTitle")
        self.cardStatus.body.addWidget(self.lblCalibTitle)
        self.lblCalib = QLabel("—")
        self.cardStatus.body.addWidget(self.lblCalib)

        # QA-Chips
        self.cardStatus.body.addItem(QSpacerItem(0,6, QSizePolicy.Minimum, QSizePolicy.Fixed))
        qaRow = QHBoxLayout()
        self.chipMeasQA = QLabel("Messung QA: —"); self.chipMeasQA.setObjectName("Chip")
        self.chipDurQA  = QLabel(f"Dauertest QA (Limit {resolve_stage.DUR_MAX_UM:.1f} µm): —"); self.chipDurQA.setObjectName("Chip")
        qaRow.addWidget(self.chipMeasQA); qaRow.addWidget(self.chipDurQA)
        self.cardStatus.body.addLayout(qaRow)

        # Neue Stage testen
        self.cardStatus.body.addItem(QSpacerItem(0,8, QSizePolicy.Minimum, QSizePolicy.Fixed))
        self.btnNewStage = QPushButton("✨ Neue Stage testen")
        self.btnNewStage.setVisible(False)
        self.btnNewStage.clicked.connect(self._new_stage)
        self.cardStatus.body.addWidget(self.btnNewStage)

        # Live-Plot Card
        self.cardPlot = Card("Live-Plot")
        grid.addWidget(self.cardPlot, 1, 1)
        self.lblTimer = QLabel("15:00:00"); self.lblTimer.setObjectName("Chip")
        self.cardPlot.layout().itemAt(0).layout().addWidget(self.lblTimer, 0, Qt.AlignRight)
        self.plotHolder = QVBoxLayout(); self.cardPlot.body.addLayout(self.plotHolder)

        self.stack.addWidget(self.stagePage)

        # ===================== Gitterschieber Seite =====================
        self.gitterschieberPage = QWidget()
        gsLayout = QVBoxLayout(self.gitterschieberPage)
        gsLayout.setContentsMargins(0, 0, 0, 0); gsLayout.setSpacing(14)

        gsCard = Card("Gitterschieber")
        gsLayout.addWidget(gsCard, 1)

        # Gemeinsames Kamerafenster + Aktionen
        self.gitterschieber_status = QLabel("")
        cam_provider = lambda: gs.capture_frame()
        self.gitterschieberCam = LiveCamEmbed(
            cam_provider,
            interval_ms=200,
            start_immediately=False,
            parent=self.gitterschieberPage,
        )
        gsCard.body.addWidget(self.gitterschieberCam)

        btn_bar = QHBoxLayout()
        btn_particle = QPushButton("Partikelanalyse")
        btn_angle = QPushButton("Winkel-Detektion")
        btn_particle.clicked.connect(self._on_gitterschieber_particles)
        btn_angle.clicked.connect(self._on_gitterschieber_angle)
        btn_bar.addWidget(btn_particle)
        btn_bar.addWidget(btn_angle)
        gsCard.body.addLayout(btn_bar)
        gsCard.body.addWidget(self.gitterschieber_status)

        gsLayout.addStretch(1)
        self.stack.addWidget(self.gitterschieberPage)

        # ===================== Autofocus Seite =====================
        self.autofocusPage = QWidget()
        autoLayout = QVBoxLayout(self.autofocusPage)
        autoLayout.setContentsMargins(0,0,0,0); autoLayout.setSpacing(14)
        add_back_btn(autoLayout)

        autoHero = Card("Autofocus")
        autoLayout.addWidget(autoHero)
        autoHeroImg = QLabel(); autoHeroImg.setAlignment(Qt.AlignCenter)
        pix_auto = QPixmap(af_img)
        if not pix_auto.isNull():
            autoHeroImg.setPixmap(pix_auto.scaled(620, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            autoHeroImg.setText("Autofocus Kamera")
        autoHero.body.addWidget(autoHeroImg, 0, Qt.AlignCenter)

        # Gemeinsames Kamerafenster oben
        try:
            self.autofocusCam = LiveCamEmbed(
                self._af_frame_provider,
                interval_ms=200,
                start_immediately=False,
                parent=self.autofocusPage,
            )
            autoLayout.addWidget(self.autofocusCam)
        except Exception as exc:
            autoLayout.addWidget(QLabel(f"Kamera nicht verfügbar: {exc}"))

        camCard = Card("Kamera Funktionen")
        autoLayout.addWidget(camCard)

        camGrid = QGridLayout(); camGrid.setSpacing(12); camGrid.setContentsMargins(0,0,0,0)
        camCard.body.addLayout(camGrid)

        self._autofocus_buttons = []
        cams = [
            ("Autofocus", 0, "Autofocus"),
            ("MACS Resolve 40x", 1, "MACS Resolve 40x"),
            ("Resolve 2", 2, "Resolve 2"),
            ("MacSEQ", 3, "MacSEQ"),
        ]
        for idx, (text, device_idx, label) in enumerate(cams):
            btn = QPushButton(text)
            btn.setMinimumHeight(90)
            btn.clicked.connect(lambda _, i=device_idx, lbl=label: self._open_cam_idx(i, lbl))
            camGrid.addWidget(btn, idx // 2, idx % 2)
            self._autofocus_buttons.append(btn)
        camGrid.setColumnStretch(0, 1); camGrid.setColumnStretch(1, 1)

        # --- Exposure Controls (Autofocus) ---
        expoCard = Card("Belichtung (Exposure)")
        autoLayout.addWidget(expoCard)

        expoGrid = QGridLayout()
        expoGrid.setSpacing(12)
        expoGrid.setContentsMargins(0,0,0,0)
        expoCard.body.addLayout(expoGrid)

        # Slider (µs skaliert), Spin (ms Anzeige)
        self.sliderExpo = QSlider(Qt.Horizontal)
        self.sliderExpo.setMinimum(1)
        self.sliderExpo.setMaximum(100000)   # wird gleich durch echte Limits ersetzt
        self.sliderExpo.setSingleStep(100)
        self.sliderExpo.valueChanged.connect(self._on_expo_slider)
        expoGrid.addWidget(QLabel("Exposure"), 1, 0)
        expoGrid.addWidget(self.sliderExpo,   1, 1)

        self.spinExpo = QDoubleSpinBox()
        self.spinExpo.setDecimals(3)
        self.spinExpo.setSuffix(" ms")
        self.spinExpo.setRange(0.001, 10000.0)  # 1 µs bis 10 s in ms
        self.spinExpo.setSingleStep(0.050)
        self.spinExpo.valueChanged.connect(self._on_expo_spin)
        expoGrid.addWidget(QLabel("Wert"), 2, 0)
        expoGrid.addWidget(self.spinExpo,  2, 1)

        # Limits aus Kamera lesen und UI initialisieren (Device 0 als Fallback)
        try:
            self._init_exposure_ui_from_device(0)
        except Exception as e:
            print("[WARN] Exposure-Init (device 0):", e)
            self._init_default_exposure_ui()

        autoLayout.addStretch(1)
        self.stack.addWidget(self.autofocusPage)

        # ===================== Laserscan Seite =====================
        self.laserscanPage = QWidget()
        laserLayout = QVBoxLayout(self.laserscanPage)
        laserLayout.setContentsMargins(0,0,0,0); laserLayout.setSpacing(14)
        add_back_btn(laserLayout)

        laserHero = Card("Laserscan Modul")
        laserLayout.addWidget(laserHero)
        pix_laser = QPixmap(laser_img)

        if not pix_laser.isNull():
            logoMini = QLabel()
            logoMini.setAlignment(Qt.AlignRight | Qt.AlignTop)
            logoMini.setPixmap(pix_laser.scaled(120, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            laserHero.body.addWidget(logoMini, 0, Qt.AlignRight | Qt.AlignTop)

        laserHeroImg = QLabel(); laserHeroImg.setAlignment(Qt.AlignCenter)
        if not pix_laser.isNull():
            laserHeroImg.setPixmap(pix_laser.scaled(620, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            laserHeroImg.setText("Laserscan Modul")
        laserHero.body.addWidget(laserHeroImg, 0, Qt.AlignCenter)

        laserInfo = Card("Status")
        laserLayout.addWidget(laserInfo)
        info_lbl = QLabel("Laserscan-Workflow wird vorbereitet. Bitte später erneut öffnen.")
        info_lbl.setWordWrap(True)
        laserInfo.body.addWidget(info_lbl)
        laserLayout.addStretch(1)

        self.stack.addWidget(self.laserscanPage)

        # ===================== Z-Trieb Seite =====================
        self.ztriebPage = QWidget()
        zLayout = QVBoxLayout(self.ztriebPage)
        zLayout.setContentsMargins(0, 0, 0, 0)
        zLayout.setSpacing(14)
        add_back_btn(zLayout)
        self.ztriebWidget = ZTriebWidget(self)
        zLayout.addWidget(self.ztriebWidget)
        self.stack.addWidget(self.ztriebPage)

        # ===================== Live View Seite =====================
        self.liveViewPage = QWidget()
        liveLayout = QVBoxLayout(self.liveViewPage)
        liveLayout.setContentsMargins(0, 0, 0, 0)
        liveLayout.setSpacing(14)
        add_back_btn(liveLayout)

        liveCard = Card("Live View · Produktions-Dashboard")
        liveLayout.addWidget(liveCard, 1)
        self._dashboard_widget = None
        self._dashboard_scroll = None
        if DASHBOARD_WIDGET_CLS is not None:
            try:
                self._dashboard_widget = DASHBOARD_WIDGET_CLS()
                self._dashboard_widget.setMinimumHeight(400)
                self._dashboard_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.NoFrame)
                scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                scroll.setWidget(self._dashboard_widget)
                self._dashboard_scroll = scroll
                liveCard.body.addWidget(scroll, 1)
            except Exception as exc:
                msg = QLabel(
                    f"Dashboard konnte nicht geladen werden:\n{exc}"
                )
                msg.setWordWrap(True)
                liveCard.body.addWidget(msg)
        else:
            fallback = QLabel("Dashboard-Modul nicht gefunden.")
            fallback.setWordWrap(True)
            detail = QLabel(
                "Bitte stelle sicher, dass das Dashboard-Projekt verfügbar ist."
            )
            detail.setWordWrap(True)
            liveCard.body.addWidget(fallback)
            liveCard.body.addWidget(detail)

        self.stack.addWidget(self.liveViewPage)
        liveLayout.addStretch(1)

        # Status bar
        if self.statusBar is None:
            self.statusBar = QLabel("Bereit.")
        self.statusBar.setObjectName("Chip")
        if self._pending_status:
            self.statusBar.setText(self._pending_status)
            self._pending_status = None
        statusWrap = QHBoxLayout(); statusWrap.addWidget(self.statusBar, 0, Qt.AlignLeft); statusWrap.addStretch(1)
        # Live laser center display (updated by LiveView.centerChanged)
        self.lblLaserCenter = QLabel(""); self.lblLaserCenter.setObjectName("Chip")
        statusWrap.addWidget(self.lblLaserCenter, 0, Qt.AlignRight)
        root.addLayout(statusWrap)

        # Initial den Timer passend zur Default-Dauer setzen
        self._set_duration_sec(15*3600)
        self._refresh_titles()
        self._show_stage_workflow()

    def _wire_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+R"), self, self._start_test)
        QShortcut(QKeySequence("Ctrl+D"), self, self._start_dauertest)
        QShortcut(QKeySequence("Ctrl+S"), self, self._stop_dauertest)
        QShortcut(QKeySequence("Ctrl+K"), self, self._open_autofocus_workflow)

    def _apply_initial_size(self):
        """Resize the window so it fits on smaller screens as well."""
        desired_w, desired_h = 1100, 780
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(desired_w, desired_h)
            return
        geom = screen.availableGeometry()
        margin_w = min(max(geom.width() // 14, 30), 70)
        margin_h = min(max(geom.height() // 14, 30), 90)
        width = min(desired_w, geom.width() - margin_w)
        height = min(desired_h, geom.height() - margin_h)
        if width <= 0:
            width = geom.width()
        if height <= 0:
            height = geom.height()
        self.resize(width, height)

    # ---------- Workflow Slots ----------
    def _show_stage_workflow(self):
        self.stack.setCurrentWidget(self.stagePage)
        self.btnStart.setFocus()
        self._set_status("Bereit für Stagetest – klicke »Test starten«.")

    def _open_autofocus_workflow(self):
        self.stack.setCurrentWidget(self.autofocusPage)
        try:
            if getattr(self, "autofocusCam", None):
                self.autofocusCam.start()
        except Exception:
            pass
        if self._autofocus_buttons:
            self._autofocus_buttons[0].setFocus()
        self._set_status("Autofocus – wähle eine Kamera.")

    def _open_laserscan_workflow(self):
        self.stack.setCurrentWidget(self.laserscanPage)
        self._set_status("Laserscan Modul – weitere Funktionen folgen.")

    def _open_ztrieb_workflow(self):
        self.stack.setCurrentWidget(self.ztriebPage)
        self._set_status("Z-Trieb – Objektivringversteller-Steuerung geöffnet.")

    def _open_live_view(self):
        self.stack.setCurrentWidget(self.liveViewPage)
        if self._dashboard_widget is None and DASHBOARD_WIDGET_CLS is not None:
            try:
                self._dashboard_widget = DASHBOARD_WIDGET_CLS()
                self._dashboard_widget.setMinimumHeight(400)
                self._dashboard_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                layout = self.liveViewPage.layout()
                if layout and layout.count() > 0:
                    card = layout.itemAt(0).widget()
                    if isinstance(card, Card):
                        scroll = QScrollArea()
                        scroll.setWidgetResizable(True)
                        scroll.setFrameShape(QFrame.NoFrame)
                        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                        scroll.setWidget(self._dashboard_widget)
                        self._dashboard_scroll = scroll
                        card.body.addWidget(scroll, 1)
            except Exception as exc:
                self._set_status(f"Dashboard-Start fehlgeschlagen: {exc}")
                return
        self._set_status("Live View – Produktions-Dashboard geöffnet.")

    def _open_gitterschieber(self):
        """
        Gitterschieber-Workflow innerhalb der Stage-Toolbox anzeigen.
        """
        try:
            self.stack.setCurrentWidget(self.gitterschieberPage)
            try:
                if getattr(self, "gitterschieberCam", None):
                    self.gitterschieberCam.start()
            except Exception:
                pass
            self._set_status("Gitterschieber geöffnet.")
        except Exception as exc:
            QMessageBox.warning(self, "Gitterschieber", f"Start fehlgeschlagen:\n{exc}")

    def _on_gitterschieber_particles(self):
        try:
            frame = self.gitterschieberCam.last_frame() or gs.capture_frame()
            if frame is None:
                self.gitterschieber_status.setText("Kein Bild verfügbar.")
                return
            gs.process_image(frame.copy())
            self.gitterschieber_status.setText("Partikelanalyse durchgeführt.")
        except Exception as exc:
            self.gitterschieber_status.setText(f"Fehler: {exc}")

    def _on_gitterschieber_angle(self):
        try:
            frame = self.gitterschieberCam.last_frame() or gs.capture_frame()
            if frame is None:
                self.gitterschieber_status.setText("Kein Bild verfügbar.")
                return
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            angle = gs.SingleImageGratingAngle(gray)
            self.gitterschieber_status.setText(f"Winkel: {angle:.3f}°")
        except Exception as exc:
            self.gitterschieber_status.setText(f"Fehler: {exc}")

    def _af_frame_provider(self):
        """Frame-Provider für Autofocus-Kamerakarte."""
        try:
            if self._af_cam is None:
                self._af_cam = IdsCam(index=0, set_min_exposure=False)
            frame = self._af_cam.aquise_frame()
            return frame
        except Exception as exc:
            print("[WARN] Autofocus-Kamera nicht verfügbar:", exc)
            return None

    # ---------- Helpers ----------
    def _reset_progress(self):
        self.pbar.setRange(0, 1); self.pbar.setValue(0); self.pbar.setFormat("—")

    def _refresh_titles(self):
        self.setWindowTitle(f"Stage-Toolbox · Charge: {self._batch}")
        self.chipBatch.setText(f"Charge: {self._batch}")

    def _acquire_batch(self):
        val = self.edBatch.text()
        if self.edBatch.validator() and not self.edBatch.hasAcceptableInput():
            QMessageBox.warning(self, "Charge", "Ungültige Chargennummer. Erlaubt: A-Z, a-z, 0-9, . _ - (max. 64).")
        self._batch = resolve_stage.sanitize_batch(val) or "NoBatch"
        self._refresh_titles()
        if self.plot is not None: self.plot.set_batch(self._batch)

    def _get_operator(self): return (self.edOperator.text() or "").strip()
    def _get_notes(self): return (self.txtNotes.toPlainText() or "").strip()

    def _set_status(self, text: str):
        self._pending_status = text
        lbl = getattr(self, "statusBar", None)
        if lbl is not None:
            lbl.setText(text)

    def _on_live_center_changed(self, x: int, y: int):
        """Handler for LiveView.centerChanged signal: update small status label with coords."""
        try:
            if hasattr(self, "lblLaserCenter"):
                self.lblLaserCenter.setText(f"Laser: {x}, {y}")
        except Exception:
            pass

    def _ensure_run_dir(self):
        if self._run_outdir is None:
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._run_outdir = resolve_stage.DATA_ROOT / self._batch / f"Run_{ts}"
            self._run_outdir.mkdir(parents=True, exist_ok=True)
            self._last_outdir = self._run_outdir
            self.btnOpenFolder.setEnabled(True)
        return self._run_outdir

    # ---------- Dauer-Helfer ----------
    def _fmt_hms(self, seconds: int) -> str:
        seconds = max(0, int(seconds))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _set_duration_sec(self, seconds: int, update_label: bool = True):
        self._duration_sec = max(60, int(seconds))
        if update_label and not self._dauer_running:
            self.lblTimer.setText(self._fmt_hms(self._duration_sec))

    def _on_duration_mode_changed(self, idx: int):
        try:
            secs = int(self.comboDur.itemData(idx))
        except Exception:
            secs = 15*3600
        self._set_duration_sec(secs)

    # ---------- Test ----------
    def _start_test(self):
        self._acquire_batch()
        out_dir = self._ensure_run_dir()
        self.btnStart.setEnabled(False); self._set_status("Test läuft…")
        self.thr=QThread()
        self.wrk=resolve_stage.TestWorker(self.sc, batch=self._batch); self.wrk.moveToThread(self.thr)
        self.thr.started.connect(self.wrk.run)
        self.wrk.new_phase.connect(self._phase); self.wrk.step.connect(self._step)
        self.wrk.calib.connect(self._show_calib)
        self.wrk.done.connect(self._done); self.wrk.error.connect(self._err)
        self.wrk.done.connect(self.thr.quit); self.wrk.error.connect(self.thr.quit)
        self.thr.finished.connect(lambda: (self.btnStart.setEnabled(True), self._set_status("Test beendet.")))
        self.thr.start()

    def _phase(self,name,maxi):
        self.phase=name; self.lblPhase.setText(name)
        self.pbar.setMaximum(maxi); self.pbar.setValue(0)
        self.pbar.setFormat(f"{name}: 0 / {maxi} (0%)")

    def _step(self,val):
        self.pbar.setValue(val)
        maxi = max(1, self.pbar.maximum())
        pct = int(round(100*val/maxi))
        self.pbar.setFormat(f"{self.phase}: {val} / {maxi} ({pct}%)")

    def _show_calib(self, d: dict):
        x = d.get("X_stepsPerMeter","—"); y = d.get("Y_stepsPerMeter","—")
        self._calib_vals["X"] = x; self._calib_vals["Y"] = y
        self.lblCalib.setText(f"stepsPerMeter · X: <b>{x}</b> | Y: <b>{y}</b>")

    def _done(self,data):
        out_dir=data["out"]; plots=data["plots"]; batch=data.get("batch","NoBatch")
        meas_max_um = float(data.get("meas_max_um", 0.0))
        self._run_outdir = out_dir
        self._last_outdir = out_dir
        self.btnOpenFolder.setEnabled(True)

        fig_paths = []
        max_abs_um = 0.0
        for ax,mot,enc,calc,spm,epm in plots:
            diff_um = np.abs((enc - calc)/epm*1e6)
            max_abs_um = max(max_abs_um, float(np.max(diff_um)))
            png = self._plot_and_save(ax,mot,enc,calc,spm,epm,out_dir,batch)
            fig_paths.append(str(png))
        self._meas_max_um = max_abs_um

        cal_x = out_dir / f"calib_x_{batch}.png"
        cal_y = out_dir / f"calib_y_{batch}.png"
        if cal_x.exists(): fig_paths.insert(0, str(cal_x))
        if cal_y.exists(): fig_paths.insert(1, str(cal_y))

        meas_ok = (self._meas_max_um <= MEAS_MAX_UM)
        self._set_chip(self.chipMeasQA, f"Messung QA: Max Δ = {self._meas_max_um:.2f} µm  → {'OK' if meas_ok else 'FAIL'}",
                       ok=meas_ok)

        report_path = out_dir / f"report_{batch}.pdf"
        try:
            self._write_report_pdf(report_path, fig_paths)
            print(f"[INFO][{batch}] Bericht gespeichert → {report_path}")
        except Exception as e:
            print(f"[WARN][{batch}] Konnte Bericht nicht erstellen:", e)

        self.lblPhase.setText("Fertig!"); self._reset_progress()
        print(f"[INFO][{batch}] Test abgeschlossen → {out_dir}")

        QMessageBox.information(self, "Messung abgeschlossen",
            f"Messung abgeschlossen.\nMax. Abweichung: {self._meas_max_um:.2f} µm\n"
            f"Grenze: {MEAS_MAX_UM:.2f} µm → {'OK' if meas_ok else 'NICHT OK'}")

    @staticmethod
    def _plot_and_save(axis,mot,enc,calc,spm,epm,out_dir: pathlib.Path,batch: str) -> pathlib.Path:
        diff, idx = enc-calc, np.linspace(0,1,len(mot))
        fig = Figure(figsize=(12,8),dpi=110, facecolor=BG_ELEV)
        ax1 = fig.add_subplot(221); style_ax(ax1); ax1.plot(idx,mot); ax1.set_title(f"Motorschritte · {axis}")
        ax2 = fig.add_subplot(222); style_ax(ax2); ax2.scatter(mot,diff,c=idx,cmap="viridis"); ax2.set_title(f"Encoder-Delta · {axis}")
        ax3 = fig.add_subplot(223); style_ax(ax3); ax3.plot(diff/epm*1e6); ax3.set_title("Delta (µm) vs Index")
        ax4 = fig.add_subplot(224); style_ax(ax4); ax4.scatter(mot/spm*1e3,diff/epm*1e6,c=idx,cmap="viridis"); ax4.set_title("Delta (µm) vs Weg")
        fig.suptitle(f"{axis}-Achse – Messung · Charge: {batch}", fontweight="semibold")
        fig.tight_layout()
        out_png = out_dir/f"{axis}_{batch}.png"
        fig.savefig(out_png)
        return out_png

    @staticmethod
    def _images_to_pdf(image_paths, pdf: PdfPages):
        for img_path in image_paths:
            if not os.path.exists(img_path): continue
            img = mpimg.imread(img_path)
            fig = Figure(figsize=(11.69, 8.27), dpi=110, facecolor=BG_ELEV)  # A4 quer
            ax = fig.add_subplot(111); ax.imshow(img); ax.axis("off")
            pdf.savefig(fig)

    def _write_report_pdf(self, pdf_path: pathlib.Path, image_paths):
        with PdfPages(pdf_path) as pdf:
            fig = Figure(figsize=(11.69, 8.27), dpi=110, facecolor=BG_ELEV)
            ax = fig.add_subplot(111); ax.axis("off"); fig.subplots_adjust(0.08,0.08,0.92,0.92)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            op   = self._get_operator() or "—"
            notes= self._get_notes() or "—"
            xspm = self._calib_vals.get("X","—")
            yspm = self._calib_vals.get("Y","—")
            meas_text = "OK" if (self._meas_max_um is not None and self._meas_max_um <= MEAS_MAX_UM) else "NICHT OK" if self._meas_max_um is not None else "—"
            dur_text  = "OK" if (self._dur_max_um is not None and self._dur_max_um <= resolve_stage.DUR_MAX_UM) else "NICHT OK" if self._dur_max_um is not None else "—"

            text = (
f"Stage Test Report\n\n"
f"Zeitpunkt: {now}\n"
f"Charge: {self._batch}\n"
f"Operator: {op}\n\n"
f"Kalibrierung (stepsPerMeter):\n"
f"  X: {xspm}\n  Y: {yspm}\n\n"
f"Bemerkungen:\n{notes}\n\n"
f"QA-Grenzen:\n"
f"  Messung: ≤ {MEAS_MAX_UM:.1f} µm  |  Ergebnis: {self._meas_max_um:.2f} µm  → {meas_text}\n"
f"  Dauertest: ≤ {resolve_stage.DUR_MAX_UM:.1f} µm |  Ergebnis: {self._dur_max_um if self._dur_max_um is not None else float('nan'):.2f} µm  → {dur_text}\n"
            )
            ax.text(0.02, 0.98, text, va="top", ha="left", fontsize=12, color=FG)
            pdf.savefig(fig)
            self._images_to_pdf(image_paths, pdf)

    def _set_chip(self, lbl: QLabel, text: str, ok: bool):
        lbl.setText(text)
        color = "#2ecc71" if ok else "#ff2740"
        lbl.setText(f'<span style="color:{color}">{text}</span>')

    def _err(self,msg):
        self._set_status("Fehler."); QMessageBox.critical(self,"Fehler",msg)

    def _open_cam_idx(self, idx: int, label: str):
        self._acquire_batch()
        try:
            detector = LaserSpotDetector()
            win = CameraWindow(
                self,
                batch=self._batch,
                device_index=idx,
                label=label,
                spot_detector=detector,
            )
            if getattr(win, "live", None) and getattr(win.live, "is_dummy", False) and not self._warned_camera_sim:
                QMessageBox.information(self, "Kamera", "IDS-Kameras nicht verfuegbar - oeffne Demo-Fenster.")
                self._warned_camera_sim = True
            try:
                us = int(self.sliderExpo.value())
                if us <= 0:
                    us = max(1, self.sliderExpo.minimum())
                win.live.set_exposure_us(us)
            except Exception as e:
                print(f"[WARN] Could not set exposure on open: {e}")
            win.show()
            self._cam_windows.append(win)
            try:
                win.closed.connect(lambda _, w=win: self._remove_cam_window(w))
            except Exception:
                pass
            try:
                win.live.centerChanged.connect(self._on_live_center_changed)
            except Exception:
                pass
            self._set_status(f"Kamera geoeffnet: {label} (Index {idx})")
            try:
                cur, mn, mx = win.live.get_exposure_limits_us()
                self._init_exposure_ui_from_limits(cur_us=cur, min_us=mn, max_us=mx)
            except Exception:
                self._init_default_exposure_ui()
        except Exception as exc:
            self._err(str(exc))

    # ---------- Dauertest (kleine + große Bewegungen) ----------
    def _toggle_dauertest(self):
        if self._dauer_running:
            self._stop_dauertest()
        else:
            self._start_dauertest()

    def _set_dauer_button(self, running: bool):
        if running:
            self.btnDauer.setText("■  Dauertest stoppen  (Ctrl+S)")
        else:
            self.btnDauer.setText("⏱️  Dauertest starten  (Ctrl+D)")
        self.btnDauer.setEnabled(True)

    def _start_dauertest(self):
        if self._dauer_running: return
        self._acquire_batch()
        out_dir = self._ensure_run_dir()

        if self.plot is None:
            self.plot=LivePlot(self, batch=self._batch); self.plotHolder.addWidget(self.plot)
        else:
            self.plot.set_batch(self._batch)
            self.plot.reset()
        self.plot.set_mode("Dauertest")

        self._dauer_running = True
        self._set_dauer_button(True)

        try:
            x_center, y_center, _ = resolve_stage.get_current_pos()
        except Exception as exc:
            QMessageBox.warning(self, "Dauertest", f"Referenzposition konnte nicht gelesen werden:\n{exc}")
            self._dauer_running = False
            self._set_dauer_button(False)
            return

        # Parameter: kleine Bewegungen + große Bewegungen (Verhältnis 120s : 30s)
        avail_x = max(0, self.sc.high_lim.get("X", 0) - self.sc.low_lim.get("X", 0))
        avail_y = max(0, self.sc.high_lim.get("Y", 0) - self.sc.low_lim.get("Y", 0))
        avail_range = max(1, min(avail_x, avail_y))
        small_step = max(500, int(avail_range * 0.01))
        small_radius = max(2000, int(avail_range * 0.05))

        self.lblPhase.setText(f"Dauertest (120s klein / 30s groß)")
        self.pbar.setMaximum(self._duration_sec)
        self.pbar.setValue(0)
        self.pbar.setFormat(f"Dauertest: 0 / {self._fmt_hms(self._duration_sec)}")
        self._set_status(f"Dauertest läuft (Step {small_step}, Radius {small_radius})…")

        self._dauer_start  = time.time()
        self._dauer_target = self._dauer_start + self._duration_sec
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()
        self.timer=QTimer(self); self.timer.timeout.connect(self._update_timer); self.timer.start(1000)

        self.dauer_thread=QThread()
        self.dauer_worker=resolve_stage.CombinedTestWorker(
            self.sc,
            batch=self._batch,
            out_dir=out_dir,
            center_x=x_center,
            center_y=y_center,
            small_step=small_step,
            small_radius=small_radius,
            dwell_small=0.2,
            dwell_large=0.1,
            limit_um=resolve_stage.DUR_MAX_UM,
            stop_at_ts=self._dauer_target,
            small_phase_sec=120.0,
            large_phase_sec=30.0,
        )
        self.dauer_worker.moveToThread(self.dauer_thread)
        self.dauer_thread.started.connect(self.dauer_worker.run)
        self.dauer_worker.update.connect(self._live_update_dur)
        self.dauer_worker.finished.connect(self._dauer_finished)
        self.dauer_worker.error.connect(self._dauer_error)
        self.dauer_worker.finished.connect(lambda *_: self.dauer_thread.quit())
        self.dauer_worker.error.connect(lambda *_: self.dauer_thread.quit())
        self.dauer_thread.finished.connect(self.dauer_worker.deleteLater)
        self.dauer_thread.finished.connect(self.dauer_thread.deleteLater)
        self.dauer_thread.start()

    def _live_update_dur(self, data: dict):
        if self.plot:
            try:
                self.plot.add_data(data)
            except Exception as e:
                print("[WARN] Dauertest Plot-Update fehlgeschlagen:", e)
        phase = data.get("phase", "—")
        idx = int(data.get("idx", 0))
        total = int(data.get("total", 1))
        max_um = float(data.get("max_abs_um", 0.0))
        limit  = float(data.get("limit_um", resolve_stage.DUR_MAX_UM))
        ok = (max_um <= limit)
        self._set_chip(self.chipDurQA, f"Dauertest QA (Limit {limit:.1f} µm): Max = {max_um:.2f} µm → {'OK' if ok else 'WARN/FAIL'}", ok=ok)
        elapsed_sec = max(0, int(float(data.get("t", 0.0)) * 60))
        self.pbar.setMaximum(self._duration_sec)
        self.pbar.setValue(min(elapsed_sec, self._duration_sec))
        pct = int(round(100 * self.pbar.value() / max(1, self._duration_sec)))
        self.pbar.setFormat(f"Dauertest: {self._fmt_hms(self.pbar.value())} / {self._fmt_hms(self._duration_sec)} ({pct}%)")
        self.lblPhase.setText(f"Dauertest · {phase} · Fehler {float(data.get('err_um',0.0)):.2f} µm (Max {max_um:.2f} µm)")

    def _dauer_finished(self, d):
        print(f"[INFO][{self._batch}] Dauertest abgeschlossen → {d.get('out')}")
        self._set_status("Dauertest beendet.")
        self._dauer_running = False
        self._set_dauer_button(False)

        outdir = self._ensure_run_dir()
        try:
            if self.plot is not None:
                out_png = outdir / f"dauertest_{self._batch}.png"
                self.plot.figure.savefig(out_png, dpi=110)
                print(f"[INFO][{self._batch}] Live-Plot gespeichert → {out_png}")
        except Exception as e:
            print(f"[WARN][{self._batch}] Konnte Live-Plot nicht speichern:", e)

        self._dur_max_um = float(d.get("dur_max_um", 0.0))
        limit = float(d.get("limit_um", resolve_stage.DUR_MAX_UM))
        dur_ok = (self._dur_max_um <= limit)
        self._set_chip(self.chipDurQA, f"Dauertest QA (Limit {limit:.1f} µm): Max = {self._dur_max_um:.2f} µm → {'OK' if dur_ok else 'FAIL'}",
                       ok=dur_ok)

        try:
            images = []
            for name in [f"calib_x_{self._batch}.png", f"calib_y_{self._batch}.png",
                         f"X_{self._batch}.png", f"Y_{self._batch}.png",
                         f"dauertest_{self._batch}.png"]:
                f = outdir / name
                if f.exists(): images.append(str(f))
            report_path = outdir / f"report_{self._batch}.pdf"
            self._write_report_pdf(report_path, images)
            print(f"[INFO][{self._batch}] Bericht aktualisiert → {report_path}")
        except Exception as e:
            print(f"[WARN][{self._batch}] Konnte Bericht nicht aktualisieren:", e)

        QMessageBox.information(self, "Dauertest abgeschlossen",
            f"Dauertest abgeschlossen.\nMax. Abweichung: {self._dur_max_um:.2f} µm\n"
            f"Grenze: {limit:.2f} µm → {'OK' if dur_ok else 'NICHT OK'}")

        self.btnNewStage.setVisible(True)
        try:
            resp = QMessageBox.question(
                self, "Neue Stage testen?",
                "Dauertest beendet. Möchtest du jetzt die Parameter für eine neue Stage zurücksetzen?",
                QMessageBox.Yes | QMessageBox.No
            )
            if resp == QMessageBox.Yes:
                self._new_stage()
            else:
                self._set_status("Bereit für neue Stage – klicke »✨ Neue Stage testen« zum Zurücksetzen.")
        except Exception as e:
            print("[WARN] Frage nach Reset fehlgeschlagen:", e)

    def _dauer_error(self, msg: str):
        QMessageBox.warning(self, "Dauertest", f"Fehler:\n{msg}")
        self._dauer_running = False
        self._set_dauer_button(False)
        self._set_status("Dauertest fehlgeschlagen.")

    def _stop_dauertest(self):
        if not self._dauer_running: return
        if hasattr(self,'dauer_worker'): self.dauer_worker.stop()
        if hasattr(self,'timer') and self.timer.isActive(): self.timer.stop()
        self.lblTimer.setText("00:00:00")
        self._set_status("Dauertest manuell gestoppt.")
        self._dauer_running=False
        self._set_dauer_button(False)

    def _update_timer(self):
        if not hasattr(self, "_dauer_target"): return
        remaining = int(self._dauer_target - time.time())
        if remaining > 0:
            self.lblTimer.setText(self._fmt_hms(remaining))
        else:
            self._stop_dauertest()

    # ---------- Reset ----------
    def _new_stage(self):
        if getattr(self, "_dauer_running", False):
            self._stop_dauertest()
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()

        self.edBatch.clear(); self.edOperator.clear(); self.txtNotes.clear()
        self._batch = "NoBatch"; self._refresh_titles()
        self.lblPhase.setText("—"); self._reset_progress()
        self.lblCalib.setText("—"); self._calib_vals = {"X": None, "Y": None}
        self._meas_max_um = None; self._dur_max_um = None
        self._set_chip(self.chipMeasQA, "Messung QA: —", ok=True)
        self._set_chip(self.chipDurQA, f"Dauertest QA (Limit {resolve_stage.DUR_MAX_UM:.1f} µm): —", ok=True)

        self.lblTimer.setText(self._fmt_hms(self._duration_sec))
        self._set_dauer_button(False)
        self.btnNewStage.setVisible(False)
        self._dauer_running = False
        self._dauer_start = None
        self._dauer_target = None

        if self.plot is not None:
            self.plot.t.clear(); self.plot.ex.clear(); self.plot.ey.clear()
            self.plot.line_ex.set_data([], [])
            self.plot.line_ey.set_data([], [])
            self.plot.set_batch(self._batch)
            self.plot.draw_idle()

        try:
            self.sc.move_abs('X', self.sc.home_pos['X'])
            self.sc.move_abs('Y', self.sc.home_pos['Y'])
        except Exception as e:
            print("[WARN] Konnte Stage nicht nach Home fahren:", e)

        self._run_outdir = None
        if self._last_outdir is None:
            self.btnOpenFolder.setEnabled(False)

    # ---------- Ordner öffnen ----------
    def _open_folder(self):
        path = self._last_outdir if self._last_outdir else pathlib.Path(".")
        try:
            self._open_in_file_manager(str(pathlib.Path(path).resolve()))
        except Exception as e:
            QMessageBox.warning(self, "Ordner öffnen", f"Konnte Ordner nicht öffnen:\n{e}")

    def _trigger_kleberoboter(self):
        """Send the Kleberoboter payload via the Zwischen-Raspi socket."""
        self.btnKleberoboter.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._set_status("Sende Payload an Gateway…")
            payload, ack = db.send_dummy_payload_gateway(
                device_id="kleberoboter",
                server_ip=db.GATEWAY_SERVER_IP,
                port=db.GATEWAY_PORT,
            )
        except Exception as e:
            self._set_status("Senden an Datenbank fehlgeschlagen.")
            QMessageBox.warning(self, "Kleberoboter", f"Senden fehlgeschlagen:\n{e}")
        else:
            info = "Kleberoboter-Payload gesendet."
            if ack:
                info += f"\nACK: {ack}"
            info += f"\nServer: {db.GATEWAY_SERVER_IP}:{db.GATEWAY_PORT}"
            info += f"\nBarcode: {payload.get('barcodenummer')}"
            QMessageBox.information(self, "Kleberoboter", info)
            self._set_status("Datenbankübertragung abgeschlossen.")
        finally:
            QApplication.restoreOverrideCursor()
            self.btnKleberoboter.setEnabled(True)

    def _sn_match(self, query: str, candidate: str) -> bool:
        """Case-insensitive match helper that ignores spaces/underscores/dashes."""
        if not query:
            return True
        cand_low = candidate.lower()
        q_low = query.lower()
        if q_low in cand_low:
            return True
        def _normalize(s: str) -> str:
            return re.sub(r"[\s_-]+", "", s.lower())
        q_norm = _normalize(query)
        c_norm = _normalize(candidate)
        if q_norm and q_norm in c_norm:
            return True
        tokens = [tok for tok in re.split(r"[\s]+", q_low) if tok]
        return all(tok in cand_low for tok in tokens)

    def _on_search_sn(self):
        """Search for a serial number under the Stage-Teststand data root and show results in a dialog.
        Matches both directory names and file names (case-insensitive substring).
        Double-click or select+Open will open the path with the system file manager.
        """
        try:
            sn = (self.edSearchSN.text() or "").strip()
            if not sn:
                QMessageBox.information(self, "Find SN", "Bitte Seriennummer eingeben.")
                return

            root = resolve_stage.DATA_ROOT
            if not root.exists():
                QMessageBox.warning(self, "Find SN", f"Stage-Teststand-Ordner nicht gefunden:\n{root}")
                return

            matches = []
            # Walk filesystem, collect dir and file matches
            for dirpath, dirnames, filenames in os.walk(root):
                for d in dirnames:
                    if self._sn_match(sn, d):
                        matches.append(str(pathlib.Path(dirpath) / d))
                for f in filenames:
                    if self._sn_match(sn, f):
                        matches.append(str(pathlib.Path(dirpath) / f))
                if len(matches) >= 500:
                    break

            if not matches:
                QMessageBox.information(self, "Find SN", f"Keine Treffer für '{sn}' gefunden.")
                return

            dlg = QDialog(self)
            dlg.setWindowTitle(f"Treffer für: {sn}")
            lay = QVBoxLayout(dlg)
            lay.addWidget(QLabel(f"Gefundene Pfade: {len(matches)}"))
            listw = QListWidget(); listw.addItems(matches)
            lay.addWidget(listw)

            btns = QHBoxLayout()
            btns.addStretch(1)
            open_btn = QPushButton("Öffnen")
            close_btn = QPushButton("Schließen")
            btns.addWidget(open_btn); btns.addWidget(close_btn)
            lay.addLayout(btns)

            def _open_sel():
                it = listw.currentItem()
                if not it:
                    return
                path = pathlib.Path(it.text())
                try:
                    # If it's an image, preview it inline; otherwise open in file manager
                    if path.is_file() and path.suffix.lower() in ('.png','.jpg','.jpeg','.bmp','.tif','.tiff'):
                        try:
                            dlg2 = QDialog(self)
                            dlg2.setWindowTitle(path.name)
                            v2 = QVBoxLayout(dlg2)
                            lbl2 = QLabel(); lbl2.setAlignment(Qt.AlignCenter)
                            pm2 = QPixmap(str(path.resolve()))
                            if pm2.isNull():
                                raise RuntimeError('Could not load image')
                            lbl2.setPixmap(pm2.scaled(900, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                            v2.addWidget(lbl2)
                            btns2 = QHBoxLayout(); btns2.addStretch(1)
                            open_btn2 = QPushButton("Öffnen im Dateimanager")
                            close_btn2 = QPushButton("Schließen")
                            btns2.addWidget(open_btn2); btns2.addWidget(close_btn2)
                            v2.addLayout(btns2)
                            open_btn2.clicked.connect(lambda: self._open_in_file_manager(str(path.resolve())))
                            close_btn2.clicked.connect(dlg2.accept)
                            dlg2.resize(920, 760)
                            dlg2.exec()
                        except Exception as e:
                            QMessageBox.warning(self, "Vorschau", f"Konnte Bild nicht anzeigen:\n{e}")
                    else:
                        # Use existing helper (opens files or folders)
                        self._open_in_file_manager(str(path.resolve()))
                except Exception as e:
                    QMessageBox.warning(self, "Öffnen", f"Konnte nicht öffnen:\n{e}")

            open_btn.clicked.connect(_open_sel)
            close_btn.clicked.connect(dlg.accept)
            listw.itemDoubleClicked.connect(lambda it: (listw.setCurrentItem(it), _open_sel()))

            dlg.resize(800, 400)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Find SN", f"Fehler bei der Suche:\n{e}")

    # ---------- Live Search Helpers ----------
    def eventFilter(self, obj, event):
        # allow arrow/enter navigation from the line edit into the popup
        try:
            if obj is getattr(self, 'edSearchSN', None) and event.type() == QEvent.KeyPress:
                key = event.key()
                if key == Qt.Key_Down and getattr(self, '_sn_popup', None) and self._sn_popup.isVisible():
                    self._sn_popup.activateWindow()
                    self._sn_popup.setFocus()
                    if self._sn_popup.count() > 0:
                        self._sn_popup.setCurrentRow(0)
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter) and getattr(self, '_sn_popup', None) and self._sn_popup.isVisible():
                    it = self._sn_popup.currentItem() or (self._sn_popup.item(0) if self._sn_popup.count() else None)
                    if it:
                        self._open_selected_sn(it)
                        return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _on_search_sn_live(self, text: str):
        # debounce incoming text changes; require at least 2 chars to search
        try:
            if not text or len(text.strip()) < 2:
                try:
                    if getattr(self, '_sn_popup', None):
                        self._sn_popup.hide()
                except Exception:
                    pass
                try: self._search_timer.stop()
                except Exception: pass
                return
            # restart debounce
            try:
                self._search_timer.start()
            except Exception:
                # fallback to immediate
                self._perform_search_sn()
        except Exception:
            pass

    def _perform_search_sn(self):
        # actual filesystem scan for the current edit contents
        try:
            sn = (self.edSearchSN.text() or "").strip()
            if not sn:
                if getattr(self, '_sn_popup', None): self._sn_popup.hide()
                return
            root = resolve_stage.DATA_ROOT
            results = []
            if root.exists():
                for dirpath, dirnames, filenames in os.walk(root):
                    for d in dirnames:
                        if self._sn_match(sn, d):
                            results.append(str(pathlib.Path(dirpath) / d))
                    for f in filenames:
                        if self._sn_match(sn, f):
                            results.append(str(pathlib.Path(dirpath) / f))
                    if len(results) >= 200:
                        break

            if not results:
                try: self._sn_popup.hide()
                except Exception: pass
                return

            # update popup list
            self._sn_popup.clear()
            self._sn_popup.addItems(results[:200])

            # position popup under the line edit
            try:
                geo = self.edSearchSN.geometry()
                pos = self.edSearchSN.mapToGlobal(self.edSearchSN.rect().bottomLeft())
                width = max(self.edSearchSN.width(), 420)
                row_h = self._sn_popup.sizeHintForRow(0) or 20
                h = min(12, len(results)) * row_h + 12
                self._sn_popup.move(pos)
                self._sn_popup.resize(width, h)
                self._sn_popup.show()
                self.edSearchSN.setFocus(Qt.OtherFocusReason)
            except Exception:
                try: self._sn_popup.show()
                except Exception: pass
        except Exception as e:
            print("[WARN] live search failed:", e)
            try: self._sn_popup.hide()
            except: pass

    def _open_selected_sn(self, item):
        try:
            if item is None: return
            path = pathlib.Path(item.text())
            if not path.exists():
                QMessageBox.warning(self, "Öffnen", f"Pfad nicht gefunden: {path}")
                return
            self._sn_popup.hide()
            # If it's an image, show an internal preview dialog; otherwise open in file manager
            if path.is_file() and path.suffix.lower() in ('.png','.jpg','.jpeg','.bmp','.tif','.tiff'):
                try:
                    dlg = QDialog(self)
                    dlg.setWindowTitle(path.name)
                    v = QVBoxLayout(dlg)
                    lbl = QLabel(); lbl.setAlignment(Qt.AlignCenter)
                    pm = QPixmap(str(path.resolve()))
                    if pm.isNull():
                        raise RuntimeError('Could not load image')
                    lbl.setPixmap(pm.scaled(900, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    v.addWidget(lbl)
                    btns = QHBoxLayout(); btns.addStretch(1)
                    open_btn = QPushButton("Öffnen im Dateimanager")
                    close_btn = QPushButton("Schließen")
                    btns.addWidget(open_btn); btns.addWidget(close_btn)
                    v.addLayout(btns)
                    open_btn.clicked.connect(lambda: self._open_in_file_manager(str(path.resolve())))
                    close_btn.clicked.connect(dlg.accept)
                    dlg.resize(920, 760)
                    dlg.exec()
                except Exception as e:
                    QMessageBox.warning(self, "Vorschau", f"Konnte Bild nicht anzeigen:\n{e}")
            else:
                self._open_in_file_manager(str(path.resolve()))
        except Exception as e:
            QMessageBox.warning(self, "Öffnen", f"Konnte nicht öffnen:\n{e}")

    def _is_image_file(self, path: pathlib.Path) -> bool:
        return path.is_file() and path.suffix.lower() in ('.png','.jpg','.jpeg','.bmp','.tif','.tiff')

    @staticmethod
    def _open_in_file_manager(path: str):
        pth = pathlib.Path(path)
        if not pth.exists():
            raise FileNotFoundError(f"Pfad existiert nicht: {pth}")
        if sys.platform.startswith("win"):
            os.startfile(str(pth))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(pth)])
        else:
            subprocess.Popen(["xdg-open", str(pth)])

    # ---------- Exposure Helpers ----------
    def _on_expo_slider(self, us_val):
        # Slider ist in µs skaliert
        ms = max(0.001, us_val / 1000.0)
        if abs(self.spinExpo.value() - ms) > 1e-6:
            self.spinExpo.blockSignals(True)
            self.spinExpo.setValue(ms)
            self.spinExpo.blockSignals(False)
        # Apply to open camera windows; fallback to device 0 when none open
        try:
            print(f"[INFO] Slider changed: applying {us_val} µs to open windows ({len(self._cam_windows)} open)")
        except Exception:
            print(f"[INFO] Slider changed: applying {us_val} µs")
        self._apply_exposure_to_open_windows(us_val)
        if not getattr(self, "_cam_windows", None):
            try: self._apply_exposure_to_device(0, us_val)
            except: pass

    def _on_expo_spin(self, ms_val):
        us = int(max(1, round(ms_val * 1000.0)))
        if self.sliderExpo.value() != us:
            self.sliderExpo.blockSignals(True)
            self.sliderExpo.setValue(us)
            self.sliderExpo.blockSignals(False)
        try:
            print(f"[INFO] Spin changed: applying {us} µs to open windows ({len(self._cam_windows)} open)")
        except Exception:
            print(f"[INFO] Spin changed: applying {us} µs")
        self._apply_exposure_to_open_windows(us)
        if not getattr(self, "_cam_windows", None):
            try: self._apply_exposure_to_device(0, us)
            except: pass

    def _init_default_exposure_ui(self, cur_us: int = 2000, min_us: int = 50, max_us: int = 200000):
        self._init_exposure_ui_from_limits(cur_us, min_us, max_us, status_prefix="Exposure (Demo)")

    def _init_exposure_ui_from_limits(self, cur_us: float, min_us: float, max_us: float, *, status_prefix: str = "Exposure"):
        cur_us = int(max(min_us, min(max_us, cur_us)))
        self.sliderExpo.blockSignals(True)
        self.spinExpo.blockSignals(True)
        self.sliderExpo.setMinimum(int(min_us))
        self.sliderExpo.setMaximum(int(max_us))
        self.sliderExpo.setValue(int(cur_us))
        self.spinExpo.setMinimum(float(min_us) / 1000.0)
        self.spinExpo.setMaximum(float(max_us) / 1000.0)
        self.spinExpo.setValue(float(cur_us) / 1000.0)
        self.sliderExpo.blockSignals(False)
        self.spinExpo.blockSignals(False)
        self._set_status(f"{status_prefix}: {cur_us/1000.0:.3f} ms (Range {min_us/1000.0:.3f}-{max_us/1000.0:.3f} ms)")

    def _init_exposure_ui_from_device(self, device_index: int):
        """Initialize exposure UI from an IdsCam instance or open live view."""
        live = self._get_live_controller_if_open(device_index)
        if live is not None:
            cur, mn, mx = live.get_exposure_limits_us()
            self._init_exposure_ui_from_limits(cur, mn, mx)
            return
        try:
            cam = IdsCam(index=device_index, set_min_exposure=False)
        except Exception as exc:
            print(f"[WARN] Exposure-Init (device {device_index}): {exc}")
            self._init_default_exposure_ui()
            return
        try:
            cur, mn, mx = cam.get_exposure_limits_us()
            self._init_exposure_ui_from_limits(cur, mn, mx)
        except Exception as exc:
            print(f"[WARN] Exposure-Init (device {device_index}): {exc}")
            self._init_default_exposure_ui()
        finally:
            try:
                cam.shutdown()
            except Exception:
                pass

    def _get_live_controller_if_open(self, device_index: int):
        for win in list(getattr(self, "_cam_windows", [])):
            try:
                if getattr(win, "is_closed", False):
                    continue
                live = getattr(win, "live", None)
                if live and getattr(live, "device_index", None) == device_index:
                    return live
            except Exception:
                continue
        return None

    def _remove_cam_window(self, win):
        try:
            self._cam_windows = [w for w in self._cam_windows if w is not win]
        except Exception:
            pass

    def _apply_exposure_to_device(self, device_index: int, exposure_us: int):
        """Set exposure on a device even when no live window is open."""
        live = self._get_live_controller_if_open(device_index)
        if live is not None:
            live.set_exposure_us(int(exposure_us))
            return
        try:
            cam = IdsCam(index=device_index, set_min_exposure=False)
        except Exception as exc:
            self._set_status(f"Exposure (Demo) gespeichert: {exposure_us/1000.0:.3f} ms")
            print(f"[WARN] Exposure-Kamera nicht verfuegbar: {exc}")
            return
        try:
            cam.set_exposure_us(int(exposure_us))
            self._set_status(f"Exposure gesetzt: {exposure_us/1000.0:.3f} ms")
        except Exception as exc:
            print(f"[WARN] Exposure setzen fehlgeschlagen: {exc}")
        finally:
            try:
                cam.shutdown()
            except Exception:
                pass

    def _apply_exposure_to_open_windows(self, exposure_us: int):
        """Wendet die Exposure-Einstellung auf alle aktuell offenen Kamerafenster an."""
        for win in list(getattr(self, "_cam_windows", [])):
            try:
                if getattr(win, "is_closed", False):
                    continue
                if hasattr(win, "live"):
                    win.live.set_exposure_us(int(exposure_us))
            except Exception as e:
                print("[WARN] Could not apply exposure to open window:", e)

# ================================================================
# MAIN
# ================================================================
if __name__=="__main__":
    app=QApplication(sys.argv)
    apply_dark_theme(app)
    gui=StageGUI(); gui.show()
    sys.exit(app.exec())

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

import csv
import datetime
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time
import threading
from typing import Optional

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
    QFrame, QSizePolicy, QSpacerItem, QComboBox, QSpinBox, QToolButton,
    QStackedWidget, QSlider, QDoubleSpinBox, QDialog, QListWidget,
    QScrollArea
)
from PySide6.QtGui     import (
    QPixmap, QPalette, QColor, QFont, QShortcut, QKeySequence,
    QRegularExpressionValidator, QIcon, QImage
)

import gitterschieber as gs
from ids_camera import IdsCam
from laser_spot_detection import LaserSpotDetector
from z_trieb import ZTriebWidget

# ========================== DATENBANK / INFRA ==========================
BASE_DIR = pathlib.Path(__file__).resolve().parent

def resolve_data_root() -> pathlib.Path:
    """Find the Stage-Teststand root, honoring env override and existing directories."""
    candidates: list[pathlib.Path] = []
    env_root = os.environ.get("STAGE_TOOLBOX_DATA_ROOT")
    if env_root:
        candidates.append(pathlib.Path(env_root).expanduser())
    home_candidate = pathlib.Path.home() / "Stage-Teststand"
    if home_candidate not in candidates:
        candidates.append(home_candidate)
    base_candidate = BASE_DIR / "Stage-Teststand"
    if base_candidate not in candidates:
        candidates.append(base_candidate)
    cwd_candidate = pathlib.Path.cwd() / "Stage-Teststand"
    if cwd_candidate not in candidates:
        candidates.append(cwd_candidate)

    for path in candidates:
        try:
            if path.exists():
                return path.resolve()
        except OSError:
            continue

    fallback = candidates[0] if candidates else (BASE_DIR / "Stage-Teststand")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve()

DATA_ROOT = resolve_data_root()

def _resolve_dashboard_widget():
    candidates = [
        BASE_DIR / "dashboard",
        BASE_DIR.parent / "dashboard",
        BASE_DIR.parent / "Pr\u00fcfstände" / "Datenbank",
        pathlib.Path.home() / "Pr\u00fcfstände" / "Datenbank",
    ]
    dashboard_cls = None
    dashboard_error = None
    for candidate in candidates:
        try:
            dash_py = candidate / "dashboard.py"
        except Exception:
            continue
        if not dash_py.exists():
            continue
        path_str = str(candidate)
        if path_str not in sys.path:
            sys.path.append(path_str)
        try:
            from dashboard import Dashboard as _DashboardWidget
        except Exception as exc:  # pragma: no cover - optional import
            dashboard_error = exc
            continue
        else:
            dashboard_cls = _DashboardWidget
            break
    return dashboard_cls, dashboard_error

DASHBOARD_WIDGET_CLS, _DASHBOARD_IMPORT_ERROR = _resolve_dashboard_widget()

KLEBEROBOTER_SERVER_IP = os.environ.get("KLEBEROBOTER_SERVER_IP", "10.3.141.1")
try:
    KLEBEROBOTER_PORT = int(os.environ.get("KLEBEROBOTER_PORT", "5000"))
except (TypeError, ValueError):
    KLEBEROBOTER_PORT = 5000
try:
    KLEBEROBOTER_BARCODE = int(os.environ.get("KLEBEROBOTER_BARCODE", "999911200301203102103142124"))
except (TypeError, ValueError):
    KLEBEROBOTER_BARCODE = 999911200301203102103142124
RASPI_WIFI_SSID = os.environ.get("RASPI_WIFI_SSID", "raspi-webgui")

def _current_wifi_ssid() -> str | None:
    """Return the currently connected Wi-Fi SSID if available."""
    nmcli_path = shutil.which("nmcli")
    if nmcli_path:
        try:
            res = subprocess.run(
                [nmcli_path, "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            pass
        else:
            for line in res.stdout.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1] or None
    iwgetid_path = shutil.which("iwgetid")
    if iwgetid_path:
        try:
            res = subprocess.run(
                [iwgetid_path, "-r"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            pass
        else:
            ssid = res.stdout.strip()
            return ssid or None
    return None

def ensure_raspi_wifi_connected(target_ssid: str, timeout: float = 15.0) -> None:
    """
    Ensure the PC is connected to the raspi-webgui WLAN, trying to auto-connect if necessary.
    Raises RuntimeError if the connection could not be established.
    """
    if not target_ssid:
        return
    current = _current_wifi_ssid()
    if current == target_ssid:
        return
    nmcli_path = shutil.which("nmcli")
    if not nmcli_path:
        raise RuntimeError(f"WLAN '{target_ssid}' nicht verbunden und 'nmcli' ist nicht verfügbar.")
    try:
        subprocess.run(
            [nmcli_path, "dev", "wifi", "connect", target_ssid],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(f"WLAN-Verbindung zu '{target_ssid}' fehlgeschlagen:\n{err}") from exc

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if _current_wifi_ssid() == target_ssid:
            return
    raise RuntimeError(f"Konnte keine Verbindung zu '{target_ssid}' herstellen.")

def send_kleberoboter_payload(
    server_ip: str | None = None,
    port: int | None = None,
    barcode: int | None = None,
) -> tuple[dict, str | None]:
    """
    Send the Kleberoboter payload to the configured Raspberry Pi relay and return payload + ACK text.
    """
    ip = server_ip or KLEBEROBOTER_SERVER_IP
    port = port or KLEBEROBOTER_PORT
    barcode_value = barcode or KLEBEROBOTER_BARCODE
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "device_id": "kleberoboter",
        "barcodenummer": barcode_value,
        "startTime": now_iso,
        "endTime": now_iso,
        "result": "ok",
    }
    message = (json.dumps(payload) + "\n").encode("utf-8")
    ack: str | None = None
    with socket.create_connection((ip, port), timeout=3) as conn:
        conn.sendall(message)
        conn.settimeout(1.0)
        try:
            data = conn.recv(256)
            ack = data.decode().strip() if data else None
            if ack == "":
                ack = None
        except socket.timeout:
            ack = None
    return payload, ack

matplotlib.use("qtagg")

try:
    import pmacspy as _pmac_module
except Exception as exc:  # pragma: no cover - hardware import fails during simulation
    _pmac_module = None
    _PMAC_IMPORT_ERROR = exc
else:
    _PMAC_IMPORT_ERROR = None


class _DummyPMACBackend:
    """Minimal in-memory replacement so the GUI works without a PMAC."""

    def __init__(self):
        base_cfg = {
            "limitLowSteps": -500_000,
            "limitHighSteps": 500_000,
            "homeStepPosition": 0,
            "stepsPerMeter": 200_000,
            "encoderStepsPerMeter": 205_000,
        }
        self._state = {"X": 0, "Y": 0, "Z": 0}
        self._encoders = {"X": 0, "Y": 0, "Z": 0}
        self._config = {axis: dict(base_cfg) for axis in self._state}

    def _split(self, path: str, section: str) -> tuple[str | None, str | None]:
        if section not in path:
            return None, None
        try:
            after = path.split(f"{section}/", 1)[1]
            axis, key = after.split("/", 1)
            return axis.upper(), key
        except Exception:
            return None, None

    def _update_encoder(self, axis: str):
        steps_per_m = float(self._config[axis].get("stepsPerMeter") or 1.0)
        enc_per_m = float(self._config[axis].get("encoderStepsPerMeter") or steps_per_m)
        step_pos = float(self._state.get(axis, 0))
        if steps_per_m == 0:
            steps_per_m = 1.0
        self._encoders[axis] = int(round(step_pos / steps_per_m * enc_per_m))

    def connect(self, uri: str):
        print(f"[SIM][PMAC] Verbinde zu {uri} (Dummy-Modus).")
        return f"sim://{uri}"

    def getParam(self, path: str):
        axis, key = self._split(path, "MicroscopeState")
        if axis and key == "stepPosition":
            return int(self._state.get(axis, 0))
        axis, key = self._split(path, "MicroscopeConfig")
        if axis and key:
            try:
                return int(self._config[axis][key])
            except KeyError:
                return 0
        return 0

    def setParam(self, path: str, value):
        axis, key = self._split(path, "MicroscopeState")
        if axis and key == "stepPosition":
            self._state[axis] = int(value)
            self._update_encoder(axis)
            return
        axis, key = self._split(path, "MicroscopeConfig")
        if axis and key and axis in self._config:
            self._config[axis][key] = int(value)
            if key in {"stepsPerMeter", "encoderStepsPerMeter"}:
                self._update_encoder(axis)

    def getStagePosInfo(self, status: dict):
        status.update({
            "xPos_encoderSteps": self._encoders["X"],
            "yPos_encoderSteps": self._encoders["Y"],
            "zPos_encoderSteps": self._encoders["Z"],
        })
        return status


class _PmacBridge:
    """Proxy that falls back to the dummy backend if hardware is absent."""

    def __init__(self, real_module):
        self._real = real_module
        self._sim = _DummyPMACBackend()
        self._use_sim = real_module is None
        if self._use_sim:
            print("[INFO] Keine PMAC-Hardware gefunden �?\" starte im Simulationsmodus.")

    @property
    def is_simulated(self) -> bool:
        return self._use_sim or self._real is None

    def _fallback(self, reason: Exception | str):
        if not self._use_sim:
            print(f"[WARN] PMAC-Fallback aktiviert ({reason}).")
            self._use_sim = True
        return self._sim

    def connect(self, uri: str):
        if self._use_sim or self._real is None:
            return self._sim.connect(uri)
        try:
            return self._real.connect(uri)
        except Exception as exc:
            return self._fallback(exc).connect(uri)

    def getParam(self, path: str):
        if self._use_sim or self._real is None:
            return self._sim.getParam(path)
        try:
            return self._real.getParam(path)
        except Exception as exc:
            return self._fallback(exc).getParam(path)

    def setParam(self, path: str, value):
        if self._use_sim or self._real is None:
            return self._sim.setParam(path, value)
        try:
            return self._real.setParam(path, value)
        except Exception as exc:
            return self._fallback(exc).setParam(path, value)

    def getStagePosInfo(self, status: dict):
        if self._use_sim or self._real is None:
            return self._sim.getStagePosInfo(status)
        try:
            return self._real.getStagePosInfo(status)
        except Exception as exc:
            return self._fallback(exc).getStagePosInfo(status)


pmac = _PmacBridge(_pmac_module)

# ========================== QA LIMITS (in µm) ==========================
MEAS_MAX_UM = 10.0   # Max. |Delta| in Messung
DUR_MAX_UM  = 8.0    # Max. |Fehler| erlaubt im Dauertest (Live + Abschluss)
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

def sanitize_batch(s: str) -> str:
    s = (s or "").strip()
    if not s: return "NoBatch"
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:64] or "NoBatch"

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
    def __init__(self, frame_provider, *, interval_ms: int = 200, parent=None):
        super().__init__(parent)
        self._frame_provider = frame_provider
        self._last_frame = None
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
        self._timer.start(interval_ms)

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

# ========================== STAGE ====================================
# ================================================================
# Stage Utilities
# ================================================================
stageStatus = {}
def get_current_pos():
    x = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/X/stepPosition")
    y = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/Y/stepPosition")
    z = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/Z/stepPosition")
    return x, y, z

def get_stage_encoders():
    st = {}
    pmac.getStagePosInfo(st)
    return st["xPos_encoderSteps"], st["yPos_encoderSteps"]

def save_stage_test(savefile_name, pos_infodict, batch: str = "NoBatch"):
    now = datetime.datetime.now()
    dt_string = now.strftime("%Y-%m-%d_%H-%M-%S")
    pth = pathlib.Path(savefile_name)
    out_dir = pth.parent if str(pth.parent) not in ("", ".") else pathlib.Path(".")
    base = pth.name
    savename = out_dir / f"{dt_string}_{batch}_{base}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(savename, "w+", newline="") as savefile:
        writer = csv.writer(savefile)
        writer.writerow(["batch","x_counter","y_counter","Time [min]",
                         "x_position [m]","y_position [m]","pos_error_x [m]","pos_error_y [m]"])
        for i in range(len(pos_infodict["x_counter"])):
            writer.writerow([
                batch,
                pos_infodict["x_counter"][i], pos_infodict["y_counter"][i],
                pos_infodict["Time [min]"][i],
                pos_infodict["x_position [m]"][i], pos_infodict["y_position [m]"][i],
                pos_infodict["pos_error_x [m]"][i], pos_infodict["pos_error_y [m]"][i]
            ])
    print(f"Saved {savename}")

def moveXinsteps(motorsteps):
    pmac.setParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/X/stepPosition", int(motorsteps))
def moveYinsteps(motorsteps):
    pmac.setParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/Y/stepPosition", int(motorsteps))
def getXencoder():
    pmac.getStagePosInfo(stageStatus); return stageStatus["xPos_encoderSteps"]
def getYencoder():
    pmac.getStagePosInfo(stageStatus); return stageStatus["yPos_encoderSteps"]
def motorsteps2encodersteps(motorStep, stepsPerMeter, encoderStepsPerMeter):
    return (motorStep / stepsPerMeter * encoderStepsPerMeter).astype(int)
def meas_linear(StartMotorSteps, StopMotorSteps, Steps, stepsPerMeter, encoderStepsPerMeter):
    motor_steps = np.linspace(StartMotorSteps, StopMotorSteps, Steps).astype(int)
    calc_encoder = motorsteps2encodersteps(motor_steps, stepsPerMeter, encoderStepsPerMeter)
    return motor_steps, calc_encoder

# ================================================================
# StageController
# ================================================================
class StageController:
    def __init__(self, uri="tcp://127.0.0.1:5050"):
        self.conn, self.status = pmac.connect(uri), {}
        self.refresh()
        root = "ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/"
        g = lambda a,k: pmac.getParam(f"{root}{a}/{k}")
        self.steps_per_m = {a: g(a,"stepsPerMeter") for a in "XY"}
        self.enc_per_m   = {a: g(a,"encoderStepsPerMeter") for a in "XY"}
        self.low_lim     = {a: g(a,"limitLowSteps") for a in "XY"}
        self.high_lim    = {a: g(a,"limitHighSteps") for a in "XY"}
        self.home_pos    = {a: g(a,"homeStepPosition") for a in "XY"}
    def refresh(self): pmac.getStagePosInfo(self.status)
    def move_abs(self, a, s): pmac.setParam(f"ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/{a}/stepPosition", int(s))
    def enc(self, a): self.refresh(); return self.status[f"{a.lower()}Pos_encoderSteps"]

# ================================================================
# Workers
# ================================================================
class TestWorker(QObject):
    new_phase = Signal(str, int)
    step      = Signal(int)
    done      = Signal(dict)
    error     = Signal(str)
    calib     = Signal(dict)

    def __init__(self, sc, batch: str = "NoBatch"):
        super().__init__()
        self.sc = sc
        self.batch = sanitize_batch(batch)
        self._meas_max_um = None

    def _calibrate_like_reference(self, out_dir: pathlib.Path):
        limitLowStepsX  = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/limitLowSteps")
        limitHighStepsX = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/limitHighSteps")
        homeStepPositionX = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/homeStepPosition")
        stepsPerMeterX  = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/stepsPerMeter")
        encoderStepsPerMeterX = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/encoderStepsPerMeter")

        limitLowStepsY  = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/limitLowSteps")
        limitHighStepsY = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/limitHighSteps")
        homeStepPositionY = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/homeStepPosition")
        stepsPerMeterY  = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/stepsPerMeter")
        encoderStepsPerMeterY = pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/encoderStepsPerMeter")

        motorStepsX, _ = meas_linear(limitLowStepsX+1000, limitHighStepsX-1000, 20, stepsPerMeterX, encoderStepsPerMeterX)
        motorStepsY, _ = meas_linear(limitLowStepsY+5000, limitHighStepsY-5000, 20, stepsPerMeterY, encoderStepsPerMeterY)

        self.new_phase.emit(f"Kalibrierung X · Charge: {self.batch}", len(motorStepsX))
        Enc = np.zeros(len(motorStepsX), dtype=float)
        for i, ms in enumerate(motorStepsX, 1):
            moveXinsteps(ms); Enc[i-1] = getXencoder(); self.step.emit(i)

        moveXinsteps(homeStepPositionX); moveYinsteps(homeStepPositionY)

        x = Enc / encoderStepsPerMeterX; y = motorStepsX
        coef = np.polyfit(x, y, 1); poly1d_fn = np.poly1d(coef)
        newMotorStepsPerMeterX = int(coef[0])

        fig = Figure(figsize=(7.2,5), dpi=110, facecolor=BG_ELEV)
        ax  = fig.add_subplot(111); style_ax(ax)
        ax.plot(x, y, "o", label=f"Messpunkte · {self.batch}")
        ax.plot(x, poly1d_fn(x), "--", label="Fit")
        ax.set_title(f"Measured Motorsteps in X-Axis · Charge: {self.batch}")
        ax.set_xlabel("Encodersteps [m]"); ax.set_ylabel("Motorsteps [steps]"); ax.legend()
        fig.savefig(out_dir / f"calib_x_{self.batch}.png")

        self.new_phase.emit(f"Kalibrierung Y · Charge: {self.batch}", len(motorStepsY))
        Enc = np.zeros(len(motorStepsY), dtype=float)
        for i, ms in enumerate(motorStepsY, 1):
            moveYinsteps(ms); Enc[i-1] = getYencoder(); self.step.emit(i)

        moveXinsteps(homeStepPositionX); moveYinsteps(homeStepPositionY)

        x = Enc / encoderStepsPerMeterY; y = motorStepsY
        coef = np.polyfit(x, y, 1); poly1d_fn = np.poly1d(coef)
        newMotorStepsPerMeterY = int(coef[0])

        fig = Figure(figsize=(7.2,5), dpi=110, facecolor=BG_ELEV)
        ax  = fig.add_subplot(111); style_ax(ax)
        ax.plot(x, y, "o", label=f"Messpunkte · {self.batch}")
        ax.plot(x, poly1d_fn(x), "--", label="Fit")
        ax.set_title(f"Measured Motorsteps in Y-Axis · Charge: {self.batch}")
        ax.set_xlabel("Encodersteps [m]"); ax.set_ylabel("Motorsteps [steps]"); ax.legend()
        fig.savefig(out_dir / f"calib_y_{self.batch}.png")

        try:
            pmac.setParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/stepsPerMeter", int(newMotorStepsPerMeterX))
            pmac.setParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/stepsPerMeter", int(newMotorStepsPerMeterY))
            self.sc.steps_per_m['X'] = int(newMotorStepsPerMeterX); self.sc.steps_per_m['Y'] = int(newMotorStepsPerMeterY)
            print(f"[APPLIED][{self.batch}] stepsPerMeter: X = {newMotorStepsPerMeterX}, Y = {newMotorStepsPerMeterY}")
        except Exception as e:
            print(f"[WARN][{self.batch}] Konnte stepsPerMeter nicht schreiben:", e)

        self.calib.emit({
            "batch": self.batch,
            "X_stepsPerMeter": int(newMotorStepsPerMeterX),
            "Y_stepsPerMeter": int(newMotorStepsPerMeterY)
        })

        return {
            "newMotorStepsPerMeterX": int(newMotorStepsPerMeterX),
            "newMotorStepsPerMeterY": int(newMotorStepsPerMeterY),
            "motorStepsX": motorStepsX, "motorStepsY": motorStepsY
        }

    @staticmethod
    def _zigzag(lo, hi, n, spm, epm):
        mot = np.linspace(lo, hi, n).astype(int)
        mot = np.r_[mot, mot[::-1]]
        return mot, (mot / spm * epm).astype(int)

    def run(self):
        try:
            S = self.sc
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out = (DATA_ROOT / self.batch / f"Run_{ts}")
            out.mkdir(parents=True, exist_ok=True)
            plot_data = []
            calib_info = self._calibrate_like_reference(out_dir=out)

            max_abs_um = 0.0

            for ax in "XY":
                spm, epm = S.steps_per_m[ax], S.enc_per_m[ax]
                mot, calc = self._zigzag(S.low_lim[ax], S.high_lim[ax], 100, spm, epm)
                self.new_phase.emit(f"Messung {ax} · Charge: {self.batch}", len(mot))
                enc = np.zeros_like(mot)
                for i, m in enumerate(mot, 1):
                    S.move_abs(ax, int(m)); enc[i-1] = S.enc(ax); self.step.emit(i)
                diff_um = np.abs((enc - calc) / epm * 1e6)
                max_abs_um = max(max_abs_um, float(np.max(diff_um)))
                plot_data.append((ax, mot, enc, calc, spm, epm))
                S.move_abs(ax, S.home_pos[ax])

            self._meas_max_um = max_abs_um
            self.done.emit({"out": out, "plots": plot_data, "calib": calib_info,
                            "batch": self.batch, "meas_max_um": max_abs_um})
        except Exception as e:
            self.error.emit(str(e))

class DauertestWorker(QObject):
    update   = Signal(dict)
    finished = Signal(dict)

    def __init__(self, sc, batch: str = "NoBatch",
                 stop_at_ts: float | None = None,
                 start_ts: float | None = None,
                 out_dir: pathlib.Path | None = None,
                 dur_limit_um: float = DUR_MAX_UM):
        super().__init__()
        self.sc = sc
        self._running = True
        self.batch = sanitize_batch(batch)
        self.stop_at_ts = stop_at_ts
        self.start_ts   = start_ts or time.time()
        self.out_dir    = out_dir
        self.dur_limit_um = float(dur_limit_um)
        self.max_abs_um = 0.0

    def stop(self): self._running=False

    def run(self):
        pos_infodict={"x_counter":[],"y_counter":[],"Time [min]":[],
                      "x_position [m]":[],"y_position [m]":[],
                      "pos_error_x [m]":[],"pos_error_y [m]":[]}
        base_name = f"{self.batch}_dauertest_values.csv"
        savefile = (self.out_dir / base_name) if self.out_dir else pathlib.Path(base_name)

        start=self.start_ts
        x_low=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/limitLowSteps")
        x_high=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/limitHighSteps")
        x_spm=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/stepsPerMeter")
        x_epm=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/encoderStepsPerMeter")
        y_low=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/limitLowSteps")
        y_high=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/limitHighSteps")
        y_spm=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/stepsPerMeter")
        y_epm=pmac.getParam("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/encoderStepsPerMeter")
        x_count=y_count=0
        try:
            while self._running and (self.stop_at_ts is None or time.time() < self.stop_at_ts):
                cx,cy,_=get_current_pos()
                if np.random.rand()>=0.5:
                    ny=cy; nx=int(x_low+np.random.rand()*(x_high-x_low)); x_count+=1
                else:
                    nx=cx; ny=int(y_low+np.random.rand()*(y_high-y_low)); y_count+=1
                self.sc.move_abs('X',nx); self.sc.move_abs('Y',ny)

                runtime=round((time.time()-start)/60,2)
                x_enc,y_enc=get_stage_encoders()
                ex=(x_enc/x_epm) - (nx/x_spm)
                ey=(y_enc/y_epm) - (ny/y_spm)

                cur_max_um = max(abs(ex), abs(ey)) * 1e6
                if cur_max_um > self.max_abs_um:
                    self.max_abs_um = cur_max_um

                self.update.emit({"t":runtime,"ex":ex,"ey":ey,
                                  "batch":self.batch,
                                  "limit_um": self.dur_limit_um,
                                  "max_abs_um": self.max_abs_um})

                pos_infodict["Time [min]"].append(runtime)
                pos_infodict["x_counter"].append(x_count)
                pos_infodict["y_counter"].append(y_count)
                pos_infodict["x_position [m]"].append(round(x_enc/x_epm,6))
                pos_infodict["y_position [m]"].append(round(y_enc/y_epm,6))
                pos_infodict["pos_error_x [m]"].append(round(ex,8))
                pos_infodict["pos_error_y [m]"].append(round(ey,8))

                time.sleep(0.2)
        finally:
            save_stage_test(str(savefile), pos_infodict, batch=self.batch)
            self.finished.emit({
                "out": str(savefile),
                "batch": self.batch,
                "out_dir": str(self.out_dir) if self.out_dir else "",
                "dur_max_um": float(self.max_abs_um),
                "limit_um": float(self.dur_limit_um)
            })


class RealUseTestWorker(QObject):
    update   = Signal(dict)
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, sc, *,
                 x_center: int,
                 y_center: int,
                 step_size: int = 1500,
                 max_radius: int = 8000,
                 n_moves: int = 300,
                 dwell: float = 0.2,
                 raster_loops: int = 3,
                 batch: str = "NoBatch"):
        super().__init__()
        self.sc = sc
        self.params = {
            "x_center": int(x_center),
            "y_center": int(y_center),
            "step_size": int(step_size),
            "max_radius": int(max_radius),
            "n_moves": int(n_moves),
            "dwell": float(dwell),
            "raster_loops": int(raster_loops),
        }
        self.batch = sanitize_batch(batch)

    def _emit_update(self, phase: str, idx: int, total: int,
                     target_x: int, target_y: int,
                     err_um: float | None = None):
        self.update.emit({
            "phase": phase,
            "idx": idx,
            "total": total,
            "target_x": target_x,
            "target_y": target_y,
            "err_um": err_um if err_um is not None else 0.0,
            "batch": self.batch,
        })

    def run(self):
        try:
            p = self.params
            x_center = p["x_center"]
            y_center = p["y_center"]
            step_size = p["step_size"]
            max_radius = p["max_radius"]
            n_moves = p["n_moves"]
            dwell = p["dwell"]
            raster_loops = p["raster_loops"]

            offsets = (-step_size, 0, step_size)
            raster_total = max(1, raster_loops * len(offsets) * len(offsets))

            # Phase 1: Raster um die Mitte
            idx = 0
            for loop in range(1, raster_loops + 1):
                for dx in offsets:
                    for dy in offsets:
                        idx += 1
                        tx = x_center + dx
                        ty = y_center + dy
                        self.sc.move_abs('X', tx)
                        self.sc.move_abs('Y', ty)
                        time.sleep(dwell)
                        # Fehlerabschätzung in µm
                        ex_enc = self.sc.enc('X')
                        ey_enc = self.sc.enc('Y')
                        spm_x = self.sc.steps_per_m['X']
                        spm_y = self.sc.steps_per_m['Y']
                        epm_x = self.sc.enc_per_m['X']
                        epm_y = self.sc.enc_per_m['Y']
                        err_um = max(abs(ex_enc/epm_x - tx/spm_x),
                                     abs(ey_enc/epm_y - ty/spm_y)) * 1e6
                        self._emit_update("raster", idx, raster_total, tx, ty, err_um)

            # Phase 2: Random-Hops
            for hop in range(1, n_moves + 1):
                dx = int(np.random.randint(-max_radius, max_radius + 1))
                dy = int(np.random.randint(-max_radius, max_radius + 1))
                tx = x_center + dx
                ty = y_center + dy
                self.sc.move_abs('X', tx)
                self.sc.move_abs('Y', ty)
                time.sleep(dwell)
                ex_enc = self.sc.enc('X')
                ey_enc = self.sc.enc('Y')
                spm_x = self.sc.steps_per_m['X']
                spm_y = self.sc.steps_per_m['Y']
                epm_x = self.sc.enc_per_m['X']
                epm_y = self.sc.enc_per_m['Y']
                err_um = max(abs(ex_enc/epm_x - tx/spm_x),
                             abs(ey_enc/epm_y - ty/spm_y)) * 1e6
                self._emit_update("random", hop, n_moves, tx, ty, err_um)

            # Phase 3: zurück zur Mitte
            self.sc.move_abs('X', x_center)
            self.sc.move_abs('Y', y_center)
            time.sleep(dwell)
            ex_enc = self.sc.enc('X')
            ey_enc = self.sc.enc('Y')
            spm_x = self.sc.steps_per_m['X']
            spm_y = self.sc.steps_per_m['Y']
            epm_x = self.sc.enc_per_m['X']
            epm_y = self.sc.enc_per_m['Y']
            err_um = max(abs(ex_enc/epm_x - x_center/spm_x),
                         abs(ey_enc/epm_y - y_center/spm_y)) * 1e6
            self._emit_update("center", 1, 1, x_center, y_center, err_um)

            self.finished.emit({
                "batch": self.batch,
                "total_moves": raster_total + n_moves + 1,
                "max_radius": max_radius,
            })
        except Exception as exc:
            self.error.emit(str(exc))


# ========================== AUTOFOCUS ================================
# ================================================================
# IDS Live-View (mit Geraeteindex)
class CameraController(QLabel):
    """Qt live-view controller built on IdsCam."""

    centerChanged = Signal(int, int)
    frameReady = Signal(object, int, int)  # emits (bytes, width, height)

    def __init__(self, parent=None, *, device_index: int = 0, accent_color: str = "#ff2740"):
        super().__init__(parent)
        self.setScaledContents(True)
        self.accent_color = accent_color or "#ff2740"

        self.device_index = int(device_index)
        self.cam = IdsCam(index=device_index)
        self._timer: Optional[QTimer] = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(0)

        self._last_centroid: Optional[tuple[int, int]] = None
        self._img_w: Optional[int] = None
        self._img_h: Optional[int] = None

        self.pixel_size_um: Optional[float] = self.cam.pixel_size_um
        self.sensor_width_px: Optional[int] = self.cam.width
        self.sensor_height_px: Optional[int] = self.cam.height

    @property
    def is_dummy(self) -> bool:
        return bool(getattr(self.cam, "_dummy", False))

    def get_exposure_limits_us(self) -> tuple[float, float, float]:
        """Expose camera exposure limits for the UI."""
        return self.cam.get_exposure_limits_us()

    def _on_tick(self) -> None:
        frame = self.cam.aquise_frame()
        if frame is None:
            return
        h, w = frame.shape
        self._img_w, self._img_h = w, h
        self.sensor_width_px, self.sensor_height_px = w, h
        self.pixel_size_um = self.cam.pixel_size_um
        self.frameReady.emit(frame.tobytes(), w, h)

    def set_exposure_us(self, val_us: float) -> None:
        self.cam.set_exposure_us(val_us)

    def update_centroid(self, cx: int, cy: int) -> None:
        self._last_centroid = (int(cx), int(cy))
        self.centerChanged.emit(int(cx), int(cy))

    def display_frame(self, qimg: QImage) -> None:
        self.setPixmap(QPixmap.fromImage(qimg))

    def close(self) -> None:
        if self._timer:
            self._timer.stop()
        self.cam.shutdown()
        super().close()

# IDS Live-View (mit Geräteindex)
# ================================================================
class CameraWindow(QWidget):
    def __init__(
        self,
        parent=None,
        batch: str = "NoBatch",
        device_index: int = 0,
        label: str = "",
        spot_detector=None,
    ):
        super().__init__(parent)
        cam_name = (label or f"Cam {device_index}")
        self.setWindowTitle(f"Kamera – {cam_name} (Mono8) · Charge: {batch}")
        self.resize(1180,640)

        # Horizontal layout: live image on the left, alignment panel on the right
        h = QHBoxLayout(self)

        # Live view (left) — expand
        self.detector = spot_detector or LaserSpotDetector()

        self.live = CameraController(
            self,
            device_index=device_index,
            accent_color=ACCENT,
        )
        try:
            self.live.frameReady.connect(self._on_frame_ready)
        except Exception:
            pass
        h.addWidget(self.live, 1)

        # Alignment panel (right) — compact fixed width
        align_card = Card("Alignment")
        align_card.setFixedWidth(320)

        # Justage controls: single toggle button (Save ↔ Clear)
        ref_btn_row = QHBoxLayout()
        self.btn_toggle_justage = QPushButton("Save Justage")
        self.btn_toggle_justage.setMinimumHeight(28)
        self.btn_toggle_justage.setMinimumWidth(140)
        ref_btn_row.addWidget(self.btn_toggle_justage)
        align_card.body.addLayout(ref_btn_row)

        # Reference coordinates display
        self.lbl_ref = QLabel("Ref: —")
        self.lbl_ref.setObjectName("Chip")
        align_card.body.addWidget(self.lbl_ref)

        # Numeric readouts (jetzt mit physikalischen Einheiten)
        self.lbl_dx = QLabel("dx: —")
        self.lbl_dy = QLabel("dy: —")
        self.lbl_dist = QLabel("dist: —")
        for lbl in (self.lbl_dx, self.lbl_dy, self.lbl_dist):
            lbl.setObjectName("Chip")
            align_card.body.addWidget(lbl)

        align_card.body.addItem(QSpacerItem(0,8, QSizePolicy.Minimum, QSizePolicy.Fixed))

        # Status indicator
        self.lbl_align_status = QLabel("Status: —")
        self.lbl_align_status.setAlignment(Qt.AlignCenter)
        align_card.body.addWidget(self.lbl_align_status)

        align_card.body.addStretch(1)

        h.addWidget(align_card, 0)

        # Internal state
        self._last_centroid = None

        # Wiring
        try:
            self.live.centerChanged.connect(self._on_live_center_changed)
        except Exception:
            pass
        try:
            self.btn_toggle_justage.clicked.connect(self._on_toggle_justage)
        except Exception:
            pass

    def _on_live_center_changed(self, x: int, y: int):
        try:
            self._last_centroid = (x, y)
            self._update_alignment(x, y)
        except Exception:
            pass

    def _update_alignment(self, cx: int, cy: int):
        # Determine camera center from LiveView if available
        w = getattr(self.live, "_img_w", None)
        h = getattr(self.live, "_img_h", None)
        if not w or not h:
            w = getattr(self.live, "sensor_width_px", None)
            h = getattr(self.live, "sensor_height_px", None)
        if not w or not h:
            print("[WARN] Keine Auflösung aus Kamera verfügbar.")
            return

        # If a Justage reference is set, compute offsets relative to it; otherwise use camera center
        ref = self.detector.get_reference_point()
        if ref is not None:
            rx, ry = ref
            dx = int(cx - int(rx)); dy = int(cy - int(ry))
        else:
            cam_cx = int(w // 2); cam_cy = int(h // 2)
            dx = int(cx - cam_cx); dy = int(cy - cam_cy)
        dist = float(np.hypot(dx, dy))

        # Physikalische Einheiten (Pixelgröße aus LiveView)
        px_um = getattr(self.live, "pixel_size_um", None)
        if px_um is None:
            print("[WARN] Keine Pixelgröße aus Kamera verfügbar.")
            return
        px_um = float(px_um)
        dx_um = dx * px_um
        dy_um = dy * px_um
        dist_um = dist * px_um
        dist_mm = dist_um / 1000.0

        self.lbl_dx.setText(f"dx: {dx:+d} px  ({dx_um:+.1f} µm)")
        self.lbl_dy.setText(f"dy: {dy:+d} px  ({dy_um:+.1f} µm)")
        self.lbl_dist.setText(f"dist: {dist:.2f} px  ({dist_um:.1f} µm · {dist_mm:.3f} mm)")

        # Fixed tolerance (no UI): use 5 px as pass threshold
        tol_px = 5.0
        tol_um = tol_px * px_um
        ok = (dist <= tol_px)
        color = "#2ecc71" if ok else "#ff2740"
        text = "OK" if ok else "ALIGN"
        self.lbl_align_status.setText(
            f'<span style="color:{color}; font-weight:600">{text} '
            f'(≤ {tol_px:.1f} px ≈ {tol_um:.1f} µm)</span>'
        )

    def _on_save_justage(self):
        """Save the current centroid as the Justage reference point."""
        try:
            if self._last_centroid is None:
                QMessageBox.warning(self, "Justage", "Kein Laser-Mittelpunkt erkannt. Bitte erstmal Justage-Laser einschalten und erkennen lassen.")
                return
            cx, cy = self._last_centroid
            self.detector.set_reference_point(cx, cy)
            self.lbl_ref.setText(f"Ref: {cx}, {cy}")
            # Re-evaluate alignment relative to reference
            self._update_alignment(cx, cy)
        except Exception as e:
            print("[WARN] Could not save justage:", e)

    def _on_clear_justage(self):
        try:
            self.detector.clear_reference_point()
            self.lbl_ref.setText("Ref: —")
            # Recompute alignment relative to center if we have a centroid
            if self._last_centroid is not None:
                self._update_alignment(*self._last_centroid)
        except Exception as e:
            print("[WARN] Could not clear justage:", e)

    def _on_toggle_justage(self):
        """Toggle Save / Clear Justage depending on whether a reference exists."""
        try:
            ref = self.detector.get_reference_point()
            if ref is None:
                # No reference -> save current centroid
                self._on_save_justage()
                try: self.btn_toggle_justage.setText("Clear Justage")
                except: pass
            else:
                # Reference exists -> clear it
                self._on_clear_justage()
                try: self.btn_toggle_justage.setText("Save Justage")
                except: pass
        except Exception as e:
            print("[WARN] Toggle justage failed:", e)

    def closeEvent(self,e):
        try: self.live.close()
        except: pass
        super().closeEvent(e)

    def _on_frame_ready(self, frame_bytes: bytes, w: int, h: int):
        try:
            ref_point = self.detector.get_reference_point()
            qimg, (cx, cy) = self.detector.process_frame(
                frame_bytes,
                w,
                h,
                ACCENT,
                ref_point,
                getattr(self.live, "is_dummy", False),
            )
            self.live.display_frame(qimg)
            self.live.update_centroid(cx, cy)
        except Exception:
            pass


# ================================================================
# Plot Widget
# ================================================================
class LivePlot(FigureCanvas):
    def __init__(self, parent=None, batch: str = "NoBatch"):
        fig = Figure(figsize=(7.2, 4.2), dpi=110, facecolor=BG_ELEV)
        super().__init__(fig)
        self.setParent(parent)
        self.ax = fig.add_subplot(111)
        self.batch = sanitize_batch(batch)
        self._apply_titles()
        style_ax(self.ax)
        self.line_ex, = self.ax.plot([], [], label="Error X")
        self.line_ey, = self.ax.plot([], [], label="Error Y")
        leg = self.ax.legend(); leg.get_frame().set_linewidth(0.6); leg.get_frame().set_edgecolor(BORDER)
        self.t,self.ex,self.ey = [],[],[]

    def _apply_titles(self):
        self.ax.set_title(f"Dauertest – Positionsfehler · Charge: {self.batch}", fontweight="semibold")
        self.ax.set_xlabel("Zeit [min]"); self.ax.set_ylabel("Fehler [µm]")

    def set_batch(self, batch: str):
        self.batch = sanitize_batch(batch); self._apply_titles(); self.draw_idle()

    def add_data(self, data):
        ex_um = float(data.get("ex", 0.0)) * 1e6
        ey_um = float(data.get("ey", 0.0)) * 1e6
        self.t.append(data["t"]); self.ex.append(ex_um); self.ey.append(ey_um)
        self.line_ex.set_data(self.t,self.ex); self.line_ey.set_data(self.t,self.ey)
        self.ax.relim(); self.ax.autoscale_view(); self.draw_idle()

# ========================== GUI =====================================
# ================================================================
# GUI
# ================================================================
class StageGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.sc=StageController()
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
        self._real_use_total: int | None = None
        self._real_use_raster_total: int = 0
        self._warned_camera_sim = False
        self.statusBar: QLabel | None = None
        self._pending_status: str | None = None
        self._af_cam = None

        # Neu: Zielkamera für Exposure-UI (wird nur für Fallback genutzt)
        self._expo_target_idx = 0

        self._build_ui(); self._wire_shortcuts()
        sim_msgs = []
        if getattr(pmac, "is_simulated", False):
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
        self.btnRealUseTest = QPushButton("Real-Use-Test"); self.btnRealUseTest.clicked.connect(self._start_real_use_test)
        self.btnOpenFolder = QPushButton("📂 Ordner öffnen"); self.btnOpenFolder.setEnabled(False); self.btnOpenFolder.clicked.connect(self._open_folder)
        self.btnKleberoboter = QPushButton("Datenbank Senden"); self.btnKleberoboter.clicked.connect(self._trigger_kleberoboter)

        for b in (self.btnStart, self.btnDauer, self.btnRealUseTest, self.btnOpenFolder, self.btnKleberoboter):
            b.setMinimumHeight(36)

        self.cardActions.body.addWidget(self.btnStart)
        self.cardActions.body.addWidget(self.btnDauer)
        self.cardActions.body.addWidget(self.btnRealUseTest)
        self.cardActions.body.addWidget(self.btnOpenFolder)
        self.cardActions.body.addWidget(self.btnKleberoboter)

        # --- Dauertest-Dauer (NEU) ---
        durRow = QHBoxLayout()
        lblDur = QLabel("Dauer")
        self.comboDur = QComboBox()
        self.comboDur.addItems(["15 h (Standard)","1 h","4 h","8 h","24 h","Benutzerdefiniert"])
        self.comboDur.setCurrentIndex(0)

        self.spinHours = QSpinBox(); self.spinHours.setRange(0, 240); self.spinHours.setValue(15)
        self.spinMinutes = QSpinBox(); self.spinMinutes.setRange(0, 59); self.spinMinutes.setValue(0)
        self.spinHours.setEnabled(False); self.spinMinutes.setEnabled(False)
        self.comboDur.currentIndexChanged.connect(self._on_duration_mode_changed)
        self.spinHours.valueChanged.connect(self._on_custom_duration_changed)
        self.spinMinutes.valueChanged.connect(self._on_custom_duration_changed)

        durRow.addWidget(lblDur); durRow.addWidget(self.comboDur, 1)
        durRow.addWidget(QLabel("h")); durRow.addWidget(self.spinHours)
        durRow.addWidget(QLabel("min")); durRow.addWidget(self.spinMinutes)
        self.cardActions.body.addLayout(durRow)

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
        self.chipDurQA  = QLabel(f"Dauertest QA (Limit {DUR_MAX_UM:.1f} µm): —"); self.chipDurQA.setObjectName("Chip")
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
        self.gitterschieberCam = LiveCamEmbed(cam_provider, interval_ms=200, parent=self.gitterschieberPage)
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
            self.autofocusCam = LiveCamEmbed(self._af_frame_provider, interval_ms=200, parent=self.autofocusPage)
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
        self._batch = sanitize_batch(val) or "NoBatch"
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
            self._run_outdir = DATA_ROOT / self._batch / f"Run_{ts}"
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
        presets = [15*3600, 1*3600, 4*3600, 8*3600, 24*3600]
        custom = (idx == 5)
        self.spinHours.setEnabled(custom)
        self.spinMinutes.setEnabled(custom)
        if not custom:
            self._set_duration_sec(presets[idx])
        else:
            self._on_custom_duration_changed()

    def _on_custom_duration_changed(self):
        hours = self.spinHours.value()
        minutes = self.spinMinutes.value()
        self._set_duration_sec(hours*3600 + minutes*60)

    # ---------- Test ----------
    def _start_test(self):
        self._acquire_batch()
        out_dir = self._ensure_run_dir()
        self.btnStart.setEnabled(False); self._set_status("Test läuft…")
        self.thr=QThread()
        self.wrk=TestWorker(self.sc, batch=self._batch); self.wrk.moveToThread(self.thr)
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
            dur_text  = "OK" if (self._dur_max_um is not None and self._dur_max_um <= DUR_MAX_UM) else "NICHT OK" if self._dur_max_um is not None else "—"

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
f"  Dauertest: ≤ {DUR_MAX_UM:.1f} µm |  Ergebnis: {self._dur_max_um if self._dur_max_um is not None else float('nan'):.2f} µm  → {dur_text}\n"
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

    # ---------- Real-Use-Test ----------
    def _start_real_use_test(self):
        try:
            x_center, y_center, _ = get_current_pos()
        except Exception as exc:
            QMessageBox.warning(self, "Real-Use-Test", f"Referenzposition konnte nicht gelesen werden:\n{exc}")
            return

        raster_loops = 3
        raster_total = raster_loops * 3 * 3
        total_moves = raster_total + 300 + 1
        self._real_use_raster_total = raster_total
        self._real_use_total = total_moves

        self.btnRealUseTest.setEnabled(False)
        self.btnDauer.setEnabled(False)
        self.btnStart.setEnabled(False)

        self.lblPhase.setText("Real-Use-Test")
        self.pbar.setMaximum(total_moves)
        self.pbar.setValue(0)
        self.pbar.setFormat(f"Real-Use-Test: 0 / {total_moves} (0%)")
        self._set_status(f"Real-Use-Test läuft (Mitte {x_center}, {y_center})…")

        self.real_use_thread = QThread()
        self.real_use_worker = RealUseTestWorker(
            self.sc,
            x_center=x_center,
            y_center=y_center,
            step_size=1500,
            max_radius=8000,
            n_moves=300,
            dwell=0.2,
            raster_loops=raster_loops,
            batch=self._batch,
        )
        self.real_use_worker.moveToThread(self.real_use_thread)
        self.real_use_thread.started.connect(self.real_use_worker.run)
        self.real_use_worker.update.connect(self._real_use_progress)
        self.real_use_worker.finished.connect(self._real_use_done)
        self.real_use_worker.error.connect(self._real_use_error)
        self.real_use_worker.finished.connect(lambda *_: self.real_use_thread.quit())
        self.real_use_worker.error.connect(lambda *_: self.real_use_thread.quit())
        self.real_use_thread.start()

    def _real_use_progress(self, data: dict):
        phase = data.get("phase", "—")
        idx = int(data.get("idx", 0))
        total = int(data.get("total", 1))
        err_um = float(data.get("err_um", 0.0))

        if phase == "raster":
            overall = idx
        elif phase == "random":
            overall = self._real_use_raster_total + idx
        else:
            overall = self._real_use_total or idx

        if self._real_use_total:
            self.pbar.setMaximum(self._real_use_total)
            self.pbar.setValue(min(overall, self._real_use_total))
            pct = int(round(100 * self.pbar.value() / max(1, self._real_use_total)))
            self.pbar.setFormat(f"Real-Use-Test: {self.pbar.value()} / {self._real_use_total} ({pct}%)")
        self.lblPhase.setText(f"Real-Use-Test · {phase} · Fehler {err_um:.2f} µm")

    def _real_use_done(self, info: dict):
        self._set_status("Real-Use-Test abgeschlossen.")
        self.lblPhase.setText("Real-Use-Test fertig")
        self.btnRealUseTest.setEnabled(True)
        self.btnDauer.setEnabled(True)
        self.btnStart.setEnabled(True)
        self._reset_progress()

    def _real_use_error(self, msg: str):
        QMessageBox.warning(self, "Real-Use-Test", f"Fehler:\n{msg}")
        self.btnRealUseTest.setEnabled(True)
        self.btnDauer.setEnabled(True)
        self.btnStart.setEnabled(True)
        self._set_status("Real-Use-Test fehlgeschlagen.")
    # ---------- Dauertest ----------
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

        self._dauer_running = True
        self._set_dauer_button(True)
        self._set_status("Dauertest läuft…")

        self._dauer_start  = time.time()
        self._dauer_target = self._dauer_start + self._duration_sec
        self._update_timer()

        self.dauer_thread=QThread()
        self.dauer_worker=DauertestWorker(
            self.sc,
            batch=self._batch,
            stop_at_ts=self._dauer_target,
            start_ts=self._dauer_start,
            out_dir=out_dir,
            dur_limit_um=DUR_MAX_UM
        )
        self.dauer_worker.moveToThread(self.dauer_thread)
        self.dauer_thread.started.connect(self.dauer_worker.run)
        self.dauer_worker.update.connect(self._live_update_dur)
        self.dauer_worker.finished.connect(self._dauer_finished)
        self.dauer_thread.start()

        self.timer=QTimer(self); self.timer.timeout.connect(self._update_timer); self.timer.start(1000)

    def _live_update_dur(self, data: dict):
        self.plot.add_data(data)
        max_um = float(data.get("max_abs_um", 0.0))
        limit  = float(data.get("limit_um", DUR_MAX_UM))
        ok = (max_um <= limit)
        self._set_chip(self.chipDurQA, f"Dauertest QA (Limit {limit:.1f} µm): Max = {max_um:.2f} µm → {'OK' if ok else 'WARN/FAIL'}", ok=ok)

    def _dauer_finished(self, d):
        print(f"[INFO][{self._batch}] Dauertest abgeschlossen → {d['out']}")
        self._set_status("Dauertest beendet.")
        self._dauer_running = False
        self._set_dauer_button(False)
        if hasattr(self,'timer') and self.timer.isActive():
            self.timer.stop()
        self.lblTimer.setText("00:00:00")

        outdir = self._ensure_run_dir()
        try:
            if self.plot is not None:
                out_png = outdir / f"dauertest_{self._batch}.png"
                self.plot.figure.savefig(out_png, dpi=110)
                print(f"[INFO][{self._batch}] Live-Plot gespeichert → {out_png}")
        except Exception as e:
            print(f"[WARN][{self._batch}] Konnte Live-Plot nicht speichern:", e)

        self._dur_max_um = float(d.get("dur_max_um", 0.0))
        limit = float(d.get("limit_um", DUR_MAX_UM))
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
        self._set_chip(self.chipDurQA, f"Dauertest QA (Limit {DUR_MAX_UM:.1f} µm): —", ok=True)

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

    def _ensure_raspi_wifi(self):
        """Make sure we're connected to the Raspi WLAN before sending data."""
        ensure_raspi_wifi_connected(RASPI_WIFI_SSID)

    def _trigger_kleberoboter(self):
        """Send the Kleberoboter payload via the Zwischen-Raspi socket."""
        self.btnKleberoboter.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                self._set_status("Verbinde mit Datenbank (raspi-webgui)…")
                self._ensure_raspi_wifi()
            except RuntimeError as err:
                self._set_status("Datenbankverbindung fehlgeschlagen.")
                QMessageBox.warning(self, "Kleberoboter", f"WLAN-Check fehlgeschlagen:\n{err}")
                return

            self._set_status("Datenbank verbunden – sende Payload…")
            payload, ack = send_kleberoboter_payload()
        except Exception as e:
            self._set_status("Senden an Datenbank fehlgeschlagen.")
            QMessageBox.warning(self, "Kleberoboter", f"Senden fehlgeschlagen:\n{e}")
        else:
            info = "Kleberoboter-Payload gesendet."
            if ack:
                info += f"\nACK: {ack}"
            info += f"\nServer: {KLEBEROBOTER_SERVER_IP}:{KLEBEROBOTER_PORT}"
            info += f"\nBarcode: {payload['barcodenummer']}"
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

            root = DATA_ROOT
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
            root = DATA_ROOT
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
                live = getattr(win, "live", None)
                if live and getattr(live, "device_index", None) == device_index:
                    return live
            except Exception:
                continue
        return None

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


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

import sys, datetime, pathlib, numpy as np, time, csv, ctypes, re, os, subprocess

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

import matplotlib
matplotlib.use("qtagg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib import image as mpimg

from PySide6.QtCore    import QObject, QThread, Signal, Qt, QTimer, QSize, QRegularExpression, QEvent
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QLineEdit, QTextEdit,
    QProgressBar, QMessageBox, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QSizePolicy, QSpacerItem, QComboBox, QSpinBox, QToolButton,
    QStackedWidget, QSlider, QDoubleSpinBox, QDialog, QListWidget
)
from PySide6.QtGui     import (
    QImage, QPixmap, QPalette, QColor, QFont, QShortcut, QKeySequence,
    QRegularExpressionValidator, QIcon, QPainter, QPen
)

import pmacspy as pmac
from ids_peak import ids_peak as p

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
    btn.setIconSize(QSize(96, 96))
    btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    btn.setText(text)
    btn.setMinimumSize(180, 150)
    btn.setAutoRaise(False)
    btn.setStyleSheet("""
        QToolButton {
            background-color: %s;
            border: 1px solid %s;
            border-radius: 16px;
            padding: 14px;
            font-weight: 600;
        }
        QToolButton:hover { background-color: %s; border-color: %s; }
    """ % (BG_ELEV, BORDER, HOVER, ACCENT))
    btn.clicked.connect(clicked_cb)
    return btn

# ================================================================
# IDS Live-View (mit Geräteindex)
# ================================================================
class LiveView(QLabel):
    """Minimaler IDS peak Live-View (Mono8) in QLabel. Wählt Kamera per device_index.

    Erweiterung:
    - versucht Pixelgröße automatisch zu erkennen (Node-Pfade, sonst ENV, sonst Default 2.2 µm)
    - stellt pixel_size_um bereit, damit CameraWindow in physikalischen Einheiten rechnen kann
    """
    # Emits (x, y) of the tracked laser centroid in pixel coordinates
    centerChanged = Signal(int, int)

    def __init__(self, parent=None, device_index: int = 0):
        super().__init__(parent)
        self.setScaledContents(True)
        self.device_index = int(device_index)
        self._img_w = None
        self._img_h = None
        self._ref_point = None
        self.pixel_size_um: float | None = None
        self.sensor_width_px: int | None = None
        self.sensor_height_px: int | None = None
        self._init_camera()

    def _detect_pixel_size_um(self):
        """Versucht die Pixelgröße automatisch zu bestimmen.

        Reihenfolge:
        1. Kamera-Node (verschiedene mögliche Namen)
        """
        try:
            candidates = [
                "SensorPixelWidth",
                "SensorPixelHeight",
                "PixelSize",
                "SensorPixelSize",
            ]
            for name in candidates:
                try:
                    node = self.remote.FindNode(name)
                    val = float(node.Value())
                    # Heuristik: falls der Wert sehr klein ist, könnte er in mm sein
                    # → < 0.001 → mm → in µm umrechnen
                    if val < 0.001:
                        val_um = val * 1e6
                    elif val < 1.0:
                        # könnte mm sein → konservativ: mm → µm
                        val_um = val * 1e3
                    else:
                        # schon in µm
                        val_um = val
                    print(f"[INFO] Pixelgröße aus Node {name}: {val_um:.3f} µm")
                    return val_um
                except Exception:
                    continue
        except Exception:
            pass

        raise RuntimeError("Pixelgröße konnte nicht aus der Kamera gelesen werden.")

    def _configure_max_resolution(self):
        """Setzt Breite/Höhe der Kamera auf die maximal verfügbaren Werte."""
        def _set_node_to_max(name: str) -> int | None:
            try:
                node = self.remote.FindNode(name)
            except Exception:
                return None
            max_val = None
            try:
                max_val = int(getattr(node, "Maximum")())
            except Exception:
                try:
                    max_node = self.remote.FindNode(f"{name}Max")
                    max_val = int(max_node.Value())
                except Exception:
                    pass
            try:
                if max_val is not None:
                    node.SetValue(max_val)
            except Exception:
                pass
            try:
                return int(node.Value())
            except Exception:
                return None

        self.sensor_width_px = _set_node_to_max("Width")
        self.sensor_height_px = _set_node_to_max("Height")
        # SensorWidth/-Height liefern ggf. die native Maximalauflösung direkt
        try:
            sensor_w = int(self.remote.FindNode("SensorWidth").Value())
            if sensor_w:
                self.sensor_width_px = sensor_w
        except Exception:
            pass
        try:
            sensor_h = int(self.remote.FindNode("SensorHeight").Value())
            if sensor_h:
                self.sensor_height_px = sensor_h
        except Exception:
            pass

    def _init_camera(self):
        p.Library.Initialize()
        dm = p.DeviceManager.Instance(); dm.Update()
        devs = dm.Devices()
        if not devs:
            raise SystemExit("Keine IDS-Kamera gefunden.")
        idx = max(0, min(self.device_index, len(devs)-1))
        self.dev    = devs[idx].OpenDevice(p.DeviceAccessType_Control)
        self.remote = self.dev.RemoteDevice().NodeMaps()[0]

        for n,v in [("AcquisitionMode","Continuous"),
                    ("TriggerSelector","FrameStart"),
                    ("TriggerMode","Off"),
                    ("PixelFormat","Mono8")]:
            try: self.remote.FindNode(n).SetCurrentEntry(v)
            except: pass

        self._configure_max_resolution()

        # Pixelgröße erkennen
        self.pixel_size_um = self._detect_pixel_size_um()

        self.ds = self.dev.DataStreams()[0].OpenDataStream()
        ps = self.remote.FindNode("PayloadSize").Value()
        self._bufs = [self.ds.AllocAndAnnounceBuffer(ps) for _ in range(3)]
        for b in self._bufs: self.ds.QueueBuffer(b)
        self.ds.StartAcquisition()
        try: self.remote.FindNode("AcquisitionStart").Execute()
        except: pass

        self._timer = QTimer(self); self._timer.timeout.connect(self._grab); self._timer.start(0)

    def set_exposure_us(self, val_us: float):
        """Setze Exposure (µs) direkt an die Kamera-Remote (robust gegen Namensvarianten)."""
        try:
            # Disable auto if present
            try: self.remote.FindNode("ExposureAuto").SetCurrentEntry("Off")
            except: pass
            node = None
            for name in ("ExposureTime", "ExposureTimeAbs", "ExposureTimeUs"):
                try:
                    n = self.remote.FindNode(name)
                    _ = n.Value()
                    node = n
                    break
                except:
                    continue
            if node is None:
                raise RuntimeError("ExposureTime-Knoten nicht gefunden.")
            # Clamp
            try:
                mn = int(max(1, round(node.Minimum())))
                mx = int(round(node.Maximum()))
                v = int(min(mx, max(mn, int(round(float(val_us))))))
            except:
                v = int(round(float(val_us)))
            node.SetValue(float(v))
            print(f"[INFO] LiveView: Exposure gesetzt auf {v} µs")
        except Exception as e:
            print("[WARN] LiveView: Exposure setzen fehlgeschlagen:", e)

    def set_reference_point(self, x: int, y: int):
        """Save a reference point (Justage laser) in pixel coordinates and keep it displayed.
        Pass None to clear.
        """
        try:
            if x is None or y is None:
                self._ref_point = None
            else:
                self._ref_point = (int(x), int(y))
            print(f"[INFO] LiveView: Reference point set to {self._ref_point}")
        except Exception as e:
            print("[WARN] LiveView: Could not set reference point:", e)

    def clear_reference_point(self):
        try:
            self._ref_point = None
            print("[INFO] LiveView: Reference point cleared")
        except Exception as e:
            print("[WARN] LiveView: Could not clear reference point:", e)

    def _grab(self):
        try: buf = self.ds.WaitForFinishedBuffer(50)
        except: return
        if not buf: return
        try:
            w,h,ptr,sz = buf.Width(), buf.Height(), int(buf.BasePtr()), buf.Size()
            # remember image resolution for external consumers
            try:
                self._img_w = int(w); self._img_h = int(h)
            except Exception:
                self._img_w = None; self._img_h = None
            arr = (ctypes.c_ubyte * sz).from_address(ptr)
            # Create a copied QImage from the camera buffer (ensure correct bytesPerLine)
            try:
                # bytes(...) makes an explicit copy to avoid lifetime issues
                raw_bytes = bytes(memoryview(arr)[:w*h])
                qimg = QImage(raw_bytes, w, h, w, QImage.Format_Grayscale8).copy()
            except Exception:
                # Fallback to previous approach if needed
                qimg = QImage(memoryview(arr)[:w*h], w, h, w, QImage.Format_Grayscale8).copy()

            # Compute laser centroid robustly: subtract background median, find brightest pixel,
            # then compute a local weighted centroid around that peak (reduces ring/Airy bias).
            try:
                img = np.frombuffer(raw_bytes if 'raw_bytes' in locals() else memoryview(arr)[:w*h], dtype=np.uint8)
                img = img.reshape((h, w)).astype(np.float32)

                # Background removal: subtract median to reduce rings / background
                try:
                    bkg = float(np.median(img))
                    img_sub = img - bkg
                    img_sub[img_sub < 0] = 0.0
                except Exception:
                    img_sub = img

                # Find global brightest pixel in the background-subtracted image
                idx = int(np.argmax(img_sub))
                py, px = divmod(idx, w)

                # Local window around the peak for refined centroid
                win = min(41, max(11, min(h, w) // 10))  # odd-ish window, clamped
                hw = win // 2
                x0 = max(0, px - hw); x1 = min(w, px + hw + 1)
                y0 = max(0, py - hw); y1 = min(h, py + hw + 1)
                sub = img_sub[y0:y1, x0:x1]

                tot = float(sub.sum())
                if tot > 0:
                    ys, xs = np.indices(sub.shape)
                    cx_local = (sub * xs).sum() / tot
                    cy_local = (sub * ys).sum() / tot
                    cx = int(round(x0 + cx_local))
                    cy = int(round(y0 + cy_local))
                else:
                    # Fallback to brightest pixel or center
                    if img_sub.sum() > 0:
                        cx, cy = px, py
                    else:
                        cx, cy = w // 2, h // 2

                # Emit centroid for external UI consumption
                try:
                    self.centerChanged.emit(cx, cy)
                except Exception:
                    pass

                # Ensure we paint in true color: convert the grayscale QImage to ARGB32
                try:
                    if qimg.format() != QImage.Format_ARGB32:
                        qimg = qimg.convertToFormat(QImage.Format_ARGB32)
                except Exception:
                    pass
                painter = QPainter(qimg)
                # 1) Camera midpoint: full-width/height dashed lines (static) - make highly visible red
                try:
                    # use the shared ACCENT color from the theme for exact match
                    pen_cam = QPen(QColor(ACCENT))
                    pen_cam.setWidth(3)
                    pen_cam.setStyle(Qt.DashLine)
                    painter.setPen(pen_cam)
                    cx0 = w // 2
                    cy0 = h // 2
                    painter.drawLine(cx0, 0, cx0, h)
                    painter.drawLine(0, cy0, w, cy0)
                except Exception:
                    pass

                # 2) Laser centroid: größere Kreuzhaare
                try:
                    pen_l = QPen(QColor(ACCENT))
                    pen_l.setWidth(3)
                    pen_l.setCapStyle(Qt.RoundCap)
                    painter.setPen(pen_l)
                    size = max(6, min(w, h) // 20)
                    painter.drawLine(max(0, cx - size), cy, min(w, cx + size), cy)
                    painter.drawLine(cx, max(0, cy - size), cx, min(h, cy + size))
                except Exception:
                    pass

                # 3) Reference point (Justage) if present: distinct marker (yellow)
                try:
                    if getattr(self, "_ref_point", None) is not None:
                        rx, ry = self._ref_point
                        pen_r = QPen(QColor("#ffd60a"))
                        pen_r.setWidth(2)
                        painter.setPen(pen_r)
                        rsize = max(6, min(w, h) // 30)
                        from PySide6.QtCore import QRect
                        painter.drawEllipse(QRect(int(rx - rsize//2), int(ry - rsize//2), int(rsize), int(rsize)))
                        # line between current centroid and reference
                        try:
                            pen_line = QPen(QColor("#ffd60a"))
                            pen_line.setStyle(Qt.DashLine)
                            painter.setPen(pen_line)
                            painter.drawLine(rx, ry, cx, cy)
                        except Exception:
                            pass
                except Exception:
                    pass

                painter.end()
            except Exception:
                # don't break the live view if processing fails
                pass

            self.setPixmap(QPixmap.fromImage(qimg))
        finally:
            self.ds.QueueBuffer(buf)

    def close(self):
        for fn in (
            lambda: self.remote.FindNode("AcquisitionStop").Execute(),
            self.ds.StopAcquisition,
            lambda: [self.ds.RevokeBuffer(b) for b in self._bufs],
            self.dev.Close,
            p.Library.Close,
        ):
            try: fn()
            except: pass
        super().close()

class CameraWindow(QWidget):
    def __init__(self, parent=None, batch: str = "NoBatch", device_index: int = 0, label: str = ""):
        super().__init__(parent)
        cam_name = (label or f"Cam {device_index}")
        self.setWindowTitle(f"Kamera – {cam_name} (Mono8) · Charge: {batch}")
        self.resize(1180,640)

        # Horizontal layout: live image on the left, alignment panel on the right
        h = QHBoxLayout(self)

        # Live view (left) — expand
        self.live = LiveView(self, device_index=device_index)
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
        ref = getattr(self.live, "_ref_point", None)
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
            self.live.set_reference_point(cx, cy)
            self.lbl_ref.setText(f"Ref: {cx}, {cy}")
            # Re-evaluate alignment relative to reference
            self._update_alignment(cx, cy)
        except Exception as e:
            print("[WARN] Could not save justage:", e)

    def _on_clear_justage(self):
        try:
            self.live.clear_reference_point()
            self.lbl_ref.setText("Ref: —")
            # Recompute alignment relative to center if we have a centroid
            if self._last_centroid is not None:
                self._update_alignment(*self._last_centroid)
        except Exception as e:
            print("[WARN] Could not clear justage:", e)

    def _on_toggle_justage(self):
        """Toggle Save / Clear Justage depending on whether a reference exists."""
        try:
            ref = getattr(self.live, "_ref_point", None)
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
        self.ax.set_xlabel("Zeit [min]"); self.ax.set_ylabel("Fehler [m]")

    def set_batch(self, batch: str):
        self.batch = sanitize_batch(batch); self._apply_titles(); self.draw_idle()

    def add_data(self, data):
        self.t.append(data["t"]); self.ex.append(data["ex"]); self.ey.append(data["ey"])
        self.line_ex.set_data(self.t,self.ex); self.line_ey.set_data(self.t,self.ey)
        self.ax.relim(); self.ax.autoscale_view(); self.draw_idle()

# ================================================================
# GUI
# ================================================================
class StageGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.sc=StageController()
        self.plot=None
        self._cam_windows=[]
        self._batch="NoBatch"; self._dauer_running=False
        self._duration_sec = 15 * 3600  # Default 15h
        self._run_outdir: pathlib.Path | None = None
        self._last_outdir: pathlib.Path | None = None
        self._meas_max_um = None
        self._dur_max_um  = None
        self._calib_vals  = {"X": None, "Y": None}

        # Neu: Zielkamera für Exposure-UI (wird nur für Fallback genutzt)
        self._expo_target_idx = 0

        self._build_ui(); self._wire_shortcuts()

    # ---------- UI ----------
    def _build_ui(self):
        self.setWindowTitle("Stage-Toolbox")
        self.resize(1220,900)
        root = QVBoxLayout(self); root.setContentsMargins(18,18,18,12); root.setSpacing(14)

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
        workflowCard = Card("Workflow")
        root.addWidget(workflowCard)
        tiles = QHBoxLayout(); tiles.setSpacing(12)
        self.btnStageTile = make_tile("Stage", stage_img, self._show_stage_workflow)
        self.btnAutofocusTile = make_tile("Autofocus", af_img, self._open_autofocus_workflow)
        self.btnLaserTile = make_tile("Laserscan Modul", laser_img, self._open_laserscan_workflow)
        tiles.addWidget(self.btnStageTile)
        tiles.addWidget(self.btnAutofocusTile)
        tiles.addWidget(self.btnLaserTile)
        tiles.addStretch(1)
        workflowCard.body.addLayout(tiles)

        # Seitenumschaltung
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # ===================== Stage Seite =====================
        self.stagePage = QWidget()
        stageLayout = QVBoxLayout(self.stagePage)
        stageLayout.setContentsMargins(0,0,0,0); stageLayout.setSpacing(14)

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

        for b in (self.btnStart, self.btnDauer, self.btnOpenFolder):
            b.setMinimumHeight(42)

        self.cardActions.body.addWidget(self.btnStart)
        self.cardActions.body.addWidget(self.btnDauer)
        self.cardActions.body.addWidget(self.btnOpenFolder)

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

        # ===================== Autofocus Seite =====================
        self.autofocusPage = QWidget()
        autoLayout = QVBoxLayout(self.autofocusPage)
        autoLayout.setContentsMargins(0,0,0,0); autoLayout.setSpacing(14)

        autoHero = Card("Autofocus")
        autoLayout.addWidget(autoHero)
        autoHeroImg = QLabel(); autoHeroImg.setAlignment(Qt.AlignCenter)
        pix_auto = QPixmap(af_img)
        if not pix_auto.isNull():
            autoHeroImg.setPixmap(pix_auto.scaled(620, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            autoHeroImg.setText("Autofocus Kamera")
        autoHero.body.addWidget(autoHeroImg, 0, Qt.AlignCenter)

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

        autoLayout.addStretch(1)
        self.stack.addWidget(self.autofocusPage)

        # ===================== Laserscan Seite =====================
        self.laserscanPage = QWidget()
        laserLayout = QVBoxLayout(self.laserscanPage)
        laserLayout.setContentsMargins(0,0,0,0); laserLayout.setSpacing(14)

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

        # Status bar
        self.statusBar = QLabel("Bereit."); self.statusBar.setObjectName("Chip")
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
        self.statusBar.setText(text)

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
            dm = p.DeviceManager.Instance(); dm.Update()
            if idx >= len(dm.Devices()):
                QMessageBox.warning(self, "Kamera", f"Keine Kamera am Index {idx} gefunden. ({label})")
                return
        except Exception:
            pass
        try:
            win = CameraWindow(self, batch=self._batch, device_index=idx, label=label)
            # Exposure beim Öffnen: aktueller UI-Wert (oder Slider-Minimum)
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
            self._set_status(f"Kamera geöffnet: {label} (Index {idx})")
            # Exposure-UI ggf. aus Live-Remote initialisieren (damit Slider/Min/Max passen)
            try:
                self._init_exposure_ui_from_remote(win.live.remote)
            except Exception as e:
                print("[WARN] Konnte Exposure-UI nicht aus Live-Remote initialisieren:", e)
        except SystemExit as e:
            QMessageBox.critical(self,"Kamera",str(e))

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

    def _init_exposure_ui_from_device(self, device_index: int):
        """Liest Limits & aktuellen Wert. Nutzt Live-Remote wenn Fenster offen, sonst kurzzeitig öffnen."""
        remote = self._get_live_remote_if_open(device_index)
        if remote is not None:
            self._init_exposure_ui_from_remote(remote)
            return
        # ephemeres Öffnen
        try:
            dm = p.DeviceManager.Instance(); dm.Update()
        except Exception:
            p.Library.Initialize()
            dm = p.DeviceManager.Instance(); dm.Update()
        devs = dm.Devices()
        if not devs or device_index >= len(devs):
            raise RuntimeError(f"Keine Kamera am Index {device_index} gefunden.")
        dev = devs[device_index].OpenDevice(p.DeviceAccessType_Control)
        try:
            remote = dev.RemoteDevice().NodeMaps()[0]
            self._init_exposure_ui_from_remote(remote)
        finally:
            try: dev.Close()
            except: pass

    def _init_exposure_ui_from_remote(self, remote):
        # Auto aus, sofern vorhanden
        for node_name, entry in [("ExposureAuto", "Off"), ("ExposureMode", "Timed")]:
            try: remote.FindNode(node_name).SetCurrentEntry(entry)
            except: pass
        # Exposure-Knoten finden (robust gegen Namensvarianten)
        node = None
        for name in ("ExposureTime", "ExposureTimeAbs", "ExposureTimeUs"):
            try:
                n = remote.FindNode(name)
                _ = n.Value()  # Zugriff testen
                node = n
                break
            except:
                node = None
        if node is None:
            raise RuntimeError("ExposureTime-Knoten nicht gefunden.")

        # Limits + aktueller Wert
        try:
            min_us = int(max(1, round(node.Minimum())))
            max_us = int(round(node.Maximum()))
        except:
            min_us, max_us = 50, 200000  # Fallback 0.05–200 ms

        try:
            cur_us = int(round(node.Value()))
        except:
            cur_us = min_us

        # UI setzen
        self.sliderExpo.blockSignals(True)
        self.spinExpo.blockSignals(True)

        self.sliderExpo.setMinimum(min_us)
        self.sliderExpo.setMaximum(max_us)
        self.sliderExpo.setValue(cur_us)

        self.spinExpo.setMinimum(min_us/1000.0)
        self.spinExpo.setMaximum(max_us/1000.0)
        self.spinExpo.setValue(cur_us/1000.0)

        self.sliderExpo.blockSignals(False)
        self.spinExpo.blockSignals(False)

        self._set_status(f"Exposure: {cur_us/1000.0:.3f} ms (Range {min_us/1000.0:.3f}–{max_us/1000.0:.3f} ms)")

    def _get_live_remote_if_open(self, device_index: int):
        for win in list(self._cam_windows):
            try:
                if win.live.device_index == device_index:
                    return win.live.remote
            except:
                continue
        return None

    def _apply_exposure_to_device(self, device_index: int, exposure_us: int):
        """Setzt Exposure. Nutzt Live-Remote, sonst ephemeres Öffnen."""
        remote = self._get_live_remote_if_open(device_index)
        if remote is not None:
            self._remote_set_exposure(remote, exposure_us)
            return
        try:
            dm = p.DeviceManager.Instance(); dm.Update()
        except Exception:
            p.Library.Initialize()
            dm = p.DeviceManager.Instance(); dm.Update()
        devs = dm.Devices()
        if not devs or device_index >= len(devs):
            print(f"[WARN] Keine Kamera am Index {device_index} gefunden.")
            return
        dev = devs[device_index].OpenDevice(p.DeviceAccessType_Control)
        try:
            remote = dev.RemoteDevice().NodeMaps()[0]
            self._remote_set_exposure(remote, exposure_us)
        except Exception as e:
            print("[WARN] Exposure setzen fehlgeschlagen:", e)
        finally:
            try: dev.Close()
            except: pass

    def _apply_exposure_to_open_windows(self, exposure_us: int):
        """Wendet die Exposure-Einstellung auf alle aktuell offenen Kamerafenster an."""
        for win in list(getattr(self, "_cam_windows", [])):
            try:
                if hasattr(win, "live"):
                    win.live.set_exposure_us(int(exposure_us))
            except Exception as e:
                print("[WARN] Could not apply exposure to open window:", e)

    def _remote_set_exposure(self, remote, exposure_us: int):
        try: remote.FindNode("ExposureAuto").SetCurrentEntry("Off")
        except: pass
        node = None
        for name in ("ExposureTime", "ExposureTimeAbs", "ExposureTimeUs"):
            try:
                n = remote.FindNode(name)
                _ = n.Value()
                node = n
                break
            except:
                continue
        if node is None:
            raise RuntimeError("ExposureTime-Knoten nicht gefunden.")
        try:
            mn = int(max(1, round(node.Minimum())))
            mx = int(round(node.Maximum()))
            exposure_us = int(min(mx, max(mn, exposure_us)))
        except:
            pass
        try:
            node.SetValue(float(exposure_us))
            self._set_status(f"Exposure gesetzt: {exposure_us/1000.0:.3f} ms")
        except Exception as e:
            print("[WARN] Exposure SetValue fehlgeschlagen:", e)

# ================================================================
# MAIN
# ================================================================
if __name__=="__main__":
    app=QApplication(sys.argv)
    apply_dark_theme(app)
    gui=StageGUI(); gui.show()
    sys.exit(app.exec())

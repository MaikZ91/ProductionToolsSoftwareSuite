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
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from collections import deque

import pandas as pd

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

from PySide6.QtCore    import QObject, QThread, Signal, Qt, QTimer, QSize, QRegularExpression, QEvent, QSortFilterProxyModel, QAbstractTableModel
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QLineEdit, QTextEdit,
    QProgressBar, QMessageBox, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QSizePolicy, QSpacerItem, QComboBox, QToolButton, QTableView,
    QStackedWidget, QSlider, QDoubleSpinBox, QDialog, QListWidget,
    QScrollArea, QCheckBox, QFormLayout, QGroupBox, QHeaderView, QAbstractItemView, QDial
)
from PySide6.QtGui     import (
    QPixmap, QPalette, QColor, QFont, QShortcut, QKeySequence,
    QRegularExpressionValidator, QIcon, QImage, QPainter, QPen
)

import autofocus
from autofocus import IdsCam, LaserSpotDetector, LiveLaserController
from commonIE import dbConnector
import commonIE
from commonIE import miltenyiBarcode
import datenbank as db
import gitterschieber as gs
from z_trieb import ZTriebWidget
import stage_control as resolve_stage


# ========================== DATENBANK / INFRA ==========================
BASE_DIR = _BASE_DIR
DASHBOARD_WIDGET_CLS, _DASHBOARD_IMPORT_ERROR = (None, None)

matplotlib.use("qtagg")

# ========================== QA LIMITS (in µm) ==========================
MEAS_MAX_UM = 10.0   # Max. |Delta| in Messung
# =======================================================================

# ================================================================
# THEME
# ================================================================
ACCENT      = "#5ce2cf"   # Frisches Mint als neuer Akzent
ACCENT_ALT  = "#f0b74a"   # Warme Zweitfarbe f�r Highlights
BG          = "#080f1a"   # Dunkles Navy
BG_ELEV     = "#111b2b"   # Erh�hte Paneele
BG_ELEV_ALT = "#0c1424"
FG          = "#e8edf5"
FG_MUTED    = "#9fb2c8"
BORDER      = "#1b2a3f"
HOVER       = "#16243a"

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
    "font.sans-serif": ["Manrope", "Inter", "Segoe UI", "DejaVu Sans", "Arial"],
    "legend.facecolor": BG_ELEV,
    "legend.edgecolor": BORDER,
})

QT_STYLESHEET = f"""
* {{
  background: transparent;
  color: {FG};
  font-family: "Manrope", "Segoe UI", Arial;
}}
QWidget {{ background-color: {BG}; }}
QLabel {{ color: {FG}; font-size: 13px; letter-spacing: 0.2px; }}

QFrame#Hero {{
  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {BG_ELEV}, stop:1 {BG_ELEV_ALT});
  border: 1px solid {BORDER};
  border-radius: 16px;
  padding: 4px;
}}
QLabel#HeroTitle {{
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 0.2px;
}}
QLabel#HeroSubtitle {{
  color: {FG_MUTED};
  font-size: 12px;
}}

QLineEdit, QTextEdit {{
  background-color: {BG_ELEV};
  color: {FG};
  padding: 9px 12px;
  border: 1px solid {BORDER};
  border-radius: 10px;
  selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; background-color: {BG_ELEV_ALT}; }}

QPushButton {{
  background-color: {BG_ELEV};
  color: {FG};
  padding: 4px 10px;
  border: 1px solid {BORDER};
  border-radius: 9px;
  font-weight: 700;
  min-height: 26px;
}}
QPushButton:hover {{ background-color: {HOVER}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: {ACCENT}; color: #051017; border-color: {ACCENT}; }}
QPushButton:disabled {{ background: #14141b; color: {FG_MUTED}; border-color: {BORDER}; }}
QPushButton[variant="primary"] {{
  background-color: {ACCENT};
  color: #041014;
  border-color: {ACCENT};
}}
QPushButton[variant="primary"]:hover {{ background-color: #6ef0d9; border-color: #6ef0d9; }}
QPushButton[variant="primary"]:pressed {{ background-color: #46bfa9; }}
QPushButton[variant="ghost"] {{
  background-color: transparent;
  color: {FG};
  border-color: {BORDER};
}}
QPushButton[variant="ghost"]:hover {{ background-color: {HOVER}; border-color: {ACCENT}; }}
QPushButton[variant="ghost"]:pressed {{ border-color: {ACCENT_ALT}; color: {ACCENT_ALT}; }}
QLineEdit[variant="metric"] {{
  font-weight: 700;
  letter-spacing: 0.2px;
  padding: 7px 10px;
}}
QLabel[role="section"] {{
  color: {FG};
  font-weight: 800;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}}

QProgressBar {{
  background: {BG_ELEV};
  color: {FG};
  border: 1px solid {BORDER};
  border-radius: 10px;
  text-align: center;
  height: 20px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 8px; }}

QComboBox, QSpinBox, QDoubleSpinBox {{
  background-color: {BG_ELEV};
  color: {FG};
  padding: 8px 11px;
  border: 1px solid {BORDER};
  border-radius: 10px;
  min-height: 32px;
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QSlider::groove:horizontal {{
  border: 1px solid {BORDER};
  height: 6px;
  background: {BG_ELEV};
  border-radius: 6px;
}}
QSlider::handle:horizontal {{
  background: {ACCENT};
  border: 1px solid {ACCENT};
  width: 16px;
  margin: -6px 0;
  border-radius: 10px;
}}

QFrame#Card {{
  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {BG_ELEV}, stop:1 {BG_ELEV_ALT});
  border: 1px solid {BORDER};
  border-radius: 14px;
}}
QLabel#CardTitle {{
  color: {FG};
  font-weight: 800;
  font-size: 14px;
  letter-spacing: 0.2px;
}}

QLabel#Chip {{
  background-color: rgba(92, 226, 207, 0.08);
  color: {FG};
  padding: 4px 8px;
  border-radius: 10px;
  border: 1px solid {ACCENT};
  font-size: 12px;
}}

QToolButton[variant="tile"] {{
  background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {BG_ELEV}, stop:1 {BG_ELEV_ALT});
  color: {FG};
  border: 1px solid {BORDER};
  border-radius: 12px;
  padding: 6px 6px 6px 6px;
  font-weight: 800;
}}
QToolButton[variant="tile"]:hover {{
  background-color: {HOVER};
  border-color: {ACCENT};
}}
QToolButton[variant="tile"]:pressed {{
  background-color: {ACCENT};
  color: #041014;
  border-color: {ACCENT};
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
# Dashboard (embedded)
# ================================================================
LIMIT_ROWS = 50
TESTTYPE_DB_MAP = {
    "kleberoboter": "kleberoboter",
    "gitterschieber_tool": "gitterschieber_tool",
    "stage_test": "stage_test",
}

DASHBOARD_STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {FG};
    font-family: Inter, "Segoe UI", Arial;
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 12px;
    margin-top: 10px;
    background-color: {BG_ELEV};
    font-weight: 600;
}}
QLabel {{
    color: {FG};
}}
QLineEdit, QComboBox {{
    background-color: {BG_ELEV};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {FG};
}}
QPushButton {{
    background-color: {BG_ELEV};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 16px;
    color: {FG};
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {HOVER};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: #0b0b0f;
}}
QTableView {{
    background-color: {BG_ELEV};
    gridline-color: {BORDER};
    selection-background-color: {ACCENT};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QHeaderView::section {{
    background-color: {BG};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 6px;
}}
QTableCornerButton::section {{
    background-color: {BG};
    border: 1px solid {BORDER};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {BG};
    border: none;
    width: 12px;
    margin: 0px;
}}
QScrollBar::handle {{
    background: {ACCENT};
    border-radius: 6px;
}}
"""


class PandasModel(QAbstractTableModel):
    """Minimal wrapper to show a pandas.DataFrame in a QTableView."""

    def __init__(self, df: pd.DataFrame):
        super().__init__()
        self._df = df.reset_index(drop=True)

    def rowCount(self, parent=None):
        return self._df.shape[0]

    def columnCount(self, parent=None):
        return self._df.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.DisplayRole:
            value = self._df.iat[index.row(), index.column()]
            col_name = self._df.columns[index.column()]

            if col_name.lower() == "ok":
                if pd.isna(value):
                    return ""
                return "OK" if bool(value) else "FAIL"

            if col_name.lower() in ("starttest", "endtest"):
                if pd.isna(value):
                    return ""
                if isinstance(value, (datetime.datetime, datetime.date)):
                    return value.strftime("%Y-%m-%d %H:%M:%S")
                return str(value)

            return str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                return str(self._df.columns[section])
            return str(section)
        return None


class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Produktions-Dashboard")
        self._apply_theme()

        self.data = pd.DataFrame()
        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy_model.setFilterKeyColumn(-1)
        self.proxy_model.setDynamicSortFilter(True)

        self._filter_connected = False
        self.conn = None

        self.initUI()
        self.update_data()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_data)
        self.timer.start(5000)

    def _apply_theme(self):
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(BG))
        pal.setColor(QPalette.Base, QColor(BG))
        pal.setColor(QPalette.AlternateBase, QColor(BG_ELEV))
        pal.setColor(QPalette.WindowText, QColor(FG))
        pal.setColor(QPalette.Text, QColor(FG))
        pal.setColor(QPalette.Button, QColor(BG_ELEV))
        pal.setColor(QPalette.ButtonText, QColor(FG))
        self.setPalette(pal)
        self.setStyleSheet(DASHBOARD_STYLESHEET)

    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(18, 18, 18, 16)
        main_layout.setSpacing(14)

        title = QLabel("Live Produktions-Dashboard")
        title.setFont(QFont("Inter", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {FG};")

        self.lbl_total = self.create_kpi("...", "Total Output")
        self.lbl_ok = self.create_kpi("...", "OK-Anteil")
        self.lbl_last = self.create_kpi("...", "Letzter Status")

        kpi_layout = QGridLayout()
        kpi_layout.addWidget(self.lbl_total, 0, 0)
        kpi_layout.addWidget(self.lbl_ok, 0, 1)
        kpi_layout.addWidget(self.lbl_last, 0, 2)

        controls = QHBoxLayout()
        self.combo_testtype = QComboBox()
        self.combo_testtype.addItems(list(TESTTYPE_DB_MAP.keys()))
        self.combo_testtype.currentIndexChanged.connect(self.on_testtype_changed)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter...")

        controls.addWidget(QLabel("Testtyp:"))
        controls.addWidget(self.combo_testtype)
        controls.addStretch()
        controls.addWidget(QLabel("Filter:"))
        controls.addWidget(self.filter_input)

        self.table = QTableView()
        self.table.setSortingEnabled(True)
        self.table.setModel(self.proxy_model)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setMinimumSectionSize(90)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setFixedHeight(230)

        entry_card = Card("Neuen Datensatz senden")
        entry_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        entry_card.setMinimumHeight(210)
        entry_card.setMaximumHeight(280)
        entry_layout = entry_card.body

        common_form = QFormLayout()
        self.le_barcode = QLineEdit()
        self.le_barcode.setPlaceholderText("Barcode scannen oder einfuegen...")
        self.le_user = QLineEdit()
        self.le_user.setPlaceholderText("User / Mitarbeiter-ID...")
        common_form.addRow("Barcode:", self.le_barcode)
        common_form.addRow("User:", self.le_user)

        self.entry_stack = QStackedWidget()

        w_kleber = QWidget()
        f_kleber = QFormLayout()
        self.cb_ok = QCheckBox("Test OK?")
        f_kleber.addRow(self.cb_ok)
        w_kleber.setLayout(f_kleber)

        w_git = QWidget()
        f_git = QFormLayout()
        self.le_particle_count = QLineEdit()
        self.le_particle_count.setPlaceholderText("Anzahl Partikel")
        self.le_justage_angle = QLineEdit()
        self.le_justage_angle.setPlaceholderText("Justage-Winkel (deg)")
        f_git.addRow("Particle Count:", self.le_particle_count)
        f_git.addRow("Justage Angle:", self.le_justage_angle)
        w_git.setLayout(f_git)

        w_stage = QWidget()
        f_stage = QFormLayout()
        self.le_field_of_view = QLineEdit()
        self.le_field_of_view.setPlaceholderText("Field of View")
        self.le_position = QLineEdit()
        self.le_position.setPlaceholderText("Position")
        self.le_x_cam1 = QLineEdit(); self.le_x_cam1.setPlaceholderText("X Cam1")
        self.le_y_cam1 = QLineEdit(); self.le_y_cam1.setPlaceholderText("Y Cam1")
        self.le_x_cam2 = QLineEdit(); self.le_x_cam2.setPlaceholderText("X Cam2")
        self.le_y_cam2 = QLineEdit(); self.le_y_cam2.setPlaceholderText("Y Cam2")
        f_stage.addRow("Field of View:", self.le_field_of_view)
        f_stage.addRow("Position:", self.le_position)
        f_stage.addRow("Cam1 X:", self.le_x_cam1)
        f_stage.addRow("Cam1 Y:", self.le_y_cam1)
        f_stage.addRow("Cam2 X:", self.le_x_cam2)
        f_stage.addRow("Cam2 Y:", self.le_y_cam2)
        w_stage.setLayout(f_stage)

        self.entry_stack.addWidget(w_kleber)
        self.entry_stack.addWidget(w_git)
        self.entry_stack.addWidget(w_stage)

        btn_row = QHBoxLayout()
        self.btn_send = UiFactory.button("Senden", variant="primary")
        self.btn_clear = UiFactory.button("Felder leeren", variant="ghost")
        btn_row.addStretch()
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_send)

        entry_layout.addLayout(common_form)
        entry_layout.addWidget(self.entry_stack)
        entry_layout.addLayout(btn_row)

        self.btn_send.clicked.connect(self.send_current_entry)
        self.btn_clear.clicked.connect(self.clear_entry_fields)

        main_layout.addWidget(title)
        main_layout.addLayout(kpi_layout)
        main_layout.addLayout(controls)
        main_layout.addWidget(self.table, 1)
        main_layout.addSpacing(10)
        main_layout.addWidget(entry_card, 0)
        main_layout.addStretch(1)
        self.setLayout(main_layout)

        if not self._filter_connected:
            self.filter_input.textChanged.connect(self.proxy_model.setFilterFixedString)
            self._filter_connected = True

        self.on_testtype_changed(0)

    def create_kpi(self, value_text, label_text):
        container = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        value = QLabel(value_text)
        value.setFont(QFont("Inter", 32, QFont.Bold))
        value.setStyleSheet(f"color: {ACCENT};")
        value.setAlignment(Qt.AlignCenter)
        label = QLabel(label_text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color: {FG_MUTED}; font-size: 14px;")
        layout.addWidget(value)
        layout.addWidget(label)
        container.setLayout(layout)
        container.setStyleSheet(
            f"background-color: {BG_ELEV}; border: 1px solid {BORDER}; border-radius: 16px;"
        )
        container.value_label = value
        return container

    def on_testtype_changed(self, idx):
        self.entry_stack.setCurrentIndex(idx)
        self.update_data()

    def clear_entry_fields(self):
        self.le_barcode.clear()
        self.le_user.clear()
        self.cb_ok.setChecked(False)
        self.le_particle_count.clear()
        self.le_justage_angle.clear()
        self.le_field_of_view.clear()
        self.le_position.clear()
        self.le_x_cam1.clear()
        self.le_y_cam1.clear()
        self.le_x_cam2.clear()
        self.le_y_cam2.clear()

    def _open_conn(self):
        if self.conn is None:
            self.conn = commonIE.dbConnector.connection()
        try:
            self.conn.connect()
        except Exception as e:
            QMessageBox.critical(self, "DB Fehler", f"Verbindung fehlgeschlagen:\n{e}")
            return False
        return True

    def _close_conn(self):
        if self.conn:
            try:
                self.conn.disconnect()
            except Exception:
                pass

    def fetch_data_from_db(self, testtype: str, limit: int = LIMIT_ROWS) -> tuple[pd.DataFrame, bool]:
        try:
            return db.fetch_test_data(testtype, limit=limit)
        except Exception as e:
            print(f"Fetch error: {e}")
            return pd.DataFrame(), False

    def update_data(self):
        key = self.combo_testtype.currentText()
        testtype = TESTTYPE_DB_MAP.get(key, key)

        df, connected = self.fetch_data_from_db(testtype, LIMIT_ROWS)
        
        if not connected:
            # Show connection error in table if not connected
            self.timer.stop()
            self.lbl_total.value_label.setText("---")
            self.lbl_ok.value_label.setText("---")
            self.lbl_last.value_label.setText("OFFLINE")
            self.lbl_last.value_label.setStyleSheet(f"color: {FG_MUTED};")
            
            error_df = pd.DataFrame({"Status": ["Datenbankverbindung nicht verfügbar"]})
            model = PandasModel(error_df)
            self.proxy_model.setSourceModel(model)
            return

        if "StartTest" in df.columns:
            df = df.sort_values("StartTest", ascending=False).reset_index(drop=True)

        self.data = df

        total = len(df)
        if "ok" in df.columns and total > 0:
            ok_bool = df["ok"].fillna(False).astype(bool)
            ok_count = ok_bool.sum()
            ok_ratio = int((ok_count / total) * 100)
            last_result = "OK" if bool(ok_bool.iloc[0]) else "FAIL"
        else:
            ok_ratio = 0
            last_result = "N/A"

        self.lbl_total.value_label.setText(str(total))
        self.lbl_ok.value_label.setText(f"{ok_ratio}%")
        self.lbl_last.value_label.setText(last_result)
        
        # Color for last result
        if last_result == "OK":
            self.lbl_last.value_label.setStyleSheet(f"color: {ACCENT};")
        elif last_result == "FAIL":
            self.lbl_last.value_label.setStyleSheet(f"color: #ef4444;")
        else:
            self.lbl_last.value_label.setStyleSheet(f"color: {FG_MUTED};")

        model = PandasModel(df)
        self.proxy_model.setSourceModel(model)

    def send_current_entry(self):
        key = self.combo_testtype.currentText()
        testtype = TESTTYPE_DB_MAP.get(key, key)

        barcode_str = self.le_barcode.text().strip() or "0"
        user_str = self.le_user.text().strip() or "unknown"

        startTime = datetime.datetime.now()
        endTime = datetime.datetime.now()

        payload = {}
        if testtype == "kleberoboter":
            payload["ok"] = self.cb_ok.isChecked()

        elif testtype == "gitterschieber_tool":
            payload["particle_count"] = self._safe_int(self.le_particle_count.text())
            payload["justage_angle"] = self._safe_float(self.le_justage_angle.text())

        elif testtype == "stage_test":
            payload["field_of_view"] = self._safe_float(self.le_field_of_view.text())
            payload["position"] = self.le_position.text().strip()
            payload["x_coordinate_cam1"] = self._safe_float(self.le_x_cam1.text())
            payload["y_coordinate_cam1"] = self._safe_float(self.le_y_cam1.text())
            payload["x_coordinate_cam2"] = self._safe_float(self.le_x_cam2.text())
            payload["y_coordinate_cam2"] = self._safe_float(self.le_y_cam2.text())

        else:
            QMessageBox.warning(self, "Unbekannter Testtyp", f"{testtype}")
            return

        try:
            barcode_obj = miltenyiBarcode.mBarcode(barcode_str)
        except Exception as e:
            QMessageBox.warning(self, "Barcode Fehler", f"Konnte Barcode nicht erzeugen:\n{e}")
            return

        if not self._open_conn():
            return
        try:
            resp = self.conn.sendData(
                startTime,
                endTime,
                0,
                testtype,
                payload,
                barcode_obj,
                user_str
            )
            print("sendData response:", resp)
        except Exception as e:
            QMessageBox.critical(self, "Sendefehler", f"Senden fehlgeschlagen:\n{e}")
        finally:
            self._close_conn()

        self.update_data()
        self.clear_entry_fields()

    def _safe_int(self, txt):
        try:
            return int(float(str(txt).replace(",", ".")))
        except Exception:
            return 0

    def _safe_float(self, txt):
        try:
            return float(str(txt).replace(",", "."))
        except Exception:
            return 0.0

    def closeEvent(self, event):
        self.timer.stop()
        self._close_conn()
        super().closeEvent(event)


# register dashboard widget for StageGUI integration
DASHBOARD_WIDGET_CLS = Dashboard
_DASHBOARD_IMPORT_ERROR = None

# ================================================================
# Reusable Card
# ================================================================
class Card(QFrame):
    def __init__(self, title: str = "", right_widget: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(5)
        header = QHBoxLayout(); header.setSpacing(6)
        self.title = QLabel(title); self.title.setObjectName("CardTitle")
        header.addWidget(self.title)
        header.addStretch(1)
        if right_widget: header.addWidget(right_widget, 0, Qt.AlignRight)
        lay.addLayout(header)
        self.body = QVBoxLayout(); self.body.setSpacing(5)
        lay.addLayout(self.body)

# ================================================================
# UI Factory (wiederverwendbare Komponenten)
# ================================================================
class UiFactory:
    LABEL_WIDTH = 112
    FIELD_HEIGHT = 28

    @staticmethod
    def button(text: str, *, variant: str = "default", min_height: int | None = None, tooltip: str | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("variant", variant)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setMinimumHeight(min_height or UiFactory.FIELD_HEIGHT)
        if tooltip:
            btn.setToolTip(tooltip)
        return btn

    @staticmethod
    def line_edit(placeholder: str = "", *, read_only: bool = False, width: int | None = None) -> QLineEdit:
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        le.setReadOnly(read_only)
        le.setMinimumHeight(UiFactory.FIELD_HEIGHT)
        if read_only:
            le.setProperty("variant", "metric")
        if width:
            le.setFixedWidth(width)
        return le

    @staticmethod
    def chip(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("Chip")
        return lbl

    @staticmethod
    def section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("role", "section")
        return lbl

    @staticmethod
    def form_row(label_text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = UiFactory.section_label(label_text)
        lbl.setFixedWidth(UiFactory.LABEL_WIDTH)
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        return row

    @staticmethod
    def metric_field(label_text: str, placeholder: str = "") -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = UiFactory.section_label(label_text)
        lbl.setFixedWidth(UiFactory.LABEL_WIDTH)
        field = UiFactory.line_edit(placeholder, read_only=True)
        row.addWidget(lbl)
        row.addWidget(field, 1)
        row.field = field  # type: ignore[attr-defined]
        return row

# ================================================================
# Workflow-Kacheln (Icons sicher laden)
# ================================================================
def _safe_icon(path: str) -> QIcon:
    try:
        if not path:
            return QIcon()
        if os.path.exists(path):
            return QIcon(path)
    except Exception:
        pass
    return QIcon()

def _first_existing(paths) -> str:
    """Return first existing path from iterable, else empty string."""
    for cand in paths:
        try:
            if cand and os.path.exists(cand):
                return str(pathlib.Path(cand).resolve())
        except Exception:
            continue
    return ""

def make_tile(text: str, icon_path: str, clicked_cb):
    btn = QToolButton()
    btn.setIcon(_safe_icon(icon_path))
    btn.setIconSize(QSize(42, 42))
    btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    btn.setText(text)
    btn.setMinimumSize(90, 72)
    btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    btn.setAutoRaise(False)
    btn.setProperty("variant", "tile")
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
            pm = frame_to_qpixmap(frame, (self.label.width(), self.label.height()))
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


def frame_to_qpixmap(frame, target_size=None) -> QPixmap:
    """Convert BGR/gray frame into a QPixmap, scaled to target_size when given."""
    if frame.ndim == 2:
        arr = frame
        if arr.dtype != np.uint8:
            arr = np.clip(arr.astype(np.float32) / float(arr.max() or 1) * 255.0, 0, 255).astype(np.uint8)
        h, w = arr.shape
        qimg = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
    elif frame.ndim == 3 and frame.shape[2] == 3:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
    else:
        raise ValueError("Unsupported frame shape.")
    pm = QPixmap.fromImage(qimg)
    if target_size is not None:
        if isinstance(target_size, QSize):
            w, h = target_size.width(), target_size.height()
        else:
            w, h = target_size
        if w and h:
            pm = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pm


class GitterschieberLiveChart(QWidget):
    """Live-Chart fǬr Partikelcount und mittlere Gr��e (angelehnt an alte GUI)."""
    add_data = Signal(int, object)

    def __init__(self, parent=None, max_points: int = 300):
        super().__init__(parent)
        fig = Figure(figsize=(4, 2.2), tight_layout=True)
        self.canvas = FigureCanvas(fig)
        self.ax = fig.add_subplot(111)
        self.ax.set_facecolor(BG)
        for spine in self.ax.spines.values():
            spine.set_color(BORDER); spine.set_linewidth(0.8)
        self.ax.tick_params(colors=FG_MUTED)
        self.ax.grid(True, alpha=0.25, color=BORDER)
        self.ax.set_xlabel("Frame", color=FG_MUTED)
        self.ax.set_ylabel("Anzahl", color=FG)

        self.ax2 = self.ax.twinx()
        self.ax2.set_ylabel("Durchmesser [px]", color="#ffd166")
        self.ax2.tick_params(colors=FG_MUTED)

        self.x = deque(maxlen=max_points)
        self.counts = deque(maxlen=max_points)
        self.mean_sizes = deque(maxlen=max_points)
        self.counter = 0

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        title = QLabel("Live: Partikel & �~-Gr��Ye", self)
        title.setStyleSheet("font-weight:600;")
        lay.addWidget(title)
        lay.addWidget(self.canvas)

        self.line_count, = self.ax.plot([], [], linewidth=2.0, label="Count", color="#5cc8ff")
        self.line_mean, = self.ax2.plot([], [], linewidth=1.8, linestyle="--", label="�~-Durchmesser", color="#ffd166")

        lines = [self.line_count, self.line_mean]
        labels = [l.get_label() for l in lines]
        leg = self.ax.legend(lines, labels, loc="upper left", frameon=True)
        leg.get_frame().set_alpha(0.2)
        leg.get_frame().set_facecolor(BG_ELEV)
        leg.get_frame().set_edgecolor(BORDER)
        for text in leg.get_texts():
            text.set_color(FG)

        self.add_data.connect(self._on_add)

    def reset(self):
        self.counter = 0
        self.x.clear(); self.counts.clear(); self.mean_sizes.clear()
        self.line_count.set_data([], []); self.line_mean.set_data([], [])
        self.canvas.draw_idle()

    def _on_add(self, count: int, mean_d):
        self.counter += 1
        self.x.append(self.counter)
        self.counts.append(count)
        self.mean_sizes.append(np.nan if mean_d is None else float(mean_d))

        self.line_count.set_data(self.x, self.counts)
        self.line_mean.set_data(self.x, self.mean_sizes)

        xmax = self.counter + 2
        xmin = max(0, xmax - len(self.x) - 2)
        self.ax.set_xlim(xmin, xmax)

        ymax_left = max(5, (max(self.counts) if len(self.counts) else 5) * 1.25)
        self.ax.set_ylim(0, ymax_left)

        valid_sizes = [v for v in list(self.mean_sizes) if np.isfinite(v)]
        if valid_sizes:
            max_right = max(valid_sizes); min_right = min(valid_sizes)
            pad = max(1.0, 0.1 * (max_right - min_right if max_right > min_right else 1.0))
            self.ax2.set_ylim(max(0, min_right - pad), max_right + pad)
        else:
            self.ax2.set_ylim(0, 10)

        self.canvas.draw_idle()


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
        fig = Figure(figsize=(4.6, 2.4), dpi=110, facecolor=BG_ELEV)
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
        self._gitterschieber_total_count = 0
        self._gs_particle_img = pathlib.Path(__file__).with_name('particle_dialog_image.jpg')
        self._gs_angle_img = pathlib.Path(__file__).with_name('angle_dialog_image.jpg')

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
        root = QVBoxLayout(self); root.setContentsMargins(8,8,8,6); root.setSpacing(6)

        def add_back_btn(container_layout):
            """Helper: adds a 'Zurück zum Workflow' button to the given layout."""
            btn = UiFactory.button("Zurück zum Workflow", variant="ghost", min_height=36)
            btn.clicked.connect(self._show_stage_workflow)
            row = QHBoxLayout()
            row.addWidget(btn)
            row.addStretch(1)
            container_layout.addLayout(row)

        # Header
        header = QHBoxLayout(); header.setSpacing(4)
        title = QLabel("Stage-Toolbox"); f = QFont("Manrope", 16, QFont.Bold); title.setFont(f)
        header.addWidget(title); header.addStretch(1)
        # Seriennummer-Suche (sucht im Stage-Teststand-Datenordner nach Dateien/Ordnern)
        self.edSearchSN = UiFactory.line_edit("Seriennummer suchen…", width=240)
        header.addWidget(self.edSearchSN)
        self.btnFindSN = UiFactory.button("Find SN", variant="ghost", min_height=26)
        self.btnFindSN.clicked.connect(lambda: self._on_search_sn())
        self.edSearchSN.returnPressed.connect(lambda: self.btnFindSN.click())
        # Live search: update as the user types (debounced)
        self.edSearchSN.textChanged.connect(self._on_search_sn_live)
        # allow forwarding arrow/enter to popup via eventFilter
        self.edSearchSN.installEventFilter(self)
        header.addWidget(self.btnFindSN)

        self.btnLiveViewTab = UiFactory.button("LIVE VIEW", variant="ghost", min_height=26)
        self.btnLiveViewTab.clicked.connect(self._open_live_view)
        header.addWidget(self.btnLiveViewTab)

        self.btnWorkflowHome = UiFactory.button("Workflow", variant="ghost", min_height=26)
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

        # Summary Bar (globale Info)
        self.chipBatch = UiFactory.chip("Charge: NoBatch")
        self.lblTimer = UiFactory.chip("15:00:00")
        self.chipMeasQA = UiFactory.chip("Messung QA: ?")
        self.chipDurQA  = UiFactory.chip(f"Dauertest QA (Limit {resolve_stage.DUR_MAX_UM:.1f} ?m): ?")

        hero = QFrame()
        hero.setObjectName("Hero")
        heroLayout = QHBoxLayout(hero)
        heroLayout.setContentsMargins(8, 4, 8, 4)
        heroLayout.setSpacing(4)

        heroText = QVBoxLayout(); heroText.setSpacing(2)
        heroTitle = QLabel("Resolve Production Suite")
        heroTitle.setObjectName("HeroTitle")
        heroSubtitle = QLabel("Gefuehrte Workflows fuer Stage, Autofocus, Laserscan & QA.")
        heroSubtitle.setObjectName("HeroSubtitle")
        heroText.addWidget(heroTitle)
        heroText.addWidget(heroSubtitle)

        chipRow = QHBoxLayout(); chipRow.setSpacing(4)
        chipRow.addWidget(self.chipBatch)
        chipRow.addWidget(self.lblTimer)
        chipRow.addWidget(self.chipMeasQA)
        chipRow.addWidget(self.chipDurQA)
        chipRow.addStretch(1)
        heroText.addLayout(chipRow)

        buttonsRow = QHBoxLayout(); buttonsRow.setSpacing(6)
        btnHeroWorkflow = UiFactory.button("Workflow oeffnen", variant="primary", min_height=28)
        btnHeroWorkflow.clicked.connect(self._show_stage_workflow)
        btnHeroLive = UiFactory.button("Live View starten", variant="ghost", min_height=26)
        btnHeroLive.clicked.connect(self._open_live_view)
        buttonsRow.addWidget(btnHeroWorkflow)
        buttonsRow.addWidget(btnHeroLive)
        buttonsRow.addStretch(1)
        heroText.addLayout(buttonsRow)

        heroLayout.addLayout(heroText, 1)
        root.addWidget(hero)

        images_dir = _BASE_DIR / "images"
        stage_img = _first_existing([images_dir / "stage.png", _BASE_DIR / "assets" / "stage_tile.png"])
        af_img    = _first_existing([images_dir / "autofocus.png", _BASE_DIR / "assets" / "autofocus_tile.png"])
        laser_img = _first_existing([images_dir / "laserscan.png", _BASE_DIR / "assets" / "laserscan_tile.png"])

        # --- WORKFLOW (Kacheln) ---
        # Seitenumschaltung (in ScrollArea, damit Vollbild sauber aussieht)
        self.stack = QStackedWidget()
        self.contentScroll = QScrollArea()
        self.contentScroll.setWidgetResizable(True)
        self.contentScroll.setFrameShape(QFrame.NoFrame)
        self.contentScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.contentScroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.contentScroll.setWidget(self.stack)
        root.addWidget(self.contentScroll, 1)

        # ===================== Stage Seite =====================
        self.stagePage = QWidget()
        stageLayout = QVBoxLayout(self.stagePage)
        stageLayout.setContentsMargins(0,0,0,0); stageLayout.setSpacing(8)

        self.workflowCard = Card("Workflow")
        stageLayout.addWidget(self.workflowCard)
        tiles = QHBoxLayout(); tiles.setSpacing(8); tiles.setContentsMargins(0, 0, 0, 0)
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
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(0)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        stageLayout.addLayout(grid, 1)

        # Left: Setup (Meta + Aktionen)
        self.cardSetup = Card("Setup")
        grid.addWidget(self.cardSetup, 0, 0)

        setupGrid = QGridLayout(); setupGrid.setSpacing(8); setupGrid.setContentsMargins(0,0,0,0)
        self.cardSetup.body.addLayout(setupGrid)

        metaCol = QVBoxLayout(); metaCol.setSpacing(8)
        # Operator
        self.edOperator = UiFactory.line_edit("Bediener: z. B. M. Zschach")
        metaCol.addLayout(UiFactory.form_row("Operator", self.edOperator))

        # Batch
        self.edBatch = UiFactory.line_edit("Chargennummer, z. B. B2025-10-30-01")
        regex = QRegularExpression(r"^[A-Za-z0-9._-]{0,64}$")
        self.edBatch.setValidator(QRegularExpressionValidator(regex))
        metaCol.addLayout(UiFactory.form_row("Charge", self.edBatch))

        # Bemerkungen
        self.txtNotes = QTextEdit(); self.txtNotes.setPlaceholderText("Bemerkungen zum Lauf.")
        self.txtNotes.setFixedHeight(50)
        metaCol.addWidget(UiFactory.section_label("Bemerkungen"))
        metaCol.addWidget(self.txtNotes)

        setupGrid.addLayout(metaCol, 0, 0)

        actionsCol = QVBoxLayout(); actionsCol.setSpacing(6)
        self.btnStart = UiFactory.button("Test starten (Ctrl+R)", variant="primary", min_height=28); self.btnStart.clicked.connect(self._start_test)
        self.btnDauer = UiFactory.button("Dauertest starten (Ctrl+D)", variant="primary", min_height=28); self.btnDauer.clicked.connect(self._toggle_dauertest)
        self.btnOpenFolder = UiFactory.button("Ordner ?ffnen", variant="ghost", min_height=32); self.btnOpenFolder.setEnabled(False); self.btnOpenFolder.clicked.connect(self._open_folder)
        self.btnKleberoboter = UiFactory.button("Datenbank senden", variant="ghost", min_height=26); self.btnKleberoboter.clicked.connect(self._trigger_kleberoboter)

        # Dauertest-Button + Dauer-Dropdown nebeneinander
        dauerRow = QHBoxLayout(); dauerRow.setSpacing(6)
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
        self.comboDur.setFixedWidth(140)
        self.comboDur.currentIndexChanged.connect(self._on_duration_mode_changed)
        dauerRow.addWidget(self.comboDur, 0, Qt.AlignRight)

        actionsCol.addWidget(self.btnStart)
        actionsCol.addLayout(dauerRow)
        actionsCol.addWidget(self.btnOpenFolder)
        actionsCol.addWidget(self.btnKleberoboter)
        actionsCol.addStretch(1)

        setupGrid.addLayout(actionsCol, 0, 1)
        setupGrid.setColumnStretch(0, 2); setupGrid.setColumnStretch(1, 1)

        # Right: Status + Plot + QA
        self.cardStatus = Card("Status & Live-Plot")
        grid.addWidget(self.cardStatus, 0, 1)
        statusRow = QHBoxLayout(); statusRow.setSpacing(8)

        statusCol = QVBoxLayout(); statusCol.setSpacing(4)
        self.lblPhase = QLabel("-")
        self.pbar = QProgressBar(); self._reset_progress()
        statusCol.addWidget(self.lblPhase)
        statusCol.addWidget(self.pbar)
        statusCol.addWidget(UiFactory.section_label("Kalibrierung"))
        self.lblCalib = QLabel("-")
        statusCol.addWidget(self.lblCalib)
        self.btnNewStage = UiFactory.button("Neue Stage testen", variant="primary", min_height=32)
        self.btnNewStage.setVisible(False)
        self.btnNewStage.clicked.connect(self._new_stage)
        statusCol.addWidget(self.btnNewStage)
        statusCol.addStretch(1)

        plotCol = QVBoxLayout(); plotCol.setSpacing(4)
        self.plotContainer = QWidget()
        self.plotContainer.setMinimumHeight(160)
        self.plotContainerLayout = QVBoxLayout(self.plotContainer)
        self.plotContainerLayout.setContentsMargins(0,0,0,0)
        self.plotContainerLayout.setSpacing(0)
        self.plotHolder = QVBoxLayout(); self.plotHolder.setSpacing(0)
        self.plotHolder.addWidget(self.plotContainer)
        plotCol.addLayout(self.plotHolder)

        statusRow.addLayout(statusCol, 1)
        statusRow.addLayout(plotCol, 2)
        self.cardStatus.body.addLayout(statusRow)

        self.stack.addWidget(self.stagePage)

        # ===================== Gitterschieber Seite =====================
        self.gitterschieberPage = QWidget()
        gsLayout = QVBoxLayout(self.gitterschieberPage)
        gsLayout.setContentsMargins(0, 0, 0, 0); gsLayout.setSpacing(14)

        gsCard = Card("Gitterschieber")
        gsLayout.addWidget(gsCard, 1)

        gsGrid = QGridLayout(); gsGrid.setSpacing(12); gsGrid.setContentsMargins(0, 0, 0, 0)
        gsCard.body.addLayout(gsGrid)

        # Linke Spalte: Kamera + Status + Chart
        leftCol = QVBoxLayout(); leftCol.setSpacing(10)
        cam_provider = lambda: gs.capture_frame()
        self.gitterschieberCam = LiveCamEmbed(
            cam_provider,
            interval_ms=150,
            start_immediately=False,
            parent=self.gitterschieberPage,
        )
        leftCol.addWidget(self.gitterschieberCam)

        angleRow = QHBoxLayout(); angleRow.setSpacing(8)
        angleRow.addWidget(UiFactory.section_label("Angle:"))
        self.gitterschieberAngleLabel = UiFactory.chip("0.0°")
        self.gitterschieberAngleLabel.setFont(QFont("Inter", 14, QFont.Bold))
        angleRow.addWidget(self.gitterschieberAngleLabel)
        angleRow.addStretch(1)
        leftCol.addLayout(angleRow)

        frameRow = UiFactory.metric_field("Frame", "Frame Count")
        self.gitterschieberFrameCount = frameRow.field
        totalRow = UiFactory.metric_field("Total", "Total Count")
        self.gitterschieberTotalCount = totalRow.field
        leftCol.addLayout(frameRow)
        leftCol.addLayout(totalRow)

        self.gitterschieberChart = GitterschieberLiveChart(self.gitterschieberPage)
        leftCol.addWidget(self.gitterschieberChart)
        gsGrid.addLayout(leftCol, 0, 0, 2, 1)

        # Rechte Spalte: Aktionen + Empfindlichkeit
        rightCol = QVBoxLayout(); rightCol.setSpacing(12)
        dialWrap = QVBoxLayout()
        dialLabel = QLabel("Angle Dial"); dialLabel.setStyleSheet("font-weight:600;")
        self.gitterschieberDial = QDial()
        self.gitterschieberDial.setNotchesVisible(True)
        self.gitterschieberDial.setRange(-180, 180)
        self.gitterschieberDial.setValue(0)
        self.gitterschieberDial.setToolTip("Winkelanzeige (read-only)")
        self.gitterschieberDial.setEnabled(False)
        dialWrap.addWidget(dialLabel)
        dialWrap.addWidget(self.gitterschieberDial)
        rightCol.addLayout(dialWrap)
        self.btnGitterschieberParticle = UiFactory.button("Partikel Detektion", variant="primary", min_height=44)
        self.btnGitterschieberAngle = UiFactory.button("Winkel Justage", variant="primary", min_height=44)
        self.btnGitterschieberAutofocus = UiFactory.button("Autofokus", variant="ghost", min_height=40)
        self.btnGitterschieberParticle.clicked.connect(self._on_gitterschieber_particles)
        self.btnGitterschieberAngle.clicked.connect(self._on_gitterschieber_angle)
        self.btnGitterschieberAutofocus.clicked.connect(self._on_gitterschieber_autofocus)

        rightCol.addWidget(self.btnGitterschieberParticle)
        rightCol.addWidget(self.btnGitterschieberAngle)
        rightCol.addWidget(self.btnGitterschieberAutofocus)

        sensWrap = QVBoxLayout()
        lblSens = QLabel("Empfindlichkeit")
        self.gitterschieberSensLabel = QLabel("balanced_high")
        self.gitterschieberSensSlider = QSlider(Qt.Horizontal)
        self.gitterschieberSensSlider.setRange(0, 100)
        self.gitterschieberSensSlider.setValue(int(gs.DETECTION_SENSITIVITY * 100))
        self.gitterschieberSensSlider.valueChanged.connect(self._on_gitterschieber_sensitivity_changed)
        self._update_gitterschieber_sens_label(self.gitterschieberSensSlider.value())
        sensRow = QHBoxLayout()
        sensRow.addWidget(self.gitterschieberSensSlider, 1)
        sensRow.addWidget(self.gitterschieberSensLabel)
        sensWrap.addWidget(lblSens)
        sensWrap.addLayout(sensRow)
        rightCol.addLayout(sensWrap)

        rightCol.addStretch(1)
        self.gitterschieber_status = QLabel("")
        rightCol.addWidget(self.gitterschieber_status)
        gsGrid.addLayout(rightCol, 0, 1)
        gsGrid.setColumnStretch(0, 3); gsGrid.setColumnStretch(1, 1)

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
            btn = UiFactory.button(text, variant="primary", min_height=90)
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
                self._dashboard_widget.setMinimumHeight(650)
                self._dashboard_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                liveCard.body.addWidget(self._dashboard_widget, 1)
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
                self._dashboard_widget.setMinimumHeight(650)
                self._dashboard_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                layout = self.liveViewPage.layout()
                if layout and layout.count() > 0:
                    card = layout.itemAt(0).widget()
                    if isinstance(card, Card):
                        card.body.addWidget(self._dashboard_widget, 1)
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
            self._gitterschieber_total_count = 0
            try:
                self.gitterschieberFrameCount.clear()
                self.gitterschieberTotalCount.setText("0")
                self.gitterschieberAngleLabel.setText("0.0°")
                try:
                    self.gitterschieberDial.setValue(0)
                except Exception:
                    pass
                self.gitterschieberChart.reset()
            except Exception:
                pass
            try:
                if getattr(self, "gitterschieberCam", None):
                    self.gitterschieberCam.start()
            except Exception:
                pass
            try:
                self._update_gitterschieber_sens_label(self.gitterschieberSensSlider.value())
            except Exception:
                pass
            try:
                self.btnGitterschieberParticle.setFocus()
            except Exception:
                pass
            self._set_status("Gitterschieber geöffnet.")
        except Exception as exc:
            QMessageBox.warning(self, "Gitterschieber", f"Start fehlgeschlagen:\n{exc}")

    def _on_gitterschieber_particles(self):
        try:
            if not self._show_gitterschieber_dialog(
                "Partikel-Detektion",
                "Bitte die Vergrößerung am Mikroskop auf die höchste Stufe stellen\n"
                "und mit dem Handrad auf das Live-Bild fokussieren.\n\n"
                "Sobald das Bild passt, \"Analyse starten\" wählen.",
                "Analyse starten",
                self._gs_particle_img,
            ):
                return
            frame = self.gitterschieberCam.last_frame() or gs.capture_frame()
            if frame is None:
                self._set_gitterschieber_status("Kein Bild verfügbar.")
                return
            result = gs.process_image(frame.copy(), sensitivity=gs.DETECTION_SENSITIVITY)
            df = result.get("dataframe")
            count = int(result.get("count", len(df) if df is not None else 0))
            self._update_gitterschieber_metrics(count, df)
            overlay = result.get("overlay")
            if overlay is not None:
                self._show_gitterschieber_overlay(overlay)
            self._set_gitterschieber_status(f"Partikelanalyse: {count} gefunden.")
        except Exception as exc:
            self._set_gitterschieber_status(f"Fehler: {exc}")

    def _on_gitterschieber_angle(self):
        try:
            if not self._show_gitterschieber_dialog(
                "Winkel-Analyse",
                "Bitte die Vergrößerung wie im Referenzbild einstellen,\n"
                "dann mit dem Handrad auf das Live-Bild fokussieren.\n\n"
                "Wenn das Bild passt, \"Analyse starten\" drücken.",
                "Analyse starten",
                self._gs_angle_img,
            ):
                return
            frame = self.gitterschieberCam.last_frame() or gs.capture_frame()
            if frame is None:
                self._set_gitterschieber_status("Kein Bild verfügbar.")
                return
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            angle = gs.SingleImageGratingAngle(gray)
            self.gitterschieberAngleLabel.setText(f"{angle:.2f}°")
            try:
                self.gitterschieberDial.setValue(int(round(angle)))
            except Exception:
                pass
            self._set_gitterschieber_status(f"Winkel: {angle:.2f}°")
        except Exception as exc:
            self._set_gitterschieber_status(f"Fehler: {exc}")

    def _on_gitterschieber_autofocus(self):
        self._set_gitterschieber_status("Autofokus aktuell nicht integriert.")

    def _on_gitterschieber_sensitivity_changed(self, val: int):
        gs.DETECTION_SENSITIVITY = float(val) / 100.0
        self._update_gitterschieber_sens_label(val)
        frame = self.gitterschieberCam.last_frame()
        if frame is None:
            return
        try:
            result = gs.process_image(frame.copy(), sensitivity=gs.DETECTION_SENSITIVITY)
            df = result.get("dataframe")
            count = int(result.get("count", len(df) if df is not None else 0))
            self._update_gitterschieber_metrics(count, df, accumulate=False)
            overlay = result.get("overlay")
            if overlay is not None:
                self._show_gitterschieber_overlay(overlay)
        except Exception:
            pass

    def _update_gitterschieber_metrics(self, count: int, df: pd.DataFrame | None, *, accumulate: bool = True):
        try:
            self.gitterschieberFrameCount.setText(str(count))
            if accumulate:
                self._gitterschieber_total_count += count
            self.gitterschieberTotalCount.setText(str(self._gitterschieber_total_count))
        except Exception:
            pass
        mean_d = None
        try:
            if df is not None and not df.empty:
                mean_d = float(df["equiv_diam_px"].mean())
        except Exception:
            mean_d = None
        try:
            self.gitterschieberChart.add_data.emit(count, mean_d)
        except Exception:
            pass

    def _show_gitterschieber_overlay(self, frame):
        try:
            if getattr(self, "gitterschieberCam", None):
                self.gitterschieberCam.stop()
            pm = frame_to_qpixmap(frame, (self.gitterschieberCam.label.width(), self.gitterschieberCam.label.height()))
            self.gitterschieberCam.label.setPixmap(pm)
            QTimer.singleShot(1200, lambda: self.gitterschieberCam.start())
        except Exception as exc:
            self._set_gitterschieber_status(f"Overlay-Fehler: {exc}")

    def _update_gitterschieber_sens_label(self, slider_val: int):
        try:
            self.gitterschieberSensLabel.setText(self._gitterschieber_sens_text(slider_val / 100.0))
        except Exception:
            pass

    def _gitterschieber_sens_text(self, val: float) -> str:
        if val < 0.33:
            return "balanced_low"
        if val < 0.66:
            return "balanced_mid"
        return "balanced_high"

    def _set_gitterschieber_status(self, text: str):
        if hasattr(self, "gitterschieber_status") and self.gitterschieber_status is not None:
            self.gitterschieber_status.setText(text)

    def _show_gitterschieber_dialog(self, title: str, text: str, accept_label: str = "OK", pixmap_path: pathlib.Path | None = None) -> bool:
        """Zeigt einen dunklen Hinweisdialog mit optionalem Bild an."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(text)
        if pixmap_path:
            try:
                p = pathlib.Path(pixmap_path)
                if p.exists():
                    pm = QPixmap(str(p))
                    if not pm.isNull():
                        box.setIconPixmap(pm.scaledToWidth(280, Qt.SmoothTransformation))
            except Exception:
                pass
        box.setStyleSheet("""
            QMessageBox { background-color:#0f1115; color:#eaeaea; }
            QMessageBox QLabel { color:#eaeaea; font-size:11pt; }
            QMessageBox QPushButton {
                background:#1b2030; color:#f0f0f0; border:1px solid #222638;
                border-radius:8px; padding:6px 14px; font-weight:600;
            }
            QMessageBox QPushButton:hover { background:#242b3f; }
            QMessageBox QPushButton:pressed { background:#29314a; }
        """)
        start_btn = box.addButton(accept_label, QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(start_btn)
        box.exec()
        return box.clickedButton() is start_btn

    def _af_frame_provider(self):
        """Frame-Provider fuer Autofocus-Kamerakarte."""
        try:
            return autofocus.acquire_frame(device_index=0)
        except Exception as exc:
            print("[WARN] Autofocus-Kamera nicht verfuegbar:", exc)
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
        color = "#3cb179" if ok else "#d95c5c"
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
            self.plot=LivePlot(self, batch=self._batch)
            # Remove old widgets in plot container (if any) to ensure full height usage
            while self.plotContainerLayout.count():
                item = self.plotContainerLayout.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
            self.plotContainerLayout.addWidget(self.plot)
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
            cur, mn, mx = autofocus.get_exposure_limits(device_index)
            self._init_exposure_ui_from_limits(cur, mn, mx)
        except Exception as exc:
            print(f"[WARN] Exposure-Init (device {device_index}): {exc}")
            self._init_default_exposure_ui()

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
            autofocus.set_exposure(device_index, exposure_us)
            self._set_status(f"Exposure gesetzt: {exposure_us/1000.0:.3f} ms")
        except Exception as exc:
            self._set_status(f"Exposure (Demo) gespeichert: {exposure_us/1000.0:.3f} ms")
            print(f"[WARN] Exposure setzen fehlgeschlagen: {exc}")

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
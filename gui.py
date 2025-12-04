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
import numpy as np

# Ensure relocated modules are importable (./Hardware, ./Algorithmen)
_BASE_DIR = pathlib.Path(__file__).resolve().parent
for _sub in ("Hardware", "Algorithmen"):
    _cand = _BASE_DIR / _sub
    if _cand.exists():
        sys.path.insert(0, str(_cand))

import cv2

from PySide6.QtCore import (
    Qt, QTimer, QSize, QPoint, QRect, Signal, Slot, QPropertyAnimation, QEasingCurve,
    QObject, QThread, QRegularExpression, QEvent, QSortFilterProxyModel, QAbstractTableModel
)
from PySide6.QtGui import (
    QColor, QPalette, QFont, QPainter, QPen, QBrush, QLinearGradient, QGradient, QIcon,
    QPainterPath, QPixmap, QShortcut, QKeySequence, QRegularExpressionValidator, QImage
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QGridLayout, QSlider, QSizePolicy, QScrollArea, QGraphicsDropShadowEffect, QAbstractItemView,
    QProgressBar, QMessageBox, QSpacerItem, QComboBox, QToolButton, QTableView, QDoubleSpinBox,
    QDialog, QListWidget, QCheckBox, QFormLayout, QGroupBox, QDial
)

# Matplotlib integration
import matplotlib
matplotlib.use("qtagg")
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib import image as mpimg
import matplotlib.pyplot as plt

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

# --- THEME CONFIGURATION (Matches Tailwind Config) ---
COLORS = {
    "bg": "#080f1a",           # Main Background
    "surface": "#111b2b",      # Card Background
    "surface_light": "#1c2a42",# Inputs / Hover
    "border": "#1b2a3f",
    "primary": "#5ce2cf",      # Mint
    "primary_hover": "#46bfa9",
    "secondary": "#f0b74a",    # Amber
    "text": "#e8edf5",
    "text_muted": "#9fb2c8",
    "danger": "#ef4444",
    "success": "#10b981",
}

FONTS = {
    "ui": "Segoe UI", # Fallback for Inter/Manrope
    "mono": "Consolas",
}

# Global Stylesheet for things hard to style in Python code
GLOBAL_STYLESHEET = f"""
QMainWindow {{ background-color: {COLORS['bg']}; }}
QWidget {{ 
    background-color: transparent; 
    color: {COLORS['text']}; 
    font-family: "{FONTS['ui']}";
    font-size: 14px;
}}

/* SCROLLBARS */
QScrollBar:vertical {{
    border: none;
    background: {COLORS['bg']};
    width: 8px;
    margin: 0px 0px 0px 0px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border']};
    min-height: 20px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{ background: {COLORS['surface_light']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

QScrollBar:horizontal {{
    border: none;
    background: {COLORS['bg']};
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {COLORS['border']};
    min-width: 20px;
    border-radius: 4px;
}}

/* GENERIC WIDGETS */
QLineEdit, QTextEdit {{
    background-color: {COLORS['surface_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    padding: 10px 12px;
    color: {COLORS['text']};
    selection-background-color: {COLORS['primary']};
    selection-color: {COLORS['bg']};
    font-size: 13px;
}}
QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {COLORS['primary']};
    background-color: {COLORS['surface']};
}}

/* TABLE WIDGET */
QTableWidget {{
    background-color: {COLORS['surface']};
    gridline-color: {COLORS['border']};
    border: none;
    border-radius: 8px;
    outline: none;
}}
QTableWidget::item {{
    padding: 8px;
    border-bottom: 1px solid {COLORS['border']};
}}
QTableWidget::item:selected {{
    background-color: {COLORS['surface_light']};
    color: {COLORS['primary']};
}}
QHeaderView::section {{
    background-color: {COLORS['surface_light']};
    color: {COLORS['text_muted']};
    padding: 8px;
    border: none;
    font-weight: bold;
    font-size: 12px;
    text-transform: uppercase;
    border-bottom: 2px solid {COLORS['bg']};
}}

/* SLIDER */
QSlider::groove:horizontal {{
    border: 1px solid {COLORS['border']};
    height: 6px;
    background: {COLORS['surface_light']};
    margin: 2px 0;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {COLORS['primary']};
    border: 1px solid {COLORS['primary']};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}
"""

# --- CUSTOM COMPONENTS ---

class ModernButton(QPushButton):
    """
    A button that mimics the Tailwind variants: Primary, Secondary, Ghost, Danger.
    """
    def __init__(self, text="", variant="primary", icon=None, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(42)
        if icon:
            # Placeholder: In a real app, pass a QIcon
            pass 

        base_style = """
            QPushButton {
                border-radius: 12px;
                font-weight: 600;
                font-size: 13px;
                padding: 0 16px;
            }
        """
        
        if variant == "primary":
            self.setStyleSheet(base_style + f"""
                QPushButton {{
                    background-color: {COLORS['primary']};
                    color: {COLORS['bg']};
                    border: 1px solid {COLORS['primary']};
                }}
                QPushButton:hover {{ background-color: {COLORS['primary_hover']}; border-color: {COLORS['primary_hover']}; }}
                QPushButton:pressed {{ background-color: {COLORS['primary']}; margin-top: 1px; }}
            """)
        elif variant == "secondary":
            self.setStyleSheet(base_style + f"""
                QPushButton {{
                    background-color: {COLORS['surface_light']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                }}
                QPushButton:hover {{ border: 1px solid {COLORS['primary']}; background-color: {COLORS['border']}; }}
            """)
        elif variant == "ghost":
            self.setStyleSheet(base_style + f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLORS['text_muted']};
                    border: none;
                }}
                QPushButton:hover {{ background-color: {COLORS['surface_light']}; color: {COLORS['text']}; }}
            """)
        elif variant == "danger":
            self.setStyleSheet(base_style + f"""
                QPushButton {{
                    background-color: rgba(239, 68, 68, 0.15);
                    color: {COLORS['danger']};
                    border: 1px solid rgba(239, 68, 68, 0.3);
                }}
                QPushButton:hover {{ background-color: rgba(239, 68, 68, 0.25); }}
            """)

class Card(QFrame):
    """
    Simulates the rounded, bordered card component.
    """
    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 16px;
            }}
        """)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0) # Handle padding internally
        self.main_layout.setSpacing(0)

        # Header
        if title:
            header_frame = QFrame()
            header_frame.setStyleSheet(f"border-bottom: 1px solid {COLORS['border']}; border-radius: 16px 16px 0 0; background-color: {COLORS['surface']}; border-left: none; border-right: none; border-top: none;")
            header_layout = QHBoxLayout(header_frame)
            header_layout.setContentsMargins(20, 16, 20, 16)
            
            lbl = QLabel(title)
            lbl.setStyleSheet("border: none; font-size: 15px; font-weight: 700; letter-spacing: 0.3px;")
            header_layout.addWidget(lbl)
            
            self.main_layout.addWidget(header_frame)

        # Body
        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("border: none;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(24, 24, 24, 24)
        self.content_layout.setSpacing(16)
        
        self.main_layout.addWidget(self.content_widget)

    def add_widget(self, widget):
        self.content_layout.addWidget(widget)

    def add_layout(self, layout):
        self.content_layout.addLayout(layout)

class StatusBadge(QLabel):
    def __init__(self, text, status="success"):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        color = COLORS['success'] if status == "success" else COLORS['danger']
        bg_opacity = "20" # hex alpha
        
        self.setStyleSheet(f"""
            QLabel {{
                color: {color};
                background-color: {color}{bg_opacity};
                border: 1px solid {color}40;
                border-radius: 12px;
                padding: 4px 10px;
                font-weight: 700;
                font-size: 11px;
            }}
        """)
        self.setFixedHeight(24)

class ModernChart(FigureCanvasQTAgg):
    def __init__(self, parent=None, height=4):
        self.fig = Figure(figsize=(5, height), dpi=100)
        self.fig.patch.set_facecolor(COLORS['surface'])
        super().__init__(self.fig)
        self.setParent(parent)
        self.ax = self.fig.add_subplot(111)
        self.style_chart()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def style_chart(self):
        self.ax.set_facecolor(COLORS['surface'])
        # Remove spines
        for spine in self.ax.spines.values():
            spine.set_visible(False)
        
        # Grid
        self.ax.grid(True, linestyle='--', linewidth=1, alpha=0.15, color=COLORS['text_muted'])
        self.ax.set_axisbelow(True)
        
        # Ticks
        self.ax.tick_params(axis='x', colors=COLORS['text_muted'], labelsize=9)
        self.ax.tick_params(axis='y', colors=COLORS['text_muted'], labelsize=9)
        self.fig.tight_layout(pad=2)

# --- VIEWS ---

class DashboardView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(24)

        # 1. KPIs
        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(24)
        
        self.add_kpi(kpi_layout, "Total Output", "1,245", "+12% vs last week", COLORS['primary'])
        self.add_kpi(kpi_layout, "Pass Rate", "98.4%", "-0.2% vs last week", COLORS['secondary'])
        self.add_kpi(kpi_layout, "Last Batch", "OK", "B2025-10-30-05", COLORS['success'])
        
        layout.addLayout(kpi_layout)

        # 2. Table
        table_card = Card("Recent Test Activity")
        
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Batch ID", "Operator", "Type", "Status", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        
        self.load_mock_data()
        
        table_card.add_widget(self.table)
        layout.addWidget(table_card)

    def add_kpi(self, layout, title, value, sub, accent_color):
        card = Card()
        # Custom KPI layout without standard header
        container = QWidget()
        l = QVBoxLayout(container)
        l.setSpacing(10)
        l.setContentsMargins(20, 20, 20, 20)
        
        # Border Left Accent
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 16px;
                border-left: 4px solid {accent_color};
            }}
        """)

        t_lbl = QLabel(title.upper())
        t_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-weight: 700; font-size: 11px; letter-spacing: 0.5px; border:none;")
        
        v_lbl = QLabel(value)
        v_lbl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 36px; border:none;")
        
        s_lbl = QLabel(sub)
        s_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px; border:none;")

        l.addWidget(t_lbl)
        l.addWidget(v_lbl)
        l.addWidget(s_lbl)
        
        card.main_layout.addWidget(container)
        layout.addWidget(card)

    def load_mock_data(self):
        data = [
            ("08:30", "B25-10-01", "M. Zschach", "Stage", "OK", "Max Δ 2.4µm"),
            ("09:15", "B25-10-02", "A. Weber", "Gitter", "OK", "Angle 0.05°"),
            ("10:00", "B25-10-03", "M. Zschach", "Kleber", "FAIL", "Dispense Err"),
            ("10:45", "B25-10-04", "K. Liu", "Stage", "OK", "Max Δ 1.1µm"),
            ("11:20", "B25-10-05", "M. Zschach", "Gitter", "OK", "Count: 142"),
        ]
        self.table.setRowCount(len(data))
        for i, (time, batch, op, type_, status, det) in enumerate(data):
            self.table.setItem(i, 0, QTableWidgetItem(time))
            self.table.setItem(i, 1, QTableWidgetItem(batch))
            self.table.setItem(i, 2, QTableWidgetItem(op))
            
            # Styled Type Badge
            type_item = QTableWidgetItem(type_)
            type_item.setForeground(QBrush(QColor(COLORS['text'])))
            self.table.setItem(i, 3, type_item)
            
            # Status Widget (using a cell widget for custom styling)
            status_widget = QWidget()
            sl = QHBoxLayout(status_widget)
            sl.setContentsMargins(0,0,0,0)
            sl.setAlignment(Qt.AlignLeft)
            badge = StatusBadge(status, "success" if status == "OK" else "danger")
            sl.addWidget(badge)
            self.table.setCellWidget(i, 4, status_widget)
            
            det_item = QTableWidgetItem(det)
            det_item.setForeground(QBrush(QColor(COLORS['text_muted'])))
            det_item.setFont(QFont(FONTS['mono'], 9))
            self.table.setItem(i, 5, det_item)

class StageControlView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.running = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_chart)
        self.x_data = deque(maxlen=60)
        self.y1_data = deque(maxlen=60)
        self.y2_data = deque(maxlen=60)
        self.tick = 0

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(24)

        # Left Column
        left_col = QVBoxLayout()
        left_col.setSpacing(24)
        
        setup_card = Card("Test Setup")
        
        form_layout = QVBoxLayout()
        form_layout.setSpacing(15)
        
        self.add_input(form_layout, "Operator", "M. Zschach")
        self.add_input(form_layout, "Batch Number", "B2025-10-30-01")
        
        lbl_note = QLabel("NOTES")
        lbl_note.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLORS['text_muted']}; border: none;")
        form_layout.addWidget(lbl_note)
        
        note_edit = QTextEdit()
        note_edit.setPlaceholderText("Enter run comments...")
        note_edit.setFixedHeight(70)
        form_layout.addWidget(note_edit)
        
        btn_layout = QGridLayout()
        self.btn_start = ModernButton("Start Test", "primary")
        self.btn_start.clicked.connect(self.toggle_test)
        btn_layout.addWidget(self.btn_start, 0, 0, 1, 2)
        btn_layout.addWidget(ModernButton("Open Folder", "secondary"), 1, 0)
        btn_layout.addWidget(ModernButton("DB Sync", "ghost"), 1, 1)
        
        form_layout.addLayout(btn_layout)
        setup_card.add_layout(form_layout)
        
        left_col.addWidget(setup_card)
        
        # Status Card
        status_card = Card("QA Status")
        sl = QVBoxLayout()
        
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Calibration (X/Y)"))
        calib_val = QLabel("1.002 / 0.998")
        calib_val.setStyleSheet(f"font-family: {FONTS['mono']}; font-weight: bold; color: {COLORS['text']};")
        row1.addWidget(calib_val)
        sl.addLayout(row1)
        
        # QA Box
        self.qa_box = QFrame()
        self.qa_box.setStyleSheet(f"""
            background-color: {COLORS['success']}15;
            border: 1px solid {COLORS['success']}40;
            border-radius: 12px;
        """)
        qa_layout = QHBoxLayout(self.qa_box)
        
        qa_info = QVBoxLayout()
        qa_lbl = QLabel("Endurance QA")
        qa_lbl.setStyleSheet(f"color: {COLORS['success']}; font-weight: bold; font-size: 12px; border:none; background:transparent;")
        qa_lim = QLabel("Limit: 8.0 µm")
        qa_lim.setStyleSheet(f"color: {COLORS['success']}; opacity: 0.8; font-size: 11px; border:none; background:transparent;")
        qa_info.addWidget(qa_lbl)
        qa_info.addWidget(qa_lim)
        
        self.qa_val = QLabel("0.42 µm")
        self.qa_val.setStyleSheet(f"color: {COLORS['success']}; font-weight: 800; font-size: 18px; border:none; background:transparent;")
        
        qa_layout.addLayout(qa_info)
        qa_layout.addStretch()
        qa_layout.addWidget(self.qa_val)
        
        sl.addWidget(self.qa_box)
        status_card.add_layout(sl)
        
        left_col.addWidget(status_card)
        left_col.addStretch()
        
        layout.addLayout(left_col, 1)

        # Right Column (Chart)
        right_col = QVBoxLayout()
        chart_card = Card("Live Position Error")
        
        # Header inside chart
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Real-time deviation (X vs Y)"))
        top_row.addStretch()
        self.timer_lbl = QLabel("15:00:00")
        self.timer_lbl.setStyleSheet(f"font-family: {FONTS['mono']}; font-size: 20px; font-weight: bold; color: {COLORS['primary']};")
        top_row.addWidget(self.timer_lbl)
        chart_card.add_layout(top_row)
        
        self.chart = ModernChart(height=5)
        self.line_x, = self.chart.ax.plot([], [], color=COLORS['primary'], linewidth=2, label="Error X")
        self.line_y, = self.chart.ax.plot([], [], color=COLORS['secondary'], linewidth=2, label="Error Y")
        
        # Chart Legend
        leg = self.chart.ax.legend(loc='upper right', facecolor=COLORS['surface'], edgecolor=COLORS['border'], labelcolor=COLORS['text'])
        leg.get_frame().set_linewidth(1)
        
        chart_card.add_widget(self.chart)
        right_col.addWidget(chart_card)
        
        layout.addLayout(right_col, 2)

    def add_input(self, layout, label, placeholder):
        lbl = QLabel(label.upper())
        lbl.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLORS['text_muted']}; border: none; margin-bottom: 4px;")
        inp = QLineEdit()
        inp.setPlaceholderText(placeholder)
        layout.addWidget(lbl)
        layout.addWidget(inp)

    def toggle_test(self):
        if self.running:
            self.running = False
            self.btn_start.setText("Start Test")
            self.btn_start.setStyleSheet(self.btn_start.styleSheet().replace(COLORS['danger'], COLORS['primary'])) # Reset to primary hack
            self.timer.stop()
        else:
            self.running = True
            self.btn_start.setText("Stop Test")
            # Create a "Danger" variant on the fly or swap widgets
            self.timer.start(100)
            self.x_data.clear()
            self.y1_data.clear()
            self.y2_data.clear()
            self.tick = 0

    def update_chart(self):
        self.tick += 1
        noise = np.random.normal(0, 0.2)
        val1 = np.sin(self.tick * 0.1) + noise
        val2 = np.cos(self.tick * 0.1) * 0.5 + noise
        
        self.x_data.append(self.tick)
        self.y1_data.append(val1)
        self.y2_data.append(val2)
        
        self.line_x.set_data(self.x_data, self.y1_data)
        self.line_y.set_data(self.x_data, self.y2_data)
        
        self.chart.ax.relim()
        self.chart.ax.autoscale_view()
        self.chart.draw()
        
        # Update QA text
        max_err = max(abs(val1), abs(val2))
        self.qa_val.setText(f"{max_err:.2f} µm")
        if max_err > 2.0: # Arbitrary visual limit
             self.qa_box.setStyleSheet(f"background-color: {COLORS['danger']}15; border: 1px solid {COLORS['danger']}40; border-radius: 12px;")
             self.qa_val.setStyleSheet(f"color: {COLORS['danger']}; font-weight: 800; font-size: 18px; border:none; background:transparent;")
        else:
             self.qa_box.setStyleSheet(f"background-color: {COLORS['success']}15; border: 1px solid {COLORS['success']}40; border-radius: 12px;")
             self.qa_val.setStyleSheet(f"color: {COLORS['success']}; font-weight: 800; font-size: 18px; border:none; background:transparent;")

class GitterschieberView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(24)
        
        # Visualizer Card (Left)
        viz_card = Card()
        
        # Custom Paint Widget for the "Live Feed" look
        self.viz_widget = QWidget()
        self.viz_widget.setStyleSheet(f"background-color: #000; border-radius: 12px;")
        
        # Mock overlay layout
        vl = QVBoxLayout(self.viz_widget)
        badge = QLabel(" • LIVE ANALYSIS ")
        badge.setStyleSheet(f"background-color: rgba(0,0,0,0.6); color: {COLORS['primary']}; border-radius: 4px; padding: 4px 8px; font-weight: bold; font-size: 11px;")
        vl.addWidget(badge, 0, Qt.AlignLeft | Qt.AlignTop)
        vl.addStretch()
        
        viz_card.add_widget(self.viz_widget)
        layout.addWidget(viz_card, 2)
        
        # Controls (Right)
        right_panel = QVBoxLayout()
        right_panel.setSpacing(24)
        
        ctrl_card = Card("Analysis Control")
        cl = QVBoxLayout()
        cl.setSpacing(12)
        cl.addWidget(ModernButton("Detect Particles", "primary"))
        cl.addWidget(ModernButton("Measure Angle", "secondary"))
        cl.addWidget(ModernButton("Autofocus", "ghost"))
        ctrl_card.add_layout(cl)
        
        right_panel.addWidget(ctrl_card)
        
        metric_card = Card("Metrics")
        ml = QVBoxLayout()
        ml.setSpacing(16)
        
        self.add_metric(ml, "Calculated Angle", "0.05°", COLORS['text'])
        self.add_metric(ml, "Particle Count", "142", COLORS['primary'])
        
        metric_card.add_layout(ml)
        right_panel.addWidget(metric_card)
        right_panel.addStretch()
        
        layout.addLayout(right_panel, 1)

    def add_metric(self, layout, label, value, color):
        container = QFrame()
        container.setStyleSheet(f"background-color: {COLORS['surface_light']}; border-radius: 12px; padding: 12px;")
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0,0,0,0)
        l = QLabel(label.upper())
        l.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10px; font-weight: 700; border: none;")
        v = QLabel(value)
        v.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: 800; border: none; font-family: {FONTS['mono']};")
        cl.addWidget(l)
        cl.addWidget(v)
        layout.addWidget(container)

class PlaceholderView(QWidget):
    def __init__(self, title):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        l = QLabel(title)
        l.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {COLORS['text_muted']};")
        layout.addWidget(l)
        sub = QLabel("Module under development")
        sub.setStyleSheet(f"color: {COLORS['text_muted']}; margin-top: 10px;")
        layout.addWidget(sub)

# --- NAVIGATION SIDEBAR ---

class SidebarButton(QPushButton):
    def __init__(self, text, icon_char="•", parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(48)
        self.icon_char = icon_char
        
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {COLORS['text_muted']};
                text-align: left;
                padding-left: 20px;
                border: none;
                border-radius: 12px;
                font-weight: 600;
                font-size: 14px;
                margin-bottom: 4px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['surface_light']};
                color: {COLORS['text']};
            }}
            QPushButton:checked {{
                background-color: {COLORS['primary']}15;
                color: {COLORS['primary']};
                border: 1px solid {COLORS['primary']}30;
            }}
        """)

# --- MAIN WINDOW ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stage-Toolbox Pro")
        self.resize(1280, 850)
        
        central = QWidget()
        self.setCentralWidget(central)
        
        # Main Layout
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 1. Sidebar
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(260)
        self.sidebar.setStyleSheet(f"background-color: {COLORS['surface']}; border-right: 1px solid {COLORS['border']};")
        
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(16, 24, 16, 24)
        
        # Brand
        brand = QLabel("StageBox")
        brand.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {COLORS['primary']}; padding-left: 12px; margin-bottom: 30px;")
        side_layout.addWidget(brand)
        
        # Nav Items
        self.stack = QStackedWidget()
        
        # Helper to add nav items
        def add_nav(text, widget):
            btn = SidebarButton(text)
            side_layout.addWidget(btn)
            self.stack.addWidget(widget)
            index = self.stack.count() - 1
            btn.clicked.connect(lambda: self.stack.setCurrentIndex(index))
            return btn
            
        self.btn_dash = add_nav("Dashboard", DashboardView())
        
        lbl_wf = QLabel("WORKFLOWS")
        lbl_wf.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px; font-weight: 800; padding-left: 12px; margin-top: 20px; margin-bottom: 10px;")
        side_layout.addWidget(lbl_wf)
        
        add_nav("Stage Control", StageControlView())
        add_nav("Autofocus", PlaceholderView("Autofocus"))
        add_nav("Gitterschieber", GitterschieberView())
        add_nav("Laserscan", PlaceholderView("Laserscan"))
        
        side_layout.addStretch()
        
        # User Profile at bottom
        user_row = QHBoxLayout()
        avatar = QLabel("MZ")
        avatar.setFixedSize(36, 36)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(f"background-color: {COLORS['primary']}; color: {COLORS['bg']}; border-radius: 18px; font-weight: bold;")
        user_label = QLabel("M. Zschach\nOperator")
        user_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {COLORS['text']}; margin-left: 8px;")
        
        user_row.addWidget(avatar)
        user_row.addWidget(user_label)
        user_row.addStretch()
        
        user_frame = QFrame()
        user_frame.setStyleSheet(f"background-color: {COLORS['surface_light']}; border-radius: 12px; padding: 8px;")
        user_frame.setLayout(user_row)
        side_layout.addWidget(user_frame)
        
        main_layout.addWidget(self.sidebar)
        
        # 2. Content Area
        content_col = QVBoxLayout()
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setSpacing(0)
        
        # Header Bar
        header = QFrame()
        header.setFixedHeight(70)
        header.setStyleSheet(f"background-color: {COLORS['bg']}; border-bottom: 1px solid {COLORS['border']};")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(30, 0, 30, 0)
        
        self.page_title = QLabel("Production Dashboard")
        self.page_title.setStyleSheet("font-size: 20px; font-weight: 700; letter-spacing: -0.5px;")
        
        search_bar = QLineEdit()
        search_bar.setPlaceholderText("Search Serial Number...")
        search_bar.setFixedWidth(280)
        search_bar.setStyleSheet(f"""
            border-radius: 20px; 
            background-color: {COLORS['surface']};
            padding-left: 16px;
        """)
        
        hl.addWidget(self.page_title)
        hl.addStretch()
        hl.addWidget(search_bar)
        
        content_col.addWidget(header)
        content_col.addWidget(self.stack)
        
        content_widget = QWidget()
        content_widget.setLayout(content_col)
        main_layout.addWidget(content_widget)

        # Init
        self.btn_dash.click()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_STYLESHEET)
    
    # Set app font
    font = QFont(FONTS['ui'])
    font.setPixelSize(14)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

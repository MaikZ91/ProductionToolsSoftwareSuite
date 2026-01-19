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
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import deque

import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
import numpy as np


import cv2

from PySide6.QtCore import (
    Qt, QTimer, QSize, QPoint, QRect, Signal, Slot, QPropertyAnimation, QEasingCurve,
    QObject, QThread, QRegularExpression, QEvent, QSortFilterProxyModel, QAbstractTableModel,
    QMetaObject
)
from PySide6.QtGui import (
    QColor, QPalette, QFont, QPainter, QPen, QBrush, QLinearGradient, QGradient, QIcon,
    QPainterPath, QPixmap, QShortcut, QKeySequence, QRegularExpressionValidator, QImage
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QGridLayout, QSlider, QSizePolicy, QScrollArea, QGraphicsDropShadowEffect, QAbstractItemView,
    QProgressBar, QMessageBox, QSpacerItem, QComboBox, QToolButton, QTableView, QDoubleSpinBox, QSpinBox,
    QDialog, QListWidget, QCheckBox, QFormLayout, QGroupBox, QDial, QInputDialog
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
from z_trieb import ZTriebController
import gitterschieber as gs
from z_trieb import ZTriebWidget
import stage_control as resolve_stage
from ie_Framework.Hardware.Camera.panda import PcoCameraBackend


# ========================== DATENBANK / INFRA ==========================

DASHBOARD_WIDGET_CLS, _DASHBOARD_IMPORT_ERROR = (None, None)

# --- THEME CONFIGURATION ---
COLORS = {
    "bg": "#000000",           # Main Background
    "surface": "#0d0d0d",      # Card Background
    "surface_light": "#1a1a1a",# Inputs / Hover
    "border": "#262626",
    "primary": "#ffffff",      # White accents
    "primary_hover": "#cccccc",
    "secondary": "#a0a0a0",    # Lighter Gray for highlights
    "text": "#ffffff",
    "text_muted": "#909090",
    "danger": "#ef4444",
    "success": "#10b981",
    "warning": "#f59e0b",
}

FONTS = {
    "ui": "Segoe UI",
    "mono": "Consolas",
}

# --- STUDIO MODE / CONFIG MANAGER ---
class ConfigManager:
    _instance = None
    CONFIG_FILE = "ui_config.json"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance.config = cls._instance._load()
        return cls._instance

    def _load(self):
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    return json.load(f)
            except: return {}
        return {}

    def save(self):
        with open(self.CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    def get(self, key, default):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()

    def delete(self, key):
        if key in self.config:
            del self.config[key]
            self.save()

UI_CONFIG = ConfigManager()
STUDIO_MODE = False 

# ========================== CAMERA REGISTRY ==========================

class GlobalCameraRegistry:
    """Central registry to map indices or names to frame providers (Singletons)."""
    def __init__(self):
        # Name, Index/ID
        self.cams = [
            ("PCO Panda", -1),
            ("IDS Camera 1 (Idx 0)", 0),
            ("IDS Camera 2 (Idx 1)", 1),
            ("IDS Camera 3 (Idx 2)", 2),
            ("IDS Camera 4 (Idx 3)", 3),
        ]
        self._instances = {} # Map idx -> camera_instance

    def get_instance(self, idx):
        if idx in self._instances:
            return self._instances[idx]
            
        instance = None
        if idx == -1: # PCO
            if PcoCameraBackend is not None:
                try:
                    instance = PcoCameraBackend()
                    instance.start()
                    print("[CameraRegistry] Started PCO Panda.")
                except Exception as e:
                    print(f"[CameraRegistry] Failed to start PCO: {e}")
            else:
                 print("[CameraRegistry] PCO Class not available.")
        else: # IDS
            # For IDS we use the autofocus helper which manages its own caching, 
            # but here we might want a direct object if needed. 
            # Actually autofocus.acquire_frame handles caching internally.
            # So we don't strictly need an instance object here for IDS if we use the helper.
            pass
            
        if instance:
            self._instances[idx] = instance
        return instance

    def get_provider(self, idx):
        """Returns a callable that returns a frame (QImage or numpy)."""
        if idx == -1: # PCO
            def pco_provider():
                cam = self.get_instance(-1)
                if cam:
                    try:
                        return cam.get_frame()
                    except: return None
                return None
            return pco_provider
        
        # IDS via autofocus helper
        def ids_provider():
            try:
                import autofocus
                return autofocus.acquire_frame(idx)
            except: return None
        return ids_provider

CAMERA_REGISTRY = GlobalCameraRegistry()
class PropertyEditor(QDialog):
    def __init__(self, target_widget, parent=None):
        super().__init__(parent)
        self.target = target_widget
        self.setWindowTitle(f"Studio Editor: {target_widget.objectName() or 'Element'}")
        self.setFixedWidth(320)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(f"background-color: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']};")
        
        layout = QFormLayout(self)
        
        # 1. Name/Text
        # 1. Text Properties
        self.le_text = QLineEdit()
        self.le_placeholder = QLineEdit()
        
        # Determine current values
        curr_text = ""
        curr_placeholder = ""
        
        # Check text
        if hasattr(target_widget, "text") and callable(target_widget.text):
            curr_text = target_widget.text() 
        elif isinstance(target_widget, Card) and hasattr(target_widget, "title_label"):
             curr_text = target_widget.title_label.text()
             
        # Check placeholder
        if hasattr(target_widget, "placeholderText") and callable(target_widget.placeholderText):
            curr_placeholder = target_widget.placeholderText()

        print(f"[Studio Debug] Editor Init: {target_widget.objectName()} | Text: '{curr_text}' | Placeholder: '{curr_placeholder}'")

        self.le_text.setText(curr_text)
        self.le_placeholder.setText(curr_placeholder)
        
        layout.addRow("Display Text:", self.le_text)
        if hasattr(target_widget, "placeholderText") or isinstance(target_widget, QLineEdit):
            layout.addRow("Placeholder:", self.le_placeholder)
        
        # 1.5 Object Name (Read Only)
        self.lbl_id = QLabel(target_widget.objectName())
        if not target_widget.objectName() or target_widget.objectName().startswith("qt_"):
             self.lbl_id.setText(f"{type(target_widget).__name__} (WARNING: NO ID)")
             self.lbl_id.setStyleSheet(f"color: {COLORS['warning']}; font-weight: bold; font-size: 11px;")
             self.lbl_id.setToolTip("This element has no permanent ID. Changes might NOT stick after restart.")
        else:
             self.lbl_id.setStyleSheet("color: #666; font-size: 11px;")
        layout.addRow("Object ID:", self.lbl_id)
        self.le_color = QLineEdit()
        self.le_color.setPlaceholderText("#RRGGBB")
        layout.addRow("Background Color:", self.le_color)
        
        # 2. Dimensions
        self.spin_w = QSpinBox(); self.spin_w.setRange(0, 2000); self.spin_w.setValue(target_widget.width())
        self.spin_h = QSpinBox(); self.spin_h.setRange(0, 2000); self.spin_h.setValue(target_widget.height())
        layout.addRow("Fixed Width:", self.spin_w)
        layout.addRow("Fixed Height:", self.spin_h)
        
        # 3. Object ID (Internal)
        # self.lbl_id = QLabel(target_widget.objectName() or "No ID (Rename Recommended)")
        # layout.addRow("Object ID:", self.lbl_id)

        btn_save = ModernButton("Apply & Save", "primary")
        btn_save.clicked.connect(self.apply_changes)
        layout.addRow(btn_save)

        # Only show Delete if it's a dynamic element (starts with Dyn_ or inside StudioToolView)
        is_dynamic = self.target.objectName().startswith("Dyn_")
        parent = self.target.parent()
        while parent:
            if isinstance(parent, StudioToolView):
                is_dynamic = True
                break
            parent = parent.parent()

        if is_dynamic:
            btn_delete = ModernButton("Delete Element", "danger")
            btn_delete.clicked.connect(self.delete_widget)
            layout.addRow(btn_delete)

    def apply_changes(self):
        obj_name = self.target.objectName()
        if not obj_name:
            obj_name = f"{type(self.target).__name__}_{id(self.target)}"
            self.target.setObjectName(obj_name)
            
        props = {}
        
        # Apply Text
        new_text = self.le_text.text()
        # Only apply if changed or empty? Apply always to be safe.
        if hasattr(self.target, "setText"):
            self.target.setText(new_text)
            props["text"] = new_text
        elif isinstance(self.target, Card) and hasattr(self.target, "title_label") and self.target.title_label:
            self.target.title_label.setText(new_text)
            props["text"] = new_text
            
        # Apply Placeholder
        if hasattr(self.target, "setPlaceholderText"):
            new_ph = self.le_placeholder.text()
            self.target.setPlaceholderText(new_ph)
            props["placeholder"] = new_ph
            
        # Apply dimensions
        if self.spin_w.value() > 0:
            self.target.setFixedWidth(self.spin_w.value())
            props["fixed_width"] = self.spin_w.value()
        if self.spin_h.value() > 0:
            self.target.setFixedHeight(self.spin_h.value())
            props["fixed_height"] = self.spin_h.value()
            
        # Apply Color
        new_color = self.le_color.text().strip()
        if new_color.startswith("#") and len(new_color) == 7:
            self.target.setStyleSheet(f"background-color: {new_color}; border: 1px solid {COLORS['border']}; border-radius: 8px;")
            props["bg_color"] = new_color
        
        self.target.update()
        if self.target.parent(): self.target.parent().update()
            
        UI_CONFIG.set(obj_name, props)
        
        # Sync with dynamic_tools if applicable
        sync_dynamic_tool_config(obj_name, props)
        self.accept()

    def delete_widget(self):
        confirm = QMessageBox.question(self, "Delete", f"Delete {self.target.objectName()}?", QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            name = self.target.objectName()
            UI_CONFIG.delete(name)
            # Find in dynamic tools if needed
            tools = UI_CONFIG.get("dynamic_tools", {})
            for tname, tcfg in tools.items():
                widgets = tcfg.get("widgets", [])
                new_widgets = [w for w in widgets if w.get("id") != name]
                if len(new_widgets) != len(widgets):
                    tcfg["widgets"] = new_widgets
                    UI_CONFIG.set("dynamic_tools", tools)
                    break
            self.target.setParent(None)
            self.target.deleteLater()
            self.accept()

class GlobalEditFilter(QObject):
    def eventFilter(self, obj, event):
        if not STUDIO_MODE:
            return False
            
        # Handle Injection logic
        if event.type() == QEvent.Enter:
             if self._is_editable(obj):
                 self._ensure_handle(obj)

        if event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.RightButton:
                target = QApplication.instance().widgetAt(event.globalPos())
                if target:
                    # Check for View background click
                    temp = target
                    while temp:
                        if isinstance(temp, StudioToolView):
                             # Only if we didn't click a child widget that is editable
                             if target == temp or target.parent() == temp:
                                self._show_view_menu(temp, event.globalPos())
                                return True
                             break
                        temp = temp.parent()
                        
                    print(f"[Studio Debug] Right-click recognized on {target.objectName() or type(target).__name__}")
                    self.open_editor(target)
                    return True 
        return False

    def _is_editable(self, obj):
        # Allow resizing of ANY widget that has a stable ID, 
        # but exclude top-level windows to avoid confusion.
        if not isinstance(obj, QWidget): return False
        if isinstance(obj, (QMainWindow, QDialog)): return False
        if not obj.objectName(): return False
        # filter out internal Qt widgets starting with qt_
        if obj.objectName().startswith("qt_"): return False
        
        # Exclude SidebarButtons from resizing handles so navigation always works
        # We check by class name string to avoid import issues
        if obj.__class__.__name__ == "SidebarButton": return False

        return True

    def _ensure_handle(self, obj):
        # Check if already has handle
        for child in obj.children():
            if isinstance(child, StudioResizeHandle):
                child.raise_()
                child.show()
                return
        
        # Create handle
        h = StudioResizeHandle(obj)
        h.show()

    def _show_view_menu(self, view, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        menu.setStyleSheet(f"background-color: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']};")
        act_add = menu.addAction("Add Component...")
        act_del = menu.addAction("Delete Tool")
        
        action = menu.exec(pos)
        if action == act_add:
            dialog = AddComponentDialog(view.tool_name, view)
            if dialog.exec(): view._on_add_success()
        elif action == act_del:
            self.delete_tool(view)

    def delete_tool(self, view):
        confirm = QMessageBox.question(None, "Delete", f"Delete tool '{view.tool_name}'?", QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            tools = UI_CONFIG.get("dynamic_tools", {})
            if view.tool_name in tools:
                del tools[view.tool_name]
                UI_CONFIG.set("dynamic_tools", tools)
                # We can't easily remove from Sidebar without complex logic, so request restart
                QMessageBox.information(None, "Success", "Tool deleted. Please restart.")

    def open_editor(self, obj):
        target = obj
        # Climb up to find something useful to edit (Card, Button, etc.)
        while target:
            if isinstance(target, (Card, ModernButton, QPushButton, QLabel, QLineEdit)): break
            if isinstance(target, QMainWindow): break
            target = target.parent()
        if not target: return
        
        # Check if target has a valid ObjectName
        if not target.objectName() or target.objectName().startswith("qt_"):
             # It's an internal or unnamed widget. 
             # We can't save it reliably.
             # We try to warn the user or auto-name it if it's a specific type.
             print(f"[Studio WARN] Editing widget without stable ID: {target}")
             # We could Auto-name it based on parent chain, but that's complex to get unique.
             # For now, we just proceed, but maybe add a visual warning in the editor?
             pass 

        print(f"[Studio] Editing: {target.objectName()} | Type: {type(target).__name__}")
        editor = PropertyEditor(target, QApplication.activeWindow())
        # Ensure it stays on top and is modal
        editor.setWindowModality(Qt.ApplicationModal)
        editor.exec()

def apply_saved_ui(widget):
    """Checks UI_CONFIG for saved properties for this widget's objectName."""
    name = widget.objectName()
    if not name: return
    
    config = UI_CONFIG.get(name, None)
    if not config: return
    
    if "text" in config:
        if hasattr(widget, "setText"):
            widget.setText(config["text"])
        elif isinstance(widget, Card) and hasattr(widget, "title_label") and widget.title_label:
            widget.title_label.setText(config["text"])
            
    if "placeholder" in config and hasattr(widget, "setPlaceholderText"):
        widget.setPlaceholderText(config["placeholder"])
            
    if "fixed_width" in config:
        widget.setFixedWidth(config["fixed_width"])
    if "fixed_height" in config:
        widget.setFixedHeight(config["fixed_height"])
    if "bg_color" in config:
        widget.setStyleSheet(f"background-color: {config['bg_color']}; border: 1px solid {COLORS['border']}; border-radius: 8px;")

def apply_saved_ui_recursive(parent):
    # Apply to self first
    apply_saved_ui(parent)
    
    # Iterate all children recursively using findChildren
    # We use QWidget to get everything
    all_widgets = parent.findChildren(QWidget)
    for w in all_widgets:
        apply_saved_ui(w)

def hex_to_rgba(hex_color, alpha):
    """Converts #RRGGBB to rgba(r, g, b, alpha) for reliable Qt styling."""
    hex_color = hex_color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) for i in range(0, 6, 2))
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {alpha})"

# Global Stylesheet for things hard to style in Python code
GLOBAL_STYLESHEET = f"""
QMainWindow {{ background-color: {COLORS['bg']}; }}
QWidget {{ 
    background-color: {COLORS['bg']}; 
    color: {COLORS['text']}; 
    font-family: "{FONTS['ui']}";
    font-size: 13px;
}}

/* SCROLLBARS */
QScrollBar:vertical {{
    border: none;
    background: #050505;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: #444444;
    min-height: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: #666666; }}
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
QPushButton {{
    background-color: {COLORS['surface_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 8px 16px;
    color: {COLORS['text']};
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {COLORS['surface']};
    border-color: {COLORS['text_muted']};
}}
QPushButton:pressed {{
    background-color: {COLORS['border']};
}}

QLineEdit, QTextEdit {{
    background-color: {COLORS['surface_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 10px 12px;
    color: {COLORS['text']};
    selection-background-color: {COLORS['primary']};
    selection-color: {COLORS['bg']};
    font-size: 13px;
}}
QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {COLORS['text_muted']};
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

/* PROGRESS BAR */
QProgressBar {{
    background-color: {COLORS['surface_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    text-align: center;
    color: {COLORS['text']};
}}
QProgressBar::chunk {{
    background-color: {COLORS['primary']};
    border-radius: 2px;
}}

/* SPIN BOX */
QSpinBox, QDoubleSpinBox {{
    background-color: {COLORS['surface_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 5px;
    color: {COLORS['text']};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {COLORS['text_muted']};
}}
"""

# --- CUSTOM COMPONENTS ---

class ModernButton(QPushButton):
    """
    A button that mimics the Tailwind variants: Primary, Secondary, Ghost, Danger.
    """
    def __init__(self, text="", variant="primary", icon=None, parent=None):
        super().__init__(text, parent)
        self.setObjectName(f"Btn_{text.replace(' ', '_')}")
        self.set_variant(variant)
        apply_saved_ui(self)

    def set_variant(self, variant):
        base_style = """
            QPushButton {
                border-radius: 6px;
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
                QPushButton:hover {{ border: 1px solid {COLORS['text_muted']}; background-color: {COLORS['border']}; }}
            """)
        elif variant == "ghost":
            self.setStyleSheet(base_style + f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLORS['text_muted']};
                    border: 1px solid transparent;
                }}
                QPushButton:hover {{ background-color: {COLORS['surface_light']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; }}
            """)
        elif variant == "danger":
            self.setStyleSheet(base_style + f"""
                QPushButton {{
                    background-color: rgba(239, 68, 68, 0.1);
                    color: {COLORS['danger']};
                    border: 1px solid rgba(239, 68, 68, 0.2);
                }}
                QPushButton:hover {{ background-color: rgba(239, 68, 68, 0.2); }}
            """)


class Card(QFrame):
    """Rounded, bordered card component."""
    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        self.title_label = None
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
            }}
        """)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        if title:
            header_frame = QFrame()
            header_frame.setStyleSheet("background-color: transparent; border: none; border-bottom: 1px solid " + COLORS['border'] + ";")
            header_layout = QHBoxLayout(header_frame)
            header_layout.setContentsMargins(16, 12, 16, 12)
            header_layout.setSpacing(0)

            self.title_label = QLabel(title)
            self.title_label.setObjectName(f"Title_{title.replace(' ', '_')}")
            self.title_label.setStyleSheet("border: none; font-size: 13px; font-weight: 700; letter-spacing: 0.3px;")
            header_layout.addWidget(self.title_label)
            self.main_layout.addWidget(header_frame)
            apply_saved_ui(self.title_label)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("border: none;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(16, 12, 16, 12)
        self.content_layout.setSpacing(12)
        self.main_layout.addWidget(self.content_widget)
        
        if title:
            self.setObjectName(f"Card_{title.replace(' ', '_')}")
            apply_saved_ui(self)

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
    # Signal for background data update
    data_updated = Signal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        
        self._is_fetching = False
        self.data_updated.connect(self._on_data_received)

        # Main layout for the entire view (Horizontal split)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # --- LEFT SIDE: CONTROLS ---
        left_side = QVBoxLayout()
        left_side.setSpacing(15)

        # 0. Selection & Refresh
        controls_card = Card("Data Source")
        cl = QVBoxLayout()
        cl.setSpacing(8)
        self.combo_testtype = QComboBox()
        self.combo_testtype.addItems(["kleberoboter", "gitterschieber_tool", "stage_test"])
        self.combo_testtype.currentIndexChanged.connect(self.trigger_refresh)
        self.combo_testtype.setFixedHeight(36)
        cl.addWidget(self.combo_testtype)
        self.status_indicator = QPushButton("● LIVE")
        self.status_indicator.setCursor(Qt.PointingHandCursor)
        self.status_indicator.clicked.connect(self.trigger_refresh)
        self.status_indicator.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {COLORS['success']}; font-weight: 800; font-size: 11px; text-align: left; padding-left: 5px; }}")
        cl.addWidget(self.status_indicator)
        cl.addWidget(self.status_indicator)
        
        # IPC Shortcut Button
        self.btn_goto_ipc = ModernButton("In Process Control (IPC)", "secondary")
        self.btn_goto_ipc.setObjectName("Dash_Btn_IPC")
        cl.addWidget(self.btn_goto_ipc)
        
        controls_card.add_layout(cl)
        left_side.addWidget(controls_card)

        # 1. New Record (Moved UP to ensure visibility)
        self.setup_entry_ui_compact(left_side)

        # 2. Key Metrics
        kpi_card = Card("Key Metrics")
        kl = QVBoxLayout()
        kl.setSpacing(10)
        self.kpi_total = self.add_kpi_compact(kl, "Total", "0", COLORS['primary'])
        self.kpi_pass = self.add_kpi_compact(kl, "Pass", "0%", COLORS['secondary'])
        self.kpi_last = self.add_kpi_compact(kl, "Latest", "---", COLORS['success'])
        kpi_card.add_layout(kl)
        left_side.addWidget(kpi_card)
        
        left_side.addStretch()
        main_layout.addLayout(left_side, 1)

        # --- RIGHT SIDE: ACTIVITY TABLE ---
        activity_card = Card("Recent Activity")
        al = QVBoxLayout()
        al.setContentsMargins(0, 0, 0, 0)
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Time", "Barcode", "User", "Status", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Style the header
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        
        self.table.setObjectName("Dash_Table")
        
        activity_card.add_widget(self.table)
        main_layout.addWidget(activity_card, 2)

        # 4. Live Update Timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_data)
        self.timer.start(10000) # Update every 10 seconds
        
        self.update_data()

    def setup_entry_ui_compact(self, layout):
        entry_card = Card("New Record")
        entry_layout = QVBoxLayout()
        entry_layout.setSpacing(8)
        
        self.le_barcode = QLineEdit()
        self.le_barcode.setPlaceholderText("Barcode...")
        self.le_barcode.setObjectName("Dash_Entry_Barcode")
        self.le_user = QLineEdit()
        self.le_user.setPlaceholderText("User...")
        self.le_user.setObjectName("Dash_Entry_User")
        
        row1 = QHBoxLayout()
        row1.addWidget(self.le_barcode)
        row1.addWidget(self.le_user)
        entry_layout.addLayout(row1)
        
        self.entry_stack = QStackedWidget()
        # Same widgets as before but more compact if needed
        w_kleber = QWidget()
        l_kleber = QHBoxLayout(w_kleber); l_kleber.setContentsMargins(0,0,0,0)
        self.cb_ok = QCheckBox("Test OK?"); self.cb_ok.setStyleSheet("font-weight: bold; color: " + COLORS['primary'] + ";")
        l_kleber.addWidget(self.cb_ok); l_kleber.addStretch()
        
        w_git = QWidget()
        l_git = QHBoxLayout(w_git); l_git.setContentsMargins(0,0,0,0); l_git.setSpacing(5)
        self.le_particles = QLineEdit(); self.le_particles.setPlaceholderText("P-Count")
        self.le_angle = QLineEdit(); self.le_angle.setPlaceholderText("Angle")
        l_git.addWidget(self.le_particles); l_git.addWidget(self.le_angle)
        
        w_stage = QWidget()
        l_stage = QHBoxLayout(w_stage); l_stage.setContentsMargins(0,0,0,0); l_stage.setSpacing(5)
        self.le_pos_name = QLineEdit(); self.le_pos_name.setPlaceholderText("Pos")
        self.le_fov = QLineEdit(); self.le_fov.setPlaceholderText("FOV")
        l_stage.addWidget(self.le_pos_name); l_stage.addWidget(self.le_fov)
        
        self.entry_stack.addWidget(w_kleber); self.entry_stack.addWidget(w_git); self.entry_stack.addWidget(w_stage)
        entry_layout.addWidget(self.entry_stack)
        
        btn_row = QHBoxLayout()
        self.btn_send = ModernButton("Send", "primary")
        self.btn_send.clicked.connect(self.send_current_entry)
        btn_row.addWidget(self.btn_send)
        entry_layout.addLayout(btn_row)
        
        entry_card.add_layout(entry_layout)
        layout.addWidget(entry_card)

    def add_kpi_compact(self, layout, title, value, color):
        container = QFrame()
        container.setStyleSheet(f"background-color: {COLORS['surface_light']}; border-radius: 8px; padding: 10px;")
        l = QHBoxLayout(container)
        l.setContentsMargins(10, 5, 10, 5)
        
        t_lbl = QLabel(title.upper())
        t_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-weight: 700; font-size: 10px; border:none;")
        
        v_lbl = QLabel(value)
        v_lbl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 18px; border:none;")
        
        l.addWidget(t_lbl)
        l.addStretch()
        l.addWidget(v_lbl)
        
        layout.addWidget(container)
        container.value_label = v_lbl
        return container

    def trigger_refresh(self):
        """Manually restart the timer and fetch data."""
        if not self.timer.isActive():
            self.timer.start(10000)
        self.update_data()

    def update_data(self):
        """Starts a background thread to fetch data."""
        if self._is_fetching:
            return
            
        testtype = self.combo_testtype.currentText()
        self._is_fetching = True
        
        # UI Feedback
        self.status_indicator.setText("● FETCHING...")
        self.status_indicator.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {COLORS['text_muted']}; font-weight: 800; font-size: 11px; margin-left: 10px; }}")

        def task():
            try:
                df, connected = db.fetch_test_data(testtype, limit=20)
                self.data_updated.emit(df, connected)
            except Exception as e:
                print(f"Background Fetch Error: {e}")
                self.data_updated.emit(pd.DataFrame(), False)
        
        threading.Thread(target=task, daemon=True).start()

    def _on_data_received(self, df, connected):
        """Processes the background result on the main thread."""
        self._is_fetching = False
        testtype = self.combo_testtype.currentText()
        
        # Update Connection Status UI
        if not connected:
            self.status_indicator.setText("● OFFLINE (Click to Retry)")
            self.status_indicator.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {COLORS['danger']}; font-weight: 800; font-size: 11px; margin-left: 10px; }}")
            
            # Stop automatic retries as requested
            self.timer.stop()
            
            # Update KPIs to fallback state
            self.kpi_total.value_label.setText("---")
            self.kpi_pass.value_label.setText("---")
            self.kpi_last.value_label.setText("N/A")
            
            # Show connection error in table
            self.table.setRowCount(1)
            item = QTableWidgetItem("Datenbankverbindung nicht verfügbar")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(QBrush(QColor(COLORS['danger'])))
            item.setFont(QFont(FONTS['ui'], 11, QFont.Bold))
            self.table.setItem(0, 0, item)
            for col in range(1, 5):
                self.table.setItem(0, col, QTableWidgetItem(""))
            self.table.setSpan(0, 0, 1, 5) # Span across all columns
            return
        else:
            self.status_indicator.setText("● LIVE")
            self.status_indicator.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {COLORS['success']}; font-weight: 800; font-size: 11px; text-align: left; padding-left: 5px; }}")
            # Clear potential spans from error state
            self.table.clearSpans()

        # Update KPIs
        total = len(df)
        ok_ratio = 0
        last_result = "N/A"
        
        if total > 0:
            if "ok" in df.columns:
                ok_bool = df["ok"].fillna(False).astype(bool)
                ok_count = ok_bool.sum()
                ok_ratio = int((ok_count / total) * 100)
                last_result = "OK" if bool(ok_bool.iloc[0]) else "FAIL"

        self.kpi_total.value_label.setText(str(total))
        self.kpi_pass.value_label.setText(f"{ok_ratio}%")
        self.kpi_last.value_label.setText(last_result)
        
        # Color based on status
        color = COLORS['text']
        if last_result == "FAIL": color = COLORS['danger']
        elif last_result == "OK": color = COLORS['success']
        self.kpi_last.value_label.setStyleSheet(f"color: {color}; font-weight: 800; font-size: 18px; border:none;")

        # Update Table
        self.table.setRowCount(len(df))
        for i, row in df.iterrows():
            # Time
            ts = row.get("StartTest", "---")
            ts_str = ts.strftime("%H:%M:%S") if pd.notna(ts) else "---"
            self.table.setItem(i, 0, QTableWidgetItem(ts_str))
            
            # Barcode
            barcode = str(row.get("barcodenummer", "---"))
            self.table.setItem(i, 1, QTableWidgetItem(barcode))
            
            # User
            user = str(row.get("user", "---"))
            self.table.setItem(i, 2, QTableWidgetItem(user))
            
            # Status
            status_val = "OK"
            if "ok" in row and not pd.isna(row["ok"]):
                status_val = "OK" if bool(row["ok"]) else "FAIL"
            
            badge = StatusBadge(status_val, "success" if status_val == "OK" else "danger")
            # We wrap it for alignment if needed, but for simplicity:
            self.table.setCellWidget(i, 3, badge)
            
            # Details
            details = ""
            if testtype == "gitterschieber_tool":
                details = f"P:{row.get('particle_count', '?')} A:{row.get('justage_angle', '?')}°"
            elif testtype == "stage_test":
                details = f"Pos:{row.get('position', '?')}"
            else:
                details = "---"
                
            det_item = QTableWidgetItem(details)
            det_item.setForeground(QBrush(QColor(COLORS['text_muted'])))
            det_item.setFont(QFont(FONTS['mono'], 9))
            self.table.setItem(i, 4, det_item)
            
        # Also sync entry stack index
        self.entry_stack.setCurrentIndex(self.combo_testtype.currentIndex())

    def clear_entry_fields(self):
        self.le_barcode.clear()
        self.le_user.clear()
        self.cb_ok.setChecked(False)
        self.le_particles.clear()
        self.le_angle.clear()
        self.le_pos_name.clear()
        self.le_fov.clear()

    def send_current_entry(self):
        testtype = self.combo_testtype.currentText()
        barcode_str = self.le_barcode.text().strip() or "0"
        user_str = self.le_user.text().strip() or "unknown"
        
        payload = {}
        if testtype == "kleberoboter":
            payload["ok"] = self.cb_ok.isChecked()
        elif testtype == "gitterschieber_tool":
            payload["particle_count"] = self._safe_int(self.le_particles.text())
            payload["justage_angle"] = self._safe_float(self.le_angle.text())
        elif testtype == "stage_test":
            payload["position"] = self.le_pos_name.text().strip()
            payload["field_of_view"] = self._safe_float(self.le_fov.text())
            
        try:
            barcode_obj = miltenyiBarcode.mBarcode(barcode_str)
            conn = dbConnector.connection()
            conn.connect()
            
            now = datetime.datetime.now()
            conn.sendData(
                now, now, 0,
                testtype, payload,
                barcode_obj, user_str
            )
            conn.disconnect()
            
            # Show success and refresh
            self.status_indicator.setText("● SENT SUCCESS")
            QTimer.singleShot(2000, lambda: self.status_indicator.setText("● LIVE"))
            self.update_data()
            self.clear_entry_fields()
            
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to send data:\n{e}")

    def _safe_int(self, txt):
        try: return int(float(str(txt).replace(",", ".")))
        except: return 0

    def _safe_float(self, txt):
        try: return float(str(txt).replace(",", "."))
        except: return 0.0

    def add_kpi(self, layout, title, value, sub, accent_color):
        card = Card()
        # Custom KPI layout without standard header
        container = QWidget()
        l = QVBoxLayout(container)
        l.setSpacing(2)
        l.setContentsMargins(10, 8, 10, 8)
        
        # Border Left Accent
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                border-left: 3px solid {accent_color};
            }}
        """)

        t_lbl = QLabel(title.upper())
        t_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-weight: 700; font-size: 10px; letter-spacing: 0.5px; border:none;")
        
        v_lbl = QLabel(value)
        v_lbl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 22px; border:none;")
        
        s_lbl = QLabel(sub)
        s_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10px; border:none;")

        l.addWidget(t_lbl)
        l.addWidget(v_lbl)
        l.addWidget(s_lbl)
        
        card.main_layout.addWidget(container)
        layout.addWidget(card)
        container.value_label = v_lbl # Reference for updates
        return container

def frame_to_qpixmap(frame, target_size=None) -> QPixmap:
    """Konvertiert ein BGR/Gray-Frame in QPixmap, skaliert auf target_size falls gegeben."""
    if frame is None: return QPixmap()
    try:
        if frame.ndim == 2:
            h, w = frame.shape
            qimg = QImage(frame.data, w, h, w, QImage.Format_Grayscale8)
        elif frame.ndim == 3:
            h, w, ch = frame.shape
            # BGR zu RGB für QImage
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        else:
            return QPixmap()
        
        pm = QPixmap.fromImage(qimg)
        if target_size:
            return pm.scaled(target_size[0], target_size[1], Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pm
    except Exception as e:
        print(f"Error converting frame: {e}")
        return QPixmap()

class LiveCamEmbed(QWidget):
    """Live camera view widget for BGR/Mono frames."""
    def __init__(self, frame_provider, *, interval_ms: int = 150, start_immediately: bool = True, parent=None):
        super().__init__(parent)
        self._frame_provider = frame_provider
        self._last_frame = None
        self._interval_ms = interval_ms
        self._autostart = bool(start_immediately)
        
        self.label = QLabel("Kein Bild")
        self.label.setAlignment(Qt.AlignCenter)
        # self.label.setFixedHeight(280) # Removed fixed height to allow resizing
        self.label.setMinimumHeight(150) # Set a reasonable minimum
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setStyleSheet(f"background-color: #050505; border-radius: 8px; border: 1px solid {COLORS['border']};")
        
        self.status = QLabel("Warte auf Frame...")
        self.status.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.status.setMaximumHeight(18)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.status, 0)
        
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if not self._timer.isActive():
            self._timer.start(self._interval_ms)

    def stop(self):
        self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self._autostart: self.start()

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

    def _tick(self):
        try:
            result = self._frame_provider()
            status_text = None
            if isinstance(result, tuple) and len(result) == 2:
                frame, status_text = result
            else:
                frame = result
            if frame is not None:
                self._last_frame = frame
                if isinstance(frame, QImage):
                    pm = QPixmap.fromImage(frame)
                    pm = pm.scaled(self.label.width(), self.label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                else:
                    pm = frame_to_qpixmap(frame, (self.label.width(), self.label.height()))
                self.label.setPixmap(pm)
                if status_text:
                    self.status.setText(status_text)
                else:
                    self.status.setText(f"LIVE | {datetime.datetime.now().strftime('%H:%M:%S')}")
            else:
                self.status.setText("Warte auf Kameradaten...")
        except Exception as exc:
            self.status.setText(f"Kamera-Fehler: {exc}")

def add_camera_monitor(layout, frame_provider, title="Monitor", stretch=1):
    """
    Creates a complete camera monitoring card and adds it to the layout.
    """
    card = Card(title)
    cam = LiveCamEmbed(frame_provider)
    card.add_widget(cam)
    layout.addWidget(card, stretch)
    return cam

class CameraWindow(QWidget):
    """Standalone window for camera monitoring."""
    def __init__(self, frame_provider, title="Camera Feed", size=(800, 600)):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(*size)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.cam = add_camera_monitor(layout, frame_provider, title=None)
        
    def closeEvent(self, event):
        self.cam.stop()
        super().closeEvent(event)

def open_camera_window(frame_provider, title="Camera Feed"):
    """Helper to open a standalone camera window."""
    win = CameraWindow(frame_provider, title)
    win.show()
    return win


class AutofocusView(QWidget):
    """Modern UI for Autofocus / Kollimator Tool."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        self._current_dev_idx = 0
        self._detector = LaserSpotDetector()
        self._laser = None
        self._last_center = None
        self._last_frame_size = None
        self._pending_cam_idx = None
        self._switch_timer = QTimer(self)
        self._switch_timer.setSingleShot(True)
        self._switch_timer.timeout.connect(self._apply_pending_camera)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # --- TOP: LIVE MONITOR (STABLE HEIGHT) ---
        # Card without title to save vertical space
        monitor_card = Card() 
        monitor_card.setStyleSheet(monitor_card.styleSheet() + "border-color: #333;")
        monitor_card.setFixedHeight(360) # Standard height for camera cards (Reduced from 410)
        self.cam_embed = LiveCamEmbed(lambda: None, start_immediately=False)
        monitor_card.add_widget(self.cam_embed)
        layout.addWidget(monitor_card)
        
        # --- BOTTOM: CONTROLS (HORIZONTAL & COMPACT) ---
        controls_row = QHBoxLayout()
        controls_row.setSpacing(15)

        # 1. Camera Selection - Compact Grid
        cam_card = Card("SYSTEM")
        cam_layout = QGridLayout()
        cam_layout.setSpacing(6)
        
        cams = CAMERA_REGISTRY.cams # Use the global registry
        self._cams = list(cams)
        try:
            print(f"[INFO] Kamera-Buttons (Name -> Index): {self._cams}")
        except Exception:
            pass
        self.btn_group = []
        for i, (text, idx) in enumerate(cams):
            btn = ModernButton(text, "secondary")
            btn.setMinimumHeight(34)
            btn.setFont(QFont(FONTS['ui'], 11, QFont.Bold))
            btn.clicked.connect(lambda _, id=idx: self._select_camera(id))
            cam_layout.addWidget(btn, i // 2, i % 2)
            self.btn_group.append(btn)
        
        cam_card.add_layout(cam_layout)
        controls_row.addWidget(cam_card, 2)

        # 2. Exposure Control - Very Narrow
        expo_card = Card("EXPOSURE")
        sl = QVBoxLayout()
        sl.setSpacing(8)
        
        header = QHBoxLayout()
        self.spin_expo = QDoubleSpinBox()
        self.spin_expo.setRange(0.01, 500.0)
        self.spin_expo.setValue(20.0)
        self.spin_expo.setSuffix(" ms")
        self.spin_expo.setFixedHeight(30)
        self.spin_expo.setStyleSheet(f"background: {COLORS['surface_light']}; border-radius: 4px; padding: 2px;")
        header.addWidget(self.spin_expo)
        sl.addLayout(header)
        
        self.slider_expo = QSlider(Qt.Horizontal)
        self.slider_expo.setRange(1, 5000)
        self.slider_expo.setValue(200)
        sl.addWidget(self.slider_expo)
        
        self.spin_expo.valueChanged.connect(lambda v: self.slider_expo.setValue(int(v * 10)))
        self.slider_expo.valueChanged.connect(lambda v: self.spin_expo.setValue(v / 10.0))
        self.spin_expo.valueChanged.connect(self._set_exposure)
        
        expo_card.add_layout(sl)
        controls_row.addWidget(expo_card, 1)

        # 3. Alignment Panel
        align_card = Card("ALIGNMENT")
        al = QVBoxLayout()
        al.setSpacing(6)

        self.btn_toggle_ref = ModernButton("Save Justage", "secondary")
        self.btn_toggle_ref.setMinimumHeight(30)
        self.btn_toggle_ref.clicked.connect(self._toggle_reference)
        al.addWidget(self.btn_toggle_ref)

        self.lbl_ref = QLabel("Ref: —")
        self.lbl_dx = QLabel("dx: —")
        self.lbl_dy = QLabel("dy: —")
        self.lbl_dist = QLabel("dist: —")
        self.lbl_align_status = QLabel("Status: —")
        for lbl in (self.lbl_ref, self.lbl_dx, self.lbl_dy, self.lbl_dist, self.lbl_align_status):
            lbl.setStyleSheet(
                f"background: {COLORS['surface_light']}; border: 1px solid {COLORS['border']}; "
                "border-radius: 6px; padding: 4px 6px; font-size: 11px;"
            )
            al.addWidget(lbl)

        align_card.add_layout(al)
        controls_row.addWidget(align_card, 1)

        layout.addLayout(controls_row)
        layout.addStretch() # Push everything up to keep it tight
        
        self._update_button_styles()
        # Start controller lazily when the view is shown.

    def _get_frame(self):
        try:
            return autofocus.acquire_frame(self._current_dev_idx)
        except:
            return None

    def _select_camera(self, idx):
        self._log_device_map()
        try:
            cam_name = next(name for name, cam_idx in self._cams if cam_idx == idx)
        except Exception:
            cam_name = f"Index {idx}"
        if idx == self._current_dev_idx:
            print(f"[INFO] Kamera unveraendert: {cam_name} (Index {idx})")
            return
        prev_idx = self._current_dev_idx
        try:
            autofocus.shutdown(prev_idx)
        except Exception:
            pass
        try:
            autofocus.shutdown_all()
        except Exception:
            pass
        self._current_dev_idx = idx
        print(f"[INFO] Kamera gewechselt: {cam_name} (Index {idx})")
        self._update_button_styles()
        self._pending_cam_idx = idx
        if self._switch_timer.isActive():
            self._switch_timer.stop()
        self._switch_timer.start(500)
        # Exposure neu lesen wenn möglich
        try:
            curr, min_e, max_e = autofocus.get_exposure_limits(idx)
            self.spin_expo.blockSignals(True)
            self.spin_expo.setValue(curr / 1000.0)
            self.spin_expo.blockSignals(False)
        except:
            pass

    def _apply_pending_camera(self):
        if self._pending_cam_idx is None:
            return
        idx = self._pending_cam_idx
        self._pending_cam_idx = None
        self._init_laser_controller(idx)

    def _log_device_map(self):
        try:
            from ids_peak import ids_peak as p
        except Exception as exc:
            print(f"[WARN] ids_peak nicht verfuegbar: {exc}")
            return
        try:
            p.Library.Initialize()
            dm = p.DeviceManager.Instance()
            dm.Update()
            devs = dm.Devices()
            print(f"[INFO] Gefundene IDS Geraete: {len(devs)}")
            for i, dev in enumerate(devs):
                try:
                    name = dev.DisplayName()
                except Exception:
                    name = "unknown"
                try:
                    model = dev.ModelName()
                except Exception:
                    model = "unknown"
                try:
                    serial = dev.SerialNumber()
                except Exception:
                    serial = "unknown"
                print(f"[INFO] IDS[{i}] name={name} model={model} serial={serial}")
        except Exception as exc:
            print(f"[WARN] Konnte IDS Geraete nicht lesen: {exc}")

    def _update_button_styles(self):
        for i, btn in enumerate(self.btn_group):
            if i == self._current_dev_idx:
                btn.set_variant("primary")
            else:
                btn.set_variant("secondary")

    def _set_exposure(self, val_ms):
        try:
            if self._laser is not None:
                self._laser.set_exposure_us(int(val_ms * 1000))
            else:
                autofocus.set_exposure(self._current_dev_idx, int(val_ms * 1000))
        except:
            pass

    def _init_laser_controller(self, idx: int):
        if self._laser is not None:
            try:
                self._laser.stop()
                self._laser.shutdown()
            except Exception:
                pass
        try:
            self._laser = LiveLaserController(idx, self._detector, parent=self)
            self._laser.frameReady.connect(self._on_laser_frame)
            self._laser.centerChanged.connect(self._on_laser_center)
            self._laser.start()
            try:
                self._set_exposure(self.spin_expo.value())
            except Exception:
                pass
            try:
                self.btn_toggle_ref.setText("Save Justage")
                self.lbl_ref.setText("Ref: —")
            except Exception:
                pass
        except Exception as exc:
            self._laser = None
            print(f"[WARN] Laser-Controller konnte nicht gestartet werden: {exc}")

    def showEvent(self, event):
        super().showEvent(event)
        if self._laser is None:
            self._init_laser_controller(self._current_dev_idx)

    def hideEvent(self, event):
        if self._laser is not None:
            try:
                self._laser.stop()
                self._laser.shutdown()
            except Exception:
                pass
            self._laser = None
        super().hideEvent(event)

    def _on_laser_frame(self, qimg: QImage):
        try:
            self._last_frame_size = (qimg.width(), qimg.height())
            pm = QPixmap.fromImage(qimg)
            pm = pm.scaled(self.cam_embed.label.width(), self.cam_embed.label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.cam_embed.label.setPixmap(pm)
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            if self._last_center is not None:
                cx, cy = self._last_center
                self.cam_embed.status.setText(f"LIVE | {ts} | {cx}, {cy}")
            else:
                self.cam_embed.status.setText(f"LIVE | {ts}")
        except Exception as exc:
            self.cam_embed.status.setText(f"Kamera-Fehler: {exc}")

    def _on_laser_center(self, x: int, y: int):
        self._last_center = (int(x), int(y))
        self._update_alignment()

    def _toggle_reference(self):
        if self._laser is None:
            return
        ref = self._laser.get_reference_point()
        if ref is None:
            if self._last_center is None:
                return
            cx, cy = self._last_center
            self._laser.set_reference_point(cx, cy)
            self.btn_toggle_ref.setText("Clear Justage")
        else:
            self._laser.clear_reference_point()
            self.btn_toggle_ref.setText("Save Justage")
        self._update_alignment()

    def _update_alignment(self):
        if self._last_center is None:
            return
        if not self._last_frame_size:
            return
        w, h = self._last_frame_size
        cx, cy = self._last_center
        ref = self._laser.get_reference_point() if self._laser is not None else None
        if ref is not None:
            rx, ry = ref
            dx = int(cx - rx)
            dy = int(cy - ry)
            self.lbl_ref.setText(f"Ref: {rx}, {ry}")
        else:
            dx = int(cx - w // 2)
            dy = int(cy - h // 2)
            self.lbl_ref.setText("Ref: —")
        dist = float(np.hypot(dx, dy))

        px_um = None
        if self._laser is not None:
            px_um = self._laser.get_pixel_size_um()
        if px_um:
            dx_um = dx * px_um
            dy_um = dy * px_um
            dist_um = dist * px_um
            dist_mm = dist_um / 1000.0
            self.lbl_dx.setText(f"dx: {dx:+d} px  ({dx_um:+.1f} µm)")
            self.lbl_dy.setText(f"dy: {dy:+d} px  ({dy_um:+.1f} µm)")
            self.lbl_dist.setText(f"dist: {dist:.2f} px  ({dist_um:.1f} µm · {dist_mm:.3f} mm)")
        else:
            self.lbl_dx.setText(f"dx: {dx:+d} px")
            self.lbl_dy.setText(f"dy: {dy:+d} px")
            self.lbl_dist.setText(f"dist: {dist:.2f} px")

        tol_px = 5.0
        ok = (dist <= tol_px)
        color = "#2ecc71" if ok else "#ff2740"
        text = "OK" if ok else "ALIGN"
        self.lbl_align_status.setText(f"Status: {text} (≤ {tol_px:.1f} px)")
        self.lbl_align_status.setStyleSheet(
            f"background: {COLORS['surface_light']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 4px 6px; font-size: 11px; color: {color};"
        )

class ZTriebVisualizer(QWidget):
    """Circular gauge for the Z-Trieb motor position."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self._current_pos = 0
        self._max_steps = 2200
        self._markers = {
            0: "HOME",
            400: "1MM",
            2040: "0.17MM"
        }

    def set_position(self, pos):
        self._current_pos = pos
        self.update()

    def _pos_to_angle(self, pos):
        # 0..2200 steps -> -225..45 degrees
        ratio = max(0, min(1, pos / self._max_steps))
        return -225 + (ratio * 270)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        rect = self.rect().adjusted(15, 15, -15, -15)
        size = min(rect.width(), rect.height())
        center = rect.center()
        
        # Colors from main theme
        bg_track = QColor(COLORS['surface_light'])
        accent = QColor(COLORS['primary'])
        text_muted = QColor(COLORS['text_muted'])
        
        # 1. Background Arc
        painter.setPen(QPen(bg_track, 12, Qt.SolidLine, Qt.RoundCap))
        # startAngle and spanAngle are in 1/16th of a degree
        painter.drawArc(center.x() - size//2, center.y() - size//2, size, size, -225*16, 270*16)
        
        # 2. Markers
        font = QFont(FONTS['ui'], 8, QFont.Bold)
        painter.setFont(font)
        for val, label in self._markers.items():
            angle = self._pos_to_angle(val)
            rad = np.radians(-angle) # Invert for screen coordinates
            
            p_inner = center + QPoint(int((size/2-15) * np.cos(rad)), int((size/2-15) * np.sin(rad)))
            p_outer = center + QPoint(int((size/2-2) * np.cos(rad)), int((size/2-2) * np.sin(rad)))
            
            painter.setPen(QPen(QColor(COLORS['border']), 2))
            painter.drawLine(p_inner, p_outer)
            
            painter.setPen(text_muted)
            t_pos = center + QPoint(int((size/2-35) * np.cos(rad)), int((size/2-35) * np.sin(rad)))
            painter.drawText(t_pos.x()-25, t_pos.y()-10, 50, 20, Qt.AlignCenter, label)

        # 3. Needle
        angle = self._pos_to_angle(self._current_pos)
        rad = np.radians(-angle)
        
        painter.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap))
        needle_end = center + QPoint(int((size/2-10)*np.cos(rad)), int((size/2-10)*np.sin(rad)))
        painter.drawLine(center, needle_end)
        
        # 4. Center Cap
        painter.setBrush(bg_track)
        painter.setPen(QPen(accent, 2))
        painter.drawEllipse(center, 6, 6)
        
        # 5. Value
        painter.setPen(QColor(COLORS['text']))
        painter.setFont(QFont(FONTS['mono'], 14, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignBottom | Qt.AlignHCenter, f"{int(self._current_pos)} STEPS")

class ZTriebView(QWidget):
    """Modern UI for controlling the Objektivringversteller (Z-Trieb)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        self.controller = ZTriebController()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._dauer_future = None
        self._stop_event = None
        
        self.setup_ui()
        
        # Connect signals
        self.controller.logMessage.connect(self._append_log)
        self.controller.runCounterChanged.connect(self._update_counter)
        self.controller.positionChanged.connect(self.visualizer.set_position)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Main horizontal split: Controls (Left) and Visualizer (Right)
        main_h_layout = QHBoxLayout()
        main_h_layout.setSpacing(15)

        # --- LEFT COLUMN: CONTROLS ---
        left_col = QVBoxLayout()
        left_col.setSpacing(15)

        # 1. Drive & Move Card (Consolidated)
        control_card = Card("Drive & Position Control")
        control_card.setObjectName("ZTrieb_Card_Control")
        cl = QVBoxLayout()
        cl.setSpacing(10)

        # Reference Button at top
        self.btn_ref = ModernButton("Reference Run", "primary")
        self.btn_ref.setObjectName("ZTrieb_Btn_Ref")
        self.btn_ref.clicked.connect(lambda: self._submit(self.controller.goto_ref))
        cl.addWidget(self.btn_ref)

        # Preset positions grid (more compact)
        btn_grid = QGridLayout()
        btn_grid.setSpacing(8)
        self.btn_home = ModernButton("Home", "secondary")
        self.btn_home.clicked.connect(lambda: self._submit(self.controller.goto_home))
        self.btn_1mm = ModernButton("1 mm", "secondary")
        self.btn_1mm.clicked.connect(lambda: self._submit(self.controller.goto_1mm))
        self.btn_high = ModernButton("0.17 mm", "secondary")
        self.btn_high.clicked.connect(lambda: self._submit(self.controller.goto_highres))
        
        btn_grid.addWidget(self.btn_home, 0, 0)
        btn_grid.addWidget(self.btn_1mm, 0, 1)
        btn_grid.addWidget(self.btn_high, 0, 2)
        cl.addLayout(btn_grid)

        # Custom Move area (compact)
        move_h = QHBoxLayout()
        self.spin_pos = QSpinBox()
        self.spin_pos.setRange(-100000, 100000)
        self.spin_pos.setValue(1000)
        self.spin_pos.setFixedWidth(120)
        move_h.addWidget(self.spin_pos)
        
        self.btn_move = ModernButton("Go to Custom Pos", "secondary")
        self.btn_move.clicked.connect(lambda: self._submit(self.controller.goto_pos, self.spin_pos.value()))
        move_h.addWidget(self.btn_move)
        cl.addLayout(move_h)

        control_card.add_layout(cl)
        left_col.addWidget(control_card)

        # 2. Endurance Card (Compact)
        dauer_card = Card("Endurance Test")
        dl = QHBoxLayout()
        dl.setSpacing(15)

        self.btn_dauer = ModernButton("Start Sequence", "primary")
        self.btn_dauer.clicked.connect(self._toggle_dauertest)
        dl.addWidget(self.btn_dauer, 1)

        counter_layout = QVBoxLayout()
        self.lbl_runs = QLabel("0")
        self.lbl_runs.setAlignment(Qt.AlignCenter)
        self.lbl_runs.setStyleSheet(f"font-size: 28px; font-weight: 800; color: {COLORS['secondary']}; border: none;")
        counter_layout.addWidget(self.lbl_runs)
        
        lbl_desc = QLabel("RUNS")
        lbl_desc.setAlignment(Qt.AlignCenter)
        lbl_desc.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {COLORS['text_muted']}; border: none;")
        counter_layout.addWidget(lbl_desc)
        dl.addLayout(counter_layout)

        dauer_card.add_layout(dl)
        left_col.addWidget(dauer_card)
        left_col.addStretch()

        main_h_layout.addLayout(left_col, 2)

        # --- RIGHT COLUMN: VISUALIZER ---
        viz_card = Card("Visual Position")
        vl = QVBoxLayout()
        self.visualizer = ZTriebVisualizer()
        vl.addWidget(self.visualizer, alignment=Qt.AlignCenter)
        viz_card.add_layout(vl)
        
        main_h_layout.addWidget(viz_card, 3)

        layout.addLayout(main_h_layout)

        # 2. Log Card
        log_card = Card("Controller Logs")
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(f"""
            QTextEdit {{
                background-color: {hex_to_rgba(COLORS['surface_light'], 0.2)};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                font-family: {FONTS['mono']};
                font-size: 12px;
                color: {COLORS['text_muted']};
                padding: 10px;
            }}
        """)
        log_card.add_widget(self.log_view)
        layout.addWidget(log_card, 1)

    def _submit(self, fn, *args):
        self.executor.submit(lambda: fn(*args))

    def _append_log(self, text):
        self.log_view.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {text}")
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _update_counter(self, count):
        self.lbl_runs.setText(str(count))

    def _toggle_dauertest(self):
        if self._dauer_future and not self._dauer_future.done():
            if self._stop_event:
                self._stop_event.set()
            self.btn_dauer.setText("Start Test Sequence")
            # We don't easily have a way to reset to primary style from here without re-setting the whole string
            # but usually it's fine as the button is recreated or we manage the style via classes
        else:
            self._stop_event = threading.Event()
            self._dauer_future = self.executor.submit(self.controller.run_dauertest, self._stop_event)
            self.btn_dauer.setText("Stop Test Sequence")

    def closeEvent(self, event):
        if self._stop_event:
            self._stop_event.set()
        self.executor.shutdown(wait=False)
        self.controller.shutdown()
        super().closeEvent(event)



class StageControlView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        self.sc = resolve_stage.StageController()
        self.running = False
        self.dauer_running = False
        
        # Test state
        self._batch = "NoBatch"
        self._run_outdir = None
        self._last_outdir = None
        self._duration_sec = 15 * 3600 # Default 15h
        self._calib_vals = {"X": "---", "Y": "---"}
        self._meas_max_um = None
        self._dur_max_um = None
        self.MEAS_MAX_UM = 10.0
        
        # Threading/Workers
        self.thr = None
        self.wrk = None
        self.dauer_thr = None
        self.dauer_wrk = None
        self.executor = ThreadPoolExecutor(max_workers=3)

        self.setup_ui()
        
        # Real-time data storage
        self.x_data = deque(maxlen=300)
        self.y1_data = deque(maxlen=300)
        self.y2_data = deque(maxlen=300)
        self.tick = 0

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Left Column
        left_col = QVBoxLayout()
        left_col.setSpacing(15)
        
        setup_card = Card("Test-Setup")
        
        form_layout = QVBoxLayout()
        form_layout.setSpacing(15)
        
        self.inputs = {}
        self.inputs["operator"] = self.add_input(form_layout, "Bediener", "M. Zschach")
        self.inputs["batch"] = self.add_input(form_layout, "Chargennummer", "B2025-10-30-01")
        
        lbl_note = QLabel("NOTIZEN")
        lbl_note.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLORS['text_muted']}; border: none;")
        form_layout.addWidget(lbl_note)
        
        self.inputs["notes"] = QTextEdit()
        self.inputs["notes"].setPlaceholderText("Kommentare hier eingeben...")
        self.inputs["notes"].setFixedHeight(70)
        form_layout.addWidget(self.inputs["notes"])
        
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(10)
        
        self.btn_start = ModernButton("Kalibriermessung starten", "primary")
        self.btn_start.clicked.connect(self.toggle_precision_test)
        btn_layout.addWidget(self.btn_start)
        
        self.btn_dauer = ModernButton("Dauertest starten", "secondary")
        self.btn_dauer.clicked.connect(self.toggle_endurance_test)
        btn_layout.addWidget(self.btn_dauer)
        
        row_btn = QHBoxLayout()
        self.btn_open = ModernButton("Ordner öffnen", "ghost")
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_open.setEnabled(False)
        row_btn.addWidget(self.btn_open)
        
        row_btn.addWidget(ModernButton("DB Sync", "ghost"))
        btn_layout.addLayout(row_btn)
        
        # Progress Area
        self.progress_container = QWidget()
        pl = QVBoxLayout(self.progress_container)
        pl.setContentsMargins(0, 10, 0, 0)
        
        self.lbl_phase = QLabel("BEREIT")
        self.lbl_phase.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {COLORS['primary']}; border: none;")
        pl.addWidget(self.lbl_phase)
        
        self.progress = QProgressBar()
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {COLORS['surface_light']};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {COLORS['primary']};
                border-radius: 4px;
            }}
        """)
        pl.addWidget(self.progress)
        
        btn_layout.addWidget(self.progress_container)
        
        form_layout.addLayout(btn_layout)
        setup_card.add_layout(form_layout)
        
        left_col.addWidget(setup_card)
        
        # Status Card
        status_card = Card("QA-Status")
        sl = QVBoxLayout()
        
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Kalibrierung (X/Y)"))
        self.lbl_calib = QLabel("--- / ---")
        self.lbl_calib.setStyleSheet(f"font-family: {FONTS['mono']}; font-weight: bold; color: {COLORS['text']};")
        row1.addWidget(self.lbl_calib)
        sl.addLayout(row1)
        
        # QA Box
        self.qa_box = QFrame()
        self.qa_box.setStyleSheet(f"""
            background-color: {hex_to_rgba(COLORS['success'], 0.1)};
            border: 1px solid {hex_to_rgba(COLORS['success'], 0.3)};
            border-radius: 12px;
        """)
        qa_layout = QHBoxLayout(self.qa_box)
        
        qa_info = QVBoxLayout()
        qa_lbl = QLabel("Dauertest QA")
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
        chart_card = Card("Live-Positionsfehler")
        
        # Header inside chart
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Echtzeit-Abweichung (X vs Y)"))
        top_row.addStretch()
        self.timer_lbl = QLabel("15:00:00")
        self.timer_lbl.setStyleSheet(f"font-family: {FONTS['mono']}; font-size: 20px; font-weight: bold; color: {COLORS['primary']};")
        top_row.addWidget(self.timer_lbl)
        chart_card.add_layout(top_row)
        
        self.chart = ModernChart(height=5)
        self.line_x, = self.chart.ax.plot([], [], color=COLORS['primary'], linewidth=2, label="Fehler X")
        self.line_y, = self.chart.ax.plot([], [], color=COLORS['secondary'], linewidth=2, label="Fehler Y")
        
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
        return inp

    def _acquire_metadata(self):
        self._batch = resolve_stage.sanitize_batch(self.inputs["batch"].text()) or "NoBatch"

    def _ensure_run_dir(self):
        if self._run_outdir is None:
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._run_outdir = resolve_stage.DATA_ROOT / self._batch / f"Run_{ts}"
            self._run_outdir.mkdir(parents=True, exist_ok=True)
            self._last_outdir = self._run_outdir
            self.btn_open.setEnabled(True)
        return self._run_outdir

    def _open_folder(self):
        path = self._last_outdir if self._last_outdir else pathlib.Path(".")
        try:
            if sys.platform == 'win32':
                os.startfile(str(path.resolve()))
            else:
                subprocess.run(['xdg-open', str(path.resolve())])
        except Exception as e:
            QMessageBox.warning(self, "Ordner öffnen", f"Konnte Ordner nicht öffnen:\n{e}")

    # --- Precision Test ---
    def toggle_precision_test(self):
        if self.running:
            if self.wrk: self.wrk.stop() # Wait, TestWorker doesn't have stop? Checking...
            self.btn_start.setText("Kalibriermessung starten")
            self.btn_start.set_variant("primary")
        else:
            self._start_precision_test()

    def _start_precision_test(self):
        if self.dauer_running:
            QMessageBox.warning(self, "Test läuft", "Der Dauertest läuft bereits.")
            return
        self._acquire_metadata()
        out_dir = self._ensure_run_dir()
        
        self.running = True
        self.btn_start.setText("Test stoppen")
        self.btn_start.set_variant("danger")
        
        self.thr = QThread()
        self.wrk = resolve_stage.TestWorker(self.sc, batch=self._batch)
        self.wrk.moveToThread(self.thr)
        
        self.thr.started.connect(self.wrk.run)
        self.wrk.new_phase.connect(self._on_phase)
        self.wrk.step.connect(self._on_step)
        self.wrk.calib.connect(self._on_calib)
        self.wrk.done.connect(self._on_precision_done)
        self.wrk.error.connect(self._on_error)
        
        self.wrk.done.connect(self.thr.quit)
        self.wrk.error.connect(self.thr.quit)
        self.thr.finished.connect(self._on_thr_finished)
        
        self.thr.start()

    def _on_phase(self, name, maxi):
        self.lbl_phase.setText(name.upper())
        self.progress.setMaximum(maxi)
        self.progress.setValue(0)

    def _on_step(self, val):
        self.progress.setValue(val)

    def _on_calib(self, d):
        x = d.get("X_stepsPerMeter", "---")
        y = d.get("Y_stepsPerMeter", "---")
        self._calib_vals["X"] = x
        self._calib_vals["Y"] = y
        self.lbl_calib.setText(f"{x} / {y}")

    def _on_precision_done(self, data):
        self.running = False
        self.btn_start.setText("Kalibriermessung starten")
        self.btn_start.set_variant("primary")
        self.lbl_phase.setText("BEENDET")
        
        out_dir = data["out"]
        plots = data["plots"]
        batch = data.get("batch", "NoBatch")
        
        fig_paths = []
        max_abs_um = 0.0
        for ax, mot, enc, calc, spm, epm in plots:
            diff_um = np.abs((enc - calc) / epm * 1e6)
            max_abs_um = max(max_abs_um, float(np.max(diff_um)))
            png = self._plot_and_save(ax, mot, enc, calc, spm, epm, out_dir, batch)
            fig_paths.append(str(png))
        self._meas_max_um = max_abs_um
        
        # Check calib images
        cal_x = out_dir / f"calib_x_{batch}.png"
        cal_y = out_dir / f"calib_y_{batch}.png"
        if cal_x.exists(): fig_paths.insert(0, str(cal_x))
        if cal_y.exists(): fig_paths.insert(1, str(cal_y))
        
        report_path = out_dir / f"report_{batch}.pdf"
        try:
            self._write_report_pdf(report_path, fig_paths)
        except Exception as e:
            print(f"Report Error: {e}")

        meas_ok = (self._meas_max_um <= self.MEAS_MAX_UM)
        msg = f"Kalibriermessung beendet.\nMax. Abweichung: {self._meas_max_um:.2f} µm\nLimit: {self.MEAS_MAX_UM:.1f} µm -> {'OK' if meas_ok else 'FEHLER'}"
        QMessageBox.information(self, "Test Beendet", msg)

    # --- Endurance Test ---
    def toggle_endurance_test(self):
        if self.dauer_running:
            self._stop_endurance_test()
        else:
            self._start_endurance_test()

    def _start_endurance_test(self):
        if self.running:
            QMessageBox.warning(self, "Test läuft", "Die Kalibriermessung läuft bereits.")
            return
        self._acquire_metadata()
        out_dir = self._ensure_run_dir()
        
        try:
            x_center, y_center, _ = resolve_stage.get_current_pos()
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte aktuelle Position nicht abrufen: {e}")
            return
            
        self.dauer_running = True
        self.btn_dauer.setText("Dauertest stoppen")
        self.btn_dauer.set_variant("danger")
        
        self.x_data.clear()
        self.y1_data.clear()
        self.y2_data.clear()
        
        self.dauer_thr = QThread()
        avail_x = max(0, self.sc.high_lim.get("X", 0) - self.sc.low_lim.get("X", 0))
        avail_y = max(0, self.sc.high_lim.get("Y", 0) - self.sc.low_lim.get("Y", 0))
        avail_range = max(1, min(avail_x, avail_y))
        small_step = max(500, int(avail_range * 0.01))
        small_radius = max(2000, int(avail_range * 0.05))

        self.dauer_wrk = resolve_stage.CombinedTestWorker(
            self.sc,
            batch=self._batch,
            out_dir=out_dir,
            center_x=x_center,
            center_y=y_center,
            small_step=small_step,
            small_radius=small_radius,
            stop_at_ts=time.time() + self._duration_sec
        )
        self.dauer_wrk.moveToThread(self.dauer_thr)
        
        self.dauer_thr.started.connect(self.dauer_wrk.run)
        self.dauer_wrk.update.connect(self._on_endurance_update)
        self.dauer_wrk.finished.connect(self._on_endurance_finished)
        self.dauer_wrk.error.connect(self._on_error)
        
        self.dauer_wrk.finished.connect(self.dauer_thr.quit)
        self.dauer_wrk.error.connect(self.dauer_thr.quit)
        self.dauer_thr.finished.connect(self._on_thr_finished)
        
        self.dauer_thr.start()

    def _on_endurance_update(self, data):
        self.tick += 1
        err_x = data.get("err_x_um", 0.0)
        err_y = data.get("err_y_um", 0.0)
        max_err = data.get("max_abs_um", 0.0)
        
        self.x_data.append(self.tick)
        self.y1_data.append(err_x)
        self.y2_data.append(err_y)
        
        self.line_x.set_data(list(self.x_data), list(self.y1_data))
        self.line_y.set_data(list(self.x_data), list(self.y2_data))
        
        self.chart.ax.relim()
        self.chart.ax.autoscale_view()
        self.chart.draw_idle()
        
        self.qa_val.setText(f"{max_err:.2f} µm")
        limit = data.get("limit_um", resolve_stage.DUR_MAX_UM)
        if max_err > limit:
             self.qa_box.setStyleSheet(f"background-color: {COLORS['danger']}15; border: 1px solid {COLORS['danger']}40; border-radius: 12px;")
             self.qa_val.setStyleSheet(f"color: {COLORS['danger']}; font-weight: 800; font-size: 18px; border:none; background:transparent;")
        else:
             self.qa_box.setStyleSheet(f"background-color: {COLORS['success']}15; border: 1px solid {COLORS['success']}40; border-radius: 12px;")
             self.qa_val.setStyleSheet(f"color: {COLORS['success']}; font-weight: 800; font-size: 18px; border:none; background:transparent;")
             
        elapsed = time.time() - (self.dauer_wrk.stop_at_ts - self._duration_sec)
        remaining = max(0, self._duration_sec - elapsed)
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        s = int(remaining % 60)
        self.timer_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")
        
        self.lbl_phase.setText(data.get("phase", "DAUERTEST").upper())
        self.progress.setMaximum(self._duration_sec)
        self.progress.setValue(int(elapsed))

    def _stop_endurance_test(self):
        if self.dauer_wrk:
            self.dauer_wrk.stop()
        self.dauer_running = False
        self.btn_dauer.setText("Start Endurance Test")
        self.btn_dauer.set_variant("secondary")

    def _on_endurance_finished(self, data):
        self.dauer_running = False
        self.btn_dauer.setText("Dauertest starten")
        self.btn_dauer.set_variant("secondary")
        self.lbl_phase.setText("BEENDET")
        
        outdir = self._ensure_run_dir()
        batch = self._batch
        
        # Save live plot
        try:
            out_png = outdir / f"dauertest_{batch}.png"
            self.chart.figure.savefig(out_png, dpi=110)
        except Exception as e:
            print(f"Save Plot Error: {e}")

        self._dur_max_um = float(data.get("dur_max_um", 0.0))
        limit = float(data.get("limit_um", resolve_stage.DUR_MAX_UM))
        
        # Update report
        try:
            images = []
            for name in [f"calib_x_{batch}.png", f"calib_y_{batch}.png",
                         f"X_{batch}.png", f"Y_{batch}.png",
                         f"dauertest_{batch}.png"]:
                f = outdir / name
                if f.exists(): images.append(str(f))
            report_path = outdir / f"report_{batch}.pdf"
            self._write_report_pdf(report_path, images)
        except Exception as e:
            print(f"Report Update Error: {e}")

        dur_ok = (self._dur_max_um <= limit)
        msg = f"Endurance test completed.\nMax deviation: {self._dur_max_um:.2f} µm\nLimit: {limit:.1f} µm -> {'OK' if dur_ok else 'FAIL'}"
        QMessageBox.information(self, "Endurance Done", msg)

    def _on_thr_finished(self):
        self.running = False
        self.dauer_running = False
        self.btn_start.setText("Start Precision Test")
        self.btn_start.set_variant("primary")
        self.btn_dauer.setText("Start Endurance Test")
        self.btn_dauer.set_variant("secondary")

    def _plot_and_save(self, axis, mot, enc, calc, spm, epm, out_dir: pathlib.Path, batch: str) -> pathlib.Path:
        diff, idx = enc - calc, np.linspace(0, 1, len(mot))
        fig = Figure(figsize=(12, 8), dpi=110, facecolor=COLORS['surface'])
        
        def style_ax(ax):
            ax.set_facecolor(COLORS['surface'])
            ax.tick_params(colors=COLORS['text'], labelsize=9)
            ax.xaxis.label.set_color(COLORS['text_muted'])
            ax.yaxis.label.set_color(COLORS['text_muted'])
            ax.title.set_color(COLORS['text'])
            ax.grid(True, color=COLORS['border'], linestyle='--', alpha=0.3)
            for spine in ax.spines.values():
                spine.set_color(COLORS['border'])

        ax1 = fig.add_subplot(221); style_ax(ax1); ax1.plot(idx, mot, color=COLORS['primary']); ax1.set_title(f"Motorschritte · {axis}")
        ax2 = fig.add_subplot(222); style_ax(ax2); ax2.scatter(mot, diff, c=idx, cmap="gray"); ax2.set_title(f"Encoder-Delta · {axis}")
        ax3 = fig.add_subplot(223); style_ax(ax3); ax3.plot(diff / epm * 1e6, color=COLORS['primary']); ax3.set_title("Delta (µm) vs Index")
        ax4 = fig.add_subplot(224); style_ax(ax4); ax4.scatter(mot / spm * 1e3, diff / epm * 1e6, c=idx, cmap="gray"); ax4.set_title("Delta (µm) vs Weg")
        
        fig.suptitle(f"{axis}-Achse – Messung · Charge: {batch}", color=COLORS['text'], fontweight="semibold")
        fig.tight_layout()
        out_png = out_dir / f"{axis}_{batch}.png"
        fig.savefig(out_png)
        return out_png

    def _write_report_pdf(self, pdf_path: pathlib.Path, image_paths):
        with PdfPages(pdf_path) as pdf:
            fig = Figure(figsize=(11.69, 8.27), dpi=110, facecolor=COLORS['surface'])
            ax = fig.add_subplot(111); ax.axis("off")
            
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            op = self.inputs["operator"].text() or "---"
            notes = self.inputs["notes"].toPlainText() or "---"
            xspm = self._calib_vals.get("X", "---")
            yspm = self._calib_vals.get("Y", "---")
            
            meas_result = "---"
            if self._meas_max_um is not None:
                meas_result = "OK" if self._meas_max_um <= self.MEAS_MAX_UM else "FAIL"
                
            dur_result = "---"
            if self._dur_max_um is not None:
                dur_result = "OK" if self._dur_max_um <= resolve_stage.DUR_MAX_UM else "FAIL"

            text = (
                f"Stage Test Report\n\n"
                f"Zeitpunkt: {now}\n"
                f"Charge: {self._batch}\n"
                f"Operator: {op}\n\n"
                f"Kalibrierung (stepsPerMeter):\n"
                f"  X: {xspm}\n  Y: {yspm}\n\n"
                f"Bemerkungen:\n{notes}\n\n"
                f"QA-Grenzen:\n"
                f"  Messung: ≤ {self.MEAS_MAX_UM:.1f} µm | Ergebnis: {self._meas_max_um if self._meas_max_um is not None else 0.0:.2f} µm -> {meas_result}\n"
                f"  Dauertest: ≤ {resolve_stage.DUR_MAX_UM:.1f} µm | Ergebnis: {self._dur_max_um if self._dur_max_um is not None else 0.0:.2f} µm -> {dur_result}\n"
            )
            ax.text(0.05, 0.95, text, va="top", ha="left", fontsize=12, color=COLORS['text'], family='monospace')
            pdf.savefig(fig)
            self._images_to_pdf(image_paths, pdf)

    def _images_to_pdf(self, image_paths, pdf: PdfPages):
        for img_path in image_paths:
            if not os.path.exists(img_path): continue
            try:
                img = mpimg.imread(img_path)
                fig = Figure(figsize=(11.69, 8.27), dpi=110, facecolor=COLORS['surface'])
                ax = fig.add_subplot(111); ax.imshow(img); ax.axis("off")
                pdf.savefig(fig)
            except Exception as e:
                print(f"Error adding image to PDF: {e}")

    def _on_error(self, msg):
        QMessageBox.critical(self, "Error", msg)
        self.running = False
        self.dauer_running = False
        self.btn_start.setText("Start Precision Test")
        self.btn_start.set_variant("primary")
        self.btn_dauer.setText("Start Endurance Test")
        self.btn_dauer.set_variant("secondary")

class GitterschieberView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=2)
        self._overlay_active = False
        self._last_frame = None
        self._last_overlay = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignTop)
        
        # Visualizer Card (Left)
        # Use a container widget to hold everything and prevent jumping
        self.cam_embed = add_camera_monitor(layout, self._get_frame, title="Live Feed")
        
        # Fix dimensions for absolute stability
        camera_card = self.cam_embed.parent().parent()
        camera_card.setFixedHeight(360) # 280 image + header + buffers
        camera_card.setFixedWidth(580)
        
        # Controls (Right)
        right_panel = QVBoxLayout()
        right_panel.setSpacing(24)
        
        ctrl_card = Card("Analysis Control")
        cl = QVBoxLayout()
        cl.setSpacing(12)
        self.btn_detect = ModernButton("Detect Particles", "primary")
        self.btn_detect.clicked.connect(self._detect_particles)
        cl.addWidget(self.btn_detect)

        self.btn_angle = ModernButton("Measure Angle", "secondary")
        self.btn_angle.clicked.connect(self._measure_angle)
        cl.addWidget(self.btn_angle)

        self.btn_af = ModernButton("Autofocus", "ghost")
        self.btn_af.clicked.connect(self._run_autofocus)
        cl.addWidget(self.btn_af)
        
        ctrl_card.add_layout(cl)
        right_panel.addWidget(ctrl_card)
        
        metric_card = Card("Metrics")
        ml = QVBoxLayout()
        ml.setSpacing(16)
        
        self.metric_angle = self.add_metric(ml, "Calculated Angle", "---", COLORS['text'])
        self.metric_particles = self.add_metric(ml, "Particle Count", "---", COLORS['primary'])
        
        metric_card.add_layout(ml)
        right_panel.addWidget(metric_card)
        right_panel.addStretch()
        
        layout.addLayout(right_panel, 1)

    def _get_frame(self):
        if self._overlay_active and self._last_overlay is not None:
            return self._last_overlay
        frame = gs.capture_frame()
        self._last_frame = frame
        return frame

    def _detect_particles(self):
        self.btn_detect.setEnabled(False)
        self.btn_detect.setText("Analyzing...")
        
        def task():
            try:
                # Capture fresh frame if live is off, or use last
                frame = gs.capture_frame()
                if frame is None: return
                
                results = gs.process_image(frame)
                count = results["count"]
                overlay = results["overlay"]
                
                # Update UI
                def update_ui():
                    self.metric_particles.value_label.setText(str(count))
                    self._last_overlay = overlay
                    self._overlay_active = True
                    # Reset overlay after 4 seconds
                    QTimer.singleShot(4000, self._clear_overlay)
                    self.btn_detect.setEnabled(True)
                    self.btn_detect.setText("Detect Particles")

                QMetaObject.invokeMethod(self, update_ui)
            except Exception as e:
                print(f"Detection Error: {e}")
                QMetaObject.invokeMethod(self, lambda: self.btn_detect.setEnabled(True))

        self.executor.submit(task)

    def _clear_overlay(self):
        self._overlay_active = False
        self._last_overlay = None

    def _measure_angle(self):
        self.btn_angle.setEnabled(False)
        self.btn_angle.setText("Measuring...")
        
        def task():
            try:
                angle = gs.MeasureSingleImageGratingAngle()
                def update_ui():
                    self.metric_angle.value_label.setText(f"{angle:.3f}°")
                    self.btn_angle.setEnabled(True)
                    self.btn_angle.setText("Measure Angle")
                QMetaObject.invokeMethod(self, update_ui)
            except Exception as e:
                print(f"Angle Error: {e}")
                QMetaObject.invokeMethod(self, lambda: self.btn_angle.setEnabled(True))

        self.executor.submit(task)

    def _run_autofocus(self):
        self.btn_af.setEnabled(False)
        self.btn_af.setText("Focusing...")
        
        def task():
            try:
                gs.autofocus()
                QMetaObject.invokeMethod(self, lambda: (self.btn_af.setEnabled(True), self.btn_af.setText("Autofocus")))
            except Exception as e:
                print(f"AF Error: {e}")
                QMetaObject.invokeMethod(self, lambda: self.btn_af.setEnabled(True))

        self.executor.submit(task)

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
        container.value_label = v
        return container


class OptikkoerperView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        self._cam = None
        self._last_error = None
        self._expo_initialized = False
        self._updating_expo = False
        self.cam_embed = None
        self.spin_expo = None
        self.slider_expo = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        monitor_card = Card()
        monitor_card.setStyleSheet(monitor_card.styleSheet() + "border-color: #333;")
        monitor_card.setFixedHeight(360)
        self.cam_embed = LiveCamEmbed(self._get_frame, interval_ms=100, start_immediately=True)
        monitor_card.add_widget(self.cam_embed)
        layout.addWidget(monitor_card)

        expo_card = Card("EXPOSURE")
        sl = QVBoxLayout()
        sl.setSpacing(8)
        self.spin_expo = QDoubleSpinBox()
        self.spin_expo.setRange(0.01, 500.0)
        self.spin_expo.setValue(20.0)
        self.spin_expo.setSuffix(" ms")
        self.spin_expo.setFixedHeight(30)
        self.spin_expo.setStyleSheet(f"background: {COLORS['surface_light']}; border-radius: 4px; padding: 2px;")
        sl.addWidget(self.spin_expo)

        self.slider_expo = QSlider(Qt.Horizontal)
        self.slider_expo.setRange(1, 5000)
        self.slider_expo.setValue(200)
        sl.addWidget(self.slider_expo)

        self.spin_expo.valueChanged.connect(lambda v: self.slider_expo.setValue(int(v * 10)))
        self.slider_expo.valueChanged.connect(lambda v: self.spin_expo.setValue(v / 10.0))
        self.spin_expo.valueChanged.connect(self._set_exposure)

        expo_card.add_layout(sl)
        layout.addWidget(expo_card)
        layout.addStretch()

    def _ensure_cam(self):
        if self._cam is None:
            self._cam = IdsCam(index=0, set_min_exposure=False)
        try:
            self._last_error = None
            if not self._expo_initialized:
                self._init_exposure_controls()
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def _get_frame(self):
        if not self._ensure_cam():
            return None
        try:
            frame = self._cam.aquise_frame()
        except Exception as exc:
            self._last_error = str(exc)
            return None
        if frame is None:
            return None
        if frame.dtype != np.uint8:
            if frame.dtype == np.uint16:
                frame = (frame >> 8).astype(np.uint8)
            else:
                max_val = float(frame.max()) if frame.size else 0.0
                if max_val > 0:
                    frame = (frame.astype(np.float32) / max_val * 255.0).astype(np.uint8)
                else:
                    frame = np.zeros_like(frame, dtype=np.uint8)
        return frame

    def _init_exposure_controls(self):
        if self._cam is None or self.spin_expo is None or self.slider_expo is None:
            return
        try:
            curr_us, min_us, max_us = self._cam.get_exposure_limits_us()
        except Exception:
            return

        min_ms = max(0.01, float(min_us) / 1000.0)
        max_ms = max(min_ms + 0.01, float(max_us) / 1000.0)
        max_ms = min(max_ms, 10000.0)
        self._updating_expo = True
        self.spin_expo.setRange(min_ms, max_ms)
        self.slider_expo.setRange(int(min_ms * 10), int(max_ms * 10))
        curr_ms = max(min_ms, min(max_ms, float(curr_us) / 1000.0))
        self.spin_expo.setValue(curr_ms)
        self.slider_expo.setValue(int(curr_ms * 10))
        self._updating_expo = False
        self._expo_initialized = True

    def _set_exposure(self, val_ms):
        if self._updating_expo:
            return
        if self._cam is None:
            return
        try:
            self._cam.set_exposure_us(float(val_ms) * 1000.0)
        except Exception as exc:
            self._last_error = str(exc)
            if self.cam_embed is not None:
                self.cam_embed.status.setText(f"EXPOSURE-Fehler: {exc}")

    def showEvent(self, event):
        super().showEvent(event)
        if self.cam_embed is not None:
            self.cam_embed.start()

    def hideEvent(self, event):
        if self.cam_embed is not None:
            self.cam_embed.stop()
        if self._cam is not None:
            self._cam.shutdown()
            self._cam = None
        self._expo_initialized = False
        super().hideEvent(event)


class PlaceholderView(QWidget):
    def __init__(self, title):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        l = QLabel(title)
        l.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {COLORS['text_muted']};")
        layout.addWidget(l)
        sub = QLabel("Module under development")
        sub.setStyleSheet(f"color: {COLORS['text_muted']}; margin-top: 10px;")
        layout.addWidget(sub)


        self.chk_edit.toggled.connect(self.toggle_edit_mode)
        cl.addWidget(self.chk_edit)

class IPCView(QWidget):
    """Graphical In Process Control View (connected to DB)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20,20,20,20)
        
        # Top Controls
        top_card = Card("Process Control Settings")
        tl = QHBoxLayout()
        
        # Source Selector
        tl.addWidget(QLabel("Data Source:"))
        self.combo_source = QComboBox()
        self.combo_source.addItems(["gitterschieber_tool", "stage_test", "kleberoboter"])
        self.combo_source.currentIndexChanged.connect(self.refresh_data)
        tl.addWidget(self.combo_source)
        
        tl.addStretch()
        
        btn_refresh = ModernButton("Refresh Data", "primary")
        btn_refresh.clicked.connect(self.refresh_data)
        tl.addWidget(btn_refresh)
        
        top_card.add_layout(tl)
        layout.addWidget(top_card)
        
        # Charts
        charts_layout = QHBoxLayout()
        
        # Chart 1: Variable Correlation (Scatter)
        c1 = Card("Correlation Analysis")
        self.chart1 = ModernChart()
        # Initialize Scatter
        self.scat = self.chart1.ax.scatter([], [], alpha=0.6, color=COLORS['primary'])
        self.chart1.ax.set_title("Waiting for data...")
        c1.add_widget(self.chart1)
        charts_layout.addWidget(c1)
        
        # Chart 2: Process Stability (Trend)
        c2 = Card("Process Stability (Trend)")
        self.chart2 = ModernChart()
        self.line_trend, = self.chart2.ax.plot([], [], color=COLORS['success'], linewidth=2)
        self.chart2.ax.set_title("TimeSeries Trend")
        c2.add_widget(self.chart2)
        charts_layout.addWidget(c2)
        
        layout.addLayout(charts_layout, 1)
        
        # Auto-Refresh Timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_data)
        self.timer.start(10000) # 10s
        
        # Initial Fetch
        QTimer.singleShot(500, self.refresh_data)

    def refresh_data(self):
        source = self.combo_source.currentText()
        
        def fetch_task():
            try:
                # Fetch more data for statistics (e.g. 100)
                df, ok = db.fetch_test_data(source, limit=100)
                if ok and not df.empty:
                    return df
            except: pass
            return None
            
        # Run in thread or just quick fetch? fetch_test_data is sync but usually fast.
        # For responsiveness, lets assume it returns fast enough or do threaded if needed.
        # Given limitations, we do it in main thread to simplify plotting updates or use a helper
        # But previous Dashboard used a thread. Let's do simple safely here for now.
        df = fetch_task()
        if df is not None:
            self._update_charts(df, source)
        else:
            print("[IPC] No data or fetch error.")

    def _update_charts(self, df, source):
        # Clear
        self.chart1.ax.cla()
        self.chart2.ax.cla()
        
        # Default styling
        def style(ax, title, xl, yl):
            ax.set_title(title, color=COLORS['text'])
            ax.set_xlabel(xl, color=COLORS['text_muted'])
            ax.set_ylabel(yl, color=COLORS['text_muted'])
            ax.grid(True, linestyle='--', alpha=0.3, color=COLORS['border'])
            
        # Logic per source
        try:
            if source == "gitterschieber_tool":
                # Plot Justage Angle vs Particle Count
                if "justage_angle" in df.columns and "particle_count" in df.columns:
                    x = pd.to_numeric(df["particle_count"], errors='coerce').fillna(0)
                    y = pd.to_numeric(df["justage_angle"], errors='coerce').fillna(0)
                    
                    style(self.chart1.ax, "Angle vs Particles", "Particles", "Angle [°]")
                    self.chart1.ax.scatter(x, y, color=COLORS['primary'], alpha=0.7)
                    
                    # Trend: Angle over time (index mostly if time is not parsed perfectly)
                    # Use index as proxy for time (assuming sorted desc, so we reverse)
                    y_trend = y.iloc[::-1].values # Newest is 0, so reverse to have history->new
                    x_trend = np.arange(len(y_trend))
                    
                    style(self.chart2.ax, "Angle Trend", "Sample Index", "Angle [°]")
                    self.chart2.ax.plot(x_trend, y_trend, '-o', color=COLORS['success'], markersize=4)
                    
            elif source == "stage_test":
                # Plot X vs Y coordinates of CAM1
                if "x_coordinate_cam1" in df.columns and "y_coordinate_cam1" in df.columns:
                    x = pd.to_numeric(df["x_coordinate_cam1"], errors='coerce').fillna(0)
                    y = pd.to_numeric(df["y_coordinate_cam1"], errors='coerce').fillna(0)
                    
                    style(self.chart1.ax, "Cam1 Position scatter", "X [mm]", "Y [mm]")
                    self.chart1.ax.scatter(x, y, c=range(len(x)), cmap='viridis', label="Pos")
                    
                    # Trend: X pos
                    y_trend = x.iloc[::-1].values
                    style(self.chart2.ax, "X-Pos Drift", "Sample", "X [mm]")
                    self.chart2.ax.plot(np.arange(len(y_trend)), y_trend, color=COLORS['warning'])

            elif source == "kleberoboter":
                 # Usually just OK/Fail. Let's plot "Duration" if time available or just yield?
                 # We can calculate 'Result' as 1=OK, 0=Fail
                 if "ok" in df.columns:
                     vals = df["ok"].apply(lambda v: 1 if v else 0).values
                     vals = vals[::-1]
                     
                     style(self.chart1.ax, "Pass/Fail Distribution", "Sample", "Status")
                     self.chart1.ax.scatter(np.arange(len(vals)), vals + np.random.normal(0,0.05,len(vals)), color=COLORS['secondary'])
                     
                     # Moving Average of Yield?
                     window = 5
                     if len(vals) > window:
                         mv = pd.Series(vals).rolling(window).mean()
                         style(self.chart2.ax, f"Yield Trend (MA {window})", "Sample", "Yield Rate")
                         self.chart2.ax.plot(np.arange(len(vals)), mv, color=COLORS['primary'])
                         self.chart2.ax.set_ylim(-0.1, 1.1)

        except Exception as e:
            print(f"[IPC] Plotting error: {e}")
            
        self.chart1.draw_idle()
        self.chart2.draw_idle()

class SettingsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)
        
        title = QLabel("Application Settings")
        title.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {COLORS['primary']};")
        layout.addWidget(title)
        
        # UI Settings Card
        ui_card = Card("User Interface Customization")
        cl = QVBoxLayout()
        
        self.chk_edit = QCheckBox("Enable Studio Mode (Visual Editor)")
        self.chk_edit.setStyleSheet(f"font-size: 16px; font-weight: 600; padding: 10px; color: {COLORS['success'] if STUDIO_MODE else COLORS['text']};")
        self.chk_edit.setChecked(STUDIO_MODE)
        self.chk_edit.toggled.connect(self.toggle_edit_mode)
        cl.addWidget(self.chk_edit)
        
        info = QLabel("Tip: When Studio Mode is active, Right-Click on any Title, Button or Card to edit its properties.")
        info.setStyleSheet(f"color: {COLORS['text_muted']}; font-style: italic;")
        cl.addWidget(info)
        
        ui_card.add_layout(cl)
        layout.addWidget(ui_card)
        
        btn_reset = ModernButton("Reset UI Configuration", "danger")
        btn_reset.clicked.connect(self.reset_config)
        layout.addWidget(btn_reset)
        
        layout.addStretch()

    def toggle_edit_mode(self, checked):
        global STUDIO_MODE
        STUDIO_MODE = checked
        print(f"[Studio] Mode changed to: {STUDIO_MODE}")
        
        color = COLORS['success'] if STUDIO_MODE else COLORS['text']
        self.chk_edit.setStyleSheet(f"font-size: 16px; font-weight: 600; padding: 10px; color: {color};")
        
        if STUDIO_MODE:
            QApplication.setOverrideCursor(Qt.CrossCursor)
        else:
            QApplication.restoreOverrideCursor()
            
        # Update visibility of Studio elements
        win = self.window()
        if isinstance(win, MainWindow):
            win.update_studio_visibility()
        
        # Force a refresh of the entire UI overlay
        QApplication.activeWindow().update()

    def reset_config(self):
        if os.path.exists(UI_CONFIG.CONFIG_FILE):
            os.remove(UI_CONFIG.CONFIG_FILE)
            QMessageBox.information(self, "Success", "Configuration reset. Please restart the application.")


class LaserscanView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        self._cam = None
        self._last_error = None
        self._expo_initialized = False
        self._updating_expo = False
        self._frame_counter = 0
        self._last_signature = None
        self._static_count = 0
        self.cam_embed = None
        self.spin_expo = None
        self.slider_expo = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        card = Card("PCO Panda")
        card.setObjectName("Laserscan_Card_Cam")
        
        # Check global PcoCameraBackend
        if PcoCameraBackend is None:
            msg = "PCO Backend nicht verfuegbar"
            if _PCO_IMPORT_ERROR:
                msg = f"{msg}: {_PCO_IMPORT_ERROR}"
            lbl = QLabel(msg)
            lbl.setStyleSheet(f"color: {COLORS['text_muted']};")
            card.add_widget(lbl)
        else:
            self.cam_embed = LiveCamEmbed(self._get_frame, interval_ms=100, start_immediately=True)
            self.cam_embed.setObjectName("Laserscan_CamEmbed") # IMPORTANT FOR RESIZE
            card.add_widget(self.cam_embed)

        layout.addWidget(card)

        expo_card = Card("EXPOSURE")
        expo_card.setObjectName("Laserscan_Card_Expo")
        sl = QVBoxLayout()
        sl.setSpacing(8)
        self.spin_expo = QDoubleSpinBox()
        self.spin_expo.setRange(0.01, 500.0)
        self.spin_expo.setValue(20.0)
        self.spin_expo.setSuffix(" ms")
        self.spin_expo.setFixedHeight(30)
        self.spin_expo.setStyleSheet(f"background: {COLORS['surface_light']}; border-radius: 4px; padding: 2px;")
        sl.addWidget(self.spin_expo)

        self.slider_expo = QSlider(Qt.Horizontal)
        self.slider_expo.setRange(1, 5000)
        self.slider_expo.setValue(200)
        sl.addWidget(self.slider_expo)

        self.spin_expo.valueChanged.connect(lambda v: self.slider_expo.setValue(int(v * 10)))
        self.slider_expo.valueChanged.connect(lambda v: self.spin_expo.setValue(v / 10.0))
        self.spin_expo.valueChanged.connect(self._set_exposure)

        expo_card.add_layout(sl)
        layout.addWidget(expo_card)
        layout.addStretch()

    def _ensure_cam(self):
        if self._cam is None:
            self._cam = PcoCameraBackend()
        try:
            self._cam.start()
            self._last_error = None
            if not self._expo_initialized:
                self._init_exposure_controls()
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def _get_frame(self):
        if PcoCameraBackend is None:
            return None
        if not self._ensure_cam():
            return None
        try:
            frame = self._cam.get_frame()
        except Exception as exc:
            self._last_error = str(exc)
            return None
        if frame is None:
            return None
        if frame.dtype != np.uint8:
            if frame.dtype == np.uint16:
                frame = (frame >> 8).astype(np.uint8)
            else:
                max_val = float(frame.max()) if frame.size else 0.0
                if max_val > 0:
                    frame = (frame.astype(np.float32) / max_val * 255.0).astype(np.uint8)
                else:
                    frame = np.zeros_like(frame, dtype=np.uint8)
        self._frame_counter += 1
        rec_count = None
        if self._cam is not None:
            rec_count = self._cam.get_recorded_count()
        if frame.size:
            sample = frame[::64, ::64]
            signature = (int(sample.min()), int(sample.max()), int(sample.mean()))
        else:
            signature = (0, 0, 0)

        if signature == self._last_signature:
            self._static_count += 1
        else:
            self._static_count = 0
            self._last_signature = signature

        ts = datetime.datetime.now().strftime('%H:%M:%S')
        status = (
            f"LIVE | {ts} | min={signature[0]} max={signature[1]} mean={signature[2]} "
            f"| frame={self._frame_counter}"
        )
        if rec_count is not None:
            status += f" | rec={rec_count}"
        if self._static_count > 5:
            status += f" | STATIC x{self._static_count}"
        return frame, status

    def _init_exposure_controls(self):
        if self._cam is None or self.spin_expo is None or self.slider_expo is None:
            return
        limits = self._cam.get_exposure_limits_s()
        if limits is not None:
            min_s, max_s = limits
            min_ms = max(0.01, min_s * 1000.0)
            max_ms = max(min_ms + 0.01, max_s * 1000.0)
            max_ms = min(max_ms, 10000.0)
            self._updating_expo = True
            self.spin_expo.setRange(min_ms, max_ms)
            self.slider_expo.setRange(int(min_ms * 10), int(max_ms * 10))
            self._updating_expo = False

        curr_s = self._cam.get_exposure_s()
        if curr_s is not None:
            curr_ms = max(self.spin_expo.minimum(), min(self.spin_expo.maximum(), curr_s * 1000.0))
            self._updating_expo = True
            self.spin_expo.setValue(curr_ms)
            self.slider_expo.setValue(int(curr_ms * 10))
            self._updating_expo = False
        self._expo_initialized = True

    def _set_exposure(self, val_ms):
        if self._updating_expo:
            return
        if self._cam is None:
            return
        try:
            self._cam.set_exposure_ms(val_ms)
        except Exception as exc:
            self._last_error = str(exc)
            if self.cam_embed is not None:
                self.cam_embed.status.setText(f"EXPOSURE-Fehler: {exc}")

    def showEvent(self, event):
        super().showEvent(event)
        if self.cam_embed is not None:
            self.cam_embed.start()

    def hideEvent(self, event):
        if self.cam_embed is not None:
            self.cam_embed.stop()
        if self._cam is not None:
            self._cam.stop()
            self._cam = None
        self._expo_initialized = False
        super().hideEvent(event)

class AddComponentDialog(QDialog):
    def __init__(self, tool_name, parent=None):
        super().__init__(parent)
        self.tool_name = tool_name
        self.setWindowTitle(f"Add Component to {tool_name}")
        self.setFixedWidth(300)
        self.setStyleSheet(f"background-color: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']};")
        
        layout = QFormLayout(self)
        
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Card", "ModernButton", "QLabel", "QLineEdit", "LiveCamEmbed"])
        layout.addRow("Component Type:", self.combo_type)
        
        self.le_id = QLineEdit()
        self.le_id.setPlaceholderText("Unique ID (e.g., my_button_1)")
        layout.addRow("Object ID:", self.le_id)
        
        self.le_text = QLineEdit()
        self.le_text.setPlaceholderText("Text (for Card, Button, Label)")
        layout.addRow("Text:", self.le_text)

        self.combo_cam_idx = QComboBox()
        for name, idx in CAMERA_REGISTRY.cams:
            self.combo_cam_idx.addItem(name, idx)
        layout.addRow("Camera Source:", self.combo_cam_idx)
        
        btn_add = ModernButton("Add Component", "primary")
        btn_add.clicked.connect(self.add_component)
        layout.addRow(btn_add)

    def add_component(self):
        comp_type = self.combo_type.currentText()
        obj_id = self.le_id.text().strip()
        text = self.le_text.text().strip()
        cam_idx = self.combo_cam_idx.currentData()

        if not obj_id:
            QMessageBox.warning(self, "Error", "Object ID cannot be empty.")
            return

        tools = UI_CONFIG.get("dynamic_tools", {})
        tool_config = tools.get(self.tool_name, {"widgets": []})
        
        # Check for duplicate ID within this tool
        for widget_cfg in tool_config["widgets"]:
            if widget_cfg.get("id") == obj_id:
                QMessageBox.warning(self, "Error", f"Component with ID '{obj_id}' already exists in this tool.")
                return

        new_widget_cfg = {"type": comp_type, "id": obj_id}
        if text:
            new_widget_cfg["text"] = text
        if comp_type == "LiveCamEmbed":
            new_widget_cfg["cam_idx"] = cam_idx

        tool_config["widgets"].append(new_widget_cfg)
        tools[self.tool_name] = tool_config
        UI_CONFIG.set("dynamic_tools", tools)
        self.accept()

# --- STUDIO HELPERS ---

def sync_dynamic_tool_config(obj_id, props):
    """Updates the dynamic tool configuration for a given object ID and property set."""
    tools = UI_CONFIG.get("dynamic_tools", {})
    changed = False
    for tool_name, tool_cfg in tools.items():
        for w_cfg in tool_cfg.get("widgets", []):
            if w_cfg.get("id") == obj_id:
                for k, v in props.items():
                    if k == "fixed_width": w_cfg["fixed_width"] = v
                    elif k == "fixed_height": w_cfg["fixed_height"] = v
                    elif k == "text": w_cfg["text"] = v
                    elif k == "placeholder": w_cfg["placeholder"] = v
                    elif k == "bg_color": w_cfg["bg_color"] = v
                changed = True
    
    if changed:
        UI_CONFIG.set("dynamic_tools", tools)
        print(f"[Studio] Synced changes for {obj_id} to dynamic tool config")

class StudioResizeHandle(QWidget):
    """Non-blocking overlay: Move via Top-Left, Resize via Bottom-Right. Center is click-through."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setMouseTracking(True)
        # Cover the whole parent
        self.resize(parent.size())
        
        # Default HIDDEN until hover
        self.setVisible(False)
        # We need to make sure we receive hover events even if hidden? 
        # No, if hidden we get nothing.
        # So we need to install event filter on PARENT to show us!
        
        parent.installEventFilter(self)
        
        self._dragging = False
        self._resizing = False
        
    def _update_geom(self):
        if self.parent():
            self.resize(self.parent().size())
            self.raise_()

    def eventFilter(self, obj, event):
        if not STUDIO_MODE: return False
        
        if obj == self.parent():
            if event.type() == QEvent.Resize:
                self._update_geom()
            elif event.type() == QEvent.Enter:
                self.setVisible(True)
                self.raise_()
            elif event.type() == QEvent.Leave:
                # hide only if we are not moving into the overlay itself
                # But since overlay covers parent, we might get weird behavior.
                # Actually, when we show overlay, it might block parent Enter/Leave?
                # No, because we set overlay transparent for mouse?
                # We need overlay to accept mouse for dragging.
                pass
                
        return False
        
    def enterEvent(self, event):
        # We are Inside the overlay
        pass

    def leaveEvent(self, event):
        # We left the overlay
        if not self._dragging and not self._resizing:
            self.setVisible(False)
        
    def paintEvent(self, event):
        if not STUDIO_MODE: return
        
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        
        # 1. Dashed Border
        pen = QPen(QColor(COLORS['primary']))
        pen.setStyle(Qt.DashLine)
        pen.setWidth(2)
        qp.setPen(pen)
        qp.setBrush(Qt.NoBrush) 
        rect = self.rect().adjusted(1,1,-1,-1)
        qp.drawRect(rect)
        
        # 2. Resize Handle (Bottom Right) - Green
        qp.setBrush(QBrush(QColor(COLORS['success'])))
        qp.setPen(Qt.NoPen)
        self._resize_rect = QRect(rect.right() - 14, rect.bottom() - 14, 14, 14)
        qp.drawEllipse(self._resize_rect)
        
        # 3. Move Handle (Top Left) - Primary Color
        qp.setBrush(QBrush(QColor(COLORS['primary'])))
        self._move_rect = QRect(rect.left(), rect.top(), 16, 16)
        # Draw a small grip icon (rectangle)
        qp.drawRect(self._move_rect)
        
        # Optional: Add small text or icon inside move rect? simpler is better.
        
    def mousePressEvent(self, event):
        if not STUDIO_MODE:
            event.ignore()
            return
        
        self._start_pos = event.globalPos()
        self._p_start_size = self.parent().size()
        
        # Logic: Only capture if on handles
        if self._resize_rect.contains(event.pos()) and event.button() == Qt.LeftButton:
            self._resizing = True
            event.accept()
        elif self._move_rect.contains(event.pos()) and event.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.SizeAllCursor)
            event.accept()
        else:
            # Pass through everything else! (Clicks on the button itself)
            event.ignore()

    def mouseMoveEvent(self, event):
        if not STUDIO_MODE:
            event.ignore()
            return

        # Cursor Updates
        if self._resize_rect.contains(event.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        elif self._move_rect.contains(event.pos()):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        # Actions
        if self._resizing:
            diff = event.globalPos() - self._start_pos
            new_w = max(40, self._p_start_size.width() + diff.x())
            new_h = max(20, self._p_start_size.height() + diff.y())
            self.parent().setFixedSize(new_w, new_h)
            
        elif self._dragging:
            # Reorder Logic
            drag_dist = event.globalPos() - self._start_pos
            threshold = 50
            
            p_widget = self.parent()
            if not p_widget.parentWidget(): return
            
            layout = p_widget.parentWidget().layout()
            
            if layout and isinstance(layout, (QVBoxLayout, QHBoxLayout)):
                idx = layout.indexOf(p_widget)
                
                # Vertical Swap
                if isinstance(layout, QVBoxLayout):
                    if drag_dist.y() > threshold and idx < layout.count() - 1:
                        self._swap_widgets(layout, idx, idx + 1)
                        self._start_pos = event.globalPos()
                    elif drag_dist.y() < -threshold and idx > 0:
                        self._swap_widgets(layout, idx, idx - 1)
                        self._start_pos = event.globalPos()

                # Horizontal Swap
                elif isinstance(layout, QHBoxLayout):
                    if drag_dist.x() > threshold and idx < layout.count() - 1:
                        self._swap_widgets(layout, idx, idx + 1)
                        self._start_pos = event.globalPos()
                    elif drag_dist.x() < -threshold and idx > 0:
                        self._swap_widgets(layout, idx, idx - 1)
                        self._start_pos = event.globalPos()
                        
    def _swap_widgets(self, layout, i, j):
        item = layout.takeAt(i)
        layout.insertItem(j, item)
        
    def mouseReleaseEvent(self, event):
        if not self._dragging and not self._resizing:
            event.ignore()
            return

        self._dragging = False
        self._resizing = False
        self.setCursor(Qt.ArrowCursor)
        
        # Save Geometry
        if self.parent() and self.parent().objectName():
            props = {
                "fixed_width": self.parent().width(),
                "fixed_height": self.parent().height()
            }
            UI_CONFIG.set(self.parent().objectName(), props)
            sync_dynamic_tool_config(self.parent().objectName(), props)
            
            # Save Order
            p_view = self.parent().parentWidget()
            if isinstance(p_view, StudioToolView):
                self._save_tool_order(p_view)

    def _save_tool_order(self, view):
        # Re-read layout and update config order
        layout = view.layout()
        if not layout: return
        
        new_order_ids = []
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w and w.objectName():
                new_order_ids.append(w.objectName())
                
        # Update UI_CONFIG
        tools = UI_CONFIG.get("dynamic_tools", {})
        if view.tool_name in tools:
            current_widgets = tools[view.tool_name]["widgets"]
            w_map = {w["id"]: w for w in current_widgets if "id" in w}
            reordered = []
            for oid in new_order_ids:
                if oid in w_map: reordered.append(w_map[oid])
            for w in current_widgets:
                if w.get("id") not in new_order_ids: reordered.append(w)
                    
            tools[view.tool_name]["widgets"] = reordered
            UI_CONFIG.set("dynamic_tools", tools)
            print("[Studio] Layout order saved.")

# class StudioWrapper(QFrame): ... REMOVED in favor of universal handle injection

class StudioToolView(QWidget):
    def __init__(self, tool_name, parent=None):
        super().__init__(parent)
        self.tool_name = tool_name
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(15)

        self.add_component_button = ModernButton(f"Click to build {tool_name} (Studio Mode)", "ghost")
        self.add_component_button.clicked.connect(self._add_component)
        self.main_layout.addWidget(self.add_component_button)
        
        # Only visible in Studio Mode
        self.add_component_button.setVisible(STUDIO_MODE)
        
        self.load_components()
        self.main_layout.addStretch()

    def _on_add_success(self):
        self._reload_full()

    def _reload_full(self):
        self.clear_layout(self.main_layout)
        self.add_component_button = ModernButton(f"Click to build {self.tool_name}", "ghost")
        self.add_component_button.clicked.connect(self._add_component)
        self.main_layout.addWidget(self.add_component_button)
        self.load_components()
        self.main_layout.addStretch()
        
    def load_components(self):
        tools = UI_CONFIG.get("dynamic_tools", {})
        tool_config = tools.get(self.tool_name, {"widgets": []})
        
        for widget_cfg in tool_config["widgets"]:
            self._create_and_add_widget(widget_cfg)

    def _create_and_add_widget(self, widget_cfg):
        comp_type = widget_cfg.get("type")
        obj_id = widget_cfg.get("id")
        text = widget_cfg.get("text", "")
        cam_idx = widget_cfg.get("cam_idx", 0)

        widget = None
        if comp_type == "Card":
            widget = Card(title=text)
        elif comp_type == "ModernButton":
            widget = ModernButton(text)
        elif comp_type == "QLabel":
            widget = QLabel(text)
        elif comp_type == "QLineEdit":
            widget = QLineEdit()
            widget.setPlaceholderText(text)
        elif comp_type == "LiveCamEmbed":
            provider = CAMERA_REGISTRY.get_provider(cam_idx)
            card = Card(title=f"Camera {cam_idx}")
            cam_embed = LiveCamEmbed(provider)
            card.add_widget(cam_embed)
            widget = card
            
        if widget:
            widget.setObjectName(obj_id)
            widget.setObjectName(obj_id)
            apply_saved_ui(widget) # Apply any saved studio mode properties
            
            # Just add widget via wrapper? NO, universal handle now.
            self.main_layout.addWidget(widget)

    def _add_component(self):
        dialog = AddComponentDialog(self.tool_name, self)
        if dialog.exec() == QDialog.Accepted:
            self._on_add_success()

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            if item.layout():
                self.clear_layout(item.layout())

# --- NAVIGATION SIDEBAR ---

class SidebarButton(QPushButton):
    def __init__(self, text, icon_char="•", parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(42)
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
                background-color: {hex_to_rgba(COLORS['primary'], 0.15)};
                color: {COLORS['primary']};
                border: 1px solid {hex_to_rgba(COLORS['primary'], 0.3)};
            }}
        """)

# --- MAIN WINDOW ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Resolve Production Suite")
        self.setObjectName("MainWindow")
        
        # Dynamic sizing: Strictly tied to the available screen vertical space.
        screen_geo = QApplication.primaryScreen().availableGeometry()
        
        # Load saved size if exists
        saved = UI_CONFIG.get("MainWindow", {})
        w = saved.get("fixed_width", min(1200, int(screen_geo.width() * 0.95)))
        h = saved.get("fixed_height", int(screen_geo.height() * 0.85))
        
        self.resize(w, h)
        
        central = QWidget()
        self.setCentralWidget(central)
        
        # Main Layout
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 1. Sidebar
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(230)
        self.sidebar.setStyleSheet(f"background-color: {COLORS['surface']}; border-right: 1px solid {COLORS['border']};")
        
        self.side_layout = QVBoxLayout(self.sidebar) # Made self.side_layout for access in _filter_navigation
        self.side_layout.setContentsMargins(16, 24, 16, 24)
        
        # Brand
        brand = QLabel("Resolve Production Tool")
        brand.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {COLORS['primary']}; padding-left: 12px; margin-bottom: 15px;")
        self.side_layout.addWidget(brand)
        
        # Nav Items
        self.stack = QStackedWidget()
        
        # Helper to add nav items
        def add_nav(text, widget):
            btn = SidebarButton(text)
            # Give it a stable ID so we can save changes!
            btn.setObjectName(f"NavBtn_{text.replace(' ', '_')}")
            
            self.side_layout.addWidget(btn)
            self.stack.addWidget(widget)
            index = self.stack.count() - 1
            def on_click():
                self.stack.setCurrentIndex(index)
                self.page_title.setText(text)
            btn.clicked.connect(on_click)
            
            # Apply saved config immediately
            apply_saved_ui(btn)
            return btn
            
        # Initialize views that need cross-referencing
        self.dashboard = DashboardView()
        self.ipc_view = IPCView()

        self.btn_dash = add_nav("Dashboard", self.dashboard)
        self.btn_ipc = add_nav("In Process Control", self.ipc_view)
        
        # Connect Dashboard Button to IPC View
        self.dashboard.btn_goto_ipc.clicked.connect(lambda: self.btn_ipc.click())
        
        lbl_wf = QLabel("WORKFLOWS")
        lbl_wf.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px; font-weight: 800; padding-left: 12px; margin-top: 20px; margin-bottom: 10px;")
        self.side_layout.addWidget(lbl_wf)
        
        self.btn_ztrieb = add_nav("Z-Trieb", ZTriebView())
        self.btn_af = add_nav("Autofocus", AutofocusView())
        add_nav("Optikkorper", OptikkoerperView())
        
        add_nav("Stage Control", StageControlView())
        add_nav("Gitterschieber", GitterschieberView())
        add_nav("Laserscan", LaserscanView())
        
        # Load Dynamic Tools
        dynamic_tools = UI_CONFIG.get("dynamic_tools", {})
        if dynamic_tools:
            lbl_dyn = QLabel("CUSTOM TOOLS")
            lbl_dyn.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px; font-weight: 800; padding-left: 12px; margin-top: 20px; margin-bottom: 10px;")
            self.side_layout.addWidget(lbl_dyn)
            for tool_name in dynamic_tools:
                add_nav(tool_name, StudioToolView(tool_name))
        
        self.btn_new_tool = SidebarButton("+ Create New Tool")
        self.btn_new_tool.setStyleSheet(self.btn_new_tool.styleSheet() + f"color: {COLORS['success']}; border: 1px dashed {COLORS['success']}; margin-top: 10px;")
        self.btn_new_tool.clicked.connect(self.create_tool)
        self.side_layout.addWidget(self.btn_new_tool)
        self.btn_new_tool.hide() # Hidden by default

        self.side_layout.addStretch()
        
        # New Settings button
        self.btn_settings = add_nav("Settings", SettingsView())
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
        self.side_layout.addWidget(user_frame)
        
        main_layout.addWidget(self.sidebar)
        
        # 2. Content Area
        content_col = QVBoxLayout()
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setSpacing(0)
        
        # Header Bar
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet(f"background-color: {COLORS['bg']}; border-bottom: 1px solid {COLORS['border']};")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(30, 0, 30, 0)
        
        self.page_title = QLabel("Dashboard")
        self.page_title.setStyleSheet("font-size: 20px; font-weight: 700; letter-spacing: -0.5px;")
        
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search Serial Number...")
        self.search_bar.setFixedWidth(280)
        self.search_bar.setStyleSheet(f"""
            border-radius: 20px; 
            background-color: {COLORS['surface']};
            padding-left: 16px;
        """)
        
        hl.addWidget(self.page_title)
        hl.addStretch()
        hl.addWidget(self.search_bar)
        self.search_bar.textChanged.connect(self._filter_navigation)
        
        content_col.addWidget(header)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background-color: transparent;")
        scroll.setWidget(self.stack)
        content_col.addWidget(scroll)
        
        content_widget = QWidget()
        content_widget.setLayout(content_col)
        main_layout.addWidget(content_widget)

        # Init
        self.btn_dash.click()
        
        # Final Step: Apply all saved configurations to the entire UI
        apply_saved_ui_recursive(self)

    def _filter_navigation(self, text):
        """Simple search filter for the sidebar."""
        text = text.lower()
        for i in range(self.side_layout.count()):
            item = self.side_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, SidebarButton):
                    widget.setVisible(text in widget.text().lower())
                elif isinstance(widget, QLabel) and widget.text().isupper(): # Workflow labels
                    # Hide workflow labels if all subsequent buttons are hidden
                    all_hidden_after_label = True
                    for j in range(i + 1, self.side_layout.count()):
                        next_item = self.side_layout.itemAt(j)
                        if next_item and next_item.widget() and isinstance(next_item.widget(), SidebarButton):
                            if next_item.widget().isVisible():
                                all_hidden_after_label = False
                                break
                        elif next_item and next_item.widget() and isinstance(next_item.widget(), QLabel) and next_item.widget().text().isupper():
                            # Stop at next label
                            break
                    widget.setVisible(not all_hidden_after_label)
                else:
                    # For other widgets like brand, user profile, etc., keep them visible
                    widget.setVisible(True)


    def create_tool(self):
        name, ok = QInputDialog.getText(self, "New Custom Tool", "Enter tool name:")
        if ok and name:
            tools = UI_CONFIG.get("dynamic_tools", {})
            if name in tools:
                QMessageBox.warning(self, "Error", "Tool already exists.")
                return
            tools[name] = {"widgets": []}
            UI_CONFIG.set("dynamic_tools", tools)
            
            # Instant update: add it to sidebar without restart
            # Check if CUSTOM TOOLS label exists or add it
            has_label = False
            for i in range(self.side_layout.count()):
                w = self.side_layout.itemAt(i).widget()
                if isinstance(w, QLabel) and w.text() == "CUSTOM TOOLS":
                    has_label = True
                    break
            
            if not has_label:
                lbl_dyn = QLabel("CUSTOM TOOLS")
                lbl_dyn.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px; font-weight: 800; padding-left: 12px; margin-top: 20px; margin-bottom: 10px;")
                # Insert before the "+" button
                idx = self.side_layout.indexOf(self.btn_new_tool)
                self.side_layout.insertWidget(idx, lbl_dyn)

            # Insert new nav button before the "+" button
            idx = self.side_layout.indexOf(self.btn_new_tool)
            # Create a localized version of add_nav or call it?
            # add_nav is a local function in __init__, so we can't call it here.
            # We recreate the logic:
            btn = SidebarButton(name)
            self.side_layout.insertWidget(idx, btn)
            new_view = StudioToolView(name)
            self.stack.addWidget(new_view)
            s_idx = self.stack.count() - 1
            def on_click():
                self.stack.setCurrentIndex(s_idx)
                self.page_title.setText(name)
            btn.clicked.connect(on_click)
            
            QMessageBox.information(self, "Success", f"Tool '{name}' created.")

    
    def update_studio_visibility(self):
        """Show/Hide Studio elements based on global state."""
        self.btn_new_tool.setVisible(STUDIO_MODE)
        
        # Update all open tool views
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if isinstance(w, StudioToolView):
                w.add_component_button.setVisible(STUDIO_MODE)

if __name__ == "__main__":
    # Enable High DPI Scaling for Windows
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_STYLESHEET)
    
    # Set app font
    font = QFont(FONTS['ui'])
    font.setPixelSize(13)
    app.setFont(font)
    
    window = MainWindow()
    
    # Store reference to filter on the app to prevent GC
    app.studio_filter = GlobalEditFilter(window)
    app.installEventFilter(app.studio_filter)
    
    window.showMaximized()
    sys.exit(app.exec())

# --- Snap/GIO Modul-Konflikte entschärfen (vor allen anderen Imports) ---
import os as _os
_os.environ.pop("GIO_MODULE_DIR", None)
import datetime
import json
import os
import pathlib
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
    Qt, QTimer, QPoint, QRect, Signal,
    QObject, QThread, QEvent,
    QMetaObject
)
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QIcon,
    QPainterPath, QPixmap, QImage,
    QFontMetrics
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QGridLayout, QSlider, QSizePolicy, QScrollArea, QAbstractItemView,
    QProgressBar, QMessageBox, QComboBox, QToolButton, QDoubleSpinBox, QSpinBox,
    QDialog, QCheckBox, QFormLayout, QInputDialog, QStyledItemDelegate,
    QStyle
)
# Matplotlib integration
import matplotlib
matplotlib.use("qtagg")
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib import image as mpimg
import autofocus
from autofocus import IdsCam, LaserSpotDetector, LiveLaserController, paint_laser_overlay
from commonIE import dbConnector
from commonIE import miltenyiBarcode
import datenbank as db
from z_trieb import ZTriebController
import gitterschieber as gs
import stage_control as resolve_stage
import stage_test as blaze_stage_test
from ie_Framework.Hardware.Camera.panda import PcoCameraBackend
from ie_Framework.Algorithm.laser_spot_detection import LaserSpotDetector as StageLaserSpotDetector
# ========================== DATENBANK / INFRA ==========================
DASHBOARD_WIDGET_CLS, _DASHBOARD_IMPORT_ERROR = (None, None)
# --- THEME CONFIGURATION ---
COLORS = {
    "bg": "#050505",            # Main Background
    "surface": "#0b0b0b",       # Card Background
    "surface_light": "#141414", # Inputs / Hover
    "border": "#1f1f1f",
    "primary": "#f5f5f5",       # Paper-like white
    "primary_hover": "#e6e6e6",
    "secondary": "#bdbdbd",     # Soft gray
    "text": "#f5f5f5",
    "text_muted": "#9a9a9a",
    "danger": "#ff5b5b",
    "success": "#7ad39b",
    "warning": "#f0b74a",
}
FONTS = {
    "ui": "Sofia Pro",
    "mono": "Consolas",
}
def _latest_stage_outdir() -> pathlib.Path:
    root = resolve_stage.DATA_ROOT
    newest = None
    newest_ts = -1.0
    try:
        for batch_dir in root.iterdir():
            if not batch_dir.is_dir():
                continue
            for run_dir in batch_dir.glob("Run_*"):
                if not run_dir.is_dir():
                    continue
                try:
                    ts = run_dir.stat().st_mtime
                except OSError:
                    continue
                if ts > newest_ts:
                    newest = run_dir
                    newest_ts = ts
    except Exception:
        return root
    return newest if newest is not None else root
def _make_folder_icon(color: str, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    tab_h = int(size * 0.35)
    body_top = int(size * 0.35)
    painter.drawRoundedRect(2, body_top, size - 4, size - body_top - 2, 2, 2)
    painter.drawRoundedRect(2, 2, int(size * 0.6), tab_h, 2, 2)
    painter.end()
    return QIcon(pm)
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
            ("PCO Panda A", -1),
            ("PCO Panda B", -2),
            ("MACS Resolve 20x", 0),
            ("AUTOFOKUS", 1),
            ("MACSeq", 2),
            ("MACS Resolve 40x", 3),
            ("Optikkorper Cam A", 4),
            ("Optikkorper Cam B", 5),
        ]
        self._instances = {} # Map idx -> camera_instance
    def get_instance(self, idx):
        if idx in self._instances:
            return self._instances[idx]
        instance = None
        if idx in (-1, -2): # PCO
            if PcoCameraBackend is not None:
                try:
                    cam_index = 0 if idx == -1 else 1
                    instance = PcoCameraBackend(cam_index=cam_index)
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
        if idx in (-1, -2): # PCO
            def pco_provider():
                cam = self.get_instance(idx)
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
# Live StageTest bus for cross-view updates (IPC)
class LiveStageBus(QObject):
    data_updated = Signal(dict)
    active_changed = Signal(bool)
    def __init__(self):
        super().__init__()
        self.active = False
    def set_active(self, is_active: bool):
        if self.active != is_active:
            self.active = is_active
            self.active_changed.emit(is_active)
    def push(self, payload: dict):
        self.data_updated.emit(payload)
LIVE_STAGE_BUS = LiveStageBus()
class LazyView(QWidget):
    """Create heavy views on demand so the main window can show quickly."""
    def __init__(self, factory, title: str | None = None, parent=None):
        super().__init__(parent)
        self._factory = factory
        self._title = title or "View"
        self._built = False
        self._child = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._placeholder = QLabel(f"{self._title} wird geladen...")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        layout.addWidget(self._placeholder)
    @property
    def view(self):
        return self._child
    def _build(self):
        if self._built:
            return
        self._built = True
        try:
            try:
                self._child = self._factory(parent=self)
            except TypeError:
                self._child = self._factory()
        except Exception as exc:
            self._placeholder.setText(f"{self._title} konnte nicht geladen werden:\n{exc}")
            return
        layout = self.layout()
        layout.removeWidget(self._placeholder)
        self._placeholder.deleteLater()
        layout.addWidget(self._child)
    def showEvent(self, event):
        super().showEvent(event)
        if not self._built:
            QTimer.singleShot(0, self._build)
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
    font-family: "{FONTS['ui']}", "Segoe UI", "Arial";
    font-size: 12px;
}}
/* SCROLLBARS */
QScrollBar:vertical {{
    border: none;
    background: #050505;
    width: 8px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border']};
    min-height: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{ background: {COLORS['text_muted']}; }}
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
    border-radius: 4px;
    padding: 8px 14px;
    color: {COLORS['text']};
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {COLORS['surface']};
    border-color: {COLORS['text_muted']};
}}
QPushButton:pressed {{
    background-color: {COLORS['border']};
}}
QLineEdit, QTextEdit {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 8px 10px;
    color: {COLORS['text']};
    selection-background-color: {COLORS['primary']};
    selection-color: {COLORS['bg']};
    font-size: 12px;
}}
QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {COLORS['text_muted']};
    background-color: {COLORS['surface']};
}}
QComboBox {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 6px 10px;
    color: {COLORS['text']};
}}
QComboBox:focus {{
    border-color: {COLORS['text_muted']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    border: none;
}}
/* TABLE WIDGET */
QTableWidget {{
    background-color: {COLORS['bg']};
    gridline-color: transparent;
    border: none;
    border-radius: 6px;
    outline: none;
    alternate-background-color: {COLORS['surface']};
}}
QTableWidget::item {{
    padding: 10px 8px;
    border-bottom: 1px solid {COLORS['border']};
    color: {COLORS['text']};
}}
QTableWidget::item:selected {{
    background-color: rgba(245, 245, 245, 0.08);
    color: {COLORS['primary']};
    border-left: 2px solid {COLORS['primary']};
}}
QTableWidget::item:hover {{
    background-color: rgba(245, 245, 245, 0.04);
}}
QHeaderView::section {{
    background-color: {COLORS['bg']};
    color: {COLORS['text_muted']};
    padding: 10px;
    border: none;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    border-bottom: 1px solid {COLORS['border']};
}}
QTableWidget QTableCornerButton::section {{
    background-color: {COLORS['bg']};
    border: none;
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
QCheckBox {{
    color: {COLORS['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid {COLORS['border']};
    background: {COLORS['surface']};
}}
QCheckBox::indicator:checked {{
    background: {COLORS['primary']};
    border: 1px solid {COLORS['primary']};
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
                border-radius: 4px;
                font-weight: 600;
                font-size: 11px;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                padding: 0 14px;
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
                    background-color: transparent;
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                }}
                QPushButton:hover {{ border: 1px solid {COLORS['text_muted']}; background-color: {COLORS['surface_light']}; }}
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
        self.header_frame = None
        self.header_layout = None
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
            }}
        """)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        if title:
            self.header_frame = QFrame()
            self.header_frame.setStyleSheet("background-color: transparent; border: none; border-bottom: 1px solid " + COLORS['border'] + ";")
            self.header_layout = QHBoxLayout(self.header_frame)
            self.header_layout.setContentsMargins(16, 12, 16, 12)
            self.header_layout.setSpacing(0)
            self.title_label = QLabel(title)
            self.title_label.setObjectName(f"Title_{title.replace(' ', '_')}")
            self.title_label.setStyleSheet("border: none; font-size: 11px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase;")
            self.header_layout.addWidget(self.title_label)
            self.main_layout.addWidget(self.header_frame)
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
    def set_compact(self, header_margins=(12, 8, 12, 8), content_margins=(12, 8, 12, 8), content_spacing=8):
        if self.header_layout:
            self.header_layout.setContentsMargins(*header_margins)
        self.content_layout.setContentsMargins(*content_margins)
        self.content_layout.setSpacing(content_spacing)
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
# --- TABLE DELEGATE ---
class DashboardTableDelegate(QStyledItemDelegate):
    def __init__(self, table, parent=None):
        super().__init__(parent)
        self.table = table
        self.radius = 8
        self.hpad = 10
        self.vpad = 6
    def paint(self, painter, option, index):
        painter.save()
        row = index.row()
        col = index.column()
        row_count = self.table.rowCount()
        col_count = self.table.columnCount()
        rect = option.rect.adjusted(0, 0, -1, -1)
        is_alt = bool(row % 2)
        base_bg = COLORS['surface_light'] if is_alt else COLORS['surface']
        border = hex_to_rgba(COLORS['border'], 0.5)
        # Row card effect: rounded corners only on first/last columns
        path = QPainterPath()
        if col == 0:
            path.addRoundedRect(rect, self.radius, self.radius)
        elif col == col_count - 1:
            path.addRoundedRect(rect, self.radius, self.radius)
        else:
            path.addRect(rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(base_bg))
        painter.drawPath(path)
        # Selected row highlight
        if option.state & QStyle.State_Selected:
            painter.setBrush(QColor(hex_to_rgba(COLORS['primary'], 0.06)))
            painter.drawPath(path)
            if col == 0:
                accent = QRect(rect.left(), rect.top(), 3, rect.height())
                painter.setBrush(QColor(COLORS['primary']))
                painter.drawRect(accent)
        # Bottom separator
        painter.setPen(QColor(border))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        # Cell content
        text = index.data() or ""
        col_name = ""
        header_item = self.table.horizontalHeaderItem(col)
        if header_item:
            col_name = header_item.text().lower()
        # Status pill
        if col_name in {"ok", "status", "result"}:
            is_ok = str(text).strip().lower() in {"true", "ok", "pass", "1", "yes"}
            bg = hex_to_rgba(COLORS['success'], 0.18) if is_ok else hex_to_rgba(COLORS['danger'], 0.18)
            fg = COLORS['success'] if is_ok else COLORS['danger']
            pill_text = "OK" if is_ok else "FAIL"
            fm = QFontMetrics(option.font)
            pw = fm.horizontalAdvance(pill_text) + 16
            ph = fm.height() + 6
            px = rect.center().x() - pw // 2
            py = rect.center().y() - ph // 2
            pill = QRect(px, py, pw, ph)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(pill, ph // 2, ph // 2)
            painter.setPen(QColor(fg))
            painter.drawText(pill, Qt.AlignCenter, pill_text)
        else:
            color = COLORS['text_muted'] if "guid" in col_name else COLORS['text']
            painter.setPen(QColor(color))
            text_rect = rect.adjusted(self.hpad, self.vpad, -self.hpad, -self.vpad)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, str(text))
        painter.restore()
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
        # Main layout for the entire view (Vertical stack)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)
        # --- TOP: KPI ROW ---
        kpi_strip = QWidget()
        kpi_row = QHBoxLayout(kpi_strip)
        kpi_row.setContentsMargins(0, 0, 0, 0)
        kpi_row.setSpacing(14)
        self.kpi_total = self.add_kpi_compact(kpi_row, "Total", "0", COLORS['primary'])
        self.kpi_pass = self.add_kpi_compact(kpi_row, "Pass", "0%", COLORS['secondary'])
        self.kpi_last = self.add_kpi_compact(kpi_row, "Latest", "---", COLORS['success'])
        kpi_row.addStretch()
        main_layout.addWidget(kpi_strip)
        # --- ACTIVITY TABLE + CONTROLS ---
        activity_card = Card("Recent Activity")
        activity_card.set_compact()
        al = QVBoxLayout()
        al.setContentsMargins(10, 6, 10, 12)
        al.setSpacing(12)
        if activity_card.title_label:
            activity_card.title_label.setStyleSheet(
                "border: none; font-size: 13px; font-weight: 700; letter-spacing: 0.4px;"
            )
        controls_strip = QWidget()
        controls_layout = QHBoxLayout(controls_strip)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)
        self.combo_testtype = QComboBox()
        self.combo_testtype.addItems(["kleberoboter", "gitterschieber_tool", "stage_test"])
        self.combo_testtype.currentIndexChanged.connect(self.trigger_refresh)
        self.combo_testtype.setFixedHeight(28)
        self.combo_testtype.setFixedWidth(190)
        self.status_indicator = QPushButton("● LIVE")
        self.status_indicator.setCursor(Qt.PointingHandCursor)
        self.status_indicator.clicked.connect(self.trigger_refresh)
        self.status_indicator.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {COLORS['success']}; "
            f"font-weight: 700; font-size: 10px; padding: 0 4px; }}"
        )
        self.btn_goto_ipc = ModernButton("IPC", "secondary")
        self.btn_goto_ipc.setObjectName("Dash_Btn_IPC")
        self.btn_goto_ipc.setFixedHeight(28)
        self.btn_toggle_entry = ModernButton("New Record", "ghost")
        self.btn_toggle_entry.setFixedHeight(28)
        controls_layout.addStretch()
        controls_layout.addWidget(self.combo_testtype)
        controls_layout.addWidget(self.status_indicator)
        controls_layout.addWidget(self.btn_goto_ipc)
        controls_layout.addWidget(self.btn_toggle_entry)
        al.addWidget(controls_strip)
        entry_card = self.setup_entry_ui_compact()
        self.entry_container = QWidget()
        self.entry_container.setMaximumWidth(520)
        self.entry_container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        entry_layout = QVBoxLayout(self.entry_container)
        entry_layout.setContentsMargins(0, 0, 0, 0)
        entry_layout.addWidget(entry_card)
        self.entry_container.setVisible(False)
        self.btn_toggle_entry.clicked.connect(
            lambda: self.entry_container.setVisible(not self.entry_container.isVisible())
        )
        entry_row = QHBoxLayout()
        entry_row.setContentsMargins(0, 0, 0, 0)
        entry_row.addWidget(self.entry_container)
        entry_row.addStretch()
        al.addLayout(entry_row)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Time", "Barcode", "User", "Status", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {COLORS['surface_light']};
                border: 1px solid {hex_to_rgba(COLORS['border'], 0.6)};
                border-radius: 12px;
                gridline-color: {COLORS['border']};
            }}
            QHeaderView::section {{
                background-color: {COLORS['surface']};
                color: {COLORS['text_muted']};
                padding: 12px 12px;
                border: none;
                border-bottom: 1px solid {hex_to_rgba(COLORS['border'], 0.7)};
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
        """)
        # Style the header
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.setSortingEnabled(True)
        self.table.setItemDelegate(DashboardTableDelegate(self.table))
        self.table.setObjectName("Dash_Table")
        al.addWidget(self.table)
        activity_card.add_layout(al)
        main_layout.addWidget(activity_card, 2)
        # 4. Manual Refresh (no auto-timer)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_data)
        # Initial fetch only
        self.update_data()
    def setup_entry_ui_compact(self, layout=None):
        entry_card = Card("New Record")
        entry_card.set_compact()
        entry_card.setMaximumWidth(520)
        entry_layout = QVBoxLayout()
        entry_layout.setSpacing(4)
        self.le_barcode = QLineEdit()
        self.le_barcode.setPlaceholderText("Barcode...")
        self.le_barcode.setObjectName("Dash_Entry_Barcode")
        self.le_barcode.setFixedHeight(28)
        self.le_barcode.setMaximumWidth(240)
        self.le_user = QLineEdit()
        self.le_user.setPlaceholderText("User...")
        self.le_user.setObjectName("Dash_Entry_User")
        self.le_user.setFixedHeight(28)
        self.le_user.setMaximumWidth(220)
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row1.addWidget(self.le_barcode)
        row1.addWidget(self.le_user)
        entry_layout.addLayout(row1)
        self.entry_stack = QStackedWidget()
        # Same widgets as before but more compact if needed
        w_kleber = QWidget()
        l_kleber = QHBoxLayout(w_kleber); l_kleber.setContentsMargins(0,0,0,0); l_kleber.setSpacing(4)
        self.cb_ok = QCheckBox("Test OK?"); self.cb_ok.setStyleSheet("font-weight: 600; color: " + COLORS['primary'] + "; font-size: 11px;")
        l_kleber.addWidget(self.cb_ok); l_kleber.addStretch()
        w_git = QWidget()
        l_git = QHBoxLayout(w_git); l_git.setContentsMargins(0,0,0,0); l_git.setSpacing(4)
        self.le_particles = QLineEdit(); self.le_particles.setPlaceholderText("P-Count")
        self.le_angle = QLineEdit(); self.le_angle.setPlaceholderText("Angle")
        self.le_particles.setFixedHeight(28)
        self.le_angle.setFixedHeight(28)
        self.le_particles.setMaximumWidth(200)
        self.le_angle.setMaximumWidth(200)
        l_git.addWidget(self.le_particles); l_git.addWidget(self.le_angle)
        w_stage = QWidget()
        l_stage = QHBoxLayout(w_stage); l_stage.setContentsMargins(0,0,0,0); l_stage.setSpacing(4)
        self.le_pos_name = QLineEdit(); self.le_pos_name.setPlaceholderText("Pos")
        self.le_fov = QLineEdit(); self.le_fov.setPlaceholderText("FOV")
        self.le_pos_name.setFixedHeight(28)
        self.le_fov.setFixedHeight(28)
        self.le_pos_name.setMaximumWidth(200)
        self.le_fov.setMaximumWidth(200)
        l_stage.addWidget(self.le_pos_name); l_stage.addWidget(self.le_fov)
        self.entry_stack.addWidget(w_kleber); self.entry_stack.addWidget(w_git); self.entry_stack.addWidget(w_stage)
        entry_layout.addWidget(self.entry_stack)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_send = ModernButton("Send", "primary")
        self.btn_send.setFixedHeight(28)
        self.btn_send.clicked.connect(self.send_current_entry)
        btn_row.addWidget(self.btn_send)
        # Gateway send is automatic when connected to raspi-webgui.
        entry_layout.addLayout(btn_row)
        entry_card.add_layout(entry_layout)
        if layout is not None:
            layout.addWidget(entry_card)
        return entry_card
    def add_kpi_compact(self, layout, title, value, color):
        container = QFrame()
        container.setMinimumHeight(92)
        container.setStyleSheet(
            f"background-color: {COLORS['surface']}; border: 1px solid {hex_to_rgba(COLORS['border'], 0.8)}; "
            f"border-radius: 12px;"
        )
        l = QHBoxLayout(container)
        l.setContentsMargins(14, 12, 14, 12)
        l.setSpacing(10)
        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet(f"background-color: {color}; border-radius: 2px;")
        body = QVBoxLayout()
        body.setSpacing(4)
        t_lbl = QLabel(title.upper())
        t_lbl.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-weight: 600; font-size: 9px; letter-spacing: 1px; border:none;"
        )
        v_lbl = QLabel(value)
        v_lbl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 22px; border:none;")
        body.addWidget(t_lbl)
        body.addWidget(v_lbl)
        body.addStretch()
        l.addWidget(accent)
        l.addLayout(body)
        l.addStretch()
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
        source_label = "GW" if db.is_on_gateway_wifi() else "DB"
        # UI Feedback
        self.status_indicator.setText(f"● FETCHING ({source_label})")
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
        source_label = "GW" if db.is_on_gateway_wifi() else "DB"
        # Update Connection Status UI
        if not connected:
            self.status_indicator.setText(f"● OFFLINE {source_label} (Click to Retry)")
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
            self.status_indicator.setText(f"● LIVE ({source_label})")
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
        # Update Table with prioritized ordering (Zeit -> Test-Parameter -> Meta)
        columns = list(df.columns)
        self.table.setSortingEnabled(False)
        # Identify time columns first, then test parameters, then meta fields
        time_priority = ["starttest", "endtest", "timestamp", "time", "date", "datetime"]
        meta_fields = {
            "barcodenummer", "barcode", "user",
            "device", "device_id", "testtype"
        }
        guid_cols = []
        for col in columns:
            normalized = col.lower().replace("_", "")
            if "testguid" in normalized:
                guid_cols.append(col)
        time_cols = []
        for name in time_priority:
            for col in columns:
                if name in col.lower() and col not in time_cols:
                    time_cols.append(col)
        # Pick up any datetime-typed columns not caught by the name heuristic
        for col in columns:
            try:
                if pd.api.types.is_datetime64_any_dtype(df[col]) and col not in time_cols:
                    time_cols.append(col)
            except Exception:
                pass
        ok_cols = [c for c in columns if c.lower() in {"ok", "status", "result"}]
        meta_cols = [c for c in columns if c.lower() in meta_fields]
        # Remove GUID from other buckets so it can be forced to the end
        meta_cols = [c for c in meta_cols if c not in guid_cols]
        time_cols = [c for c in time_cols if c not in guid_cols]
        ok_cols = [c for c in ok_cols if c not in guid_cols and c not in time_cols]
        param_cols = [c for c in columns if c not in time_cols and c not in meta_cols and c not in guid_cols and c not in ok_cols]
        ordered_columns = time_cols + param_cols + ok_cols + [c for c in meta_cols if c not in time_cols] + guid_cols
        if time_cols and ok_cols:
            try:
                start_idx = next(i for i, c in enumerate(time_cols) if "starttest" in c.lower())
                ordered_columns = (
                    time_cols[: start_idx + 1]
                    + ok_cols
                    + time_cols[start_idx + 1 :]
                    + param_cols
                    + [c for c in meta_cols if c not in time_cols]
                    + guid_cols
                )
            except StopIteration:
                pass
        if not ordered_columns:
            ordered_columns = columns
        # Sort data by time (desc) then parameters (asc) to rank newest first
        sort_cols = time_cols[:1] + param_cols
        display_df = df
        if sort_cols:
            try:
                ascending = [False] + [True] * (len(sort_cols) - 1)
                display_df = df.sort_values(by=sort_cols, ascending=ascending, na_position="last")
            except Exception as e:
                print(f"DashboardView sort fallback: {e}")
        self.table.clear()
        self.table.setColumnCount(len(ordered_columns))
        self.table.setHorizontalHeaderLabels([str(c) for c in ordered_columns])
        self.table.setRowCount(len(display_df))
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        for i, (_, row) in enumerate(display_df.iterrows()):
            for j, col in enumerate(ordered_columns):
                val = row.get(col)
                if pd.isna(val):
                    text = ""
                elif hasattr(val, "strftime"):
                    text = val.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    text = str(val)
                item = QTableWidgetItem(text)
                item.setFont(QFont(FONTS['ui'], 10))
                self.table.setItem(i, j, item)
        # Default sort indicator on time column if available
        if time_cols:
            time_index = ordered_columns.index(time_cols[0])
            self.table.setSortingEnabled(True)
            self.table.sortItems(time_index, Qt.DescendingOrder)
        else:
            self.table.setSortingEnabled(True)
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
        barcode_str = self.le_barcode.text().strip()
        if not barcode_str:
            barcode_str = str(db.DUMMY_BARCODE)
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
        if db.is_on_gateway_wifi():
            try:
                payload, ack = db.send_payload_gateway(
                    device_id=testtype,
                    barcode=barcode_str,
                    payload=payload,
                    user=user_str,
                    start_time=datetime.datetime.now(datetime.timezone.utc),
                    end_time=datetime.datetime.now(datetime.timezone.utc),
                )
            except Exception as e:
                self.status_indicator.setText("● GATEWAY FAILED")
                QMessageBox.critical(self, "Gateway Error", f"Senden ueber Gateway fehlgeschlagen:\n{e}")
                return
            if ack == "OK":
                self.status_indicator.setText("● GATEWAY SENT")
                QTimer.singleShot(2000, lambda: self.status_indicator.setText("● LIVE"))
                self.update_data()
                self.clear_entry_fields()
            else:
                self.status_indicator.setText("● GATEWAY NO ACK")
                QMessageBox.warning(
                    self,
                    "Gateway Antwort",
                    f"Unerwartete Gateway-Antwort: {ack!r}\nPayload: {payload}",
                )
            return
        try:
            barcode_obj = miltenyiBarcode.mBarcode(barcode_str)
        except Exception as e:
            QMessageBox.warning(self, "Barcode Fehler", f"Konnte Barcode nicht erzeugen:\n{e}")
            return
        conn = None
        try:
            conn = dbConnector.connection()
            conn.connect()
            now = datetime.datetime.now()
            conn.sendData(
                now, now, 0,
                testtype, payload,
                barcode_obj, user_str
            )
            # Show success and refresh
            self.status_indicator.setText("● SENT SUCCESS")
            QTimer.singleShot(2000, lambda: self.status_indicator.setText("● LIVE"))
            self.update_data()
            self.clear_entry_fields()
        except Exception as e:
            self.status_indicator.setText("● SEND FAILED")
            QMessageBox.critical(self, "Database Error", f"Failed to send data:\n{e}")
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass
    def send_current_entry_gateway(self):
        testtype = self.combo_testtype.currentText()
        if testtype != "kleberoboter":
            QMessageBox.information(
                self,
                "Gateway Hinweis",
                "Gateway-Senden ist aktuell nur fuer 'kleberoboter' implementiert.",
            )
            return
        barcode_str = self.le_barcode.text().strip()
        if not barcode_str:
            barcode_str = str(db.DUMMY_BARCODE)
        result_val = bool(self.cb_ok.isChecked())
        try:
            payload, ack = db.send_dummy_payload_gateway(
                device_id="kleberoboter",
                barcode=int(barcode_str),
                result=result_val,
                start_time=datetime.datetime.now(datetime.timezone.utc),
                end_time=datetime.datetime.now(datetime.timezone.utc),
            )
        except Exception as e:
            self.status_indicator.setText("● GATEWAY FAILED")
            QMessageBox.critical(self, "Gateway Error", f"Senden ueber Gateway fehlgeschlagen:\n{e}")
            return
        if ack == "OK":
            self.status_indicator.setText("● GATEWAY SENT")
            QTimer.singleShot(2000, lambda: self.status_indicator.setText("● LIVE"))
            self.update_data()
            self.clear_entry_fields()
        else:
            self.status_indicator.setText("● GATEWAY NO ACK")
            QMessageBox.warning(
                self,
                "Gateway Antwort",
                f"Unerwartete Gateway-Antwort: {ack!r}\nPayload: {payload}",
            )
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
class CameraWidget(QWidget):
    """Single reusable camera widget for any camera source."""
    def __init__(self, frame_provider, *, interval_ms: int = 150, start_immediately: bool = False, analyzer=None, parent=None):
        super().__init__(parent)
        self._frame_provider = frame_provider
        self._last_frame = None
        self._interval_ms = interval_ms
        self._autostart = bool(start_immediately)
        self._is_running = False
        self._analyzer = analyzer
        self.label = QLabel("Kein Bild")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMinimumHeight(150)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setStyleSheet(f"background-color: #050505; border-radius: 8px; border: 1px solid {COLORS['border']};")
        self.status = QLabel("Warte auf Kameradaten...")
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
    def set_provider(self, frame_provider):
        self._frame_provider = frame_provider
    def set_analyzer(self, analyzer):
        self._analyzer = analyzer
    def open(self):
        self.start()
    def start(self):
        if self._frame_provider is None:
            self.status.setText("Keine Kamera")
            return
        if not self._timer.isActive():
            self._timer.start(self._interval_ms)
        self._is_running = True
    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
        self._is_running = False
    def showEvent(self, event):
        super().showEvent(event)
        if self._autostart and not self._is_running:
            self.start()
    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)
    def _apply_frame(self, frame, status_text=None):
        if frame is None:
            if status_text:
                self.status.setText(status_text)
            else:
                self.status.setText("Warte auf Kameradaten...")
            return
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
    def _tick(self):
        try:
            result = self._frame_provider() if self._frame_provider else None
            status_text = None
            if isinstance(result, tuple) and len(result) == 2:
                frame, status_text = result
            else:
                frame = result
            if self._analyzer is not None and frame is not None:
                try:
                    analyzed = self._analyzer(frame)
                    if isinstance(analyzed, tuple) and len(analyzed) == 2:
                        frame, status_text = analyzed
                    elif isinstance(analyzed, str):
                        status_text = analyzed
                except Exception as exc:
                    status_text = f"Analyse-Fehler: {exc}"
            self._apply_frame(frame, status_text)
        except Exception as exc:
            self.status.setText(f"Kamera-Fehler: {exc}")
def add_camera_monitor(layout, frame_provider, title="Monitor", stretch=1):
    """
    Creates a complete camera monitoring card and adds it to the layout.
    """
    card = Card(title)
    cam = CameraWidget(frame_provider, start_immediately=False)
    card.add_widget(cam)
    layout.addWidget(card, stretch)
    return cam
class StageTestLaserProvider:
    def __init__(self, backend, axis: str):
        self.backend = backend
        self.axis = axis
        self._last_center = None
    def __call__(self):
        if self.backend is None:
            return None
        frame = self.backend.capture_frame(self.axis)
        if frame is None:
            return None
        try:
            if frame.ndim == 3:
                gray = frame[..., 1]
            else:
                gray = frame
            x, y, _ = StageLaserSpotDetector.detect_laser_spot_otsu(gray)
            self._last_center = (int(x), int(y))
        except Exception:
            x, y = None, None
        overlay = frame.copy()
        if overlay.ndim == 2:
            overlay = cv2.cvtColor(overlay, cv2.COLOR_GRAY2BGR)
        if x is not None and y is not None:
            cv2.circle(overlay, (int(x), int(y)), 6, (0, 255, 0), 1)
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if x is not None and y is not None:
            status = f"{self.axis} | {ts} | {x}, {y}"
        else:
            status = f"{self.axis} | {ts}"
        return overlay, status
class PcoLaserController(QObject):
    """Live controller for PCO Panda cameras with laser overlay support."""
    frameReady = Signal(QImage)
    centerChanged = Signal(int, int)
    def __init__(self, cam_index: int, detector: LaserSpotDetector, parent=None):
        super().__init__(parent)
        self.cam_index = cam_index
        self.detector = detector
        self._backend = None
        self._ref_point = None
        self._timeout_ms = 200
        self._last_init_attempt = 0.0
        self._retry_interval_s = 1.0
        self._last_init_error = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._init_camera()
    def _init_camera(self):
        self._last_init_attempt = time.monotonic()
        try:
            self._backend = PcoCameraBackend(cam_index=self.cam_index)
            self._backend.start()
            self._last_init_error = None
        except Exception as exc:
            self._backend = None
            self._last_init_error = str(exc)
            print(f"[WARN] PCO konnte nicht initialisiert werden: {exc}")
    def _maybe_reinit_camera(self):
        if (time.monotonic() - self._last_init_attempt) < self._retry_interval_s:
            return
        self._init_camera()
    def start(self, interval_ms: int = 140):
        if not self._timer.isActive():
            self._timer.start(interval_ms)
    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
    def shutdown(self):
        self.stop()
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass
        self._backend = None
    def _tick(self):
        if self._backend is None:
            self._maybe_reinit_camera()
            return
        try:
            frame = self._backend.get_frame()
            if frame is None:
                return
            qimg, (cx, cy) = paint_laser_overlay(
                frame,
                self.detector,
                ref_point=self._ref_point,
                is_dummy=False,
            )
            self.frameReady.emit(qimg)
            self.centerChanged.emit(int(cx), int(cy))
        except Exception as exc:
            print(f"[WARN] PCO Live-Frame fehlgeschlagen: {exc}")
    # ---- Camera controls -------------------------------------------------
    def set_exposure_us(self, exposure_us: int):
        if self._backend is None:
            return
        try:
            self._backend.set_exposure_ms(float(exposure_us) / 1000.0)
            self._timeout_ms = max(100, int(exposure_us / 1000.0) + 150)
        except Exception as exc:
            print(f"[WARN] PCO Exposure setzen fehlgeschlagen: {exc}")
    def get_exposure_limits_us(self) -> tuple[float, float, float]:
        if self._backend is None:
            return 2000.0, 50.0, 200000.0
        try:
            limits = self._backend.get_exposure_limits_s()
            curr = self._backend.get_exposure_s()
            if limits is None:
                return 2000.0, 50.0, 200000.0
            min_s, max_s = limits
            curr_s = curr if curr is not None else min_s
            return curr_s * 1_000_000.0, min_s * 1_000_000.0, max_s * 1_000_000.0
        except Exception as exc:
            print(f"[WARN] PCO Exposure-Limits nicht lesbar: {exc}")
            return 2000.0, 50.0, 200000.0
    def set_reference_point(self, x: int | None, y: int | None):
        if x is None or y is None:
            self._ref_point = None
        else:
            self._ref_point = (int(x), int(y))
    def clear_reference_point(self):
        self._ref_point = None
    def get_reference_point(self) -> tuple[int, int] | None:
        return self._ref_point
    def get_pixel_size_um(self) -> float | None:
        return None
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
        self._last_qimage = None
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
        monitor_card.setMinimumHeight(520)  # Larger live view
        self.cam_embed = CameraWidget(self._get_hub_frame, start_immediately=False)
        monitor_card.add_widget(self.cam_embed)
        layout.addWidget(monitor_card, 3)
        # --- BOTTOM: CONTROLS (HORIZONTAL & COMPACT) ---
        controls_row = QHBoxLayout()
        controls_row.setSpacing(15)
        # 1. Camera Selection - Grouped
        cam_name_map = {idx: name for name, idx in CAMERA_REGISTRY.cams}
        kollimator_idxs = [0, 3, 2]  # MACS Resolve 20x, 40x, MACSeq
        autofocus_idxs = [1, -2]     # AUTOFOKUS, PCO Panda B
        self._cams = []
        self._cam_buttons = {}
        self.btn_group = []
        def _add_cam_buttons(target_layout, idxs):
            for i, idx in enumerate(idxs):
                text = cam_name_map.get(idx, f"Index {idx}")
                btn = ModernButton(text, "secondary")
                btn.setMinimumHeight(34)
                btn.setFont(QFont(FONTS['ui'], 11, QFont.Bold))
                btn.clicked.connect(lambda _, id=idx: self._select_camera(id))
                target_layout.addWidget(btn, i // 2, i % 2)
                self.btn_group.append(btn)
                self._cam_buttons[idx] = btn
                self._cams.append((text, idx))
        cam_koll_card = Card("KOLLIMATOR")
        cam_koll_layout = QGridLayout()
        cam_koll_layout.setSpacing(6)
        _add_cam_buttons(cam_koll_layout, kollimator_idxs)
        cam_koll_card.add_layout(cam_koll_layout)
        controls_row.addWidget(cam_koll_card, 2)
        cam_af_card = Card("AUTOFOKUS")
        cam_af_layout = QGridLayout()
        cam_af_layout.setSpacing(6)
        _add_cam_buttons(cam_af_layout, autofocus_idxs)
        cam_af_card.add_layout(cam_af_layout)
        controls_row.addWidget(cam_af_card, 2)
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
        self.btn_save_align_pdf = ModernButton("Save PDF", "ghost")
        self.btn_save_align_pdf.setMinimumHeight(30)
        self.btn_save_align_pdf.clicked.connect(self._save_alignment_pdf)
        al.addWidget(self.btn_save_align_pdf)
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
        # Keep alignment panel size stable even as live values change.
        fm = QFontMetrics(self.lbl_dist.font())
        sample_widths = [
            fm.horizontalAdvance("Ref: 9999, 9999"),
            fm.horizontalAdvance("dx: +9999 px  (+9999.9 um)"),
            fm.horizontalAdvance("dy: +9999 px  (+9999.9 um)"),
            fm.horizontalAdvance("dist: 9999.99 px  (+9999.9 um ? 99.999 mm)"),
            fm.horizontalAdvance("Status: ALIGN (<= 999.9 px)"),
        ]
        fixed_w = max(sample_widths) + 12
        for lbl in (self.lbl_ref, self.lbl_dx, self.lbl_dy, self.lbl_dist, self.lbl_align_status):
            lbl.setFixedWidth(fixed_w)
        align_card.add_layout(al)
        controls_row.addWidget(align_card, 1)
        layout.addLayout(controls_row, 1)
        try:
            print(f"[INFO] Kamera-Buttons (Name -> Index): {self._cams}")
        except Exception:
            pass
        self._update_button_styles()
        # Start controller lazily when the view is shown.
    def _get_hub_frame(self):
        if self._last_qimage is None:
            return None
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        if self._last_center is not None:
            cx, cy = self._last_center
            status = f"AUTOFOKUS | {ts} | {cx}, {cy}"
        else:
            status = f"AUTOFOKUS | {ts}"
        return self._last_qimage, status
    def _get_frame(self):
        try:
            return autofocus.acquire_frame(self._current_dev_idx)
        except:
            return None
    def _select_camera(self, idx):
        if idx >= 0:
            self._log_device_map()
        try:
            cam_name = next(name for name, cam_idx in self._cams if cam_idx == idx)
        except Exception:
            cam_name = f"Index {idx}"
        if idx == self._current_dev_idx:
            print(f"[INFO] Kamera unveraendert: {cam_name} (Index {idx})")
            return
        prev_idx = self._current_dev_idx
        self._shutdown_laser_controller()
        try:
            if prev_idx >= 0:
                autofocus.shutdown(prev_idx)
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
            if idx >= 0:
                curr, min_e, max_e = autofocus.get_exposure_limits(idx)
                self._apply_exposure_limits(curr, min_e, max_e)
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
        for idx, btn in self._cam_buttons.items():
            if idx == self._current_dev_idx:
                btn.set_variant("primary")
            else:
                btn.set_variant("secondary")
    def _set_exposure(self, val_ms):
        try:
            if self._laser is not None:
                self._laser.set_exposure_us(int(val_ms * 1000))
            elif self._current_dev_idx >= 0:
                autofocus.set_exposure(self._current_dev_idx, int(val_ms * 1000))
        except:
            pass
    def _init_laser_controller(self, idx: int):
        self._shutdown_laser_controller()
        try:
            if idx < 0:
                cam_index = 0 if idx == -1 else 1
                self._laser = PcoLaserController(cam_index, self._detector, parent=self)
            else:
                self._laser = LiveLaserController(idx, self._detector, parent=self)
            self._laser.frameReady.connect(self._on_laser_frame)
            self._laser.centerChanged.connect(self._on_laser_center)
            self._laser.start()
            try:
                self._set_exposure(self.spin_expo.value())
            except Exception:
                pass
            try:
                curr, min_e, max_e = self._laser.get_exposure_limits_us()
                self._apply_exposure_limits(curr, min_e, max_e)
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
        if self.cam_embed is not None:
            self.cam_embed.start()
    def hideEvent(self, event):
        self._shutdown_laser_controller()
        if self.cam_embed is not None:
            self.cam_embed.stop()
        super().hideEvent(event)
    def _on_laser_frame(self, qimg: QImage):
        try:
            self._last_qimage = qimg
            self._last_frame_size = (qimg.width(), qimg.height())
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            if self._last_center is not None:
                cx, cy = self._last_center
                if self.cam_embed is not None:
                    self.cam_embed.status.setText(f"LIVE | {ts} | {cx}, {cy}")
            else:
                if self.cam_embed is not None:
                    self.cam_embed.status.setText(f"LIVE | {ts}")
        except Exception as exc:
            if self.cam_embed is not None:
                self.cam_embed.status.setText(f"Kamera-Fehler: {exc}")
    def _shutdown_laser_controller(self):
        if self._laser is None:
            return
        try:
            self._laser.stop()
            self._laser.shutdown()
        except Exception:
            pass
        self._laser = None
    def _apply_exposure_limits(self, curr_us, min_us, max_us):
        try:
            min_ms = max(0.01, float(min_us) / 1000.0)
            max_ms = max(min_ms + 0.01, float(max_us) / 1000.0)
            curr_ms = float(curr_us) / 1000.0
            self.spin_expo.blockSignals(True)
            self.slider_expo.blockSignals(True)
            self.spin_expo.setRange(min_ms, max_ms)
            self.slider_expo.setRange(int(min_ms * 10), int(max_ms * 10))
            self.spin_expo.setValue(curr_ms)
            self.slider_expo.setValue(int(curr_ms * 10))
            self.spin_expo.blockSignals(False)
            self.slider_expo.blockSignals(False)
        except Exception:
            pass
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
    def _save_alignment_pdf(self):
        if self._last_qimage is None:
            QMessageBox.warning(self, "PDF speichern", "Kein Bild verfuegbar.")
            return
        if self._last_center is None or not self._last_frame_size:
            QMessageBox.warning(self, "PDF speichern", "Keine Alignment-Daten verfuegbar.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = _latest_stage_outdir()
        default_dir.mkdir(parents=True, exist_ok=True)
        path_str = str(default_dir / f"autofocus_alignment_{ts}.pdf")
        w, h = self._last_frame_size
        cx, cy = self._last_center
        ref = self._laser.get_reference_point() if self._laser is not None else None
        if ref is not None:
            rx, ry = ref
            dx = int(cx - rx)
            dy = int(cy - ry)
            ref_text = f"{rx}, {ry}"
        else:
            dx = int(cx - w // 2)
            dy = int(cy - h // 2)
            ref_text = "center"
        dist = float(np.hypot(dx, dy))
        px_um = None
        if self._laser is not None:
            px_um = self._laser.get_pixel_size_um()
        tol_px = 5.0
        ok = (dist <= tol_px)
        with PdfPages(path_str) as pdf:
            fig = Figure(figsize=(11.69, 8.27), dpi=110, facecolor=COLORS['surface'])
            ax_text = fig.add_subplot(121)
            ax_text.axis("off")
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            text_lines = [
                "Autofocus Alignment Report",
                "",
                f"Zeitpunkt: {now}",
                f"Bildgroesse: {w} x {h} px",
                f"Center: {cx}, {cy} px",
                f"Referenz: {ref_text}",
                f"dx/dy: {dx:+d} px / {dy:+d} px",
                f"dist: {dist:.2f} px",
                f"Toleranz: +/- {tol_px:.1f} px -> {'OK' if ok else 'ALIGN'}",
            ]
            if px_um:
                dx_um = dx * px_um
                dy_um = dy * px_um
                dist_um = dist * px_um
                dist_mm = dist_um / 1000.0
                text_lines += [
                    f"dx/dy: {dx_um:+.1f} um / {dy_um:+.1f} um",
                    f"dist: {dist_um:.1f} um ({dist_mm:.3f} mm)",
                ]
            text = "\n".join(text_lines)
            ax_text.text(0.05, 0.95, text, va="top", ha="left", fontsize=12, color=COLORS['text'], family='monospace')
            qimg = self._last_qimage.convertToFormat(QImage.Format_RGB888)
            h, w = qimg.height(), qimg.width()
            stride = qimg.bytesPerLine()
            buf = qimg.constBits()
            arr = np.frombuffer(buf, np.uint8, count=qimg.sizeInBytes())
            arr = arr.reshape((h, stride // 3, 3))[:, :w, :]
            ax_img = fig.add_subplot(122)
            ax_img.imshow(arr)
            ax_img.axis("off")
            fig.tight_layout()
            pdf.savefig(fig)
        QMessageBox.information(self, "PDF gespeichert", f"Report gespeichert:\\n{path_str}")
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
        self._last_db_sync_ts = 0.0
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
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        # Left Column
        left_col = QVBoxLayout()
        left_col.setSpacing(10)
        setup_card = Card("Test-Setup")
        setup_card.set_compact()
        form_layout = QVBoxLayout()
        form_layout.setSpacing(10)
        self.inputs = {}
        self.inputs["operator"] = self.add_input(form_layout, "Bediener", "M. Zschach")
        self.inputs["batch"] = self.add_input(form_layout, "Chargennummer", "B2025-10-30-01")
        lbl_note = QLabel("NOTIZEN")
        lbl_note.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLORS['text_muted']}; border: none;")
        form_layout.addWidget(lbl_note)
        self.inputs["notes"] = QTextEdit()
        self.inputs["notes"].setPlaceholderText("Kommentare hier eingeben...")
        self.inputs["notes"].setFixedHeight(60)
        form_layout.addWidget(self.inputs["notes"])
        dur_row = QHBoxLayout()
        dur_lbl = QLabel("DAUERTEST-DAUER (H)")
        dur_lbl.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLORS['text_muted']}; border: none;")
        self.duration_hours = QComboBox()
        self.duration_hours.addItems(["1", "2", "4", "6", "8", "10", "12", "15", "18", "24", "36", "48"])
        self.duration_hours.setCurrentText("8")
        self.duration_hours.setFixedHeight(28)
        self.duration_hours.setCursor(Qt.PointingHandCursor)
        self.duration_hours.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                padding: 6px 10px;
                color: {COLORS['text']};
            }}
            QComboBox::drop-down {{
                border-left: 1px solid {COLORS['border']};
                width: 28px;
            }}
            QComboBox::down-arrow {{
                image: url(:/qt-project.org/styles/commonstyle/images/arrowdown-16.png);
            }}
        """)
        dur_row.addWidget(dur_lbl)
        dur_row.addStretch()
        dur_row.addWidget(self.duration_hours)
        form_layout.addLayout(dur_row)
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)
        self.btn_start = ModernButton("Kalibriermessung starten", "primary")
        self.btn_start.clicked.connect(self.toggle_precision_test)
        btn_layout.addWidget(self.btn_start)
        self.btn_dauer = ModernButton("Dauertest starten", "secondary")
        self.btn_dauer.clicked.connect(self.toggle_endurance_test)
        btn_layout.addWidget(self.btn_dauer)
        row_btn = QHBoxLayout()
        self.btn_open = ModernButton("Ordner öffnen", "ghost")
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_open.setEnabled(True)
        row_btn.addWidget(self.btn_open)
        self.chk_db_sync = QCheckBox("DB Sync")
        self.chk_db_sync.setChecked(False)
        self.chk_db_sync.setStyleSheet(f"font-weight: 600; color: {COLORS['text_muted']};")
        self.chk_db_sync.toggled.connect(self._on_db_sync_toggled)
        row_btn.addWidget(self.chk_db_sync)
        btn_layout.addLayout(row_btn)
        # Progress Area
        self.progress_container = QWidget()
        pl = QVBoxLayout(self.progress_container)
        pl.setContentsMargins(0, 6, 0, 0)
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
        status_card.set_compact()
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
        qa_lim = QLabel(f"Limit: {resolve_stage.DUR_MAX_UM:.1f} µm")
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
        chart_card.set_compact()
        # Header inside chart
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Echtzeit-Abweichung (X vs Y)"))
        top_row.addStretch()
        self.timer_lbl = QLabel("15:00:00")
        self.timer_lbl.setStyleSheet(f"font-family: {FONTS['mono']}; font-size: 16px; font-weight: bold; color: {COLORS['primary']};")
        top_row.addWidget(self.timer_lbl)
        chart_card.add_layout(top_row)
        self.chart = ModernChart(height=5)
        self.chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.line_x, = self.chart.ax.plot([], [], color=COLORS['primary'], linewidth=2, label="Fehler X")
        self.line_y, = self.chart.ax.plot([], [], color=COLORS['secondary'], linewidth=2, label="Fehler Y")
        self.chart.ax.set_xlabel("Zeit [min]", color=COLORS['text_muted'])
        self.chart.ax.set_ylabel("Abweichung [µm]", color=COLORS['text_muted'])
        # Chart Legend
        leg = self.chart.ax.legend(loc='upper right', facecolor=COLORS['surface'], edgecolor=COLORS['border'], labelcolor=COLORS['text'])
        leg.get_frame().set_linewidth(1)
        self.chart.fig.subplots_adjust(left=0.08, right=0.98, top=0.92, bottom=0.12)
        chart_card.add_widget(self.chart)
        right_col.addWidget(chart_card)
        layout.addLayout(right_col, 3)
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
    def _send_stage_db_event(self, user_id: str, event_label: str):
        """Log stage test starts into the kleberoboter DB without blocking the UI."""
        def _task():
            conn = None
            try:
                now = datetime.datetime.now()
                if db.is_on_gateway_wifi():
                    db.send_payload_gateway(
                        device_id="kleberoboter",
                        barcode=str(db.DUMMY_BARCODE),
                        payload={"ok": True, "event": event_label},
                        user=str(user_id),
                        start_time=now,
                        end_time=now,
                    )
                else:
                    conn = dbConnector.connection()
                    conn.connect()
                    barcode_obj = miltenyiBarcode.mBarcode(str(db.DUMMY_BARCODE))
                    conn.sendData(
                        now,
                        now,
                        0,
                        "kleberoboter",
                        {"ok": True, "event": event_label},
                        barcode_obj,
                        str(user_id),
                    )
                print(f"[StageControl] DB event sent: {event_label} (user {user_id})")
            except Exception as e:
                print(f"[StageControl] DB event failed ({event_label}): {e}")
            finally:
                if conn:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
        threading.Thread(target=_task, daemon=True).start()
    def _send_gitterschieber_measurement(self, err_x_um: float, err_y_um: float):
        """Send live endurance measurements into gitterschieber_tool (particle_count + justage_angle)."""
        def _task():
            conn = None
            try:
                now = datetime.datetime.now()
                payload = {
                    "particle_count": int(round(abs(err_x_um))),
                    "justage_angle": round(float(err_y_um), 3),
                }
                if db.is_on_gateway_wifi():
                    db.send_payload_gateway(
                        device_id="gitterschieber_tool",
                        barcode=str(db.DUMMY_BARCODE),
                        payload=payload,
                        user="stage_sync",
                        start_time=now,
                        end_time=now,
                    )
                else:
                    conn = dbConnector.connection()
                    conn.connect()
                    barcode_obj = miltenyiBarcode.mBarcode(str(db.DUMMY_BARCODE))
                    conn.sendData(
                        now,
                        now,
                        0,
                        "gitterschieber_tool",
                        payload,
                        barcode_obj,
                        "stage_sync",
                    )
            except Exception as e:
                print(f"[StageControl] DB sync failed: {e}")
            finally:
                if conn:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
        threading.Thread(target=_task, daemon=True).start()
    def _on_db_sync_toggled(self, checked: bool):
        LIVE_STAGE_BUS.set_active(bool(checked))
        if checked:
            LIVE_STAGE_BUS.push({"event": "stage_test_start", "ts": datetime.datetime.now()})
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
        self._send_stage_db_event(user_id="100", event_label="Kalibrierung")
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
        if hasattr(self, "duration_hours"):
            self._duration_sec = int(float(self.duration_hours.currentText()) * 3600)
        self._acquire_metadata()
        out_dir = self._ensure_run_dir()
        self._send_stage_db_event(user_id="200", event_label="Dauertest")
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
        LIVE_STAGE_BUS.set_active(bool(self.chk_db_sync.isChecked()))
        if self.chk_db_sync.isChecked():
            LIVE_STAGE_BUS.push({"event": "stage_test_start", "ts": datetime.datetime.now()})
    def _on_endurance_update(self, data):
        self.tick += 1
        err_x = data.get("err_x_um")
        err_y = data.get("err_y_um")
        if err_x is None:
            err_x = float(data.get("ex", 0.0)) * 1e6
        if err_y is None:
            err_y = float(data.get("ey", 0.0)) * 1e6
        max_err = data.get("max_abs_um", 0.0)
        t_val = data.get("t")
        x_val = float(t_val) if t_val is not None else self.tick
        self.x_data.append(x_val)
        self.y1_data.append(err_x)
        self.y2_data.append(err_y)
        self.line_x.set_data(list(self.x_data), list(self.y1_data))
        self.line_y.set_data(list(self.x_data), list(self.y2_data))
        if self.x_data:
            xmin = self.x_data[0]
            xmax = self.x_data[-1]
            if xmin == xmax:
                xmax = xmin + 1.0
            self.chart.ax.set_xlim(xmin, xmax)
        y_max = max(
            0.5,
            max(abs(min(self.y1_data, default=0.0)), abs(max(self.y1_data, default=0.0))),
            max(abs(min(self.y2_data, default=0.0)), abs(max(self.y2_data, default=0.0))),
        )
        y_pad = max(0.2, y_max * 0.15)
        self.chart.ax.set_ylim(-y_max - y_pad, y_max + y_pad)
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
        if self.chk_db_sync.isChecked():
            now = time.time()
            if now - self._last_db_sync_ts >= 1.5:
                self._last_db_sync_ts = now
                self._send_gitterschieber_measurement(err_x, err_y)
    def _stop_endurance_test(self):
        if self.dauer_wrk:
            self.dauer_wrk.stop()
        self.dauer_running = False
        self.btn_dauer.setText("Start Endurance Test")
        self.btn_dauer.set_variant("secondary")
        LIVE_STAGE_BUS.set_active(False)
    def _on_endurance_finished(self, data):
        self.dauer_running = False
        self.btn_dauer.setText("Dauertest starten")
        self.btn_dauer.set_variant("secondary")
        self.lbl_phase.setText("BEENDET")
        LIVE_STAGE_BUS.set_active(False)
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
class BlazeStageTestWorker(QObject):
    progress = Signal(int, int)
    finished = Signal()
    error = Signal(str)
    def __init__(self, backend, mode, steps, cycles, sleep_s):
        super().__init__()
        self.backend = backend
        self.mode = mode
        self.steps = steps
        self.cycles = cycles
        self.sleep_s = sleep_s
    def run(self):
        try:
            def _progress(cur, max_steps):
                self.progress.emit(int(cur), int(max_steps))
            if self.mode == "Y":
                self.backend.measure_axis("Y", self.steps, self.cycles, self.sleep_s, progress_cb=_progress)
            elif self.mode == "Z":
                self.backend.measure_axis("Z", self.steps, self.cycles, self.sleep_s, progress_cb=_progress)
            else:
                self.backend.measure_all_axis(self.steps, self.cycles, self.sleep_s, progress_cb=_progress)
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
class BlazeStageTestView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")
        self.backend = None
        self.backend_future = None
        self.backend_timer = QTimer(self)
        self.backend_timer.timeout.connect(self._poll_backend_ready)
        self.worker = None
        self.worker_thread = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._preview_embeds = {}
        self._preview_providers = {}
        self._live_chart_timer = QTimer(self)
        self._live_chart_timer.timeout.connect(self._update_live_tracking)
        self._live_chart_max = 250
        self._build_ui()
        self._init_backend_async()
    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        left_col = QVBoxLayout()
        left_col.setSpacing(10)
        setup_card = Card("Blaze Stage Test")
        setup_card.set_compact()
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        row = 0
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 50000)
        self.steps_spin.setValue(1000)
        self.steps_spin.setFixedHeight(28)
        form.addWidget(self._label("ANZAHL STEPS"), row, 0)
        form.addWidget(self.steps_spin, row, 1)
        row += 1
        self.cycles_spin = QSpinBox()
        self.cycles_spin.setRange(1, 20)
        self.cycles_spin.setValue(3)
        self.cycles_spin.setFixedHeight(28)
        form.addWidget(self._label("ANZAHL DURCHLAEUFE"), row, 0)
        form.addWidget(self.cycles_spin, row, 1)
        row += 1
        self.serial_input = QLineEdit()
        self.serial_input.setPlaceholderText("Seriennummer")
        self.serial_input.setFixedHeight(28)
        form.addWidget(self._label("SERIENNUMMER"), row, 0)
        form.addWidget(self.serial_input, row, 1)
        row += 1
        form.addWidget(self._section_label("MESSUNG"), row, 0, 1, 2)
        row += 1
        btn_row = QVBoxLayout()
        btn_row.setSpacing(6)
        self.btn_start_y = ModernButton("Messung starten Y", "primary")
        self.btn_start_z = ModernButton("Messung starten Z", "secondary")
        self.btn_start_yz = ModernButton("Messung starten YZ", "ghost")
        btn_row.addWidget(self.btn_start_y, alignment=Qt.AlignLeft)
        btn_row.addWidget(self.btn_start_z, alignment=Qt.AlignLeft)
        btn_row.addWidget(self.btn_start_yz, alignment=Qt.AlignLeft)
        form.addLayout(btn_row, row, 0, 1, 2)
        row += 1
        self.lbl_progress = QLabel("Fortschritt: 0/0")
        self.lbl_progress.setStyleSheet(f"color: {COLORS['text_muted']};")
        form.addWidget(self.lbl_progress, row, 0, 1, 2)
        row += 1
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
        form.addWidget(self.progress, row, 0, 1, 2)
        form.setColumnStretch(0, 0)
        form.setColumnStretch(1, 1)
        setup_card.add_layout(form)
        left_col.addWidget(setup_card)
        stage_card = Card("Positionierung")
        stage_card.set_compact()
        stl = QVBoxLayout()
        stl.setSpacing(6)
        self.btn_setup = ModernButton("Einrichtung Laseraufsatz", "secondary")
        self.btn_pos = ModernButton("Mess Positionierung", "ghost")
        self.btn_test_y = ModernButton("Test Y", "ghost")
        self.btn_test_z = ModernButton("Test Z", "ghost")
        stl.addWidget(self.btn_setup, alignment=Qt.AlignLeft)
        stl.addWidget(self.btn_pos, alignment=Qt.AlignLeft)
        stl.addWidget(self.btn_test_y, alignment=Qt.AlignLeft)
        stl.addWidget(self.btn_test_z, alignment=Qt.AlignLeft)
        stage_card.add_layout(stl)
        left_col.addWidget(stage_card)
        cam_card = Card("Kamera Setup")
        cam_card.set_compact()
        cam_layout = QVBoxLayout()
        cam_layout.setSpacing(6)
        self.btn_cam_y1 = ModernButton("CAM Y1", "ghost")
        self.btn_cam_y2 = ModernButton("CAM Y2", "ghost")
        self.btn_cam_z1 = ModernButton("CAM Z1", "ghost")
        self.btn_cam_z2 = ModernButton("CAM Z2", "ghost")
        cam_layout.addWidget(self.btn_cam_y1, alignment=Qt.AlignLeft)
        cam_layout.addWidget(self.btn_cam_y2, alignment=Qt.AlignLeft)
        cam_layout.addWidget(self.btn_cam_z1, alignment=Qt.AlignLeft)
        cam_layout.addWidget(self.btn_cam_z2, alignment=Qt.AlignLeft)
        cam_card.add_layout(cam_layout)
        left_col.addWidget(cam_card)
        left_col.addStretch()
        layout.addLayout(left_col, 1)
        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        status_card = Card("Status")
        status_card.set_compact()
        sl = QVBoxLayout()
        self.lbl_status = QLabel("Bereit")
        self.lbl_status.setStyleSheet(f"font-weight: 700; color: {COLORS['primary']};")
        sl.addWidget(self.lbl_status)
        status_card.add_layout(sl)
        right_col.addWidget(status_card)
        cam_preview_card = Card("Kamera Live")
        cam_preview_card.set_compact()
        cam_preview_layout = QGridLayout()
        cam_preview_layout.setSpacing(8)
        preview_axes = ["Y", "Y1", "Z", "Z1"]
        for i, axis in enumerate(preview_axes):
            preview = CameraWidget(lambda: None, start_immediately=False)
            preview.label.setMinimumHeight(120)
            preview.status.setText(f"{axis} | warte auf Hardware")
            cam_preview_layout.addWidget(preview, i // 2, i % 2)
            self._preview_embeds[axis] = preview
        cam_preview_card.add_layout(cam_preview_layout)
        right_col.addWidget(cam_preview_card, 2)
        live_card = Card("Live Tracking")
        live_card.set_compact()
        live_layout = QVBoxLayout()
        live_header = QHBoxLayout()
        self.lbl_live_state = QLabel("Live: aus")
        self.lbl_live_state.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        live_header.addWidget(self.lbl_live_state)
        live_header.addStretch()
        self.lbl_live_last = QLabel("---")
        self.lbl_live_last.setStyleSheet(f"font-family: {FONTS['mono']}; font-size: 11px; color: {COLORS['text']};")
        live_header.addWidget(self.lbl_live_last)
        live_layout.addLayout(live_header)
        self.live_chart = ModernChart(height=4)
        self.live_chart.ax.set_xlabel("Position / Index", color=COLORS['text_muted'])
        self.live_chart.ax.set_ylabel("Pixel", color=COLORS['text_muted'])
        self.line_x1, = self.live_chart.ax.plot([], [], color=COLORS['primary'], linewidth=2, label="Cam1 X")
        self.line_x2, = self.live_chart.ax.plot([], [], color=COLORS['secondary'], linewidth=2, label="Cam2 X")
        self.line_y1, = self.live_chart.ax.plot([], [], color=COLORS['success'], linewidth=2, label="Cam1 Y")
        self.line_y2, = self.live_chart.ax.plot([], [], color=COLORS['warning'], linewidth=2, label="Cam2 Y")
        leg = self.live_chart.ax.legend(loc='upper right', facecolor=COLORS['surface'], edgecolor=COLORS['border'], labelcolor=COLORS['text'])
        leg.get_frame().set_linewidth(1)
        live_layout.addWidget(self.live_chart)
        live_card.add_layout(live_layout)
        right_col.addWidget(live_card, 2)
        right_col.addStretch()
        layout.addLayout(right_col, 2)
        self.btn_start_y.clicked.connect(lambda: self._start_measurement("Y"))
        self.btn_start_z.clicked.connect(lambda: self._start_measurement("Z"))
        self.btn_start_yz.clicked.connect(lambda: self._start_measurement("YZ"))
        self.btn_setup.clicked.connect(lambda: self._submit_backend("endposition"))
        self.btn_pos.clicked.connect(lambda: self._submit_backend("position_y"))
        self.btn_test_y.clicked.connect(lambda: self._submit_backend("test_y"))
        self.btn_test_z.clicked.connect(lambda: self._submit_backend("test_z"))
        self.btn_cam_y1.clicked.connect(lambda: self._submit_backend("test_camera", "Y"))
        self.btn_cam_y2.clicked.connect(lambda: self._submit_backend("test_camera", "Y1"))
        self.btn_cam_z1.clicked.connect(lambda: self._submit_backend("test_camera", "Z"))
        self.btn_cam_z2.clicked.connect(lambda: self._submit_backend("test_camera", "Z1"))
        for btn in [
            self.btn_start_y,
            self.btn_start_z,
            self.btn_start_yz,
            self.btn_setup,
            self.btn_pos,
            self.btn_test_y,
            self.btn_test_z,
            self.btn_cam_y1,
            self.btn_cam_y2,
            self.btn_cam_z1,
            self.btn_cam_z2,
        ]:
            self._compact_button(btn)
        self._controls = [
            self.steps_spin,
            self.cycles_spin,
            self.serial_input,
            self.btn_start_y,
            self.btn_start_z,
            self.btn_start_yz,
            self.btn_setup,
            self.btn_pos,
            self.btn_test_y,
            self.btn_test_z,
            self.btn_cam_y1,
            self.btn_cam_y2,
            self.btn_cam_z1,
            self.btn_cam_z2,
        ]
    def _label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLORS['text_muted']}; border: none;")
        return lbl
    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {COLORS['text_muted']}; "
            "letter-spacing: 1px; border: none;"
        )
        return lbl
    def _compact_button(self, button, height=24):
        button.setFixedHeight(height)
        button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    def _init_backend_async(self):
        if self.backend_future:
            return
        self.lbl_status.setText("Initialisiere Hardware...")
        self.backend_future = self.executor.submit(blaze_stage_test.StageTestBackend)
        self.backend_timer.start(200)
    def _poll_backend_ready(self):
        if not self.backend_future:
            return
        if not self.backend_future.done():
            return
        self.backend_timer.stop()
        try:
            self.backend = self.backend_future.result()
        except Exception as exc:
            self.backend_future = None
            self.lbl_status.setText("Hardware Fehler")
            QMessageBox.critical(self, "Blaze Stage Test", f"Initialisierung fehlgeschlagen:\n{exc}")
            return
        self.backend_future = None
        cam_error = getattr(self.backend, "camera_error", None)
        if cam_error:
            self.lbl_status.setText("Bereit (ohne Kamera)")
            QMessageBox.warning(
                self,
                "Blaze Stage Test",
                f"Kamera nicht verfuegbar:\n{cam_error}\nDu kannst die Buehne ohne Kamera nutzen.",
            )
        else:
            self.lbl_status.setText("Bereit")
        self._set_controls_enabled(True)
        self._init_previews()
    def _init_previews(self):
        if not self._preview_embeds or self.backend is None:
            return
        self._preview_providers.clear()
        for axis, embed in self._preview_embeds.items():
            provider = StageTestLaserProvider(self.backend, axis)
            self._preview_providers[axis] = provider
            embed.set_provider(provider)
            embed.status.setText(f"{axis} | bereit (manuell starten)")
    def _ensure_backend(self):
        if self.backend is None:
            QMessageBox.warning(self, "Blaze Stage Test", "Hardware ist noch nicht bereit.")
            return False
        return True
    def _submit_backend(self, method_name, *args):
        if not self._ensure_backend():
            return
        method = getattr(self.backend, method_name, None)
        if method is None:
            QMessageBox.critical(self, "Blaze Stage Test", f"Backend-Methode fehlt: {method_name}")
            return
        if method_name == "test_camera":
            method(*args)
            if args:
                axis = args[0]
                embed = self._preview_embeds.get(axis)
                if embed is not None:
                    embed.start()
            return
        self.executor.submit(method, *args)
    def _start_measurement(self, mode):
        if self.worker_thread:
            return
        if not self._ensure_backend():
            return
        serial = self.serial_input.text().strip()
        if serial:
            self.backend.set_serial_number(serial)
        self.backend.clear_list()
        steps = self.steps_spin.value()
        cycles = self.cycles_spin.value()
        self.progress.setMaximum(self.backend.max_steps)
        self.progress.setValue(0)
        self.lbl_progress.setText(f"Fortschritt: 0/{self.backend.max_steps}")
        self.lbl_status.setText(f"Messung startet ({mode})")
        self._set_running_state(True)
        self._open_live_cameras(mode)
        self._start_live_tracking()
        self.worker = BlazeStageTestWorker(self.backend, mode, steps, cycles, 1)
        self.worker_thread = QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()
    def _set_controls_enabled(self, enabled):
        for ctrl in getattr(self, "_controls", []):
            ctrl.setEnabled(enabled)
    def _set_running_state(self, running):
        enabled = not running
        self.btn_start_y.setEnabled(enabled)
        self.btn_start_z.setEnabled(enabled)
        self.btn_start_yz.setEnabled(enabled)
    def _open_live_cameras(self, mode):
        if self.backend is None:
            return
        axis_map = {
            "Y": ["Y", "Y1"],
            "Z": ["Z", "Z1"],
            "YZ": ["Y", "Y1", "Z", "Z1"],
        }
        axes = set(axis_map.get(mode, []))
        for axis, embed in self._preview_embeds.items():
            if axis in axes:
                embed.start()
            else:
                embed.stop()
    def _close_live_cameras(self):
        for embed in self._preview_embeds.values():
            embed.stop()
    def _start_live_tracking(self):
        if not self._live_chart_timer.isActive():
            self._live_chart_timer.start(400)
        self.lbl_live_state.setText("Live: an")
    def _stop_live_tracking(self):
        if self._live_chart_timer.isActive():
            self._live_chart_timer.stop()
        self.lbl_live_state.setText("Live: aus")
    def _update_live_tracking(self):
        if self.backend is None:
            return
        try:
            snap = self.backend.get_live_snapshot()
        except Exception:
            return
        if not snap:
            return
        pos = snap.get("pos", [])
        x1 = snap.get("x_coord", [])
        y1 = snap.get("y_coord", [])
        x2 = snap.get("x_coord2", [])
        y2 = snap.get("y_coord2", [])
        n = min(len(pos), len(x1), len(y1), len(x2), len(y2))
        if n <= 0:
            return
        if n > self._live_chart_max:
            pos = pos[-self._live_chart_max:]
            x1 = x1[-self._live_chart_max:]
            y1 = y1[-self._live_chart_max:]
            x2 = x2[-self._live_chart_max:]
            y2 = y2[-self._live_chart_max:]
            n = len(pos)
        x_axis = pos if len(pos) == n else list(range(n))
        self.line_x1.set_data(x_axis, x1)
        self.line_x2.set_data(x_axis, x2)
        self.line_y1.set_data(x_axis, y1)
        self.line_y2.set_data(x_axis, y2)
        if x_axis:
            xmin = x_axis[0]
            xmax = x_axis[-1] if x_axis[-1] != x_axis[0] else x_axis[0] + 1
            self.live_chart.ax.set_xlim(xmin, xmax)
        y_min = min(min(x1, default=0), min(x2, default=0), min(y1, default=0), min(y2, default=0))
        y_max = max(max(x1, default=0), max(x2, default=0), max(y1, default=0), max(y2, default=0))
        if y_min == y_max:
            y_min -= 1
            y_max += 1
        pad = max(1.0, (y_max - y_min) * 0.1)
        self.live_chart.ax.set_ylim(y_min - pad, y_max + pad)
        self.live_chart.draw_idle()
        self.lbl_live_last.setText(f"X1:{x1[-1]:.1f} Y1:{y1[-1]:.1f} X2:{x2[-1]:.1f} Y2:{y2[-1]:.1f}")
    def _on_progress(self, cur, max_steps):
        self.progress.setMaximum(int(max_steps))
        self.progress.setValue(int(cur))
        self.lbl_progress.setText(f"Fortschritt: {cur}/{max_steps}")
    def _on_finished(self):
        self.lbl_status.setText("Fertig")
        self._set_running_state(False)
        self.worker_thread = None
        self.worker = None
        self._stop_live_tracking()
        self._close_live_cameras()
    def _on_error(self, msg):
        self.lbl_status.setText("Fehler")
        self._set_running_state(False)
        self.worker_thread = None
        self.worker = None
        self._stop_live_tracking()
        self._close_live_cameras()
        QMessageBox.critical(self, "Stage Test Fehler", msg)
    def hideEvent(self, event):
        self._stop_live_tracking()
        self._close_live_cameras()
        super().hideEvent(event)
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
    def showEvent(self, event):
        super().showEvent(event)
        if self.cam_embed is not None:
            self.cam_embed.start()
    def hideEvent(self, event):
        if self.cam_embed is not None:
            self.cam_embed.stop()
        super().hideEvent(event)
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
        self._cams = {}
        self._last_error = {}
        self._expo_initialized = False
        self._updating_expo = False
        self._cam_indices = [4, 5]
        self._primary_cam_idx = self._cam_indices[0] if self._cam_indices else 0
        self._cam_titles = ["Optikkorper Cam A", "Optikkorper Cam B"]
        self.cam_embed = None
        self._cam_buttons = {}
        self._current_cam_idx = self._primary_cam_idx
        self._no_frame_counts = {}
        self._last_log_ts = {}
        self._last_log_msg = {}
        self._cam_timeouts = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        monitor_card = Card("Optikkorper Live")
        monitor_card.setStyleSheet(monitor_card.styleSheet() + "border-color: #333;")
        monitor_card.setMinimumHeight(520)
        monitor_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.cam_embed = CameraWidget(self._get_frame, start_immediately=False)
        monitor_card.add_widget(self.cam_embed)
        layout.addWidget(monitor_card, 3)
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        cam_card = Card("KAMERAS")
        cam_layout = QGridLayout()
        cam_layout.setSpacing(6)
        for i, cam_idx in enumerate(self._cam_indices):
            title = self._cam_titles[i] if i < len(self._cam_titles) else f"Optikkorper CAM {i + 1}"
            btn = ModernButton(title, "secondary")
            btn.setMinimumHeight(34)
            btn.setFont(QFont(FONTS['ui'], 11, QFont.Bold))
            btn.clicked.connect(lambda _, idx=cam_idx: self._select_camera(idx))
            cam_layout.addWidget(btn, i // 2, i % 2)
            self._cam_buttons[cam_idx] = btn
        cam_card.add_layout(cam_layout)
        controls_row.addWidget(cam_card, 2)
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
        controls_row.addWidget(expo_card, 1)
        layout.addLayout(controls_row)
        save_row = QHBoxLayout()
        save_row.setSpacing(10)
        save_card = Card("SAVE")
        save_layout = QVBoxLayout()
        save_layout.setSpacing(6)
        self.btn_save_optik_pdf = ModernButton("Save PDF", "ghost")
        self.btn_save_optik_pdf.setMinimumHeight(30)
        self.btn_save_optik_pdf.clicked.connect(self._save_optikkorper_pdf)
        save_layout.addWidget(self.btn_save_optik_pdf)
        save_card.add_layout(save_layout)
        save_row.addWidget(save_card, 1)
        save_row.addStretch()
        layout.addLayout(save_row)
        layout.addStretch()
        self._update_button_styles()
    def _ensure_cam(self, idx):
        cam = self._cams.get(idx)
        if cam is None:
            cam = IdsCam(index=idx, set_min_exposure=False)
            self._cams[idx] = cam
        try:
            self._last_error[idx] = None
            if not self._expo_initialized:
                self._init_exposure_controls(cam)
            if idx not in self._cam_timeouts:
                try:
                    curr_us, _mn, _mx = cam.get_exposure_limits_us()
                    self._cam_timeouts[idx] = max(200, int(curr_us / 1000.0) + 200)
                except Exception:
                    self._cam_timeouts[idx] = 250
            return True
        except Exception as exc:
            self._last_error[idx] = str(exc)
            return False
    def _log_cam_status(self, idx: int, msg: str):
        now = time.monotonic()
        last_ts = self._last_log_ts.get(idx, 0.0)
        last_msg = self._last_log_msg.get(idx)
        if msg != last_msg or (now - last_ts) > 2.0:
            print(f"[Optikkoerper] Cam {idx}: {msg}")
            self._last_log_ts[idx] = now
            self._last_log_msg[idx] = msg
    def _get_frame(self):
        idx = self._current_cam_idx
        if not self._ensure_cam(idx):
            err = self._last_error.get(idx)
            msg = f"Kamera-Fehler: {err}" if err else f"Keine Kamera (Index {idx})"
            self._log_cam_status(idx, msg)
            return None, msg
        try:
            timeout_ms = self._cam_timeouts.get(idx, 250)
            frame = self._cams[idx].aquise_frame(timeout_ms=timeout_ms)
        except Exception as exc:
            self._last_error[idx] = str(exc)
            msg = str(exc)
            if "GC_ERR_TIMEOUT" in msg or "PEAK_RETURN_CODE_TIMEOUT" in msg:
                self._cam_timeouts[idx] = min(2000, self._cam_timeouts.get(idx, 250) + 200)
            try:
                self._cams[idx].shutdown()
            except Exception:
                pass
            self._cams.pop(idx, None)
            msg = f"Kamera-Fehler: {exc}"
            self._log_cam_status(idx, msg)
            return None, msg
        if frame is None:
            count = self._no_frame_counts.get(idx, 0) + 1
            self._no_frame_counts[idx] = count
            msg = f"Kein Bild (Cam {idx})"
            self._log_cam_status(idx, msg)
            return None, msg
        self._no_frame_counts[idx] = 0
        if idx in self._cam_timeouts:
            self._cam_timeouts[idx] = max(200, self._cam_timeouts[idx] - 100)
        msg = f"Cam {idx}"
        self._log_cam_status(idx, msg)
        return self._normalize_frame(frame), msg
    def _normalize_frame(self, frame):
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
    def _init_exposure_controls(self, cam):
        if cam is None:
            return
        try:
            curr_us, min_us, max_us = cam.get_exposure_limits_us()
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
        cam = self._cams.get(self._current_cam_idx)
        if cam is None:
            return
        try:
            cam.set_exposure_us(float(val_ms) * 1000.0)
        except Exception as exc:
            self._last_error[self._current_cam_idx] = str(exc)
            if self.cam_embed is not None:
                self.cam_embed.status.setText(f"EXPOSURE-Fehler: {exc}")
    def showEvent(self, event):
        super().showEvent(event)
        if self.cam_embed is not None:
            self.cam_embed.start()
    def hideEvent(self, event):
        if self.cam_embed is not None:
            self.cam_embed.stop()
        for cam in self._cams.values():
            try:
                cam.shutdown()
            except Exception:
                pass
        self._cams.clear()
        self._expo_initialized = False
        super().hideEvent(event)
    def _frame_to_rgb(self, frame):
        if frame is None:
            return None
        if isinstance(frame, QImage):
            qimg = frame.convertToFormat(QImage.Format_RGB888)
            h, w = qimg.height(), qimg.width()
            stride = qimg.bytesPerLine()
            buf = qimg.constBits()
            arr = np.frombuffer(buf, np.uint8, count=qimg.sizeInBytes())
            arr = arr.reshape((h, stride // 3, 3))[:, :w, :]
            return arr
        if isinstance(frame, np.ndarray):
            if frame.ndim == 2:
                return np.stack([frame] * 3, axis=-1)
            if frame.ndim == 3 and frame.shape[2] == 3:
                return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if frame.ndim == 3 and frame.shape[2] == 4:
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
        return None
    def _save_optikkorper_pdf(self):
        frame = self.cam_embed._last_frame if self.cam_embed is not None else None
        if frame is None:
            QMessageBox.warning(self, "PDF speichern", "Kein Bild verfuegbar.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = _latest_stage_outdir()
        default_dir.mkdir(parents=True, exist_ok=True)
        path_str = str(default_dir / f"optikkorper_capture_{ts}.pdf")
        with PdfPages(path_str) as pdf:
            fig = Figure(figsize=(11.69, 8.27), dpi=110, facecolor=COLORS['surface'])
            fig.text(0.02, 0.98, "Optikkoerper Capture", va="top", ha="left", fontsize=14, color=COLORS['text'])
            fig.text(0.02, 0.94, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     va="top", ha="left", fontsize=10, color=COLORS['text_muted'])
            ax = fig.add_subplot(1, 1, 1)
            try:
                i = self._cam_indices.index(self._current_cam_idx)
                title = self._cam_titles[i]
            except Exception:
                title = f"Cam {self._current_cam_idx}"
            ax.set_title(title, fontsize=10, color=COLORS['text'])
            rgb = self._frame_to_rgb(frame)
            if rgb is not None:
                ax.imshow(rgb)
            else:
                ax.text(0.5, 0.5, "Kein Bild", ha="center", va="center", color=COLORS['text_muted'])
            ax.axis("off")
            fig.tight_layout(rect=[0, 0, 1, 0.9])
            pdf.savefig(fig)
        QMessageBox.information(self, "PDF gespeichert", f"Report gespeichert:\n{path_str}")
    def _select_camera(self, idx: int):
        if idx == self._current_cam_idx:
            return
        try:
            cam = self._cams.get(idx)
            if cam is not None and bool(getattr(cam, "_dummy", False)):
                cam.shutdown()
                self._cams.pop(idx, None)
        except Exception:
            pass
        self._current_cam_idx = idx
        self._expo_initialized = False
        self._update_button_styles()
    def _update_button_styles(self):
        for idx, btn in self._cam_buttons.items():
            if idx == self._current_cam_idx:
                btn.set_variant("primary")
            else:
                btn.set_variant("secondary")
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
        self.lbl_ipc_source = QLabel("Source: DB")
        self.lbl_ipc_source.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        tl.addWidget(self.lbl_ipc_source)
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
        # Live StageTest wiring (DB-backed)
        self._stage_live_active = False
        self._stage_test_start_dt = None
        LIVE_STAGE_BUS.active_changed.connect(self._on_live_stage_active)
        LIVE_STAGE_BUS.data_updated.connect(self._on_live_stage_event)
        # Initial Fetch
        QTimer.singleShot(500, self.refresh_data)
    def refresh_data(self):
        if not self.isVisible():
            return
        source_label = "GW" if db.is_on_gateway_wifi() else "DB"
        self.lbl_ipc_source.setText(f"Source: {source_label}")
        source = self.combo_source.currentText()
        if source == "stage_test" and self._stage_live_active:
            df, ok = db.fetch_test_data(
                "gitterschieber_tool",
                limit=200,
                prefer_gateway=db.is_on_gateway_wifi(),
            )
            if ok and df is not None:
                df = self._filter_stage_df(df)
                self._plot_stage_live_from_db(df)
            return
        def fetch_task():
            try:
                # Fetch more data for statistics (e.g. 100)
                df, ok = db.fetch_test_data(
                    source,
                    limit=100,
                    prefer_gateway=db.is_on_gateway_wifi(),
                )
                if ok:
                    return df
            except: pass
            return None
        # Run in thread or just quick fetch? fetch_test_data is sync but usually fast.
        # For responsiveness, lets assume it returns fast enough or do threaded if needed.
        # Given limitations, we do it in main thread to simplify plotting updates or use a helper
        # But previous Dashboard used a thread. Let's do simple safely here for now.
        df = fetch_task()
        if df is not None:
            if source == "stage_test" and self._stage_live_active:
                df = self._filter_stage_df(df)
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
    def _on_live_stage_active(self, active: bool):
        self._stage_live_active = active
        if not active:
            self._stage_test_start_dt = None
        if self.isVisible() and self.combo_source.currentText() == "stage_test":
            self.refresh_data()
    def _on_live_stage_event(self, payload: dict):
        if payload.get("event") == "stage_test_start":
            self._stage_test_start_dt = payload.get("ts")
            if self.isVisible() and self.combo_source.currentText() == "stage_test":
                self.refresh_data()
    def _filter_stage_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self._stage_test_start_dt or df is None or df.empty:
            return df
        time_col = "StartTest" if "StartTest" in df.columns else None
        if not time_col and "EndTest" in df.columns:
            time_col = "EndTest"
        if not time_col:
            return df
        ts = pd.to_datetime(df[time_col], errors="coerce")
        start_ts = pd.Timestamp(self._stage_test_start_dt)
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_localize(None)
        if start_ts.tzinfo is not None:
            start_ts = start_ts.tz_localize(None)
        return df.loc[ts >= start_ts].copy()
    def _plot_stage_live_from_db(self, df):
        self.chart1.ax.cla()
        self.chart2.ax.cla()
        x_vals = pd.to_numeric(df.get("particle_count", pd.Series(dtype=float)), errors='coerce').fillna(0)
        y_vals = pd.to_numeric(df.get("justage_angle", pd.Series(dtype=float)), errors='coerce').fillna(0)
        # Order oldest->newest and build real time axis (minutes from first)
        time_col = None
        for c in ("StartTest", "EndTest"):
            if c in df.columns:
                time_col = c
                break
        if time_col:
            df_sorted = df.copy()
            df_sorted[time_col] = pd.to_datetime(df_sorted[time_col], errors="coerce")
            df_sorted = df_sorted.sort_values(by=time_col, ascending=True)
            x_vals = pd.to_numeric(df_sorted.get("particle_count", pd.Series(dtype=float)), errors='coerce').fillna(0)
            y_vals = pd.to_numeric(df_sorted.get("justage_angle", pd.Series(dtype=float)), errors='coerce').fillna(0)
            times = df_sorted[time_col].fillna(method="ffill")
            if not times.empty and pd.notna(times.iloc[0]):
                t0 = times.iloc[0]
                t = (times - t0).dt.total_seconds().fillna(0) / 60.0
            else:
                t = pd.Series(np.arange(len(x_vals)), dtype=float)
        else:
            t = pd.Series(np.arange(len(x_vals)), dtype=float)
        def style(ax, title, xl, yl):
            ax.set_title(title, color=COLORS['text'])
            ax.set_xlabel(xl, color=COLORS['text_muted'])
            ax.set_ylabel(yl, color=COLORS['text_muted'])
            ax.grid(True, linestyle='--', alpha=0.3, color=COLORS['border'])
        style(self.chart1.ax, "Echtzeit-Abweichung (X vs Y)", "Time [min]", "Error [µm]")
        self.chart1.ax.plot(list(t), list(x_vals), color=COLORS['primary'], linewidth=2, label="Fehler X")
        self.chart1.ax.plot(list(t), list(y_vals), color=COLORS['secondary'], linewidth=2, label="Fehler Y")
        self.chart1.ax.legend(loc='upper right', facecolor=COLORS['surface'], edgecolor=COLORS['border'], labelcolor=COLORS['text'])
        y_max = max(
            0.5,
            max(abs(min(x_vals, default=0.0)), abs(max(x_vals, default=0.0))),
            max(abs(min(y_vals, default=0.0)), abs(max(y_vals, default=0.0))),
        )
        y_pad = max(0.2, y_max * 0.15)
        self.chart1.ax.set_ylim(-y_max - y_pad, y_max + y_pad)
        if len(t) > 0:
            t_min = float(t.iloc[0]) if hasattr(t, "iloc") else float(t[0])
            t_max = float(t.iloc[-1]) if hasattr(t, "iloc") else float(t[-1])
            if t_min == t_max:
                t_max = t_min + 1.0
            self.chart1.ax.set_xlim(t_min, t_max)
        self.chart2.ax.set_title("Live Stage Control (DB Sync)", color=COLORS['text_muted'])
        self.chart2.ax.set_axis_off()
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
            self.cam_embed = CameraWidget(self._get_frame, start_immediately=False)
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
            self.cam_embed.open()
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
        self.combo_type.addItems(["Card", "ModernButton", "QLabel", "QLineEdit", "CameraWidget"])
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
        if comp_type == "CameraWidget":
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
        elif comp_type in ("CameraWidget", "LiveCamEmbed"):
            provider = CAMERA_REGISTRY.get_provider(cam_idx)
            card = Card(title=f"Camera {cam_idx}")
            cam_embed = CameraWidget(provider, start_immediately=False)
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
                padding-left: 16px;
                border: 1px solid transparent;
                border-radius: 8px;
                font-weight: 600;
                font-size: 11px;
                letter-spacing: 1px;
                text-transform: uppercase;
                margin-bottom: 4px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['surface_light']};
                color: {COLORS['text']};
                border: 1px solid transparent;
            }}
            QPushButton:checked {{
                background-color: {hex_to_rgba(COLORS['primary'], 0.08)};
                color: {COLORS['primary']};
                border: 1px solid {hex_to_rgba(COLORS['primary'], 0.2)};
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
        self.sidebar.setStyleSheet(f"background-color: {COLORS['bg']}; border-right: 1px solid {COLORS['border']};")
        self.side_layout = QVBoxLayout(self.sidebar) # Made self.side_layout for access in _filter_navigation
        self.side_layout.setContentsMargins(16, 24, 16, 24)
        # Brand
        brand = QLabel("Resolve Production Tool")
        brand.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLORS['primary']}; letter-spacing: 1.5px; text-transform: uppercase; padding-left: 12px; margin-bottom: 12px;")
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
        # Keep device selection in sync between Dashboard and IPC views.
        def _sync_combo(src, dst):
            text = src.currentText()
            idx = dst.findText(text)
            if idx >= 0 and idx != dst.currentIndex():
                dst.blockSignals(True)
                dst.setCurrentIndex(idx)
                dst.blockSignals(False)
        self.dashboard.combo_testtype.currentIndexChanged.connect(
            lambda: _sync_combo(self.dashboard.combo_testtype, self.ipc_view.combo_source)
        )
        self.ipc_view.combo_source.currentIndexChanged.connect(
            lambda: _sync_combo(self.ipc_view.combo_source, self.dashboard.combo_testtype)
        )
        _sync_combo(self.dashboard.combo_testtype, self.ipc_view.combo_source)
        self.btn_dash = add_nav("Dashboard", self.dashboard)
        self.btn_ipc = add_nav("In Process Control", self.ipc_view)
        # Connect Dashboard Button to IPC View
        self.dashboard.btn_goto_ipc.clicked.connect(lambda: self.btn_ipc.click())
        wf_header = self._make_nav_section("WORKFLOWS")
        self.side_layout.addWidget(wf_header)
        self.btn_ztrieb = add_nav("Z-Trieb", ZTriebView())
        self.btn_af = add_nav("Autofocus", AutofocusView())
        add_nav("Optikkorper", OptikkoerperView())
        self.stage_view = LazyView(StageControlView, "Stage Control")
        add_nav("Stage Control", self.stage_view)
        self.blaze_stage_view = LazyView(BlazeStageTestView, "Blaze Stage Test")
        add_nav("Blaze Stage Test", self.blaze_stage_view)
        add_nav("Gitterschieber", GitterschieberView())
        add_nav("Laserscan", LaserscanView())
        # Load Dynamic Tools
        dynamic_tools = UI_CONFIG.get("dynamic_tools", {})
        if dynamic_tools:
            dyn_header = self._make_nav_section("CUSTOM TOOLS")
            self.side_layout.addWidget(dyn_header)
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
        avatar.setStyleSheet(f"background-color: {COLORS['primary']}; color: {COLORS['bg']}; border-radius: 18px; font-weight: 600; letter-spacing: 0.5px;")
        user_label = QLabel("M. Zschach\nOperator")
        user_label.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {COLORS['text']}; margin-left: 8px;")
        user_row.addWidget(avatar)
        user_row.addWidget(user_label)
        user_row.addStretch()
        user_frame = QFrame()
        user_frame.setStyleSheet(f"background-color: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 8px;")
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
        self.page_title.setStyleSheet("font-size: 18px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase;")
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search Serial Number...")
        self.search_bar.setFixedWidth(280)
        self.search_bar.setStyleSheet(f"""
            border-radius: 16px; 
            background-color: {COLORS['surface']};
            padding-left: 14px;
        """)
        self.btn_open_stage_folder = QToolButton()
        self.btn_open_stage_folder.setIcon(_make_folder_icon(COLORS['text_muted'], 16))
        self.btn_open_stage_folder.setToolTip("Stage-Ordner oeffnen")
        self.btn_open_stage_folder.setCursor(Qt.PointingHandCursor)
        self.btn_open_stage_folder.setFixedSize(28, 28)
        self.btn_open_stage_folder.setStyleSheet(
            f"QToolButton {{ border: 1px solid transparent; border-radius: 6px; "
            f"background: transparent; color: {COLORS['text']}; }}"
            f"QToolButton:hover {{ background: {COLORS['surface_light']}; "
            f"border: 1px solid {COLORS['border']}; }}"
        )
        self.btn_open_stage_folder.clicked.connect(self._open_stage_data_folder)
        hl.addWidget(self.page_title)
        hl.addStretch()
        hl.addWidget(self.search_bar)
        hl.addWidget(self.btn_open_stage_folder)
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
    def _open_stage_data_folder(self):
        if hasattr(self, "stage_view") and self.stage_view is not None:
            view = self.stage_view.view if hasattr(self.stage_view, "view") else self.stage_view
            if view is not None and hasattr(view, "_last_outdir"):
                path = view._last_outdir if view._last_outdir else _latest_stage_outdir()
            else:
                path = _latest_stage_outdir()
        else:
            path = _latest_stage_outdir()
        try:
            if sys.platform == 'win32':
                os.startfile(str(path.resolve()))
            else:
                subprocess.run(['xdg-open', str(path.resolve())])
        except Exception as e:
            QMessageBox.warning(self, "Ordner oeffnen", f"Konnte Ordner nicht oeffnen:\n{e}")
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
                dyn_header = self._make_nav_section("CUSTOM TOOLS")
                # Insert before the "+" button
                idx = self.side_layout.indexOf(self.btn_new_tool)
                self.side_layout.insertWidget(idx, dyn_header)
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
    def _make_nav_section(self, title: str) -> QWidget:
        """Create a clear non-clickable section divider for the sidebar."""
        wrapper = QWidget()
        wrapper.setObjectName(f"NavSection_{title.replace(' ', '_')}")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(12, 12, 12, 8)
        wl.setSpacing(6)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-size: 9px; font-weight: 600; "
            f"letter-spacing: 1.2px; text-transform: uppercase;"
        )
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        line.setStyleSheet(f"color: {COLORS['border']}; background-color: {COLORS['border']};")
        line.setFixedHeight(1)
        wl.addWidget(lbl)
        wl.addWidget(line)
        return wrapper
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
    font.setPixelSize(12)
    app.setFont(font)
    window = MainWindow()
    # Store reference to filter on the app to prevent GC
    app.studio_filter = GlobalEditFilter(window)
    app.installEventFilter(app.studio_filter)
    window.showMaximized()
    sys.exit(app.exec())

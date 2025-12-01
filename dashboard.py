import sys
import datetime
import json
import io
import pandas as pd

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QGridLayout,
    QComboBox, QLineEdit, QTableView, QHBoxLayout, QMessageBox,
    QCheckBox, QPushButton, QFormLayout, QStackedWidget, QGroupBox
)
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtCore import Qt, QTimer, QSortFilterProxyModel, QAbstractTableModel

#DB-Module
import commonIE
from commonIE import dbConnector
import ie_Framework.miltenyiBarcode  # Barcode-Objekt

#Anzahl Datensätze 
LIMIT_ROWS = 50  
TESTTYPE_DB_MAP = {
    "kleberoboter": "kleberoboter",
    "gitterschieber_tool": "gitterschieber_tool",
    "stage_test": "stage_test",
}

ACCENT   = "#ff2740"
BG       = "#0b0b0f"
BG_ELEV  = "#121218"
FG       = "#e8e8ea"
FG_MUTED = "#9ea0a6"
BORDER   = "#222230"
HOVER    = "#1b1b26"

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

# =============================================================================
# Pandas -> Qt Model
# =============================================================================
class PandasModel(QAbstractTableModel):
    """
    Minimaler Wrapper, um einen pandas.DataFrame in einer QTableView anzuzeigen.
    """
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
                return "✔️" if bool(value) else "❌"

            # Datumsformat
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
            else:
                return str(section)
        return None

# =============================================================================
# Dashboard
# =============================================================================
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
        self.update_data()  # initial load

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_data)
        self.timer.start(5000)  # 5s Refresh

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

    # -----------------------------------------------------------------
    # UI Aufbau
    # -----------------------------------------------------------------
    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(18, 18, 18, 16)
        main_layout.setSpacing(14)

        # Titel
        title = QLabel("Live Produktions-Dashboard")
        title.setFont(QFont("Inter", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {FG};")

        # KPIs
        self.lbl_total = self.create_kpi("...", "Total Output")
        self.lbl_ok = self.create_kpi("...", "OK-Anteil")
        self.lbl_last = self.create_kpi("...", "Letzter Status")

        kpi_layout = QGridLayout()
        kpi_layout.addWidget(self.lbl_total, 0, 0)
        kpi_layout.addWidget(self.lbl_ok, 0, 1)
        kpi_layout.addWidget(self.lbl_last, 0, 2)

        # Auswahl + Filter
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

        # Tabelle
        self.table = QTableView()
        self.table.setSortingEnabled(True)
        self.table.setModel(self.proxy_model)
        self.table.setAlternatingRowColors(True)

        # Dateneingabebereich (unten)
        entry_group = QGroupBox("Neuen Datensatz senden")
        entry_layout = QVBoxLayout()

        # Gemeinsame Felder (Barcode & User)
        common_form = QFormLayout()
        self.le_barcode = QLineEdit()
        self.le_barcode.setPlaceholderText("Barcode scannen oder einfügen...")
        self.le_user = QLineEdit()
        self.le_user.setPlaceholderText("User / Mitarbeiter-ID...")
        common_form.addRow("Barcode:", self.le_barcode)
        common_form.addRow("User:", self.le_user)

        # StackedWidget für testtypspezifische Felder
        self.entry_stack = QStackedWidget()

        # --- Panel: kleberoboter ---
        w_kleber = QWidget()
        f_kleber = QFormLayout()
        self.cb_ok = QCheckBox("Test OK?")
        f_kleber.addRow(self.cb_ok)
        w_kleber.setLayout(f_kleber)

        # --- Panel: gitterschieber_tool ---
        w_git = QWidget()
        f_git = QFormLayout()
        self.le_particle_count = QLineEdit()
        self.le_particle_count.setPlaceholderText("Anzahl Partikel")
        self.le_justage_angle = QLineEdit()
        self.le_justage_angle.setPlaceholderText("Justage-Winkel (°)")
        f_git.addRow("Particle Count:", self.le_particle_count)
        f_git.addRow("Justage Angle:", self.le_justage_angle)
        w_git.setLayout(f_git)

        # --- Panel: stage_test ---
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

        # Stacked Reihenfolge muss zu Combo passen!
        self.entry_stack.addWidget(w_kleber)  # index 0
        self.entry_stack.addWidget(w_git)     # index 1
        self.entry_stack.addWidget(w_stage)   # index 2

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_send = QPushButton("Senden")
        self.btn_clear = QPushButton("Felder leeren")
        btn_row.addStretch()
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_send)

        # Zusammenbauen
        entry_layout.addLayout(common_form)
        entry_layout.addWidget(self.entry_stack)
        entry_layout.addLayout(btn_row)
        entry_group.setLayout(entry_layout)

        # Button-Connects
        self.btn_send.clicked.connect(self.send_current_entry)
        self.btn_clear.clicked.connect(self.clear_entry_fields)

        # Alles in Main-Layout
        main_layout.addWidget(title)
        main_layout.addLayout(kpi_layout)
        main_layout.addLayout(controls)
        main_layout.addWidget(self.table)
        main_layout.addWidget(entry_group)
        self.setLayout(main_layout)

        # Filter-Signal nur einmal verbinden
        if not self._filter_connected:
            self.filter_input.textChanged.connect(self.proxy_model.setFilterFixedString)
            self._filter_connected = True

        # Anfangszustand
        self.on_testtype_changed(0)

    # -----------------------------------------------------------------
    # UI-Helfer
    # -----------------------------------------------------------------
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
        """Wenn Testtyp gewechselt wird: Formular-Panel wechseln & Daten neu laden."""
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

    # -----------------------------------------------------------------
    # DB-Verbindung
    # -----------------------------------------------------------------
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

    # -----------------------------------------------------------------
    # Daten aus DB holen
    # -----------------------------------------------------------------
    def fetch_data_from_db(self, testtype: str, limit: int = LIMIT_ROWS) -> pd.DataFrame:
        if not self._open_conn():
            return pd.DataFrame()

        try:
            raw = self.conn.getLastTests(limit, testtype)
        except Exception as e:
            QMessageBox.warning(self, "DB Fehler", f"Datenabruf fehlgeschlagen:\n{e}")
            self._close_conn()
            return pd.DataFrame()

        self._close_conn()

        # Direkt DF?
        if isinstance(raw, pd.DataFrame):
            df = raw.copy()
        elif isinstance(raw, str):
            df = self._parse_string_to_df(raw)
        else:
            df = pd.DataFrame({"_raw": [str(raw)]})

        if "ok" not in df.columns:
            df["ok"] = pd.NA

        for col in ("StartTest", "EndTest"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        return df

    def _parse_string_to_df(self, raw: str) -> pd.DataFrame:
        try:
            obj = json.loads(raw)
            if isinstance(obj, list):
                return pd.DataFrame(obj)
            elif isinstance(obj, dict):
                return pd.DataFrame([obj])
        except Exception:
            pass
        try:
            return pd.read_csv(io.StringIO(raw))
        except Exception:
            pass
        return pd.DataFrame({"raw": [raw]})

    # -----------------------------------------------------------------
    # Dashboard-Daten aktualisieren
    # -----------------------------------------------------------------
    def update_data(self):
        key = self.combo_testtype.currentText()
        testtype = TESTTYPE_DB_MAP.get(key, key)

        df = self.fetch_data_from_db(testtype, LIMIT_ROWS)
        if "StartTest" in df.columns:
            df = df.sort_values("StartTest", ascending=False).reset_index(drop=True)

        self.data = df

        total = len(df)
        if "ok" in df.columns and total > 0:
            ok_bool = df["ok"].fillna(False).astype(bool)
            ok_count = ok_bool.sum()
            ok_ratio = int((ok_count / total) * 100)
            last_result = "✔️" if bool(ok_bool.iloc[0]) else "❌"
        else:
            ok_ratio = 0
            last_result = "N/A"

        self.lbl_total.value_label.setText(str(total))
        self.lbl_ok.value_label.setText(f"{ok_ratio}%")
        self.lbl_last.value_label.setText(last_result)

        model = PandasModel(df)
        self.proxy_model.setSourceModel(model)

    # -----------------------------------------------------------------
    # Daten senden (Eingabe -> DB)
    # -----------------------------------------------------------------
    def send_current_entry(self):
        key = self.combo_testtype.currentText()
        testtype = TESTTYPE_DB_MAP.get(key, key)

        # Barcode & User
        barcode_str = self.le_barcode.text().strip() or "0"
        user_str = self.le_user.text().strip() or "unknown"

        startTime = datetime.datetime.now()
        endTime = datetime.datetime.now()

        # Testtyp-spezifische Payload bauen
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

        # Barcode-Objekt bauen
        try:
            barcode_obj = ie_Framework.miltenyiBarcode.mBarcode(barcode_str)
        except Exception as e:
            QMessageBox.warning(self, "Barcode Fehler", f"Konnte Barcode nicht erzeugen:\n{e}")
            return

        # Senden
        if not self._open_conn():
            return
        try:
            resp = self.conn.sendData(
                startTime, endTime, 0, testtype,  # resulttype_id = 0 (wie in deinem Code)
                payload,
                barcode_obj,
                user_str
            )
            print("sendData response:", resp)
        except Exception as e:
            QMessageBox.critical(self, "Sendefehler", f"Senden fehlgeschlagen:\n{e}")
        finally:
            self._close_conn()

        # Refresh Dashboard nach Senden
        self.update_data()
        self.clear_entry_fields()

    # -----------------------------------------------------------------
    # Parsing-Helfer
    # -----------------------------------------------------------------
    def _safe_int(self, txt):
        try:
            return int(float(txt.replace(",", ".")))
        except Exception:
            return 0

    def _safe_float(self, txt):
        try:
            return float(txt.replace(",", "."))
        except Exception:
            return 0.0

    # -----------------------------------------------------------------
    # Aufräumen
    # -----------------------------------------------------------------
    def closeEvent(self, event):
        self.timer.stop()
        self._close_conn()
        super().closeEvent(event)


# =============================================================================
# main
# =============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    dash = Dashboard()
    dash.resize(1200, 800)
    dash.show()
    sys.exit(app.exec())

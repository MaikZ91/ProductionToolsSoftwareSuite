"""Z-Trieb (Objektivringversteller) integration for the Stage-Toolbox GUI."""

from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import os
from typing import Callable, Optional
from pathlib import Path
import ctypes

# Make sure the vendor MCS package (in this repo) is importable even with the space in the folder name.
_HERE = Path(__file__).resolve().parent
for _cand in (
    _HERE / "Miltenyi CAN System (MCS)",
    _HERE.parent / "Miltenyi CAN System (MCS)",
):
    if (_cand / "mcs").exists() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))
        break

# Add local PCANBasic build (if present) to the loader path so libpcanbasic.so can be found without system install.
_PCAN_LIB_DIR = _HERE / "peak-linux-driver-8.20.0" / "libpcanbasic" / "pcanbasic" / "lib"
if _PCAN_LIB_DIR.exists():
    _ld_paths = os.environ.get("LD_LIBRARY_PATH", "")
    _ld_list = [p for p in _ld_paths.split(os.pathsep) if p]
    if str(_PCAN_LIB_DIR) not in _ld_list:
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join([str(_PCAN_LIB_DIR)] + _ld_list)
    try:
        ctypes.cdll.LoadLibrary(str(_PCAN_LIB_DIR / "libpcanbasic.so"))
    except Exception:
        pass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QPushButton,
    QSpinBox,
    QLabel,
    QTextEdit,
    QMessageBox,
    QGroupBox,
)

try:
    import mcs  # type: ignore
except Exception as exc:  # pragma: no cover - optional dependency
    mcs = None
    _MCS_IMPORT_ERROR = exc
else:
    _MCS_IMPORT_ERROR = None


log = logging.getLogger(__name__)


class _DummyDriver:
    """Minimal stand-in for the MCS driver so the GUI works without hardware."""

    def __init__(self, logger: Callable[[str], None]):
        self._log = logger
        self._pos = 0
        self._port = {1: 0}
        self._move_speed = 0

    def set_parameter(self, idx, value):
        self._log(f"[SIM] set_parameter({idx}, {value})")

    def reset(self, reset_mask: int | None = None):
        self._log(f"[SIM] reset(mask={reset_mask})")

    def init(self):
        self._log("[SIM] init driver")

    def set_port(self, port, value, length=1):
        self._port[port] = value
        self._log(f"[SIM] set_port({port}) -> {value}")

    def get_port(self, port):
        return self._port.get(port, 0)

    def move_rel(self, position, speed):
        self._pos += int(position)
        self._move_speed = int(speed)
        self._log(f"[SIM] move_rel {position} @ {speed}")
        time.sleep(0.05)
        self._move_speed = 0

    def move_abs(self, position, speed):
        self._pos = int(position)
        self._move_speed = int(speed)
        self._log(f"[SIM] move_abs -> {position} @ {speed}")
        time.sleep(0.05)
        self._move_speed = 0

    def get_move(self):
        return (self._pos, self._move_speed)

    def stop(self):
        self._log("[SIM] stop")
        self._move_speed = 0

    def set_port_direction(self, port, value):
        self._log(f"[SIM] set_port_direction({port}, {value})")

    def move(self, position, speed, cmd_mode=0):
        self.move_abs(position, speed)


class ZTriebController(QObject):
    """Encapsulates Objektivringversteller operations."""

    logMessage = Signal(str)
    runCounterChanged = Signal(int)
    hardwareAvailable = Signal(bool)

    lbPort = 1
    moveSpeed = 800
    homePosition = 0
    freerange_steps = 172
    one_mm_pos = 400
    highres_pos = 2040
    address = 0x45C

    def __init__(self):
        super().__init__()
        self._bus = None
        self._driver = None
        self._dummy = False  # Keinen Simulationsmodus mehr nutzen

    def connect(self):
        """(Re-)Establish the connection to the Z-Trieb driver."""
        self._connect()

    def _connect(self):
        self._emit_log(f"[DEBUG] Verbinde Z-Trieb @ CAN-Adresse 0x{self.address:X} …")
        if mcs is None:
            self.hardwareAvailable.emit(False)
            self._emit_log(
                "[ERROR] MCS-Bibliothek nicht verfügbar – Objektivringversteller nicht gefunden."
            )
            return
        try:
            if self._bus is not None:
                try:
                    self._bus.close()
                except Exception:
                    pass
                self._bus = None
            self._driver = None
            self._bus = mcs.get_mcs(register="scan")
            try:
                bus_info = getattr(self._bus, "info", None)
                if callable(bus_info):
                    self._emit_log(f"[DEBUG] MCS Info: {bus_info()}")
            except Exception:
                pass
            try:
                registered = [
                    f"0x{dev.can_id:X}"
                    for dev in (self._bus.get_registered_devices() or [])
                ]
            except Exception:
                registered = []
            self._emit_log(
                f"[DEBUG] Registrierte Geräte: {', '.join(registered) if registered else 'keine'}"
            )
            self._driver = self._bus.get_device(self.address)
            self.hardwareAvailable.emit(True)
            self._emit_log(
                "[INFO] MCS-Bus verbunden – Z-Trieb Treiber aktiv "
                f"(Typ: {type(self._driver).__name__})."
            )
        except Exception as exc:  # pragma: no cover - hardware specific
            self.hardwareAvailable.emit(False)
            self._emit_log(
                f"[ERROR] Objektivringversteller nicht gefunden ({exc})."
            )

    def _ensure_driver(self) -> bool:
        """Try to reconnect if the driver is missing."""
        if self._driver is not None:
            return True
        self._emit_log("[WARN] Kein Treiber verbunden – versuche Neuverbindung …")
        self._connect()
        if self._driver is None:
            self._emit_log("[ERROR] Z-Trieb Treiber weiterhin nicht verfügbar.")
            return False
        return True

    # Utility
    def _emit_log(self, text: str):
        self.logMessage.emit(text)

    def shutdown(self):
        try:
            if self._bus:
                self._bus.close()
                self._emit_log("[INFO] MCS-Bus geschlossen.")
        except Exception:
            pass

    # High level commands
    def goto_ref(self):
        d = self._driver
        if d is None and not self._ensure_driver():
            return
        d = self._driver
        self._emit_log("Referenzfahrt gestartet…")
        try:
            d.set_parameter(0, 1)
            d.reset()
            d.init()
            d.set_parameter(2, 300)
            d.set_parameter(3, 30)
            d.set_parameter(4, 40)
            d.reset()
            d.init()
            start_time = time.time()
            d.set_port(port=255, value=1, length=1)
            d.move_rel(position=int(-16000), speed=self.moveSpeed)
            while d.get_port(port=self.lbPort) == 0:
                time.sleep(0.1)
            d.stop()
            end_time = time.time()
            self._emit_log(
                f"Hall-Sensor nach {end_time - start_time:.1f} s erreicht – fahre frei."
            )
            d.move_rel(position=int(16000), speed=self.moveSpeed)
            while d.get_port(port=self.lbPort) == 1:
                time.sleep(0.1)
            d.stop()
            time.sleep(0.5)
            d.move_rel(position=int(800), speed=int(self.moveSpeed / 2))
            time.sleep(0.5)
            d.reset(reset_mask=0x08)
            d.init()
            d.set_port(port=255, value=0, length=1)
            d.move_rel(position=int(-self.freerange_steps / 2), speed=self.moveSpeed)
            time.sleep(0.5)
            d.reset(reset_mask=0x08)
            d.init()
            pos, speed = d.get_move()
            self._emit_log(f"Referenzfahrt abgeschlossen: Position {pos}, Speed {speed}.")
        except Exception as exc:
            self._emit_log(f"[ERROR] goto_ref: {exc}")

    def goto_pos(self, slot_position: int):
        d = self._driver
        if d is None and not self._ensure_driver():
            return
        d = self._driver
        self._emit_log(f"Fahre zu Position {slot_position}…")
        try:
            moveData = d.get_move()
            distance = slot_position - moveData[0]
            d.move_abs(position=slot_position, speed=self.moveSpeed)
            moveData = d.get_move()
            while moveData[1] != 0:
                time.sleep(0.1)
                moveData = d.get_move()
            jerk = int(self.freerange_steps / 2)
            if distance > 0:
                d.move_rel(position=-jerk, speed=self.moveSpeed)
            elif distance < 0:
                d.move_rel(position=jerk, speed=self.moveSpeed)
            time.sleep(0.2)
            pos = d.get_move()[0]
            self._emit_log(f"Position erreicht → {pos}.")
        except Exception as exc:
            self._emit_log(f"[ERROR] goto_pos: {exc}")

    def goto_home(self):
        self.goto_pos(self.homePosition)

    def goto_1mm(self):
        self.goto_pos(self.one_mm_pos)

    def goto_highres(self):
        self.goto_pos(self.highres_pos)

    def run_dauertest(self, stop_event: threading.Event):
        positions = [self.homePosition, self.one_mm_pos, self.highres_pos]
        run_procedure = [0, 1, 2, 1, 0]
        counter = 0
        waittime = 2
        self._emit_log("Starte Dauertest…")
        try:
            while not stop_event.is_set():
                self._emit_log(f"Run #{counter}")
                self.goto_ref()
                if stop_event.is_set():
                    break
                time.sleep(waittime)
                for idx in run_procedure:
                    self.goto_pos(positions[idx])
                    if stop_event.is_set():
                        break
                    time.sleep(waittime)
                counter += 1
                self.runCounterChanged.emit(counter)
        except Exception as exc:
            self._emit_log(f"[ERROR] Dauertest: {exc}")
        finally:
            self._emit_log("Dauertest beendet.")


class ZTriebWidget(QWidget):
    """PySide6 widget that exposes the Objektivringversteller controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = ZTriebController()
        self.controller.logMessage.connect(self._append_log)
        self.controller.runCounterChanged.connect(self._update_counter)
        self.controller.hardwareAvailable.connect(self._on_hw_state)
        self.controller.connect()

        self.executor = ThreadPoolExecutor(max_workers=1)
        self._dauer_future = None
        self._stop_event = None

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        btn_row = QHBoxLayout()
        self.btnRef = QPushButton("Referenzfahrt")
        self.btnHome = QPushButton("Home")
        self.btn1mm = QPushButton("1 mm")
        self.btnHigh = QPushButton("0.17 mm")
        for btn in (self.btnRef, self.btnHome, self.btn1mm, self.btnHigh):
            btn.setMinimumHeight(36)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.spinCustom = QSpinBox()
        self.spinCustom.setRange(-100000, 100000)
        self.spinCustom.setValue(1000)
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("Benutzerdefiniert:"))
        custom_row.addWidget(self.spinCustom)
        self.btnGotoCustom = QPushButton("Position fahren")
        custom_row.addWidget(self.btnGotoCustom)
        layout.addLayout(custom_row)

        dauer_box = QGroupBox("Dauertest")
        dauer_layout = QHBoxLayout(dauer_box)
        self.btnDauertest = QPushButton("Start")
        self.lblRuns = QLabel("Runs: 0")
        dauer_layout.addWidget(self.btnDauertest)
        dauer_layout.addWidget(self.lblRuns)
        layout.addWidget(dauer_box)

        self.logView = QTextEdit()
        self.logView.setReadOnly(True)
        self.logView.setMinimumHeight(200)
        layout.addWidget(self.logView)

        # Connections
        self.btnRef.clicked.connect(lambda: self._submit(self.controller.goto_ref))
        self.btnHome.clicked.connect(lambda: self._submit(self.controller.goto_home))
        self.btn1mm.clicked.connect(lambda: self._submit(self.controller.goto_1mm))
        self.btnHigh.clicked.connect(lambda: self._submit(self.controller.goto_highres))
        self.btnGotoCustom.clicked.connect(
            lambda: self._submit(
                self.controller.goto_pos, int(self.spinCustom.value())
            )
        )
        self.btnDauertest.clicked.connect(self._toggle_dauertest)

        self._append_log("Z-Trieb bereit.")

    def _submit(self, fn: Callable, *args):
        def wrapper():
            fn(*args)

        self.executor.submit(wrapper)

    def _toggle_dauertest(self):
        if self._dauer_future and not self._dauer_future.done():
            self._stop_dauertest()
        else:
            self._start_dauertest()

    def _start_dauertest(self):
        self._stop_event = threading.Event()
        self._dauer_future = self.executor.submit(
            self.controller.run_dauertest, self._stop_event
        )
        self.btnDauertest.setText("Stop")

    def _stop_dauertest(self):
        if self._stop_event:
            self._stop_event.set()
        self.btnDauertest.setText("Start")

    def _append_log(self, text: str):
        self.logView.append(text)

    def _update_counter(self, count: int):
        self.lblRuns.setText(f"Runs: {count}")

    def _on_hw_state(self, available: bool):
        if available:
            return
        if _MCS_IMPORT_ERROR is not None:
            QMessageBox.information(
                self,
                "Z-Trieb",
                f"MCS-Bibliothek nicht verfügbar ({_MCS_IMPORT_ERROR}). Simulationsmodus aktiv.",
            )
        else:
            QMessageBox.warning(
                self,
                "Z-Trieb",
                "Z-Trieb Treiber nicht verfügbar – bitte Hardware und CAN-Verbindung prüfen. Details im Log.",
            )

    def closeEvent(self, event):
        try:
            self._stop_dauertest()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.controller.shutdown()
        except Exception:
            pass
        super().closeEvent(event)


def _run_standalone():
    """Small debug GUI so Z-Trieb can be tested without the full Stage-Toolbox."""
    import sys as _sys
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication.instance() or QApplication(_sys.argv)
    win = QMainWindow()
    win.setWindowTitle("Z-Trieb Debug")
    widget = ZTriebWidget()
    win.setCentralWidget(widget)
    win.resize(520, 420)
    win.show()
    widget._append_log("[DEBUG] Standalone-Modus gestartet.")
    widget._append_log(f"[DEBUG] Adresse: 0x{widget.controller.address:X}")
    _sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover - manual debug helper
    _run_standalone()

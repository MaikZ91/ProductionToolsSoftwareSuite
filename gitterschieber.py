"""
Headless Gitterschieber Toolkit (v2)
------------------------------------
Refactored to remove all GUI code. Provides:
 - capture_frame(): grab a frame from the DinoLite camera (or returns None on failure)
 - process_image(frame): particle detection on a given frame (returns overlay + dataframe)
 - SingleImageGratingAngle: angle helper from AngleAnalysisFunctions
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import cv2
import numpy as np
import matplotlib

matplotlib.use("Agg")  # Offscreen saves only
import matplotlib.pyplot as plt
import pandas as pd
import tifffile as tif
import seaborn as sns
from PIL import Image

from pipython import GCSDevice, pitools

import ie_Framework.Hardware.Motor.EightMotorcontroller as stage
from ie_Framework.Hardware.Camera.DinoLiteController import DinoLiteController, DummyDinoLite
from ie_Framework.Algorithm import AngleAnalysisFunctions as AAF
from ie_Framework.Algorithm.particle_detection import blend_overlay_and_annotate, particle_detection

# Von aussen direkt nutzbar
SingleImageGratingAngle = AAF.SingleImageGratingAngle

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
STAGE_WAIT_TIME = 1e-4
startpos_x = 8000
startpos_y = 97000
startpos_z = 3000
working_distance_dif = 32000

THEORIE_WINKEL_DEG = 8.95
GRATING_ERROR_TOLERANCE_MRAD = 2

SIM7_centerpos = [118395, 154950]
SIM31_centerpos = [81525, 151110]
SIM31_SEcornerpos = [85620, 298950]
currentPosXYZ = [0, 0, 0]

maxVolt = 4.022 * 1e3
x_addr = 18
y_addr = 19
z_addr = 20
voltage_step = 0.166 * 1e3
OP_amp_factor = 2

piezoshift_angle_to_cam = 100
grating_angle_to_cam = 100
grating_angle_error = 100

# Empfindlichkeit fuer Partikeldetektion (0..1)
DETECTION_SENSITIVITY = 0.66


def wait_time(old_pos: float, new_pos: float) -> float:
    """Berechnet die noetige Wartezeit in Sekunden basierend auf dem Weg der Stage."""
    stage_displacement = abs(new_pos - old_pos)
    return stage_displacement * STAGE_WAIT_TIME

# ---------------------------------------------------------------------------
# Hardware-Layer: seriell (Stage) + Kamera
# ---------------------------------------------------------------------------
_motor_lock = threading.Lock()
try:
    stage = stage.MotorController(port_number=5, baud_rate=9600, verbose=False)
except Exception:
    stage = None


_camera_lock = threading.Lock()
if DinoLiteController is None:
    _dino_lite: Optional[Any] = DummyDinoLite()
else:
    try:
        _dino_lite = DinoLiteController()
    except Exception:
        _dino_lite = DummyDinoLite()
_dpc_device: Optional[GCSDevice] = None
_dpc_axis: Optional[Any] = None


def capture_frame() -> Optional[np.ndarray]:
    """Liefert ein aktuelles BGR-Frame oder None bei Fehlern."""
    cam = _dino_lite
    if cam is None:
        return None
    with _camera_lock:
        try:
            return cam.capture_image()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Partikeldetektion
# ---------------------------------------------------------------------------


def _save_particle_plot(df: pd.DataFrame, out_dir: Path):
    if df is None or df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    diam = df["equiv_diam_px"]
    sns.histplot(diam, bins=20, kde=False, color="blue", ax=axes[0])
    axes[0].set_title("Histogramm Partikel (px)")
    axes[0].set_xlabel("Durchmesser [px]")
    axes[0].set_ylabel("Anzahl")

    sns.boxplot(x=diam, color="lightblue", ax=axes[1])
    axes[1].set_title("Boxplot Partikel (px)")
    axes[1].set_xlabel("Durchmesser [px]")

    fig.tight_layout()
    fig.savefig(out_dir / "particle_plot.png", dpi=220)
    plt.close(fig)


def process_image(frame: np.ndarray, *, save_dir: str | Path | None = None, sensitivity: float | None = None) -> Dict[str, Any]:
    """Partikelanalyse auf einem Frame. Gibt Overlay, Maske, DataFrame und Count zurueck."""
    sensitivity_val = DETECTION_SENSITIVITY if sensitivity is None else sensitivity
    overlay, mask, df, intermediates = particle_detection(
        frame,
        sensitivity=sensitivity_val,
        save_dir=None,
        return_intermediates=True,
        return_overlay_on=frame,
    )

    if save_dir is not None:
        out = Path(save_dir)
        out.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out / "detected.tif"), overlay)
        cv2.imwrite(str(out / "mask.tif"), mask)
        df.to_csv(str(out / "particles.csv"), index=False)
        for key, arr in intermediates.items():
            cv2.imwrite(str(out / f"{key}.tif"), arr)
        _save_particle_plot(df, out)

    return {"count": int(len(df)), "overlay": overlay, "mask": mask, "dataframe": df}


def analyse_current_frame_particles(save_dir: str | Path | None = None) -> Optional[Dict[str, Any]]:
    frame = capture_frame()
    if frame is None:
        return None
    return process_image(frame.copy(), save_dir=save_dir)


def analyse_current_frame_angle() -> Tuple[Optional[np.ndarray], Optional[float]]:
    frame = capture_frame()
    if frame is None:
        return None, None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    angle = SingleImageGratingAngle(gray)
    return frame, angle


def autofocus(
    *,
    focus_range: int = 2000,
    step: int = 100,
    settle_s: float = 0.5,
) -> Optional[np.ndarray]:
    """Autofokus entlang der Z-Achse."""
    try:
        current_z = int(stage.current_pos(z_addr))
    except Exception:
        current_z = 0
    start = int(current_z - focus_range / 2)
    end = int(current_z + focus_range / 2)

    best_score = -1.0
    best_pos = current_z
    best_frame: Optional[np.ndarray] = None

    for pos in range(start, end + step, step):
        stage.move_to_pos(z_addr, pos)
        if settle_s > 0:
            time.sleep(settle_s)
        frame = capture_frame()
        if frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        score = cv2.Laplacian(gray, cv2.CV_64F).var()
        if score > best_score:
            best_score = score
            best_pos = pos
            best_frame = frame

    if best_pos != current_z:
        stage.move_to_pos(z_addr, best_pos)
        if settle_s > 0:
            time.sleep(settle_s)
    return best_frame



def connect_DPC(start_pos: float = 300.0) -> float:
    """Initialisiert PI DPC via USB, verfuehrt auf Startposition und gibt Position zurueck."""
    global _dpc_device, _dpc_axis
    if _dpc_device is None or _dpc_axis is None:
        dev = GCSDevice()
        devices = dev.EnumerateUSB()
        if not devices:
            raise RuntimeError("Kein DPC-Geraet gefunden")
        dev.ConnectUSB(devices[0])
        axis = list(dev.qSAI())[0]
        dev.SVO(axis, True)
        pitools.startup(dev)
        _dpc_device = dev
        _dpc_axis = axis
    assert _dpc_device is not None and _dpc_axis is not None
    _dpc_device.MOV(_dpc_axis, start_pos)
    pitools.waitontarget(_dpc_device, [_dpc_axis])
    pos = float(_dpc_device.qPOS()[_dpc_axis])
    return pos


def close_DPC() -> None:
    """Schliesst die DPC Verbindung, falls offen."""
    global _dpc_device, _dpc_axis
    if _dpc_device is None:
        return
    try:
        _dpc_device.CloseConnection()
    finally:
        _dpc_device = None
        _dpc_axis = None


atexit.register(close_DPC)


def _ensure_dpc() -> tuple[GCSDevice, Any]:
    """Stellt sicher, dass eine DPC Verbindung besteht und liefert Device + Achse."""
    if _dpc_device is None or _dpc_axis is None:
        connect_DPC()
    assert _dpc_device is not None and _dpc_axis is not None
    return _dpc_device, _dpc_axis


def startpos(delay_s: float = 2.0) -> tuple[int, int, int]:
    """Faehrt die vordefinierten Startpositionen an."""
    stage.move_to_pos(x_addr, startpos_x)
    if delay_s:
        time.sleep(delay_s)
    stage.move_to_pos(y_addr, startpos_y)
    if delay_s:
        time.sleep(delay_s)
    stage.move_to_pos(z_addr, startpos_z)
    return startpos_x, startpos_y, startpos_z


def acquire_single_frame() -> np.ndarray:
    """Nimmt ein einzelnes Frame auf und fasst RGB zu einem Graubild zusammen."""
    frame = capture_frame()
    if frame is None:
        raise RuntimeError("Konnte kein Kamerabild aufnehmen")
    if frame.ndim == 3:
        return frame.sum(axis=2).astype(np.uint16)
    return frame.astype(np.uint16)


def acquire_shiftstack(um_range: Sequence[float]) -> np.ndarray:
    """Erstellt einen Stack ueber Piezo-Verfahrwege und liefert ihn als 3D-Array."""
    piezo, axis = _ensure_dpc()
    positions = list(um_range)
    first_frame = acquire_single_frame()
    height, width = first_frame.shape
    shiftstack = np.zeros((len(positions), height, width), dtype=np.uint16)

    for i, um in enumerate(positions):
        piezo.MOV(axis, float(um))
        pitools.waitontarget(piezo, [axis])
        time.sleep(0.5)
        shiftstack[i] = acquire_single_frame()

    piezo.MOV(axis, 0.0)
    pitools.waitontarget(piezo, [axis])
    return shiftstack


def save_shiftstack(shiftstack: np.ndarray, dirname: str | Path = "Testmeasurement") -> Path:
    """Speichert den Stack als TIF und liefert den Pfad zur Datei."""
    out_dir = Path(dirname)
    out_dir.mkdir(parents=True, exist_ok=True)
    rotated = np.rot90(shiftstack, k=1, axes=(1, 2))
    out_file = out_dir / "BWstack_rotated.tif"
    tif.imwrite(out_file, rotated.astype("uint16"), photometric="minisblack")
    return out_file


def GratingShiftwSave(
    save_dir: str | Path = "piezo_angle_meas",
    um_range: Sequence[float] | np.ndarray = np.arange(0, 300, 10),
) -> Path:
    """Verfuehrt Piezo-Schritte, speichert die aufgenommenen Frames und liefert das Zielverzeichnis."""
    piezo, axis = _ensure_dpc()
    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stage.move_to_pos(x_addr, SIM31_SEcornerpos[0])
    stage.move_to_pos(y_addr, SIM31_SEcornerpos[1])
    time.sleep(3)

    for um in um_range:
        piezo.MOV(axis, float(um))
        pitools.waitontarget(piezo, [axis])
        frame = capture_frame()
        if frame is None:
            continue
        cv2.imwrite(str(out_dir / f"Voltage_{int(um)}mV.tif"), frame)

    piezo.MOV(axis, 0.0)
    pitools.waitontarget(piezo, [axis])
    return out_dir


def MeasureShiftFFT(
    um_range: Sequence[float] | np.ndarray = np.arange(0, 300, 10),
) -> tuple[float, float]:
    """Piezo-Gitterwinkel ueber FFT-Analyse bestimmen."""
    connect_DPC()
    stage.move_to_pos(z_addr, startpos_z + working_distance_dif)
    stage.move_to_pos(x_addr, SIM31_centerpos[0])
    stage.move_to_pos(y_addr, SIM31_centerpos[1])
    autofocus()
    time.sleep(2.5)

    shiftstack = acquire_shiftstack(um_range)
    shiftstack = np.flip(shiftstack, axis=2)
    piezo_angle, grating_angle = AAF.AnalysePiezoAngleFFT(shiftstack)
    update_grating_angle_error(grating_angle, piezo_angle)
    return piezo_angle, grating_angle


def MeasureShiftGratingEdge(
    um_range: Sequence[float] | np.ndarray = np.arange(0, 300, 10),
    shift_dy: int = -150,
    shift_dx: int = -200,
) -> tuple[float, float]:
    """Piezo-Gitterwinkel ueber Kantenverschiebung bestimmen."""
    stage.move_to_pos(x_addr, SIM31_SEcornerpos[0])
    time.sleep(2.5)
    stage.move_to_pos(y_addr, SIM31_SEcornerpos[1])
    time.sleep(5)

    shiftstack = acquire_shiftstack(um_range)
    piezo_angle, grating_angle = AAF.AnalysePiezoAngleGratingEdge(shiftstack, shift_dy, shift_dx)
    update_grating_angle_error(grating_angle, piezo_angle)
    return piezo_angle, grating_angle


def MeasureSingleImageGratingAngle() -> float:
    """Gitterwinkel aus einem Einzelbild bestimmen."""
    stage.move_to_pos(y_addr, SIM31_centerpos[1])
    time.sleep(1)
    stage.move_to_pos(x_addr, SIM31_centerpos[0])
    time.sleep(1)

    single_frame = acquire_single_frame()
    angle_deg = SingleImageGratingAngle(single_frame)
    update_grating_angle_error(angle_deg, piezoshift_angle_to_cam)
    return angle_deg


def update_grating_angle_error(grating_angle: float, shift_angle: float) -> float:
    """Aktualisiert die globalen Winkelwerte und gibt den Fehler zurueck."""
    global grating_angle_error, piezoshift_angle_to_cam, grating_angle_to_cam

    piezoshift_angle_to_cam = shift_angle
    grating_angle_to_cam = grating_angle

    grating_angle_error = -1 * (grating_angle_to_cam - THEORIE_WINKEL_DEG - piezoshift_angle_to_cam)
    grating_angle_error_mrad = grating_angle_error * np.pi / 0.18
    print(
        f"New grating angle error is {round(grating_angle_error, 3)} degrees, "
        f"or {round(grating_angle_error_mrad, 2)} mrad."
    )
    if abs(grating_angle_error_mrad) < GRATING_ERROR_TOLERANCE_MRAD:
        print(f"Grating angle error is within tolerance of {GRATING_ERROR_TOLERANCE_MRAD} mrad.")
    else:
        direction = "clockwise" if grating_angle_error > 0 else "counter-clockwise"
        print(f"Tolerance not yet reached. Please adjust grating ({direction}).")
    return grating_angle_error


__all__ = [
    "capture_frame",
    "process_image",
    "particle_detection",
    "analyse_current_frame_particles",
    "analyse_current_frame_angle",
    "SingleImageGratingAngle",
    "autofocus",
    "startpos",
    "wait_time",
    "acquire_single_frame",
    "acquire_shiftstack",
    "save_shiftstack",
    "GratingShiftwSave",
    "MeasureShiftFFT",
    "MeasureShiftGratingEdge",
    "MeasureSingleImageGratingAngle",
    "update_grating_angle_error",
    "connect_DPC",
    "close_DPC",
]


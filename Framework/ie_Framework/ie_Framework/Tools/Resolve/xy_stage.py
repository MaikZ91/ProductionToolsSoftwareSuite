#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stage-spezifische Logik ohne GUI-Elemente.
Contains PMAC bridge, motion helpers, and endurance-test workers.
"""
from __future__ import annotations

import datetime
import os
import pathlib
import re
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal
from data_management import save_calibration_plot, save_stage_test


# ---------------------------------------------------------------------------
# Calibration helper block
# Contains helper functions for:
# - reading PMAC status/config
# - moving axes and reading encoders
# - generating calibration motion sequences
# PMAC connection/initialization stays wrapped in functions/worker flows
# so importing this module does not trigger hardware access.
# ---------------------------------------------------------------------------
stageStatus = {}
_pmac_real_backend = None
_pmac_sim_backend = None
_pmac_use_sim = False


def connect_and_read_stage_config(uri: str = "tcp://127.0.0.1:5050", connect_pmac: bool = False):
    """
    Optionally connect to PMAC, print current stage status, and return X/Y config values.

    This function:
    - optionally calls `pmac_connect(uri)` when `connect_pmac=True`
    - refreshes `stageStatus` via `pmac_get_stage_pos_info(...)`
    - prints available stage status fields to the console
    - reads and returns calibration-relevant X/Y PMAC configuration values
    """
    if connect_pmac:
        res = pmac_connect(uri)

    res = pmac_get_stage_pos_info(stageStatus)

    if stageStatus.get("error"):
        # print("Something went wrong: ", stageStatus.get("error"))
        pass
    else:
        for key, label in (
            ("xPos", "xPos"),
            ("xPosEnc", "xPosEnc"),
            ("yPos", "yPos"),
            ("yPosEnc", "yPosEnc"),
            ("zPos", "zPos"),
            ("xPos_motorSteps", "xPos_motorSteps"),
            ("yPos_motorSteps", "yPos_motorSteps"),
            ("xPos_encoderSteps", "xPos_encoderSteps"),
            ("yPos_encoderSteps", "yPos_encoderSteps"),
            ("zPos_voiceCoilSteps", "zPos_voiceCoilSteps"),
            ("zPos_samBoardSteps", "zPos_samBoardSteps"),
        ):
            if key in stageStatus:
                # print(f"{label}:    ", stageStatus[key])
                pass

    # get config for x axis
    limitLowStepsX = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/limitLowSteps")
    limitHighStepsX = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/limitHighSteps")
    homeStepPositionX = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/homeStepPosition")
    stepsPerMeterX = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/stepsPerMeter")
    encoderStepsPerMeterX = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/encoderStepsPerMeter")
    encoderMinPositionX = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/encoderMinPosition")
    encoderMaxPositionX = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/encoderMaxPosition")

    # get config for Y axis
    limitLowStepsY = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/limitLowSteps")
    limitHighStepsY = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/limitHighSteps")
    homeStepPositionY = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/homeStepPosition")
    stepsPerMeterY = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/stepsPerMeter")
    encoderStepsPerMeterY = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/encoderStepsPerMeter")
    encoderMinPositionY = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/encoderMinPosition")
    encoderMaxPositionY = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/encoderMaxPosition")

    return (
        limitLowStepsX, limitHighStepsX, homeStepPositionX, stepsPerMeterX, encoderStepsPerMeterX,
        encoderMinPositionX, encoderMaxPositionX,
        limitLowStepsY, limitHighStepsY, homeStepPositionY, stepsPerMeterY, encoderStepsPerMeterY,
        encoderMinPositionY, encoderMaxPositionY,
    )

def moveXinsteps(motorsteps):
    """Move X axis to an absolute motor-step position."""
    pmac_set_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/X/stepPosition", int(motorsteps))


def moveYinsteps(motorsteps):
    """Move Y axis to an absolute motor-step position."""
    pmac_set_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/Y/stepPosition", int(motorsteps))


def get_current_pos():
    """Liefert die aktuellen Motor-Sollpositionen in Steps (X, Y, Z)."""
    x = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/X/stepPosition")
    y = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/Y/stepPosition")
    z = pmac_get_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/Z/stepPosition")
    return x, y, z


def getXencoder():
    """Read and print the current X encoder position in encoder steps."""
    res = pmac_get_stage_pos_info(stageStatus)
    # print("xPosEnc: ", stageStatus["xPos_encoderSteps"])
    return stageStatus["xPos_encoderSteps"]


def getYencoder():
    """Read and print the current Y encoder position in encoder steps."""
    res = pmac_get_stage_pos_info(stageStatus)
    # print("yPosEnc: ", stageStatus["yPos_encoderSteps"])
    return stageStatus["yPos_encoderSteps"]


def get_stage_encoders():
    """Liefert die aktuellen Encoder-Istwerte (X, Y) in Encodersteps."""
    st = {}
    pmac_get_stage_pos_info(st)
    return st["xPos_encoderSteps"], st["yPos_encoderSteps"]


def motorsteps2encodersteps(motorStep, stepsPerMeter, encoderStepsPerMeter):
    """Convert motor steps to expected encoder steps using axis calibration values."""
    encodersteps = motorStep / stepsPerMeter * encoderStepsPerMeter
    return encodersteps.astype(int)


def meas_linear(StartMotorSteps, StopMotorSteps, Steps, stepsPerMeter, encoderStepsPerMeter):
    """Generate linearly spaced motor-step targets and expected encoder values."""
    motor_steps = np.linspace(StartMotorSteps, StopMotorSteps, Steps).astype(int)
    calc_encoder = motorsteps2encodersteps(motor_steps, stepsPerMeter, encoderStepsPerMeter)
    return motor_steps, calc_encoder


def meas_random(StartMotorSteps, StopMotorSteps, Steps, stepsPerMeter, encoderStepsPerMeter):
    """Generate random motor-step targets within a range and expected encoder values."""
    motor_steps = np.random.uniform(StartMotorSteps, StopMotorSteps, Steps)
    calc_encoder = motorsteps2encodersteps(motor_steps, stepsPerMeter, encoderStepsPerMeter)
    return motor_steps, calc_encoder


def meas_single_zickzack(StartMotorSteps, StopMotorSteps, Steps, stepsPerMeter, encoderStepsPerMeter):
    """Generate one forward/backward sweep and expected encoder values."""
    motor_steps = np.linspace(StartMotorSteps, StopMotorSteps, Steps).astype(int)
    motor_steps_flipped = np.flipud(motor_steps)
    motor_steps = np.append(motor_steps, motor_steps_flipped)
    calc_encoder = motorsteps2encodersteps(motor_steps, stepsPerMeter, encoderStepsPerMeter)
    return motor_steps, calc_encoder


def meas_zickzack(StartMotorSteps, StopMotorSteps, Steps, repetitions, stepsPerMeter, encoderStepsPerMeter):
    """Generate repeated forward/backward sweeps and expected encoder values."""
    motor_steps = np.linspace(StartMotorSteps, StopMotorSteps, Steps).astype(int)
    motor_steps_flipped = np.flipud(motor_steps)
    onerepetition = np.append(motor_steps, motor_steps_flipped)
    motor_steps = onerepetition
    for i in range(repetitions):
        motor_steps = np.append(motor_steps, onerepetition)
    calc_encoder = motorsteps2encodersteps(motor_steps, stepsPerMeter, encoderStepsPerMeter)
    return motor_steps, calc_encoder


def meas_moving_zickzack(
    StartMotorSteps,
    StopMotorSteps,
    Steps,
    movingstep,
    repetitions,
    stepsPerMeter,
    encoderStepsPerMeter,
):
    """Generate shifted repeated zig-zag sweeps and expected encoder values."""
    linear = np.linspace(StartMotorSteps, StopMotorSteps, Steps).astype(int)
    linear_flipped = np.flipud(linear)
    onerepetition = np.append(linear, linear_flipped)
    motor_steps = onerepetition
    for i in range(repetitions):
        motor_steps = np.append(motor_steps, onerepetition + (i + 1) * movingstep)
    calc_encoder = motorsteps2encodersteps(motor_steps, stepsPerMeter, encoderStepsPerMeter)
    return motor_steps, calc_encoder

def meas_zigzag_linear(lo, hi, n, spm, epm):
    """Zig-zag motion (forward/backward) including expected encoder values."""
    mot = np.linspace(lo, hi, n).astype(int)
    mot = np.r_[mot, mot[::-1]]
    return mot, (mot / spm * epm).astype(int)


def apply_calibration_steps_per_meter(sc, batch: str, x_spm: int, y_spm: int):
    """Schreibt neue stepsPerMeter in PMAC und aktualisiert den StageController-Cache."""
    try:
        pmac_set_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/X/stepsPerMeter", int(x_spm))
        pmac_set_param("ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/Y/stepsPerMeter", int(y_spm))
        sc.steps_per_m["X"] = int(x_spm)
        sc.steps_per_m["Y"] = int(y_spm)
        print(f"[APPLIED][{batch}] stepsPerMeter: X = {x_spm}, Y = {y_spm}")
    except Exception as e:
        print(f"[WARN][{batch}] Konnte stepsPerMeter nicht schreiben:", e)

def measure_axis_encoder_points(
    axis: str,
    motor_steps: np.ndarray,
    home_x: int,
    home_y: int,
) -> np.ndarray:
    """Move one axis through points and return encoder readings (simple/original style)."""
    enc = np.zeros(len(motor_steps), dtype=float)
    for i in range(len(motor_steps)):
        ms = motor_steps[i]
        if axis == "X":
            moveXinsteps(ms)
            enc[i] = getXencoder()
        else:
            moveYinsteps(ms)
            enc[i] = getYencoder()
    moveXinsteps(home_x)
    moveYinsteps(home_y)
    return enc

def measure_axis_encoder_points_with_callbacks(
    axis: str,
    motor_steps: np.ndarray,
    home_x: int,
    home_y: int,
    on_phase=None,
    on_step=None,
    batch: str = "NoBatch",
) -> np.ndarray:
    """Extended variant with progress callbacks for worker/UI usage."""
    if on_phase is not None:
        on_phase(f"Calibration {axis} Â· Batch: {batch}", len(motor_steps))
    enc = np.zeros(len(motor_steps), dtype=float)
    for i, ms in enumerate(motor_steps, 1):
        if axis == "X":
            moveXinsteps(ms)
            enc[i - 1] = getXencoder()
        else:
            moveYinsteps(ms)
            enc[i - 1] = getYencoder()
        if on_step is not None:
            on_step(i)
    moveXinsteps(home_x)
    moveYinsteps(home_y)
    return enc

def _ensure_pmac_sim_backend():
    global _pmac_sim_backend
    if _pmac_sim_backend is None:
        _pmac_sim_backend = _DummyPMACBackend()
    return _pmac_sim_backend


def _pmac_fallback(reason: Exception | str):
    """Switch to simulation backend after the first hardware failure."""
    global _pmac_use_sim
    if not _pmac_use_sim:
        print(f"[WARN] PMAC fallback enabled ({reason}).")
        _pmac_use_sim = True
    return _ensure_pmac_sim_backend()


def pmac_connect(uri: str):
    """Connect through hardware backend, or simulation backend if needed."""
    if _pmac_use_sim or _pmac_real_backend is None:
        return _ensure_pmac_sim_backend().connect(uri)
    try:
        return _pmac_real_backend.connect(uri)
    except Exception as exc:
        return _pmac_fallback(exc).connect(uri)


def pmac_get_param(path: str):
    """Read a PMAC parameter with automatic simulation fallback."""
    if _pmac_use_sim or _pmac_real_backend is None:
        return _ensure_pmac_sim_backend().getParam(path)
    try:
        return _pmac_real_backend.getParam(path)
    except Exception as exc:
        return _pmac_fallback(exc).getParam(path)


def pmac_set_param(path: str, value):
    """Write a PMAC parameter with automatic simulation fallback."""
    if _pmac_use_sim or _pmac_real_backend is None:
        return _ensure_pmac_sim_backend().setParam(path, value)
    try:
        return _pmac_real_backend.setParam(path, value)
    except Exception as exc:
        return _pmac_fallback(exc).setParam(path, value)


def pmac_get_stage_pos_info(status: dict):
    """Read stage position/status with automatic simulation fallback."""
    if _pmac_use_sim or _pmac_real_backend is None:
        return _ensure_pmac_sim_backend().getStagePosInfo(status)
    try:
        return _pmac_real_backend.getStagePosInfo(status)
    except Exception as exc:
        return _pmac_fallback(exc).getStagePosInfo(status)
    
def sanitize_batch(s: str) -> str:
    """Sanitize batch name for use in file paths."""
    s = (s or "").strip()
    if not s:
        return "NoBatch"
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:64] or "NoBatch"

def build_calibration_motor_steps(
    limitLowStepsX,
    limitHighStepsX,
    stepsPerMeterX,
    encoderStepsPerMeterX,
    limitLowStepsY,
    limitHighStepsY,
    stepsPerMeterY,
    encoderStepsPerMeterY,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate sampling points for linear calibration."""
    motorStepsX, _ = meas_linear(
        limitLowStepsX + 1000,
        limitHighStepsX - 1000,
        20,
        stepsPerMeterX,
        encoderStepsPerMeterX,
    )
    motorStepsY, _ = meas_linear(
        limitLowStepsY + 5000,
        limitHighStepsY - 5000,
        20,
        stepsPerMeterY,
        encoderStepsPerMeterY,
    )
    return motorStepsX, motorStepsY

def fit_motorsteps_per_meter(enc: np.ndarray, motor_steps: np.ndarray, encoder_steps_per_meter: int):
    """Lineare Regression: Encoderposition (m) auf Motorsteps abbilden."""
    x = enc / encoder_steps_per_meter
    y = motor_steps
    coef = np.polyfit(x, y, 1)
    return x, y, np.poly1d(coef), int(coef[0])

def calibrate_x_y_original(
    motorStepsX=None,
    motorStepsY=None,
    encoderStepsPerMeterX=None,
    encoderStepsPerMeterY=None,
    homeStepPositionX=None,
    homeStepPositionY=None,
):
    """
    Executes X/Y stage calibration.

    Procedure:
      - Move stage to predefined motor step positions
      - Read encoder feedback at each position
      - Compute linear regression (polyfit)
      - Derive updated stepsPerMeter estimates

    Returns:
      Dictionary containing calibration results and measurement data.
    """
    if (
        motorStepsX is None
        or motorStepsY is None
        or encoderStepsPerMeterX is None
        or encoderStepsPerMeterY is None
        or homeStepPositionX is None
        or homeStepPositionY is None
    ):
        (
            limitLowStepsX, limitHighStepsX, homeStepPositionX, stepsPerMeterX, encoderStepsPerMeterX,
            encoderMinPositionX, encoderMaxPositionX,
            limitLowStepsY, limitHighStepsY, homeStepPositionY, stepsPerMeterY, encoderStepsPerMeterY,
            encoderMinPositionY, encoderMaxPositionY,
        ) = connect_and_read_stage_config(connect_pmac=False)
        motorStepsX, motorStepsY = build_calibration_motor_steps(
            limitLowStepsX, limitHighStepsX, stepsPerMeterX, encoderStepsPerMeterX,
            limitLowStepsY, limitHighStepsY, stepsPerMeterY, encoderStepsPerMeterY,
        )

    # --- X axis measurement ---
    Enc = measure_axis_encoder_points(
        "X",
        motorStepsX,
        homeStepPositionX,
        homeStepPositionY,
    )

    xX = Enc / encoderStepsPerMeterX
    yX = motorStepsX
    coefX = np.polyfit(xX, yX, 1)
    newMotorStepsPerMeterX = int(coefX[0])
    fitX = np.poly1d(coefX)

    # --- Y axis measurement ---
    Enc = measure_axis_encoder_points(
        "Y",
        motorStepsY,
        homeStepPositionX,
        homeStepPositionY,
    )

    xY = Enc / encoderStepsPerMeterY
    yY = motorStepsY
    coefY = np.polyfit(xY, yY, 1)
    newMotorStepsPerMeterY = int(coefY[0])
    fitY = np.poly1d(coefY)

    return {
        "newMotorStepsPerMeterX": newMotorStepsPerMeterX,
        "newMotorStepsPerMeterY": newMotorStepsPerMeterY,
        "X": {"x": xX, "y": yX, "fit_y": fitX(xX)},
        "Y": {"x": xY, "y": yY, "fit_y": fitY(xY)},
    }


# Backward-compatible alias used by older helper flows.
calibrate_x_y = calibrate_x_y_original



def run_stage_calibration(
    sc,
    batch: str = "NoBatch",
    out_dir: pathlib.Path | None = None,
    on_phase=None,
    on_step=None,
    on_calib=None,
):
    """Run linear X/Y calibration and apply new stepsPerMeter values."""
    if out_dir is None:
        raise ValueError("out_dir is required")
    batch = sanitize_batch(batch)
    (
        limitLowStepsX, limitHighStepsX, homeStepPositionX, stepsPerMeterX, encoderStepsPerMeterX,
        encoderMinPositionX, encoderMaxPositionX,
        limitLowStepsY, limitHighStepsY, homeStepPositionY, stepsPerMeterY, encoderStepsPerMeterY,
        encoderMinPositionY, encoderMaxPositionY,
    ) = connect_and_read_stage_config(connect_pmac=False)

    motorStepsX, motorStepsY = build_calibration_motor_steps(
        limitLowStepsX, limitHighStepsX, stepsPerMeterX, encoderStepsPerMeterX,
        limitLowStepsY, limitHighStepsY, stepsPerMeterY, encoderStepsPerMeterY,
    )

    enc_x = measure_axis_encoder_points_with_callbacks(
        "X",
        motorStepsX,
        home_x=homeStepPositionX,
        home_y=homeStepPositionY,
        on_phase=on_phase,
        on_step=on_step,
        batch=batch,
    )
    x_fit, y_fit, poly_x, newMotorStepsPerMeterX = fit_motorsteps_per_meter(
        enc_x, motorStepsX, encoderStepsPerMeterX
    )
    save_calibration_plot(out_dir, "X", batch, x_fit, y_fit, poly_x)

    enc_y = measure_axis_encoder_points_with_callbacks(
        "Y",
        motorStepsY,
        home_x=homeStepPositionX,
        home_y=homeStepPositionY,
        on_phase=on_phase,
        on_step=on_step,
        batch=batch,
    )
    x_fit, y_fit, poly_y, newMotorStepsPerMeterY = fit_motorsteps_per_meter(
        enc_y, motorStepsY, encoderStepsPerMeterY
    )
    save_calibration_plot(out_dir, "Y", batch, x_fit, y_fit, poly_y)

    apply_calibration_steps_per_meter(sc, batch, newMotorStepsPerMeterX, newMotorStepsPerMeterY)

    calib_payload = {
        "batch": batch,
        "X_stepsPerMeter": int(newMotorStepsPerMeterX),
        "Y_stepsPerMeter": int(newMotorStepsPerMeterY),
    }
    if on_calib is not None:
        on_calib(calib_payload)

    return {
        "newMotorStepsPerMeterX": int(newMotorStepsPerMeterX),
        "newMotorStepsPerMeterY": int(newMotorStepsPerMeterY),
        "motorStepsX": motorStepsX,
        "motorStepsY": motorStepsY,
    }

def run_stage_measurement(
    sc,
    batch: str = "NoBatch",
    on_phase=None,
    on_step=None,
):
    """Run X/Y zig-zag measurement and return plot data + max error."""
    sc = reconnect_stage_controller(sc)
    batch = sanitize_batch(batch)
    plot_data = []
    max_abs_um = 0.0

    for ax in "XY":
        spm, epm = sc.steps_per_m[ax], sc.enc_per_m[ax]
        mot, calc = meas_zigzag_linear(sc.low_lim[ax], sc.high_lim[ax], 100, spm, epm)
        if on_phase is not None:
            on_phase(f"Measurement {ax} · Batch: {batch}", len(mot))
        enc = np.zeros_like(mot)
        for i, m in enumerate(mot, 1):
            sc.move_abs(ax, int(m))
            enc[i - 1] = sc.enc(ax)
            if on_step is not None:
                on_step(i)
        diff_um = np.abs((enc - calc) / epm * 1e6)
        max_abs_um = max(max_abs_um, float(np.max(diff_um)))
        plot_data.append((ax, mot, enc, calc, spm, epm))
        sc.move_abs(ax, sc.home_pos[ax])

    return {"plots": plot_data, "meas_max_um": float(max_abs_um), "batch": batch}


    
# ---------------------------------------------------------------------------
# Basis/Defaults
# ---------------------------------------------------------------------------
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

    # Nimm den ersten bereits existierenden Pfad (Reihenfolge = PrioritÃ¤t).
    for path in candidates:
        try:
            if path.exists():
                return path.resolve()
        except OSError:
            continue

    # If nothing exists, create the preferred candidate.
    fallback = candidates[0] if candidates else (BASE_DIR / "Stage-Teststand")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve()


DATA_ROOT = resolve_data_root()
DUR_MAX_UM = 25.5    # Max allowed |error| in endurance test (live + final check)

# ---------------------------------------------------------------------------
# PMAC Bridge (Hardware / Simulation)
# ---------------------------------------------------------------------------
try:
    import pmacspy as _pmac_module
except Exception as exc:  # pragma: no cover - hardware import fails during simulation
    _pmac_module = None
    _PMAC_IMPORT_ERROR = exc
else:
    _PMAC_IMPORT_ERROR = None

_pmac_real_backend = _pmac_module
_pmac_use_sim = _pmac_module is None
if _pmac_use_sim:
    print("[INFO] Keine PMAC-Hardware gefunden - starte im Simulationsmodus.")


# ---------------------------------------------------------------------------
# Stage Controller + Worker
# ---------------------------------------------------------------------------
class StageController:
    """
    Thin PMAC/Dummy backend adapter used by the Resolve Production Tool.

    Responsibilities:
    - establish a PMAC connection (or simulation fallback)
    - cache axis configuration values that are read often during tests
    - provide simple absolute moves and encoder reads for X/Y

    Notes:
    - Axis names are expected as uppercase strings ("X", "Y").
    - `status` stores the latest result from `pmac_get_stage_pos_info(...)`.
    - Config values are cached at init to reduce repeated PMAC reads during loops.
      If PMAC config is changed externally, create a new controller or refresh
      the relevant cached dictionaries manually.
    """
    def __init__(self, uri="tcp://127.0.0.1:5050"):
        """
        Connect to the stage backend and cache calibration/limit parameters.

        Parameters:
        - `uri`: PMAC endpoint, defaults to local bridge on port 5050.
        """
        # `pmac_connect` transparently falls back to the in-memory dummy backend
        # when no hardware backend is available or the first PMAC call fails.
        self.uri = uri
        self.conn, self.status = pmac_connect(uri), {}

        # Prime `self.status` once so downstream reads can immediately access
        # encoder/status keys without an extra explicit refresh.
        self.refresh()

        # PMAC config root for per-axis static-ish values used in calibration and
        # endurance test target generation.
        root = "ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeConfig/"
        g = lambda a, k: pmac_get_param(f"{root}{a}/{k}")

        # Cached conversion factors:
        # - steps_per_m: motor steps -> meters
        # - enc_per_m: encoder steps -> meters
        self.steps_per_m = {a: g(a,"stepsPerMeter") for a in "XY"}
        self.enc_per_m   = {a: g(a,"encoderStepsPerMeter") for a in "XY"}

        # Cached travel limits and home positions used by current test workers.
        self.low_lim     = {a: g(a,"limitLowSteps") for a in "XY"}
        self.high_lim    = {a: g(a,"limitHighSteps") for a in "XY"}
        self.home_pos    = {a: g(a,"homeStepPosition") for a in "XY"}

    def refresh(self):
        """
        Refresh the current PMAC stage status into `self.status`.

        This updates encoder-related keys like:
        - `xPos_encoderSteps`
        - `yPos_encoderSteps`
        """
        pmac_get_stage_pos_info(self.status)

    def move_abs(self, a, s):
        """
        Move one axis to an absolute motor-step target.

        Parameters:
        - `a`: axis name ("X" or "Y")
        - `s`: absolute target in motor steps
        """
        pmac_set_param(f"ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/{a}/stepPosition", int(s))

    def enc(self, a):
        """
        Return the latest encoder position for one axis in encoder steps.

        A fresh status read is performed before returning the value.
        """
        self.refresh()
        return self.status[f"{a.lower()}Pos_encoderSteps"]


def reconnect_stage_controller(sc: StageController | None = None) -> StageController:
    """Create a fresh StageController (new PMAC connection), preserving URI when available."""
    uri = getattr(sc, "uri", "tcp://127.0.0.1:5050")
    return StageController(uri=uri)

class TestWorker(QObject):
    new_phase = Signal(str, int)
    step      = Signal(int)
    done      = Signal(dict)
    error     = Signal(str)
    calib     = Signal(dict)
    sam_reconnect_required = Signal(dict)

    def __init__(self, sc, batch: str = "NoBatch"):
        super().__init__()
        self.sc = sc
        self.batch = sanitize_batch(batch)
        self._meas_max_um = None
        self._stop_requested = False
        self._sam_pause_event = threading.Event()
        self._sam_continue = False

    def stop(self):
        """
        Compatibility stop hook used by the GUI.

        The precision run is not yet fully interruptible inside all low-level
        calibration/move loops, but this prevents AttributeError and allows
        future cooperative checks to key off `_stop_requested`.
        """
        self._stop_requested = True
        self.provide_sam_reconnect_decision(False)

    def provide_sam_reconnect_decision(self, continue_measurement: bool):
        self._sam_continue = bool(continue_measurement)
        self._sam_pause_event.set()

    def _wait_for_sam_reconnect(self) -> bool:
        self._sam_continue = False
        self._sam_pause_event.clear()
        self.sam_reconnect_required.emit({
            "batch": self.batch,
            "message": "SAM Board trennen und neu verbinden.",
        })
        self._sam_pause_event.wait()
        return bool(self._sam_continue) and not self._stop_requested

    def run(self):
        try:
            result = run_stage_calibration_and_measurement(
                self.sc,
                batch=self.batch,
                out_root=DATA_ROOT,
                on_phase=self.new_phase.emit,
                on_step=self.step.emit,
                on_calib=self.calib.emit,
                on_post_calibration_pause=self._wait_for_sam_reconnect,
            )
            self._meas_max_um = float(result.get("meas_max_um", 0.0))
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class CalibrationWorker(QObject):
    """Worker for running only X/Y calibration."""
    new_phase = Signal(str, int)
    step = Signal(int)
    done = Signal(dict)
    error = Signal(str)
    calib = Signal(dict)

    def __init__(self, sc, batch: str = "NoBatch", out_dir: pathlib.Path | None = None):
        super().__init__()
        self.sc = sc
        self.batch = sanitize_batch(batch)
        self.out_dir = pathlib.Path(out_dir) if out_dir is not None else None
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if self.out_dir is None:
                raise ValueError("out_dir is required for CalibrationWorker")
            calib_info = run_stage_calibration(
                self.sc,
                batch=self.batch,
                out_dir=self.out_dir,
                on_phase=self.new_phase.emit,
                on_step=self.step.emit,
                on_calib=self.calib.emit,
            )
            self.done.emit({
                "out": self.out_dir,
                "batch": self.batch,
                "calib": calib_info,
                "aborted": bool(self._stop_requested),
            })
        except Exception as e:
            self.error.emit(str(e))


class MeasurementWorker(QObject):
    """Worker for running only X/Y measurement with current calibration."""
    new_phase = Signal(str, int)
    step = Signal(int)
    done = Signal(dict)
    error = Signal(str)

    def __init__(self, sc, batch: str = "NoBatch", out_dir: pathlib.Path | None = None):
        super().__init__()
        self.sc = sc
        self.batch = sanitize_batch(batch)
        self.out_dir = pathlib.Path(out_dir) if out_dir is not None else None
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if self.out_dir is None:
                raise ValueError("out_dir is required for MeasurementWorker")
            meas_info = run_stage_measurement(
                self.sc,
                batch=self.batch,
                on_phase=self.new_phase.emit,
                on_step=self.step.emit,
            )
            self.done.emit({
                "out": self.out_dir,
                "batch": self.batch,
                "plots": meas_info.get("plots", []),
                "meas_max_um": float(meas_info.get("meas_max_um", 0.0)),
                "aborted": bool(self._stop_requested),
            })
        except Exception as e:
            self.error.emit(str(e))

class ExtendedEnduranceTestWorker(QObject):
    """Endurance-test worker with alternating small/large random moves."""
    update   = Signal(dict)
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(
        self,
        sc,
        batch: str = "NoBatch",
        out_dir: pathlib.Path | None = None,
        center_x: int = 0,
        center_y: int = 0,
        small_step: int = 500,
        small_radius: int = 2000,
        small_phase_sec: float = 120.0,
        large_phase_sec: float = 30.0,
        dwell_small: float = 0.2,
        dwell_large: float = 0.1,
        limit_um: float = DUR_MAX_UM,
        stop_at_ts: float | None = None,
    ):
        super().__init__()
        self.sc = sc
        self.batch = sanitize_batch(batch)
        self.out_dir = out_dir
        self.center_x = int(center_x)
        self.center_y = int(center_y)
        self.small_step = int(max(1, small_step))
        self.small_radius = int(max(1, small_radius))
        self.small_phase_sec = float(small_phase_sec)
        self.large_phase_sec = float(large_phase_sec)
        self.dwell_small = float(dwell_small)
        self.dwell_large = float(dwell_large)
        self.limit_um = float(limit_um)
        self.max_abs_um = 0.0
        self._running = True
        self.start_ts = time.time()
        self.stop_at_ts = float(stop_at_ts) if stop_at_ts else None

        self.pos_infodict={"x_counter":[],"y_counter":[],"Time [min]":[],
                           "x_position [m]":[],"y_position [m]":[],
                           "pos_error_x [m]":[],"pos_error_y [m]":[]}
        base_name = f"{self.batch}_combined_values.csv"
        self.savefile = (self.out_dir / base_name) if self.out_dir else pathlib.Path(base_name)

    def stop(self):
        """Requests a clean stop."""
        self._running = False

    def _clamp(self, axis: str, val: int) -> int:
        """Clamp target positions strictly to configured axis limits."""
        lo = self.sc.low_lim[axis]
        hi = self.sc.high_lim[axis]
        return int(min(max(val, lo), hi))

    def _log_move(self, phase: str, idx: int, total: int, tx: int, ty: int, move_idx: int, dwell: float):
        """Execute one move, measure encoder values, and emit an update."""
        self.sc.move_abs('X', tx)
        self.sc.move_abs('Y', ty)
        time.sleep(dwell)

        # Compute position error from encoder actual value and expected motor position:
        # (EncoderSteps / EncPerMeter) - (MotorSteps / StepsPerMeter)
        x_enc, y_enc = get_stage_encoders()
        spm_x = self.sc.steps_per_m['X']; spm_y = self.sc.steps_per_m['Y']
        epm_x = self.sc.enc_per_m['X'];   epm_y = self.sc.enc_per_m['Y']
        err_x = (x_enc/epm_x) - (tx/spm_x)
        err_y = (y_enc/epm_y) - (ty/spm_y)
        err_um = max(abs(err_x), abs(err_y)) * 1e6
        self.max_abs_um = max(self.max_abs_um, float(err_um))
        runtime = round((time.time()-self.start_ts)/60, 2)

        self.pos_infodict["Time [min]"].append(runtime)
        self.pos_infodict["x_counter"].append(move_idx)
        self.pos_infodict["y_counter"].append(move_idx)
        self.pos_infodict["x_position [m]"].append(round(x_enc/epm_x,6))
        self.pos_infodict["y_position [m]"].append(round(y_enc/epm_y,6))
        self.pos_infodict["pos_error_x [m]"].append(round(err_x,8))
        self.pos_infodict["pos_error_y [m]"].append(round(err_y,8))

        self.update.emit({
            "phase": phase,
            "idx": idx,
            "total": total,
            "target_x": tx,
            "target_y": ty,
            "err_um": err_um,
            "max_abs_um": float(self.max_abs_um),
            "limit_um": float(self.limit_um),
            "t": runtime,
            "ex": err_x,
            "ey": err_y,
            "batch": self.batch,
        })

    def run(self):
        """Run the endurance test, log values, and save the CSV at the end."""
        try:
            move_idx = 0
            phase = "Kleine Amplituden"
            small_idx = 0
            large_idx = 0
            now = time.time()
            target_stop = self.stop_at_ts or float("inf")

            while self._running and now < target_stop:
                # Phases alternate by time between local and global movement.
                phase_duration = self.small_phase_sec if phase == "Kleine Amplituden" else self.large_phase_sec
                dwell = self.dwell_small if phase == "Kleine Amplituden" else self.dwell_large
                phase_end = min(target_stop, now + phase_duration)
                # Rough progress estimate within the current phase.
                est_total = max(1, int(max(1.0, (phase_end - now) / max(0.01, dwell))))

                while self._running and (time.time() < phase_end):
                    if phase == "Kleine Amplituden":
                        small_idx += 1
                        # Random offset around the center, quantized to `small_step`.
                        dx_raw = int(np.random.randint(-self.small_radius, self.small_radius + 1))
                        dy_raw = int(np.random.randint(-self.small_radius, self.small_radius + 1))
                        dx = int(round(dx_raw / self.small_step)) * self.small_step
                        dy = int(round(dy_raw / self.small_step)) * self.small_step
                        tx = self._clamp('X', self.center_x + dx)
                        ty = self._clamp('Y', self.center_y + dy)
                        move_idx += 1
                        self._log_move("Kleine Amplituden", small_idx, est_total, tx, ty, move_idx, dwell)
                    else:
                        large_idx += 1
                        tx = int(np.random.randint(self.sc.low_lim['X'], self.sc.high_lim['X'] + 1))
                        ty = int(np.random.randint(self.sc.low_lim['Y'], self.sc.high_lim['Y'] + 1))
                        move_idx += 1
                        self._log_move("Große Amplituden", large_idx, est_total, tx, ty, move_idx, dwell)
                    if self.stop_at_ts and time.time() >= self.stop_at_ts:
                        break
                now = time.time()
                phase = "GroÃŸe Amplituden" if phase == "Kleine Amplituden" else "Kleine Amplituden"

            # Return to center if not aborted
            if self._running:
                try:
                    self.sc.move_abs('X', self.center_x)
                    self.sc.move_abs('Y', self.center_y)
                except Exception:
                    pass
        except Exception as exc:
            self.error.emit(str(exc))
            return
        finally:
            try:
                self.sc.move_abs('X', self.center_x)
                self.sc.move_abs('Y', self.center_y)
            except Exception:
                pass

        # Save results
        try:
            save_stage_test(
                str(self.savefile),
                self.pos_infodict,
                batch=self.batch,
                dur_max_um=DUR_MAX_UM,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return

        self.finished.emit({
            "out": str(self.savefile),
            "batch": self.batch,
            "out_dir": str(self.out_dir) if self.out_dir else "",
            "dur_max_um": float(self.max_abs_um),
            "limit_um": float(self.limit_um),
        })

#Dummy 
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
        # Encoderposition wird aus der Motorposition und der Achskalibrierung abgeleitet.
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


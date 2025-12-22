#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stage-spezifische Logik ohne GUI-Elemente.
Enthält PMAC-Bridge, Bewegungs-Helper und Dauertest-Worker.
"""

import csv
import datetime
import os
import pathlib
import re
import time

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Signal

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
DUR_MAX_UM = 8.0    # Max. |Fehler| erlaubt im Dauertest (Live + Abschluss)

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
            print("[INFO] Keine PMAC-Hardware gefunden – starte im Simulationsmodus.")

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

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def sanitize_batch(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "NoBatch"
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:64] or "NoBatch"


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
    st = {}
    pmac.getStagePosInfo(st)
    return st["xPos_encoderSteps"]


def getYencoder():
    st = {}
    pmac.getStagePosInfo(st)
    return st["yPos_encoderSteps"]


def motorsteps2encodersteps(motorStep, stepsPerMeter, encoderStepsPerMeter):
    return (motorStep / stepsPerMeter * encoderStepsPerMeter).astype(int)


def meas_linear(StartMotorSteps, StopMotorSteps, Steps, stepsPerMeter, encoderStepsPerMeter):
    motor_steps = np.linspace(StartMotorSteps, StopMotorSteps, Steps).astype(int)
    calc_encoder = motorsteps2encodersteps(motor_steps, stepsPerMeter, encoderStepsPerMeter)
    return motor_steps, calc_encoder


# ---------------------------------------------------------------------------
# Stage Controller + Worker
# ---------------------------------------------------------------------------
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

    def refresh(self):
        pmac.getStagePosInfo(self.status)

    def move_abs(self, a, s):
        pmac.setParam(f"ConfigRoot/DriverConfig/SamBoardCfg/MicroscopeState/{a}/stepPosition", int(s))

    def enc(self, a):
        self.refresh()
        return self.status[f"{a.lower()}Pos_encoderSteps"]


# Minimal Styling (keine GUI-Abhängigkeiten)
BG        = "#0b0b0f"
BG_ELEV   = "#121218"
FG_MUTED  = "#9ea0a6"
BORDER    = "#222230"


def _style_ax(ax):
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_color(BORDER)
        spine.set_linewidth(0.8)
    ax.grid(True)
    ax.tick_params(colors=FG_MUTED, labelsize=10)


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
        ax  = fig.add_subplot(111); _style_ax(ax)
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
        ax  = fig.add_subplot(111); _style_ax(ax)
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

    def stop(self):
        self._running=False

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


class CombinedTestWorker(QObject):
    update   = Signal(dict)
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(
        self,
        sc,
        *,
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
        self._running = False

    def _clamp(self, axis: str, val: int) -> int:
        lo = self.sc.low_lim[axis]
        hi = self.sc.high_lim[axis]
        return int(min(max(val, lo), hi))

    def _log_move(self, phase: str, idx: int, total: int, tx: int, ty: int, move_idx: int, dwell: float):
        self.sc.move_abs('X', tx)
        self.sc.move_abs('Y', ty)
        time.sleep(dwell)

        # Nutze gleiche Fehlerberechnung wie im Referenz-Dauertest:
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
        try:
            move_idx = 0
            phase = "Kleine Amplituden"
            small_idx = 0
            large_idx = 0
            now = time.time()
            target_stop = self.stop_at_ts or float("inf")

            while self._running and now < target_stop:
                phase_duration = self.small_phase_sec if phase == "Kleine Amplituden" else self.large_phase_sec
                dwell = self.dwell_small if phase == "Kleine Amplituden" else self.dwell_large
                phase_end = min(target_stop, now + phase_duration)
                # rough estimate for progress in this phase
                est_total = max(1, int(max(1.0, (phase_end - now) / max(0.01, dwell))))

                while self._running and (time.time() < phase_end):
                    if phase == "Kleine Amplituden":
                        small_idx += 1
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
                phase = "Große Amplituden" if phase == "Kleine Amplituden" else "Kleine Amplituden"

            # Zurück zur Mitte, wenn nicht abgebrochen
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
            save_stage_test(str(self.savefile), self.pos_infodict, batch=self.batch)
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


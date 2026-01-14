"""
Backend-Helfer fuer Autofocus/Kollimator-Kameras.
Enthaelt ausschliesslich Framework-Imports und kapselt die IdsCam-/LaserSpot-Logik.
"""
from __future__ import annotations

import atexit
from typing import Dict, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtGui import QImage, QColor, QPainter, QPen

from ie_Framework.Hardware.Camera.ids_camera import IdsCam
from ie_Framework.Algorithm.laser_spot_detection import LaserSpotDetector

_cams: Dict[int, IdsCam] = {}


def acquire_frame(device_index: int = 0):
    """Liefert ein aktuelles Frame der angegebenen IDS-Kamera (mit Cache)."""
    cam = _cams.get(device_index)
    if cam is None:
        cam = IdsCam(index=device_index, set_min_exposure=False)
        _cams[device_index] = cam
    return cam.aquise_frame()


def get_exposure_limits(device_index: int = 0) -> Tuple[int, int, int]:
    """Gibt aktuelle, minimale und maximale Exposure (in us) zurueck."""
    cam = IdsCam(index=device_index, set_min_exposure=False)
    try:
        return cam.get_exposure_limits_us()
    finally:
        try:
            cam.shutdown()
        except Exception:
            pass


def set_exposure(device_index: int, exposure_us: int) -> None:
    """Setzt die Exposure; nutzt bestehende Instanz oder legt eine neue an."""
    cam = _cams.get(device_index)
    if cam is None:
        cam = IdsCam(index=device_index, set_min_exposure=False)
        _cams[device_index] = cam
    cam.set_exposure_us(int(exposure_us))


def shutdown(device_index: int | None = None) -> None:
    """Beendet eine bestimmte Kamera oder alle gecachten Kameras."""
    if device_index is None:
        shutdown_all()
        return
    cam = _cams.pop(device_index, None)
    if cam is None:
        return
    try:
        cam.shutdown()
    except Exception:
        pass


def shutdown_all() -> None:
    """Beendet alle gecachten IDS-Kameras."""
    cams = list(_cams.items())
    _cams.clear()
    for _, cam in cams:
        try:
            cam.shutdown()
        except Exception:
            pass


def _ensure_gray8(frame: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Return (gray_frame_uint8, width, height) for any supported input frame."""
    if frame.ndim == 3 and frame.shape[2] == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if frame.dtype != np.uint8:
        max_val = float(frame.max() or 1.0)
        frame = np.clip(frame.astype(np.float32) / max_val * 255.0, 0, 255).astype(np.uint8)
    gray = np.ascontiguousarray(frame)
    h, w = gray.shape
    return gray, w, h


def _simulate_dummy_centroid(tick: int, width: int, height: int) -> tuple[int, int]:
    """Pseudo-random moving centroid for dummy mode (mirrors old detector logic)."""
    cx = int(width / 2 + np.sin(tick / 12.0) * (width * 0.25))
    cy = int(height / 2 + np.cos(tick / 15.0) * (height * 0.25))
    return cx, cy


def paint_laser_overlay(
    frame: np.ndarray,
    detector: LaserSpotDetector,
    *,
    accent_color: str = "#ff2740",
    ref_point: tuple[int, int] | None = None,
    is_dummy: bool = False,
    simulate_fn=None,
) -> tuple[QImage, tuple[int, int]]:
    """Draw laser overlays for a frame and return the QImage plus centroid."""
    gray, width, height = _ensure_gray8(frame)
    frame_bytes = gray.tobytes()
    if is_dummy:
        if simulate_fn is not None:
            cx, cy = simulate_fn(width, height)
        else:
            cx, cy = (width // 2, height // 2)
    else:
        cx, cy = detector.detect_laser_spot(gray)
    ref = ref_point
    qimg = QImage(frame_bytes, width, height, width, QImage.Format_Grayscale8).copy()
    if qimg.format() != QImage.Format_ARGB32:
        qimg = qimg.convertToFormat(QImage.Format_ARGB32)
    painter = QPainter(qimg)
    try:
        pen_cam = QPen(QColor(accent_color))
        pen_cam.setWidth(3)
        pen_cam.setStyle(Qt.DashLine)
        painter.setPen(pen_cam)
        cx0 = width // 2
        cy0 = height // 2
        painter.drawLine(cx0, 0, cx0, height)
        painter.drawLine(0, cy0, width, cy0)
    except Exception:
        pass
    try:
        pen_l = QPen(QColor(accent_color))
        pen_l.setWidth(3)
        pen_l.setCapStyle(Qt.RoundCap)
        painter.setPen(pen_l)
        size = max(6, min(width, height) // 20)
        painter.drawLine(max(0, cx - size), cy, min(width, cx + size), cy)
        painter.drawLine(cx, max(0, cy - size), cx, min(height, cy + size))
    except Exception:
        pass
    if ref is not None:
        try:
            rx, ry = ref
            pen_r = QPen(QColor("#ffd60a"))
            pen_r.setWidth(2)
            painter.setPen(pen_r)
            rsize = max(6, min(width, height) // 30)
            painter.drawEllipse(
                int(rx - rsize // 2),
                int(ry - rsize // 2),
                int(rsize),
                int(rsize),
            )
            pen_line = QPen(QColor("#ffd60a"))
            pen_line.setStyle(Qt.DashLine)
            painter.setPen(pen_line)
            painter.drawLine(rx, ry, cx, cy)
        except Exception:
            pass
    try:
        painter.end()
    except Exception:
        pass
    return qimg, (cx, cy)


class LiveLaserController(QObject):
    """Simple live controller that grabs frames and emits overlays for laser centering."""

    frameReady = Signal(QImage)
    centerChanged = Signal(int, int)

    def __init__(self, device_index: int, detector: LaserSpotDetector, parent=None):
        super().__init__(parent)
        self.device_index = device_index
        self.detector = detector
        self.cam: IdsCam | None = None
        self.is_dummy = False
        self._sim_tick = 0
        self._ref_point: tuple[int, int] | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._init_camera()

    def _init_camera(self):
        try:
            self.cam = IdsCam(index=self.device_index, set_min_exposure=False)
            self.is_dummy = bool(getattr(self.cam, "_dummy", False))
        except Exception as exc:
            self.cam = None
            self.is_dummy = True
            print(f"[WARN] Kamera konnte nicht initialisiert werden: {exc}")

    def start(self, interval_ms: int = 120):
        if not self._timer.isActive():
            self._timer.start(interval_ms)

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()

    def shutdown(self):
        self.stop()
        try:
            if self.cam is not None:
                self.cam.shutdown()
        except Exception:
            pass

    def _tick(self):
        if self.cam is None:
            return
        try:
            frame = self.cam.aquise_frame()
            if frame is None:
                return
            qimg, (cx, cy) = paint_laser_overlay(
                frame,
                self.detector,
                ref_point=self._ref_point,
                is_dummy=self.is_dummy,
                simulate_fn=self._next_dummy_centroid if self.is_dummy else None,
            )
            self.frameReady.emit(qimg)
            self.centerChanged.emit(int(cx), int(cy))
        except Exception as exc:
            print(f"[WARN] Live-Frame fehlgeschlagen: {exc}")

    # ---- Camera controls -------------------------------------------------
    def set_exposure_us(self, exposure_us: int):
        if self.cam is None:
            return
        try:
            self.cam.set_exposure_us(int(exposure_us))
        except Exception as exc:
            print(f"[WARN] Exposure setzen fehlgeschlagen: {exc}")

    def get_exposure_limits_us(self) -> tuple[float, float, float]:
        if self.cam is None:
            return 2000.0, 50.0, 200000.0
        try:
            return self.cam.get_exposure_limits_us()
        except Exception as exc:
            print(f"[WARN] Exposure-Limits nicht lesbar: {exc}")
            return 2000.0, 50.0, 200000.0

    def _next_dummy_centroid(self, width: int, height: int) -> tuple[int, int]:
        """Advance dummy centroid tick and return simulated center."""
        cx, cy = _simulate_dummy_centroid(self._sim_tick, width, height)
        self._sim_tick += 1
        return cx, cy

    # ---- Reference point handling ---------------------------------------
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
        if self.cam is None:
            return None
        try:
            return float(self.cam.get_pixel_size_um())
        except Exception:
            return None


atexit.register(shutdown_all)


__all__ = [
    "acquire_frame",
    "get_exposure_limits",
    "set_exposure",
    "shutdown",
    "shutdown_all",
    "IdsCam",
    "LaserSpotDetector",
    "LiveLaserController",
    "paint_laser_overlay",
]

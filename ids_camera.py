"""Lightweight wrapper around IDS peak cameras for the Stage-Toolbox."""

from __future__ import annotations

import ctypes
import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

try:
    from ids_peak import ids_peak as _ids_peak_module
    IDS_PEAK_AVAILABLE = True
    IDS_PEAK_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - hardware SDK optional
    _ids_peak_module = None
    IDS_PEAK_AVAILABLE = False
    IDS_PEAK_IMPORT_ERROR = exc
    print(f"[INFO] IDS peak SDK nicht verfügbar ({exc}); LiveView verwendet Demo-Bilder.")

IDS_PEAK_MODULE = _ids_peak_module


class CameraController(QLabel):
    """Live-view QLabel that handles IDS peak cameras and a simulation fallback."""

    centerChanged = Signal(int, int)
    frameReady = Signal(object, int, int)  # emits (bytes, width, height)

    def __init__(
        self,
        parent=None,
        *,
        device_index: int = 0,
        ids_peak_module=None,
        accent_color: str = "#ff2740",
    ):
        super().__init__(parent)
        self.setScaledContents(True)

        # External dependencies & configuration
        self.device_index = int(device_index)
        self._ids_peak = ids_peak_module if ids_peak_module is not None else _ids_peak_module
        self.accent_color = accent_color or "#ff2740"

        # Runtime state (shared between real & dummy modes)
        self.pixel_size_um: Optional[float] = None
        self.sensor_width_px: Optional[int] = None
        self.sensor_height_px: Optional[int] = None
        self._img_w: Optional[int] = None
        self._img_h: Optional[int] = None
        self._timer: Optional[QTimer] = None
        self._dummy_mode = False
        self._dummy_tick = 0
        self._dummy_exposure_us = 2000
        self._last_centroid: Optional[tuple[int, int]] = None

        self._init_camera()

    @property
    def is_dummy(self) -> bool:
        return bool(self._dummy_mode)

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------
    def _init_camera(self) -> None:
        """Connect to a real IDS camera, otherwise fall back to the dummy pipeline."""
        p = self._ids_peak
        if p is None:
            self._init_dummy_pipeline("IDS peak SDK nicht verfügbar.")
            return
        try:
            p.Library.Initialize()
            dm = p.DeviceManager.Instance()
            dm.Update()
            devs = dm.Devices()
            if not devs:
                raise RuntimeError("Keine IDS-Kamera gefunden.")
            idx = max(0, min(self.device_index, len(devs) - 1))
            self.dev = devs[idx].OpenDevice(p.DeviceAccessType_Control)
            self.remote = self.dev.RemoteDevice().NodeMaps()[0]

            for node, entry in [
                ("AcquisitionMode", "Continuous"),
                ("TriggerSelector", "FrameStart"),
                ("TriggerMode", "Off"),
                ("PixelFormat", "Mono8"),
            ]:
                try:
                    self.remote.FindNode(node).SetCurrentEntry(entry)
                except Exception:
                    pass

            self._configure_max_resolution()
            self.pixel_size_um = self._detect_pixel_size_um()

            self.ds = self.dev.DataStreams()[0].OpenDataStream()
            payload = self.remote.FindNode("PayloadSize").Value()
            self._bufs = [self.ds.AllocAndAnnounceBuffer(payload) for _ in range(3)]
            for buf in self._bufs:
                self.ds.QueueBuffer(buf)
            self.ds.StartAcquisition()
            try:
                self.remote.FindNode("AcquisitionStart").Execute()
            except Exception:
                pass

            self._timer = QTimer(self)
            self._timer.timeout.connect(self._grab)
            self._timer.start(0)
        except Exception as exc:
            self._init_dummy_pipeline(str(exc))

    def _configure_max_resolution(self) -> None:
        """Force the camera to use maximum width/height."""
        if not hasattr(self, "remote"):
            return

        def _set_node_to_max(name: str) -> Optional[int]:
            try:
                node = self.remote.FindNode(name)
            except Exception:
                return None
            max_val = None
            try:
                max_val = int(getattr(node, "Maximum")())
            except Exception:
                try:
                    max_node = self.remote.FindNode(f"{name}Max")
                    max_val = int(max_node.Value())
                except Exception:
                    pass
            try:
                if max_val is not None:
                    node.SetValue(max_val)
            except Exception:
                pass
            try:
                return int(node.Value())
            except Exception:
                return None

        self.sensor_width_px = _set_node_to_max("Width")
        self.sensor_height_px = _set_node_to_max("Height")
        try:
            sensor_w = int(self.remote.FindNode("SensorWidth").Value())
            if sensor_w:
                self.sensor_width_px = sensor_w
        except Exception:
            pass
        try:
            sensor_h = int(self.remote.FindNode("SensorHeight").Value())
            if sensor_h:
                self.sensor_height_px = sensor_h
        except Exception:
            pass

    def _detect_pixel_size_um(self) -> float:
        """Read pixel size from the camera node map with unit heuristics."""
        p = self._ids_peak
        if p is None or not hasattr(self, "remote"):
            raise RuntimeError("Keine Kamera-Remote für Pixelgröße verfügbar.")
        candidates = [
            "SensorPixelWidth",
            "SensorPixelHeight",
            "PixelSize",
            "SensorPixelSize",
        ]
        for name in candidates:
            try:
                node = self.remote.FindNode(name)
                val = float(node.Value())
                if val < 0.001:
                    val_um = val * 1e6
                elif val < 1.0:
                    val_um = val * 1e3
                else:
                    val_um = val
                print(f"[INFO] Pixelgröße aus Node {name}: {val_um:.3f} µm")
                return val_um
            except Exception:
                continue
        raise RuntimeError("Pixelgröße konnte nicht aus der Kamera gelesen werden.")

    def _init_dummy_pipeline(self, reason: str | None = None) -> None:
        """Set up simulation mode with a moving laser dot."""
        self._dummy_mode = True
        if reason:
            print(f"[SIM][CameraController] Verwende Demo-Modus ({reason}).")
        self.pixel_size_um = 2.2
        self.sensor_width_px = 1280
        self.sensor_height_px = 1024
        self._img_w = self.sensor_width_px
        self._img_h = self.sensor_height_px
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._grab_dummy)
        self._timer.start(50)

    # ------------------------------------------------------------------
    # Public controls
    # ------------------------------------------------------------------
    def set_exposure_us(self, val_us: float) -> None:
        """Apply exposure (µs) to the active camera/dummy pipeline."""
        if getattr(self, "_dummy_mode", False):
            self._dummy_exposure_us = int(max(1, round(float(val_us))))
            print(
                f"[SIM][CameraController] Exposure gesetzt auf {self._dummy_exposure_us} µs (Demo)."
            )
            return
        if not hasattr(self, "remote") or self._ids_peak is None:
            return
        try:
            try:
                self.remote.FindNode("ExposureAuto").SetCurrentEntry("Off")
            except Exception:
                pass
            node = None
            for name in ("ExposureTime", "ExposureTimeAbs", "ExposureTimeUs"):
                try:
                    candidate = self.remote.FindNode(name)
                    _ = candidate.Value()
                    node = candidate
                    break
                except Exception:
                    continue
            if node is None:
                raise RuntimeError("ExposureTime-Knoten nicht gefunden.")
            try:
                mn = int(max(1, round(node.Minimum())))
                mx = int(round(node.Maximum()))
                value = int(min(mx, max(mn, int(round(float(val_us))))))
            except Exception:
                value = int(round(float(val_us)))
            node.SetValue(float(value))
            print(f"[INFO] CameraController: Exposure gesetzt auf {value} µs")
        except Exception as exc:
            print("[WARN] CameraController: Exposure setzen fehlgeschlagen:", exc)

    # ------------------------------------------------------------------
    # Frame grabbing (real/dummy)
    # ------------------------------------------------------------------
    def _grab(self) -> None:
        if getattr(self, "_dummy_mode", False):
            self._grab_dummy()
            return
        try:
            buf = self.ds.WaitForFinishedBuffer(50)
        except Exception:
            return
        if not buf:
            return
        try:
            w, h, ptr, size = (
                buf.Width(),
                buf.Height(),
                int(buf.BasePtr()),
                buf.Size(),
            )
            self._img_w = int(w)
            self._img_h = int(h)
            arr = (ctypes.c_ubyte * size).from_address(ptr)
            try:
                frame_bytes = bytes(memoryview(arr)[: w * h])
            except Exception:
                view = memoryview(arr)[: w * h]
                frame_bytes = bytes(view)
            self._emit_frame(frame_bytes, w, h)
        finally:
            try:
                self.ds.QueueBuffer(buf)
            except Exception:
                pass

    def _grab_dummy(self) -> None:
        try:
            w = int(self.sensor_width_px or 640)
            h = int(self.sensor_height_px or 480)
            self._img_w = w
            self._img_h = h
            frame = np.zeros((h, w), dtype=np.uint8)
            frame_bytes = frame.tobytes()
            self._emit_frame(frame_bytes, w, h)
        except Exception:
            pass

    def _emit_frame(self, frame_bytes: bytes, w: int, h: int) -> None:
        try:
            self.frameReady.emit(frame_bytes, w, h)
        except Exception:
            pass

    def update_centroid(self, cx: int, cy: int) -> None:
        """Update centroid from external detector and notify listeners."""
        self._last_centroid = (int(cx), int(cy))
        try:
            self.centerChanged.emit(int(cx), int(cy))
        except Exception:
            pass

    def display_frame(self, qimg: QImage) -> None:
        try:
            self.setPixmap(QPixmap.fromImage(qimg))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Release timers, camera handles and buffers."""
        try:
            if self._timer:
                self._timer.stop()
        except Exception:
            pass
        if not getattr(self, "_dummy_mode", False):
            p = self._ids_peak
            for fn in (
                lambda: self.remote.FindNode("AcquisitionStop").Execute()
                if hasattr(self, "remote")
                else None,
                self.ds.StopAcquisition if hasattr(self, "ds") else None,
                lambda: [self.ds.RevokeBuffer(b) for b in self._bufs]
                if hasattr(self, "_bufs")
                else None,
                self.dev.Close if hasattr(self, "dev") else None,
                p.Library.Close if p is not None else None,
            ):
                if fn is None:
                    continue
                try:
                    fn()
                except Exception:
                    pass
        super().close()

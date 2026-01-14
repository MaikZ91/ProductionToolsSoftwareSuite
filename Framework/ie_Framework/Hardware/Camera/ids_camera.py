"""Minimal IDS peak camera wrapper with optional live streaming helper."""

from __future__ import annotations

import ctypes
import time
import threading
from typing import Optional

import numpy as np

"""
Requires the IDS peak SDK (including the Python bindings) to be installed
for real camera operation: https://en.ids-imaging.com/ids-peak-sdk.html
"""
try:
    from ids_peak import ids_peak as _ids_peak
    IDS_PEAK_AVAILABLE = True
    IDS_PEAK_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:
    _ids_peak = None
    IDS_PEAK_AVAILABLE = False
    IDS_PEAK_IMPORT_ERROR = exc


class IdsCam:
    """
    Minimal IDS peak camera wrapper.

    - Auto-initializes IDS peak when available
    - Selects Mono12 if supported (else Mono8)
    - Sets max resolution and (optionally) minimum exposure
    - Starts acquisition immediately

    If the SDK is missing or no camera is found, the class falls back to
    a dummy mode and returns synthetic black frames instead of raising.
    """

    def __init__(self, index: int = 0, *, set_min_exposure: bool = True) -> None:
        """
        Parameters
        ----------
        index : int
            Camera index in the IDS DeviceManager (ignored in dummy mode).
        set_min_exposure : bool
            If True, exposure is forced to the minimum on init. Disable when
            you only want to query limits without touching the camera.
        """
        self.index = index
        self.dev = None
        self.remote = None
        self.ds = None
        self.buf = None
        self.width = 0
        self.height = 0
        self.pixel_size_um = None
        self.pixel_format = "Mono8"
        self._dummy = False
        self._set_min_exposure_on_init = bool(set_min_exposure)
        self._exposure_us: float | None = None
        self._init_camera()

    def _init_camera(self) -> None:
        """Initialize real IDS camera if possible, otherwise switch to dummy mode."""
        if _ids_peak is None:
            self._init_dummy("IDS peak SDK not available")
            return
        try:
            _ids_peak.Library.Initialize()
            dm = _ids_peak.DeviceManager.Instance()
            dm.Update()
            devs = dm.Devices()
            if not devs:
                self._init_dummy("no IDS camera found")
                return

            self.dev = devs[self.index].OpenDevice(_ids_peak.DeviceAccessType_Control)
            self.remote = self.dev.RemoteDevice().NodeMaps()[0]

            self.pixel_format = self._select_pixel_format()
            self.remote.FindNode("PixelFormat").SetCurrentEntry(self.pixel_format)
            self.remote.FindNode("AcquisitionMode").SetCurrentEntry("Continuous")
            self.remote.FindNode("TriggerMode").SetCurrentEntry("Off")

            w = self.remote.FindNode("Width")
            h = self.remote.FindNode("Height")
            w.SetValue(int(w.Maximum()))
            h.SetValue(int(h.Maximum()))
            self.width = int(w.Value())
            self.height = int(h.Value())

            self.pixel_size_um = float(self.remote.FindNode("SensorPixelWidth").Value())

            if self._set_min_exposure_on_init:
                self._set_min_exposure()
            else:
                try:
                    node = self._find_exposure_node()
                    self._exposure_us = float(node.Value())
                except Exception:
                    self._exposure_us = None

            self.ds = self.dev.DataStreams()[0].OpenDataStream()
            payload = self.remote.FindNode("PayloadSize").Value()
            self.buf = self.ds.AllocAndAnnounceBuffer(payload)
            self.ds.QueueBuffer(self.buf)

            self.ds.StartAcquisition()
            self.remote.FindNode("AcquisitionStart").Execute()
        except Exception as exc:
            self._init_dummy(f"camera init failed: {exc}")

    def _init_dummy(self, reason: str) -> None:
        """
        Enter dummy mode (no SDK or no camera).

        Frames returned by aquise_frame() will be black images with a fixed size.
        """
        self._dummy = True
        self.dev = None
        self.remote = None
        self.ds = None
        self.buf = None
        self.width = 640
        self.height = 480
        self.pixel_size_um = 2.2
        self.pixel_format = "Mono8"
        self._exposure_us = 2000.0
        print(f"[IdsCam] Dummy mode active ({reason}).")

    def _select_pixel_format(self) -> str:
        """Return Mono12 if supported by the camera, otherwise Mono8."""
        entries = {
            e.SymbolicValue() for e in self.remote.FindNode("PixelFormat").Entries()
        }
        return "Mono12" if "Mono12" in entries else "Mono8"

    def _find_exposure_node(self):
        """Find the exposure node across common naming variants."""
        for name in ("ExposureTime", "ExposureTimeAbs", "ExposureTimeUs"):
            try:
                n = self.remote.FindNode(name)
                _ = n.Value()
                return n
            except Exception:
                continue
        raise RuntimeError("ExposureTime node not found.")

    def _set_min_exposure(self) -> None:
        """Set exposure time to the minimum supported value."""
        n = self._find_exposure_node()
        exp = float(n.Minimum())
        n.SetValue(exp)
        self._exposure_us = exp

    def set_exposure_us(self, us: float) -> None:
        """
        Set exposure time in microseconds. Has no effect in dummy mode.

        Parameters
        ----------
        us : float
            Desired exposure time.
        """
        if self._dummy or self.remote is None:
            self._exposure_us = float(us)
            return
        n = self._find_exposure_node()
        mn, mx = float(n.Minimum()), float(n.Maximum())
        exp = max(mn, min(mx, float(us)))
        n.SetValue(exp)
        self._exposure_us = exp

    def get_exposure_limits_us(self) -> tuple[float, float, float]:
        """
        Return (current, minimum, maximum) exposure times in microseconds.

        In dummy mode, returns a fixed placeholder range.
        """
        if self._dummy or self.remote is None:
            cur = float(self._exposure_us if self._exposure_us is not None else 2000.0)
            return cur, 50.0, 200000.0
        node = self._find_exposure_node()
        cur = float(node.Value())
        mn = float(node.Minimum())
        mx = float(node.Maximum())
        self._exposure_us = cur
        return cur, mn, mx

    def set_resolution(self, width: int, height: int) -> None:
        """
        Set sensor resolution. In dummy mode, this only changes the dummy frame size.

        Parameters
        ----------
        width : int
            Desired width.
        height : int
            Desired height.
        """
        if self._dummy or self.remote is None:
            self.width = int(width)
            self.height = int(height)
            return
        wn = self.remote.FindNode("Width")
        hn = self.remote.FindNode("Height")
        wn.SetValue(max(int(wn.Minimum()), min(int(wn.Maximum()), int(width))))
        hn.SetValue(max(int(hn.Minimum()), min(int(hn.Maximum()), int(height))))
        self.width = int(wn.Value())
        self.height = int(hn.Value())

    def get_pixel_size_um(self) -> float:
        """
        Return physical pixel size in µm.

        In dummy mode, this returns a fixed placeholder value.
        """
        return float(self.pixel_size_um)

    def get_resolution(self) -> tuple[int, int]:
        """Return current resolution as (width, height)."""
        return self.width, self.height

    def get_model_info(self) -> dict:
        """
        Return basic camera metadata.

        In dummy mode, fields will contain placeholder values.
        """
        if self._dummy or self.dev is None:
            return {
                "model": "DUMMY",
                "serial": "DUMMY",
                "id": "DUMMY",
                "display_name": "IdsCam Dummy",
                "pixel_format": self.pixel_format,
            }
        return {
            "model": self.dev.ModelName(),
            "serial": self.dev.SerialNumber(),
            "id": self.dev.ID(),
            "display_name": self.dev.DisplayName(),
            "pixel_format": self.pixel_format,
        }

    def aquise_frame(self, timeout_ms: int = 50) -> np.ndarray:
        """
        Capture a single frame.

        Returns
        -------
        numpy.ndarray
            uint8 for Mono8, uint16 for Mono12. In dummy mode, a black frame.
        """
        if self._dummy or self.ds is None:
            return np.zeros((self.height, self.width), dtype=np.uint8)

        buf = self.ds.WaitForFinishedBuffer(timeout_ms)
        w, h = buf.Width(), buf.Height()
        ptr, size = int(buf.BasePtr()), buf.Size()
        arr = (ctypes.c_ubyte * size).from_address(ptr)

        bpp = 1 if self.pixel_format == "Mono8" else 2
        raw = bytes(memoryview(arr)[: w * h * bpp])
        dtype = np.uint8 if bpp == 1 else np.uint16
        frame = np.frombuffer(raw, dtype=dtype).reshape(h, w)

        self.ds.QueueBuffer(buf)
        return frame

    def start_stream(self, callback, interval_s: float = 0.0) -> threading.Event:
        """
        Start a simple live acquisition loop that calls `callback(frame)`.

        This runs in a background thread and works for both real camera
        and dummy mode.

        Parameters
        ----------
        callback : callable
            Function that will be called as callback(frame) for each frame.
        interval_s : float
            Optional sleep time between frames in seconds
            (0.0 = as fast as possible).

        Returns
        -------
        threading.Event
            Event that can be set() to stop the stream.
        """
        stop_event = threading.Event()

        def _loop():
            while not stop_event.is_set():
                frame = self.aquise_frame()
                callback(frame)
                if interval_s > 0:
                    time.sleep(interval_s)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        return stop_event

    def shutdown(self) -> None:
        """
        Stop acquisition and release resources.

        Safe to call in dummy mode; no-op if no real camera was opened.
        """
        if self._dummy:
            return
        try:
            if self.remote is not None:
                self.remote.FindNode("AcquisitionStop").Execute()
            if self.ds is not None:
                self.ds.StopAcquisition()
                if self.buf is not None:
                    self.ds.RevokeBuffer(self.buf)
            if self.dev is not None:
                self.dev.Close()
            if _ids_peak is not None:
                _ids_peak.Library.Close()
        except Exception:
            pass

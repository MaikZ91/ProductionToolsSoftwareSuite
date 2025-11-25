"""Reusable laser spot centroid detection."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen


class LaserSpotDetector:
    """Finds the centroid of the brightest spot in a grayscale frame."""

    def __init__(self, min_window: int = 11, max_window: int = 41):
        self.min_window = min_window
        self.max_window = max_window
        self._sim_tick = 0
        self._ref_point: tuple[int, int] | None = None

    def set_reference_point(self, x: int | None, y: int | None) -> None:
        """Store or clear the current reference point."""
        if x is None or y is None:
            self._ref_point = None
        else:
            self._ref_point = (int(x), int(y))

    def clear_reference_point(self) -> None:
        self._ref_point = None

    def get_reference_point(self) -> tuple[int, int] | None:
        return self._ref_point

    def detect(self, frame_bytes: bytes, width: int, height: int) -> tuple[int, int]:
        """Return the centroid (x, y) in pixel coordinates."""
        img = (
            np.frombuffer(frame_bytes, dtype=np.uint8)
            .reshape((height, width))
            .astype(np.float32)
        )
        try:
            background = float(np.median(img))
            img_sub = np.maximum(img - background, 0.0)
        except Exception:
            img_sub = img

        idx = int(np.argmax(img_sub))
        py, px = divmod(idx, width)

        win = min(self.max_window, max(self.min_window, min(height, width) // 10))
        hw = max(1, win // 2)
        x0 = max(0, px - hw)
        x1 = min(width, px + hw + 1)
        y0 = max(0, py - hw)
        y1 = min(height, py + hw + 1)
        sub = img_sub[y0:y1, x0:x1]
        total = float(sub.sum())
        if total > 0:
            ys, xs = np.indices(sub.shape)
            cx_local = float((sub * xs).sum() / total)
            cy_local = float((sub * ys).sum() / total)
            cx = int(round(x0 + cx_local))
            cy = int(round(y0 + cy_local))
        else:
            cx, cy = (px, py) if img_sub.sum() > 0 else (width // 2, height // 2)
        return cx, cy

    def simulate_centroid(self, width: int, height: int) -> tuple[int, int]:
        """Return a pseudo-random moving centroid for dummy mode."""
        t = self._sim_tick
        self._sim_tick += 1
        cx = int(width / 2 + np.sin(t / 12.0) * (width * 0.25))
        cy = int(height / 2 + np.cos(t / 15.0) * (height * 0.25))
        return cx, cy

    def process_frame(
        self,
        frame_bytes: bytes,
        width: int,
        height: int,
        accent_color: str,
        ref_point: tuple[int, int] | None = None,
        is_dummy: bool = False,
    ) -> tuple[QImage, tuple[int, int]]:
        """Return a painted QImage plus centroid coordinates."""
        qimg = QImage(frame_bytes, width, height, width, QImage.Format_Grayscale8).copy()
        if is_dummy:
            cx, cy = self.simulate_centroid(width, height)
        else:
            cx, cy = self.detect(frame_bytes, width, height)
        if ref_point is None:
            ref_point = self._ref_point
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
        if ref_point is not None:
            try:
                rx, ry = ref_point
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

"""Reusable laser spot centroid detection"""

from __future__ import annotations

import numpy as np


class LaserSpotDetector:
    """Compute the centroid of the brightest spot in a grayscale image.

    The detector expects a 2D grayscale numpy array and returns the
    centroid position of the brightest area as integer pixel coordinates.
    """

    @staticmethod
    def detect_laser_spot(
        frame: np.ndarray,
    ) -> tuple[int, int]:
        """
        Return centroid `(x, y)` of the brightest spot in a grayscale frame.

        Parameters:
            frame: Grayscale image as 2D array (uint8 preferred).

        Returns:
            Centroid coordinates as `(x, y)` in pixel space.
        """
        if frame.ndim != 2:
            raise ValueError("detect_laser_spot expects a 2D grayscale array")

        img = frame.astype(np.float32, copy=False)
        height, width = img.shape

        # Clamp the local refinement window to a reasonable range.
        min_window = 11
        max_window = 41

        # Subtract median background to dampen uniform illumination.
        background = float(np.median(img))
        img_sub = np.maximum(img - background, 0.0)

        # Find global maximum as a first pass.
        idx = int(np.argmax(img_sub))
        py, px = divmod(idx, width)

        # Use a local window around the maximum to compute weighted centroid.
        win = min(max_window, max(min_window, min(height, width) // 10))
        hw = max(1, win // 2)
        x0 = max(0, px - hw)
        x1 = min(width, px + hw + 1)
        y0 = max(0, py - hw)
        y1 = min(height, py + hw + 1)
        sub = img_sub[y0:y1, x0:x1]

        total = float(sub.sum())
        if total > 0.0:
            ys, xs = np.indices(sub.shape)
            cx_local = float((sub * xs).sum() / total)
            cy_local = float((sub * ys).sum() / total)
            cx = int(round(x0 + cx_local))
            cy = int(round(y0 + cy_local))
        else:
            # Fallback: return image center when no contrast is found.
            cx, cy = width // 2, height // 2
        return cx, cy

"""Laser spot centroid detection.

Two complementary approaches:
- Intensity-weighted centroid: fast, robust for a single smooth spot (tracking).
- Otsu segmentation centroid: explicit spot masking, better for messy backgrounds
  or irregular spot shapes (analysis/validation).
"""

from __future__ import annotations

import cv2
import numpy as np
import scipy.ndimage
from skimage import filters


class LaserSpotDetector:
    """Detect a laser spot centroid in 2D grayscale frames.

    Returns pixel coordinates as (x, y). Use the intensity-based method for
    fast tracking; use the Otsu-based method when the background is messy or
    the spot shape is irregular.
    """

    @staticmethod
    def detect_laser_spot(frame: np.ndarray) -> tuple[int, int]:
        """Return centroid (x, y) using an intensity-weighted local centroid.

        How it works:
        - subtracts median background to reduce uniform illumination
        - seeds with the global maximum
        - refines in a local window via intensity-weighted centroid

        Best for:
        - real-time tracking / control loops
        - single dominant spot with smooth intensity profile

        Notes:
        - If the frame has no usable contrast, returns the image center.
        """
        if frame.ndim != 2:
            raise ValueError("detect_laser_spot expects a 2D grayscale array")

        img = frame.astype(np.float32, copy=False)
        height, width = img.shape

        # Background suppression (robust against uniform illumination).
        background = float(np.median(img))
        img_sub = np.maximum(img - background, 0.0)

        # Seed with the brightest pixel.
        idx = int(np.argmax(img_sub))
        py, px = divmod(idx, width)

        # Clamp local refinement window size.
        min_window = 11
        max_window = 41
        win = min(max_window, max(min_window, min(height, width) // 10))
        hw = max(1, win // 2)

        # Extract local ROI around the peak.
        x0 = max(0, px - hw)
        x1 = min(width, px + hw + 1)
        y0 = max(0, py - hw)
        y1 = min(height, py + hw + 1)
        sub = img_sub[y0:y1, x0:x1]

        total = float(sub.sum())
        if total <= 0.0:
            return width // 2, height // 2

        ys, xs = np.indices(sub.shape)
        cx_local = float((sub * xs).sum() / total)
        cy_local = float((sub * ys).sum() / total)

        cx = int(round(x0 + cx_local))
        cy = int(round(y0 + cy_local))
        return cx, cy

    @staticmethod
    def detect_laser_spot_otsu(
        frame: np.ndarray,
    ) -> tuple[int, int, np.ndarray | None]:
        """Return centroid (x, y) using Otsu segmentation + center of mass.

        How it works:
        - thresholds the image via Otsu to segment bright regions
        - selects the largest connected component (contour)
        - refines centroid via intensity center-of-mass within a mask

        Best for:
        - offline analysis / validation
        - structured backgrounds, reflections, irregular spot shapes

        Notes:
        - Slower and can be sensitive to threshold artifacts.
        - If no contour is found, returns image center and None.
        """
        if frame.ndim != 2:
            raise ValueError("detect_laser_spot_otsu expects a 2D grayscale array")

        height, width = frame.shape

        # Otsu threshold and binarize (normalize to stabilize around mid-gray).
        otsu_threshold = filters.threshold_otsu(frame)
        norm = 127 / otsu_threshold if otsu_threshold else 1.0
        _, binary_image = cv2.threshold(
            (frame * norm).astype(np.uint8),
            127,
            255,
            cv2.THRESH_BINARY,
        )

        contours, _ = cv2.findContours(
            binary_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            return width // 2, height // 2, None

        # Use largest blob as the spot candidate.
        contour = max(contours, key=cv2.contourArea)

        # Build a compact mask (enclosing circle) for center-of-mass refinement.
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        mask = np.zeros_like(binary_image)
        cv2.circle(mask, (int(cx), int(cy)), int(radius), 255, -1)

        cy_f, cx_f = scipy.ndimage.center_of_mass(frame, mask)
        return int(cx_f), int(cy_f), contour

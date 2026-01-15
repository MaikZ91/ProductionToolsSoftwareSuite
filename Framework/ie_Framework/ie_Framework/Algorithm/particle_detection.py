"""
Particle detection utilities extracted from gitterschieber.
- blend_overlay_and_annotate(base_bgr, overlay_bgr, count, alpha)
- particle_detection(img_or_path, ...)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np
import pandas as pd
from PIL import Image


def blend_overlay_and_annotate(base_bgr: np.ndarray, overlay_bgr: np.ndarray, count: int, alpha: float = 0.55) -> np.ndarray:
    """Blendet Overlay halbtransparent ein und versieht es mit Count-Label."""
    if base_bgr.shape[:2] != overlay_bgr.shape[:2]:
        overlay_bgr = cv2.resize(overlay_bgr, (base_bgr.shape[1], base_bgr.shape[0]), interpolation=cv2.INTER_AREA)
    comp = cv2.addWeighted(overlay_bgr, alpha, base_bgr, 1.0 - alpha, 0.0)
    label = f"Particles: {count}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.9
    thickness = 2
    (tw, th), _ = cv2.getTextSize(label, font, scale, thickness)
    pad = 10
    x0, y0 = 15, 20
    box = comp.copy()
    cv2.rectangle(box, (x0 - pad, y0 - pad), (x0 + tw + pad, y0 + th + pad), (0, 0, 0), -1)
    comp = cv2.addWeighted(box, 0.35, comp, 0.65, 0.0)
    cv2.putText(comp, label, (x0, y0 + th), font, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
    cv2.putText(comp, label, (x0, y0 + th), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
    return comp


def particle_detection(
    img_or_path: Any,
    *,
    sensitivity: float = 0.66,
    dog_sigma_small: float = 3.0,
    dog_sigma_large: float = 9.0,
    bg_sigma_min: float = 21.0,
    bg_sigma_max: float = 14.0,
    min_circ_min: float = 0.70,
    min_circ_max: float = 0.55,
    min_contrast_min: float = 0.20,
    min_contrast_max: float = 0.10,
    min_dist_min: int = 11,
    min_dist_max: int = 8,
    min_diam_px: int = 10,
    max_diam_px: int = 50,
    border_exclude: int = 10,
    fft_sigma_k: float = 2.5,
    fft_search_r_factor: float = 0.18,
    save_dir: str | Path | None = None,
    return_intermediates: bool = False,
    return_overlay_on: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, pd.DataFrame] | Tuple[np.ndarray, np.ndarray, pd.DataFrame, Dict[str, np.ndarray]]:
    """
    One-call Partikeldetektion inkl. FFT-Gitterentfernung.
    - img_or_path: Pfad zu einem Bild oder np.ndarray (HxW oder HxWx3, uint8/float).
    - sensitivity: 0..1 (beeinflusst Schwellenwerte).
    Gibt Overlay, Maske, DataFrame (cx,cy,area_px,circularity,equiv_diam_px) zurueck.
    Optional: intermediates mit 'filtered','g_corr','dog','thresh'.
    """

    def _as_gray_f32(img):
        if isinstance(img, (str, Path)):
            arr = cv2.imread(str(img), cv2.IMREAD_UNCHANGED)
            if arr is None:
                raise FileNotFoundError(f"Bild konnte nicht geladen werden: {img}")
        else:
            arr = img
        if arr is None:
            raise ValueError("Kein Bild uebergeben")
        if arr.ndim == 2:
            gray = arr
        else:
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        return gray.astype(np.float32), arr

    def _filter_image_grid(gray_f32: np.ndarray, sigma_k: float = 2.5, search_r_factor: float = 0.18) -> np.ndarray:
        h, w = gray_f32.shape
        cy, cx = h // 2, w // 2
        inner_keep_r = max(4, int(min(h, w) * 0.006))
        notch_r = max(3, int(min(h, w) * 0.008))
        search_r = max(4, int(min(h, w) * search_r_factor))
        max_peaks = 400

        fft = np.fft.fft2(gray_f32)
        fft_shift = np.fft.fftshift(fft)
        mag = np.log1p(np.abs(fft_shift))

        Y, X = np.ogrid[:h, :w]
        d2 = (Y - cy) ** 2 + (X - cx) ** 2
        search_zone = (d2 > inner_keep_r**2) & (d2 < search_r**2)
        thresh = float(mag.mean() + sigma_k * mag.std())
        ys, xs = np.nonzero(search_zone & (mag > thresh))

        mask_f = np.ones((h, w), dtype=np.float32)

        def notch(m, y0, x0, r):
            y1 = max(0, y0 - r)
            y2 = min(h, y0 + r + 1)
            x1 = max(0, x0 - r)
            x2 = min(w, x0 + r + 1)
            yy, xx = np.ogrid[y1:y2, x1:x2]
            sub = m[y1:y2, x1:x2]
            sub[(yy - y0) ** 2 + (xx - x0) ** 2 <= r * r] = 0.0
            m[y1:y2, x1:x2] = sub

        if ys.size:
            order = np.argsort(-mag[ys, xs])[:max_peaks]
            for y0, x0 in zip(ys[order], xs[order]):
                notch(mask_f, y0, x0, notch_r)
                ys2, xs2 = 2 * cy - y0, 2 * cx - x0
                if 0 <= ys2 < h and 0 <= xs2 < w:
                    notch(mask_f, ys2, xs2, notch_r)

        # DC erhalten
        y1 = max(0, cy - inner_keep_r)
        y2 = min(h, cy + inner_keep_r + 1)
        x1 = max(0, cx - inner_keep_r)
        x2 = min(w, cx + inner_keep_r + 1)
        yy, xx = np.ogrid[y1:y2, x1:x2]
        sub = mask_f[y1:y2, x1:x2]
        sub[(yy - cy) ** 2 + (xx - cx) ** 2 <= inner_keep_r**2] = 1.0
        mask_f[y1:y2, x1:x2] = sub

        img_f = np.real(np.fft.ifft2(np.fft.ifftshift(fft_shift * mask_f)))
        mn, mx = float(img_f.min()), float(img_f.max())
        if np.isfinite(mn) and np.isfinite(mx) and (mx - mn) >= 1e-12:
            return (255.0 * (img_f - mn) / (mx - mn)).clip(0, 255).astype(np.uint8)
        return np.zeros_like(gray_f32, dtype=np.uint8)

    gray_f32, orig_bgr_or_gray = _as_gray_f32(img_or_path)
    filtered_u8 = _filter_image_grid(gray_f32, sigma_k=fft_sigma_k, search_r_factor=fft_search_r_factor)

    # Parameter aus Sensitivity ableiten
    bg_sigma = float(np.interp(sensitivity, [0, 1], [bg_sigma_min, bg_sigma_max]))
    min_circularity = float(np.interp(sensitivity, [0, 1], [min_circ_min, min_circ_max]))
    min_contrast_rel = float(np.interp(sensitivity, [0, 1], [min_contrast_min, min_contrast_max]))
    min_dist_px = int(np.interp(sensitivity, [0, 1], [min_dist_min, min_dist_max]))

    g = filtered_u8.astype(np.float32)
    bg = cv2.GaussianBlur(g, (0, 0), bg_sigma)
    g_corr = cv2.subtract(g, bg)
    g_corr = cv2.normalize(g_corr, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    g_corr = cv2.bilateralFilter(g_corr, d=5, sigmaColor=10, sigmaSpace=6)

    if dog_sigma_large <= dog_sigma_small:
        dog_sigma_large = dog_sigma_small + 0.2
    small = cv2.GaussianBlur(g_corr, (0, 0), dog_sigma_small)
    large = cv2.GaussianBlur(g_corr, (0, 0), dog_sigma_large)
    dog = cv2.subtract(small, large)
    dog = cv2.normalize(dog, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    _, th = cv2.threshold(dog, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)

    # Randbereiche ausschliessen
    th[:border_exclude, :] = 0
    th[-border_exclude:, :] = 0
    th[:, :border_exclude] = 0
    th[:, -border_exclude:] = 0

    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = g.shape
    min_area = np.pi * (min_diam_px / 2.0) ** 2
    max_area = np.pi * (max_diam_px / 2.0) ** 2

    centers, kept, rows, radii = [], [], [], []
    for c in cnts:
        area = cv2.contourArea(c)
        if not (min_area <= area <= max_area):
            continue

        perim = cv2.arcLength(c, True)
        if perim <= 1e-6:
            continue
        circularity = 4.0 * np.pi * area / (perim * perim)
        if circularity < min_circularity:
            continue

        x, y, cw, ch = cv2.boundingRect(c)
        x1, y1 = max(0, x - 5), max(0, y - 5)
        x2, y2 = min(w, x + cw + 5), min(h, y + ch + 5)
        roi = g_corr[y : y + ch, x : x + cw]
        rim = g_corr[y1:y2, x1:x2]
        obj_mean = float(np.mean(roi)) if roi.size else 0.0
        sur_mean = float(np.mean(rim)) if rim.size else 1.0
        rel_contrast = abs(obj_mean - sur_mean) / max(1.0, sur_mean)
        if rel_contrast < min_contrast_rel:
            continue

        M = cv2.moments(c)
        cx = M["m10"] / M["m00"] if M["m00"] else x + cw / 2
        cy = M["m01"] / M["m00"] if M["m00"] else y + ch / 2

        if any((cx - px) ** 2 + (cy - py) ** 2 < min_dist_px**2 for px, py in centers):
            continue

        centers.append((cx, cy))
        kept.append(c)
        equiv_diam = 2.0 * np.sqrt(area / np.pi)
        rows.append(
            {
                "cx": cx,
                "cy": cy,
                "area_px": area,
                "circularity": circularity,
                "equiv_diam_px": equiv_diam,
            }
        )
        r_eq = max(3.0, equiv_diam / 2.0)
        radii.append(int(round(r_eq + 6.0)))

    overlay = cv2.cvtColor(g_corr, cv2.COLOR_GRAY2BGR)
    mask = np.zeros_like(g_corr, dtype=np.uint8)
    cv2.drawContours(mask, kept, -1, 255, thickness=-1)
    cv2.drawContours(overlay, kept, -1, (255, 0, 0), 3)
    for (cx, cy), r in zip(centers, radii):
        cv2.circle(overlay, (int(round(cx)), int(round(cy))), r, (0, 255, 255), 3)

    df = pd.DataFrame(rows)
    intermediates = {"filtered": filtered_u8, "g_corr": g_corr, "dog": dog, "thresh": th}

    overlay_base = None
    if return_overlay_on is not None:
        base = return_overlay_on
        if isinstance(base, (str, Path)):
            base = cv2.imread(str(base), cv2.IMREAD_COLOR)
        if base is not None:
            if base.ndim == 2:
                base = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
            overlay_base = blend_overlay_and_annotate(base, overlay, len(df))

    result_overlay = overlay_base if overlay_base is not None else overlay

    if save_dir is not None:
        out = Path(save_dir)
        out.mkdir(parents=True, exist_ok=True)
        Image.fromarray(filtered_u8).save(str(out / "filtered.tif"), compression="tiff_lzw")
        cv2.imwrite(str(out / "g_corr.tif"), g_corr)
        cv2.imwrite(str(out / "dog.tif"), dog)
        cv2.imwrite(str(out / "thresh.tif"), th)
        cv2.imwrite(str(out / "mask.tif"), mask)
        cv2.imwrite(str(out / "overlay.tif"), result_overlay)
        df.to_csv(str(out / "particles.csv"), index=False)

    if return_intermediates:
        return result_overlay, mask, df, intermediates
    return result_overlay, mask, df


__all__ = ["blend_overlay_and_annotate", "particle_detection"]

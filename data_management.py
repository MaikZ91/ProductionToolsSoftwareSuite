from __future__ import annotations
import csv
import datetime
import io
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

import pandas as pd
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib import image as mpimg
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox
from ie_Framework.DB import dbConnector
from ie_Framework.Utility import miltenyiBarcode

# Gateway-Defaultwerte
GATEWAY_SERVER_IP = "10.3.141.1"
GATEWAY_PORT = 5000
RASPI_WIFI_SSID = "raspi-webgui"
DUMMY_BARCODE = 999911200301203102103142124
PREFER_GATEWAY = os.environ.get("PREFER_GATEWAY", "1").lower() in ("1", "true", "yes")
GATEWAY_CONNECT_RETRIES = int(os.environ.get("GATEWAY_CONNECT_RETRIES", "2"))
GATEWAY_RETRY_DELAY = float(os.environ.get("GATEWAY_RETRY_DELAY", "0.4"))
GATEWAY_SOCKET_TIMEOUT = float(os.environ.get("GATEWAY_SOCKET_TIMEOUT", "2.0"))
GATEWAY_READ_TIMEOUT = float(os.environ.get("GATEWAY_READ_TIMEOUT", "3.0"))

TESTTYPE_DB_MAP = {
    "kleberoboter": "kleberoboter",
    "gitterschieber_tool": "gitterschieber_tool",
    "stage_test": "stage_test",
}

TIME_PRIORITY = ["starttest", "endtest", "timestamp", "time", "date", "datetime"]
META_FIELDS = {
    "barcodenummer",
    "barcode",
    "user",
    "device",
    "device_id",
    "testtype",
}

PDF_DB_TESTTYPE_CANDIDATES = [
    "stage_test",
    "gitterschieber_tool",
    "kleberoboter",
    "laserscan_fine_lens",
    "laserscan_fine_prisma",
]

DEFAULT_PDF_COLORS = {
    "bg": "#050505",
    "surface": "#0b0b0b",
    "surface_light": "#141414",
    "border": "#1f1f1f",
    "primary": "#f5f5f5",
    "primary_hover": "#e6e6e6",
    "secondary": "#bdbdbd",
    "text": "#f5f5f5",
    "text_muted": "#9a9a9a",
    "danger": "#ff5b5b",
    "success": "#7ad39b",
    "warning": "#f0b74a",
}
PDF_COLORS = dict(DEFAULT_PDF_COLORS)
PDF_DEFAULT_DIR_PROVIDER = None
PDF_UPLOAD_SUCCESS_CALLBACK = None


class PdfModule:
    @staticmethod
    def set_theme(colors: dict | None):
        if colors:
            PDF_COLORS.update(colors)

    @staticmethod
    def set_default_dir_provider(provider):
        global PDF_DEFAULT_DIR_PROVIDER
        PDF_DEFAULT_DIR_PROVIDER = provider

    @staticmethod
    def set_upload_success_callback(callback):
        global PDF_UPLOAD_SUCCESS_CALLBACK
        PDF_UPLOAD_SUCCESS_CALLBACK = callback

    @staticmethod
    def _get_default_dir() -> pathlib.Path:
        if PDF_DEFAULT_DIR_PROVIDER:
            try:
                return pathlib.Path(PDF_DEFAULT_DIR_PROVIDER())
            except Exception:
                pass
        return pathlib.Path.cwd()

    @staticmethod
    def _base_figure():
        return Figure(figsize=(11.69, 8.27), dpi=110, facecolor=PDF_COLORS["surface"])

    @staticmethod
    def qimage_to_rgb_array(qimg: QImage):
        if qimg is None:
            return None
        qimg = qimg.convertToFormat(QImage.Format_RGB888)
        h, w = qimg.height(), qimg.width()
        stride = qimg.bytesPerLine()
        buf = qimg.constBits()
        arr = np.frombuffer(buf, np.uint8, count=qimg.sizeInBytes())
        return arr.reshape((h, stride // 3, 3))[:, :w, :]

    @staticmethod
    def _load_image(path: str | None, log_errors: bool = False):
        if not path or not os.path.exists(path):
            return None
        try:
            return mpimg.imread(path)
        except Exception as exc:
            if log_errors:
                print(f"Error reading image for PDF: {exc}")
            return None

    @staticmethod
    def _render_page(pdf: PdfPages, page: dict):
        kind = page.get("type") or "text"
        if kind == "figure":
            fig = page.get("figure")
            if fig is not None:
                pdf.savefig(fig)
            return
        if kind == "image_path":
            img = PdfModule._load_image(page.get("path"), log_errors=True)
            if img is None:
                return
            page = dict(page)
            page["image"] = img
            kind = "image"
        if kind == "image_grid":
            images = []
            for path in page.get("paths") or []:
                img = PdfModule._load_image(path)
                if img is not None:
                    images.append(img)
            if not images:
                page = {"type": "text", "title": page.get("title"), "text": "Kein Bild"}
                kind = "text"
            else:
                page = dict(page)
                page["images"] = images
        if kind == "summary":
            page = dict(page)
            page["text"] = page.get("lines", [])
            kind = "text"

        fig = PdfModule._base_figure()
        title = page.get("title")
        header_lines = page.get("header_lines")
        if title:
            fig.text(0.02, 0.98, title, va="top", ha="left", fontsize=14, color=PDF_COLORS["text"])
        if header_lines:
            y = 0.94 if title else 0.98
            for line in header_lines:
                fig.text(0.02, y, line, va="top", ha="left", fontsize=10, color=PDF_COLORS["text_muted"])
                y -= 0.04

        if kind == "text":
            ax = fig.add_subplot(111)
            ax.axis("off")
            text = page.get("text", "")
            if text is None:
                text = ""
            if isinstance(text, (list, tuple)):
                text = "\n".join(text)
            ax.text(0.05, 0.95, text, va="top", ha="left", fontsize=12, color=PDF_COLORS["text"], family="monospace")
        elif kind == "image":
            ax = fig.add_subplot(111)
            ax.axis("off")
            image = page.get("image")
            if image is not None:
                ax.imshow(image)
            else:
                ax.text(0.5, 0.5, "Kein Bild", ha="center", va="center", color=PDF_COLORS["text_muted"])
        elif kind == "text_image":
            ax_text = fig.add_subplot(121)
            ax_text.axis("off")
            ax_text.text(
                0.05,
                0.95,
                page.get("text", "") or "",
                va="top",
                ha="left",
                fontsize=12,
                color=PDF_COLORS["text"],
                family="monospace",
            )
            ax_img = fig.add_subplot(122)
            ax_img.axis("off")
            image = page.get("image")
            if image is not None:
                ax_img.imshow(image)
            else:
                ax_img.text(0.5, 0.5, "Kein Bild", ha="center", va="center", color=PDF_COLORS["text_muted"])
        elif kind == "image_grid":
            cols = max(1, int(page.get("cols", 2)))
            images = page.get("images") or []
            rows = int(np.ceil(len(images) / cols)) if images else 1
            for i, img in enumerate(images, 1):
                ax = fig.add_subplot(rows, cols, i)
                ax.axis("off")
                if img is not None:
                    ax.imshow(img)
                else:
                    ax.text(0.5, 0.5, "Kein Bild", ha="center", va="center", color=PDF_COLORS["text_muted"])
        else:
            ax = fig.add_subplot(111)
            ax.axis("off")
            ax.text(
                0.05,
                0.95,
                str(page.get("text", "") or ""),
                va="top",
                ha="left",
                fontsize=12,
                color=PDF_COLORS["text"],
                family="monospace",
            )

        if title or header_lines:
            if kind in {"image", "image_grid"}:
                fig.tight_layout(rect=[0, 0, 1, 0.9])
            elif kind == "text_image":
                fig.tight_layout(rect=[0, 0, 1, 0.95])
        pdf.savefig(fig)

    @staticmethod
    def _is_path(value) -> bool:
        try:
            return value is not None and pathlib.Path(value).exists()
        except Exception:
            return False

    @staticmethod
    def _normalize_item(item):
        if isinstance(item, dict):
            return item
        if isinstance(item, Figure):
            return {"type": "figure", "figure": item}
        if isinstance(item, QImage):
            return {"type": "image", "image": PdfModule.qimage_to_rgb_array(item)}
        if isinstance(item, np.ndarray):
            return {"type": "image", "image": item}
        if isinstance(item, tuple) and len(item) == 2:
            return {"type": "text_image", "text": item[0], "image": item[1]}
        if isinstance(item, (pathlib.Path, str)) and PdfModule._is_path(item):
            return {"type": "image_path", "path": str(item)}
        if isinstance(item, (pathlib.Path, str)):
            return {"type": "text", "text": str(item)}
        return {"type": "text", "text": str(item)}

    @staticmethod
    def write_pdf(
        pdf_path: pathlib.Path | str,
        items,
        db_test_type: str | None = None,
        db_test_guid: str | None = None,
    ):
        PdfModule.report(
            pdf_path,
            items,
            db_test_type=db_test_type,
            db_test_guid=db_test_guid,
        )

    @staticmethod
    def report(
        pdf_path: pathlib.Path | str,
        items,
        db_test_type: str | None = None,
        db_test_guid: str | None = None,
        upload_to_db: bool = True,
    ):
        if not isinstance(items, (list, tuple)):
            items = [items]
        with PdfPages(str(pdf_path)) as pdf:
            for item in items:
                page = PdfModule._normalize_item(item)
                PdfModule._render_page(pdf, page)
        if upload_to_db:
            def _on_success(guid):
                cb = PDF_UPLOAD_SUCCESS_CALLBACK
                if callable(cb):
                    try:
                        cb(guid, db_test_type)
                    except Exception:
                        pass
            upload_pdf_to_db_async(
                pdf_path=pdf_path,
                preferred_testtype=db_test_type,
                on_success=_on_success,
            )

    @staticmethod
    def save_camera_pdf_capture(parent, frame_provider, filename_prefix, page_title, header_lines_provider=None, db_test_type: str = "gitterschieber_tool"):
        frame = frame_provider()
        if frame is None:
            QMessageBox.warning(parent, "PDF speichern", "Kein Bild verfuegbar.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = PdfModule._get_default_dir()
        default_dir.mkdir(parents=True, exist_ok=True)
        path_str = str(default_dir / f"{filename_prefix}_{ts}.pdf")
        header_lines = header_lines_provider() if header_lines_provider else None
        PdfModule.report(path_str, [{
            "type": "image",
            "title": page_title,
            "header_lines": header_lines,
            "image": frame,
        }], db_test_type=db_test_type)
        QMessageBox.information(parent, "PDF gespeichert", f"Report gespeichert:\n{path_str}")

    @staticmethod
    def build_alignment_report(last_center, last_frame_size, laser):
        if last_center is None or not last_frame_size:
            return None, "Keine Alignment-Daten verfuegbar."
        w, h = last_frame_size
        cx, cy = last_center
        ref = laser.get_reference_point() if laser is not None else None
        if ref is not None:
            rx, ry = ref
            dx = int(cx - rx)
            dy = int(cy - ry)
            ref_text = f"{rx}, {ry}"
        else:
            dx = int(cx - w // 2)
            dy = int(cy - h // 2)
            ref_text = "center"
        dist = float(np.hypot(dx, dy))
        px_um = None
        if laser is not None:
            px_um = laser.get_pixel_size_um()
        tol_px = 5.0
        ok = (dist <= tol_px)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text_lines = [
            "Autofocus Alignment Report",
            "",
            f"Zeitpunkt: {now}",
            f"Bildgroesse: {w} x {h} px",
            f"Center: {cx}, {cy} px",
            f"Referenz: {ref_text}",
            f"dx/dy: {dx:+d} px / {dy:+d} px",
            f"dist: {dist:.2f} px",
            f"Toleranz: +/- {tol_px:.1f} px -> {'OK' if ok else 'ALIGN'}",
        ]
        if px_um:
            dx_um = dx * px_um
            dy_um = dy * px_um
            dist_um = dist * px_um
            dist_mm = dist_um / 1000.0
            text_lines += [
                f"dx/dy: {dx_um:+.1f} um / {dy_um:+.1f} um",
                f"dist: {dist_um:.1f} um ({dist_mm:.3f} mm)",
            ]
        return "\n".join(text_lines), None

    @staticmethod
    def save_text_image_pdf(parent, image_provider, text_provider, filename_prefix, page_title=None, db_test_type: str = "gitterschieber_tool"):
        image = image_provider()
        if image is None:
            QMessageBox.warning(parent, "PDF speichern", "Kein Bild verfuegbar.")
            return
        text, err = text_provider()
        if text is None:
            QMessageBox.warning(parent, "PDF speichern", err or "Keine Daten verfuegbar.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = PdfModule._get_default_dir()
        default_dir.mkdir(parents=True, exist_ok=True)
        path_str = str(default_dir / f"{filename_prefix}_{ts}.pdf")
        PdfModule.report(path_str, [{
            "type": "text_image",
            "title": page_title,
            "text": text,
            "image": image,
        }], db_test_type=db_test_type)
        QMessageBox.information(parent, "PDF gespeichert", f"Report gespeichert:\n{path_str}")


# ---------------------------------------------------------------------------
# Stage test data/report helpers (used by Resolve xy_stage)
# ---------------------------------------------------------------------------
_STAGE_BG = "#0b0b0f"
_STAGE_BG_ELEV = "#121218"
_STAGE_FG_MUTED = "#9ea0a6"
_STAGE_BORDER = "#222230"


def _style_stage_ax(ax):
    ax.set_facecolor(_STAGE_BG)
    for spine in ax.spines.values():
        spine.set_color(_STAGE_BORDER)
        spine.set_linewidth(0.8)
    ax.grid(True)
    ax.tick_params(colors=_STAGE_FG_MUTED, labelsize=10)


def _safe_last(values, default=0.0):
    try:
        return float(values[-1]) if values else default
    except Exception:
        return default


def _max_abs_um_from_errors(pos_infodict: dict) -> float:
    max_abs = 0.0
    for key in ("pos_error_x [m]", "pos_error_y [m]"):
        vals = pos_infodict.get(key, [])
        for val in vals:
            try:
                max_abs = max(max_abs, abs(float(val)))
            except Exception:
                continue
    return max_abs * 1e6


def save_calibration_plot(out_dir: pathlib.Path, axis: str, batch: str, x, y, poly1d_fn):
    """Save the calibration plot for one axis as PNG."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig = Figure(figsize=(7.2, 5), dpi=110, facecolor=_STAGE_BG_ELEV)
    ax = fig.add_subplot(111)
    _style_stage_ax(ax)
    ax.plot(x, y, "o", label=f"Samples · {batch}")
    ax.plot(x, poly1d_fn(x), "--", label="Fit")
    ax.set_title(f"Measured Motorsteps in {axis}-Axis · Charge: {batch}")
    ax.set_xlabel("Encodersteps [m]")
    ax.set_ylabel("Motorsteps [steps]")
    ax.legend()
    fig.savefig(out_dir / f"calib_{axis.lower()}_{batch}.png")


def _build_stage_test_report_text(batch: str, csv_name: str, pos_infodict: dict) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_points = len(pos_infodict.get("Time [min]", []))
    last_time = _safe_last(pos_infodict.get("Time [min]", []), 0.0)
    last_x = _safe_last(pos_infodict.get("x_position [m]", []), 0.0)
    last_y = _safe_last(pos_infodict.get("y_position [m]", []), 0.0)
    last_ex = _safe_last(pos_infodict.get("pos_error_x [m]", []), 0.0)
    last_ey = _safe_last(pos_infodict.get("pos_error_y [m]", []), 0.0)
    max_abs_um = _max_abs_um_from_errors(pos_infodict)
    return (
        "Stage Test Report (PMAC)\n\n"
        f"Zeitpunkt: {now}\n"
        f"Charge: {batch}\n"
        f"CSV: {csv_name}\n"
        f"Samples: {n_points}\n\n"
        "Latest values:\n"
        f"  Zeit [min]: {last_time:.2f}\n"
        f"  X Position [m]: {last_x:.6f}\n"
        f"  Y Position [m]: {last_y:.6f}\n"
        f"  Error X [um]: {last_ex * 1e6:.2f}\n"
        f"  Error Y [um]: {last_ey * 1e6:.2f}\n\n"
        f"Max |Error| [um]: {max_abs_um:.2f}\n"
    )


def _build_stage_test_report_plot(pos_infodict: dict, dur_max_um: float = 25.5) -> Figure | None:
    time_vals = pos_infodict.get("Time [min]", [])
    err_x = pos_infodict.get("pos_error_x [m]", [])
    err_y = pos_infodict.get("pos_error_y [m]", [])
    if not time_vals or (not err_x and not err_y):
        return None
    try:
        t = np.asarray(time_vals, dtype=float)
    except Exception:
        return None

    fig = Figure(figsize=(11.0, 6.2), dpi=110, facecolor=_STAGE_BG_ELEV)
    ax = fig.add_subplot(111)
    _style_stage_ax(ax)
    if err_x:
        ax.plot(t, np.asarray(err_x, dtype=float) * 1e6, label="Error X")
    if err_y:
        ax.plot(t, np.asarray(err_y, dtype=float) * 1e6, label="Error Y")
    ax.axhline(dur_max_um, color="#ff5b5b", linestyle="--", linewidth=1, label=f"Limit {dur_max_um:.1f} µm")
    ax.axhline(-dur_max_um, color="#ff5b5b", linestyle="--", linewidth=1)
    ax.set_title("Position error over time")
    ax.set_xlabel("Zeit [min]")
    ax.set_ylabel("Abweichung [µm]")
    ax.legend()
    fig.tight_layout()
    return fig


def save_stage_test(
    savefile_name,
    pos_infodict,
    batch: str = "NoBatch",
    write_pdf: bool = True,
    dur_max_um: float = 25.5,
):
    """Save CSV (and optionally PDF) for a stage test run."""
    now = datetime.datetime.now()
    dt_string = now.strftime("%Y-%m-%d_%H-%M-%S")
    pth = pathlib.Path(savefile_name)
    out_dir = pth.parent if str(pth.parent) not in ("", ".") else pathlib.Path(".")
    base = pth.name
    savename = out_dir / f"{dt_string}_{batch}_{base}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(savename, "w+", newline="") as savefile:
        writer = csv.writer(savefile)
        writer.writerow([
            "batch", "x_counter", "y_counter", "Time [min]",
            "x_position [m]", "y_position [m]", "pos_error_x [m]", "pos_error_y [m]"
        ])
        for i in range(len(pos_infodict["x_counter"])):
            writer.writerow([
                batch,
                pos_infodict["x_counter"][i], pos_infodict["y_counter"][i],
                pos_infodict["Time [min]"][i],
                pos_infodict["x_position [m]"][i], pos_infodict["y_position [m]"][i],
                pos_infodict["pos_error_x [m]"][i], pos_infodict["pos_error_y [m]"][i],
            ])
    print(f"Saved {savename}")
    if write_pdf:
        try:
            pdf_path = savename.with_suffix(".pdf")
            text = _build_stage_test_report_text(batch, savename.name, pos_infodict)
            pages = [{"type": "text", "title": "Stage Test Report", "text": text}]
            fig = _build_stage_test_report_plot(pos_infodict, dur_max_um=dur_max_um)
            if fig is not None:
                pages.append(fig)
            PdfModule.write_pdf(pdf_path, pages)
            print(f"Saved {pdf_path}")
        except Exception as exc:
            print(f"[WARN] PDF export failed: {exc}")


@dataclass
class DashboardViewModel:
    total: int
    ok_ratio: int
    last_result: str
    result_type: str
    ordered_columns: list[str]
    time_column: str | None
    display_df: pd.DataFrame


def current_ssid() -> str | None:
    nmcli = shutil.which("nmcli")
    if nmcli:
        res = subprocess.run(
            [nmcli, "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            capture_output=True,
            text=True,
        )
        for line in res.stdout.splitlines():
            if line.startswith("yes:"):
                return line.split(":", 1)[1] or None
    iwgetid = shutil.which("iwgetid")
    if iwgetid:
        res = subprocess.run([iwgetid, "-r"], capture_output=True, text=True)
        ssid = res.stdout.strip()
        return ssid or None
    return None


def is_on_gateway_wifi(target_ssid: str = RASPI_WIFI_SSID) -> bool:
    return current_ssid() == target_ssid


def gateway_connect(
    server_ip: str | None = None,
    port: int | None = None,
    target_ssid: str = RASPI_WIFI_SSID,
    timeout: float = 15.0,
    socket_timeout: float = 3.0,
):
    """
    Stellt (best-effort) die WLAN-Verbindung sicher und liefert eine Socket-Verbindung zum Gateway.
    """
    if target_ssid:
        if current_ssid() != target_ssid:
            nmcli = shutil.which("nmcli")
            if nmcli:
                subprocess.run(
                    [nmcli, "dev", "wifi", "connect", target_ssid],
                    capture_output=True,
                    text=True,
                )
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(1.0)
                if current_ssid() == target_ssid:
                    break

    target_ip = server_ip or GATEWAY_SERVER_IP
    target_port = port or GATEWAY_PORT
    return socket.create_connection((target_ip, target_port), timeout=socket_timeout)


def _read_gateway_line(conn: socket.socket, timeout: float) -> str:
    conn.settimeout(timeout)
    f = conn.makefile("r")
    line = f.readline()
    return line.strip() if line else ""


def _gateway_request(payload: dict) -> dict:
    last_err: Exception | None = None
    for attempt in range(GATEWAY_CONNECT_RETRIES + 1):
        try:
            with gateway_connect(socket_timeout=GATEWAY_SOCKET_TIMEOUT) as conn:
                conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                raw = _read_gateway_line(conn, GATEWAY_READ_TIMEOUT)
                if not raw:
                    raise RuntimeError("empty gateway response")
                return json.loads(raw)
        except Exception as e:
            last_err = e
            if attempt < GATEWAY_CONNECT_RETRIES:
                time.sleep(GATEWAY_RETRY_DELAY)
    return {"status": "ERR", "message": str(last_err) if last_err else "gateway error"}


def send_dummy_payload_gateway(
    device_id: str,
    server_ip: str | None = None,
    port: int | None = None,
    barcode: int | None = None,
    result: str | None = None,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
) -> tuple[dict, str | None]:
    """
    Send a payload to the gateway (kleberoboter or another device) and return payload + ACK.
    Parameters can be supplied directly or taken from the predefined profiles.
    """
    
    now = datetime.datetime.now(datetime.timezone.utc)
    start_iso = (start_time or now).isoformat().replace("+00:00", "Z")
    end_iso = (end_time or now).isoformat().replace("+00:00", "Z")
    payload = {
        "device_id": device_id,
        "barcodenummer": barcode,
        "startTime": start_iso,
        "endTime": end_iso,
        "result": result,
    }
    message = (json.dumps(payload) + "\n").encode("utf-8")
    ack: str | None = None
    with gateway_connect(server_ip, port) as conn:
        conn.sendall(message)
        conn.settimeout(1.0)
        try:
            data = _read_gateway_line(conn, 1.0)
            ack = data if data else None
            if ack == "":
                ack = None
        except socket.timeout:
            ack = None
    return payload, ack


def send_payload_gateway(
    device_id: str,
    payload: dict,
    server_ip: str | None = None,
    port: int | None = None,
    barcode: str | int | None = None,
    user: str | None = None,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
) -> tuple[dict, str | None]:
    now = datetime.datetime.now(datetime.timezone.utc)
    start_iso = (start_time or now).isoformat().replace("+00:00", "Z")
    end_iso = (end_time or now).isoformat().replace("+00:00", "Z")
    message_payload = {
        "mode": "INGEST",
        "device_id": device_id,
        "barcodenummer": barcode,
        "startTime": start_iso,
        "endTime": end_iso,
        "payload": payload,
        "user": user,
    }
    if "ok" in payload:
        message_payload["result"] = payload.get("ok")
    elif "result" in payload:
        message_payload["result"] = payload.get("result")

    message = (json.dumps(message_payload) + "\n").encode("utf-8")
    ack: str | None = None
    with gateway_connect(server_ip, port) as conn:
        conn.sendall(message)
        conn.settimeout(1.0)
        try:
            data = _read_gateway_line(conn, 1.0)
            ack = data if data else None
            if ack == "":
                ack = None
        except socket.timeout:
            ack = None
    return message_payload, ack


# =============================================================================
# Database Connection and Data Retrieval Functions
# =============================================================================

def parse_db_response(raw):
    """
    Parse various database response formats into a pandas DataFrame.
    Handles DataFrame, JSON string, CSV string, and raw data.
    """
    
    # Already a DataFrame
    if isinstance(raw, pd.DataFrame):
        return raw.copy()
    
    # Try JSON parsing
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
            if isinstance(obj, list):
                return pd.DataFrame(obj)
            elif isinstance(obj, dict):
                return pd.DataFrame([obj])
        except Exception:
            pass
        
        # Try CSV parsing
        try:
            return pd.read_csv(io.StringIO(raw))
        except Exception:
            pass
        
        # Fallback: raw string
        return pd.DataFrame({"raw": [raw]})
    
    # Unknown format
    return pd.DataFrame({"_raw": [str(raw)]})


def to_dataframe(raw):
    if isinstance(raw, tuple) and len(raw) >= 2:
        raw = raw[1]
    try:
        return parse_db_response(raw)
    except Exception:
        if isinstance(raw, pd.DataFrame):
            return raw
        return pd.DataFrame()


def find_guid_column_name(columns) -> str | None:
    for col in columns:
        norm = str(col).lower().replace("_", "").replace("-", "")
        if "testguid" in norm:
            return str(col)
    for col in columns:
        norm = str(col).lower().replace("_", "").replace("-", "")
        if "guid" in norm and "test" in norm:
            return str(col)
    return None


def find_time_column_name(columns) -> str | None:
    priorities = ("starttest", "endtest", "timestamp", "datetime", "time", "date")
    lowered = [(str(c), str(c).lower()) for c in columns]
    for key in priorities:
        for original, low in lowered:
            if key in low:
                return original
    return None


def extract_latest_test_guid(conn, preferred_testtype: str | None = None) -> str | None:
    candidates = []
    if preferred_testtype:
        candidates.append(preferred_testtype)
    for t in PDF_DB_TESTTYPE_CANDIDATES:
        if t not in candidates:
            candidates.append(t)

    best_guid = None
    best_ts = pd.Timestamp.min
    for testtype in candidates:
        try:
            raw = conn.getLastTests(5, testtype)
            df = to_dataframe(raw)
            if df.empty:
                continue
            guid_col = find_guid_column_name(df.columns)
            if not guid_col:
                continue
            time_col = find_time_column_name(df.columns)
            if time_col and time_col in df.columns:
                ts_series = pd.to_datetime(df[time_col], errors="coerce")
                idx = ts_series.fillna(pd.Timestamp.min).idxmax()
                row = df.loc[idx]
                ts = ts_series.loc[idx]
            else:
                row = df.iloc[0]
                ts = pd.Timestamp.min
            guid = str(row.get(guid_col, "")).strip()
            if not guid:
                continue
            if ts >= best_ts:
                best_ts = ts
                best_guid = guid
        except Exception:
            continue
    return best_guid


def _prepare_file_for_db_upload(
    file_path: pathlib.Path,
    required_suffix: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path | None]:
    filename = file_path.name
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    if required_suffix:
        suffix = required_suffix.lower()
        if not safe_name.lower().endswith(suffix):
            safe_name = f"{safe_name or 'upload'}{suffix}"
    requires_copy = (" " in filename) or (safe_name != filename)
    if not requires_copy:
        return file_path, None
    tmp_dir = pathlib.Path(tempfile.gettempdir()) / "resolve_db_upload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    tmp_path = tmp_dir / f"{ts}_{safe_name}"
    shutil.copyfile(str(file_path), str(tmp_path))
    return tmp_path, tmp_path


def upload_pdf_to_db_simple(
    pdf,
    particle_count: int = 0,
    justage_angle: float = 0.0,
    preferred_testtype: str | None = None,
    user: str = "pdf_upload",
):
    testtype = (preferred_testtype or "gitterschieber_tool").strip() or "gitterschieber_tool"
    pdf_path = pathlib.Path(str(pdf))
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")
    upload_path, cleanup_path = _prepare_file_for_db_upload(pdf_path, required_suffix=".pdf")
    payload = {
        "particle_count": int(particle_count),
        "justage_angle": float(justage_angle),
    }
    try:
        with dbConnector.connection() as c:
            now = datetime.datetime.now()
            c.sendData(
                now,
                now,
                0,
                testtype,
                payload,
                miltenyiBarcode.mBarcode(str(DUMMY_BARCODE)),
                user,
            )
            df = to_dataframe(c.getLastTests(1, testtype))
            if df.empty:
                raise RuntimeError("Keine Testdaten nach PDF-Upload gefunden.")
            guid_col = find_guid_column_name(df.columns)
            if not guid_col:
                raise RuntimeError("Konnte Test_GUID-Spalte fuer PDF-Upload nicht ermitteln.")
            test_guid = str(df.iloc[0].get(guid_col, "")).strip()
            if not test_guid:
                raise RuntimeError("Leere Test_GUID nach PDF-Upload.")
            c.saveFile(test_guid, str(upload_path))
            return test_guid
    finally:
        if cleanup_path is not None:
            try:
                cleanup_path.unlink(missing_ok=True)
            except Exception:
                pass


def upload_pdf_to_db_async(
    pdf_path: pathlib.Path | str,
    preferred_testtype: str | None = None,
    particle_count: int = 0,
    justage_angle: float = 0.0,
    user: str = "pdf_upload",
    on_success=None,
    on_error=None,
):
    def _job():
        try:
            guid = upload_pdf_to_db_simple(
                pdf_path,
                particle_count=particle_count,
                justage_angle=justage_angle,
                preferred_testtype=preferred_testtype,
                user=user,
            )
            if callable(on_success):
                on_success(guid)
        except Exception as exc:
            if callable(on_error):
                on_error(exc)

    th = threading.Thread(target=_job, daemon=True)
    th.start()
    return th


def _to_int(value, default: int = 0) -> int:
    try:
        text = str(value).strip()
        if text == "":
            return default
        return int(text)
    except Exception:
        return default


def _to_float(value, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def build_dashboard_payload(testtype: str, raw_fields: dict | None = None) -> dict:
    fields = raw_fields or {}
    if testtype == "kleberoboter":
        return {"ok": bool(fields.get("ok", False))}
    if testtype == "gitterschieber_tool":
        return {
            "particle_count": _to_int(fields.get("particle_count"), default=0),
            "justage_angle": _to_float(fields.get("justage_angle"), default=0.0),
        }
    if testtype == "stage_test":
        return {
            "position": str(fields.get("position", "")).strip(),
            "field_of_view": _to_float(fields.get("field_of_view"), default=0.0),
        }
    return {}


def normalize_dashboard_entry_input(
    testtype: str,
    raw_fields: dict | None = None,
    barcode: str | int | None = None,
    user: str | None = None,
    media_path: str | None = None,
) -> dict:
    barcode_text = str(barcode).strip() if barcode is not None else ""
    user_text = str(user).strip() if user is not None else ""
    return {
        "testtype": str(testtype).strip(),
        "payload": build_dashboard_payload(testtype=str(testtype).strip(), raw_fields=raw_fields),
        "barcode": barcode_text or str(DUMMY_BARCODE),
        "user": user_text or "unknown",
        "media_path": (media_path or "").strip() or None,
    }


def save_dashboard_entry(
    testtype: str,
    payload: dict,
    barcode: str | int,
    user: str,
    media_path: str | None = None,
    send_timeout_sec: float = 10.0,
    media_timeout_sec: float = 30.0,
) -> dict:
    result = {
        "send_rc": 0,
        "test_guid": "",
        "media_uploaded": False,
    }
    conn = None
    try:
        conn = dbConnector.connection()
        conn.connect()
        if hasattr(conn, "comm_socket") and conn.comm_socket:
            conn.comm_socket.settimeout(float(send_timeout_sec))
        now = datetime.datetime.now()
        send_rc = int(
            conn.sendData(
                now,
                now,
                0,
                testtype,
                payload,
                miltenyiBarcode.mBarcode(str(barcode)),
                user,
            )
        )
        result["send_rc"] = send_rc
        if send_rc != 0:
            raise RuntimeError(f"sendData fehlgeschlagen (rc={send_rc}).")
        media_path_text = (media_path or "").strip()
        if not media_path_text:
            return result
        media_file = pathlib.Path(media_path_text)
        if not media_file.exists():
            raise FileNotFoundError(f"Media-Datei nicht gefunden: {media_path_text}")
        if hasattr(conn, "comm_socket") and conn.comm_socket:
            conn.comm_socket.settimeout(float(media_timeout_sec))
        latest_df = to_dataframe(conn.getLastTests(3, testtype))
        guid_col = find_guid_column_name(latest_df.columns)
        if latest_df.empty or not guid_col:
            raise RuntimeError("Konnte Test_GUID fuer Media-Upload nicht ermitteln.")
        test_guid = str(latest_df.iloc[0].get(guid_col, "")).strip()
        if not test_guid:
            raise RuntimeError("Leere Test_GUID fuer Media-Upload.")
        conn.saveFile(test_guid, str(media_file))
        result["test_guid"] = test_guid
        result["media_uploaded"] = True
        return result
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


def send_dashboard_entry(
    testtype: str,
    payload: dict,
    barcode: str | int,
    user: str,
    media_path: str | None = None,
    send_timeout_sec: float = 10.0,
    media_timeout_sec: float = 30.0,
    prefer_gateway: bool | None = None,
) -> dict:
    media_path_text = (media_path or "").strip()
    if prefer_gateway is None:
        prefer_gateway = is_on_gateway_wifi()

    if prefer_gateway:
        message_payload, ack = send_payload_gateway(
            device_id=testtype,
            barcode=barcode,
            payload=payload,
            user=user,
            start_time=datetime.datetime.now(datetime.timezone.utc),
            end_time=datetime.datetime.now(datetime.timezone.utc),
        )
        return {
            "transport": "gateway",
            "ack": ack,
            "message_payload": message_payload,
            "media_ignored": bool(media_path_text),
        }

    db_result = save_dashboard_entry(
        testtype=testtype,
        payload=payload,
        barcode=barcode,
        user=user,
        media_path=media_path_text or None,
        send_timeout_sec=send_timeout_sec,
        media_timeout_sec=media_timeout_sec,
    )
    return {
        "transport": "db",
        "db_result": db_result,
        "media_ignored": False,
    }


def send_dashboard_entry_async(
    testtype: str,
    payload: dict,
    barcode: str | int,
    user: str,
    media_path: str | None = None,
    send_timeout_sec: float = 10.0,
    media_timeout_sec: float = 30.0,
    prefer_gateway: bool | None = None,
    on_success=None,
    on_error=None,
):
    def _job():
        try:
            result = send_dashboard_entry(
                testtype=testtype,
                payload=payload,
                barcode=barcode,
                user=user,
                media_path=media_path,
                send_timeout_sec=send_timeout_sec,
                media_timeout_sec=media_timeout_sec,
                prefer_gateway=prefer_gateway,
            )
            if callable(on_success):
                on_success(result)
        except Exception as exc:
            if callable(on_error):
                on_error(exc)

    th = threading.Thread(target=_job, daemon=True)
    th.start()
    return th


def send_dashboard_entry_from_raw(
    testtype: str,
    raw_fields: dict | None = None,
    barcode: str | int | None = None,
    user: str | None = None,
    media_path: str | None = None,
    send_timeout_sec: float = 10.0,
    media_timeout_sec: float = 30.0,
    prefer_gateway: bool | None = None,
) -> dict:
    normalized = normalize_dashboard_entry_input(
        testtype=testtype,
        raw_fields=raw_fields,
        barcode=barcode,
        user=user,
        media_path=media_path,
    )
    return send_dashboard_entry(
        testtype=normalized["testtype"],
        payload=normalized["payload"],
        barcode=normalized["barcode"],
        user=normalized["user"],
        media_path=normalized["media_path"],
        send_timeout_sec=send_timeout_sec,
        media_timeout_sec=media_timeout_sec,
        prefer_gateway=prefer_gateway,
    )


def send_dashboard_entry_from_raw_async(
    testtype: str,
    raw_fields: dict | None = None,
    barcode: str | int | None = None,
    user: str | None = None,
    media_path: str | None = None,
    send_timeout_sec: float = 10.0,
    media_timeout_sec: float = 30.0,
    prefer_gateway: bool | None = None,
    on_success=None,
    on_error=None,
):
    def _job():
        try:
            result = send_dashboard_entry_from_raw(
                testtype=testtype,
                raw_fields=raw_fields,
                barcode=barcode,
                user=user,
                media_path=media_path,
                send_timeout_sec=send_timeout_sec,
                media_timeout_sec=media_timeout_sec,
                prefer_gateway=prefer_gateway,
            )
            if callable(on_success):
                on_success(result)
        except Exception as exc:
            if callable(on_error):
                on_error(exc)

    th = threading.Thread(target=_job, daemon=True)
    th.start()
    return th


def send_test_data(
    testtype: str,
    payload: dict,
    user: str,
    barcode: str | int | None = None,
    result: int = 0,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    send_timeout_sec: float = 10.0,
) -> int:
    conn = None
    try:
        conn = dbConnector.connection()
        conn.connect()
        if hasattr(conn, "comm_socket") and conn.comm_socket:
            conn.comm_socket.settimeout(float(send_timeout_sec))
        now = datetime.datetime.now()
        start = start_time or now
        end = end_time or now
        bc = str(barcode if barcode is not None else DUMMY_BARCODE)
        rc = conn.sendData(
            start,
            end,
            int(result),
            testtype,
            payload,
            miltenyiBarcode.mBarcode(bc),
            str(user),
        )
        return int(rc)
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


def get_media_presence_map(test_guids: list[str]) -> dict[str, bool]:
    media_lookup: dict[str, bool] = {}
    unique_guids = []
    for guid in test_guids:
        g = str(guid).strip()
        if g and g not in media_lookup:
            media_lookup[g] = False
            unique_guids.append(g)
    if not unique_guids:
        return media_lookup
    conn = None
    try:
        conn = dbConnector.connection()
        conn.connect()
        for guid in unique_guids:
            try:
                raw = conn.getFileListFromTest(guid)
                files_df = to_dataframe(raw)
                media_lookup[guid] = not files_df.empty
            except Exception:
                media_lookup[guid] = False
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
    return media_lookup


def get_file_list_from_test(test_guid: str) -> pd.DataFrame:
    conn = None
    try:
        conn = dbConnector.connection()
        conn.connect()
        raw = conn.getFileListFromTest(test_guid)
        if isinstance(raw, tuple) and len(raw) >= 2 and int(raw[0]) != 0:
            raise RuntimeError("Dateiliste konnte nicht geladen werden.")
        return to_dataframe(raw)
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


def download_file_bytes(file_id: int):
    conn = None
    try:
        conn = dbConnector.connection()
        conn.connect()
        return conn.downloadFile(int(file_id))
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


def _parse_gateway_payload(payload: dict | list | None) -> pd.DataFrame:
    if not payload:
        return pd.DataFrame()
    if isinstance(payload, list):
        df = pd.DataFrame(payload)
    elif isinstance(payload, dict):
        df = pd.DataFrame([payload])
    else:
        df = pd.DataFrame({"_raw": [str(payload)]})

    if "barcodenummer" not in df.columns:
        if "Device_GUID" in df.columns:
            df["barcodenummer"] = df["Device_GUID"].astype(str)
        else:
            df["barcodenummer"] = pd.NA
    if "user" not in df.columns:
        if "Employee_ID" in df.columns:
            df["user"] = df["Employee_ID"].astype(str)
        else:
            df["user"] = pd.NA

    for col in ("StartTest", "EndTest"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def build_dashboard_view_model(df: pd.DataFrame) -> DashboardViewModel:
    """
    Prepare the dashboard data model: KPI values, ordered columns, and sorted DataFrame.
    """
    df = df.copy()
    columns = list(df.columns)
    guid_cols = []
    for col in columns:
        if "testguid" in col.lower():
            guid_cols.append(col)

    time_cols: list[str] = []
    for name in TIME_PRIORITY:
        for col in columns:
            if name in col.lower() and col not in time_cols:
                time_cols.append(col)

    for col in columns:
        try:
            if (
                pd.api.types.is_datetime64_any_dtype(df[col])
                and col not in time_cols
            ):
                time_cols.append(col)
        except Exception:
            pass

    ok_cols = [c for c in columns if c.lower() in {"ok", "status", "result"}]
    meta_cols = [c for c in columns if c.lower() in META_FIELDS]
    meta_cols = [c for c in meta_cols if c not in guid_cols]
    time_cols = [c for c in time_cols if c not in guid_cols]
    ok_cols = [c for c in ok_cols if c not in guid_cols and c not in time_cols]

    param_cols = [
        c
        for c in columns
        if c not in time_cols and c not in meta_cols and c not in guid_cols and c not in ok_cols
    ]

    ordered_columns = (
        time_cols
        + param_cols
        + ok_cols
        + [c for c in meta_cols if c not in time_cols]
        + guid_cols
    )

    if not ordered_columns:
        ordered_columns = columns

    sort_cols = time_cols[:1] + param_cols
    display_df = df
    if sort_cols:
        try:
            ascending = [False] + [True] * (len(sort_cols) - 1)
            display_df = df.sort_values(
                by=sort_cols, ascending=ascending, na_position="last"
            )
        except Exception as e:
            print(f"DashboardView sort fallback: {e}")

    display_df = display_df.reindex(columns=ordered_columns)

    total = len(df)
    ok_ratio = 0
    last_result = "N/A"
    if total > 0 and "ok" in df.columns:
        ok_bool = df["ok"].fillna(False).astype(bool)
        ok_count = ok_bool.sum()
        ok_ratio = int((ok_count / total) * 100)
        last_result = "OK" if bool(ok_bool.iloc[0]) else "FAIL"

    if last_result == "FAIL":
        result_type = "fail"
    elif last_result == "OK":
        result_type = "ok"
    else:
        result_type = "neutral"

    time_column = time_cols[0] if time_cols else None

    return DashboardViewModel(
        total=total,
        ok_ratio=ok_ratio,
        last_result=last_result,
        result_type=result_type,
        ordered_columns=ordered_columns,
        time_column=time_column,
        display_df=display_df,
    )


def fetch_test_data(
    testtype: str,
    limit: int = 50,
    barcode: str | None = None,
    prefer_gateway: bool | None = None,
) -> tuple[pd.DataFrame, bool]:
    """
    Fetch test data from the database and return as a pandas DataFrame.
    
    Args:
        testtype: Type of test to fetch (e.g., 'kleberoboter', 'gitterschieber_tool')
        limit: Maximum number of rows to fetch
    
    Returns:
        tuple containing:
        - pandas DataFrame with test data, or empty DataFrame on error
        - bool: True if connection was successful, False otherwise
    """
    
    if prefer_gateway is None:
        prefer_gateway = PREFER_GATEWAY and is_on_gateway_wifi()

    def _finalize_df(df: pd.DataFrame) -> pd.DataFrame:
        if "ok" not in df.columns:
            df["ok"] = pd.NA
        for col in ("StartTest", "EndTest"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df

    def _try_gateway() -> tuple[pd.DataFrame, bool]:
        bc = str(barcode) if barcode else None
        gw = get_data_from_gateway(testtype, bc, limit=limit)
        if gw.get("status") == "OK":
            df = _parse_gateway_payload(gw.get("data"))
            return _finalize_df(df), True
        return pd.DataFrame(), False

    if prefer_gateway:
        df, ok = _try_gateway()
        if ok:
            return df, True

    conn = None
    try:
        conn = dbConnector.connection()
        conn.connect()

        raw = conn.getLastTests(limit, testtype)
        df = parse_db_response(raw)
        return _finalize_df(df), True

    except Exception as e:
        print(f"Error fetching test data (DB): {e}")
        if not prefer_gateway and is_on_gateway_wifi():
            try:
                df, ok = _try_gateway()
                if ok:
                    return df, True
            except Exception as gw_e:
                print(f"Error fetching test data (Gateway): {gw_e}")
        return pd.DataFrame(), False

    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass

def fetch_all_test_data(limit: int = 50) -> dict[str, pd.DataFrame]:
    """Fetch test data for all known devices/test types."""
    data = {}
    for testtype in TESTTYPE_DB_MAP.keys():
        df, _ = fetch_test_data(testtype, limit=limit)
        data[testtype] = df
    return data

def get_data_from_gateway(
    device_id: str,
    barcode: str | None = None,
    limit: int = 50,
) -> dict:
    """
    Fragt den letzten Status eines Barcodes vom Gateway ab (GET).
    """
    payload = {
        "mode": "QUERY",
        "device_id": device_id,
        "limit": limit,
    }
    if barcode:
        payload["barcodenummer"] = barcode
    
    return _gateway_request(payload)


class Datenbank:
    """Datenbank-API mit logisch gruppierten Unterbereichen."""

    DUMMY_BARCODE = DUMMY_BARCODE
    GATEWAY_PORT = GATEWAY_PORT
    GATEWAY_SERVER_IP = GATEWAY_SERVER_IP
    RASPI_WIFI_SSID = RASPI_WIFI_SSID
    TESTTYPE_DB_MAP = TESTTYPE_DB_MAP

    class Gateway:
        gateway_connect = staticmethod(gateway_connect)
        is_on_gateway_wifi = staticmethod(is_on_gateway_wifi)
        send_dummy_payload_gateway = staticmethod(send_dummy_payload_gateway)
        send_payload_gateway = staticmethod(send_payload_gateway)
        get_data_from_gateway = staticmethod(get_data_from_gateway)

    class Parsing:
        parse_db_response = staticmethod(parse_db_response)
        to_dataframe = staticmethod(to_dataframe)
        find_guid_column_name = staticmethod(find_guid_column_name)
        find_time_column_name = staticmethod(find_time_column_name)
        extract_latest_test_guid = staticmethod(extract_latest_test_guid)

    class Uploads:
        upload_pdf_to_db_simple = staticmethod(upload_pdf_to_db_simple)
        upload_pdf_to_db_async = staticmethod(upload_pdf_to_db_async)

    class Dashboard:
        build_dashboard_payload = staticmethod(build_dashboard_payload)
        normalize_dashboard_entry_input = staticmethod(normalize_dashboard_entry_input)
        build_dashboard_view_model = staticmethod(build_dashboard_view_model)
        send_dashboard_entry = staticmethod(send_dashboard_entry)
        send_dashboard_entry_async = staticmethod(send_dashboard_entry_async)
        send_dashboard_entry_from_raw = staticmethod(send_dashboard_entry_from_raw)
        send_dashboard_entry_from_raw_async = staticmethod(send_dashboard_entry_from_raw_async)
        save_dashboard_entry = staticmethod(save_dashboard_entry)
        fetch_test_data = staticmethod(fetch_test_data)
        fetch_all_test_data = staticmethod(fetch_all_test_data)
        send_test_data = staticmethod(send_test_data)

    class Files:
        get_media_presence_map = staticmethod(get_media_presence_map)
        get_file_list_from_test = staticmethod(get_file_list_from_test)
        download_file_bytes = staticmethod(download_file_bytes)


class LokaleSpeicherung:
    PdfModule = PdfModule


__all__ = [
    "Datenbank",
    "LokaleSpeicherung",
]

if __name__ == "__main__":
    """
    # Beispiel: Dummy-Payload an Gateway fuer den Kleberoboter senden
    send_dummy_payload_gateway(
        device_id="kleberoboter",
        result="ok",
    )

    """
    bc = "999911200301203102103142124"
    
    # DATEN ABFRAGEN (GET)
    db_status = get_data_from_gateway("kleberoboter", bc)
    if db_status.get("status") == "OK" and db_status.get("data"):
        print(f"Letztes Ergebnis in DB: {db_status['data'][0].get('ok')}")
    else:
        print("Keine Daten vorhanden oder Fehler.")
    

    for device_id, df in fetch_all_test_data(limit=5).items():
        print(f"--- {device_id} ---")
        print(df)
    
    """
   

   

    

    

    conn = dbConnector.connection()
    try:
        conn.connect()
        start = datetime.datetime.now()
        end = datetime.datetime.now()

        conn.sendData(
            start,
            end,
            0,
            "kleberoboter",
            {"ok": True},
            miltenyiBarcode.mBarcode(str(DUMMY_BARCODE)),
            "test_user",
        )

        conn.sendData(
            start,
            end,
            0,
            "gitterschieber_tool",
            {"particle_count": 42, "justage_angle": 12.5},
            miltenyiBarcode.mBarcode(str(DUMMY_BARCODE)),
            "test_user",
        )

        conn.sendData(
            start,
            end,
            0,
            "stage_test",
            {
                "field_of_view": 1.23,
                "position": "A1",
                "x_coordinate_cam1": 100.1,
                "y_coordinate_cam1": 200.2,
                "x_coordinate_cam2": 110.3,
                "y_coordinate_cam2": 210.4,
            },
            miltenyiBarcode.mBarcode(str(DUMMY_BARCODE)),
            "test_user",
        )
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
            
"""

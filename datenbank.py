from __future__ import annotations
import datetime
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import pandas as pd
from commonIE import dbConnector
from commonIE import miltenyiBarcode

# Gateway-Defaultwerte
GATEWAY_SERVER_IP = "10.3.141.1"
GATEWAY_PORT = 5000
RASPI_WIFI_SSID = "raspi-webgui"
DUMMY_BARCODE = 999911200301203102103142124

TESTTYPE_DB_MAP = {
    "kleberoboter": "kleberoboter",
    "gitterschieber_tool": "gitterschieber_tool",
    "stage_test": "stage_test",
}


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

    return socket.create_connection((GATEWAY_SERVER_IP, GATEWAY_PORT), timeout=socket_timeout)


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

            data = conn.recv(256)
            ack = data.decode().strip() if data else None
            if ack == "":
                ack = None
        except socket.timeout:
            ack = None
    return payload, ack


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


def fetch_test_data(testtype: str, limit: int = 50) -> tuple[pd.DataFrame, bool]:
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
    
    conn = None
    try:
        conn = dbConnector.connection()
        conn.connect()

        raw = conn.getLastTests(limit, testtype)
        df = parse_db_response(raw)

        if "ok" not in df.columns:
            df["ok"] = pd.NA

        for col in ("StartTest", "EndTest"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        return df, True

    except Exception as e:
        print(f"Error fetching test data: {e}")
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


__all__ = [
    "DUMMY_BARCODE",
    "GATEWAY_PORT",
    "GATEWAY_SERVER_IP",
    "RASPI_WIFI_SSID",
    "TESTTYPE_DB_MAP",
    "gateway_connect",
    "send_dummy_payload_gateway",
    "parse_db_response",
    "fetch_test_data",
    "fetch_all_test_data",
]

if __name__ == "__main__":
    for device_id, df in fetch_all_test_data(limit=5).items():
        print(f"--- {device_id} ---")
        print(df)

    # Beispiel: Dummy-Payload an Gateway fuer den Kleberoboter senden
    send_dummy_payload_gateway(
        device_id="kleberoboter",
        result="ok",
    )

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

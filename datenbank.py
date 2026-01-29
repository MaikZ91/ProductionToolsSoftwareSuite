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
from dataclasses import dataclass

import pandas as pd
from commonIE import dbConnector
from commonIE import miltenyiBarcode

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


__all__ = [
    "DUMMY_BARCODE",
    "GATEWAY_PORT",
    "GATEWAY_SERVER_IP",
    "RASPI_WIFI_SSID",
    "TESTTYPE_DB_MAP",
    "gateway_connect",
    "send_dummy_payload_gateway",
    "send_payload_gateway",
    "parse_db_response",
    "fetch_test_data",
    "fetch_all_test_data",
    "build_dashboard_view_model",
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

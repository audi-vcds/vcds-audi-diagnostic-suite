#!/usr/bin/env python3
"""Offline ELM327 Wi-Fi OBD scan (multi-vehicle, V-LINK adapter).

Run while Mac is connected to V-LINK Wi-Fi (no internet needed).
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from obd_decode import (
    VEHICLE_STATES,
    append_index,
    decode_command,
    decode_vin,
    enrich_scan_result,
    merge_dtc_lists,
    parse_dtcs,
    parse_response_frames,
    session_index_record,
    strip_isotp,
)
from obd_vehicles import DEFAULT_VEHICLE_ID, LIVE_PIDS_SHARED, VehicleProfile, get_vehicle_profile

try:
    from obd_pids import (
        MODE01_DISCOVERY_COMMANDS,
        MODE09_DISCOVERY_COMMANDS,
        MODE09_INFO_COMMANDS,
        PID_01_NAMES,
        parse_supported_pids,
        parse_supported_pids_from_response,
        strip_isotp as pids_strip_isotp,
        _parse_response_frames as pids_parse_response_frames,
    )
except ImportError:
    # obd_pids.py may be added separately; minimal fallback for compile/run.
    MODE01_DISCOVERY_COMMANDS = ("0100", "0120", "0140", "0160", "0180", "01A0", "01C0")
    MODE09_DISCOVERY_COMMANDS = ("0900",)
    MODE09_INFO_COMMANDS = ("0902", "0904", "0906", "090A", "090D")
    PID_01_NAMES: dict[str, str] = {}
    parse_supported_pids = None  # type: ignore[assignment]

    def parse_supported_pids_from_response(cmd: str, response: str) -> list[str]:
        mode = cmd[:2]
        disc_pid = cmd[2:4]
        expected_service = 0x41 if mode == "01" else 0x49
        disc_val = int(disc_pid, 16)
        base = 0 if disc_pid == "00" else disc_val
        pids: list[str] = []
        for frames in parse_response_frames(response).values():
            for frame in frames:
                payload = strip_isotp(frame)
                if len(payload) < 6:
                    continue
                if payload[0] != expected_service or payload[1] != disc_val:
                    continue
                bitmap = payload[2:6]
                for i, byte in enumerate(bitmap):
                    for bit in range(8):
                        if byte & (1 << (7 - bit)):
                            pid_num = base + i * 8 + bit + 1
                            if pid_num <= 0xFF:
                                pids.append(f"{pid_num:02X}")
        return pids

    def pids_strip_isotp(frame: list[int]) -> list[int]:
        return strip_isotp(frame)

    def pids_parse_response_frames(response: str) -> dict[str, list[list[int]]]:
        return parse_response_frames(response)

DEFAULT_HOSTS = ("192.168.0.10", "192.168.1.1", "192.168.4.1", "192.168.0.1")
DEFAULT_PORTS = (35000, 23)
DEFAULT_AT_TIMEOUT = 4.0
DEFAULT_OBD_TIMEOUT = 12.0
DEFAULT_OBD_FAST_TIMEOUT = 6.0
DEFAULT_OBD_RETRY_TIMEOUT = 20.0
OBD_FAIL_MARKERS = frozenset(
    {"SEARCHING...", "SEARCHING", "STOPPED", "NO DATA", "UNABLE TO CONNECT", "ERROR", "?", ""}
)
INIT_COMMANDS_BASE = ("ATZ", "ATE0", "ATL0", "ATS0", "ATH1")
INIT_COMMANDS_TAIL = ("ATDPN", "ATI", "AT@1")
PROTOCOL_MAP = {
    "auto": "ATSP0",
    "0": "ATSP0",
    "6": "ATSP6",
    "7": "ATSP7",
    "3": "ATSP3",
}
PROTOCOL_FALLBACKS = ("ATSP6", "ATSP7", "ATSP0")


def protocols_to_try(atsp_cmd: str) -> tuple[str, ...]:
    """Prefer fixed CAN first — ATSP0 auto-search costs 10–30s on many adapters."""
    if atsp_cmd == "ATSP0":
        return PROTOCOL_FALLBACKS
    return (atsp_cmd,)


MODE01_RANGE_MARKERS = frozenset({"00", "20", "40", "60", "80", "A0", "C0"})
MODE09_STATIC_COMMANDS = tuple(MODE09_INFO_COMMANDS)
DTC_COMMANDS = (
    ("03", "dtc_stored"),
    ("07", "dtc_pending"),
    ("0A", "dtc_permanent"),
)
# Freeze-frame snapshot (Mode 02) — same PID set as Mode 01 when DTC was set
MODE02_CORE_COMMANDS = (
    "0200",
    "0202",
    "0204",
    "0205",
    "020C",
    "020D",
    "0211",
)
# Mode 06: on-board monitor test results (CAN: request supported MIDs then dump)
MODE06_DISCOVERY_COMMANDS = ("0600", "0620", "0640", "0660", "0680", "06A0", "06C0")

# Live / drive poll set (Mode 01). Intersected with ECU-supported PIDs.
LIVE_PIDS_DEFAULT = LIVE_PIDS_SHARED
LIVE_DISPLAY_KEYS = (
    ("rpm", "RPM"),
    ("speed_kmh", "V"),
    ("coolant_c", "ECT"),
    ("calculated_engine_load", "LOAD"),
    ("throttle_pct", "TPS"),
    ("intake_manifold_absolute_pressure", "MAP"),
    ("fuel_rail_gauge_pressure", "RAIL"),
    ("control_module_voltage_v", "VBAT"),
    ("short_term_fuel_trim_bank_1", "STFT"),
    ("long_term_fuel_trim_bank_1", "LTFT"),
    ("timing_advance", "SA"),
)


class ActionLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write("INFO", "session", "log started", {"path": str(path)})

    def _ts(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def _write(self, level: str, action: str, detail: str = "", data: dict[str, Any] | None = None) -> None:
        entry: dict[str, Any] = {
            "ts": self._ts(),
            "level": level,
            "action": action,
        }
        if detail:
            entry["detail"] = detail
        if data:
            entry["data"] = data
        self.entries.append(entry)

        line = f"{entry['ts']} [{level}] {action}"
        if detail:
            line += f" | {detail}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def info(self, action: str, detail: str = "", **data: Any) -> None:
        self._write("INFO", action, detail, data or None)

    def warn(self, action: str, detail: str = "", **data: Any) -> None:
        self._write("WARN", action, detail, data or None)

    def error(self, action: str, detail: str = "", **data: Any) -> None:
        self._write("ERROR", action, detail, data or None)

    def command(self, cmd: str, response: str, elapsed_ms: int, label: str = "") -> None:
        preview = (response or "(пусто)").replace("\n", " / ")
        if len(preview) > 180:
            preview = preview[:177] + "..."
        detail = f"cmd={cmd}"
        if label:
            detail += f" label={label}"
        detail += f" elapsed={elapsed_ms}ms response={preview}"
        self._write("CMD", "elm", detail, {"cmd": cmd, "label": label, "elapsed_ms": elapsed_ms, "response": response})


_LOGGER: ActionLogger | None = None


def log(msg: str) -> None:
    if _LOGGER is not None:
        _LOGGER.info("message", msg)
    else:
        print(msg, flush=True)


def set_logger(logger: ActionLogger) -> None:
    global _LOGGER
    _LOGGER = logger


def wifi_ssid() -> str | None:
    try:
        out = subprocess.check_output(
            ["networksetup", "-getairportnetwork", "en0"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if "not associated" in out.lower():
        return None
    if ":" in out:
        return out.split(":", 1)[1].strip()
    return out.strip() or None


def local_wifi_ip() -> str | None:
    try:
        out = subprocess.check_output(["ipconfig", "getifaddr", "en0"], text=True, timeout=5)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    ip = out.strip()
    return ip or None


def route_interface(host: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["route", "-n", "get", host],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if line.strip().startswith("interface:"):
            return line.split(":", 1)[1].strip()
    return None


def preflight(host: str) -> list[str]:
    warnings: list[str] = []
    ssid = wifi_ssid()
    ip = local_wifi_ip()
    iface = route_interface(host)

    log(f"Wi-Fi SSID: {ssid or 'не подключён'}")
    log(f"IP en0: {ip or 'нет'}")
    log(f"Маршрут до {host}: {iface or 'неизвестно'}")
    if _LOGGER:
        _LOGGER.info("preflight", data={"ssid": ssid, "ip": ip, "route_iface": iface, "host": host})

    if ssid and "v-link" not in ssid.lower():
        warnings.append(f"SSID не похож на V-LINK: {ssid}")
    if ip and not ip.startswith("192.168."):
        warnings.append(f"IP не из сети сканера: {ip}")
    if iface and iface.startswith("utun"):
        warnings.append(
            f"Трафик до {host} идёт через VPN ({iface}). "
            "Отключите VPN/прокси (Surge, Clash и т.п.) перед съёмом."
        )
    for warning in warnings:
        if _LOGGER:
            _LOGGER.warn("preflight", warning)
    return warnings


class ElmSession:
    def __init__(self, host: str, port: int, at_timeout: float = DEFAULT_AT_TIMEOUT) -> None:
        self.host = host
        self.port = port
        self.at_timeout = at_timeout
        self.sock: socket.socket | None = None
        self.obd_ready = False
        self.obd_timeout = DEFAULT_OBD_TIMEOUT
        self.obd_fast_timeout = DEFAULT_OBD_FAST_TIMEOUT
        self.obd_retry_timeout = DEFAULT_OBD_RETRY_TIMEOUT

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=8)
        self.sock.settimeout(self.at_timeout)
        time.sleep(0.15)
        self._drain()

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _drain(self) -> str:
        assert self.sock is not None
        chunks: list[bytes] = []
        deadline = time.time() + 0.5
        while time.time() < deadline:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("ascii", errors="replace")

    def send(self, cmd: str, wait: float = 0.35, label: str = "", *, obd: bool = False, timeout: float | None = None, quiet: bool = False) -> str:
        assert self.sock is not None
        started = time.perf_counter()
        read_timeout = timeout if timeout is not None else (DEFAULT_OBD_TIMEOUT if obd else self.at_timeout)
        payload = cmd if cmd.endswith("\r") else f"{cmd}\r"
        self.sock.sendall(payload.encode("ascii", errors="ignore"))
        time.sleep(wait)

        chunks: list[bytes] = []
        deadline = time.time() + read_timeout
        self.sock.settimeout(0.25)
        while time.time() < deadline:
            try:
                data = self.sock.recv(8192)
            except socket.timeout:
                if chunks and recv_complete(b"".join(chunks).decode("ascii", errors="replace"), obd=obd):
                    break
                continue
            if not data:
                break
            chunks.append(data)
            text = b"".join(chunks).decode("ascii", errors="replace")
            if recv_complete(text, obd=obd):
                # Multi-frame (VIN): give ELM a brief moment for trailing frames
                time.sleep(0.08 if cmd.startswith("09") else 0.02)
                try:
                    extra = self.sock.recv(8192)
                    if extra:
                        chunks.append(extra)
                except socket.timeout:
                    pass
                break
        self.sock.settimeout(self.at_timeout)
        raw = b"".join(chunks).decode("ascii", errors="replace")
        response = clean_elm(raw)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if _LOGGER and not quiet:
            _LOGGER.command(cmd, response, elapsed_ms, label=label)
        return response


def clean_elm(text: str) -> str:
    text = text.replace("\r", "\n")
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == ">":
            continue
        lines.append(line)
    return "\n".join(lines)


def has_ecu_hex(response: str) -> bool:
    """Detect OBD service response bytes (41/43/47/49/4A) inside ATH1 frames."""
    cleaned = clean_elm(response).upper().replace(" ", "")
    # With headers: 7E8064100… / without: 4100…
    return bool(
        re.search(r"(?:^|\n)(?:7[0-9A-F]{2})?(?:0[0-9A-F])?(4[01379A])", cleaned)
        or re.search(r"(?:^|\n)4[01379A][0-9A-F]", cleaned)
    )


def is_failed_obd_response(response: str) -> bool:
    cleaned = clean_elm(response).upper()
    if not cleaned:
        return True
    if has_ecu_hex(response):
        return False
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return all(line in OBD_FAIL_MARKERS or line.startswith("SEARCHING") for line in lines)


def recv_complete(raw: str, *, obd: bool) -> bool:
    cleaned = clean_elm(raw)
    if not obd:
        if not cleaned:
            return ">" in raw
        lines = cleaned.splitlines()
        last = lines[-1]
        if last == "OK" or last.startswith("ELM327") or "ERROR" in cleaned:
            return True
        return ">" in raw and bool(cleaned)

    upper = cleaned.upper()
    if has_ecu_hex(cleaned):
        return True
    if "NO DATA" in upper or "UNABLE TO CONNECT" in upper:
        return True
    if "SEARCHING" in upper:
        return False
    if "STOPPED" in upper or "ERROR" in upper:
        return True
    return ">" in raw and bool(cleaned)


def build_init_commands(atsp: str, *, include_atz: bool = True) -> tuple[str, ...]:
    base = INIT_COMMANDS_BASE if include_atz else INIT_COMMANDS_BASE[1:]
    return base + (atsp,) + INIT_COMMANDS_TAIL


def pid01_name(cmd: str) -> str:
    suffix = cmd[2:] if len(cmd) == 4 and cmd.startswith("01") else cmd
    return PID_01_NAMES.get(cmd) or PID_01_NAMES.get(suffix, "")


def normalize_pid_hex(pid: str) -> str:
    pid = pid.upper()
    if len(pid) == 4 and pid.startswith(("01", "09")):
        return pid[2:]
    return pid


def discovery_block_supported(supported: list[str], disc_cmd: str) -> bool:
    if disc_cmd.endswith("00"):
        return True
    marker = disc_cmd[2:4].upper()
    normalized = {normalize_pid_hex(p) for p in supported}
    return marker in normalized


def pids_to_query_commands(supported: list[str], mode: str) -> list[str]:
    markers = MODE01_RANGE_MARKERS if mode == "01" else frozenset({"00"})
    cmds: list[str] = []
    seen: set[str] = set()
    for pid in supported:
        suffix = normalize_pid_hex(pid)
        if suffix in markers:
            continue
        cmd = pid if len(pid) == 4 else f"{mode}{suffix}"
        if cmd not in seen:
            seen.add(cmd)
            cmds.append(cmd)
    return sorted(cmds)


def run_init(
    session: ElmSession,
    atsp_cmd: str,
    result: dict,
    step: list[int],
    *,
    include_atz: bool = True,
) -> str:
    protocol_used = ""
    for cmd in build_init_commands(atsp_cmd, include_atz=include_atz):
        step[0] += 1
        log(f"[{step[0]}] {cmd}")
        wait = 0.35 if cmd == "ATZ" else 0.08
        response = session.send(cmd, wait=wait, label="init")
        result["commands"][cmd] = response
        if cmd == "ATDPN":
            protocol_used = response.strip().splitlines()[-1] if response else ""
    return protocol_used


def query_obd(
    session: ElmSession,
    cmd: str,
    *,
    label: str,
    retry_probe: bool = False,
    quiet: bool = False,
) -> str:
    timeout = session.obd_fast_timeout if session.obd_ready else session.obd_timeout
    wait = 0.25 if cmd.startswith("09") else (0.05 if session.obd_ready else 0.12)
    response = session.send(cmd, wait=wait, label=label, obd=True, timeout=timeout, quiet=quiet)
    if retry_probe and is_failed_obd_response(response):
        if _LOGGER:
            _LOGGER.warn(
                "obd_retry",
                f"{cmd} пусто, повтор с таймаутом {session.obd_retry_timeout}s",
            )
        log(f"{cmd} без данных — повтор с таймаутом {int(session.obd_retry_timeout)} с...")
        response = session.send(
            cmd,
            wait=0.2,
            label=f"{label}_retry",
            obd=True,
            timeout=session.obd_retry_timeout,
            quiet=quiet,
        )
    if not is_failed_obd_response(response):
        session.obd_ready = True
    return response


def try_ecu_probe(
    session: ElmSession,
    result: dict,
    atsp_cmd: str,
    step: list[int],
) -> tuple[str, str]:
    response = ""
    protocol_used = ""
    tried = protocols_to_try(atsp_cmd)
    for idx, proto in enumerate(tried):
        if idx > 0:
            log(f"пробую протокол {proto}...")
            if _LOGGER:
                _LOGGER.info("protocol_try", proto)
        session.obd_ready = False
        # ATZ already done in TCP probe — skip on first protocol try
        protocol_used = run_init(session, proto, result, step, include_atz=(idx > 0))
        response = query_obd(
            session,
            "0100",
            label=f"probe_0100_{proto}",
            retry_probe=True,
        )
        result["commands"]["0100"] = response
        if not is_failed_obd_response(response):
            return protocol_used, response

    return protocol_used, response


def discover_mode01_pids(
    session: ElmSession,
    result: dict,
    step: list[int],
    *,
    skip_first: bool = False,
) -> list[str]:
    supported: list[str] = []
    seen_pids: set[str] = set()

    for disc_cmd in MODE01_DISCOVERY_COMMANDS:
        if not discovery_block_supported(supported, disc_cmd):
            break

        if skip_first and disc_cmd == "0100":
            response = result["commands"].get("0100", "")
            skip_first = False
        else:
            step[0] += 1
            log(f"[{step[0]}] {disc_cmd} (mode01 discovery)")
            response = query_obd(
                session,
                disc_cmd,
                label=f"discover_{disc_cmd}",
            )
            result["commands"][disc_cmd] = response

        if is_failed_obd_response(response):
            break

        result["ecu_connected"] = True
        for pid in parse_supported_pids_from_response(disc_cmd, response):
            norm = normalize_pid_hex(pid)
            if norm not in seen_pids:
                seen_pids.add(norm)
                supported.append(norm)

    log(f"discovered {len(supported)} mode01 PIDs")
    if _LOGGER:
        _LOGGER.info("mode01_discovered", count=len(supported), pids=supported)
    return supported


def discover_mode09_pids(
    session: ElmSession,
    result: dict,
    step: list[int],
) -> list[str]:
    """Only 0900 is a bitmap. VIN/CALID/ECU name queried later via MODE09_STATIC_COMMANDS."""
    supported: list[str] = []
    seen_pids: set[str] = set()

    for disc_cmd in MODE09_DISCOVERY_COMMANDS:
        step[0] += 1
        log(f"[{step[0]}] {disc_cmd} (mode09 discovery)")
        response = query_obd(
            session,
            disc_cmd,
            label=f"discover_{disc_cmd}",
        )
        result["commands"][disc_cmd] = response
        if is_failed_obd_response(response):
            # Many ECUs leave Mode 09 sparse — still try static VIN/CALID later
            continue
        result["ecu_connected"] = True
        for pid in parse_supported_pids_from_response(disc_cmd, response):
            norm = normalize_pid_hex(pid)
            if norm not in seen_pids:
                seen_pids.add(norm)
                supported.append(norm)

    log(f"discovered {len(supported)} mode09 PIDs")
    if _LOGGER:
        _LOGGER.info("mode09_discovered", count=len(supported), pids=supported)
    return supported


def query_mode01_pids(
    session: ElmSession,
    result: dict,
    supported: list[str],
    step: list[int],
) -> None:
    for cmd in pids_to_query_commands(supported, "01"):
        if cmd in result["commands"]:
            continue
        name = pid01_name(cmd)
        step[0] += 1
        detail = f"({name})" if name else ""
        log(f"[{step[0]}] querying {cmd} {detail}".rstrip())
        response = query_obd(
            session,
            cmd,
            label=name or cmd,
        )
        result["commands"][cmd] = response
        if not is_failed_obd_response(response):
            result["ecu_connected"] = True


def query_mode09_commands(
    session: ElmSession,
    result: dict,
    supported: list[str],
    step: list[int],
) -> None:
    cmds = set(pids_to_query_commands(supported, "09"))
    cmds.update(MODE09_STATIC_COMMANDS)
    for cmd in sorted(cmds):
        if cmd in result["commands"]:
            continue
        step[0] += 1
        log(f"[{step[0]}] querying {cmd} (mode09)")
        response = query_obd(
            session,
            cmd,
            label=cmd,
        )
        result["commands"][cmd] = response
        if not is_failed_obd_response(response):
            result["ecu_connected"] = True
        if cmd == "0902":
            result["vin"] = decode_vin(response)
            if _LOGGER:
                _LOGGER.info("vin_parsed", result["vin"] or "не получен")


def query_dtc_commands(
    session: ElmSession,
    result: dict,
    step: list[int],
) -> None:
    for cmd, label in DTC_COMMANDS:
        step[0] += 1
        log(f"[{step[0]}] {cmd} ({label})")
        response = query_obd(
            session,
            cmd,
            label=label,
        )
        result["commands"][cmd] = response
        if not is_failed_obd_response(response):
            result["ecu_connected"] = True
        dtc_map = parse_dtcs(response)
        result["dtc"][label] = merge_dtc_lists([dtc_map])
        if _LOGGER:
            _LOGGER.info("dtc_parsed", label, codes=result["dtc"][label])


def query_mode02_freeze_frame(
    session: ElmSession,
    result: dict,
    step: list[int],
) -> None:
    """Mode 02 — sensor snapshot at the moment a DTC was stored (if any)."""
    for cmd in MODE02_CORE_COMMANDS:
        if cmd in result["commands"]:
            continue
        step[0] += 1
        log(f"[{step[0]}] {cmd} (freeze_frame)")
        response = query_obd(session, cmd, label=f"freeze_{cmd}")
        result["commands"][cmd] = response
        if not is_failed_obd_response(response):
            result["ecu_connected"] = True


def _parse_mode06_supported(response: str, disc_cmd: str) -> list[str]:
    """Parse Mode 06 CAN supported-MID bitmap (service 0x46)."""
    if not response or len(disc_cmd) < 4 or parse_supported_pids is None:
        return []
    disc_val = int(disc_cmd[2:4], 16)
    base = 0 if disc_val == 0 else disc_val
    found: list[str] = []
    seen: set[str] = set()
    for frames in pids_parse_response_frames(response).values():
        for frame in frames:
            payload = pids_strip_isotp(frame)
            if len(payload) < 6 or payload[0] != 0x46 or payload[1] != disc_val:
                continue
            for mid in parse_supported_pids(base, payload):
                if mid not in seen:
                    seen.add(mid)
                    found.append(mid)
    return found

def query_mode06_tests(
    session: ElmSession,
    result: dict,
    step: list[int],
) -> list[str]:
    """Mode 06 — onboard monitor test results (raw MID dumps for trending)."""
    supported: list[str] = []
    seen: set[str] = set()
    for disc_cmd in MODE06_DISCOVERY_COMMANDS:
        step[0] += 1
        log(f"[{step[0]}] {disc_cmd} (mode06 discovery)")
        response = query_obd(session, disc_cmd, label=f"discover_{disc_cmd}")
        result["commands"][disc_cmd] = response
        if is_failed_obd_response(response):
            break
        result["ecu_connected"] = True
        for mid in _parse_mode06_supported(response, disc_cmd):
            if mid not in seen:
                seen.add(mid)
                supported.append(mid)

    for mid in supported:
        if mid in MODE01_RANGE_MARKERS:
            continue
        cmd = f"06{mid}"
        if cmd in result["commands"]:
            continue
        step[0] += 1
        log(f"[{step[0]}] querying {cmd} (mode06)")
        response = query_obd(session, cmd, label=f"mode06_{mid}")
        result["commands"][cmd] = response

    log(f"discovered {len(supported)} mode06 MIDs")
    if _LOGGER:
        _LOGGER.info("mode06_discovered", count=len(supported), mids=supported)
    result["supported_mids_mode06"] = supported
    return supported


def _parse_live_pids_arg(raw: str) -> list[str]:
    if not raw.strip():
        return list(LIVE_PIDS_DEFAULT)
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        pid = part.upper()
        if pid.startswith("01") and len(pid) == 4:
            pid = pid[2:]
        pid = normalize_pid_hex(pid)
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out or list(LIVE_PIDS_DEFAULT)


def _format_live_line(n: int, elapsed_s: float, metrics: dict[str, Any], cycle_ms: int) -> str:
    parts = [f"#{n:<4}", f"{elapsed_s:6.1f}s", f"{cycle_ms:4}ms"]
    for key, label in LIVE_DISPLAY_KEYS:
        if key not in metrics:
            continue
        val = metrics[key]
        if isinstance(val, float):
            parts.append(f"{label}={val:.1f}" if abs(val) < 1000 else f"{label}={val:.0f}")
        else:
            parts.append(f"{label}={val}")
    return "  ".join(parts)


def _connect_session(
    result: dict,
    hosts: tuple[str, ...],
    ports: tuple[int, ...],
    obd_timeout: float,
    obd_retry_timeout: float,
    obd_fast_timeout: float,
) -> ElmSession | None:
    for host in hosts:
        for port in ports:
            if _LOGGER:
                _LOGGER.info("connect_try", f"{host}:{port}")
            else:
                log(f"пробую {host}:{port} ...")
            candidate = ElmSession(host, port)
            try:
                candidate.connect()
                if _LOGGER:
                    _LOGGER.info("connect_tcp", f"TCP OK {host}:{port}")
                probe = candidate.send("ATZ", wait=0.35, label="probe")
                if not probe:
                    if _LOGGER:
                        _LOGGER.warn("connect_empty", f"порт открыт, ответ пустой — {host}:{port}")
                    candidate.close()
                    continue
                result["adapter"] = {"host": host, "port": port}
                if _LOGGER:
                    _LOGGER.info("connect_ok", f"{host}:{port}")
                candidate.obd_timeout = obd_timeout
                candidate.obd_fast_timeout = obd_fast_timeout
                candidate.obd_retry_timeout = obd_retry_timeout
                return candidate
            except OSError as exc:
                if _LOGGER:
                    _LOGGER.error("connect_fail", f"{host}:{port}", error=str(exc))
                candidate.close()
    return None


def live_monitor(
    hosts: tuple[str, ...],
    ports: tuple[int, ...],
    output: Path,
    *,
    interval: float = 1.0,
    live_pids: list[str] | None = None,
    obd_timeout: float = DEFAULT_OBD_TIMEOUT,
    obd_retry_timeout: float = DEFAULT_OBD_RETRY_TIMEOUT,
    obd_fast_timeout: float = DEFAULT_OBD_FAST_TIMEOUT,
    label: str = "live",
    vehicle_state: str = "engine_running",
    note: str = "",
    protocol: str = "auto",
    vehicle_profile: VehicleProfile | None = None,
) -> dict:
    """Poll dynamic Mode 01 PIDs until Ctrl+C; append each cycle to .live.jsonl."""
    profile = vehicle_profile or get_vehicle_profile(None)
    started = datetime.now(timezone.utc).isoformat()
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    atsp_cmd = PROTOCOL_MAP[protocol]
    wanted = [normalize_pid_hex(p) for p in (live_pids or list(profile.live_pids))]
    result: dict = {
        "schema_version": 2,
        "session_id": session_id,
        "started_at": started,
        "vehicle_profile": profile.to_context(),
        "context": {
            "label": label,
            "vehicle_state": vehicle_state,
            "note": note or "live monitor until Ctrl+C",
            "vehicle_id": profile.id,
            "vehicle_display": profile.display_name,
        },
        "hosts_tried": list(hosts),
        "ports_tried": list(ports),
        "wifi_ssid": wifi_ssid(),
        "local_ip": local_wifi_ip(),
        "warnings": [],
        "adapter": None,
        "commands": {},
        "dtc": {},
        "vin": None,
        "supported_pids_mode01": [],
        "supported_pids_mode09": [],
        "supported_mids_mode06": [],
        "protocol_used": None,
        "scan_mode": "live",
        "live": {
            "interval_s": interval,
            "pids_wanted": wanted,
            "pids_polled": [],
            "samples": 0,
            "jsonl": None,
        },
        "ecu_connected": False,
        "success": False,
        "error": None,
        "action_log": [],
        "metrics": {},
    }
    result["warnings"] = preflight(hosts[0])

    jsonl_path = output.with_suffix(".live.jsonl")
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    result["live"]["jsonl"] = str(jsonl_path)

    session: ElmSession | None = None
    try:
        session = _connect_session(
            result, hosts, ports, obd_timeout, obd_retry_timeout, obd_fast_timeout
        )
        if session is None:
            result["error"] = "Не удалось получить ответ от адаптера ни на одном host:port"
            if _LOGGER:
                _LOGGER.error("scan_abort", result["error"])
            return result

        step = [0]
        protocol_used, probe_response = try_ecu_probe(session, result, atsp_cmd, step)
        result["protocol_used"] = protocol_used
        probe_ok = not is_failed_obd_response(probe_response)
        if not probe_ok:
            result["error"] = (
                "Адаптер отвечает, но ECU не ответил (SEARCHING/STOPPED). "
                "Проверьте зажигание и Wi-Fi V-LINK."
            )
            return result

        result["ecu_connected"] = True
        supported = discover_mode01_pids(session, result, step, skip_first=True)
        result["supported_pids_mode01"] = [normalize_pid_hex(p) for p in supported]
        supported_set = set(result["supported_pids_mode01"])
        poll_pids = [p for p in wanted if p in supported_set]
        # Odometer is slow — poll every 10th cycle
        slow_pids = {"A6"}
        fast_pids = [p for p in poll_pids if p not in slow_pids]
        slow_list = [p for p in poll_pids if p in slow_pids]
        result["live"]["pids_polled"] = poll_pids
        if not fast_pids:
            result["error"] = "Нет пересечения LIVE PID с поддерживаемыми ECU"
            return result

        # VIN once (best-effort)
        step[0] += 1
        vin_resp = query_obd(session, "0902", label="vin_once")
        result["commands"]["0902"] = vin_resp
        result["vin"] = decode_vin(vin_resp)

        log("")
        log(
            f"LIVE: опрос {len(poll_pids)} PID каждые {interval:g}s "
            f"({', '.join('01' + p for p in poll_pids)})"
        )
        log("Остановка: Ctrl+C")
        log(f"Сэмплы → {jsonl_path}")
        log("")

        t0 = time.monotonic()
        n = 0
        with jsonl_path.open("w", encoding="utf-8") as fh:
            while True:
                n += 1
                cycle_cmds = list(fast_pids)
                if slow_list and (n == 1 or n % 10 == 0):
                    cycle_cmds.extend(slow_list)
                cycle_raw: dict[str, str] = {}
                metrics: dict[str, Any] = {}
                t_cycle = time.perf_counter()
                for pid in cycle_cmds:
                    cmd = f"01{pid}"
                    response = query_obd(session, cmd, label=f"live_{pid}", quiet=True)
                    cycle_raw[cmd] = response
                    result["commands"][cmd] = response
                    decoded = decode_command(cmd, response)
                    for ecu_id, values in decoded.items():
                        if ecu_id == "vehicle":
                            continue
                        bucket = metrics.setdefault(ecu_id, {})
                        for k, v in values.items():
                            if k != "dtc":
                                bucket[k] = v
                cycle_ms = int((time.perf_counter() - t_cycle) * 1000)
                engine = metrics.get("7E8") or next(iter(metrics.values()), {})
                sample = {
                    "n": n,
                    "t": datetime.now(timezone.utc).isoformat(),
                    "elapsed_s": round(time.monotonic() - t0, 3),
                    "cycle_ms": cycle_ms,
                    "metrics": engine,
                    "by_ecu": metrics,
                    "commands": cycle_raw,
                }
                fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
                fh.flush()
                result["live"]["samples"] = n
                result["metrics"] = metrics
                print(_format_live_line(n, sample["elapsed_s"], engine, cycle_ms), flush=True)

                # sleep remainder of interval (ponytail: simple, no catch-up backlog)
                spent = time.perf_counter() - t_cycle
                sleep_for = interval - spent
                if sleep_for > 0:
                    time.sleep(sleep_for)
    except KeyboardInterrupt:
        log("")
        log(f"LIVE остановлен (Ctrl+C), сэмплов: {result['live']['samples']}")
        result["success"] = result["ecu_connected"] and result["live"]["samples"] > 0
        if _LOGGER:
            _LOGGER.info("live_stop", f"samples={result['live']['samples']}")
    finally:
        if session is not None:
            session.close()
            if _LOGGER:
                _LOGGER.info("session_close", "адаптер отключён")
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        if result["ecu_connected"] and result["live"]["samples"] > 0:
            result["success"] = True
        if _LOGGER:
            result["action_log"] = _LOGGER.entries
            _LOGGER.info("session", "log finished")
    return result


def scan(
    hosts: tuple[str, ...],
    ports: tuple[int, ...],
    obd_timeout: float = DEFAULT_OBD_TIMEOUT,
    obd_retry_timeout: float = DEFAULT_OBD_RETRY_TIMEOUT,
    obd_fast_timeout: float = DEFAULT_OBD_FAST_TIMEOUT,
    *,
    label: str = "unspecified",
    vehicle_state: str = "unknown",
    note: str = "",
    protocol: str = "auto",
    vehicle_profile: VehicleProfile | None = None,
) -> dict:
    profile = vehicle_profile or get_vehicle_profile(None)
    started = datetime.now(timezone.utc).isoformat()
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    atsp_cmd = PROTOCOL_MAP[protocol]
    result: dict = {
        "schema_version": 2,
        "session_id": session_id,
        "started_at": started,
        "vehicle_profile": profile.to_context(),
        "context": {
            "label": label,
            "vehicle_state": vehicle_state,
            "note": note,
            "vehicle_id": profile.id,
            "vehicle_display": profile.display_name,
        },
        "hosts_tried": list(hosts),
        "ports_tried": list(ports),
        "wifi_ssid": wifi_ssid(),
        "local_ip": local_wifi_ip(),
        "warnings": [],
        "adapter": None,
        "commands": {},
        "dtc": {},
        "vin": None,
        "supported_pids_mode01": [],
        "supported_pids_mode09": [],
        "supported_mids_mode06": [],
        "protocol_used": None,
        "scan_mode": "full",
        "ecu_connected": False,
        "success": False,
        "error": None,
        "action_log": [],
    }

    result["warnings"] = preflight(hosts[0])

    session: ElmSession | None = None
    try:
        session = _connect_session(
            result, hosts, ports, obd_timeout, obd_retry_timeout, obd_fast_timeout
        )
        if session is None:
            result["error"] = "Не удалось получить ответ от адаптера ни на одном host:port"
            if _LOGGER:
                _LOGGER.error("scan_abort", result["error"])
            return result

        step = [0]
        protocol_used, probe_response = try_ecu_probe(session, result, atsp_cmd, step)
        result["protocol_used"] = protocol_used
        probe_ok = not is_failed_obd_response(probe_response)
        if probe_ok:
            result["ecu_connected"] = True
        supported_mode01 = discover_mode01_pids(
            session,
            result,
            step,
            skip_first=probe_ok,
        )
        query_mode01_pids(session, result, supported_mode01, step)
        supported_mode09 = discover_mode09_pids(session, result, step)
        query_mode09_commands(session, result, supported_mode09, step)
        query_dtc_commands(session, result, step)
        query_mode02_freeze_frame(session, result, step)
        query_mode06_tests(session, result, step)

        result["supported_pids_mode01"] = [normalize_pid_hex(p) for p in supported_mode01]
        result["supported_pids_mode09"] = [normalize_pid_hex(p) for p in supported_mode09]
        result["success"] = result["ecu_connected"]
        if not result["success"]:
            result["error"] = (
                "Адаптер отвечает, но ECU не ответил (SEARCHING/STOPPED). "
                "Проверьте зажигание, блокиратор OBD на сигнализации, протокол CAN."
            )
            if _LOGGER:
                _LOGGER.error("scan_incomplete", result["error"])
        elif _LOGGER:
            _LOGGER.info("scan_ok", "OBD данные получены")
        return result
    finally:
        if session is not None:
            session.close()
            if _LOGGER:
                _LOGGER.info("session_close", "адаптер отключён")
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        if _LOGGER:
            result["action_log"] = _LOGGER.entries
            _LOGGER.info("session", "log finished")


def write_outputs(result: dict, output: Path, dumps_root: Path) -> tuple[Path, Path, Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = output.with_suffix(".json")
    txt_path = output.with_suffix(".txt")
    log_path = output.with_suffix(".log")
    done_path = output.with_suffix(".done")

    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if _LOGGER:
        _LOGGER.info("write_json", str(json_path))

    summary = result.get("summary") or {}
    ctx = result.get("context") or {}
    lines = [
        "OBD scan",
        f"session_id: {result.get('session_id')}",
        f"vehicle: {ctx.get('vehicle_display') or (result.get('vehicle_profile') or {}).get('display_name')}",
        f"label: {ctx.get('label')}",
        f"vehicle_state: {ctx.get('vehicle_state')}",
        f"note: {ctx.get('note') or '—'}",
        f"started: {result.get('started_at')}",
        f"finished: {result.get('finished_at')}",
        f"wifi: {result.get('wifi_ssid')}",
        f"ip: {result.get('local_ip')}",
        f"adapter: {result.get('adapter')}",
        f"scan_mode: {result.get('scan_mode')}",
        f"protocol_used: {result.get('protocol_used')}",
        f"ecu_connected: {result.get('ecu_connected')}",
        f"success: {result.get('success')}",
        f"log: {log_path}",
        "",
    ]
    live_meta = result.get("live") or {}
    if live_meta:
        lines.extend(
            [
                "live:",
                f"  samples: {live_meta.get('samples')}",
                f"  interval_s: {live_meta.get('interval_s')}",
                f"  pids: {', '.join(live_meta.get('pids_polled') or [])}",
                f"  jsonl: {live_meta.get('jsonl')}",
                "",
            ]
        )
    if result.get("warnings"):
        lines.append("warnings:")
        lines.extend(f"  - {w}" for w in result["warnings"])
        lines.append("")
    vin = (result.get("vehicle") or {}).get("vin") or result.get("vin")
    if vin:
        lines.extend(["VIN:", f"  {vin}", ""])
    if summary.get("dtc"):
        lines.extend(["DTC:", f"  {', '.join(summary['dtc'])}", ""])
    elif result.get("dtc"):
        lines.append("DTC:")
        for key, codes in result["dtc"].items():
            lines.append(f"  {key}: {', '.join(codes) if codes else 'нет'}")
        lines.append("")

    lines.append("metrics (engine 7E8):")
    engine = summary.get("engine") or (result.get("metrics") or {}).get("7E8", {})
    if engine:
        for key, val in sorted(engine.items()):
            lines.append(f"  {key}: {val}")
    else:
        lines.append("  —")
    lines.append("")
    lines.append("metrics (transmission 7E9):")
    transmission = summary.get("transmission") or (result.get("metrics") or {}).get("7E9", {})
    if transmission:
        for key, val in sorted(transmission.items()):
            lines.append(f"  {key}: {val}")
    else:
        lines.append("  —")
    lines.append("")
    lines.append("raw commands:")
    for cmd, resp in result.get("commands", {}).items():
        lines.append(f"--- {cmd} ---")
        lines.append(resp or "(пусто)")
        lines.append("")

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    if _LOGGER:
        _LOGGER.info("write_txt", str(txt_path))

    index_path = dumps_root / "index.jsonl"
    append_index(index_path, session_index_record(result, json_path))
    if _LOGGER:
        _LOGGER.info("write_index", str(index_path))

    date_dir = dumps_root / datetime.now().strftime("%Y-%m-%d") / "sessions"
    date_dir.mkdir(parents=True, exist_ok=True)
    archive_json = date_dir / json_path.name
    if archive_json.resolve() != json_path.resolve():
        archive_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
        if _LOGGER:
            _LOGGER.info("write_archive", str(archive_json))

    done_path.write_text(
        f"ok={result.get('success')}\njson={json_path}\ntxt={txt_path}\nlog={log_path}\nindex={index_path}\n",
        encoding="utf-8",
    )
    if _LOGGER:
        _LOGGER.info("write_done", str(done_path))
    return json_path, txt_path, log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline OBD scan via V-LINK Wi-Fi ELM327")
    parser.add_argument(
        "--output",
        default="",
        help="Путь без расширения (создаст .json .txt .log .done). "
        "По умолчанию: EXEED → obd-dumps/obd-…, Honda → obd-dumps/honda-civic-2013/obd-…",
    )
    parser.add_argument("--host", action="append", default=[], help="IP адаптера (можно несколько раз)")
    parser.add_argument("--port", type=int, action="append", default=[], help="TCP порт (можно несколько раз)")
    parser.add_argument(
        "--obd-timeout",
        type=float,
        default=DEFAULT_OBD_TIMEOUT,
        help=f"Таймаут OBD-запросов в секундах (по умолчанию {DEFAULT_OBD_TIMEOUT:g})",
    )
    parser.add_argument(
        "--obd-retry-timeout",
        type=float,
        default=DEFAULT_OBD_RETRY_TIMEOUT,
        help=f"Таймаут повтора при пустом ответе (по умолчанию {DEFAULT_OBD_RETRY_TIMEOUT:g})",
    )
    parser.add_argument(
        "--obd-fast-timeout",
        type=float,
        default=DEFAULT_OBD_FAST_TIMEOUT,
        help=f"Таймаут после первого успешного OBD-ответа (по умолчанию {DEFAULT_OBD_FAST_TIMEOUT:g})",
    )
    parser.add_argument(
        "--vehicle",
        default=DEFAULT_VEHICLE_ID,
        metavar="PROFILE",
        help=(
            "Профиль авто: exeed_vx (по умолчанию), honda_civic_2013. "
            "Влияет на каталог дампов, протокол по умолчанию и LIVE PID"
        ),
    )
    parser.add_argument(
        "--protocol",
        choices=sorted(PROTOCOL_MAP),
        default=None,
        help="OBD протокол: auto=ATSP0, 6/7/3=CAN variants (по умолчанию — из профиля авто)",
    )
    parser.add_argument(
        "--label",
        default="unspecified",
        help="Метка сессии для сравнения (например baseline, before_trip)",
    )
    parser.add_argument(
        "--state",
        choices=VEHICLE_STATES,
        default="unknown",
        help="Состояние авто в момент съёма",
    )
    parser.add_argument("--note", default="", help="Произвольный комментарий к сессии")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Непрерывный опрос динамических PID (поездка) до Ctrl+C",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Интервал между циклами live-опроса, сек (по умолчанию 1)",
    )
    parser.add_argument(
        "--pids",
        default="",
        help="Список Mode01 PID для live через запятую (напр. 0C,0D,05). Пусто = набор по умолчанию",
    )
    args = parser.parse_args()

    try:
        vehicle_profile = get_vehicle_profile(args.vehicle)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    protocol = args.protocol or vehicle_profile.default_protocol
    if protocol not in PROTOCOL_MAP:
        print(f"Неизвестный протокол: {protocol}", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dumps_root = Path(__file__).resolve().parent.parent / "obd-dumps"
    default_dir = vehicle_profile.dumps_dir(dumps_root)
    default_dir.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else default_dir / f"obd-{stamp}"

    hosts = tuple(args.host) if args.host else DEFAULT_HOSTS
    ports = tuple(args.port) if args.port else DEFAULT_PORTS
    atsp_cmd = PROTOCOL_MAP[protocol]
    mode = "live" if args.live else "full"

    logger = ActionLogger(output.with_suffix(".log"))
    set_logger(logger)

    log("=== OBD scan (offline) ===")
    log(f"Авто: {vehicle_profile.display_name} (профиль {vehicle_profile.id})")
    for note_line in vehicle_profile.preflight_notes:
        log(f"  • {note_line}")
    log("Зажигание: ON. VPN/прокси: выключить.")
    log(f"Сессия: label={args.label} state={args.state} mode={mode}")
    if args.note:
        log(f"Заметка: {args.note}")
    log(
        f"OBD таймаут: {args.obd_timeout:g}s (fast: {args.obd_fast_timeout:g}s, "
        f"повтор: {args.obd_retry_timeout:g}s), протокол: {atsp_cmd} (CAN первым), режим: {mode}"
    )
    if args.live:
        log(f"LIVE interval={args.interval:g}s  stop=Ctrl+C")
    log(f"Результат: {output}.{{json,txt,log,done}}")
    logger.info(
        "scan_start",
        f"hosts={hosts} ports={ports} obd_timeout={args.obd_timeout} "
        f"obd_retry={args.obd_retry_timeout} protocol={atsp_cmd} scan_mode={mode} "
        f"vehicle={vehicle_profile.id}",
    )
    log("")

    if args.live:
        result = live_monitor(
            hosts,
            ports,
            output,
            interval=max(0.2, args.interval),
            live_pids=_parse_live_pids_arg(args.pids) if args.pids.strip() else None,
            obd_timeout=args.obd_timeout,
            obd_retry_timeout=args.obd_retry_timeout,
            obd_fast_timeout=args.obd_fast_timeout,
            label=args.label if args.label != "unspecified" else "live",
            vehicle_state=args.state if args.state != "unknown" else "engine_running",
            note=args.note,
            protocol=protocol,
            vehicle_profile=vehicle_profile,
        )
    else:
        result = scan(
            hosts,
            ports,
            args.obd_timeout,
            args.obd_retry_timeout,
            args.obd_fast_timeout,
            label=args.label,
            vehicle_state=args.state,
            note=args.note,
            protocol=protocol,
            vehicle_profile=vehicle_profile,
        )
    result = enrich_scan_result(result)
    # Единый index для всех авто — как до введения профилей.
    json_path, txt_path, log_path = write_outputs(result, output, dumps_root)

    log("")
    if result.get("success") or result.get("ecu_connected"):
        log(f"ГОТОВО (ECU ответил): {json_path}")
        log(f"Краткий отчёт: {txt_path}")
        log(f"Лог действий: {log_path}")
        if args.live and result.get("live", {}).get("jsonl"):
            log(f"LIVE сэмплы: {result['live']['jsonl']} ({result['live']['samples']} шт.)")
        log("Можно возвращаться в интернет и прислать .json, .txt или .log в чат.")
        return 0

    log(f"СБОЙ: {result.get('error')}")
    log(f"Лог действий: {log_path}")
    log(f"Отчёт: {txt_path}")
    log("Проверьте: Wi-Fi V-LINK, зажигание ON, VPN выкл, режим ТО на сигнализации.")
    return 2


if __name__ == "__main__":
    sys.exit(main())

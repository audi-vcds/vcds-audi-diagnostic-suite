#!/usr/bin/env python3
"""Decode ELM327 CAN/OBD responses for session tracking."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from obd_pids import (
        PID_01_NAMES,
        decode_mode01_pid,
        decode_mode09,
        parse_supported_pids,
        parse_supported_pids_from_response,
    )
except ImportError:
    PID_01_NAMES = {}
    decode_mode01_pid = None  # type: ignore[assignment,misc]
    decode_mode09 = None  # type: ignore[assignment,misc]
    parse_supported_pids = None  # type: ignore[assignment,misc]
    parse_supported_pids_from_response = None  # type: ignore[assignment,misc]

SCHEMA_VERSION = 2

ECU_NAMES: dict[str, str] = {
    "7E8": "engine",
    "7E9": "transmission",
    "7E0": "engine_req",
    "7E1": "transmission_req",
}

VEHICLE_STATES = (
    "unknown",
    "ignition_on",
    "engine_idle",
    "engine_running",
    "cold_start",
    "warm_idle",
    "after_drive",
    "charging_ev",
    "custom",
)

CAN_ID_PREFIXES = ("7E8", "7E9", "7E0", "7E1")
CAN_ID_RE = re.compile(r"^(7[0-9A-F]{2})")

MODE01_DISCOVERY_COMMANDS = ("0100", "0120", "0140", "0160", "0180", "01A0", "01C0")
MODE09_DISCOVERY_COMMANDS = ("0900",)

# Short keys used by schema v1 dumps and obd-sessions.py list/compare views.
LEGACY_METRIC_ALIASES: dict[str, str] = {
    "engine_rpm": "rpm",
    "vehicle_speed": "speed_kmh",
    "engine_coolant_temperature": "coolant_c",
    "intake_air_temperature": "intake_air_c",
    "throttle_position": "throttle_pct",
    "fuel_tank_level": "fuel_level_pct",
    "control_module_voltage": "control_module_voltage_v",
    "odometer": "odometer_km",
}


def split_can_line(line: str) -> tuple[str, list[int]] | None:
    cleaned = line.strip().upper().replace(" ", "")
    if not re.fullmatch(r"[0-9A-F]+", cleaned):
        return None
    match = CAN_ID_RE.match(cleaned)
    if match is None:
        return None
    can_id = match.group(1)
    payload_hex = cleaned[len(can_id) :]
    if len(payload_hex) % 2:
        return None
    data = [int(payload_hex[i : i + 2], 16) for i in range(0, len(payload_hex), 2)]
    return can_id, data


def parse_response_frames(response: str) -> dict[str, list[list[int]]]:
    frames: dict[str, list[list[int]]] = {}
    for line in response.splitlines():
        parsed = split_can_line(line)
        if parsed is None:
            continue
        can_id, data = parsed
        frames.setdefault(can_id, []).append(data)
    return frames


def reassemble_isotp(frames: list[list[int]]) -> bytes:
    if not frames:
        return b""
    out = bytearray()
    total: int | None = None
    first = frames[0]
    if (first[0] & 0xF0) == 0x10 and len(first) >= 2:
        total = ((first[0] & 0x0F) << 8) | first[1]
        out.extend(first[2:])
        for frame in frames[1:]:
            if frame and (frame[0] & 0xF0) == 0x20:
                out.extend(frame[1:])
    elif (first[0] & 0xF0) == 0x00:
        out.extend(first[1:] if len(first) > 1 else first)
    else:
        out.extend(first)
    if total is not None:
        return bytes(out[:total])
    return bytes(out)


def decode_vin(response: str) -> str | None:
    frames_by_ecu = parse_response_frames(response)
    for ecu_frames in frames_by_ecu.values():
        assembled = reassemble_isotp(ecu_frames)
        marker = assembled.find(bytes([0x49, 0x02, 0x01]))
        if marker >= 0:
            vin_bytes = assembled[marker + 3 : marker + 20]
            vin = "".join(chr(b) for b in vin_bytes if 32 <= b <= 126)
            match = re.search(r"[A-HJ-NPR-Z0-9]{17}", vin)
            if match:
                return match.group(0)

    hex_blob = re.sub(r"[^0-9A-Fa-f]", "", response).upper()
    marker = hex_blob.find("4C5654")
    if marker >= 0 and len(hex_blob) >= marker + 34:
        try:
            vin = bytes.fromhex(hex_blob[marker : marker + 34]).decode("ascii")
        except ValueError:
            vin = ""
        if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            return vin
    return None


def parse_dtcs(response: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for can_id, frames in parse_response_frames(response).items():
        for frame in frames:
            payload = strip_isotp(frame)
            if len(payload) < 2:
                continue
            service = payload[0]
            if service not in (0x43, 0x47, 0x4A):
                continue
            count = payload[1]
            dtc_payload = payload[2 : 2 + count * 2]
            codes: list[str] = []
            for i in range(0, len(dtc_payload), 2):
                if i + 1 >= len(dtc_payload):
                    break
                b1, b2 = dtc_payload[i], dtc_payload[i + 1]
                prefix = "PCBU"[(b1 >> 6) & 3]
                codes.append(f"{prefix}{((b1 & 0x3F) << 8) | b2:04X}")
            if codes or count == 0:
                result[can_id] = codes
    return result


def merge_dtc_lists(dtc_maps: list[dict[str, list[str]]]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for dtc_map in dtc_maps:
        for codes in dtc_map.values():
            for code in codes:
                if code not in seen:
                    seen.add(code)
                    merged.append(code)
    return merged


def strip_isotp(frame: list[int]) -> list[int]:
    if not frame:
        return []
    pci = frame[0]
    if (pci & 0xF0) == 0x00:
        length = pci & 0x0F
        return frame[1 : 1 + length]
    if (pci & 0xF0) == 0x10 and len(frame) >= 2:
        return frame[2:]
    if (pci & 0xF0) == 0x20:
        return frame[1:]
    return frame


def decode_mode01(pid: str, frame: list[int]) -> dict[str, Any]:
    """Legacy fallback decoder when obd_pids is unavailable."""
    payload = strip_isotp(frame)
    if len(payload) < 2 or payload[0] != 0x41:
        return {}
    data = payload[2:]
    metrics: dict[str, Any] = {}

    if pid == "0C" and len(data) >= 2:
        metrics["rpm"] = round(((data[0] << 8) | data[1]) / 4, 1)
    elif pid == "0D" and len(data) >= 1:
        metrics["speed_kmh"] = data[0]
    elif pid == "05" and len(data) >= 1:
        metrics["coolant_c"] = data[0] - 40
    elif pid == "0F" and len(data) >= 1:
        metrics["intake_air_c"] = data[0] - 40
    elif pid == "11" and len(data) >= 1:
        metrics["throttle_pct"] = round(data[0] * 100 / 255, 1)
    elif pid == "2F" and len(data) >= 1:
        metrics["fuel_level_pct"] = round(data[0] * 100 / 255, 1)
    elif pid == "42" and len(data) >= 2:
        metrics["control_module_voltage_v"] = round(((data[0] << 8) | data[1]) / 1000, 2)

    return metrics


def _decode_mode01_frame(pid: str, frame: list[int]) -> dict[str, Any]:
    payload = strip_isotp(frame)
    if len(payload) < 2 or payload[0] != 0x41:
        return {}
    data = payload[2:]
    if decode_mode01_pid is not None:
        return decode_mode01_pid(pid, data)
    return decode_mode01(pid, frame)


def _merge_metric_values(bucket: dict[str, Any], pid: str, values: dict[str, Any]) -> None:
    if not values:
        return
    pid_name = PID_01_NAMES.get(pid, pid.lower())
    for key, value in values.items():
        if key == "raw_hex":
            bucket[f"pid_{pid}_{pid_name}"] = value
            continue
        bucket[key] = value
        legacy_key = LEGACY_METRIC_ALIASES.get(key)
        if legacy_key:
            bucket[legacy_key] = value


def decode_command(cmd: str, response: str) -> dict[str, dict[str, Any]]:
    """Return per-ECU decoded metrics for a single OBD command."""
    cmd = cmd.upper()
    per_ecu: dict[str, dict[str, Any]] = {}

    if cmd in {"03", "07", "0A"}:
        for can_id, codes in parse_dtcs(response).items():
            per_ecu.setdefault(can_id, {})["dtc"] = codes
        return per_ecu

    if cmd.startswith("09") and len(cmd) == 4:
        pid = cmd[2:4]
        if decode_mode09 is not None:
            decoded = decode_mode09(pid, response)
            if decoded:
                per_ecu["vehicle"] = decoded
        elif cmd == "0902":
            vin = decode_vin(response)
            if vin:
                per_ecu["vehicle"] = {"vin": vin}
        return per_ecu

    if cmd.startswith("01") and len(cmd) == 4:
        pid = cmd[2:4]
        for can_id, frames in parse_response_frames(response).items():
            for frame in frames:
                metrics = _decode_mode01_frame(pid, frame)
                if metrics:
                    bucket = per_ecu.setdefault(can_id, {})
                    _merge_metric_values(bucket, pid, metrics)
    return per_ecu


def _normalize_vehicle_info(info: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(info)
    vin = normalized.get("vin") or normalized.get("vehicle_identification_number")
    if vin:
        normalized["vin"] = vin
    return normalized


def _derive_supported_pids_mode01(commands: dict[str, str]) -> list[str] | None:
    if parse_supported_pids_from_response is not None:
        discovered: list[str] = []
        for cmd in MODE01_DISCOVERY_COMMANDS:
            response = commands.get(cmd)
            if response:
                discovered.extend(parse_supported_pids_from_response(cmd, response))
        return sorted(set(discovered)) if discovered else None
    if parse_supported_pids is None:
        return None
    discovered: list[str] = []
    for cmd in MODE01_DISCOVERY_COMMANDS:
        response = commands.get(cmd)
        if not response:
            continue
        base_pid = int(cmd[2:4], 16)
        for frames in parse_response_frames(response).values():
            for frame in frames:
                payload = strip_isotp(frame)
                discovered.extend(parse_supported_pids(base_pid, payload))
    return sorted(set(discovered)) if discovered else None


def _derive_supported_pids_mode09(commands: dict[str, str]) -> list[str] | None:
    response = commands.get("0900")
    if not response:
        return None
    if parse_supported_pids_from_response is not None:
        discovered = parse_supported_pids_from_response("0900", response)
        return sorted(set(discovered)) if discovered else None
    if parse_supported_pids is None:
        return None
    discovered: list[str] = []
    for frames in parse_response_frames(response).values():
        for frame in frames:
            payload = strip_isotp(frame)
            discovered.extend(parse_supported_pids(0, payload))
    return sorted(set(discovered)) if discovered else None


def enrich_scan_result(result: dict[str, Any]) -> dict[str, Any]:
    """Add schema_version, metrics, ecus, session summary from raw scan result."""
    commands: dict[str, str] = result.get("commands", {})
    ecus: dict[str, dict[str, Any]] = {}
    metrics: dict[str, dict[str, Any]] = {}
    vehicle_info: dict[str, Any] = _normalize_vehicle_info(dict(result.get("vehicle_info") or {}))
    vin: str | None = result.get("vin") or vehicle_info.get("vin")

    for cmd, response in commands.items():
        if not response or cmd.startswith("AT"):
            continue
        decoded = decode_command(cmd, response)
        for key, values in decoded.items():
            if key == "vehicle":
                vehicle_info.update(values)
                vehicle_info = _normalize_vehicle_info(vehicle_info)
                if vehicle_info.get("vin"):
                    vin = vehicle_info["vin"]
                continue
            ecus.setdefault(key, {})[cmd] = response.splitlines()
            bucket = metrics.setdefault(key, {})
            for metric_key, metric_val in values.items():
                if metric_key != "dtc":
                    bucket[metric_key] = metric_val

    dtc_block = result.get("dtc") or {}
    all_dtcs = merge_dtc_lists(
        [parse_dtcs(commands.get(cmd, "")) for cmd in ("03", "07", "0A") if cmd in commands]
    )

    context = result.get("context") or {
        "label": "unspecified",
        "vehicle_state": "unknown",
        "note": "",
    }

    session_id = result.get("session_id")
    if not session_id:
        session_id = result.get("started_at", "")[:19].replace(":", "").replace("T", "-")

    supported_pids_mode01 = result.get("supported_pids_mode01")
    if supported_pids_mode01 is None:
        supported_pids_mode01 = _derive_supported_pids_mode01(commands)

    supported_pids_mode09 = result.get("supported_pids_mode09")
    if supported_pids_mode09 is None:
        supported_pids_mode09 = _derive_supported_pids_mode09(commands)

    if vin is None and vehicle_info.get("vin"):
        vin = vehicle_info["vin"]
    vehicle_info = _normalize_vehicle_info(vehicle_info)

    enriched = dict(result)
    enriched.update(
        {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "context": context,
            "vehicle": {"vin": vin},
            "vin": vin,
            "vehicle_info": vehicle_info,
            "ecus": {
                ecu_id: {
                    "name": ECU_NAMES.get(ecu_id, ecu_id),
                    "metrics": metrics.get(ecu_id, {}),
                }
                for ecu_id in sorted(set(ecus) | set(metrics))
                if ecu_id != "vehicle"
            },
            "metrics": metrics,
            "dtc_all": all_dtcs,
            "summary": build_summary(metrics, all_dtcs, vin, context, vehicle_info),
        }
    )

    if supported_pids_mode01 is not None:
        enriched["supported_pids_mode01"] = supported_pids_mode01
    if supported_pids_mode09 is not None:
        enriched["supported_pids_mode09"] = supported_pids_mode09
    if result.get("protocol_used") is not None:
        enriched["protocol_used"] = result["protocol_used"]
    if result.get("scan_mode") is not None:
        enriched["scan_mode"] = result["scan_mode"]

    return enriched


def build_summary(
    metrics: dict[str, dict[str, Any]],
    dtcs: list[str],
    vin: str | None,
    context: dict[str, Any],
    vehicle_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    engine = metrics.get("7E8", {})
    transmission = metrics.get("7E9", {})
    info = vehicle_info or {}
    summary_vin = vin or info.get("vin")
    return {
        "vehicle_state": context.get("vehicle_state", "unknown"),
        "label": context.get("label", "unspecified"),
        "vin": summary_vin,
        "vehicle_info": info,
        "dtc_count": len(dtcs),
        "dtc": dtcs,
        "engine": engine,
        "transmission": transmission,
    }


def session_index_record(result: dict[str, Any], json_path: Path) -> dict[str, Any]:
    summary = result.get("summary") or build_summary(
        result.get("metrics", {}),
        result.get("dtc_all", []),
        result.get("vehicle", {}).get("vin") or result.get("vin"),
        result.get("context", {}),
        result.get("vehicle_info"),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": result.get("session_id"),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "path": str(json_path),
        "ecu_connected": result.get("ecu_connected"),
        "success": result.get("success"),
        "context": result.get("context"),
        "vehicle_profile": result.get("vehicle_profile"),
        "summary": summary,
    }


def append_index(index_path: Path, record: dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

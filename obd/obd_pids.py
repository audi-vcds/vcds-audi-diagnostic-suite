#!/usr/bin/env python3
"""SAE J1979 Mode 01 / Mode 09 PID definitions and decoders for generic OBD-II scanners."""

from __future__ import annotations

import re
from typing import Any

MODE01_DISCOVERY_COMMANDS = ["0100", "0120", "0140", "0160", "0180", "01A0", "01C0"]
# Только bitmap-команды. VIN/CALID/имя ECU запрашиваются отдельно.
MODE09_DISCOVERY_COMMANDS = ["0900"]
MODE09_INFO_COMMANDS = ["0902", "0904", "0906", "090A", "090D"]

PID_01_NAMES: dict[str, str] = {
    "01": "monitor_status_since_clear",
    "02": "freeze_dtc",
    "03": "fuel_system_status",
    "04": "calculated_engine_load",
    "05": "engine_coolant_temperature",
    "06": "short_term_fuel_trim_bank_1",
    "07": "long_term_fuel_trim_bank_1",
    "08": "short_term_fuel_trim_bank_2",
    "09": "long_term_fuel_trim_bank_2",
    "0A": "fuel_pressure_gauge",
    "0B": "intake_manifold_absolute_pressure",
    "0C": "engine_rpm",
    "0D": "vehicle_speed",
    "0E": "timing_advance",
    "0F": "intake_air_temperature",
    "10": "maf_air_flow_rate",
    "11": "throttle_position",
    "12": "commanded_secondary_air_status",
    "13": "oxygen_sensors_present_2_banks",
    "14": "oxygen_sensor_1_bank_1",
    "15": "oxygen_sensor_2_bank_1",
    "16": "oxygen_sensor_3_bank_2",
    "17": "oxygen_sensor_4_bank_2",
    "18": "oxygen_sensor_5_bank_1",
    "19": "oxygen_sensor_6_bank_1",
    "1A": "oxygen_sensor_7_bank_2",
    "1B": "oxygen_sensor_8_bank_2",
    "1C": "obd_standards",
    "1D": "oxygen_sensors_present_4_banks",
    "1E": "auxiliary_input_status",
    "1F": "runtime_since_engine_start",
    "20": "pids_supported_21_40",
    "21": "distance_with_mil_on",
    "22": "fuel_rail_pressure_relative",
    "23": "fuel_rail_gauge_pressure",
    "24": "oxygen_sensor_1_wideband",
    "25": "oxygen_sensor_2_wideband",
    "26": "oxygen_sensor_3_wideband",
    "27": "oxygen_sensor_4_wideband",
    "28": "oxygen_sensor_5_wideband",
    "29": "oxygen_sensor_6_wideband",
    "2A": "oxygen_sensor_7_wideband",
    "2B": "oxygen_sensor_8_wideband",
    "2C": "commanded_egr",
    "2D": "egr_error",
    "2E": "commanded_evaporative_purge",
    "2F": "fuel_tank_level",
    "30": "warmups_since_codes_cleared",
    "31": "distance_since_codes_cleared",
    "32": "evap_system_vapor_pressure",
    "33": "absolute_barometric_pressure",
    "34": "oxygen_sensor_1_wideband_current",
    "35": "oxygen_sensor_2_wideband_current",
    "36": "oxygen_sensor_3_wideband_current",
    "37": "oxygen_sensor_4_wideband_current",
    "38": "oxygen_sensor_5_wideband_current",
    "39": "oxygen_sensor_6_wideband_current",
    "3A": "oxygen_sensor_7_wideband_current",
    "3B": "oxygen_sensor_8_wideband_current",
    "3C": "catalyst_temperature_bank_1_sensor_1",
    "3D": "catalyst_temperature_bank_2_sensor_1",
    "3E": "catalyst_temperature_bank_1_sensor_2",
    "3F": "catalyst_temperature_bank_2_sensor_2",
    "40": "pids_supported_41_60",
    "41": "monitor_status_this_drive_cycle",
    "42": "control_module_voltage",
    "43": "absolute_load_value",
    "44": "fuel_air_commanded_equivalence_ratio",
    "45": "relative_throttle_position",
    "46": "ambient_air_temperature",
    "47": "absolute_throttle_position_b",
    "48": "absolute_throttle_position_c",
    "49": "accelerator_pedal_position_d",
    "4A": "accelerator_pedal_position_e",
    "4B": "accelerator_pedal_position_f",
    "4C": "commanded_throttle_actuator",
    "4D": "time_run_with_mil_on",
    "4E": "time_since_trouble_codes_cleared",
    "4F": "maximum_values_for_air_fuel_equivalence",
    "50": "maximum_maf_air_flow_rate",
    "51": "fuel_type",
    "52": "ethanol_fuel_percent",
    "53": "absolute_evap_system_vapor_pressure",
    "54": "evap_system_vapor_pressure_alt",
    "55": "short_term_secondary_oxygen_trim_bank_1_3",
    "56": "long_term_secondary_oxygen_trim_bank_1_3",
    "57": "short_term_secondary_oxygen_trim_bank_2_4",
    "58": "long_term_secondary_oxygen_trim_bank_2_4",
    "59": "fuel_rail_absolute_pressure",
    "5A": "relative_accelerator_pedal_position",
    "5B": "hybrid_battery_pack_remaining_life",
    "5C": "engine_oil_temperature",
    "5D": "fuel_injection_timing",
    "5E": "engine_fuel_rate",
    "5F": "emission_requirements",
    "60": "pids_supported_61_80",
    "61": "drivers_demand_engine_percent_torque",
    "62": "actual_engine_percent_torque",
    "63": "engine_reference_torque",
    "64": "engine_percent_torque_data",
    "65": "auxiliary_input_output_supported",
    "66": "mass_air_flow_sensor",
    "67": "engine_coolant_temperature_sensor",
    "68": "intake_air_temperature_sensor",
    "69": "mass_air_flow_sensor_b",
    "6A": "engine_cooling_system_thermostat",
    "6B": "egr_temperature",
    "6C": "commanded_egr_duty_cycle",
    "6D": "fuel_pressure_control_system",
    "6E": "injection_pressure_control_system",
    "6F": "turbocharger_compressor_inlet_pressure",
    "70": "boost_pressure_control",
    "71": "variable_geometry_turbo_control",
    "72": "wastegate_control",
    "73": "exhaust_pressure",
    "74": "turbocharger_rpm",
    "75": "turbocharger_temperature",
    "76": "turbocharger_temperature_2",
    "77": "charge_air_cooler_temperature",
    "78": "exhaust_gas_temperature_bank_1",
    "79": "exhaust_gas_temperature_bank_2",
    "7A": "dpf_differential_pressure",
    "7B": "dpf",
    "7C": "dpf_temperature",
    "7D": "nox_nte_control_area_status",
    "7E": "pm_nte_control_area_status",
    "7F": "engine_run_time",
    "80": "pids_supported_81_a0",
    "81": "engine_run_time_aecd_1",
    "82": "engine_run_time_aecd_2",
    "83": "nox_sensor",
    "84": "manifold_surface_temperature",
    "85": "nox_control_system",
    "86": "particulate_matter_sensor",
    "87": "intake_manifold_absolute_pressure_alt",
    "88": "scr_induce_system",
    "89": "run_time_for_aecd_timers",
    "8A": "diesel_aftertreatment",
    "8B": "nox_sensor_corrected_concentration",
    "8C": "cylinder_fuel_rate",
    "8D": "evap_system_vapor_pressure_alt2",
    "8E": "engine_friction_percent_torque",
    "8F": "pmd_sensor_supported",
    "90": "wwh_obd_vehicle_information",
    "91": "wwh_obd_vehicle_counters",
    "92": "fuel_pressure_control_system",
    "93": "nox_warning_and_inducement_system",
    "94": "exhaust_gas_temperature_sensor",
    "95": "exhaust_gas_temperature_sensor_2",
    "96": "hybrid_ev_vehicle_system_data",
    "97": "diesel_exhaust_fluid_sensor_data",
    "98": "oxygen_sensor_data",
    "99": "engine_fuel_rate_alt",
    "9A": "engine_exhaust_flow_rate",
    "9B": "fuel_system_percentage_use",
    "9C": "obd_system_information",
    "9D": "nox_control_system_data",
    "9E": "particulate_matter_concentration_sensor",
    "9F": "engine_fuel_rate_alt2",
    "A0": "pids_supported_a1_c0",
    "A1": "nox_sensor_corrected_concentration_bank_1_2",
    "A2": "cylinder_fuel_rate_alt",
    "A3": "evap_system_vapor_pressure_alt3",
    "A4": "transmission_actual_gear",
    "A5": "diesel_exhaust_fluid_dosing",
    "A6": "odometer",
    "A7": "nox_concentration_corrected_bank_1_sensor_2",
    "A8": "nox_concentration_corrected_bank_2_sensor_1",
    "A9": "nox_concentration_corrected_bank_2_sensor_2",
    "AA": "pids_supported_ab_c0",
    "AB": "diesel_exhaust_particulate_sensor",
    "AC": "commanded_throttle_actuator_alt",
    "AD": "engine_fuel_rate_alt3",
    "AE": "engine_exhaust_flow_rate_alt",
    "AF": "fuel_system_percentage_use_alt",
    "B0": "pids_supported_b1_d0",
    "B1": "nox_sensor_corrected_concentration_alt",
    "B2": "cylinder_fuel_rate_alt2",
    "B3": "evap_system_vapor_pressure_alt4",
    "B4": "transmission_actual_gear_alt",
    "B5": "diesel_exhaust_fluid_dosing_alt",
    "B6": "odometer_alt",
    "B7": "nox_concentration_corrected_alt",
    "B8": "nox_concentration_corrected_alt2",
    "B9": "nox_concentration_corrected_alt3",
    "BA": "pids_supported_bb_d0",
    "BB": "reserved_bb",
    "BC": "reserved_bc",
    "BD": "reserved_bd",
    "BE": "reserved_be",
    "BF": "reserved_bf",
    "C0": "pids_supported_c1_e0",
    "C1": "reserved_c1",
    "C2": "reserved_c2",
    "C3": "reserved_c3",
    "C4": "reserved_c4",
    "C5": "reserved_c5",
    "C6": "reserved_c6",
    "C7": "reserved_c7",
    "C8": "reserved_c8",
    "C9": "reserved_c9",
    "CA": "reserved_ca",
    "CB": "reserved_cb",
    "CC": "reserved_cc",
    "CD": "reserved_cd",
    "CE": "reserved_ce",
    "CF": "reserved_cf",
    "D0": "pids_supported_d1_f0",
    "D1": "reserved_d1",
    "D2": "reserved_d2",
    "D3": "reserved_d3",
    "D4": "reserved_d4",
    "D5": "reserved_d5",
    "D6": "reserved_d6",
    "D7": "reserved_d7",
    "D8": "reserved_d8",
    "D9": "reserved_d9",
    "DA": "reserved_da",
    "DB": "reserved_db",
    "DC": "reserved_dc",
    "DD": "reserved_dd",
    "DE": "reserved_de",
    "DF": "reserved_df",
    "E0": "pids_supported_e1_ff",
    "E1": "reserved_e1",
    "E2": "reserved_e2",
    "E3": "reserved_e3",
    "E4": "reserved_e4",
    "E5": "reserved_e5",
    "E6": "reserved_e6",
    "E7": "reserved_e7",
    "E8": "reserved_e8",
    "E9": "reserved_e9",
    "EA": "reserved_ea",
    "EB": "reserved_eb",
    "EC": "reserved_ec",
    "ED": "reserved_ed",
    "EE": "reserved_ee",
    "EF": "reserved_ef",
    "F0": "reserved_f0",
    "F1": "reserved_f1",
    "F2": "reserved_f2",
    "F3": "reserved_f3",
    "F4": "reserved_f4",
    "F5": "reserved_f5",
    "F6": "reserved_f6",
    "F7": "reserved_f7",
    "F8": "reserved_f8",
    "F9": "reserved_f9",
    "FA": "reserved_fa",
    "FB": "reserved_fb",
    "FC": "reserved_fc",
    "FD": "reserved_fd",
    "FE": "reserved_fe",
    "FF": "reserved_ff",
}

MODE09_NAMES: dict[str, str] = {
    "00": "mode09_supported_pids",
    "01": "vin_message_count",
    "02": "vehicle_identification_number",
    "03": "calibration_id_message_count",
    "04": "calibration_id",
    "05": "calibration_verification_numbers_count",
    "06": "calibration_verification_numbers",
    "07": "in_use_performance_tracking_count",
    "08": "in_use_performance_tracking",
    "09": "ecu_name_message_count",
    "0A": "ecu_name",
    "0B": "exhaust_regulation_obd_standards",
    "0C": "wobd_vin",
    "0D": "ecu_information",
    "0E": "auxiliary_ecu_identification",
    "0F": "erotan",
}

_FUEL_SYSTEM_STATUS = {
    0: "motor_off",
    1: "open_loop_insufficient_temp",
    2: "closed_loop",
    4: "open_loop_engine_load",
    8: "open_loop_system_failure",
    16: "closed_loop_fault",
}

_SECONDARY_AIR_STATUS = {
    1: "upstream",
    2: "downstream",
    4: "outside_or_off",
    8: "pump_on_diagnostic",
}

_OBD_STANDARDS = {
    1: "OBD-II_CARB",
    2: "OBD_EPA",
    3: "OBD_and_OBD-II",
    4: "OBD-I",
    5: "not_OBD_compliant",
    6: "EOBD",
    7: "EOBD_and_OBD-II",
    8: "EOBD_and_OBD",
    9: "EOBD_OBD_and_OBD-II",
    10: "JOBD",
    11: "JOBD_and_OBD-II",
    12: "JOBD_and_EOBD",
    13: "JOBD_EOBD_and_OBD-II",
    14: "reserved",
    15: "reserved",
    16: "reserved",
    17: "engine_manufacturer_diagnostic",
    18: "engine_manufacturer_diagnostic_plus_OBD-II",
    19: "heavy_duty_OBD",
    20: "heavy_duty_OBD_and_OBD-II",
    21: "WWH-OBD",
    22: "WWH-OBD_and_OBD-II",
    23: "reserved",
    24: "heavy_duty_EOBD",
    25: "heavy_duty_EOBD_and_OBD-II",
    26: "heavy_duty_EOBD_and_WWH-OBD",
    27: "heavy_duty_EOBD_WWH-OBD_and_OBD-II",
    28: "reserved",
    29: "Brazil_OBD",
    30: "Brazil_OBD_and_OBD-II",
    31: "reserved",
    32: "reserved",
    33: "EMD",
    34: "EMD_plus",
    35: "HD_EMD",
    36: "HD_EMD_plus",
    37: "NA_HD_EMD",
    38: "NA_HD_EMD_plus",
}

_FUEL_TYPES = {
    0: "not_available",
    1: "gasoline",
    2: "methanol",
    3: "ethanol",
    4: "diesel",
    5: "lpg",
    6: "cng",
    7: "propane",
    8: "electric",
    9: "bifuel_gasoline",
    10: "bifuel_methanol",
    11: "bifuel_ethanol",
    12: "bifuel_lpg",
    13: "bifuel_cng",
    14: "bifuel_propane",
    15: "bifuel_electric",
    16: "bifuel_mixed",
    17: "hybrid_gasoline",
    18: "hybrid_ethanol",
    19: "hybrid_diesel",
    20: "hybrid_electric",
    21: "hybrid_mixed",
    22: "hybrid_regenerative",
}


def strip_isotp(frame: list[int]) -> list[int]:
    """Strip ISO-TP PCI: single frame 0x0N means N data bytes follow."""
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


def _u16(data: list[int], index: int = 0) -> int:
    if index + 1 >= len(data):
        return 0
    return (data[index] << 8) | data[index + 1]


def _signed_percent(byte_val: int) -> float:
    return round((byte_val - 128) * 100 / 128, 2)


def _percent_255(byte_val: int) -> float:
    return round(byte_val * 100 / 255, 2)


def _temp_minus_40(byte_val: int) -> int:
    return byte_val - 40


def _parse_can_line(line: str) -> tuple[str, list[int]] | None:
    cleaned = line.strip().upper().replace(" ", "")
    if not re.fullmatch(r"[0-9A-F]+", cleaned):
        return None
    for prefix_len in (3, 4):
        if len(cleaned) <= prefix_len:
            continue
        can_id = cleaned[:prefix_len]
        payload_hex = cleaned[prefix_len:]
        if len(payload_hex) % 2:
            continue
        data = [int(payload_hex[i : i + 2], 16) for i in range(0, len(payload_hex), 2)]
        return can_id, data
    return None


def _parse_response_frames(response: str) -> dict[str, list[list[int]]]:
    frames: dict[str, list[list[int]]] = {}
    for line in response.splitlines():
        parsed = _parse_can_line(line)
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
        length = first[0] & 0x0F
        out.extend(first[1 : 1 + length])
    else:
        out.extend(first)
    if total is not None:
        return bytes(out[:total])
    return bytes(out)


def _normalize_mode01_data(data: list[int]) -> list[int]:
    if not data:
        return []
    if data[0] == 0x41 and len(data) >= 3:
        return data[2:]
    if data[0] == 0x41 and len(data) == 2:
        return []
    return data


def parse_supported_pids(base_pid: int, data_bytes: list[int]) -> list[str]:
    """Parse 4-byte bitmap after 41 XX response; base 0 for 0100 -> PIDs 01-20."""
    raw = list(data_bytes)
    if raw and raw[0] == 0x41:
        raw = raw[2:] if len(raw) >= 2 else []
    elif raw and raw[0] == 0x49:
        raw = raw[2:] if len(raw) >= 2 else []
    if len(raw) < 4:
        return []
    bitfield = (raw[0] << 24) | (raw[1] << 16) | (raw[2] << 8) | raw[3]
    supported: list[str] = []
    for bit_index in range(32):
        if bitfield & (1 << (31 - bit_index)):
            pid_num = base_pid + bit_index + 1
            if 1 <= pid_num <= 0xFF:
                supported.append(f"{pid_num:02X}")
    return supported


def parse_supported_pids_from_response(cmd: str, response: str) -> list[str]:
    """Parse supported PID bitmap from ELM response to discovery command (0100, 0900, …)."""
    if not response or not cmd or len(cmd) < 4:
        return []
    mode = cmd[:2].upper()
    disc_pid = cmd[2:4].upper()
    expected_service = 0x41 if mode == "01" else 0x49
    disc_val = int(disc_pid, 16)
    base_pid = 0 if disc_pid == "00" else disc_val

    supported: list[str] = []
    seen: set[str] = set()
    for frames in _parse_response_frames(response).values():
        for frame in frames:
            payload = strip_isotp(frame)
            if len(payload) < 3:
                continue
            if payload[0] != expected_service or payload[1] != disc_val:
                continue
            for pid in parse_supported_pids(base_pid, payload):
                if pid not in seen:
                    seen.add(pid)
                    supported.append(pid)
    return supported


def _decode_monitor_status(name: str, data: list[int]) -> dict[str, Any]:
    """SAE J1979 PID 01/41 monitor status (spark vs compression ignition)."""
    if len(data) < 4:
        return {"raw_hex": "".join(f"{b:02X}" for b in data)}
    a, b, c, d = data[0], data[1], data[2], data[3]
    mil_on = bool(a & 0x80)
    dtc_count = a & 0x7F
    compression = bool(b & 0x08)
    continuous = {
        "misfire": {"available": bool(b & 0x01), "incomplete": bool(b & 0x10)},
        "fuel_system": {"available": bool(b & 0x02), "incomplete": bool(b & 0x20)},
        "comprehensive_component": {"available": bool(b & 0x04), "incomplete": bool(b & 0x40)},
    }
    if compression:
        # Diesel / compression-ignition layout
        tests = {
            **continuous,
            "nmhc_catalyst": {"available": bool(c & 0x01), "incomplete": bool(d & 0x01)},
            "nox_scr": {"available": bool(c & 0x02), "incomplete": bool(d & 0x02)},
            "boost_pressure": {"available": bool(c & 0x08), "incomplete": bool(d & 0x08)},
            "exhaust_gas_sensor": {"available": bool(c & 0x20), "incomplete": bool(d & 0x20)},
            "pm_filter": {"available": bool(c & 0x40), "incomplete": bool(d & 0x40)},
            "egr_vvt": {"available": bool(c & 0x80), "incomplete": bool(d & 0x80)},
        }
    else:
        # Spark-ignition layout
        tests = {
            **continuous,
            "catalyst": {"available": bool(c & 0x01), "incomplete": bool(d & 0x01)},
            "heated_catalyst": {"available": bool(c & 0x02), "incomplete": bool(d & 0x02)},
            "evap_system": {"available": bool(c & 0x04), "incomplete": bool(d & 0x04)},
            "secondary_air": {"available": bool(c & 0x08), "incomplete": bool(d & 0x08)},
            "ac_refrigerant": {"available": bool(c & 0x10), "incomplete": bool(d & 0x10)},
            "oxygen_sensor": {"available": bool(c & 0x20), "incomplete": bool(d & 0x20)},
            "oxygen_sensor_heater": {"available": bool(c & 0x40), "incomplete": bool(d & 0x40)},
            "egr_vvt": {"available": bool(c & 0x80), "incomplete": bool(d & 0x80)},
        }
    return {
        name: {
            "mil_on": mil_on,
            "dtc_count": dtc_count,
            "ignition": "compression" if compression else "spark",
            "tests": tests,
        }
    }


def _decode_oxygen_sensor_legacy(data: list[int], name: str) -> dict[str, Any]:
    if len(data) < 2:
        return {"raw_hex": "".join(f"{b:02X}" for b in data)}
    voltage = round(data[0] / 200, 3)
    trim = _signed_percent(data[1])
    return {name: {"voltage_v": voltage, "short_term_fuel_trim_pct": trim}}


def _decode_oxygen_sensor_wideband(data: list[int], name: str) -> dict[str, Any]:
    if len(data) < 4:
        return {"raw_hex": "".join(f"{b:02X}" for b in data)}
    ratio = round(_u16(data, 0) * 2 / 65536, 4)
    voltage = round(data[2] / 200, 3)
    trim = _signed_percent(data[3])
    return {name: {"equivalence_ratio": ratio, "voltage_v": voltage, "short_term_fuel_trim_pct": trim}}


def _decode_oxygen_sensor_wideband_current(data: list[int], name: str) -> dict[str, Any]:
    if len(data) < 4:
        return {"raw_hex": "".join(f"{b:02X}" for b in data)}
    ratio = round(_u16(data, 0) * 2 / 65536, 4)
    current_ma = round((_u16(data, 2) - 32768) / 256, 2)
    return {name: {"equivalence_ratio": ratio, "current_ma": current_ma}}


def _decode_catalyst_temp(data: list[int], name: str) -> dict[str, Any]:
    if len(data) < 2:
        return {"raw_hex": "".join(f"{b:02X}" for b in data)}
    return {name: round(_u16(data) / 10 - 40, 1)}


def decode_mode01_pid(pid: str, data: list[int]) -> dict[str, Any]:
    """Decode Mode 01 PID payload bytes (after 41 XX) using SAE J1979 formulas."""
    pid = pid.upper()
    payload = _normalize_mode01_data(data)
    name = PID_01_NAMES.get(pid, f"pid_{pid.lower()}")

    if pid in {"00", "20", "40", "60", "80", "A0", "C0", "E0"}:
        base = 0 if pid == "00" else int(pid, 16)
        supported = parse_supported_pids(base, [0x41, int(pid, 16)] + payload[:4])
        return {name: supported}

    if pid == "01":
        return _decode_monitor_status(name, payload)
    if pid == "41":
        return _decode_monitor_status(name, payload)

    if pid == "02" and len(payload) >= 2:
        b1, b2 = payload[0], payload[1]
        prefix = "PCBU"[(b1 >> 6) & 3]
        return {name: f"{prefix}{((b1 & 0x3F) << 8) | b2:04X}"}

    if pid == "03" and len(payload) >= 2:
        return {
            name: {
                "system_1": _FUEL_SYSTEM_STATUS.get(payload[0], f"unknown_{payload[0]}"),
                "system_2": _FUEL_SYSTEM_STATUS.get(payload[1], f"unknown_{payload[1]}"),
            }
        }

    if pid == "04" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "05" and payload:
        return {name: _temp_minus_40(payload[0])}
    if pid in {"06", "08", "55", "57"} and payload:
        return {name: _signed_percent(payload[0])}
    if pid in {"07", "09", "56", "58"} and payload:
        return {name: _signed_percent(payload[0])}
    if pid == "0A" and payload:
        return {name: payload[0] * 3}
    if pid == "0B" and payload:
        return {name: payload[0]}
    if pid == "0C" and len(payload) >= 2:
        return {name: round(_u16(payload) / 4, 1)}
    if pid == "0D" and payload:
        return {name: payload[0]}
    if pid == "0E" and payload:
        return {name: round(payload[0] / 2 - 64, 1)}
    if pid == "0F" and payload:
        return {name: _temp_minus_40(payload[0])}
    if pid == "10" and len(payload) >= 2:
        return {name: round(_u16(payload) / 100, 2)}
    if pid == "11" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "12" and payload:
        return {name: _SECONDARY_AIR_STATUS.get(payload[0], f"unknown_{payload[0]}")}
    if pid == "13" and payload:
        bits = payload[0]
        sensors = [idx + 1 for idx in range(8) if bits & (1 << idx)]
        return {name: sensors}
    if pid in {"14", "15", "16", "17", "18", "19", "1A", "1B"}:
        return _decode_oxygen_sensor_legacy(payload, name)
    if pid == "1C" and payload:
        return {name: _OBD_STANDARDS.get(payload[0], f"unknown_{payload[0]}")}
    if pid == "1D" and payload:
        bits = payload[0]
        sensors = [idx + 1 for idx in range(8) if bits & (1 << idx)]
        return {name: sensors}
    if pid == "1E" and payload:
        return {name: "pto_active" if payload[0] & 0x01 else "pto_inactive"}
    if pid == "1F" and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "21" and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "22" and len(payload) >= 2:
        return {name: round(_u16(payload) * 0.079, 2)}
    if pid == "23" and len(payload) >= 2:
        return {name: _u16(payload) * 10}
    if pid in {"24", "25", "26", "27", "28", "29", "2A", "2B"}:
        return _decode_oxygen_sensor_wideband(payload, name)
    if pid == "2C" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "2D" and payload:
        return {name: round((payload[0] - 128) * 100 / 128, 2)}
    if pid == "2E" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "2F" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "30" and payload:
        return {name: payload[0]}
    if pid == "31" and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "32" and len(payload) >= 2:
        return {name: round((_u16(payload) - 32768) / 4, 2)}
    if pid == "33" and payload:
        return {name: payload[0]}
    if pid in {"34", "35", "36", "37", "38", "39", "3A", "3B"}:
        return _decode_oxygen_sensor_wideband_current(payload, name)
    if pid in {"3C", "3D", "3E", "3F"}:
        return _decode_catalyst_temp(payload, name)
    if pid == "42" and len(payload) >= 2:
        return {name: round(_u16(payload) / 1000, 3)}
    if pid == "43" and len(payload) >= 2:
        return {name: round(_u16(payload) * 100 / 255, 2)}
    if pid == "44" and len(payload) >= 2:
        return {name: round(_u16(payload) * 2 / 65536, 4)}
    if pid in {"45", "47", "48", "49", "4A", "4B", "4C", "5A"} and payload:
        return {name: _percent_255(payload[0])}
    if pid == "46" and payload:
        return {name: _temp_minus_40(payload[0])}
    if pid in {"4D", "4E", "1F"} and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "4F" and len(payload) >= 4:
        return {
            name: {
                "max_equivalence_ratio": payload[0],
                "max_oxygen_sensor_voltage_v": payload[1],
                "max_oxygen_sensor_current_ma": payload[2],
                "max_intake_manifold_pressure_kpa": payload[3] * 10,
            }
        }
    if pid == "50" and len(payload) >= 2:
        return {name: {"max_maf_gps": _u16(payload) * 10}}
    if pid == "51" and payload:
        return {name: _FUEL_TYPES.get(payload[0], f"unknown_{payload[0]}")}
    if pid == "52" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "53" and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "54" and len(payload) >= 2:
        return {name: round(_u16(payload) / 4, 2)}
    if pid == "59" and len(payload) >= 2:
        return {name: _u16(payload) * 10}
    if pid == "5B" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "5C" and payload:
        return {name: _temp_minus_40(payload[0])}
    if pid == "5D" and len(payload) >= 2:
        return {name: round((_u16(payload) / 128) - 210, 2)}
    if pid == "5E" and len(payload) >= 2:
        return {name: round(_u16(payload) / 20, 2)}
    if pid == "5F" and payload:
        return {name: payload[0]}
    if pid in {"61", "62"} and payload:
        return {name: payload[0] - 125}
    if pid == "63" and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "67" and len(payload) >= 2:
        # Byte0 = sensor support bitmask; following bytes = temps (°C = x - 40)
        support = payload[0]
        sensors: dict[str, int] = {}
        idx = 1
        for bit in range(8):
            if support & (1 << bit) and idx < len(payload):
                sensors[f"sensor_{bit + 1}_c"] = payload[idx] - 40
                idx += 1
        return {name: sensors} if sensors else {name: _temp_minus_40(payload[1] if len(payload) > 1 else payload[0])}
    if pid == "68" and payload:
        return {name: _temp_minus_40(payload[0])}
    if pid == "6B" and len(payload) >= 2:
        return {name: round(_u16(payload) / 10 - 40, 1)}
    if pid == "6C" and payload:
        return {name: _percent_255(payload[0])}
    if pid == "6F" and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "74" and len(payload) >= 2:
        return {name: round(_u16(payload) / 4, 1)}
    if pid in {"75", "76", "77", "78", "79", "7C"} and len(payload) >= 2:
        return {name: round(_u16(payload) / 10 - 40, 1)}
    if pid == "7A" and len(payload) >= 2:
        return {name: round(_u16(payload) / 10, 2)}
    if pid == "7F" and len(payload) >= 2:
        return {name: _u16(payload)}
    if pid == "83" and len(payload) >= 2:
        return {name: round(_u16(payload) / 10 - 40, 1)}
    if pid == "84" and len(payload) >= 2:
        return {name: round(_u16(payload) / 10 - 40, 1)}
    if pid == "8C" and len(payload) >= 2:
        return {name: round(_u16(payload) / 20, 2)}
    if pid == "A4" and payload:
        return {name: payload[0]}
    if pid == "A6" and len(payload) >= 4:
        # SAE J1979: 0.1 km/bit
        raw_km = (payload[0] << 24) | (payload[1] << 16) | (payload[2] << 8) | payload[3]
        return {name: round(raw_km / 10, 1)}

    if payload:
        return {"raw_hex": "".join(f"{b:02X}" for b in payload)}
    return {"raw_hex": ""}


def _extract_mode09_payload(assembled: bytes, info_type: int) -> bytes | None:
    marker = bytes([0x49, info_type])
    pos = assembled.find(marker)
    if pos < 0:
        return None
    body = assembled[pos + 2 :]
    if info_type == 0x02:
        if body and body[0] == 0x01:
            return body[1:]
        return body
    if info_type in {0x04, 0x0A, 0x0D}:
        if body and body[0] <= 0x0F:
            return body[1:]
        return body
    if info_type == 0x00:
        return body[:4] if len(body) >= 4 else body
    if info_type == 0x06:
        return body
    return body


def _ascii_from_bytes(raw: bytes) -> str:
    return "".join(chr(b) for b in raw if 32 <= b <= 126).strip()


def decode_mode09(pid: str, response_text: str) -> dict[str, Any]:
    """Decode Mode 09 response text (ELM327 / CAN hex lines), with ISO-TP reassembly.

    `pid` is the 2-hex info type (`02`, `04`, …) or full command (`0902`).
    """
    pid = pid.upper().replace(" ", "")
    if len(pid) == 4 and pid.startswith("09"):
        pid = pid[2:]
    if len(pid) != 2:
        return {"raw_hex": ""}
    name = MODE09_NAMES.get(pid, f"mode09_{pid.lower()}")
    info_type = int(pid, 16)

    frames_by_ecu = _parse_response_frames(response_text)
    assembled_chunks: list[bytes] = []
    for ecu_frames in frames_by_ecu.values():
        assembled_chunks.append(reassemble_isotp(ecu_frames))

    if not assembled_chunks:
        hex_blob = re.sub(r"[^0-9A-Fa-f]", "", response_text).upper()
        if hex_blob:
            try:
                assembled_chunks.append(bytes.fromhex(hex_blob))
            except ValueError:
                pass

    for assembled in assembled_chunks:
        payload = _extract_mode09_payload(assembled, info_type)
        if payload is None:
            continue

        if info_type == 0x00:
            supported = parse_supported_pids(0, [0x49, 0x00, *list(payload[:4])])
            return {name: supported}

        if info_type == 0x02:
            vin_raw = _ascii_from_bytes(payload[:17])
            match = re.search(r"[A-HJ-NPR-Z0-9]{17}", vin_raw)
            if match:
                return {name: match.group(0)}
            if len(vin_raw) >= 11:
                return {name: vin_raw}

        if info_type == 0x04:
            text = _ascii_from_bytes(payload)
            if text:
                return {name: text}

        if info_type == 0x06 and payload:
            cvns = []
            for i in range(0, len(payload), 4):
                chunk = payload[i : i + 4]
                if len(chunk) == 4:
                    cvns.append(chunk.hex().upper())
            return {name: cvns if cvns else payload.hex().upper()}

        if info_type == 0x0A:
            text = _ascii_from_bytes(payload)
            if text:
                return {name: text}

        if info_type == 0x0D:
            text = _ascii_from_bytes(payload)
            if text:
                return {name: text}

        if info_type in {0x01, 0x03, 0x05, 0x07, 0x09} and payload:
            return {name: payload[0]}

        if info_type == 0x0B and payload:
            return {name: payload[0]}

        if payload:
            return {name: payload.hex().upper()}

    hex_blob = re.sub(r"[^0-9A-Fa-f]", "", response_text).upper()
    if info_type == 0x02:
        marker = hex_blob.find("4902")
        if marker >= 0:
            try:
                raw = bytes.fromhex(hex_blob[marker + 4 : marker + 4 + 36])
                if raw and raw[0] == 0x01:
                    raw = raw[1:]
                vin = _ascii_from_bytes(raw[:17])
                match = re.search(r"[A-HJ-NPR-Z0-9]{17}", vin)
                if match:
                    return {name: match.group(0)}
            except ValueError:
                pass

    return {"raw_hex": hex_blob[:64]} if hex_blob else {"raw_hex": ""}

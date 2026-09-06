#!/usr/bin/env python3
"""Vehicle profiles for OBD scan sessions.

EXEED VX keeps the original dump layout (obd-dumps/ root) so existing
sessions and workflows stay unchanged. Other cars get a dedicated subdir.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Same LIVE set as historical LIVE_PIDS_DEFAULT in obd_scan.py.
# Unsupported PIDs are filtered after ECU discovery — safe for any car.
LIVE_PIDS_SHARED = (
    "0C",  # rpm
    "0D",  # speed
    "05",  # coolant
    "04",  # load
    "0B",  # MAP
    "11",  # throttle
    "0E",  # timing
    "06",  # STFT
    "07",  # LTFT
    "42",  # module voltage
    "23",  # fuel rail
    "2F",  # fuel level
    "43",  # absolute load
    "44",  # commanded lambda
    "0F",  # IAT
    "46",  # ambient
    "A6",  # odometer (slow; skipped if ECU has no support)
)


@dataclass(frozen=True)
class VehicleProfile:
    id: str
    display_name: str
    make: str
    model: str
    year: int | None
    # Always prefer "auto" so ATSP6 → ATSP7 → ATSP0 fallback stays intact.
    default_protocol: str
    live_pids: tuple[str, ...]
    # Empty string = dump into obd-dumps/ root (EXEED legacy layout).
    dumps_subdir: str
    preflight_notes: tuple[str, ...]
    expected_ecus: tuple[str, ...]

    def to_context(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["live_pids"] = list(self.live_pids)
        payload["preflight_notes"] = list(self.preflight_notes)
        payload["expected_ecus"] = list(self.expected_ecus)
        return payload

    def dumps_dir(self, dumps_root: Any) -> Any:
        """Return dump directory for this profile (root for EXEED legacy layout)."""
        if self.dumps_subdir:
            return dumps_root / self.dumps_subdir
        return dumps_root


VEHICLE_PROFILES: dict[str, VehicleProfile] = {
    "exeed_vx": VehicleProfile(
        id="exeed_vx",
        display_name="EXEED VX",
        make="EXEED",
        model="VX",
        year=None,
        default_protocol="auto",
        live_pids=LIVE_PIDS_SHARED,
        dumps_subdir="",  # legacy: obd-dumps/obd-*.json
        preflight_notes=(
            "На VX блокиратор OBD на сигнализации может давать NO DATA — "
            "попробуйте сервисный режим сигнализации.",
            "Ожидаются ECU двигателя (7E8) и КПП (7E9).",
        ),
        expected_ecus=("7E8", "7E9"),
    ),
    "honda_civic_2013": VehicleProfile(
        id="honda_civic_2013",
        display_name="Honda Civic 2013",
        make="Honda",
        model="Civic",
        year=2013,
        default_protocol="auto",
        live_pids=LIVE_PIDS_SHARED,
        dumps_subdir="honda-civic-2013",
        preflight_notes=(
            "Honda Civic 2013: тот же OBD-II CAN fallback (ATSP6→7→0), что и EXEED.",
            "Обычно отвечает ECU двигателя (7E8); КПП на 7E9 может отсутствовать.",
        ),
        expected_ecus=("7E8",),
    ),
}

DEFAULT_VEHICLE_ID = "exeed_vx"


def vehicle_profile_ids() -> tuple[str, ...]:
    return tuple(VEHICLE_PROFILES)


def get_vehicle_profile(vehicle_id: str | None) -> VehicleProfile:
    key = (vehicle_id or DEFAULT_VEHICLE_ID).strip().lower()
    profile = VEHICLE_PROFILES.get(key)
    if profile is None:
        known = ", ".join(sorted(VEHICLE_PROFILES))
        raise ValueError(f"Неизвестный профиль авто: {vehicle_id!r}. Доступны: {known}")
    return profile

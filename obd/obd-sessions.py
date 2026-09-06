#!/usr/bin/env python3
"""Index, list and compare OBD scan sessions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from obd_decode import enrich_scan_result, session_index_record

DEFAULT_DUMPS = Path(__file__).resolve().parent.parent / "obd-dumps"
METRIC_KEYS = (
    "rpm",
    "speed_kmh",
    "coolant_c",
    "intake_air_c",
    "throttle_pct",
    "fuel_level_pct",
    "control_module_voltage_v",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_session_files(dumps_root: Path, *, vehicle_id: str | None = None) -> list[Path]:
    patterns = [
        dumps_root.glob("obd-*.json"),
        dumps_root.glob("*/sessions/obd-*.json"),
        dumps_root.glob("*/obd-*.json"),
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(pattern)
    if vehicle_id:
        try:
            from obd_vehicles import get_vehicle_profile

            subdir = get_vehicle_profile(vehicle_id).dumps_subdir
            scoped = dumps_root / subdir
            if scoped.is_dir():
                files = [p for p in files if scoped in p.parents or p.parent == scoped]
        except ValueError:
            pass
    seen: set[str] = set()
    result: list[Path] = []
    for path in sorted(files, key=lambda p: p.name):
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def load_index(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_index(index_path: Path, rows: list[dict[str, Any]]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def backfill(dumps_root: Path, index_path: Path, *, rebuild: bool = False, vehicle_id: str | None = None) -> int:
    rows: list[dict[str, Any]] = [] if rebuild else load_index(index_path)
    existing_ids = {row.get("session_id") for row in rows}
    added = 0
    for path in find_session_files(dumps_root, vehicle_id=vehicle_id):
        raw = load_json(path)
        session_id = path.stem.removeprefix("obd-")
        raw["session_id"] = session_id
        enriched = enrich_scan_result(raw)
        if session_id in existing_ids and not rebuild:
            continue
        record = session_index_record(enriched, path)
        if session_id in existing_ids and rebuild:
            rows = [row for row in rows if row.get("session_id") != session_id]
        rows.append(record)
        existing_ids.add(session_id)
        added += 1
        path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    rows.sort(key=lambda row: row.get("session_id") or "")
    write_index(index_path, rows)
    return added


def list_sessions(dumps_root: Path, index_path: Path, *, vehicle_id: str | None = None) -> None:
    rows = load_index(index_path)
    if vehicle_id:
        rows = [
            row
            for row in rows
            if (row.get("context") or {}).get("vehicle_id") == vehicle_id
            or (row.get("vehicle_profile") or {}).get("id") == vehicle_id
        ]
    if not rows:
        print("index.jsonl пуст. Запустите: python3 scripts/obd-sessions.py backfill")
        return
    print(
        f"{'session_id':<18} {'vehicle':<18} {'state':<16} {'label':<20} {'ecu':<5} {'dtc':<4} rpm  coolant"
    )
    print("-" * 108)
    for row in rows:
        summary = row.get("summary") or {}
        engine = summary.get("engine") or {}
        ctx = row.get("context") or {}
        vehicle = (
            ctx.get("vehicle_display")
            or (row.get("vehicle_profile") or {}).get("display_name")
            or ctx.get("vehicle_id")
            or "—"
        )
        print(
            f"{row.get('session_id','?'):<18} "
            f"{str(vehicle)[:18]:<18} "
            f"{ctx.get('vehicle_state', summary.get('vehicle_state', '?')):<16} "
            f"{ctx.get('label', summary.get('label', '?')):<20} "
            f"{str(row.get('ecu_connected')):<5} "
            f"{summary.get('dtc_count', 0):<4} "
            f"{engine.get('rpm', '—'):>4} "
            f"{engine.get('coolant_c', '—'):>7}"
        )


def resolve_session(dumps_root: Path, token: str, *, vehicle_id: str | None = None) -> Path | None:
    candidates = find_session_files(dumps_root, vehicle_id=vehicle_id)
    direct = dumps_root / f"obd-{token}.json"
    if direct.exists():
        return direct
    direct2 = dumps_root / f"{token}.json"
    if direct2.exists():
        return direct2
    if vehicle_id:
        try:
            from obd_vehicles import get_vehicle_profile

            scoped = dumps_root / get_vehicle_profile(vehicle_id).dumps_subdir / f"obd-{token}.json"
            if scoped.exists():
                return scoped
        except ValueError:
            pass
    matches = [p for p in candidates if token in p.name]
    if len(matches) == 1:
        return matches[0]
    return None


def compare_sessions(left: dict[str, Any], right: dict[str, Any]) -> None:
    left_id = left.get("session_id", "?")
    right_id = right.get("session_id", "?")
    print(f"Сравнение: {left_id}  →  {right_id}")
    print("")

    left_ctx = left.get("context") or {}
    right_ctx = right.get("context") or {}
    left_vehicle = left_ctx.get("vehicle_display") or (left.get("vehicle_profile") or {}).get("display_name")
    right_vehicle = right_ctx.get("vehicle_display") or (right.get("vehicle_profile") or {}).get("display_name")
    print(f"vehicle: {left_vehicle or '—'} → {right_vehicle or '—'}")
    print(f"state:  {left_ctx.get('vehicle_state')} → {right_ctx.get('vehicle_state')}")
    print(f"label:  {left_ctx.get('label')} → {right_ctx.get('label')}")
    print("")

    left_metrics = left.get("metrics") or {}
    right_metrics = right.get("metrics") or {}
    ecu_ids = sorted(set(left_metrics) | set(right_metrics))
    for ecu_id in ecu_ids:
        print(f"[{ecu_id}]")
        left_vals = left_metrics.get(ecu_id, {})
        right_vals = right_metrics.get(ecu_id, {})
        keys = sorted(set(left_vals) | set(right_vals))
        for key in keys:
            if key not in METRIC_KEYS:
                continue
            lv = left_vals.get(key)
            rv = right_vals.get(key)
            if lv == rv:
                print(f"  {key}: {lv}")
            else:
                delta = ""
                if isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
                    delta = f" ({rv - lv:+.1f})"
                print(f"  {key}: {lv} → {rv}{delta}")
        print("")

    left_dtc = set(left.get("dtc_all") or [])
    right_dtc = set(right.get("dtc_all") or [])
    if left_dtc != right_dtc:
        print("DTC:")
        print(f"  только в {left_id}: {', '.join(sorted(left_dtc - right_dtc)) or '—'}")
        print(f"  только в {right_id}: {', '.join(sorted(right_dtc - left_dtc)) or '—'}")
    else:
        print(f"DTC: без изменений ({len(left_dtc)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="OBD sessions index / compare")
    parser.add_argument("--dumps", type=Path, default=DEFAULT_DUMPS)
    parser.add_argument(
        "--vehicle",
        default="",
        help="Фильтр по профилю авто: exeed_vx, honda_civic_2013 (пусто = все)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("backfill", help="Пересобрать index.jsonl и обогатить старые JSON")
    rebuild = sub.add_parser("rebuild", help="Полностью пересобрать index.jsonl")
    sub.add_parser("list", help="Список сессий из index.jsonl")

    compare = sub.add_parser("compare", help="Сравнить две сессии")
    compare.add_argument("left", help="session_id или фрагмент имени файла")
    compare.add_argument("right", help="session_id или фрагмент имени файла")

    args = parser.parse_args()
    dumps_root = args.dumps.expanduser().resolve()
    index_path = dumps_root / "index.jsonl"
    vehicle_id = args.vehicle.strip() or None

    if args.cmd == "backfill":
        added = backfill(dumps_root, index_path, vehicle_id=vehicle_id)
        print(f"index.jsonl: +{added} сессий, всего {len(load_index(index_path))}")
        return 0

    if args.cmd == "rebuild":
        added = backfill(dumps_root, index_path, rebuild=True, vehicle_id=vehicle_id)
        print(f"index.jsonl: пересобран, {added} сессий")
        return 0

    if args.cmd == "list":
        list_sessions(dumps_root, index_path, vehicle_id=vehicle_id)
        return 0

    if args.cmd == "compare":
        left_path = resolve_session(dumps_root, args.left, vehicle_id=vehicle_id)
        right_path = resolve_session(dumps_root, args.right, vehicle_id=vehicle_id)
        if left_path is None or right_path is None:
            print("Не найдена сессия:", args.left if left_path is None else args.right)
            return 2
        compare_sessions(enrich_scan_result(load_json(left_path)), enrich_scan_result(load_json(right_path)))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())

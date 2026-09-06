#!/usr/bin/env bash
# Офлайн-съём OBD через Wi-Fi V-LINK. Интернет не нужен.
#
# Порядок:
#   1. Зажигание ON (двигатель можно не заводить).
#   2. Отключить VPN/прокси (Surge, Clash и т.п.).
#   3. Mac → Wi-Fi → V-LINK.
#   4. Запустить этот скрипт в Terminal.
#   5. Дождаться "ГОТОВО", вернуться в интернет, прислать .json/.txt агенту.
#
# macOS: системный bash 3.2 + set -u — нельзя раскрывать пустой массив "${arr[@]}".

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/obd-dumps"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$OUT_DIR"

# Лог появляется после выбора авто (путь зависит от профиля).
LOG_FILE=""

log_shell() {
  local line
  line="$(printf '%s [shell] %s' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*")"
  if [[ -n "${LOG_FILE}" ]]; then
    printf '%s\n' "$line" | tee -a "$LOG_FILE"
  else
    printf '%s\n' "$line"
  fi
}

echo "=== OBD scan ==="
echo "Папка результатов: $OUT_DIR"
echo ""
echo "Перед стартом:"
echo "  • Зажигание ON"
echo "  • VPN/прокси выключены"
echo "  • Mac подключён к Wi-Fi V-LINK"
echo ""
read -r -p "Подключились к V-LINK? Enter для старта, Ctrl+C для отмены..."

echo ""
echo "Автомобиль:"
echo "  1) EXEED VX (по умолчанию) — дампы как раньше в obd-dumps/"
echo "  2) Honda Civic 2013 — дампы в obd-dumps/honda-civic-2013/"
read -r -p "  номер [1]: " VEHICLE_CHOICE
case "${VEHICLE_CHOICE:-1}" in
  2)
    VEHICLE_ID="honda_civic_2013"
    OUT_BASE="$OUT_DIR/honda-civic-2013/obd-$STAMP"
    mkdir -p "$OUT_DIR/honda-civic-2013"
    ;;
  *)
    VEHICLE_ID="exeed_vx"
    # EXEED: тот же путь, что до выбора авто — obd-dumps/obd-YYYYMMDD-HHMMSS
    OUT_BASE="$OUT_DIR/obd-$STAMP"
    ;;
esac

LOG_FILE="$OUT_BASE.log"
# Создаём пустой лог заранее (каталог уже есть)
: >"$LOG_FILE" || true

log_shell "=== OBD scan wrapper ==="
log_shell "output base: $OUT_BASE"
log_shell "vehicle=$VEHICLE_ID"
log_shell "user confirmed V-LINK, starting python"

echo "Лог: $LOG_FILE"
echo "Авто: $VEHICLE_ID"
echo ""
echo "Режим съёма:"
echo "  1) полный скан (один снимок)"
echo "  2) LIVE — непрерывный опрос в поездке до Ctrl+C"
read -r -p "  номер [1]: " MODE_CHOICE
MODE_CHOICE="${MODE_CHOICE:-1}"
LIVE_INTERVAL="1"
if [[ "$MODE_CHOICE" == "2" ]]; then
  SESSION_LABEL="${SESSION_LABEL:-live}"
  SESSION_STATE="${SESSION_STATE:-engine_running}"
  echo ""
  read -r -p "  интервал сек [1]: " LIVE_INTERVAL
  LIVE_INTERVAL="${LIVE_INTERVAL:-1}"
  echo "  Остановка LIVE: Ctrl+C"
fi

echo ""
echo "Метка сессии — произвольное имя для сравнения (Enter = baseline):"
read -r -p "  метка: " SESSION_LABEL
SESSION_LABEL="${SESSION_LABEL:-baseline}"

echo ""
echo "Состояние авто:"
echo "  1) зажигание вкл (двигатель выкл)"
echo "  2) прогретый, холостой ход"
echo "  3) двигатель работает / в движении"
echo "  4) холодный запуск"
echo "  5) прогретый на месте"
echo "  6) сразу после поездки"
read -r -p "  номер [1]: " STATE_CHOICE
case "${STATE_CHOICE:-1}" in
  2) SESSION_STATE="engine_idle" ;;
  3) SESSION_STATE="engine_running" ;;
  4) SESSION_STATE="cold_start" ;;
  5) SESSION_STATE="warm_idle" ;;
  6) SESSION_STATE="after_drive" ;;
  *) SESSION_STATE="ignition_on" ;;
esac

# LIVE defaults override if user picked live first then left label/state at defaults above
if [[ "$MODE_CHOICE" == "2" ]]; then
  if [[ "$SESSION_LABEL" == "baseline" ]]; then
    SESSION_LABEL="live"
  fi
  if [[ "$SESSION_STATE" == "ignition_on" && "${STATE_CHOICE:-1}" == "1" ]]; then
    SESSION_STATE="engine_running"
  fi
fi

read -r -p "  заметка (необязательно): " SESSION_NOTE

log_shell "session label=$SESSION_LABEL state=$SESSION_STATE note=$SESSION_NOTE mode=$MODE_CHOICE vehicle=$VEHICLE_ID"

# Собираем аргументы без пустых массивов (bash 3.2 + set -u).
# --protocol auto: тот же fallback ATSP6→7→0, что работал на EXEED.
set +u
PY_ARGS=(
  --output "$OUT_BASE"
  --vehicle "$VEHICLE_ID"
  --obd-timeout 12
  --obd-fast-timeout 6
  --obd-retry-timeout 20
  --protocol auto
  --label "$SESSION_LABEL"
  --state "$SESSION_STATE"
  --note "$SESSION_NOTE"
)
if [[ "$MODE_CHOICE" == "2" ]]; then
  PY_ARGS+=(--live --interval "$LIVE_INTERVAL")
fi
python3 "$ROOT/scripts/obd_scan.py" "${PY_ARGS[@]}"
EXIT_CODE=$?
set -u

log_shell "python exit code: $EXIT_CODE"

echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
  echo "Файлы:"
  ls -lh "$OUT_BASE".json "$OUT_BASE".txt "$OUT_BASE".log "$OUT_BASE".done "$OUT_BASE".live.jsonl 2>/dev/null || true
  echo ""
  echo "Вернитесь в интернет и пришлите в чат:"
  echo "  $OUT_BASE.json"
  if [[ -f "$OUT_BASE.live.jsonl" ]]; then
    echo "  $OUT_BASE.live.jsonl"
  fi
  echo "  или $OUT_BASE.log"
else
  echo "Скан завершился с ошибкой (код $EXIT_CODE). Лог:"
  echo "  $OUT_BASE.log"
fi

log_shell "wrapper finished"

exit "$EXIT_CODE"

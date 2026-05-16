import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "user_schedule_config.json")
DEFAULT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Rome")
logger = logging.getLogger(__name__)

DAY_NAMES = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
PY_WEEKDAY_TO_IT = {
    0: "Lun",
    1: "Mar",
    2: "Mer",
    3: "Gio",
    4: "Ven",
    5: "Sab",
    6: "Dom",
}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(DEFAULT_TIMEZONE)
    except Exception:
        logger.warning("[SCHEDULE] Timezone non valida %s, uso Europe/Rome", DEFAULT_TIMEZONE)
        return ZoneInfo("Europe/Rome")


def _now() -> datetime:
    return datetime.now(_tz())


def _as_local(dt: datetime | None = None) -> datetime:
    if dt is None:
        return _now()
    zone = _tz()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def _parse_hhmm(value: str | None, fallback: str) -> time:
    raw = str(value or fallback).strip()
    try:
        h, m = raw.split(":", 1)
        h = int(h)
        m = int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(raw)
        return time(h, m)
    except Exception:
        logger.warning("[SCHEDULE] Orario non valido '%s', uso fallback %s", raw, fallback)
        h, m = fallback.split(":", 1)
        return time(int(h), int(m))


def load_schedule_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("[SCHEDULE] Errore lettura user_schedule_config.json, ignoro configurazione")
        return {}


def save_schedule_config(data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="user_schedule_", suffix=".tmp", dir=os.path.dirname(CONFIG_PATH))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_PATH)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def get_user_schedule(user_id: int):
    data = load_schedule_config()
    raw = data.get(str(user_id), {})
    if not isinstance(raw, dict):
        raw = {}
    days = raw.get("days", [])
    if not isinstance(days, list):
        days = []
    time_range = raw.get("time_range", {})
    if not isinstance(time_range, dict):
        time_range = {}

    # normalizza orari incompleti: se esiste solo start/end, completa l'altro.
    cleaned_time_range = {}
    start = str(time_range.get("start", "")).strip()
    end = str(time_range.get("end", "")).strip()
    if start or end:
        cleaned_time_range["start"] = start or "00:00"
        cleaned_time_range["end"] = end or "23:59"

    return {
        "days": [d for d in days if d in DAY_NAMES],
        "time_range": cleaned_time_range,
    }


def set_user_schedule(user_id: int, schedule: dict):
    data = load_schedule_config()
    data[str(user_id)] = schedule
    save_schedule_config(data)


def _day_allowed(dt: datetime, allowed_days: list[str]) -> bool:
    if not allowed_days:
        return True
    return PY_WEEKDAY_TO_IT[dt.weekday()] in allowed_days


def _time_allowed(dt: datetime, start_t: time, end_t: time) -> bool:
    if start_t == end_t:
        return True
    now_t = dt.time()
    if start_t < end_t:
        return start_t <= now_t <= end_t
    # fascia notturna: es. 22:00 -> 06:00
    return now_t >= start_t or now_t <= end_t


def is_datetime_within_active_schedule(user_id: int, dt: datetime | None = None) -> bool:
    """
    True se la data/ora indicata è dentro la finestra di pubblicazione dell'utente.
    Gestisce correttamente:
    - nessuna configurazione = sempre attivo;
    - soli giorni = tutto il giorno;
    - soli orari = tutti i giorni;
    - fasce notturne 22:00 -> 06:00.
    """
    schedule = get_user_schedule(user_id)
    allowed_days = schedule.get("days", [])
    time_range = schedule.get("time_range", {})

    if not allowed_days and not time_range:
        return True

    local_dt = _as_local(dt)

    if not time_range:
        return _day_allowed(local_dt, allowed_days)

    start_t = _parse_hhmm(time_range.get("start"), "00:00")
    end_t = _parse_hhmm(time_range.get("end"), "23:59")

    if start_t == end_t:
        return _day_allowed(local_dt, allowed_days)

    if start_t < end_t:
        return _day_allowed(local_dt, allowed_days) and _time_allowed(local_dt, start_t, end_t)

    # Fascia notturna. La parte dopo mezzanotte appartiene logicamente al giorno precedente.
    if local_dt.time() >= start_t:
        schedule_day = local_dt
    else:
        schedule_day = local_dt - timedelta(days=1)
    return _day_allowed(schedule_day, allowed_days) and _time_allowed(local_dt, start_t, end_t)


def is_within_active_schedule(user_id: int) -> bool:
    return is_datetime_within_active_schedule(user_id, None)


def next_active_datetime(user_id: int, from_dt: datetime | None = None) -> datetime | None:
    """
    Restituisce il prossimo datetime utile in cui l'autopost può pubblicare.
    Se la configurazione non limita giorni/orari, restituisce None.
    """
    schedule = get_user_schedule(user_id)
    allowed_days = schedule.get("days", [])
    time_range = schedule.get("time_range", {})

    if not allowed_days and not time_range:
        return None

    base = _as_local(from_dt).replace(microsecond=0)

    if is_datetime_within_active_schedule(user_id, base):
        return base

    # Solo giorni: prossima mezzanotte consentita.
    if not time_range:
        for offset in range(0, 15):
            day = base + timedelta(days=offset)
            if _day_allowed(day, allowed_days):
                candidate = day.replace(hour=0, minute=0, second=5, microsecond=0)
                if candidate > base:
                    return candidate
        return base + timedelta(hours=1)

    start_t = _parse_hhmm(time_range.get("start"), "00:00")
    end_t = _parse_hhmm(time_range.get("end"), "23:59")

    if start_t == end_t:
        for offset in range(0, 15):
            day = base + timedelta(days=offset)
            if _day_allowed(day, allowed_days):
                candidate = day.replace(hour=0, minute=0, second=5, microsecond=0)
                if candidate > base:
                    return candidate
        return base + timedelta(hours=1)

    for offset in range(0, 15):
        day = base + timedelta(days=offset)

        if start_t < end_t:
            if not _day_allowed(day, allowed_days):
                continue
            candidate = day.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
            if candidate > base:
                return candidate
            continue

        # Fascia notturna: candidato = inizio fascia del giorno consentito.
        if not _day_allowed(day, allowed_days):
            continue
        candidate = day.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
        if candidate > base:
            return candidate

    return base + timedelta(hours=1)


def next_allowed_timestamp_after_interval(user_id: int, interval_minutes: int) -> float:
    """
    Calcola il prossimo next_post rispettando SEMPRE la finestra oraria.
    Prima applica l'intervallo, poi se il risultato cade fuori orario lo sposta
    al prossimo orario valido.
    """
    base = _now() + timedelta(minutes=max(1, int(interval_minutes)))
    if is_datetime_within_active_schedule(user_id, base):
        return base.timestamp()
    nxt = next_active_datetime(user_id, base)
    return (nxt or base).timestamp()


def format_schedule_status(user_id: int) -> str:
    schedule = get_user_schedule(user_id)
    days = schedule.get("days", [])
    time_range = schedule.get("time_range", {})
    start = time_range.get("start", "sempre") if time_range else "sempre"
    end = time_range.get("end", "sempre") if time_range else "sempre"
    days_txt = ", ".join(days) if days else "tutti i giorni"
    return f"giorni={days_txt} | orario={start}-{end}"

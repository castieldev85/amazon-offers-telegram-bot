import json
import logging
import os
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


def _now() -> datetime:
    try:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    except Exception:
        logger.warning("[SCHEDULE] Timezone non valida %s, uso Europe/Rome", DEFAULT_TIMEZONE)
        return datetime.now(ZoneInfo("Europe/Rome"))


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
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
    return {
        "days": [d for d in days if d in DAY_NAMES],
        "time_range": time_range,
    }


def set_user_schedule(user_id: int, schedule: dict):
    data = load_schedule_config()
    data[str(user_id)] = schedule
    save_schedule_config(data)


def _day_allowed(dt: datetime, allowed_days: list[str]) -> bool:
    if not allowed_days:
        return True
    return PY_WEEKDAY_TO_IT[dt.weekday()] in allowed_days


def is_within_active_schedule(user_id: int) -> bool:
    """
    True se il bot può pubblicare adesso per l'utente.

    Regole V2.9:
    - se giorni e orari non sono configurati, non blocca mai;
    - se sono configurati solo gli orari, valgono tutti i giorni;
    - se sono configurati solo i giorni, valgono tutto il giorno;
    - se start == end, interpreta la fascia come 24h attiva;
    - gestisce fasce notturne tipo 22:00 -> 06:00.
    """
    schedule = get_user_schedule(user_id)
    allowed_days = schedule.get("days", [])
    time_range = schedule.get("time_range", {})

    if not allowed_days and not time_range:
        return True

    now = _now()
    if not _day_allowed(now, allowed_days):
        return False

    if not time_range:
        return True

    start_t = _parse_hhmm(time_range.get("start"), "00:00")
    end_t = _parse_hhmm(time_range.get("end"), "23:59")

    if start_t == end_t:
        return True

    now_t = now.time()
    if start_t < end_t:
        return start_t <= now_t <= end_t

    # Fascia che attraversa la mezzanotte.
    return now_t >= start_t or now_t <= end_t


def next_active_datetime(user_id: int) -> datetime | None:
    """
    Restituisce il prossimo datetime utile in cui l'autopost può riprovare.
    Serve per evitare il log/loop continuo "fuori orario" ogni minuto.
    """
    schedule = get_user_schedule(user_id)
    allowed_days = schedule.get("days", [])
    time_range = schedule.get("time_range", {})

    if not allowed_days and not time_range:
        return None

    now = _now()

    if not time_range:
        # Solo filtro giorni: riprova a mezzanotte del prossimo giorno consentito.
        for offset in range(0, 8):
            day = now + timedelta(days=offset)
            if _day_allowed(day, allowed_days):
                candidate = day.replace(hour=0, minute=0, second=5, microsecond=0)
                if candidate > now:
                    return candidate
        return now + timedelta(hours=1)

    start_t = _parse_hhmm(time_range.get("start"), "00:00")
    end_t = _parse_hhmm(time_range.get("end"), "23:59")

    if start_t == end_t:
        # Fascia 24h: se il problema è il giorno, vai al prossimo giorno valido.
        for offset in range(0, 8):
            day = now + timedelta(days=offset)
            if _day_allowed(day, allowed_days):
                candidate = day.replace(hour=0, minute=0, second=5, microsecond=0)
                if candidate > now:
                    return candidate
        return now + timedelta(hours=1)

    for offset in range(0, 8):
        day = now + timedelta(days=offset)
        if not _day_allowed(day, allowed_days):
            continue

        start_dt = day.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
        end_dt = day.replace(hour=end_t.hour, minute=end_t.minute, second=59, microsecond=0)

        if start_t < end_t:
            if now <= start_dt:
                return start_dt
            if start_dt <= now <= end_dt:
                return now
            continue

        # Fascia notturna: start oggi, fine domani.
        if now <= start_dt:
            return start_dt
        overnight_end = end_dt + timedelta(days=1)
        if start_dt <= now <= overnight_end:
            return now

    return now + timedelta(hours=1)


def format_schedule_status(user_id: int) -> str:
    schedule = get_user_schedule(user_id)
    days = schedule.get("days", [])
    time_range = schedule.get("time_range", {})
    start = time_range.get("start", "sempre") if time_range else "sempre"
    end = time_range.get("end", "sempre") if time_range else "sempre"
    days_txt = ", ".join(days) if days else "tutti i giorni"
    return f"giorni={days_txt} | orario={start}-{end}"

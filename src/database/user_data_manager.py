import json
import os
import threading
import time
from datetime import datetime
from typing import Any

from src.configs.settings import USER_DATA_PATH


DEFAULT_USER_DATA: dict[str, Any] = {
    "categories": [],
    "min_discount": 20,
    "days_delay": 2,
    "post_interval": 15,
    "offers_per_cycle": 1,
    "buffer_clear_days": 0,
    "last_buffer_clear": 0,
    "category_scrolls": 8,
    "category_pages": 2,
    "telegram_channels": [],
    "telegram_source_channels": [],
    "telegram_source_limit": 30,
    "watchlist": [],
}

# Windows può bloccare per pochi millisecondi i file JSON se più thread leggono/scrivono
# contemporaneamente. Tutte le operazioni su user_data.json passano da questo lock.
_USER_DATA_LOCK = threading.RLock()


def _merge_defaults(user: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_USER_DATA)
    merged.update(user or {})
    return merged


def _load_user_data_unlocked() -> dict:
    if not os.path.exists(USER_DATA_PATH):
        return {}
    try:
        with open(USER_DATA_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_user_data() -> dict:
    with _USER_DATA_LOCK:
        return _load_user_data_unlocked()


def _save_user_data_unlocked(data: dict):
    dirpath = os.path.dirname(USER_DATA_PATH)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)

    # tmp unico per thread: evita collisioni su user_data.json.tmp
    tmp_path = f"{USER_DATA_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"

    last_error: Exception | None = None
    for attempt in range(10):
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, USER_DATA_PATH)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
        finally:
            # Se os.replace è riuscito, tmp_path non esiste più.
            # Se è fallito, proviamo a pulirlo senza bloccare il bot.
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    if last_error:
        raise last_error


def save_user_data(data: dict):
    with _USER_DATA_LOCK:
        _save_user_data_unlocked(data)


def ensure_user_entry(user_id: int) -> dict:
    with _USER_DATA_LOCK:
        data = _load_user_data_unlocked()
        uid = str(user_id)
        changed = False

        if uid not in data:
            data[uid] = _merge_defaults({
                "created_at": datetime.utcnow().isoformat(),
            })
            changed = True
        else:
            before = dict(data[uid]) if isinstance(data[uid], dict) else {}
            data[uid] = _merge_defaults(before)
            changed = data[uid] != before

        # Importante: i getter non devono riscrivere user_data.json a ogni chiamata.
        # Salviamo solo se l'utente manca o se sono stati aggiunti nuovi campi default.
        if changed:
            _save_user_data_unlocked(data)

        return data


def get_user_categories(user_id: int) -> list:
    data = ensure_user_entry(user_id)
    return data.get(str(user_id), {}).get("categories", [])


def toggle_user_category(user_id: int, category_code: str):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        uid = str(user_id)
        categories = data[uid].setdefault("categories", [])

        if category_code in categories:
            categories.remove(category_code)
        else:
            categories.append(category_code)

        _save_user_data_unlocked(data)


def get_user_min_discount(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        return int(data.get(str(user_id), {}).get("min_discount", 20))
    except Exception:
        return 20


def set_user_min_discount(user_id: int, discount_value: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        data[str(user_id)]["min_discount"] = int(discount_value)
        _save_user_data_unlocked(data)


def get_user_days_limit(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        return int(data.get(str(user_id), {}).get("days_delay", 2))
    except Exception:
        return 2


def set_user_days_limit(user_id: int, days: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        data[str(user_id)]["days_delay"] = int(days)
        _save_user_data_unlocked(data)


def get_user_buffer_clear_days(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        return int(data.get(str(user_id), {}).get("buffer_clear_days", 0))
    except Exception:
        return 0


def set_user_buffer_clear_days(user_id: int, days: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        uid = str(user_id)
        data[uid]["buffer_clear_days"] = int(days)
        data[uid]["last_buffer_clear"] = time.time() if int(days) > 0 else 0
        _save_user_data_unlocked(data)


def get_user_post_interval(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        return int(data.get(str(user_id), {}).get("post_interval", 15))
    except Exception:
        return 15


def set_user_post_interval(user_id: int, minutes: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        data[str(user_id)]["post_interval"] = int(minutes)
        _save_user_data_unlocked(data)


def get_user_offers_per_cycle(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        value = int(data.get(str(user_id), {}).get("offers_per_cycle", 1))
        return max(1, min(value, 10))
    except Exception:
        return 1


def set_user_offers_per_cycle(user_id: int, count: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        value = max(1, min(int(count), 10))
        data[str(user_id)]["offers_per_cycle"] = value
        _save_user_data_unlocked(data)


def get_user_category_scrolls(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        value = int(data.get(str(user_id), {}).get("category_scrolls", 8))
        return max(1, min(value, 30))
    except Exception:
        return 8


def set_user_category_scrolls(user_id: int, scrolls: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        value = max(1, min(int(scrolls), 30))
        data[str(user_id)]["category_scrolls"] = value
        _save_user_data_unlocked(data)



def get_user_category_pages(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        value = int(data.get(str(user_id), {}).get("category_pages", 2))
        return max(1, min(value, 10))
    except Exception:
        return 2


def set_user_category_pages(user_id: int, pages: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        value = max(1, min(int(pages), 10))
        data[str(user_id)]["category_pages"] = value
        _save_user_data_unlocked(data)

def get_user_min_rating(user_id: int) -> float:
    data = ensure_user_entry(user_id)
    try:
        return float(data.get(str(user_id), {}).get("min_rating", 0.0))
    except Exception:
        return 0.0


def set_user_min_rating(user_id: int, rating: float):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        data[str(user_id)]["min_rating"] = float(rating)
        _save_user_data_unlocked(data)


def get_user_prime_only(user_id: int) -> bool:
    data = ensure_user_entry(user_id)
    return bool(data.get(str(user_id), {}).get("prime_only", False))


def set_user_prime_only(user_id: int, prime: bool):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        data[str(user_id)]["prime_only"] = bool(prime)
        _save_user_data_unlocked(data)


def get_user_max_price(user_id: int) -> float | None:
    data = ensure_user_entry(user_id)
    value = data.get(str(user_id), {}).get("max_price", None)
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def set_user_max_price(user_id: int, price: float | None):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        data[str(user_id)]["max_price"] = price
        _save_user_data_unlocked(data)



def get_user_telegram_source_channels(user_id: int) -> list[str]:
    """Restituisce le fonti Telegram salvate.

    V3.10: le fonti vengono salvate anche in telegram_sources.json per non
    perderle al riavvio o durante migrazioni di user_data.json. Se il nuovo
    store è vuoto, migra automaticamente i vecchi valori da user_data.json.
    """
    from src.telegram_sources.source_store import get_sources, set_sources

    stored = get_sources(user_id)
    if stored:
        return stored

    data = ensure_user_entry(user_id)
    legacy = data.get(str(user_id), {}).get("telegram_source_channels", [])
    if isinstance(legacy, list) and legacy:
        set_sources(user_id, legacy)
        return legacy
    return []


def add_user_telegram_source_channel(user_id: int, channel: str):
    from src.telegram_sources.importer import normalize_channel_name, TELEGRAM_SOURCE_CATEGORY
    from src.telegram_sources.source_store import add_source

    clean = normalize_channel_name(channel)
    if not clean:
        raise ValueError("Canale Telegram non valido")

    # Salvataggio persistente separato, robusto al riavvio.
    add_source(user_id, clean)

    # Mantengo anche il valore legacy in user_data.json per compatibilità.
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        uid = str(user_id)
        sources = data[uid].setdefault("telegram_source_channels", [])
        if clean not in sources:
            sources.append(clean)
        categories = data[uid].setdefault("categories", [])
        if TELEGRAM_SOURCE_CATEGORY not in categories:
            categories.append(TELEGRAM_SOURCE_CATEGORY)
        _save_user_data_unlocked(data)


def remove_user_telegram_source_channel(user_id: int, channel: str):
    from src.telegram_sources.importer import normalize_channel_name
    from src.telegram_sources.source_store import remove_source

    clean = normalize_channel_name(channel)
    remove_source(user_id, clean)

    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        uid = str(user_id)
        sources = data[uid].setdefault("telegram_source_channels", [])
        data[uid]["telegram_source_channels"] = [x for x in sources if normalize_channel_name(x) != clean]
        _save_user_data_unlocked(data)


def get_user_telegram_source_limit(user_id: int) -> int:
    data = ensure_user_entry(user_id)
    try:
        value = int(data.get(str(user_id), {}).get("telegram_source_limit", 30))
        return max(5, min(value, 200))
    except Exception:
        return 30


def set_user_telegram_source_limit(user_id: int, limit: int):
    with _USER_DATA_LOCK:
        data = ensure_user_entry(user_id)
        data[str(user_id)]["telegram_source_limit"] = max(5, min(int(limit), 200))
        _save_user_data_unlocked(data)

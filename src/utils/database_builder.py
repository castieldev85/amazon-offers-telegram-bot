# src/utils/database_builder.py

import json
import os
import time
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from src.database.user_data_manager import get_user_days_limit, load_user_data

logger = logging.getLogger(__name__)

from src.configs.settings import PUBLISHED_DB_PATH, LINK_MAP_PATH


def safe_load_json(path: str) -> Dict[str, Any]:
    """
    Carica un file JSON in sicurezza.
    Restituisce {} se il file non esiste, è vuoto o è corrotto.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[safe_load_json] Errore lettura {path}: {e}")
        return {}


def safe_save_json(path: str, data: Dict[str, Any]) -> None:
    """
    Salva in modo sicuro un dict in JSON. Crea la cartella se necessario.
    """
    dirpath = os.path.dirname(path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.error(f"[safe_save_json] Impossibile salvare {path}: {e}")


def load_db() -> Dict[str, Any]:
    return safe_load_json(PUBLISHED_DB_PATH)


def save_db(data: Dict[str, Any]) -> None:
    safe_save_json(PUBLISHED_DB_PATH, data)


def add_to_publication_log(user_id: int, asin: str) -> None:
    """
    Aggiunge entry semplice (data) al log delle pubblicazioni locali.
    """
    db = load_db()
    uid = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if uid not in db:
        db[uid] = {}
    db[uid][asin] = today
    save_db(db)


def _find_link_map_entry(mapping: Dict[str, Any], user_id: int, asin: str) -> Optional[Dict[str, Any]]:
    """
    Trova l'ultima entry di pubblicazione per user+ASIN.
    Supporta sia la vecchia chiave "ASIN" sia la nuova chiave "user_id:ASIN".
    """
    asin = str(asin or "").strip().upper()
    if not asin:
        return None

    direct_keys = [f"{user_id}:{asin}", asin]
    candidates: list[Dict[str, Any]] = []

    for key in direct_keys:
        entry = mapping.get(key)
        if isinstance(entry, dict) and str(entry.get("chat_id")) == str(user_id):
            candidates.append(entry)

    for entry in mapping.values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("chat_id")) != str(user_id):
            continue
        if str(entry.get("asin", "")).strip().upper() == asin:
            candidates.append(entry)

    if not candidates:
        return None

    def _ts(item: Dict[str, Any]) -> float:
        try:
            return float(item.get("timestamp", 0) or 0)
        except Exception:
            return 0.0

    return max(candidates, key=_ts)


def is_valid_for_resend(user_id: int, asin: str) -> bool:
    """
    True se l'ASIN può essere reinviato a user_id.
    False se lo stesso ASIN è stato già pubblicato entro days_delay giorni.
    """
    asin = str(asin or "").strip().upper()
    if not asin:
        return False

    mapping = safe_load_json(LINK_MAP_PATH)
    if not mapping:
        return True

    entry = _find_link_map_entry(mapping, user_id, asin)
    if not entry:
        return True

    timestamp = entry.get("timestamp")
    if not timestamp:
        return True

    try:
        published_at = datetime.utcfromtimestamp(float(timestamp))
    except Exception as e:
        logger.warning(f"[is_valid_for_resend] impossibile parsare timestamp per {asin}: {e}")
        return True

    days_limit = get_user_days_limit(user_id)
    cutoff_date = datetime.utcnow() - timedelta(days=days_limit)
    return published_at < cutoff_date


def is_within_post_interval(user_id: int) -> bool:
    """
    Verifica se l'intervallo di pubblicazione è stato rispettato per l'utente.
    """
    data = load_user_data()
    user_data = data.get(str(user_id), {})

    now = time.time()
    next_post = user_data.get("next_post", 0)

    if now >= next_post:
        logger.debug(f"[INTERVALLO] OK: ora={now}, next_post={next_post} per user {user_id}")
        return True
    else:
        remaining = int(next_post - now)
        logger.debug(f"[INTERVALLO] NO: ancora {remaining} secondi per user {user_id}")
        return False


def get_last_posted_timestamp(user_id: int, asin: str) -> Optional[float]:
    """
    Restituisce timestamp (epoch seconds) dell'ultima pubblicazione per asin a user_id,
    oppure None se non esiste.
    """
    mapping = safe_load_json(LINK_MAP_PATH)
    entry = _find_link_map_entry(mapping, user_id, asin)
    if entry:
        try:
            return float(entry.get("timestamp"))
        except Exception:
            return None
    return None


def get_last_posted_date(user_id: int, asin: str) -> str:
    """
    Restituisce data leggibile dell'ultima pubblicazione per asin a user_id,
    oppure una stringa di fallback se non disponibile.
    """
    mapping = safe_load_json(LINK_MAP_PATH)
    asin_info = _find_link_map_entry(mapping, user_id, asin)
    if not asin_info:
        return "❓ sconosciuta"

    timestamp = asin_info.get("timestamp")
    if not timestamp:
        return "❓ sconosciuta"

    try:
        dt = datetime.utcfromtimestamp(float(timestamp))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        return f"❓ errore data: {e}"

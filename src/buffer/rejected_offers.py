import json
import logging
import os
import time
from typing import Iterable

from src.configs.settings import INVALID_BUFFER_QUARANTINE_HOURS, REJECTED_OFFERS_PATH

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _safe_asin(asin) -> str:
    return str(asin or "").strip().upper()


def _key(user_id: int, category_code: str) -> str:
    return f"{int(user_id)}:{str(category_code or '').strip()}"


def _load_data() -> dict:
    if not os.path.exists(REJECTED_OFFERS_PATH):
        return {}
    try:
        with open(REJECTED_OFFERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[REJECTED] Errore lettura {REJECTED_OFFERS_PATH}: {e}")
        return {}


def _save_data(data: dict) -> None:
    folder = os.path.dirname(REJECTED_OFFERS_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp_path = f"{REJECTED_OFFERS_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, REJECTED_OFFERS_PATH)


def cleanup_expired_rejections() -> int:
    ttl = max(1, int(INVALID_BUFFER_QUARANTINE_HOURS)) * 3600
    cutoff = _now() - ttl
    data = _load_data()
    changed = False
    removed = 0

    for bucket_key in list(data.keys()):
        bucket = data.get(bucket_key)
        if not isinstance(bucket, dict):
            data.pop(bucket_key, None)
            changed = True
            continue
        for asin in list(bucket.keys()):
            info = bucket.get(asin) or {}
            ts = float(info.get("ts", 0) or 0)
            if ts < cutoff:
                bucket.pop(asin, None)
                removed += 1
                changed = True
        if not bucket:
            data.pop(bucket_key, None)
            changed = True

    if changed:
        _save_data(data)
    return removed


def mark_rejected_asins(user_id: int, category_code: str, asins: Iterable[str], reason: str = "low_score") -> int:
    clean_asins = sorted({_safe_asin(a) for a in (asins or []) if _safe_asin(a)})
    if not clean_asins:
        return 0

    cleanup_expired_rejections()
    data = _load_data()
    bucket_key = _key(user_id, category_code)
    bucket = data.setdefault(bucket_key, {})
    ts = _now()

    for asin in clean_asins:
        bucket[asin] = {"ts": ts, "reason": reason}

    _save_data(data)
    logger.info(
        f"[REJECTED] Messi in quarantena {len(clean_asins)} ASIN per {bucket_key} "
        f"({INVALID_BUFFER_QUARANTINE_HOURS}h) reason={reason}"
    )
    return len(clean_asins)


def is_rejected_asin(user_id: int, category_code: str, asin: str) -> bool:
    asin = _safe_asin(asin)
    if not asin:
        return False

    ttl = max(1, int(INVALID_BUFFER_QUARANTINE_HOURS)) * 3600
    data = _load_data()
    info = (data.get(_key(user_id, category_code)) or {}).get(asin)
    if not info:
        return False

    ts = float((info or {}).get("ts", 0) or 0)
    if ts and (_now() - ts) <= ttl:
        return True

    # Scaduto: pulizia pigra.
    cleanup_expired_rejections()
    return False

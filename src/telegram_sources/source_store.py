"""Persistenza separata per le fonti Telegram.

Questa scelta evita che i canali sorgente vadano persi se user_data.json viene
rigenerato, sovrascritto o migrato tra versioni diverse del bot.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

_STORE_LOCK = threading.RLock()
DEFAULT_PATH = os.getenv("TELEGRAM_SOURCES_PATH", "telegram_sources.json")


def _load_unlocked(path: str = DEFAULT_PATH) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return {}
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_unlocked(data: dict[str, Any], path: str = DEFAULT_PATH) -> None:
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    last_error: Exception | None = None
    for attempt in range(10):
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
    if last_error:
        raise last_error


def get_sources(user_id: int) -> list[str]:
    with _STORE_LOCK:
        data = _load_unlocked()
        user = data.get(str(user_id), {})
        sources = user.get("channels", [])
        return sources if isinstance(sources, list) else []


def set_sources(user_id: int, channels: list[str]) -> None:
    with _STORE_LOCK:
        data = _load_unlocked()
        uid = str(user_id)
        user = data.setdefault(uid, {})
        user["channels"] = channels
        user["updated_at"] = int(time.time())
        _save_unlocked(data)


def add_source(user_id: int, channel: str) -> None:
    channels = get_sources(user_id)
    if channel not in channels:
        channels.append(channel)
        set_sources(user_id, channels)


def remove_source(user_id: int, channel: str) -> None:
    channels = [x for x in get_sources(user_id) if x != channel]
    set_sources(user_id, channels)
    clear_source_stats(user_id, channel)


def get_source_stats(user_id: int, channel: str | None = None) -> dict[str, Any]:
    """Restituisce le statistiche delle fonti Telegram salvate.

    Se channel è valorizzato, restituisce solo le statistiche di quel canale.
    Le statistiche vengono aggiornate a ogni scansione manuale/automatica.
    """
    with _STORE_LOCK:
        data = _load_unlocked()
        user = data.get(str(user_id), {})
        stats = user.get("stats", {})
        if not isinstance(stats, dict):
            return {}
        if channel is None:
            return stats
        return stats.get(channel, {}) if isinstance(stats.get(channel, {}), dict) else {}


def set_source_stats(user_id: int, channel: str, stats: dict[str, Any]) -> None:
    """Salva le statistiche dell'ultima scansione per una fonte Telegram."""
    with _STORE_LOCK:
        data = _load_unlocked()
        uid = str(user_id)
        user = data.setdefault(uid, {})
        all_stats = user.setdefault("stats", {})
        if not isinstance(all_stats, dict):
            all_stats = {}
            user["stats"] = all_stats
        previous = all_stats.get(channel, {})
        if not isinstance(previous, dict):
            previous = {}

        merged = dict(previous)
        merged.update(stats or {})
        merged["updated_at"] = int(time.time())
        all_stats[channel] = merged
        user["updated_at"] = int(time.time())
        _save_unlocked(data)


def clear_source_stats(user_id: int, channel: str) -> None:
    """Elimina le statistiche associate a una fonte rimossa."""
    with _STORE_LOCK:
        data = _load_unlocked()
        uid = str(user_id)
        user = data.get(uid, {})
        stats = user.get("stats", {})
        if isinstance(stats, dict) and channel in stats:
            stats.pop(channel, None)
            user["updated_at"] = int(time.time())
            _save_unlocked(data)

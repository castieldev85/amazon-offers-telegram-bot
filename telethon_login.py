#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Login guidato Telethon per Fonti Telegram avanzate.

Esegui una sola volta:
    python telethon_login.py

Richiede nel file .env:
    TELETHON_API_ID=...
    TELETHON_API_HASH=...
    TELETHON_SESSION=telegram_user.session
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient


def main() -> None:
    load_dotenv()

    api_id_raw = os.getenv("TELETHON_API_ID", "").strip()
    api_hash = os.getenv("TELETHON_API_HASH", "").strip()
    session = os.getenv("TELETHON_SESSION", "telegram_user.session").strip() or "telegram_user.session"

    if not api_id_raw or not api_hash:
        print("❌ TELETHON_API_ID o TELETHON_API_HASH mancanti nel file .env")
        print("Apri https://my.telegram.org → API development tools e crea una app.")
        return

    try:
        api_id = int(api_id_raw)
    except ValueError:
        print("❌ TELETHON_API_ID deve essere numerico")
        return

    print("🔐 Login Telethon")
    print("━━━━━━━━━━━━━━━━━━")
    print(f"Sessione: {session}")
    print("Usa preferibilmente un account Telegram dedicato, non il tuo personale.")
    print()

    with TelegramClient(session, api_id, api_hash) as client:
        me = client.get_me()
        print("✅ Login completato")
        if me:
            username = f"@{me.username}" if getattr(me, "username", None) else "senza username"
            print(f"Account: {getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''} ({username})".strip())
        print()
        print("Ora puoi impostare nel .env:")
        print("TELETHON_ENABLED=true")
        print("e riavviare il bot.")


if __name__ == "__main__":
    main()

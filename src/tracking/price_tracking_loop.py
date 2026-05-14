
import asyncio
import json
import os
from datetime import datetime
from src.scraper.product_scraper import extract_product_info
from src.utils.product import Product

TRACKING_FILE = "price_tracking.json"

async def check_price_tracking_loop(app_context):
    while True:
        print(f"[TRACKING] 🔄 Avvio controllo prezzi: {datetime.now().strftime('%H:%M:%S')}")
        if not os.path.exists(TRACKING_FILE):
            await asyncio.sleep(300)
            continue

        try:
            with open(TRACKING_FILE, "r", encoding="utf-8") as f:
                tracking = json.load(f)
        except:
            print("[TRACKING] ⚠️ Errore lettura file tracking")
            await asyncio.sleep(300)
            continue

        for user_id, products in tracking.items():
            for asin, info in products.items():
                if info.get("notified"):
                    continue

                product = extract_product_info(asin)
                if not product or product.price == "N/D":
                    continue

                try:
                    current_price = float(str(product.price).replace(",", "."))
                    target_price = float(info["target_price"])
                except:
                    continue

                if current_price <= target_price:
                    try:
                        msg = (
                            f"📉 <b>Prezzo monitorato in calo!</b>\\n\\n"
                            f"🔔 <b>{product.title}</b>\\n"
                            f"💰 Ora a: <b>{current_price}€</b>\\n"
                            f"🎯 Target: {target_price}€\\n\\n"
                            f"{product.link}"
                        )
                        await app_context.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="HTML")
                        tracking[user_id][asin]["notified"] = True
                    except Exception as e:
                        print(f"[TRACKING] ❌ Errore notifica user {user_id}: {e}")

        try:
            with open(TRACKING_FILE, "w", encoding="utf-8") as f:
                json.dump(tracking, f, indent=2)
        except:
            print("[TRACKING] ⚠️ Errore salvataggio tracking.json")

        await asyncio.sleep(300)  # ogni 5 minuti

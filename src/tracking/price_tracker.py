
import json
import os
from src.scraper.product_scraper import extract_product_info

TRACKING_FILE = "price_tracking.json"

def load_tracking():
    if not os.path.exists(TRACKING_FILE):
        return {}
    with open(TRACKING_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tracking(data):
    with open(TRACKING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

async def add_to_price_tracking(user_id: int, asin: str, context):
    product = extract_product_info(asin)
    if not product or not product.price or product.price == "N/D":
        await context.bot.send_message(
        user_id,
        (
            f"🔔 Tracciamento attivato per:\n"
            f"<b>{product.title}</b>\n"
            f"Prezzo attuale: {price_float:.2f}€"
        ),
        parse_mode="HTML"
    )

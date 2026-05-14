import re
import asyncio
from telethon.sync import TelegramClient
from src.utils.amazon_parser import extract_product_info
from src.buffer.buffer_manager import add_products_to_buffer
from src.utils.database_builder import is_valid_for_resend
from src.utils.product import Product  # Classe Product già usata nel tuo bot
from src.configs.settings import BUFFER_PATH

# Inserisci i tuoi dati Telegram
api_id = 123456     # Sostituisci con il tuo
api_hash = 'abc123def456gh789'  # Sostituisci con il tuo
session_name = 'telegram_scraper'

# Gruppo da cui leggere (senza @)
target_group = 'offertesconticodici'

# ID utente per salvataggio buffer (puoi assegnarne uno fisso o uno reale)
fake_user_id = 999999999

# Categoria fittizia da usare (può essere "cat_telegram" o simile)
category = "cat_telegram"

# Quanti messaggi leggere
message_limit = 20


def extract_amazon_links(text):
    return re.findall(r'(https?://www\.amazon\.[a-z\.]+/[^\s]+)', text)


async def main():
    async with TelegramClient(session_name, api_id, api_hash) as client:
        group = await client.get_entity(target_group)
        async for msg in client.iter_messages(group, limit=message_limit):
            if not msg.message:
                continue

            text = msg.message
            amazon_links = extract_amazon_links(text)

            for link in amazon_links:
                print(f"\n🔗 Trovato link: {link}")

                try:
                    prod_data = extract_product_info(link)
                    asin = prod_data.get("asin")
                    if not asin:
                        print("❌ ASIN non trovato.")
                        continue

                    # Check duplicati
                    if not is_valid_for_resend(fake_user_id, asin):
                        print(f"⛔ Prodotto {asin} già pubblicato di recente.")
                        continue

                    # Crea oggetto Product
                    product = Product(
                        asin=asin,
                        title=prod_data.get("title"),
                        price=prod_data.get("price"),
                        old_price=prod_data.get("old_price"),
                        discount=prod_data.get("discount"),
                        image_url=prod_data.get("image_url"),
                        coupon=prod_data.get("coupon"),
                        deal_type=prod_data.get("deal_type"),
                        marketplace=prod_data.get("marketplace"),
                        url=link,
                        category=category
                    )

                    # Aggiungi al buffer
                    add_products_to_buffer(fake_user_id, category, [product])
                    print(f"✅ Aggiunto al buffer: {asin}")

                except Exception as e:
                    print(f"⚠️ Errore su {link}: {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())

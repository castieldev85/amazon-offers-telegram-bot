
import os
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from src.utils.product import Product
from src.utils.amazon_parser import extract_product_info
from src.utils.database_builder import is_valid_for_resend
from src.utils.affiliate import generate_affiliate_link
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

SEARCH_RESULTS_DIR = "search_results"
os.makedirs(SEARCH_RESULTS_DIR, exist_ok=True)

def start_selenium():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=options)

def search_asins_from_keyword(keyword):
    driver = start_selenium()
    url = f"https://www.amazon.it/s?k={keyword.replace(' ', '+')}"
    driver.get(url)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()

    asin_elements = soup.select('[data-asin]')
    asins = [el["data-asin"] for el in asin_elements if el["data-asin"]]
    return list(dict.fromkeys(asins))[:20]  # max 20 risultati unici

async def search_and_show_offers(chat_id, keyword, context):
    from src.database.user_data_manager import get_user_min_discount
    min_discount = get_user_min_discount(chat_id)
    all_asins = search_asins_from_keyword(keyword)

    products = []
    for asin in all_asins:
        if not is_valid_for_resend(chat_id, asin):
            continue
        product = extract_product_info(f"https://www.amazon.it/dp/{asin}")
        if product and product.discount >= min_discount:
            products.append(product)

    products.sort(key=lambda p: float(str(p.price).replace(",", ".") or 9999))

    if not products:
        await context.bot.send_message(chat_id, "❌ Nessuna offerta trovata con i tuoi filtri.")
        return

    filepath = os.path.join(SEARCH_RESULTS_DIR, f"{chat_id}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in products], f, indent=2)

    await send_offer_page(chat_id, 0, context)

async def send_offer_page(chat_id, index, context):
    filepath = os.path.join(SEARCH_RESULTS_DIR, f"{chat_id}.json")
    if not os.path.exists(filepath):
        await context.bot.send_message(chat_id, "❌ Nessun risultato salvato.")
        return

    with open(filepath, "r", encoding="utf-8") as f:
        items = json.load(f)

    if index < 0 or index >= len(items):
        return

    from_dict = Product.from_dict if hasattr(Product, 'from_dict') else lambda x: Product(**x)
    current = from_dict(items[index])
    link = generate_affiliate_link(chat_id, current.asin)

    caption = (
        f"🔥 <b>{current.title}</b>\n"
        f"💰 <b>{current.price}€</b>\n"
        f"📉 Sconto: <b>{current.discount}%</b>\n"
        f"\n{link}"
    )

    buttons = []
    if index > 0:
        buttons.append(InlineKeyboardButton("◀️", callback_data=f"search_offer:{chat_id}:{index - 1}"))
    buttons.append(InlineKeyboardButton("🔔 Traccia Prezzo", callback_data=f"track_price:{current.asin}"))
    if index < len(items) - 1:
        buttons.append(InlineKeyboardButton("▶️", callback_data=f"search_offer:{chat_id}:{index + 1}"))

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Scopri Offerta", url=link)],
        buttons
    ])

    await context.bot.send_photo(
        chat_id=chat_id,
        photo=current.image,
        caption=caption,
        parse_mode="HTML",
        reply_markup=markup
    )

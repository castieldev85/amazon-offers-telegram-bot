from src.scraper.electronics_scraper import get_asins_from_electronics
from src.scraper.product_scraper import extract_product_info
from src.buffer.buffer_manager import add_products_to_buffer, needs_refill
from src.utils.product import Product

def refill_electronics_buffer_for_user(user_id: int, min_discount_percent: int = 20, max_products: int = 10):
    category_code = "cat_elettronica"

    if not needs_refill(category_code):
        print(f"[REFILL] Il buffer di {category_code} è già pieno.")
        return

    print(f"[REFILL] Avvio refill per {category_code}...")

    # Trova ASIN dalla pagina elettronica
    asins = get_asins_from_electronics(min_discount_label="3", max_scrolls=5)
    selected_asins = asins[:max_products]

    final_products = []

    for asin in selected_asins:
        product = extract_product_info(asin)

        if product:
            print(f"[DEBUG] ➜ {product.title} | {product.discount}%")
            if product.discount >= min_discount_percent:
                final_products.append(product)
                print(f"[REFILL] ✔ Inserito nel buffer")
            else:
                print(f"[REFILL] ❌ Sconto troppo basso")
        else:
            print(f"[REFILL] ⚠️ Prodotto non valido o parsing fallito")

    add_products_to_buffer(category_code, final_products)
    print(f"[REFILL] ✅ Aggiunti {len(final_products)} prodotti nel buffer {category_code}")

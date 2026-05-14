import requests
from bs4 import BeautifulSoup
from src.utils.product import Product, extract_asin_from_url

def extract_product_info(url: str) -> Product | None:
    # 1. User-Agent più moderno
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        # Se Amazon blocca la richiesta, lo vedi qui
        if response.status_code != 200:
            print(f"[ERROR] Amazon ha risposto con status {response.status_code}")
            return None
            
        soup = BeautifulSoup(response.content, "html.parser")

        asin = extract_asin_from_url(url)
        
        # Titolo
        title_el = soup.select_one("#productTitle")
        title = title_el.get_text(strip=True) if title_el else "Titolo non trovato"

        # Immagine (Aggiornato)
        image_tag = soup.select_one("#landingImage, #imgBlkFront, #main-image")
        image_url = ""
        if image_tag:
            # Spesso Amazon mette l'immagine in 'data-old-hires' o nel dizionario 'data-a-dynamic-image'
            image_url = image_tag.get("src")
        if not image_url:
            image_url = "https://via.placeholder.com/250x250?text=Immagine+non+disponibile"

        # PREZZO (I nuovi selettori di Amazon)
        # Amazon ora usa spesso classi invece di ID per i prezzi
        price_el = soup.select_one("span.a-price span.a-offscreen, span.a-color-price, .apexPriceToPay span.a-offscreen")
        price_str = price_el.get_text(strip=True) if price_el else "0"
        
        # Pulizia prezzo (rimuove € e sistema virgole)
        price = price_str.replace("€", "").replace("\xa0", "").strip()

        # PREZZO VECCHIO (Barrato)
        old_price_el = soup.select_one("span.a-price.a-text-price span.a-offscreen, .basisPrice .a-offscreen, .a-text-strike")
        old_price_str = old_price_el.get_text(strip=True) if old_price_el else ""
        old_price = old_price_str.replace("€", "").replace("\xa0", "").strip()

        # Calcolo sconto migliorato
        discount = 0
        try:
            p_clean = float(price.replace(".", "").replace(",", "."))
            o_clean = float(old_price.replace(".", "").replace(",", "."))
            if o_clean > p_clean:
                discount = round(100 - (p_clean / o_clean * 100))
        except:
            pass

        return Product(
            asin=asin,
            title=title,
            price=price,
            old_price=old_price,
            discount=discount,
            image=image_url,
            link=url
        )

    except Exception as e:
        print(f"[ERROR] extract_product_info: {e}")
        return None

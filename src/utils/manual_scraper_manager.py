
# File: src/utils/manual_scraper_manager.py

import os
import re
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from src.utils.product import Product

class ManualScraperManager:
    def __init__(self):
        self.driver = None

    def start_driver(self):
        if self.driver is None:
            chromium_service = Service()
            options = webdriver.ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--log-level=1")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--enable-unsafe-swiftshader")

            if os.environ.get("REMOTE_CHROMIUM"):
                self.driver = webdriver.Remote(
                    command_executor=os.environ["REMOTE_CHROMIUM"],
                    options=options
                )
                self.driver.command_executor._conn.timeout = 300
            else:
                self.driver = webdriver.Chrome(service=chromium_service, options=options)

            self.driver.set_page_load_timeout(30)
            self.driver.implicitly_wait(5)

    def close_driver(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

    def extract_product_info(self, url: str) -> Product | None:
        self.start_driver()

        try:
            self.driver.get(url)
            time.sleep(2)

            try:
                cookie_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "sp-cc-accept"))
                )
                cookie_button.click()
                time.sleep(1)
            except:
                pass

            try:
                title_el = self.driver.find_element(By.ID, "productTitle")
                title = title_el.text.strip()
            except:
                title = "Titolo non trovato"

            try:
                price_whole = self.driver.find_element(By.CSS_SELECTOR, ".a-price .a-price-whole").text
                price_fraction = self.driver.find_element(By.CSS_SELECTOR, ".a-price .a-price-fraction").text
                price = f"{price_whole}.{price_fraction}"
                price = price.replace("\n", "").replace("€", "").strip()
            except:
                price = "N/D"

            try:
                old_price_el = self.driver.find_element(By.CSS_SELECTOR, ".priceBlockStrikePriceString")
                old_price = old_price_el.text.replace("€", "").strip()
            except:
                old_price = ""

            discount = 0
            if old_price and price and old_price != "N/D" and price != "N/D":
                try:
                    old_price_val = float(old_price.replace(",", "."))
                    price_val = float(price.replace(",", "."))
                    discount = int(round((old_price_val - price_val) / old_price_val * 100))
                except:
                    discount = 0

            # Migliorato: ricerca alternativa immagine
            image_url = ""
            try:
                image_url = self.driver.find_element(By.ID, "landingImage").get_attribute("src")
            except:
                try:
                    image_url = self.driver.find_element(By.ID, "imgTagWrapperId").find_element(By.TAG_NAME, "img").get_attribute("src")
                except:
                    image_url = ""

            try:
                coupon_el = self.driver.find_element(By.XPATH, "//*[contains(text(), 'Coupon')]")
                has_coupon = True if coupon_el else False
            except:
                has_coupon = False

            try:
                limited_offer_el = self.driver.find_element(By.ID, "dealBadge")
                is_limited_offer = True if limited_offer_el else False
            except:
                is_limited_offer = False

            asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
            asin = asin_match.group(1) if asin_match else ""

            return Product(
                asin=asin,
                title=title,
                price=price,
                old_price=old_price,
                discount=discount,
                image=image_url,
                has_coupon=has_coupon,
                is_limited_offer=is_limited_offer,
                category=None,
                link=url
            )

        except Exception as e:
            print(f"[SCRAPER ERROR] {e}")
            return None

    def __del__(self):
        self.close_driver()

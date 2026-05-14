import os
import re
import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib3.exceptions import MaxRetryError
from selenium.common.exceptions import TimeoutException
from src.configs.scraper_settings import MAX_SCROLLS, SCROLL_DELAY, SCROLL_AMOUNT_PX

logger = logging.getLogger(__name__)

def start_selenium():
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
        try:
            driver = webdriver.Remote(
                command_executor=os.environ["REMOTE_CHROMIUM"],
                options=options
            )
        except MaxRetryError:
            driver = webdriver.Remote(
                command_executor=os.environ["REMOTE_CHROMIUM"] + "/wd/hub",
                options=options
            )
        driver.command_executor._conn.timeout = 300
    else:
        driver = webdriver.Chrome(service=chromium_service, options=options)

    driver.set_page_load_timeout(180)
    driver.implicitly_wait(10)
    return driver

def get_asins_from_casa_cucina(min_discount_label="3"):
    url = "https://www.amazon.it/s?k=offerte+casa+cucina"
    asins = set()
    driver = None

    try:
        driver = start_selenium()
        driver.set_page_load_timeout(30)  # ⬅️ timeout più basso

        try:
            driver.get(url)
        except TimeoutException:
            logger.error("⏳ Timeout caricamento pagina alimentari.")
            return []
        except Exception as e:
            logger.error(f"❌ Errore apertura URL alimentari: {e}")
            return []

        time.sleep(3)

        # Gestione cookie
        try:
            cookie_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "sp-cc-accept"))
            )
            cookie_button.click()
            time.sleep(1)
            logger.info("✅ Cookie accettati.")
        except:
            logger.info("ℹ️ Nessun cookie da accettare.")

        # Funzione interna per raccogliere ASIN
        def scan():
            deal_elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/dp/']")
            for el in deal_elements:
                href = el.get_attribute("href")
                match = re.search(r"/dp/(\w{10})", href)
                if match:
                    asin = match.group(1)
                    if asin not in asins:
                        logger.debug(f"🔗 ASIN trovato: {asin}")
                        asins.add(asin)

        MAX_PAGES = 5
        current_page = 1

        while current_page <= MAX_PAGES:
            logger.info(f"📄 Pagina {current_page}")
            scan()

            for step in range(MAX_SCROLLS):
                driver.execute_script(f"window.scrollBy(0, {SCROLL_AMOUNT_PX});")
                time.sleep(SCROLL_DELAY)
                scan()

            try:
                next_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "a.s-pagination-next"))
                )
                driver.execute_script("arguments[0].click();", next_button)
                time.sleep(4)
                current_page += 1
            except Exception:
                logger.info("⛔ Fine pagine o nessun pulsante 'Successivo'.")
                break

        logger.info(f"✅ Totale ASIN trovati (alimentari): {len(asins)}")

    except Exception as e:
        logger.error(f"❌ Errore generale scraper alimentari: {e}")

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

    return list(asins)

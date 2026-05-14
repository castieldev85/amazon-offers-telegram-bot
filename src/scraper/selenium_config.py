import os
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)

def start_selenium():
    """
    Configurazione ottimizzata per Selenium su macOS (2026).
    Gestisce automaticamente l'installazione del driver.
    """
    options = webdriver.ChromeOptions()
    
    # Modalità Headless (indispensabile per i bot)
    options.add_argument("--headless=new") 
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # User-Agent reale per evitare il blocco "Robot Check"
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument("--lang=it-IT")

    try:
        # Usa WebDriver Manager per scaricare il driver corretto automaticamente
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        # Timeout per evitare che il bot si appenda su pagine lente
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        logger.error(f"❌ Errore critico avvio Selenium: {e}")
        return None

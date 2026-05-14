import os
import logging
import time
import requests

logger = logging.getLogger(__name__)

IMGUR_UPLOAD_URL = "https://api.imgur.com/3/image"
CLIENT_ID = os.getenv("IMGUR_CLIENT_ID")  # assicurati di averlo esportato
HEADERS = {"Authorization": f"Client-ID {CLIENT_ID}"}
MAX_RETRIES = 3
BACKOFF_FACTOR = 2  # secondi

def upload_to_cdn(filepath: str) -> str:
    """
    Carica un’immagine su Imgur, con retry esponenziale in caso di 5xx.
    Restituisce l'URL del file su Imgur.
    Solleva RuntimeError se tutti i tentativi falliscono.
    """
    if not CLIENT_ID:
        raise RuntimeError("IMGUR_CLIENT_ID non impostato")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(filepath, "rb") as f:
                files = {"image": f}
                resp = requests.post(
                    IMGUR_UPLOAD_URL,
                    headers=HEADERS,
                    files=files,
                    timeout=10
                )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            link = data.get("link")
            if not link:
                raise RuntimeError(f"Imgur risposta senza link: {data}")
            return link

        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            logger.warning(f"Errore Imgur {status} (tentativo {attempt}/{MAX_RETRIES})")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            logger.warning(f"Connessione Imgur fallita (tentativo {attempt}/{MAX_RETRIES}): {e}")

        # se non è l’ultimo tentativo, attendi backoff
        if attempt < MAX_RETRIES:
            wait = BACKOFF_FACTOR ** (attempt - 1)
            logger.info(f"Attendo {wait}s prima del retry")
            time.sleep(wait)

    # tutti i tentativi falliti
    logger.error("Upload Imgur fallito dopo tutti i tentativi")
    raise RuntimeError("Caricamento Imgur fallito")

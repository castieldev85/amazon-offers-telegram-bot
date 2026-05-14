import os
import pandas as pd
import logging
from src.telegram.post_manager import send_post_to_channels  # già nel tuo bot

BASE_PATH = "data/offers"

def get_user_excel_path(user_id: int) -> str:
    """
    Restituisce il percorso Excel per l'utente (creando la cartella se non esiste).
    """
    user_folder = os.path.join(BASE_PATH, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return os.path.join(user_folder, "Promozioni.xlsx")


def load_offers(user_id: int) -> pd.DataFrame:
    """
    Carica le offerte dal file Excel dell'utente.
    """
    path = get_user_excel_path(user_id)
    if not os.path.exists(path):
        logging.warning(f"[PROMO] Nessun file per utente {user_id}")
        return pd.DataFrame()
    try:
        return pd.read_excel(path)
    except Exception as e:
        logging.error(f"[PROMO] Errore caricamento file utente {user_id}: {e}")
        return pd.DataFrame()


def save_offers(user_id: int, df: pd.DataFrame):
    """
    Salva il DataFrame aggiornato nel file Excel dell'utente.
    """
    path = get_user_excel_path(user_id)
    df.to_excel(path, index=False)


def get_best_offer(user_id: int):
    """
    Seleziona l'offerta migliore (rating più alto o altra metrica).
    """
    df = load_offers(user_id)
    if df.empty:
        return None, df

    # ordina per valutazione media o altro criterio
    if "avg_rating" in df.columns:
        df = df.sort_values(by="avg_rating", ascending=False)
    else:
        logging.warning(f"[PROMO] Nessuna colonna 'avg_rating' trovata per utente {user_id}")

    offer = df.iloc[0].to_dict()
    df = df.iloc[1:].reset_index(drop=True)
    return offer, df


def publish_best_offer(user_id: int):
    """
    Pubblica la migliore offerta e la rimuove dal file.
    """
    offer, df = get_best_offer(user_id)
    if not offer:
        logging.info(f"[PROMO] Nessuna offerta da pubblicare per {user_id}")
        return

    titolo = offer.get("parent_asin_name", "Offerta Amazon")
    link = offer.get("link", "")
    brand = offer.get("brand", "")
    categoria = offer.get("category", "")
    rating = offer.get("avg_rating", "")

    text = (
        f"🔥 *{titolo}*\n"
        f"🏷️ Brand: {brand}\n"
        f"📦 Categoria: {categoria}\n"
        f"⭐ Valutazione: {rating}\n"
        f"👉 [Vedi su Amazon]({link})"
    )

    try:
        send_post_to_channels(user_id, text)
        logging.info(f"[PROMO] Pubblicata offerta per {user_id}: {titolo}")
        save_offers(user_id, df)
    except Exception as e:
        logging.error(f"[PROMO] Errore pubblicazione utente {user_id}: {e}")

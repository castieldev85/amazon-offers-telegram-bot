import json
from datetime import datetime, timedelta
from src.database.user_data_manager import load_user_data, save_user_data

# ✅ Attiva licenza per un utente con scadenza definita (es. 30 giorni)
def attiva_licenza(user_id: int, giorni: int = 30):
    data = load_user_data()
    uid = str(user_id)

    if uid not in data:
        data[uid] = {}

    data[uid]["has_license"] = True
    data[uid]["license_expires"] = (datetime.now() + timedelta(days=giorni)).strftime("%Y-%m-%d")

    save_user_data(data)
    print(f"✅ Licenza attiva per {user_id} fino al {data[uid]['license_expires']}")

# 🧾 Cliente imposta liberamente il proprio tag affiliato (se ha licenza valida)
def set_user_tag_id(user_id: int, tag_id: str) -> bool:
    data = load_user_data()
    uid = str(user_id)

    if uid not in data or not data[uid].get("has_license"):
        return False

    # Check validità licenza
    expires_str = data[uid].get("license_expires")
    if expires_str:
        try:
            expires = datetime.strptime(expires_str, "%Y-%m-%d")
            if expires < datetime.now():
                return False  # licenza scaduta
        except:
            return False

    data[uid]["tag_id"] = tag_id
    save_user_data(data)
    return True

# 🔍 Usato da shortlink_generator → restituisce tag affiliato corretto
def get_affiliate_tag(user_id: int) -> str:
    from src.utils.shortlink_generator import DEFAULT_TAG_ID  # evitare import circolare
    data = load_user_data()
    user = data.get(str(user_id), {})

    expires_str = user.get("license_expires")
    if user.get("has_license") and expires_str:
        try:
            expires = datetime.strptime(expires_str, "%Y-%m-%d")
            if expires >= datetime.now() and user.get("tag_id"):
                return user["tag_id"]
        except:
            pass

    return DEFAULT_TAG_ID

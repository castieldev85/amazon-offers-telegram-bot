# src/utils/facebook_publisher.py

import requests
from src.database.user_data_manager import load_user_data

def get_affiliate_link(user_id: int, asin: str) -> str:
    # Qui generi il link affiliato (oppure lo recuperi da user_data)
    data = load_user_data().get(str(user_id), {})
    tag = data.get("tag_id", "")
    return f"https://www.amazon.it/dp/{asin}/?tag={tag}"

def publish_to_facebook_file(user_id: int, image_path: str, caption: str) -> bool:
    cfg = load_user_data().get(str(user_id), {}).get("facebook_config", {})
    page_id = cfg.get("page_id")
    token   = cfg.get("access_token")
    if not page_id or not token:
        return False
    url = f"https://graph.facebook.com/v16.0/{page_id}/photos"
    with open(image_path, "rb") as img:
        files = {"source": img}
        data  = {"caption": caption, "access_token": token}
        resp  = requests.post(url, files=files, data=data)
    return resp.ok

from PIL import Image

def prepare_for_instagram(filepath: str) -> str:
    """
    Controlla l'aspect ratio e, se necessario, aggiunge bordi bianchi
    per ottenere un'immagine quadrata (1:1).

    Ritorna il percorso del file pronto per Instagram.
    """
    img = Image.open(filepath).convert("RGB")
    w, h = img.size
    ratio = w / h

    MIN_RATIO = 4 / 5    # 0.8
    MAX_RATIO = 1.91     # ~1.91

    # Se è già in range, restituisci il file originale
    if MIN_RATIO <= ratio <= MAX_RATIO:
        return filepath

    # Altrimenti: crea un canvas quadrato (max dimension) con sfondo bianco
    new_size = max(w, h)
    canvas = Image.new("RGB", (new_size, new_size), (255, 255, 255))
    offset_x = (new_size - w) // 2
    offset_y = (new_size - h) // 2
    canvas.paste(img, (offset_x, offset_y))

    # Salva come nuovo file
    new_path = filepath.replace(".jpg", "_ig.jpg")
    canvas.save(new_path, format="JPEG", quality=95)
    return new_path

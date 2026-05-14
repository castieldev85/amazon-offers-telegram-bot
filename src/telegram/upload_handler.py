"""
Modulo legacy disattivato nella V2.
La V2 usa python-telegram-bot in main.py; l'upload Excel va collegato lì se serve.
Questo file resta solo per compatibilità con eventuali import vecchi.
"""


def handle_excel_upload(*args, **kwargs):
    raise RuntimeError("upload_handler legacy non attivo in V2: usa main.py / python-telegram-bot.")

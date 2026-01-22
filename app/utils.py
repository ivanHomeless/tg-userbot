def split_text(text, limit=4096):
    """Режет текст на куски по 4096 символов"""
    return [text[i:i+limit] for i in range(0, len(text), limit)]

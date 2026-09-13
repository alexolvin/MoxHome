class LangDetectException(Exception):
    """Ошибка детекции: нет текста, нет профилей, невозможно определить (code)."""

    code: int

    def __init__(self, code: int, message: str) -> None: ...
    def get_code(self) -> int: ...

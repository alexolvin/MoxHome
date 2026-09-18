"""Минимальные стробы lxml.html (поверхность, используемая tests/).

lxml не публикует py.typed; полные стробы (lxml-stubs) устарели.
Стробы описывают только то, что использует проект, — ничего не прячут.
"""

class _Element:
    """Элемент HTML-дерева (xpath/get/text_content/tag/attrib)."""

    tag: str
    attrib: dict[str, str]

    def xpath(self, query: str) -> list[_Element]: ...
    def get(self, key: str) -> str | None: ...
    def text_content(self) -> str: ...


def fromstring(html: str) -> _Element: ...

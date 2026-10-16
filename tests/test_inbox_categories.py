from src.core.models import REPLY_CATEGORIES_ALL
from src.inbox.categories import REPLY_LABELS_TR
from src.llm.base import REPLY_CATEGORIES


def test_every_category_has_a_turkish_label():
    """REPLY_CATEGORIES_ALL'daki her kategori için bir etiket olmalı, fazlası olmamalı."""
    assert set(REPLY_LABELS_TR) == REPLY_CATEGORIES_ALL


def test_bounce_is_not_an_llm_producible_category():
    """Bounce yalnızca kuralla belirlenir; LLM'in izinli kümesinde olmamalı."""
    assert "bounce" not in REPLY_CATEGORIES
    assert REPLY_CATEGORIES < REPLY_CATEGORIES_ALL


def test_labels_are_non_empty_strings():
    for category, label in REPLY_LABELS_TR.items():
        assert isinstance(label, str) and label.strip()

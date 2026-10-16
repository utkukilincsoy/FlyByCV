from src.core.tags import TAG_LABELS, TAG_VOCABULARY


def test_every_tag_has_a_label():
    """TAG_LABELS ile TAG_VOCABULARY birebir aynı etiketleri içermeli."""
    assert set(TAG_LABELS) == TAG_VOCABULARY


def test_labels_are_not_empty():
    """Hiçbir okunuş boş veya baş/son boşluklu olmamalı."""
    for tag, label in TAG_LABELS.items():
        assert label, f"{tag} için okunuş boş"
        assert label == label.strip(), f"{tag} okunuşunda fazla boşluk var"


def test_labels_are_unique():
    """İki etiket aynı okunuşa sahip olmamalı (mailde 'X ve X' çıkmasın)."""
    assert len(set(TAG_LABELS.values())) == len(TAG_LABELS)


def test_labels_do_not_contain_conjunction():
    """Okunuşta ' ve ' geçmemeli: iki etiket ' ve ' ile birleştiriliyor."""
    for tag, label in TAG_LABELS.items():
        assert " ve " not in label, f"{tag} okunuşu ' ve ' içeriyor"

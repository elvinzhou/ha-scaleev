import json
import re
from pathlib import Path

TRANSLATIONS = Path(__file__).resolve().parent.parent / "custom_components/scaleev/translations/en.json"


def test_no_urls_in_translations():
    """hassfest rejects URLs in translation strings; use description placeholders."""
    assert not re.search(r"https?://", TRANSLATIONS.read_text())
    json.loads(TRANSLATIONS.read_text())

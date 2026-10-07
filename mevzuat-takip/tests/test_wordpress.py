import json
from pathlib import Path

from mevzuat.sources import wordpress

FIXTURE = Path(__file__).parent / "fixtures" / "masak_posts.json"


def test_parse_posts():
    duyurular = wordpress.parse_posts(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert len(duyurular) == 10
    ilk = duyurular[0]
    assert ilk.id == 6522
    assert ilk.baslik.startswith("BMGK Tarafından")
    assert "&#" not in ilk.baslik
    assert ilk.yayin.year == 2026


def test_temizle_entity_ve_etiket():
    assert wordpress.temizle("A &#8211; <b>B</b>  C") == "A – B C"

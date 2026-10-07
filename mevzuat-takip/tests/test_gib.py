import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from mevzuat.sources import gib
from mevzuat.sources.gib import GibKaynagi

# Gerçek API cevabı: listPublish, type=1 (Mevzuat), 01.10.2026
VERI = json.loads((Path(__file__).parent / "fixtures" / "gib_duyurular_mevzuat.json").read_text(encoding="utf-8"))


def test_parse_duyurular():
    duyurular = gib.parse_duyurular(VERI)
    assert len(duyurular) == 10
    ilk = duyurular[0]
    assert ilk.id == 19096
    assert ilk.baslik == "11822 Sayılı Cumhurbaşkanı Kararı Resmi Gazete’de Yayımlandı"
    assert ilk.yayin == datetime(2026, 10, 1, 7, 45)
    assert ilk.slug.startswith("19096_")


def _sahte_api(sayfalar: list[dict], istekler: list):
    def cevap(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        if request.url.path.endswith("/findBySlug"):
            return httpx.Response(200, json={"resultContainer": {"description": "<p>ÖTV tutarları</p><p>ikinci</p>"}})
        return httpx.Response(200, json=sayfalar[int(request.url.params["page"])])

    return httpx.Client(transport=httpx.MockTransport(cevap))


def _sayfa(icerik: list[dict], son: bool) -> dict:
    return {"resultContainer": {"content": icerik, "last": son}}


def test_tara_ilk_calisma_sadece_bugun():
    istekler = []
    with _sahte_api([VERI], istekler) as c:
        (adim,) = GibKaynagi("gib_mevzuat", "GİB Duyurusu", [1]).tara(c, None, date(2026, 10, 1), datetime(2026, 10, 1, 9))
    assert [k.dis_id for k in adim.kayitlar] == ["19096"]
    k = adim.kayitlar[0]
    assert k.url == "https://gib.gov.tr/duyuru-arsivi/guncel/" + gib.parse_duyurular(VERI)[0].slug
    assert k.kaynakca.startswith("GİB Duyurusu, 01.10.2026: 11822 Sayılı")
    assert adim.checkpoint == "2026-10-01T09:00:00"
    assert json.loads(istekler[0].content) == {"type": 1, "ilkodu": "UNIVERSAL"}
    assert istekler[0].method == "POST"


def test_tara_eski_duyuru_gorunce_sonraki_sayfaya_gecmez():
    icerik = VERI["resultContainer"]["content"]
    istekler = []
    with _sahte_api([_sayfa(icerik[:2], False), _sayfa(icerik[2:4], False), _sayfa(icerik[4:], True)], istekler) as c:
        # checkpoint 26.09 09:00, bir gün örtüşme → 25.09 09:00 sonrası: 01.10, 30.09 (sayfa 0), 25.09 (sayfa 1)
        (adim,) = GibKaynagi("g", "GİB", [1]).tara(c, "2026-09-26T09:00:00", date(2026, 10, 1), datetime(2026, 10, 1, 9))
    assert [k.yayin_tarihi for k in adim.kayitlar] == [date(2026, 10, 1), date(2026, 9, 30), date(2026, 9, 25)]
    assert len(istekler) == 2  # sayfa 1'de eski duyuru göründü, sayfa 2 istenmedi


def test_icerik_slugdan():
    istekler = []
    with _sahte_api([], istekler) as c:
        icerik = GibKaynagi("g", "GİB", [1]).icerik(c, "19096", "https://gib.gov.tr/duyuru-arsivi/guncel/19096_abc")
    assert icerik.metin == "ÖTV tutarları\nikinci"
    assert istekler[0].url.params["slug"] == "19096_abc"


def test_bilinmeyen_tur_hata():
    with pytest.raises(ValueError, match="turler"):
        GibKaynagi("g", "GİB", [9])

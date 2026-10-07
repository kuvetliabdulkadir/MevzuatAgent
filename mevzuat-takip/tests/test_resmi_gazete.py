from datetime import date
from pathlib import Path

import httpx
import pytest

from mevzuat.sources import resmi_gazete as rg

FIXTURE = Path(__file__).parent / "fixtures" / "rg_fihrist_2026-10-01.html"
TARIH = date(2026, 10, 1)


@pytest.fixture
def kayitlar():
    return rg.parse_fihrist(FIXTURE.read_text(encoding="utf-8"), TARIH)


def test_sayi_okunur(kayitlar):
    assert all(k.sayi == 33387 for k in kayitlar)


def test_kayit_sayilari(kayitlar):
    mevzuat = [k for k in kayitlar if not k.ilan_mi]
    ilanlar = [k for k in kayitlar if k.ilan_mi]
    assert len(mevzuat) == 18
    assert len(ilanlar) == 4


def test_hiyerarsi_ve_temizlik(kayitlar):
    ilk = kayitlar[0]
    assert ilk.bolum == "YÜRÜTME VE İDARE BÖLÜMÜ"
    assert ilk.alt_baslik == "CUMHURBAŞKANI KARARLARI"
    assert "Malvarlığının Dondurulması" in ilk.baslik
    assert not ilk.baslik.startswith("–")
    assert ilk.url.endswith("20261001-3-2.pdf")


def test_ozel_bosluk_normalize_edilir(kayitlar):
    # Fihristte "Karar Sayısı:&#8200;11823" gibi punctuation space var.
    assert all(" " not in k.baslik for k in kayitlar)


def test_alt_basliklar(kayitlar):
    teblig = [k for k in kayitlar if k.alt_baslik == "TEBLİĞ"]
    assert len(teblig) == 1
    assert teblig[0].url.endswith(".htm")


def test_mukerrer_duyuru_linki_kayit_sayilmaz():
    html = (FIXTURE.parent / "rg_fihrist_2026-07-03_mukerrer_duyurulu.html").read_text(encoding="utf-8")
    kayitlar = rg.parse_fihrist(html, date(2026, 7, 3))
    assert kayitlar
    assert all("/fihrist?" not in k.url for k in kayitlar)


def test_yayimlanmayan_gun_ayri_durum():
    html = (FIXTURE.parent / "rg_fihrist_2026-08-30_yayimlanmadi.html").read_text(encoding="utf-8")

    def handler(request):
        return httpx.Response(200, text=html)

    with _client(handler) as client:
        sonuc = rg.fetch_fihrist(client, date(2026, 8, 30))
    assert sonuc.durum is rg.Durum.YAYIMLANMADI
    assert sonuc.html is None


def test_yapi_degisirse_hata_verir():
    with pytest.raises(ValueError):
        rg.parse_fihrist("<html><body>bambaşka sayfa</body></html>", TARIH)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_302_henuz_yok():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://www.resmigazete.gov.tr/"})

    with _client(handler) as client:
        assert rg.fetch_fihrist(client, date(2026, 10, 5)).durum is rg.Durum.HENUZ_YOK


def test_mukerrerler_302de_durur(monkeypatch):
    monkeypatch.setattr(rg, "ISTEK_ARASI_BEKLEME", 0)
    html = FIXTURE.read_text(encoding="utf-8")
    istenenler = []

    def handler(request):
        istenenler.append(str(request.url))
        if "mukerrer=2" in str(request.url):
            return httpx.Response(302, headers={"Location": "/"})
        return httpx.Response(200, text=html)

    with _client(handler) as client:
        sonuc = rg.gunun_kayitlari(client, TARIH)

    assert sonuc.durum is rg.Durum.YAYIMLANDI
    assert len(istenenler) == 3
    assert len(sonuc.kayitlar) == 36
    assert {k.mukerrer for k in sonuc.kayitlar} == {0, 1}


def test_bayramda_mukerrer_yine_de_aranir(monkeypatch):
    monkeypatch.setattr(rg, "ISTEK_ARASI_BEKLEME", 0)
    bayram = (FIXTURE.parent / "rg_fihrist_2026-08-30_yayimlanmadi.html").read_text(encoding="utf-8")
    normal = FIXTURE.read_text(encoding="utf-8")

    def handler(request):
        url = str(request.url)
        if "mukerrer=1" in url:
            return httpx.Response(200, text=normal)
        if "mukerrer=" in url:
            return httpx.Response(302, headers={"Location": "/"})
        return httpx.Response(200, text=bayram)

    with _client(handler) as client:
        sonuc = rg.gunun_kayitlari(client, TARIH)

    assert sonuc.durum is rg.Durum.YAYIMLANDI
    assert {k.mukerrer for k in sonuc.kayitlar} == {1}


def test_bugun_henuz_cikmadiysa_bos_doner():
    def handler(request):
        return httpx.Response(302, headers={"Location": "/"})

    with _client(handler) as client:
        sonuc = rg.gunun_kayitlari(client, TARIH)

    assert sonuc.durum is rg.Durum.HENUZ_YOK
    assert sonuc.kayitlar == []

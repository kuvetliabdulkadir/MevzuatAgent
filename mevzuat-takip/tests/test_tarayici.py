from datetime import date, datetime
from pathlib import Path

import pytest

from mevzuat.sources import tarayici
from mevzuat.sources.tarayici import TarayiciKaynagi

FIXTURES = Path(__file__).parent / "fixtures"
# Tarayıcıda oluşmuş HTML (Playwright ile kaydedildi, 01.10.2026)
LISTE = (FIXTURES / "gib_liste_mevzuat_2026-10-01.html").read_text(encoding="utf-8")
DETAY = (FIXTURES / "gib_duyuru_19096.html").read_text(encoding="utf-8")

AYAR = dict(
    ad="gib_mevzuat",
    etiket="GİB Duyurusu",
    liste_url="https://gib.gov.tr/duyuru-arsivi/mevzuat",
    oge="div.MuiGrid-item",
    baslik="a[data-testid='link']",
    tarih="p[class*='dateParser']",
    icerik="div.cms-content",
)


def _ogeler():
    return tarayici.parse_liste(
        LISTE, AYAR["liste_url"], AYAR["oge"], AYAR["baslik"], AYAR["tarih"], "%d.%m.%Y"
    )


def test_parse_liste():
    ogeler = _ogeler()
    assert len(ogeler) >= 5
    ilk = ogeler[0]
    assert ilk.baslik == "11822 Sayılı Cumhurbaşkanı Kararı Resmi Gazete’de Yayımlandı"
    assert ilk.tarih == date(2026, 10, 1)
    assert ilk.url == "https://gib.gov.tr/duyuru-arsivi/mevzuat/19096_11822_sayili_cumhurbaskani_karari_resmi_gazetede_yayimlandi"
    assert len({o.url for o in ogeler}) == len(ogeler)
    assert [o.tarih for o in ogeler] == sorted((o.tarih for o in ogeler), reverse=True)


def test_parse_icerik():
    metin = tarayici.parse_icerik(DETAY, AYAR["icerik"])
    assert "Bazı Mallara Uygulanan Özel Tüketim Vergisi" in metin
    assert "33387" in metin
    assert "Duyuru Arşivi" not in metin  # sayfa menüsü içeriğe karışmıyor


def test_parse_icerik_alan_yoksa_hata():
    with pytest.raises(ValueError, match="İçerik alanı bulunamadı"):
        tarayici.parse_icerik("<html><body><p>x</p></body></html>", AYAR["icerik"])


def _tara(monkeypatch, html, checkpoint, bugun):
    monkeypatch.setattr(tarayici, "sayfa_html", lambda url, bekle: html)
    return list(TarayiciKaynagi(**AYAR).tara(None, checkpoint, bugun, datetime.combine(bugun, datetime.min.time())))


def test_tara_ilk_calisma_sadece_bugun(monkeypatch):
    (adim,) = _tara(monkeypatch, LISTE, None, date(2026, 10, 1))
    assert [k.yayin_tarihi for k in adim.kayitlar] == [date(2026, 10, 1)]
    assert adim.checkpoint == "2026-10-01"
    k = adim.kayitlar[0]
    assert k.dis_id == k.url
    assert k.kaynakca.startswith("GİB Duyurusu, 01.10.2026: 11822 Sayılı")


def test_tara_checkpointten_ortusmeyle(monkeypatch):
    (adim,) = _tara(monkeypatch, LISTE, "2026-10-01", date(2026, 10, 2))
    # checkpoint 01.10 → bir gün örtüşmeyle 30.09 dahil
    assert {k.yayin_tarihi for k in adim.kayitlar} == {date(2026, 10, 1), date(2026, 9, 30)}
    assert adim.bilgi == {}


def test_tara_liste_doluysa_uyari(monkeypatch):
    (adim,) = _tara(monkeypatch, LISTE, "2020-01-01", date(2026, 10, 2))
    assert len(adim.kayitlar) == len(_ogeler())
    assert "kaçmış olabilir" in adim.bilgi["uyari"]


def test_tara_hic_oge_okunamazsa_hata(monkeypatch):
    with pytest.raises(ValueError, match="seçicileri kontrol edin"):
        _tara(monkeypatch, "<html><body></body></html>", None, date(2026, 10, 1))


# TCMB basın duyuruları: liste JavaScript ile geliyor (düz HTML'de yok, RSS/API yok; kaynak bulucu "tarayıcı
# tipi gerekir" diyor). Chromium'da oluşmuş HTML Playwright ile kaydedildi (05.10.2026).
TCMB = dict(
    ad="tcmb_basin",
    etiket="TCMB Basın Duyurusu",
    liste_url="https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Duyurular/Basin",
    oge="div.block-collection-box",
    baslik="a.collection-title",
    tarih="div.collection-tag",
    icerik="div.tcmb-content",
)
TCMB_LISTE = (FIXTURES / "tcmb_basin_liste_2026-10-05.html").read_text(encoding="utf-8")
TCMB_DETAY = (FIXTURES / "tcmb_duyuru_2026-43.html").read_text(encoding="utf-8")


def test_tcmb_liste():
    ogeler = tarayici.parse_liste(TCMB_LISTE, TCMB["liste_url"], TCMB["oge"], TCMB["baslik"], TCMB["tarih"])
    assert len(ogeler) >= 10
    ilk = ogeler[0]
    assert ilk.baslik == "Makroihtiyati Çerçeveye İlişkin Basın Duyurusu (2026-43)"
    assert ilk.tarih == date(2026, 10, 1)  # "01/10/2026"
    assert ilk.url.endswith("/Duyurular/Basin/2026/DUY2026-43")
    # Kutudaki PDF simgesi linki başlık sayılmıyor; ama bazı duyuruların başlığı doğrudan PDF'e gidiyor (konuşmalar)
    assert sum(".pdf" in o.url for o in ogeler) == 3


def test_tcmb_icerik():
    metin = tarayici.parse_icerik(TCMB_DETAY, TCMB["icerik"])
    assert "Makroihtiyati Çerçeveye İlişkin Basın Duyurusu" in metin
    assert "makrofinansal istikrar" in metin


def test_tcmb_tara(monkeypatch):
    monkeypatch.setattr(tarayici, "sayfa_html", lambda url, bekle: TCMB_LISTE)
    bugun = date(2026, 10, 5)
    (adim,) = TarayiciKaynagi(**TCMB).tara(None, "2026-09-15", bugun, datetime.combine(bugun, datetime.min.time()))
    assert [k.baslik[:30] for k in adim.kayitlar] == [
        "Makroihtiyati Çerçeveye İlişki", "Para Politikası Kurulu Toplant", "Türk Lirası Likidite Yönetimin",
        "İİT-İSEDAK Merkez Bankaları Fo"]  # checkpoint 15.09 → bir gün örtüşmeyle 14.09 dahil
    assert adim.kayitlar[0].kaynakca.startswith("TCMB Basın Duyurusu, 01.10.2026: Makroihtiyati")


def test_pdf_duyuru_tarayicisiz_indirilir(monkeypatch):
    """Başlığı doğrudan PDF'e giden duyuru: Chromium açılmaz, dosya httpx ile iner ve PDF okuyucusundan geçer."""
    from mevzuat import kaynak_yonetimi as ky
    from mevzuat.sources import liste

    monkeypatch.setattr(ky, "_dns", lambda host: ["93.184.216.34"])  # adres kontrolü internete çıkmasın

    def tarayici_acilmamali(url, bekle):
        raise AssertionError("PDF için tarayıcı açıldı")

    monkeypatch.setattr(tarayici, "sayfa_html", tarayici_acilmamali)
    okunan = {}
    monkeypatch.setattr(liste, "belge_icerigi", lambda veri, ct: okunan.update(veri=veri, ct=ct) or liste.Icerik("pdf metni"))

    class Cevap:
        content, headers = b"%PDF-1.4 ...", {"content-type": "application/pdf"}

        def raise_for_status(self):
            pass

    class Istemci:
        def get(self, url):
            okunan["url"] = url
            return Cevap()

    url = "https://www.tcmb.gov.tr/wps/wcm/connect/241f923c/konusma.pdf?MOD=AJPERES"
    icerik = TarayiciKaynagi(**TCMB).icerik(Istemci(), url, url)
    assert icerik.metin == "pdf metni"
    assert okunan == {"url": url, "veri": b"%PDF-1.4 ...", "ct": "application/pdf"}


def test_ic_aga_giden_duyuru_tarayicida_acilmaz(monkeypatch):
    """Ele geçirilmiş bir liste sayfası tarayıcıyı şirket iç ağına yönlendiremez."""
    from mevzuat import kaynak_yonetimi as ky

    monkeypatch.setattr(ky, "_dns", lambda host: [host] if host[0].isdigit() else ["93.184.216.34"])
    acilan = []
    monkeypatch.setattr(tarayici, "sayfa_html", lambda url, bekle: acilan.append(url) or "<div class='tcmb-content'>x</div>")
    kaynak = TarayiciKaynagi(**TCMB)
    with pytest.raises(ValueError, match="İç ağ"):
        kaynak.icerik(None, "x", "http://10.0.0.5/gizli")
    with pytest.raises(ValueError, match="İç ağ"):
        kaynak.icerik(None, "x", "http://169.254.169.254/latest/meta-data/x.pdf")
    assert acilan == []
    assert kaynak.icerik(None, "x", "https://www.tcmb.gov.tr/duyuru").metin == "x"

"""Rapora giren belgenin tabloları/görselleri, PDF kopyası, mailde gömülü gösterim ve arşivde "rapora giren belgeler"."""
from datetime import date, datetime
from email import message_from_bytes
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import belge_gorselleri as bg, kayit_belgesi, rapor
from mevzuat.db import Calisma, Kayit, KayitBelgesi, KayitGorseli, Rapor, init_db
from mevzuat.mail import mesaj_olustur
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

FIXTURES = Path(__file__).parent / "fixtures"
PAROLA = "dogru-parola-123"


def _sayfa(cizgiler: list[int], genislik=2000, yukseklik=2800) -> Image.Image:
    resim = Image.new("RGB", (genislik, yukseklik), "white")
    c = ImageDraw.Draw(resim)
    for y in cizgiler:
        c.line([(200, y), (1800, y)], fill="black", width=3)
    # Yazı satırları (çizgi sayılmamalı): kısa koyu parçalar.
    for y in range(100, 2700, 60):
        for x in range(200, 1700, 90):
            c.rectangle([x, y, x + 50, y + 20], fill="black")
    return resim


def test_cizgili_tablo_bulunur_tek_cizgi_tablo_degil():
    # 4 yatay çizgi (3 satırlık tablo) + uzakta tek bir başlık çizgisi.
    kutular = bg.cizgili_tablolar(_sayfa([1000, 1060, 1120, 1180, 2500]))
    assert len(kutular) == 1
    sol, ust, sag, alt = kutular[0]
    assert abs(ust - 1000) <= 3 and abs(alt - 1180) <= 3 and sol <= 220 and sag >= 1780
    # Sayfa çerçevesi (üst ve alt kenar) tablo sayılmaz.
    assert bg.cizgili_tablolar(_sayfa([50, 1400, 2750])) == []


def test_taranmis_belgede_tablo_goruntusu(monkeypatch):
    # RG ÖTV kararı, gövdesi resim: iki tutar tablosu kesilip görüntü olur (Tesseract gerekmez, bozuk satır yok).
    veri = (FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes()
    gorseller = bg.cikar(veri, {0: "MADDE 1- temiz okunmuş metin"})
    assert [g.tur for g in gorseller] == ["goruntu", "goruntu"]
    assert all(g.sayfa == 1 and g.png.startswith(b"\x89PNG") and g.genislik <= bg.GORUNTU_GENISLIGI for g in gorseller)
    assert all(len(g.png) < 200_000 for g in gorseller)


def test_bozuk_satir_bolgesi_kenar_satirlariyla():
    S = bg._Satir
    satirlar = [
        S("Tesiste aşağıdaki petroller* depolanacaktır.", 90, 270, 1500, 310),
        S("HAMPETROr..,....,..,.,.,.,.,... | |", 86, 312, 1500, 380),
        S(".YakitNafta.'*<<<cC (0", 86, 410, 1500, 480),
        S("GEZ", 86, 485, 200, 500),
        S("Biodizel Türleri ,*|*|*|*<(«(«(«c«c«c«coc0c 00", 86, 510, 1500, 580),
        S("(*) Depolanacak petroller (x) ile işaretlenecektir.", 100, 928, 1500, 966),
    ]
    # Bozuk iki satır ve aradaki "GEZ" tek bölge; üstteki tablo başlık satırı dahil, cümleler dışarıda.
    assert bg.bozuk_bolgeler(satirlar) == [(1, 4)]


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(e)
    with Session(e) as s:
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
        s.add(Calisma(id=1, baslangic=datetime(2026, 11, 28, 7), durum="BASARILI", ozet={}))
        s.add(Rapor(id=38, olusturuldu=datetime(2026, 11, 28, 8), durum="ONAY_BEKLIYOR", konu="Rapor", kayit_sayisi=1))
        s.commit()
    return e


def _kayit(s: Session, **alanlar) -> Kayit:
    v = dict(kaynak="resmi_gazete", dis_id="https://www.resmigazete.gov.tr/eskiler/2026/11/20261128-20.pdf",
             yayin_tarihi=date(2026, 11, 28), baslik="Enerji Piyasası Düzenleme Kurulunun 15074 Sayılı Kararı",
             tur="KURUL KARARI", url="https://www.resmigazete.gov.tr/eskiler/2026/11/20261128-20.pdf",
             kaynakca="Resmî Gazete, 28.11.2026, Sayı: 33444, KURUL KARARI: Enerji…", ilgili=True,
             eslesmeler={"Araç": ["motorin (içerikte)"]}, is_kollari=["Oto kiralama"],
             icerik="MADDE 1- İşaretli petrollere depolama hizmeti. KIYMETLİ maden değil, motorin türleri.",
             icerik_durumu="OCR_ILE_OKUNDU", ilk_gorulme=datetime(2026, 11, 28, 7), calisma_id=1, rapor_id=38)
    v.update(alanlar)
    k = Kayit(**v)
    s.add(k)
    s.flush()
    return k


def test_sakla_mailde_gomulu_goruntu_ve_tablo(engine):
    veri = (FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes()
    with Session(engine) as s:
        k = _kayit(s)
        assert kayit_belgesi.sakla(s, k, veri, {0: "temiz"}) == 2
        # Yazılı PDF tablosu gibi satır/hücreli bir tablo da (birleşik hücreyle).
        s.add(KayitGorseli(kayit_id=k.id, sira=3, sayfa=2, tur="tablo", aciklama="s. 2 tablo 1",
                           tablo=[["Petrol", "Fiyat", None], ["Benzin", "6,00 TL", "M3"]]))
        s.commit()
        assert s.get(KayitBelgesi, k.id).veri == veri
        # Yeniden saklama eskileri siler, çoğaltmaz.
        kayit_belgesi.sakla(s, k, veri, {0: "temiz"})
        s.add(KayitGorseli(kayit_id=k.id, sira=3, sayfa=2, tur="tablo", aciklama="s. 2 tablo 1",
                           tablo=[["Petrol", "Fiyat", None], ["Benzin", "6,00 TL", "M3"]]))
        s.commit()
        assert len(kayit_belgesi.gorseller(s, k.id)) == 3

        mail = rapor.rapor_olustur([k], date(2026, 11, 28), ek_ekle=False, session=s)
    assert len(mail.gomulu) == 2 and all(g.cid and g.mime == "image/png" for g in mail.gomulu)
    for g in mail.gomulu:
        assert f'src="cid:{g.cid}"' in mail.html
    assert '<td colspan="2"' in mail.html and ">6,00 TL</td>" in mail.html
    assert "Belgedeki tablo ve görseller" in mail.html and "3 tablo/görsel" in mail.metin
    mail.alicilar = ["a@firma.com"]
    msg = message_from_bytes(mesaj_olustur(mail, "rapor@firma.com").as_bytes())
    resimler = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert len(resimler) == 2 and {p["Content-ID"] for p in resimler} == {f"<{g.cid}>" for g in mail.gomulu}
    assert any(p.get_content_type() == "multipart/related" for p in msg.walk())


def test_mail_goruntu_siniri(engine, monkeypatch):
    monkeypatch.setattr(rapor, "MAIL_GORUNTU_SINIRI", 10)
    with Session(engine) as s:
        k = _kayit(s)
        s.add(KayitGorseli(kayit_id=k.id, sira=1, sayfa=1, tur="goruntu", aciklama="s. 1 tablo", png=b"\x89PNG" + b"0" * 50))
        s.commit()
        mail = rapor.rapor_olustur([k], date(2026, 11, 28), ek_ekle=False, session=s)
    assert mail.gomulu == [] and "maile sığmadı" in mail.html


def _post(c, yol, veri=None):
    return c.post(yol, json=veri or {}, headers={"X-CSRF-Token": c.get("/panel/api/oturum").json()["csrf"]})


def test_arsivde_rapora_giren_belgeler(engine):
    veri = (FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes()
    with Session(engine) as s:
        k = _kayit(s)
        kayit_belgesi.sakla(s, k, veri, {0: "temiz"})
        _kayit(s, dis_id="x2", url="https://ornek.gov.tr/x2", baslik="Başka Duyuru", icerik="altın ve gümüş",
               icerik_durumu="TAMAM", yayin_tarihi=date(2026, 11, 27))
        _kayit(s, dis_id="x3", url="https://ornek.gov.tr/x3", baslik="Raporlanmamış", rapor_id=None)
        s.commit()
        kayit_id = k.id
    c = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    assert c.get("/panel/api/arsiv/rapor-belgeleri").status_code == 401
    _post(c, "/panel/api/giris", {"eposta": "onay@firma.com", "parola": PAROLA})
    hepsi = c.get("/panel/api/arsiv/rapor-belgeleri").json()
    assert hepsi["toplam"] == 2 and [r["baslik"] for r in hepsi["satirlar"]][1] == "Başka Duyuru"
    assert hepsi["satirlar"][0]["gorsel_sayisi"] == 2 and hepsi["satirlar"][0]["belge_var"]
    # Türkçe büyük/küçük harf (KIYMETLİ ↔ kıymetli), bütün kelimeler, metinde bulunca vurgulu parça.
    r = c.get("/panel/api/arsiv/rapor-belgeleri", params={"q": "kıymetli motorin"}).json()
    assert r["toplam"] == 1 and "<mark>KIYMETLİ</mark>" in r["satirlar"][0]["parca"]
    assert c.get("/panel/api/arsiv/rapor-belgeleri", params={"q": "15074"}).json()["toplam"] == 1
    assert c.get("/panel/api/arsiv/rapor-belgeleri", params={"q": "raporlanmamış"}).json()["toplam"] == 0
    d = c.get(f"/panel/api/arsiv/rapor-belgeleri/{kayit_id}").json()
    assert d["rapor_id"] == 38 and d["belge_var"] and len(d["gorseller"]) == 2 and d["metin"].startswith("MADDE 1")
    g = c.get(d["gorseller"][0]["url"])
    assert g.status_code == 200 and g.headers["content-type"] == "image/png"
    p = c.get(f"/panel/api/kayit-belgesi/{kayit_id}")
    assert p.status_code == 200 and p.content == veri and "frame-ancestors 'self'" in p.headers["content-security-policy"]
    # Rapor penceresi de görselleri ve belge kopyasını verir.
    kalem = next(x for x in c.get("/panel/api/raporlar/38").json()["kalemler"] if x["id"] == kayit_id)
    assert len(kalem["gorseller"]) == 2 and kalem["belge_var"]
    assert c.get("/panel/api/arsiv/rapor-belgeleri/999").status_code == 404


def test_pipeline_ilgili_kaydin_belgesini_saklar(engine, monkeypatch):
    from mevzuat import pipeline
    from mevzuat.icerik import Icerik

    veri = (FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes()

    class Kaynak:
        def icerik(self, client, dis_id, url):
            return Icerik("MADDE 1- motorin türleri", ocr_ile=True, belge=veri, ocr_sayfalari={0: "temiz"})

    with Session(engine) as s:
        k = _kayit(s, icerik=None, icerik_durumu="BEKLIYOR", rapor_id=None)
        s.commit()
        monkeypatch.setattr(pipeline, "icerikle_puanla", lambda kayit, *a, **kw: None)
        monkeypatch.setattr(pipeline, "ISTEK_ARASI_BEKLEME", 0)
        pipeline._icerik_cek(s, None, {"resmi_gazete": Kaynak()}, [])
        assert s.get(KayitBelgesi, k.id) is not None
        assert len(s.scalars(select(KayitGorseli).where(KayitGorseli.kayit_id == k.id)).all()) == 2

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from mevzuat import pipeline
from mevzuat.db import Calisma, Kayit, alembic_config, init_db, migrate
from mevzuat.filtre import konulari_yukle
from mevzuat.kaynak_yonetimi import ayarlari_temizle
from mevzuat.sources import kaynak_olustur
from mevzuat.sources import mevzuat_gov as mg
from mevzuat.sources.base import KayitTaslagi

BUGUN = date(2026, 10, 8)
SIMDI = datetime(2026, 10, 8, 7)
ANA_SAYFA = '<form><input name="antiforgerytoken" type="hidden" value="gizli-anahtar" /></form>'


@pytest.fixture(autouse=True)
def beklemesiz(monkeypatch):
    monkeypatch.setattr(mg, "ISTEK_ARASI_BEKLEME", 0)


# Servisin gerçek cevabındaki alanlar (08.10.2026), iki tarih biçimiyle.
def _satir(no: int, tarih: str, ad: str, tur: int = 7, url: str | None = None) -> dict:
    return {"mevzuatNo": str(no), "mevAdi": ad, "resmiGazeteTarihi": tarih, "resmiGazeteSayisi": "33393",
            "mevzuatTertip": "5", "mevzuatTur": tur,
            "url": url or f"mevzuat?MevzuatNo={no}&MevzuatTur={tur}&MevzuatTertip=5"}


class SahteSite:
    """Ana sayfa güvenlik anahtarını verir, arama servisi türe göre sayfalı liste döner."""

    def __init__(self, listeler: dict[str, list[dict]]):
        self.listeler = listeler
        self.istekler: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.istekler.append(request)
        if request.url.path == "/":
            return httpx.Response(200, text=ANA_SAYFA)
        if request.url.path.endswith("MevzuatDatatable"):
            govde = json.loads(request.content)
            liste = self.listeler.get(govde["parameters"]["MevzuatTur"], [])
            parca = liste[govde["start"]:govde["start"] + govde["length"]]
            return httpx.Response(200, json={"draw": 1, "recordsTotal": len(liste), "data": parca})
        if "MevzuatFihristDetayIframe" in str(request.url):
            return httpx.Response(200, text="<html><body><p>MADDE 1- Yönetmeliğin metni.</p></body></html>")
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def servis_istekleri(self) -> list[dict]:
        return [json.loads(r.content) for r in self.istekler if r.url.path.endswith("MevzuatDatatable")]


def test_parse_iki_tarih_bicimi_ve_ad_temizligi():
    veri = {"data": [
        _satir(1, "07.10.2026", "KUYUM <span style='background-color:yellow'>TİCARETİ</span>\r\nYÖNETMELİĞİ"),
        _satir(11, "19/09/2026", "Genelge", 22, "https://www.mevzuat.gov.tr/MevzuatMetin/CumhurbaskanligiGenelgeleri/20260919-11.pdf"),
        _satir(2, "..", "Tarihsiz"),
    ]}
    a, b = mg.parse_mevzuatlar(veri)
    assert a == mg.Mevzuat("KUYUM TİCARETİ YÖNETMELİĞİ", date(2026, 10, 7), 33393,
                           "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=1&MevzuatTur=7&MevzuatTertip=5")
    assert b.rg_tarihi == date(2026, 9, 19) and b.url.endswith("20260919-11.pdf")
    with pytest.raises(ValueError, match="beklenmeyen cevap"):
        mg.parse_mevzuatlar({"Success": False, "Message": "Sistemsel hata oluştu"})


def test_tara_son_taramadan_beri_olanlar_ve_anahtar_gonderilir():
    site = SahteSite({
        "KurumVeKurulusYonetmeligi": [_satir(3, "07.10.2026", "Kuyum Yönetmeliği"), _satir(2, "02.10.2026", "Eski"),
                                      _satir(1, "20.09.2026", "Çok eski")],
        "Teblig": [_satir(9, "06.10.2026", "Altın Tebliği", 9)],
    })
    kaynak = kaynak_olustur("mevzuat_gov_yeni", "mevzuat_gov", {"turler": ["KurumVeKurulusYonetmeligi", "Teblig"]})
    with site.client() as c:
        (adim,) = list(kaynak.tara(c, "2026-10-06", BUGUN, SIMDI))
    # Örtüşme 5 gün, 06.10 checkpoint'inden 01.10'a kadar geriye bakılır.
    assert [k.baslik for k in adim.kayitlar] == ["Kuyum Yönetmeliği", "Eski", "Altın Tebliği"]
    assert adim.checkpoint == "2026-10-08"
    kuyum = adim.kayitlar[0]
    assert kuyum.tur == "Yönetmelik" and kuyum.sayi == 33393 and kuyum.dis_id == kuyum.url
    assert kuyum.kaynakca == "Mevzuat Bilgi Sistemi, Resmî Gazete 07.10.2026, Sayı: 33393, Yönetmelik: Kuyum Yönetmeliği"
    servis = [r for r in site.istekler if r.url.path.endswith("MevzuatDatatable")]
    assert all(r.headers["antiforgerytoken"] == "gizli-anahtar" for r in servis)
    parametreler = site.servis_istekleri()[0]["parameters"]
    assert parametreler["AranacakIfade"] == "" and parametreler["BaslangicTarihi"] == "2026"


def test_eski_kayit_gorunce_sonraki_sayfaya_gecilmez(monkeypatch):
    monkeypatch.setattr(mg, "SAYFA_BOYU", 2)
    yeni = [_satir(i, "07.10.2026", f"Yeni {i}") for i in range(3)]
    site = SahteSite({"Teblig": yeni + [_satir(9, "01.01.2026", "Eski")] + [_satir(8, "07.10.2026", "Gelmemeli")]})
    with site.client() as c:
        sonuc = mg.mevzuatlar(c, "anahtar", "Teblig", date(2026, 10, 3), BUGUN)
    assert [m.ad for m in sonuc] == ["Yeni 0", "Yeni 1", "Yeni 2"]
    assert [g["start"] for g in site.servis_istekleri()] == [0, 2]


def test_icerik_html_iframeden_pdf_dogrudan():
    site = SahteSite({})
    kaynak = kaynak_olustur("mevzuat_gov_yeni", "mevzuat_gov", {})
    with site.client() as c:
        html = kaynak.icerik(c, "x", "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=46325&MevzuatTur=7&MevzuatTertip=5")
    assert "Yönetmeliğin metni" in html.metin
    assert "MevzuatTur=7&MevzuatNo=46325&MevzuatTertip=5" in str(site.istekler[-1].url)


def test_anahtar_yoksa_anlasilir_hata():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>"))) as c:
        with pytest.raises(ValueError, match="güvenlik anahtarı"):
            mg.anahtar_al(c)


def test_bilinmeyen_tur_hata():
    with pytest.raises(ValueError, match="turler"):
        kaynak_olustur("mevzuat_gov_yeni", "mevzuat_gov", {"turler": ["Olmayan"]})


def test_panel_tur_secimini_yazi_olarak_saklar():
    ayarlar = ayarlari_temizle("mevzuat_gov", {"turler": ["Teblig", "Kanun"], "ortusme_gun": 3})
    assert ayarlar == {"turler": ["Kanun", "Teblig"], "ortusme_gun": 3}  # seçenek sırasıyla
    assert ayarlari_temizle("gib", {"turler": ["1"]})["turler"] == [1]  # GİB yine sayı
    with pytest.raises(ValueError, match="geçersiz seçim"):
        ayarlari_temizle("mevzuat_gov", {"turler": ["Olmayan"]})


def test_resmi_gazetede_yakalanan_belge_tekrar_ilgili_olmaz():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        calisma = Calisma(id=1, baslangic=SIMDI, durum="CALISIYOR", ozet={})
        s.add(calisma)
        s.add(Kayit(kaynak="resmi_gazete", dis_id="rg1", yayin_tarihi=date(2026, 10, 1), sayi=33387,
                    baslik="Bazı Mallara Uygulanan Özel Tüketim Vergisi Tutarlarının Yeniden Belirlenmesi Hakkında Karar",
                    tur="", bolum="", mukerrer=0, url="u", kaynakca="k", ilgili=True, eslesmeler={}, is_kollari=[],
                    icerik_durumu="TAMAM", ilk_gorulme=SIMDI, calisma_id=1))
        s.commit()
        konular = konulari_yukle(Path(__file__).parents[1] / "config" / "konular.toml")
        kaynak = kaynak_olustur("mevzuat_gov_yeni", "mevzuat_gov", {})

        def taslak(baslik: str, dis_id: str, sayi: int = 33387) -> KayitTaslagi:
            return KayitTaslagi(dis_id=dis_id, yayin_tarihi=date(2026, 10, 1), baslik=baslik, url=dis_id, kaynakca="k",
                                sayi=sayi)

        # Aynı belge büyük harfle ve sonunda karar sayısıyla, ilgisiz olarak tutulur, rapora girmez.
        assert pipeline._ekle(s, kaynak, taslak("BAZI MALLARA UYGULANAN ÖZEL TÜKETİM VERGİSİ TUTARLARININ YENİDEN "
                                                "BELİRLENMESİ HAKKINDA KARAR (KARAR SAYISI: 11822)", "m1"), konular, calisma)
        # Resmî Gazete'de olmayan belge ilgili olur.
        assert pipeline._ekle(s, kaynak, taslak("Katma Değer Vergisi Genel Uygulama Tebliği", "m2"), konular, calisma)
        s.commit()
        kayitlar = {k.dis_id: k for k in s.query(Kayit).filter(Kayit.kaynak == "mevzuat_gov_yeni")}
        assert not kayitlar["m1"].ilgili and kayitlar["m1"].icerik_durumu == "GEREKSIZ"
        assert kayitlar["m2"].ilgili and "Vergi" in kayitlar["m2"].eslesmeler


def test_migration_kurulu_sisteme_kaynagi_ekler(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'eski.db'}")
    command.upgrade(alembic_config(engine.url.render_as_string(hide_password=False)), "0016")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO kaynaklar (ad, etiket, tip, ayarlar, varsayilan_konular, sira, aktif, kaldirildi,"
                          " surum, guncelleme) VALUES ('resmi_gazete', 'Resmî Gazete', 'resmi_gazete', '{}', '[]', 0, 1, 0,"
                          " 1, '2026-10-01')"))
    migrate(engine)
    with engine.connect() as conn:
        satirlar = conn.execute(text("SELECT ad, tip, sira, ayarlar FROM kaynaklar ORDER BY sira")).all()
    assert [s[:3] for s in satirlar] == [("resmi_gazete", "resmi_gazete", 0), ("mevzuat_gov_yeni", "mevzuat_gov", 1)]
    assert "Teblig" in json.loads(satirlar[1][3])["turler"]


def test_migration_bos_kurulumda_bir_sey_eklemez(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'yeni.db'}")
    migrate(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM kaynaklar")).scalar() == 0  # toml'dan tohumlanacak

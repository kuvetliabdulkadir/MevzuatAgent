"""Mevzuat arama ve okuma adresleri (/api/v1): dış sistemlerin kullandığı, sürümlü, sadece okuma yapan adresler."""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import rapor
from mevzuat.db import AliciGrubu, Calisma, KaynakTanimi, Kayit, KonuTanimi, init_db
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import api_anahtari_uret, kullanici_ekle

SIMDI = datetime(2026, 10, 1, 7)


class Posta:
    def gonder(self, mail):
        pass


def _kayit(dis_id, baslik, gun, eslesmeler, is_kollari, kaynak="resmi_gazete", **ek) -> Kayit:
    return Kayit(kaynak=kaynak, dis_id=dis_id, yayin_tarihi=date(2026, 10, gun), baslik=baslik, tur="TEBLİĞLER",
                 bolum="", mukerrer=0, url=f"https://www.resmigazete.gov.tr/{dis_id}.htm",
                 kaynakca=f"Resmî Gazete, {gun:02d}.10.2026, TEBLİĞLER: {baslik}", ilgili=bool(eslesmeler),
                 eslesmeler=eslesmeler, is_kollari=is_kollari, icerik_durumu="TAMAM", ilk_gorulme=SIMDI,
                 calisma_id=1, **ek)


@pytest.fixture
def ortam():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        admin = kullanici_ekle(s, "admin@firma.com", "A", "admin", "dogru-parola-123")
        kullanici_ekle(s, "onay@firma.com", "O", "onaylayici", "dogru-parola-123")
        s.add(Calisma(id=1, baslangic=SIMDI, durum="BASARILI", ozet={}))
        s.add_all([
            KaynakTanimi(ad="resmi_gazete", etiket="Resmî Gazete", tip="resmi_gazete", ayarlar={}, varsayilan_konular=[],
                         sira=0, aktif=True, kaldirildi=False, surum=1, guncelleme=SIMDI),
            KaynakTanimi(ad="masak", etiket="MASAK Duyurusu", tip="wordpress", ayarlar={}, varsayilan_konular=[],
                         sira=1, aktif=True, kaldirildi=False, surum=1, guncelleme=SIMDI),
            KaynakTanimi(ad="eski", etiket="Eski", tip="html", ayarlar={}, varsayilan_konular=[],
                         sira=2, aktif=False, kaldirildi=True, surum=1, guncelleme=SIMDI),
            KonuTanimi(ad="Vergi", is_kollari=["Ortak"], kelimeler=["özel tüketim vergisi"], haric=[], dislanan=[],
                       aciklama="Fiyatlara yansır.", aktif=True, surum=1, guncelleme=SIMDI),
            KonuTanimi(ad="Kıymetli madenler", is_kollari=["Kuyum"], kelimeler=["altın"], haric=[], dislanan=[],
                       aktif=True, surum=1, guncelleme=SIMDI),
            KonuTanimi(ad="Pasif konu", is_kollari=["Ortak"], kelimeler=["x"], haric=[], dislanan=[], aktif=False,
                       surum=1, guncelleme=SIMDI),
        ])
        s.add_all([
            _kayit("k1", "İTHALATTA ÖZEL TÜKETİM VERGİSİ Tebliği", 1, {"Vergi": ["özel tüketim vergisi"]}, ["Ortak"],
                   icerik="MADDE 1- (1) Özel tüketim vergisi tutarları yeniden belirlenmiştir."),
            _kayit("k2", "Altın ve Kıymetli Maden Tebliği", 3, {"Kıymetli madenler": ["altın"]}, ["Kuyum"]),
            _kayit("k3", "Şüpheli İşlem Bildirimi Duyurusu", 5, {"Kıymetli madenler": ["altın"]}, ["Kuyum"], kaynak="masak"),
            _kayit("k4", "Alakasız Yönetmelik", 7, {}, []),
        ])
        s.add(AliciGrubu(ad="Herkes", is_kollari=["Kuyum", "Ortak"], adresler=["gizli-adres@firma.com"], aktif=True,
                         guncellendi=SIMDI))
        s.commit()
        r = rapor.onaya_sun(s, Posta(), ["onay@firma.com"], bugun=date(2026, 10, 8))
        idler = {k.dis_id: k.id for k in s.query(Kayit)}
        rapor.karar_ver(s, r, 2, onay=True, dahil_idler={idler["k1"], idler["k2"]}, notu="Vergiye dikkat.")
        _, anahtar = api_anahtari_uret(s, "Portal", "onaylayici", None, admin)
        s.commit()
        rapor_id = r.id
    client = TestClient(uygulama_olustur(engine, Posta(), [], "t" * 40, ek_ekle=False, arayuz=None))
    client.headers["Authorization"] = f"Bearer {anahtar}"
    return client, idler, rapor_id


def test_anahtarsiz_401(ortam):
    client, *_ = ortam
    cevap = TestClient(client.app).get("/api/v1/mevzuat")
    assert cevap.status_code == 401 and "detail" in cevap.json()


def test_arama_varsayilan_ilgililer_yeniden_eskiye(ortam):
    client, *_ = ortam
    veri = client.get("/api/v1/mevzuat").json()
    assert veri["toplam"] == 3 and [k["baslik"][:5] for k in veri["kayitlar"]] == ["Şüphe", "Altın", "İTHAL"]
    ilk = veri["kayitlar"][0]
    assert ilk["kaynak"] == "masak" and ilk["kaynak_adi"] == "MASAK Duyurusu" and ilk["konular"] == ["Kıymetli madenler"]
    assert client.get("/api/v1/mevzuat?sadece_ilgili=false").json()["toplam"] == 4


@pytest.mark.parametrize("sorgu, beklenen", [
    ("q=ithalatta", ["k1"]),        # büyük İ küçük i ile bulunur
    ("q=ŞÜPHELİ", ["k3"]),
    ("konu=Vergi", ["k1"]),
    ("is_kolu=Kuyum", ["k3", "k2"]),
    ("kaynak=masak", ["k3"]),
    ("baslangic=2026-10-02&bitis=2026-10-04", ["k2"]),
    ("q=yok-boyle-bir-sey", []),
])
def test_arama_suzgecleri(ortam, sorgu, beklenen):
    client, idler, _ = ortam
    numara = {v: k for k, v in idler.items()}
    assert [numara[k["id"]] for k in client.get(f"/api/v1/mevzuat?{sorgu}").json()["kayitlar"]] == beklenen


def test_sayfalama_ve_hatali_parametre(ortam):
    client, *_ = ortam
    ikinci = client.get("/api/v1/mevzuat?adet=2&sayfa=2").json()
    assert ikinci["toplam"] == 3 and len(ikinci["kayitlar"]) == 1 and ikinci["sayfa"] == 2
    for hatali in ("adet=101", "sayfa=0", "baslangic=01.10.2026"):
        assert client.get(f"/api/v1/mevzuat?{hatali}").status_code == 422


def test_detay_ozet_metin_ve_rapor(ortam):
    client, idler, rapor_id = ortam
    d = client.get(f"/api/v1/mevzuat/{idler['k1']}").json()
    assert d["ozet"] == "Özel tüketim vergisi tutarları yeniden belirlenmiştir."
    assert d["eslesmeler"] == {"Vergi": ["özel tüketim vergisi"]} and d["metin_durumu"] == "TAMAM"
    assert d["rapor"] == {"id": rapor_id, "durum": "ONAYLANDI"} and d["degisiklikler"] is None
    assert client.get("/api/v1/mevzuat/99999").status_code == 404


def test_konular_ve_kaynaklar_sadece_aktifler(ortam):
    client, *_ = ortam
    assert [k["ad"] for k in client.get("/api/v1/konular").json()] == ["Kıymetli madenler", "Vergi"]
    assert client.get("/api/v1/konular").json()[1]["aciklama"] == "Fiyatlara yansır."
    assert [k["ad"] for k in client.get("/api/v1/kaynaklar").json()] == ["resmi_gazete", "masak"]


def test_raporlar_kalemler_ve_eposta_sizmaz(ortam):
    client, idler, rapor_id = ortam
    liste = client.get("/api/v1/raporlar").json()
    assert liste["toplam"] == 1 and liste["raporlar"][0]["durum"] == "ONAYLANDI"
    assert client.get("/api/v1/raporlar?durum=REDDEDILDI").json()["toplam"] == 0
    detay = client.get(f"/api/v1/raporlar/{rapor_id}")
    gonderildi = {k["id"]: k["gonderildi"] for k in detay.json()["kalemler"]}
    assert gonderildi == {idler["k1"]: True, idler["k2"]: True, idler["k3"]: False}  # k3 dağıtımdan çıkarıldı
    assert detay.json()["karar_notu"] == "Vergiye dikkat." and "gizli-adres" not in detay.text
    assert client.get("/api/v1/raporlar/999").status_code == 404
    assert client.get("/api/v1/raporlar?durum=BILINMEYEN").status_code == 422


def test_x_api_anahtari_basligi_da_calisir(ortam):
    client, *_ = ortam
    anahtar = client.headers["Authorization"].split()[1]
    dis = TestClient(client.app)
    assert dis.get("/api/v1/konular", headers={"X-API-Anahtari": anahtar}).status_code == 200
    assert dis.get("/api/v1/konular", headers={"Authorization": "Bearer mvz_yanlis"}).status_code == 401

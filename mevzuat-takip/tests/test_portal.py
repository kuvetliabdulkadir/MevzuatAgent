"""Mevzuat arama ve okuma adresleri (/api/v1): dış sistemlerin kullandığı, sürümlü, sadece okuma yapan adresler."""

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import kaynak_bulucu, rapor
from mevzuat import kaynak_yonetimi as ky
from mevzuat import zamanlama as zm
from mevzuat.db import AliciGrubu, Calisma, Denetim, KaynakTanimi, Kayit, KonuTanimi, Kullanici, init_db
from mevzuat.http import make_client
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
    client.app.state.engine = engine
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


# --- yazma adresleri: panelin aynı işlemleri, sabit cevap biçimiyle

def _anahtar(client, rol: str) -> dict:
    with Session(client.app.state.engine) as s:
        admin = s.scalars(select(Kullanici).where(Kullanici.rol == "admin")).one()
        _, anahtar = api_anahtari_uret(s, f"Deneme {rol}", rol, None, admin)
        s.commit()
    return {"Authorization": f"Bearer {anahtar}"}


def _yeni_rapor(client) -> tuple[int, int]:
    with Session(client.app.state.engine) as s:
        k = _kayit("k5", "Yeni Altın Tebliği", 8, {"Kıymetli madenler": ["altın"]}, ["Kuyum"])
        s.add(k)
        s.commit()
        r = rapor.onaya_sun(s, Posta(), ["onay@firma.com"], bugun=date(2026, 10, 9))
        return r.id, k.id


def test_v1_karar_onay_ret_ve_gorev_ayriligi(ortam):
    client, *_ = ortam
    rapor_id, kalem = _yeni_rapor(client)
    adres = f"/api/v1/raporlar/{rapor_id}/karar"
    # Yönetici anahtarı rapor onaylayamaz, deneme denetime yazılır.
    assert client.post(adres, json={"karar": "onayla", "dahil": [kalem]}, headers=_anahtar(client, "admin")).status_code == 403
    cevap = client.post(adres, json={"karar": "onayla", "dahil": [kalem], "notu": "Portaldan."})
    assert cevap.status_code == 200, cevap.text
    assert set(cevap.json()) == {"tur", "mesaj"} and cevap.json()["tur"] == "basari"
    assert client.post(adres, json={"karar": "reddet", "notu": "x"}).status_code == 409  # karar zaten verildi
    assert client.get(f"/api/v1/raporlar/{rapor_id}").json()["durum"] in ("ONAYLANDI", "GONDERILDI")
    assert client.post("/api/v1/raporlar/999/karar", json={"karar": "onayla"}).status_code == 404
    assert client.post(adres, json={"karar": "belki"}).status_code == 422
    with Session(client.app.state.engine) as s:
        islemler = [d.islem for d in s.scalars(select(Denetim))]
    assert "yetkisiz_karar_denemesi" in islemler and "onay" in islemler


def test_v1_ek_gonderim(ortam):
    client, idler, rapor_id = ortam
    grup = client.get("/api/v1/gruplar").json()["gruplar"][0]["id"]
    cevap = client.post(f"/api/v1/raporlar/{rapor_id}/ek-gonderim",
                        json={"dahil": [idler["k3"]], "gruplar": [grup], "ek_adresler": ["mudur@firma.com"]})
    assert cevap.status_code == 200, cevap.text
    assert set(cevap.json()) == {"tur", "mesaj"}


def test_v1_gruplar_sabit_bicim(ortam):
    client, *_ = ortam
    liste = client.get("/api/v1/gruplar").json()
    assert set(liste) == {"gruplar", "is_kollari", "kapsanmayan"}
    assert set(liste["gruplar"][0]) == {"id", "ad", "is_kollari", "adresler", "aktif", "guncellendi"}
    yeni = client.post("/api/v1/gruplar", json={"ad": "Oto", "is_kollari": ["Oto kiralama"], "adresler": ["oto@firma.com"]})
    assert yeni.status_code == 200 and yeni.json()["ad"] == "Oto"
    adres = f"/api/v1/gruplar/{yeni.json()['id']}"
    duzen = client.put(adres, json={"ad": "Oto", "adresler": ["oto@firma.com"], "is_kollari": ["Oto kiralama"], "aktif": False})
    assert duzen.status_code == 200 and duzen.json()["aktif"] is False
    assert client.put(adres, json={"ad": "Oto", "is_kollari": ["Ortak"], "adresler": ["bozuk"]}).status_code == 400
    assert client.put("/api/v1/gruplar/999", json={"ad": "x"}).status_code == 404


KONU = {"ad": "Döviz", "is_kollari": ["Ortak"], "kelimeler": ["döviz"], "aciklama": "Kur kararları."}
KONU_ALANLARI = {"id", "ad", "aciklama", "is_kollari", "kelimeler", "haric", "dislanan", "aktif", "surum"}


def test_v1_konu_ekle_duzenle_pasiflestir(ortam):
    client, *_ = ortam
    konu = client.post("/api/v1/konular", json=KONU)
    assert konu.status_code == 200, konu.text
    # Panelin listedeki ek alanları (eşleşme sayısı, kullanım, eski adlar) v1 cevabına girmez.
    assert set(konu.json()) == KONU_ALANLARI
    adres = f"/api/v1/konular/{konu.json()['id']}"
    duzen = client.put(adres, json={**KONU, "kelimeler": ["döviz", "kur"], "surum": konu.json()["surum"]})
    assert duzen.status_code == 200 and duzen.json()["kelimeler"] == ["döviz", "kur"]
    assert client.put(adres, json={**KONU, "surum": konu.json()["surum"]}).status_code == 409  # eski sürüm
    pasif = client.post(f"{adres}/pasif", json={"surum": duzen.json()["surum"]})
    assert pasif.status_code == 200 and pasif.json()["aktif"] is False
    assert "Döviz" not in [k["ad"] for k in client.get("/api/v1/konular").json()]
    assert "Döviz" in [k["ad"] for k in client.get("/api/v1/konular?pasifler=true").json()]
    assert client.post(f"{adres}/aktif", json={"surum": pasif.json()["surum"]}).json()["aktif"] is True
    assert set(client.get("/api/v1/konular").json()[0]) == KONU_ALANLARI  # liste ve düzenleme aynı biçimde
    assert client.post("/api/v1/konular", json={**KONU, "ad": "Boş", "kelimeler": []}).status_code == 400


def test_v1_konu_onizleme(ortam):
    client, *_ = ortam
    cevap = client.post("/api/v1/konular/onizleme", json={"ad": "Deneme", "is_kollari": ["Ortak"], "kelimeler": ["yönetmelik"]})
    assert cevap.status_code == 200, cevap.text
    veri = cevap.json()
    assert veri["gun"] == 90 and isinstance(veri["uyarilar"], list)
    assert set(veri) == {"gun", "taranan", "ayni_kalan", "eslesecek_sayisi", "dusecek_sayisi", "eslesecek", "dusecek",
                         "uyarilar"}


YENI_KAYNAK = {"tip": "wordpress", "etiket": "BDDK Duyurusu",
               "ayarlar": {"api_url": "https://www.bddk.org.tr/wp-json/wp/v2/posts"}, "varsayilan_konular": []}
KAYNAK_ALANLARI = {"ad", "etiket", "tip", "aktif", "ayarlar", "varsayilan_konular", "kaldirildi", "surum"}


def test_v1_kaynak_ekle_duzenle_kaldir(ortam, monkeypatch):
    client, *_ = ortam
    monkeypatch.setattr(ky, "_dns", lambda host: ["93.184.216.34"])
    tipler = client.get("/api/v1/kaynak-tipleri").json()
    assert "wordpress" in [t["tip"] for t in tipler["tipler"]] and "Vergi" in tipler["konular"]
    kaynak = client.post("/api/v1/kaynaklar", json=YENI_KAYNAK)
    assert kaynak.status_code == 200, kaynak.text
    # Panel aynı istekte tarama sayılarını da döner, v1 cevabı sadece tanım.
    assert set(kaynak.json()) == KAYNAK_ALANLARI and kaynak.json()["ad"] == "bddk_duyurusu"
    adres = "/api/v1/kaynaklar/bddk_duyurusu"
    duzen = client.put(adres, json={**YENI_KAYNAK, "etiket": "BDDK", "surum": kaynak.json()["surum"]})
    assert duzen.status_code == 200 and duzen.json()["etiket"] == "BDDK"
    assert client.put(adres, json={**YENI_KAYNAK, "surum": 1}).status_code == 409
    kaldir = client.post(f"{adres}/kaldir", json={"surum": duzen.json()["surum"]})
    assert kaldir.status_code == 200 and kaldir.json()["kaldirildi"] is True
    assert "bddk_duyurusu" not in [k["ad"] for k in client.get("/api/v1/kaynaklar").json()]
    assert "bddk_duyurusu" in [k["ad"] for k in client.get("/api/v1/kaynaklar?kaldirilanlar=true").json()]
    assert client.post(f"{adres}/geri-getir", json={"surum": kaldir.json()["surum"]}).json()["kaldirildi"] is False
    assert client.post("/api/v1/kaynaklar/yok/kaldir", json={"surum": 1}).status_code == 404
    assert set(client.get("/api/v1/kaynaklar").json()[0]) == KAYNAK_ALANLARI


def test_v1_kaynak_dene_ve_bul(ortam, monkeypatch):
    client, *_ = ortam
    monkeypatch.setattr(ky, "_dns", lambda host: ["93.184.216.34"])
    yazi = json.loads((Path(__file__).parent / "fixtures" / "masak_posts.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(ky, "make_client", lambda **_: make_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=yazi, headers={"X-WP-TotalPages": "1"}))))
    deneme = client.post("/api/v1/kaynaklar/dene", json=YENI_KAYNAK)
    assert deneme.status_code == 200, deneme.text
    assert set(deneme.json()) == {"kayitlar", "toplam", "kesildi", "gun"}
    sayfa = "<html><head><title>Deneme Sitesi</title></head><body><p>Duyuru yok.</p></body></html>"
    monkeypatch.setattr(kaynak_bulucu, "make_client", lambda **_: make_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=sayfa, headers={"Content-Type": "text/html"}))))
    bul = client.post("/api/v1/kaynaklar/bul", json={"adres": "https://site.gov.tr/duyurular"})
    assert bul.status_code == 200, bul.text
    veri = bul.json()
    assert veri["bulundu"] is False and veri["onerilen_ad"] == "Deneme Sitesi"
    assert veri["adimlar"] and set(veri["adimlar"][0]) == {"yol", "sonuc", "not"}  # alan adı "not" olarak kalır


def test_v1_tarama_ve_zamanlama(ortam):
    client, *_ = ortam
    zaman = client.get("/api/v1/zamanlama").json()
    assert set(zaman) == {"saatler", "surum", "zamanlayici_calisiyor", "son_nabiz", "durum", "sonraki"}
    yeni = client.put("/api/v1/zamanlama", json={"saatler": ["07:00", "19:00"], "surum": zaman["surum"]})
    assert yeni.status_code == 200 and yeni.json()["saatler"] == ["07:00", "19:00"]
    assert client.put("/api/v1/zamanlama", json={"saatler": ["07:00"], "surum": zaman["surum"]}).status_code == 409
    assert client.post("/api/v1/tarama").status_code == 409  # zamanlayıcı çalışmıyor
    with Session(client.app.state.engine) as s:
        zm.nabiz_yaz(s, datetime.now() - timedelta(seconds=10), "bekliyor", datetime.now() + timedelta(hours=5),
                     [time(7), time(19)])
    tarama = client.post("/api/v1/tarama")
    assert tarama.status_code == 200, tarama.text
    assert set(tarama.json()) == {"istek", "son_calismalar", "zamanlama"} and tarama.json()["istek"]["durum"] == "BEKLIYOR"
    assert client.post("/api/v1/tarama").status_code == 409  # zaten sırada
    assert client.get("/api/v1/tarama/durum").json()["istek"]["id"] == tarama.json()["istek"]["id"]


def test_panel_adresleri_aynen_calisir(ortam):
    client, *_ = ortam
    # Panelin kendi adresi v1'le aynı işi yapar ama kendi biçimiyle döner, v1 bundan etkilenmez.
    assert client.get("/api/gruplar").json()["gruplar"][0]["adresler"] == ["gizli-adres@firma.com"]
    assert "toplam_kayit" in client.get("/api/kaynaklar").json()["kaynaklar"][0]
    assert "toplam_kayit" not in client.get("/api/v1/kaynaklar").json()[0]

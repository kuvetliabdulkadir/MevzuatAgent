"""Panelden kaynak yönetimi (#10 adım 2): doğrulama, iç ağ koruması (SSRF), API, kaydetmeden dene."""

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import kaynak_yonetimi as ky
from mevzuat import tanimlar
from mevzuat.db import Calisma, Denetim, Kayit, KaynakDurumu, KaynakTanimi, init_db
from mevzuat.http import make_client
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

ROOT = Path(__file__).parent.parent
MASAK = json.loads((ROOT / "tests" / "fixtures" / "masak_posts.json").read_text(encoding="utf-8"))
PAROLA = "dogru-parola-123"
GENEL_IP = "93.184.216.34"


def _cozumle(adresler: dict[str, list[str]]):
    return lambda host: adresler.get(host, [GENEL_IP])


# ---- iç ağ koruması -------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, ip, hata", [
    ("http://site.gov.tr/api", GENEL_IP, "https://"),
    ("ftp://site.gov.tr/api", GENEL_IP, "https://"),
    ("https://user:pw@site.gov.tr/api", GENEL_IP, "kullanıcı adı"),
    ("https://site.gov.tr:8443/api", GENEL_IP, "443"),
    ("https://site.gov.tr/api", "127.0.0.1", "İç ağ"),
    ("https://site.gov.tr/api", "10.1.2.3", "İç ağ"),
    ("https://site.gov.tr/api", "172.20.0.5", "İç ağ"),
    ("https://site.gov.tr/api", "192.168.1.10", "İç ağ"),
    ("https://site.gov.tr/api", "169.254.169.254", "İç ağ"),  # bulut meta veri servisi
    ("https://site.gov.tr/api", "::1", "İç ağ"),
    ("https://site.gov.tr/api", "fd00::1", "İç ağ"),
    ("https://site.gov.tr/api", "fe80::1%eth0", "İç ağ"),
    ("https://site.gov.tr/api", "::ffff:10.0.0.1", "İç ağ"),
    ("https://site.gov.tr/api", "100.64.0.1", "İç ağ"),  # operatör NAT'ı
])
def test_guvenli_olmayan_adres_reddedilir(url, ip, hata):
    with pytest.raises(ValueError, match=hata):
        ky.guvenli_url(url, lambda host: [ip])


def test_ic_aga_cozulen_tek_ip_bile_reddedilir():
    with pytest.raises(ValueError, match="İç ağ"):
        ky.guvenli_url("https://site.gov.tr/api", lambda host: [GENEL_IP, "10.0.0.1"])


def test_genel_adres_kabul_edilir_ve_temizlenir():
    adres = ky.guvenli_url("  https://site.gov.tr/wp-json/wp/v2/posts ", lambda h: [GENEL_IP, "2606:2800:220:1::"])
    assert adres == "https://site.gov.tr/wp-json/wp/v2/posts"


def test_cozulemeyen_adres():
    with pytest.raises(ValueError, match="çözümlenemedi"):
        ky.guvenli_url("https://olmayan-alan-adi.invalid/api")


def test_bildirilen_boyutu_buyuk_cevap_indirilmez():
    def handler(request):
        return httpx.Response(200, headers={"Content-Length": "999999999"}, content=b"x")

    with make_client(transport=httpx.MockTransport(handler), en_fazla_bayt=1000) as c,             pytest.raises(httpx.HTTPError, match="çok büyük"):
        c.get("https://site.gov.tr/")


# ---- ad üretme ve ayar doğrulama ------------------------------------------------------------------------

def test_ad_uretme():
    assert ky.ad_uret("BDDK Duyurusu", set()) == "bddk_duyurusu"
    assert ky.ad_uret("Şirket İçi Gümrük Ödüncü", set()) == "sirket_ici_gumruk_oduncu"
    assert ky.ad_uret("BDDK Duyurusu", {"bddk_duyurusu", "bddk_duyurusu_2"}) == "bddk_duyurusu_3"
    assert ky.ad_uret("!!!", set()) == "kaynak"


def test_ayar_temizleme():
    c = _cozumle({})
    temiz = ky.ayarlari_temizle("wordpress", {"api_url": "https://a.gov.tr/x", "kotu": "alan"}, c)
    assert temiz == {"api_url": "https://a.gov.tr/x", "ortusme_gun": 1}
    with pytest.raises(ValueError, match="API adresi"):
        ky.ayarlari_temizle("wordpress", {"api_url": ""}, c)
    with pytest.raises(ValueError, match="0 ile 30"):
        ky.ayarlari_temizle("wordpress", {"api_url": "https://a.gov.tr/x", "ortusme_gun": 99}, c)
    assert ky.ayarlari_temizle("gib", {"turler": ["2", 1, "1"]}, c) == {"turler": [1, 2], "ortusme_gun": 1}
    with pytest.raises(ValueError, match="geçersiz seçim"):
        ky.ayarlari_temizle("gib", {"turler": [9]}, c)


# ---- API ------------------------------------------------------------------------------------------------

def _istek(client, yontem, yol, veri=None):
    csrf = client.get("/api/oturum").json()["csrf"]
    return client.request(yontem, yol, json=veri, headers={"X-CSRF-Token": csrf})


@pytest.fixture
def ortam(monkeypatch):
    monkeypatch.setattr(ky, "_dns", _cozumle({"ic.firma.local": ["10.0.0.5"]}))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        tanimlar.tohumla(s, ROOT / "config" / "kaynaklar.toml", ROOT / "config" / "konular.toml")
        kullanici_ekle(s, "sorumlu@firma.com", "Sorumlu", "onaylayici", PAROLA)
        s.add(Calisma(id=1, baslangic=datetime(2026, 10, 2, 6, 30), bitis=datetime(2026, 10, 2, 6, 31),
                      durum="HATALI", ozet={}, hata="gib_mevzuat: ConnectError('kapalı')"))
        s.add(KaynakDurumu(kaynak="masak", checkpoint="2026-10-02T06:30:00", guncellendi=datetime(2026, 10, 2, 6, 31)))
        for i, ilgili in enumerate([True, False, False]):
            s.add(Kayit(kaynak="masak", dis_id=str(i), yayin_tarihi=date(2026, 10, 1), baslik=f"B{i}", url="u",
                        kaynakca="k", ilgili=ilgili, eslesmeler={}, is_kollari=[], icerik_durumu="YOK",
                        ilk_gorulme=datetime.now(), calisma_id=1))
        s.commit()
    client = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    _istek(client, "post", "/api/giris", {"eposta": "sorumlu@firma.com", "parola": PAROLA})
    return client, engine


YENI = {"tip": "wordpress", "etiket": "BDDK Duyurusu",
        "ayarlar": {"api_url": "https://www.bddk.org.tr/wp-json/wp/v2/posts"}, "varsayilan_konular": []}


def test_liste_durumla_gelir(ortam):
    client, _ = ortam
    liste = {k["ad"]: k for k in client.get("/api/kaynaklar").json()["kaynaklar"]}
    assert list(liste) == ["resmi_gazete", "masak", "gib_mevzuat", "mevzuat_gov_yeni"]
    assert liste["masak"]["checkpoint"] == "2026-10-02T06:30:00" and liste["masak"]["toplam_kayit"] == 3
    assert liste["masak"]["ilgili_kayit"] == 1 and liste["masak"]["son_hata"] is None
    assert liste["gib_mevzuat"]["son_hata"] == "ConnectError('kapalı')"
    assert liste["masak"]["varsayilan_konular"] == ["MASAK / suç gelirleri / yaptırımlar"]
    tipler = {t["tip"]: t for t in client.get("/api/kaynak-tipleri").json()["tipler"]}
    assert tipler["wordpress"]["eklenebilir"] and not tipler["resmi_gazete"]["eklenebilir"]
    assert tipler["gib"]["alanlar"][0]["secenekler"]["1"] == "Mevzuat"


def test_ekle_duzenle_kaldir_geri_getir_denetime_yazilir(ortam):
    client, engine = ortam
    cevap = _istek(client, "post", "/api/kaynaklar", YENI)
    assert cevap.status_code == 200, cevap.text
    yeni = cevap.json()
    assert yeni["ad"] == "bddk_duyurusu" and yeni["surum"] == 1 and yeni["ayarlar"]["ortusme_gun"] == 1

    # Aynı görünen adla ikinci kaynak: kod adı çakışmaz.
    assert _istek(client, "post", "/api/kaynaklar", YENI).json()["ad"] == "bddk_duyurusu_2"

    konu_adi = next(k for k in client.get("/api/kaynak-tipleri").json()["konular"] if k.startswith("Vergi"))
    duzenleme = {**YENI, "etiket": "BDDK", "varsayilan_konular": [konu_adi], "surum": 1}
    cevap = _istek(client, "put", "/api/kaynaklar/bddk_duyurusu", duzenleme)
    assert cevap.status_code == 200 and cevap.json()["etiket"] == "BDDK" and cevap.json()["surum"] == 2
    # Eski formla (sürüm 1) ikinci düzenleme: çakışma.
    assert _istek(client, "put", "/api/kaynaklar/bddk_duyurusu", duzenleme).status_code == 409

    assert _istek(client, "post", "/api/kaynaklar/bddk_duyurusu/kaldir", {"surum": 2}).status_code == 200
    with Session(engine) as s:
        assert "bddk_duyurusu" not in [k.ad for k in tanimlar.kaynaklari_oku(s)]
    assert _istek(client, "post", "/api/kaynaklar/bddk_duyurusu/kaldir", {"surum": 3}).status_code == 400
    assert _istek(client, "post", "/api/kaynaklar/bddk_duyurusu/geri-getir", {"surum": 3}).status_code == 200
    with Session(engine) as s:
        kaynak = next(k for k in tanimlar.kaynaklari_oku(s) if k.ad == "bddk_duyurusu")
        assert kaynak.etiket == "BDDK" and kaynak.varsayilan_konular == (konu_adi,)
        islemler = [(d.islem, d.detay["kaynak"]) for d in s.scalars(select(Denetim).where(Denetim.islem.like("kaynak_%")))]
        assert islemler == [("kaynak_ekle", "bddk_duyurusu"), ("kaynak_ekle", "bddk_duyurusu_2"),
                            ("kaynak_degistir", "bddk_duyurusu"), ("kaynak_kaldir", "bddk_duyurusu"),
                            ("kaynak_geri_getir", "bddk_duyurusu")]
        degisim = s.scalar(select(Denetim).where(Denetim.islem == "kaynak_degistir")).detay
        assert degisim["once"]["etiket"] == "BDDK Duyurusu" and degisim["sonra"]["etiket"] == "BDDK"


@pytest.mark.parametrize("degisiklik, hata", [
    ({"tip": "resmi_gazete", "ayarlar": {}}, "eklenemez"),
    ({"tip": "olmayan"}, "Bilinmeyen"),
    ({"ayarlar": {"api_url": "https://ic.firma.local/wp-json"}}, "İç ağ"),
    ({"ayarlar": {"api_url": "http://www.bddk.org.tr/x"}}, "https://"),
    ({"varsayilan_konular": ["Olmayan Konu"]}, "Tanımsız"),
    ({"etiket": "   "}, "boş olamaz"),
])
def test_hatali_kaynak_kaydedilmez(ortam, degisiklik, hata):
    client, engine = ortam
    cevap = _istek(client, "post", "/api/kaynaklar", {**YENI, **degisiklik})
    assert cevap.status_code == 400 and hata in cevap.json()["detail"]
    with Session(engine) as s:
        assert len(s.scalars(select(KaynakTanimi)).all()) == 4


def test_rg_duzenlenebilir_ad_ve_tip_degismez(ortam):
    client, engine = ortam
    cevap = _istek(client, "put", "/api/kaynaklar/resmi_gazete",
                   {"tip": "wordpress", "etiket": "Resmî Gazete (T.C.)", "ayarlar": {"api_url": "https://x.gov.tr"},
                    "varsayilan_konular": [], "surum": 1})
    assert cevap.status_code == 200
    with Session(engine) as s:
        rg = s.get(KaynakTanimi, "resmi_gazete")
        assert rg.tip == "resmi_gazete" and rg.ayarlar == {} and rg.etiket == "Resmî Gazete (T.C.)"


def test_girissiz_ve_csrfsiz_istek_reddedilir(ortam):
    client, _ = ortam
    assert client.post("/api/kaynaklar", json=YENI).status_code == 403  # CSRF yok
    _istek(client, "post", "/api/cikis")
    assert client.get("/api/kaynaklar").status_code == 401
    assert _istek(client, "post", "/api/kaynaklar/dene", YENI).status_code == 401


def test_dene_kaydetmeden_tarar_eslesmeleri_gosterir(ortam, monkeypatch):
    client, engine = ortam
    istekler = []

    def handler(request):
        istekler.append(request)
        return httpx.Response(200, json=MASAK, headers={"X-WP-TotalPages": "1"})

    monkeypatch.setattr(ky, "make_client", lambda **_: make_client(transport=httpx.MockTransport(handler)))
    cevap = _istek(client, "post", "/api/kaynaklar/dene", YENI)
    assert cevap.status_code == 200, cevap.text
    sonuc = cevap.json()
    assert sonuc["toplam"] == len(MASAK) and sonuc["gun"] == 7 and not sonuc["kesildi"]
    assert any(k["eslesen"] for k in sonuc["kayitlar"])  # MASAK duyurularında ★ çıkar
    assert istekler[0].url.host == "www.bddk.org.tr"
    with Session(engine) as s:
        assert len(s.scalars(select(KaynakTanimi)).all()) == 4  # hiçbir şey yazılmadı
        assert len(s.scalars(select(Kayit)).all()) == 3


def test_dene_ulasilamayan_kaynakta_anlasilir_hata(ortam, monkeypatch):
    client, _ = ortam

    def handler(request):
        return httpx.Response(404, json={"code": "rest_no_route"})

    monkeypatch.setattr(ky, "make_client", lambda **_: make_client(transport=httpx.MockTransport(handler)))
    cevap = _istek(client, "post", "/api/kaynaklar/dene", YENI)
    assert cevap.status_code == 400 and "ulaşılamadı" in cevap.json()["detail"]

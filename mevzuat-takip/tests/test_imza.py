"""İmzalı istek (HMAC). Kopyalanan, değiştirilen, eski istek ve anahtarın doğrudan gönderilmesi reddedilir."""

import json
import secrets
import time
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from mevzuat import panel_ayarlari
from mevzuat.db import ApiAnahtari, Denetim, migrate
from mevzuat.web import imza, uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

PAROLA = "dogru-parola-123"
ANAHTAR = "t" * 40


class Posta:
    def gonder(self, mail):
        pass


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    for alan in panel_ayarlari.ALANLAR:
        monkeypatch.delenv(alan.degisken, raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'i.db'}")
    migrate(engine)
    with Session(engine) as s:
        kullanici_ekle(s, "admin@firma.com", "Bilgi İşlem", "admin", PAROLA)
    app = uygulama_olustur(engine, Posta(), None, ANAHTAR, ek_ekle=False, arayuz=None,
                           ayar_oku=lambda db: panel_ayarlari.etkin(db, ANAHTAR))
    panel = TestClient(app)
    csrf = panel.get("/api/oturum").json()["csrf"]
    cevap = panel.post("/api/giris", json={"eposta": "admin@firma.com", "parola": PAROLA},
                       headers={"X-CSRF-Token": csrf})
    csrf = cevap.json()["csrf"]
    cevap = panel.post("/api/api-anahtarlari", json={"ad": "Şirket portalı", "rol": "tam", "gun": None},
                       headers={"X-CSRF-Token": csrf})
    assert cevap.status_code == 200, cevap.text
    return panel, TestClient(app), cevap.json()["anahtar"], engine


# Dış sistemin yaptığı imza, dokümandaki örnek kodla aynı adımlar.
def imzala(anahtar, yontem, adres, govde=b"", zaman=None, numara=None):
    parca = urlsplit(adres)
    zaman = str(zaman if zaman is not None else int(time.time()))
    numara = numara or secrets.token_urlsafe(24)
    return {"X-Anahtar": anahtar[:10], "X-Zaman": zaman, "X-Numara": numara,
            "X-Imza": imza.imza_hesapla(anahtar, yontem, parca.path, parca.query, zaman, numara, govde)}


def _govde(veri):
    return json.dumps(veri).encode()


def test_imzali_istek_calisir_kopyasi_reddedilir(ortam):
    _, dis, anahtar, _ = ortam
    basliklar = imzala(anahtar, "GET", "/api/v1/mevzuat?sadece_ilgili=false&adet=5")
    assert dis.get("/api/v1/mevzuat?sadece_ilgili=false&adet=5", headers=basliklar).status_code == 200
    # Network sekmesinden kopyalanan istek, aynı başlıklarla, saniyeler içinde.
    tekrar = dis.get("/api/v1/mevzuat?sadece_ilgili=false&adet=5", headers=basliklar)
    assert tekrar.status_code == 401 and "daha önce kullanıldı" in tekrar.json()["detail"]


def test_degistirilen_istek_reddedilir(ortam):
    _, dis, anahtar, _ = ortam
    # Sorgu değişti.
    basliklar = imzala(anahtar, "GET", "/api/v1/mevzuat?adet=5")
    assert dis.get("/api/v1/mevzuat?adet=100", headers=basliklar).status_code == 401
    # Adres değişti.
    basliklar = imzala(anahtar, "GET", "/api/v1/mevzuat")
    assert dis.get("/api/v1/raporlar", headers=basliklar).status_code == 401
    # Yöntem değişti.
    basliklar = imzala(anahtar, "GET", "/api/v1/gruplar")
    assert dis.post("/api/v1/gruplar", headers=basliklar, json={}).status_code == 401
    # Gövde değişti.
    veri = {"ad": "Kuyum", "is_kollari": ["Kuyum"], "adresler": ["kuyum@firma.com"], "aktif": True}
    basliklar = imzala(anahtar, "POST", "/api/v1/gruplar", _govde(veri))
    cevap = dis.post("/api/v1/gruplar", headers={**basliklar, "Content-Type": "application/json"},
                     content=_govde({**veri, "adresler": ["saldirgan@kotu.com"]}))
    assert cevap.status_code == 401 and "İmza tutmuyor" in cevap.json()["detail"]
    # Doğru imzalı aynı gövde geçer.
    basliklar = imzala(anahtar, "POST", "/api/v1/gruplar", _govde(veri))
    cevap = dis.post("/api/v1/gruplar", headers={**basliklar, "Content-Type": "application/json"}, content=_govde(veri))
    assert cevap.status_code == 200, cevap.text


def test_eski_ileri_tarihli_ve_bicimsiz_istek_reddedilir(ortam):
    _, dis, anahtar, _ = ortam
    simdi = int(time.time())
    for zaman in (simdi - imza.PENCERE - 5, simdi + imza.PENCERE + 5):
        cevap = dis.get("/api/v1/konular", headers=imzala(anahtar, "GET", "/api/v1/konular", zaman=zaman))
        assert cevap.status_code == 401 and "X-Zaman" in cevap.json()["detail"]
    cevap = dis.get("/api/v1/konular", headers=imzala(anahtar, "GET", "/api/v1/konular", numara="kisa"))
    assert cevap.status_code == 401
    eksik = imzala(anahtar, "GET", "/api/v1/konular")
    del eksik["X-Imza"]
    assert dis.get("/api/v1/konular", headers=eksik).status_code == 401


def test_anahtar_dogrudan_gonderilemez_oturumla_v1_acilmaz(ortam):
    panel, dis, anahtar, engine = ortam
    for basliklar in ({"Authorization": f"Bearer {anahtar}"}, {"X-API-Anahtari": anahtar}):
        cevap = dis.get("/api/v1/mevzuat", headers=basliklar)
        assert cevap.status_code == 401 and "imzalanmalı" in cevap.json()["detail"]
    # Panelde giriş yapmış kişi de /api/v1'i oturumla açamaz.
    assert panel.get("/api/v1/mevzuat").status_code == 401
    # İmza başlıkları panelin kendi adreslerinde geçmez, dış sistem sadece /api/v1 kullanır.
    assert dis.get("/api/kullanicilar", headers=imzala(anahtar, "GET", "/api/kullanicilar")).status_code == 401
    with Session(engine) as s:
        assert s.scalar(select(Denetim).where(Denetim.islem == "api_imzasiz_istek")) is not None


def test_iptal_edilen_ve_imzasiz_uretilmis_anahtar_reddedilir(ortam):
    panel, dis, anahtar, engine = ortam
    with Session(engine) as s:
        s.execute(update(ApiAnahtari).values(gizli=None))
        s.commit()
    cevap = dis.get("/api/v1/konular", headers=imzala(anahtar, "GET", "/api/v1/konular"))
    assert cevap.status_code == 401 and "yeni anahtar" in cevap.json()["detail"]
    csrf = panel.get("/api/oturum").json()["csrf"]
    kayit_id = panel.get("/api/api-anahtarlari").json()["anahtarlar"][0]["id"]
    panel.post(f"/api/api-anahtarlari/{kayit_id}/iptal", headers={"X-CSRF-Token": csrf})
    cevap = dis.get("/api/v1/konular", headers=imzala(anahtar, "GET", "/api/v1/konular"))
    assert cevap.status_code == 401 and "iptal" in cevap.json()["detail"]
    # Yanlış anahtarla atılan imza.
    sahte = anahtar[:10] + "x" * 33
    assert dis.get("/api/v1/konular", headers=imzala(sahte, "GET", "/api/v1/konular")).status_code == 401
    with Session(engine) as s:
        sebepler = {d.detay["sebep"] for d in s.scalars(select(Denetim).where(Denetim.islem == "api_imza_gecersiz"))}
    assert {"imzasiz_anahtar", "anahtar_yok"} <= sebepler


def test_hiz_siniri(ortam, monkeypatch):
    _, dis, anahtar, _ = ortam
    monkeypatch.setattr(imza, "DAKIKA_SINIRI", 3)
    kodlar = [dis.get("/api/v1/konular", headers=imzala(anahtar, "GET", "/api/v1/konular")).status_code
              for _ in range(4)]
    assert kodlar == [200, 200, 200, 429]


def test_yeni_sekmede_acilan_adres_reddedilir(ortam):
    panel, _, _, _ = ortam
    sekme = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    # Giriş yapmış kişi panelin adresini yeni sekmede açar.
    assert panel.get("/api/kullanicilar").status_code == 200
    cevap = panel.get("/api/kullanicilar", headers=sekme)
    assert cevap.status_code == 401 and "doğrudan açılamaz" in cevap.json()["detail"]
    # Doküman sayfası yeni sekmede açılır.
    assert panel.get("/api/dokuman", headers=sekme).status_code == 200


def test_dokuman_imzali_istekle_gelir(ortam):
    panel, dis, anahtar, _ = ortam
    csrf = panel.get("/api/oturum").json()["csrf"]
    surum = panel.get("/api/ayarlar").json()["surum"]
    panel.put("/api/ayarlar", json={"degisiklikler": {"dokuman_acik": False}, "surum": surum},
              headers={"X-CSRF-Token": csrf})
    assert dis.get("/api/dokuman/openapi.json").status_code == 401
    cevap = dis.get("/api/dokuman/openapi.json", headers=imzala(anahtar, "GET", "/api/dokuman/openapi.json"))
    assert cevap.status_code == 200 and "/api/v1/mevzuat" in cevap.json()["paths"]


def test_panelin_butun_islemleri_v1_de(ortam):
    _, dis, anahtar, _ = ortam

    def al(adres):
        cevap = dis.get(adres, headers=imzala(anahtar, "GET", adres))
        assert cevap.status_code == 200, (adres, cevap.text)
        return cevap.json()

    assert [k["eposta"] for k in al("/api/v1/kullanicilar")["kullanicilar"]] == ["admin@firma.com"]
    ayarlar = {a["ad"]: a for a in al("/api/v1/ayarlar")["alanlar"]}
    assert ayarlar["smtp_sifre"]["deger"] is None and ayarlar["api_imza"]["deger"] is True
    assert "api_istegi" in al("/api/v1/denetim")["islemler"]
    # Konu eklenir, geçmişinde eklenme satırı görünür.
    govde = _govde({"ad": "Gümrük", "is_kollari": ["Ortak"], "kelimeler": ["gümrük"], "haric": [], "dislanan": [],
                    "aciklama": ""})
    cevap = dis.post("/api/v1/konular", content=govde,
                     headers={**imzala(anahtar, "POST", "/api/v1/konular", govde), "Content-Type": "application/json"})
    assert cevap.status_code == 200, cevap.text
    gecmis = al(f"/api/v1/konular/{cevap.json()['id']}/gecmis")["gecmis"]
    assert len(gecmis) == 1 and gecmis[0]["kim"] == "API: Şirket portalı"
    # Mail ayarı yokken deneme maili hata mesajı döner, kayıt değişmez.
    govde = _govde({"adres": "bilgi@firma.com"})
    cevap = dis.post("/api/v1/ayarlar/mail-dene", content=govde,
                     headers={**imzala(anahtar, "POST", "/api/v1/ayarlar/mail-dene", govde),
                              "Content-Type": "application/json"})
    assert cevap.status_code == 200 and cevap.json()["tur"] == "hata"

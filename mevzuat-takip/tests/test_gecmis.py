"""Kaynak/konu değişiklik geçmişi ve admin kurtarma (#10 adım 7)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import kaynak_yonetimi as ky
from mevzuat import tanimlar
from mevzuat.db import Denetim, KaynakTanimi, KonuTanimi, init_db
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

ROOT = Path(__file__).parent.parent
PAROLA = "dogru-parola-123"
YENI = {"tip": "wordpress", "etiket": "BDDK Duyurusu",
        "ayarlar": {"api_url": "https://www.bddk.org.tr/wp-json/wp/v2/posts"}, "varsayilan_konular": []}


@pytest.fixture
def ortam(monkeypatch, tmp_path):
    monkeypatch.setattr(ky, "_dns", lambda host: ["93.184.216.34"])
    monkeypatch.setenv("MEVZUAT_IZLENEN", str(tmp_path / "yok.toml"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        tanimlar.tohumla(s, ROOT / "config" / "kaynaklar.toml", ROOT / "config" / "konular.toml")
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
        kullanici_ekle(s, "admin@firma.com", "Ali Admin", "admin", PAROLA)
    app = uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None)
    onay, admin = TestClient(app), TestClient(app)
    _istek(onay, "post", "/api/giris", {"eposta": "onay@firma.com", "parola": PAROLA})
    _istek(admin, "post", "/api/giris", {"eposta": "admin@firma.com", "parola": PAROLA})
    return onay, admin, engine


def _istek(client, yontem, yol, veri=None):
    csrf = client.get("/api/oturum").json()["csrf"]
    return client.request(yontem, yol, json=veri, headers={"X-CSRF-Token": csrf})


def _kaynak(client, ad):
    return next(k for k in client.get("/api/kaynaklar").json()["kaynaklar"] if k["ad"] == ad)


def test_kaynak_gecmisi_ve_admin_onceki_hale_dondurur(ortam):
    onay, admin, engine = ortam
    _istek(onay, "post", "/api/kaynaklar", YENI)
    _istek(onay, "put", "/api/kaynaklar/bddk_duyurusu", {**YENI, "etiket": "Yanlış Ad", "aktif": False, "surum": 1})

    gecmis = admin.get("/api/kaynaklar/bddk_duyurusu/gecmis").json()["gecmis"]
    assert [g["islem"] for g in gecmis] == ["kaynak_degistir", "kaynak_ekle"]  # yeniden eskiye
    duzenleme = gecmis[0]
    assert duzenleme["kim"] == "Ayşe Onay"
    assert {f["alan"]: (f["once"], f["sonra"]) for f in duzenleme["farklar"]} == {
        "etiket": ("BDDK Duyurusu", "Yanlış Ad"), "aktif": (True, False)}
    assert onay.get("/api/kaynaklar/bddk_duyurusu/gecmis").status_code == 200  # onaylayıcı da görür

    cevap = _istek(admin, "post", "/api/kaynaklar/bddk_duyurusu/geri-al", {"denetim_id": duzenleme["id"], "surum": 2})
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["etiket"] == "BDDK Duyurusu" and cevap.json()["aktif"] and cevap.json()["surum"] == 3

    gecmis = admin.get("/api/kaynaklar/bddk_duyurusu/gecmis").json()["gecmis"]
    assert gecmis[0]["islem"] == "kaynak_geri_al" and gecmis[0]["kim"] == "Ali Admin"
    assert gecmis[0]["geri_alinan"] == duzenleme["id"]
    assert len(gecmis) == 3  # geçmiş silinmez

    # Eklemenin öncesi = kaldırılmış hal.
    eklenme = gecmis[-1]
    assert _istek(admin, "post", "/api/kaynaklar/bddk_duyurusu/geri-al", {"denetim_id": eklenme["id"], "surum": 3}).status_code == 200
    with Session(engine) as s:
        assert s.get(KaynakTanimi, "bddk_duyurusu").kaldirildi


def test_onaylayici_geri_alamaz_denetime_yazilir(ortam):
    onay, _, engine = ortam
    _istek(onay, "post", "/api/kaynaklar", YENI)
    (eklenme,) = onay.get("/api/kaynaklar/bddk_duyurusu/gecmis").json()["gecmis"]
    cevap = _istek(onay, "post", "/api/kaynaklar/bddk_duyurusu/geri-al", {"denetim_id": eklenme["id"], "surum": 1})
    assert cevap.status_code == 403
    with Session(engine) as s:
        assert s.scalar(select(Denetim).where(Denetim.islem == "yetkisiz_kurtarma_denemesi")) is not None
        assert not s.get(KaynakTanimi, "bddk_duyurusu").kaldirildi


def test_baska_kaynagin_kaydi_ve_eski_surum_reddedilir(ortam):
    onay, admin, _ = ortam
    _istek(onay, "post", "/api/kaynaklar", YENI)
    _istek(onay, "put", "/api/kaynaklar/masak", {**YENI, "tip": "wordpress", "etiket": "MASAK",
                                                 "ayarlar": {"api_url": "https://masak.hmb.gov.tr/portal/v2/posts"},
                                                 "varsayilan_konular": [], "surum": 1})
    masak_kaydi = admin.get("/api/kaynaklar/masak/gecmis").json()["gecmis"][0]
    cevap = _istek(admin, "post", "/api/kaynaklar/bddk_duyurusu/geri-al", {"denetim_id": masak_kaydi["id"], "surum": 1})
    assert cevap.status_code == 400 and "bulunamadı" in cevap.json()["detail"]
    cevap = _istek(admin, "post", "/api/kaynaklar/masak/geri-al", {"denetim_id": masak_kaydi["id"], "surum": 1})
    assert cevap.status_code == 409  # form açıldıktan sonra değişmiş


def test_masak_varsayilan_konusu_geri_gelir(ortam):
    """Yanlışlıkla MASAK'ın 'her kaydı ilgili' konusu silindi: admin bir tıkla geri getirir."""
    onay, admin, engine = ortam
    masak = _kaynak(onay, "masak")
    _istek(onay, "put", "/api/kaynaklar/masak", {"tip": "wordpress", "etiket": masak["etiket"], "ayarlar": masak["ayarlar"],
                                                 "varsayilan_konular": [], "aktif": True, "surum": masak["surum"]})
    kayit = admin.get("/api/kaynaklar/masak/gecmis").json()["gecmis"][0]
    assert _istek(admin, "post", "/api/kaynaklar/masak/geri-al", {"denetim_id": kayit["id"], "surum": 2}).status_code == 200
    with Session(engine) as s:
        assert s.get(KaynakTanimi, "masak").varsayilan_konular == ["MASAK / suç gelirleri / yaptırımlar"]


def test_konu_yeniden_adlandirma_ve_kelime_degisikligi_geri_alinir(ortam):
    onay, admin, engine = ortam
    vergi = next(k for k in onay.get("/api/konular").json()["konular"] if k["ad"] == "Vergi")
    form = {a: vergi[a] for a in ("is_kollari", "haric", "dislanan")}
    _istek(onay, "put", f"/api/konular/{vergi['id']}",
           {**form, "ad": "Vergiler", "kelimeler": ["vergi"], "surum": vergi["surum"]})
    (kayit,) = admin.get(f"/api/konular/{vergi['id']}/gecmis").json()["gecmis"]
    alanlar = {f["alan"] for f in kayit["farklar"]}
    assert alanlar == {"ad", "kelimeler"}

    cevap = _istek(admin, "post", f"/api/konular/{vergi['id']}/geri-al", {"denetim_id": kayit["id"], "surum": 2})
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["ad"] == "Vergi" and cevap.json()["kelimeler"] == vergi["kelimeler"]
    assert "Vergiler" in cevap.json()["eski_adlar"]
    with Session(engine) as s:
        assert "Vergi" in [k.ad for k in tanimlar.konulari_oku(s)]


def test_konu_aciklamasi_geri_alinir(ortam):
    onay, admin, _ = ortam
    vergi = next(k for k in onay.get("/api/konular").json()["konular"] if k["ad"] == "Vergi")
    form = {a: vergi[a] for a in ("ad", "is_kollari", "kelimeler", "haric", "dislanan")}
    _istek(onay, "put", f"/api/konular/{vergi['id']}", {**form, "aciklama": "İlk yazı.", "surum": vergi["surum"]})
    _istek(onay, "put", f"/api/konular/{vergi['id']}", {**form, "aciklama": "Yanlışlıkla silindi", "surum": 2})
    son, _ = admin.get(f"/api/konular/{vergi['id']}/gecmis").json()["gecmis"]
    assert son["farklar"] == [{"alan": "aciklama", "once": "İlk yazı.", "sonra": "Yanlışlıkla silindi"}]
    cevap = _istek(admin, "post", f"/api/konular/{vergi['id']}/geri-al", {"denetim_id": son["id"], "surum": 3})
    assert cevap.status_code == 200 and cevap.json()["aciklama"] == "İlk yazı."


def test_eklenen_konunun_oncesi_pasif_hal(ortam):
    onay, admin, engine = ortam
    yeni = _istek(onay, "post", "/api/konular", {"ad": "Gümrük", "is_kollari": ["Ortak"], "kelimeler": ["gümrük"],
                                                  "haric": [], "dislanan": []}).json()
    (kayit,) = admin.get(f"/api/konular/{yeni['id']}/gecmis").json()["gecmis"]
    assert _istek(admin, "post", f"/api/konular/{yeni['id']}/geri-al", {"denetim_id": kayit["id"], "surum": 1}).status_code == 200
    with Session(engine) as s:
        assert not s.get(KonuTanimi, yeni["id"]).aktif


def test_kullanici_bilgisinde_kurtarma_yetkisi(ortam):
    onay, admin, _ = ortam
    assert admin.get("/api/oturum").json()["kullanici"]["kurtarma_yapabilir"]
    assert not onay.get("/api/oturum").json()["kullanici"]["kurtarma_yapabilir"]

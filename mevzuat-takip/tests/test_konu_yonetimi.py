"""Panelden konu yönetimi (#10 adım 5): ekle, düzenle, yeniden adlandır, pasifleştir, önizleme."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import pipeline, rapor, surum, tanimlar
from mevzuat.db import Calisma, Denetim, Kayit, KaynakTanimi, KonuTanimi, init_db
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

ROOT = Path(__file__).parent.parent
PAROLA = "dogru-parola-123"
BUGUN = date.today()
KUYUM = "Kıymetli madenler ve kuyumculuk"
MASAK = "MASAK / suç gelirleri / yaptırımlar"


def _kayit(i: int, baslik: str, eslesmeler: dict, gun_once: int = 1) -> Kayit:
    return Kayit(kaynak="resmi_gazete", dis_id=f"d{i}", yayin_tarihi=BUGUN - timedelta(days=gun_once), baslik=baslik,
                 url="u", kaynakca="k", ilgili=bool(eslesmeler), eslesmeler=eslesmeler, is_kollari=[],
                 icerik_durumu="TAMAM", ilk_gorulme=datetime.now(), calisma_id=1)


@pytest.fixture
def izlenen(tmp_path, monkeypatch):
    yol = tmp_path / "izlenen.toml"
    yol.write_text(f'[[mevzuat]]\nad = "Kuyum Yönetmeliği"\ntur = 7\ntertip = 5\nno = 1\nkonu = "{KUYUM}"\n',
                   encoding="utf-8")
    monkeypatch.setenv("MEVZUAT_IZLENEN", str(yol))
    return yol


@pytest.fixture
def ortam(izlenen):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        tanimlar.tohumla(s, ROOT / "config" / "kaynaklar.toml", ROOT / "config" / "konular.toml")
        kullanici_ekle(s, "sorumlu@firma.com", "Sorumlu", "onaylayici", PAROLA)
        s.add(Calisma(id=1, baslangic=datetime.now(), durum="BASARILI", ozet={}))
        s.add_all([
            _kayit(1, "Altın Ticareti Hakkında Tebliğ", {KUYUM: ["altın"]}),
            _kayit(2, "Sarrafiye Basımına İlişkin Karar", {}),
            _kayit(3, "Ziynet Eşyası Yönetmeliği", {KUYUM: ["ziynet"]}),
            _kayit(4, "Eski Altın Tebliği", {KUYUM: ["altın"]}, gun_once=200),  # 90 günün dışında
        ])
        s.commit()
    client = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    _istek(client, "post", "/api/giris", {"eposta": "sorumlu@firma.com", "parola": PAROLA})
    return client, engine


def _istek(client, yontem, yol, veri=None):
    csrf = client.get("/api/oturum").json()["csrf"]
    return client.request(yontem, yol, json=veri, headers={"X-CSRF-Token": csrf})


def _konu(client, ad):
    return next(k for k in client.get("/api/konular").json()["konular"] if k["ad"] == ad)


def _form(konu: dict, **degisiklik) -> dict:
    return {**{a: konu[a] for a in ("ad", "is_kollari", "kelimeler", "haric", "dislanan", "aciklama")},
            "surum": konu["surum"],
            **degisiklik}


def test_liste_90_gunluk_eslesme_ve_kullanim(ortam):
    client, _ = ortam
    cevap = client.get("/api/konular").json()
    kuyum = next(k for k in cevap["konular"] if k["ad"] == KUYUM)
    assert kuyum["eslesme_90"] == 2  # 200 gün önceki sayılmaz
    assert kuyum["kullanim"] == {"kaynaklar": [], "izlenen": True}
    masak = next(k for k in cevap["konular"] if k["ad"] == MASAK)
    assert masak["kullanim"]["kaynaklar"] == ["MASAK Duyurusu"]
    assert "Kuyum" in cevap["is_kolu_secenekleri"] and "Ortak" in cevap["is_kolu_secenekleri"]


def test_onizleme_eslesecek_ve_dusecek_basliklar(ortam):
    client, engine = ortam
    kuyum = _konu(client, KUYUM)
    kelimeler = [k for k in kuyum["kelimeler"] if k != "ziynet"] + ["sarrafiye", "ab"]
    cevap = _istek(client, "post", "/api/konular/onizleme", {**_form(kuyum, kelimeler=kelimeler), "id": kuyum["id"]})
    assert cevap.status_code == 200, cevap.text
    sonuc = cevap.json()
    assert [s["baslik"] for s in sonuc["eslesecek"]] == ["Sarrafiye Basımına İlişkin Karar"]
    assert sonuc["eslesecek"][0]["kelimeler"] == ["sarrafiye"]
    assert [s["baslik"] for s in sonuc["dusecek"]] == ["Ziynet Eşyası Yönetmeliği"]
    assert sonuc["ayni_kalan"] == 1 and sonuc["taranan"] == 3 and sonuc["gun"] == 90
    assert any("Kısa kelime (ab)" in u for u in sonuc["uyarilar"])
    with Session(engine) as s:  # önizleme hiçbir şey yazmaz
        assert "sarrafiye" not in s.scalar(select(KonuTanimi).where(KonuTanimi.ad == KUYUM)).kelimeler


def test_uyari_sadece_yeni_eklenen_kelime_icin(ortam):
    """Tarayıcı denemesinde görüldü: Vergi'deki mevcut "kdv"/"ötv" her düzenlemede uyarı veriyordu."""
    client, _ = ortam
    vergi = _konu(client, "Vergi")
    sonuc = _istek(client, "post", "/api/konular/onizleme",
                   {**_form(vergi, kelimeler=[*vergi["kelimeler"], "noterlik"]), "id": vergi["id"]}).json()
    assert sonuc["uyarilar"] == []
    sonuc = _istek(client, "post", "/api/konular/onizleme",
                   {**_form(vergi, kelimeler=[*vergi["kelimeler"], "tv", "altın"]), "id": vergi["id"]}).json()
    assert any("Kısa kelime (tv)" in u for u in sonuc["uyarilar"]) and any(KUYUM in u for u in sonuc["uyarilar"])


def test_ayni_kelime_iki_konuda_uyarisi(ortam):
    client, _ = ortam
    cevap = _istek(client, "post", "/api/konular/onizleme",
                   {"ad": "Yeni", "is_kollari": ["Ortak"], "kelimeler": ["Altın", "gümrük"], "haric": [], "dislanan": []})
    assert any(KUYUM in u and "altın" in u for u in cevap.json()["uyarilar"])


def test_ekle_duzenle_denetime_yazilir_tarama_dbden_okur(ortam):
    client, engine = ortam
    cevap = _istek(client, "post", "/api/konular", {"ad": "  Gümrük  ", "is_kollari": ["Ortak", "Ortak"],
                                                    "kelimeler": ["Gümrük", "gümrük", " antrepo "], "haric": [],
                                                    "dislanan": []})
    assert cevap.status_code == 200, cevap.text
    yeni = cevap.json()
    assert yeni["ad"] == "Gümrük" and yeni["kelimeler"] == ["Gümrük", "antrepo"] and yeni["is_kollari"] == ["Ortak"]
    cevap = _istek(client, "put", f"/api/konular/{yeni['id']}", _form(yeni, kelimeler=["gümrük", "antrepo", "transit"]))
    assert cevap.status_code == 200 and cevap.json()["surum"] == 2
    assert _istek(client, "put", f"/api/konular/{yeni['id']}", _form(yeni)).status_code == 409  # eski sürüm
    with Session(engine) as s:
        gumruk = next(k for k in tanimlar.konulari_oku(s) if k.ad == "Gümrük")
        assert gumruk.kelimeler == ("gümrük", "antrepo", "transit")
        islemler = [d.islem for d in s.scalars(select(Denetim).where(Denetim.islem.like("konu_%")))]
        assert islemler == ["konu_ekle", "konu_degistir"]


def test_aciklama_kaydedilir_sadelesir_rapora_gider(ortam):
    client, engine = ortam
    masak = _konu(client, MASAK)
    assert masak["aciklama"] == ""
    yazi = "  Kuyumcular   5549 sayılı Kanunda yükümlüdür.\n\n Eşik değişiklikleri doğrudan uygulanır.  "
    cevap = _istek(client, "put", f"/api/konular/{masak['id']}", _form(masak, aciklama=yazi))
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["aciklama"] == "Kuyumcular 5549 sayılı Kanunda yükümlüdür.\n\nEşik değişiklikleri doğrudan uygulanır."
    # Yeniden adlandırılınca eski adla eşleşmiş kayıtlar da aynı açıklamayı alır.
    _istek(client, "put", f"/api/konular/{masak['id']}", _form(_konu(client, MASAK), ad="MASAK"))
    with Session(engine) as s:
        aciklamalar = rapor.konu_aciklamalari(s)
    assert aciklamalar[MASAK] == aciklamalar["MASAK"] and aciklamalar[MASAK].startswith("Kuyumcular")
    assert "Vergi" not in aciklamalar  # açıklaması olmayan konu yok


def test_cok_uzun_aciklama_kaydedilmez(ortam):
    client, _ = ortam
    vergi = _konu(client, "Vergi")
    cevap = _istek(client, "put", f"/api/konular/{vergi['id']}", _form(vergi, aciklama="a" * 1001))
    assert cevap.status_code == 400 and "1000 karakter" in cevap.json()["detail"]


@pytest.mark.parametrize("govde, hata", [
    ({"ad": "", "is_kollari": ["Ortak"], "kelimeler": ["x"]}, "boş olamaz"),
    ({"ad": "Y", "is_kollari": ["Ortak"], "kelimeler": ["  "]}, "En az bir anahtar kelime"),
    ({"ad": "Y", "is_kollari": [], "kelimeler": ["x"]}, "En az bir iş kolu"),
    ({"ad": "vergi", "is_kollari": ["Ortak"], "kelimeler": ["x"]}, "başka bir konuda"),
])
def test_hatali_konu_kaydedilmez(ortam, govde, hata):
    client, _ = ortam
    cevap = _istek(client, "post", "/api/konular", {"haric": [], "dislanan": [], **govde})
    assert cevap.status_code == 400 and hata in cevap.json()["detail"]


def test_yeniden_adlandirma_kaynaklari_ve_takip_listesini_bozmaz(ortam, izlenen):
    client, engine = ortam
    masak = _konu(client, MASAK)
    assert _istek(client, "put", f"/api/konular/{masak['id']}", _form(masak, ad="MASAK ve yaptırımlar")).status_code == 200
    kuyum = _konu(client, KUYUM)
    cevap = _istek(client, "put", f"/api/konular/{kuyum['id']}", _form(kuyum, ad="Kuyumculuk"))
    assert cevap.status_code == 200 and cevap.json()["eski_adlar"] == [KUYUM]
    assert cevap.json()["eslesme_90"] == 2  # eski adla eşleşen kayıtlar da sayılır
    assert cevap.json()["kullanim"]["izlenen"]  # takip listesi eski adı kullanıyor, yine bağlı

    with Session(engine) as s:
        assert s.get(KaynakTanimi, "masak").varsayilan_konular == ["MASAK ve yaptırımlar"]
        kaynaklar, konular = tanimlar.kaynaklari_oku(s), tanimlar.konulari_oku(s)
        izlenenler = tanimlar.izlenenleri_esle(s, surum.izlenenleri_yukle(izlenen))
    assert izlenenler[0].konu == "Kuyumculuk"
    pipeline.ayarlari_dogrula(kaynaklar, konular, izlenenler)  # gece taraması başlar

    # Eski ad yeni bir konuya verilemez (takip listesi karışmasın); geri dönmek serbest.
    assert "başka bir konuda" in _istek(client, "post", "/api/konular",
                                        {"ad": KUYUM, "is_kollari": ["Kuyum"], "kelimeler": ["x"]}).json()["detail"]
    kuyum = _konu(client, "Kuyumculuk")
    cevap = _istek(client, "put", f"/api/konular/{kuyum['id']}", _form(kuyum, ad=KUYUM))
    assert cevap.status_code == 200 and cevap.json()["eski_adlar"] == ["Kuyumculuk"]


def test_kullanilan_konu_pasiflesmez_kullanilmayan_pasiflesir_ve_geri_gelir(ortam):
    client, engine = ortam
    masak, kuyum = _konu(client, MASAK), _konu(client, KUYUM)
    cevap = _istek(client, "post", f"/api/konular/{masak['id']}/pasif", {"surum": masak["surum"]})
    assert cevap.status_code == 400 and "MASAK Duyurusu" in cevap.json()["detail"]
    cevap = _istek(client, "post", f"/api/konular/{kuyum['id']}/pasif", {"surum": kuyum["surum"]})
    assert cevap.status_code == 400 and "izlenen_mevzuat.toml" in cevap.json()["detail"]

    kisisel = _konu(client, "Kişisel veriler")
    assert _istek(client, "post", f"/api/konular/{kisisel['id']}/pasif", {"surum": kisisel["surum"]}).status_code == 200
    with Session(engine) as s:
        assert "Kişisel veriler" not in [k.ad for k in tanimlar.konulari_oku(s)]
    kisisel = _konu(client, "Kişisel veriler")
    assert not kisisel["aktif"]
    assert _istek(client, "post", f"/api/konular/{kisisel['id']}/aktif", {"surum": kisisel["surum"]}).status_code == 200
    with Session(engine) as s:
        assert "Kişisel veriler" in [k.ad for k in tanimlar.konulari_oku(s)]


def test_girissiz_reddedilir(ortam):
    client, _ = ortam
    _istek(client, "post", "/api/cikis")
    assert client.get("/api/konular").status_code == 401
    assert _istek(client, "post", "/api/konular/onizleme", {"ad": "x", "kelimeler": ["x"]}).status_code == 401

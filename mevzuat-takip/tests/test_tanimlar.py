"""Kaynak ve konu tanımlarının DB'ye taşınması (#10 adım 1): toml sadece ilk kurulum tohumu."""

from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mevzuat import tanimlar
from mevzuat.db import KaynakDurumu, KaynakTanimi, KonuTanimi, alembic_config, init_db
from mevzuat.filtre import konu_tanimlari, konulari_yukle
from mevzuat.sources import kaynak_tanimlari, kaynaklari_yukle
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

ROOT = Path(__file__).parent.parent
KAYNAKLAR, KONULAR = ROOT / "config" / "kaynaklar.toml", ROOT / "config" / "konular.toml"


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _ozellik(kaynak) -> tuple:
    return type(kaynak), kaynak.ad, kaynak.etiket, kaynak.varsayilan_konular, sorted(vars(kaynak).items(), key=str)


def test_bos_dbye_bir_kez_aktarilir_okunan_tomldakiyle_ayni(session):
    assert tanimlar.tohumla(session, KAYNAKLAR, KONULAR) == {"konu": 8, "kaynak": 4}
    assert tanimlar.konulari_oku(session) == konulari_yukle(KONULAR)
    assert [_ozellik(k) for k in tanimlar.kaynaklari_oku(session)] == [_ozellik(k) for k in kaynaklari_yukle(KAYNAKLAR)]
    assert [k.ad for k in tanimlar.kaynaklari_oku(session)] == ["resmi_gazete", "masak", "gib_mevzuat", "mevzuat_gov_yeni"]  # sıra korunur

    # İkinci açılış: tablolar dolu, dosyaya hiç bakılmaz (silinmiş olsa da olur).
    assert tanimlar.tohumla(session, Path("yok/kaynaklar.toml"), Path("yok/konular.toml")) == {}
    assert session.scalar(select(KonuTanimi).where(KonuTanimi.ad == "Kıymetli madenler ve kuyumculuk")).surum == 1


def test_dosya_yoksa_ve_db_bossa_anlasilir_hata(session):
    with pytest.raises(SystemExit, match="ilk kurulum dosyası bulunamadı"):
        tanimlar.tohumla(session, Path("yok/kaynaklar.toml"), KONULAR)


def test_hatali_tomldan_hicbir_sey_aktarilmaz(session, tmp_path):
    yol = tmp_path / "k.toml"
    yol.write_text('[[kaynak]]\nad = "x"\ntip = "wordpress"\netiket = "X"\n', encoding="utf-8")  # api_url eksik
    with pytest.raises(ValueError, match="ayarları hatalı"):
        tanimlar.tohumla(session, yol, KONULAR)
    session.rollback()
    assert session.scalars(select(KaynakTanimi)).all() == []


def test_pasif_ve_kaldirilan_kaynak_taranmaz_pasif_konu_okunmaz(session):
    tanimlar.tohumla(session, KAYNAKLAR, KONULAR)
    session.get(KaynakTanimi, "masak").aktif = False
    session.get(KaynakTanimi, "gib_mevzuat").kaldirildi = True
    session.get(KaynakTanimi, "mevzuat_gov_yeni").kaldirildi = True
    konu = session.scalar(select(KonuTanimi).where(KonuTanimi.ad == "Kıymetli madenler ve kuyumculuk"))
    konu.aktif = False
    session.commit()
    assert [k.ad for k in tanimlar.kaynaklari_oku(session)] == ["resmi_gazete"]
    assert "Kıymetli madenler ve kuyumculuk" not in [k.ad for k in tanimlar.konulari_oku(session)]


def test_kelimeler_girildigi_gibi_saklanir_okurken_kucuk_harfe_cevrilir(session):
    tanimlar.tohumla(session, KAYNAKLAR, KONULAR)
    session.add(KonuTanimi(ad="Deneme", is_kollari=["Yeni İş Kolu"], kelimeler=["İTHALAT Rejimi"], haric=[], dislanan=[],
                           aktif=True, surum=1, guncelleme=datetime.now()))
    session.commit()
    assert session.scalar(select(KonuTanimi).where(KonuTanimi.ad == "Deneme")).kelimeler == ["İTHALAT Rejimi"]
    deneme = next(k for k in tanimlar.konulari_oku(session) if k.ad == "Deneme")
    assert deneme.kelimeler == ("ithalat rejimi",)
    assert "Yeni İş Kolu" in tanimlar.is_kollari(session) and "Ortak" in tanimlar.is_kollari(session)


def test_disa_aktarilan_toml_tekrar_tohum_olur(session, tmp_path):
    tanimlar.tohumla(session, KAYNAKLAR, KONULAR)
    session.get(KaynakTanimi, "masak").aktif = False
    session.commit()
    kaynak_metni, konu_metni = tanimlar.disa_aktar(session)
    (tmp_path / "k.toml").write_text(kaynak_metni, encoding="utf-8")
    (tmp_path / "c.toml").write_text(konu_metni, encoding="utf-8")

    assert konu_tanimlari(tmp_path / "c.toml") == konu_tanimlari(KONULAR)
    disari = kaynak_tanimlari(tmp_path / "k.toml")
    beklenen = kaynak_tanimlari(KAYNAKLAR)
    beklenen[1]["aktif"] = False
    assert disari == beklenen


def test_eski_veritabani_yukseltilir_checkpoint_gecerli_kalir(tmp_path, monkeypatch):
    """Önceki sürümün (0007) veritabanı: tablolar migration ile gelir, toml'dan dolar, checkpoint aynı adla."""
    url = f"sqlite:///{tmp_path / 'eski.db'}"
    command.upgrade(alembic_config(url), "0007")
    engine = create_engine(url)
    with Session(engine) as s:
        s.add(KaynakDurumu(kaynak="masak", checkpoint="2026-10-01T18:00:00", guncellendi=datetime.now()))
        s.commit()
    monkeypatch.setenv("MEVZUAT_KAYNAKLAR", str(KAYNAKLAR))
    monkeypatch.setenv("MEVZUAT_KONULAR", str(KONULAR))

    tanimlar.hazirla(engine)
    tanimlar.hazirla(engine)  # ikinci kez: tekrar aktarmaz

    with Session(engine) as s:
        assert len(s.scalars(select(KaynakTanimi)).all()) == 4 and len(s.scalars(select(KonuTanimi)).all()) == 8
        adlar = {k.ad for k in tanimlar.kaynaklari_oku(s)}
        assert {d.kaynak for d in s.scalars(select(KaynakDurumu))} <= adlar


def test_panel_is_kolu_listesi_her_istekte_dbden(tmp_path):
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        tanimlar.tohumla(s, KAYNAKLAR, KONULAR)
        kullanici_ekle(s, "sorumlu@firma.com", "Sorumlu", "onaylayici", "dogru-parola-123")
    client = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    csrf = client.get("/api/oturum").json()["csrf"]
    client.post("/api/giris", json={"eposta": "sorumlu@firma.com", "parola": "dogru-parola-123"},
                headers={"X-CSRF-Token": csrf})
    assert "Gümrük" not in client.get("/api/gruplar").json()["is_kollari"]

    with Session(engine) as s:
        s.add(KonuTanimi(ad="Gümrük", is_kollari=["Gümrük"], kelimeler=["gümrük"], haric=[], dislanan=[],
                         aktif=True, surum=1, guncelleme=datetime.now()))
        s.commit()
    assert "Gümrük" in client.get("/api/gruplar").json()["is_kollari"]  # yeniden başlatmadan


def test_panel_ayardan_kurulur_ve_tanimlari_aktarir(tmp_path, monkeypatch):
    """Docker provasında bulundu: uvicorn'un çağırdığı fabrika fonksiyonu hiçbir testte çalışmıyordu."""
    from mevzuat.web import ayardan_olustur

    monkeypatch.setenv("MEVZUAT_DB_URL", f"sqlite:///{tmp_path / 'p.db'}")
    monkeypatch.setenv("MEVZUAT_GIZLI_ANAHTAR", "t" * 40)
    monkeypatch.setenv("MEVZUAT_KAYNAKLAR", str(KAYNAKLAR))
    monkeypatch.setenv("MEVZUAT_KONULAR", str(KONULAR))
    monkeypatch.delenv("MEVZUAT_SMTP_HOST", raising=False)
    monkeypatch.chdir(tmp_path)  # proje klasöründeki .env okunmasın
    client = TestClient(ayardan_olustur())
    assert client.get("/saglik").status_code == 200
    with Session(create_engine(f"sqlite:///{tmp_path / 'p.db'}")) as s:
        assert len(tanimlar.kaynaklari_oku(s)) == 4


def test_ice_aktar_degisikligi_uygular_dosyada_olmayani_kaldirir(session, tmp_path):
    tanimlar.tohumla(session, KAYNAKLAR, KONULAR)
    kaynak_metni, konu_metni = tanimlar.disa_aktar(session)
    # Değişiklik: bir konuya kelime eklendi, bir konu silindi, yeni konu eklendi; GİB kaynağı silindi.
    konu_metni = konu_metni.replace('"kuyum",', '"kuyum", "sarrafiye",', 1)
    bloklar = konu_metni.split("\n\n")
    silinen = next(b for b in bloklar if 'ad = "Vergi' in b)
    konu_metni = "\n\n".join(b for b in bloklar if b is not silinen) + '\n[[konu]]\nad = "Gümrük"\nis_kollari = ["Ortak"]\nkelimeler = ["gümrük"]\n'
    kaynak_metni = "\n\n[[kaynak]]".join(b for b in kaynak_metni.split("\n\n[[kaynak]]") if 'ad = "gib_mevzuat"' not in b)
    (tmp_path / "k.toml").write_text(kaynak_metni, encoding="utf-8")
    (tmp_path / "c.toml").write_text(konu_metni, encoding="utf-8")

    ozet = tanimlar.ice_aktar(session, tmp_path / "k.toml", tmp_path / "c.toml")

    vergi = silinen.splitlines()[1].split('"')[1]
    assert ozet["konu_degisen"] == ["Kıymetli madenler ve kuyumculuk"]
    assert ozet["konu_eklenen"] == ["Gümrük"] and ozet["konu_kaldirilan"] == [vergi]
    assert ozet["kaynak_kaldirilan"] == ["gib_mevzuat"] and ozet["kaynak_degisen"] == ["mevzuat_gov_yeni"]  # sırası bir öne kaydı
    kuyum = next(k for k in tanimlar.konulari_oku(session) if k.ad == "Kıymetli madenler ve kuyumculuk")
    assert "sarrafiye" in kuyum.kelimeler
    assert session.scalar(select(KonuTanimi).where(KonuTanimi.ad == "Kıymetli madenler ve kuyumculuk")).surum == 2
    assert vergi not in [k.ad for k in tanimlar.konulari_oku(session)]
    assert [k.ad for k in tanimlar.kaynaklari_oku(session)] == ["resmi_gazete", "masak", "mevzuat_gov_yeni"]

    # Aynı dosya ikinci kez: değişiklik yok, sürüm artmaz.
    assert not any(tanimlar.ice_aktar(session, tmp_path / "k.toml", tmp_path / "c.toml").values())
    # Eski dosyaya dönmek kaldırılanları geri getirir.
    tanimlar.ice_aktar(session, KAYNAKLAR, KONULAR)
    assert tanimlar.konulari_oku(session) == konulari_yukle(KONULAR)
    assert [k.ad for k in tanimlar.kaynaklari_oku(session)] == ["resmi_gazete", "masak", "gib_mevzuat", "mevzuat_gov_yeni"]


def test_ice_aktarmada_hata_varsa_hicbir_sey_yazilmaz(session, tmp_path):
    tanimlar.tohumla(session, KAYNAKLAR, KONULAR)
    kaynak_metni, konu_metni = tanimlar.disa_aktar(session)
    (tmp_path / "k.toml").write_text(kaynak_metni.replace("MASAK / suç gelirleri / yaptırımlar", "Yanlış Konu"),
                                     encoding="utf-8")
    (tmp_path / "c.toml").write_text(konu_metni.replace('"kuyum",', '"kuyum", "sarrafiye",', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="tanımsız konu"):
        tanimlar.ice_aktar(session, tmp_path / "k.toml", tmp_path / "c.toml")
    assert tanimlar.konulari_oku(session) == konulari_yukle(KONULAR)

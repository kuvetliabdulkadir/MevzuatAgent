"""Zamanlayıcı servisi + tarama saatleri panelden (4. iş). Sahte saat ve sahte süreçle, beklemeden."""

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import zamanlama as zm
from mevzuat.db import Calisma, Denetim, SistemAyari, init_db
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

PAROLA = "dogru-parola-123"


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.delenv("MEVZUAT_CALISMA_SAATLERI", raising=False)
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(e)
    return e


class SahteSurec:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


class Saat:
    def __init__(self, simdi: datetime):
        self.simdi = simdi

    def __call__(self):
        return self.simdi

    def ilerle(self, **sure):
        self.simdi += timedelta(**sure)


def _zamanlayici(engine, simdi, gecikme=0.0):
    saat, baslatilan = Saat(simdi), []

    def baslat(istek_id):
        baslatilan.append(saat())
        return SahteSurec()

    return zm.Zamanlayici(engine, baslat=baslat, saat=saat, gecikme=lambda: gecikme), saat, baslatilan


# ---- saat kuralları -------------------------------------------------------------------------------------

@pytest.mark.parametrize("liste, hata", [
    ([], "En az 1"),
    (["01:00", "05:00", "08:00", "11:00", "14:00", "17:00", "20:00"], "en fazla 6"),
    (["6.30"], "SS:DD"),
    (["25:00"], "SS:DD"),
    (["06:30", "07:00"], "en az 1 saat"),
    (["23:30", "00:10"], "en az 1 saat"),  # gece yarısından geçerken de
])
def test_hatali_saatler(liste, hata):
    with pytest.raises(ValueError, match=hata):
        zm.saatleri_dogrula(liste)


def test_saatler_siralanir_tekrar_atilir():
    assert zm.saatleri_dogrula(["18:00", " 06:30", "06:30"]) == [time(6, 30), time(18, 0)]


def test_ilk_okumada_ortam_degiskeninden_sonra_db(engine, monkeypatch):
    monkeypatch.setenv("MEVZUAT_CALISMA_SAATLERI", "07:00,19:00")
    with Session(engine) as s:
        assert zm.saatleri_oku(s) == [time(7), time(19)]
    monkeypatch.setenv("MEVZUAT_CALISMA_SAATLERI", "08:00")  # artık DB esas
    with Session(engine) as s:
        assert zm.saatleri_oku(s) == [time(7), time(19)]


def test_kacan_calisma_kurali():
    saatler = [time(6, 30), time(18)]
    sabah = datetime(2026, 10, 3, 9, 0)
    assert zm.kacan_calisma_var_mi(sabah, saatler, datetime(2026, 10, 2, 18, 5))  # 06:30 kaçmış
    assert not zm.kacan_calisma_var_mi(sabah, saatler, datetime(2026, 10, 3, 6, 34))  # yapılmış
    # 06:30'dan 12 saatten fazla geçmiş (18:00 da geçti → son planlı 18:00, o da kaçmış ama pencere içinde)
    assert zm.kacan_calisma_var_mi(datetime(2026, 10, 3, 19, 0), saatler, datetime(2026, 10, 3, 6, 34))
    # Tek saat, 13 saat sonra açıldı: pencere dışı, bir sonraki planlıyı bekler.
    assert not zm.kacan_calisma_var_mi(datetime(2026, 10, 3, 19, 31), [time(6, 30)], None)


# ---- döngü ----------------------------------------------------------------------------------------------

def test_planli_saatte_calisir_gecikmeyle_ve_nabiz_yazar(engine):
    z, saat, baslatilan = _zamanlayici(engine, datetime(2026, 10, 3, 6, 0), gecikme=120)
    with Session(engine) as s:
        s.add(Calisma(baslangic=datetime(2026, 10, 2, 18, 3), durum="BASARILI", ozet={}))
        s.commit()
    z.kur()
    assert baslatilan == [] and z.hedef == datetime(2026, 10, 3, 6, 32)
    saat.ilerle(minutes=31)
    z.adim()
    assert baslatilan == []  # 06:31: rastgele gecikme henüz dolmadı
    saat.ilerle(minutes=1)
    z.adim()
    assert baslatilan == [datetime(2026, 10, 3, 6, 32)] and z.hedef == datetime(2026, 10, 3, 18, 2)
    with Session(engine) as s:
        nabiz = s.get(SistemAyari, zm.NABIZ).deger
    assert nabiz["durum"] == "tarama" and nabiz["sonraki"] == "2026-10-03T18:02"
    z.surec.returncode = 0
    saat.ilerle(seconds=30)
    z.adim()
    assert z.surec is None
    with Session(engine) as s:
        assert s.get(SistemAyari, zm.NABIZ).deger["durum"] == "bekliyor"


def test_kacan_calisma_acilista_bir_kez(engine):
    z, _, baslatilan = _zamanlayici(engine, datetime(2026, 10, 3, 8, 0))
    z.kur()  # hiç çalışma yok, 06:30 1,5 saat önce
    assert baslatilan == [datetime(2026, 10, 3, 8, 0)] and z.hedef == datetime(2026, 10, 3, 18, 0)


def test_saat_degisince_hedef_degisir_kacan_saat_kaybolmaz(engine):
    z, saat, baslatilan = _zamanlayici(engine, datetime(2026, 10, 3, 7, 0))
    with Session(engine) as s:
        s.add(Calisma(baslangic=datetime(2026, 10, 3, 6, 31), durum="BASARILI", ozet={}))
        s.commit()
    z.kur()
    assert z.hedef == datetime(2026, 10, 3, 18, 0)
    with Session(engine) as s:
        zm.saatleri_kaydet(s, ["06:30", "09:00", "18:00"], 1, None)
        s.commit()
    saat.ilerle(minutes=5)
    z.adim()
    assert z.hedef == datetime(2026, 10, 3, 9, 0) and baslatilan == []
    saat.ilerle(hours=2)
    z.adim()
    assert baslatilan == [datetime(2026, 10, 3, 9, 5)]


def test_tarama_surerken_vakti_gelen_calisma_atlanir(engine):
    z, saat, baslatilan = _zamanlayici(engine, datetime(2026, 10, 3, 17, 0))
    with Session(engine) as s:
        s.add(Calisma(baslangic=datetime(2026, 10, 3, 6, 31), durum="BASARILI", ozet={}))
        s.commit()
    z.kur(hemen=True)  # elle başlatılan tarama hâlâ sürüyor
    assert len(baslatilan) == 1
    z.hedef = datetime(2026, 10, 3, 17, 30)
    saat.ilerle(minutes=40)
    z.adim()
    assert len(baslatilan) == 1 and z.hedef > saat()  # iki tarama üst üste binmedi


# ---- panel ----------------------------------------------------------------------------------------------

@pytest.fixture
def panel(engine):
    with Session(engine) as s:
        kullanici_ekle(s, "onay@firma.com", "Onay", "onaylayici", PAROLA)
    client = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    csrf = client.get("/api/oturum").json()["csrf"]
    client.post("/api/giris", json={"eposta": "onay@firma.com", "parola": PAROLA}, headers={"X-CSRF-Token": csrf})
    return client


def _put(client, veri):
    return client.put("/api/zamanlama", json=veri, headers={"X-CSRF-Token": client.get("/api/oturum").json()["csrf"]})


def test_panel_saatleri_degistirir_denetime_yazar(panel, engine):
    durum = panel.get("/api/zamanlama").json()
    assert durum["saatler"] == ["06:30", "18:00"] and not durum["zamanlayici_calisiyor"]
    cevap = _put(panel, {"saatler": ["07:00", "12:00", "19:00"], "surum": durum["surum"]})
    assert cevap.status_code == 200 and cevap.json()["saatler"] == ["07:00", "12:00", "19:00"]
    assert _put(panel, {"saatler": ["08:00"], "surum": durum["surum"]}).status_code == 409  # eski form
    assert "en az 1 saat" in _put(panel, {"saatler": ["07:00", "07:30"], "surum": 2}).json()["detail"]
    with Session(engine) as s:
        d = s.scalar(select(Denetim).where(Denetim.islem == "tarama_saatleri"))
        assert d.detay == {"once": ["06:30", "18:00"], "sonra": ["07:00", "12:00", "19:00"]}


def test_panel_nabizdan_zamanlayici_durumunu_gosterir(panel, engine):
    with Session(engine) as s:
        zm.nabiz_yaz(s, datetime.now() - timedelta(seconds=40), "bekliyor", datetime(2030, 1, 1, 6, 33), [time(6, 30)])
    durum = panel.get("/api/zamanlama").json()
    assert durum["zamanlayici_calisiyor"] and durum["durum"] == "bekliyor" and durum["sonraki"] == "2030-01-01T06:33"
    with Session(engine) as s:
        zm.nabiz_yaz(s, datetime.now() - timedelta(minutes=5), "bekliyor", None, [time(6, 30)])
    assert not panel.get("/api/zamanlama").json()["zamanlayici_calisiyor"]  # nabız 2 dk'dan eski

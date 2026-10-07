"""Panelden "Şimdi tara" (5. iş): panel istek yazar, zamanlayıcı alır ve sonuçlandırır. Günlük iş sahte."""

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import zamanlama as zm
from mevzuat.db import Calisma, Denetim, Rapor, TaramaIstegi, init_db
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

PAROLA = "dogru-parola-123"


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.delenv("MEVZUAT_CALISMA_SAATLERI", raising=False)
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(e)
    with Session(e) as s:
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
    return e


def _nabiz(engine, durum="bekliyor", once=timedelta(seconds=10)):
    with Session(engine) as s:
        zm.nabiz_yaz(s, datetime.now() - once, durum, datetime.now() + timedelta(hours=5), [time(6, 30), time(18)])


@pytest.fixture
def client(engine):
    c = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    _post(c, "/api/giris", {"eposta": "onay@firma.com", "parola": PAROLA})
    return c


def _post(c, yol, veri=None):
    return c.post(yol, json=veri or {}, headers={"X-CSRF-Token": c.get("/api/oturum").json()["csrf"]})


class SahteGunluk:
    """Günlük işin yaptığını taklit eder: çalışma kaydı (ve isterse rapor) yazar, sonra 'biter'."""

    def __init__(self, engine, durum="BASARILI", rapor=True):
        self.engine, self.durum, self.rapor, self.returncode = engine, durum, rapor, None

    def __call__(self, istek_id):
        self.istek_id = istek_id
        with Session(self.engine) as s:
            calisma = Calisma(baslangic=datetime.now(), bitis=datetime.now(), durum=self.durum,
                              ozet={"resmi_gazete": {"yeni": 3}, "masak": {"yeni": 1}},
                              hata=None if self.durum == "BASARILI" else "masak: ConnectError('kapalı')")
            s.add(calisma)
            s.flush()
            if istek_id is not None:  # gerçek günlük iş gibi: sonucu isteğe kendisi bağlar
                zm.istege_yaz(s, istek_id, calisma_id=calisma.id)
            if self.rapor:
                rapor = Rapor(olusturuldu=datetime.now(), durum="ONAY_BEKLIYOR", konu="R", alicilar=[], kayit_sayisi=1)
                s.add(rapor)
                s.flush()
                if istek_id is not None:
                    zm.istege_yaz(s, istek_id, rapor_id=rapor.id)
            s.commit()
        return self

    def poll(self):
        return self.returncode


def _zamanlayici(engine, gunluk):
    z = zm.Zamanlayici(engine, baslat=gunluk, gecikme=lambda: 0)
    z.saatler = [time(6, 30), time(18)]
    z.baz = datetime.now()
    z.hedef = datetime.now() + timedelta(hours=5)  # planlı tarama araya girmesin
    return z


def test_istek_zamanlayici_alir_sonucu_panelde(engine, client):
    _nabiz(engine)
    cevap = _post(client, "/api/tarama")
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["istek"]["durum"] == "BEKLIYOR" and cevap.json()["istek"]["isteyen"] == "Ayşe Onay"
    assert _post(client, "/api/tarama").status_code == 409  # ikinci istek

    gunluk = SahteGunluk(engine)
    z = _zamanlayici(engine, gunluk)
    z.adim()  # isteği alır, günlük işi başlatır
    assert client.get("/api/tarama/durum").json()["istek"]["durum"] == "CALISIYOR"
    assert client.get("/api/tarama/durum").json()["zamanlama"]["durum"] == "tarama"
    gunluk.returncode = 0
    z.adim()  # bitti
    durum = client.get("/api/tarama/durum").json()
    istek = durum["istek"]
    assert istek["durum"] == "BITTI" and istek["rapor_id"] is not None
    assert istek["calisma"]["yeni_toplam"] == 4 and istek["calisma"]["yeni"] == {"resmi_gazete": 3, "masak": 1}
    assert durum["son_calismalar"][0]["id"] == istek["calisma"]["id"]
    assert _post(client, "/api/tarama").status_code == 200  # bitti: yeniden istenebilir
    with Session(engine) as s:
        assert [d.islem for d in s.scalars(select(Denetim).where(Denetim.islem == "tarama_istegi"))] == ["tarama_istegi"] * 2


def test_hatali_tarama_ve_baslamayan_tarama(engine, client):
    _nabiz(engine)
    _post(client, "/api/tarama")
    gunluk = SahteGunluk(engine, durum="HATALI", rapor=False)
    z = _zamanlayici(engine, gunluk)
    z.adim()
    gunluk.returncode = 1
    z.adim()
    istek = client.get("/api/tarama/durum").json()["istek"]
    assert istek["durum"] == "HATALI" and "ConnectError" in istek["hata"] and istek["rapor_id"] is None

    # Kilit yüzünden hiç başlamayan günlük iş (çalışma kaydı yok).
    _post(client, "/api/tarama")

    class Baslamayan:
        returncode = 0

        def __init__(self, istek_id):
            pass

        def poll(self):
            return 0

    z.baslat = Baslamayan
    z.adim()  # alır
    z.adim()  # bitti, çalışma yok
    istek = client.get("/api/tarama/durum").json()["istek"]
    assert istek["durum"] == "HATALI" and "başlamadı" in istek["hata"]


@pytest.mark.parametrize("nabiz, hata", [
    (None, "Zamanlayıcı çalışmıyor"),
    ({"once": timedelta(minutes=5)}, "Zamanlayıcı çalışmıyor"),
    ({"durum": "tarama"}, "planlı tarama sürüyor"),
])
def test_istek_kabul_edilmeyen_durumlar(engine, client, nabiz, hata):
    if nabiz is not None:
        _nabiz(engine, **nabiz)
    cevap = _post(client, "/api/tarama")
    assert cevap.status_code == 409 and hata in cevap.json()["detail"]
    with Session(engine) as s:
        assert s.scalars(select(TaramaIstegi)).all() == []


def test_zamanlayici_yeniden_baslarsa_yarim_istek_kapanir(engine, client):
    _nabiz(engine)
    _post(client, "/api/tarama")
    z = _zamanlayici(engine, SahteGunluk(engine))
    z.adim()
    z2 = zm.Zamanlayici(engine, baslat=SahteGunluk(engine), gecikme=lambda: 0)
    z2.kur()
    istek = client.get("/api/tarama/durum").json()["istek"]
    assert istek["durum"] == "HATALI" and "yeniden başladı" in istek["hata"]


def test_girissiz_reddedilir(engine):
    c = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    assert c.get("/api/tarama/durum").status_code == 401
    assert _post(c, "/api/tarama").status_code == 401


def test_ayni_anda_iki_istek_db_kurali_yakalar(engine, client, monkeypatch):
    """İki tıklama aynı anda: ikisi de 'aktif istek yok' kontrolünü geçse bile ikinci kayıt DB'ye giremez."""
    _nabiz(engine)
    with Session(engine) as s:
        s.add(TaramaIstegi(isteyen_id=None, istendi=datetime.now(), durum="BEKLIYOR"))
        s.commit()
    monkeypatch.setattr(zm, "AKTIF_ISTEK", ())  # uygulama kontrolünü devre dışı bırak: yarışı taklit eder
    cevap = _post(client, "/api/tarama")
    assert cevap.status_code == 409 and "zaten sırada" in cevap.json()["detail"]
    with Session(engine) as s:
        assert len(s.scalars(select(TaramaIstegi)).all()) == 1


def test_surec_baslatilamazsa_istek_takili_kalmaz(engine, client):
    _nabiz(engine)
    _post(client, "/api/tarama")

    def baslatilamayan(istek_id):
        raise OSError("bellek yok")

    z = _zamanlayici(engine, baslatilamayan)
    z.adim()
    assert z.surec is None and z.istek_id is None
    istek = client.get("/api/tarama/durum").json()["istek"]
    assert istek["durum"] == "HATALI" and "başlatılamadı" in istek["hata"] and "bellek yok" in istek["hata"]
    assert _post(client, "/api/tarama").status_code == 200  # yeni istek açılabilir


def test_baska_taramanin_sonucu_istege_baglanmaz(engine, client):
    """Tarama sürerken elle başlatılan başka bir çalışma ve rapor isteğe karışmaz: bağlantıyı günlük iş yazar."""
    _nabiz(engine)
    _post(client, "/api/tarama")
    gunluk = SahteGunluk(engine, rapor=False)
    z = _zamanlayici(engine, gunluk)
    z.adim()
    SahteGunluk(engine)(None)  # elle CLI ile çalışan ayrı tarama: çalışma + rapor, isteksiz
    gunluk.returncode = 0
    z.adim()
    istek = client.get("/api/tarama/durum").json()["istek"]
    with Session(engine) as s:
        ilk_calisma = s.scalar(select(Calisma).order_by(Calisma.id))
    assert istek["durum"] == "BITTI" and istek["calisma"]["id"] == ilk_calisma.id and istek["rapor_id"] is None

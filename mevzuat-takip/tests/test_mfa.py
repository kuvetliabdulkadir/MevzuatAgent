import time

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat.db import Denetim, Kullanici, init_db
from mevzuat.web import MFA_BEKLEME, uygulama_olustur
from mevzuat.web import guvenlik
from mevzuat.web.guvenlik import MAX_DENEME, kullanici_ekle

PAROLA = "dogru-parola-2026"
ANAHTAR = "t" * 40
EPOSTA = "sorumlu@firma.com"


class Posta:
    def gonder(self, mail):
        pass


def _ortam(mfa_zorunlu: bool):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        kullanici_ekle(s, EPOSTA, "Ayşe Sorumlu", "onaylayici", PAROLA)
    app = uygulama_olustur(engine, Posta(), [], ANAHTAR, ek_ekle=False, mfa_zorunlu=mfa_zorunlu, arayuz=None)
    return TestClient(app), engine


@pytest.fixture
def zorunlu():
    return _ortam(mfa_zorunlu=True)


def _post(client, yol, veri):
    return client.post(yol, json=veri, headers={"X-CSRF-Token": client.get("/api/oturum").json()["csrf"]})


def _parola(client) -> str:
    cevap = _post(client, "/api/giris", {"eposta": EPOSTA, "parola": PAROLA})
    assert cevap.status_code == 200
    return cevap.json()["sonraki"]


def _gizli(engine) -> str:
    with Session(engine) as s:
        return s.scalar(select(Kullanici)).mfa_gizli


def _kod(gizli: str, kaydir: int = 0) -> str:
    return pyotp.TOTP(gizli).at(time.time() + kaydir * 30)


def _kur(client, engine) -> str:
    """Parola + kurulum. Döner: sır."""
    assert _parola(client) == "kurulum"
    client.get("/api/giris/mfa-kurulum")
    gizli = _gizli(engine)
    assert _post(client, "/api/giris/mfa-kurulum", {"kod": _kod(gizli)}).json()["sonraki"] == "panel"
    return gizli


def _cikis(client):
    _post(client, "/api/cikis", {})


def _islemler(engine) -> list[str]:
    with Session(engine) as s:
        return [d.islem for d in s.scalars(select(Denetim).order_by(Denetim.id))]


def test_zorunluyken_parola_tek_basina_oturum_acmaz(zorunlu):
    client, engine = zorunlu
    assert _parola(client) == "kurulum"
    assert client.get("/api/oturum").json() | {"csrf": None} == {"csrf": None, "kullanici": None, "mfa": "kurulum", "mail_kapali": False}
    assert client.get("/api/raporlar").status_code == 401  # kod girilmeden panel açılmaz
    assert "parola_dogrulandi" in _islemler(engine) and "giris" not in _islemler(engine)


def test_kurulum_qr_ve_ilk_kod(zorunlu):
    client, engine = zorunlu
    _parola(client)
    bilgi = client.get("/api/giris/mfa-kurulum").json()
    gizli = _gizli(engine)
    assert bilgi["gizli"] == " ".join(gizli[i:i + 4] for i in range(0, len(gizli), 4))
    qr = client.get("/api/giris/mfa-qr.svg")
    assert qr.headers["content-type"].startswith("image/svg+xml") and "<svg" in qr.text and "<script" not in qr.text
    # Sayfa yenilenince sır değişmez (kullanıcı QR'ı okuttuysa geçersiz kalmasın)
    client.get("/api/giris/mfa-kurulum")
    assert _gizli(engine) == gizli

    cevap = _post(client, "/api/giris/mfa-kurulum", {"kod": "000000"})
    assert cevap.status_code == 400 and "Kod hatalı" in cevap.json()["detail"]

    assert _post(client, "/api/giris/mfa-kurulum", {"kod": _kod(gizli)}).status_code == 200
    assert client.get("/api/raporlar").status_code == 200
    with Session(engine) as s:
        assert s.scalar(select(Kullanici)).mfa_aktif
    assert "mfa_kuruldu" in _islemler(engine) and _islemler(engine)[-1] == "giris"
    # Kurulum bitti: QR artık verilmez (sır başkasının eline geçmesin)
    assert client.get("/api/giris/mfa-qr.svg").status_code == 401


def test_kurulmus_kullanici_kod_ister_ve_ayni_kod_iki_kez_gecmez(zorunlu):
    client, engine = zorunlu
    gizli = _kur(client, engine)
    ilk_kod = _kod(gizli)  # kurulumda kullanıldı
    _cikis(client)

    assert _parola(client) == "kod"
    assert client.get("/api/giris/mfa-qr.svg").status_code == 401  # kurulu kullanıcıya QR gösterilmez
    assert _post(client, "/api/giris/kod", {"kod": ilk_kod}).status_code == 400  # tekrar kullanım
    assert _post(client, "/api/giris/kod", {"kod": _kod(gizli, kaydir=1)}).json()["sonraki"] == "panel"


def test_hatali_kodlar_hesabi_kilitler(zorunlu):
    client, engine = zorunlu
    gizli = _kur(client, engine)
    _cikis(client)
    _parola(client)
    for _ in range(MAX_DENEME):
        _post(client, "/api/giris/kod", {"kod": "123456"})
    cevap = _post(client, "/api/giris/kod", {"kod": _kod(gizli, kaydir=1)})
    assert cevap.status_code == 400  # doğru kod bile kilit süresince geçmez
    assert "hesap_kilitlendi" in _islemler(engine)


def test_kod_bekleme_suresi_dolar(zorunlu, monkeypatch):
    client, engine = zorunlu
    gizli = _kur(client, engine)
    _cikis(client)
    _parola(client)
    csrf = client.get("/api/oturum").json()["csrf"]
    gercek = time.time
    monkeypatch.setattr(time, "time", lambda: gercek() + MFA_BEKLEME + 1)
    cevap = client.post("/api/giris/kod", json={"kod": _kod(gizli, kaydir=1)}, headers={"X-CSRF-Token": csrf})
    assert cevap.status_code == 401


def test_kod_adimlarina_parolasiz_girilemez(zorunlu):
    client, _ = zorunlu
    for yol in ["/api/giris/mfa-kurulum", "/api/giris/mfa-qr.svg"]:
        assert client.get(yol).status_code == 401
    assert _post(client, "/api/giris/kod", {"kod": "123456"}).status_code == 401


def test_mfa_kapaliyken_kurmamis_kullanici_dogrudan_girer():
    client, engine = _ortam(mfa_zorunlu=False)
    assert _parola(client) == "panel"
    assert _islemler(engine)[-1] == "giris"


def test_kapaliyken_bile_kurmus_kullanicidan_kod_istenir(zorunlu):
    client, engine = zorunlu
    _kur(client, engine)
    acik = TestClient(uygulama_olustur(engine, Posta(), [], ANAHTAR, ek_ekle=False, mfa_zorunlu=False, arayuz=None))
    assert _parola(acik) == "kod"


def test_sifirlama_sonrasi_yeniden_kurulum(zorunlu):
    client, engine = zorunlu
    eski = _kur(client, engine)
    with Session(engine) as s:
        guvenlik.mfa_sifirla(s, s.scalar(select(Kullanici)))
    _cikis(client)
    assert _parola(client) == "kurulum"
    client.get("/api/giris/mfa-kurulum")
    assert _gizli(engine) != eski
    assert "mfa_sifirlandi" in _islemler(engine)


def test_kod_bicimi_ve_pencere():
    gizli = pyotp.random_base32()
    simdi = 1_800_000_000.0
    totp = pyotp.TOTP(gizli)
    assert guvenlik._kod_adimi(gizli, totp.at(simdi), simdi) is not None
    assert guvenlik._kod_adimi(gizli, totp.at(simdi - 30), simdi) is not None  # telefon 30 sn geride
    assert guvenlik._kod_adimi(gizli, totp.at(simdi - 90), simdi) is None
    kod = totp.at(simdi)
    assert guvenlik._kod_adimi(gizli, f"{kod[:3]} {kod[3:]}", simdi) is not None  # "123 456" yazılabilir
    assert guvenlik._kod_adimi(gizli, "12ab56", simdi) is None


# ---- açık oturumların geçersizleşmesi -------------------------------------------------------------

def test_mfa_sonradan_zorunlu_yapilinca_kodsuz_eski_oturum_gecmez():
    """Tarayıcıda denerken bulundu: MFA kapalıyken açılmış çerez, MFA açılınca da geçiyordu."""
    client, engine = _ortam(mfa_zorunlu=False)
    assert _parola(client) == "panel"
    assert client.get("/api/raporlar").status_code == 200
    zorunlu = TestClient(uygulama_olustur(engine, Posta(), [], ANAHTAR, ek_ekle=False, mfa_zorunlu=True, arayuz=None))
    zorunlu.cookies = client.cookies  # aynı tarayıcı, sunucu MFA=1 ile yeniden başlatıldı
    assert zorunlu.get("/api/raporlar").status_code == 401


def test_mfa_sifirlaninca_acik_oturum_biter(zorunlu):
    client, engine = zorunlu
    _kur(client, engine)
    assert client.get("/api/raporlar").status_code == 200
    with Session(engine) as s:
        guvenlik.mfa_sifirla(s, s.scalar(select(Kullanici)))
    assert client.get("/api/raporlar").status_code == 401


def test_parola_degisince_ve_ayni_numara_baska_kisiye_gecince_oturum_biter():
    client, engine = _ortam(mfa_zorunlu=False)
    _parola(client)
    with Session(engine) as s:
        s.scalar(select(Kullanici)).parola_hash = guvenlik.parola_hashle("yeni-parola-2026-xyz")
        s.commit()
    assert client.get("/api/raporlar").status_code == 401

    client, engine = _ortam(mfa_zorunlu=False)
    _parola(client)
    with Session(engine) as s:  # yedekten dönüş vb.: aynı id artık başka bir kişi
        s.scalar(select(Kullanici)).eposta = "baska@firma.com"
        s.commit()
    assert client.get("/api/raporlar").status_code == 401

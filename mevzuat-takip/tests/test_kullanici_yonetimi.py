"""Panelden kullanıcı yönetimi (admin): davet ve sıfırlama linki (mail), pasifleştirme, parolamı değiştir,
denetim kaydı."""

import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat.db import Denetim, Kullanici, ParolaLinki, init_db
from mevzuat.mail import Mail
from mevzuat.web import guvenlik, uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

PAROLA = "dogru-parola-123"
YENI = "yepyeni-parola-456"
PANEL = "https://mevzuat.firma.com.tr"


class Posta:
    def __init__(self):
        self.giden: list[Mail] = []
        self.bozuk = False

    def gonder(self, mail: Mail) -> None:
        if self.bozuk:
            raise ConnectionError("smtp kapalı")
        self.giden.append(mail)

    def son_link(self) -> str:
        return re.search(r"\?parola=([A-Za-z0-9_-]+)", self.giden[-1].metin).group(1)


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(e)
    with Session(e) as s:
        kullanici_ekle(s, "admin@firma.com", "Ali Admin", "admin", PAROLA)
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
    return e


@pytest.fixture
def posta():
    return Posta()


def _istemci(engine, posta, panel_adresi=PANEL):
    return TestClient(uygulama_olustur(engine, posta, None, "t" * 40, ek_ekle=False, arayuz=None,
                                       panel_adresi=panel_adresi))


def _post(c, yol, veri=None):
    return c.post(yol, json=veri or {}, headers={"X-CSRF-Token": c.get("/api/oturum").json()["csrf"]})


def _giris(c, eposta, parola=PAROLA):
    return _post(c, "/api/giris", {"eposta": eposta, "parola": parola})


@pytest.fixture
def admin(engine, posta):
    c = _istemci(engine, posta)
    assert _giris(c, "admin@firma.com").status_code == 200
    return c


def _kullanici(engine, eposta) -> Kullanici:
    with Session(engine) as s:
        return guvenlik.kullanici_bul(s, eposta)


def test_davet_ile_yeni_kullanici_parolasini_kendisi_belirler(engine, posta, admin):
    cevap = _post(admin, "/api/kullanicilar", {"eposta": "Yeni@Firma.com", "ad": " Veli  Yeni ", "rol": "onaylayici"})
    assert cevap.status_code == 200 and cevap.json()["mesaj"]["tur"] == "basari", cevap.text
    yeni = next(k for k in cevap.json()["kullanicilar"] if k["eposta"] == "yeni@firma.com")
    assert yeni["ad"] == "Veli Yeni" and yeni["davet_bekliyor"] and yeni["aktif"]

    mail = posta.giden[-1]
    assert mail.alicilar == ["yeni@firma.com"] and "davet" in mail.konu
    assert f"{PANEL}/?parola=" in mail.html and "Onaylayıcı" in mail.metin
    token = posta.son_link()

    c = _istemci(engine, posta)
    assert _giris(c, "yeni@firma.com", "bilinmiyor-123456").status_code == 400  # davetle parola belirlenmeden
    kontrol = _post(c, "/api/parola-linki/kontrol", {"token": token})
    assert kontrol.json() == {"eposta": "yeni@firma.com", "ad": "Veli Yeni", "tur": "davet"}
    assert _post(c, "/api/parola-linki/kullan", {"token": token, "parola": "kisa"}).status_code == 400
    assert _post(c, "/api/parola-linki/kullan", {"token": token, "parola": YENI}).status_code == 200
    assert _post(c, "/api/parola-linki/kullan", {"token": token, "parola": YENI}).status_code == 400  # tek kullanım
    assert _giris(c, "yeni@firma.com", YENI).json()["sonraki"] == "panel"
    with Session(engine) as s:
        assert s.scalar(select(ParolaLinki.ozet)) != token  # link DB'de açık durmaz


def test_sifirlama_linki_eski_linki_iptal_eder_ve_oturumlari_kapatir(engine, posta, admin):
    onay = _istemci(engine, posta)
    _giris(onay, "onay@firma.com")
    assert onay.get("/api/raporlar").status_code == 200
    with Session(engine) as s:  # onaylayıcı daha önce giriş yaptı: link "sıfırlama" olur
        assert s.get(Kullanici, _kullanici(engine, "onay@firma.com").id).son_giris is not None
    oid = _kullanici(engine, "onay@firma.com").id

    _post(admin, f"/api/kullanicilar/{oid}/parola-linki")
    ilk = posta.son_link()
    cevap = _post(admin, f"/api/kullanicilar/{oid}/parola-linki")
    assert cevap.json()["mesaj"]["tur"] == "basari" and "sıfırlama" in posta.giden[-1].konu
    ikinci = posta.son_link()

    c = _istemci(engine, posta)
    assert _post(c, "/api/parola-linki/kontrol", {"token": ilk}).status_code == 400  # yenisi gidince eskisi geçersiz
    assert _giris(onay, "onay@firma.com").status_code == 200  # eski parola link kullanılana kadar geçerli
    assert _post(c, "/api/parola-linki/kullan", {"token": ikinci, "parola": YENI}).status_code == 200
    assert onay.get("/api/raporlar").status_code == 401  # açık oturum kapandı
    assert _giris(c, "onay@firma.com", PAROLA).status_code == 400
    assert _giris(c, "onay@firma.com", YENI).status_code == 200


def test_suresi_dolan_link_gecmez(engine):
    with Session(engine) as s:
        k = guvenlik.kullanici_bul(s, "onay@firma.com")
        token, son = guvenlik.parola_linki_olustur(s, k, "sifirlama", None, simdi=datetime(2026, 10, 1, 9))
        s.commit()
        assert son == datetime(2026, 10, 1, 10)
        assert guvenlik.parola_linki_bul(s, token, simdi=datetime(2026, 10, 1, 9, 59)) is not None
        with pytest.raises(ValueError, match="süresi dolmuş"):
            guvenlik.parola_belirle(s, token, YENI, simdi=datetime(2026, 10, 1, 10) + timedelta(seconds=1))


def test_pasiflestirme_kurallari(engine, posta, admin):
    oid, aid = _kullanici(engine, "onay@firma.com").id, _kullanici(engine, "admin@firma.com").id
    onay = _istemci(engine, posta)
    _giris(onay, "onay@firma.com")
    _post(admin, f"/api/kullanicilar/{oid}/parola-linki")
    token = posta.son_link()

    assert _post(admin, f"/api/kullanicilar/{oid}/aktiflik", {"aktif": False}).status_code == 200
    assert onay.get("/api/raporlar").status_code == 401  # açık oturumu bitti
    assert _giris(onay, "onay@firma.com").status_code == 400
    assert _post(onay, "/api/parola-linki/kontrol", {"token": token}).status_code == 400  # bekleyen link de iptal
    assert _post(admin, f"/api/kullanicilar/{oid}/parola-linki").status_code == 400

    cevap = _post(admin, f"/api/kullanicilar/{aid}/aktiflik", {"aktif": False})
    assert cevap.status_code == 400 and "Kendi hesabınızı" in cevap.json()["detail"]
    assert _post(admin, f"/api/kullanicilar/{oid}/aktiflik", {"aktif": True}).status_code == 200
    assert _giris(onay, "onay@firma.com").status_code == 200


def test_son_aktif_admin_pasiflestirilemez(engine):
    with Session(engine) as s:
        ikinci = kullanici_ekle(s, "admin2@firma.com", "Admin İki", "admin", PAROLA)
        admin = guvenlik.kullanici_bul(s, "admin@firma.com")
        guvenlik.aktiflik_degistir(s, admin, False, ikinci)  # iki admin varken olur
        with pytest.raises(ValueError, match="Son aktif yönetici"):
            guvenlik.aktiflik_degistir(s, ikinci, False, admin)


def test_onaylayici_kullanici_yonetemez_ve_denetimi_goremez(engine, posta):
    c = _istemci(engine, posta)
    _giris(c, "onay@firma.com")
    assert c.get("/api/kullanicilar").status_code == 403
    assert c.get("/api/denetim").status_code == 403
    assert _post(c, "/api/kullanicilar", {"eposta": "x@firma.com", "ad": "X", "rol": "admin"}).status_code == 403
    with Session(engine) as s:
        assert s.scalar(select(Denetim).where(Denetim.islem == "yetkisiz_yonetim_denemesi")) is not None
        assert guvenlik.kullanici_bul(s, "x@firma.com") is None


def test_panel_adresi_yoksa_ve_mail_gitmezse(engine, posta):
    c = _istemci(engine, posta, panel_adresi=None)
    _giris(c, "admin@firma.com")
    cevap = _post(c, "/api/kullanicilar", {"eposta": "y@firma.com", "ad": "Y", "rol": "onaylayici"})
    assert cevap.json()["mesaj"]["tur"] == "hata" and "MEVZUAT_PANEL_ADRESI" in cevap.json()["mesaj"]["mesaj"]
    assert posta.giden == []

    c = _istemci(engine, posta)
    _giris(c, "admin@firma.com")
    posta.bozuk = True
    yid = _kullanici(engine, "y@firma.com").id
    cevap = _post(c, f"/api/kullanicilar/{yid}/parola-linki")
    assert cevap.json()["mesaj"]["tur"] == "hata" and "Mail gönderilemedi" in cevap.json()["mesaj"]["mesaj"]


def test_parolami_degistir(engine, posta):
    c = _istemci(engine, posta)
    _giris(c, "onay@firma.com")
    cevap = _post(c, "/api/parolam", {"eski": "yanlis-parola-1", "yeni": YENI})
    assert cevap.status_code == 400 and "Mevcut parola" in cevap.json()["detail"]
    assert _post(c, "/api/parolam", {"eski": PAROLA, "yeni": "kisa"}).status_code == 400
    assert _post(c, "/api/parolam", {"eski": PAROLA, "yeni": YENI}).status_code == 200
    assert c.get("/api/raporlar").status_code == 200  # kendi oturumu açık kalır
    assert _giris(_istemci(engine, posta), "onay@firma.com", YENI).status_code == 200


def test_denetim_kaydi_ve_filtre(engine, posta, admin):
    _giris(_istemci(engine, posta), "onay@firma.com", "yanlis-parola-1")
    hepsi = admin.get("/api/denetim").json()
    assert hepsi["kayitlar"][0]["islem"] == "giris_basarisiz"  # en yeni önce
    assert "giris_basarisiz" in hepsi["islemler"]
    assert {"id": _kullanici(engine, "onay@firma.com").id, "ad": "Ayşe Onay"} in hepsi["kullanicilar"]
    basarisiz = admin.get("/api/denetim", params={"islem": "giris_basarisiz"}).json()["kayitlar"]
    assert basarisiz and all(k["islem"] == "giris_basarisiz" for k in basarisiz)
    assert basarisiz[0]["kullanici"] == "Ayşe Onay"
    ilk = hepsi["kayitlar"][0]["id"]
    assert all(k["id"] < ilk for k in admin.get("/api/denetim", params={"once": ilk}).json()["kayitlar"])


def test_pasif_hesap_suresi_dolunca_silinir(engine, posta, admin):
    oid = _kullanici(engine, "onay@firma.com").id
    _post(admin, f"/api/kullanicilar/{oid}/parola-linki")
    assert _post(admin, f"/api/kullanicilar/{oid}/aktiflik", {"aktif": False}).status_code == 200
    satir = next(k for k in admin.get("/api/kullanicilar").json()["kullanicilar"] if k["id"] == oid)
    assert satir["silinecek"] and admin.get("/api/kullanicilar").json()["pasif_silme_gun"] == 30

    with Session(engine) as s:
        pasif = s.get(Kullanici, oid).pasif_tarihi
        assert guvenlik.pasifleri_sil(s, 30, pasif + timedelta(days=29)) == []  # süre dolmadı
        assert guvenlik.pasifleri_sil(s, 0, pasif + timedelta(days=99)) == []  # 0, silme kapalı
        assert guvenlik.pasifleri_sil(s, 30, pasif + timedelta(days=30)) == ["onay@firma.com"]
        k = s.get(Kullanici, oid)
        assert k.silindi and k.ad == "Silinmiş kullanıcı" and "onay@firma.com" not in k.eposta and k.parola_hash == "!"
        assert s.scalar(select(ParolaLinki).where(ParolaLinki.kullanici_id == oid)) is None
        assert s.scalar(select(Denetim).where(Denetim.islem == "kullanici_silindi")).detay["kullanici_no"] == oid
        assert guvenlik.pasifleri_sil(s, 30, pasif + timedelta(days=60)) == []  # ikinci kez silinmez

    assert oid not in [k["id"] for k in admin.get("/api/kullanicilar").json()["kullanicilar"]]
    cevap = _post(admin, f"/api/kullanicilar/{oid}/aktiflik", {"aktif": True})
    assert cevap.status_code == 400 and "silinmiş" in cevap.json()["detail"]
    # Aynı adres yeni kullanıcı olarak yeniden eklenebilir.
    assert _post(admin, "/api/kullanicilar", {"ad": "Ayşe", "eposta": "onay@firma.com", "rol": "onaylayici"}).status_code == 200


def test_yeniden_acilan_hesabin_silme_suresi_sifirlanir(engine):
    with Session(engine) as s:
        admin, onay = guvenlik.kullanici_bul(s, "admin@firma.com"), guvenlik.kullanici_bul(s, "onay@firma.com")
        guvenlik.aktiflik_degistir(s, onay, False, admin)
        guvenlik.aktiflik_degistir(s, onay, True, admin)
        assert onay.pasif_tarihi is None and guvenlik.silinme_tarihi(onay, 30) is None
        assert guvenlik.pasifleri_sil(s, 30, datetime.now() + timedelta(days=365)) == []

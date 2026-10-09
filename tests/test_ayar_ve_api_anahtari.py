"""Panelden değişen ayarlar, API anahtarları ve API kullanıcısı rolü."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mevzuat import gunluk, panel_ayarlari
from mevzuat.db import ApiAnahtari, Denetim, Kullanici, SistemAyari, migrate
from mevzuat.mail import DosyaGonderici, SmtpGonderici
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

PAROLA = "dogru-parola-123"
ANAHTAR = "t" * 40


class Posta:
    def __init__(self):
        self.giden = []

    def gonder(self, mail):
        self.giden.append(mail)


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    for alan in panel_ayarlari.ALANLAR:
        monkeypatch.delenv(alan.degisken, raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    migrate(engine)
    with Session(engine) as s:
        kullanici_ekle(s, "admin@firma.com", "Bilgi İşlem", "admin", PAROLA)
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
        kullanici_ekle(s, "api@disfirma.com", "Dış Geliştirici", "api", PAROLA)
    app = uygulama_olustur(engine, Posta(), None, ANAHTAR, ek_ekle=False, arayuz=None,
                           ayar_oku=lambda db: panel_ayarlari.etkin(db, ANAHTAR))
    return TestClient(app), engine


def _csrf(client):
    return client.get("/api/oturum").json()["csrf"]


def _istek(client, yontem, yol, veri=None):
    return client.request(yontem, yol, json=veri, headers={"X-CSRF-Token": _csrf(client)})


def _giris(client, eposta):
    _istek(client, "post", "/api/cikis")
    assert _istek(client, "post", "/api/giris", {"eposta": eposta, "parola": PAROLA}).status_code == 200


def _ayarlar(client):
    return {a["ad"]: a for a in client.get("/api/ayarlar").json()["alanlar"]}


# ---- panel ayarları -----------------------------------------------------------------------------------

def test_panel_degeri_env_onune_gecer_sifirlaninca_enve_doner(tmp_path, monkeypatch):
    monkeypatch.setenv("MEVZUAT_SMTP_HOST", "smtp.env.com")
    monkeypatch.setenv("MEVZUAT_SAKLAMA_GUN", "45")
    engine = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    migrate(engine)
    with Session(engine) as s:
        a = panel_ayarlari.etkin(s, ANAHTAR)
        assert a["smtp_host"] == "smtp.env.com" and a["saklama_gun"] == 45 and a["smtp_port"] == 587
        panel_ayarlari.kaydet(s, {"smtp_host": "smtp.panel.com", "smtp_sifre": "gizli-sifre"}, 0, 1, ANAHTAR)
        s.commit()
        a = panel_ayarlari.etkin(s, ANAHTAR)
        assert a["smtp_host"] == "smtp.panel.com" and a["smtp_sifre"] == "gizli-sifre"
        # Şifre veritabanında açık yazılmaz.
        assert "gizli-sifre" not in str(s.get(SistemAyari, panel_ayarlari.AYAR_ANAHTARI).deger)
        # Gizli anahtar değişirse şifre çözülemez, .env'deki (yoksa boş) kullanılır, panel bunu söyler.
        assert panel_ayarlari.etkin(s, "x" * 40)["smtp_sifre"] is None
        assert next(a for a in panel_ayarlari.panel_gorunumu(s, "x" * 40)["alanlar"] if a["ad"] == "smtp_sifre")["cozulemedi"]
        # Sıfırlanan ayar .env'e döner.
        panel_ayarlari.kaydet(s, {"smtp_host": None}, 1, 1, ANAHTAR)
        assert panel_ayarlari.etkin(s, ANAHTAR)["smtp_host"] == "smtp.env.com"


@pytest.mark.parametrize("degisiklik, hata", [
    ({"smtp_port": 70000}, "arasında"),
    ({"smtp_port": "abc"}, "sayı"),
    ({"panel_adresi": "javascript:alert(1)"}, "geçersiz"),
    ({"admin_alicilari": "a@firma.com, bozuk"}, "Geçersiz e-posta"),
    ({"olmayan": "x"}, "Bilinmeyen"),
])
def test_hatali_ayar_kaydedilmez(ortam, degisiklik, hata):
    client, _ = ortam
    _giris(client, "admin@firma.com")
    cevap = _istek(client, "put", "/api/ayarlar", {"degisiklikler": degisiklik, "surum": 0})
    assert cevap.status_code == 400 and hata in cevap.json()["detail"]


def test_ayarlar_sadece_admin_sifre_gosterilmez_ve_degisiklik_hemen_gecerli(ortam):
    client, engine = ortam
    _giris(client, "onay@firma.com")
    assert client.get("/api/ayarlar").status_code == 403
    # SMTP yokken panel "mail kapalı" der.
    assert client.get("/api/oturum").json()["mail_kapali"] is True

    _giris(client, "admin@firma.com")
    cevap = _istek(client, "put", "/api/ayarlar", {"degisiklikler": {
        "smtp_host": "smtp.firma.com", "smtp_kullanici": "bot@firma.com", "smtp_sifre": "uygulama-sifresi",
        "panel_adresi": "https://mevzuat.firma.com/", "mfa_zorunlu": False}, "surum": 0})
    assert cevap.status_code == 200 and cevap.json()["surum"] == 1
    alanlar = _ayarlar(client)
    assert alanlar["smtp_sifre"]["deger"] is None and alanlar["smtp_sifre"]["dolu"] is True
    assert "uygulama-sifresi" not in client.get("/api/ayarlar").text
    assert alanlar["panel_adresi"]["deger"] == "https://mevzuat.firma.com" and alanlar["panel_adresi"]["kaynak"] == "panel"
    assert alanlar["smtp_port"]["kaynak"] == "varsayilan"
    # Yeniden başlatmadan geçerli, artık mail açık.
    assert client.get("/api/oturum").json()["mail_kapali"] is False
    with Session(engine) as s:
        assert isinstance(panel_ayarlari.gonderici(panel_ayarlari.etkin(s, ANAHTAR)), SmtpGonderici)
        denetim = s.scalars(select(Denetim).where(Denetim.islem == "panel_ayarlari")).one()
        assert "smtp_sifre" in denetim.detay["degisen"] and "uygulama-sifresi" not in str(denetim.detay)
    # Eski sürümle kaydetmeye çalışan (başkası değiştirmiş) reddedilir.
    assert _istek(client, "put", "/api/ayarlar", {"degisiklikler": {"smtp_port": 25}, "surum": 0}).status_code == 409


def test_gonderen_adi_kimden_satirinda():
    from email import message_from_bytes

    from mevzuat.mail import Mail, mesaj_olustur

    a = {alan.ad: panel_ayarlari.env_degeri(alan) for alan in panel_ayarlari.ALANLAR} | {
        "smtp_host": "smtp.firma.com", "smtp_kullanici": "bot@firma.com", "smtp_gonderen_adi": "Uyum Birimi Çalışanları"}
    gonderici = panel_ayarlari.gonderici(a)
    msg = message_from_bytes(bytes(mesaj_olustur(Mail("K", "<p>h</p>", "m", ["x@firma.com"]), gonderici.gonderen)))
    from email.header import decode_header, make_header
    assert str(make_header(decode_header(msg["From"]))) == "Uyum Birimi Çalışanları <bot@firma.com>"
    # Ad boşsa sadece adres, satır sonu girilse de başlığa geçmez.
    assert panel_ayarlari.gonderici(a | {"smtp_gonderen_adi": None}).gonderen == "bot@firma.com"
    assert panel_ayarlari.temizle(panel_ayarlari.ALAN["smtp_gonderen_adi"], "Uyum\r\nBcc: x@y.com") == "Uyum Bcc: x@y.com"


def test_bos_smtp_dosyaya_yazar():
    a = {alan.ad: panel_ayarlari.env_degeri(alan) for alan in panel_ayarlari.ALANLAR} | {"smtp_host": None}
    assert isinstance(panel_ayarlari.gonderici(a), DosyaGonderici)


# ---- API anahtarları --------------------------------------------------------------------------------------

def _anahtar_uret(client, rol="onaylayici", gun=30, ad="Muhasebe entegrasyonu"):
    _giris(client, "admin@firma.com")
    cevap = _istek(client, "post", "/api/api-anahtarlari", {"ad": ad, "rol": rol, "gun": gun})
    assert cevap.status_code == 200, cevap.text
    return cevap.json()["anahtar"]


def test_anahtar_cerezsiz_calisir_rolunun_yetkisiyle(ortam):
    client, engine = ortam
    anahtar = _anahtar_uret(client)
    assert anahtar.startswith("mvz_")
    dis = TestClient(client.app)  # çerezsiz, başka bir sistem
    assert dis.get("/api/raporlar").status_code == 401
    assert dis.get("/api/raporlar", headers={"X-API-Anahtari": anahtar}).status_code == 200
    # Anahtarla veri değiştiren istek CSRF istemez, ama rolün yetkisi geçerlidir (onaylayıcı kullanıcı yönetemez).
    cevap = dis.post("/api/kullanicilar", json={"eposta": "x@firma.com", "ad": "X", "rol": "admin"},
                     headers={"X-API-Anahtari": anahtar})
    assert cevap.status_code == 403
    with Session(engine) as s:
        assert s.scalars(select(Denetim).where(Denetim.islem == "api_istegi")).first() is not None
        kayit = s.scalars(select(ApiAnahtari)).one()
        assert kayit.son_kullanim is not None and anahtar not in kayit.ozet  # anahtarın kendisi saklanmaz
        # Anahtarın hesabı onay maili almaz, parolayla giriş yapamaz.
        assert gunluk.onaylayici_adresleri(s) == ["onay@firma.com"]
        hesap = s.get(Kullanici, kayit.kullanici_id)
        assert hesap.api_hesabi and hesap.ad == "API: Muhasebe entegrasyonu"


def test_admin_anahtari_yonetir_ama_onaylayamaz_anahtar_anahtar_uretemez(ortam):
    client, _ = ortam
    anahtar = _anahtar_uret(client, rol="admin", gun=None)
    dis = TestClient(client.app)
    basliklar = {"X-API-Anahtari": anahtar}
    assert dis.get("/api/kullanicilar", headers=basliklar).status_code == 200
    # Görev ayrılığı anahtarda da geçerli.
    assert dis.post("/api/raporlar/1/karar", json={"karar": "onayla", "dahil": [1]}, headers=basliklar).status_code == 403
    assert dis.post("/api/api-anahtarlari", json={"ad": "Yeni", "rol": "admin"}, headers=basliklar).status_code == 403
    # Anahtarın hesabı kullanıcı listesinde görünmez.
    assert all(not k["eposta"].endswith("@api.anahtari") for k in client.get("/api/kullanicilar").json()["kullanicilar"])


def test_iptal_suresi_dolmus_ve_yanlis_anahtar_reddedilir(ortam):
    client, engine = ortam
    anahtar = _anahtar_uret(client)
    dis = TestClient(client.app)
    assert dis.get("/api/raporlar", headers={"X-API-Anahtari": "mvz_yanlis"}).status_code == 401
    with Session(engine) as s:
        s.scalars(select(ApiAnahtari)).one().son_kullanma = datetime.now() - timedelta(minutes=1)
        s.commit()
    assert dis.get("/api/raporlar", headers={"X-API-Anahtari": anahtar}).status_code == 401
    ikinci = _anahtar_uret(client, ad="İkinci")
    kayit_id = next(a["id"] for a in client.get("/api/api-anahtarlari").json()["anahtarlar"] if a["ad"] == "İkinci")
    assert _istek(client, "post", f"/api/api-anahtarlari/{kayit_id}/iptal").status_code == 200
    assert dis.get("/api/raporlar", headers={"X-API-Anahtari": ikinci}).status_code == 401
    durumlar = {a["ad"]: a["durum"] for a in client.get("/api/api-anahtarlari").json()["anahtarlar"]}
    assert durumlar == {"Muhasebe entegrasyonu": "suresi_doldu", "İkinci": "iptal"}


def test_onaylayici_anahtar_uretemez(ortam):
    client, _ = ortam
    _giris(client, "onay@firma.com")
    assert _istek(client, "post", "/api/api-anahtarlari", {"ad": "X", "rol": "admin"}).status_code == 403


# ---- API kullanıcısı rolü -----------------------------------------------------------------------------------

def test_api_kullanicisi_sadece_dokumani_gorur_istek_icin_anahtar_ister(ortam):
    client, _ = ortam
    anahtar = _anahtar_uret(client)
    _dokumani_kapat(client)
    _giris(client, "api@disfirma.com")
    assert [m["anahtar"] for m in client.get("/api/menu").json()] == ["api_dokumani"]
    assert client.get("/api/dokuman/openapi.json").status_code == 200
    assert client.get("/api/raporlar").status_code == 403
    # Dokümandaki "Authorize" ile anahtar girince istek anahtarın rolüyle çalışır.
    assert client.get("/api/raporlar", headers={"X-API-Anahtari": anahtar}).status_code == 200
    sema = client.get("/api/dokuman/openapi.json").json()
    assert sema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"


def _dokumani_kapat(client):
    _giris(client, "admin@firma.com")
    surum = client.get("/api/ayarlar").json()["surum"]
    assert _istek(client, "put", "/api/ayarlar", {"degisiklikler": {"dokuman_acik": False}, "surum": surum}).status_code == 200


def test_dokuman_varsayilan_herkese_acik_istekler_anahtar_ister(ortam):
    client, _ = ortam
    anahtar = _anahtar_uret(client)
    dis = TestClient(client.app)  # panel hesabı yok, şirket portalı gibi
    assert dis.get("/api/dokuman/openapi.json").status_code == 200
    assert dis.get("/api/dokuman/indir?bicim=json").status_code == 200
    # Doküman açık ama istekler anahtarsız çalışmaz.
    assert dis.get("/api/v1/mevzuat").status_code == 401
    assert dis.get("/api/v1/mevzuat", headers={"Authorization": f"Bearer {anahtar}"}).status_code == 200
    # Panelden kapatılınca doküman da anahtar ister, yeniden başlatma gerekmez.
    _dokumani_kapat(client)
    assert dis.get("/api/dokuman/openapi.json").status_code == 401
    assert dis.get("/api/dokuman/openapi.json", headers={"Authorization": f"Bearer {anahtar}"}).status_code == 200


def test_panel_hesabi_olmadan_anahtarla_dokuman(ortam):
    client, _ = ortam
    anahtar = _anahtar_uret(client)
    _dokumani_kapat(client)
    surum = client.get("/api/ayarlar").json()["surum"]
    _istek(client, "put", "/api/ayarlar", {"degisiklikler": {"panel_adresi": "https://mevzuat.firma.com"}, "surum": surum})
    dis = TestClient(client.app)  # panel hesabı yok
    assert dis.get("/api/dokuman").status_code == 200  # sayfa açılır, anahtar ister
    assert dis.get("/api/dokuman/openapi.json").status_code == 401
    assert dis.get("/api/dokuman/openapi.json", headers={"X-API-Anahtari": "mvz_yanlis"}).status_code == 401
    sema = dis.get("/api/dokuman/openapi.json", headers={"X-API-Anahtari": anahtar}).json()
    assert "servers" not in sema  # sayfadaki doküman istekleri kendi adresine atar
    json_ = dis.get("/api/dokuman/indir?bicim=json", headers={"X-API-Anahtari": anahtar})
    assert json_.status_code == 200 and "/api/v1/mevzuat" in json_.json()["paths"]
    # İndirilen doküman bilgisayardan açılır ya da Postman'e yüklenir, sunucu adresini taşır.
    assert json_.json()["servers"] == [{"url": "https://mevzuat.firma.com", "description": "Mevzuat Takip"}]
    # API kullanıcısı da oturumla indirebilir.
    _giris(client, "api@disfirma.com")
    assert client.get("/api/dokuman/indir?bicim=json").status_code == 200


def test_onaylayici_api_dokumanini_goremez_anahtarla_gelen_gorur(ortam):
    client, _ = ortam
    anahtar = _anahtar_uret(client)  # onaylayıcı rollü anahtar, dış sistem için
    _dokumani_kapat(client)
    _giris(client, "onay@firma.com")
    assert "api_dokumani" not in [m["anahtar"] for m in client.get("/api/menu").json()]
    assert client.get("/api/dokuman/openapi.json").status_code == 403
    assert client.get("/api/dokuman/indir?bicim=json").status_code == 403
    assert client.get("/api/dokuman/openapi.json", headers={"X-API-Anahtari": anahtar}).status_code == 200
    _giris(client, "admin@firma.com")
    assert client.get("/api/dokuman/openapi.json").status_code == 200


def test_anahtar_komut_satirindan_uretilir_listelenir_iptal_edilir(tmp_path, monkeypatch, capsys):
    """Panel kullanılmayan kurulumda anahtar sunucudan komutla verilir."""
    import argparse

    from mevzuat import cli

    url = f"sqlite:///{tmp_path / 'k.db'}"
    monkeypatch.setenv("MEVZUAT_DB_URL", url)
    cli.api_anahtari_uret_komutu(argparse.Namespace(ad="Portal", rol="admin", gun=30))
    anahtar = capsys.readouterr().out.strip().splitlines()[-1]
    assert anahtar.startswith("mvz_")
    engine = create_engine(url)
    with Session(engine) as s:
        assert gunluk  # içe aktarma kullanılsın
        from mevzuat.web import guvenlik
        kayit, hesap = guvenlik.api_anahtari_hesabi(s, anahtar)
        assert hesap.rol == "admin" and kayit.olusturan_id is None
    cli.api_anahtari_listele_komutu(argparse.Namespace())
    assert "Portal" in capsys.readouterr().out and anahtar not in capsys.readouterr().out
    cli.api_anahtari_iptal_komutu(argparse.Namespace(id=kayit.id))
    with Session(engine) as s:
        assert guvenlik.api_anahtari_hesabi(s, anahtar) is None
        assert guvenlik.api_anahtari_durumu(s, anahtar) == "iptal"


def test_bosluklu_anahtar_kabul_red_nedeni_denetimde(ortam):
    client, engine = ortam
    anahtar = _anahtar_uret(client)
    dis = TestClient(client.app)
    assert dis.get("/api/raporlar", headers={"X-API-Anahtari": f"  {anahtar} "}).status_code == 200
    assert dis.get("/api/raporlar", headers={"X-API-Anahtari": anahtar[:10]}).status_code == 401
    with Session(engine) as s:
        detay = s.scalars(select(Denetim).where(Denetim.islem == "api_anahtari_gecersiz")).one().detay
    assert detay == {"yol": "/api/raporlar", "on_ek": anahtar[:10], "uzunluk": 10, "durum": "yok"}


def test_tam_yetki_anahtari_her_seyi_yapar_panelde_gorev_ayriligi_kalir(ortam):
    client, _ = ortam
    tam = _anahtar_uret(client, rol="tam", ad="Test ekibi")
    admin = _anahtar_uret(client, rol="admin", ad="Admin anahtarı")
    dis = TestClient(client.app)
    t, a = {"X-API-Anahtari": tam}, {"X-API-Anahtari": admin}
    for yol in ("/api/kullanicilar", "/api/ayarlar", "/api/denetim", "/api/gruplar", "/api/kaynaklar", "/api/raporlar"):
        assert dis.get(yol, headers=t).status_code == 200, yol
    # Rapor kararı: tam yetki izin kontrolünü geçer (rapor olmadığı için 404), admin anahtarı yetkisiz (403).
    karar = {"karar": "onayla", "dahil": [1]}
    assert dis.post("/api/raporlar/1/karar", json=karar, headers=t).status_code == 404
    assert dis.post("/api/raporlar/1/karar", json=karar, headers=a).status_code == 403
    assert [m["anahtar"] for m in dis.get("/api/menu", headers=t).json()][-3:] == ["ayarlar", "api_anahtarlari", "api_dokumani"]
    # Panel kullanıcısı "tam" rolüyle eklenemez.
    _giris(client, "admin@firma.com")
    assert _istek(client, "post", "/api/kullanicilar", {"eposta": "x@firma.com", "ad": "X", "rol": "tam"}).status_code == 422

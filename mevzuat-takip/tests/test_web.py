from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import rapor
from mevzuat.db import AliciGrubu, Calisma, Denetim, Gonderim, Kayit, Kullanici, Rapor, init_db
from mevzuat.mail import Mail
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import MAX_DENEME, kullanici_ekle

PAROLA = "dogru-parola-123"
ANAHTAR = "t" * 40
IS_KOLLARI = ["Döviz/Altın", "Kuyum", "Ortak", "Oto kiralama"]


class Posta:
    def __init__(self):
        self.giden: list[Mail] = []

    def gonder(self, mail: Mail) -> None:
        self.giden.append(mail)


@pytest.fixture
def arayuz(tmp_path) -> Path:
    """Derlenmiş arayüzün yerine geçen küçük bir dist klasörü."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><div id="root"></div><script src="/assets/app.js"></script>',
                                     encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "gizli.txt").write_text("DIŞARIDAKİ DOSYA", encoding="utf-8")
    return dist


@pytest.fixture
def ortam(arayuz):
    # Tek bağlantı paylaşılır: bellek içi SQLite her bağlantıda ayrı bir veritabanıdır.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        kullanici_ekle(s, "sorumlu@firma.com", "Ayşe Sorumlu", "onaylayici", PAROLA)
        s.add(Calisma(id=1, baslangic=datetime(2026, 10, 1, 7), durum="BASARILI", ozet={}))
        for i, baslik in enumerate(["Kıymetli Maden Tebliği", "<script>alert(1)</script> Yönetmeliği"]):
            s.add(Kayit(
                kaynak="resmi_gazete", dis_id=f"u{i}", yayin_tarihi=date(2026, 10, 1), baslik=baslik, tur="TEBLİĞ",
                bolum="", mukerrer=0, url=f"https://x/{i}.htm", kaynakca=f"Resmî Gazete, 01.10.2026: {baslik}",
                ilgili=True, eslesmeler={"Kıymetli madenler": ["kıymetli maden"]}, is_kollari=["Kuyum"],
                icerik="MADDE 1- kıymetli maden metni.", icerik_durumu="TAMAM",
                ilk_gorulme=datetime(2026, 10, 1, 7), calisma_id=1,
            ))
        s.add(AliciGrubu(ad="Kuyum Ekibi", is_kollari=["Kuyum"], adresler=["herkes@firma.com"], aktif=True,
                         guncellendi=datetime(2026, 10, 1)))
        s.commit()
        r = rapor.onaya_sun(s, Posta(), ["sorumlu@firma.com"], bugun=date(2026, 10, 1))
        rapor_id = r.id
    posta = Posta()
    app = uygulama_olustur(engine, posta, IS_KOLLARI, ANAHTAR, ek_ekle=False, arayuz=arayuz)
    return TestClient(app), engine, posta, rapor_id


def _csrf(client: TestClient) -> str:
    return client.get("/api/oturum").json()["csrf"]


def _post(client: TestClient, yol: str, veri: dict, csrf: str | None = None):
    return client.post(yol, json=veri, headers={"X-CSRF-Token": csrf if csrf is not None else _csrf(client)})


def _put(client: TestClient, yol: str, veri: dict):
    return client.put(yol, json=veri, headers={"X-CSRF-Token": _csrf(client)})


def _giris(client: TestClient, parola: str = PAROLA, eposta: str = "sorumlu@firma.com"):
    return _post(client, "/api/giris", {"eposta": eposta, "parola": parola})


def _kayit_idleri(engine, rapor_id: int) -> list[int]:
    with Session(engine) as s:
        return [k.id for k in rapor.rapor_kayitlari(s, s.get(Rapor, rapor_id))]


# ---- giriş ve oturum ------------------------------------------------------------------------------

def test_girissiz_erisim_reddedilir(ortam):
    client, _, posta, rapor_id = ortam
    for yol in ["/api/raporlar", f"/api/raporlar/{rapor_id}", "/api/gruplar"]:
        assert client.get(yol).status_code == 401
    assert _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "onayla", "dahil": [1]}).status_code == 401
    assert client.get("/api/oturum").json()["kullanici"] is None
    assert posta.giden == []


def test_giris_ve_guvenlik_basliklari(ortam):
    client, *_ = ortam
    cevap = _giris(client)
    assert cevap.status_code == 200 and cevap.json()["sonraki"] == "panel"
    cerez = cevap.headers["set-cookie"].lower()
    assert "httponly" in cerez and "samesite=strict" in cerez
    oturum = client.get("/api/oturum")
    kullanici = oturum.json()["kullanici"]
    assert kullanici["ad"] == "Ayşe Sorumlu" and kullanici["karar_verebilir"] and kullanici["grup_yonetebilir"]
    assert "parola_hash" not in kullanici and "mfa_gizli" not in kullanici
    csp = oturum.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert oturum.headers["cache-control"] == "no-store"
    assert "openapi" not in client.get("/openapi.json").text  # API dokümanı yok (arayüz sayfası döner)
    assert client.get("/docs").headers["content-type"].startswith("text/html")


def test_hatali_giris_ayni_mesaj_kullanici_var_mi_belli_olmaz(ortam):
    client, *_ = ortam
    yanlis_parola = _giris(client, parola="yanlis-parola-xyz")
    olmayan = _giris(client, eposta="yok@firma.com")
    assert yanlis_parola.status_code == olmayan.status_code == 400
    assert yanlis_parola.json() == olmayan.json()


def test_art_arda_hatali_giriste_hesap_kilitlenir(ortam):
    client, engine, *_ = ortam
    for _ in range(MAX_DENEME):
        _giris(client, parola="yanlis-parola-xyz")
    assert _giris(client).status_code == 400  # doğru parola bile kilit süresince işe yaramaz
    with Session(engine) as s:
        assert s.scalar(select(Kullanici)).kilit_bitis is not None
        islemler = [d.islem for d in s.scalars(select(Denetim))]
    assert "hesap_kilitlendi" in islemler and "giris_kilitli" in islemler


def test_csrf_olmadan_giris_ve_karar_reddedilir(ortam):
    client, engine, posta, rapor_id = ortam
    client.get("/api/oturum")
    assert _post(client, "/api/giris", {"eposta": "sorumlu@firma.com", "parola": PAROLA}, csrf="sahte").status_code == 403
    cevap = client.post("/api/giris", json={"eposta": "sorumlu@firma.com", "parola": PAROLA})  # başlıksız
    assert cevap.status_code == 403
    _giris(client)
    ilk = _kayit_idleri(engine, rapor_id)[0]
    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "onayla", "dahil": [ilk]}, csrf="sahte")
    assert cevap.status_code == 403
    assert posta.giden == []


def test_giriste_csrf_tokeni_yenilenir(ortam):
    """Oturum sabitleme: girişten önceki token (ve oturum) girişten sonra geçersiz."""
    client, *_ = ortam
    eski = _csrf(client)
    yeni = _giris(client).json()["csrf"]
    assert yeni != eski
    assert _post(client, "/api/cikis", {}, csrf=eski).status_code == 403


def test_pasif_kullanicinin_acik_oturumu_kapanir(ortam):
    client, engine, *_ = ortam
    _giris(client)
    assert client.get("/api/raporlar").status_code == 200
    with Session(engine) as s:
        s.scalar(select(Kullanici)).aktif = False
        s.commit()
    assert client.get("/api/raporlar").status_code == 401


def test_cikis(ortam):
    client, *_ = ortam
    _giris(client)
    assert _post(client, "/api/cikis", {}).status_code == 200
    assert client.get("/api/raporlar").status_code == 401


def test_kisa_gizli_anahtar_reddedilir():
    with pytest.raises(ValueError, match="32"):
        uygulama_olustur(create_engine("sqlite://"), Posta(), [], "kisa")


def test_parola_kurallari():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        with pytest.raises(ValueError, match="12"):
            kullanici_ekle(s, "a@b.com", "A", "onaylayici", "kisa")
        with pytest.raises(ValueError, match="Rol"):
            kullanici_ekle(s, "a@b.com", "A", "patron", PAROLA)
        k = kullanici_ekle(s, "A@B.com", "A", "admin", PAROLA)
        assert k.eposta == "a@b.com" and k.parola_hash.startswith("$argon2id$") and PAROLA not in k.parola_hash
        with pytest.raises(ValueError, match="zaten"):
            kullanici_ekle(s, "a@b.com", "A", "admin", PAROLA)


# ---- raporlar ve onay -----------------------------------------------------------------------------

def test_onay_akisi_uctan_uca(ortam):
    client, engine, posta, rapor_id = ortam
    _giris(client)
    liste = client.get("/api/raporlar").json()
    assert [r["id"] for r in liste["bekleyenler"]] == [rapor_id] and liste["gecmis"] == []

    detay = client.get(f"/api/raporlar/{rapor_id}")
    assert detay.headers["content-type"] == "application/json"  # HTML değil: başlık arayüzde metin olarak basılır
    kalemler = detay.json()["kalemler"]
    assert kalemler[1]["baslik"] == "<script>alert(1)</script> Yönetmeliği"
    assert kalemler[0]["gidecek"] == [{"ad": "Kuyum Ekibi", "kisi": 1}]  # onaydan önce dağıtım önizlemesi
    assert all(k["url"].startswith("https://x/") for k in kalemler)  # onaylayıcı belgenin aslına gidebilir
    assert all(k["nedenler"] == [] for k in kalemler)  # konulara açıklama yazılmamış
    ilk, ikinci = kalemler[0]["id"], kalemler[1]["id"]

    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar",
                  {"karar": "onayla", "dahil": [ilk], "notu": "Kuyum ekibi incelesin."})
    assert cevap.status_code == 200 and cevap.json()["tur"] == "basari" and "gönderildi" in cevap.json()["mesaj"]

    assert len(posta.giden) == 1
    mail = posta.giden[0]
    assert mail.alicilar == ["herkes@firma.com"] and "1 kalem" in mail.konu
    assert "Kuyum ekibi incelesin." in mail.html
    with Session(engine) as s:
        r = s.get(Rapor, rapor_id)
        assert r.durum == "GONDERILDI" and r.karar_notu == "Kuyum ekibi incelesin."
        assert s.get(Kayit, ikinci).haric
        denetim = s.scalars(select(Denetim).where(Denetim.islem == "onay")).one()
        assert denetim.detay["rapor_id"] == rapor_id and denetim.detay["dahil"] == [ilk]
    detay = client.get(f"/api/raporlar/{rapor_id}").json()
    assert detay["rapor"]["karar_veren"] == "Ayşe Sorumlu"
    (gonderim,) = detay["gonderimler"]
    assert gonderim["durum"] == "GONDERILDI" and gonderim["gruplar"] == ["Kuyum Ekibi"] and gonderim["kalemler"] == [1]

    # Aynı rapora ikinci karar (ör. butona iki kez basma) dağıtımı tekrarlamaz.
    tekrar = _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "onayla", "dahil": [ilk]})
    assert tekrar.status_code == 409
    assert len(posta.giden) == 1


def test_ek_gonderim_gonderilmeyen_kalemi_baska_kisiye_gonderir(ortam):
    client, engine, posta, rapor_id = ortam
    _giris(client)
    kalemler = client.get(f"/api/raporlar/{rapor_id}").json()["kalemler"]
    ilk, ikinci = kalemler[0]["id"], kalemler[1]["id"]
    _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "onayla", "dahil": [ilk]})
    assert len(posta.giden) == 1

    cevap = _post(client, f"/api/raporlar/{rapor_id}/ek-gonderim",
                  {"dahil": [ikinci], "gruplar": [], "ek_adresler": ["yeni@firma.com"], "notu": "Size de gelsin."})
    assert cevap.status_code == 200 and cevap.json()["tur"] == "basari" and "1 adrese" in cevap.json()["mesaj"]
    assert len(posta.giden) == 2
    mail = posta.giden[1]
    assert mail.alicilar == ["yeni@firma.com"] and "Size de gelsin." in mail.html
    with Session(engine) as s:
        assert s.get(Rapor, rapor_id).durum == "GONDERILDI" and not s.get(Kayit, ikinci).haric
        assert s.scalars(select(Denetim).where(Denetim.islem == "ek_gonderim")).one().detay["dahil"] == [ikinci]
    gonderimler = client.get(f"/api/raporlar/{rapor_id}").json()["gonderimler"]
    assert [g["notu"] for g in gonderimler] == [None, "Size de gelsin."]
    # Alıcısız istek reddedilir, mail gitmez.
    bos = _post(client, f"/api/raporlar/{rapor_id}/ek-gonderim", {"dahil": [ikinci], "gruplar": []})
    assert bos.status_code == 400 and len(posta.giden) == 2


def test_ret_notsuz_olmaz(ortam):
    client, engine, posta, rapor_id = ortam
    _giris(client)
    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "reddet", "notu": "  "})
    assert cevap.status_code == 400 and "sebep yazmak zorunludur" in cevap.json()["detail"]
    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "reddet", "notu": "İlgisiz."})
    assert cevap.status_code == 200 and "reddedildi" in cevap.json()["mesaj"]
    with Session(engine) as s:
        assert s.get(Rapor, rapor_id).durum == "REDDEDILDI"
    assert posta.giden == []


def test_gecersiz_karar_ve_olmayan_rapor(ortam):
    client, *_, rapor_id = ortam
    _giris(client)
    assert _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "sil"}).status_code == 422
    assert client.get("/api/raporlar/999").status_code == 404
    assert _post(client, "/api/raporlar/999/karar", {"karar": "reddet", "notu": "x"}).status_code == 404


def test_admin_raporu_gorur_ama_karar_veremez(ortam):
    """Görev ayrılığı: admin raporu okur, ama onay/ret yalnızca onaylayıcının."""
    client, engine, posta, rapor_id = ortam
    with Session(engine) as s:
        kullanici_ekle(s, "admin@firma.com", "Bilgi İşlem", "admin", PAROLA)
    _giris(client, eposta="admin@firma.com")
    assert client.get("/api/oturum").json()["kullanici"]["karar_verebilir"] is False
    assert client.get(f"/api/raporlar/{rapor_id}").status_code == 200

    ilk = _kayit_idleri(engine, rapor_id)[0]
    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "onayla", "dahil": [ilk]})
    assert cevap.status_code == 403
    cevap = _post(client, f"/api/raporlar/{rapor_id}/ek-gonderim", {"dahil": [ilk], "ek_adresler": ["x@firma.com"]})
    assert cevap.status_code == 403
    assert posta.giden == []
    with Session(engine) as s:
        assert s.get(Rapor, rapor_id).durum == "ONAY_BEKLIYOR"
        islemler = [d.islem for d in s.scalars(select(Denetim))]
        assert "yetkisiz_karar_denemesi" in islemler and "yetkisiz_ek_gonderim_denemesi" in islemler


# ---- alıcı grupları ------------------------------------------------------------------------------

def _grup(client: TestClient, **alanlar):
    veri = {"ad": "Döviz Ekibi", "is_kollari": ["Döviz/Altın", "Ortak"],
            "adresler": ["Doviz1@firma.com", "doviz2@firma.com"], "aktif": True, **alanlar}
    return _post(client, "/api/gruplar", veri)


def test_onaylayici_grup_ekler_ve_duzenler_denetime_yazilir(ortam):
    client, engine, *_ = ortam
    _giris(client)
    liste = client.get("/api/gruplar").json()
    assert [g["ad"] for g in liste["gruplar"]] == ["Kuyum Ekibi"]
    assert liste["is_kollari"] == IS_KOLLARI and "Döviz/Altın" in liste["kapsanmayan"]

    cevap = _grup(client)
    assert cevap.status_code == 200
    grup = cevap.json()
    assert grup["adresler"] == ["doviz1@firma.com", "doviz2@firma.com"] and grup["is_kollari"] == ["Döviz/Altın", "Ortak"]
    assert "Döviz/Altın" not in client.get("/api/gruplar").json()["kapsanmayan"]

    cevap = _put(client, f"/api/gruplar/{grup['id']}", {"ad": "Döviz Ekibi", "is_kollari": ["Döviz/Altın"],
                                                        "adresler": ["doviz1@firma.com"], "aktif": False})
    assert cevap.status_code == 200 and cevap.json()["aktif"] is False
    with Session(engine) as s:
        kayitlar = list(s.scalars(select(Denetim).where(Denetim.islem.in_(["grup_ekle", "grup_degistir"]))))
    assert [d.islem for d in kayitlar] == ["grup_ekle", "grup_degistir"]
    assert kayitlar[1].detay["once"]["aktif"] is True and kayitlar[1].detay["sonra"]["aktif"] is False


def test_admin_de_grup_yonetebilir(ortam):
    client, engine, *_ = ortam
    with Session(engine) as s:
        kullanici_ekle(s, "admin@firma.com", "Bilgi İşlem", "admin", PAROLA)
    _giris(client, eposta="admin@firma.com")
    assert _grup(client).status_code == 200


def test_hatali_grup_kaydedilmez(ortam):
    client, engine, *_ = ortam
    _giris(client)
    cevap = _grup(client, adresler=["dogru@firma.com", "yanlis-adres"])
    assert cevap.status_code == 400 and cevap.json()["detail"] == "Geçersiz e-posta adresi: yanlis-adres"
    cevap = _grup(client, ad="kuyum ekibi")
    assert cevap.status_code == 400 and "zaten var" in cevap.json()["detail"]
    assert _put(client, "/api/gruplar/999", {"ad": "X", "is_kollari": ["Kuyum"], "adresler": ["x@firma.com"]}).status_code == 404
    with Session(engine) as s:
        assert s.scalar(select(AliciGrubu).where(AliciGrubu.ad == "Döviz Ekibi")) is None


def test_grup_kaydi_csrf_ve_giris_ister(ortam):
    client, engine, *_ = ortam
    assert _grup(client).status_code == 401
    _giris(client)
    cevap = client.post("/api/gruplar", json={"ad": "X", "is_kollari": ["Kuyum"], "adresler": ["x@firma.com"]})
    assert cevap.status_code == 403
    with Session(engine) as s:
        assert s.scalar(select(AliciGrubu).where(AliciGrubu.ad == "X")) is None


def test_gruba_gitmeyen_kalem_gorunur_ve_grupsuz_onay_engellenir(ortam):
    client, engine, posta, rapor_id = ortam
    with Session(engine) as s:
        s.scalar(select(AliciGrubu)).aktif = False
        s.commit()
    _giris(client)
    kalemler = client.get(f"/api/raporlar/{rapor_id}").json()["kalemler"]
    assert kalemler[0]["gidecek"] == []
    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "onayla", "dahil": [kalemler[0]["id"]]})
    assert cevap.status_code == 400 and "hiçbir alıcı grubuna gitmiyor" in cevap.json()["detail"]
    assert posta.giden == []
    with Session(engine) as s:
        assert s.get(Rapor, rapor_id).durum == "ONAY_BEKLIYOR" and s.scalar(select(Gonderim)) is None


# ---- arayüz dosyaları ----------------------------------------------------------------------------

def test_arayuz_dosyalari_sunulur_klasor_disina_cikilmaz(ortam):
    client, *_ = ortam
    ana = client.get("/")
    assert '<div id="root">' in ana.text and "script-src 'self'" in ana.headers["content-security-policy"]
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert '<div id="root">' in client.get("/raporlar/5").text  # tek sayfa: bilinmeyen yol → index.html
    for yol in ["/..%2fgizli.txt", "/assets/..%2f..%2fgizli.txt", "/%2e%2e/gizli.txt"]:
        assert "DIŞARIDAKİ" not in client.get(yol).text, yol
    assert client.get("/api/yok").status_code == 404


def test_arayuz_derlenmemisse_aciklama():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    client = TestClient(uygulama_olustur(engine, Posta(), [], ANAHTAR, arayuz=Path("/yok/dist")))
    cevap = client.get("/")
    assert cevap.status_code == 503 and "npm run build" in cevap.text
    assert client.get("/saglik").text == "ok"


def test_onay_ekraninda_grup_secimi_ve_kisiye_ozel_adres(ortam):
    client, engine, posta, rapor_id = ortam
    _giris(client)
    detay = client.get(f"/api/raporlar/{rapor_id}").json()
    assert detay["gruplar"] == [{"id": detay["gruplar"][0]["id"], "ad": "Kuyum Ekibi", "adresler": ["herkes@firma.com"], "is_kollari": ["Kuyum"]}]
    assert detay["adres_onerileri"] == ["herkes@firma.com"]
    ilk = detay["kalemler"][0]["id"]

    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar", {
        "karar": "onayla", "dahil": [ilk], "gruplar": [], "ek_adresler": ["avukat@firma.com"]})
    assert cevap.status_code == 200, cevap.text
    assert [m.alicilar for m in posta.giden] == [["avukat@firma.com"]]  # Kuyum Ekibi işaretlenmedi
    with Session(engine) as s:
        denetim = s.scalars(select(Denetim).where(Denetim.islem == "onay")).one()
        assert denetim.detay["gruplar"] == [] and denetim.detay["ek_adresler"] == ["avukat@firma.com"]


def test_mail_kapaliyken_gonderildi_denmez(ortam, tmp_path):
    """SMTP ayarsızken mail sadece dosyaya yazılır; panel "gönderildi" deyip kullanıcıyı yanıltmamalı."""
    from mevzuat.mail import DosyaGonderici

    _, engine, _, rapor_id = ortam
    client = TestClient(uygulama_olustur(engine, DosyaGonderici(tmp_path), IS_KOLLARI, ANAHTAR, ek_ekle=False,
                                         arayuz=None))
    assert client.get("/api/oturum").json()["mail_kapali"] is False  # girişsiz kişiye ayar bilgisi verilmez
    _giris(client)
    assert client.get("/api/oturum").json()["mail_kapali"] is True
    ilk = client.get(f"/api/raporlar/{rapor_id}").json()["kalemler"][0]["id"]
    cevap = _post(client, f"/api/raporlar/{rapor_id}/karar", {"karar": "onayla", "dahil": [ilk]}).json()
    assert cevap["tur"] == "hata" and "GÖNDERİLMEDİ" in cevap["mesaj"]
    assert list(tmp_path.glob("*.eml"))

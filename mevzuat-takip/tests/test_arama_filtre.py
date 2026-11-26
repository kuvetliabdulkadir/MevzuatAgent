"""Faz 2 (arşivde arama) ve Faz 3 (konu filtresi: Türkçe ek kuralı, ortak deyimler, puanlama, arşive uygulama)."""

from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import arama, arsiv, filtre, puan, turkce_ek
from mevzuat import zamanlama as zm
from mevzuat.db import ArsivKonu, ArsivMevzuat, ArsivSurumu, KonuTanimi, init_db
from mevzuat.filtre import konu_olustur, tr_kucuk
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

PAROLA = "dogru-parola-123"
KUYUM = konu_olustur("Kıymetli madenler", ["altın", "kıymetli maden", "kuyum", "gümüş"], ["Kuyum"],
                     haric=["altıncı"], dislanan=["personel yönetmeliği"])


# --- Türkçe ek kuralı ve deyimler --------------------------------------------------------------------------------

@pytest.mark.parametrize("kok,kelime,beklenen", [
    ("altın", "altının", True), ("altın", "altınlar", True), ("kuyum", "kuyumculuk", True),
    ("vergi", "vergilendirilmesi", True), ("harç", "harcı", True), ("kdv", "kdv'nin", True),
    ("merkez bankası", "merkez bankasının", True), ("dondur", "dondurulması", True),
    ("altın", "altınbaş", False), ("gümüş", "gümüşhane", False), ("altın", "altınordu", False),
    ("altın", "altınyayla", False), ("gümüş", "gümüşpınar", False),
    ("altın*", "altınbaş", True),  # yıldız: eski kural, başı tutsun yeter
])
def test_turkce_ek_kurali(kok, kelime, beklenen):
    assert turkce_ek.var_mi(kok, tr_kucuk(kelime)) is beklenen


def test_ortak_deyimler_ve_filtre():
    assert filtre.eslesmeler("BİTKİ ÇEŞİTLERİNİN KAYIT ALTINA ALINMASI YÖNETMELİĞİ", [KUYUM]) == {}
    assert filtre.eslesmeler("ÇANAKKALE ŞEHRİNE ALTIN MADALYA VERİLMESİ HAKKINDA KANUN", [KUYUM]) == {}
    assert filtre.eslesmeler("ALTINBAŞ ÜNİVERSİTESİ ANA YÖNETMELİĞİ", [KUYUM]) == {}
    assert filtre.eslesmeler("KUYUM TİCARETİ HAKKINDA YÖNETMELİK", [KUYUM]) == {"Kıymetli madenler": ["kuyum"]}
    assert filtre.eslesmeler("Altının ve gümüşün ithalatı", [KUYUM]) == {"Kıymetli madenler": ["altın", "gümüş"]}
    # Maskeleme metnin uzunluğunu değiştirmez (kanıt parçalarının yeri kaymasın).
    metin = "kontrol altına alınması ve altıncı madde"
    assert len(filtre.hazirla(metin, KUYUM)) == len(metin)


# --- puanlama ---------------------------------------------------------------------------------------------------

YONETMELIK = """KUYUM TİCARETİ HAKKINDA YÖNETMELİK
MADDE 1 – (1) Bu Yönetmeliğin amacı, kıymetli maden ve kuyum ticaretinin esaslarını belirlemektir.
MADDE 2 – (1) Bu Yönetmelik, altın ve gümüş alım satımı yapan kuyumcuları kapsar.
MADDE 3 – (1) Bu Yönetmelikte geçen; a) Kıymetli maden: altın, gümüş ve platini ifade eder.
MADDE 4 – (1) Kuyumcular altın alımında kimlik tespiti yapar.
MADDE 5 – (1) Bu Yönetmelik yayımı tarihinde yürürlüğe girer."""
ATIF = """BELEDİYE ZABITA YÖNETMELİĞİ
MADDE 1 – (1) Bu Yönetmeliğin amacı belediye zabıta hizmetlerini düzenlemektir.
MADDE 2 – (1) Bu Yönetmelik zabıta birimlerini kapsar.
MADDE 3 – (1) Bu Yönetmelik, 3/3/2026 tarihli Kuyum Ticareti Kanununa dayanılarak hazırlanmıştır.
""" + "\n".join(f"MADDE {i} – (1) Zabıta denetim yapar ve tutanak düzenler." for i in range(4, 40))


def test_puanlama_bolum_agirliklari_ve_kanit():
    p = puan.puanla("KUYUM TİCARETİ HAKKINDA YÖNETMELİK", YONETMELIK, KUYUM)
    assert p.ilgili and p.ayrinti["amac"] > 0 and p.ayrinti["baslik"] > 0
    assert {k.tur for k in p.kanitlar} >= {"baslik", "amac"} and any("kuyum" in k.parca.lower() for k in p.kanitlar)
    # Sadece dayanak maddesinde geçen atıf ilgili yapmaz.
    a = puan.puanla("BELEDİYE ZABITA YÖNETMELİĞİ", ATIF, KUYUM)
    assert not a.ilgili and a.ayrinti["madde"] == 0
    # Dışlanan ifade başlıkta: veto değil, ceza.
    d = puan.puanla("KUYUM PERSONEL YÖNETMELİĞİ", "", KUYUM)
    assert d.ayrinti["ceza"] == puan.DISLANAN_CEZASI and not d.ilgili
    # Başlıkta geçmeyen ama metninde yoğun geçen belge ilgili olur (içerik filtresi).
    icerik = "\n".join(f"MADDE {i} – (1) Kuyumcular altın ve gümüş satışında kıymetli maden belgesi düzenler." for i in range(1, 12))
    assert puan.puanla("ESNAF VE SANATKARLAR TEBLİĞİ", icerik, KUYUM).ilgili


def test_yapisiz_metin_baslik_ya_da_giris_ister():
    """Madde yapısı olmayan metin (mahkeme kararı, liste, duyuru): yayılma yok, yoğunluk sınırlı. Konu metnin içinde
    geçip girişinde geçmiyorsa ilgili olmaz; girişinde de geçiyorsa olur."""
    dolgu = " ".join(["Başvurucu dava açmış, mahkeme davayı reddetmiş, karar kesinleşmiştir."] * 15)
    icinde = dolgu + " Uyuşmazlık altın ve gümüş alımından doğan alacağa ilişkindir, kuyumcu altın teslim etmemiştir."
    p = puan.puanla("ANAYASA MAHKEMESİ KARARI", icinde, KUYUM)
    assert not p.ilgili and p.ayrinti["yayilma"] == 0 and p.ayrinti["yogunluk"] <= puan.YAPISIZ_YOGUNLUK_EN_FAZLA
    giriste = "Kuyumcuların altın ve gümüş alım satımında uygulanacak esaslar duyurulmuştur. " + dolgu
    assert puan.puanla("DUYURU", giriste, KUYUM).ilgili


def test_degisiklik_maddesi_amac_sayilmaz_tek_madde_sinirli():
    degisiklik = ("MADDE 1 – (1) Elektrik Yönetmeliğinin 4 üncü maddesinde geçen “altın” ibaresi “gümüş” şeklinde "
                  "değiştirilmiştir. " + " ".join(f"MADDE {i} – (1) Şebeke işletmecisi ölçüm yapar." for i in range(2, 8)))
    p = puan.puanla("ELEKTRİK YÖNETMELİĞİNDE DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK", degisiklik, KUYUM)
    assert p.ayrinti["amac"] == 0 and not p.ilgili
    assert {k.tur for k in p.kanitlar} == {"govde"}
    assert p.ayrinti["yogunluk"] <= puan.TEK_MADDE_YOGUNLUK_EN_FAZLA  # geçişlerin hepsi tek maddede


def test_kelime_agirligi_yaygin_kelimeyi_zayiflatir():
    assert puan.kelime_agirligi(10, 19_000) == 1.0  # ayırt edici kelime
    assert puan.kelime_agirligi(2_600, 19_000) < 0.5  # "ithalat" gibi yaygın kelime
    assert puan.kelime_agirligi(19_000, 19_000) == puan.AGIRLIK_EN_AZ
    assert puan.kelime_agirligi(5, 0) == 1.0  # arşiv yok
    icerik = "\n".join(f"MADDE {i} – (1) Esnaf satışta fatura düzenler ve deftere işler, denetimde ibraz eder."
                       + (" Altın ve gümüş satışında da belge verilir." if i % 10 == 5 else "") for i in range(1, 31))
    tam = puan.puanla("ESNAF TEBLİĞİ", icerik, KUYUM)
    zayif = puan.puanla("ESNAF TEBLİĞİ", icerik, KUYUM, agirlik={"altın": 0.3, "gümüş": 0.3})
    assert zayif.puan < tam.puan and zayif.ayrinti["gecis"] < tam.ayrinti["gecis"]
    # Başlık ağırlıktan etkilenmez.
    assert puan.puanla("ALTIN TEBLİĞİ", "", KUYUM, agirlik={"altın": 0.3}).ayrinti["baslik"] == puan.BASLIK_PUANI


# --- arşivde arama ve konu uygulama ------------------------------------------------------------------------------

@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(e)
    with Session(e) as s:
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
        for anahtar, ad, metin in [("9.5.1", "KUYUM TİCARETİ HAKKINDA YÖNETMELİK", YONETMELIK),
                                   ("9.5.2", "BELEDİYE ZABITA YÖNETMELİĞİ", ATIF),
                                   ("9.5.3", "ALTINBAŞ ÜNİVERSİTESİ YÖNETMELİĞİ", "MADDE 1 – Altınbaş Üniversitesi <b>eğitim</b> verir.")]:
            s.add(ArsivMevzuat(anahtar=anahtar, tur="Teblig", ad=ad, url="x", bicim="html", mulga=False,
                               ilk_gorulme=datetime(2026, 11, 23), son_gorulme=datetime(2026, 11, 23), kayip_sayisi=0,
                               durum="TAMAM", deneme=0, surum_sayisi=1))
            s.flush()
            s.add(ArsivSurumu(anahtar=anahtar, surum_no=1, cekildi=datetime(2026, 11, 23), ozet="x", metin=metin,
                              okuma="html", ekler=[]))
        s.add(KonuTanimi(ad=KUYUM.ad, is_kollari=["Kuyum"], kelimeler=list(KUYUM.kelimeler), haric=list(KUYUM.haric),
                         dislanan=list(KUYUM.dislanan), aktif=True, surum=1, guncelleme=datetime(2026, 11, 23),
                         eski_adlar=[], aciklama=""))
        s.commit()
    return e


def test_arama_ek_kurali_ifade_ve_vurgu(engine):
    with Session(engine) as s:
        assert arama.dizini_kur(s) > 3
        r = arama.ara(s, "altın")
        assert [x["anahtar"] for x in r["sonuclar"]] == ["9.5.1"]  # Altınbaş bulunmaz
        parca = r["sonuclar"][0]["parcalar"][0]
        assert "<mark>altın</mark>" in parca["parca"] and parca["bolum"].startswith("Madde")
        r = arama.ara(s, '"kimlik tespiti" kuyumcu')
        assert r["terimler"] == ["kimlik tespiti", "kuyumcu"] and r["sonuclar"][0]["parcalar"][0]["bolum"] == "Madde 4"
        assert arama.ara(s, "altın*")["toplam"] == 2  # yıldızla başı tutsun yeter
        assert arama.ara(s, "eğitim")["sonuclar"][0]["parcalar"][0]["parca"].count("&lt;b&gt;") == 1  # kaçışlı
        assert arama.ara(s, "  ")["toplam"] == 0


def test_konulari_uygula_liste_ve_imza(engine):
    with Session(engine) as s:
        from mevzuat.tanimlar import konulari_oku
        konular = konulari_oku(s)
        assert not arama.filtre_guncel_mi(s, konular)
        assert arama.kelime_agirliklari(s) == {}  # sıklık henüz yok: her kelime 1
        sayac = arama.tum_arsivi_filtrele(s, konular)
        assert sayac == {KUYUM.ad: 1} and arama.filtre_guncel_mi(s, konular)
        # Sıklık hesaplandı: "altın" 3 mevzuatın birinde (Altınbaş sayılmaz), "kuyum" ikisinde (dayanak atfı dahil).
        from mevzuat.db import SistemAyari
        siklik = s.get(SistemAyari, arama.SIKLIK_ANAHTARI).deger
        assert siklik["toplam"] == 3 and siklik["belge"]["altın"] == 1 and siklik["belge"]["kuyum"] == 2
        assert set(arama.kelime_agirliklari(s)) == set(KUYUM.kelimeler)
        ilgili = s.scalars(select(ArsivKonu).where(ArsivKonu.ilgili.is_(True))).all()
        assert [k.anahtar for k in ilgili] == ["9.5.1"] and ilgili[0].kanit
        assert arsiv.liste(s, konu=KUYUM.ad)["toplam"] == 1 and arsiv.liste(s, konu="*")["satirlar"][0]["konular"] == [KUYUM.ad]
        assert arsiv.detay(s, "9.5.1")["konular"][0]["konu"] == KUYUM.ad
        # Konu değişince imza tutmaz, zamanlayıcı yeniden hesaplar.
        konu = s.scalar(select(KonuTanimi))
        konu.kelimeler = ["altın", "kuyum", "pırlanta"]
        s.commit()
        assert not arama.filtre_guncel_mi(s, konulari_oku(s))


class SahteSurec:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


def test_zamanlayici_konu_degisince_filtreyi_baslatir(engine):
    baslatilan = []
    simdi = [datetime(2026, 11, 23, 12, 0)]

    def baslat():
        baslatilan.append(1)
        return SahteSurec()

    z = zm.Zamanlayici(engine, baslat=lambda i: SahteSurec(), saat=lambda: simdi[0], gecikme=lambda: 0,
                       arsiv_baslat=lambda i: SahteSurec(), filtre_baslat=baslat)
    z.kur()
    z.adim()
    assert baslatilan == [1]  # hiç hesaplanmamış
    z.adim()
    assert baslatilan == [1]  # süreç sürüyor
    z.filtre_surec.returncode = 0
    with Session(engine) as s:
        from mevzuat.tanimlar import konulari_oku
        arama.dizini_kur(s)
        arama.tum_arsivi_filtrele(s, konulari_oku(s))
    simdi[0] += timedelta(minutes=10)
    z.adim()
    assert baslatilan == [1] and z.filtre_surec is None  # güncel, yeniden başlamaz


def _post(c, yol, veri=None):
    return c.post(yol, json=veri or {}, headers={"X-CSRF-Token": c.get("/panel/api/oturum").json()["csrf"]})


def test_panel_arama_ve_konu_uclari(engine):
    with Session(engine) as s:
        from mevzuat.tanimlar import konulari_oku
        arama.dizini_kur(s)
        arama.tum_arsivi_filtrele(s, konulari_oku(s))
    c = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    assert c.get("/panel/api/arsiv/ara", params={"q": "altın"}).status_code == 401
    _post(c, "/panel/api/giris", {"eposta": "onay@firma.com", "parola": PAROLA})
    r = c.get("/panel/api/arsiv/ara", params={"q": "kuyum"}).json()
    # Arama filtre değildir: dayanak maddesindeki atıf da bulunur, başlıkta geçen önce gelir.
    assert r["toplam"] == 2 and r["sonuclar"][0]["anahtar"] == "9.5.1" and r["sonuclar"][0]["baslikta"]
    assert c.get("/panel/api/arsiv/ara", params={"q": "kuyum", "konu": KUYUM.ad}).json()["toplam"] == 1
    k = c.get("/panel/api/arsiv/konular").json()
    assert k["konular"] == [{"ad": KUYUM.ad, "ilgili": 1}] and k["guncel"] and k["toplam_ilgili"] == 1
    assert c.get("/panel/api/arsiv/liste", params={"konu": "*"}).json()["toplam"] == 1


# --- bütün konulara ortak dışlama: üniversitenin iç yönetmeliği (2026-11-26) ---------------------------------

def test_universite_ic_yonetmeligi_hicbir_konuya_girmez():
    from mevzuat import filtre, puan
    from mevzuat.filtre import konulari_yukle

    konular = konulari_yukle(Path(__file__).parents[1] / "config" / "konular.toml")
    baslik = "İSTANBUL ŞEHİR ÜNİVERSİTESİ ALTIN VE KIYMETLİ METAL ÇALIŞMALARI UYGULAMA VE ARAŞTIRMA MERKEZİ YÖNETMELİĞİ"
    metin = "MADDE 1 – (1) Bu Yönetmeliğin amacı; altın ve kıymetli maden, kuyum ve kuyumculuk çalışmalarıdır. " * 5
    assert filtre.genel_dislama(baslik) and filtre.eslesmeler(baslik, konular) == {}
    assert puan.ilgili_konular(baslik, metin, konular) == []
    # Üniversite kuruluş kanunu ve üniversite adı geçmeyen yönetmelik kuralın dışında.
    assert not filtre.genel_dislama("TÜRK-JAPON BİLİM VE TEKNOLOJİ ÜNİVERSİTESİNİN KURULUŞU HAKKINDA KANUN")
    assert not filtre.genel_dislama("Kuyum Ticareti Hakkında Yönetmelik")
    assert filtre.eslesmeler("Kuyum Ticareti Hakkında Yönetmelik", konular)
    # Küçük harf ve ek: "Üniversitesinin … yönetmeliğinde değişiklik" de dışlanır.
    assert filtre.genel_dislama("Akdeniz Üniversitesinin Lisans Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik")

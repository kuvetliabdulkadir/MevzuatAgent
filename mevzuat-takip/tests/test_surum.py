import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from mevzuat import pipeline, rapor, surum
from mevzuat.db import Calisma, IzlenenMevzuat, Kayit, MetinSurumu, init_db
from mevzuat.filtre import konulari_yukle
from mevzuat.surum import IzlenenTanim

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
# Gerçek sayfalar (01.10.2026)
GUNCELLENENLER = (FIXTURES / "mevzuat_gov_bugun_guncellenenler.html").read_text(encoding="utf-8")
KUYUM_HTML = (FIXTURES / "mevzuat_gov_7.5.38527_kuyum.html").read_text(encoding="utf-8")
KUYUM = "7.5.38527"
KUYUM_TANIM = IzlenenTanim("Kuyum Ticareti Hakkında Yönetmelik", 7, 5, 38527, "Kıymetli madenler ve kuyumculuk")
EKIM_1 = date(2026, 10, 1)
SIMDI = datetime(2026, 10, 1, 6, 30)


@pytest.fixture(autouse=True)
def beklemesiz(monkeypatch):
    monkeypatch.setattr(surum, "ISTEK_ARASI_BEKLEME", 0)


@pytest.fixture
def konular():
    return konulari_yukle(ROOT / "config" / "konular.toml")


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        s.add(Calisma(id=1, baslangic=SIMDI, durum="CALISIYOR", ozet={}))
        s.commit()
        yield s


class SahteMevzuatGov:
    """mevzuat.gov.tr yerine: ana sayfa + metinler (anahtar → html). İstenen adresler `istekler`de."""

    def __init__(self, metinler: dict[str, str], guncellenenler: str = GUNCELLENENLER):
        self.metinler = metinler
        self.guncellenenler = guncellenenler
        self.istekler: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.istekler.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(200, text=self.guncellenenler)
        p = request.url.params
        return httpx.Response(200, text=self.metinler.get(surum.anahtar(p["MevzuatTur"], p["MevzuatTertip"], p["MevzuatNo"]), ""))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def metin_istekleri(self) -> list[str]:
        return [u for u in self.istekler if "MevzuatFihristDetayIframe" in u]


def _degistir(html: str) -> str:
    """Kuyum yönetmeliğine gerçekçi üç değişiklik: madde metni, yeni ek madde, kaldırılan geçici madde."""
    # Kaynak HTML Word'den: kelimeler arasında satır sonları var ("GEÇİCİ\n\nMADDE 1").
    html = re.sub(r"Meslek\s+odasına kayıtlı olunması", "Meslek odasına ve ticaret siciline kayıtlı olunması", html, count=1)
    gecici = re.search(r"GEÇİCİ\s+MADDE 1", html)
    return (html[:gecici.start()] + "EK MADDE 1 – (1) Kuyum işletmeleri alım satımlarını elektronik ortamda kaydeder.</p><p>"
            + html[gecici.end():])


def _calistir(session, site, konular, tanimlar=(KUYUM_TANIM,), simdi=SIMDI):
    with site.client() as c:
        return surum.takip_et(session, c, list(tanimlar), konular, session.get(Calisma, 1), simdi.date(), simdi)


# --- ayrıştırma ---------------------------------------------------------------------------------

def test_bugun_guncellenenler_iki_link_bicimi():
    liste = surum.parse_guncellenenler(GUNCELLENENLER)
    assert len(liste) == 19
    otv = next(g for g in liste if g.anahtar == "1.5.4760")
    assert otv.ad == "ÖZEL TÜKETİM VERGİSİ KANUNU" and otv.rg_tarihi == date(2002, 6, 12)
    # Cumhurbaşkanı kararları doğrudan PDF'e bağlı: /MevzuatMetin/20.5.11822.pdf
    assert any(g.anahtar == "20.5.11822" and g.rg_tarihi == EKIM_1 for g in liste)


def test_bolum_bulunamazsa_hata():
    with pytest.raises(ValueError, match="site tasarımı"):
        surum.parse_guncellenenler("<html><body></body></html>")


def test_bolumler_gercek_yonetmelik():
    b = surum.bolumler(surum.html_metni(KUYUM_HTML))
    adlar = list(b)
    assert adlar[0] == "Başlangıç" and adlar[1] == "Madde 1"
    assert "Geçici Madde 1" in adlar and "Madde 15" in adlar
    assert b["Madde 6"].startswith("MADDE 6 –")


def test_bolum_adlari_turkce_ve_atiflar_baslik_degil():
    b = surum.bolumler("Giriş. GEÇİCİ MADDE 2 – a. EK MADDE 3- b. Mükerrer Madde 115 – c. Madde 9/A – d. "
                       "bu Kanunun Madde 6, Geçici Madde 7 hükümleri. (I) SAYILI LİSTE x (I) sayılı liste y")
    assert list(b) == ["Başlangıç", "Geçici Madde 2", "Ek Madde 3", "Mükerrer Madde 115", "Madde 9/A", "(I) Sayılı Liste"]


def test_farklar_uc_tur():
    eski = surum.html_metni(KUYUM_HTML)
    farklar = surum.farklar(eski, surum.html_metni(_degistir(KUYUM_HTML)))
    turler = {(f.bolum, f.tur) for f in farklar}
    assert ("Madde 6", "değişti") in turler
    assert ("Ek Madde 1", "eklendi") in turler
    assert ("Geçici Madde 1", "kaldırıldı") in turler
    m6 = next(f for f in farklar if f.bolum == "Madde 6")
    assert "«ve ticaret siciline»" in m6.yeni and "«»" in m6.eski


def test_tutar_degisikligi_okunur():
    eski = "Başlık MADDE 1 – Altın için tutar 2,5000 TL, gümüş için 1,2000 TL olarak uygulanır."
    (fark,) = surum.farklar(eski, eski.replace("2,5000", "2,7500"))
    assert fark.eski.endswith("«2,5000» TL, gümüş için 1,2000 TL olarak uygulanır.")
    assert "«2,7500»" in fark.yeni


def test_sadece_bosluk_farki_degisiklik_degil():
    assert surum.farklar("MADDE 1 – a  b\nc", "MADDE 1 –  a b c") == []


# --- günlük adım ---------------------------------------------------------------------------------

def test_ilk_cekim_taban_rapor_yok(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    sonuc = _calistir(session, site, konular)
    assert sonuc["taban"] >= 1 and sonuc["degisen"] == 0
    assert session.scalar(select(func.count()).select_from(Kayit)) == 0
    assert session.scalar(select(func.count()).select_from(MetinSurumu).where(MetinSurumu.anahtar == KUYUM)) == 1


def test_degisiklik_kaydi_ve_rapor(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    site.metinler[KUYUM] = _degistir(KUYUM_HTML)
    sonuc = _calistir(session, site, konular, simdi=SIMDI + timedelta(days=8))
    assert sonuc["degisen"] == 1

    kayit = session.scalar(select(Kayit))
    assert kayit.kaynak == "mevzuat_gov" and kayit.ilgili and kayit.icerik_durumu == "TAMAM"
    assert kayit.baslik == "Kuyum Ticareti Hakkında Yönetmelik — güncel metni değişti"
    assert "Kuyum" in kayit.is_kollari
    assert {d["bolum"] for d in kayit.degisiklikler} >= {"Madde 6", "Ek Madde 1", "Geçici Madde 1"}

    mail = rapor.rapor_olustur([kayit], EKIM_1)
    for govde in (mail.html, mail.metin):
        assert "Değişen mevzuat" in govde and "Madde 6" in govde
        assert "ve ticaret siciline" in govde and "Eski:" in govde and "Yeni:" in govde


def test_metin_eski_haline_donerse_yine_ayri_kayit(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    for gun, metin in [(8, _degistir(KUYUM_HTML)), (16, KUYUM_HTML)]:
        site.metinler[KUYUM] = metin
        _calistir(session, site, konular, simdi=SIMDI + timedelta(days=gun))
    assert session.scalar(select(func.count()).select_from(Kayit)) == 2  # dis_id çakışmadı


def test_listede_yoksa_haftada_bir_kontrol(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    ilk = len([u for u in site.metin_istekleri() if "38527" in u])
    _calistir(session, site, konular, simdi=SIMDI + timedelta(days=3))
    assert len([u for u in site.metin_istekleri() if "38527" in u]) == ilk  # 3 gün: çekilmedi
    _calistir(session, site, konular, simdi=SIMDI + timedelta(days=7))
    assert len([u for u in site.metin_istekleri() if "38527" in u]) == ilk + 1


def test_bugun_guncellenenlerde_ciktiysa_hemen_cekilir(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    site.guncellenenler = GUNCELLENENLER.replace("MevzuatNo=4760&amp;MevzuatTur=1", "MevzuatNo=38527&amp;MevzuatTur=7")
    once = len(site.metin_istekleri())
    _calistir(session, site, konular, simdi=SIMDI + timedelta(hours=12))
    assert any("38527" in u for u in site.metin_istekleri()[once:])


def test_otomatik_takip_sadece_eski_ve_ilgili_mevzuat(session, konular):
    site = SahteMevzuatGov({})
    # Ertesi gün: liste 01.10 tarihli yeni yayınları hâlâ gösteriyor (sitede görülen durum, 02.10.2026)
    sonuc = _calistir(session, site, konular, tanimlar=(), simdi=SIMDI + timedelta(days=1))
    otomatik = {i.anahtar: i for i in session.scalars(select(IzlenenMevzuat))}
    # ÖTV Kanunu (2002, "vergi") ve döviz dönüşüm tebliği (2023, "döviz") takibe girer
    assert {"1.5.4760", "9.5.40039"} <= otomatik.keys()
    assert all(i.neden == "otomatik" for i in otomatik.values())
    # Yeni yayımlanan ÖTV kararı (11822, dün) girmez: yeni yayın, onu Resmî Gazete kaynağı yakalıyor.
    # Ayrıca sadece PDF olarak var (HTML metni boş), takibe alınsa her hafta hata verirdi.
    assert "20.5.11822" not in otomatik and "20.5.11821" not in otomatik
    assert sonuc["otomatik_eklenen"] == len(otomatik)


def test_bos_metin_surum_sayilmaz(session, konular):
    site = SahteMevzuatGov({KUYUM: "<html><body></body></html>"})
    sonuc = _calistir(session, site, konular)
    assert sonuc["hata"] >= 1
    assert session.scalar(select(func.count()).select_from(MetinSurumu).where(MetinSurumu.anahtar == KUYUM)) == 0


def test_ayardan_cikarilan_takip_edilmez(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    once = len(site.metin_istekleri())
    _calistir(session, site, konular, tanimlar=(), simdi=SIMDI + timedelta(days=30))
    assert not any("38527" in u for u in site.metin_istekleri()[once:])


def test_aktif_false_takibi_kapatir(tmp_path):
    (tmp_path / "i.toml").write_text('aktif = false\n[[mevzuat]]\nad = "X"\ntur = 1\ntertip = 5\nno = 1\n', encoding="utf-8")
    assert surum.izlenenleri_yukle(tmp_path / "i.toml") is None


def test_ayar_dosyasi_gecerli(konular):
    tanimlar = surum.izlenenleri_yukle(ROOT / "config" / "izlenen_mevzuat.toml")
    assert len(tanimlar) >= 20
    pipeline.ayarlari_dogrula([], konular, tanimlar)  # konu adları konular.toml'da var


def test_tanimsiz_konu_hata(konular):
    with pytest.raises(ValueError, match="tanımsız konu"):
        pipeline.ayarlari_dogrula([], konular, [IzlenenTanim("X", 1, 5, 1, "Yazım Hatası")])

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from mevzuat import arsiv, arsiv_cekim, pipeline, rapor, surum
from mevzuat.db import (
    ArsivArama,
    ArsivIstegi,
    ArsivMevzuat,
    ArsivSurumu,
    Calisma,
    IzlenenMevzuat,
    Kayit,
    MetinSurumu,
    SistemAyari,
    init_db,
)
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
def beklemesiz(monkeypatch, tmp_path):
    monkeypatch.setattr(surum, "ISTEK_ARASI_BEKLEME", 0)
    monkeypatch.setattr(arsiv, "ISTEK_ARASI_BEKLEME", 0)
    # Günlük takip metni arşive çekiyor, arşiv dosyaları testin geçici klasörüne.
    monkeypatch.setenv("MEVZUAT_ARSIV_KLASORU", str(tmp_path / "arsiv"))


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


def test_liste_bos_gelirse_tekrar_istenir():
    # Site bazen listesi boş ana sayfa veriyor, ikinci istekte dolu geliyor.
    bos = re.sub(r"<tbody>.*?</tbody>", "<tbody></tbody>", GUNCELLENENLER, flags=re.S)
    assert surum.parse_guncellenenler(bos) == []
    sayfalar = iter([bos, GUNCELLENENLER])
    istekler = []

    def site(request):
        istekler.append(request)
        return httpx.Response(200, text=next(sayfalar))

    with httpx.Client(transport=httpx.MockTransport(site)) as c:
        assert len(surum.guncellenenleri_cek(c)) == 19
    assert len(istekler) == 2
    # Gerçekten boş günde sınır kadar denenir, sonra boş liste döner.
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=bos))) as c:
        assert surum.guncellenenleri_cek(c) == []


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


def test_okuyucu_bicim_farki_degisiklik_degil():
    """Okuyucu sürümü değişince çıkan boşluk/tablo ayırıcısı farkları (KDV Kanunu, yerel okuyucu 1 → 2)."""
    eski = "MADDE 2 – Teslim yapılmış sayılır. [1] Teslim sayılan haller: MADDE 15 – Tarihi Sayısı 14/4/2021 31454"
    yeni = "MADDE 2 – Teslim yapılmış sayılır.[1] Teslim sayılan haller: MADDE 15 – Tarihi | Sayısı 14/4/2021 | 31454"
    assert surum.farklar(eski, yeni) == []
    # Aynı maddede gerçek değişiklik varsa sadece o parça kalır.
    yeni2 = ("MADDE 2 – Teslim yapılmış sayılır.[1] Teslim sayılan haller: " + "x " * 40
             + "ve on bir gün içinde bildirilir.")
    eski2 = "MADDE 2 – Teslim yapılmış sayılır. [1] Teslim sayılan haller: " + "x " * 40 + "ve on gün içinde bildirilir."
    f = surum.farklar(eski2, yeni2)
    assert len(f) == 1 and "«bir»" in f[0].yeni


# --- günlük adım: arşivle bağlantı -------------------------------------------------------------------

def _surumler(session, anahtar_=KUYUM):
    return session.scalars(select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar_)
                           .order_by(ArsivSurumu.surum_no)).all()


def _iframe(site, no):
    return [u for u in site.metin_istekleri() if f"MevzuatNo={no}&" in u]


# Konuyla ilgisi olmayan, madde yapılı sade bir metin (Jandarma yatakhaneleri yönetmeliği yerine).
def _sade(ek: str = "") -> str:
    maddeler = "".join(f"<p>MADDE {i} – ({i}) Yatakhanelerin işletilmesinde uyulacak esaslar bu maddede "
                       f"düzenlenmiştir ve idare tarafından uygulanır.</p>" for i in range(1, 6))
    return f"<html><body><p>JANDARMA YATAKHANELERİ YÖNETMELİĞİ</p>{maddeler}{ek}</body></html>"


def _arsive_v1(session, site, anahtar_=KUYUM, ad="Kuyum Ticareti Hakkında Yönetmelik"):
    """Tam taramada alınmış gibi arşive v1 yazar (takipte metin_surumleri yok)."""
    session.add(ArsivMevzuat(anahtar=anahtar_, tur="TumYonetmelik", ad=ad, url=surum.metin_url(anahtar_), bicim="html",
                             ilk_gorulme=SIMDI, son_gorulme=SIMDI, durum="BEKLIYOR"))
    session.commit()
    with site.client() as c:
        sonuc = arsiv_cekim.mevzuati_cek(session, arsiv.Istemci(c), session.get(ArsivMevzuat, anahtar_),
                                         arsiv.arsiv_klasoru(), SIMDI - timedelta(days=30), None, arsiv.metin_adresi)
    session.commit()
    assert sonuc == "yeni"


def test_ilk_cekim_arsive_taban_rapor_yok(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    sonuc = _calistir(session, site, konular)
    assert sonuc["taban"] >= 1 and sonuc["degisen"] == 0
    assert session.scalar(select(func.count()).select_from(Kayit)) == 0
    assert [s.surum_turu for s in _surumler(session)] == ["ilk"]
    # Eski tablo yeni veri almaz.
    assert session.scalar(select(func.count()).select_from(MetinSurumu)) == 0
    # Arama dizini de yazıldı.
    assert session.scalar(select(func.count()).select_from(ArsivArama).where(ArsivArama.anahtar == KUYUM)) > 0


def test_arsivdeki_v1_ilk_degisiklikte_karsilastirilir(session, konular):
    """Tam taramadan gelen v1 taban: takipte hiç çekilmemiş olsa da ilk değişiklik v1↔v2 farkı olarak rapora girer."""
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _arsive_v1(session, site)
    site.metinler[KUYUM] = _degistir(KUYUM_HTML)
    sonuc = _calistir(session, site, konular)
    assert sonuc["degisen"] == 1 and sonuc["taban"] == 0

    assert [(s.surum_no, s.surum_turu) for s in _surumler(session)] == [(1, "ilk"), (2, "degisiklik")]
    kayit = session.scalar(select(Kayit))
    assert kayit.dis_id == f"{KUYUM}@v2"
    assert kayit.kaynak == "mevzuat_gov" and kayit.ilgili and kayit.icerik_durumu == "TAMAM"
    assert kayit.baslik == "Kuyum Ticareti Hakkında Yönetmelik — güncel metni değişti"
    assert "Kuyum" in kayit.is_kollari
    assert {d["bolum"] for d in kayit.degisiklikler} >= {"Madde 6", "Ek Madde 1", "Geçici Madde 1"}

    mail = rapor.rapor_olustur([kayit], EKIM_1)
    for govde in (mail.html, mail.metin):
        assert "Değişen mevzuat" in govde and "Madde 6" in govde
        assert "ve ticaret siciline" in govde and "Eski:" in govde and "Yeni:" in govde


def test_v2_v3_ayri_karsilastirilir(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    for gun, metin in [(8, _degistir(KUYUM_HTML)), (16, KUYUM_HTML)]:
        site.metinler[KUYUM] = metin
        _calistir(session, site, konular, simdi=SIMDI + timedelta(days=gun))
    kayitlar = session.scalars(select(Kayit).order_by(Kayit.id)).all()
    assert [k.dis_id for k in kayitlar] == [f"{KUYUM}@v2", f"{KUYUM}@v3"]
    # v2↔v3: eklenen ek madde geri kaldırıldı, geçici madde geri geldi.
    v3 = {(d["bolum"], d["tur"]) for d in kayitlar[1].degisiklikler}
    assert ("Ek Madde 1", "kaldırıldı") in v3 and ("Geçici Madde 1", "eklendi") in v3


def test_okuma_duzeltmesi_rapor_uretmez(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    # Okuyucu iyileşmiş gibi: karşılaştırma özeti farklı, sitenin kaynağı aynı (karar 19).
    v1 = _surumler(session)[0]
    v1.ozet = "0" * 64
    session.commit()
    sonuc = _calistir(session, site, konular, simdi=SIMDI + timedelta(days=8))
    assert sonuc["okuma_duzeltmesi"] == 1 and sonuc["degisen"] == 0
    assert [s.surum_turu for s in _surumler(session)] == ["ilk", "okuma_duzeltmesi"]
    assert session.scalar(select(func.count()).select_from(Kayit)) == 0


def test_takipte_olmayan_ilgisiz_degisiklik_rapora_girmez(session, konular):
    jandarma = "7.5.38828"
    site = SahteMevzuatGov({jandarma: _sade()})
    _arsive_v1(session, site, jandarma, "JANDARMA GENEL KOMUTANLIĞI VARDİYA YATAKHANELERİ")
    site.metinler[jandarma] = _sade("<p>EK MADDE 1 – (1) Yatakhaneler gece de açık tutulur.</p>")
    sonuc = _calistir(session, site, konular, tanimlar=())
    assert sonuc["ilgisiz_degisen"] == 1 and sonuc["degisen"] == 0
    assert len(_surumler(session, jandarma)) == 2  # değişiklik arşivde yine sürüm
    assert session.scalar(select(func.count()).select_from(Kayit)) == 0


def test_takipte_olmayan_degisiklik_konuya_uyarsa_rapora_girer(session, konular):
    """Bugün güncellenenlerde çıkan, takipte olmayan ama değişen maddeleri bir konuyla ilgili mevzuat."""
    tasinmaz = "7.5.24645"  # listede "TAŞINMAZ TİCARETİ HAKKINDA YÖNETMELİK" (ad konuya uymuyor)
    site = SahteMevzuatGov({tasinmaz: _sade()})
    _arsive_v1(session, site, tasinmaz, "TAŞINMAZ TİCARETİ HAKKINDA YÖNETMELİK")
    altin = ("<p>EK MADDE 1 – (1) Kuyum ve altın alım satımında kıymetli maden işlemleri, kuyumcu ziynet eşyası "
             "ve altın hesapları kayda alınır. Altın ve kıymetli madenler için ayrıca kuyum defteri tutulur.</p>")
    site.metinler[tasinmaz] = _sade(altin)
    sonuc = _calistir(session, site, konular, tanimlar=())
    assert sonuc["degisen"] == 1
    kayit = session.scalar(select(Kayit))
    assert kayit.dis_id == f"{tasinmaz}@v2"
    assert any(k.endswith("(değişiklikte)") for k in kayit.eslesmeler["Kıymetli madenler ve kuyumculuk"])


def test_arsivde_olmayan_mevzuat_acilir_pdf_haftalik_ise_kalir(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    karar = session.get(ArsivMevzuat, "20.5.11822")  # sadece PDF, yeni yayın
    assert karar is not None and karar.bicim == "pdf" and karar.durum == "BEKLIYOR"
    assert karar.tur == "CumhurbaskaniKararlari" and karar.url.endswith("/MevzuatMetin/20.5.11822.pdf")
    assert not any("20.5.11822" in u for u in site.istekler)
    otv = session.get(ArsivMevzuat, "1.5.4760")
    assert otv.tur == "Kanun" and otv.bicim == "html"


def test_listede_yoksa_haftada_bir_kontrol(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    ilk = len(_iframe(site, 38527))
    _calistir(session, site, konular, simdi=SIMDI + timedelta(days=3))
    assert len(_iframe(site, 38527)) == ilk  # 3 gün: çekilmedi
    _calistir(session, site, konular, simdi=SIMDI + timedelta(days=7))
    assert len(_iframe(site, 38527)) > ilk


def test_bugun_guncellenenlerde_ciktiysa_hemen_cekilir(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    site.guncellenenler = GUNCELLENENLER.replace("MevzuatNo=4760&amp;MevzuatTur=1", "MevzuatNo=38527&amp;MevzuatTur=7")
    once = len(_iframe(site, 38527))
    _calistir(session, site, konular, simdi=SIMDI + timedelta(hours=12))
    assert len(_iframe(site, 38527)) > once


def test_arsiv_isi_surerken_sonraki_calismaya_kalir(session, konular, tmp_path):
    from filelock import FileLock

    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    klasor = arsiv.arsiv_klasoru()
    klasor.mkdir(parents=True, exist_ok=True)
    with FileLock(str(klasor / "arsiv.lock")):
        sonuc = _calistir(session, site, konular)
    assert sonuc["ertelenen"] >= 1 and not _iframe(site, 38527)
    assert KUYUM in session.get(SistemAyari, surum.BEKLEYEN_AYARI).deger
    assert session.get(IzlenenMevzuat, KUYUM).son_kontrol is None
    # Kilit boşalınca sonraki çalışmada bakılır, bekleyen listesi temizlenir. Bugün listesi artık boş olsa bile.
    site.guncellenenler = GUNCELLENENLER.replace('id="bugunGuncellenenMevzuat"', 'id="bos"') + \
        '<div id="bugunGuncellenenMevzuat"><table><tbody></tbody></table></div>'
    sonuc = _calistir(session, site, konular, simdi=SIMDI + timedelta(hours=1))
    assert sonuc["taban"] >= 1 and _iframe(site, 38527)
    assert session.get(SistemAyari, surum.BEKLEYEN_AYARI).deger == []


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
    _calistir(session, site, konular)
    assert _surumler(session) == []
    assert session.get(ArsivMevzuat, KUYUM).durum in ("SITEDE_YOK", "HATA")


def test_ayardan_cikarilan_takip_edilmez(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    once = len(_iframe(site, 38527))
    _calistir(session, site, konular, tanimlar=(), simdi=SIMDI + timedelta(days=30))
    assert len(_iframe(site, 38527)) == once


# --- haftalık liste yenilemesi -----------------------------------------------------------------------

def test_haftalik_istek_bos_arsivde_acilmaz_dolu_arsivde_haftada_bir(session, konular):
    assert arsiv.haftalik_istek(session, SIMDI) is None  # boş arşivde bu iş tam tarama olurdu
    _calistir(session, SahteMevzuatGov({KUYUM: KUYUM_HTML}), konular)
    istek = arsiv.haftalik_istek(session, SIMDI)
    assert istek is not None and istek.secenekler == {"turler": list(arsiv.TURLER), "yeniden": False, "haftalik": True}
    assert arsiv.haftalik_istek(session, SIMDI) is None  # zaten sırada
    istek.durum, istek.bitti = "BITTI", SIMDI
    session.commit()
    assert arsiv.haftalik_istek(session, SIMDI + timedelta(days=6)) is None
    assert arsiv.haftalik_istek(session, SIMDI + timedelta(days=7, minutes=1)) is not None


def test_haftalik_istek_deneme_seti_ve_hatali_isi_saymaz(session, konular):
    _calistir(session, SahteMevzuatGov({KUYUM: KUYUM_HTML}), konular)
    session.add_all([
        ArsivIstegi(istendi=SIMDI, durum="BITTI", secenekler={"anahtarlar": [KUYUM]}),
        ArsivIstegi(istendi=SIMDI, durum="HATALI", secenekler={"turler": ["Kanun"], "yeniden": False}),
    ])
    session.commit()
    assert arsiv.haftalik_istek(session, SIMDI + timedelta(hours=1)) is not None


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


# --- ayrıntılı fark gösterimi ------------------------------------------------------------------------

def test_fark_cikan_giren_ve_ozet():
    eski = "MADDE 1 – Vergi oranı yüzde 20 olarak uygulanır. MADDE 2 – Beyan ayda bir verilir. MADDE 3 – Eski hüküm."
    yeni = ("MADDE 1 – Vergi oranı yüzde 25 olarak uygulanır. MADDE 2 – (Mülga: RG-1/1/2027-1) "
            "MADDE 4 – Yeni hüküm eklenmiştir.")
    f = {x.bolum: x for x in surum.farklar(eski, yeni)}
    assert f["Madde 1"].tur == "değişti" and f["Madde 1"].cikan == ("20",) and f["Madde 1"].giren == ("25",)
    assert f["Madde 2"].tur == "mülga edildi"
    assert f["Madde 3"].tur == "kaldırıldı" and f["Madde 4"].tur == "eklendi"
    from dataclasses import asdict
    assert surum.fark_ozeti([asdict(x) for x in f.values()]) == (
        "Madde 1 değişti; Madde 2 mülga edildi; Madde 4 eklendi; Madde 3 kaldırıldı")


def test_cok_degisiklikli_madde_kalan_sayisini_soyler(monkeypatch):
    monkeypatch.setattr(surum, "BOLUM_BASINA_FARK", 2)
    dolgu = " ".join(["aynı"] * 30)
    eski = "MADDE 1 – " + f" {dolgu} ".join(["a1", "b1", "c1", "d1"])
    yeni = "MADDE 1 – " + f" {dolgu} ".join(["a2", "b2", "c2", "d2"])
    f = surum.farklar(eski, yeni)
    assert len(f) == 3 and "2 değişiklik daha" in f[-1].yeni


def test_rapor_degisiklikleri_isaretli_ve_kacisli(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    site.metinler[KUYUM] = _degistir(KUYUM_HTML).replace("ticaret siciline", "ticaret <b>siciline</b>", 1)
    _calistir(session, site, konular, simdi=SIMDI + timedelta(days=8))
    kayit = session.scalar(select(Kayit))
    mail = rapor.rapor_olustur([kayit], EKIM_1)
    assert "Özet:" in mail.html and "Madde 6 değişti" in mail.html and "Ek Madde 1 eklendi" in mail.html
    assert "font-weight:bold" in mail.html and "line-through" in mail.html
    assert "Eklenen:" in mail.html and "Eklenen:" in mail.metin and "Özet: " in mail.metin
    assert "<b>siciline</b>" not in mail.html  # kaynaktan gelen HTML kaçışlanır


def test_pdfli_yeni_yayin_ertesi_gun_de_cekilmez(session, konular):
    site = SahteMevzuatGov({KUYUM: KUYUM_HTML})
    _calistir(session, site, konular)
    _calistir(session, site, konular, simdi=SIMDI + timedelta(days=1))
    assert not any("/MevzuatMetin/" in u for u in site.istekler)
    assert session.get(ArsivMevzuat, "20.5.11822").durum == "BEKLIYOR"

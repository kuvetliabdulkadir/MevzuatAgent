"""Mevzuat arşivi (arsiv.py): liste, metin, ekler, PDF, sürüm, kalktı, kaldığı yerden devam, durdurma, zamanlayıcı ve panel.
Site sahte (httpx.MockTransport), internete çıkılmaz."""

import json
from datetime import datetime, time, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import arsiv, arsiv_cekim, ocr
from mevzuat import zamanlama as zm
from mevzuat.db import ArsivIstegi, ArsivMevzuat, ArsivSurumu, Denetim, init_db
from mevzuat.http import make_client
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

FIXTURES = Path(__file__).parent / "fixtures"
def _metinli_pdf() -> bytes:
    """Gerçek yazı katmanı olan (Helvetica, 500 karakteri aşan) tek sayfalık PDF."""
    satirlar = [f"BT /F1 10 Tf 40 {780 - 14 * i} Td (Madde {i + 1} - Bu Karar yayimi tarihinde yururluge girer ve uygulanir.) Tj ET"
                for i in range(20)]
    akis = "\n".join(satirlar).encode("latin-1")
    nesneler = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(akis) + akis + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    cikti, yerler = b"%PDF-1.4\n", []
    for i, n in enumerate(nesneler, 1):
        yerler.append(len(cikti))
        cikti += b"%d 0 obj\n" % i + n + b"\nendobj\n"
    xref = len(cikti)
    cikti += b"xref\n0 %d\n0000000000 65535 f \n" % (len(nesneler) + 1)
    cikti += b"".join(b"%010d 00000 n \n" % y for y in yerler)
    return cikti + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(nesneler) + 1, xref)


METINLI_PDF = _metinli_pdf()
TARANMIS_PDF = (FIXTURES / "rg_20261001-3-2_taranmis.pdf").read_bytes()
PAROLA = "dogru-parola-123"
UZUN = "Bu mevzuatın metni yeterince uzundur. " * 10


def _satir(ad, url, tarih="01.02.2020", sayi="31000"):
    return {"mevAdi": ad, "url": url, "resmiGazeteTarihi": tarih, "resmiGazeteSayisi": sayi}


class SahteSite:
    """mevzuat.gov.tr'nin arşivin kullandığı kısımları. Listeler, metinler ve dosyalar testte değiştirilebilir."""

    def __init__(self):
        self.listeler = {
            "Kanun": [_satir("BİRİNCİ KANUN", "mevzuat?MevzuatNo=1&MevzuatTur=1&MevzuatTertip=5"),
                      _satir("İKİNCİ KANUN", "mevzuat?MevzuatNo=2&MevzuatTur=1&MevzuatTertip=5"),
                      _satir("BOŞ METİNLİ KANUN", "mevzuat?MevzuatNo=3&MevzuatTur=1&MevzuatTertip=5")],
            "CumhurbaskaniKararlari": [_satir("TARANMIŞ KARAR", "https://www.mevzuat.gov.tr/MevzuatMetin/20.5.9.pdf")],
            "CumhurbaskanligiGenelgeleri": [_satir("GENELGE", "https://www.mevzuat.gov.tr/MevzuatMetin/CumhurbaskanligiGenelgeleri/20261114-13.pdf",
                                                   tarih="14/11/2026", sayi="")],
            "MulgaKanun": [_satir("ESKİ KANUNUN YÜRÜRLÜKTEN KALDIRILMIŞ HÜKÜMLERİ", "mevzuat?MevzuatNo=765&MevzuatTur=5&MevzuatTertip=3")],
        }
        self.metinler = {
            "1.5.1": f"<html><body><p>Madde 1 – {UZUN}</p><a href=\"1.5.1-Ek.docx\">Ekleri için tıklayınız</a></body></html>",
            "1.5.2": f"<html><body><p>Madde 1 – {UZUN}</p></body></html>",
            "1.5.3": "<html><body></body></html>",
            "5.3.765": f"<html><body><p>Mülga hükümler. {UZUN}</p></body></html>",
        }
        self.dosyalar = {
            "/MevzuatMetin/yonetmelik/1.5.1-Ek.docx": (b"PK\x03\x04 ek dosyasi", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "/MevzuatMetin/20.5.9.pdf": (TARANMIS_PDF, "application/pdf"),
            "/MevzuatMetin/CumhurbaskanligiGenelgeleri/20261114-13.pdf": (METINLI_PDF, "application/pdf"),
        }
        self.hatali: set[str] = set()  # bu anahtarların metni 500 döner
        # (tür, başlangıç) sayfası bu kadar kez boş cevap verir (site ara ara JSON yerine boş dönüyor), -1 ise hep.
        self.bozuk: dict[tuple[str, int], int] = {}
        self.istekler: list[str] = []
        self.liste_istekleri: list[tuple[str, int]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        yol = request.url.path
        self.istekler.append(str(request.url))
        if yol == "/" and request.method == "GET":
            return httpx.Response(200, text='<input name="antiforgerytoken" type="hidden" value="anahtar-1" />')
        if yol == "/anasayfa/MevzuatDatatable":
            assert request.headers["antiforgerytoken"] == "anahtar-1"
            govde = json.loads(request.content)
            tur, bas, boy = govde["parameters"]["MevzuatTur"], govde["start"], govde["length"]
            self.liste_istekleri.append((tur, bas))
            if self.bozuk.get((tur, bas)):
                self.bozuk[(tur, bas)] -= 1
                return httpx.Response(200, content=b"", headers={"content-type": "text/html"})
            hepsi = self.listeler.get(tur, [])
            return httpx.Response(200, json={"recordsTotal": len(hepsi), "data": hepsi[bas:bas + boy]})
        if yol == "/anasayfa/MevzuatFihristDetayIframe":
            q = request.url.params
            a = f'{q["MevzuatTur"]}.{q["MevzuatTertip"]}.{q["MevzuatNo"]}'
            if a in self.hatali:
                return httpx.Response(500)
            return httpx.Response(200, text=self.metinler.get(a, "<html></html>"))
        if yol in self.dosyalar:
            veri, tur = self.dosyalar[yol]
            return httpx.Response(200, content=veri, headers={"content-type": tur})
        # Site olmayan dosyada 200 ile HTML sayfa veriyor.
        return httpx.Response(200, text="<html>bulunamadı</html>", headers={"content-type": "text/html"})


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(e)
    with Session(e) as s:
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
    return e


@pytest.fixture
def site():
    return SahteSite()


def _calistir(engine, site, tmp_path, **kw):
    client = make_client(transport=httpx.MockTransport(site), uyu=lambda s: None)
    return arsiv.arsivi_olustur(engine, client=client, uyu=lambda s: None, klasor=tmp_path / "arsiv", **kw)


def test_anahtar_ve_adresler():
    assert arsiv.anahtar_bul("mevzuat?MevzuatNo=4760&MevzuatTur=1&MevzuatTertip=5") == "1.5.4760"
    assert arsiv.anahtar_bul("https://www.mevzuat.gov.tr/MevzuatMetin/20.5.11994.pdf") == "20.5.11994"
    assert arsiv.anahtar_bul("/MevzuatMetin/CumhurbaskanligiGenelgeleri/20261114-13.pdf") == "genelge.20261114-13"
    assert arsiv.anahtar_bul("http://www.resmigazete.gov.tr/eskiler/2026/10/20261031.pdf").startswith("url.")
    assert arsiv.metin_adresi("1.5.4760").endswith("MevzuatTur=1&MevzuatNo=4760&MevzuatTertip=5")
    assert arsiv.metin_adresi("genelge.20261114-13") is None
    # Göreli ek bağlantısı yönetmelik klasöründe aranır.
    assert arsiv_cekim.dosya_adaylari("9.5.40085-Ek.docx")[0][0] == "https://www.mevzuat.gov.tr/MevzuatMetin/yonetmelik/9.5.40085-Ek.docx"
    baglantilar = [("9.5.1-EK.zip", "Ekler"), ("mevzuat?x=1", "bağlantı"), ("7.5.2-%20EK.docx", "ek")]
    assert arsiv_cekim.ek_baglantilari(baglantilar)[0] == ["9.5.1-EK.zip", "7.5.2-%20EK.docx"]


def test_ilk_kurulum_metin_ek_pdf_ve_durumlar(engine, site, tmp_path, monkeypatch):
    monkeypatch.setattr(arsiv, "SAYFA_BOYU", 2)  # Kanun listesi 2 sayfaya bölünsün
    monkeypatch.setattr(ocr, "ocr_kullanilabilir", lambda: False)  # Tesseract olmayan sunucu gibi
    assert _calistir(engine, site, tmp_path) == "BITTI"
    assert ("Kanun", 2) in site.liste_istekleri  # toplamdan hesaplanan ikinci sayfa da istendi
    with Session(engine) as s:
        m = {x.anahtar: x for x in s.scalars(select(ArsivMevzuat))}
        assert set(m) == {"1.5.1", "1.5.2", "1.5.3", "20.5.9", "genelge.20261114-13", "5.3.765"}
        assert m["1.5.1"].durum == "TAMAM" and m["1.5.1"].surum_sayisi == 1 and m["1.5.1"].ad == "BİRİNCİ KANUN"
        assert m["1.5.1"].rg_tarihi.isoformat() == "2020-02-01" and m["1.5.1"].rg_sayisi == 31000
        assert m["1.5.3"].durum == "SITEDE_YOK" and m["1.5.3"].surum_sayisi == 0  # HTML boş, PDF de yok
        assert "metin yok" in m["1.5.3"].son_hata
        assert m["20.5.9"].durum == "OCR_GEREKLI" and m["20.5.9"].bicim == "pdf"  # taranmış, OCR yapılamadı
        assert m["20.5.9"].kalite == "kusurlu"
        assert m["genelge.20261114-13"].durum == "TAMAM"
        assert m["5.3.765"].mulga and m["5.3.765"].durum == "TAMAM"
        # Ek dosyası diske yazıldı, sürümde yolu ve özeti duruyor.
        v1 = s.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == "1.5.1"))
        assert "Madde 1" in v1.metin and v1.okuma == "html" and len(v1.ekler) == 1
        assert v1.surum_turu == "ilk" and v1.okuyucu_surumu == arsiv_cekim.OKUYUCU_SURUMU and v1.kaynak_ozeti
        ek = v1.ekler[0]
        assert ek["ad"] == "1.5.1-Ek.docx" and ek["boyut"] > 0 and ek["durum"] == "indi"
        assert (tmp_path / "arsiv" / ek["yol"]).read_bytes().startswith(b"PK")
        pdf = s.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == "genelge.20261114-13"))
        assert pdf.okuma == "pdf_metin" and (tmp_path / "arsiv" / pdf.pdf_yolu).read_bytes()[:5] == b"%PDF-"
        taranmis = s.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == "20.5.9"))
        assert taranmis.okuma == "okunamadi" and taranmis.pdf_yolu and taranmis.kalite == "kusurlu"


def test_degisen_metin_yeni_surum_degismeyen_surum_uretmez(engine, site, tmp_path):
    _calistir(engine, site, tmp_path)
    site.metinler["1.5.2"] = f"<html><body><p>Madde 1 – {UZUN} Değişti.</p></body></html>"
    # Ek dosyası değişince de yeni sürüm olur (adı aynı kalsa da).
    site.dosyalar["/MevzuatMetin/yonetmelik/1.5.1-Ek.docx"] = (b"PK\x03\x04 yeni ek", "application/octet-stream")
    assert _calistir(engine, site, tmp_path, yeniden=True) == "BITTI"
    _calistir(engine, site, tmp_path, yeniden=True)  # hiçbir şey değişmedi
    with Session(engine) as s:
        sayilar = {m.anahtar: m.surum_sayisi for m in s.scalars(select(ArsivMevzuat))}
        assert sayilar["1.5.2"] == 2 and sayilar["1.5.1"] == 2 and sayilar["genelge.20261114-13"] == 1
        assert arsiv.metin(s, "1.5.2", 1)["metin"] != arsiv.metin(s, "1.5.2")["metin"]
        assert "Değişti" in arsiv.metin(s, "1.5.2")["metin"]


def test_iki_tam_listede_gorunmeyen_kalkti_sayilir(engine, site, tmp_path):
    _calistir(engine, site, tmp_path)
    site.listeler["Kanun"] = site.listeler["Kanun"][:2]  # üçüncü kanun siteden düştü
    _calistir(engine, site, tmp_path)
    with Session(engine) as s:
        m = s.get(ArsivMevzuat, "1.5.3")
        assert m.kayip_sayisi == 1 and m.kalkti is None  # tek sefer yetmez
    _calistir(engine, site, tmp_path)
    with Session(engine) as s:
        assert s.get(ArsivMevzuat, "1.5.3").kalkti is not None
        assert arsiv.liste(s, durum="KALKTI")["toplam"] == 1
    # Geri gelirse kalktı işareti silinir, satır hiç silinmez.
    site.listeler["Kanun"].append(_satir("BOŞ METİNLİ KANUN", "mevzuat?MevzuatNo=3&MevzuatTur=1&MevzuatTertip=5"))
    _calistir(engine, site, tmp_path)
    with Session(engine) as s:
        assert s.get(ArsivMevzuat, "1.5.3").kalkti is None


def test_bozuk_gelen_liste_sayfasi_tekrar_istenir(engine, site, tmp_path, monkeypatch):
    monkeypatch.setattr(arsiv, "SAYFA_BOYU", 2)
    site.bozuk[("Kanun", 2)] = 3  # üç kez boş, dördüncüde düzgün
    assert _calistir(engine, site, tmp_path, turler=["Kanun"]) == "BITTI"
    assert site.liste_istekleri.count(("Kanun", 2)) == 4
    with Session(engine) as s:
        assert s.get(ArsivMevzuat, "1.5.3") is not None


def test_hic_alinamayan_liste_sayfasi_isi_durdurmaz(engine, site, tmp_path, monkeypatch):
    monkeypatch.setattr(arsiv, "SAYFA_BOYU", 2)
    _calistir(engine, site, tmp_path, turler=["Kanun"])
    site.bozuk[("Kanun", 2)] = -1  # hep boş
    with Session(engine) as s:
        istek = arsiv.istek_olustur(s, 1, ["Kanun", "MulgaKanun"], False, zamanlayici_calisiyor=True)
        s.commit()
        istek_id = istek.id
    assert _calistir(engine, site, tmp_path, istek_id=istek_id) == "BITTI"
    with Session(engine) as s:
        istek = s.get(ArsivIstegi, istek_id)
        assert "eksik kaldı" in istek.hata and "Kanun listesinin 2. kaydından" in istek.hata
        assert "0 bayt" in istek.ilerleme["liste"]["Kanun"]["eksik"]  # sitenin cevabı hatada görünür
        # Eksik listede görünmeyen üçüncü kanun kayıp sayılmaz, sonraki tür (mülga) yine işlendi.
        assert s.get(ArsivMevzuat, "1.5.3").kayip_sayisi == 0
        assert s.get(ArsivMevzuat, "5.3.765").durum == "TAMAM"


def test_hata_veren_kaldigi_yerden_devam_eder(engine, site, tmp_path):
    site.hatali = {"1.5.2"}
    _calistir(engine, site, tmp_path, turler=["Kanun"])
    with Session(engine) as s:
        m = s.get(ArsivMevzuat, "1.5.2")
        assert m.durum == "HATA" and m.deneme == 1 and m.son_hata
        assert s.get(ArsivMevzuat, "1.5.1").durum == "TAMAM"
    site.hatali, site.istekler = set(), []
    _calistir(engine, site, tmp_path, turler=["Kanun"])
    metin_istekleri = [u for u in site.istekler if "MevzuatFihristDetayIframe" in u]
    assert len(metin_istekleri) == 1 and "MevzuatNo=2" in metin_istekleri[0]  # sadece kalan çekildi
    with Session(engine) as s:
        assert s.get(ArsivMevzuat, "1.5.2").durum == "TAMAM"


def test_panelden_durdurulan_is_durur(engine, site, tmp_path):
    with Session(engine) as s:
        istek = ArsivIstegi(istendi=datetime.now(), basladi=datetime.now(), durum="DURDURULUYOR",
                            secenekler={"turler": ["Kanun"], "yeniden": False})
        s.add(istek)
        s.commit()
        istek_id = istek.id
    assert _calistir(engine, site, tmp_path, istek_id=istek_id) == "DURDURULDU"
    with Session(engine) as s:
        istek = s.get(ArsivIstegi, istek_id)
        assert istek.durum == "DURDURULDU" and istek.bitti and istek.ilerleme["liste"]["Kanun"]["toplam"] == 3
        assert s.scalar(select(ArsivSurumu).limit(1)) is None  # metne geçmeden durdu


def test_istek_secenekleri_ve_ilerleme_yazilir(engine, site, tmp_path):
    with Session(engine) as s:
        istek = arsiv.istek_olustur(s, 1, ["Kanun"], False, zamanlayici_calisiyor=True)
        s.commit()
        istek_id = istek.id
    assert _calistir(engine, site, tmp_path, istek_id=istek_id) == "BITTI"
    with Session(engine) as s:
        istek = s.get(ArsivIstegi, istek_id)
        assert istek.durum == "BITTI" and istek.ilerleme["metin"]["toplam"] == 3 and istek.ilerleme["metin"]["sitede_yok"] == 1
        assert {m.tur for m in s.scalars(select(ArsivMevzuat))} == {"Kanun"}


def test_istek_kurallari(engine):
    with Session(engine) as s:
        with pytest.raises(ValueError, match="Zamanlayıcı çalışmıyor"):
            arsiv.istek_olustur(s, 1, None, False, zamanlayici_calisiyor=False)
        with pytest.raises(ValueError, match="Bilinmeyen tür"):
            arsiv.istek_olustur(s, 1, ["Uydurma"], False, zamanlayici_calisiyor=True)
        arsiv.istek_olustur(s, 1, None, False, zamanlayici_calisiyor=True)
        s.commit()
        with pytest.raises(ValueError, match="zaten sırada"):
            arsiv.istek_olustur(s, 1, None, False, zamanlayici_calisiyor=True)
        # Sıradaki iş durdurulunca hemen durur.
        assert arsiv.durdur(s).durum == "DURDURULDU"
        with pytest.raises(ValueError, match="arşiv işi yok"):
            arsiv.durdur(s)


class SahteSurec:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


def test_zamanlayici_arsiv_istegini_alir_ve_sonuclandirir(engine):
    baslatilan = []

    def arsiv_baslat(istek_id):
        baslatilan.append(istek_id)
        return SahteSurec()

    z = zm.Zamanlayici(engine, baslat=lambda i: SahteSurec(), saat=lambda: datetime(2026, 11, 20, 12, 0),
                       gecikme=lambda: 0, arsiv_baslat=arsiv_baslat)
    z.kur()
    with Session(engine) as s:
        istek = arsiv.istek_olustur(s, 1, None, False, zamanlayici_calisiyor=True)
        s.commit()
        istek_id = istek.id
    # Açılışta kaçan günlük iş başladı: o sürerken arşiv işi başlamaz (ikisi aynı arşiv kilidini kullanıyor).
    assert z.surec is not None
    z.adim()
    assert baslatilan == []
    z.surec.returncode = 0
    z.adim()
    assert baslatilan == [istek_id]
    with Session(engine) as s:
        assert s.get(ArsivIstegi, istek_id).durum == "CALISIYOR"
    # Süreç sonucu yazamadan öldü, istek HATALI olur ve yeni istek alınabilir.
    z.arsiv_surec.returncode = 1
    z.adim()
    with Session(engine) as s:
        istek = s.get(ArsivIstegi, istek_id)
        assert istek.durum == "HATALI" and "kaldığı yerden" in istek.hata
    assert z.arsiv_surec is None
    # Zamanlayıcı yeniden başlarsa çalışır görünen arşiv isteği yarım kalmış sayılır ve kendiliğinden devam ettirilir:
    # aynı seçeneklerle yeni istek, ilk işin eşiğiyle ("yeniden" seçiliyse baştan değil, kaldığı yerden).
    with Session(engine) as s:
        s.add(ArsivIstegi(istendi=datetime(2026, 11, 20, 9, 0), durum="CALISIYOR",
                          secenekler={"turler": ["Kanun"], "yeniden": True}))
        s.commit()
    zm.Zamanlayici(engine, saat=lambda: datetime(2026, 11, 20, 12, 5), gecikme=lambda: 0).kur()
    with Session(engine) as s:
        eski, yeni = s.scalars(select(ArsivIstegi).order_by(ArsivIstegi.id.desc()).limit(2)).all()[::-1]
        assert eski.durum == "HATALI" and "kendiliğinden devam" in eski.hata
        assert yeni.durum == "BEKLIYOR" and yeni.secenekler == {
            "turler": ["Kanun"], "yeniden": True, "esik": "2026-11-20T09:00:00", "devam_eden": eski.id}


def test_zamanlayici_haftalik_liste_isini_gece_siraya_koyar(engine):
    with Session(engine) as s:
        s.add(ArsivMevzuat(anahtar="1.5.1", tur="Kanun", ad="X", url="u", bicim="html", ilk_gorulme=datetime(2026, 11, 1),
                           son_gorulme=datetime(2026, 11, 1), durum="TAMAM", surum_sayisi=1))
        s.commit()
    simdi = [datetime(2026, 11, 20, 12, 0)]
    baslatilan = []
    z = zm.Zamanlayici(engine, baslat=lambda i: SahteSurec(), saat=lambda: simdi[0], gecikme=lambda: 9 * 3600,
                       arsiv_baslat=lambda i: baslatilan.append(i) or SahteSurec())
    z.kur()
    z.surec = None  # açılışta başlayan günlük iş bitmiş olsun
    z.adim()
    with Session(engine) as s:
        assert s.scalar(select(ArsivIstegi)) is None  # gündüz açılmaz
    simdi[0] = datetime(2026, 11, 21, 2, 0)
    z.adim()
    with Session(engine) as s:
        istek = s.scalar(select(ArsivIstegi))
        assert istek.secenekler.get("haftalik") and istek.isteyen_id is None
    z.adim()
    assert baslatilan == [istek.id]


def _post(c, yol, veri=None):
    return c.post(yol, json=veri or {}, headers={"X-CSRF-Token": c.get("/panel/api/oturum").json()["csrf"]})


def test_panel_arsiv_uclari(engine, site, tmp_path):
    c = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    assert c.get("/panel/api/arsiv/durum").status_code == 401
    _post(c, "/panel/api/giris", {"eposta": "onay@firma.com", "parola": PAROLA})
    # Zamanlayıcı çalışmıyorsa başlatılamaz.
    assert _post(c, "/panel/api/arsiv/baslat", {"turler": ["Kanun"]}).status_code == 409
    with Session(engine) as s:
        zm.nabiz_yaz(s, datetime.now() - timedelta(seconds=5), "bekliyor", datetime.now() + timedelta(hours=1), [time(6, 30)])
    r = _post(c, "/panel/api/arsiv/baslat", {"turler": ["Kanun"]})
    assert r.status_code == 200 and r.json()["istek"]["durum"] == "BEKLIYOR"
    assert r.json()["istek"]["secenekler"] == {"turler": ["Kanun"], "yeniden": False}
    assert _post(c, "/panel/api/arsiv/baslat", {}).status_code == 409
    assert _post(c, "/panel/api/arsiv/durdur").json()["istek"]["durum"] == "DURDURULDU"
    with Session(engine) as s:
        assert {d.islem for d in s.scalars(select(Denetim))} >= {"arsiv_istegi", "arsiv_durdur"}
    # Arşiv dolunca liste, künye ve metin okunur.
    _calistir(engine, site, tmp_path)
    d = c.get("/panel/api/arsiv/durum").json()
    assert d["toplam"] == 6 and d["surum_toplam"] == 5
    assert next(t for t in d["turler"] if t["kod"] == "Kanun")["sayilar"] == {"TAMAM": 2, "SITEDE_YOK": 1}
    liste = c.get("/panel/api/arsiv/liste", params={"ara": "birinci"}).json()
    assert liste["toplam"] == 1 and liste["satirlar"][0]["anahtar"] == "1.5.1"
    assert c.get("/panel/api/arsiv/liste", params={"durum": "OCR_GEREKLI"}).json()["satirlar"][0]["anahtar"] == "20.5.9"
    detay = c.get("/panel/api/arsiv/mevzuat/1.5.1").json()
    assert detay["surumler"][0]["surum_no"] == 1 and detay["surumler"][0]["ekler"][0]["ad"] == "1.5.1-Ek.docx"
    assert "Madde 1" in c.get("/panel/api/arsiv/mevzuat/1.5.1/metin").json()["metin"]
    assert c.get("/panel/api/arsiv/mevzuat/yok.1.1").status_code == 404
    assert c.get("/panel/api/arsiv/mevzuat/1.5.3/metin").status_code == 404

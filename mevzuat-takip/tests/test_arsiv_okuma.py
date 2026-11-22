"""Arşiv okuyucu 2 (2026-11-22): metin düzeni, HTML/PDF/ek okuma, çekim, sürüm türü, fark, teslim kontrolü, panel uçları.
Site sahte (httpx.MockTransport), internete çıkılmaz. OCR sahte fonksiyonla, gerçek Tesseract gerekmez."""

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mevzuat import (arsiv, arsiv_cekim, arsiv_fark, arsiv_kontrol, ek_okuma, html_okuma, metin_duzen as md, ocr,
                     pdf_okuma)
from mevzuat.db import ArsivDosyasi, ArsivMevzuat, ArsivSurumu, init_db
from mevzuat.http import make_client
from mevzuat.web import uygulama_olustur
from mevzuat.web.guvenlik import kullanici_ekle

from test_arsiv import METINLI_PDF, TARANMIS_PDF

PAROLA = "dogru-parola-123"
SITE = "https://www.mevzuat.gov.tr/"


# --- metin düzeni ----------------------------------------------------------------------------------------------

def test_kelime_onarimi_ve_satir_birlestirme():
    assert md.kelimeleri_onar("Bankam ı zca, vadesine en ç ok 3 ay kalan senetler kar şı l ığı nda yap ı lacak") == \
        "Bankamızca, vadesine en çok 3 ay kalan senetler karşılığında yapılacak"
    assert md.kelimeleri_onar("b i r i n c i k ı s ı m ve B İ R İ N C İ BÖLÜM") == "birinci kısım ve BİRİNCİ BÖLÜM"
    # Gerçek tek harfli kelimeler ve bent işaretleri bozulmaz.
    assert md.kelimeleri_onar("o kişi a) bendi ile b) bendi") == "o kişi a) bendi ile b) bendi"
    metin = ("MADDE 5 – (1) Fon kayıp ve zararının sorumlulardan\ntahsiline ilişkin usul ve\nesaslar şunlardır:\n"
             "a) Ar-\nGe merkezleri ka-\nyıtlı olduğu birime verir.\n(2) İkinci fıkra.\nGÜM01-6523 568923.716\nGÜM01-6478 569055.962"
             "\n\nİKİNCİ BÖLÜM\nSon Hükümler")
    assert md.satirlari_birlestir(metin) == (
        "MADDE 5 – (1) Fon kayıp ve zararının sorumlulardan tahsiline ilişkin usul ve esaslar şunlardır:\n"
        "a) Ar-Ge merkezleri kayıtlı olduğu birime verir.\n(2) İkinci fıkra.\nGÜM01-6523 568923.716\nGÜM01-6478 569055.962"
        "\n\nİKİNCİ BÖLÜM\nSon Hükümler")
    # Satır düzeni farkı karşılaştırmada fark sayılmaz; NFC ve kontrol karakterleri temizlenir.
    assert md.karsilastirma_metni("a  b\nc") == md.karsilastirma_metni("a b c")
    assert md.temizle("Yön\x13etmelik I\u0307") == "Yönetmelik İ"


def test_anlamsiz_ve_cop_orani():
    cop = "ağd Çiğli CA ADSLĞAI Saclar ş öy ylali ÖELEY Vü ÂS gideli yali Âzalli ari dB Şi GE Bİ AZ İşli ii yali Şana " * 3
    assert md.anlamsiz_mi(cop)
    assert not md.anlamsiz_mi("GÜM01-6523 568923.716 4505494.342 GÜM01-6478 569055.962 " * 10)  # sayı tablosu
    assert not md.anlamsiz_mi("Bu Yönetmeliğin amacı, Kurum merkez ve fabrikalarının satış işlemlerini düzenlemektir. " * 3)
    assert md.cop_orani("Bu Yönetmelik yayımı tarihinde yürürlüğe girer.")[0] < 0.1
    assert md.cop_orani(cop)[0] > 0.35


# --- HTML -------------------------------------------------------------------------------------------------------

WORD_HTML = """<html><head><style>p{}</style><script>alert(1)</script></head><body>
<h1 align=center><span>MAKİNA KURUMU YÖNETMELİĞİ</span></h1>
<p class=MsoNormal><b><span>Madde 1-</span></b><span> Bankam</span><span style='font-family:Times'>ı</span><span>zca,
vadesine en </span><span>ç</span><span>ok 3 ay.</span></p>
<p onclick="alert(2)">&lt;script&gt;kötü&lt;/script&gt; <a href="javascript:x()">bağlantı</a></p>
<table><tr><td colspan=2>Başlık</td></tr><tr><td>a</td><td>b <img src="9.5.1_dosyalar/image001.png"></td></tr></table>
<p><img src="9.5.1_dosyalar/image002.gif"></p>
<p><a href="9.5.1-Ek.docx">Ekleri için tıklayınız</a></p>
</body></html>"""


def test_html_yapisiyla_okunur_ve_gorunum_guvenli():
    h = html_okuma.html_oku(WORD_HTML)
    assert "Madde 1- Bankamızca, vadesine en çok 3 ay." in h.metin.split("\n")
    assert "Başlık" in h.metin and "a | b [Resim 1]" in h.metin and "[Resim 2]" in h.metin
    assert h.resimler == ["9.5.1_dosyalar/image001.png", "9.5.1_dosyalar/image002.gif"]
    assert ("9.5.1-Ek.docx", "Ekleri için tıklayınız") in h.baglantilar and h.tablo_sayisi == 1
    g = h.gorunum
    assert '<h2 class="orta">' in g and "<b>Madde 1-</b>" in g and '<td colspan="2">' in g
    assert 'data-resim="0"' in g and 'data-resim="1"' in g
    # Betik, olay öznitelikleri ve bağlantı adresleri görünüme taşınmaz, metin kaçışlanır.
    assert "<script" not in g and "onclick" not in g and "javascript:" not in g and "alert" not in g
    assert "&lt;script&gt;kötü" in g


def test_duz_metinden_gorunum():
    g = html_okuma.duz_metinden_gorunum("BİRİNCİ BÖLÜM\nMADDE 1 – (1) Amaç.\n(2) Fıkra.\na) Bent.\nDüz <b>yazı</b>")
    assert '<p class="baslik orta"><b>BİRİNCİ BÖLÜM</b></p>' in g and '<b>MADDE 1 –</b>' in g
    assert '<p class="fikra">(2) Fıkra.</p>' in g and '<p class="bent">a) Bent.</p>' in g and "&lt;b&gt;" in g


# --- ekler ------------------------------------------------------------------------------------------------------

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _zip(dosyalar: dict[str, bytes | str], utf8: bool = True) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        for ad, icerik in dosyalar.items():
            bilgi = zipfile.ZipInfo(ad)
            if not utf8:
                bilgi.filename = ad.encode("cp857").decode("cp437")
            z.writestr(bilgi, icerik)
    return tampon.getvalue()


def _docx() -> bytes:
    govde = f"""<w:document {W}><w:body>
<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>EK-1 LİSTE</w:t></w:r></w:p>
<w:tbl>
 <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Başlık</w:t></w:r></w:p></w:tc></w:tr>
 <w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Birleşik</w:t></w:r></w:p></w:tc>
       <w:tc><w:p><w:r><w:t>1,5</w:t></w:r></w:p></w:tc></w:tr>
 <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p><w:r><w:t>2,5</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl></w:body></w:document>"""
    return _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": govde})


def _xlsx() -> bytes:
    S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    return _zip({
        "xl/workbook.xml": f'<workbook {S} {R}><sheets><sheet name="Tarife" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst {S}><si><t>Gümrük</t></si><si><t>Oran</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                                    '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>12.5</v></c></row></sheetData>'
                                    '<mergeCells><mergeCell ref="A1:B1"/></mergeCells></worksheet>',
    })


def test_ek_bicimi_icerikten_anlasilir():
    assert ek_okuma.bicim_bul(b"{\\rtf1 merhaba", "ek.doc") == "rtf"  # ".doc" görünen RTF
    assert ek_okuma.bicim_bul(_docx(), "ek.doc") == "docx"  # ".doc" görünen docx
    assert ek_okuma.bicim_bul(_xlsx(), "ek.xls") == "xlsx"
    assert ek_okuma.bicim_bul(b"\xd0\xcf\x11\xe0eski", "Ek.DOC") == "doc"
    assert ek_okuma.bicim_bul(b"%PDF-1.4", "x") == "pdf" and ek_okuma.bicim_bul(b"Rar!\x1a\x07", "x") == "rar"
    assert ek_okuma.bicim_bul(b"<html><body>bulunamad\xc4\xb1</body></html>", "ek.docx") == "html"


def test_docx_ve_xlsx_tablolariyla_okunur(tmp_path):
    d = ek_okuma.ek_oku(_docx(), "ek.docx", tmp_path, "g", "a" * 64)
    assert d.durum == "tamam" and d.metin.split("\n") == ["EK-1 LİSTE", "Başlık", "Birleşik | 1,5", "2,5"]
    assert '<td colspan="2">Başlık</td>' in d.gorunum and '<td rowspan="2">Birleşik</td>' in d.gorunum
    assert '<p class="orta"><b>EK-1 LİSTE</b></p>' in d.gorunum
    x = ek_okuma.ek_oku(_xlsx(), "ek.xlsx", tmp_path, "g", "b" * 64)
    assert x.metin.split("\n") == ["[Sayfa: Tarife]", "Gümrük", "Oran | 12.5"] and '<td colspan="2">Gümrük</td>' in x.gorunum


def test_zip_acilir_turkce_adlar_ve_cevirici_yoksa_gorunum_yok(tmp_path, monkeypatch):
    arsiv_dosyasi = _zip({"Ek-1 özet cetveli.docx": _docx(), "klasör/eski.doc": b"\xd0\xcf\x11\xe0eski"}, utf8=False)
    z = ek_okuma.ek_oku(arsiv_dosyasi, "ekler.zip", tmp_path, "g", "c" * 64)
    assert z.bicim == "zip" and [a for a, _ in z.ic_dosyalar] == ["Ek-1 özet cetveli.docx", "klasör/eski.doc"]
    monkeypatch.setattr(ek_okuma, "soffice_yolu", lambda: None)
    eski = ek_okuma.ek_oku(b"\xd0\xcf\x11\xe0eski", "eski.doc", tmp_path, "g", "d" * 64)
    assert eski.durum == "gorunum_yok" and "LibreOffice" in eski.notlar[0]


# --- PDF ---------------------------------------------------------------------------------------------------------

def test_pdf_yazi_katmani_ve_ocr_yoksa_okunamadi(tmp_path, monkeypatch):
    (tmp_path / "m.pdf").write_bytes(METINLI_PDF)
    p = pdf_okuma.pdf_oku(tmp_path / "m.pdf", tmp_path, "g/m", ocr_acik=False)
    assert p.okuma == "pdf_metin" and "Madde 1 - Bu Karar" in p.metin and p.sayfalar[0]["yontem"] == "yazi"
    assert '<section class="sayfa" data-sayfa="1">' in p.gorunum
    (tmp_path / "t.pdf").write_bytes(TARANMIS_PDF)
    t = pdf_okuma.pdf_oku(tmp_path / "t.pdf", tmp_path, "g/t", ocr_acik=False)
    assert t.okuma == "okunamadi" and t.kusurlar and all(s["yontem"] == "okunamadi" for s in t.sayfalar)
    # Başlığı yazı, gövdesi resim olan RG sayfası OCR'a gidecek sayfa sayılır (eşikte kalmaz).
    assert "sayfanın çoğu resim" in t.sayfalar[0]["uyari"]


def test_pdf_ocr_gorsel_sayfa_ve_onbellekten_devam(tmp_path, monkeypatch):
    (tmp_path / "t.pdf").write_bytes(TARANMIS_PDF)
    okunan = []
    yazi = "MADDE 1 – Bu Karar yayımı tarihinde yürürlüğe girer.\nMADDE 2 – Bu Kararı Cumhurbaşkanı yürütür."
    harita = "ağd Çiğli CA ADSLĞAI Saclar ş öy ylali ÖELEY Vü ÂS gideli yali Âzalli ari dB Şi GE Bİ AZ İşli " * 4

    def sahte_ocr(png, *a, **k):
        okunan.append(png.name)
        return yazi if png.name.startswith("0") else harita

    monkeypatch.setattr(ocr, "goruntu_oku", sahte_ocr)
    onbellek = tmp_path / "onbellek.json"
    p = pdf_okuma.pdf_oku(tmp_path / "t.pdf", tmp_path, "goruntu/t", onbellek, ocr_acik=True, paralel=2)
    assert [s["yontem"] for s in p.sayfalar] == ["ocr", "gorsel"] and p.okuma == "ocr"
    assert "MADDE 1 – Bu Karar" in p.metin and "s. 2: görüntü olarak saklandı" in p.metin
    assert harita.strip()[:20] in p.arama_metni  # anlamsız OCR sadece aramada
    assert (tmp_path / "goruntu/t/s2.png").exists() and 'data-yol="goruntu/t/s2.png"' in p.gorunum
    # İkinci okumada OCR yeniden yapılmaz (iş yarıda kalırsa kaldığı sayfadan devam).
    okunan.clear()
    pdf_okuma.pdf_oku(tmp_path / "t.pdf", tmp_path, "goruntu/t", onbellek, ocr_acik=True)
    assert okunan == []


# --- çekim ------------------------------------------------------------------------------------------------------

def test_dosya_adaylari_ve_ek_baglantilari():
    a, bozuk = arsiv_cekim.dosya_adaylari("http://rega.basbakanlik.gov.tr/Eskiler/2008/09/20080927-12-1.doc")
    assert a[0] == "https://www.resmigazete.gov.tr/Eskiler/2008/09/20080927-12-1.doc" and not bozuk
    assert "https://www.resmigazete.gov.tr/eskiler/2008/09/20080927-12-1.doc" in a
    a, _ = arsiv_cekim.dosya_adaylari("http://mevzuat.basbakanlik.gov.tr/mevzuat/cache/yonetmelik/9.5.10947-Ek-4.doc")
    assert a[0] == SITE + "MevzuatMetin/yonetmelik/9.5.10947-Ek-4.doc"
    a, bozuk = arsiv_cekim.dosya_adaylari("file:///F:\\2009-5\\2009-5-ekler.doc")
    assert bozuk and a == [SITE + "MevzuatMetin/yonetmelik/2009-5-ekler.doc", SITE + "MevzuatMetin/2009-5-ekler.doc"]
    _, bozuk = arsiv_cekim.dosya_adaylari("../../ekrem.duman/Desktop/9.5.12086-ek.doc")
    assert bozuk
    a, _ = arsiv_cekim.dosya_adaylari("9.5.13040 ek.doc")
    assert SITE + "MevzuatMetin/yonetmelik/9.5.13040%20ek.doc" in a
    ekler, supheli = arsiv_cekim.ek_baglantilari([
        ("9.5.1-EK.zip", "Ekler"), ("mevzuat?x=1", "bağlantı"), ("9.5.2-ek", "Ekleri için tıklayınız"),
        ("https://www.resmigazete.gov.tr/eskiler/2022/06/20220628-16-1.pdf", "Ek"), ("#ust", "başa dön"),
        ("baska.htm", "Metni görmek için tıklayınız")])
    assert ekler == ["9.5.1-EK.zip", "9.5.2-ek", "https://www.resmigazete.gov.tr/eskiler/2022/06/20220628-16-1.pdf"]
    assert supheli == ["baska.htm"] or supheli == []


class Site:
    def __init__(self):
        self.yanitlar: dict[str, httpx.Response | callable] = {}
        self.istekler: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.istekler.append(url)
        for onek, cevap in self.yanitlar.items():
            if url.startswith(onek):
                return cevap(request) if callable(cevap) else cevap
        return httpx.Response(302, headers={"location": "/Anasayfa/ErrorPage?code=404"})


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(e)
    with Session(e) as s:
        kullanici_ekle(s, "onay@firma.com", "Ayşe Onay", "onaylayici", PAROLA)
    return e


def _mevzuat(s: Session, anahtar: str, bicim: str = "html", url: str | None = None) -> ArsivMevzuat:
    tur, tertip, no = anahtar.split(".") if anahtar[0].isdigit() else ("20", "5", "1")
    m = ArsivMevzuat(anahtar=anahtar, tur="Teblig", ad=f"MEVZUAT {anahtar}", url=url or f"{SITE}mevzuat?MevzuatNo={no}&MevzuatTur={tur}&MevzuatTertip={tertip}",
                     bicim=bicim, mulga=False, ilk_gorulme=datetime(2026, 11, 22), son_gorulme=datetime(2026, 11, 22),
                     kayip_sayisi=0, durum="BEKLIYOR", deneme=0, surum_sayisi=0)
    s.add(m)
    s.flush()
    return m


def _cek(s, site, m, kok, monkeypatch=None):
    ist = arsiv.Istemci(make_client(transport=httpx.MockTransport(site), uyu=lambda x: None), uyu=lambda x: None)
    return arsiv_cekim.mevzuati_cek(s, ist, m, kok, datetime(2026, 11, 22, 12), None, arsiv.metin_adresi)


def _iframe(anahtar):
    t, te, no = anahtar.split(".")
    return f"{SITE}anasayfa/MevzuatFihristDetayIframe?MevzuatTur={t}&MevzuatNo={no}&MevzuatTertip={te}"


def test_metni_resim_olan_sayfa_ekler_ve_site_pdf(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_kullanilabilir", lambda: True)
    monkeypatch.setattr(ocr, "goruntu_oku", lambda *a, **k: "2014 YILI BÜTÇE UYGULAMA TEBLİĞİ\nMemurlardan yemek bedeli alınır.")
    site = Site()
    site.yanitlar = {
        _iframe("9.5.19302"): httpx.Response(200, text='<html><body><p><img src="9.5.19302_dosyalar/image001.png"></p>'
                                                         '<p><a href="9.5.19302-Ek.docx">Ek</a></p></body></html>'),
        SITE + "MevzuatMetin/yonetmelik/9.5.19302_dosyalar/image001.png": httpx.Response(200, content=b"\x89PNG\r\n\x1a\nresim", headers={"content-type": "image/png"}),
        SITE + "MevzuatMetin/yonetmelik/9.5.19302-Ek.docx": httpx.Response(200, content=_docx(), headers={"content-type": "application/octet-stream"}),
        SITE + "mevzuat?MevzuatNo=19302": httpx.Response(200, text='<a href="https://www.mevzuat.gov.tr/File/GeneratePdf?mevzuatNo=19302&amp;mevzuatTur=Teblig&amp;mevzuatTertip=5">pdf</a>'),
        SITE + "File/GeneratePdf": httpx.Response(200, content=METINLI_PDF, headers={"content-type": "application/pdf"}),
    }
    with Session(engine) as s:
        m = _mevzuat(s, "9.5.19302")
        assert _cek(s, site, m, tmp_path) == "yeni"
        v = s.scalar(select(ArsivSurumu))
        assert v.okuma == "html+resim" and "[Resim 1]\n2014 YILI BÜTÇE UYGULAMA TEBLİĞİ" in v.metin
        assert v.resimler[0]["durum"] == "indi" and f'data-sha="{v.resimler[0]["sha256"]}"' in v.gorunum
        assert v.ekler[0]["durum"] == "indi" and v.ekler[0]["bicim"] == "docx"
        assert s.get(ArsivDosyasi, v.ekler[0]["sha256"]).metin.startswith("EK-1 LİSTE")
        assert v.site_pdf_turu == "uretilen" and (tmp_path / v.site_pdf_yolu).read_bytes()[:5] == b"%PDF-"
        assert m.durum == "TAMAM" and m.kalite == "temiz" and v.surum_turu == "ilk"
        # Aynı kaynak yeniden çekilince sürüm olmaz ve sitenin ürettiği PDF yeniden istenmez.
        site.istekler.clear()
        assert _cek(s, site, m, tmp_path) == "ayni"
        assert not any("GeneratePdf" in u for u in site.istekler)


def test_tiklayiniz_eki_ana_metin_olur_ve_bos_sayfa_asil_pdfden_okunur(engine, tmp_path):
    site = Site()
    site.yanitlar = {
        _iframe("7.5.10391"): httpx.Response(200, text='<p>ELEKTRİK YÖNETMELİĞİ</p><p>Metnini görmek için <a href="7.5.10391%20metin.docx">tıklayınız</a>.</p>'),
        SITE + "MevzuatMetin/yonetmelik/7.5.10391%20metin.docx": httpx.Response(200, content=_docx()),
        _iframe("1.3.6301"): httpx.Response(200, text="<html><body></body></html>"),
        SITE + "mevzuat?MevzuatNo=6301": httpx.Response(200, text='<a href="/MevzuatMetin/1.3.6301.pdf">PDF</a>'),
        SITE + "MevzuatMetin/1.3.6301.pdf": httpx.Response(200, content=METINLI_PDF, headers={"content-type": "application/pdf"}),
    }
    with Session(engine) as s:
        m = _mevzuat(s, "7.5.10391")
        _cek(s, site, m, tmp_path)
        v = s.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == "7.5.10391"))
        assert v.okuma == "html+ek" and "[Ek: 7.5.10391 metin.docx]\nEK-1 LİSTE" in v.metin
        k = _mevzuat(s, "1.3.6301")
        _cek(s, site, k, tmp_path)
        v = s.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == "1.3.6301"))
        assert v.okuma == "pdf_metin" and "Madde 1" in v.metin and v.site_pdf_turu == "asil" and v.pdf_yolu


def test_sitede_olmayan_ve_olu_alan_adi(engine, tmp_path):
    site = Site()
    olu_istek = []

    def olu(request):
        olu_istek.append(str(request.url))
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)

    site.yanitlar = {
        _iframe("9.5.56"): httpx.Response(200, text='<p><img src="9.5.56_dosyalar/image001.jpg"></p>'
                                                    '<a href="http://eski.kapali.gov.tr/a.doc">ek</a><a href="http://eski.kapali.gov.tr/b.doc">ek</a>'),
        "http://eski.kapali.gov.tr": olu, "https://eski.kapali.gov.tr": olu,
        SITE + "mevzuat?MevzuatNo=56": httpx.Response(200, text="<p>sayfa</p>"),
        SITE + "MevzuatMetin/20.5.6731.pdf": httpx.Response(302, headers={"location": "/Anasayfa/ErrorPage?code=404"}),
    }
    with Session(engine) as s:
        m = _mevzuat(s, "9.5.56")
        assert _cek(s, site, m, tmp_path) == "sitede_yok"
        assert m.durum == "SITEDE_YOK" and "resimden ibaret" in m.son_hata
        # Çözülemeyen alan adı iş boyunca bir kez denenir (ikinci ekte istek atılmaz).
        assert len({u.split("/")[2] for u in olu_istek}) == 1 and all("a.doc" in u for u in olu_istek)
        p = _mevzuat(s, "20.5.6731", bicim="pdf", url=SITE + "MevzuatMetin/20.5.6731.pdf")
        assert _cek(s, site, p, tmp_path) == "sitede_yok" and "PDF sitede yok" in p.son_hata


def test_surum_turu_okuma_duzeltmesi_ve_degisiklik(engine, tmp_path):
    with Session(engine) as s:
        m = _mevzuat(s, "9.5.1")
        g = arsiv_cekim.Gunluk(None)

        def oku(metin, kaynak):
            return arsiv_cekim.Okuma(metin=metin, gorunum="<p>x</p>", kaynak_parcalari=[kaynak])
        simdi = datetime(2026, 11, 22)
        assert arsiv_cekim.surum_kaydet(s, None, m, oku("Madde 1 – eski okuma", "k1"), tmp_path, simdi, g) == "yeni"
        # Aynı metin, farklı satır düzeni: sürüm olmaz.
        assert arsiv_cekim.surum_kaydet(s, None, m, oku("Madde 1 –\neski okuma", "k1"), tmp_path, simdi, g) == "ayni"
        # Kaynak aynı, okuma değişti: okuma düzeltmesi.
        assert arsiv_cekim.surum_kaydet(s, None, m, oku("Madde 1 – düzgün okuma", "k1"), tmp_path, simdi, g) == "okuma_duzeltmesi"
        # Kaynak değişti: gerçek değişiklik, RG notu okunur.
        yeni = "Madde 1 – (Değişik: RG-15/12/2026-33461) yeni hüküm"
        assert arsiv_cekim.surum_kaydet(s, None, m, oku(yeni, "k2"), tmp_path, simdi, g) == "yeni"
        turler = [v.surum_turu for v in s.scalars(select(ArsivSurumu).order_by(ArsivSurumu.surum_no))]
        assert turler == ["ilk", "okuma_duzeltmesi", "degisiklik"] and m.surum_sayisi == 3
        son = s.scalar(select(ArsivSurumu).where(ArsivSurumu.surum_no == 3))
        assert son.rg_notlari == [{"bolum": "Madde 1", "tur": "Değişik", "tarih": "15/12/2026", "sayi": "33461"}]
        d = arsiv.detay(s, "9.5.1")
        assert d["surumler"][0]["rg_yeni"] == [{"tarih": "15/12/2026", "sayi": "33461"}]


def test_istemci_site_zorlaninca_yavaslar():
    kodlar = iter([503] * 2 + [200] * 40)
    bekleme = []
    ist = arsiv.Istemci(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(next(kodlar)))),
                        uyu=bekleme.append, saat=lambda: 0.0)
    ist.get("https://x/1")
    ist.get("https://x/2")
    assert ist.ek_bekleme == 6.0  # 3 → 6
    for i in range(40):
        ist.get(f"https://x/{i}")
    assert ist.ek_bekleme < 3  # düzelince geri iner
    assert max(bekleme) == pytest.approx(arsiv.ISTEK_ARASI_BEKLEME + 6.0)


# --- fark, kontrol, katlama ---------------------------------------------------------------------------------------

def test_madde_madde_fark():
    eski = "GİRİŞ\nMadde 1 – Bu Yönetmelik eski hükümdür.\nMadde 2 – Kaldırılacak madde.\nMadde 3 – Mülga olacak madde metni."
    yeni = ("GİRİŞ\nMadde 1 – Bu Yönetmelik yeni hükümdür.\nMadde 3 – (Mülga: RG-1/1/2027-1)\n"
            "Madde 4 – Eklenen madde.")
    f = arsiv_fark.fark(eski, yeni)
    turler = {b["bolum"]: b["tur"] for b in f["bolumler"]}
    assert turler == {"Başlangıç": "ayni", "Madde 1": "degisti", "Madde 2": "kaldirildi", "Madde 3": "mulga", "Madde 4": "eklendi"}
    assert [b["bolum"] for b in f["bolumler"]] == ["Başlangıç", "Madde 1", "Madde 2", "Madde 3", "Madde 4"]
    m1 = next(b for b in f["bolumler"] if b["bolum"] == "Madde 1")["html"]
    assert "<del>eski</del> <ins>yeni</ins>" in m1
    assert f["sayac"] == {"degisti": 1, "eklendi": 1, "kaldirildi": 1, "mulga": 1}


def test_teslim_kontrolu_ve_katlama(engine, tmp_path):
    with Session(engine) as s:
        m = _mevzuat(s, "9.5.1")
        g = arsiv_cekim.Gunluk(None)
        o = arsiv_cekim.Okuma(metin="Madde 1 – temiz metin", gorunum="<p>x</p>", kaynak_parcalari=["k"])
        arsiv_cekim.surum_kaydet(s, None, m, o, tmp_path, datetime(2026, 11, 22), g)
        m.durum = "TAMAM"
        o2 = arsiv_cekim.Okuma(metin="Madde 1 – (cid:5)(cid:7) bozuk", gorunum="", kaynak_parcalari=["k2"])
        arsiv_cekim.surum_kaydet(s, None, m, o2, tmp_path, datetime(2026, 11, 23), g)
        s.commit()
        r = arsiv_kontrol.kontrol(s, tmp_path)
        assert not r["hatasiz"] and r["zorunlu"]["cid_kalintisi"] == ["9.5.1"]
        assert r["sayilar"]["birden_cok_surumlu"] == 1 and "# Arşiv teslim kontrolü" in arsiv_kontrol.rapor_metni(r)
        # Katlama: son sürüm v1 olur, öncekiler silinir.
        assert arsiv_kontrol.katla(s) == (1, 1)
        surumler = s.scalars(select(ArsivSurumu)).all()
        assert [(v.surum_no, v.surum_turu) for v in surumler] == [(1, "ilk")] and "bozuk" in surumler[0].metin
        assert s.get(ArsivMevzuat, "9.5.1").surum_sayisi == 1


# --- panel uçları ----------------------------------------------------------------------------------------------

def _post(c, yol, veri=None):
    return c.post(yol, json=veri or {}, headers={"X-CSRF-Token": c.get("/panel/api/oturum").json()["csrf"]})


def test_panel_gorunum_fark_dosya_ve_guvenlik(engine, tmp_path, monkeypatch):
    kok = tmp_path / "arsiv"
    monkeypatch.setenv("MEVZUAT_ARSIV_KLASORU", str(kok))
    ek = _docx()
    with Session(engine) as s:
        m = _mevzuat(s, "9.5.7")
        yol = arsiv_cekim.dosya_yaz(kok, "ekler", "9.5.7", "Ek 1.docx", ek)
        d = arsiv_cekim.dosya_isle(s, kok, ek, "Ek 1.docx", yol, "9.5.7", datetime(2026, 11, 22))
        (kok / "goruntu" / "abc").mkdir(parents=True)
        (kok / "goruntu" / "abc" / "s1.png").write_bytes(b"\x89PNG resim")
        g = arsiv_cekim.Gunluk(None)
        o = arsiv_cekim.Okuma(metin="Madde 1 – eski", kaynak_parcalari=["k"],
                              gorunum='<p>Madde 1</p><figure><img data-yol="goruntu/abc/s1.png"></figure>',
                              ekler=[{"ad": "Ek 1.docx", "sha256": d.sha256, "boyut": len(ek), "yol": yol, "durum": "indi"}])
        arsiv_cekim.surum_kaydet(s, None, m, o, kok, datetime(2026, 11, 22), g)
        o2 = arsiv_cekim.Okuma(metin="Madde 1 – yeni", gorunum="<p>x</p>", kaynak_parcalari=["k2"])
        arsiv_cekim.surum_kaydet(s, None, m, o2, kok, datetime(2026, 11, 23), g)
        m.durum = "TAMAM"
        s.commit()
        ozet = d.sha256
    c = TestClient(uygulama_olustur(engine, None, None, "t" * 40, ek_ekle=False, arayuz=None))
    assert c.get(f"/panel/api/arsiv/dosya/{ozet}").status_code == 401
    _post(c, "/panel/api/giris", {"eposta": "onay@firma.com", "parola": PAROLA})
    gor = c.get("/panel/api/arsiv/mevzuat/9.5.7/gorunum", params={"surum": 1}).json()
    assert 'src="/panel/api/arsiv/goruntu?yol=goruntu/abc/s1.png"' in gor["gorunum"]
    assert c.get("/panel/api/arsiv/goruntu", params={"yol": "goruntu/abc/s1.png"}).content == b"\x89PNG resim"
    # Arşiv dışına çıkan ya da biçimi uymayan yol verilmez.
    for kotu in ("../mevzuat.db", "goruntu/../../x.png", "ekler/9.5.7/x.docx", "goruntu/abc/../../s.png"):
        assert c.get("/panel/api/arsiv/goruntu", params={"yol": kotu}).status_code == 404
    # İndirme orijinal dosyayı orijinal adıyla verir.
    r = c.get(f"/panel/api/arsiv/dosya/{ozet}", params={"indir": 1})
    assert r.status_code == 200 and r.content == ek and "attachment" in r.headers["content-disposition"]
    assert "Ek%201.docx" in r.headers["content-disposition"] or "Ek 1.docx" in r.headers["content-disposition"]
    assert c.get("/panel/api/arsiv/dosya/" + "0" * 64).status_code == 404
    assert c.get("/panel/api/arsiv/dosya/..%2F..%2Fmevzuat.db").status_code == 404
    ek_g = c.get(f"/panel/api/arsiv/dosya/{ozet}/gorunum").json()
    assert ek_g["bicim"] == "docx" and "<table>" in ek_g["gorunum"]
    f = c.get("/panel/api/arsiv/mevzuat/9.5.7/fark", params={"eski": 1, "yeni": 2}).json()
    assert f["sayac"]["degisti"] == 1 and "<ins>yeni</ins>" in f["bolumler"][0]["html"]
    detay = c.get("/panel/api/arsiv/mevzuat/9.5.7").json()
    assert detay["surumler"][1]["ekler"][0]["gorunum_var"] and detay["surumler"][0]["surum_turu"] == "degisiklik"
    assert c.get("/panel/api/arsiv/mevzuat/9.5.7/pdf").status_code == 404


def test_mulga_listesi_eksik_gelirse_farkli_sayfa_boyuyla_tamamlanir():
    hepsi = [{"mevAdi": f"MÜLGA {i}", "url": f"mevzuat?MevzuatNo={i}&MevzuatTur=5&MevzuatTertip=3"} for i in range(3)]

    def site(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<input name="antiforgerytoken" type="hidden" value="a" />')
        govde = json.loads(request.content)
        boy = govde["length"]
        # 100'lük sayfada site bir kaydı atlıyor (sayaç 3 diyor), 50'likte tamam veriyor.
        veri = hepsi[:2] if boy == 100 else hepsi[govde["start"]:govde["start"] + boy]
        return httpx.Response(200, json={"recordsTotal": 3, "data": veri})

    ist = arsiv.Istemci(make_client(transport=httpx.MockTransport(site), uyu=lambda x: None), uyu=lambda x: None)
    satirlar, toplam, eksik = arsiv.tur_listesi(ist, arsiv.MULGA)
    assert toplam == 3 and eksik is None and sorted(d["mevAdi"] for d in satirlar) == ["MÜLGA 0", "MÜLGA 1", "MÜLGA 2"]

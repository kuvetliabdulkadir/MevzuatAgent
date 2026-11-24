"""Ek dosyalarını okur: biçimi bulur, metni ve panelde gösterilecek görünümü (tablolarıyla HTML) çıkarır.

Orijinal dosyaya hiç dokunulmaz, "İndir" her zaman onu verir (karar 26). Burada üretilen metin arama ve fark için,
görünüm sadece panelde göstermek içindir.

Biçim uzantıdan değil dosyanın ilk baytlarından anlaşılır (".doc" görünen dosyaların bir kısmı RTF ya da docx çıktı).
  docx, xlsx, pptx, odt, ods   zip içindeki XML'den doğrudan okunur (paragraflar, tablolar, birleşik hücreler).
  doc, xls, ppt, rtf           LibreOffice ile docx/xlsx/pdf'e çevrilip okunur (MEVZUAT_SOFFICE ya da PATH'te soffice).
  pdf                          pdf_okuma ile (sayfa sayfa, OCR, görsel, tablo).
  zip, rar, 7z                 açılır, içindeki her dosya ayrı ek gibi okunur (iç içe en fazla 2 kat, boyut ve adet sınırı).
  resim                        OCR, anlamsızsa sadece görüntü.
LibreOffice ya da arşiv açıcı yoksa ek yine saklanır, durumu "görünüm yok" olur.
"""

import html as html_lib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from mevzuat import html_okuma, metin_duzen as md, pdf_okuma

log = logging.getLogger(__name__)

OKUYUCU = 2
CEVIRI_ZAMAN_ASIMI = 300  # sn, LibreOffice tek dosya
ARSIV_EN_FAZLA_DOSYA = 300
ARSIV_EN_FAZLA_TOPLAM = 1024 * 1024 * 1024  # açılmış toplam boyut
ARSIV_EN_FAZLA_DOSYA_BOYUTU = 300 * 1024 * 1024
ARSIV_EN_FAZLA_DERINLIK = 2
EXCEL_EN_FAZLA_HUCRE = 200_000  # dev tablolar görünümde kesilir (metin tam kalır)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


@dataclass
class EkOkuma:
    bicim: str
    durum: str  # tamam / gorunum_yok / hata
    metin: str = ""
    gorunum: str = ""
    notlar: list[str] = field(default_factory=list)
    ic_dosyalar: list[tuple[str, bytes]] = field(default_factory=list)  # arşivden çıkan (ad, içerik)
    sayfalar: list[dict] = field(default_factory=list)


# --- biçim ---------------------------------------------------------------------------------------------------

# Dosyanın ilk baytlarından tanınan biçimler (imza, biçim). OLE ve ZIP ayrıca incelenir (aynı imzayı birçok biçim kullanır).
_IMZALAR: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", "pdf"), (b"{\\rtf", "rtf"), (b"Rar!", "rar"), (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"\x89PNG", "resim"), (b"GIF8", "resim"), (b"\xff\xd8\xff", "resim"), (b"BM", "resim"),
    (b"II*\x00", "resim"), (b"MM\x00*", "resim"),
)
_OLE = b"\xd0\xcf\x11\xe0"
_ZIP = (b"PK\x03\x04", b"PK\x05\x06")
_OLE_UZANTI = {"doc": "doc", "dot": "doc", "xls": "xls", "xlt": "xls", "ppt": "ppt", "pps": "ppt"}
# ZIP içindeki bu dosya varsa biçim bu.
_ZIP_ICERIK = (("word/document.xml", "docx"), ("xl/workbook.xml", "xlsx"), ("ppt/presentation.xml", "pptx"))
_ODF = ((b"opendocument.text", "odt"), (b"opendocument.spreadsheet", "ods"), (b"opendocument.presentation", "odp"))


def _ole_bicimi(veri: bytes, uzanti: str) -> str:
    """Eski Office (OLE): hangisi olduğu uzantıdan, uzantı uymuyorsa içerikteki akış adından."""
    if uzanti in _OLE_UZANTI:
        return _OLE_UZANTI[uzanti]
    if "WordDocument".encode("utf-16-le") in veri[:200_000]:
        return "doc"
    if "Workbook".encode("utf-16-le") in veri[:200_000]:
        return "xls"
    return "ole"


def _zip_bicimi(veri: bytes) -> str:
    """Office Open XML ve OpenDocument da ZIP'tir; içindeki dosyalara bakılır."""
    try:
        with zipfile.ZipFile(io.BytesIO(veri)) as z:
            adlar = set(z.namelist())
            for dosya, bicim in _ZIP_ICERIK:
                if dosya in adlar:
                    return bicim
            if "mimetype" in adlar:
                tur = z.read("mimetype")[:100]
                for isaret, bicim in _ODF:
                    if isaret in tur:
                        return bicim
    except zipfile.BadZipFile:
        return "bozuk"
    return "zip"


def bicim_bul(veri: bytes, ad: str = "") -> str:
    uzanti = Path(ad.lower()).suffix.lstrip(".")
    for imza, bicim in _IMZALAR:
        if veri.startswith(imza):
            return bicim
    if veri.startswith(_OLE):
        return _ole_bicimi(veri, uzanti)
    if veri.startswith(_ZIP):
        return _zip_bicimi(veri)
    bas = veri[:600].lstrip(b"\xef\xbb\xbf\xff\xfe\xfe\xff").lower().replace(b"\x00", b"")
    if bas.lstrip().startswith((b"<!doctype", b"<html", b"<?xml", b"<head")) or b"<html" in bas:
        return "html"
    return "txt" if uzanti in ("txt", "csv") else "bilinmiyor"


# --- docx ----------------------------------------------------------------------------------------------------

def _w_metin(eleman) -> str:
    parcalar = []
    for d in eleman.iter():
        if d.tag == W + "t":
            parcalar.append(d.text or "")
        elif d.tag == W + "tab":
            parcalar.append(" ")
        elif d.tag in (W + "br", W + "cr"):
            parcalar.append("\n")
    return "".join(parcalar)


def _w_paragraf_html(p) -> tuple[str, str]:
    """Paragrafın (düz metin, html). Kalın/eğik/altı çizili koşular işaretlenir."""
    duz, html = [], []
    for kosu in p.iter(W + "r"):
        rpr = kosu.find(W + "rPr")
        bicimler = []
        if rpr is not None:
            for ad, et in (("b", "b"), ("i", "i"), ("u", "u")):
                e = rpr.find(W + ad)
                if e is not None and e.get(W + "val") not in ("0", "false", "none"):
                    bicimler.append(et)
        yazi = _w_metin(kosu)
        if not yazi:
            continue
        duz.append(yazi)
        ic = html_lib.escape(yazi, quote=False).replace("\n", "<br>")
        for b in bicimler:
            ic = f"<{b}>{ic}</{b}>"
        html.append(ic)
    duz_metin = "".join(duz).strip()
    hiza = p.find(f"{W}pPr/{W}jc")
    sinif = ' class="orta"' if hiza is not None and hiza.get(W + "val") == "center" else ""
    return duz_metin, f"<p{sinif}>{''.join(html).strip()}</p>" if duz_metin else ""


@dataclass
class _Hucre:
    duz: str
    html: str
    yay: int = 1  # yatay birleşme (gridSpan → colspan)
    dikey: str | None = None  # dikey birleşme: "restart" başlatır, "continue" üsttekinin devamı (vMerge)
    satir: int = 1  # hesaplanan rowspan


def _w_hucre(tc) -> _Hucre:
    tcpr = tc.find(W + "tcPr")
    yay, dikey = 1, None
    if tcpr is not None:
        gs = tcpr.find(W + "gridSpan")
        if gs is not None and (gs.get(W + "val") or "").isdigit():
            yay = int(gs.get(W + "val"))
        vm = tcpr.find(W + "vMerge")
        if vm is not None:
            dikey = vm.get(W + "val") or "continue"
    duz, html = [], []
    for p in tc.findall(W + "p"):
        d, h = _w_paragraf_html(p)
        if d:
            duz.append(d)
            html.append(re.sub(r"^<p[^>]*>|</p>$", "", h))
    for alt in tc.findall(W + "tbl"):  # iç içe tablo hücre metnine katılır
        m, _ = _w_tablo(alt)
        duz.extend(m)
        html.extend(html_lib.escape(x, quote=False) for x in m)
    return _Hucre(" ".join(duz), "<br>".join(html), yay, dikey)


def _dikey_birlestir(satirlar: list[list[_Hucre]]) -> None:
    """"restart" hücresi, aynı sütunda altında kesintisiz gelen "continue" hücreleri kadar rowspan alır."""
    konumlar = []
    for s in satirlar:
        bas, liste = 0, []
        for h in s:
            liste.append(bas)
            bas += h.yay
        konumlar.append(liste)
    for i, s in enumerate(satirlar):
        for j, h in enumerate(s):
            if h.dikey != "restart":
                continue
            sutun = konumlar[i][j]
            for alt in range(i + 1, len(satirlar)):
                ayni = [x for x, k in zip(satirlar[alt], konumlar[alt], strict=True) if k == sutun]
                if not ayni or ayni[0].dikey != "continue":
                    break
                h.satir += 1


def _tablo_ciktisi(satirlar: list[list[_Hucre]]) -> tuple[list[str], str]:
    metin, govde = [], []
    for s in satirlar:
        gorunen = [h for h in s if h.dikey != "continue"]
        if satir := " | ".join(h.duz for h in gorunen if h.duz):
            metin.append(satir)
        hucreler = []
        for h in gorunen:
            oz = (f' colspan="{h.yay}"' if h.yay > 1 else "") + (f' rowspan="{h.satir}"' if h.satir > 1 else "")
            hucreler.append(f"<td{oz}>{h.html}</td>")
        govde.append("<tr>" + "".join(hucreler) + "</tr>")
    return metin, '<div class="tablo"><table>' + "".join(govde) + "</table></div>"


def _w_tablo(tbl) -> tuple[list[str], str]:
    """Word tablosu: metin satırları ("hücre | hücre") ve HTML; birleşik hücreler colspan/rowspan olur."""
    satirlar = [[_w_hucre(tc) for tc in tr.findall(W + "tc")] for tr in tbl.findall(W + "tr")]
    _dikey_birlestir(satirlar)
    return _tablo_ciktisi(satirlar)


def docx_oku(veri: bytes) -> EkOkuma:
    with zipfile.ZipFile(io.BytesIO(veri)) as z:
        kok = ET.fromstring(z.read("word/document.xml"))
    govde = kok.find(W + "body")
    metin, gorunum, tablo = [], [], 0
    for e in list(govde) if govde is not None else []:
        if e.tag == W + "p":
            d, h = _w_paragraf_html(e)
            if d:
                metin.append(d)
                gorunum.append(h)
        elif e.tag == W + "tbl":
            m, h = _w_tablo(e)
            metin.extend(m)
            gorunum.append(h)
            tablo += 1
        elif e.tag == W + "sdt":  # içerik denetimi içindeki paragraflar
            for p in e.iter(W + "p"):
                d, h = _w_paragraf_html(p)
                if d:
                    metin.append(d)
                    gorunum.append(h)
    notlar = [f"{tablo} tablo"] if tablo else []
    return EkOkuma("docx", "tamam", md.paragraflari_duzenle("\n".join(metin)), "\n".join(gorunum), notlar)


# --- xlsx ----------------------------------------------------------------------------------------------------

def _sutun_no(ref: str) -> int:
    n = 0
    for c in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(c) - 64
    return n - 1


def _hucre_konumu(ref: str) -> tuple[int, int]:
    """"B12" → (11, 1): 0'dan başlayan satır ve sütun."""
    return int(re.search(r"\d+", ref).group(0)) - 1, _sutun_no(ref)


def _paylasilan_metinler(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    return ["".join(t.text or "" for t in si.iter(S + "t"))
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(S + "si")]


def _sayfa_yollari(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Çalışma kitabındaki sayfalar sırasıyla (ad, zip içindeki yol)."""
    iliskiler = {}
    if "xl/_rels/workbook.xml.rels" in z.namelist():
        for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
            hedef = r.get("Target", "")
            iliskiler[r.get("Id")] = hedef.lstrip("/") if hedef.startswith("/") else "xl/" + hedef
    adlar = set(z.namelist())
    return [(s.get("name", ""), iliskiler.get(s.get(R + "id"), ""))
            for s in ET.fromstring(z.read("xl/workbook.xml")).iter(S + "sheet")
            if iliskiler.get(s.get(R + "id"), "") in adlar]


def _hucre_degeri(c, paylasilan: list[str]) -> str:
    v, tur = c.find(S + "v"), c.get("t")
    if tur == "s" and v is not None and (v.text or "").isdigit():
        n = int(v.text)
        return paylasilan[n] if n < len(paylasilan) else ""
    if tur == "inlineStr":
        return "".join(t.text or "" for t in c.iter(S + "t"))
    return (v.text or "") if v is not None else ""


def _sayfa_hucreleri(ws, paylasilan: list[str]) -> dict[tuple[int, int], str]:
    hucreler = {}
    for c in ws.iter(S + "c"):
        if (ref := c.get("r", "")) and (deger := _hucre_degeri(c, paylasilan).strip()):
            hucreler[_hucre_konumu(ref)] = deger
    return hucreler


def _birlesik_hucreler(ws) -> dict[tuple[int, int], tuple[int, int] | None]:
    """Birleşik alanın sol üst hücresi (rowspan, colspan); alanın diğer hücreleri None (yazılmaz)."""
    birlesik: dict[tuple[int, int], tuple[int, int] | None] = {}
    for mc in ws.iter(S + "mergeCell"):
        a, _, b = (mc.get("ref") or "").partition(":")
        if not (a and b):
            continue
        (r1, c1), (r2, c2) = _hucre_konumu(a), _hucre_konumu(b)
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                birlesik[(r, c)] = None
        birlesik[(r1, c1)] = (r2 - r1 + 1, c2 - c1 + 1)
    return birlesik


def _excel_sayfasi(ad: str, hucreler: dict, birlesik: dict) -> tuple[list[str], str, bool]:
    """Bir sayfanın metin satırları, HTML'i ve görünümün hücre sınırında kesilip kesilmediği."""
    satir_sayisi = max(r for r, _ in hucreler) + 1
    sutun_sayisi = max(c for _, c in hucreler) + 1
    metin, govde, toplam = [f"[Sayfa: {ad}]"], [], 0
    for r in range(satir_sayisi):
        degerler = [hucreler.get((r, c), "") for c in range(sutun_sayisi)]
        if any(degerler):
            metin.append(" | ".join(d for d in degerler if d))
        if toplam > EXCEL_EN_FAZLA_HUCRE:
            continue  # metin tam kalır, görünüm kesilir
        tds = []
        for c in range(sutun_sayisi):
            yay = birlesik.get((r, c), (1, 1))
            if yay is None:
                continue
            oz = (f' rowspan="{yay[0]}"' if yay[0] > 1 else "") + (f' colspan="{yay[1]}"' if yay[1] > 1 else "")
            tds.append(f"<td{oz}>{html_lib.escape(hucreler.get((r, c), ''), quote=False)}</td>")
            toplam += 1
        govde.append("<tr>" + "".join(tds) + "</tr>")
    html = (f'<section class="excel-sayfasi"><h4>{html_lib.escape(ad)}</h4><div class="tablo"><table>'
            + "".join(govde) + "</table></div></section>")
    return metin, html, toplam > EXCEL_EN_FAZLA_HUCRE


def xlsx_oku(veri: bytes) -> EkOkuma:
    metin, gorunum, notlar = [], [], []
    with zipfile.ZipFile(io.BytesIO(veri)) as z:
        paylasilan = _paylasilan_metinler(z)
        for ad, yol in _sayfa_yollari(z):
            ws = ET.fromstring(z.read(yol))
            hucreler = _sayfa_hucreleri(ws, paylasilan)
            if not hucreler:
                continue
            m, h, kesildi = _excel_sayfasi(ad, hucreler, _birlesik_hucreler(ws))
            metin += m
            gorunum.append(h)
            if kesildi:
                notlar.append(f"{ad}: görünüm ilk {EXCEL_EN_FAZLA_HUCRE} hücreyle sınırlandı (metin tam)")
    return EkOkuma("xlsx", "tamam", "\n".join(metin), "\n".join(gorunum), notlar)


# --- pptx / odt / ods ------------------------------------------------------------------------------------------

def pptx_oku(veri: bytes) -> EkOkuma:
    with zipfile.ZipFile(io.BytesIO(veri)) as z:
        slaytlar = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                          key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group(0)))
        metin, gorunum = [], []
        for n, ad in enumerate(slaytlar, 1):
            paragraflar = []
            for p in ET.fromstring(z.read(ad)).iter(A + "p"):
                t = "".join(x.text or "" for x in p.iter(A + "t")).strip()
                if t:
                    paragraflar.append(t)
            metin.append(f"[Slayt {n}]")
            metin.extend(paragraflar)
            gorunum.append(f'<section class="slayt"><h4>Slayt {n}</h4>'
                           + "".join(f"<p>{html_lib.escape(p, quote=False)}</p>" for p in paragraflar) + "</section>")
    return EkOkuma("pptx", "tamam", "\n".join(metin), "\n".join(gorunum))


# --- LibreOffice -----------------------------------------------------------------------------------------------

def soffice_yolu() -> str | None:
    aday = os.environ.get("MEVZUAT_SOFFICE") or shutil.which("soffice") or shutil.which("libreoffice")
    if not aday:
        for yol in (r"C:\Program Files\LibreOffice\program\soffice.exe", "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice"):
            if Path(yol).exists():
                return yol
    return aday


def libreoffice_cevir(veri: bytes, kaynak_uzanti: str, hedef: str) -> bytes:
    """Dosyayı LibreOffice ile `hedef` biçime çevirir (docx, xlsx, pdf). Her çağrı kendi profil klasörünü kullanır,
    aynı anda çalışan çeviriler birbirini kilitlemesin."""
    soffice = soffice_yolu()
    if not soffice:
        raise FileNotFoundError("LibreOffice (soffice) bulunamadı")
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        girdi = t / f"girdi.{kaynak_uzanti}"
        girdi.write_bytes(veri)
        profil = (t / "profil").as_uri()
        komut = [soffice, f"-env:UserInstallation={profil}", "--headless", "--norestore", "--convert-to", hedef,
                 "--outdir", str(t / "cikti"), str(girdi)]
        try:
            subprocess.run(komut, capture_output=True, timeout=CEVIRI_ZAMAN_ASIMI, check=False)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"LibreOffice {CEVIRI_ZAMAN_ASIMI} sn içinde bitmedi") from e
        ciktilar = list((t / "cikti").glob("*"))
        if not ciktilar:
            raise RuntimeError("LibreOffice çıktı üretmedi")
        return ciktilar[0].read_bytes()


# --- arşivler -------------------------------------------------------------------------------------------------

def _zip_adi(bilgi: zipfile.ZipInfo) -> str:
    # UTF-8 bayrağı yoksa zipfile adı cp437 sayar; Türkçe Windows'ta yapılmış arşivler cp857'dir.
    if bilgi.flag_bits & 0x800:
        return bilgi.filename
    try:
        return bilgi.filename.encode("cp437").decode("cp857")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return bilgi.filename


def zip_ac(veri: bytes) -> tuple[list[tuple[str, bytes]], list[str]]:
    dosyalar, notlar, toplam = [], [], 0
    with zipfile.ZipFile(io.BytesIO(veri)) as z:
        for bilgi in z.infolist():
            if bilgi.is_dir():
                continue
            ad = _zip_adi(bilgi)
            if len(dosyalar) >= ARSIV_EN_FAZLA_DOSYA:
                notlar.append(f"ilk {ARSIV_EN_FAZLA_DOSYA} dosya açıldı")
                break
            if bilgi.file_size > ARSIV_EN_FAZLA_DOSYA_BOYUTU or toplam + bilgi.file_size > ARSIV_EN_FAZLA_TOPLAM:
                notlar.append(f"{ad}: çok büyük, açılmadı ({bilgi.file_size} bayt)")
                continue
            if bilgi.flag_bits & 0x1:
                notlar.append(f"{ad}: parolalı, açılmadı")
                continue
            try:
                icerik = z.read(bilgi)
            except Exception as e:
                notlar.append(f"{ad}: açılamadı ({e!r})"[:200])
                continue
            toplam += len(icerik)
            dosyalar.append((ad, icerik))
    return dosyalar, notlar


def arsiv_acici() -> list[str] | None:
    """rar ve 7z için harici açıcı komutu. bsdtar (libarchive) ve 7-Zip denenir."""
    for ad in ("7zz", "7z", "7za"):
        if yol := shutil.which(ad):
            return [yol, "x", "-y", "-bd"]
    for yol in (r"C:\Program Files\7-Zip\7z.exe",):
        if Path(yol).exists():
            return [yol, "x", "-y", "-bd"]
    if yol := shutil.which("bsdtar"):
        return [yol, "-xf"]
    return None


def disaridan_ac(veri: bytes, uzanti: str) -> tuple[list[tuple[str, bytes]], list[str]]:
    komut = arsiv_acici()
    if not komut:
        raise FileNotFoundError("rar/7z açıcı (7-Zip ya da bsdtar) bulunamadı")
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        girdi = t / f"girdi.{uzanti}"
        girdi.write_bytes(veri)
        hedef = t / "ac"
        hedef.mkdir()
        if Path(komut[0]).name.startswith("bsdtar"):
            arg = komut + [str(girdi), "-C", str(hedef)]
        else:
            arg = komut + [f"-o{hedef}", str(girdi)]
        try:
            sonuc = subprocess.run(arg, capture_output=True, timeout=CEVIRI_ZAMAN_ASIMI, check=False)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("arşiv açma zaman aşımı") from e
        dosyalar, notlar, toplam = [], [], 0
        for yol in sorted(hedef.rglob("*")):
            if not yol.is_file() or yol.is_symlink():
                continue
            # Arşivin dışına yazmaya çalışan adlar (../) zaten hedef dışına çıkamaz; yine de kontrol.
            if hedef.resolve() not in yol.resolve().parents:
                continue
            boyut = yol.stat().st_size
            if len(dosyalar) >= ARSIV_EN_FAZLA_DOSYA or toplam + boyut > ARSIV_EN_FAZLA_TOPLAM:
                notlar.append("sınır aşıldı, kalan dosyalar açılmadı")
                break
            toplam += boyut
            dosyalar.append((yol.relative_to(hedef).as_posix(), yol.read_bytes()))
        if sonuc.returncode != 0 and not dosyalar:
            raise RuntimeError(f"arşiv açılamadı: {sonuc.stderr.decode('utf-8', 'replace')[:200]}")
        return dosyalar, notlar


# --- ana giriş ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Ek:
    """Okuyuculara verilen bilgi: dosyanın kendisi ve sayfa görüntülerinin yazılacağı yer."""
    veri: bytes
    ad: str
    bicim: str
    kok: Path
    goruntu_klasoru: str
    sha: str


def _cevirerek(hedef: str, okuyucu):
    """Eski biçimi LibreOffice ile `hedef` biçime çevirip o biçimin okuyucusuyla okuyan okuyucu."""
    def oku(ek: _Ek) -> EkOkuma:
        sonuc = okuyucu(libreoffice_cevir(ek.veri, ek.bicim, hedef))
        sonuc.bicim = ek.bicim
        sonuc.notlar.append("LibreOffice ile çevrildi")
        return sonuc
    return oku


def _pdf_eki(ek: _Ek) -> EkOkuma:
    with tempfile.TemporaryDirectory() as tmp:
        yol = Path(tmp) / "ek.pdf"
        yol.write_bytes(ek.veri)
        p = pdf_okuma.pdf_oku(yol, ek.kok, ek.goruntu_klasoru, pdf_okuma.ocr_onbellek_yolu(ek.kok, ek.sha))
    return EkOkuma("pdf", "tamam" if p.okuma != "okunamadi" else "gorunum_yok", p.metin, p.gorunum, p.kusurlar,
                   sayfalar=p.sayfalar)


def _arsiv_eki(acici):
    """`acici(ek)` → (içindeki dosyalar, notlar). Dosyalar ayrı ek gibi işlenmek üzere döner."""
    def oku(ek: _Ek) -> EkOkuma:
        dosyalar, notlar = acici(ek)
        return EkOkuma(ek.bicim, "tamam", "", "", notlar + [f"{len(dosyalar)} dosya"], ic_dosyalar=dosyalar)
    return oku


def _resim_eki(ek: _Ek) -> EkOkuma:
    with tempfile.TemporaryDirectory() as tmp:
        yol = Path(tmp) / f"resim{Path(ek.ad).suffix or '.png'}"
        yol.write_bytes(ek.veri)
        metin, durum = pdf_okuma.resim_oku(yol)
    return EkOkuma("resim", "tamam", metin if durum == "metin" else "", "", [f"OCR: {durum}"])


def _html_eki(ek: _Ek) -> EkOkuma:
    from mevzuat.icerik import html_coz
    h = html_okuma.html_oku(html_coz(ek.veri))
    return EkOkuma("html", "tamam", h.metin, h.gorunum)


def _metin_eki(ek: _Ek) -> EkOkuma:
    metin = md.temizle(ek.veri.decode("utf-8", "replace"))
    return EkOkuma("txt", "tamam", metin, html_okuma.duz_metinden_gorunum(metin))


# Biçim → okuyucu. Yeni biçim buraya bir satır eklenerek desteklenir, ek_oku değişmez.
OKUYUCULAR = {
    "docx": lambda ek: docx_oku(ek.veri),
    "xlsx": lambda ek: xlsx_oku(ek.veri),
    "pptx": lambda ek: pptx_oku(ek.veri),
    "doc": _cevirerek("docx", docx_oku), "rtf": _cevirerek("docx", docx_oku), "odt": _cevirerek("docx", docx_oku),
    "xls": _cevirerek("xlsx", xlsx_oku), "ods": _cevirerek("xlsx", xlsx_oku),
    "ppt": _cevirerek("pptx", pptx_oku), "odp": _cevirerek("pptx", pptx_oku),
    "pdf": _pdf_eki,
    "zip": _arsiv_eki(lambda ek: zip_ac(ek.veri)),
    "rar": _arsiv_eki(lambda ek: disaridan_ac(ek.veri, ek.bicim)), "7z": _arsiv_eki(lambda ek: disaridan_ac(ek.veri, ek.bicim)),
    "resim": _resim_eki,
    "html": _html_eki,
    "txt": _metin_eki,
}


def ek_oku(veri: bytes, ad: str, kok: Path, goruntu_klasoru: str, sha: str) -> EkOkuma:
    """Ek dosyasını okur. Arşivse içindekiler `ic_dosyalar`da döner (çağıran her birini ayrı ek gibi işler).
    Çevirici yoksa (LibreOffice, 7-Zip) ya da biçim tanınmadıysa durum "gorunum_yok", okuma hata verirse "hata"."""
    bicim = bicim_bul(veri, ad)
    okuyucu = OKUYUCULAR.get(bicim)
    if okuyucu is None:
        return EkOkuma(bicim, "gorunum_yok", notlar=[f"biçim tanınmadı ({bicim})"])
    try:
        return okuyucu(_Ek(veri, ad, bicim, kok, goruntu_klasoru, sha))
    except FileNotFoundError as e:
        return EkOkuma(bicim, "gorunum_yok", notlar=[str(e)])
    except Exception as e:
        log.warning("Ek okunamadı %s (%s): %r", ad, bicim, e)
        return EkOkuma(bicim, "hata", notlar=[f"okunamadı: {e!r}"[:300]])

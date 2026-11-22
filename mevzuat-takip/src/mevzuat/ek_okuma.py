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

def bicim_bul(veri: bytes, ad: str = "") -> str:
    uzanti = Path(ad.lower()).suffix.lstrip(".")
    bas = veri[:8]
    if bas.startswith(b"%PDF"):
        return "pdf"
    if bas.startswith(b"{\\rtf"):
        return "rtf"
    if bas.startswith(b"Rar!"):
        return "rar"
    if bas.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if bas.startswith(b"\xd0\xcf\x11\xe0"):
        # Eski Office (OLE). Hangisi olduğu uzantıdan, uzantı uymuyorsa içerikten tahmin edilir.
        if uzanti in ("doc", "xls", "ppt", "dot", "xlt", "pps"):
            return {"dot": "doc", "xlt": "xls", "pps": "ppt"}.get(uzanti, uzanti)
        if b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t" in veri[:200_000]:
            return "doc"
        if b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k" in veri[:200_000]:
            return "xls"
        return "ole"
    if bas.startswith(b"PK\x03\x04") or bas.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(_bayt_dosyasi(veri)) as z:
                adlar = set(z.namelist())
                if "word/document.xml" in adlar:
                    return "docx"
                if "xl/workbook.xml" in adlar:
                    return "xlsx"
                if "ppt/presentation.xml" in adlar:
                    return "pptx"
                if "mimetype" in adlar:
                    mt = z.read("mimetype")[:100]
                    if b"opendocument.text" in mt:
                        return "odt"
                    if b"opendocument.spreadsheet" in mt:
                        return "ods"
                    if b"opendocument.presentation" in mt:
                        return "odp"
        except zipfile.BadZipFile:
            return "bozuk"
        return "zip"
    if bas[:4] in (b"\x89PNG", b"GIF8") or bas[:3] == b"\xff\xd8\xff" or bas[:2] in (b"BM",) or bas[:4] in (b"II*\x00", b"MM\x00*"):
        return "resim"
    bas_metin = veri[:600].lstrip(b"\xef\xbb\xbf\xff\xfe\xfe\xff").lower().replace(b"\x00", b"")
    if bas_metin.lstrip().startswith((b"<!doctype", b"<html", b"<?xml", b"<head")) or b"<html" in bas_metin:
        return "html"
    if uzanti in ("txt", "csv"):
        return "txt"
    return "bilinmiyor"


def _bayt_dosyasi(veri: bytes):
    import io
    return io.BytesIO(veri)


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


def _w_tablo(tbl) -> tuple[list[str], str]:
    """Word tablosu, birleşik hücreler (gridSpan, vMerge) colspan/rowspan olur."""
    satirlar = []
    for tr in tbl.findall(W + "tr"):
        hucreler = []
        for tc in tr.findall(W + "tc"):
            tcpr = tc.find(W + "tcPr")
            yay = 1
            dikey = None
            if tcpr is not None:
                gs = tcpr.find(W + "gridSpan")
                if gs is not None and (gs.get(W + "val") or "").isdigit():
                    yay = int(gs.get(W + "val"))
                vm = tcpr.find(W + "vMerge")
                if vm is not None:
                    dikey = vm.get(W + "val") or "continue"
            ic_duz, ic_html = [], []
            for p in tc.findall(W + "p"):
                d, h = _w_paragraf_html(p)
                if d:
                    ic_duz.append(d)
                    ic_html.append(re.sub(r"^<p[^>]*>|</p>$", "", h))
            for alt in tc.findall(W + "tbl"):  # iç içe tablo hücre metnine katılır
                m, _ = _w_tablo(alt)
                ic_duz.extend(m)
                ic_html.extend(html_lib.escape(x, quote=False) for x in m)
            hucreler.append({"duz": " ".join(ic_duz), "html": "<br>".join(ic_html), "yay": yay, "dikey": dikey})
        satirlar.append(hucreler)
    # vMerge: "restart" olan hücre aşağıdaki "continue" sayısı kadar rowspan alır, devam hücreleri yazılmaz.
    sutun_konumu = []
    for s in satirlar:
        konum, konumlar = 0, []
        for h in s:
            konumlar.append(konum)
            konum += h["yay"]
        sutun_konumu.append(konumlar)
    for i, s in enumerate(satirlar):
        for j, h in enumerate(s):
            if h["dikey"] == "restart":
                n, k = 1, sutun_konumu[i][j]
                for alt in range(i + 1, len(satirlar)):
                    eslesen = [x for x, kk in zip(satirlar[alt], sutun_konumu[alt]) if kk == k]
                    if eslesen and eslesen[0]["dikey"] == "continue":
                        n += 1
                    else:
                        break
                h["satir"] = n
    metin, govde = [], []
    for s in satirlar:
        metin_satiri = " | ".join(h["duz"] for h in s if h["duz"] and h["dikey"] != "continue")
        if metin_satiri:
            metin.append(metin_satiri)
        hucre_html = []
        for h in s:
            if h["dikey"] == "continue":
                continue
            oz = (f' colspan="{h["yay"]}"' if h["yay"] > 1 else "") + (f' rowspan="{h["satir"]}"' if h.get("satir", 1) > 1 else "")
            hucre_html.append(f"<td{oz}>{h['html']}</td>")
        govde.append("<tr>" + "".join(hucre_html) + "</tr>")
    return metin, '<div class="tablo"><table>' + "".join(govde) + "</table></div>"


def docx_oku(veri: bytes) -> EkOkuma:
    with zipfile.ZipFile(_bayt_dosyasi(veri)) as z:
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


def xlsx_oku(veri: bytes) -> EkOkuma:
    with zipfile.ZipFile(_bayt_dosyasi(veri)) as z:
        adlar = set(z.namelist())
        paylasilan = []
        if "xl/sharedStrings.xml" in adlar:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(S + "si"):
                paylasilan.append("".join(t.text or "" for t in si.iter(S + "t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        iliskiler = {}
        if "xl/_rels/workbook.xml.rels" in adlar:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                hedef = r.get("Target", "")
                iliskiler[r.get("Id")] = hedef.lstrip("/") if hedef.startswith("/") else "xl/" + hedef
        metin, gorunum, notlar = [], [], []
        for sayfa in wb.iter(S + "sheet"):
            ad = sayfa.get("name", "")
            yol = iliskiler.get(sayfa.get(R + "id"), "")
            if yol not in adlar:
                continue
            ws = ET.fromstring(z.read(yol))
            hucreler: dict[tuple[int, int], str] = {}
            for satir in ws.iter(S + "row"):
                for c in satir.findall(S + "c"):
                    ref, tur = c.get("r", ""), c.get("t")
                    if not ref:
                        continue
                    v = c.find(S + "v")
                    if tur == "s" and v is not None and (v.text or "").isdigit():
                        deger = paylasilan[int(v.text)] if int(v.text) < len(paylasilan) else ""
                    elif tur == "inlineStr":
                        deger = "".join(t.text or "" for t in c.iter(S + "t"))
                    else:
                        deger = (v.text or "") if v is not None else ""
                    if deger.strip():
                        hucreler[(int(re.search(r"\d+", ref).group(0)) - 1, _sutun_no(ref))] = deger.strip()
            if not hucreler:
                continue
            birlesik = {}
            for mc in ws.iter(S + "mergeCell"):
                a, _, b = (mc.get("ref") or "").partition(":")
                if a and b:
                    r1, c1 = int(re.search(r"\d+", a).group(0)) - 1, _sutun_no(a)
                    r2, c2 = int(re.search(r"\d+", b).group(0)) - 1, _sutun_no(b)
                    birlesik[(r1, c1)] = (r2 - r1 + 1, c2 - c1 + 1)
                    for rr in range(r1, r2 + 1):
                        for cc in range(c1, c2 + 1):
                            if (rr, cc) != (r1, c1):
                                birlesik[(rr, cc)] = None
            satir_sayisi = max(r for r, _ in hucreler) + 1
            sutun_sayisi = max(c for _, c in hucreler) + 1
            metin.append(f"[Sayfa: {ad}]")
            govde, toplam = [], 0
            for r in range(satir_sayisi):
                degerler = [hucreler.get((r, c), "") for c in range(sutun_sayisi)]
                if any(degerler):
                    metin.append(" | ".join(d for d in degerler if d))
                if toplam > EXCEL_EN_FAZLA_HUCRE:
                    continue
                tds = []
                for c in range(sutun_sayisi):
                    b = birlesik.get((r, c), (1, 1))
                    if b is None:
                        continue
                    oz = (f' rowspan="{b[0]}"' if b[0] > 1 else "") + (f' colspan="{b[1]}"' if b[1] > 1 else "")
                    tds.append(f"<td{oz}>{html_lib.escape(hucreler.get((r, c), ''), quote=False)}</td>")
                    toplam += 1
                govde.append("<tr>" + "".join(tds) + "</tr>")
            if toplam > EXCEL_EN_FAZLA_HUCRE:
                notlar.append(f"{ad}: görünüm ilk {EXCEL_EN_FAZLA_HUCRE} hücreyle sınırlandı (metin tam)")
            gorunum.append(f'<section class="excel-sayfasi"><h4>{html_lib.escape(ad)}</h4><div class="tablo"><table>'
                           + "".join(govde) + "</table></div></section>")
    return EkOkuma("xlsx", "tamam", "\n".join(metin), "\n".join(gorunum), notlar)


# --- pptx / odt / ods ------------------------------------------------------------------------------------------

def pptx_oku(veri: bytes) -> EkOkuma:
    with zipfile.ZipFile(_bayt_dosyasi(veri)) as z:
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
    with zipfile.ZipFile(_bayt_dosyasi(veri)) as z:
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
            sonuc = subprocess.run(arg, capture_output=True, timeout=CEVIRI_ZAMAN_ASIMI)
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

def ek_oku(veri: bytes, ad: str, kok: Path, goruntu_klasoru: str, sha: str) -> EkOkuma:
    """Ek dosyasını okur. Arşivse içindekiler `ic_dosyalar`da döner (çağıran her birini ayrı ek gibi işler)."""
    bicim = bicim_bul(veri, ad)
    try:
        if bicim == "docx":
            return docx_oku(veri)
        if bicim == "xlsx":
            return xlsx_oku(veri)
        if bicim == "pptx":
            return pptx_oku(veri)
        if bicim in ("doc", "rtf", "odt"):
            sonuc = docx_oku(libreoffice_cevir(veri, bicim, "docx"))
            sonuc.bicim = bicim
            sonuc.notlar.append("LibreOffice ile çevrildi")
            return sonuc
        if bicim in ("xls", "ods"):
            sonuc = xlsx_oku(libreoffice_cevir(veri, bicim, "xlsx"))
            sonuc.bicim = bicim
            sonuc.notlar.append("LibreOffice ile çevrildi")
            return sonuc
        if bicim in ("ppt", "odp"):
            sonuc = pptx_oku(libreoffice_cevir(veri, bicim, "pptx"))
            sonuc.bicim = bicim
            sonuc.notlar.append("LibreOffice ile çevrildi")
            return sonuc
        if bicim == "pdf":
            with tempfile.TemporaryDirectory() as tmp:
                yol = Path(tmp) / "ek.pdf"
                yol.write_bytes(veri)
                p = pdf_okuma.pdf_oku(yol, kok, goruntu_klasoru, pdf_okuma.ocr_onbellek_yolu(kok, sha))
            durum = "tamam" if p.okuma != "okunamadi" else "gorunum_yok"
            return EkOkuma("pdf", durum, p.metin, p.gorunum, p.kusurlar, sayfalar=p.sayfalar)
        if bicim == "zip":
            dosyalar, notlar = zip_ac(veri)
            return EkOkuma("zip", "tamam", "", "", notlar + [f"{len(dosyalar)} dosya"], ic_dosyalar=dosyalar)
        if bicim in ("rar", "7z"):
            dosyalar, notlar = disaridan_ac(veri, bicim)
            return EkOkuma(bicim, "tamam", "", "", notlar + [f"{len(dosyalar)} dosya"], ic_dosyalar=dosyalar)
        if bicim == "resim":
            with tempfile.TemporaryDirectory() as tmp:
                yol = Path(tmp) / f"resim{Path(ad).suffix or '.png'}"
                yol.write_bytes(veri)
                metin, durum = pdf_okuma.resim_oku(yol)
            return EkOkuma("resim", "tamam", metin if durum == "metin" else "", "", [f"OCR: {durum}"])
        if bicim == "html":
            from mevzuat.icerik import html_coz
            h = html_okuma.html_oku(html_coz(veri))
            return EkOkuma("html", "tamam", h.metin, h.gorunum)
        if bicim == "txt":
            metin = md.temizle(veri.decode("utf-8", "replace"))
            return EkOkuma("txt", "tamam", metin, html_okuma.duz_metinden_gorunum(metin))
        return EkOkuma(bicim, "gorunum_yok", notlar=[f"biçim tanınmadı ({bicim})"])
    except FileNotFoundError as e:
        return EkOkuma(bicim, "gorunum_yok", notlar=[str(e)])
    except Exception as e:
        log.warning("Ek okunamadı %s (%s): %r", ad, bicim, e)
        return EkOkuma(bicim, "hata", notlar=[f"okunamadı: {e!r}"[:300]])

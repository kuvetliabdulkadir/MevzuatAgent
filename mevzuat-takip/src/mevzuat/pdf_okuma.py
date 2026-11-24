"""PDF'i sayfa sayfa okur: yazı katmanı, OCR, görsel/harita sayfası ve tablolar. Kaynaktan bağımsız.

Eski arşiv okuyucusu belgenin toplam yazısı 500 karakteri geçince sayfalara hiç bakmıyordu, resim olan sayfalar
okunmadan kalıyordu (25 belgede 599 sayfa). OCR da 30 sayfada kesiliyordu (66 belge). Burada her sayfa ayrı karar verir:

  1. Yazı katmanı iyi mi? Boş (30 karakterden az), (cid:N) ya da yedek karakter (U+FFFD) içeriyor, harfleri dağılmış
     (sözlük oranı düşük, sayı tablosu değil) ya da sayfanın çoğu resim ve yazısı kısaysa iyi değildir.
  2. İyi değilse sayfa OCR'dan geçer. Sayfa sınırı yoktur. Sonuç sayfa sayfa önbelleğe yazılır, iş yarıda kalırsa
     kaldığı sayfadan devam eder. Birden çok sayfa paralel okunur (MEVZUAT_OCR_PARALEL).
  3. OCR çıktısı anlamsızsa (harita, fotoğraf) sayfa "görsel" olur: metne "[s. N: görsel ...]" yazılır, sayfa görüntü
     olarak saklanır ve panelde yerinde gösterilir, OCR yazısı sadece aramaya girer. Sayfanın bir kısmı anlamsızsa
     o bloklar metinden çıkar, sayfa görüntüsü de saklanır.
  4. Yazı katmanlı sayfadaki tablolar pdfplumber ile bulunur. Güvenilir olanlar (sütun sayısı tutarlı, boş hücre az)
     HTML tabloya çevrilir, güvenilmez olanlar sayfadan kesilip görüntü olarak gösterilir. Metinde iki durumda da
     satır satır ("hücre | hücre") yer alır, arama ve fark için.
  5. Bir sayfa okunamazsa (OCR zaman aşımı, Tesseract yok) sadece o sayfa "okunamadı" olur, belge kesilmez ve
     kalite "kusurlu" işaretlenir.
"""

import html as html_lib
import json
import logging
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2

from mevzuat import html_okuma, metin_duzen as md, ocr

log = logging.getLogger(__name__)

OKUYUCU = 2
YAZI_ESIGI = 30  # sayfada bundan az yazı varsa boş sayılır
# Yazısı bundan kısa ve resmi olan sayfa OCR'a gider. RG'deki taranmış CB kararlarında başlık yazı (~140 karakter),
# gövde resimdir (resim sayfanın %19'u, fixtures/rg_20261001-3-2_taranmis.pdf).
KARISIK_YAZI_ESIGI = 200
KARISIK_RESIM_ORANI = 0.05
GORUNTU_DPI = 150  # panelde gösterilen sayfa/tablo görüntüsü
TABLO_BOS_ORANI = 0.5
TABLO_ARAMA_EN_FAZLA_CIZGI = 3000
# OCR sayfası sınıfları (metin_duzen.cop_orani; 2026-11-22 ölçümü: temiz OCR < 0,10, taranmış tablo/harita 0,15-0,35).
GURULTULU_COP = 0.15  # bundan fazlası: panelde sayfa görüntüsü, OCR yazısı metinde
GORSEL_COP = 0.35  # bundan fazlası: görsel/harita, OCR yazısı sadece aramada
AZ_YAZI = 40  # bundan az parçalı OCR sayfası (kroki, şekil) görüntüyle gösterilir


@dataclass
class PdfOkuma:
    metin: str
    gorunum: str
    okuma: str  # pdf_metin / ocr / karisik / okunamadi
    sayfa_sayisi: int
    sayfalar: list[dict] = field(default_factory=list)  # {no, yontem, karakter, uyari, goruntu, tablo_yapi, tablo_goruntu}
    arama_metni: str = ""  # görsel sayfaların OCR yazısı (metne girmez, sadece arama)
    kusurlar: list[str] = field(default_factory=list)


def _resim_orani(sayfa) -> float:
    alan = (sayfa.width * sayfa.height) or 1
    return min(1.0, sum(max(0, (r["x1"] - r["x0"]) * (r["bottom"] - r["top"])) for r in sayfa.images) / alan)


# Yazı katmanı neden kullanılamaz (None ise iyidir).
def yazi_sorunu(metin: str, resim_orani: float) -> str | None:
    if len(metin.strip()) < YAZI_ESIGI:
        return "yazı yok"
    if md.kalinti_kusurlari(metin):
        return "bozuk font"
    # Harfleri dağılmış katman ("Ista -; nb A. u b l a O n k t a"): parçaların çoğu sözlükte değil ve çoğu tek harf.
    # Sadece Türkçe karakter kullanmayan ("yururluge") ama düzgün yazı katmanı buna girmez.
    cop, parca = md.cop_orani(metin)
    parcalar = metin.split()
    tek_harf = sum(1 for p in parcalar if len(p) == 1 and p.isalpha()) / max(1, len(parcalar))
    if parca >= 20 and cop >= 0.3 and tek_harf >= 0.25:
        return "harfleri dağılmış yazı katmanı"
    if len(metin) < KARISIK_YAZI_ESIGI and resim_orani > KARISIK_RESIM_ORANI:
        return "sayfanın çoğu resim"
    return None


def _hucre(deger) -> str:
    return " ".join(str(deger).split()) if deger not in (None, "") else ""


# Bulunan tablo güvenilir mi: en az 2 satır ve 2 sütun, boş hücre oranı yarıdan az.
def _tablo_guvenilir(satirlar: list[list]) -> bool:
    if len(satirlar) < 2:
        return False
    sutun = max(len(s) for s in satirlar)
    if sutun < 2 or any(len(s) != sutun for s in satirlar):
        return False
    hucreler = [h for s in satirlar for h in s]
    return sum(1 for h in hucreler if _hucre(h) == "") / len(hucreler) < TABLO_BOS_ORANI


def _tablo_html(satirlar: list[list]) -> str:
    govde = []
    for s in satirlar:
        hucreler, i = [], 0
        while i < len(s):
            yayilma = 1
            # pdfplumber birleşik hücrenin devamını None verir.
            while i + yayilma < len(s) and s[i + yayilma] is None:
                yayilma += 1
            oz = f' colspan="{yayilma}"' if yayilma > 1 else ""
            ic = html_lib.escape("" if s[i] is None else str(s[i]), quote=False).replace("\n", "<br>")
            hucreler.append(f"<td{oz}>{ic}</td>")
            i += yayilma
        govde.append("<tr>" + "".join(hucreler) + "</tr>")
    return '<div class="tablo"><table>' + "".join(govde) + "</table></div>"


def _goruntu_html(yol: str, alt: str, sinif: str = "sayfa-goruntusu") -> str:
    return f'<figure class="{sinif}"><img data-yol="{html_lib.escape(yol)}" alt="{html_lib.escape(alt)}"></figure>'


class _Onbellek:
    """OCR sonuçları, sayfa sayfa. İş yarıda kalırsa okunmuş sayfalar yeniden okunmaz."""

    def __init__(self, yol: Path | None):
        self.yol = yol
        self.veri: dict[str, str] = {}
        if yol and yol.exists():
            try:
                self.veri = json.loads(yol.read_text("utf-8"))
            except (OSError, ValueError):
                self.veri = {}

    def al(self, no: int) -> str | None:
        return self.veri.get(str(no))

    def koy(self, no: int, metin: str) -> None:
        self.veri[str(no)] = metin
        if self.yol:
            self.yol.parent.mkdir(parents=True, exist_ok=True)
            gecici = self.yol.with_suffix(".yaziliyor")
            gecici.write_text(json.dumps(self.veri, ensure_ascii=False), "utf-8")
            gecici.replace(self.yol)


def _render(pdf: pypdfium2.PdfDocument, i: int, hedef: Path, dpi: int) -> None:
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gorsel = pdf[i].render(scale=dpi / 72).to_pil()
    gorsel.save(hedef, optimize=True)


@dataclass
class _Okunus:
    """Bir PDF okunurken sayfa sayfa biriken sonuçlar ve görüntülerin yazılacağı yer."""
    pdf_yolu: Path
    kok: Path
    goruntu_klasoru: str
    metin: dict[int, str] = field(default_factory=dict)
    gorunum: dict[int, list[str]] = field(default_factory=dict)
    bilgi: dict[int, dict] = field(default_factory=dict)
    arama: dict[int, str] = field(default_factory=dict)
    kusurlar: list[str] = field(default_factory=list)

    def goruntu_yolu(self, i: int, ek: str = "") -> str:
        return f"{self.goruntu_klasoru}/s{i + 1}{ek}.png"

    # Yazı ya da OCR ile okunmuş düz metin sayfası (sınıflı paragraflar).
    def duz_sayfa(self, i: int, metin: str, onceki_gorunum: list[str] | None = None) -> None:
        self.metin[i] = metin
        self.gorunum[i] = (onceki_gorunum or []) + [html_okuma.duz_metinden_gorunum(metin)]
        self.bilgi[i]["karakter"] = len(metin)


def _okuma_bicimi(yontemler: set[str]) -> str:
    """Sayfaların okunuşundan belgenin okuma biçimi: pdf_metin / ocr / karisik / okunamadi."""
    okuyan = yontemler - {"okunamadi"}
    if not okuyan:
        return "okunamadi"
    if okuyan <= {"yazi"}:
        return "pdf_metin"
    return "karisik" if "yazi" in okuyan else "ocr"


def pdf_oku(pdf_yolu: Path, kok: Path, goruntu_klasoru: str, onbellek_yolu: Path | None = None,
            ocr_acik: bool | None = None, paralel: int | None = None,
            ilerle=lambda yapilan, toplam: None) -> PdfOkuma:
    """`kok` arşiv klasörü; sayfa ve tablo görüntüleri kok/goruntu_klasoru altına yazılır, yolları ona göre döner.
    `ocr_acik` verilmezse Tesseract varsa OCR yapılır."""
    o = _Okunus(pdf_yolu, kok, goruntu_klasoru)
    ocr_gereken: list[tuple[int, str]] = []
    with pdfplumber.open(str(pdf_yolu)) as pdf:
        sayfa_sayisi = len(pdf.pages)
        for i, sayfa in enumerate(pdf.pages):
            o.bilgi[i] = {"no": i + 1, "yontem": "yazi", "karakter": 0, "uyari": [], "goruntu": None,
                          "tablo_yapi": 0, "tablo_goruntu": 0}
            if (yazi := _yazi_katmanini_oku(o, sayfa, i)) is not None:
                ocr_gereken.append((i, yazi))
            ilerle(i + 1, sayfa_sayisi)
    if ocr_gereken:
        acik = ocr.ocr_kullanilabilir() if ocr_acik is None else ocr_acik
        sonuclar = _ocr_calistir(pdf_yolu, [i for i, _ in ocr_gereken], acik, paralel or ocr.paralel_sayisi(),
                                 _Onbellek(onbellek_yolu))
        _ocr_sonuclarini_yaz(o, ocr_gereken, sonuclar)
    sirali = range(sayfa_sayisi)
    metin = "\n\n".join(o.metin[i] for i in sirali if o.metin.get(i))
    gorunum = "\n".join(f'<section class="sayfa" data-sayfa="{i + 1}"><p class="sayfa-no">s. {i + 1}</p>'
                        + "\n".join(o.gorunum.get(i, [])) + "</section>" for i in sirali)
    return PdfOkuma(metin=metin, gorunum=gorunum, okuma=_okuma_bicimi({b["yontem"] for b in o.bilgi.values()}),
                    sayfa_sayisi=sayfa_sayisi, sayfalar=[o.bilgi[i] for i in sirali],
                    arama_metni="\n\n".join(o.arama[i] for i in sorted(o.arama)),
                    kusurlar=o.kusurlar + md.kalinti_kusurlari(metin))


def _yazi_katmanini_oku(o: _Okunus, sayfa, i: int) -> str | None:
    """Yazı katmanı iyiyse sayfayı yazar ve None döner; değilse OCR'a gidecek ham yazıyı döner."""
    bilgi = o.bilgi[i]
    try:
        yazi = md.temizle(sayfa.extract_text() or "")
        if sorun := yazi_sorunu(yazi, _resim_orani(sayfa)):
            bilgi["uyari"].append(sorun)
            return yazi
        metin, gorunum = _yazili_sayfa(sayfa, i, o)
        o.metin[i], o.gorunum[i], bilgi["karakter"] = "\n".join(metin), gorunum, len("\n".join(metin))
        return None
    except Exception as e:  # tek sayfanın hatası belgeyi düşürmesin
        log.warning("PDF sayfası okunamadı %s s.%d: %r", o.pdf_yolu, i + 1, e)
        bilgi["uyari"].append(f"yazı katmanı okunamadı: {e!r}"[:200])
        return ""
    finally:
        if hasattr(sayfa, "close"):
            sayfa.close()


def _tablolari_bul(sayfa, bilgi: dict) -> list:
    cizgi = len(sayfa.lines) + len(sayfa.rects)
    if cizgi > TABLO_ARAMA_EN_FAZLA_CIZGI:
        # Vektör çizim (harita, kroki): tablo araması dakikalarca sürebiliyor ve tablo bulmuyor.
        bilgi["uyari"].append(f"çizim sayfası ({cizgi} çizgi), tablo aranmadı")
        return []
    if cizgi < 4:
        return []
    try:
        return sorted(sayfa.find_tables(), key=lambda t: t.bbox[1])
    except Exception as e:
        bilgi["uyari"].append(f"tablo bulunamadı: {e!r}"[:200])
        return []


def _tablo_ekle(sayfa, tablo, i: int, n: int, o: _Okunus, metin: list[str], gorunum: list[str]) -> None:
    """Tablonun satırları metne ("hücre | hücre"); görünüme güvenilirse HTML tablo, değilse sayfadan kesilmiş görüntü."""
    bilgi = o.bilgi[i]
    satirlar = tablo.extract() or []
    satir_metni = [md.paragraflari_duzenle(s) for s in (" | ".join(_hucre(h) for h in r if _hucre(h)) for r in satirlar)
                   if s.strip()]
    if satir_metni:
        metin.append("\n".join(satir_metni))
    if _tablo_guvenilir(satirlar):
        gorunum.append(_tablo_html(satirlar))
        bilgi["tablo_yapi"] += 1
        return
    x0, top, x1, bottom = tablo.bbox
    yol = o.goruntu_yolu(i, f"-tablo{n + 1}")
    hedef = o.kok / yol
    if not hedef.exists():
        hedef.parent.mkdir(parents=True, exist_ok=True)
        kutu = (max(0, x0 - 4), max(0, top - 4), min(sayfa.width, x1 + 4), min(sayfa.height, bottom + 4))
        sayfa.crop(kutu).to_image(resolution=GORUNTU_DPI).save(str(hedef))
    gorunum.append(_goruntu_html(yol, f"s. {i + 1} tablo {n + 1}", "tablo-goruntusu"))
    bilgi["tablo_goruntu"] += 1


def _yazili_sayfa(sayfa, i: int, o: _Okunus) -> tuple[list[str], list[str]]:
    """Yazı katmanı iyi olan sayfa. Tablo varsa tablonun üstü, tablo, altı sırasıyla."""
    metin, gorunum = [], []

    def yazi_ekle(parca: str) -> None:
        if duz := md.satirlari_birlestir(parca):
            metin.append(duz)
            gorunum.append(html_okuma.duz_metinden_gorunum(duz))

    tablolar = _tablolari_bul(sayfa, o.bilgi[i])
    if not tablolar:
        yazi_ekle(sayfa.extract_text() or "")
        return metin, gorunum
    ust = 0.0
    for n, t in enumerate(tablolar):
        top, bottom = t.bbox[1], t.bbox[3]
        if top > ust + 1:
            yazi_ekle(sayfa.crop((0, ust, sayfa.width, min(top, sayfa.height))).extract_text() or "")
        _tablo_ekle(sayfa, t, i, n, o, metin, gorunum)
        ust = max(ust, bottom)
    if ust < sayfa.height - 1:
        yazi_ekle(sayfa.crop((0, ust, sayfa.width, sayfa.height)).extract_text() or "")
    return metin, gorunum


def _ocr_calistir(pdf_yolu: Path, sayfalar: list[int], acik: bool, paralel: int,
                  onbellek: _Onbellek) -> dict[int, str | Exception]:
    """Sayfaları OCR'lar (paralel), sonucu önbelleğe yazar. Önbellekte olanlar yeniden okunmaz. Okunamayan sayfanın
    sonucu hata nesnesidir; OCR kapalıysa önbellekte olmayan sayfa sonuçta hiç yer almaz."""
    sonuclar: dict[int, str | Exception] = {i: o for i in sayfalar if (o := onbellek.al(i)) is not None}
    bekleyen = [i for i in sayfalar if i not in sonuclar]
    if not (bekleyen and acik):
        return sonuclar
    pdf = pypdfium2.PdfDocument(str(pdf_yolu))
    try:
        with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=paralel) as havuz:
            isler: dict[int, Future] = {}

            def topla(bitene_kadar: bool) -> None:
                for no in [n for n in isler if bitene_kadar or isler[n].done()]:
                    try:
                        sonuclar[no] = isler.pop(no).result()
                        onbellek.koy(no, sonuclar[no])
                    except Exception as e:
                        sonuclar[no] = e
                    (Path(tmp) / f"{no}.png").unlink(missing_ok=True)

            for i in bekleyen:
                png = Path(tmp) / f"{i}.png"
                try:
                    _render(pdf, i, png, ocr.DPI)  # pdfium iş parçacığı güvenli değil, ana iş parçacığında
                except Exception as e:
                    sonuclar[i] = e
                    continue
                isler[i] = havuz.submit(ocr.goruntu_oku, png, ocr.SAYFA_ZAMAN_ASIMI, paralel > 1)
                # Diskte en fazla 2 x paralel sayfa görüntüsü dursun: en eski iş bitene kadar bekle.
                while len(isler) >= 2 * paralel:
                    isler[next(iter(isler))].exception()
                    topla(False)
            topla(True)
    finally:
        pdf.close()
    return sonuclar


def _cumle_satiri_mi(satir: str) -> bool:
    """En az 5 sözlük kelimesi olan ve küçük harfi büyük harfinden çok olan satır (başlık değil, cümle)."""
    if sum(c.islower() for c in satir) <= sum(c.isupper() for c in satir):
        return False
    return sum(1 for k in md._KELIME.findall(md.kucuk(satir)) if k in md.sozluk()) >= 5


def ocr_sinifi(sonuc: str) -> tuple[str, str | None]:
    """OCR sonucunun sınıfı ve uyarısı (çöp oranı ölçümüyle, GURULTULU_COP / GORSEL_COP):
    "gorsel"       harita, fotoğraf: yazı anlamsız, metne girmez, sadece arama; panelde sayfa görüntüsü
    "ocr_goruntu"  taranmış tablo, kroki, az yazılı ya da gürültülü sayfa: panelde görüntü, yazı metinde
    "ocr"          düz OCR metni"""
    cop, parca = md.cop_orani(sonuc)
    if not sonuc.strip() or cop >= GORSEL_COP or md.anlamsiz_mi(sonuc, en_az_kelime=20):
        return "gorsel", None
    if sum(1 for s in sonuc.split("\n") if s.count("|") >= 2) >= 5:
        return "ocr_goruntu", "taranmış tablo"
    # Az yazılı sayfa (kroki, şekil etiketi) görüntüyle gösterilir; içinde düzgün bir cümle satırı varsa (kısa karar
    # sayfası) metin olarak kalır. Büyük harfli başlık satırı ("MERSİN İLİ ... KOORDİNAT ÇİZELGESİ") cümle sayılmaz.
    if parca < AZ_YAZI and not any(_cumle_satiri_mi(s) for s in sonuc.split("\n")):
        return "ocr_goruntu", "az yazılı sayfa"
    if cop >= GURULTULU_COP:
        return "ocr_goruntu", f"OCR gürültülü (%{cop * 100:.0f})"
    return "ocr", None


def _okunamadi(o: _Okunus, i: int, yazi: str, sonuc: Exception | None) -> None:
    neden = "OCR yapılamadı (Tesseract yok)" if sonuc is None else f"OCR hatası: {sonuc}"
    o.bilgi[i]["yontem"] = "okunamadi"
    o.bilgi[i]["uyari"].append(neden[:200])
    o.kusurlar.append(f"s. {i + 1} okunamadı: {neden[:120]}")
    # Yazı katmanında az da olsa temiz yazı varsa o kalır.
    if len(yazi.strip()) >= YAZI_ESIGI and not md.kalinti_kusurlari(yazi):
        o.duz_sayfa(i, md.satirlari_birlestir(yazi))


def _ocr_sonuclarini_yaz(o: _Okunus, gereken: list[tuple[int, str]], sonuclar: dict[int, str | Exception]) -> None:
    goruntu_pdf = None
    try:
        for i, yazi in gereken:
            sonuc = sonuclar.get(i)
            if sonuc is None or isinstance(sonuc, Exception):
                _okunamadi(o, i, yazi, sonuc)
                continue
            sinif, uyari = ocr_sinifi(sonuc)
            bilgi = o.bilgi[i]
            bilgi["yontem"] = sinif
            if uyari:
                bilgi["uyari"].append(uyari)
            if sinif == "ocr":
                o.duz_sayfa(i, md.satirlari_birlestir(sonuc))
                continue
            yol = o.goruntu_yolu(i)
            if not (o.kok / yol).exists():
                goruntu_pdf = goruntu_pdf or pypdfium2.PdfDocument(str(o.pdf_yolu))
                _render(goruntu_pdf, i, o.kok / yol, GORUNTU_DPI)
            bilgi["goruntu"] = yol
            resim = _goruntu_html(yol, f"s. {i + 1}")
            if sinif == "gorsel":
                o.arama[i] = sonuc
                o.metin[i] = (f"[s. {i + 1}: görüntü olarak saklandı (harita, şekil ya da okunamayan tablo), "
                              "orijinal PDF'e bakınız]")
                o.gorunum[i] = [resim]
                continue
            metin = md.satirlari_birlestir(sonuc)
            o.duz_sayfa(i, metin)
            o.gorunum[i] = [resim, '<details class="ocr-metni"><summary>OCR ile okunan yazı</summary>'
                            + html_okuma.duz_metinden_gorunum(metin) + "</details>"]
    finally:
        if goruntu_pdf is not None:
            goruntu_pdf.close()


# Bir resim dosyasını (HTML sayfasındaki gövde resmi, ek içindeki resim) okur. Anlamsızsa (fotoğraf, harita) boş döner.
def resim_oku(yol: Path) -> tuple[str, str]:
    """(metin, durum). durum: "metin", "gorsel" (anlamsız/boş), "okunamadi"."""
    if not ocr.ocr_kullanilabilir():
        return "", "okunamadi"
    try:
        metin = ocr.goruntu_oku(yol)
    except Exception as e:
        log.warning("Resim okunamadı %s: %r", yol, e)
        return "", "okunamadi"
    if not metin.strip() or md.anlamsiz_mi(metin, en_az_kelime=15):
        return metin, "gorsel"
    return md.satirlari_birlestir(metin), "metin"


def ocr_onbellek_yolu(kok: Path, sha: str) -> Path:
    return kok / "onbellek" / "ocr" / f"{sha[:16]}.json"

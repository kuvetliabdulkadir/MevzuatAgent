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


def pdf_oku(pdf_yolu: Path, kok: Path, goruntu_klasoru: str, onbellek_yolu: Path | None = None,
            ocr_acik: bool | None = None, paralel: int | None = None,
            ilerle=lambda yapilan, toplam: None) -> PdfOkuma:
    """`kok` arşiv klasörü; sayfa ve tablo görüntüleri kok/goruntu_klasoru altına yazılır, yolları ona göre döner.
    `ocr_acik` verilmezse Tesseract varsa OCR yapılır."""
    if ocr_acik is None:
        ocr_acik = ocr.ocr_kullanilabilir()
    paralel = paralel or ocr.paralel_sayisi()
    onbellek = _Onbellek(onbellek_yolu)
    sayfa_metni: dict[int, str] = {}
    sayfa_gorunumu: dict[int, list[str]] = {}
    bilgiler: dict[int, dict] = {}
    arama: dict[int, str] = {}
    kusurlar: list[str] = []
    ocr_gereken: list[tuple[int, str]] = []

    with pdfplumber.open(str(pdf_yolu)) as pdf:
        sayfa_sayisi = len(pdf.pages)
        for i, sayfa in enumerate(pdf.pages):
            bilgi = {"no": i + 1, "yontem": "yazi", "karakter": 0, "uyari": [], "goruntu": None,
                     "tablo_yapi": 0, "tablo_goruntu": 0}
            bilgiler[i] = bilgi
            try:
                yazi = md.temizle(sayfa.extract_text() or "")
                sorun = yazi_sorunu(yazi, _resim_orani(sayfa))
                if sorun:
                    bilgi["uyari"].append(sorun)
                    ocr_gereken.append((i, yazi))
                    continue
                metin_parcalari, gorunum_parcalari = _yazili_sayfa(sayfa, i, kok, goruntu_klasoru, bilgi)
                sayfa_metni[i] = "\n".join(metin_parcalari)
                sayfa_gorunumu[i] = gorunum_parcalari
                bilgi["karakter"] = len(sayfa_metni[i])
            except Exception as e:  # tek sayfanın hatası belgeyi düşürmesin
                log.warning("PDF sayfası okunamadı %s s.%d: %r", pdf_yolu, i + 1, e)
                bilgi["uyari"].append(f"yazı katmanı okunamadı: {e!r}"[:200])
                ocr_gereken.append((i, ""))
            finally:
                if hasattr(sayfa, "close"):
                    sayfa.close()
            ilerle(i + 1, sayfa_sayisi)

    if ocr_gereken:
        _ocr_sayfalari(pdf_yolu, kok, goruntu_klasoru, ocr_gereken, ocr_acik, paralel, onbellek,
                       sayfa_metni, sayfa_gorunumu, bilgiler, arama, kusurlar)

    yontemler = {b["yontem"] for b in bilgiler.values()}
    okuyan = yontemler - {"okunamadi"}
    if not okuyan:
        okuma = "okunamadi"
    elif okuyan <= {"yazi"}:
        okuma = "pdf_metin"
    elif "yazi" in okuyan:
        okuma = "karisik"
    else:
        okuma = "ocr"
    sirali = range(sayfa_sayisi)
    metin = "\n\n".join(sayfa_metni[i] for i in sirali if sayfa_metni.get(i))
    gorunum = "\n".join(
        f'<section class="sayfa" data-sayfa="{i + 1}"><p class="sayfa-no">s. {i + 1}</p>' + "\n".join(sayfa_gorunumu.get(i, []))
        + "</section>" for i in sirali)
    for k in md.kalinti_kusurlari(metin):
        kusurlar.append(k)
    return PdfOkuma(metin=metin, gorunum=gorunum, okuma=okuma, sayfa_sayisi=sayfa_sayisi,
                    sayfalar=[bilgiler[i] for i in sirali], arama_metni="\n\n".join(arama[i] for i in sorted(arama)),
                    kusurlar=kusurlar)


def _yazili_sayfa(sayfa, i: int, kok: Path, goruntu_klasoru: str, bilgi: dict) -> tuple[list[str], list[str]]:
    """Yazı katmanı iyi olan sayfa. Tablo varsa tablonun üstü, tablo, altı sırasıyla."""
    tablolar = []
    cizgi = len(sayfa.lines) + len(sayfa.rects)
    if cizgi > TABLO_ARAMA_EN_FAZLA_CIZGI:
        # Vektör çizim (harita, kroki): tablo araması dakikalarca sürebiliyor ve tablo bulmuyor.
        bilgi["uyari"].append(f"çizim sayfası ({cizgi} çizgi), tablo aranmadı")
    elif cizgi >= 4:
        try:
            tablolar = sorted(sayfa.find_tables(), key=lambda t: t.bbox[1])
        except Exception as e:
            bilgi["uyari"].append(f"tablo bulunamadı: {e!r}"[:200])
    metin, gorunum = [], []

    def yazi_ekle(parca: str) -> None:
        duz = md.satirlari_birlestir(parca)
        if duz:
            metin.append(duz)
            gorunum.append(html_okuma.duz_metinden_gorunum(duz))

    if not tablolar:
        yazi_ekle(sayfa.extract_text() or "")
        return metin, gorunum
    ust = 0.0
    for n, t in enumerate(tablolar):
        x0, top, x1, bottom = t.bbox
        if top > ust + 1:
            yazi_ekle(sayfa.crop((0, ust, sayfa.width, min(top, sayfa.height))).extract_text() or "")
        satirlar = t.extract() or []
        satir_metni = [" | ".join(_hucre(h) for h in s if _hucre(h)) for s in satirlar]
        satir_metni = [md.paragraflari_duzenle(s) for s in satir_metni if s.strip()]
        if satir_metni:
            metin.append("\n".join(satir_metni))
        if _tablo_guvenilir(satirlar):
            gorunum.append(_tablo_html(satirlar))
            bilgi["tablo_yapi"] += 1
        else:
            yol = f"{goruntu_klasoru}/s{i + 1}-tablo{n + 1}.png"
            hedef = kok / yol
            if not hedef.exists():
                hedef.parent.mkdir(parents=True, exist_ok=True)
                sayfa.crop((max(0, x0 - 4), max(0, top - 4), min(sayfa.width, x1 + 4), min(sayfa.height, bottom + 4))) \
                    .to_image(resolution=GORUNTU_DPI).save(str(hedef))
            gorunum.append(_goruntu_html(yol, f"s. {i + 1} tablo {n + 1}", "tablo-goruntusu"))
            bilgi["tablo_goruntu"] += 1
        ust = max(ust, bottom)
    if ust < sayfa.height - 1:
        yazi_ekle(sayfa.crop((0, ust, sayfa.width, sayfa.height)).extract_text() or "")
    return metin, gorunum


def _ocr_sayfalari(pdf_yolu: Path, kok: Path, goruntu_klasoru: str, gereken: list[tuple[int, str]], ocr_acik: bool,
                   paralel: int, onbellek: _Onbellek, sayfa_metni: dict, sayfa_gorunumu: dict, bilgiler: dict,
                   arama: dict, kusurlar: list[str]) -> None:
    sonuclar: dict[int, str | Exception] = {}
    bekleyen = [(i, y) for i, y in gereken if onbellek.al(i) is None]
    for i, _ in gereken:
        if (o := onbellek.al(i)) is not None:
            sonuclar[i] = o
    if bekleyen and ocr_acik:
        pdf = pypdfium2.PdfDocument(str(pdf_yolu))
        try:
            with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=paralel) as havuz:
                isler: dict[int, Future] = {}

                def topla(bitene_kadar: bool) -> None:
                    for no in list(isler):
                        if bitene_kadar or isler[no].done():
                            try:
                                sonuclar[no] = isler.pop(no).result()
                                onbellek.koy(no, sonuclar[no])
                            except Exception as e:
                                sonuclar[no] = e
                            png = Path(tmp) / f"{no}.png"
                            png.unlink(missing_ok=True)

                for i, _ in bekleyen:
                    # Görüntü çevirme (pdfium) iş parçacığı güvenli değil, ana iş parçacığında yapılır.
                    png = Path(tmp) / f"{i}.png"
                    try:
                        _render(pdf, i, png, ocr.DPI)
                    except Exception as e:
                        sonuclar[i] = e
                        continue
                    isler[i] = havuz.submit(ocr.goruntu_oku, png, ocr.SAYFA_ZAMAN_ASIMI, paralel > 1)
                    # Bellekte/diskte en fazla 2 x paralel sayfa görüntüsü dursun.
                    while len(isler) >= 2 * paralel:
                        isler[next(iter(isler))].exception()  # en eski iş bitene kadar bekle
                        topla(False)
                topla(True)
        finally:
            pdf.close()

    goruntu_pdf = None
    try:
        for i, yazi in gereken:
            bilgi = bilgiler[i]
            sonuc = sonuclar.get(i)
            if sonuc is None or isinstance(sonuc, Exception):
                neden = "OCR yapılamadı (Tesseract yok)" if sonuc is None else f"OCR hatası: {sonuc}"
                bilgi["yontem"], bilgi["uyari"] = "okunamadi", bilgi["uyari"] + [neden[:200]]
                kusurlar.append(f"s. {i + 1} okunamadı: {neden[:120]}")
                if len(yazi.strip()) >= YAZI_ESIGI and not md.kalinti_kusurlari(yazi):
                    sayfa_metni[i] = md.satirlari_birlestir(yazi)
                    sayfa_gorunumu[i] = [html_okuma.duz_metinden_gorunum(sayfa_metni[i])]
                continue
            cop, parca = md.cop_orani(sonuc)
            tablo_gibi = sum(1 for s in sonuc.split("\n") if s.count("|") >= 2) >= 5
            gorsel = not sonuc.strip() or cop >= GORSEL_COP or md.anlamsiz_mi(sonuc, en_az_kelime=20)
            # Az yazılı sayfa (kroki, şekil etiketi) görüntüyle gösterilir; ama içinde düzgün bir cümle satırı varsa
            # (kısa karar sayfası) metin olarak kalır.
            cumleli = any(sum(1 for k in md._KELIME.findall(md.kucuk(satir)) if k in md.sozluk()) >= 5
                          for satir in sonuc.split("\n"))
            goruntulu = gorsel or cop >= GURULTULU_COP or tablo_gibi or (parca < AZ_YAZI and not cumleli)
            if goruntulu:
                yol = f"{goruntu_klasoru}/s{i + 1}.png"
                if not (kok / yol).exists():
                    if goruntu_pdf is None:
                        goruntu_pdf = pypdfium2.PdfDocument(str(pdf_yolu))
                    _render(goruntu_pdf, i, kok / yol, GORUNTU_DPI)
                bilgi["goruntu"] = yol
            if gorsel:
                # Harita, fotoğraf: OCR yazısı anlamsız, metne girmez, sadece arama.
                bilgi["yontem"] = "gorsel"
                arama[i] = sonuc
                sayfa_metni[i] = (f"[s. {i + 1}: görüntü olarak saklandı (harita, şekil ya da okunamayan tablo), "
                                  "orijinal PDF'e bakınız]")
                sayfa_gorunumu[i] = [_goruntu_html(yol, f"s. {i + 1}")]
                continue
            sayfa_metni[i] = md.satirlari_birlestir(sonuc)
            bilgi["karakter"] = len(sayfa_metni[i])
            if goruntulu:
                # Taranmış tablo, kroki, az yazılı sayfa: panelde sayfanın kendisi, OCR yazısı metinde (arama ve fark).
                bilgi["yontem"] = "ocr_goruntu"
                bilgi["uyari"].append(
                    "taranmış tablo" if tablo_gibi else "az yazılı sayfa" if parca < AZ_YAZI else f"OCR gürültülü (%{cop * 100:.0f})")
                sayfa_gorunumu[i] = [_goruntu_html(yol, f"s. {i + 1}"),
                                     '<details class="ocr-metni"><summary>OCR ile okunan yazı</summary>'
                                     + html_okuma.duz_metinden_gorunum(sayfa_metni[i]) + "</details>"]
                continue
            bilgi["yontem"] = "ocr"
            sayfa_gorunumu[i] = [html_okuma.duz_metinden_gorunum(sayfa_metni[i])]
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

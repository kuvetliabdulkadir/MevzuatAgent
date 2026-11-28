"""Rapora giren PDF belgenin tabloları ve okunamayan bölümlerinin görüntüsü. Kaynaktan bağımsız.

Rapor kalemi düz metindir; tablo satır satır yazıya, taranmış tablo ise OCR'da harf yığınına döner (EPDK kurul kararı,
RG 28.11.2026: işaret kutulu petrol türleri tablosu "Biodizel Türleri ,*|*|*<(«(«c«c"). Burada:
  1. Yazı katmanlı sayfadaki tablolar pdfplumber ile bulunur (pdf_okuma ile aynı kural). Güvenilir olan (sütun sayısı
     tutarlı, boş hücre az) satır/hücre olarak saklanır ve mailde/panelde gerçek tablo olur; güvenilmez olan sayfadan
     kesilip görüntü olur. Birden çok sayfada aynen tekrar eden tablo (sayfa başlığı) atlanır.
  2. OCR'lı sayfada metin_duzen.bozuk_satir_mi'ye takılan satır varsa sayfa bir kez daha Tesseract'tan satır
     konumlarıyla (TSV) geçer; bozuk satırların bulunduğu bölge (art arda olanlar tek bölge, üstte/altta cümle olmayan
     kısa satırlar dahil) sayfadan kesilip görüntü olur.
Görüntüler gri, 16 renk PNG (ölçüm: kesilmiş tablo 20-80 KB). Hata belge okumasını bozmaz, sadece görsel çıkmaz.
"""

import io
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pypdfium2
from PIL import Image

from mevzuat import metin_duzen as md, ocr

log = logging.getLogger(__name__)

EN_FAZLA_GORSEL = 6  # belge başına
EN_FAZLA_SAYFA = 40  # tablo aranan sayfa (OCR zaten 30 sayfada kesiliyor)
EN_FAZLA_SATIR = 60  # saklanan tablo satırı, fazlası "N satır daha"
GORUNTU_GENISLIGI = 1000  # piksel, mail okunur kalsın, şişmesin
OCR_DPI = 300
TABLO_DPI = 150
PAY = 6  # kesilen bölgenin kenar payı (300 DPI piksel)
BIRLESME_PAYI = 60  # bu kadar yakın iki bölge birleşir (300 DPI piksel, 0,2 inç)


@dataclass
class Gorsel:
    sayfa: int  # 1'den
    tur: str  # "tablo" / "goruntu"
    aciklama: str
    tablo: list[list] | None = None
    png: bytes | None = None
    genislik: int | None = None
    yukseklik: int | None = None


# Gösterim boyutu belgedeki gerçek boyuttur (inç başına 92 piksel: A4 yazı alanı ~600 piksel); dar tablo geniş
# görünmez, bütün görüntüler aynı yazı büyüklüğünde. PNG'nin kendisi en fazla GORUNTU_GENISLIGI (net kalsın).
GOSTERIM_PPI = 92


def _png(gorsel: Image.Image, dpi: int) -> tuple[bytes, int, int]:
    gosterim = (round(gorsel.width * GOSTERIM_PPI / dpi), round(gorsel.height * GOSTERIM_PPI / dpi))
    g = gorsel.convert("L")
    if g.width > GORUNTU_GENISLIGI:
        g = g.resize((GORUNTU_GENISLIGI, round(g.height * GORUNTU_GENISLIGI / g.width)), Image.LANCZOS)
    g = g.quantize(colors=16)
    tampon = io.BytesIO()
    g.save(tampon, format="PNG", optimize=True)
    return tampon.getvalue(), *gosterim


def _hucre(deger) -> str | None:
    return None if deger is None else " ".join(str(deger).split())


# --- yazı katmanlı sayfalar: tablolar --------------------------------------------------------------

def _tablo_imzasi(satirlar: list[list]) -> str:
    return "\x1f".join("\x1e".join(_hucre(h) or "" for h in s) for s in satirlar)


def _yazili_tablolar(pdf: pdfplumber.PDF, sayfalar: list[int]) -> list[Gorsel]:
    from mevzuat.pdf_okuma import _tablo_guvenilir, _tablolari_bul

    bulunan: list[tuple[int, object, list[list]]] = []
    imza_sayisi: dict[str, int] = {}
    for i in sayfalar:
        sayfa = pdf.pages[i]
        for t in _tablolari_bul(sayfa, {"uyari": []}):
            satirlar = t.extract() or []
            if len(satirlar) < 2 or max((len(s) for s in satirlar), default=0) < 2:
                continue
            imza = _tablo_imzasi(satirlar)
            imza_sayisi[imza] = imza_sayisi.get(imza, 0) + 1
            bulunan.append((i, t, satirlar))
    sonuc = []
    for i, t, satirlar in bulunan:
        if imza_sayisi[_tablo_imzasi(satirlar)] > 1:
            continue  # her sayfada tekrar eden başlık kutusu
        n = sum(1 for g in sonuc if g.sayfa == i + 1) + 1
        if _tablo_guvenilir(satirlar):
            fazla = len(satirlar) - EN_FAZLA_SATIR
            sonuc.append(Gorsel(i + 1, "tablo", f"s. {i + 1} tablo {n}" + (f" (ilk {EN_FAZLA_SATIR} satır, {fazla} satır "
                                "daha ekteki belgede)" if fazla > 0 else ""),
                                tablo=[[_hucre(h) for h in s] for s in satirlar[:EN_FAZLA_SATIR]]))
        else:
            sayfa = pdf.pages[i]
            x0, top, x1, bottom = t.bbox
            kutu = (max(0, x0 - 4), max(0, top - 4), min(sayfa.width, x1 + 4), min(sayfa.height, bottom + 4))
            png, gen, yuk = _png(sayfa.crop(kutu).to_image(resolution=TABLO_DPI).original, TABLO_DPI)
            sonuc.append(Gorsel(i + 1, "goruntu", f"s. {i + 1} tablo {n}", png=png, genislik=gen, yukseklik=yuk))
    return sonuc


# --- OCR'lı sayfalar: okunamayan bölgeler ----------------------------------------------------------

@dataclass
class _Satir:
    metin: str
    sol: int
    ust: int
    sag: int
    alt: int


def _tsv_satirlari(goruntu: Path) -> list[_Satir]:
    # "tsv" ayar dosyası her tessdata klasöründe yok (tessdata_best), ayar doğrudan verilir.
    komut = ocr._komut(str(goruntu)) + ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
    sonuc = subprocess.run(komut, capture_output=True, timeout=ocr.SAYFA_ZAMAN_ASIMI)
    if sonuc.returncode != 0:
        raise ocr.OcrHatasi(sonuc.stderr.decode("utf-8", "replace")[:300])
    satirlar: dict[tuple, _Satir] = {}
    for satir in sonuc.stdout.decode("utf-8", "replace").splitlines()[1:]:
        p = satir.split("\t")
        if len(p) < 12 or p[0] != "5" or not p[11].strip():
            continue
        anahtar = (int(p[2]), int(p[3]), int(p[4]))
        sol, ust, gen, yuk = (int(x) for x in p[6:10])
        if (s := satirlar.get(anahtar)) is None:
            satirlar[anahtar] = _Satir(p[11], sol, ust, sol + gen, ust + yuk)
        else:
            s.metin += " " + p[11]
            s.sol, s.ust, s.sag, s.alt = min(s.sol, sol), min(s.ust, ust), max(s.sag, sol + gen), max(s.alt, ust + yuk)
    return sorted(satirlar.values(), key=lambda s: (s.ust, s.sol))


def _kisa_gurultu(metin: str) -> bool:
    s = metin.strip()
    return len(s) <= 3 and not any(c.isdigit() for c in s)


# Bölgeye komşu, cümle olmayan kısa satır (tablonun başlık satırı ya da son satırı): "HAMPETROr..,....,.. | |".
def _tablo_kenari(metin: str) -> bool:
    s = metin.strip()
    return len(s) < 45 and not s.endswith((".", ":", ";"))


def bozuk_bolgeler(satirlar: list[_Satir]) -> list[tuple[int, int]]:
    """Bozuk satırların [ilk, son] dizinleri; aradaki kısa gürültü satırları bölgeye dahil, üst ve altta en fazla iki
    tablo kenarı satırı eklenir. Bozuk satırı olmayan bölge yoktur."""
    bozuk = [md.bozuk_satir_mi(md.noktalama_yiginsiz(s.metin)) for s in satirlar]
    bolgeler: list[list[int]] = []
    for i, b in enumerate(bozuk):
        if not b:
            continue
        if bolgeler and all(_kisa_gurultu(satirlar[j].metin) for j in range(bolgeler[-1][1] + 1, i)):
            bolgeler[-1][1] = i
        else:
            bolgeler.append([i, i])
    for bolge in bolgeler:
        for _ in range(2):
            j = bolge[0] - 1
            if j >= 0 and not bozuk[j] and _tablo_kenari(satirlar[j].metin):
                bolge[0] = j
        for _ in range(2):
            j = bolge[1] + 1
            if j < len(satirlar) and not bozuk[j] and _tablo_kenari(satirlar[j].metin):
                bolge[1] = j
    # Genişleme iki bölgeyi birleştirebilir.
    birlesik: list[list[int]] = []
    for b in bolgeler:
        if birlesik and b[0] <= birlesik[-1][1] + 1:
            birlesik[-1][1] = max(birlesik[-1][1], b[1])
        else:
            birlesik.append(b)
    return [(a, b) for a, b in birlesik]


# Taranmış sayfadaki çizgili tablo: uzun yatay çizgiler. Görüntü yatayda 1/16'ya küçültülür (kutu ortalaması),
# çizgi üstündeki hücreler koyu kalır, yazı açılır. Satırın en uzun koyu dizisi metin genişliğinin %30'unu geçiyorsa
# çizgidir. Aralarında en fazla 0,6 inç olan en az 3 çizgi, en az 2 dikey çizgiyle bir tablodur (başlık altındaki
# tek çizgi, çift çizgi, altı çizili paragraf değil).
CIZGI_ADIMI = 16
CIZGI_ORANI = 0.30
CIZGI_ARALIGI = int(OCR_DPI * 0.6)
EN_AZ_CIZGI = 3


def cizgili_tablolar(resim: Image.Image) -> list[tuple[int, int, int, int]]:
    """(sol, üst, sağ, alt) piksel kutuları. Sayfa çerçevesi gibi sayfanın %75'inden yüksek bölge tablo sayılmaz."""
    gri = resim.convert("L").point(lambda p: 255 if p < 128 else 0)
    kucuk = gri.resize((max(1, gri.width // CIZGI_ADIMI), gri.height), Image.BOX)
    gen = kucuk.width
    veri = kucuk.tobytes()
    cizgiler: list[tuple[int, int, int]] = []  # (y, sol, sağ) küçük ölçekte x
    for y in range(kucuk.height):
        satir = veri[y * gen:(y + 1) * gen]
        en_uzun, bas, sayac, en_bas = 0, 0, 0, 0
        for x, p in enumerate(satir):
            if p >= 200:
                if sayac == 0:
                    bas = x
                sayac += 1
                if sayac > en_uzun:
                    en_uzun, en_bas = sayac, bas
            else:
                sayac = 0
        if en_uzun >= gen * CIZGI_ORANI:
            cizgiler.append((y, en_bas, en_bas + en_uzun))
    # Kalın çizginin art arda satırları tek çizgi.
    tekil: list[tuple[int, int, int]] = []
    for c in cizgiler:
        if tekil and c[0] - tekil[-1][0] <= 6:
            tekil[-1] = (c[0], min(tekil[-1][1], c[1]), max(tekil[-1][2], c[2]))
        else:
            tekil.append(c)
    gruplar: list[list[tuple[int, int, int]]] = []
    for c in tekil:
        if gruplar and c[0] - gruplar[-1][-1][0] <= CIZGI_ARALIGI:
            gruplar[-1].append(c)
        else:
            gruplar.append([c])
    kutular = []
    for g in gruplar:
        if len(g) < EN_AZ_CIZGI:
            continue
        ust, alt = g[0][0], g[-1][0]
        if alt - ust > resim.height * 0.75:
            continue
        sol, sag = min(c[1] for c in g) * CIZGI_ADIMI, max(c[2] for c in g) * CIZGI_ADIMI
        kutu = (sol, ust, min(resim.width, sag + CIZGI_ADIMI), alt)
        if _dikey_cizgi_sayisi(gri, kutu) >= EN_AZ_DIKEY:
            kutular.append(kutu)
    return kutular


# Altı çizili satırlar da art arda yatay çizgi verir (AYM kararı, RG 28.11.2026); tablo ayrıca dikey çizgi ister:
# kutunun yüksekliğinin en az yarısı boyunca kesintisiz koyu giden sütun. Taramadaki eğiklik çizgiyi yan sütuna
# kaydırır, o yüzden 8 piksellik sütun grupları ve 4 satırlık kesinti hoş görülür. Yazı satırları arasındaki boşluk
# bundan büyüktür, yazı dikey çizgi gibi görünmez.
EN_AZ_DIKEY = 2
DIKEY_ORANI = 0.5
DIKEY_GRUP = 8
DIKEY_KESINTI = 4


def _dikey_cizgi_sayisi(gri: Image.Image, kutu: tuple[int, int, int, int]) -> int:
    sol, ust, sag, alt = kutu
    parca = gri.crop((max(0, sol - DIKEY_GRUP), ust, min(gri.width, sag + DIKEY_GRUP), alt + 1))
    if parca.height < 2:
        return 0
    gen = max(1, parca.width // DIKEY_GRUP)
    veri = parca.resize((gen, parca.height), Image.BOX).tobytes()
    gereken = parca.height * DIKEY_ORANI
    sayi, onceki = 0, False
    for x in range(gen):
        en_uzun = sayac = bosluk = 0
        for y in range(parca.height):
            if veri[y * gen + x] >= 255 // DIKEY_GRUP:  # grupta en az bir koyu piksel
                sayac += bosluk + 1 if sayac else 1
                bosluk = 0
                en_uzun = max(en_uzun, sayac)
            elif sayac:
                bosluk += 1
                if bosluk > DIKEY_KESINTI:
                    sayac = bosluk = 0
        cizgi = en_uzun >= gereken
        if cizgi and not onceki:
            sayi += 1
        onceki = cizgi
    return sayi


def _ust_uste(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _ocr_bolgeleri(veri: bytes, sayfalar: list[int], tsv_sayfalari: set[int]) -> list[Gorsel]:
    """Her OCR sayfasında çizgili tablolar; `tsv_sayfalari`nda ayrıca bozuk satır bölgeleri (satır konumları TSV'den).
    Üst üste binen bölgeler birleşir."""
    sonuc: list[Gorsel] = []
    pdf = pypdfium2.PdfDocument(veri)
    try:
        with tempfile.TemporaryDirectory() as klasor:
            for i in sayfalar:
                resim = pdf[i].render(scale=OCR_DPI / 72).to_pil()
                # (üst, alt, sol, sağ, tablo mu)
                bolgeler = [(u, a_, s_, g, True) for s_, u, g, a_ in cizgili_tablolar(resim)]
                if i in tsv_sayfalari and ocr.ocr_kullanilabilir():
                    yol = Path(klasor) / f"s{i + 1}.png"
                    resim.save(yol)
                    if satirlar := _tsv_satirlari(yol):
                        sol, sag = min(s.sol for s in satirlar), max(s.sag for s in satirlar)
                        for a, b in bozuk_bolgeler(satirlar):
                            bolgeler.append((min(s.ust for s in satirlar[a:b + 1]), max(s.alt for s in satirlar[a:b + 1]),
                                             sol, sag, False))
                birlesik: list[list] = []
                for b in sorted(bolgeler):
                    # Üst üste binen ya da arası 0,2 inçten az bölgeler (aynı tablonun parçaları) tek görüntü.
                    if birlesik and _ust_uste((b[0] - BIRLESME_PAYI, b[1]), (birlesik[-1][0], birlesik[-1][1])):
                        o = birlesik[-1]
                        birlesik[-1] = [o[0], max(o[1], b[1]), min(o[2], b[2]), max(o[3], b[3]), o[4] or b[4]]
                    else:
                        birlesik.append(list(b))
                for n, (ust, alt, sol, sag, tablo) in enumerate(birlesik, start=1):
                    kutu = (max(0, sol - PAY), max(0, ust - PAY), min(resim.width, sag + PAY), min(resim.height, alt + PAY))
                    png, gen, yuk = _png(resim.crop(kutu), OCR_DPI)
                    ad = "tablo (taranmış)" if tablo else "okunamayan bölüm (taranmış tablo ya da görsel)"
                    sonuc.append(Gorsel(i + 1, "goruntu", f"s. {i + 1} {ad}" + (f" {n}" if len(birlesik) > 1 else ""),
                                        png=png, genislik=gen, yukseklik=yuk))
    finally:
        pdf.close()
    return sonuc


def cikar(veri: bytes, ocr_sayfalari: dict[int, str] | None = None) -> list[Gorsel]:
    """`ocr_sayfalari` OCR'la okunmuş sayfaların metni (0'dan sayfa no). Taranmış sayfalarda çizgili tablolar aranır;
    bozuk satırı olan sayfalar ayrıca satır konumlarıyla (TSV) yeniden okunur. Verilmezse (sonradan doldurma) günlük
    okuyucunun OCR'a gönderdiği sayfalar alınır (icerik.ocr_secimi) ve hepsi TSV ile okunur."""
    from mevzuat.icerik import OCR_SAYFA_SINIRI, ocr_secimi

    try:
        if ocr_sayfalari is None:
            taranmis = ocr_secimi(veri)[1][:OCR_SAYFA_SINIRI] if ocr.ocr_kullanilabilir() else []
            tsv = set(taranmis)
        else:
            taranmis = sorted(ocr_sayfalari)
            tsv = {i for i in taranmis
                   if any(md.bozuk_satir_mi(md.noktalama_yiginsiz(s)) for s in ocr_sayfalari[i].splitlines())}
        with pdfplumber.open(io.BytesIO(veri)) as pdf:
            yazili = [i for i in range(min(len(pdf.pages), EN_FAZLA_SAYFA)) if i not in set(taranmis)]
            gorseller = _yazili_tablolar(pdf, yazili)
        if taranmis:
            gorseller += _ocr_bolgeleri(veri, taranmis[:EN_FAZLA_SAYFA], tsv)
    except Exception:
        log.exception("Belgenin tablo/görselleri çıkarılamadı")
        return []
    gorseller.sort(key=lambda g: g.sayfa)
    return gorseller[:EN_FAZLA_GORSEL]

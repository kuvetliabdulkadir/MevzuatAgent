"""Metin düzeni, kaynaktan bağımsız. Her okuyucunun (HTML, PDF, OCR, ek dosyası) çıktısı buradan geçer.

Ne yapar:
  - NFC'ye çevirir (aynı harf iki farklı kodlamayla gelmesin), görünmez kontrol karakterlerini atar.
  - Satır birleştirme: PDF ve OCR metninde satır sonu sayfanın düzeninden gelir, cümlenin ortasında kırılır
    ("zararının sorumlulardan\\ntahsiline"). Cümle ortasındaki kırılmalar boşluk olur, MADDE, (1), a) gibi yapı
    satırları ve cümle sonları yeni paragraf kalır.
  - Satır sonunda tireyle bölünmüş kelimeyi birleştirir ("ka-\\nyıtlı" → "kayıtlı"), birleşik hali sözlükte yoksa
    tireyi korur ("Ar-\\nGe" → "Ar-Ge").
  - Kelime içi kopmaları onarır ("Bankam ı zca" → "Bankamızca", "B İ R İ N C İ" → "BİRİNCİ"). Sadece parçalar
    birleşince sözlükte olan bir kelime çıkıyorsa birleştirilir.
  - Karşılaştırma metni: sürüm farkı ve değişiklik kararı satır düzenine bakmasın diye boşluklar normalleştirilir.

Sözlük (veri/sozluk.txt.gz) arşivdeki HTML metinlerden üretildi: en az 3 belgede geçen 104 bin kelime, küçük harf.
Belirli bir siteye ait kural yoktur, kurallar sadece metnin kendisine bakar.
"""

import gzip
import re
import unicodedata
from functools import cache
from pathlib import Path

SOZLUK_YOLU = Path(__file__).parent / "veri" / "sozluk.txt.gz"
HARF = "a-zçğıöşüâîû"
_KELIME = re.compile(rf"[{HARF}]{{3,}}")
_KONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‍﻿]")
# Yeni paragrafa başlayan yapı satırları (madde, fıkra, bent, bölüm başlıkları, liste işaretleri).
_YAPI_BASI = re.compile(
    rf"^(?:(?:EK|Ek|GEÇİCİ|Geçici|MÜKERRER|Mükerrer)\s+)?(?:MADDE|Madde)\s+\d"
    rf"|^\(\d+[a-zA-Z]?\)|^[{HARF}]\)\s|^[A-ZÇĞİÖŞÜ]\)\s|^\d+[.)]\s|^[-–—•·*]\s"
    rf"|^(?:BİRİNCİ|İKİNCİ|ÜÇÜNCÜ|DÖRDÜNCÜ|BEŞİNCİ|ALTINCI|YEDİNCİ|SEKİZİNCİ|DOKUZUNCU|ONUNCU)\s+(?:BÖLÜM|KISIM)"
)


@cache
def sozluk() -> frozenset[str]:
    with gzip.open(SOZLUK_YOLU, "rt", encoding="utf-8") as f:
        return frozenset(f.read().split())


# Türkçe küçük harf (I → ı, İ → i). str.lower() "I" harfini "i" yapıyor.
def kucuk(metin: str) -> str:
    return metin.replace("I", "ı").replace("İ", "i").lower()


def sozlukte_mi(kelime: str) -> bool:
    return kucuk(kelime) in sozluk()


# Metindeki 3+ harfli kelimelerin ne kadarı sözlükte (0-1). Kelime yoksa None.
def sozluk_orani(metin: str) -> float | None:
    kelimeler = _KELIME.findall(kucuk(metin))
    if not kelimeler:
        return None
    s = sozluk()
    return sum(k in s for k in kelimeler) / len(kelimeler)


# Sayı ve harf dışı işaretler hariç, karakterlerin ne kadarı rakam. Sayı tabloları (koordinat, tutar) bununla ayrılır.
def rakam_orani(metin: str) -> float:
    dolu = [c for c in metin if not c.isspace()]
    return sum(c.isdigit() for c in dolu) / len(dolu) if dolu else 0.0


# Okunan metin anlamlı Türkçe mi. Harita ve fotoğraf OCR'ı anlamsız harf yığını verir, sayı tabloları ise anlamlı sayılır.
def anlamsiz_mi(metin: str, en_az_kelime: int = 20, esik: float = 0.4) -> bool:
    kelimeler = _KELIME.findall(kucuk(metin))
    if len(kelimeler) < en_az_kelime:
        return False
    if rakam_orani(metin) > 0.3:
        return False
    oran = sum(k in sozluk() for k in kelimeler) / len(kelimeler)
    return oran < esik


_NOKTALAMA = ".,;:()[]|\"“”‘’!?—–-*«»/\\'`©®°<>=+_~^%&$#@{}…•·"


# Metindeki parçaların (boşlukla ayrılmış) ne kadarı ne sözlükte kelime ne de sayı. Temiz OCR'da 0,10'un altında,
# taranmış tablo ve haritada 0,15-0,35 (20.5.9419, 20.5.5085 ölçümü). (oran, parça sayısı)
def cop_orani(metin: str) -> tuple[float, int]:
    parcalar = [p.strip(_NOKTALAMA) for p in metin.split()]
    parcalar = [p for p in parcalar if p]
    if not parcalar:
        return 0.0, 0
    s = sozluk()
    iyi = sum(1 for p in parcalar if any(c.isdigit() for c in p) or kucuk(p) in s)
    return 1 - iyi / len(parcalar), len(parcalar)


# NFC, kontrol karakterleri, yedek karakter dışında temel temizlik. Satır yapısına dokunmaz.
def temizle(metin: str) -> str:
    metin = unicodedata.normalize("NFC", metin or "")
    metin = metin.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").replace("­", "")
    return _KONTROL.sub("", metin)


# --- kelime onarımı -----------------------------------------------------------------------------------

_TEK_HARF = re.compile(rf"^[{HARF}]$", re.IGNORECASE)
# "B İ R İ N C İ" gibi harf harf aralıklı yazılmış kelime (en az 4 harf).
_ARALIKLI = re.compile(r"(?<!\S)(?:[A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû] ){3,}[A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû](?!\S)")


# Boşluksuz harf dizisini sözlük kelimelerine böler ("birincikısım" → ["birinci", "kısım"]). Bölünemezse None.
def _sozlukle_bol(harfler: str) -> list[str] | None:
    s, k = sozluk(), kucuk(harfler)
    n = len(k)
    if n > 60:
        return None
    # en_iyi[i]: harfler[:i] için en az kelimeli bölme.
    en_iyi: list[list[int] | None] = [None] * (n + 1)
    en_iyi[0] = []
    for i in range(1, n + 1):
        for j in range(max(0, i - 25), i - 1):
            if en_iyi[j] is not None and k[j:i] in s:
                aday = en_iyi[j] + [i]
                if en_iyi[i] is None or len(aday) < len(en_iyi[i]):
                    en_iyi[i] = aday
    if en_iyi[n] is None:
        return None
    sinirlar = [0] + en_iyi[n]
    return [harfler[a:b] for a, b in zip(sinirlar, sinirlar[1:])]


def _aralikli_birlestir(satir: str) -> str:
    def degistir(m: re.Match) -> str:
        birlesik = m.group(0).replace(" ", "")
        if sozlukte_mi(birlesik):
            return birlesik
        if kelimeler := _sozlukle_bol(birlesik):
            return " ".join(kelimeler)
        return birlesik if birlesik.isupper() else m.group(0)
    return _ARALIKLI.sub(degistir, satir)


# Bir satırdaki kelime içi kopmaları onarır ("kar şı l ığı nda" → "karşılığında").
def kelimeleri_onar(satir: str) -> str:
    satir = _aralikli_birlestir(satir)
    parcalar = satir.split(" ")
    if len(parcalar) < 2:
        return satir
    s = sozluk()
    sonuc: list[str] = []
    i = 0
    while i < len(parcalar):
        birlesti = False
        # En uzun birleşmeyi önce dene (6 parçaya kadar, "kar şı l ığı nda").
        for n in (6, 5, 4, 3, 2):
            grup = parcalar[i:i + n]
            if len(grup) < n or any(not g for g in grup):
                continue
            # Parçaların sonundaki noktalama birleşikte de sonda kalsın; ortadaki parçalar sadece harf olmalı.
            govde = grup[:-1] + [re.sub(rf"[^{HARF}A-ZÇĞİÖŞÜÂÎÛ]+$", "", grup[-1])]
            kuyruk = grup[-1][len(govde[-1]):]
            if not all(re.fullmatch(rf"[{HARF}A-ZÇĞİÖŞÜÂÎÛ]+", g) for g in govde):
                continue
            birlesik = "".join(govde)
            if kucuk(birlesik) not in s:
                continue
            # İlk parça kendi başına bir kelimeyse ve geri kalanı da birleşince kelime oluyorsa ilk parça ayrı kalır
            # ("en ç ok" → "en çok", "ençok" değil). Sıradaki turda geri kalanı birleşir.
            if len(govde[0]) > 1 and kucuk(govde[0]) in s and kucuk("".join(govde[1:])) in s:
                continue
            tek_harf_var = any(len(g) == 1 for g in govde)
            sozlukte_olmayan = any(kucuk(g) not in s for g in govde)
            # "o", "a" gibi tek başına da kelime olan harfler gruba tek harf olarak girerse yine de sözlük şartı var.
            if tek_harf_var or (n == 2 and sozlukte_olmayan and all(kucuk(g) not in s for g in govde if len(g) > 2)):
                sonuc.append(birlesik + kuyruk)
                i += n
                birlesti = True
                break
        if not birlesti:
            sonuc.append(parcalar[i])
            i += 1
    return " ".join(sonuc)


# --- satır birleştirme ----------------------------------------------------------------------------------

def _yapi_satiri(satir: str) -> bool:
    return bool(_YAPI_BASI.match(satir))


# Satır tamamen büyük harfli kısa bir başlık mı ("AMAÇ, KAPSAM VE TANIMLAR").
def _baslik_satiri(satir: str) -> bool:
    harfler = [c for c in satir if c.isalpha()]
    return 3 <= len(harfler) and len(satir) <= 120 and all(c.isupper() for c in harfler)


# PDF ve OCR metninde sayfa düzeninden gelen satır sonlarını birleştirir, paragrafları "\n" ile ayırır.
def satirlari_birlestir(metin: str) -> str:
    """Bloklar ("\\n\\n" ile ayrılmış sayfalar/paragraflar) ayrı kalır. Bir satır küçük harf ya da virgülle bitiyor ve
    sonraki satır küçük harfle başlıyorsa aynı cümlenin devamıdır, boşlukla birleşir. Tire ile biten satırda kelime
    birleştirilir. Yapı satırları (MADDE, (1), a), başlıklar) her zaman yeni paragraftır. Tablo satırları (sayıyla biten)
    birleştirilmez."""
    bloklar = []
    for blok in re.split(r"\n\s*\n", temizle(metin)):
        satirlar = [re.sub(r"[ \t]+", " ", s).strip() for s in blok.split("\n")]
        satirlar = [s for s in satirlar if s]
        paragraflar: list[str] = []
        for satir in satirlar:
            if not paragraflar:
                paragraflar.append(satir)
                continue
            onceki = paragraflar[-1]
            yeni_paragraf = _yapi_satiri(satir) or _baslik_satiri(satir) or _baslik_satiri(onceki)
            if not yeni_paragraf and re.search(rf"[{HARF}A-ZÇĞİÖŞÜÂÎÛ]-$", onceki) and re.match(rf"[{HARF}A-ZÇĞİÖŞÜÂÎÛ]", satir):
                # Tireyle bölünmüş kelime: birleşik hali sözlükteyse tire düşer, değilse ("Ar-Ge") tire kalır.
                son_kelime = re.search(r"(\S+)-$", onceki).group(1)
                ilk_kelime = re.match(r"\S+", satir).group(0)
                ilk_govde = re.sub(rf"[^{HARF}A-ZÇĞİÖŞÜ]+$", "", ilk_kelime)
                if ilk_govde[:1].islower() and sozlukte_mi(son_kelime + ilk_govde):
                    paragraflar[-1] = onceki[:-1] + satir
                else:
                    paragraflar[-1] = onceki + satir
                continue
            if not yeni_paragraf and re.search(rf"[{HARF},;]$", kucuk(onceki)) and re.match(rf"[{HARF}(]", satir):
                paragraflar[-1] = onceki + " " + satir
                continue
            paragraflar.append(satir)
        bloklar.append("\n".join(kelimeleri_onar(p) for p in paragraflar))
    return "\n\n".join(b for b in bloklar if b)


# HTML'den paragraf paragraf gelmiş metni düzenler (satır birleştirme gerekmez, kelime onarımı ve temizlik yeter).
def paragraflari_duzenle(metin: str) -> str:
    satirlar = (re.sub(r"[ \t]+", " ", s).strip() for s in temizle(metin).split("\n"))
    return "\n".join(kelimeleri_onar(s) for s in satirlar if s)


# --- karşılaştırma ----------------------------------------------------------------------------------------

# Sürüm farkı ve "değişti mi" kararı için metin. Satır düzeni ve boşluk farkları önemsizdir.
def karsilastirma_metni(metin: str) -> str:
    return " ".join(temizle(metin).split())


# Metinde kalan okuma kusurları: (cid:N), yedek karakter. Teslim kontrolü ve sürüm kalitesi kullanır.
def kalinti_kusurlari(metin: str) -> list[str]:
    kusurlar = []
    if n := len(re.findall(r"\(cid:\d+\)", metin)):
        kusurlar.append(f"{n} (cid:N) kalıntısı")
    if n := metin.count("�"):
        kusurlar.append(f"{n} yedek karakter (U+FFFD)")
    return kusurlar

"""Türkçe ek kuralı: aranan kökten sonra sadece Türkçe ek gelebilir. Filtre ve arama ortak kullanır (karar 10, 14, 17).

Eski kural kelimenin başını arıyordu, sonu serbestti: "altın" → "altınbaş", "Gümüşhane", "altınordu" da eşleşiyordu
(19.265 başlıkta "Kıymetli madenler" 73 eşleşme, ~50'si ilgisiz). Burada kökten sonra gelen harfler, Türkçe ek sırasına
uyan eklere bölünebiliyorsa eşleşme sayılır:

    yapım eki (en fazla 3) → çoğul → iyelik → hal ("-daki" dahil) → "-ki" → ek-fiil

"altının" (ın), "kuyumculuk" (cu+luk), "vergilendirilmesi" (lendir+il+me+si) eşleşir. "altınbaş", "gümüşhane",
"altınordu" eşleşmez, çünkü "baş", "hane", "ordu" ek değildir. "yayla" iki hal eki ("ya"+"yla") olduğu için eşleşmez.
Kökün son harfi p/ç/t/k ise yumuşamış hali de denenir ("harç" → "harcı"). Kesme işareti olabilir ("KDV'nin").
Aranan ifadenin sonuna "*" yazılırsa eski kural geçerlidir (başı tutsun yeter, bilinçli kök araması).
Çok kelimeli ifadede ek sadece son kelimeye gelir ("katma değer vergisinin").
"""

import re
from functools import cache, lru_cache

HARF = "a-z0-9çğıöşüâîû"
_KESME = "'’`´"

# Ek envanteri, yuvalara göre (sıra yukarıdaki gibi). Ünlü uyumu ve ünsüz benzeşmesi biçimleri ayrı ayrı yazılı.
_DORTLU = ("ı", "i", "u", "ü")
_IKILI = ("a", "e")


def _dort(kalip: str) -> list[str]:
    return [kalip.replace("I", u) for u in _DORTLU]


def _iki(kalip: str) -> list[str]:
    return [kalip.replace("A", u) for u in _IKILI]


def _ikiden(*kaliplar: str) -> list[str]:
    """Kalıptaki A (a/e) ve I (ı/i/u/ü) yerlerine bütün ünlüleri koyar ("lAndIr" → landır, lendir, ... 8 biçim)."""
    sonuc = []
    for k in kaliplar:
        for a in _iki(k) if "A" in k else [k]:
            sonuc += _dort(a) if "I" in a else [a]
    return sonuc


YAPIM = set(_ikiden(
    "lIk", "lI", "sIz", "cI", "çI", "lAş", "lAştIr", "lAn", "lAndIr", "lAt", "sAl", "Il", "In", "n",
    "dIr", "tIr", "t", "Ar", "Ir", "r", "mA", "mAk", "Iş", "ş", "IcI", "yIcI", "Im", "Ik", "gI", "kI", "gIn",
    "lAm", "sA", "cA", "msI", "Imsa", "AcAk", "yAcAk", "dIk", "tIk", "mIş", "Iver",
))
COGUL = set(_iki("lAr"))
IYELIK = set(_ikiden("Im", "m", "In", "n", "I", "sI", "ImIz", "mIz", "InIz", "nIz", "lArI"))
HAL = set(_ikiden("I", "yI", "nI", "A", "yA", "nA", "dA", "tA", "ndA", "dAn", "tAn", "ndAn", "In", "nIn", "yIn",
                  "lA", "ylA", "cA", "çA", "ncA", "dAkI", "tAkI", "ndAkI"))
KI = {"ki", "kü"}
EKFIIL = set(_ikiden("dIr", "tIr", "dIrlAr", "tIrlAr", "ydI", "dI", "tI", "ymIş", "mIş", "ysA", "sA"))
# Yuvalar: (ekler, en fazla kaç kez).
YUVALAR = ((YAPIM, 3), (COGUL, 1), (IYELIK, 1), (HAL, 1), (KI, 1), (EKFIIL, 1))
_EN_UZUN = max(len(e) for y, _ in YUVALAR for e in y)


@lru_cache(maxsize=200_000)
def ek_mi(ek: str) -> bool:
    """`ek` (kökten sonraki harfler) Türkçe ek sırasına uyan eklere bölünebiliyor mu."""
    if not ek:
        return True

    @cache
    def dene(konum: int, yuva: int, tekrar: int) -> bool:
        if konum == len(ek):
            return True
        for y in range(yuva, len(YUVALAR)):
            ekler, en_fazla = YUVALAR[y]
            kullanildi = tekrar if y == yuva else 0
            if kullanildi >= en_fazla:
                continue
            for uz in range(1, min(_EN_UZUN, len(ek) - konum) + 1):
                if ek[konum:konum + uz] in ekler and dene(konum + uz, y, kullanildi + 1):
                    return True
        return False

    return dene(0, 0, 0)


_YUMUSAMA = {"p": "b", "ç": "c", "t": "d", "k": "ğ"}


@lru_cache(maxsize=10_000)
def desen(ifade: str) -> re.Pattern:
    """Küçük harfe çevrilmiş ifade için arama deseni. Eşleşmenin "ek" grubu kökten sonraki harflerdir."""
    ifade = ifade.rstrip("*").strip()
    kelimeler = ifade.split()
    govde = r"\s+".join(re.escape(k) for k in kelimeler)
    son = kelimeler[-1] if kelimeler else ""
    if son and son[-1] in _YUMUSAMA and len(son) > 2:
        # "harç" | "harc" (+ünlü ile başlayan ek)
        yumusak = r"\s+".join(re.escape(k) for k in kelimeler[:-1] + [son[:-1] + _YUMUSAMA[son[-1]]])
        govde = f"(?:{govde}|{yumusak}(?=[aeıioöuü]))"
    kesme = f"[{_KESME}]?"
    return re.compile(rf"(?<![{HARF}])(?P<kok>{govde}){kesme}(?P<ek>[{HARF}]*)", re.IGNORECASE)


def bul(ifade: str, metin: str) -> list[tuple[int, int]]:
    """Küçük harfe çevrilmiş `metin`de ifadenin geçtiği yerler (başlangıç, bitiş). Ek kuralına uymayanlar atılır."""
    serbest = ifade.endswith("*")
    sonuc = []
    for m in desen(ifade).finditer(metin):
        if serbest or ek_mi(m.group("ek")):
            sonuc.append((m.start(), m.end()))
    return sonuc


def var_mi(ifade: str, metin: str) -> bool:
    serbest = ifade.endswith("*")
    return any(serbest or ek_mi(m.group("ek")) for m in desen(ifade).finditer(metin))

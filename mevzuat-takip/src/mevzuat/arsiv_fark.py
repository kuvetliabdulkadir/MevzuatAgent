"""Arşivde iki sürümün madde madde farkı, panelde gösterilecek biçimde.

Metin maddelere bölünür (surum.bolumler: "Başlangıç", "Madde 5", "Geçici Madde 2", "(I) Sayılı Liste"). Her madde için:
  ayni         değişmedi
  degisti      madde içinde kelime düzeyinde fark: eklenen <ins>, silinen <del>, yerinde
  eklendi      yeni sürümde var, eskide yok
  kaldirildi   eskide var, yenide yok; eski yerinde gösterilir ("v3'te kaldırıldı")
  mulga        madde duruyor ama metni "(Mülga: RG-…)" notuna indi; eski metin üstü çizili gösterilir
Karşılaştırma satır düzeninden bağımsızdır (boşluklar normalleştirilir), okuma düzeltmesinden doğan satır farkları
değişiklik sayılmaz.
"""

import difflib
import html as html_lib
import re

from mevzuat.surum import bolumler

_MULGA = re.compile(r"^\s*(?:(?:Ek|EK|Geçici|GEÇİCİ|Mükerrer|MÜKERRER)\s+)?(?:Madde|MADDE)\s+\S+\s*[–—-]?\s*\(\s*Mülga", re.IGNORECASE)
_KELIME_SINIRI = 60_000  # bundan uzun bölümlerde hızlı (autojunk) karşılaştırma


def _e(metin: str) -> str:
    return html_lib.escape(metin, quote=False)


# İki metnin kelime kelime farkı, yerinde işaretli HTML.
def kelime_farki(eski: str, yeni: str) -> str:
    e, y = eski.split(), yeni.split()
    hizli = len(e) + len(y) > _KELIME_SINIRI
    parcalar = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(a=e, b=y, autojunk=hizli).get_opcodes():
        if op == "equal":
            parcalar.append(_e(" ".join(e[a1:a2])))
            continue
        if a2 > a1:
            parcalar.append(f"<del>{_e(' '.join(e[a1:a2]))}</del>")
        if b2 > b1:
            parcalar.append(f"<ins>{_e(' '.join(y[b1:b2]))}</ins>")
    return " ".join(parcalar)


def fark(eski_metin: str, yeni_metin: str) -> dict:
    eski, yeni = bolumler(eski_metin), bolumler(yeni_metin)
    eski_sira = list(eski)
    sonuc: list[dict] = []
    for ad, metin in yeni.items():
        if ad not in eski:
            sonuc.append({"bolum": ad, "tur": "eklendi", "html": f"<ins>{_e(metin)}</ins>", "eski": None, "yeni": metin})
        elif " ".join(eski[ad].split()) == " ".join(metin.split()):
            sonuc.append({"bolum": ad, "tur": "ayni", "html": _e(metin), "eski": eski[ad], "yeni": metin})
        else:
            tur = "mulga" if _MULGA.match(metin) and not _MULGA.match(eski[ad]) else "degisti"
            sonuc.append({"bolum": ad, "tur": tur, "html": kelime_farki(eski[ad], metin), "eski": eski[ad],
                          "yeni": metin})
    # Kaldırılan maddeler eski sıradaki yerlerine (eskide kendinden önce gelen ve yenide de olan maddenin arkasına).
    for i, ad in enumerate(eski_sira):
        if ad in yeni:
            continue
        kayit = {"bolum": ad, "tur": "kaldirildi", "html": f"<del>{_e(eski[ad])}</del>", "eski": eski[ad], "yeni": None}
        konum = 0
        for onceki in reversed(eski_sira[:i]):
            yer = next((j for j, s in enumerate(sonuc) if s["bolum"] == onceki), None)
            if yer is not None:
                konum = yer + 1
                break
        sonuc.insert(konum, kayit)
    sayac = {t: sum(1 for s in sonuc if s["tur"] == t) for t in ("degisti", "eklendi", "kaldirildi", "mulga")}
    return {"sayac": sayac, "bolumler": sonuc}

"""HTML belgeyi yapısıyla okur: düz metin (paragraf paragraf) ve panelde gösterilecek temizlenmiş HTML.

Eski okuyucu (icerik.html_metni) her etiketin arasına satır sonu koyuyordu. Word'den çıkmış sayfalarda harfler ayrı
<span>'lerde durduğu için kelimeler bölünüyordu ("Bankam\\nı\\nzca") ve cümleler satır satır kırılıyordu. Burada sadece
blok etiketleri (p, div, başlık, liste, tablo satırı) yeni paragraf açar, satır içi etiketler birleşir, HTML'in kendi
boşluk kuralı uygulanır (kaynaktaki satır sonları boşluk olur).

Görünüm HTML'i sadece güvenli etiketlerden kurulur, kaynak HTML'in kendisi panele hiç verilmez:
p, h2-h4, b, i, u, sup, sub, br, table, thead, tbody, tr, th, td (colspan/rowspan), ul, ol, li, img (data-resim).
Stil, betik, bağlantı adresi ve olay öznitelikleri taşınmaz. Metin html.escape ile yazılır.
"""

import html as html_lib
import re
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from mevzuat import metin_duzen as md

_ATILAN = {"script", "style", "head", "title", "meta", "link", "noscript", "iframe", "object", "embed", "svg", "template"}
_BLOK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "center", "section", "article",
         "header", "footer", "address", "dd", "dt", "form", "fieldset", "hr", "ul", "ol", "dl", "body", "html", "main",
         "nav", "aside", "figure", "figcaption", "caption"}
_KALIN = {"b", "strong"}
_EGIK = {"i", "em"}
_BASLIK_SEVIYE = {"h1": "h2", "h2": "h3", "h3": "h4", "h4": "h4", "h5": "h4", "h6": "h4"}


@dataclass
class HtmlSonucu:
    metin: str  # paragraflar "\n" ile, tablo satırları "hücre | hücre"
    gorunum: str  # güvenli HTML
    resimler: list[str] = field(default_factory=list)  # <img src> sırasıyla; metinde "[Resim N]", görünümde data-resim="N"
    baglantilar: list[tuple[str, str]] = field(default_factory=list)  # (href, bağlantı yazısı)
    tablo_sayisi: int = 0


def _hizalama(dugum) -> str:
    a = dugum.attributes
    hiza = (a.get("align") or "").lower()
    stil = (a.get("style") or "").lower().replace(" ", "")
    if hiza == "center" or "text-align:center" in stil or dugum.tag == "center":
        return "orta"
    if hiza == "right" or "text-align:right" in stil:
        return "sag"
    return ""


class _Kurucu:
    def __init__(self):
        self.gorunum: list[str] = []
        self.metin: list[str] = []
        self.p_html: list[str] = []
        self.p_metin: list[str] = []
        self.p_etiket = "p"
        self.p_sinif = ""
        self.resimler: list[str] = []
        self.baglantilar: list[tuple[str, str]] = []
        self.tablo_sayisi = 0

    # Açık paragrafı kapatır (boşsa atar).
    def bosalt(self) -> None:
        duz = " ".join("".join(self.p_metin).split())
        if duz:
            sinif = f' class="{self.p_sinif}"' if self.p_sinif else ""
            ic = "".join(self.p_html).strip()
            ic = re.sub(r"(?:<br>\s*)+$", "", ic)
            self.gorunum.append(f"<{self.p_etiket}{sinif}>{ic}</{self.p_etiket}>")
            self.metin.append(duz)
        self.p_html, self.p_metin, self.p_etiket, self.p_sinif = [], [], "p", ""

    def yazi(self, metin: str) -> None:
        metin = re.sub(r"[\s\xa0]+", " ", md.temizle(metin))
        if not metin:
            return
        if not self.p_metin or "".join(self.p_metin).endswith(" "):
            metin = metin.lstrip() if not self.p_metin else metin
        self.p_metin.append(metin)
        self.p_html.append(html_lib.escape(metin, quote=False))

    def resim(self, src: str) -> None:
        self.bosalt()
        i = len(self.resimler)
        self.resimler.append(src)
        self.gorunum.append(f'<figure class="resim"><img data-resim="{i}" alt="Resim {i + 1}"></figure>')
        self.metin.append(f"[Resim {i + 1}]")


_TABLO_ICI = {"tr", "td", "th", "tbody", "thead", "tfoot"}


# Biçim etiketinin görünümdeki karşılığı (kalın, eğik, altı çizili, üst/alt simge) ya da None.
def _sarma_etiketi(dugum) -> str | None:
    etiket = dugum.tag
    if etiket in _KALIN:
        return "b"
    if etiket in _EGIK and "font-style:normal" not in (dugum.attributes.get("style") or "").lower().replace(" ", ""):
        return "i"
    return etiket if etiket in ("u", "sup", "sub") else None


# Blok etiket (p, div, başlık, liste, tablo hücresi): yeni paragraf açar, içini yazar, kapatır.
def _blok(dugum, k: _Kurucu) -> None:
    k.bosalt()
    if dugum.tag in _BASLIK_SEVIYE:
        k.p_etiket = _BASLIK_SEVIYE[dugum.tag]
    elif dugum.tag == "li":
        k.p_sinif = "madde-isareti"
    if hiza := _hizalama(dugum):
        k.p_sinif = (k.p_sinif + " " + hiza).strip()
    _satir_ici(dugum, k)
    k.bosalt()


# Satır içi etiket (span, b, i, a, font ...): içi aynı paragrafa yazılır, biçimliyse sarılır, bağlantı kaydedilir.
def _satir_ici_etiket(dugum, k: _Kurucu) -> None:
    if dugum.tag == "a" and (href := (dugum.attributes.get("href") or "").strip()):
        k.baglantilar.append((href, " ".join((dugum.text() or "").split())))
    sar = _sarma_etiketi(dugum)
    if sar:
        k.p_html.append(f"<{sar}>")
    _satir_ici(dugum, k)
    if sar:
        k.p_html.append(f"</{sar}>")


def _satir_ici(dugum, k: _Kurucu) -> None:
    """Bir düğümün içini paragraf içine yazar, blok etiket görürse paragrafı bölür."""
    for cocuk in dugum.iter(include_text=True):
        etiket = cocuk.tag
        if etiket == "-text":
            k.yazi(cocuk.text_content or "")
        elif etiket in _ATILAN or etiket == "-comment":
            continue
        elif etiket == "br":
            k.p_html.append("<br>")
            k.p_metin.append(" ")
        elif etiket == "img":
            src = (cocuk.attributes.get("src") or "").strip()
            if src and not src.startswith("data:"):
                k.resim(src)
        elif etiket == "table":
            k.bosalt()
            _tablo(cocuk, k)
        elif etiket in _BLOK or etiket in _TABLO_ICI:
            _blok(cocuk, k)
        elif etiket == "hr":
            k.bosalt()
        else:
            _satir_ici_etiket(cocuk, k)


# Tablonun kendi satırları (iç içe tablonun satırları dıştaki tabloya sayılmaz).
def _kendi_satirlari(tablo) -> list:
    satirlar = []
    for tr in tablo.css("tr"):
        ata = tr.parent
        while ata is not None and ata.tag != "table":
            ata = ata.parent
        if ata is not None and ata.mem_id == tablo.mem_id:
            satirlar.append(tr)
    return satirlar


def _resim_numaralarini_kaydir(metin: str, kayma: int, desen: str, bicim: str) -> str:
    return re.sub(desen, lambda m: bicim.format(int(m.group(1)) + kayma), metin)


def _hucre_oku(hucre, k: _Kurucu) -> tuple[str, str, str, str]:
    """(etiket, colspan/rowspan öznitelikleri, görünüm, metin). Hücredeki resimler ve bağlantılar ana listeye taşınır,
    resim numaraları ana listedeki yerlerine göre kaydırılır."""
    ic = _Kurucu()
    _satir_ici(hucre, ic)
    ic.bosalt()
    kayma = len(k.resimler)
    gorunum = "<br>".join(re.sub(r"^<p[^>]*>|</p>$", "", g) for g in ic.gorunum)
    gorunum = _resim_numaralarini_kaydir(gorunum, kayma, r'data-resim="(\d+)"', 'data-resim="{}"')
    gorunum = re.sub(r"</?(?:h\d|figure)[^>]*>", "", gorunum)
    metin = " ".join(_resim_numaralarini_kaydir(t, kayma, r"\[Resim (\d+)\]", "[Resim {}]") for t in ic.metin)
    k.resimler.extend(ic.resimler)
    k.baglantilar.extend(ic.baglantilar)
    oz = ""
    for ad in ("colspan", "rowspan"):
        deger = (hucre.attributes.get(ad) or "").strip()
        if deger.isdigit() and 1 < int(deger) <= 1000:
            oz += f' {ad}="{int(deger)}"'
    return hucre.tag, oz, gorunum, metin


def _tablo(tablo, k: _Kurucu) -> None:
    """Tabloyu yapısıyla görünüme, satır satır ("hücre | hücre") metne yazar. İç içe tablo hücrenin metnine katılır."""
    satirlar = [hucreler for tr in _kendi_satirlari(tablo)
                if (hucreler := [_hucre_oku(h, k) for h in tr.iter() if h.tag in ("td", "th")])]
    if not satirlar:
        return
    k.tablo_sayisi += 1
    govde = "".join("<tr>" + "".join(f"<{e}{oz}>{g}</{e}>" for e, oz, g, _ in s) + "</tr>" for s in satirlar)
    k.gorunum.append(f'<div class="tablo"><table>{govde}</table></div>')
    for s in satirlar:
        satir = " | ".join(m for _, _, _, m in s if m)
        if satir.strip(" |"):
            k.metin.append(satir)


def html_oku(html: str) -> HtmlSonucu:
    agac = HTMLParser(html)
    for d in agac.css(",".join(sorted(_ATILAN))):
        d.decompose()
    kok = agac.body or agac.root
    k = _Kurucu()
    if kok is not None:
        _satir_ici(kok, k)
    k.bosalt()
    metin = md.paragraflari_duzenle("\n".join(k.metin))
    return HtmlSonucu(metin=metin, gorunum="\n".join(k.gorunum), resimler=k.resimler, baglantilar=k.baglantilar,
                      tablo_sayisi=k.tablo_sayisi)


# --- düz metinden görünüm (PDF, OCR, ek dosyası) ---------------------------------------------------------------

_MADDE_BASI = re.compile(
    r"^((?:(?:EK|Ek|GEÇİCİ|Geçici|MÜKERRER|Mükerrer)\s+)?(?:MADDE|Madde)\s+\d+(?:\s*/\s*[A-ZÇĞİÖŞÜ])?\s*[–—-])\s*")
_FIKRA = re.compile(r"^\(\d+[a-zA-Z]?\)\s")
_BENT = re.compile(r"^(?:[a-zçğıöşü]|[A-ZÇĞİÖŞÜ]|\d+)\)\s")


# Düz metnin bir paragrafını sınıfıyla HTML'e çevirir.
def paragraf_html(p: str) -> str:
    e = html_lib.escape(p, quote=False)
    if m := _MADDE_BASI.match(p):
        bas = html_lib.escape(m.group(1), quote=False)
        return f'<p class="madde"><b>{bas}</b> {html_lib.escape(p[m.end():], quote=False)}</p>'
    harfler = [c for c in p if c.isalpha()]
    if harfler and len(p) <= 120 and all(c.isupper() for c in harfler) and len(harfler) >= 3:
        return f'<p class="baslik orta"><b>{e}</b></p>'
    if _FIKRA.match(p):
        return f'<p class="fikra">{e}</p>'
    if _BENT.match(p):
        return f'<p class="bent">{e}</p>'
    if " | " in p:
        return f'<p class="tablo-satiri">{e}</p>'
    return f"<p>{e}</p>"


def duz_metinden_gorunum(metin: str) -> str:
    return "\n".join(paragraf_html(p) for p in metin.split("\n") if p.strip())

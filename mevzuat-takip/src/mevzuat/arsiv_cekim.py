"""Arşivde bir mevzuatın çekilip okunması (okuyucu sürümü 2, 2026-11-22). İş akışı ve panel arsiv.py'de.

Bir HTML mevzuat için:
  1. Metin sayfası (iframe) alınır, html_okuma ile yapısıyla okunur (paragraflar, tablolar, resim yerleri).
  2. Gövdedeki resimler indirilir ve OCR'lanır. Sayfanın yazısı kısaysa resimlerin yazısı asıl metindir (metni resim
     olan sayfalar, ölçüm A). Görünümde resmin kendisi durur.
  3. Ekler indirilir (ek_indir). Eski alan adları yenisine çevrilir, Resmî Gazete ve diğer kamu siteleri de denenir,
     yerel diske işaret eden bozuk bağlantılar "kaynakta bozuk" olur. Her ek okunur (ek_okuma), arşivlerin içi açılır.
  4. Sayfanın yazısı kısa ve asıl içerik ekteyse ("tıklayınız", ölçüm B) ekin metni ana metne katılır.
  5. Mevzuatın sayfasından sitenin PDF'i bulunur (karar 27): sitede kayıtlı PDF varsa o, yoksa sitenin ürettiği PDF
     (sadece yeni sürüm olunca indirilir, çünkü her istekte baytları değişiyor).
  6. Metin ve görünüm hiçbir yerde yoksa (sayfa boş, resim yok, PDF yok) durum SITEDE_YOK olur.
PDF mevzuat (CB kararı, genelge) için PDF indirilir ve pdf_okuma ile sayfa sayfa okunur.

Sürüm kararı (karar 19): karşılaştırma metni (boşluklar normalleştirilmiş) ve dosya özetleri değişmediyse yeni sürüm
olmaz. Değiştiyse ve sitenin ham kaynağı (kaynak_ozeti) aynıysa sürüm "okuma_duzeltmesi" olur (okuyucu iyileşti,
mevzuat değişmedi), farklıysa "degisiklik".
"""

import hashlib
import json
import logging
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat import ek_okuma, html_okuma, metin_duzen as md, pdf_okuma
from mevzuat.db import ArsivDosyasi, ArsivMevzuat, ArsivSurumu
from mevzuat.http import CozumlenemeyenAdres
from mevzuat.sources import mevzuat_gov

log = logging.getLogger("mevzuat.arsiv")

OKUYUCU_SURUMU = 2
SITE = mevzuat_gov.SITE
EK_UZANTILARI = ("doc", "docx", "xls", "xlsx", "pdf", "zip", "rar", "7z", "rtf", "odt", "ods", "ppt", "pptx", "pps")
_EK_LINKI = re.compile(rf"\.({'|'.join(EK_UZANTILARI)})$", re.IGNORECASE)
# Sayfa yazısı bundan kısaysa resimlerin ve eklerin metni asıl metin sayılır (ölçüm A ve B).
KISA_METIN = 500
EN_AZ_METIN = 200
ARSIV_DERINLIK = 2
# Taşınmış adresler (veri, siteye özel kod değil). Eski adresin başı yenisiyle değiştirilip denenir.
# 2026-11-22 ölçümü: rega.basbakanlik.gov.tr çözülmüyor, aynı yol resmigazete.gov.tr'de açılıyor;
# mevzuat.basbakanlik.gov.tr/…/cache/yonetmelik/ dosyaları /MevzuatMetin/yonetmelik/ altında.
TASINAN_ADRESLER: tuple[tuple[str, str], ...] = (
    ("http://rega.basbakanlik.gov.tr/", "https://www.resmigazete.gov.tr/"),
    ("https://rega.basbakanlik.gov.tr/", "https://www.resmigazete.gov.tr/"),
    ("http://www.resmigazete.gov.tr/", "https://www.resmigazete.gov.tr/"),
    ("http://mevzuat.basbakanlik.gov.tr/mevzuat/cache/yonetmelik/", f"{SITE}MevzuatMetin/yonetmelik/"),
    ("http://mevzuat.basbakanlik.gov.tr/cache/yonetmelik/", f"{SITE}MevzuatMetin/yonetmelik/"),
    ("http://www.mevzuat.gov.tr/cache/yonetmelik/", f"{SITE}MevzuatMetin/yonetmelik/"),
    ("https://www.mevzuat.gov.tr/cache/yonetmelik/", f"{SITE}MevzuatMetin/yonetmelik/"),
    ("http://www.mevzuat.gov.tr/", SITE),
    ("http://mevzuat.gov.tr/", SITE),
)
# Yerel diske ya da kişisel klasöre işaret eden, kaynakta bozuk bağlantı.
_BOZUK_BAGLANTI = re.compile(r"^file:|^[a-z]:[\\/]|\\|(?:^|/)\.\./|/desktop/|/masaüstü/", re.IGNORECASE)
_TIKLA = re.compile(r"t[ıi]klay[ıi]n[ıi]z|\bek(?:ler)?(?:i|ini)?\b", re.IGNORECASE)


# --- yardımcılar ---------------------------------------------------------------------------------------------

def sha(veri: bytes) -> str:
    return hashlib.sha256(veri).hexdigest()


def guvenli_ad(ad: str) -> str:
    ad = re.sub(r"[^\w.\-]+", "_", unquote(ad)).strip("._")
    return ad[:120] or "dosya"


def dosya_yaz(klasor: Path, alt: str, anahtar: str, ad: str, veri: bytes) -> str:
    """Dosyayı özetine göre adlandırıp yazar (aynı içerik iki kez yazılmaz), klasöre göre yolunu döner."""
    yol = Path(alt) / guvenli_ad(anahtar) / f"{sha(veri)[:16]}_{guvenli_ad(ad)}"
    tam = klasor / yol
    if not tam.exists():
        tam.parent.mkdir(parents=True, exist_ok=True)
        gecici = tam.with_suffix(tam.suffix + ".yaziliyor")
        gecici.write_bytes(veri)
        gecici.replace(tam)
    return yol.as_posix()


def dosya_mi(r: httpx.Response) -> bool:
    """Cevap gerçekten bir dosya mı. Site olmayan dosyada 200 ile HTML hata sayfası ya da 302 veriyor."""
    if r.status_code != 200 or not r.content:
        return False
    tur = r.headers.get("content-type", "").lower()
    if "text/html" in tur:
        return False
    return ek_okuma.bicim_bul(r.content[:4096]) != "html"


def sitede_yok_mu(r: httpx.Response) -> bool:
    konum = r.headers.get("location", "")
    return r.status_code in (404, 410) or (r.status_code in (301, 302, 303) and "error" in konum.lower())


# --- günlük (karar 22) --------------------------------------------------------------------------------------

class Gunluk:
    """İş günlüğü: her mevzuat için bir JSON satırı (arsiv/gunluk/is-N.jsonl). İstemci yaptığı her isteği buraya bildirir."""

    def __init__(self, yol: Path | None):
        self.yol = yol
        self.kayit: dict | None = None
        if yol:
            yol.parent.mkdir(parents=True, exist_ok=True)

    def basla(self, anahtar: str) -> dict:
        self.kayit = {"zaman": datetime.now().isoformat(timespec="seconds"), "anahtar": anahtar, "istekler": [],
                      "adimlar": [], "uyarilar": [], "_bas": time.monotonic()}
        return self.kayit

    def istek(self, url: str, durum: int | str, bayt: int, sure: float) -> None:
        if self.kayit is not None:
            self.kayit["istekler"].append({"url": url[:300], "http": durum, "bayt": bayt, "sure": round(sure, 2)})

    def adim(self, adim_adi: str, bas: float, **ek) -> None:
        if self.kayit is not None:
            self.kayit["adimlar"].append({"adim": adim_adi, "sure": round(time.monotonic() - bas, 2), **ek})

    def uyari(self, metin: str) -> None:
        if self.kayit is not None:
            self.kayit["uyarilar"].append(metin[:300])

    def bitir(self, **alanlar) -> None:
        if self.kayit is None:
            return
        k = self.kayit
        k["sure"] = round(time.monotonic() - k.pop("_bas"), 2)
        k.update(alanlar)
        self.yaz(k)
        self.kayit = None

    def yaz(self, satir: dict) -> None:
        if self.yol:
            with self.yol.open("a", encoding="utf-8") as f:
                f.write(json.dumps(satir, ensure_ascii=False, default=str) + "\n")


# --- adresler -----------------------------------------------------------------------------------------------

def _site_klasorlerinde(ad: str) -> list[str]:
    return [f"{SITE}MevzuatMetin/yonetmelik/{ad}", f"{SITE}MevzuatMetin/{ad}"]


def _tam_adres_adaylari(href: str) -> list[str]:
    """Tam adres: taşınan adresler tablosuyla yenisi, https'li hali ve kendisi."""
    kucuk = href.lower()
    adaylar = [yeni + href[len(eski):] for eski, yeni in TASINAN_ADRESLER if kucuk.startswith(eski)]
    if kucuk.startswith("http://"):
        adaylar.append("https://" + href[len("http://"):])
    adaylar.append(href)
    # Resmî Gazete'nin eski bağlantılarında klasör "Eskiler", bugünkü sitede "eskiler".
    return adaylar + [a.replace("/Eskiler/", "/eskiler/") for a in adaylar if "resmigazete.gov.tr/Eskiler/" in a]


def _kodlu_bicimleriyle(adaylar: list[str]) -> list[str]:
    """Boşluklu ve Türkçe karakterli adlar kodlanmış haliyle de denenir; geçersiz adresler atılır."""
    tum: list[str] = []
    for a in adaylar:
        for v in (a, quote(unquote(a), safe=":/?&=%#+@,;~")):
            if v not in tum and urlparse(v).scheme in ("http", "https") and urlparse(v).hostname:
                tum.append(v)
    return tum


# Ek ya da resim bağlantısının denenecek tam adresleri ve bağlantı kaynakta bozuk mu.
def dosya_adaylari(href: str) -> tuple[list[str], bool]:
    """Göreli bağlantı ("9.5.40085-Ek.docx") sayfanın kendi adresine göre açınca boş sayfa veriyor; dosya
    /MevzuatMetin/yonetmelik/ altında. Tam adresler taşınan adresler tablosuyla da denenir. Yerel diske işaret eden
    (kaynakta bozuk) bağlantıda dosya adı sitede yine de aranır."""
    href = href.strip()
    bozuk = bool(_BOZUK_BAGLANTI.search(href))
    taban = href.replace("\\", "/").split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
    if bozuk:
        adaylar = _site_klasorlerinde(taban) if taban else []
    elif href.lower().startswith(("http://", "https://")):
        adaylar = _tam_adres_adaylari(href)
    elif href.startswith("/"):
        adaylar = [urljoin(SITE, href)]
    else:
        adaylar = _site_klasorlerinde(href) + ([f"{SITE}MevzuatMetin/yonetmelik/{taban}"] if "/" in href and taban else [])
    return _kodlu_bicimleriyle(adaylar), bozuk


# Sayfadaki ek bağlantıları: uzantısı ek dosyası olan ya da uzantısız ama yazısı/adresi ek olduğunu söyleyen.
def ek_baglantilari(baglantilar: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """(ek sayılanlar, sayılmayan şüpheli bağlantılar). Uzantısız olanlar indirilince içeriğine bakılır."""
    ekler, supheli = [], []
    for href, yazi in baglantilar:
        h = href.strip()
        if not h or h.startswith(("#", "mailto:", "javascript:")):
            continue
        yol = unquote(h.split("?")[0].split("#")[0])
        son = yol.rstrip("/").rsplit("/", 1)[-1]
        uzantisiz_ek = son and not re.search(r"\.[A-Za-z0-9]{1,5}$", son) and (
            _TIKLA.search(yazi) or re.search(r"(?i)[-_ ]ek", son))
        if _EK_LINKI.search(yol) or uzantisiz_ek:
            if h not in ekler:
                ekler.append(h)
        elif _TIKLA.search(yazi) and not re.search(r"(?i)\.(?:s?html?|aspx?|php)$", son) and "mevzuat?" not in h:
            supheli.append(h)
    return ekler, supheli


# --- dosya indirme ve okuma ---------------------------------------------------------------------------------

def _dns_hatasi_mi(e: BaseException | None) -> bool:
    """Alan adı çözülemedi mi (kapanmış site). Hata zinciri boyunca bakılır."""
    while e is not None:
        if isinstance(e, CozumlenemeyenAdres) or "getaddrinfo" in str(e) or "Name or service not known" in str(e):
            return True
        e = e.__cause__ or e.__context__
    return False


def dosya_indir(ist, adaylar: list[str]) -> tuple[bytes | None, str | None, list[dict], str]:
    """(veri, inen adres, denenenler, sonuç). Sonuç: indi / sitede_yok (sunucu 404/302 verdi) / ulasilamadi.
    Alan adı çözülemeyen site (kapanmış eski adres) iş boyunca bir daha denenmez."""
    denenen, ulasildi = [], False
    # İstemci iş boyunca yaşar; ölü alan adları onun üzerinde tutulur (arsiv.Istemci.olu_alanlar).
    olu: set[str] = getattr(ist, "olu_alanlar", set())
    for aday in adaylar:
        alan = (urlparse(aday).hostname or "").lower()
        if alan in olu:
            denenen.append({"url": aday, "sonuc": "alan adı çözülemiyor (denenmedi)"})
            continue
        try:
            r = ist.get(aday)
        except httpx.HTTPError as e:
            if _dns_hatasi_mi(e):
                olu.add(alan)
            denenen.append({"url": aday, "sonuc": repr(e)[:150]})
            continue
        ulasildi = True
        if dosya_mi(r):
            denenen.append({"url": aday, "sonuc": f"{r.status_code} dosya"})
            return r.content, aday, denenen, "indi"
        denenen.append({"url": aday, "sonuc": f"{r.status_code} {r.headers.get('content-type', '')[:40]}".strip()})
    return None, None, denenen, "sitede_yok" if ulasildi else "ulasilamadi"


def dosya_isle(session: Session, kok: Path, veri: bytes, ad: str, yol: str, anahtar: str, simdi: datetime,
               derinlik: int = 0) -> ArsivDosyasi:
    """Dosyayı okur (ek_okuma) ve arsiv_dosyalari'na yazar. Aynı içerik daha önce aynı okuyucuyla okunduysa tekrar okunmaz.
    Arşivse içindekiler diske yazılıp her biri ayrıca işlenir."""
    ozet = sha(veri)
    kayit = session.get(ArsivDosyasi, ozet)
    # Aynı okuyucuyla başarıyla okunduysa tekrar okunmaz. Görünümü çıkmamış (çevirici yoktu) ya da hata vermiş dosya
    # her seferinde yeniden denenir.
    if kayit is not None and kayit.okuyucu_surumu == OKUYUCU_SURUMU and kayit.durum == "tamam":
        return kayit
    sonuc = ek_okuma.ek_oku(veri, ad, kok, f"goruntu/{ozet[:16]}", ozet)
    ic, metinler = [], []
    if sonuc.ic_dosyalar and derinlik < ARSIV_DERINLIK:
        for ic_ad, icerik in sonuc.ic_dosyalar:
            ic_yol = dosya_yaz(kok, "ekler", anahtar, ic_ad.replace("/", "_"), icerik)
            alt = dosya_isle(session, kok, icerik, ic_ad, ic_yol, anahtar, simdi, derinlik + 1)
            ic.append({"ad": ic_ad, "sha256": alt.sha256, "boyut": alt.boyut, "yol": alt.yol, "bicim": alt.bicim,
                       "durum": alt.durum})
            if alt.metin:
                metinler.append(f"[Dosya: {ic_ad}]\n{alt.metin}")
    elif sonuc.ic_dosyalar:
        sonuc.notlar.append(f"iç içe arşiv {ARSIV_DERINLIK} kattan derin, içi açılmadı")
    metin = sonuc.metin or "\n\n".join(metinler)
    alanlar = dict(ad=ad[:500], bicim=sonuc.bicim, boyut=len(veri), yol=yol, durum=sonuc.durum, metin=metin or None,
                   gorunum=sonuc.gorunum or None, notlar=sonuc.notlar, ic=ic, sayfalar=sonuc.sayfalar,
                   okuyucu_surumu=OKUYUCU_SURUMU, islendi=simdi)
    if kayit is None:
        kayit = ArsivDosyasi(sha256=ozet, **alanlar)
        session.add(kayit)
    else:
        for k, v in alanlar.items():
            setattr(kayit, k, v)
    session.flush()
    return kayit


def ek_indir(session: Session, ist, href: str, anahtar: str, kok: Path, simdi: datetime, gunluk: Gunluk) -> dict:
    """Bir ek bağlantısı. Dönen sözlük sürümün `ekler` listesine girer:
    {ad, href, url, sha256, boyut, yol, bicim, durum (indi/sitede_yok/ulasilamadi/kaynakta_bozuk/ek_degil),
     okuma (tamam/gorunum_yok/hata), denenen [{url, sonuc}], ic [...]}"""
    ad = unquote(href.replace("\\", "/").split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]) or href[:100]
    ek: dict = {"ad": ad, "href": href}
    adaylar, bozuk = dosya_adaylari(href)
    veri, url, denenen, sonuc = dosya_indir(ist, adaylar)
    ek["denenen"] = denenen
    if veri is None:
        ek["durum"] = "kaynakta_bozuk" if bozuk else sonuc
        gunluk.uyari(f"ek inmedi ({ek['durum']}): {href}")
        return ek
    bicim = ek_okuma.bicim_bul(veri, ad)
    if bicim == "html":
        ek["durum"] = "ek_degil"
        return ek
    yol = dosya_yaz(kok, "ekler", anahtar, ad, veri)
    bas = time.monotonic()
    kayit = dosya_isle(session, kok, veri, ad, yol, anahtar, simdi)
    gunluk.adim("ek_oku", bas, ad=ad, bicim=kayit.bicim, okuma=kayit.durum)
    ek.update(url=url, sha256=kayit.sha256, boyut=len(veri), yol=yol, bicim=kayit.bicim, durum="indi",
              okuma=kayit.durum, ic=kayit.ic)
    if bozuk:
        ek["not"] = "kaynaktaki bağlantı bozuktu, dosya adıyla sitede bulundu"
    return ek


# --- okuma sonucu -------------------------------------------------------------------------------------------

@dataclass
class Okuma:
    metin: str = ""
    gorunum: str = ""
    okuma: str = "html"
    kaynak_parcalari: list[str] = field(default_factory=list)
    pdf_yolu: str | None = None
    site_pdf_yolu: str | None = None
    site_pdf_turu: str | None = None
    uretilen_pdf_adresi: str | None = None
    ekler: list[dict] = field(default_factory=list)
    resimler: list[dict] = field(default_factory=list)
    sayfalar: list[dict] = field(default_factory=list)
    kusurlar: list[str] = field(default_factory=list)
    arama_metni: str = ""
    sitede_yok: str | None = None  # dolu ise mevzuat sitede yok, nedeni


def _pdf_ile_oku(kok: Path, anahtar: str, veri: bytes, ad: str, sonuc: Okuma, gunluk: Gunluk) -> None:
    sonuc.pdf_yolu = dosya_yaz(kok, "pdf", anahtar, ad, veri)
    bas = time.monotonic()
    ozet = sha(veri)
    p = pdf_okuma.pdf_oku(kok / sonuc.pdf_yolu, kok, f"goruntu/{ozet[:16]}", pdf_okuma.ocr_onbellek_yolu(kok, ozet))
    gunluk.adim("pdf_oku", bas, sayfa=p.sayfa_sayisi, okuma=p.okuma,
                yontemler={y: sum(1 for s in p.sayfalar if s["yontem"] == y) for y in {s["yontem"] for s in p.sayfalar}},
                tablo_yapi=sum(s["tablo_yapi"] for s in p.sayfalar), tablo_goruntu=sum(s["tablo_goruntu"] for s in p.sayfalar))
    sonuc.metin, sonuc.gorunum, sonuc.okuma = p.metin, p.gorunum, p.okuma
    sonuc.sayfalar, sonuc.arama_metni = p.sayfalar, p.arama_metni
    sonuc.kusurlar += p.kusurlar
    sonuc.kaynak_parcalari.append(ozet)


def _site_pdf_baglantilari(sayfa_html: str) -> tuple[str | None, str | None]:
    """Mevzuatın sitedeki sayfasından (asıl PDF adresi, PDF üretme adresi)."""
    asil = uretilen = None
    for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", sayfa_html):
        h = href.replace("&amp;", "&").strip()
        if re.search(r"(?i)/MevzuatMetin/[^\"']+\.pdf$", h) and asil is None:
            asil = urljoin(SITE, h)
        elif "GeneratePdf" in h and uretilen is None:
            uretilen = urljoin(SITE, h)
    return asil, uretilen


@dataclass
class _Cekim:
    """Bir HTML mevzuatın çekimi boyunca adımların paylaştığı bağlam."""
    session: Session
    ist: object
    m: ArsivMevzuat
    kok: Path
    simdi: datetime
    gunluk: Gunluk
    sonuc: Okuma = field(default_factory=Okuma)


_RESIM_ISARETI = re.compile(r"\[Resim (\d+)\]")
_RESIM_SATIRI = re.compile(r"\[Resim \d+[^\]]*\]")


def _sayfayi_al(c: _Cekim, iframe: str) -> html_okuma.HtmlSonucu:
    bas = time.monotonic()
    r = c.ist.get(iframe)
    r.raise_for_status()
    govde = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", r.text)
    c.sonuc.kaynak_parcalari.append(sha(govde.encode("utf-8")))
    h = html_okuma.html_oku(r.text)
    c.gunluk.adim("html_oku", bas, karakter=len(h.metin), resim=len(h.resimler), tablo=h.tablo_sayisi)
    return h


def _resimleri_isle(c: _Cekim, h: html_okuma.HtmlSonucu) -> tuple[str, str, dict[int, str]]:
    """Gövde resimlerini indirir ve okur (ölçüm A). (metin, görünüm, {resim sırası: OCR yazısı}) döner; metindeki
    "[Resim N]" işaretinin arkasına resmin yazısı girer, görünümde yer tutucu resmin özetine bağlanır."""
    gorunum, yazilar = h.gorunum, {}
    for i, src in enumerate(h.resimler):
        kayit = {"src": src}
        veri, url, denenen, durum = dosya_indir(c.ist, dosya_adaylari(src)[0])
        if veri is None:
            kayit.update(durum=durum, denenen=denenen)
            c.gunluk.uyari(f"resim inmedi: {src}")
        else:
            ad = src.replace("\\", "/").rsplit("/", 1)[-1]
            yol = dosya_yaz(c.kok, "resim", c.m.anahtar, ad, veri)
            bas = time.monotonic()
            dosya = dosya_isle(c.session, c.kok, veri, ad, yol, c.m.anahtar, c.simdi)
            c.gunluk.adim("resim_oku", bas, ad=ad, bicim=dosya.bicim)
            kayit.update(url=url, sha256=dosya.sha256, boyut=len(veri), yol=yol, durum="indi",
                         ocr="metin" if dosya.metin else "gorsel")
            c.sonuc.kaynak_parcalari.append(dosya.sha256)
            if dosya.metin:
                yazilar[i] = dosya.metin
            gorunum = gorunum.replace(f'data-resim="{i}"', f'data-sha="{dosya.sha256}"', 1)
        c.sonuc.resimler.append(kayit)

    def isaret(eslesme: re.Match) -> str:
        n = int(eslesme.group(1)) - 1
        if n in yazilar:
            return f"[Resim {n + 1}]\n{yazilar[n]}"
        if n < len(c.sonuc.resimler) and c.sonuc.resimler[n].get("durum") != "indi":
            return f"[Resim {n + 1}: indirilemedi]"
        return f"[Resim {n + 1}: görsel]"

    # İşaret satırın içinde de olabilir (resim tablo hücresinde, 9.5.6022).
    return _RESIM_ISARETI.sub(isaret, h.metin), gorunum, yazilar


def _ekleri_isle(c: _Cekim, baglantilar: list[tuple[str, str]]) -> None:
    hrefler, supheli = ek_baglantilari(baglantilar)
    if supheli:
        c.gunluk.uyari(f"ek sayılmayan 'tıklayınız' bağlantıları: {supheli[:5]}")
    for href in hrefler:
        ek = ek_indir(c.session, c.ist, href, c.m.anahtar, c.kok, c.simdi, c.gunluk)
        if ek.get("durum") == "ek_degil":
            continue
        c.sonuc.ekler.append(ek)
        if ek.get("sha256"):
            c.sonuc.kaynak_parcalari.append(ek["sha256"])


def _ek_metnini_kat(c: _Cekim, metin: str) -> str:
    """Sayfanın yazısı kısa ve asıl içerik ekteyse ("tıklayınız", ölçüm B) eklerin metni ana metne katılır."""
    for ek in c.sonuc.ekler:
        dosya = c.session.get(ArsivDosyasi, ek["sha256"]) if ek.get("sha256") else None
        if dosya is not None and dosya.metin:
            metin += f"\n\n[Ek: {ek['ad']}]\n{dosya.metin}"
            c.sonuc.okuma = "html+ek"
    return metin


def _site_pdfini_al(c: _Cekim, sayfa_bos: bool) -> bool:
    """Sitenin PDF'i (karar 27). Sitede kayıtlı PDF varsa indirilir; sayfa boşsa metin ondan okunur (True döner).
    Sadece sitenin ürettiği PDF varsa adresi saklanır, yeni sürüm olunca indirilir (surum_kaydet)."""
    bas = time.monotonic()
    try:
        sayfa = c.ist.get(c.m.url)
        asil, uretilen = _site_pdf_baglantilari(sayfa.text if sayfa.status_code == 200 else "")
    except httpx.HTTPError as e:
        asil = uretilen = None
        c.gunluk.uyari(f"mevzuat sayfası alınamadı: {e!r}"[:200])
    okundu = False
    if asil:
        veri, _, _, durum = dosya_indir(c.ist, [asil])
        if veri is not None and veri[:5] == b"%PDF-":
            c.sonuc.site_pdf_yolu = dosya_yaz(c.kok, "pdf", c.m.anahtar, "site.pdf", veri)
            c.sonuc.site_pdf_turu = "asil"
            c.sonuc.kaynak_parcalari.append(sha(veri))
            if sayfa_bos:
                _pdf_ile_oku(c.kok, c.m.anahtar, veri, "site.pdf", c.sonuc, c.gunluk)
                okundu = True
        else:
            c.gunluk.uyari(f"sayfadaki PDF inmedi: {asil} ({durum})")
    elif uretilen:
        c.sonuc.uretilen_pdf_adresi = uretilen
    c.gunluk.adim("site_pdf", bas, asil=bool(asil), uretilen=bool(uretilen))
    return okundu


def _sonucu_degerlendir(s: Okuma, yazi: str, resim_yazisi_var: bool) -> None:
    """Sitede yok mu (metin, resim, PDF, ek hiçbiri yok) ve kalite kusurları."""
    # Bir iki kelimelik bağlantı yazısı ("ek", "tıklayınız") metin sayılmaz; gerçek mevzuatın en az başlığı vardır.
    icerik_yok = (len(md.karsilastirma_metni(_RESIM_SATIRI.sub("", s.metin))) < 20
                  and not any(r.get("durum") == "indi" for r in s.resimler) and not s.pdf_yolu
                  and not any(e.get("durum") == "indi" for e in s.ekler))
    if icerik_yok:
        s.sitede_yok = (f"Sayfa sadece {len(s.resimler)} resimden ibaret ama resimler sitede yok "
                        "(site de bozuk resim gösteriyor); sitede PDF de yok." if s.resimler
                        else "Sayfada metin yok, resim yok, sitede PDF de yok.")
        return
    if len(yazi) < EN_AZ_METIN and not resim_yazisi_var and s.okuma == "html" and not s.pdf_yolu and s.ekler:
        s.kusurlar.append("sayfanın yazısı çok kısa; asıl metin ekte olabilir ama ekin metni okunamadı")
    s.kusurlar += [f"resim inmedi: {r.get('src')}" for r in s.resimler if r.get("durum") != "indi"]


def html_mevzuat_oku(session: Session, ist, m: ArsivMevzuat, iframe: str, kok: Path, simdi: datetime,
                     gunluk: Gunluk) -> Okuma:
    c = _Cekim(session, ist, m, kok, simdi, gunluk)
    h = _sayfayi_al(c, iframe)
    metin, gorunum, resim_yazilari = _resimleri_isle(c, h)
    yazi = _RESIM_ISARETI.sub("", h.metin).strip()  # sayfanın kendi yazısı (resimler hariç)
    kisa = len(yazi) < KISA_METIN and not resim_yazilari
    c.sonuc.okuma = "html+resim" if resim_yazilari and len(yazi) < KISA_METIN else "html"
    _ekleri_isle(c, h.baglantilar)
    if kisa:
        metin = _ek_metnini_kat(c, metin)
    sayfa_bos = len(yazi) < EN_AZ_METIN and not resim_yazilari and c.sonuc.okuma != "html+ek"
    if not _site_pdfini_al(c, sayfa_bos):
        c.sonuc.metin, c.sonuc.gorunum = metin, gorunum
    _sonucu_degerlendir(c.sonuc, yazi, bool(resim_yazilari))
    return c.sonuc


def pdf_mevzuat_oku(ist, m: ArsivMevzuat, kok: Path, gunluk: Gunluk) -> Okuma:
    sonuc = Okuma()
    r = ist.get(m.url)
    if sitede_yok_mu(r):
        sonuc.sitede_yok = f"PDF sitede yok ({r.status_code} {r.headers.get('location', '')[:80]})".strip()
        return sonuc
    if not (dosya_mi(r) and r.content[:5] == b"%PDF-"):
        r.raise_for_status()
        raise ValueError(f"PDF beklenirken başka içerik geldi ({r.headers.get('content-type')})")
    _pdf_ile_oku(kok, m.anahtar, r.content, "metin.pdf", sonuc, gunluk)
    sonuc.site_pdf_yolu, sonuc.site_pdf_turu = sonuc.pdf_yolu, "asil"
    return sonuc


# --- RG notları (karar 25) ------------------------------------------------------------------------------------

_RG_NOTU = re.compile(
    r"\((?P<tur>Değişik|Ek|Mülga|İptal|Değişik ibare|Ek ibare|Ek cümle|Değişik cümle|Ek fıkra|Mülga fıkra)[^():]{0,40}:\s*"
    r"(?P<govde>[^()]{0,120}?RG\s*[-–]\s*(?P<tarih>\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(?P<sayi>\d+)(?:\s*/\s*\d+\s*md\.?)?)[^()]*\)",
    re.IGNORECASE)


def rg_notlari(metin: str) -> list[dict]:
    from mevzuat.surum import bolumler
    notlar = []
    for bolum, parca in bolumler(metin).items():
        for m in _RG_NOTU.finditer(parca):
            notlar.append({"bolum": bolum, "tur": m.group("tur"), "tarih": m.group("tarih"), "sayi": m.group("sayi")})
    return notlar


# --- sürüm ------------------------------------------------------------------------------------------------------

def surum_kaydet(session: Session, ist, m: ArsivMevzuat, o: Okuma, kok: Path, simdi: datetime,
                 gunluk: Gunluk) -> str:
    """Okumayı gerekiyorsa yeni sürüm olarak yazar. "yeni", "ayni" ya da "okuma_duzeltmesi" döner."""
    dosya_ozetleri = sorted({e["sha256"] for e in o.ekler if e.get("sha256")}
                            | {r["sha256"] for r in o.resimler if r.get("sha256")})
    ozet = sha((md.karsilastirma_metni(o.metin) + "\n" + "\n".join(dosya_ozetleri)).encode("utf-8"))
    kaynak_ozeti = sha("\n".join(o.kaynak_parcalari).encode("utf-8"))
    son = session.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == m.anahtar)
                         .order_by(ArsivSurumu.surum_no.desc()).limit(1))
    kalite_notlari = list(dict.fromkeys(o.kusurlar))
    kalite = "kusurlu" if kalite_notlari or o.okuma in ("okunamadi", "ocr_gerekli") else "temiz"
    m.kalite = kalite
    if son is not None and son.ozet == ozet:
        return "ayni"
    if son is None:
        tur = "ilk"
    elif son.kaynak_ozeti and son.kaynak_ozeti == kaynak_ozeti:
        tur = "okuma_duzeltmesi"
    else:
        tur = "degisiklik"
    # Sitenin ürettiği PDF her istekte farklı bayt veriyor, sadece yeni sürümde (ya da önceki yoksa) alınır.
    if o.uretilen_pdf_adresi and not o.site_pdf_yolu:
        if tur == "okuma_duzeltmesi" and son.site_pdf_yolu:
            o.site_pdf_yolu, o.site_pdf_turu = son.site_pdf_yolu, son.site_pdf_turu
        else:
            b = time.monotonic()
            veri, _, _, durum = dosya_indir(ist, [o.uretilen_pdf_adresi])
            if veri is not None and veri[:5] == b"%PDF-":
                o.site_pdf_yolu = dosya_yaz(kok, "pdf", m.anahtar, f"site-uretilen-{simdi:%Y%m%d}.pdf", veri)
                o.site_pdf_turu = "uretilen"
            else:
                gunluk.uyari(f"sitenin ürettiği PDF alınamadı ({durum})")
            gunluk.adim("uretilen_pdf", b, alindi=o.site_pdf_yolu is not None)
    no = (son.surum_no if son else 0) + 1
    session.add(ArsivSurumu(
        anahtar=m.anahtar, surum_no=no, cekildi=simdi, ozet=ozet, metin=o.metin, okuma=o.okuma, pdf_yolu=o.pdf_yolu,
        ekler=o.ekler, okuyucu_surumu=OKUYUCU_SURUMU, surum_turu=tur, kalite=kalite, kalite_notlari=kalite_notlari,
        kaynak_ozeti=kaynak_ozeti, gorunum=o.gorunum, arama_metni=o.arama_metni or None, sayfalar=o.sayfalar,
        resimler=o.resimler, site_pdf_yolu=o.site_pdf_yolu, site_pdf_turu=o.site_pdf_turu, rg_notlari=rg_notlari(o.metin)))
    m.surum_sayisi = no
    return "okuma_duzeltmesi" if tur == "okuma_duzeltmesi" else "yeni"


def mevzuati_cek(session: Session, ist, m: ArsivMevzuat, kok: Path, simdi: datetime,
                 gunluk: Gunluk | None = None, metin_adresi=None) -> str:
    """Bir mevzuatı çeker, okur, sürümler. Sonuç: yeni / ayni / okuma_duzeltmesi / sitede_yok."""
    gunluk = gunluk or Gunluk(None)
    gunluk.basla(m.anahtar)
    try:
        iframe = metin_adresi(m.anahtar) if (metin_adresi and m.bicim == "html") else None
        if iframe:
            o = html_mevzuat_oku(session, ist, m, iframe, kok, simdi, gunluk)
        else:
            o = pdf_mevzuat_oku(ist, m, kok, gunluk)
        if o.sitede_yok:
            m.durum, m.son_hata, m.son_cekim, m.deneme = "SITEDE_YOK", o.sitede_yok, simdi, 0
            gunluk.bitir(sonuc="sitede_yok", neden=o.sitede_yok)
            return "sitede_yok"
        sonuc = surum_kaydet(session, ist, m, o, kok, simdi, gunluk)
        m.durum = "OCR_GEREKLI" if o.okuma in ("okunamadi", "ocr_gerekli") else "TAMAM"
        m.son_cekim, m.deneme, m.son_hata = simdi, 0, None
        sayfalar = o.sayfalar
        gunluk.bitir(
            sonuc=sonuc, okuma=o.okuma, karakter=len(o.metin), kalite=m.kalite, kusurlar=o.kusurlar[:10],
            sayfa=len(sayfalar), ocr_sayfa=sum(1 for s in sayfalar if s["yontem"].startswith("ocr")),
            gorsel_sayfa=sum(1 for s in sayfalar if s["yontem"] == "gorsel"),
            tablo_yapi=sum(s.get("tablo_yapi", 0) for s in sayfalar),
            tablo_goruntu=sum(s.get("tablo_goruntu", 0) for s in sayfalar),
            resim=len(o.resimler), ek=len(o.ekler), ek_inmeyen=sum(1 for e in o.ekler if e.get("durum") != "indi"),
            site_pdf=o.site_pdf_turu)
        return sonuc
    except Exception as e:
        gunluk.bitir(sonuc="hata", hata=repr(e)[:500], iz=traceback.format_exc()[-3000:])
        raise

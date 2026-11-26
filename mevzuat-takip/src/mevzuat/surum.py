"""Güncel (konsolide) metin takibi. mevzuat.gov.tr'deki metin değişince eski ve yeni halini madde madde karşılaştırır.

Kaynak eklentisi değil de ayrı bir adım olmasının sebebi, fark bulmak için önceki sürümün gerekmesi. O da veritabanında duruyor.

Her çalışmada şu adımlar izlenir.
  1. mevzuat.gov.tr ana sayfasındaki "BUGÜN GÜNCELLENENLER" listesi okunur. RG tarihi bugünden önce olan eski bir
     mevzuatın adı konu filtresine takılırsa takibe alınır, bunlara "otomatik" denir.
     Bugün yayımlananlar atlanır, onları Resmî Gazete kaynağı zaten yakalıyor.
  2. Takip listesi config/izlenen_mevzuat.toml dosyasındakiler ("ayar") ve daha önce otomatik eklenenlerden oluşur.
  3. Metni arşive (arsiv.py) çekilenler: bugün güncellenenlerin hepsi, takipteki bir mevzuat da son kontrolden bu
     yana KONTROL_ARALIGI geçtiyse. Liste sadece bugünü gösterdiği için sunucunun kapalı kaldığı günlerde kaçan
     güncellemeler takiptekilerde en geç bu sürede yakalanır. Çekim arşiv işiyle aynı (arsiv_cekim.mevzuati_cek):
     metin, resimler, ekler, sitenin PDF'i; değiştiyse arşive yeni sürüm yazılır, arama dizini ve konu filtresi güncellenir.
  4. Arşiv taban. Arşivdeki son sürüm v1 ise yeni hal v2 olur ve v1↔v2 farkı "Değişen mevzuat" kaydı olarak rapor ve
     onay akışına girer, sonra v2↔v3. Arşivde olmayan mevzuat açılır, ilk sürümü rapor üretmez. Sitenin kaynağı aynı
     kalıp okuma iyileştiyse (okuma düzeltmesi) rapor yok. Takipte olmayan mevzuatın değişikliği adı ya da değişen
     maddeleri bir konuya uyuyorsa rapora girer. Eski metin_surumleri tablosu yeni veri almaz, eski satırlar kanıt olarak durur.

Örnek bir config/izlenen_mevzuat.toml kaydı aşağıda.
    [[mevzuat]]
    ad = "Özel Tüketim Vergisi Kanunu"
    tur = 1          # mevzuat.gov.tr adresindeki MevzuatTur
    tertip = 5       # MevzuatTertip
    no = 4760        # MevzuatNo
    konu = "Vergi"   # config/konular.toml dosyasındaki konu adı, başlıkta kelime geçmese de ilgili sayılır
"""
# "Değişiklik tespiti" (diff) burada, takip edilen kanun/yönetmeliklerin güncel metni değişince,
# eski ve yeni hali madde madde karşılaştırılır ve fark onaya sunulur.

# difflib, iki metni karşılaştırıp farkları bulan Python kütüphanesi.
import difflib
import hashlib
import logging
import re
import time
import tomllib

# asdict, dataclass nesnesini sözlüğe çevirir (veritabanına JSON olarak yazmak için).
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# parse_qs adresteki parametre kısmını sözlüğe çevirir.
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat.db import (
    ArsivMevzuat,
    ArsivSurumu,
    Calisma,
    IzlenenMevzuat,
    Kayit,
    SistemAyari,
)
from mevzuat.filtre import Konu, eslesmeler, genel_dislama, is_kollari
from mevzuat.icerik import html_metni

log = logging.getLogger(__name__)

# Bu adımın kayıtlarda görünen kaynak adı.
KAYNAK_ADI = "mevzuat_gov"
ANA_SAYFA = "https://www.mevzuat.gov.tr/"
# Bir mevzuatın güncel metninin adresi, {tur}, {no}, {tertip} doldurulur.
METIN_URL = "https://www.mevzuat.gov.tr/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={tur}&MevzuatNo={no}&MevzuatTertip={tertip}"
# Listede çıkmasa bile her mevzuat en geç 7 günde bir kontrol edilir.
KONTROL_ARALIGI = timedelta(days=7)
# "Bugün güncellenenler" listesinde yeni yayınlar da var, mevzuat.gov.tr bunları bir-iki gün geç işliyor
# (02.10.2026 listesinde 01.10 tarihli CB kararları). RG tarihi bu kadar yeni olan "metni güncellendi" değil,
# "yeni yayımlandı" sayılır ve otomatik takibe alınmaz, onları Resmî Gazete kaynağı zaten yakalıyor.
YENI_YAYIN_SURESI = timedelta(days=30)
ISTEK_ARASI_BEKLEME = 1.0
# Site bazı isteklerde listesi boş bir ana sayfa veriyor (08.10.2026, 8 istekten 1'i). Boş gelirse bu kadar kez tekrar istenir.
LISTE_DENEME = 3
# Rapordaki bir fark parçasının en fazla kaç karakteri gösterilir (tamamı arşivde, panelde).
PARCA_UZUNLUGU = 1200
# Eklenen ya da kaldırılan maddenin en fazla kaç karakteri gösterilir.
MADDE_UZUNLUGU = 3000
# Bir maddede en fazla bu kadar fark parçası gösterilir, fazlası "… N değişiklik daha" satırı olur.
BOLUM_BASINA_FARK = 12
# Çıkarılan/eklenen ifade listesinde bir ifadenin en fazla uzunluğu.
IFADE_UZUNLUGU = 300


# --- ayar ----------------------------------------------------------------------------------------

# Takip listesindeki bir mevzuat, adı, mevzuat.gov.tr numaraları (tür, tertip, no), konusu.
@dataclass(frozen=True)
class IzlenenTanim:
    ad: str
    tur: int
    tertip: int
    no: int
    konu: str | None = None

    # Tek bir kimlik, "1.5.4760" gibi.
    @property
    def anahtar(self) -> str:
        return anahtar(self.tur, self.tertip, self.no)


# Tür, tertip ve numarayı noktayla birleştirip tek kimlik yapar.
def anahtar(tur: int | str, tertip: int | str, no: int | str) -> str:
    return f"{tur}.{tertip}.{no}"


# "tür.tertip.no" kimliğinden metin adresini üretir.
def metin_url(anahtar_: str) -> str:
    tur, tertip, no = anahtar_.split(".")
    return METIN_URL.format(tur=tur, tertip=tertip, no=no)


# izlenen_mevzuat.toml dosyasını okur.
def izlenenleri_yukle(yol: Path) -> list[IzlenenTanim] | None:
    """Dosyada `aktif = false` yazıyorsa None döner. O zaman güncel metin takibi hiç çalışmaz ve mevzuat.gov.tr'ye hiç gidilmez."""
    with open(yol, "rb") as f:
        veri = tomllib.load(f)
    # Dosyada "aktif = false" yazıyorsa takip tamamen kapalı.
    if not veri.get("aktif", True):
        return None
    tanimlar = veri.get("mevzuat", [])
    sonuc = [IzlenenTanim(**t) for t in tanimlar]
    # Aynı mevzuat iki kez yazılmışsa yakala.
    tekrar = {t.anahtar for t in sonuc if sum(1 for u in sonuc if u.anahtar == t.anahtar) > 1}
    if tekrar:
        raise ValueError(f"izlenen_mevzuat.toml'da tekrar eden mevzuat: {sorted(tekrar)}")
    return sonuc


# --- mevzuat.gov.tr ------------------------------------------------------------------------------

# "Bugün güncellenenler" listesindeki bir satır.
@dataclass(frozen=True)
class Guncellenen:
    anahtar: str
    ad: str
    # Mevzuatın ilk yayımlandığı Resmî Gazete tarihi.
    rg_tarihi: date | None
    sadece_pdf: bool = False  # bazı Cumhurbaşkanı kararlarının HTML metni yok (sayfa boş geliyor)
    # Listedeki bağlantı (arşivde olmayan mevzuat bu adresle açılır).
    href: str = ""


# Listedeki linkten "tür.tertip.no" kimliğini çıkarır (iki farklı link biçimi var).
def _linkten_anahtar(href: str) -> str | None:
    """Linkler iki biçimde geliyor. Biri "mevzuat?MevzuatNo=4760&MevzuatTur=1&MevzuatTertip=5" gibi, öbürü
    Cumhurbaşkanı kararlarındaki doğrudan PDF, "/MevzuatMetin/20.5.11832.pdf" gibi (tür.tertip.no)."""
    url = urlparse(urljoin(ANA_SAYFA, href))
    q = parse_qs(url.query)
    # 1. biçim, adreste üç parametre de varsa.
    if {"MevzuatTur", "MevzuatTertip", "MevzuatNo"} <= q.keys():
        return anahtar(q["MevzuatTur"][0], q["MevzuatTertip"][0], q["MevzuatNo"][0])
    # 2. biçim, dosya adında "20.5.11832.pdf".
    m = re.search(r"/MevzuatMetin/(\d+)\.(\d+)\.(\d+)\.\w+$", url.path)
    return anahtar(*m.groups()) if m else None


# Ana sayfanın HTML'inden "bugün güncellenenler" listesini çıkarır.
def parse_guncellenenler(sayfa: str) -> list[Guncellenen]:
    """Ana sayfadaki "BUGÜN GÜNCELLENENLER" sekmesi (düz HTML, JS gerekmez)."""
    pane = HTMLParser(sayfa).css_first("#bugunGuncellenenMevzuat")
    if pane is None:
        raise ValueError("mevzuat.gov.tr: 'bugün güncellenenler' bölümü bulunamadı; site tasarımı değişmiş olabilir")
    sonuc = []
    # Tablonun her satırı bir mevzuat.
    for satir in pane.css("tbody tr"):
        link = satir.css_first("td.small a")
        if link is None:
            continue
        anahtar_ = _linkten_anahtar(link.attributes.get("href", ""))
        if anahtar_ is None:
            log.warning("mevzuat.gov.tr: tanınmayan bağlantı biçimi atlandı: %s", link.attributes.get("href"))
            continue
        # Satırdaki "Resmî Gazete Tarihi: 01.10.2026" yazısını bul.
        tarih = re.search(r"Resmî Gazete Tarihi:\s*(\d{2}\.\d{2}\.\d{4})", satir.text())
        sonuc.append(Guncellenen(
            anahtar=anahtar_,
            ad=" ".join(link.text().split()),
            rg_tarihi=datetime.strptime(tarih.group(1), "%d.%m.%Y").date() if tarih else None,
            sadece_pdf="/MevzuatMetin/" in link.attributes.get("href", ""),
            href=link.attributes.get("href", ""),
        ))
    return sonuc


# Ana sayfayı indirip "bugün güncellenenler" listesini çıkarır, liste boş gelirse birkaç kez tekrar ister.
def guncellenenleri_cek(client: httpx.Client) -> list[Guncellenen]:
    """Gerçekten güncelleme olmayan günde liste her denemede boş gelir, o zaman boş liste döner."""
    for deneme in range(LISTE_DENEME):
        if deneme:
            time.sleep(ISTEK_ARASI_BEKLEME)
        response = client.get(ANA_SAYFA)
        response.raise_for_status()
        liste = parse_guncellenenler(response.text)
        if liste:
            return liste
        log.info("mevzuat.gov.tr: bugün güncellenenler listesi boş geldi (deneme %d/%d)", deneme + 1, LISTE_DENEME)
    return []


# Bir mevzuatın güncel metnini indirip düz metne çevirir.
def metin_cek(client: httpx.Client, anahtar_: str) -> str:
    response = client.get(metin_url(anahtar_))
    response.raise_for_status()
    metin = html_metni(response.text)
    if len(metin) < 200:
        # Olmayan numara da 200 dönüyor (boş sayfa). Boş metni sürüm diye saklarsak sonra "her şey eklendi" çıkar.
        raise ValueError(f"{anahtar_}: metin boş geldi ({len(metin)} karakter)")
    return metin


# Metnin SHA-256 özeti. Metin bir harf bile değişse özet tamamen değişir, böylece metnin değişip değişmediği hızlıca anlaşılır.
def ozet(metin: str) -> str:
    return hashlib.sha256(metin.encode("utf-8")).hexdigest()


# --- madde madde karşılaştırma ---------------------------------------------------------------------

# Madde başlıkları, "Madde 12 –", "MADDE 9/A-", "Ek Madde 3 –", "Geçici Madde 5 –", "Mükerrer Madde 115 –".
# Tire şart, metin içindeki atıflar ("Madde 6, Geçici Madde 7") başlık sayılmasın.
# Ekli listeler, "(I) SAYILI LİSTE" (büyük harf, metin içindeki "(I) sayılı liste" atıfları eşleşmez).
_BOLUM = re.compile(
    r"\b(?P<on>(?:Ek|EK|Geçici|GEÇİCİ|Mükerrer|MÜKERRER)\s+)?(?:Madde|MADDE)\s+(?P<no>\d+(?:\s*/\s*[A-ZÇĞİÖŞÜ])?)\s*[–—-]"
    r"|(?P<liste>\((?:[IVX]+)\)\s+SAYILI\s+L[İI]STE)"
)


# str.capitalize() Türkçe İ harfini bozuyor, o yüzden ön ekler sabit tabloyla yazılır.
_ON_EKLER = {"EK": "Ek", "GEÇİCİ": "Geçici", "MÜKERRER": "Mükerrer"}


# Bulunan madde başlığından düzgün bir bölüm adı üretir, örneğin "GEÇİCİ MADDE 5" yerine "Geçici Madde 5".
def _bolum_adi(m: re.Match) -> str:
    # Liste başlığıysa "SAYILI LİSTE" yazısını "Sayılı Liste" yap.
    if m.group("liste"):
        return re.sub(r"\s+SAYILI\s+L[İI]STE", " Sayılı Liste", m.group("liste"))
    # Ön eki (Ek/Geçici/Mükerrer) düzgün yaz.
    on = (m.group("on") or "").strip()
    on = _ON_EKLER.get(on.upper().replace("I", "İ") if on else "", on)
    # Madde numarasındaki boşlukları at, "9 / A" yazısı "9/A" olsun.
    no = re.sub(r"\s+", "", m.group("no"))
    return f"{on + ' ' if on else ''}Madde {no}"


# Metni maddelere böler, {"Madde 1", "...", "Madde 2", "...", ...}.
def bolumler(metin: str) -> dict[str, str]:
    """Metni bölümlere ayırır, örneğin "Başlangıç", "Madde 1", "Geçici Madde 2", "(I) Sayılı Liste" gibi anahtarlarla.
    Satır sonları önemsenmez, çünkü kaynak HTML Word'den geliyor ve "Madde" ile "1 –" ayrı satırlarda olabiliyor."""
    # Bütün metni tek satıra indir.
    duz = " ".join(metin.split())
    sonuc: dict[str, str] = {}
    # Bütün madde başlıklarının yerlerini bul.
    eslesmeler_ = list(_BOLUM.finditer(duz))
    # Her bölümün başladığı yer ve adı, ilk maddeden önceki kısım "Başlangıç".
    sinirlar = [(0, "Başlangıç")] + [(m.start(), _bolum_adi(m)) for m in eslesmeler_]
    for i, (bas, ad) in enumerate(sinirlar):
        # Bölüm, bir sonraki bölümün başına kadar sürer.
        son = sinirlar[i + 1][0] if i + 1 < len(sinirlar) else len(duz)
        parca = duz[bas:son].strip()
        if not parca:
            continue
        # Aynı ad ikinci kez geçerse (ör. eklerdeki tablolarda "Madde 1 –") ayrı tutulur.
        anahtar_ = ad
        n = 2
        while anahtar_ in sonuc:
            anahtar_ = f"{ad} ({n})"
            n += 1
        sonuc[anahtar_] = parca
    return sonuc


# Tek bir fark, hangi madde, ne oldu, eski ve yeni hali (değişen kısım «» içinde), çıkarılan ve eklenen ifadeler.
@dataclass(frozen=True)
class Fark:
    bolum: str
    tur: str  # "eklendi" / "kaldırıldı" / "değişti" / "mülga edildi"
    eski: str | None
    yeni: str | None
    # Bu parçada metinden çıkarılan ve metne eklenen ifadeler, sırasıyla (değişti / mülga edildi). Eski kayıtlarda yok.
    cikan: tuple[str, ...] = ()
    giren: tuple[str, ...] = ()


# Uzun metni sınıra (varsayılan PARCA_UZUNLUGU) kısaltıp sonuna "…" koyar.
def _kisalt(metin: str, sinir: int = PARCA_UZUNLUGU) -> str:
    return metin if len(metin) <= sinir else metin[: sinir - 1].rstrip() + "…"


# Madde metni "(Mülga: RG-…)" notuna indiyse "mülga edildi" denir.
_MULGA_NOTU = re.compile(r"\(\s*Mülga\b", re.IGNORECASE)


# Bir maddenin eski ve yeni halini kelime kelime karşılaştırır, değişen kelimeleri «» içine alır.
def _degisen_parcalar(eski: str, yeni: str, baglam: int = 8) -> tuple[list[tuple[str, str, tuple, tuple]], int]:
    """Kelime bazında karşılaştırma. Değişen kelimeler «» içinde, çevresinde `baglam` kelime.
    Birbirine yakın değişiklikler tek parçada birleşir. Her parça (eski, yeni, çıkan ifadeler, giren ifadeler);
    ikinci değer gösterilmeyen (BOLUM_BASINA_FARK üstü) parça sayısı."""
    # Metinleri kelime listesine çevir.
    e, y = eski.split(), yeni.split()
    # difflib farkları "işlem" listesi olarak verir, "aynı" olanları at, sadece değişenler kalsın.
    islemler = [op for op in difflib.SequenceMatcher(a=e, b=y, autojunk=False).get_opcodes() if op[0] != "equal"]
    # Birbirine yakın (16 kelimeden yakın) değişiklikleri aynı grupta topla.
    gruplar: list[list[tuple]] = []
    for op in islemler:
        if gruplar and op[1] - gruplar[-1][-1][2] <= 2 * baglam:
            gruplar[-1].append(op)
        else:
            gruplar.append([op])

    # İç yardımcı, kelime listesinin bir parçasını alır, değişen aralıkları «» içine koyar, baş/sona "…" ekler.
    def isaretle(kelimeler: list[str], araliklar: list[tuple[int, int]], bas: int, son: int) -> str:
        parcalar = ["…"] if bas > 0 else []
        i = bas
        for a, b in araliklar:
            # Değişmeyen kısım.
            parcalar += kelimeler[i:a]
            # Değişen kısım «» içinde (silinmişse boş «»).
            parcalar.append("«" + " ".join(kelimeler[a:b]) + "»" if b > a else "«»")
            i = b
        parcalar += kelimeler[i:son]
        if son < len(kelimeler):
            parcalar.append("…")
        return " ".join(parcalar)

    sonuc = []
    # Her grup için eski ve yeni metinden çevresiyle birlikte bir parça çıkar (en fazla BOLUM_BASINA_FARK grup).
    for grup in gruplar[:BOLUM_BASINA_FARK]:
        # Parçanın başı ve sonu, ilk değişiklikten 8 kelime önce, son değişiklikten 8 kelime sonra.
        e_bas, e_son = max(0, grup[0][1] - baglam), min(len(e), grup[-1][2] + baglam)
        y_bas, y_son = max(0, grup[0][3] - baglam), min(len(y), grup[-1][4] + baglam)
        cikan = tuple(_kisalt(" ".join(e[op[1]:op[2]]), IFADE_UZUNLUGU) for op in grup if op[2] > op[1])
        giren = tuple(_kisalt(" ".join(y[op[3]:op[4]]), IFADE_UZUNLUGU) for op in grup if op[4] > op[3])
        sonuc.append((
            _kisalt(isaretle(e, [(op[1], op[2]) for op in grup], e_bas, e_son)),
            _kisalt(isaretle(y, [(op[3], op[4]) for op in grup], y_bas, y_son)),
            cikan, giren,
        ))
    return sonuc, max(0, len(gruplar) - BOLUM_BASINA_FARK)


# Boşluk, tablo hücre ayırıcısı ve işaretler atılmış hal. Sadece bunlarda ayrılan iki metin aynı sayılır: okuyucu
# değişince "sayılır. [1]" ile "sayılır.[1]", tablo satırında "|" farkı çıkıyor (yerel v1 okuyucu 1 ile ölçüldü, 2026-11-26).
_BICIM = re.compile(r"[\s|«»…]+")


# Sitenin dipnot işaretleri "[22]": metne yeni dipnot eklenince sonrakilerin hepsi kayıyor ([22] → [24]), bu tek başına
# değişiklik sayılmaz (1.5.6222 Sporda Şiddet Kanunu, 2026-11-26: 7 maddede sadece dipnot numarası farkı).
_DIPNOT = re.compile(r"\[\d{1,3}\]")


def _ayni_yazi(a: str, b: str) -> bool:
    return _BICIM.sub("", _DIPNOT.sub("", a)) == _BICIM.sub("", _DIPNOT.sub("", b))


# İki metin arasındaki bütün farkları madde madde listeler.
def farklar(eski_metin: str, yeni_metin: str) -> list[Fark]:
    eski, yeni = bolumler(eski_metin), bolumler(yeni_metin)
    sonuc = []
    # Yeni metindeki her madde, eskide yoksa "eklendi", varsa ve farklıysa "değişti" (ya da "mülga edildi").
    for ad, metin in yeni.items():
        if ad not in eski:
            sonuc.append(Fark(ad, "eklendi", None, _kisalt(metin, MADDE_UZUNLUGU)))
        elif eski[ad] != metin and not _ayni_yazi(eski[ad], metin):
            tur = "mülga edildi" if _MULGA_NOTU.search(metin) and not _MULGA_NOTU.search(eski[ad]) else "değişti"
            parcalar, kalan = _degisen_parcalar(eski[ad], metin)
            for e, y, cikan, giren in parcalar:
                # Maddede gerçek değişiklik var ama bu parça sadece biçim farkı.
                if not _ayni_yazi(e, y):
                    sonuc.append(Fark(ad, tur, e, y, cikan, giren))
            if kalan:
                sonuc.append(Fark(ad, tur, None, f"… bu maddede {kalan} değişiklik daha var "
                                                 "(tamamı panelde: Arşiv → mevzuat → Değişiklikler)."))
    # Eski metinde olup yenide olmayan madde, "kaldırıldı".
    for ad, metin in eski.items():
        if ad not in yeni:
            sonuc.append(Fark(ad, "kaldırıldı", _kisalt(metin, MADDE_UZUNLUGU), None))
    return sonuc


# Fark listesini okunur bir metne çevirir (kaydın içerik alanına yazılır).
def fark_metni(fark_listesi: list[Fark]) -> str:
    """Kaydın içerik alanı için okunur özet (panel, filtre ve düz metin mail bunu kullanır)."""
    satirlar = []
    for f in fark_listesi:
        if f.tur == "eklendi":
            satirlar.append(f"{f.bolum} eklendi: {f.yeni}")
        elif f.tur == "kaldırıldı":
            satirlar.append(f"{f.bolum} kaldırıldı (eski hali): {f.eski}")
        elif f.eski is None:
            satirlar.append(f"{f.bolum}: {f.yeni}")
        else:
            satirlar.append(f"{f.bolum} {f.tur}.\n  Eski: {f.eski}\n  Yeni: {f.yeni}")
    return "\n".join(satirlar)


# Raporun başındaki tek satırlık özet: "Madde 6 değişti; Ek Madde 1 eklendi; Geçici Madde 1 kaldırıldı".
def fark_ozeti(fark_listesi: list[dict]) -> str:
    gruplar: dict[str, list[str]] = {}
    for f in fark_listesi:
        bolumler_ = gruplar.setdefault(f.get("tur") or "değişti", [])
        if f.get("bolum") and f["bolum"] not in bolumler_:
            bolumler_.append(f["bolum"])
    sira = ("değişti", "mülga edildi", "eklendi", "kaldırıldı")
    return "; ".join(f"{', '.join(gruplar[t])} {t}" for t in sira if gruplar.get(t))


# --- günlük adım ---------------------------------------------------------------------------------

# Takip tablosunda mevzuatın satırını bulur ya da oluşturur.
def _izlenen_kaydi(session: Session, anahtar_: str, ad: str, neden: str, konu: str | None, simdi: datetime) -> IzlenenMevzuat:
    izlenen = session.get(IzlenenMevzuat, anahtar_)
    if izlenen is None:
        izlenen = IzlenenMevzuat(anahtar=anahtar_, ad=ad, neden=neden, konu=konu, eklendi=simdi, son_kontrol=None)
        session.add(izlenen)
    elif neden == "ayar":
        # Ayar dosyası belirleyicidir (ad/konu orada düzeltilmiş olabilir), otomatik eklenen ayara alınabilir.
        izlenen.ad, izlenen.konu, izlenen.neden = ad, konu, "ayar"
    return izlenen


# Metni değişen mevzuat için rapora girecek bir kayıt oluşturur, başlığı "güncel metni değişti" diye biter.
def _degisiklik_kaydi(
    session: Session, m: ArsivMevzuat, izlenen: IzlenenMevzuat | None, fark_listesi: list[Fark], surum_no: int,
    konular: list[Konu], agirlik: dict[str, float], calisma: Calisma, bugun: date, simdi: datetime,
) -> Kayit | None:
    """Rapor şartı: mevzuat takipte, adı bir konuya uyuyor ya da değişen maddeler puanla bir konuyla ilgili.
    Hiçbiri değilse kayıt açılmaz (None); değişiklik yine arşivde yeni sürüm olarak durur."""
    ad = izlenen.ad if izlenen else m.ad
    # Mevzuatın adı hangi konulara uyuyor, takip listesinde konusu yazılıysa onu da ekle.
    eslesen = eslesmeler(ad, konular)
    if izlenen is not None and izlenen.konu:
        eslesen.setdefault(izlenen.konu, ["(takip listesindeki mevzuat)"])
    icerik = fark_metni(fark_listesi)
    # Değişen maddeler de puanlanır (arşivle aynı puan): KDV tebliğinin altın bölümü değiştiyse Kuyum'a da gider,
    # değişen metinde bir kez "altın" geçmesi ise yetmez.
    from mevzuat import pipeline

    eslesen = pipeline.icerikle_eslesmeler("", icerik, konular, eslesen, agirlik, ek="(değişiklikte)")
    # Bütün konulardan dışlanan mevzuat (üniversite iç yönetmeliği) değişince rapora girmez; takipteyse girer.
    if izlenen is None and genel_dislama(ad):
        return None
    if not eslesen and izlenen is None:
        return None
    kayit = Kayit(
        kaynak=KAYNAK_ADI,
        # Arşiv sürüm numarası: metin eski haline dönse bile her değişiklik ayrı kayıttır. Eski kayıtlar
        # "anahtar@<metin_surumleri.id>" biçiminde, "@v" ile çakışmaz.
        dis_id=f"{m.anahtar}@v{surum_no}",
        yayin_tarihi=bugun,
        baslik=f"{ad} — güncel metni değişti",
        tur="Değişen mevzuat",
        bolum="",
        sayi=None,
        mukerrer=0,
        url=m.url or metin_url(m.anahtar),
        kaynakca=f"Mevzuat Bilgi Sistemi, {ad}, güncel metin {bugun:%d.%m.%Y}",
        # Takipteki mevzuat zaten ilgili seçilmiş; takipte değilse konulara uyduğu için buraya geldi.
        ilgili=True,
        eslesmeler=eslesen,
        is_kollari=is_kollari(eslesen, konular),
        icerik=icerik,
        # İçerik zaten elimizde (fark metni), indirmeye gerek yok.
        icerik_durumu="TAMAM",
        ilk_gorulme=simdi,
        calisma_id=calisma.id,
        # Farkların ham listesi (rapor eski/yeni tablosunu bundan çiziyor).
        degisiklikler=[asdict(f) for f in fark_listesi],
    )
    session.add(kayit)
    return kayit


# --- arşivle bağlantı ----------------------------------------------------------------------------

# Tür numarası (anahtarın ilk parçası) → arşivin listeleme türü (arsiv.TURLER). Arşivde olmayan bir mevzuat bugün
# güncellenenlerde ya da takip listesinde çıkınca bununla açılır. Sunucu arşivinde ölçüldü (2026-11-26, 19.346 kayıt):
# her numara tek türde. 5, 24, 25 sadece mülga listesinde, onlar açılmaz.
TUR_NUMARALARI = {
    "0": "Kanun", "1": "Kanun", "2": "Tuzuk", "6": "Tuzuk", "17": "Tuzuk", "4": "KHK",
    "3": "TumYonetmelik", "7": "TumYonetmelik", "8": "TumYonetmelik", "21": "TumYonetmelik",
    "9": "Teblig", "19": "CumhurbaskaniKararnameleri", "20": "CumhurbaskaniKararlari",
}
# Arşiv işi sürerken (dosya kilidi dolu) ya da hata yüzünden bakılamayanlar burada bekler, sonraki çalışmada denenir.
BEKLEYEN_AYARI = "arsiv_takip_bekleyen"


def _bekleyenler(session: Session) -> list[str]:
    ayar = session.get(SistemAyari, BEKLEYEN_AYARI)
    return list(ayar.deger) if ayar is not None and isinstance(ayar.deger, list) else []


def _bekleyenleri_yaz(session: Session, anahtarlar: set[str], simdi: datetime) -> None:
    ayar = session.get(SistemAyari, BEKLEYEN_AYARI)
    if ayar is None:
        if anahtarlar:
            session.add(SistemAyari(anahtar=BEKLEYEN_AYARI, deger=sorted(anahtarlar), guncelleme=simdi))
        return
    ayar.deger, ayar.guncelleme = sorted(anahtarlar), simdi


# Arşivde olmayan mevzuatı açar (metni henüz yok). Tür numarası tanınmıyorsa None.
def _arsive_ekle(session: Session, anahtar_: str, g: Guncellenen | None, izlenen: IzlenenMevzuat | None,
                 simdi: datetime) -> ArsivMevzuat | None:
    tur = TUR_NUMARALARI.get(anahtar_.split(".")[0])
    if tur is None:
        return None
    t, tertip, no = anahtar_.split(".")
    pdf = g is not None and g.sadece_pdf
    url = (urljoin(ANA_SAYFA, g.href) if g is not None and g.href
           else f"{ANA_SAYFA}mevzuat?MevzuatNo={no}&MevzuatTur={t}&MevzuatTertip={tertip}")
    m = ArsivMevzuat(anahtar=anahtar_, tur=tur, ad=(g.ad if g else izlenen.ad if izlenen else anahtar_), url=url,
                     rg_tarihi=g.rg_tarihi if g else None, rg_sayisi=None, bicim="pdf" if pdf else "html",
                     mulga=False, ilk_gorulme=simdi, son_gorulme=simdi, kayip_sayisi=0, durum="BEKLIYOR",
                     deneme=0, surum_sayisi=0)
    session.add(m)
    session.commit()
    return m


def _son_surum(session: Session, anahtar_: str) -> ArsivSurumu | None:
    return session.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar_)
                          .order_by(ArsivSurumu.surum_no.desc()).limit(1))


@dataclass
class _Baglam:
    """Bir takip çalışmasında her mevzuat için aynı kalanlar."""
    session: Session
    ist: object  # arsiv.Istemci
    klasor: Path
    gunluk: object  # arsiv_cekim.Gunluk
    konular: list[Konu]
    agirlik: dict[str, float]
    calisma: Calisma
    bugun: date
    simdi: datetime


# Bir mevzuatı arşive çeker, değiştiyse önceki arşiv sürümüyle karşılaştırır. Sonuç sayacın anahtarı.
def _arsivle_karsilastir(b: _Baglam, anahtar_: str, g: Guncellenen | None, izlenen: IzlenenMevzuat | None) -> str:
    """Sonuçlar: taban (arşive ilk sürüm), degisen (rapor kaydı açıldı), ilgisiz_degisen (değişti ama hiçbir konuya
    uymuyor), ayni, okuma_duzeltmesi (kaynak aynı, rapor yok, karar 19), sitede_yok, arsive_eklenen (PDF'li yeni
    yayın, metni haftalık işte), atlandi (tür tanınmadı), hata."""
    from mevzuat import arsiv, arsiv_cekim

    s = b.session
    m = s.get(ArsivMevzuat, anahtar_)
    if m is None:
        m = _arsive_ekle(s, anahtar_, g, izlenen, b.simdi)
        if m is None:
            log.warning("Arşivde yok ve türü tanınmadı, atlandı: %s", anahtar_)
            return "atlandi"
    # Cumhurbaşkanı kararları gibi PDF'li yeni yayınlarda OCR uzun sürebilir; henüz hiç sürümü yoksa metni haftalık
    # liste işi alır (liste ertesi gün de aynı yeni yayınları gösteriyor).
    if m.bicim == "pdf" and not m.surum_sayisi:
        return "arsive_eklenen"
    onceki = _son_surum(s, anahtar_)
    onceki_no, onceki_metin = (onceki.surum_no, onceki.metin) if onceki else (0, None)
    try:
        sonuc = arsiv_cekim.mevzuati_cek(s, b.ist, m, b.klasor, b.simdi, b.gunluk, arsiv.metin_adresi)
    except Exception as e:
        s.rollback()
        m = s.get(ArsivMevzuat, anahtar_)
        m.durum, m.deneme, m.son_hata = "HATA", m.deneme + 1, repr(e)[:1000]
        s.commit()
        log.warning("Güncel metin arşive alınamadı: %s (%s): %r", m.ad[:80], anahtar_, e)
        return "hata"
    if sonuc in ("yeni", "okuma_duzeltmesi"):
        arsiv.dizin_ve_filtre(s, anahtar_, b.konular, b.simdi)
    if sonuc != "yeni":
        return sonuc
    yeni = _son_surum(s, anahtar_)
    if onceki_metin is None or yeni is None or yeni.surum_no == onceki_no:
        return "taban"  # ilk sürüm, karşılaştıracak önceki hal yok, rapor üretilmez
    fark_listesi = farklar(onceki_metin, yeni.metin)
    if not fark_listesi:
        return "ayni"  # sürüm farkı eklerde ya da yalnız biçimde; maddelerde fark yok
    kayit = _degisiklik_kaydi(s, m, izlenen, fark_listesi, yeni.surum_no, b.konular, b.agirlik, b.calisma,
                              b.bugun, b.simdi)
    return "degisen" if kayit is not None else "ilgisiz_degisen"


# Her gün çalışan adım bu. Takip listesini günceller; bugün güncellenenleri ve vakti gelen takiptekileri arşive çeker,
# arşivde yeni sürüm (değişiklik) çıktıysa önceki sürümle karşılaştırıp fark kaydı oluşturur.
def takip_et(
    session: Session,
    client: httpx.Client,
    tanimlar: list[IzlenenTanim],
    konular: list[Konu],
    calisma: Calisma,
    bugun: date,
    simdi: datetime,
    klasor: Path | None = None,
) -> dict:
    """Arşiv taban: mevzuatın arşivdeki son sürümü v1 ise yeni hali v2 olur ve v1↔v2 rapora girer, sonra v2↔v3.
    Arşivde olmayan mevzuat açılır, ilk sürümü rapor üretmez (yeni yayını Resmî Gazete kaynağı zaten getiriyor).
    Arşiv işi sürüyorsa (arsiv.lock dolu) bu çalışmada arşive dokunulmaz, bakılacaklar sonraki çalışmaya kalır."""
    from filelock import FileLock, Timeout

    from mevzuat import arama, arsiv
    from mevzuat.arsiv_cekim import Gunluk

    # Özette görünecek sayaçlar.
    ozet_sayac = {"taban": 0, "degisen": 0, "ilgisiz_degisen": 0, "ayni": 0, "okuma_duzeltmesi": 0, "sitede_yok": 0,
                  "arsive_eklenen": 0, "atlandi": 0, "hata": 0, "ertelenen": 0, "otomatik_eklenen": 0}

    # Ana sayfayı indir, "bugün güncellenenler" listesini çıkar.
    bugun_guncellenen = {g.anahtar: g for g in guncellenenleri_cek(client)}

    # Ayar dosyasındaki her mevzuatı takip tablosuna ekle/güncelle.
    for t in tanimlar:
        _izlenen_kaydi(session, t.anahtar, t.ad, "ayar", t.konu, simdi)
    # Bugün güncellenenlerden eski tarihli (30 günden eski), PDF olmayan, konulara takılan ve henüz takipte olmayanları otomatik ekle.
    for g in bugun_guncellenen.values():
        metni_guncellendi = g.rg_tarihi is not None and g.rg_tarihi < bugun - YENI_YAYIN_SURESI
        if (metni_guncellendi and not g.sadece_pdf and eslesmeler(g.ad, konular)
                and session.get(IzlenenMevzuat, g.anahtar) is None):
            _izlenen_kaydi(session, g.anahtar, g.ad, "otomatik", None, simdi)
            ozet_sayac["otomatik_eklenen"] += 1
    session.commit()

    ayardakiler = {t.anahtar for t in tanimlar}
    # Ayar dosyasından çıkarılan takip bırakılır (eski kayıtlar kanıt olarak durur).
    izlenenler = {i.anahtar: i for i in session.scalars(select(IzlenenMevzuat)).all()
                  if not (i.neden == "ayar" and i.anahtar not in ayardakiler)}
    # Bakılacaklar: önceki çalışmadan kalanlar, bugün güncellenenlerin hepsi, son kontrolden 7 gün geçen takiptekiler.
    bakilacak: dict[str, Guncellenen | None] = dict.fromkeys(_bekleyenler(session))
    bakilacak.update(bugun_guncellenen)
    for i in izlenenler.values():
        if i.son_kontrol is None or simdi - i.son_kontrol >= KONTROL_ARALIGI:
            bakilacak.setdefault(i.anahtar, None)

    klasor = klasor or arsiv.arsiv_klasoru()
    klasor.mkdir(parents=True, exist_ok=True)
    kilit = FileLock(str(klasor / "arsiv.lock"), timeout=0)
    try:
        kilit.acquire()
    except Timeout:
        log.warning("Arşiv işi sürüyor (arsiv.lock dolu); %d mevzuat sonraki çalışmaya kaldı", len(bakilacak))
        _bekleyenleri_yaz(session, set(bakilacak), simdi)
        session.commit()
        ozet_sayac["ertelenen"] = len(bakilacak)
        return ozet_sayac
    try:
        gunluk = Gunluk(klasor / "gunluk" / f"takip-{simdi:%Y%m%d-%H%M%S}.jsonl")
        b = _Baglam(session, arsiv.Istemci(client, gunluk=gunluk), klasor, gunluk, konular,
                    arama.kelime_agirliklari(session), calisma, bugun, simdi)
        kalan: set[str] = set()
        # Önce takiptekiler.
        for anahtar_ in sorted(bakilacak, key=lambda a: (a not in izlenenler, a)):
            izlenen = izlenenler.get(anahtar_)
            sonuc = _arsivle_karsilastir(b, anahtar_, bakilacak[anahtar_], izlenen)
            ozet_sayac[sonuc] += 1
            m = session.get(ArsivMevzuat, anahtar_)
            # Hata verdiyse sonraki çalışmada tekrar denenir (en fazla arsiv.EN_FAZLA_DENEME kez).
            if sonuc == "hata" and m is not None and m.deneme < arsiv.EN_FAZLA_DENEME:
                kalan.add(anahtar_)
            elif izlenen is not None and sonuc != "atlandi":
                izlenen.son_kontrol = simdi
            session.commit()  # her mevzuat ayrı, yarıda kesilirse yapılanlar kaybolmaz
        _bekleyenleri_yaz(session, kalan, simdi)
        session.commit()
    finally:
        kilit.release()
    return ozet_sayac

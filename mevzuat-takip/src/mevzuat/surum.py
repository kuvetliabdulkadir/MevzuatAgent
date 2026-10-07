"""Güncel (konsolide) metin takibi. mevzuat.gov.tr'deki metin değişince eski ve yeni halini madde madde karşılaştırır.

Kaynak eklentisi değil de ayrı bir adım olmasının sebebi, fark bulmak için önceki sürümün gerekmesi. O da veritabanında duruyor.

Her çalışmada şu adımlar izlenir.
  1. mevzuat.gov.tr ana sayfasındaki "BUGÜN GÜNCELLENENLER" listesi okunur. RG tarihi bugünden önce olan eski bir
     mevzuatın adı konu filtresine takılırsa takibe alınır, bunlara "otomatik" denir.
     Bugün yayımlananlar atlanır, onları Resmî Gazete kaynağı zaten yakalıyor.
  2. Takip listesi config/izlenen_mevzuat.toml dosyasındakiler ("ayar") ve daha önce otomatik eklenenlerden oluşur.
  3. Takipteki bir mevzuatın metni iki durumda çekilir. Bugün güncellenenler listesinde çıktıysa ya da son
     kontrolden bu yana KONTROL_ARALIGI geçtiyse. Liste sadece bugünü gösterdiği için sunucunun kapalı kaldığı
     günlerde kaçan güncellemeler en geç bu sürede yakalanır. Metinler büyük (ÖTV Kanunu yaklaşık 1 MB),
     hepsini her gün çekmek siteye gereksiz yük olur.
  4. İlk çekim taban (baseline) olur ve rapor üretmez. Sonrakilerde metnin SHA-256'sı değiştiyse yeni sürüm
     saklanır ve farklar "Değişen mevzuat" kaydı olarak rapor ve onay akışına girer.

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
from selectolax.parser import HTMLParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat.db import Calisma, IzlenenMevzuat, Kayit, MetinSurumu
from mevzuat.filtre import Konu, eslesmeler, is_kollari
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
# Rapordaki bir farkın en fazla kaç karakteri gösterilir (tamamı panelde/metinde).
PARCA_UZUNLUGU = 400
# Bir maddede en fazla 4 fark parçası gösterilir.
BOLUM_BASINA_FARK = 4


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
        ))
    return sonuc


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


# Tek bir fark, hangi madde, ne oldu (eklendi/kaldırıldı/değişti), eski ve yeni hali.
@dataclass(frozen=True)
class Fark:
    bolum: str
    tur: str  # "eklendi" / "kaldırıldı" / "değişti"
    eski: str | None
    yeni: str | None


# Uzun metni 400 karaktere kısaltıp sonuna "…" koyar.
def _kisalt(metin: str) -> str:
    return metin if len(metin) <= PARCA_UZUNLUGU else metin[: PARCA_UZUNLUGU - 1].rstrip() + "…"


# Bir maddenin eski ve yeni halini kelime kelime karşılaştırır, değişen kelimeleri «» içine alır.
def _degisen_parcalar(eski: str, yeni: str, baglam: int = 8) -> list[tuple[str, str]]:
    """Kelime bazında karşılaştırma. Değişen kelimeler «» içinde, çevresinde `baglam` kelime.
    Birbirine yakın değişiklikler tek parçada birleşir."""
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
    # Her grup için eski ve yeni metinden çevresiyle birlikte bir parça çıkar (en fazla 4 grup).
    for grup in gruplar[:BOLUM_BASINA_FARK]:
        # Parçanın başı ve sonu, ilk değişiklikten 8 kelime önce, son değişiklikten 8 kelime sonra.
        e_bas, e_son = max(0, grup[0][1] - baglam), min(len(e), grup[-1][2] + baglam)
        y_bas, y_son = max(0, grup[0][3] - baglam), min(len(y), grup[-1][4] + baglam)
        sonuc.append((
            _kisalt(isaretle(e, [(op[1], op[2]) for op in grup], e_bas, e_son)),
            _kisalt(isaretle(y, [(op[3], op[4]) for op in grup], y_bas, y_son)),
        ))
    return sonuc


# İki metin arasındaki bütün farkları madde madde listeler.
def farklar(eski_metin: str, yeni_metin: str) -> list[Fark]:
    eski, yeni = bolumler(eski_metin), bolumler(yeni_metin)
    sonuc = []
    # Yeni metindeki her madde, eskide yoksa "eklendi", varsa ve farklıysa "değişti".
    for ad, metin in yeni.items():
        if ad not in eski:
            sonuc.append(Fark(ad, "eklendi", None, _kisalt(metin)))
        elif eski[ad] != metin:
            for e, y in _degisen_parcalar(eski[ad], metin):
                sonuc.append(Fark(ad, "değişti", e, y))
    # Eski metinde olup yenide olmayan madde, "kaldırıldı".
    for ad, metin in eski.items():
        if ad not in yeni:
            sonuc.append(Fark(ad, "kaldırıldı", _kisalt(metin), None))
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
        else:
            satirlar.append(f"{f.bolum} değişti.\n  Eski: {f.eski}\n  Yeni: {f.yeni}")
    return "\n".join(satirlar)


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
    session: Session, izlenen: IzlenenMevzuat, fark_listesi: list[Fark], surum: MetinSurumu,
    konular: list[Konu], calisma: Calisma, bugun: date, simdi: datetime,
) -> Kayit:
    # Mevzuatın adı hangi konulara uyuyor, takip listesinde konusu yazılıysa onu da ekle.
    eslesen = eslesmeler(izlenen.ad, konular)
    if izlenen.konu:
        eslesen.setdefault(izlenen.konu, ["(takip listesindeki mevzuat)"])
    icerik = fark_metni(fark_listesi)
    for konu, kelimeler in eslesmeler(icerik, konular).items():  # değişen metinde geçen konular da (iş kolu genişler)
        mevcut = eslesen.setdefault(konu, [])
        mevcut += [f"{k} (değişiklikte)" for k in kelimeler if k not in mevcut]
    kayit = Kayit(
        kaynak=KAYNAK_ADI,
        # Sürüm numarası, metin eski haline dönse bile her değişiklik ayrı kayıttır.
        dis_id=f"{izlenen.anahtar}@{surum.id}",
        yayin_tarihi=bugun,
        baslik=f"{izlenen.ad} — güncel metni değişti",
        tur="Değişen mevzuat",
        bolum="",
        sayi=None,
        mukerrer=0,
        url=metin_url(izlenen.anahtar),
        kaynakca=f"Mevzuat Bilgi Sistemi, {izlenen.ad}, güncel metin {bugun:%d.%m.%Y}",
        ilgili=True,  # takipteki mevzuat zaten ilgili seçilmiş
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


# Her gün çalışan adım bu. Takip listesini günceller, vakti gelen mevzuatın metnini çeker, değiştiyse fark kaydı oluşturur.
def takip_et(
    session: Session,
    client: httpx.Client,
    tanimlar: list[IzlenenTanim],
    konular: list[Konu],
    calisma: Calisma,
    bugun: date,
    simdi: datetime,
) -> dict:
    # Özette görünecek sayaçlar.
    ozet_sayac = {"taban": 0, "degisen": 0, "ayni": 0, "hata": 0, "otomatik_eklenen": 0}

    # Ana sayfayı indir, "bugün güncellenenler" listesini çıkar.
    response = client.get(ANA_SAYFA)
    response.raise_for_status()
    bugun_guncellenen = {g.anahtar: g for g in parse_guncellenenler(response.text)}

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
    # Takipteki her mevzuat için.
    for izlenen in session.scalars(select(IzlenenMevzuat).order_by(IzlenenMevzuat.anahtar)).all():
        if izlenen.neden == "ayar" and izlenen.anahtar not in ayardakiler:
            continue  # ayar dosyasından çıkarılmış, takip bırakılır (eski sürümler kanıt olarak durur)
        # Bugün listede çıkmadıysa ve son kontrolden 7 gün geçmediyse atla.
        vakti_geldi = izlenen.son_kontrol is None or simdi - izlenen.son_kontrol >= KONTROL_ARALIGI
        if izlenen.anahtar not in bugun_guncellenen and not vakti_geldi:
            continue
        # Güncel metni indir (hata olursa say ve sıradakine geç). finally, her durumda 1 sn bekle.
        try:
            metin = metin_cek(client, izlenen.anahtar)
        except Exception:
            log.exception("Güncel metin alınamadı: %s (%s)", izlenen.ad, izlenen.anahtar)
            ozet_sayac["hata"] += 1
            continue
        finally:
            time.sleep(ISTEK_ARASI_BEKLEME)

        # Yeni metnin özeti ve veritabanındaki son sürüm.
        yeni_ozet = ozet(metin)
        son = session.scalar(
            select(MetinSurumu).where(MetinSurumu.anahtar == izlenen.anahtar).order_by(MetinSurumu.id.desc()).limit(1)
        )
        # İlk kez ya da metin değiştiyse yeni sürüm olarak sakla.
        if son is None or son.ozet != yeni_ozet:
            surum = MetinSurumu(anahtar=izlenen.anahtar, cekildi=simdi, ozet=yeni_ozet, metin=metin)
            session.add(surum)
            session.flush()  # surum.id lazım
        if son is None:
            ozet_sayac["taban"] += 1  # ilk çekim, karşılaştıracak önceki hal yok, rapor üretilmez
        elif son.ozet == yeni_ozet:
            ozet_sayac["ayni"] += 1
        # Değiştiyse farkları bul, gerçek fark varsa rapora girecek kaydı oluştur.
        else:
            fark_listesi = farklar(son.metin, metin)
            if fark_listesi:  # yalnız boşluk/biçim farkıysa rapor üretme
                _degisiklik_kaydi(session, izlenen, fark_listesi, surum, konular, calisma, bugun, simdi)
                ozet_sayac["degisen"] += 1
            else:
                ozet_sayac["ayni"] += 1
        izlenen.son_kontrol = simdi
        session.commit()  # her mevzuat ayrı, yarıda kesilirse yapılanlar kaybolmaz
    return ozet_sayac

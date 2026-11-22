"""Mevzuat arşivi. mevzuat.gov.tr'de listelenen bütün yürürlükteki mevzuatı, metinleri, ekleri, resimleri ve sitenin
PDF'iyle sürümleyerek saklar. Ayrıntılı plan: mevzuat-arsiv-plani.md

Her çalışmada şu adımlar izlenir.
  1. Liste. Seçilen her tür, sitenin listeleme servisinden (mevzuat_gov.API_URL) 100'lük sayfalarla baştan sona çekilir.
     Sayfa sayısı servisin verdiği toplamdan hesaplanır. Mülga listesi bazen 100'den az kayıtlı sayfa verip devamını da
     veriyor, "sayfa kısa geldi, bitti" kuralı orada eksik bırakıyordu.
     Yeni mevzuat eklenir, var olanın son görülme zamanı güncellenir. Bir türün listesi tam geldiyse, o türde bu sefer
     görünmeyenin kayıp sayısı artar, üst üste 2 tam listede görünmeyen "kalktı" sayılır. Satır silinmez.
  2. Metin. Metni henüz alınmamış ya da hata vermiş mevzuat sırayla çekilir (önce takip listesindekiler, sonra kanunlar ...).
     Okuma arsiv_cekim.py'de: HTML yapısıyla, gövde resimleri, ekler (içleri açılarak), sitenin PDF'i; PDF'ler sayfa sayfa,
     gerekirse OCR ile, sınırsız. Ayrıntı orada.
  3. Sürüm. Karşılaştırma metni ve dosya özetleri aynıysa yeni sürüm olmaz. Farklıysa v2, v3 diye eklenir; sitenin ham
     kaynağı aynıysa türü "okuma_duzeltmesi" olur (karar 19), bildirime ve "son değişiklikler"e girmez.
     "Yeniden kontrol" seçilirse arşivdeki her şey baştan çekilir.

Siteye nezaket. İstekler arasında en az 1,5 sn beklenir, istekler tek tek atılır. Site 429/503 verir ya da cevap vermezse
aralık kendiliğinden açılır (en fazla 60 sn), düzelince geri döner. Üst üste 10 mevzuat hata verirse 10 dakika beklenir,
bu 3 kez olursa iş durur. Kesilen iş yeniden başlatılınca (zamanlayıcı yeniden başlarsa kendiliğinden) kaldığı yerden devam eder.

PDF, ek, resim ve sayfa görüntüleri veritabanına değil MEVZUAT_ARSIV_KLASORU klasörüne (varsayılan "arsiv") yazılır.
Dosya adı içeriğin özetinden gelir, aynı dosya iki kez yazılmaz. Her işin ayrıntılı günlüğü arsiv/gunluk/is-N.jsonl.

Panelden başlatılır. Panel sadece istek yazar (ArsivIstegi), zamanlayıcı `cli arsiv-kur --istek N` sürecini başlatır.
Elle de çalışır: uv run python -m mevzuat.cli arsiv-kur --tur Kanun
"""
# Arşiv, mevzuat.gov.tr'deki her şeyin bir kopyası. Liste, metin ve ekler toplanır, değişen metin yeni sürüm olur.

import hashlib
import html as html_lib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import httpx
from filelock import FileLock, Timeout
from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mevzuat import arsiv_cekim, arsiv_fark, html_okuma
from mevzuat.arsiv_cekim import Gunluk
from mevzuat.db import ArsivDosyasi, ArsivIstegi, ArsivMevzuat, ArsivSurumu, IzlenenMevzuat, Kullanici
from mevzuat.filtre import tr_kucuk
from mevzuat.http import TEKRAR_DENENEBILIR, make_client
from mevzuat.sources import mevzuat_gov

log = logging.getLogger("mevzuat.arsiv")

SITE = mevzuat_gov.SITE
# Listeleme servisinin türleri ve panelde görünen adları. Sıra, metin çekme sırasıdır.
# TumYonetmelik bütün yönetmelikleri kapsıyor (Cumhurbaşkanlığı ve Bakanlar Kurulu, kurum, üniversite).
# CumhurbaskanligiKararnameleriFihristi alınmaz, metin değil Resmî Gazete'ye bağlantı listesi.
TURLER = {
    "Kanun": "Kanun",
    "KHK": "KHK",
    "CumhurbaskaniKararnameleri": "CB Kararnamesi",
    "Tuzuk": "Tüzük",
    "TumYonetmelik": "Yönetmelik",
    "Teblig": "Tebliğ",
    "CumhurbaskanligiGenelgeleri": "CB Genelgesi",
    "CumhurbaskaniKararlari": "CB Kararı",
    "MulgaKanun": "Mülga",
}
MULGA = "MulgaKanun"
SAYFA_BOYU = 100  # servis 100'e kadar kabul ediyor, 500'ü reddetti (20.11.2026)
TELAFI_SAYFA_BOYLARI = (50, 25, 10)  # eksik gelen mülga listesi bu boylarla yeniden alınır
ISTEK_ARASI_BEKLEME = 1.5
# Bir mevzuat en fazla bu kadar kez hata verirse kendiliğinden tekrar denenmez ("yeniden kontrol" yine dener).
EN_FAZLA_DENEME = 3
# Üst üste bu kadar mevzuat hata verirse site sorunlu sayılır, beklenir.
UST_USTE_HATA = 10
HATA_BEKLEMESI = 10 * 60
EN_FAZLA_HATA_BEKLEMESI = 3
# Tam listede üst üste bu kadar kez görünmeyen "kalktı" sayılır. Site bazen eksik cevap veriyor, tek seferde düşürülmez.
KALKTI_ESIGI = 2
# Liste en az bu oranda geldiyse "tam" sayılır ve kalktı kontrolü yapılır.
TAM_LISTE_ORANI = 0.98
# Liste sayfası bozuk gelirse bu kadar saniye arayla tekrar istenir (toplam yaklaşık 4 dakika).
LISTE_TEKRAR_BEKLEMELERI = (5, 15, 30, 60, 120)
# İlerleme kaydı bu kadar mevzuatta bir yazılır.
ILERLEME_ARALIGI = 10
# Arşivde tek bir dosyanın en büyük boyutu. Haritalı/tablolu eski PDF'ler yüzlerce MB olabiliyor (ör. 177 MB),
# günlük işteki 100 MB sınırı arşivde dosyaları atlatıyordu.
ARSIV_EN_FAZLA_BAYT = 1024 * 1024 * 1024
AKTIF_ISTEK = ("BEKLIYOR", "CALISIYOR", "DURDURULUYOR")
# Site zorlanınca istek aralığı açılır (Istemci).
YAVASLATAN_KODLAR = (429, 502, 503, 504)
EN_FAZLA_EK_BEKLEME = 60.0
YAVASLAMA_SONRASI_BASARI = 20


# Dosyaların yazılacağı klasör.
def arsiv_klasoru() -> Path:
    return Path(os.environ.get("MEVZUAT_ARSIV_KLASORU", "arsiv"))


# --- anahtar ve adresler --------------------------------------------------------------------------

# Listedeki adresten tekil kimlik çıkarır.
def anahtar_bul(url: str) -> str:
    """Üç biçim var. "mevzuat?MevzuatNo=4760&MevzuatTur=1&MevzuatTertip=5" ise "1.5.4760", "/MevzuatMetin/20.5.11994.pdf"
    ise "20.5.11994", "/MevzuatMetin/CumhurbaskanligiGenelgeleri/20261114-13.pdf" ise "genelge.20261114-13".
    Hiçbiri değilse adresin özetinden bir kimlik üretilir."""
    adres = urlparse(urljoin(SITE, url))
    q = parse_qs(adres.query)
    if {"MevzuatTur", "MevzuatTertip", "MevzuatNo"} <= q.keys():
        return f'{q["MevzuatTur"][0]}.{q["MevzuatTertip"][0]}.{q["MevzuatNo"][0]}'
    if m := re.search(r"/MevzuatMetin/(\d+)\.(\d+)\.(\d+)\.\w+$", adres.path):
        return ".".join(m.groups())
    if m := re.search(r"/CumhurbaskanligiGenelgeleri/([^/]+)\.\w+$", adres.path):
        return f"genelge.{unquote(m.group(1))}"
    return "url." + hashlib.sha256(adres.geturl().encode()).hexdigest()[:16]


# Anahtar tür.tertip.no biçimindeyse metnin iframe adresi, değilse None.
def metin_adresi(anahtar: str) -> str | None:
    parca = anahtar.split(".")
    if len(parca) == 3 and all(p.isdigit() for p in parca):
        tur, tertip, no = parca
        return mevzuat_gov.METIN_URL.format(tur=tur, tertip=tertip, no=no)
    return None


# --- siteye istek ----------------------------------------------------------------------------------

class Istemci:
    """İstekler arası en az ISTEK_ARASI_BEKLEME bırakan ince sarmalayıcı. Listeleme servisinin güvenlik anahtarını da tutar.
    Site 429/503/502/504 verir ya da zaman aşımı olursa aralık ikiye katlanır (en fazla EN_FAZLA_EK_BEKLEME),
    üst üste YAVASLAMA_SONRASI_BASARI başarılı istekten sonra yarıya iner. Her istek günlüğe (varsa) bildirilir."""

    def __init__(self, client: httpx.Client, uyu: Callable[[float], None] = time.sleep,
                 saat: Callable[[], float] = time.monotonic, gunluk: Gunluk | None = None):
        self.client, self.uyu, self.saat = client, uyu, saat
        self.son: float | None = None
        self.anahtar: str | None = None
        self.gunluk = gunluk
        self.ek_bekleme = 0.0
        self.basari = 0

    # Bir önceki istekten bu yana yeterince zaman geçmediyse bekle.
    def _bekle(self) -> None:
        if self.son is not None:
            kalan = ISTEK_ARASI_BEKLEME + self.ek_bekleme - (self.saat() - self.son)
            if kalan > 0:
                self.uyu(kalan)
        self.son = self.saat()

    def _yavasla(self, neden: str) -> None:
        self.ek_bekleme = min(EN_FAZLA_EK_BEKLEME, max(3.0, self.ek_bekleme * 2))
        self.basari = 0
        log.warning("Site zorlanıyor (%s), istek aralığı %.1f sn", neden, ISTEK_ARASI_BEKLEME + self.ek_bekleme)

    def get(self, url: str) -> httpx.Response:
        self._bekle()
        bas = time.monotonic()
        try:
            r = self.client.get(url)
        except httpx.TimeoutException as e:
            self._yavasla("zaman aşımı")
            if self.gunluk:
                self.gunluk.istek(url, "zaman_asimi", 0, time.monotonic() - bas)
            raise e
        except httpx.HTTPError as e:
            if self.gunluk:
                self.gunluk.istek(url, e.__class__.__name__, 0, time.monotonic() - bas)
            raise
        if self.gunluk:
            self.gunluk.istek(url, r.status_code, len(r.content), time.monotonic() - bas)
        if r.status_code in YAVASLATAN_KODLAR:
            self._yavasla(f"HTTP {r.status_code}")
        elif self.ek_bekleme:
            self.basari += 1
            if self.basari >= YAVASLAMA_SONRASI_BASARI:
                self.ek_bekleme = self.ek_bekleme / 2 if self.ek_bekleme > 1 else 0.0
                self.basari = 0
        return r

    # Listeleme servisinden bir sayfa. Cevap bozuk gelirse artan aralıklarla, her seferinde yeni güvenlik anahtarıyla tekrar dener.
    def liste_sayfasi(self, tur: str, baslangic: int, boy: int | None = None) -> dict:
        """Site uzun listelemede ara ara boş ya da JSON olmayan cevap veriyor (20.11.2026, yönetmelik listesinin 1.600. kaydı).
        Hepsi tutmazsa ListeAlinamadi verir, sitenin son cevabı mesajda yazar."""
        son_hata = ""
        for bekleme in (0, *LISTE_TEKRAR_BEKLEMELERI):
            if bekleme:
                log.warning("Liste %s, %d. kayıt alınamadı, %d sn sonra tekrar: %s", tur, baslangic, bekleme, son_hata)
                self.uyu(bekleme)
                # Anahtarın süresi dolmuş olabilir, yenisini al.
                self.anahtar = None
            try:
                if self.anahtar is None:
                    self._bekle()
                    self.anahtar = mevzuat_gov.anahtar_al(self.client)
                parametreler = {"MevzuatTur": tur, "YonetmelikMevzuatTur": "OsmanliKanunu", "AranacakIfade": "",
                                "AranacakYer": "2", "KurumId": "0", "BaslangicTarihi": "", "BitisTarihi": ""}
                govde = {"draw": 1, "columns": mevzuat_gov._SUTUNLAR, "order": [], "start": baslangic,
                         "length": boy or SAYFA_BOYU, "search": {"value": "", "regex": False}, "parameters": parametreler}
                self._bekle()
                # Gövde ve başlıklar mevzuat_gov kaynağının gönderdiğiyle aynı, site bu biçimi kabul ediyor.
                r = self.client.post(
                    mevzuat_gov.API_URL, content=json.dumps(govde),
                    headers={"Content-Type": "application/json; charset=utf-8", "antiforgerytoken": self.anahtar},
                    extensions={TEKRAR_DENENEBILIR: True})
            except (httpx.HTTPError, ValueError) as e:
                son_hata = repr(e)[:300]
                continue
            try:
                r.raise_for_status()
                veri = r.json()
                if not isinstance(veri, dict) or "data" not in veri:
                    raise ValueError("data alanı yok")
                return veri
            except (httpx.HTTPStatusError, ValueError) as e:
                # Sitenin ne döndürdüğü hatada görünsün (boş mu, HTML mi, hata sayfası mı).
                son_hata = (f"{e.__class__.__name__}; cevap {r.status_code} {r.headers.get('content-type', '')!r}, "
                            f"{len(r.content)} bayt: {r.text[:150]!r}")
        raise ListeAlinamadi(f"{TURLER.get(tur, tur)} listesinin {baslangic}. kaydından sonrası alınamadı "
                             f"({len(LISTE_TEKRAR_BEKLEMELERI) + 1} deneme). Son cevap: {son_hata}")


# --- liste -----------------------------------------------------------------------------------------

class ListeAlinamadi(Exception):
    pass


# Bir türün listesi, servisin söylediği toplam ve liste eksik kaldıysa sebebi.
def tur_listesi(ist: Istemci, tur: str,
                ilerle: Callable[[int, int], None] = lambda a, t: None) -> tuple[list[dict], int, str | None]:
    """Bir sayfa hiç alınamazsa iş durmaz, o ana kadar gelenler döner ve eksik sebebi yazılır. Gelenler arşive girer,
    eksik listede kalktı kontrolü yapılmaz (listeyi_isle), bir sonraki çalışmada liste tamamlanır."""
    try:
        ilk = ist.liste_sayfasi(tur, 0)
    except ListeAlinamadi as e:
        return [], 0, str(e)
    toplam = int(ilk.get("recordsTotal") or 0)
    satirlar = list(ilk["data"])
    ilerle(len(satirlar), toplam)
    for baslangic in range(SAYFA_BOYU, toplam, SAYFA_BOYU):
        try:
            satirlar += ist.liste_sayfasi(tur, baslangic)["data"]
        except ListeAlinamadi as e:
            return satirlar, toplam, str(e)
        ilerle(len(satirlar), toplam)
    # Mülga listesi tutarsız: sayaç 185 derken bazı sayfalar eksik geliyor (20.11.2026: 168, 87). Tekil sayı toplamdan
    # azsa liste farklı sayfa boylarıyla yeniden alınır, anahtara göre birleştirilir (plan, Faz 1 madde 2).
    if tur == MULGA and toplam:
        gorulen = {_kunye(d)["anahtar"] for d in satirlar}
        for boy in TELAFI_SAYFA_BOYLARI:
            if len(gorulen) >= toplam:
                break
            for baslangic in range(0, toplam, boy):
                try:
                    for d in ist.liste_sayfasi(tur, baslangic, boy)["data"]:
                        if (a := _kunye(d)["anahtar"]) not in gorulen:
                            gorulen.add(a)
                            satirlar.append(d)
                except ListeAlinamadi:
                    break
            log.info("Mülga listesi telafisi (%d'lik sayfa): %d/%d tekil kayıt", boy, len(gorulen), toplam)
    return satirlar, toplam, None


# Servis satırından künye alanları.
def _kunye(d: dict) -> dict:
    url = urljoin(SITE, d.get("url") or "")
    sayi = str(d.get("resmiGazeteSayisi") or "").strip()
    return {
        "anahtar": anahtar_bul(url),
        "ad": " ".join(re.sub(r"<[^>]+>", "", d.get("mevAdi") or "").split()),
        "url": url,
        "rg_tarihi": mevzuat_gov._tarih(d.get("resmiGazeteTarihi")),
        "rg_sayisi": int(sayi) if sayi.isdigit() else None,
        "bicim": "pdf" if urlparse(url).path.lower().endswith(".pdf") else "html",
    }


# Listeyi veritabanına işler. Yeni, geri gelen ve kalkan sayısını döner.
def listeyi_isle(session: Session, tur: str, satirlar: list[dict], toplam: int, simdi: datetime,
                 calisma_basi: datetime | None = None) -> dict:
    """`calisma_basi` bu çalışmanın başladığı an. O andan sonra herhangi bir türün listesinde görülen kayıp sayılmaz
    (aynı mevzuat iki türün listesinde birden çıkabiliyor)."""
    calisma_basi = calisma_basi or simdi
    sayac = {"yeni": 0, "geri_gelen": 0, "kalkan": 0}
    gorulen = set()
    for d in satirlar:
        k = _kunye(d)
        if not k["ad"] or k["anahtar"] in gorulen:
            continue
        gorulen.add(k["anahtar"])
        m = session.get(ArsivMevzuat, k["anahtar"])
        if m is None:
            session.add(ArsivMevzuat(**k, tur=tur, mulga=tur == MULGA, ilk_gorulme=simdi, son_gorulme=simdi,
                                     kayip_sayisi=0, durum="BEKLIYOR", deneme=0, surum_sayisi=0))
            sayac["yeni"] += 1
            continue
        m.ad, m.url, m.rg_tarihi, m.rg_sayisi = k["ad"], k["url"], k["rg_tarihi"], k["rg_sayisi"]
        m.son_gorulme, m.kayip_sayisi = simdi, 0
        if m.kalkti is not None and not m.mulga:
            m.kalkti = None
            sayac["geri_gelen"] += 1
    session.flush()
    # Liste tam geldiyse bu türde görünmeyenin kayıp sayısı artar. Mülga listesi tutarsız, orada yapılmaz.
    if tur != MULGA and toplam and len(satirlar) >= toplam * TAM_LISTE_ORANI:
        for m in session.scalars(select(ArsivMevzuat).where(
                ArsivMevzuat.tur == tur, ArsivMevzuat.son_gorulme < calisma_basi, ArsivMevzuat.kalkti.is_(None))).all():
            m.kayip_sayisi += 1
            if m.kayip_sayisi >= KALKTI_ESIGI:
                m.kalkti = simdi
                sayac["kalkan"] += 1
    session.commit()
    return sayac


# Metni çekilecek mevzuat, öncelik sırasıyla.
def kuyruk(session: Session, turler: list[str], yeniden: bool, esik: datetime,
           anahtarlar: list[str] | None = None) -> list[str]:
    q = select(ArsivMevzuat.anahtar, ArsivMevzuat.tur).where(ArsivMevzuat.kalkti.is_(None))
    if anahtarlar:
        # Deneme çalıştırması: sadece verilen anahtarlar, durumlarına bakılmaksızın.
        q = q.where(ArsivMevzuat.anahtar.in_(anahtarlar))
    else:
        q = q.where(ArsivMevzuat.tur.in_(turler))
        if yeniden:
            q = q.where(or_(ArsivMevzuat.son_cekim.is_(None), ArsivMevzuat.son_cekim < esik))
        else:
            q = q.where(ArsivMevzuat.durum.in_(("BEKLIYOR", "HATA")), ArsivMevzuat.deneme < EN_FAZLA_DENEME)
    izlenen = set(session.scalars(select(IzlenenMevzuat.anahtar)))
    sira = {t: i for i, t in enumerate(TURLER)}
    return [a for a, _ in sorted(session.execute(q).all(),
                                 key=lambda s: (s[0] not in izlenen, sira.get(s[1], 99), s[0]))]


# --- iş --------------------------------------------------------------------------------------------

class Durduruldu(Exception):
    pass


class SiteCevapVermiyor(Exception):
    pass


# İşin günlük dosyası (karar 22).
def gunluk_yolu(klasor: Path, istek_id: int | None, simdi: datetime) -> Path:
    ad = f"is-{istek_id}.jsonl" if istek_id is not None else f"elle-{simdi:%Y%m%d-%H%M%S}.jsonl"
    return klasor / "gunluk" / ad


# Günlükten iş özeti: sonuçlara göre sayılar, kusurlular, hatalar, en yavaşlar.
def gunluk_ozeti(yol: Path) -> dict:
    sonuc: dict = {"sonuc": {}, "okuma": {}, "kusurlu": [], "hata": [], "ek_inmeyen": 0, "en_yavas": [],
                   "istek": 0, "sayfa": 0, "ocr_sayfa": 0, "gorsel_sayfa": 0, "tablo_yapi": 0, "tablo_goruntu": 0}
    if not yol.exists():
        return sonuc
    yavas = []
    with yol.open(encoding="utf-8") as f:
        for satir in f:
            try:
                k = json.loads(satir)
            except ValueError:
                continue
            if "anahtar" not in k:
                continue
            ad = k.get("sonuc", "?")
            sonuc["sonuc"][ad] = sonuc["sonuc"].get(ad, 0) + 1
            if k.get("okuma"):
                sonuc["okuma"][k["okuma"]] = sonuc["okuma"].get(k["okuma"], 0) + 1
            if k.get("kalite") == "kusurlu":
                sonuc["kusurlu"].append(k["anahtar"])
            if ad == "hata":
                sonuc["hata"].append({"anahtar": k["anahtar"], "hata": k.get("hata")})
            for alan in ("ek_inmeyen", "sayfa", "ocr_sayfa", "gorsel_sayfa", "tablo_yapi", "tablo_goruntu"):
                sonuc[alan] += k.get(alan) or 0
            sonuc["istek"] += len(k.get("istekler") or [])
            yavas.append((k.get("sure") or 0, k["anahtar"]))
    sonuc["en_yavas"] = [{"anahtar": a, "sure": s} for s, a in sorted(yavas, reverse=True)[:10]]
    sonuc["kusurlu"] = sonuc["kusurlu"][:200]
    sonuc["hata"] = sonuc["hata"][:200]
    return sonuc


# Arşiv işinin kendisi. Son durumu döner, BITTI, DURDURULDU ya da HATALI.
def arsivi_olustur(
    engine: Engine,
    istek_id: int | None = None,
    turler: list[str] | None = None,
    yeniden: bool = False,
    client: httpx.Client | None = None,
    uyu: Callable[[float], None] = time.sleep,
    saat: Callable[[], datetime] = datetime.now,
    klasor: Path | None = None,
    anahtarlar: list[str] | None = None,
) -> str:
    """`anahtarlar` verilirse liste adımı atlanır, sadece onlar (arşivde olmaları gerekir) baştan çekilir (deneme seti)."""
    klasor = klasor or arsiv_klasoru()
    klasor.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        istek = session.get(ArsivIstegi, istek_id) if istek_id else None
        if istek is not None:
            turler = istek.secenekler.get("turler") or turler
            yeniden = bool(istek.secenekler.get("yeniden", yeniden))
            anahtarlar = istek.secenekler.get("anahtarlar") or anahtarlar
        turler = [t for t in TURLER if t in (turler or TURLER)]
        # Yarım kalıp kendiliğinden devam ettirilen iş ilk işin eşiğini taşır; yoksa "yeniden" her şeyi baştan alırdı.
        esik = saat()
        if istek is not None:
            esik = istek.istendi
            if devam_esigi := istek.secenekler.get("esik"):
                esik = datetime.fromisoformat(devam_esigi)
        gunluk = Gunluk(gunluk_yolu(klasor, istek_id, saat()))
        ilerleme: dict = {"asama": "liste", "turler": turler, "yeniden": yeniden, "liste": {},
                          "metin": {"toplam": 0, "biten": 0, "yeni_surum": 0, "okuma_duzeltmesi": 0, "ayni": 0,
                                    "hata": 0, "sitede_yok": 0, "ocr_gerekli": 0, "kusurlu": 0},
                          "gunluk": gunluk.yol.name if gunluk.yol else None}
        ist: Istemci | None = None

        # İlerlemeyi isteğe yazar, panelden durdur denildiyse Durduruldu fırlatır.
        def kaydet_ve_bak() -> None:
            ilerleme["guncellendi"] = saat().isoformat(timespec="seconds")
            ilerleme["istek_araligi"] = round(ISTEK_ARASI_BEKLEME + ist.ek_bekleme, 1) if ist else None
            if istek_id is None:
                return
            session.execute(update(ArsivIstegi).where(ArsivIstegi.id == istek_id).values(ilerleme=dict(ilerleme)))
            session.commit()
            if session.scalar(select(ArsivIstegi.durum).where(ArsivIstegi.id == istek_id)) == "DURDURULUYOR":
                raise Durduruldu()

        def bitir(durum: str, hata: str | None = None) -> str:
            ilerleme["asama"] = "bitti"
            ilerleme["guncellendi"] = saat().isoformat(timespec="seconds")
            gunluk.yaz({"ozet": True, "durum": durum, "hata": hata, "metin": ilerleme["metin"],
                        "bitti": saat().isoformat(timespec="seconds")})
            if istek_id is not None:
                session.rollback()
                session.execute(update(ArsivIstegi).where(ArsivIstegi.id == istek_id).values(
                    durum=durum, bitti=saat(), ilerleme=dict(ilerleme), hata=hata))
                session.commit()
            log.info("Arşiv işi bitti: %s %s", durum, ilerleme["metin"])
            return durum

        kilit = FileLock(str(klasor / "arsiv.lock"), timeout=0)
        try:
            kilit.acquire()
        except Timeout:
            return bitir("HATALI", "Başka bir arşiv işi sürüyor (arsiv.lock kilitli).")
        try:
            ist = Istemci(client or make_client(zaman_asimi=300.0, en_fazla_bayt=ARSIV_EN_FAZLA_BAYT), uyu=uyu,
                          gunluk=gunluk)
            calisma_basi = saat()
            eksik_listeler: list[str] = []
            # 1. Liste (deneme çalıştırmasında atlanır).
            for tur in ([] if anahtarlar else turler):
                ilerleme["tur"] = tur
                ilerleme["liste"][tur] = {"alinan": 0, "toplam": None}

                def ilerle(alinan: int, toplam: int, tur=tur) -> None:
                    ilerleme["liste"][tur] = {"alinan": alinan, "toplam": toplam}
                    if alinan % (SAYFA_BOYU * 10) == 0:
                        kaydet_ve_bak()

                satirlar, toplam, eksik = tur_listesi(ist, tur, ilerle)
                # Eksik listede kalktı kontrolü yapılmaz (toplam 0 verilir), son sayfa eksik kalsa bile yanlış "kalktı" olmasın.
                sayac = listeyi_isle(session, tur, satirlar, 0 if eksik else toplam, saat(), calisma_basi)
                ilerleme["liste"][tur].update(sayac, alinan=len(satirlar), toplam=toplam or None)
                if eksik:
                    ilerleme["liste"][tur]["eksik"] = eksik
                    eksik_listeler.append(eksik)
                    log.warning("Liste %s eksik kaldı, iş devam ediyor: %s", tur, eksik)
                log.info("Liste %s: %d/%d kayıt, %s", tur, len(satirlar), toplam, sayac)
                kaydet_ve_bak()

            # 2. Metin.
            ilerleme["asama"], ilerleme["tur"] = "metin", None
            sira = kuyruk(session, turler, yeniden, esik, anahtarlar)
            ilerleme["metin"]["toplam"] = len(sira)
            kaydet_ve_bak()
            ust_uste, beklemeler = 0, 0
            for i, anahtar in enumerate(sira, 1):
                m = session.get(ArsivMevzuat, anahtar)
                ilerleme["son"] = {"anahtar": anahtar, "ad": m.ad[:120]}
                try:
                    sonuc = arsiv_cekim.mevzuati_cek(session, ist, m, klasor, saat(), gunluk, metin_adresi)
                    sayac = ilerleme["metin"]
                    alan = "yeni_surum" if sonuc == "yeni" else sonuc
                    sayac[alan] = sayac.get(alan, 0) + 1
                    if m.durum == "OCR_GEREKLI":
                        sayac["ocr_gerekli"] += 1
                    if m.kalite == "kusurlu" and sonuc != "sitede_yok":
                        sayac["kusurlu"] += 1
                    ust_uste = 0
                except Exception as e:
                    session.rollback()
                    m = session.get(ArsivMevzuat, anahtar)
                    m.durum, m.deneme, m.son_hata = "HATA", m.deneme + 1, repr(e)[:1000]
                    ilerleme["metin"]["hata"] += 1
                    ust_uste += 1
                    log.warning("Arşiv metni alınamadı: %s (%s): %r", m.ad[:80], anahtar, e)
                session.commit()
                ilerleme["metin"]["biten"] = i
                # Site üst üste hata veriyorsa bekle, çok tekrar ederse dur.
                if ust_uste >= UST_USTE_HATA:
                    beklemeler += 1
                    if beklemeler > EN_FAZLA_HATA_BEKLEMESI:
                        raise SiteCevapVermiyor(
                            f"Site üst üste {UST_USTE_HATA} mevzuatta hata verdi, {EN_FAZLA_HATA_BEKLEMESI} kez beklendi. "
                            "Daha sonra yeniden başlatın, kaldığı yerden devam eder.")
                    log.warning("Üst üste %d hata, %d sn bekleniyor", ust_uste, HATA_BEKLEMESI)
                    kaydet_ve_bak()
                    uyu(HATA_BEKLEMESI)
                    ust_uste = 0
                if i % ILERLEME_ARALIGI == 0:
                    kaydet_ve_bak()
            # İş bitti ama bazı listeler eksik kaldıysa panelde görünsün, yeniden başlatınca tamamlanır.
            if eksik_listeler:
                return bitir("BITTI", "Bazı listeler site cevap vermediği için eksik kaldı, gelenlerin metni alındı. "
                                      "Yeniden başlatın, eksikler tamamlanır. " + " | ".join(eksik_listeler))
            return bitir("BITTI")
        except Durduruldu:
            return bitir("DURDURULDU")
        except Exception as e:
            log.exception("Arşiv işi hata verdi")
            return bitir("HATALI", f"{e}"[:2000] or repr(e)[:2000])
        finally:
            kilit.release()


# --- panel -----------------------------------------------------------------------------------------

# Panelden "Arşivi oluştur" denince çağrılır, sadece istek yazar (commit etmez).
def istek_olustur(session: Session, kullanici_id: int, turler: list[str] | None, yeniden: bool,
                  zamanlayici_calisiyor: bool, simdi: datetime | None = None) -> ArsivIstegi:
    simdi = simdi or datetime.now()
    secili = [t for t in TURLER if t in (turler or TURLER)]
    if turler and set(turler) - set(TURLER):
        raise ValueError(f"Bilinmeyen tür: {sorted(set(turler) - set(TURLER))}")
    if not zamanlayici_calisiyor:
        raise ValueError("Zamanlayıcı çalışmıyor; arşiv işi başlatılamaz. Sunucu yöneticisine bildirin.")
    if session.scalar(select(ArsivIstegi).where(ArsivIstegi.durum.in_(AKTIF_ISTEK)).limit(1)):
        raise ValueError("Bir arşiv işi zaten sırada ya da çalışıyor.")
    istek = ArsivIstegi(isteyen_id=kullanici_id, istendi=simdi, durum="BEKLIYOR",
                        secenekler={"turler": secili, "yeniden": bool(yeniden)})
    session.add(istek)
    try:
        session.flush()
    except IntegrityError:
        raise ValueError("Bir arşiv işi zaten sırada ya da çalışıyor.") from None
    return istek


# Panelden "Durdur". Sıradaki iş hemen durur, çalışan iş sıradaki mevzuattan sonra durur (commit etmez).
def durdur(session: Session, simdi: datetime | None = None) -> ArsivIstegi:
    istek = session.scalar(select(ArsivIstegi).where(ArsivIstegi.durum.in_(AKTIF_ISTEK)).limit(1))
    if istek is None:
        raise ValueError("Çalışan ya da sırada bekleyen arşiv işi yok.")
    if istek.durum == "BEKLIYOR":
        istek.durum, istek.bitti = "DURDURULDU", simdi or datetime.now()
    else:
        istek.durum = "DURDURULUYOR"
    return istek


# Zamanlayıcı başlarken çağırır. Önceki zamanlayıcı (sunucu yeniden başladı, container güncellendi) arşiv işi sürerken
# kapandıysa iş yarım kalmıştır. Çalışan iş HATALI işaretlenir ve aynı seçeneklerle, ilk işin eşiğiyle yeni bir istek
# sıraya konur: iş kendiliğinden kaldığı yerden devam eder. Panelden durdurulmakta olan iş devam ettirilmez.
def yarim_kalanlari_devam_ettir(session: Session, simdi: datetime) -> ArsivIstegi | None:
    yeni = None
    for istek in session.scalars(select(ArsivIstegi).where(ArsivIstegi.durum.in_(("CALISIYOR", "DURDURULUYOR")))).all():
        devam = istek.durum == "CALISIYOR"
        istek.durum, istek.bitti = ("HATALI" if devam else "DURDURULDU"), simdi
        istek.hata = ("Zamanlayıcı arşiv işi sürerken yeniden başladı; iş kendiliğinden devam ettirildi."
                      if devam else "Zamanlayıcı yeniden başladı; durdurma isteği tamamlandı.")
        session.flush()
        if devam and yeni is None:
            secenekler = dict(istek.secenekler or {})
            secenekler.setdefault("esik", istek.istendi.isoformat(timespec="seconds"))
            secenekler["devam_eden"] = istek.id
            yeni = ArsivIstegi(isteyen_id=istek.isteyen_id, istendi=simdi, durum="BEKLIYOR", secenekler=secenekler)
            session.add(yeni)
            session.flush()
            istek.hata += f" Yeni iş: #{yeni.id}."
    return yeni


# Arşiv süreci bitince zamanlayıcı çağırır. Süreç sonucu kendisi yazamadan öldüyse istek HATALI olur.
def istegi_sonuclandir(session: Session, istek_id: int, cikis_kodu: int, simdi: datetime) -> None:
    istek = session.get(ArsivIstegi, istek_id)
    if istek is not None and istek.durum in ("CALISIYOR", "DURDURULUYOR"):
        istek.durum, istek.bitti = "HATALI", simdi
        istek.hata = (f"Arşiv süreci beklenmedik şekilde bitti (çıkış kodu {cikis_kodu}). "
                      "Yeniden başlatın, kaldığı yerden devam eder.")
    session.commit()


def _istek_json(session: Session, istek: ArsivIstegi | None) -> dict | None:
    if istek is None:
        return None
    isteyen = session.get(Kullanici, istek.isteyen_id) if istek.isteyen_id else None
    zaman = lambda z: z.isoformat(timespec="seconds") if z else None
    return {"id": istek.id, "durum": istek.durum, "istendi": zaman(istek.istendi), "basladi": zaman(istek.basladi),
            "bitti": zaman(istek.bitti), "isteyen": isteyen.ad if isteyen else None, "secenekler": istek.secenekler,
            "ilerleme": istek.ilerleme, "hata": istek.hata}


# Panelin Arşiv sayfasının özet verisi.
def arsiv_durumu(session: Session) -> dict:
    istek = session.scalar(select(ArsivIstegi).order_by(ArsivIstegi.id.desc()).limit(1))
    sayilar: dict[str, dict[str, int]] = {}
    for tur, durum, n in session.execute(select(ArsivMevzuat.tur, ArsivMevzuat.durum, func.count())
                                         .group_by(ArsivMevzuat.tur, ArsivMevzuat.durum)):
        sayilar.setdefault(tur, {})[durum] = n
    kalkan = dict(session.execute(select(ArsivMevzuat.tur, func.count()).where(ArsivMevzuat.kalkti.is_not(None))
                                  .group_by(ArsivMevzuat.tur)).all())
    ilk = session.scalar(select(func.min(ArsivMevzuat.ilk_gorulme)))
    return {
        "istek": _istek_json(session, istek),
        "turler": [{"kod": k, "ad": v, "sayilar": sayilar.get(k, {}), "toplam": sum(sayilar.get(k, {}).values()),
                    "kalkan": kalkan.get(k, 0)} for k, v in TURLER.items()],
        "toplam": session.scalar(select(func.count()).select_from(ArsivMevzuat)) or 0,
        "surum_toplam": session.scalar(select(func.count()).select_from(ArsivSurumu)) or 0,
        "baslangic": ilk.isoformat(timespec="seconds") if ilk else None,
    }


# Büyük harfe Türkçe çevirme (sitedeki adların çoğu büyük harfle yazılmış).
def _tr_buyuk(metin: str) -> str:
    return metin.replace("i", "İ").replace("ı", "I").upper()


# Arşiv listesi, arama ve süzme ile, sayfa sayfa.
def liste(session: Session, ara: str = "", tur: str = "", durum: str = "", sayfa: int = 1, boyut: int = 50) -> dict:
    q = select(ArsivMevzuat)
    if ara := ara.strip():
        q = q.where(or_(ArsivMevzuat.anahtar == ara, ArsivMevzuat.ad.contains(ara, autoescape=True),
                        ArsivMevzuat.ad.contains(_tr_buyuk(ara), autoescape=True),
                        ArsivMevzuat.ad.contains(tr_kucuk(ara), autoescape=True)))
    if tur:
        q = q.where(ArsivMevzuat.tur == tur)
    if durum == "KALKTI":
        q = q.where(ArsivMevzuat.kalkti.is_not(None))
    elif durum == "KUSURLU":
        q = q.where(ArsivMevzuat.kalite == "kusurlu")
    elif durum in ("SITEDE_YOK", "BOS"):
        q = q.where(ArsivMevzuat.durum.in_(("SITEDE_YOK", "BOS")))
    elif durum:
        q = q.where(ArsivMevzuat.durum == durum)
    toplam = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    sayfa, boyut = max(1, sayfa), min(max(1, boyut), 200)
    satirlar = session.scalars(q.order_by(ArsivMevzuat.rg_tarihi.desc().nulls_last(), ArsivMevzuat.anahtar)
                               .offset((sayfa - 1) * boyut).limit(boyut))
    return {"toplam": toplam, "sayfa": sayfa, "boyut": boyut, "satirlar": [_kunye_json(m) for m in satirlar]}


def _zaman(z: datetime | None) -> str | None:
    return z.isoformat(timespec="seconds") if z else None


def _kunye_json(m: ArsivMevzuat) -> dict:
    durum = "SITEDE_YOK" if m.durum == "BOS" else m.durum
    return {"anahtar": m.anahtar, "ad": m.ad, "tur": m.tur, "tur_adi": TURLER.get(m.tur, m.tur), "url": m.url,
            "bicim": m.bicim, "rg_tarihi": m.rg_tarihi.isoformat() if m.rg_tarihi else None, "rg_sayisi": m.rg_sayisi,
            "mulga": m.mulga, "kalkti": _zaman(m.kalkti), "durum": durum, "surum_sayisi": m.surum_sayisi,
            "son_cekim": _zaman(m.son_cekim), "son_hata": m.son_hata, "ilk_gorulme": _zaman(m.ilk_gorulme),
            "kalite": m.kalite}


def _ek_json(session: Session, e: dict) -> dict:
    dosya = session.get(ArsivDosyasi, e["sha256"]) if e.get("sha256") else None
    durum = e.get("durum") or ("indi" if e.get("sha256") else "sitede_yok")
    return {"ad": e.get("ad"), "boyut": e.get("boyut"), "sha256": e.get("sha256"), "durum": durum,
            "bicim": e.get("bicim") or (dosya.bicim if dosya else None),
            "okuma": dosya.durum if dosya else None, "gorunum_var": bool(dosya and (dosya.gorunum or dosya.ic)),
            "not": e.get("not"), "denenen": e.get("denenen") or [],
            "ic": [{"ad": i.get("ad"), "sha256": i.get("sha256"), "boyut": i.get("boyut"), "bicim": i.get("bicim"),
                    "durum": i.get("durum")} for i in (e.get("ic") or (dosya.ic if dosya else []))]}


def _surum_json(session: Session, s: ArsivSurumu, onceki: ArsivSurumu | None) -> dict:
    yeni_notlar = []
    if s.rg_notlari:
        eski = {(n["tarih"], n["sayi"]) for n in (onceki.rg_notlari if onceki else [])}
        gorulen = set()
        for n in s.rg_notlari:
            k = (n["tarih"], n["sayi"])
            if k not in eski and k not in gorulen:
                gorulen.add(k)
                yeni_notlar.append({"tarih": n["tarih"], "sayi": n["sayi"]})
    sayfalar = s.sayfalar or []
    return {"surum_no": s.surum_no, "cekildi": _zaman(s.cekildi), "okuma": s.okuma, "uzunluk": len(s.metin),
            "surum_turu": s.surum_turu, "kalite": s.kalite, "kalite_notlari": s.kalite_notlari or [],
            "okuyucu_surumu": s.okuyucu_surumu, "pdf": bool(s.pdf_yolu), "site_pdf_turu": s.site_pdf_turu,
            "site_pdf": bool(s.site_pdf_yolu), "gorunum_var": bool(s.gorunum),
            "sayfa_sayisi": len(sayfalar),
            "sayfa_yontemleri": {y: sum(1 for p in sayfalar if p.get("yontem") == y) for y in {p.get("yontem") for p in sayfalar}},
            "resim_sayisi": len(s.resimler or []),
            "ekler": [_ek_json(session, e) for e in s.ekler or []],
            "rg_yeni": yeni_notlar}


# Bir mevzuatın künyesi ve sürümleri (metinsiz).
def detay(session: Session, anahtar: str) -> dict | None:
    m = session.get(ArsivMevzuat, anahtar)
    if m is None:
        return None
    surumler = list(session.scalars(select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar)
                                    .order_by(ArsivSurumu.surum_no)))
    json_ler = [_surum_json(session, s, surumler[i - 1] if i else None) for i, s in enumerate(surumler)]
    return {**_kunye_json(m), "surumler": list(reversed(json_ler))}


def _surum(session: Session, anahtar: str, surum_no: int | None) -> ArsivSurumu | None:
    q = select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar)
    q = q.where(ArsivSurumu.surum_no == surum_no) if surum_no else q.order_by(ArsivSurumu.surum_no.desc()).limit(1)
    return session.scalar(q)


# Bir sürümün metni, sürüm verilmezse en sonuncusu.
def metin(session: Session, anahtar: str, surum_no: int | None = None) -> dict | None:
    s = _surum(session, anahtar, surum_no)
    if s is None:
        return None
    return {"anahtar": anahtar, "surum_no": s.surum_no, "cekildi": _zaman(s.cekildi), "okuma": s.okuma, "metin": s.metin}


_DATA_SHA = re.compile(r'data-sha="([0-9a-f]{64})"')
_DATA_YOL = re.compile(r'data-yol="([^"]+)"')


# Görünüm HTML'indeki yer tutucuları panelin dosya adreslerine çevirir. Görünüm kaydedilirken güvenli etiketlerden
# kurulduğu için (html_okuma) burada yalnızca resim adresleri eklenir.
def gorunum_adresleri(gorunum: str, taban: str) -> str:
    gorunum = _DATA_SHA.sub(lambda m: f'src="{taban}/dosya/{m.group(1)}" data-sha="{m.group(1)}" loading="lazy"', gorunum)
    return _DATA_YOL.sub(lambda m: f'src="{taban}/goruntu?yol={quote(html_lib.unescape(m.group(1)))}" loading="lazy"', gorunum)


# Bir sürümün panelde gösterilecek hali. Eski okuyucunun sürümlerinde görünüm yoksa düz metinden kurulur.
def gorunum(session: Session, anahtar: str, surum_no: int | None, taban: str) -> dict | None:
    s = _surum(session, anahtar, surum_no)
    if s is None:
        return None
    html = s.gorunum or html_okuma.duz_metinden_gorunum(s.metin)
    return {"anahtar": anahtar, "surum_no": s.surum_no, "okuma": s.okuma, "gorunum": gorunum_adresleri(html, taban),
            "sayfalar": s.sayfalar or []}


# İki sürümün madde madde farkı.
def fark(session: Session, anahtar: str, eski_no: int, yeni_no: int) -> dict | None:
    eski, yeni = _surum(session, anahtar, eski_no), _surum(session, anahtar, yeni_no)
    if eski is None or yeni is None:
        return None
    sonuc = arsiv_fark.fark(eski.metin, yeni.metin)
    return {"anahtar": anahtar, "eski": eski_no, "yeni": yeni_no, "eski_cekildi": _zaman(eski.cekildi),
            "yeni_cekildi": _zaman(yeni.cekildi), "yeni_surum_turu": yeni.surum_turu, **sonuc}


# Panelin dosya indirmesi: sadece arşive kayıtlı dosyalar verilir (özetle aranır), yol arşiv klasörünün dışına çıkamaz.
def dosya_bul(session: Session, ozet: str, klasor: Path | None = None) -> tuple[Path, str, str] | None:
    """(tam yol, indirme adı, biçim) ya da None."""
    if not re.fullmatch(r"[0-9a-f]{64}", ozet):
        return None
    d = session.get(ArsivDosyasi, ozet)
    if d is None:
        return None
    return _guvenli_yol(klasor, d.yol), d.ad.replace("/", "_").replace("\\", "_"), d.bicim


def _guvenli_yol(klasor: Path | None, yol: str) -> Path | None:
    kok = (klasor or arsiv_klasoru()).resolve()
    tam = (kok / yol).resolve()
    if kok not in tam.parents or not tam.is_file():
        return None
    return tam


# Sayfa ve tablo görüntüsü (goruntu/ altı).
def goruntu_bul(yol: str, klasor: Path | None = None) -> Path | None:
    if not re.fullmatch(r"goruntu/[0-9A-Za-z_.\-]+/[0-9A-Za-z_.\-]+\.png", yol or ""):
        return None
    return _guvenli_yol(klasor, yol)


# Bir sürümün PDF'i: "site" sitenin PDF'i (asıl ya da üretilen), "metin" metnin okunduğu PDF.
def surum_pdf(session: Session, anahtar: str, surum_no: int | None, tur: str = "site",
              klasor: Path | None = None) -> tuple[Path, str] | None:
    s = _surum(session, anahtar, surum_no)
    if s is None:
        return None
    yol = (s.site_pdf_yolu or s.pdf_yolu) if tur == "site" else (s.pdf_yolu or s.site_pdf_yolu)
    if not yol:
        return None
    tam = _guvenli_yol(klasor, yol)
    if tam is None:
        return None
    ek = "-uretilen" if tur == "site" and s.site_pdf_turu == "uretilen" else ""
    return tam, f"{arsiv_cekim.guvenli_ad(anahtar)}-v{s.surum_no}{ek}.pdf"


# Bir ekin panelde gösterilecek hali.
def ek_gorunumu(session: Session, ozet: str, taban: str) -> dict | None:
    d = session.get(ArsivDosyasi, ozet) if re.fullmatch(r"[0-9a-f]{64}", ozet or "") else None
    if d is None:
        return None
    return {"sha256": d.sha256, "ad": d.ad, "bicim": d.bicim, "boyut": d.boyut, "durum": d.durum, "notlar": d.notlar,
            "gorunum": gorunum_adresleri(d.gorunum or "", taban) if d.gorunum else None,
            "ic": d.ic or [], "sayfalar": d.sayfalar or []}


# Bir işin günlüğü: özet ve son kayıtlar (isteğe göre sadece hata/uyarılı olanlar).
def is_gunlugu(session: Session, istek_id: int, sadece: str = "", adet: int = 200,
               klasor: Path | None = None) -> dict | None:
    istek = session.get(ArsivIstegi, istek_id)
    if istek is None:
        return None
    yol = gunluk_yolu(klasor or arsiv_klasoru(), istek_id, datetime.now())
    kayitlar = []
    if yol.exists():
        with yol.open(encoding="utf-8") as f:
            for satir in f:
                try:
                    k = json.loads(satir)
                except ValueError:
                    continue
                if "anahtar" not in k:
                    continue
                if sadece == "hata" and k.get("sonuc") != "hata":
                    continue
                if sadece == "uyari" and not (k.get("uyarilar") or k.get("kusurlar") or k.get("sonuc") in ("hata", "sitede_yok")):
                    continue
                k.pop("istekler", None)
                k.pop("iz", None)
                kayitlar.append(k)
    return {"istek": _istek_json(session, istek), "ozet": gunluk_ozeti(yol), "kayitlar": kayitlar[-min(adet, 1000):],
            "toplam": len(kayitlar)}

# Resmî Gazete okuyucusu. Her günün "fihrist" sayfası (o gün yayımlanan her şeyin listesi) indirilip okunur.
# Mükerrer sayılar (aynı gün çıkan ek sayılar) da tek tek kontrol edilir.
import re
# time.sleep ile istekler arasında bekliyoruz, siteyi yormayalım.
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
# Enum, sabit seçenekler listesi (YAYIMLANDI / YAYIMLANMADI / HENUZ_YOK). Yazım hatası yapılamasın diye.
from enum import Enum

import httpx
from selectolax.parser import HTMLParser

from mevzuat.icerik import Icerik, belge_icerigi
from mevzuat.sources.base import KayitTaslagi, TaramaAdimi

# Sitenin ana adresi.
BASE_URL = "https://www.resmigazete.gov.tr"
ISTEK_ARASI_BEKLEME = 1.0  # saniye, siteye nazik davranmak için


# Fihristteki tek bir satır (bir yönetmelik, tebliğ, karar...).
@dataclass(frozen=True)
class RGKaydi:
    tarih: date
    sayi: int | None
    mukerrer: int  # 0 yani normal sayı
    bolum: str  # ör. "YÜRÜTME VE İDARE BÖLÜMÜ"
    alt_baslik: str  # ör. "YÖNETMELİKLER"
    baslik: str
    url: str

    # @property, fonksiyon ama alan gibi kullanılır (k.ilan_mi). İlan bölümündeki kayıtlar mevzuat değil.
    @property
    def ilan_mi(self) -> bool:
        return "/ilanlar/" in self.url


# Bir günün, istenirse mükerrer sayısının da, fihrist adresini üretir.
def fihrist_url(tarih: date, mukerrer: int = 0) -> str:
    url = f"{BASE_URL}/fihrist?tarih={tarih.isoformat()}"
    if mukerrer:
        url += f"&mukerrer={mukerrer}"
    return url


# Başlıkları temizler.
def temizle(metin: str) -> str:
    # Başlıklar "–– " ile başlıyor ve &#8200, gibi özel boşluklar içeriyor.
    # Her türlü boşluğu tek normal boşluğa çevir.
    metin = re.sub(r"\s+", " ", metin)
    # Baştaki tireleri ve boşlukları at.
    return metin.strip().lstrip("–-").strip()


# Bayram gibi günlerde sayfada yazan cümle. Bunu görürsek "o gün gazete yok" diyoruz.
YAYIMLANMADI_IFADESI = "Resmî Gazete yayımlanmamaktadır"


# Bir günün fihristi için üç olası durum.
class Durum(Enum):
    # Gazete çıkmış, içinde kayıtlar var.
    YAYIMLANDI = "yayimlandi"
    # 200 + "bugün Resmî Gazete yayımlanmamaktadır" (bayram vb.). O gün kesin olarak gazete yok.
    YAYIMLANMADI = "yayimlanmadi"
    # 302 ile ana sayfaya yönlendirme. Bugün için "henüz çıkmadı", mükerrer için "o numara yok".
    HENUZ_YOK = "henuz_yok"


# Fihrist sayfasını indirmenin sonucu, durum + (yayımlandıysa) sayfanın HTML'i.
@dataclass(frozen=True)
class FihristSonucu:
    durum: Durum
    html: str | None = None


# Bir günün toplam sonucu, durum + o günün bütün kayıtları (mükerrerler dahil).
@dataclass(frozen=True)
class GunSonucu:
    durum: Durum
    kayitlar: list[RGKaydi]


# Fihrist sayfasını indirip üç durumdan hangisi olduğuna karar verir.
def fetch_fihrist(client: httpx.Client, tarih: date, mukerrer: int = 0) -> FihristSonucu:
    response = client.get(fihrist_url(tarih, mukerrer))
    # Site bizi ana sayfaya yönlendirdiyse (302), o gün/o mükerrer henüz yok.
    if response.is_redirect:
        return FihristSonucu(Durum.HENUZ_YOK)
    # Başka bir hata (500 vb.) varsa dur.
    response.raise_for_status()
    # Sayfada "yayımlanmamaktadır" yazıyorsa o gün gazete yok.
    if YAYIMLANMADI_IFADESI in response.text:
        return FihristSonucu(Durum.YAYIMLANMADI)
    # Normal durum, gazete var, HTML'ini geri ver.
    return FihristSonucu(Durum.YAYIMLANDI, response.text)


# Fihrist sayfasının HTML'inden bütün kayıtları (bölüm, alt başlık, başlık, link) çıkarır.
def parse_fihrist(html: str, tarih: date, mukerrer: int = 0) -> list[RGKaydi]:
    tree = HTMLParser(html)

    # Sayfanın üstündeki "33387 Sayılı Resmî Gazete" yazısından sayı numarasını çek.
    sayi = None
    preview = tree.css_first(".preview-title")
    if preview:
        eslesme = re.search(r"(\d+)\s+Sayılı", preview.text())
        if eslesme:
            sayi = int(eslesme.group(1))

    # Asıl liste #html-content kutusunda.
    icerik = tree.css_first("#html-content")
    if icerik is None:
        # Sayfa yapısı değişmiş olabilir, sessizce boş liste dönmek yerine hata ver.
        raise ValueError("Fihristte #html-content bulunamadı, sayfa yapısı değişmiş olabilir.")

    kayitlar = []
    # Şu an hangi bölüm ve alt başlığın altında olduğumuzu tutar, sayfayı yukarıdan aşağı okurken güncelliyoruz.
    bolum = alt_baslik = ""
    # Kutunun içindeki her düğümü sırayla gez.
    for node in icerik.iter():
        # Düğümün CSS sınıflarını liste olarak al.
        siniflar = (node.attributes.get("class") or "").split()
        # Bölüm başlığı (ör. "YÜRÜTME VE İDARE BÖLÜMÜ"), yeni bölüm başladı, alt başlığı sıfırla.
        if "html-title" in siniflar:
            bolum = temizle(node.text())
            alt_baslik = ""
        # Alt başlık (ör. "YÖNETMELİKLER").
        elif "html-subtitle" in siniflar:
            alt_baslik = temizle(node.text())
        # Asıl kayıt satırı.
        elif "fihrist-item" in siniflar:
            link = node.css_first("a")
            # Linki olmayan satırı atla.
            if link is None or not link.attributes.get("href"):
                continue
            # Normal fihristte mükerrer sayıyı duyuran link de fihrist-item olarak duruyor
            # Bu linkin adresi fihrist sayfasının kendisi. Mevzuat değil, mükerrerler ayrıca çekiliyor.
            if "/fihrist?" in link.attributes["href"]:
                continue
            # Kaydı o anki bölüm/alt başlıkla birlikte listeye ekle.
            kayitlar.append(
                RGKaydi(
                    tarih=tarih,
                    sayi=sayi,
                    mukerrer=mukerrer,
                    bolum=bolum,
                    alt_baslik=alt_baslik,
                    baslik=temizle(link.text()),
                    url=link.attributes["href"],
                )
            )
    return kayitlar


# Bir günün normal sayısını ve bütün mükerrerlerini toplar.
def gunun_kayitlari(
    client: httpx.Client, tarih: date, ilanlar_dahil: bool = False, max_mukerrer: int = 20
) -> GunSonucu:
    """Normal sayı + tüm mükerrerler."""
    # Önce normal sayı.
    ana = fetch_fihrist(client, tarih)
    # Bugünün gazetesi henüz çıkmadıysa boş dön.
    if ana.durum is Durum.HENUZ_YOK:
        return GunSonucu(Durum.HENUZ_YOK, [])

    # Gazete varsa kayıtlarını oku (bayramda html boş olur).
    kayitlar = parse_fihrist(ana.html, tarih) if ana.html else []

    # Normal sayı yayımlanmasa bile (bayram) mükerrer çıkabilir, o yüzden her durumda bakılır.
    # 1. mükerrer, 2. mükerrer... yok diyene kadar (en fazla 20) dene.
    for n in range(1, max_mukerrer + 1):
        time.sleep(ISTEK_ARASI_BEKLEME)
        sonuc = fetch_fihrist(client, tarih, mukerrer=n)
        # Bu numarada mükerrer yoksa sonrakiler de yok, dur.
        if sonuc.html is None:
            break
        kayitlar.extend(parse_fihrist(sonuc.html, tarih, mukerrer=n))

    # İlanlar (ihale, kayıp ilanı vs.) mevzuat değil, çıkar.
    if not ilanlar_dahil:
        kayitlar = [k for k in kayitlar if not k.ilan_mi]
    # Normal sayı ya da en az bir mükerrer varsa "yayımlandı", yoksa "yayımlanmadı".
    durum = Durum.YAYIMLANDI if ana.html or kayitlar else Durum.YAYIMLANMADI
    return GunSonucu(durum, kayitlar)


# Raporda görünecek kaynak satırını üretir, "Resmî Gazete, 01.10.2026, Sayı: 33387 (1. Mükerrer), YÖNETMELİKLER: Başlık".
def kaynakca(k: RGKaydi, etiket: str = "Resmî Gazete") -> str:
    mukerrer = f" ({k.mukerrer}. Mükerrer)" if k.mukerrer else ""
    return f"{etiket}, {k.tarih:%d.%m.%Y}, Sayı: {k.sayi}{mukerrer}, {k.alt_baslik}: {k.baslik}"


# Resmî Gazete kaynağı.
class ResmiGazeteKaynagi:
    """Checkpoint = son tamamlanan gün (YYYY-MM-DD). Her gün ayrı bir TaramaAdimi'dır."""

    def __init__(self, ad: str, etiket: str = "Resmî Gazete"):
        self.ad = ad
        self.etiket = etiket

    # Checkpoint gününden bugüne kadar her günü sırayla tarar. Sunucu birkaç gün kapalı kaldıysa kaçan günler de taranır.
    def tara(
        self, client: httpx.Client, checkpoint: str | None, bugun: date, simdi: datetime
    ) -> Iterator[TaramaAdimi]:
        # İlk çalıştırmada geçmiş taranmaz, bugünden başlanır.
        # Checkpoint günü tekrar taranır, o güne gün içinde mükerrer eklenmiş olabilir.
        tarih = date.fromisoformat(checkpoint) if checkpoint else bugun
        # Bugüne gelene kadar her gün için.
        while tarih <= bugun:
            sonuc = gunun_kayitlari(client, tarih)
            # Gazete henüz çıkmamışsa.
            if sonuc.durum is Durum.HENUZ_YOK:
                # Geçmiş bir gün için bu olmamalı, olduysa site garip davranıyor, hata ver.
                if tarih < bugun:
                    # Geçmiş bir gün için ya gazete ya da "yayımlanmamaktadır" sayfası olmalı.
                    raise RuntimeError(f"{tarih} fihristi 302 döndü; geçmiş gün için beklenmiyor")
                # Bugünse, checkpoint'i ilerletme (None), sonraki taramada (18:00) tekrar bakılsın.
                yield TaramaAdimi([], checkpoint=None, bilgi={tarih.isoformat(): sonuc.durum.value})
                return

            # Günün kayıtlarını ortak kayıt taslağına çevir. Kimlik olarak belgenin adresi kullanılıyor.
            kayitlar = [
                KayitTaslagi(
                    dis_id=k.url, yayin_tarihi=k.tarih, baslik=k.baslik, url=k.url,
                    kaynakca=kaynakca(k, self.etiket), tur=k.alt_baslik, bolum=k.bolum,
                    sayi=k.sayi, mukerrer=k.mukerrer,
                )
                for k in sonuc.kayitlar
            ]
            # Bu günü bir adım olarak ver, checkpoint bu güne ilerler. Özete "yayimlandi/yayimlanmadi" yazılır.
            yield TaramaAdimi(kayitlar, checkpoint=tarih.isoformat(), bilgi={tarih.isoformat(): sonuc.durum.value})
            # Sonraki güne geç ve biraz bekle.
            tarih += timedelta(days=1)
            time.sleep(ISTEK_ARASI_BEKLEME)

    # Belgenin metni, belge HTML de olabilir PDF de, belge_icerigi ikisini de okur (PDF görüntüyse OCR).
    def icerik(self, client: httpx.Client, dis_id: str, url: str) -> Icerik:
        response = client.get(url)
        response.raise_for_status()
        return belge_icerigi(response.content, response.headers.get("content-type", ""), response.charset_encoding)

    # Denemeler için N gün önceki gün.
    def geriye_checkpoint(self, bugun: date, simdi: datetime, gun: int) -> str:
        return (bugun - timedelta(days=gun)).isoformat()

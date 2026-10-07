"""Her kaynağın uyması gereken sözleşme.

Yeni kaynak eklemenin iki yolu var.
  - Var olan bir tipe uyuyorsa (örneğin WordPress API) sadece config/kaynaklar.toml dosyasına eklenir.
  - Uymuyorsa bu protokolü uygulayan bir sınıf yazılır ve sources/__init__.py içindeki TIPLER'e kaydedilir.
Pipeline kaynakların ne olduğunu bilmez, sadece bu arayüzü çağırır.
"""
# Bu dosya bir "kural listesi", her kaynak (Resmî Gazete, MASAK, GİB...) hangi fonksiyonlara sahip olmalı,
# hangi bilgiyi hangi şekilde geri vermeli, onu tarif ediyor. Kendisi hiçbir siteye bağlanmaz.

# Iterator, "sırayla tek tek değer veren şey" demek. tara() sonuçları parça parça verdiği için lazım.
from collections.abc import Iterator
# dataclass, sadece veri tutan küçük sınıfları kısa yoldan yazmamızı sağlıyor (__init__ yazmaya gerek kalmıyor).
from dataclasses import dataclass, field
# date sadece gün, datetime gün ve saat birlikte.
from datetime import date, datetime
# Protocol, "şu fonksiyonlara sahip olan her sınıf bu türden sayılır" demenin yolu (Java'daki interface gibi).
from typing import Protocol

# httpx, internetten sayfa indirmek için kullandığımız kütüphane (requests'in modern hali).
import httpx

# Icerik, bir belgenin okunmuş metnini ve "OCR ile mi okundu" bilgisini tutan küçük sınıf.
from mevzuat.icerik import Icerik


# frozen=True, bu nesne oluşturulduktan sonra değiştirilemez. Yanlışlıkla bir alanı ezmeyelim diye.
@dataclass(frozen=True)
class KayitTaslagi:
    """Kaynaktan gelen, henüz DB'ye yazılmamış kayıt."""

    # Kaynağın içinde bu duyuruyu tek başına tanıtan kimlik. Aynı duyuru iki kez gelirse bununla anlıyoruz.
    dis_id: str  # kaynak içinde tekil (RG, belge URL'si, WordPress, post id)
    # Duyurunun yayımlandığı gün.
    yayin_tarihi: date
    # Duyurunun başlığı. Filtre (anahtar kelime araması) önce buna bakıyor.
    baslik: str
    # Duyurunun adresi. Mailde gösterilmez, sadece içeriği indirmek için kullanılır.
    url: str  # maile girmez, içerik çekmek için
    # Raporun altında "kaynak" olarak yazılacak satır.
    kaynakca: str  # rapordaki atıf, "Resmî Gazete, 01.10.2026, Sayı: 33387, …"
    # Belgenin türü (YÖNETMELİK, TEBLİĞ, Duyuru...). Boş olabilir.
    tur: str = ""
    # Resmî Gazete'deki bölüm adı (YÜRÜTME VE İDARE BÖLÜMÜ gibi). Diğer kaynaklarda boş.
    bolum: str = ""
    # Resmî Gazete sayı numarası. Diğer kaynaklarda yok (None).
    sayi: int | None = None
    # Resmî Gazete mükerrer sayısıysa kaçıncı mükerrer olduğu. 0 normal sayı demek.
    mukerrer: int = 0


# Bunda frozen yok çünkü pipeline bu nesneyi oluşturduktan sonra içine bilgi ekleyebiliyor.
@dataclass
class TaramaAdimi:
    """Bir tarama parçası. Pipeline her adımın kayıtlarını yazar, sonra checkpoint'i ilerletir
    ve commit eder. Böylece yarıda kesilen tarama, tamamlanan adımları kaybetmez."""

    # Bu adımda bulunan yeni duyurular.
    kayitlar: list[KayitTaslagi]
    # "Buraya kadar taradım" işareti. Bir sonraki tarama buradan devam eder.
    checkpoint: str | None  # None, checkpoint ilerlemez (ör. bugünün gazetesi henüz çıkmadı)
    # Çalışma özetine düşülecek ek notlar (ör. "liste doldu, eski duyurular kaçmış olabilir").
    # default_factory=dict, her nesneye ayrı boş sözlük verir (hepsi aynı sözlüğü paylaşmasın diye).
    bilgi: dict = field(default_factory=dict)  # çalışma özetine eklenir


# Bir kaynak sınıfının sahip olması gereken alanlar ve fonksiyonlar. Gövdelerdeki üç nokta, burayı her kaynağın kendisinin yazdığı anlamına geliyor.
class Kaynak(Protocol):
    # Kaynağın kısa adı (ör. "resmi_gazete"). Veritabanında checkpoint bu adla tutulur.
    ad: str
    # Bu kaynağın her kaydı, başlığına bakılmaksızın bu konularla ilgili sayılır (ayardan gelir).
    # Ör. MASAK'ın her duyurusu yükümlüleri ilgilendirir, başlığında anahtar kelime geçmese bile.
    varsayilan_konular: tuple[str, ...]

    # Asıl tarama fonksiyonu, siteye gidip checkpoint'ten sonraki yeni duyuruları parça parça (adım adım) verir.
    def tara(
        self, client: httpx.Client, checkpoint: str | None, bugun: date, simdi: datetime
    ) -> Iterator[TaramaAdimi]:
        """checkpoint'ten itibaren yeni kayıtlar. checkpoint None ise ilk çalıştırmadır."""
        ...

    # Tek bir duyurunun tam metnini indirip okur (HTML ya da PDF).
    def icerik(self, client: httpx.Client, dis_id: str, url: str) -> Icerik: ...

    # "30 gün öncesinden başla" gibi denemeler için, o güne karşılık gelen checkpoint değerini hesaplar.
    def geriye_checkpoint(self, bugun: date, simdi: datetime, gun: int) -> str:
        """`gun` gün öncesine karşılık gelen checkpoint (elle deneme için)."""
        ...

"""İçeriği JavaScript ile oluşan siteler. Sayfa gerçek bir tarayıcıda (Playwright ve Chromium) açılır,
oluşan HTML CSS seçicileriyle okunur. Site API'si ya da düz HTML'i olan kaynaklar için kullanılmamalı,
çünkü yavaştır, sunucuda Chromium ister ve site tasarımı değişince seçiciler bozulur.

Şu an bu tipi kullanan kaynak yok, ihtiyaç olursa hazır dursun diye var. Aşağıdaki GİB örneği canlı
doğrulandı (01.10.2026), ama GİB üretimde kendi API'siyle (tip = "gib") taranıyor.

GİB için örnek bir config/kaynaklar.toml kaydı aşağıda. Site Next.js ile yapılmış, ilk HTML'de içerik yok.
    [[kaynak]]
    ad = "gib_mevzuat"
    tip = "tarayici"
    etiket = "GİB Duyurusu"
    liste_url = "https://gib.gov.tr/duyuru-arsivi/mevzuat"
    oge = "div.MuiGrid-item"                  # listedeki her duyurunun kutusu
    baslik = "a[data-testid='link']"          # öğenin içinde, metni başlık, href'i duyurunun adresi
    tarih = "p[class*='dateParser']"          # öğenin içinde, ilk eşleşen kullanılır
    icerik = "div.cms-content"                # duyuru sayfasında metnin bulunduğu alan

05.10.2026'da liste ve içerik canlı denendi, sayfa başına 1,5 ile 5 saniye sürdü.
  - GİB, yukarıdaki örnekle 8 duyuru ve içerikleri geldi.
  - TCMB basın duyuruları Döviz ve Altın için aday. Listesi JavaScript ile geliyor, düz HTML'de yok.
        liste_url = "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Duyurular/Basin"
        oge = "div.block-collection-box"
        baslik = "a.collection-title"
        tarih = "div.collection-tag"           # 01/10/2026
        icerik = "div.tcmb-content"
    Sistemin kendi akışında, ayrı bir deneme DB'siyle denendi (05.10.2026). 19 duyuru geldi, 2'si doğrudan PDF'ti
    (konuşma slaytları), bunlar tarayıcısız indirilip OCR ile okundu. Rapor ve onay maili oluştu. Testi tests/test_tarayici.py dosyasında.
  - MASAK ana sayfası okunamıyor. Tarih "29 Eyl" diye yılsız ve kısaltılmış ayla yazıyor, tarih_oku bunu tanımaz ve tarama hata verir.
    MASAK zaten WordPress API'siyle taranıyor.

Liste ayrıştırma ve tarama `sources/liste.py` dosyasında, düz HTML tipiyle (`html`) ortak. Burada sadece sayfa
Chromium ile indirilir. Önce `html` tipi denenmeli, ilk HTML'de liste varsa tarayıcıya gerek yok.

Kurulumu isteğe bağlı bir ektir, normal kurulumda gelmez. `uv sync --extra tarayici` ve sonra `uv run --extra tarayici playwright install chromium`
çalıştırılır, Linux sunucuda `--with-deps` ile ve root olarak. Docker'da ne ekleneceği Dockerfile başında yazıyor.
"""

# Bu dosya, içeriği JavaScript ile sonradan oluşan siteleri gerçek bir tarayıcıda (Chromium) açıp okur.
# Listeyi okuma işi liste.py ile ortak, burada sadece "sayfayı tarayıcıyla indir" kısmı var.
# Eski kodlar bu isimleri buradan içe aktarıyordu, kırılmasınlar diye burada da duruyorlar.
from mevzuat.sources.liste import (  # noqa: F401 (eski içe aktarımlar)
    ListeKaynagi,
    Oge,
    parse_icerik,
    parse_liste,
)

# Bir sayfanın açılması için en fazla 60 saniye bekle. Değer milisaniye cinsinden, 60_000 aslında 60000, alt çizgi sadece okunsun diye.
SAYFA_ZAMAN_ASIMI_MS = 60_000


# Sayfayı görünmez bir Chromium penceresinde açar, JavaScript çalışıp içerik oluşunca HTML'ini geri verir.
def sayfa_html(url: str, bekle: str, zaman_asimi_ms: int = SAYFA_ZAMAN_ASIMI_MS) -> str:
    """Sayfayı başsız Chromium'da açar, `bekle` seçicisi görünene kadar bekler ve oluşan HTML'i döner.
    "networkidle" kullanılmıyor, çünkü analitik istekleri yüzünden sayfa hiç boşa çıkmayabiliyor."""
    # Playwright isteğe bağlı bir paket, kurulu değilse anlaşılır bir hata ver.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Tarayıcı kaynağı için playwright kurulu değil: uv sync --extra tarayici") from e
    # Playwright'ı başlat ("with" bitince her şeyi kendisi kapatır) ve Chromium'u aç.
    with sync_playwright() as p:
        tarayici = p.chromium.launch()
        try:
            # Yeni sekme aç, dili Türkçe ayarla (site tarihleri Türkçe göstersin).
            sayfa = tarayici.new_page(locale="tr-TR")
            # Adrese git, sayfa yüklenene kadar bekle.
            sayfa.goto(url, wait_until="load", timeout=zaman_asimi_ms)
            # Aradığımız parça (ör. duyuru başlığı) ekranda görünene kadar bekle, JavaScript işini bitirsin.
            sayfa.wait_for_selector(bekle, timeout=zaman_asimi_ms)
            # O anki sayfanın tam HTML'ini geri ver.
            return sayfa.content()
        # Hata olsa da olmasa da tarayıcıyı kapat, yoksa bellekte açık kalır. finally her durumda çalışır.
        finally:
            tarayici.close()


# Tarayıcı tipi kaynak, liste.py'deki her şeyi miras alır, sadece sayfaları Chromium'la indirir.
class TarayiciKaynagi(ListeKaynagi):
    # Liste sayfasını aç, başlıklar görünene kadar bekle.
    def _liste_html(self, client) -> str:
        return sayfa_html(self.liste_url, self.baslik_secici)

    # Duyuru sayfasını aç, metin alanı görünene kadar bekle. Duyuru adresi sitenin kendi sayfasından geldiği için önce
    # adres kontrolünden geçer, ele geçirilmiş bir sayfa tarayıcıyı şirket iç ağındaki bir adrese yönlendiremesin.
    # Not, Chromium sayfanın içindeki resim ve betikleri kendisi yükler, onları bu kontrol kapsamaz. Bu tip sadece
    # güvenilen sitelerle kullanılmalı.
    def _detay_html(self, client, url: str) -> str:
        from mevzuat.kaynak_yonetimi import guvenli_url

        return sayfa_html(guvenli_url(url, semalar=("https", "http")), self.icerik_secici)

    # Doğrudan PDF olan duyuru da aynı adres kontrolünden geçer.
    def _belge_indir(self, client, url: str):
        from mevzuat.kaynak_yonetimi import guvenli_url

        return super()._belge_indir(client, guvenli_url(url, semalar=("https", "http")))

"""Düz HTML duyuru listesi. Sayfa httpx ile indirilir, Chromium kullanılmaz ve hızlıdır, CSS seçicileriyle okunur.
Ayrıştırma ve tarama `tarayici` tipiyle ortaktır (`sources/liste.py`). Panelden eklenebilir.

Örnek bir config/kaynaklar.toml kaydı aşağıda.
    [[kaynak]]
    ad = "ornek_kurum"
    tip = "html"
    etiket = "Örnek Kurum Duyurusu"
    liste_url = "https://www.ornek.gov.tr/duyurular"
    oge = "ul.duyurular li"        # listedeki her duyurunun kutusu
    baslik = "a"                    # öğenin içinde, metni başlık, href'i duyurunun adresi
    tarih = "span.tarih"            # öğenin içinde, "02.10.2026" ya da "2 Ekim 2026" gibi
    icerik = "div.icerik"           # duyuru sayfasında metnin bulunduğu alan

Güvenlik. Liste adresi panelde kaydedilirken kontrol edilir, https ve genel internet olmalı. Duyuru adresleri
sitenin kendi HTML'inden geldiği için her içerik isteğinden önce de aynı kontrol yapılır. Böylece ele geçirilmiş
ya da yanlış bir sayfa sunucuyu şirket içi ağa istek atmaya yönlendiremez.
"""
# Bu dosya, "duyuru listesi düz bir web sayfası olan" siteleri okur (ör. Rekabet Kurumu).
# Sayfayı indirir, ortak liste okuyucusuna (liste.py) verir. Her indirmeden önce adresi güvenlik kontrolünden geçirir.

# Yönlendirme adresi göreli olabilir, tam adrese çevirmek için.
from urllib.parse import urljoin

# İnternetten sayfa indirmek için.
import httpx

# html_coz, indirilen baytları doğru harf kodlamasıyla (utf-8 / cp1254) yazıya çevirir. Türkçe harfler bozulmasın diye.
from mevzuat.icerik import html_coz
# ListeKaynagi, listeyi okuma, tarih bulma, tarama işinin ortak hali. Biz sadece "sayfayı nasıl indireceğiz" kısmını yazıyoruz.
from mevzuat.sources.liste import ListeKaynagi


# Güvenli indirmede en fazla kaç yönlendirme takip edilir.
YONLENDIRME_SINIRI = 3


# Bir adresi indirip yazıya çeviren küçük yardımcı. Adın başındaki alt çizgi bunun dosyanın iç işi olduğunu, dışarıdan kullanılmaması gerektiğini söyler.
def _indir(client: httpx.Client, url: str) -> str:
    # Sayfayı iste.
    response = client.get(url)
    # Site hata döndüyse (404, 500...) burada dur, hatayı yukarı fırlat.
    response.raise_for_status()
    # Türk kurum sitelerinde cp1254 (windows-1254) hâlâ yaygın, başlık ve <meta charset> dikkate alınır.
    return html_coz(response.content, response.charset_encoding)


# Önce adresi güvenlik kontrolünden geçirip sonra indiren sürüm. semalar izin verilen başlangıçlar, https ya da http.
def _guvenli_indir(client: httpx.Client, url: str, semalar: tuple[str, ...]) -> str:
    # import'u fonksiyonun içinde yapıyoruz, iki dosya birbirini en üstte çağırınca Python kilitleniyor (döngüsel içe aktarma).
    from mevzuat.kaynak_yonetimi import (
        guvenli_url,  # döngüsel içe aktarmayı önlemek için burada
    )

    # guvenli_url adresi kontrol eder (iç ağ yok, standart port...), sorun yoksa indiriyoruz.
    url = guvenli_url(url, semalar=semalar)
    # Yönlendirme en fazla 3 kez takip edilir, her yeni adres aynı kontrolden geçer (ör. TCMB http'den https'e yönlendiriyor).
    for _ in range(YONLENDIRME_SINIRI):
        response = client.get(url, follow_redirects=False)
        if not response.is_redirect:
            response.raise_for_status()
            return html_coz(response.content, response.charset_encoding)
        url = guvenli_url(urljoin(url, response.headers["location"]), semalar=semalar)
    raise ValueError(f"Çok fazla yönlendirme: {url}")


# Düz HTML kaynağı. Parantezdeki ListeKaynagi sayesinde ortak liste kaynağının bütün özelliklerini miras alıyor.
class HtmlKaynagi(ListeKaynagi):
    # Kayıtta kontrol edilen liste adresi taramada da tekrar kontrol edilir (DNS sonradan değişmiş olabilir).
    # Duyuru listesi sayfasını indirir. Liste adresi sadece https olabilir.
    def _liste_html(self, client: httpx.Client) -> str:
        return _guvenli_indir(client, self.liste_url, ("https",))

    # Tek bir duyurunun sayfasını indirir. Bazı siteler duyuru linklerini http verdiği için o da kabul.
    def _detay_html(self, client: httpx.Client, url: str) -> str:
        return _guvenli_indir(client, url, ("https", "http"))

    # Duyuru bir sayfa değil doğrudan PDF ise önce adres kontrolü yapılır, sonra üst sınıfın ortak indirme fonksiyonu çağrılır. super() üst sınıf demek.
    def _belge_indir(self, client: httpx.Client, url: str) -> httpx.Response:
        # Doğrudan PDF olan duyuru da sayfa linkiyle aynı adres kontrolünden geçer.
        from mevzuat.kaynak_yonetimi import guvenli_url

        return super()._belge_indir(client, guvenli_url(url, semalar=("https", "http")))

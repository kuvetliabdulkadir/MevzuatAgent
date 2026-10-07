# İnternete çıkan HER isteğin geçtiği yer. İki koruma katmanı kuruyor.
# Birincisi, site bir anlık düşerse isteği birkaç kez tekrar dener.
# İkincisi SSRF koruması, sunucunun şirket iç ağına ya da bulut meta verisine istek atmasını engeller.
# ipaddress, IP adreslerini inceleyip "özel mi, genel mi" diye anlamak için.
import ipaddress
import logging
# socket, alan adını (gib.gov.tr) IP adresine çevirmek (DNS sorgusu) için.
import socket
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

# Sitelere kendimizi bu isimle tanıtıyoruz (tarayıcı taklidi yapmıyoruz, dürüstçe "biz bir botuz" diyoruz).
USER_AGENT = "MevzuatTakip/0.1"

# Geçici sayılan durumlar, kaynak bir an düştü/yoğun. 404, 403, 400 kalıcıdır, denemek boşuna yük.
# 429 çok istek attın demek, 500, 502, 503 ve 504 ise sunucu tarafında geçici sorun demek.
TEKRAR_KODLARI = {429, 500, 502, 503, 504}
TEKRAR_HATALARI = (httpx.TransportError,)  # bağlantı, zaman aşımı, protokol hataları
# Sadece okuma istekleri tekrarlanır. Okuma amaçlı POST (ör. GİB listesi) isteğe
# extensions={TEKRAR_DENENEBILIR, True} koyarak izin verir.
TEKRAR_DENENEBILIR = "tekrar_denenebilir"
RETRY_AFTER_SINIRI = 60.0  # sn, daha uzun bekleme isteyen kaynak bir sonraki taramaya kalır
# Bir cevap en fazla bu kadar olabilir (açılmış hali, sıkıştırılmış dosya açılınca şişse de sayılır). Resmî Gazete'nin
# en büyük PDF'leri birkaç on MB, sınır bol tutuldu. Kötü niyetli ya da bozuk bir site sonsuz veri gönderip belleği
# dolduramasın diye her istemcide var.
EN_FAZLA_BAYT = 100 * 1024 * 1024


# Hata zincirinde sertifika hatası varsa tekrar denemenin anlamı yok.
def _kalici_baglanti_hatasi(e: Exception) -> bool:
    """Sertifika doğrulama hatası geçici değildir, sitenin sertifikası ya da zinciri bozuktur, tekrar denemek boşuna."""
    hata: BaseException | None = e
    # Hata başka bir hatadan doğmuş olabilir, zincir boyunca geriye doğru bak.
    while hata is not None:
        if isinstance(hata, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(hata):
            return True
        hata = hata.__cause__ or hata.__context__
    return False


# Site "şu kadar saniye sonra tekrar gel" dediyse (Retry-After başlığı) o süreyi okur.
def _retry_after(response: httpx.Response) -> float | None:
    deger = response.headers.get("Retry-After")
    if not deger:
        return None
    # Değer düz sayı olabilir ("30" saniye)...
    try:
        return max(0.0, float(deger))
    except ValueError:
        pass
    # ...ya da bir tarih ("Wed, 02 Oct 2026 10:00:00 GMT"), şimdiden o ana kaç saniye var.
    try:
        return max(0.0, (parsedate_to_datetime(deger) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


# Cevap izin verilenden büyükse fırlatılan özel hata.
class BoyutAsildi(httpx.HTTPError):
    def __init__(self, mesaj: str):
        super().__init__(mesaj)


# İç ağa bağlanma girişiminde fırlatılan özel hata.
class GuvensizAdres(httpx.HTTPError):
    """İç ağ / özel IP'ye bağlanma girişimi (SSRF)."""


# Bu IP genel internette mi diye bakar. İç ağ, kendi bilgisayarı ve bulut meta verisi gibi adreslerde False döner.
def ip_disa_acik_mi(ip: str) -> bool:
    """IP genel internette mi (iç ağ, loopback, link-local, bulut meta verisi, operatör NAT'ı değil)."""
    try:
        # IPv6'da "%eth0" gibi ek olabilir, atıyoruz.
        adres = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    # IPv6 içine gömülmüş IPv4 adresiyse içindeki IPv4'e bak (kandırmaya karşı).
    if isinstance(adres, ipaddress.IPv6Address) and adres.ipv4_mapped:
        adres = adres.ipv4_mapped
    # is_global, Python'un "bu adres herkese açık internette" bilgisi. Çoklu yayın adresleri de hariç.
    return adres.is_global and not adres.is_multicast


# "Alan adı alıp IP listesi döndüren fonksiyon" türü (testlerde sahte DNS verebilmek için).
Cozumleyici = Callable[[str], list[str]]


# Gerçek DNS sorgusu, alan adının bütün IP adreslerini sıralı ve tekrarsız verir.
def dns_coz(host: str) -> list[str]:
    try:
        return sorted({b[4][0] for b in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
    except socket.gaierror as e:
        raise GuvensizAdres(f"Adres çözümlenemedi: {host}") from e


# SSRF koruması, her bağlantıdan önce adresi çözer, iç ağ değilse o IP'ye bağlanır.
class SsrfKoruyanTransport(httpx.BaseTransport):
    """Alan adını bir kez çözer, bütün IP'lerin genel internette olduğunu doğrular ve bağlantıyı tam o doğrulanmış
    IP'ye sabitler. Host başlığı ve TLS SNI alan adı olarak kalır, sertifika yine doğrulanır.

    Neden gerekli. guvenli_url adresi kontrol ederken bir kez, httpx bağlanırken bir kez daha çözümlüyordu. Arada DNS
    değişirse (DNS rebinding) saldırgan genel bir IP ile kontrolü geçip bağlantıyı iç adrese yönlendirebiliyordu,
    örneğin 169.254.169.254 bulut meta verisine. IP'yi sabitlemek bu açığı kapatır."""

    # ic, asıl işi yapan alttaki taşıyıcı, yani gerçek HTTP bağlantısı.
    def __init__(self, ic: httpx.BaseTransport, cozumle: Cozumleyici = dns_coz):
        self.ic = ic
        self.cozumle = cozumle

    # httpx her istekte bunu çağırır.
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        # Adres zaten IP ise onu kullan, değilse DNS ile IP'lerini bul.
        try:
            ipaddress.ip_address(host)  # zaten IP ise (örn. sabitlenmiş) sadece doğrula
            ipler = [host]
        except ValueError:
            ipler = self.cozumle(host)
        if not ipler:
            raise GuvensizAdres(f"Adres çözümlenemedi: {host}")
        # IP'lerden biri bile iç ağdaysa bağlanma.
        for ip in ipler:
            if not ip_disa_acik_mi(ip):
                raise GuvensizAdres(f"İç ağ / özel adrese bağlantı engellendi ({host} → {ip}).")
        # Kontrol ettiğimiz ilk IP'ye bağlanacağız.
        hedef = ipler[0]
        # İsteğin kopyasını yap, adresteki alan adı yerine IP yaz, Host başlığını çıkar (aşağıda tekrar koyacağız).
        # sni_hostname, HTTPS sertifikası yine alan adına göre doğrulansın.
        istek = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=hedef),
            headers=[(a, d) for a, d in request.headers.raw if a.lower() != b"host"],
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        # Sunucu hangi siteyi istediğimizi anlasın diye Host başlığına asıl alan adını yaz.
        istek.headers["Host"] = request.url.netloc.decode("ascii")
        # Gerçek bağlantıyı alttaki taşıyıcıya yaptır.
        return self.ic.handle_request(istek)

    # Kapatılınca alttakini de kapat.
    def close(self) -> None:
        self.ic.close()


# Geçici hatalarda isteği tekrar deneyen katman.
class TekrarDeneyenTransport(httpx.BaseTransport):
    """Geçici hatada isteği `tekrar` kez daha dener, yani toplam tekrar+1 istek atılır. Aralarda 2, 4 ve 8 saniye beklenir,
    429 ve 503 cevaplarında makul bir Retry-After varsa ona uyulur. Son deneme de başarısızsa sonuç eskisi gibi döner
    ya da hata yükselir. Yani tarama davranışı değişmez, sadece anlık kesintiler checkpoint'i bir sonraki taramaya bırakmaz."""

    def __init__(
        self,
        ic: httpx.BaseTransport,
        # Kaç kez TEKRAR denensin (ilk istek hariç).
        tekrar: int = 3,
        # İlk bekleme süresi (her seferinde iki katına çıkar).
        bekleme: float = 2.0,
        # Bekleme fonksiyonu (testlerde gerçekten beklemesin diye değiştirilebiliyor).
        uyu: Callable[[float], None] = time.sleep,
        # Cevap en fazla kaç bayt olabilir. None verilirse sınır yok.
        en_fazla_bayt: int | None = None,
    ):
        self.ic = ic
        self.en_fazla_bayt = en_fazla_bayt
        self.tekrar = tekrar
        self.bekleme = bekleme
        self.uyu = uyu

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # Sadece okuma istekleri (GET/HEAD) ya da "tekrar denenebilir" işaretli olanlar tekrarlanır.
        # Diğerlerini (ör. bir şey kaydeden POST) iki kez göndermek tehlikeli olabilir, bir kez gönder geç.
        if request.method not in ("GET", "HEAD") and not request.extensions.get(TEKRAR_DENENEBILIR):
            response = self.ic.handle_request(request)
            self._boyut_kontrol(response)
            return self._sinirli_oku(request, response)
        # Toplam deneme sayısı (ilk istek + tekrarlar).
        toplam = self.tekrar + 1
        for i in range(1, toplam + 1):
            # Bu son deneme mi.
            son = i == toplam
            # Bu denemeden sonra beklenecek süre, 2, 4, 8 sn.
            sure = self.bekleme * 2 ** (i - 1)
            try:
                response = self.ic.handle_request(request)
                # Cevap çok büyükse hemen dur.
                self._boyut_kontrol(response)
                # Gövde burada okunur, okuma zaman aşımı/kopan bağlantı da tekrar denensin
                # (yoksa hata, transport'tan çıktıktan sonra Client'ta yükselir).
                response = self._sinirli_oku(request, response)
            # Bağlantı hatası olduysa.
            except TEKRAR_HATALARI as e:
                # Son denemeyse ya da sertifika hatasıysa vazgeç, hatayı yukarı fırlat.
                if son or _kalici_baglanti_hatasi(e):
                    raise
                log.warning("%s %s: %r — %.0f sn sonra tekrar (deneme %d/%d)",
                            request.method, request.url, e, sure, i, toplam)
            # Cevap geldiyse.
            else:
                # Geçici bir hata kodu değilse (ya da son denemeyse) cevabı olduğu gibi ver.
                if response.status_code not in TEKRAR_KODLARI or son:
                    return response
                # 429/503'te site makul bir bekleme süresi söylediyse ona uy.
                if response.status_code in (429, 503):
                    istenen = _retry_after(response)
                    if istenen is not None and istenen <= RETRY_AFTER_SINIRI:
                        sure = istenen
                # Bu cevabı kapat, tekrar deneyeceğiz.
                response.close()
                log.warning("%s %s: HTTP %d — %.0f sn sonra tekrar (deneme %d/%d)",
                            request.method, request.url, response.status_code, sure, i, toplam)
            # Bekle ve döngünün başına dön.
            self.uyu(sure)
        # Döngü her durumda return/raise ile biter, buraya hiç gelinmemeli.
        raise AssertionError("ulaşılamaz")

    # Gövdeyi parça parça okur, sınır aşılınca indirmeyi keser. Sunucu boyut bildirmeden parça parça gönderse de
    # (chunked) ya da sıkıştırılmış küçük bir dosya açılınca dev bir veriye dönüşse de sınır böylece çalışır.
    def _sinirli_oku(self, request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if self.en_fazla_bayt is None:
            response.read()
            return response
        parcalar, toplam = [], 0
        # iter_bytes açılmış (sıkıştırması çözülmüş) veriyi verir, saydığımız bayt gerçekten bellekte tutulacak olan.
        for parca in response.iter_bytes():
            toplam += len(parca)
            if toplam > self.en_fazla_bayt:
                response.close()
                raise BoyutAsildi(f"Cevap çok büyük (sınır {self.en_fazla_bayt} bayt), indirme kesildi")
            parcalar.append(parca)
        response.close()
        # Veri artık açılmış halde, sıkıştırma ve boyut başlıkları yeni cevaba taşınmaz.
        basliklar = [(a, d) for a, d in response.headers.multi_items()
                     if a.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        yeni = httpx.Response(response.status_code, headers=basliklar, content=b"".join(parcalar),
                              request=request, extensions=response.extensions)
        yeni.read()
        return yeni

    # Sunucunun bildirdiği boyut sınırı aşıyorsa cevabı hiç indirmeden reddeder.
    def _boyut_kontrol(self, response: httpx.Response) -> None:
        """Bildirilen boyutu sınırı aşan cevap indirilmez (tekrar da denenmez). Bildirilmeyen boyut _sinirli_oku'da."""
        if self.en_fazla_bayt is None:
            return
        # Content-Length başlığı cevabın boyutunu söyler. Yoksa ya da bozuksa 0 say.
        try:
            boyut = int(response.headers.get("Content-Length", "0"))
        except ValueError:
            boyut = 0
        if boyut > self.en_fazla_bayt:
            response.close()
            raise BoyutAsildi(f"Cevap çok büyük ({boyut} bayt, sınır {self.en_fazla_bayt})")

    def close(self) -> None:
        self.ic.close()


# Projenin her yerinde kullanılan internet istemcisini kurar, iki koruma katmanı + ortak ayarlar.
def make_client(
    transport: httpx.BaseTransport | None = None,
    uyu: Callable[[float], None] = time.sleep,
    zaman_asimi: float = 30.0,
    en_fazla_bayt: int | None = EN_FAZLA_BAYT,
) -> httpx.Client:
    # follow_redirects=False bilinçli, Resmî Gazete olmayan sayılar için 302 ile
    # ana sayfaya yönlendiriyor, takip edersek yanlış günün verisini parse ederiz.
    # Gerçek ağ çıkışında (transport verilmedi) SSRF koruması, her bağlantı doğrulanmış genel IP'ye sabitlenir.
    # Testler kendi transport'unu (MockTransport) verir, orada gerçek bağlantı yok, sabitleme atlanır.
    # Katmanlar içten dışa doğru gerçek HTTP, SSRF koruması ve tekrar deneme.
    ic = transport or SsrfKoruyanTransport(httpx.HTTPTransport())
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        # 30 saniyede cevap gelmezse vazgeç.
        timeout=zaman_asimi,
        follow_redirects=False,
        transport=TekrarDeneyenTransport(ic, uyu=uyu, en_fazla_bayt=en_fazla_bayt),
    )

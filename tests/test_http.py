import email.utils
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mevzuat.http import TEKRAR_DENENEBILIR, USER_AGENT, make_client


def _istemci(cevaplar):
    """`cevaplar` sırayla döner: int → o durum kodu, Exception → yükseltilir."""
    istekler, beklemeler = [], []

    def handler(request):
        istekler.append(request)
        cevap = cevaplar[min(len(istekler), len(cevaplar)) - 1]
        if isinstance(cevap, Exception):
            raise cevap
        if isinstance(cevap, httpx.Response):
            return cevap
        return httpx.Response(cevap, text=f"durum {cevap}")

    client = make_client(transport=httpx.MockTransport(handler), uyu=beklemeler.append)
    return client, istekler, beklemeler


def test_gecici_hatadan_sonra_basarili(caplog):
    client, istekler, beklemeler = _istemci([503, 502, 200])
    with caplog.at_level(logging.WARNING), client:
        r = client.get("https://x/a")
    assert r.status_code == 200 and r.text == "durum 200"
    assert len(istekler) == 3 and beklemeler == [2, 4]
    assert sum("tekrar" in m for m in caplog.messages) == 2
    assert istekler[0].headers["User-Agent"] == USER_AGENT


def test_kalici_hata_tekrar_denenmez():
    client, istekler, beklemeler = _istemci([404])
    with client:
        r = client.get("https://x/yok")
    assert r.status_code == 404 and len(istekler) == 1 and beklemeler == []
    with pytest.raises(httpx.HTTPStatusError):
        r.raise_for_status()


def test_hep_zaman_asimi_son_hatayi_yukseltir():
    client, istekler, beklemeler = _istemci([httpx.ReadTimeout("yavaş")])
    with client, pytest.raises(httpx.ReadTimeout):
        client.get("https://x/a")
    assert len(istekler) == 4 and beklemeler == [2, 4, 8]


def test_hep_503_son_cevap_doner():
    client, istekler, _ = _istemci([503])
    with client:
        r = client.get("https://x/a")
    assert r.status_code == 503 and len(istekler) == 4  # davranış eskisi gibi: çağıran raise_for_status ile görür


def test_baglanti_hatasi_tekrar_denenir():
    client, istekler, _ = _istemci([httpx.ConnectError("kapalı"), 200])
    with client:
        assert client.get("https://x/a").status_code == 200
    assert len(istekler) == 2


def test_retry_after_uyulur_cok_uzunsa_uyulmaz():
    client, _, beklemeler = _istemci([httpx.Response(429, headers={"Retry-After": "7"}),
                                      httpx.Response(503, headers={"Retry-After": "3600"}), 200])
    with client:
        assert client.get("https://x/a").status_code == 200
    assert beklemeler == [7, 4]  # 3600 sn sınırı aşıyor → normal bekleme

    tarih = email.utils.format_datetime(datetime.now(UTC) + timedelta(seconds=30))
    client, _, beklemeler = _istemci([httpx.Response(503, headers={"Retry-After": tarih}), 200])
    with client:
        client.get("https://x/a")
    assert 25 <= beklemeler[0] <= 30


def test_yonlendirme_takip_edilmez_ve_tekrar_denenmez():
    client, istekler, _ = _istemci([httpx.Response(302, headers={"Location": "https://x/ana"})])
    with client:
        r = client.get("https://x/eski")
    assert r.status_code == 302 and len(istekler) == 1  # RG olmayan sayı → 302; ana sayfa parse edilmemeli


def test_post_sadece_izinliyse_tekrar_denenir():
    client, istekler, _ = _istemci([503, 200])
    with client:
        assert client.post("https://x/yaz", json={}).status_code == 503
    assert len(istekler) == 1

    client, istekler, _ = _istemci([503, 200])
    with client:
        r = client.post("https://x/liste", json={"type": 1}, extensions={TEKRAR_DENENEBILIR: True})
    assert r.status_code == 200 and len(istekler) == 2
    assert istekler[0].content == istekler[1].content == b'{"type":1}'


def test_govde_okunurken_kopan_baglanti_tekrar_denenir():
    class KopanGovde(httpx.SyncByteStream):
        def __iter__(self):
            yield b"yar"
            raise httpx.ReadError("bağlantı koptu")

    client, istekler, _ = _istemci([httpx.Response(200, stream=KopanGovde()), httpx.Response(200, text="tam")])
    with client:
        assert client.get("https://x/a").text == "tam"
    assert len(istekler) == 2


def test_sertifika_hatasi_tekrar_denenmez():
    """Gerçek kurum sitesinde görüldü (BDDK): bozuk sertifika zinciri geçici değil; 3 kez denemek 14 sn kayıp."""
    import ssl

    def hata():
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")
        except ssl.SSLError as e:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]") from e

    istekler, beklemeler = [], []

    def handler(request):
        istekler.append(request)
        hata()

    with make_client(transport=httpx.MockTransport(handler), uyu=beklemeler.append) as client,             pytest.raises(httpx.ConnectError):
        client.get("https://x/a")
    assert len(istekler) == 1 and beklemeler == []


# --- SSRF koruması: DNS rebinding'e karşı IP sabitleme ---

from mevzuat.http import GuvensizAdres, SsrfKoruyanTransport, ip_disa_acik_mi  # noqa: E402


def _yakalayan():
    """İç transport: hangi IP'ye bağlanıldığını ve Host/SNI'yi kaydeder."""
    kayit = {}

    def handle(request):
        kayit["host"] = request.url.host
        kayit["Host"] = request.headers.get("Host")
        kayit["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="ok")

    t = httpx.MockTransport(handle)
    t.handle_request = handle  # type: ignore
    return t, kayit


def test_ssrf_dogrulanan_ip_e_sabitlenir_host_ve_sni_korunur():
    ic, kayit = _yakalayan()
    tr = SsrfKoruyanTransport(ic, cozumle=lambda h: ["93.184.216.34"])
    with httpx.Client(transport=tr) as c:
        c.get("https://ornek.gov.tr/duyuru")
    assert kayit["host"] == "93.184.216.34"  # alan adı değil, doğrulanan IP'ye bağlanıldı
    assert kayit["Host"] == "ornek.gov.tr" and kayit["sni"] == "ornek.gov.tr"  # sertifika yine doğrulanır


def test_ssrf_rebinding_engellenir():
    """Çözümleme genel IP döndürse bile, aynı çağrıda iç IP de dönerse bağlantı reddedilir (tek seferlik çözümleme)."""
    ic, _ = _yakalayan()
    tr = SsrfKoruyanTransport(ic, cozumle=lambda h: ["93.184.216.34", "169.254.169.254"])
    with httpx.Client(transport=tr) as c, pytest.raises(GuvensizAdres, match="İç ağ"):
        c.get("https://kotu.example/")


def test_ssrf_ic_ip_e_dogrudan_baglanti_reddedilir():
    ic, _ = _yakalayan()
    tr = SsrfKoruyanTransport(ic, cozumle=lambda h: ["10.0.0.5"])
    with httpx.Client(transport=tr) as c, pytest.raises(GuvensizAdres, match="İç ağ"):
        c.get("https://ic.firma.local/")


def test_ip_disa_acik_mi():
    assert ip_disa_acik_mi("93.184.216.34") and ip_disa_acik_mi("2606:2800:220:1::")
    for ic in ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "fd00::1", "100.64.0.1"]:
        assert not ip_disa_acik_mi(ic), ic


# Boyut sınırı. Sunucu boyut bildirmeden parça parça gönderse de (chunked) sınır çalışmalı, önceden sadece bildirilen
# boyuta bakılıyordu ve 1 MB sınıra rağmen 5 MB inebiliyordu (güvenlik denetimi, 2026-10-06).
def _parcali(adet: int, boyut: int = 100_000):
    for _ in range(adet):
        yield b"x" * boyut


def test_boyut_bildirilmese_de_sinir_asilinca_indirme_kesilir():
    from mevzuat.http import BoyutAsildi

    gelen = []

    def handler(request):
        def akis():
            for parca in _parcali(50):
                gelen.append(len(parca))
                yield parca
        return httpx.Response(200, content=akis())

    with make_client(transport=httpx.MockTransport(handler), en_fazla_bayt=1_000_000) as c:
        with pytest.raises(BoyutAsildi):
            c.get("https://ornek.test/buyuk")
    assert sum(gelen) <= 1_100_000  # sınırı geçen ilk parçada durdu, 5 MB'ın hepsi okunmadı


def test_sikistirilmis_dosya_acilinca_sinir_asarsa_kesilir():
    import gzip

    from mevzuat.http import BoyutAsildi

    bomba = gzip.compress(b"0" * 5_000_000)  # 5 MB sıfır, sıkıştırılmış hali birkaç KB
    handler = lambda request: httpx.Response(200, content=bomba, headers={"Content-Encoding": "gzip"})  # noqa: E731
    with make_client(transport=httpx.MockTransport(handler), en_fazla_bayt=1_000_000) as c:
        with pytest.raises(BoyutAsildi):
            c.get("https://ornek.test/bomba")


def test_sinir_icindeki_cevap_aynen_gelir_sikistirma_cozulur():
    import gzip

    handler = lambda request: httpx.Response(200, content=gzip.compress("merhaba dünya".encode()),  # noqa: E731
                                              headers={"Content-Encoding": "gzip", "Content-Type": "text/plain; charset=utf-8"})
    with make_client(transport=httpx.MockTransport(handler), en_fazla_bayt=1_000_000) as c:
        r = c.get("https://ornek.test/kucuk")
    assert r.text == "merhaba dünya" and r.headers["content-type"].startswith("text/plain")


def test_varsayilan_istemcide_de_sinir_var_post_dahil():
    from mevzuat.http import EN_FAZLA_BAYT, BoyutAsildi

    assert EN_FAZLA_BAYT is not None
    istemci = make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_parcali(3))))
    assert istemci._transport.en_fazla_bayt == EN_FAZLA_BAYT
    with make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_parcali(30))),
                     en_fazla_bayt=1_000_000) as c, pytest.raises(BoyutAsildi):
        c.post("https://ornek.test/liste", json={})  # tekrar denenmeyen POST yolu da sınırlı

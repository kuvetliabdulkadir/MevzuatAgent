"""Düz HTML kaynak tipi (#10 adım 4): httpx ile indirilir, tarayici tipiyle aynı ayrıştırıcı. İnternetsiz."""

from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from mevzuat import kaynak_yonetimi as ky
from mevzuat.http import make_client
from mevzuat.sources import kaynak_olustur
from mevzuat.sources.html import HtmlKaynagi
from mevzuat.sources.liste import tarih_oku

FIXTURES = Path(__file__).parent / "fixtures"
LISTE = (FIXTURES / "html_liste_cp1254.html").read_bytes()
DETAY = (FIXTURES / "html_detay.html").read_bytes()
AYAR = {"ad": "ornek", "etiket": "Örnek Kurum", "liste_url": "https://www.ornek.gov.tr/duyurular",
        "oge": "ul.duyurular li", "baslik": "a", "tarih": "span.tarih", "icerik": "div.icerik"}


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    # 10.0.0.5 gibi IP yazılmış adresler kendine çözülür; alan adları genel bir IP'ye.
    monkeypatch.setattr(ky, "_dns", lambda host: [host] if host[0].isdigit() else ["93.184.216.34"])


def _istemci(istekler: list | None = None):
    def handler(request):
        if istekler is not None:
            istekler.append(str(request.url))
        if request.url.path == "/duyurular":
            return httpx.Response(200, content=LISTE, headers={"Content-Type": "text/html"})  # charset yok
        return httpx.Response(200, content=DETAY, headers={"Content-Type": "text/html; charset=utf-8"})

    return make_client(transport=httpx.MockTransport(handler))


def _tara(checkpoint, bugun, istekler=None):
    with _istemci(istekler) as c:
        return list(HtmlKaynagi(**AYAR).tara(c, checkpoint, bugun, datetime.combine(bugun, datetime.min.time())))


@pytest.mark.parametrize("metin, beklenen", [
    ("02.10.2026", date(2026, 10, 2)),
    ("Yayın tarihi: 2.10.2026 14:30", date(2026, 10, 2)),
    ("02-10-2026", date(2026, 10, 2)),
    ("2 Ekim 2026", date(2026, 10, 2)),
    ("  15   AĞUSTOS 2026 Cuma", date(2026, 8, 15)),
    ("1 şubat 2026", date(2026, 2, 1)),
    ("1 Eki 2026 00:00:00", date(2026, 10, 1)),  # TCMB akışı, kısaltılmış ay
    ("5 ağu. 2026", date(2026, 8, 5)),
    ("3 Mar 2026", date(2026, 3, 3)),
    ("3 Mart 2026", date(2026, 3, 3)),
    ("31.02.2026", None),
    ("Duyuru", None),
])
def test_tarih_okuma(metin, beklenen):
    assert tarih_oku(metin) == beklenen


def test_liste_cp1254_cozulur_tarihsiz_kutu_atlanir():
    (adim,) = _tara("2026-09-30", date(2026, 10, 2))
    basliklar = [k.baslik for k in adim.kayitlar]
    # Türkçe karakterler bozulmadan (sayfa cp1254, HTTP başlığında charset yok); boşluklar sadeleşir.
    assert basliklar[0] == "Kıymetli Madenler Ticareti Hakkında Tebliğ Taslağı Görüşe Açıldı"
    assert "SGK Prim Ödeme Süresi Uzatıldı" in basliklar
    assert "Eski Duyuru" not in basliklar  # checkpoint öncesi (örtüşme 1 gün → 29.09'dan sonrası)
    assert "Tarihsiz kutu" not in basliklar
    ilk = adim.kayitlar[0]
    assert ilk.url == "https://www.ornek.gov.tr/duyuru/kiymetli-maden-tebligi"  # göreli link tamamlanır
    assert ilk.yayin_tarihi == date(2026, 10, 2) and ilk.kaynakca.startswith("Örnek Kurum, 02.10.2026: Kıymetli")
    assert adim.checkpoint == "2026-10-02"


def test_ilk_calisma_sadece_bugun():
    (adim,) = _tara(None, date(2026, 10, 2))
    assert [k.yayin_tarihi for k in adim.kayitlar] == [date(2026, 10, 2)]


def test_secici_hic_oge_bulamazsa_sessiz_bos_donmez():
    with _istemci() as c, pytest.raises(ValueError, match="hiç duyuru okunamadı"):
        list(HtmlKaynagi(**{**AYAR, "oge": "div.yok"}).tara(c, None, date(2026, 10, 2), datetime(2026, 10, 2)))


def test_icerik_secilen_alandan():
    istekler = []
    with _istemci(istekler) as c:
        metin = HtmlKaynagi(**AYAR).icerik(c, "x", "https://www.ornek.gov.tr/duyuru/kiymetli-maden-tebligi").metin
    assert "16 Ekim 2026" in metin and "Menü" not in metin
    assert istekler == ["https://www.ornek.gov.tr/duyuru/kiymetli-maden-tebligi"]


def test_ic_aga_giden_duyuru_linki_indirilmez():
    """Liste sayfası (ele geçirilmiş ya da yanlış) sunucuyu iç ağa istek atmaya yönlendiremez."""
    istekler = []
    (adim,) = _tara("2026-09-30", date(2026, 10, 2), istekler)
    ic = next(k for k in adim.kayitlar if k.baslik == "İç Ağa Giden Link")
    with _istemci(istekler) as c, pytest.raises(ValueError, match="İç ağ"):
        HtmlKaynagi(**AYAR).icerik(c, ic.dis_id, ic.url)
    assert not any("10.0.0.5" in i for i in istekler)


def test_ic_aga_giden_pdf_linki_de_indirilmez():
    """Doğrudan PDF olan duyuru (sayfa değil) da aynı adres kontrolünden geçer."""
    istekler = []
    with _istemci(istekler) as c, pytest.raises(ValueError, match="İç ağ"):
        HtmlKaynagi(**AYAR).icerik(c, "x", "http://10.0.0.5/gizli/rapor.pdf")
    with _istemci(istekler) as c, pytest.raises(ValueError, match="standart port"):
        HtmlKaynagi(**AYAR).icerik(c, "x", "https://www.ornek.gov.tr:8443/rapor.pdf")
    assert istekler == []


def test_yonlendirme_kontrol_edilerek_takip_edilir():
    """TCMB duyuru linkleri http, site https'e yönlendiriyor. Yönlendirme takip edilir, iç ağa olan engellenir."""
    istekler = []

    def handler(request):
        istekler.append(str(request.url))
        if request.url.scheme == "http":
            return httpx.Response(302, headers={"Location": str(request.url.copy_with(scheme="https"))})
        if request.url.path == "/ic":
            return httpx.Response(302, headers={"Location": "http://10.0.0.5/gizli"})
        if request.url.path == "/dongu":
            return httpx.Response(302, headers={"Location": "/dongu"})
        return httpx.Response(200, content=DETAY, headers={"Content-Type": "text/html; charset=utf-8"})

    with make_client(transport=httpx.MockTransport(handler)) as c:
        assert "görüşe açıktır" in HtmlKaynagi(**AYAR).icerik(c, "x", "http://www.ornek.gov.tr/duyuru/1").metin
        with pytest.raises(ValueError, match="İç ağ"):
            HtmlKaynagi(**AYAR).icerik(c, "x", "https://www.ornek.gov.tr/ic")
        with pytest.raises(ValueError, match="Çok fazla yönlendirme"):
            HtmlKaynagi(**AYAR).icerik(c, "x", "https://www.ornek.gov.tr/dongu")
    assert istekler[:2] == ["http://www.ornek.gov.tr/duyuru/1", "https://www.ornek.gov.tr/duyuru/1"]
    assert not any("10.0.0.5" in i for i in istekler)


def test_gecersiz_secici_ve_bos_alan_kaydedilemez():
    with pytest.raises(ValueError, match="seçicisi geçersiz"):
        kaynak_olustur("x", "html", {**{k: v for k, v in AYAR.items() if k not in ("ad", "etiket")}, "oge": "::x"}, "X")
    with pytest.raises(ValueError, match="'Öğe seçicisi' boş olamaz"):
        ky.ayarlari_temizle("html", {"liste_url": "https://a.gov.tr/d", "oge": " ", "baslik": "a", "tarih": "s",
                                     "icerik": "div"})
    temiz = ky.ayarlari_temizle("html", {"liste_url": "https://a.gov.tr/d", "oge": " ul  li ", "baslik": "a",
                                         "tarih": "span", "icerik": "div", "fazla": "x"})
    assert temiz == {"liste_url": "https://a.gov.tr/d", "oge": "ul li", "baslik": "a", "tarih": "span",
                     "icerik": "div", "ortusme_gun": 1}
    with pytest.raises(ValueError, match="https://"):
        ky.ayarlari_temizle("html", {"liste_url": "http://a.gov.tr/d", "oge": "li", "baslik": "a", "tarih": "s",
                                     "icerik": "div"})


def test_panelden_dene_html_tipiyle(monkeypatch):
    monkeypatch.setattr(ky, "make_client", lambda **_: _istemci())
    form = ky.KaynakFormu("Örnek Kurum", {k: v for k, v in AYAR.items() if k not in ("ad", "etiket")}, [])
    from mevzuat.filtre import konulari_yukle

    konular = konulari_yukle(Path(__file__).parent.parent / "config" / "konular.toml")
    sonuc = ky.dene("html", form, konular, {k.ad for k in konular}, bugun=date(2026, 10, 2), simdi=datetime(2026, 10, 2))
    kiymetli = next(k for k in sonuc["kayitlar"] if k["baslik"].startswith("Kıymetli"))
    assert "Kıymetli madenler ve kuyumculuk" in kiymetli["eslesen"] and "Kuyum" in kiymetli["is_kollari"]
    assert ky.TIP_FORMLARI["html"].eklenebilir


def test_ogenin_kendisi_link_baslik_icinde_duz_metin():
    """Rekabet Kurumu yapısı (03.10.2026): <a href><table><td class=tablotitle>…</td><td align=right>25.9.2026</td>."""
    from mevzuat.sources.liste import parse_liste

    html = """<div class="icerik01"><h4>Kurum Duyuruları</h4>
      <a href="/tr/Duyurular/yildiz-entegre"><table><tr>
        <td class="tablotitle">Soruşturmanın Sözlü Savunma Toplantısı 6 Ekim 2026 Tarihinde Yapılacaktır.</td>
        <td align="right">25.9.2026</td></tr>
        <tr><td class="tablored" align="right"><a href="/tr/Duyurular/yildiz-entegre">Devamı</a></td></tr></table></a>
      <a href="javascript:void(0)"><table><tr><td class="tablotitle">Linksiz</td><td align="right">24.9.2026</td></tr></table></a>
    </div>"""
    (oge,) = parse_liste(html, "https://www.rekabet.gov.tr/tr/Duyurular", "div.icerik01 > a", "td.tablotitle",
                         "td[align=right]")
    assert oge.url == "https://www.rekabet.gov.tr/tr/Duyurular/yildiz-entegre"
    assert oge.tarih == date(2026, 9, 25)  # başlıktaki "6 Ekim 2026" değil, tarih hücresi
    assert oge.baslik.startswith("Soruşturmanın Sözlü")

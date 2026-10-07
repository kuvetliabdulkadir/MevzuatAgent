"""Adresten kaynak tipini ve ayarlarını bulma (kaynak_bulucu) ve RSS tipi. Siteler sahte (MockTransport)."""

from datetime import date, datetime

import httpx
import pytest

from mevzuat import kaynak_yonetimi as ky
from mevzuat.http import make_client
from mevzuat.kaynak_bulucu import bul, liste_bul
from mevzuat.sources import kaynak_olustur
from mevzuat.sources.rss import parse_akis

SITE = "https://www.ornek.gov.tr"
MENU = "<nav><ul><li><a href='/'>Ana Sayfa</a></li><li><a href='/kurumsal'>Kurumsal Yapımız Hakkında</a></li></ul></nav>"
# Her sayfada aynı, uzun, gizli bir metin (ör. KVKK penceresi): "ana metin" sanılmamalı.
KVKK = "<div class='modal' style='display:none'>" + "Kişisel verileriniz 6698 sayılı Kanun kapsamında işlenir. " * 30 + "</div>"


@pytest.fixture(autouse=True)
def genel_dns(monkeypatch):
    def cozumle(host):
        return ["10.0.0.5"] if host.startswith("ic.") else ["93.184.216.34"]
    monkeypatch.setattr(ky, "_dns", cozumle)


def _site(sayfalar: dict[str, tuple[int, str, str | bytes]]):
    """yol → (durum, içerik türü, gövde). Olmayan yol 404."""
    istekler = []

    def handler(request: httpx.Request):
        istekler.append(str(request.url))
        yol = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        for anahtar in (str(request.url), yol, request.url.path):
            if anahtar in sayfalar:
                durum, tur, govde = sayfalar[anahtar]
                headers = {"content-type": tur}
                if durum in (301, 302):
                    headers["location"] = govde
                    govde = ""
                return httpx.Response(durum, headers=headers, content=govde if isinstance(govde, bytes) else govde.encode())
        return httpx.Response(404)

    return make_client(transport=httpx.MockTransport(handler)), istekler


def _sayfa(govde: str, baslik: str = "Örnek Kurum - Duyurular", bas: str = "") -> tuple[int, str, str]:
    return 200, "text/html; charset=utf-8", f"<html><head><title>{baslik}</title>{bas}</head><body>{MENU}{govde}{KVKK}</body></html>"


def _detay(baslik: str, metin: str) -> tuple[int, str, str]:
    return _sayfa(f"<div class='orta'><div class='icerik'><h1>{baslik}</h1><p>{metin}</p></div></div>"
                  "<aside><a href='/d/1'>Diğer duyurular listesine git</a></aside>", baslik)


DUYURULAR = [
    ("/d/1", "Kıymetli Madenler Tebliği Taslağı Görüşe Açıldı", "02.10.2026",
     "Kıymetli madenler ticaretine ilişkin tebliğ taslağı 16 Ekim tarihine kadar görüşe açılmıştır."),
    ("/d/2", "SGK Prim Ödeme Süresi Uzatıldı", "01.10.2026",
     "Eylül ayı prim ödeme süresi işverenlerin talebi üzerine beş gün uzatılmıştır."),
    ("/d/3", "Döviz Bürolarına İlişkin Yeni Düzenleme", "29.09.2026",
     "Döviz büroları için asgari sermaye şartı yeniden belirlenmiş ve yürürlük tarihi açıklanmıştır."),
]


def _detaylar():
    return {yol: _detay(b, m) for yol, b, _, m in DUYURULAR}


# ---- düz HTML -------------------------------------------------------------------------------------------

def test_html_liste_ve_icerik_secicisi_bulunur():
    liste = "".join(f"<li class='kalem'><a href='{y}'>{b}</a><span class='tarih'>{t}</span></li>"
                    for y, b, t, _ in DUYURULAR)
    client, _ = _site({"/duyurular": _sayfa(f"<ul class='duyurular'>{liste}</ul>"), **_detaylar()})
    sonuc = bul(f"{SITE}/duyurular", client)
    assert sonuc["bulundu"] and sonuc["tip"] == "html", sonuc["adimlar"]
    a = sonuc["ayarlar"]
    assert (a["oge"], a["baslik"], a["tarih"]) == ("ul.duyurular > li.kalem", "a", "span.tarih")
    assert a["icerik"] == "div.icerik"  # KVKK penceresi ya da menü değil
    assert "16 Ekim" in sonuc["icerik_ornegi"] and "Kişisel verileriniz" not in sonuc["icerik_ornegi"]
    assert sonuc["onerilen_ad"] == "Örnek Kurum - Duyurular"
    assert [o["tarih"] for o in sonuc["ornekler"]] == ["2026-10-02", "2026-10-01", "2026-09-29"]
    assert [x["sonuc"] for x in sonuc["adimlar"]] == ["yok", "yok", "bulundu"]
    kaynak_olustur("deneme", "html", a, "Deneme")  # kaydedilecek ayar taramada da kurulabilir


def test_butun_kutusu_link_olan_liste():
    """Rekabet Kurumu düzeni: her duyuru bir <a>, içinde tablo; başlık hücresi özetten önce, tarih ayrı hücrede."""
    liste = "".join(
        f"<a href='{y}'><table><tr><td class='baslik'>{b}</td><td align='right'>{t}</td></tr>"
        f"<tr><td colspan='2'>{m} Bu özet başlıktan daha uzun bir metindir ve başlık sanılmamalıdır.</td></tr></table></a>"
        for y, b, t, m in DUYURULAR)
    client, _ = _site({"/duyurular": _sayfa(f"<div id='liste'>{liste}</div>"), **_detaylar()})
    a = bul(f"{SITE}/duyurular", client)["ayarlar"]
    assert (a["oge"], a["baslik"], a["tarih"]) == ("div#liste > a", "td.baslik", 'td[align="right"]')


def test_tarihi_basligin_icinde_olan_liste():
    """Ticaret Bakanlığı düzeni: ayrı tarih alanı yok, tarih başlığın sonunda."""
    liste = "".join(f"<li><a href='{y}'><div class='text'><h5>{b} - {t}</h5><p></p></div></a></li>"
                    for y, b, t, _ in DUYURULAR)
    client, _ = _site({"/duyurular": _sayfa(f"<ul class='dizin'>{liste}</ul>"), **_detaylar()})
    sonuc = bul(f"{SITE}/duyurular", client)
    assert sonuc["bulundu"], sonuc["adimlar"]
    assert (sonuc["ayarlar"]["baslik"], sonuc["ayarlar"]["tarih"]) == ("h5", "h5")
    assert sonuc["ornekler"][0]["tarih"] == "2026-10-02"


def test_icerik_basligi_icermiyorsa_bulundu_denmez():
    """Duyuru sayfalarında başlık geçmiyorsa (yanlış alan) içerik seçicisi kabul edilmez; html tipi bulunamaz."""
    liste = "".join(f"<li><a href='{y}'>{b}</a> <span class='t'>{t}</span></li>" for y, b, t, _ in DUYURULAR)
    alakasiz = {y: _sayfa(f"<div class='icerik'><p>Hava durumu: yarın {i} derece, parçalı bulutlu bir gün bekleniyor.</p></div>")
                for i, (y, _, _, _) in enumerate(DUYURULAR)}
    client, _ = _site({"/duyurular": _sayfa(f"<ul>{liste}</ul>"), **alakasiz})
    sonuc = bul(f"{SITE}/duyurular", client)
    assert not sonuc["bulundu"]
    assert "liste bulundu ama duyuru sayfalarında metin alanı bulunamadı" in sonuc["adimlar"][-1]["not"]


def test_liste_bul_fiksturdeki_elle_yazilan_ayari_bulur():
    from mevzuat.icerik import html_coz
    from pathlib import Path
    html = html_coz((Path(__file__).parent / "fixtures" / "html_liste_cp1254.html").read_bytes())
    sonuc = liste_bul(html, "https://www.ornek.gov.tr/duyurular")
    assert (sonuc["oge"], sonuc["baslik"], sonuc["tarih"]) == ("ul.duyurular > li", "a", "span.tarih")


# ---- WordPress ve RSS -----------------------------------------------------------------------------------

def test_wordpress_sayfadaki_api_linkinden_bulunur():
    posts = ('[{"id": 7, "date": "2026-10-02T10:00:00", "modified": "2026-10-02T10:00:00", '
             '"title": {"rendered": "MASAK Genel Tebliği"}, "link": "https://www.ornek.gov.tr/?p=7"}]')
    client, istekler = _site({
        "/": _sayfa("<p>Ana sayfa</p>", bas=f"<link rel='https://api.w.org/' href='{SITE}/portal/'>"),
        "/portal/wp/v2/posts": (200, "application/json", posts),
    })
    sonuc = bul(f"{SITE}/", client)
    assert sonuc["tip"] == "wordpress" and sonuc["ayarlar"] == {"api_url": f"{SITE}/portal/wp/v2/posts"}
    assert sonuc["ornekler"] == [{"baslik": "MASAK Genel Tebliği", "tarih": "2026-10-02", "url": "https://www.ornek.gov.tr/?p=7"}]


RSS = f"""<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>Duyurular</title>
{''.join(f"<item><title>{b}</title><link>{SITE}{y}</link><pubDate>{p}</pubDate></item>" for (y, b, _, _), p in
         zip(DUYURULAR, ["Fri, 02 Oct 2026 09:00:00 +0300", "Thu, 01 Oct 2026 09:00:00 +0300", "29.09.2026"]))}
</channel></rss>"""


def test_rss_sayfadaki_alternate_linkinden_bulunur():
    client, _ = _site({
        "/": _sayfa("<p>Hoş geldiniz</p>", bas="<link rel='alternate' type='application/rss+xml' href='/rss/duyuru'>"),
        "/rss/duyuru": (200, "application/rss+xml", RSS), **_detaylar(),
    })
    sonuc = bul(f"{SITE}/", client)
    assert sonuc["tip"] == "rss" and sonuc["ayarlar"]["akis_url"] == f"{SITE}/rss/duyuru"
    assert sonuc["ayarlar"]["icerik"] == "div.icerik"
    assert [o["tarih"] for o in sonuc["ornekler"]] == ["2026-10-02", "2026-10-01", "2026-09-29"]


def test_atom_adresi_dogrudan_girilir():
    atom = (f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>X</title>'
            f'<entry><title>Atom Duyurusu Başlığı</title><link href="{SITE}/a/1"/><updated>2026-10-02T08:00:00Z</updated></entry>'
            '</feed>')
    client, _ = _site({"/atom.xml": (200, "application/atom+xml", atom)})
    sonuc = bul(f"{SITE}/atom.xml", client)
    assert sonuc["tip"] == "rss" and sonuc["ornekler"][0]["baslik"] == "Atom Duyurusu Başlığı"
    assert "icerik" not in sonuc["ayarlar"]  # duyuru sayfası açılamadı: ana metin taramada kendiliğinden bulunur


def test_sayfa_engelliyse_altindaki_akis_bulunur():
    """Reddit düzeni: sayfa 403 verir, "/.rss" akışı açıktır."""
    client, _ = _site({"/r/ornek": (403, "text/html", "Blocked"), "/r/ornek/.rss": (200, "application/atom+xml", RSS)})
    sonuc = bul(f"{SITE}/r/ornek", client)
    assert sonuc["tip"] == "rss" and sonuc["ayarlar"]["akis_url"] == f"{SITE}/r/ornek/.rss"
    assert sonuc["adimlar"][0]["not"].startswith("sayfa açılmadı (403)")


def test_sayfa_engelli_ve_akis_yoksa_hata_kalir():
    client, _ = _site({"/": (403, "text/html", "Blocked")})
    with pytest.raises(ValueError, match="403"):
        bul(f"{SITE}/", client)


def test_bolum_altindaki_feed_bulunur():
    client, _ = _site({"/duyurular": _sayfa("<p>Liste yok</p>"), "/duyurular/feed": (200, "application/rss+xml", RSS)})
    assert bul(f"{SITE}/duyurular", client)["ayarlar"]["akis_url"] == f"{SITE}/duyurular/feed"


def test_akista_doctype_reddedilir():
    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_akis(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>', SITE)


def test_rss_kaynagi_tarar_ve_icerigi_secicisiz_okur():
    client, _ = _site({"/rss": (200, "application/rss+xml", RSS), **_detaylar()})
    kaynak = kaynak_olustur("ornek", "rss", {"akis_url": f"{SITE}/rss"}, "Örnek Duyurusu")
    adim = next(kaynak.tara(client, "2026-10-01", date(2026, 10, 2), datetime(2026, 10, 2, 7)))
    # Örtüşme 1 gün: 30.09'dan beri olanlar.
    assert [k.baslik for k in adim.kayitlar] == [DUYURULAR[0][1], DUYURULAR[1][1]]
    assert adim.kayitlar[0].kaynakca == f"Örnek Duyurusu, 02.10.2026: {DUYURULAR[0][1]}"
    metin = kaynak.icerik(client, adim.kayitlar[0].dis_id, adim.kayitlar[0].url).metin
    assert "16 Ekim" in metin and "Kişisel verileriniz" not in metin


# ---- bulunamayan, yönlendirme, güvenlik -----------------------------------------------------------------

def test_javascript_sitesi_acikca_soylenir():
    sayfa = (200, "text/html", "<html><head><title>MASAK</title></head><body><noscript>You need to enable JavaScript "
             "to run this app.</noscript><div id='root'></div><script src='/a.js'></script><script src='/b.js'></script>"
             "<script>var x=1;</script></body></html>")
    client, _ = _site({"/": sayfa})
    sonuc = bul(f"{SITE}/", client)
    assert not sonuc["bulundu"] and "JavaScript" in sonuc["aciklama"]
    assert [a["yol"] for a in sonuc["adimlar"]] == ["WordPress API", "RSS/Atom akışı", "Düz HTML listesi"]


def test_yonlendirme_takip_edilir_ic_aga_yonlendirme_engellenir():
    liste = "".join(f"<li><a href='{y}'>{b}</a><span class='tarih'>{t}</span></li>" for y, b, t, _ in DUYURULAR)
    client, _ = _site({"/eski": (301, "", "/duyurular"), "/duyurular": _sayfa(f"<ul class='x'>{liste}</ul>"),
                       **_detaylar(), "/ice": (302, "", "https://ic.firma.local/gizli")})
    assert bul(f"{SITE}/eski", client)["ayarlar"]["liste_url"] == f"{SITE}/duyurular"
    with pytest.raises(ValueError, match="İç ağ"):
        bul(f"{SITE}/ice", client)
    with pytest.raises(ValueError, match="https"):
        bul("http://www.ornek.gov.tr/", client)


def test_ulasilamayan_adres():
    def handler(request):
        raise httpx.ConnectError("bağlantı reddedildi")
    with make_client(transport=httpx.MockTransport(handler), uyu=lambda s: None) as c, \
            pytest.raises(ValueError, match="ulaşılamadı"):
        bul(f"{SITE}/", c)


def test_akista_dosyanin_ilerisindeki_doctype_de_reddedilir():
    """Önceden sadece ilk 4 KB'a bakılıyordu, başa uzun bir açıklama konup tanım arkaya itilebiliyordu."""
    dolgu = b'<?xml version="1.0"?><!--' + b"a" * 5000 + b"-->"
    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_akis(dolgu + b'<!DOCTYPE r [<!ENTITY e "x">]><rss><channel></channel></rss>', SITE)

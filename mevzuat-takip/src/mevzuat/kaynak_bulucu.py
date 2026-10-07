"""Panelden kaynak eklerken tipi ve ayarları adresten kendiliğinden bulur. Kullanıcı sadece sitenin adresini girer.

Yollar en sağlamdan en kırılgana doğru sırayla denenir, ilk uyan seçilir.
  1. WordPress API. Sayfadaki <link rel="https://api.w.org/"> etiketine, /wp-json/wp/v2/posts adresine ya da adresin kendisine bakılır.
  2. RSS ya da Atom akışı. Adresin kendisi akış olabilir, sayfada <link rel="alternate" type="...rss/atom..."> olabilir, /feed ya da /rss olabilir.
  3. Düz HTML. Sayfada tekrar eden "tarih ve başlık linki" kutuları aranır. Liste, başlık ve tarih seçicileri
     bunlardan çıkarılır ve gerçekten okuyabildiği doğrulanır. İçerik seçicisi ilk duyuru sayfalarının ana metin
     kutusundan çıkarılır ve hepsinde çalıştığı doğrulanır.
Hiçbiri olmazsa her adımın neden olmadığı döner ve panel elle ayar formunu açar. Sayfa JavaScript'le oluşuyorsa
bu da söylenir, o zaman tarayıcı tipi gerekir ve panelden eklenmez.

Hiçbir şey kaydetmez. Her istek iç ağ kontrolünden geçer, yönlendirmeler de elle takip edilip kontrol edilir.
"""

# Panelde "Yeni kaynak" eklerken sadece adres girilir, bu dosya o adresin hangi tipte okunabileceğini kendisi bulur.
# Önce WordPress servisi, sonra RSS akışı, en son sayfadaki listeden seçicileri tahmin etmeyi dener.
import re
from collections.abc import Callable
from dataclasses import dataclass
# urlencode sözlüğü adres parametresine çevirir, urlsplit adresi parçalarına ayırır.
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
# Node, HTML ağacındaki tek bir düğüm (etiket).
from selectolax.parser import HTMLParser, Node

from mevzuat.filtre import tr_kucuk
from mevzuat.http import make_client
from mevzuat.icerik import ana_icerik_dugumu, gizlileri_at, html_coz
from mevzuat.kaynak_yonetimi import (
    DENE_EN_FAZLA_BAYT,
    DENE_ZAMAN_ASIMI,
    TIP_FORMLARI,
    Cozumleyici,
    guvenli_url,
)
from mevzuat.sources.liste import parse_icerik, parse_liste, tarih_oku
from mevzuat.sources.rss import akis_mi, parse_akis
from mevzuat.sources.wordpress import parse_posts

# Bir adreste en fazla 3 yönlendirme takip edilir.
EN_FAZLA_YONLENDIRME = 3
EN_AZ_OGE = 3  # bundan az duyuru okunan liste "bulundu" sayılmaz
# Panelde gösterilecek örnek duyuru sayısı.
ORNEK_SAYISI = 8
ICERIK_SAYFA_SAYISI = 3  # içerik seçicisi bu kadar duyuru sayfasında denenir
# CSS sınıf/kimlik adı olarak güvenle yazılabilecek kalıp (özel karakter yok).
_SINIF = re.compile(r"^[A-Za-z_][\w-]*$")
# Özellik değeri olarak güvenle yazılabilecek kalıp.
_DEGER = re.compile(r"^[\w-]+$")


# "Bu yol uymadı" anlamında iç hata, mesajı panelde "neden olmadı" diye gösterilir.
class _Yok(Exception):
    """Bu yol uymadı. Mesaj panelde "neden olmadı" olarak gösterilir."""


# İndirilen bir sayfa, son adresi, ham baytları, içerik türü ve harf kodlaması.
@dataclass
class _Sayfa:
    url: str
    veri: bytes
    tur: str
    charset: str | None

    # Baytları doğru harf kodlamasıyla yazıya çevirip verir.
    @property
    def html(self) -> str:
        return html_coz(self.veri, self.charset)


# Adresi indirir, yönlendirme varsa her yeni adresi de güvenlik kontrolünden geçirerek elle takip eder.
def _getir(client: httpx.Client, url: str, cozumle: Cozumleyici | None, semalar=("https",)) -> _Sayfa:
    """Yönlendirmeler elle takip edilir, ilki dahil her adres iç ağ kontrolünden geçer."""
    # En fazla 3 yönlendirme + 1 asıl istek.
    for _ in range(EN_FAZLA_YONLENDIRME + 1):
        # Her adımda adresi kontrol et (yönlendirme iç ağa götürmesin).
        url = guvenli_url(url, cozumle, semalar)
        r = client.get(url)
        # Yönlendirme cevabıysa yeni adrese geç.
        if r.is_redirect and "location" in r.headers:
            url = urljoin(url, r.headers["location"])
            continue
        r.raise_for_status()
        return _Sayfa(url, r.content, r.headers.get("content-type", "").lower(), r.charset_encoding)
    # Döngü bitti ama hâlâ yönlendiriyorsa vazgeç.
    raise _Yok("çok fazla yönlendirme")


# Adresin kökünü verir, örneğin "https://site.gov.tr/a/b" adresinden "https://site.gov.tr" kalır.
def _koken(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


# Panelde gösterilecek tek örnek duyuru.
def _ornek(baslik: str, tarih, url: str) -> dict:
    return {"baslik": baslik, "tarih": tarih.isoformat(), "url": url}


# ---- 1. WordPress --------------------------------------------------------------------------------------

# Birinci yol, site WordPress mi diye bakar. Olası API adreslerini sırayla dener, yazı dönen ilk adres kazanır.
def _wordpress(client, sayfa: _Sayfa, agac: HTMLParser | None, cozumle) -> dict:
    adaylar = []
    # Girilen adres zaten bir API adresi gibiyse onu dene.
    if "wp-json" in sayfa.url or sayfa.url.rstrip("/").endswith("/posts"):
        adaylar.append(sayfa.url.split("?")[0].rstrip("/"))
    # Sayfada WordPress'in "API burada" etiketi varsa ondan adres üret.
    if agac is not None:
        for link in agac.css('link[rel="https://api.w.org/"]'):
            if href := link.attributes.get("href"):
                adaylar.append(urljoin(sayfa.url, href).rstrip("/") + "/wp/v2/posts")
    # Son çare, standart WordPress adresi.
    adaylar.append(_koken(sayfa.url) + "/wp-json/wp/v2/posts")
    son_hata = "WordPress API'si yok"
    # Tekrarları atıp sırayla dene.
    for api in dict.fromkeys(adaylar):
        try:
            # Sadece birkaç yazı ve gerekli alanları iste.
            r = client.get(guvenli_url(api, cozumle) + "?" + urlencode(
                {"per_page": ORNEK_SAYISI, "_fields": "id,date,modified,title,link"}))
            # Cevap 200 ve JSON değilse bu adres değil.
            if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                continue
            yazilar = parse_posts(r.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            son_hata = f"WordPress API'si okunamadı ({e.__class__.__name__})"
            continue
        # Yazı geldiyse WordPress bulundu.
        if yazilar:
            return {"tip": "wordpress", "ayarlar": {"api_url": api},
                    "aciklama": "Sitenin WordPress veri servisi bulundu; en sağlam yol.",
                    "ornekler": [_ornek(y.baslik, y.yayin.date(), y.url) for y in yazilar]}
    raise _Yok(son_hata)


# ---- 2. RSS/Atom ---------------------------------------------------------------------------------------

# İkinci yol, sitenin RSS ya da Atom akışı var mı diye bakar.
def _rss(client, sayfa: _Sayfa, agac: HTMLParser | None, cozumle) -> dict:
    adaylar: list[str | _Sayfa] = []
    # Girilen adresin kendisi akışsa ilk aday o.
    if akis_mi(sayfa.veri):
        adaylar.append(sayfa)
    # Sayfada "alternatif: RSS" etiketi varsa adresini aday yap.
    if agac is not None:
        for link in agac.css('link[rel="alternate"]'):
            tur = (link.attributes.get("type") or "").lower()
            if ("rss" in tur or "atom" in tur) and (href := link.attributes.get("href")):
                adaylar.append(urljoin(sayfa.url, href))
    # Yaygın akış adresleri, önce girilen sayfanın altında (Reddit gibi "/.rss" dahil), sonra sitenin kökünde.
    kok = _koken(sayfa.url)
    taban = sayfa.url.split("#")[0].split("?")[0].rstrip("/")
    if taban != kok:
        adaylar += [taban + "/.rss", taban + "/feed", taban + "/rss"]
    adaylar += [kok + "/feed", kok + "/rss"]
    gorulen = set()
    # Her adayı indir, gerçekten akışsa ve içinden duyuru okunuyorsa bulundu.
    for aday in adaylar:
        try:
            akis = aday if isinstance(aday, _Sayfa) else _getir(client, aday, cozumle)
        except (httpx.HTTPError, ValueError, _Yok):
            continue
        if akis.url in gorulen or not akis_mi(akis.veri):
            continue
        gorulen.add(akis.url)
        try:
            ogeler = parse_akis(akis.veri, akis.url)
        except ValueError:
            continue
        if ogeler:
            # En yeni duyurular önce.
            ogeler.sort(key=lambda o: o.tarih, reverse=True)
            return {"tip": "rss", "ayarlar": {"akis_url": akis.url},
                    "aciklama": "Sitenin duyuru akışı (RSS/Atom) bulundu; site tasarımı değişse de bozulmaz.",
                    "ornekler": [_ornek(o.baslik, o.tarih, o.url) for o in ogeler[:ORNEK_SAYISI]],
                    "icerik_urls": [(o.url, o.baslik) for o in ogeler[:ICERIK_SAYFA_SAYISI]]}
    raise _Yok("RSS/Atom akışı yok")


# ---- 3. düz HTML ---------------------------------------------------------------------------------------

# 3. YOL için yardımcılar. Bir düğümün basit CSS seçicisi, "div.duyuru.liste".
def _basit(d: Node) -> str:
    """etiket.sinif1.sinif2 (CSS'te kaçış gerektiren sınıflar alınmaz)."""
    siniflar = [s for s in (d.attributes.get("class") or "").split() if _SINIF.match(s)]
    return d.tag + "".join(f".{s}" for s in siniflar)


# Düğümün kimliği (id) varsa "div#icerik", yoksa basit seçici.
def _kimlikli(d: Node) -> str:
    kimlik = d.attributes.get("id") or ""
    return f"{d.tag}#{kimlik}" if _SINIF.match(kimlik) else _basit(d)


# Düğümün metni, tek satır halinde.
def _metin(d: Node) -> str:
    return " ".join(d.text(separator=" ", strip=True).split())


# Kutudaki en uzun metinli gerçek linki bulur (duyuru başlığı genelde budur).
def _baslik_linki(kutu: Node) -> Node | None:
    """Kutudaki duyuru linki. Metni en uzun olan, en az 10 karakterlik gerçek link seçilir, kutunun kendisi de olabilir."""
    en, uzunluk = None, 9
    for link in kutu.css("a[href]"):
        href = (link.attributes.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        if len(_metin(link)) > uzunluk:
            en, uzunluk = link, len(_metin(link))
    return en


# Bir düğümün başka bir kutunun içinde olup olmadığına bakar.
def _icinde(d: Node, kutu: Node) -> bool:
    while d is not None:
        if d.mem_id == kutu.mem_id:
            return True
        d = d.parent
    return False


# Bir hedef düğüm için olası seçiciler, sınıflı hali, özellikli hali, sadece etiket adı.
def _goreli_adaylar(hedef: Node) -> list[str]:
    adaylar = [_basit(hedef)]
    for ad, deger in hedef.attributes.items():
        if ad not in ("class", "id", "href", "style") and deger and _DEGER.match(deger):
            adaylar.append(f'{hedef.tag}[{ad}="{deger}"]')
    adaylar.append(hedef.tag)
    return list(dict.fromkeys(adaylar))


# Bütün kutularda kendi hedefini bulabilen seçiciyi seçer (en az %80'inde tutmalı).
def _goreli_secici(kutular: list[Node], hedefler: list[Node]) -> str | None:
    """Kutuların en az %80'inde kendi hedefini (ilk eşleşme olarak) bulan en dar seçici. Adaylar bütün
    hedeflerden toplanır (bir öğede sınıf eksik olabilir)."""
    adaylar = dict.fromkeys(s for h in hedefler for s in _goreli_adaylar(h))
    # Önce daha belirli (sınıflı/özellikli) seçicileri dene.
    for secici in sorted(adaylar, key=lambda s: (s == s.split(".")[0].split("[")[0], "[" in s)):
        tutan = sum(1 for k, h in zip(kutular, hedefler)
                    if (n := k.css_first(secici)) is not None and n.mem_id == h.mem_id)
        if tutan >= 0.8 * len(kutular):
            return secici
    return None


# Kutunun başlık düğümü, genelde başlık linki, kutunun tamamı linkse içindeki ilk uzun yazı.
def _baslik_dugumu(kutu: Node, tarih_idler: set[int]) -> Node | None:
    link = _baslik_linki(kutu)
    if link is None:
        return None
    if link.mem_id != kutu.mem_id:
        return link
    # Bütün kutu bir link (ör. Rekabet Kurumu, <a><table>…), başlık, tarih olmayan ve metni yeterince uzun İLK iç
    # düğüm (başlık özetten önce gelir, en uzunu seçmek bazen özeti seçer).
    for d in kutu.traverse():
        if d.mem_id in tarih_idler or d.tag in ("-text", "script", "style"):
            continue
        if len(d.text(deep=False, strip=True)) >= 10:
            return d
    return None


# 3. YOL, Sayfada tekrar eden "tarih + başlık linki" kutularını bulup liste/başlık/tarih seçicilerini çıkarır.
def liste_bul(html: str, url: str) -> dict:
    """Duyuru listesi sayfasından seçicileri bulur. {oge, baslik, tarih, ogeler} döner, bulamazsa _Yok döner.
    Önce tarihi ayrı bir alanda olan listeler aranır. Yoksa tarihi başlığın içinde yazan listeler aranır,
    örneğin Ticaret Bakanlığı'ndaki gibi "… Toplantısı - 01.10.2026"."""
    # Önce tarihi ayrı alanda olan listeleri dene, olmazsa tarihi başlığın içinde olanları.
    try:
        return _liste_bul(html, url, tarih_baslikta=False)
    except _Yok as ilk:
        try:
            return _liste_bul(html, url, tarih_baslikta=True)
        except _Yok:
            raise ilk from None


# Asıl arama. tarih_baslikta, tarih başlık yazısının içinde mi (ör. "... Toplantısı - 01.10.2026").
def _liste_bul(html: str, url: str, tarih_baslikta: bool) -> dict:
    # Sayfayı ağaca çevir, kod bloklarını sil.
    agac = HTMLParser(html)
    for d in agac.css("script, style, noscript"):
        d.decompose()
    govde = agac.body
    if govde is None:
        raise _Yok("sayfa boş")
    # Önce sayfadaki tarih gibi görünen bütün kısa yazıları bul.
    tarihler = []
    for d in govde.traverse():
        if d.tag in ("-text", "a"):  # başlık linkindeki tarih ("… 01.10.2026 tarihli …") liste tarihi değildir
            continue
        metin = _metin(d)
        if 6 <= len(metin) <= (250 if tarih_baslikta else 40) and tarih_oku(metin):
            tarihler.append(d)
    idler = {d.mem_id for d in tarihler}
    # En içteki tarih düğümleri (üstleri de aynı tarihi içeriyor ama daha genel).
    tarihler = [d for d in tarihler if not any(c.mem_id in idler and c.mem_id != d.mem_id for c in d.traverse())]
    # Tarih başlığın içindeyse başlık düğümü tarih düğümünün kendisi olabilir.
    tarih_idler = set() if tarih_baslikta else {d.mem_id for d in tarihler}
    # Hiç tarih yoksa liste de yok.
    if not tarihler:
        raise _Yok("sayfada tarih bulunamadı")

    # Sonra her tarihin içinde bulunduğu ve başlık linki olan en küçük kutuyu bul, en fazla 8 kat yukarı çık.
    # Her tarih için onu içeren en küçük "başlık linkli" kutu, kutular yapılarına göre gruplanır.
    gruplar: dict[tuple[str, str], dict[int, tuple[Node, Node]]] = {}
    for t in tarihler:
        kutu = t
        for _ in range(8):
            kutu = kutu.parent
            if kutu is None or kutu.tag in ("body", "html"):
                kutu = None
                break
            if _baslik_linki(kutu) is not None:
                break
        else:
            kutu = None
        if kutu is None:
            continue
        # Kutuyu yapısına göre grupla, aynı biçimdeki kutular aynı listenin elemanlarıdır.
        imza = (_basit(kutu), _kimlikli(kutu.parent) if kutu.parent is not None else "")
        gruplar.setdefault(imza, {}).setdefault(kutu.mem_id, (kutu, t))

    # Sonra en kalabalık gruptan başlayarak her grup için seçicileri dene.
    adaylar = sorted(gruplar.items(), key=lambda g: len(g[1]), reverse=True)
    for (oge_basit, ebeveyn), uyeler in adaylar:
        # 3'ten az elemanlı grup liste sayılmaz (sıralı olduğu için sonrakiler de az).
        if len(uyeler) < EN_AZ_OGE:
            break
        kutular = [k for k, _ in uyeler.values()]
        tarih_dugumleri = [t for _, t in uyeler.values()]
        # Her kutunun başlığını bul, bulunamayan varsa bu grup olmaz.
        basliklar = [_baslik_dugumu(k, tarih_idler) for k in kutular]
        if any(b is None for b in basliklar):
            continue
        # Başlık ve tarih için bütün kutularda çalışan seçicileri bul.
        baslik = _goreli_secici(kutular, basliklar)
        tarih = _goreli_secici(kutular, tarih_dugumleri)
        if baslik is None or tarih is None:
            continue
        dede = kutular[0].parent.parent if kutular[0].parent is not None else None
        # Tercih sırası, "üst > öğe" (yeterince dar), sadece öğe (çok genel, "a" gibi), dede dahil (düzen sınıflarına
        # bağlanır, site tasarımı değişince kırılır). Bu gruptaki duyuruların sayısını tam tutan önce seçilir.
        secenekler = [f"{ebeveyn} > {oge_basit}", oge_basit]
        if dede is not None and dede.tag not in ("body", "html"):
            secenekler.append(f"{_kimlikli(dede)} > {ebeveyn} > {oge_basit}")
        sonuclar = []
        # Her öğe seçicisi adayıyla listeyi gerçekten okumayı dene, en az 3 duyuru okunmalı.
        for oge in dict.fromkeys(secenekler):
            try:
                ogeler = parse_liste(html, url, oge, baslik, tarih)
            except ValueError:
                continue
            if len(ogeler) >= EN_AZ_OGE:
                sonuclar.append((abs(len(ogeler) - len(uyeler)), secenekler.index(oge), oge, ogeler))
        # Okunan sayı gruptaki kutu sayısına en yakın (ve tercih sırası önde) olanı seç.
        if sonuclar:
            _, _, oge, ogeler = min(sonuclar, key=lambda s: s[:2])
            return {"oge": oge, "baslik": baslik, "tarih": tarih, "ogeler": ogeler}
    raise _Yok("sayfada tekrar eden \"tarih + başlık linki\" kutuları bulunamadı")


# Düğümün metni, boş olmayan satırlar listesi olarak.
def _satirlar(d: Node) -> list[str]:
    return [s for s in (" ".join(x.split()) for x in d.text(separator="\n").splitlines()) if s]


# Duyuru sayfasında asıl metnin kutusunu bulur, diğer duyuru sayfalarında da olan satırlar (menü vb.) gürültü sayılır.
def _icerik_kutusu(agac: HTMLParser, ortak: set[str]) -> Node | None:
    """Sayfaya özgü satırların (diğer duyuru sayfalarında olmayan) hepsini kapsayan en dar kutu. Menü, gizli
    KVKK penceresi, sayfa altı gibi her sayfada aynı olan metin "gürültü" sayılır."""
    govde = agac.body
    if govde is None:
        return None
    # Bu sayfaya özgü toplam yazı miktarı, çok azsa vazgeç.
    ozgu_toplam = sum(len(s) for s in _satirlar(govde) if s not in ortak)
    if ozgu_toplam < 30:
        return None
    en_iyi, en_iyi_puan = None, 0.0
    # Her kutuya puan ver, özgü yazı çok, ortak (gürültü) yazı az olan kazanır.
    for d in govde.traverse():
        if d.tag in ("-text", "script", "style", "a", "span", "p", "li", "h1", "h2", "h3", "h4", "b", "strong"):
            continue
        satirlar = _satirlar(d)
        ozgu = sum(len(s) for s in satirlar if s not in ortak)
        if ozgu < 0.6 * ozgu_toplam:  # duyurunun çoğunu kapsamayan kutu (ör. sadece başlık) değil
            continue
        gurultu = sum(len(s) for s in satirlar if s in ortak)
        puan = ozgu - 0.5 * gurultu
        if puan >= en_iyi_puan:  # eşitlikte içteki (sonra gelen) kutu, aynı metni taşıyan en dar kutu
            en_iyi, en_iyi_puan = d, puan
    return en_iyi


# Bulunan metin kutusu için seçici adayları, kendisi ya da kimliği/sınıfı olan yakın üstü.
def _icerik_adaylari(d: Node) -> list[str]:
    """Kutunun seçici adayları. Önce kutunun kendisi (id ya da sınıf), yoksa id ya da sınıfı olan en yakın üst öğe."""
    adaylar = []
    ust = d
    for _ in range(4):
        if ust is None or ust.tag in ("body", "html"):
            break
        if _SINIF.match(ust.attributes.get("id") or ""):
            adaylar.append(_kimlikli(ust))
        if ust.attributes.get("class"):
            adaylar.append(_basit(ust))
        ust = ust.parent
    return adaylar


# Metnin 3 harften uzun kelimeleri (Türkçe küçük harfle).
def _kelimeler(metin: str) -> set[str]:
    return {k for k in re.findall(r"\w+", tr_kucuk(metin)) if len(k) >= 3}


# Bulunan metin, duyurunun başlığındaki kelimelerin en az yüzde 60'ını içermeli.
def _basligi_iceriyor(metin: str, baslik: str) -> bool:
    """Duyuru sayfasında bulunan metin o duyurunun başlığını, en azından kelimelerinin çoğunu içermeli. İçermiyorsa yanlış
    alan seçilmiştir, örneğin menü ya da haber kutusu."""
    kelimeler = _kelimeler(baslik)
    return not kelimeler or len(kelimeler & _kelimeler(metin)) >= 0.6 * len(kelimeler)


# İçerik seçicisini bulur, ilk 3 duyuru sayfasını indirip karşılaştırır, hepsinde doğru metni veren seçiciyi seçer.
def icerik_secicisi_bul(client, duyurular: list[tuple[str, str]], cozumle) -> tuple[str, str]:
    """Bütün duyuru sayfalarında (adres ve başlık) o sayfanın kendi metnini bulan seçiciyi ve ilk sayfadan bir parçayı döner.
    Sayfalar karşılaştırılır, her sayfada aynı olan metin (menü gibi) içerik sayılmaz."""
    sayfalar, basliklar = [], []
    # İlk 3 duyuru sayfasını indir.
    for u, baslik in duyurular[:ICERIK_SAYFA_SAYISI]:
        try:
            sayfalar.append(_getir(client, u, cozumle, ("https", "http")).html)
            basliklar.append(baslik)
        except (httpx.HTTPError, ValueError, _Yok):
            continue
    if not sayfalar:
        raise _Yok("duyuru sayfaları açılamadı")
    # Her sayfanın ağacını hazırla (kod blokları ve gizli öğeler silinmiş).
    agaclar = []
    for html in sayfalar:
        agac = HTMLParser(html)
        for d in agac.css("script, style, noscript"):
            d.decompose()
        gizlileri_at(agac)
        agaclar.append(agac)
    # Her sayfanın satır kümesi (sayfalar arası ortak satırları bulmak için).
    satir_kumeleri = [set(_satirlar(a.body)) if a.body is not None else set() for a in agaclar]
    adaylar = []
    # İlk iki sayfada metin kutusunu bul, seçici adaylarını topla.
    for i, agac in enumerate(agaclar[:2]):
        if len(agaclar) > 1:
            ortak = set.intersection(*(k for j, k in enumerate(satir_kumeleri) if j != i)) & satir_kumeleri[i]
            kutu = _icerik_kutusu(agac, ortak)
        else:  # tek sayfa, karşılaştıracak başka sayfa yok, ana metin tahmini
            kutu = ana_icerik_dugumu(agac, en_az=30)
        if kutu is not None:
            adaylar += _icerik_adaylari(kutu)
    # Her adayı (ve genel "article", "main" seçicilerini) bütün sayfalarda dene.
    for secici in dict.fromkeys([*adaylar, "article", "main"]):
        try:
            metinler = [parse_icerik(h, secici) for h in sayfalar]
        except ValueError:
            continue
        # Her sayfada metin var, sayfalar arasında farklı (her yerde aynı olan kutu içerik değildir) ve o duyurunun
        # başlığını içeriyor.
        if (all(len(m) >= 30 for m in metinler) and len(set(metinler)) == len(metinler)
                and all(_basligi_iceriyor(m, b) for m, b in zip(metinler, basliklar))):
            return secici, metinler[0]
    raise _Yok("duyuru sayfalarında metin alanı bulunamadı")


# 3. YOL'un kendisi, sayfadan listeyi bul, örnekleri ve içerik sayfası adreslerini hazırla.
def _html(client, sayfa: _Sayfa, agac: HTMLParser | None, cozumle) -> dict:
    if agac is None:
        raise _Yok("adres bir web sayfası değil")
    if not sayfa.url.startswith("https://"):
        raise _Yok("liste sayfası https değil")
    liste = liste_bul(sayfa.html, sayfa.url)
    ogeler = sorted(liste["ogeler"], key=lambda o: o.tarih, reverse=True)
    return {"tip": "html", "ayarlar": {"liste_url": sayfa.url, "oge": liste["oge"], "baslik": liste["baslik"],
                                       "tarih": liste["tarih"]},
            "aciklama": f"Sayfadaki duyuru listesi okundu ({len(ogeler)} duyuru). Site tasarımı değişirse "
                        "ayarların güncellenmesi gerekir.",
            "ornekler": [_ornek(o.baslik, o.tarih, o.url) for o in ogeler[:ORNEK_SAYISI]],
            "icerik_urls": [(o.url, o.baslik) for o in ogeler[:ICERIK_SAYFA_SAYISI]]}


# ---- hepsi ---------------------------------------------------------------------------------------------

# Denenecek yolların sırası (en sağlamdan en kırılgana).
_YOLLAR: list[tuple[str, Callable]] = [("WordPress API", _wordpress), ("RSS/Atom akışı", _rss), ("Düz HTML listesi", _html)]


# Hiçbir yol bulamazsa sayfa JavaScript ile mi doluyor diye bakar, öyleyse kullanıcıya bunu söyleriz.
def _javascript_mi(sayfa: _Sayfa, adimlar: list[dict]) -> bool:
    """Listenin sayfaya JavaScript'le sonradan yüklenip yüklenmediğine bakar. "JavaScript'i açın" yazısı varsa ya da betik çok ve sayfada
    hiç tarih yoksa öyle sayılır. Bu durumda menüler düz HTML'de olsa da duyuru listesi yoktur."""
    agac = HTMLParser(sayfa.html)
    betik = len(agac.css("script"))
    for d in agac.css("script, style"):
        d.decompose()
    gorunen = " ".join((agac.body.text(separator=" ") if agac.body else "").split()).lower()
    tarihsiz = any(a["not"] == "sayfada tarih bulunamadı" for a in adimlar)
    return "enable javascript" in gorunen or "javascript'i" in gorunen or (betik >= 3 and (tarihsiz or len(gorunen) < 400))


# Asıl fonksiyon bu. Adresi alır, üç yolu sırayla dener, ilk bulunanı ya da neden bulunamadığını döner.
def bul(adres: str, client: httpx.Client | None = None, cozumle: Cozumleyici | None = None) -> dict:
    """Bir sözlük döner. Alanları bulundu, tip, tip_etiketi, ayarlar, aciklama, onerilen_ad, ornekler, icerik_ornegi ve adimlar.
    adimlar her biri yol, sonuc ve not alanı olan bir listedir. Adrese hiç ulaşılamazsa ValueError verir."""
    # İstemci verilmediyse kısa zaman aşımlı, boyut sınırlı kendi istemcimizi aç.
    kendi = client is None
    client = client or make_client(zaman_asimi=DENE_ZAMAN_ASIMI, en_fazla_bayt=DENE_EN_FAZLA_BAYT)
    try:
        yollar = _YOLLAR
        adimlar = []
        engel = None
        try:
            # Adresi indir, ulaşılamıyorsa anlaşılır hata.
            sayfa = _getir(client, adres, cozumle)
        except _Yok as e:
            raise ValueError(f"Adres açılamadı: {e}") from e
        except httpx.HTTPStatusError as e:
            # Site sayfayı engelledi ya da sayfa yok (403, 404). Akış adresleri yine açık olabilir, sadece onları dene.
            engel = ValueError(f"Adrese ulaşılamadı: {e.__class__.__name__} ({e})")
            sayfa = _Sayfa(adres, b"", "", None)
            yollar = [y for y in _YOLLAR if y[1] is _rss]
            adimlar.append({"yol": "Sayfa", "sonuc": "yok",
                            "not": f"sayfa açılmadı ({e.response.status_code}), sadece akış adresleri denendi"})
        except httpx.HTTPError as e:
            raise ValueError(f"Adrese ulaşılamadı: {e.__class__.__name__} ({e})") from e
        # Sayfa akış ya da JSON değilse HTML ağacına çevir.
        agac = None if engel or akis_mi(sayfa.veri) or "json" in sayfa.tur else HTMLParser(sayfa.html)
        # Sayfanın <title>'ı önerilen ad olarak kullanılır.
        baslik = agac.css_first("title") if agac is not None else None
        onerilen = " ".join(baslik.text().split())[:100] if baslik is not None else ""
        # Yolları sırayla dene, her birinin sonucunu "adımlar" listesine yaz (panelde gösteriliyor).
        for ad, yol in yollar:
            try:
                sonuc = yol(client, sayfa, agac, cozumle)
            except _Yok as e:
                adimlar.append({"yol": ad, "sonuc": "yok", "not": str(e)})
                continue
            except httpx.HTTPError as e:
                adimlar.append({"yol": ad, "sonuc": "yok", "not": f"istek başarısız ({e.__class__.__name__})"})
                continue
            # HTML ve RSS için içerik seçicisini de bulmaya çalış.
            icerik_ornegi = None
            if sonuc["tip"] in ("html", "rss"):
                try:
                    secici, icerik_ornegi = icerik_secicisi_bul(client, sonuc.pop("icerik_urls"), cozumle)
                except _Yok as e:
                    if sonuc["tip"] == "html":  # html tipinde içerik seçicisi zorunlu
                        adimlar.append({"yol": ad, "sonuc": "yok", "not": f"liste bulundu ama {e}"})
                        continue
                else:
                    sonuc["ayarlar"]["icerik"] = secici
            # Bulundu, sonucu panelin istediği biçimde döndür.
            adimlar.append({"yol": ad, "sonuc": "bulundu", "not": sonuc["aciklama"]})
            return {"bulundu": True, **sonuc, "tip_etiketi": TIP_FORMLARI[sonuc["tip"]].etiket,
                    "onerilen_ad": onerilen, "icerik_ornegi": (icerik_ornegi or "")[:500] or None, "adimlar": adimlar}
        # Sayfa açılmamıştı ve akış da bulunamadıysa asıl hatayı ver.
        if engel:
            raise engel
        # Hiçbir yol olmadı.
        aciklama = "Otomatik bulunamadı; ayarları elle girebilirsiniz."
        if agac is not None and _javascript_mi(sayfa, adimlar):
            aciklama = ("Duyuru listesi bu sayfaya JavaScript'le sonradan yükleniyor; düz HTML olarak okunamıyor. "
                        "Sitede RSS ya da veri servisi (API) adresi varsa onu girin. Yoksa bu site panelden "
                        "eklenemez (tarayıcı tipi gerekir; sunucu yöneticisine danışın).")
        return {"bulundu": False, "aciklama": aciklama, "onerilen_ad": onerilen, "adimlar": adimlar}
    finally:
        if kendi:
            client.close()

"""RSS 2.0 ya da Atom akışı olan kaynaklar. Birçok kurum sitesinde duyuru akışı var ve HTML seçicilerinden sağlamdır,
site tasarımı değişse de akış aynı kalır. Panelden eklenebilir, adres girilince kendiliğinden bulunur (kaynak_bulucu).

Örnek bir config/kaynaklar.toml kaydı aşağıda.
    [[kaynak]]
    ad = "ornek_kurum"
    tip = "rss"
    etiket = "Örnek Kurum Duyurusu"
    akis_url = "https://www.ornek.gov.tr/rss/duyurular"
    icerik = "div.icerik"     # isteğe bağlı, duyuru sayfasında metnin bulunduğu alan, boşsa ana metin kendiliğinden bulunur

Güvenlik. XML'de DOCTYPE ve ENTITY reddedilir, dış varlık ve "milyar kahkaha" saldırılarına karşı. Akış ve duyuru
adresleri her istekte iç ağ kontrolünden geçer, html tipiyle aynı şekilde.
"""
# RSS/Atom, sitelerin "yeni yazılarım bunlar" diye yayımladığı, makinenin okuması için yapılmış XML listesi.
# Bu dosya o listeyi okuyup duyuruları çıkarır.

import re
# XML okumak için Python'un kendi kütüphanesi. ET kısaltmasıyla kullanıyoruz.
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
# RSS'teki "Wed, 02 Oct 2026 10:00:00 +0300" gibi tarihleri çözen hazır fonksiyon (e-posta tarih biçimiyle aynı).
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import httpx

# ana_metin, içerik seçicisi verilmemişse sayfadaki asıl yazıyı kendiliğinden bulmaya çalışan fonksiyon.
from mevzuat.icerik import Icerik, ana_metin
from mevzuat.sources.base import KayitTaslagi, TaramaAdimi
# html tipindeki güvenli indirme fonksiyonunu burada da kullanıyoruz (adres kontrolü + indirme).
from mevzuat.sources.html import _guvenli_indir
from mevzuat.sources.liste import parse_icerik, tarih_oku

# Atom akışlarında etiket adlarının başına bu adres ekleniyor (XML "ad alanı"). Arama yaparken lazım.
ATOM = "{http://www.w3.org/2005/Atom}"


# Akıştaki tek bir duyuru.
@dataclass(frozen=True)
class AkisOgesi:
    baslik: str
    url: str
    tarih: date


# Akıştaki tarih yazısını gerçek tarihe çevirir. Üç biçimi sırayla dener.
def _tarih(metin: str | None) -> date | None:
    """RSS pubDate (RFC 822), Atom (ISO 8601) ya da Türkçe sitelerde görülen "02.10.2026" gibi biçimler."""
    # None gelirse boş yazı say, baştaki/sondaki boşlukları at.
    metin = (metin or "").strip()
    if not metin:
        return None
    # Önce RSS'in standart tarih biçimini dene.
    try:
        return parsedate_to_datetime(metin).date()
    except (TypeError, ValueError, IndexError):
        pass
    # Olmazsa Atom'un tarih biçimini dene. Sondaki Z harfi UTC demek, Python'un eski sürümleri onu tanımadığı için +00:00 yapıyoruz.
    try:
        return datetime.fromisoformat(metin.replace("Z", "+00:00")).date()
    # O da olmazsa 02.10.2026 ya da 2 Ekim 2026 gibi Türkçe biçimleri dene.
    except ValueError:
        return tarih_oku(metin)


# İndirilen dosyanın gerçekten bir RSS ya da Atom akışı mı yoksa normal web sayfası mı olduğuna ilk 2 KB'a bakarak karar verir.
def akis_mi(veri: bytes) -> bool:
    bas = veri[:2048].lstrip().lower()
    # Dosya xml başlığıyla başlıyor ve içinde rss, feed ya da rdf etiketi geçiyorsa, ya da doğrudan bu etiketlerle başlıyorsa akıştır.
    return bas.startswith(b"<?xml") and (b"<rss" in bas or b"<feed" in bas or b"<rdf" in bas) or \
        bas.startswith((b"<rss", b"<feed"))


# Akış XML'ini okuyup içindeki duyuruları (başlık, adres, tarih) çıkarır.
def parse_akis(veri: bytes, taban_url: str) -> list[AkisOgesi]:
    # Güvenlik, DOCTYPE/ENTITY içeren XML, sunucunun dosyalarını okutmaya ya da belleği şişirmeye kullanılabilir. Okumuyoruz.
    # Dosyanın tamamına bakılır. Sadece başına bakmak yetmiyordu, başa uzun bir açıklama konup tanım arkaya itilebiliyordu.
    if re.search(rb"<!(DOCTYPE|ENTITY)", veri, re.IGNORECASE):
        raise ValueError("Akışta DOCTYPE/ENTITY var; güvenlik için okunmaz")
    # XML'i ağaç yapısına çevir. Bozuksa anlaşılır bir hata ver.
    try:
        kok = ET.fromstring(veri)
    except ET.ParseError as e:
        raise ValueError(f"Akış XML olarak okunamadı: {e}") from e
    # Sonuç listesi ve görülen adresler.
    sonuc, gorulen = [], set()
    # Üç akış türünün de duyuru etiketlerini topla, RSS 2.0 "item", Atom "entry", RSS 1.0 "item".
    for oge in [*kok.iter("item"), *kok.iter(f"{ATOM}entry"), *kok.iter("{http://purl.org/rss/1.0/}item")]:
        # Başlığı hangi türdeysek oradan al. or, ilki boşsa sıradakine bakar.
        baslik = oge.findtext("title") or oge.findtext(f"{ATOM}title") or oge.findtext("{http://purl.org/rss/1.0/}title")
        # Linki al (RSS'te etiketin içinde yazı olarak durur).
        link = oge.findtext("link") or oge.findtext("{http://purl.org/rss/1.0/}link")
        # Atom'da link bir özellik (href) olarak durur ve birden fazla olabilir, "alternate" olanı (asıl sayfa) seç.
        if not link:
            atom_link = next((a for a in oge.findall(f"{ATOM}link") if a.get("rel", "alternate") == "alternate"), None)
            link = atom_link.get("href") if atom_link is not None else None
        # Tarihi, bulunduğu ilk etiketten al ve çevir.
        tarih = _tarih(oge.findtext("pubDate") or oge.findtext(f"{ATOM}published") or oge.findtext(f"{ATOM}updated")
                       or oge.findtext("{http://purl.org/dc/elements/1.1/}date"))
        # Başlıktaki fazla boşlukları temizle.
        baslik = " ".join((baslik or "").split())
        # Başlığı, linki ya da tarihi eksik olanı atla.
        if not baslik or not link or tarih is None:
            continue
        # Yarım linki tam adrese çevir.
        url = urljoin(taban_url, link.strip())
        # Aynısı zaten eklendiyse atla.
        if url in gorulen:
            continue
        gorulen.add(url)
        sonuc.append(AkisOgesi(baslik=baslik, url=url, tarih=tarih))
    return sonuc


# RSS tipi kaynak.
class RssKaynagi:
    # Kurulum, akış adresi zorunlu, içerik seçicisi isteğe bağlı.
    def __init__(self, ad: str, etiket: str, akis_url: str, icerik: str = "", ortusme_gun: int = 1):
        if not akis_url.strip():
            raise ValueError("akis_url boş olamaz")
        self.ad = ad
        self.etiket = etiket
        self.akis_url = akis_url
        self.icerik_secici = icerik.strip()
        # Checkpoint gününden kaç gün geriye bakılsın (tekrar gelenler zaten elenir).
        self.ortusme = timedelta(days=ortusme_gun)

    # Akış dosyasını güvenli şekilde (adres kontrolüyle) indirir.
    def _akis(self, client: httpx.Client) -> bytes:
        from mevzuat.kaynak_yonetimi import guvenli_url  # döngüsel içe aktarmayı önlemek için burada

        response = client.get(guvenli_url(self.akis_url, semalar=("https",)))
        response.raise_for_status()
        return response.content

    # Tarama, akışı oku, checkpoint'ten yeni olanları kayıt taslağı yap. Mantık liste.py'deki tara ile aynı.
    def tara(
        self, client: httpx.Client, checkpoint: str | None, bugun: date, simdi: datetime
    ) -> Iterator[TaramaAdimi]:
        ogeler = parse_akis(self._akis(client), self.akis_url)
        # Akış boş geldiyse sessiz kalma, hata ver (adres değişmiş olabilir).
        if not ogeler:
            raise ValueError(f"{self.ad}: akışta hiç duyuru okunamadı; adres değişmiş olabilir")
        # Bu tarihten eskiler alınmaz.
        esik = (date.fromisoformat(checkpoint) if checkpoint else bugun) - (self.ortusme if checkpoint else timedelta())
        bilgi = {}
        # Akıştaki en eski duyuru bile yeniyse arada kaçan olabilir, özete uyarı düş.
        if min(o.tarih for o in ogeler) >= esik and checkpoint:
            bilgi = {"uyari": "akışın tamamı yeni, eski duyurular kaçmış olabilir"}
        # Eşikten yeni her duyuru için kayıt taslağı.
        kayitlar = [
            KayitTaslagi(dis_id=o.url, yayin_tarihi=o.tarih, baslik=o.baslik, url=o.url,
                         kaynakca=f"{self.etiket}, {o.tarih:%d.%m.%Y}: {o.baslik}", tur="Duyuru")
            for o in ogeler if o.tarih >= esik
        ]
        # Checkpoint bugüne ilerler.
        yield TaramaAdimi(kayitlar, checkpoint=bugun.isoformat(), bilgi=bilgi)

    # Duyuru sayfasını indirip metnini çıkarır. Seçici verildiyse o alanı, verilmediyse sayfanın ana metnini alır.
    def icerik(self, client: httpx.Client, dis_id: str, url: str) -> Icerik:
        html = _guvenli_indir(client, url, ("https", "http"))
        return Icerik(parse_icerik(html, self.icerik_secici) if self.icerik_secici else ana_metin(html))

    # Denemeler için N gün önceki tarih.
    def geriye_checkpoint(self, bugun: date, simdi: datetime, gun: int) -> str:
        return (bugun - timedelta(days=gun)).isoformat()

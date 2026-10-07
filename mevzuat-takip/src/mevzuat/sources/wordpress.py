"""WordPress REST API'si olan kaynaklar. Örneğin MASAK sitesi React ile yapılmış, içerik /portal/v2/posts adresinden geliyor.

Örnek bir config/kaynaklar.toml kaydı aşağıda.
    [[kaynak]]
    ad = "masak"
    tip = "wordpress"
    etiket = "MASAK Duyurusu"
    api_url = "https://masak.hmb.gov.tr/portal/v2/posts"
"""
# WordPress ile yapılmış siteler, yazılarını JSON olarak veren hazır bir servise (REST API) sahip.
# Sayfayı kazımak yerine bu servisi soruyoruz, daha hızlı ve site tasarımı değişince bozulmuyor.

# html.unescape, "&amp;" gibi HTML kodlarını normal karaktere (&) çevirir.
import html
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from mevzuat.icerik import Icerik, html_metni
from mevzuat.sources.base import KayitTaslagi, TaramaAdimi


# API'den gelen tek bir yazı (duyuru).
@dataclass(frozen=True)
class Yazi:
    # WordPress'in verdiği numara.
    id: int
    # Yayımlanma ve son güncellenme zamanı.
    yayin: datetime
    guncelleme: datetime
    baslik: str
    url: str


# Başlıktaki HTML etiketlerini ve kodlarını temizler, böylece "&amp;" gibi yazılar normal "&" işaretine döner.
def temizle(metin: str) -> str:
    # Önce <...> etiketlerini sil, sonra &amp, gibi kodları çöz.
    metin = html.unescape(re.sub(r"<[^>]+>", "", metin))
    # Birden fazla boşluğu teke indir, baş/son boşlukları at.
    return re.sub(r"\s+", " ", metin).strip()


# API'nin döndürdüğü JSON listesini Yazi nesnelerine çevirir.
def parse_posts(veri: list[dict]) -> list[Yazi]:
    return [
        Yazi(
            id=p["id"],
            # "2026-10-02T10:00:00" yazısını tarih-saate çevir.
            yayin=datetime.fromisoformat(p["date"]),
            guncelleme=datetime.fromisoformat(p["modified"]),
            # Başlık "rendered" içinde HTML olarak geliyor, temizliyoruz.
            baslik=temizle(p["title"]["rendered"]),
            url=p["link"],
        )
        for p in veri
    ]


# Belli bir zamandan sonraki bütün yazıları, sayfa sayfa (her seferinde 50 tane) çeker.
def yazilar(client: httpx.Client, api_url: str, sonra: datetime, per_page: int = 50) -> list[Yazi]:
    """`sonra` tarihinden sonra yayımlanan yazılar (yeniden eskiye), tüm sayfalar."""
    sonuc = []
    sayfa = 1
    # Son sayfaya gelene kadar dön.
    while True:
        # API'ye gönderilecek sorgu, bu tarihten sonrası, sayfa başına 50, şu sayfa, sadece şu alanlar (gereksiz veri gelmesin).
        params = {
            "after": sonra.strftime("%Y-%m-%dT%H:%M:%S"),
            "per_page": per_page,
            "page": sayfa,
            "_fields": "id,date,modified,title,link",
        }
        response = client.get(api_url, params=params)
        response.raise_for_status()
        # Gelen yazıları sonuca ekle.
        sonuc.extend(parse_posts(response.json()))
        # Son sayfadan sonrası istenirse API 400 döner, toplam sayfa sayısını header'dan oku.
        toplam_sayfa = int(response.headers.get("X-WP-TotalPages", "1"))
        # Son sayfadaysak bitti.
        if sayfa >= toplam_sayfa:
            return sonuc
        # Değilsek bir sonraki sayfaya geç.
        sayfa += 1


# WordPress tipi kaynak.
class WordPressKaynagi:
    def __init__(self, ad: str, etiket: str, api_url: str, ortusme_gun: int = 1):
        self.ad = ad
        self.etiket = etiket
        # Sondaki "/" işaretini at ki aşağıda adres birleştirirken "//" olmasın.
        self.api_url = api_url.rstrip("/")
        # Saat dilimi / geç indeksleme kaymalarına karşı checkpoint'ten bu kadar geriye bakılır.
        # Tekrar gelen kayıtlar dis_id ile elenir.
        self.ortusme = timedelta(days=ortusme_gun)

    # Tarama, checkpoint zamanından (1 gün geri payıyla) sonraki yazıları çek, kayıt taslağına çevir.
    def tara(
        self, client: httpx.Client, checkpoint: str | None, bugun: date, simdi: datetime
    ) -> Iterator[TaramaAdimi]:
        # İlk çalıştırmada geçmiş taranmaz, bugünün başından başlanır.
        # Burada checkpoint bir tarih-saat ("2026-10-05T18:06:53"), yoksa bugünün 00:00'ı.
        baslangic = datetime.fromisoformat(checkpoint) if checkpoint else datetime.combine(bugun, datetime.min.time())
        kayitlar = [
            KayitTaslagi(
                # Kimlik olarak WordPress'in yazı numarası.
                dis_id=str(y.id),
                yayin_tarihi=y.yayin.date(),
                baslik=y.baslik,
                url=y.url,
                kaynakca=f"{self.etiket}, {y.yayin:%d.%m.%Y}: {y.baslik}",
                tur="Duyuru",
            )
            for y in yazilar(client, self.api_url, baslangic - self.ortusme)
        ]
        # Checkpoint şu ana ilerler.
        yield TaramaAdimi(kayitlar, checkpoint=simdi.isoformat())

    # Tek yazının metni, API'den o numaralı yazının sadece içeriğini iste, HTML'i düz metne çevir.
    def icerik(self, client: httpx.Client, dis_id: str, url: str) -> Icerik:
        response = client.get(f"{self.api_url}/{dis_id}", params={"_fields": "content"})
        response.raise_for_status()
        return Icerik(html_metni(response.json()["content"]["rendered"]))

    # Denemeler için N gün önceki zaman (burada saat de var).
    def geriye_checkpoint(self, bugun: date, simdi: datetime, gun: int) -> str:
        return (simdi - timedelta(days=gun)).isoformat()

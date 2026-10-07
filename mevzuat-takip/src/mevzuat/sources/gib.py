"""Gelir İdaresi Başkanlığı duyuruları. Site Next.js ile yapılmış, içerik sitenin kendi JSON API'sinden geliyor.
Tarayıcı gerekmez, token istemez.

Örnek bir config/kaynaklar.toml kaydı aşağıda.
    [[kaynak]]
    ad = "gib_mevzuat"
    tip = "gib"
    etiket = "GİB Duyurusu"
    turler = [1]   # sitedeki sekmeler, 1 Mevzuat, 2 Sistem ve Uygulama, 3 Sınav ve Kariyer, 4 Diğer
"""
# GİB'in sitesi sayfayı JavaScript ile dolduruyor, ama arkada kullandığı veri servisini (API) biz de doğrudan çağırıyoruz.
# Böylece tarayıcıya gerek kalmıyor.

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

# TEKRAR_DENENEBILIR, bu isteğin bağlantı koparsa güvenle tekrar gönderilebileceğini işaretleyen etiket (http.py).
from mevzuat.http import TEKRAR_DENENEBILIR
from mevzuat.icerik import Icerik, html_metni
from mevzuat.sources.base import KayitTaslagi, TaramaAdimi

# GİB'in duyuru veri servisinin adresi.
API_URL = "https://gib.gov.tr/api/gibportal/duyuru"
# Duyuru sayfası, her tür bu adresten açılıyor (sitenin ana sayfası da böyle bağlıyor). Maile girmez.
SITE_URL = "https://gib.gov.tr/duyuru-arsivi/guncel"
# API'deki type numarasının sitedeki sekme adı.
TURLER = {1: "Mevzuat", 2: "Sistem / Uygulama", 3: "Sınav / Kariyer", 4: "Diğer"}


# API'den gelen tek duyuru.
@dataclass(frozen=True)
class Duyuru:
    id: int
    baslik: str
    yayin: datetime
    # slug, adresteki okunur parça (ör. "19096_11822_sayili_cumhurbaskani_karari"). İçeriği bununla istiyoruz.
    slug: str


# API cevabındaki listeyi Duyuru nesnelerine çevirir.
def parse_duyurular(veri: dict) -> list[Duyuru]:
    return [
        Duyuru(
            id=d["id"],
            # Başlıktaki fazla boşlukları temizle.
            baslik=" ".join(d["title"].split()),
            yayin=datetime.fromisoformat(d["startdate"]),
            slug=d["slug"],
        )
        # Duyurular cevabın resultContainer içindeki content kısmında duruyor.
        for d in veri["resultContainer"]["content"]
    ]


# Belli bir zamandan sonraki duyuruları, gerektiği kadar sayfa çekerek toplar.
def duyurular(client: httpx.Client, tur: int, sonra: datetime, sayfa_boyu: int = 50) -> list[Duyuru]:
    """`sonra` tarihinden sonra yayımlanan duyurular (yeniden eskiye), gerektiği kadar sayfa."""
    sonuc = []
    # GİB'de sayfalar 0'dan başlıyor.
    sayfa = 0
    while True:
        # GİB listeyi POST isteğiyle veriyor. Sorguda yeniden eskiye sıralama, sayfa numarası ve tür var, tür 1 Mevzuat demek.
        response = client.post(
            f"{API_URL}/listPublish",
            params={"preview": "false", "page": sayfa, "size": sayfa_boyu, "sortFieldName": "startdate", "sortType": "DESC"},
            json={"type": tur, "ilkodu": "UNIVERSAL"},
            extensions={TEKRAR_DENENEBILIR: True},  # POST ama sadece listeleme, tekrar denemek güvenli
        )
        response.raise_for_status()
        veri = response.json()
        parca = parse_duyurular(veri)
        # Bu sayfadakilerden sadece "sonra"dan yeni olanları al.
        sonuc.extend(d for d in parca if d.yayin > sonra)
        # Yeniden eskiye sıralı, sayfada `sonra`dan eski duyuru çıktıysa devamına bakmaya gerek yok.
        # Ya da API "son sayfa" dediyse dur.
        if veri["resultContainer"]["last"] or any(d.yayin <= sonra for d in parca):
            return sonuc
        sayfa += 1


# GİB tipi kaynak.
class GibKaynagi:
    # turler hangi sekmelerin taranacağı. Biz sadece 1'i, yani Mevzuat'ı kullanıyoruz.
    def __init__(self, ad: str, etiket: str, turler: list[int], ortusme_gun: int = 1):
        # Bilmediğimiz bir tür numarası yazıldıysa ya da liste boşsa hata ver.
        bilinmeyen = set(turler) - set(TURLER)
        if not turler or bilinmeyen:
            raise ValueError(f"turler {sorted(TURLER)} içinden seçilmeli, verilen: {turler}")
        self.ad = ad
        self.etiket = etiket
        self.turler = list(turler)
        # Checkpoint'ten bu kadar geriye bakılır, tekrar gelen kayıtlar dis_id ile elenir.
        self.ortusme = timedelta(days=ortusme_gun)

    # Tarama, her seçili tür için checkpoint'ten sonraki duyuruları çek.
    def tara(
        self, client: httpx.Client, checkpoint: str | None, bugun: date, simdi: datetime
    ) -> Iterator[TaramaAdimi]:
        # İlk çalıştırmada geçmiş taranmaz, bugünün başından başlanır.
        # Checkpoint varsa ondan 1 gün öncesi, yoksa bugünün 00:00'ı.
        baslangic = datetime.fromisoformat(checkpoint) - self.ortusme if checkpoint else datetime.combine(bugun, datetime.min.time())
        kayitlar = [
            KayitTaslagi(
                dis_id=str(d.id),
                yayin_tarihi=d.yayin.date(),
                baslik=d.baslik,
                # Duyurunun sitedeki adresi (içerik isterken sonundaki slug'ı kullanıyoruz).
                url=f"{SITE_URL}/{d.slug}",
                kaynakca=f"{self.etiket}, {d.yayin:%d.%m.%Y}: {d.baslik}",
                tur="Duyuru",
            )
            # İki katlı döngü, her tür için, o türün her duyurusu.
            for tur in self.turler
            for d in duyurular(client, tur, baslangic)
        ]
        # Checkpoint şu ana ilerler.
        yield TaramaAdimi(kayitlar, checkpoint=simdi.isoformat())

    # Duyurunun metni, adresin son parçasını (slug) API'ye verip açıklama alanını alıyoruz.
    def icerik(self, client: httpx.Client, dis_id: str, url: str) -> Icerik:
        response = client.get(f"{API_URL}/findBySlug", params={"slug": url.rsplit("/", 1)[-1], "preview": "false"})
        response.raise_for_status()
        return Icerik(html_metni(response.json()["resultContainer"]["description"]))

    # Denemeler için N gün önceki zaman.
    def geriye_checkpoint(self, bugun: date, simdi: datetime, gun: int) -> str:
        return (simdi - timedelta(days=gun)).isoformat()

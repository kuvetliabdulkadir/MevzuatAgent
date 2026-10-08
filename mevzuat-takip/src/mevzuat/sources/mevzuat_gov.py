"""Mevzuat Bilgi Sistemi (mevzuat.gov.tr) yeni eklenen mevzuat. Sitenin arama formunun kullandığı JSON servisi okunur.
Tarayıcı gerekmez. Servis önce ana sayfadan alınan güvenlik anahtarını (antiforgerytoken) ister.

Her türün listesi kelimesiz istenir, site en yeniden eskiye sıralı verir. Son taramadan bu yana Resmî Gazete
tarihi olanlar alınır, kelime eşleşmesini diğer kaynaklar gibi bizim konu filtremiz yapar. Sitenin kendi
araması kelime parçasını da yakalıyor ("altın" araması "altında" getiriyor), bu yüzden onun aramasına güvenilmez.
mevzuat.gov.tr yeni yayımları bir iki gün geç ekliyor, bu yüzden örtüşme birkaç gündür.
Resmî Gazete kaynağının zaten yakaladığı belge rapora ikinci kez girmez (pipeline._ekle).

Örnek bir config/kaynaklar.toml kaydı aşağıda.
    [[kaynak]]
    ad = "mevzuat_gov_yeni"   # "mevzuat_gov" adını güncel metin takibi (surum.py) kullanıyor
    tip = "mevzuat_gov"
    etiket = "Mevzuat Bilgi Sistemi"
    turler = ["Kanun", "CumhurbaskaniKararlari", "Teblig"]
"""
# Sitenin arama sayfası sonuçları JavaScript ile doldurur, ama arkada kullandığı servisi biz de doğrudan çağırıyoruz.

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from mevzuat.http import TEKRAR_DENENEBILIR
from mevzuat.icerik import Icerik, belge_icerigi, html_metni
from mevzuat.sources.base import KayitTaslagi, TaramaAdimi

SITE = "https://www.mevzuat.gov.tr/"
API_URL = "https://www.mevzuat.gov.tr/anasayfa/MevzuatDatatable"
# HTML metni olan mevzuatın metin adresi, surum.py de aynı adresi kullanır.
METIN_URL = "https://www.mevzuat.gov.tr/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={tur}&MevzuatNo={no}&MevzuatTertip={tertip}"
# Sitedeki arama formlarının tür adları ve rapordaki tekil adları.
TURLER = {
    "Kanun": "Kanun",
    "CumhurbaskaniKararnameleri": "Cumhurbaşkanlığı Kararnamesi",
    "CumhurbaskaniKararlari": "Cumhurbaşkanı Kararı",
    "CumhurbaskanligiVeBakanlarKuruluYonetmelik": "Cumhurbaşkanlığı Yönetmeliği",
    "KurumVeKurulusYonetmeligi": "Yönetmelik",
    "Teblig": "Tebliğ",
    "CumhurbaskanligiGenelgeleri": "Cumhurbaşkanlığı Genelgesi",
}
SAYFA_BOYU = 50
# Bir türde en fazla bu kadar sayfa okunur, uzun kapalı kalmada site gereksiz yere taranmasın.
EN_FAZLA_SAYFA = 4
# İstekler arası bekleme, site hızlı art arda isteklere cevap vermeyi kesiyor.
ISTEK_ARASI_BEKLEME = 1.5
# DataTables'ın beklediği sütun tanımı (sitedeki tablo üç sütunlu).
_SUTUNLAR = [{"data": None, "name": "", "searchable": True, "orderable": False,
              "search": {"value": "", "regex": False}} for _ in range(3)]


# Servisten gelen tek mevzuat.
@dataclass(frozen=True)
class Mevzuat:
    ad: str
    rg_tarihi: date
    rg_sayisi: int | None
    url: str  # mevzuatın sitedeki sayfası ya da PDF'i


# Ana sayfadan güvenlik anahtarını alır, servis bunsuz cevap vermez (çerezi client saklar).
def anahtar_al(client: httpx.Client) -> str:
    response = client.get(SITE)
    response.raise_for_status()
    m = re.search(r'name="antiforgerytoken"[^>]*value="([^"]+)"', response.text)
    if m is None:
        raise ValueError("mevzuat.gov.tr: güvenlik anahtarı bulunamadı; site tasarımı değişmiş olabilir")
    return m.group(1)


# "07.10.2026" ya da "19/09/2026" biçimindeki tarihi okur.
def _tarih(metin: str | None) -> date | None:
    m = re.fullmatch(r"(\d{2})[./](\d{2})[./](\d{4})", (metin or "").strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


# Servis cevabındaki listeyi Mevzuat nesnelerine çevirir, tarihi okunamayanı atlar.
def parse_mevzuatlar(veri: dict) -> list[Mevzuat]:
    if "data" not in veri:
        raise ValueError(f"mevzuat.gov.tr: beklenmeyen cevap: {veri.get('Message') or list(veri)}")
    sonuc = []
    for d in veri["data"]:
        tarih = _tarih(d.get("resmiGazeteTarihi"))
        if tarih is None:
            continue
        sayi = (d.get("resmiGazeteSayisi") or "").strip()
        sonuc.append(Mevzuat(
            # Ad bazen vurgulama etiketi ve satır sonu içeriyor.
            ad=" ".join(re.sub(r"<[^>]+>", "", d["mevAdi"]).split()),
            rg_tarihi=tarih,
            rg_sayisi=int(sayi) if sayi.isdigit() else None,
            url=urljoin(SITE, d["url"]),
        ))
    return sonuc


# Bir türün `sonra` tarihinden (dahil) yeni mevzuatını, gerektiği kadar sayfa çekerek toplar.
def mevzuatlar(client: httpx.Client, anahtar: str, tur: str, sonra: date, bugun: date) -> list[Mevzuat]:
    sonuc = []
    for sayfa in range(EN_FAZLA_SAYFA):
        if sayfa:
            time.sleep(ISTEK_ARASI_BEKLEME)
        parametreler = {"MevzuatTur": tur, "YonetmelikMevzuatTur": "OsmanliKanunu", "AranacakIfade": "",
                        "AranacakYer": "2", "KurumId": "0", "BaslangicTarihi": str(sonra.year), "BitisTarihi": str(bugun.year)}
        govde = {"draw": 1, "columns": _SUTUNLAR, "order": [], "start": sayfa * SAYFA_BOYU, "length": SAYFA_BOYU,
                 "search": {"value": "", "regex": False}, "parameters": parametreler}
        response = client.post(
            API_URL, content=json.dumps(govde),
            headers={"Content-Type": "application/json; charset=utf-8", "antiforgerytoken": anahtar},
            extensions={TEKRAR_DENENEBILIR: True},  # POST ama sadece listeleme, tekrar denemek güvenli
        )
        response.raise_for_status()
        veri = response.json()
        parca = parse_mevzuatlar(veri)
        sonuc.extend(m for m in parca if m.rg_tarihi >= sonra)
        # Yeniden eskiye sıralı, sayfada eski tarihli çıktıysa ya da liste bittiyse devamına bakmaya gerek yok.
        if len(veri["data"]) < SAYFA_BOYU or any(m.rg_tarihi < sonra for m in parca):
            break
    return sonuc


# mevzuat.gov.tr tipi kaynak.
class MevzuatGovKaynagi:
    # Resmî Gazete'de zaten yakalanan belge tekrar rapora girmesin (pipeline._ekle).
    resmi_gazete_tekrari = True

    def __init__(self, ad: str, etiket: str = "Mevzuat Bilgi Sistemi", turler: list[str] = tuple(TURLER),
                 ortusme_gun: int = 5):
        bilinmeyen = set(turler) - set(TURLER)
        if not turler or bilinmeyen:
            raise ValueError(f"turler {sorted(TURLER)} içinden seçilmeli, verilen: {turler}")
        self.ad = ad
        self.etiket = etiket
        self.turler = list(turler)
        # Site yeni yayımları geç eklediği için son taramadan bu kadar gün geriye de bakılır, tekrarlar dis_id ile elenir.
        self.ortusme = timedelta(days=ortusme_gun)

    def tara(
        self, client: httpx.Client, checkpoint: str | None, bugun: date, simdi: datetime
    ) -> Iterator[TaramaAdimi]:
        # İlk çalıştırmada da örtüşme kadar geriye bakılır, site birkaç gün geç eklediği için.
        sonra = (date.fromisoformat(checkpoint) if checkpoint else bugun) - self.ortusme
        anahtar = anahtar_al(client)
        kayitlar = []
        for tur in self.turler:
            time.sleep(ISTEK_ARASI_BEKLEME)
            for m in mevzuatlar(client, anahtar, tur, sonra, bugun):
                kayitlar.append(KayitTaslagi(
                    dis_id=m.url,
                    yayin_tarihi=m.rg_tarihi,
                    baslik=m.ad,
                    url=m.url,
                    kaynakca=f"{self.etiket}, Resmî Gazete {m.rg_tarihi:%d.%m.%Y}"
                             + (f", Sayı: {m.rg_sayisi}" if m.rg_sayisi else "") + f", {TURLER[tur]}: {m.ad}",
                    tur=TURLER[tur],
                    sayi=m.rg_sayisi,
                ))
        yield TaramaAdimi(kayitlar, checkpoint=bugun.isoformat())

    # PDF'se doğrudan okunur, değilse sayfanın metni iframe adresinden alınır.
    def icerik(self, client: httpx.Client, dis_id: str, url: str) -> Icerik:
        q = parse_qs(urlparse(url).query)
        if {"MevzuatTur", "MevzuatNo", "MevzuatTertip"} <= q.keys():
            response = client.get(METIN_URL.format(tur=q["MevzuatTur"][0], no=q["MevzuatNo"][0], tertip=q["MevzuatTertip"][0]))
            response.raise_for_status()
            return Icerik(html_metni(response.text))
        response = client.get(url)
        response.raise_for_status()
        return belge_icerigi(response.content, response.headers.get("content-type", ""), response.charset_encoding)

    def geriye_checkpoint(self, bugun: date, simdi: datetime, gun: int) -> str:
        return (bugun - timedelta(days=gun)).isoformat()

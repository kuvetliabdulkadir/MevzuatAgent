"""Sürümlü adresler (/api/v1). Dış sistemlerin (şirket portalı vb.) mevzuatı okuduğu, raporu onayladığı, taramayı,
kaynakları, konuları ve alıcı gruplarını yönettiği adresler. Alanlar değişirse yeni sürüm (/api/v2) açılır, v1 bozulmaz.
Kimlik doğrulama her istekte API anahtarıyla yapılır (Authorization: Bearer).
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mevzuat import rapor as rapor_modulu
from mevzuat.db import KaynakTanimi, Kayit, KonuTanimi, Rapor
from mevzuat.filtre import tr_kucuk

# --- cevap modelleri

class Hata(BaseModel):
    detail: str = Field(description="Hatanın açıklaması", examples=["API anahtarı geçersiz, süresi dolmuş ya da iptal edilmiş."])


class RaporBaglantisi(BaseModel):
    id: int = Field(description="Rapor numarası", examples=[12])
    durum: str = Field(description="ONAY_BEKLIYOR, ONAYLANDI, GONDERILDI ya da REDDEDILDI", examples=["GONDERILDI"])


class MevzuatOzeti(BaseModel):
    id: int = Field(description="Kaydın numarası, detay için /mevzuat/{id}", examples=[1042])
    baslik: str = Field(examples=["Bazı Mallara Uygulanan Özel Tüketim Vergisi Tutarlarının Yeniden Belirlenmesi Hakkında Karar"])
    tur: str = Field(description="Belgenin türü", examples=["Cumhurbaşkanı Kararı"])
    kaynak: str = Field(description="Kaynağın kod adı, /kaynaklar listesindeki ad", examples=["resmi_gazete"])
    kaynak_adi: str = Field(description="Kaynağın görünen adı", examples=["Resmî Gazete"])
    yayin_tarihi: date = Field(examples=["2026-10-01"])
    url: str = Field(description="Belgenin resmî adresi", examples=["https://www.resmigazete.gov.tr/eskiler/2026/10/20261001-3.pdf"])
    kaynakca: str = Field(examples=["Resmî Gazete, 01.10.2026, Sayı: 33387 — Cumhurbaşkanı Kararı (Karar Sayısı: 11822)"])
    ilgili: bool = Field(description="Şirketi ilgilendiren bir konuya uyuyor mu", examples=[True])
    konular: list[str] = Field(description="Uyduğu konular (kategoriler)", examples=[["Vergi"]])
    is_kollari: list[str] = Field(description="Etkilenen iş kolları", examples=[["Ortak", "Oto kiralama"]])
    rapor: RaporBaglantisi | None = Field(description="Girdiği rapor, henüz rapora girmediyse null")


class Degisiklik(BaseModel):
    bolum: str = Field(examples=["Madde 4"])
    tur: str = Field(description="eklendi, kaldırıldı ya da değişti", examples=["değişti"])
    eski: str | None = Field(examples=["… «yüzde 20» oranında …"])
    yeni: str | None = Field(examples=["… «yüzde 25» oranında …"])


class MevzuatDetayi(MevzuatOzeti):
    eslesmeler: dict[str, list[str]] = Field(description="Konu adından o konuda eşleşen kelimelere",
                                              examples=[{"Vergi": ["özel tüketim vergisi"]}])
    ozet: str | None = Field(description="Belgenin asıl hükmü (genelde 1. maddenin ilk cümlesi)",
                             examples=["Kurşunsuz benzin özel tüketim vergisi tutarları yeniden belirlenmiştir."])
    ozet_tablosu: list[str] = Field(description="Özet bir tabloya atıf yapıyorsa tablonun satırları", examples=[[]])
    yururluk: str | None = Field(description="Yürürlük tarihi ya da hükmü", examples=["01.10.2026"])
    one_cikanlar: list[str] = Field(description="Eşleşen kelimenin geçtiği öne çıkan cümleler (en fazla 2)", examples=[[]])
    metin: str | None = Field(description="Belgenin okunmuş metni (taranmış belgelerde OCR, hatalı olabilir)")
    metin_durumu: str = Field(description="TAMAM, OCR_ILE_OKUNDU, OCR_GEREKLI, HATA, BEKLIYOR ya da GEREKSIZ",
                              examples=["TAMAM"])
    degisiklikler: list[Degisiklik] | None = Field(description="Güncel metni değişen mevzuatta madde madde farklar, "
                                                               "diğerlerinde null")


class MevzuatListesi(BaseModel):
    toplam: int = Field(description="Filtreye uyan bütün kayıtların sayısı", examples=[57])
    sayfa: int = Field(examples=[1])
    adet: int = Field(examples=[20])
    kayitlar: list[MevzuatOzeti]


class Konu(BaseModel):
    id: int = Field(description="Konu numarası, düzenleme adreslerinde kullanılır", examples=[1])
    ad: str = Field(examples=["Kıymetli madenler ve kuyumculuk"])
    aciklama: str = Field(description="Bu konu şirketi neden ilgilendiriyor", examples=["Kuyum işletmelerini doğrudan etkiler."])
    is_kollari: list[str] = Field(examples=[["Kuyum", "Döviz/Altın"]])
    kelimeler: list[str] = Field(description="Başlık ve metinde aranan kelimeler", examples=[["altın", "kıymetli maden"]])
    haric: list[str] = Field(description="Kelime geçse de eşleşme sayılmayan ifadeler", examples=[["altında", "altıncı"]])
    dislanan: list[str] = Field(description="Başlıkta geçerse kayıt bu konuya hiç girmez", examples=[[]])
    aktif: bool = Field(description="Pasif konu süzmede kullanılmaz", examples=[True])
    surum: int = Field(description="Düzenlerken gönderilir, kayıt bu arada değiştiyse 409 döner", examples=[2])


class Kaynak(BaseModel):
    ad: str = Field(description="Kod adı, /mevzuat?kaynak= filtresinde ve düzenleme adreslerinde kullanılır",
                    examples=["resmi_gazete"])
    etiket: str = Field(examples=["Resmî Gazete"])
    tip: str = Field(description="Okuma yolu, /kaynak-tipleri listesindeki tip", examples=["resmi_gazete"])
    aktif: bool = Field(examples=[True])
    ayarlar: dict = Field(description="Tipe özel ayarlar (adres, seçiciler), alanları /kaynak-tipleri'nde", examples=[{}])
    varsayilan_konular: list[str] = Field(description="Kaynağın her kaydı bu konulara uymuş sayılır", examples=[[]])
    kaldirildi: bool = Field(description="Kaldırılan kaynak taranmaz, geri getirilebilir", examples=[False])
    surum: int = Field(description="Düzenlerken gönderilir, kayıt bu arada değiştiyse 409 döner", examples=[1])


class RaporOzeti(BaseModel):
    id: int = Field(examples=[12])
    konu: str = Field(examples=["Mevzuat Raporu — 01.10.2026 — 3 kalem (Kuyum, Ortak)"])
    durum: str = Field(description="ONAY_BEKLIYOR, ONAYLANDI, GONDERILDI ya da REDDEDILDI", examples=["GONDERILDI"])
    olusturuldu: datetime = Field(examples=["2026-10-01T07:12:00"])
    karar_zamani: datetime | None = Field(examples=["2026-10-01T09:30:00"])
    gonderildi: datetime | None = Field(examples=["2026-10-01T09:30:05"])
    kalem_sayisi: int = Field(examples=[3])


class RaporListesi(BaseModel):
    toplam: int = Field(examples=[8])
    sayfa: int = Field(examples=[1])
    adet: int = Field(examples=[20])
    raporlar: list[RaporOzeti]


class RaporKalemi(MevzuatOzeti):
    gonderildi: bool = Field(description="Kalem dağıtıma girdi mi (onaylayıcı çıkarmadıysa)", examples=[True])


class RaporDetayi(RaporOzeti):
    karar_notu: str | None = Field(description="Onaylayıcının notu ya da ret sebebi", examples=["Vergi kalemine dikkat."])
    kalemler: list[RaporKalemi]


class Mesaj(BaseModel):
    tur: Literal["basari", "hata"] = Field(description="hata ise işlem kaydedildi ama mail gönderiminde sorun var",
                                           examples=["basari"])
    mesaj: str = Field(examples=["Onaylandı ve 2 adrese gönderildi."])


class Grup(BaseModel):
    id: int = Field(examples=[1])
    ad: str = Field(examples=["Kuyum Ekibi"])
    is_kollari: list[str] = Field(description="Grup bu iş kollarındaki kalemleri alır", examples=[["Kuyum", "Ortak"]])
    adresler: list[str] = Field(examples=[["kuyum@firma.com.tr"]])
    aktif: bool = Field(examples=[True])
    guncellendi: str | None = Field(examples=["2026-10-08T14:18"])


class GrupListesi(BaseModel):
    gruplar: list[Grup]
    is_kollari: list[str] = Field(description="Gruba verilebilecek iş kolları", examples=[["Döviz/Altın", "Kuyum", "Ortak"]])
    kapsanmayan: list[str] = Field(description="Hiçbir aktif grubun almadığı iş kolları, bu kalemler kimseye gitmez",
                                   examples=[["Oto kiralama"]])


class TipAlani(BaseModel):
    ad: str = Field(description="ayarlar içindeki anahtar", examples=["akis_url"])
    etiket: str = Field(examples=["Akış adresi"])
    tur: str = Field(description="url, metin, sayi ya da secim_listesi", examples=["url"])
    aciklama: str
    zorunlu: bool
    varsayilan: Any = None
    secenekler: dict[str, str] | None = Field(None, description="secim_listesi için değerden görünen ada")


class KaynakTipi(BaseModel):
    tip: str = Field(examples=["rss"])
    etiket: str = Field(examples=["RSS / Atom akışı"])
    aciklama: str
    eklenebilir: bool = Field(description="false ise bu tipte yeni kaynak eklenemez, var olan düzenlenir")
    alanlar: list[TipAlani]


class KaynakTipleri(BaseModel):
    tipler: list[KaynakTipi]
    konular: list[str] = Field(description="varsayilan_konular alanına yazılabilecek konu adları")


class DenemeSatiri(BaseModel):
    baslik: str
    tarih: date
    kaynakca: str
    eslesen: dict[str, list[str]] = Field(description="Uyduğu konular ve yakalanan kelimeler, boşsa ilgisiz")
    is_kollari: list[str]


class KaynakDenemesi(BaseModel):
    kayitlar: list[DenemeSatiri] = Field(description="Bulunan duyurular, en fazla 100")
    toplam: int
    kesildi: bool = Field(description="100'den fazla duyuru bulunduysa true")
    gun: int = Field(description="Kaç gün geriye bakıldı", examples=[7])


class Ornek(BaseModel):
    baslik: str
    tarih: date
    url: str


class BulmaAdimi(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    yol: str = Field(examples=["RSS"])
    sonuc: str = Field(description="bulundu ya da yok", examples=["bulundu"])
    not_: str = Field(alias="not", examples=["Sitenin duyuru akışı (RSS/Atom) bulundu."])


class KaynakBulma(BaseModel):
    bulundu: bool
    tip: str | None = Field(None, description="Bulunduysa kaynak eklerken gönderilecek tip", examples=["rss"])
    tip_etiketi: str | None = None
    ayarlar: dict | None = Field(None, description="Bulunduysa kaynak eklerken gönderilecek ayarlar")
    aciklama: str
    onerilen_ad: str = Field(description="Sayfanın başlığı, etiket için öneri")
    ornekler: list[Ornek] = Field(default_factory=list, description="Okunan ilk duyurular")
    icerik_ornegi: str | None = Field(None, description="Duyuru sayfasından okunan metnin başı")
    adimlar: list[BulmaAdimi] = Field(description="Denenen yollar ve sonuçları")


class OnizlemeSatiri(BaseModel):
    baslik: str
    tarih: date
    kaynak: str
    kelimeler: list[str]
    baska_konular: list[str] | None = Field(None, description="Düşecek kayıt başka konulara yine uyuyorsa onlar")


class KonuOnizleme(BaseModel):
    gun: int = Field(examples=[90])
    taranan: int = Field(description="Bakılan başlık sayısı")
    ayni_kalan: int
    eslesecek_sayisi: int
    dusecek_sayisi: int
    eslesecek: list[OnizlemeSatiri] = Field(description="Yeni tanımla eşleşecek, şimdi eşleşmeyenler")
    dusecek: list[OnizlemeSatiri] = Field(description="Şimdi eşleşen, yeni tanımla düşecekler")
    uyarilar: list[str] = Field(description="Kaydı engellemeyen uyarılar")


class Zamanlama(BaseModel):
    saatler: list[str] = Field(description="Planlı tarama saatleri, Türkiye saati", examples=[["06:30", "18:00"]])
    surum: int = Field(examples=[1])
    zamanlayici_calisiyor: bool
    son_nabiz: str | None = Field(examples=["2026-10-08T14:20:00"])
    durum: str | None = Field(description="Zamanlayıcının o an ne yaptığı", examples=["bekliyor"])
    sonraki: str | None = Field(description="Sonraki planlı tarama", examples=["2026-10-08T18:04"])


class CalismaOzeti(BaseModel):
    id: int
    baslangic: str
    bitis: str | None
    durum: str = Field(description="CALISIYOR, BASARILI ya da HATALI", examples=["BASARILI"])
    yeni: dict[str, int | None] = Field(description="Kaynak başına yeni kayıt sayısı", examples=[{"resmi_gazete": 14}])
    yeni_toplam: int
    hata: str | None


class TaramaIstegi(BaseModel):
    id: int
    durum: str = Field(description="BEKLIYOR, CALISIYOR, BITTI ya da HATALI", examples=["BEKLIYOR"])
    istendi: str
    basladi: str | None
    bitti: str | None
    isteyen: str | None
    calisma: CalismaOzeti | None
    rapor_id: int | None = Field(description="Tarama sonunda oluşan rapor, yeni kayıt yoksa null")
    hata: str | None


class TaramaDurumu(BaseModel):
    istek: TaramaIstegi | None = Field(description="En son şimdi tara isteği")
    son_calismalar: list[CalismaOzeti] = Field(description="Son 5 tarama")
    zamanlama: Zamanlama


HATALAR = {
    401: {"model": Hata, "description": "Anahtar yok, geçersiz, süresi dolmuş ya da iptal edilmiş"},
    403: {"model": Hata, "description": "Anahtarın yetkisi yok"},
}
BULUNAMADI = {404: {"model": Hata, "description": "Kayıt yok"}}


# --- çeviriler

def _konular(k: Kayit) -> list[str]:
    return sorted(k.eslesmeler or {})


def _ozet(k: Kayit, etiketler: dict[str, str], raporlar: dict[int, str]) -> dict:
    return {"id": k.id, "baslik": k.baslik, "tur": rapor_modulu.tur_adi(k), "kaynak": k.kaynak,
            "kaynak_adi": etiketler.get(k.kaynak, k.kaynak), "yayin_tarihi": k.yayin_tarihi, "url": k.url,
            "kaynakca": rapor_modulu.kisa_kaynakca(k), "ilgili": k.ilgili, "konular": _konular(k),
            "is_kollari": list(k.is_kollari or []),
            "rapor": {"id": k.rapor_id, "durum": raporlar[k.rapor_id]} if k.rapor_id in raporlar else None}


def _etiketler(db: Session) -> dict[str, str]:
    return {k.ad: k.etiket for k in db.scalars(select(KaynakTanimi))}


def _rapor_durumlari(db: Session, idler: set[int]) -> dict[int, str]:
    if not idler:
        return {}
    return dict(db.execute(select(Rapor.id, Rapor.durum).where(Rapor.id.in_(idler))).all())


def _rapor_ozeti(r: Rapor) -> dict:
    return {"id": r.id, "konu": r.konu, "durum": r.durum, "olusturuldu": r.olusturuldu, "karar_zamani": r.karar_zamani,
            "gonderildi": r.gonderildi, "kalem_sayisi": r.kayit_sayisi}


# --- adresler

def portal_api(oturum: Callable[[], Session], giris_gerekli: Callable, guvenlik_bagimliliklari: list) -> APIRouter:
    """Portal API'sinin adresleri. giris_gerekli panelle aynı kimlik doğrulamadır (API anahtarı ya da oturum)."""
    api = APIRouter(prefix="/api/v1", dependencies=guvenlik_bagimliliklari, responses=HATALAR)

    def kimlik(request: Request) -> None:
        with oturum() as db:
            giris_gerekli(request, db)

    @api.get("/mevzuat", response_model=MevzuatListesi, dependencies=[Depends(kimlik)], tags=["Mevzuat"],
             summary="Mevzuat arama")
    def mevzuat_ara(
        q: str | None = Query(None, max_length=200, description="Başlıkta geçen kelime (büyük/küçük harf fark etmez)",
                              examples=["altın"]),
        kaynak: str | None = Query(None, description="Kaynak kod adı (/kaynaklar)", examples=["resmi_gazete"]),
        konu: str | None = Query(None, description="Konu adı (/konular)", examples=["Vergi"]),
        is_kolu: str | None = Query(None, description="İş kolu", examples=["Kuyum"]),
        baslangic: date | None = Query(None, description="Bu tarihten (dahil) sonra yayımlananlar", examples=["2026-09-01"]),
        bitis: date | None = Query(None, description="Bu tarihe (dahil) kadar yayımlananlar", examples=["2026-10-31"]),
        sadece_ilgili: bool = Query(True, description="false verilirse şirketi ilgilendirmeyenler de gelir"),
        sayfa: int = Query(1, ge=1, description="1'den başlar"),
        adet: int = Query(20, ge=1, le=100, description="Sayfadaki kayıt sayısı, en fazla 100"),
    ):
        """Takip edilen kaynaklarda yayımlanan mevzuatı arar, yeniden eskiye sıralı döner.
        Varsayılan olarak sadece şirketi ilgilendiren (bir konuya uyan) kayıtlar gelir."""
        with oturum() as db:
            sorgu = select(Kayit).order_by(Kayit.yayin_tarihi.desc(), Kayit.id.desc())
            if sadece_ilgili:
                sorgu = sorgu.where(Kayit.ilgili.is_(True))
            if kaynak:
                sorgu = sorgu.where(Kayit.kaynak == kaynak)
            if baslangic:
                sorgu = sorgu.where(Kayit.yayin_tarihi >= baslangic)
            if bitis:
                sorgu = sorgu.where(Kayit.yayin_tarihi <= bitis)
            kayitlar = list(db.scalars(sorgu))
            # Türkçe harfler (İ/ı) veritabanında güvenilir küçültülmediği için başlık, konu ve iş kolu süzmesi burada.
            if q:
                aranan = tr_kucuk(q.strip())
                kayitlar = [k for k in kayitlar if aranan in tr_kucuk(k.baslik)]
            if konu:
                kayitlar = [k for k in kayitlar if konu in (k.eslesmeler or {})]
            if is_kolu:
                kayitlar = [k for k in kayitlar if is_kolu in (k.is_kollari or [])]
            sayfadakiler = kayitlar[(sayfa - 1) * adet: sayfa * adet]
            etiketler = _etiketler(db)
            raporlar = _rapor_durumlari(db, {k.rapor_id for k in sayfadakiler if k.rapor_id})
            return {"toplam": len(kayitlar), "sayfa": sayfa, "adet": adet,
                    "kayitlar": [_ozet(k, etiketler, raporlar) for k in sayfadakiler]}

    @api.get("/mevzuat/{kayit_id}", response_model=MevzuatDetayi, dependencies=[Depends(kimlik)], tags=["Mevzuat"],
             summary="Mevzuat detayı", responses=BULUNAMADI)
    def mevzuat_detay(kayit_id: int):
        """Tek bir kaydın ayrıntısı: özet, yürürlük, öne çıkan cümleler, eşleşen konular ve kelimeler, okunmuş metin.
        Güncel metni değişen mevzuatta madde madde eski ve yeni hali `degisiklikler` alanındadır."""
        with oturum() as db:
            k = db.get(Kayit, kayit_id)
            if k is None:
                raise HTTPException(404, "Kayıt bulunamadı.")
            kalem = rapor_modulu._kalem(1, k)
            raporlar = _rapor_durumlari(db, {k.rapor_id} if k.rapor_id else set())
            return {**_ozet(k, _etiketler(db), raporlar), "eslesmeler": k.eslesmeler or {}, "ozet": kalem.ozet,
                    "ozet_tablosu": kalem.ozet_tablosu, "yururluk": kalem.yururluk, "one_cikanlar": kalem.one_cikanlar,
                    "metin": k.icerik, "metin_durumu": k.icerik_durumu, "degisiklikler": k.degisiklikler}

    @api.get("/konular", response_model=list[Konu], dependencies=[Depends(kimlik)], tags=["Kategoriler"],
             summary="Konular (kategoriler)")
    def konular(pasifler: bool = Query(False, description="true verilirse pasif konular da gelir")):
        """Mevzuatın süzüldüğü konular. Bir kayıt bir ya da birden çok konuya uyar, /mevzuat?konu= ile süzülür."""
        with oturum() as db:
            sorgu = select(KonuTanimi).order_by(KonuTanimi.ad)
            if not pasifler:
                sorgu = sorgu.where(KonuTanimi.aktif)
            return [{"id": k.id, "ad": k.ad, "aciklama": k.aciklama or "", "is_kollari": k.is_kollari,
                     "kelimeler": k.kelimeler, "haric": k.haric, "dislanan": k.dislanan, "aktif": k.aktif,
                     "surum": k.surum} for k in db.scalars(sorgu)]

    @api.get("/kaynaklar", response_model=list[Kaynak], dependencies=[Depends(kimlik)], tags=["Kategoriler"],
             summary="Taranan kaynaklar")
    def kaynaklar(kaldirilanlar: bool = Query(False, description="true verilirse kaldırılan kaynaklar da gelir")):
        """Mevzuatın toplandığı kaynaklar (Resmî Gazete, MASAK, GİB ...). /mevzuat?kaynak= ile süzülür."""
        with oturum() as db:
            sorgu = select(KaynakTanimi).order_by(KaynakTanimi.sira)
            if not kaldirilanlar:
                sorgu = sorgu.where(KaynakTanimi.kaldirildi.is_(False))
            return [{"ad": k.ad, "etiket": k.etiket, "tip": k.tip, "aktif": k.aktif, "ayarlar": dict(k.ayarlar),
                     "varsayilan_konular": list(k.varsayilan_konular), "kaldirildi": k.kaldirildi, "surum": k.surum}
                    for k in db.scalars(sorgu)]

    @api.get("/raporlar", response_model=RaporListesi, dependencies=[Depends(kimlik)], tags=["Raporlar"],
             summary="Raporlar")
    def raporlar(
        durum: Literal["ONAY_BEKLIYOR", "ONAYLANDI", "GONDERILDI", "REDDEDILDI"] | None = Query(
            None, description="Sadece bu durumdakiler"),
        sayfa: int = Query(1, ge=1),
        adet: int = Query(20, ge=1, le=100),
    ):
        """Taramalardan çıkan ve onaya sunulan raporlar, yeniden eskiye sıralı."""
        with oturum() as db:
            sorgu = select(Rapor)
            if durum:
                sorgu = sorgu.where(Rapor.durum == durum)
            toplam = db.scalar(select(func.count()).select_from(sorgu.subquery()))
            sayfadakiler = db.scalars(sorgu.order_by(Rapor.id.desc()).offset((sayfa - 1) * adet).limit(adet))
            return {"toplam": toplam, "sayfa": sayfa, "adet": adet, "raporlar": [_rapor_ozeti(r) for r in sayfadakiler]}

    @api.get("/raporlar/{rapor_id}", response_model=RaporDetayi, dependencies=[Depends(kimlik)], tags=["Raporlar"],
             summary="Rapor detayı", responses=BULUNAMADI)
    def rapor_detay(rapor_id: int):
        """Raporun kalemleri ve kararı. `gonderildi` false olan kalem onaylayıcı tarafından dağıtımdan çıkarılmıştır.
        Alıcıların e-posta adresleri bu API'de yer almaz."""
        with oturum() as db:
            r = db.get(Rapor, rapor_id)
            if r is None:
                raise HTTPException(404, "Rapor bulunamadı.")
            etiketler = _etiketler(db)
            karar_verildi = r.durum in ("ONAYLANDI", "GONDERILDI")
            kalemler = [{**_ozet(k, etiketler, {r.id: r.durum}), "gonderildi": karar_verildi and not k.haric}
                        for k in rapor_modulu.rapor_kayitlari(db, r)]
            return {**_rapor_ozeti(r), "karar_notu": r.karar_notu, "kalemler": kalemler}

    return api


# --- yazma adresleri

# Panelin /api adreslerinden v1'e de açılanlar ve cevaplarının sabit biçimi. İş kuralları (yetki, görev ayrılığı,
# denetim kaydı) panelin fonksiyonlarında tek yerde kalır. Cevap bu modellerden geçer, panelin eklediği alan v1'e
# sızmaz, panelde bir alanın adı değişirse cevap modele uymaz ve testler yakalar.
YAZMA_ADRESLERI: dict[tuple[str, str], type[BaseModel]] = {
    ("POST", "/api/raporlar/{rapor_id}/karar"): Mesaj,
    ("POST", "/api/raporlar/{rapor_id}/ek-gonderim"): Mesaj,
    ("GET", "/api/gruplar"): GrupListesi,
    ("POST", "/api/gruplar"): Grup,
    ("PUT", "/api/gruplar/{grup_id}"): Grup,
    ("GET", "/api/kaynak-tipleri"): KaynakTipleri,
    ("POST", "/api/kaynaklar"): Kaynak,
    ("PUT", "/api/kaynaklar/{ad}"): Kaynak,
    ("POST", "/api/kaynaklar/{ad}/kaldir"): Kaynak,
    ("POST", "/api/kaynaklar/{ad}/geri-getir"): Kaynak,
    ("POST", "/api/kaynaklar/dene"): KaynakDenemesi,
    ("POST", "/api/kaynaklar/bul"): KaynakBulma,
    ("POST", "/api/konular"): Konu,
    ("PUT", "/api/konular/{konu_id}"): Konu,
    ("POST", "/api/konular/{konu_id}/pasif"): Konu,
    ("POST", "/api/konular/{konu_id}/aktif"): Konu,
    ("POST", "/api/konular/onizleme"): KonuOnizleme,
    ("GET", "/api/zamanlama"): Zamanlama,
    ("PUT", "/api/zamanlama"): Zamanlama,
    ("GET", "/api/tarama/durum"): TaramaDurumu,
    ("POST", "/api/tarama"): TaramaDurumu,
}


def yazma_adreslerini_ekle(api: APIRouter, panel_yollari: list, belge: dict, gruplar: dict[str, str]) -> set[tuple[str, str]]:
    """YAZMA_ADRESLERI'ndeki panel adreslerini aynı fonksiyonla /api/v1 altına da ekler. belge panel_belgesi.BELGE,
    gruplar adresin ilk parçasından dokümandaki gruba. Eklenen panel adreslerini döner, bunlar dokümanda gizlenir."""
    eklenen = set()
    for yol in panel_yollari:
        if not isinstance(yol, APIRoute):
            continue
        for yontem in yol.methods:
            model = YAZMA_ADRESLERI.get((yontem, yol.path))
            if model is None:
                continue
            baslik, aciklama, yetki, hatalar = belge[(yontem, yol.path)]
            alt = yol.path.removeprefix("/api")
            api.add_api_route(alt, yol.endpoint, methods=[yontem], response_model=model, summary=baslik,
                              description=f"{aciklama}\n\n**Yetki:** {yetki}".strip(), responses=hatalar,
                              tags=[gruplar[alt.split("/")[1]]])
            eklenen.add((yontem, yol.path))
    if eksik := set(YAZMA_ADRESLERI) - eklenen:
        raise RuntimeError(f"v1'e açılacak panel adresi bulunamadı: {sorted(eksik)}")
    return eklenen

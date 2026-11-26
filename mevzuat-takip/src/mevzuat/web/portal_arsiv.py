"""Dış sistemler için arşiv adresleri (/api/arsiv/...). Sadece okur.

Şirket portalı mevzuat.gov.tr arşivini bunlarla kullanır: liste ve süzgeçler, tam metin arama, mevzuatın künyesi ve sürümleri,
bir sürümün metni, iki sürümün madde madde farkı, son değişiklikler, dosyalar (ekler, PDF'ler) ve eklerin okunmuş metni.
Cevaplar sabit modellerden geçer (panelin ek alanları sızmaz). Kimlik doğrulama /api'nin geri kalanıyla aynıdır
(imzalı API anahtarı, web/imza.py). İş kuralları panelle aynı fonksiyonlarda (arsiv.py, arama.py) tek yerde durur.
"""

from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mevzuat import arama, arsiv
from mevzuat.db import ArsivDosyasi

# --- cevap modelleri


class ArsivKunye(BaseModel):
    anahtar: str = Field(description="Mevzuatın kimliği (tür.tertip.no ya da genelge.<tarih-no>)", examples=["7.5.38527"])
    ad: str = Field(examples=["KUYUM TİCARETİ HAKKINDA YÖNETMELİK"])
    tur: str = Field(description="Sitenin listeleme türü (Kanun, TumYonetmelik, Teblig ...)", examples=["TumYonetmelik"])
    tur_adi: str = Field(examples=["Yönetmelik"])
    url: str = Field(description="Mevzuatın mevzuat.gov.tr'deki adresi")
    rg_tarihi: date | None = Field(description="Resmî Gazete tarihi", examples=["2021-04-14"])
    rg_sayisi: int | None = Field(examples=[31454])
    mulga: bool = Field(description="Sitenin mülga listesinden mi")
    kalkti: str | None = Field(description="Siteden kalktıysa tespit zamanı")
    durum: str = Field(description="TAMAM, BEKLIYOR, OCR_GEREKLI, SITEDE_YOK ya da HATA", examples=["TAMAM"])
    surum_sayisi: int = Field(description="Arşivdeki sürüm sayısı (okuma düzeltmeleri dahil)", examples=[2])
    degisiklik_sayisi: int = Field(0, description="Gerçek değişiklik sürümü sayısı (v1'den beri kaç kez değişti)")
    son_degisiklik: str | None = Field(None, description="Son değişiklik sürümünün alındığı an")
    konular: list[str] = Field(default_factory=list, description="İlgili olduğu konular")


class ArsivListesi(BaseModel):
    toplam: int
    sayfa: int
    adet: int
    kayitlar: list[ArsivKunye]


class AramaParcasi(BaseModel):
    bolum: str = Field(examples=["Madde 6"])
    parca: str = Field(description="Bulunan yerin çevresi. HTML kaçışlıdır, bulunan kelimeler <mark> içindedir")


class AramaSatiri(BaseModel):
    anahtar: str
    ad: str
    tur: str
    rg_tarihi: date | None
    baslikta: bool = Field(description="Aranan başlıkta da geçiyor mu")
    madde_sayisi: int = Field(description="Kaç maddede geçiyor")
    gecis: int = Field(description="Toplam kaç kez geçiyor")
    parcalar: list[AramaParcasi]


class AramaSonucu(BaseModel):
    sorgu: str
    toplam: int
    sayfa: int
    adet: int
    eksik: bool = Field(False, description="Çok fazla sonuç olduğu için sadece ilk kısmı döndü; aramayı daraltın")
    sonuclar: list[AramaSatiri]


class ArsivEki(BaseModel):
    ad: str | None
    sha256: str | None = Field(description="Dosyanın özeti; /arsiv/dosya/{sha256} ile indirilir")
    boyut: int | None
    bicim: str | None = Field(examples=["doc"])
    durum: str = Field(description="indi, sitede_yok, kaynakta_bozuk ya da ulasilamadi")


class ArsivSurumu(BaseModel):
    surum_no: int = Field(examples=[2])
    cekildi: str = Field(description="Siteden alındığı an")
    surum_turu: str = Field(description="ilk, degisiklik (kaynak değişti) ya da okuma_duzeltmesi (sadece okuma iyileşti)")
    okuma: str = Field(description="html, pdf_metin, ocr, karisik ...")
    kalite: str = Field(description="temiz ya da kusurlu")
    kalite_notlari: list[str]
    uzunluk: int = Field(description="Metnin karakter sayısı")
    site_pdf: bool = Field(description="Sitenin PDF'i var mı (/arsiv/mevzuat/{anahtar}/pdf)")
    rg_yeni: list[dict] = Field(description="Bu sürümde ilk kez görülen Resmî Gazete notları [{tarih, sayi}]")
    ekler: list[ArsivEki]


class KonuKaniti(BaseModel):
    konu: str
    kanit: list[dict] = Field(description="Neden ilgili: [{kelime, bolum, tur, parca}]")


class ArsivDetayi(ArsivKunye):
    surumler: list[ArsivSurumu] = Field(description="Yeniden eskiye")
    konu_kanitlari: list[KonuKaniti]


class ArsivMetni(BaseModel):
    anahtar: str
    surum_no: int
    cekildi: str
    okuma: str
    metin: str = Field(description="Sürümün düz metni")


class FarkBolumu(BaseModel):
    bolum: str = Field(examples=["Madde 6"])
    tur: str = Field(description="ayni, degisti, eklendi, kaldirildi ya da mulga")
    eski: str | None
    yeni: str | None
    html: str = Field(description="Kelime düzeyinde işaretli metin: silinen <del>, eklenen <ins> (HTML kaçışlı)")


class ArsivFarki(BaseModel):
    anahtar: str
    eski: int
    yeni: int
    eski_cekildi: str
    yeni_cekildi: str
    yeni_surum_turu: str
    sayac: dict[str, int] = Field(description="degisti, eklendi, kaldirildi, mulga sayıları")
    bolumler: list[FarkBolumu]


class SonDegisiklik(BaseModel):
    anahtar: str
    ad: str
    surum: int
    cekildi: str
    durum: str = Field(description="dogrulandi (yeni RG/AYM notu var) ya da elle_bak")
    yeni_rg_notlari: list[str]
    ozet: str = Field(examples=["Madde 6, Geçici Madde 1 değişti"])
    rapor: str


class SurumSecenegi(BaseModel):
    kod: str = Field(examples=["v2"])
    no: int
    sayi: int


class DonemSecenegi(BaseModel):
    kod: str = Field(examples=["son7"])
    gun: int
    sayi: int


class SurumSecenekleri(BaseModel):
    surumler: list[SurumSecenegi]
    donemler: list[DonemSecenegi]


class EkMetni(BaseModel):
    sha256: str
    ad: str
    bicim: str
    durum: str
    metin: str | None = Field(description="Dosyanın okunmuş metni (Word, Excel, PDF, OCR); okunamadıysa null")


BULUNAMADI = {404: {"description": "Bulunamadı"}}
DOSYA = {200: {"description": "Dosyanın kendisi (orijinal adıyla, değiştirilmeden)",
               "content": {"application/octet-stream": {}}}, **BULUNAMADI}


# --- adresler

def portal_arsiv_api(oturum: Callable[[], Session], kimlik: Callable) -> APIRouter:
    api = APIRouter(prefix="/arsiv", dependencies=[Depends(kimlik)], tags=["Arşiv"])

    @api.get("/mevzuat", response_model=ArsivListesi, summary="Arşiv listesi")
    def liste(
        q: str = Query("", max_length=200, description="Adında ya da anahtarında geçen", examples=["kuyum"]),
        tur: str = Query("", description="Kanun, KHK, Tuzuk, CumhurbaskaniKararnameleri, TumYonetmelik, Teblig, "
                                         "CumhurbaskanligiGenelgeleri, CumhurbaskaniKararlari, MulgaKanun"),
        konu: str = Query("", description="O konuyla ilgililer; * herhangi bir konuyla ilgililer"),
        surum: str = Query("", description="vN: şu anki hali N. sürüm olanlar (v1 hiç değişmemiş); sonG: son G günde "
                                           "değişenler. Seçenekler /arsiv/surumler"),
        durum: str = Query("", description="TAMAM, BEKLIYOR, OCR_GEREKLI, SITEDE_YOK, HATA, KUSURLU, KALKTI"),
        sayfa: int = Query(1, ge=1),
        adet: int = Query(50, ge=1, le=200),
    ):
        """Arşivdeki mevzuat, Resmî Gazete tarihine göre yeniden eskiye (sürüm süzgecinde son değişikliğe göre)."""
        with oturum() as db:
            try:
                r = arsiv.liste(db, q[:200], tur, durum, sayfa, adet, konu=konu[:200], surum=surum)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
        return {"toplam": r["toplam"], "sayfa": r["sayfa"], "adet": r["boyut"], "kayitlar": r["satirlar"]}

    @api.get("/ara", response_model=AramaSonucu, summary="Arşivde tam metin arama")
    def ara(
        q: str = Query(..., min_length=2, max_length=300, examples=['"kıymetli maden"'],
                       description='Kelime ya da "tırnak içinde ifade"; sonuna * yazılırsa başı tutması yeter. Kelimenin '
                                   'ekli halleri de bulunur (altın → altının), başka kelimeler bulunmaz (Altınbaş).'),
        tur: str = Query("", description="Tür (/arsiv/mevzuat ile aynı)"),
        konu: str = Query("", description="Sadece o konuyla ilgililer"),
        sayfa: int = Query(1, ge=1),
    ):
        """Metinlerin son sürümünde madde madde arar; her sonuçta bulunduğu maddeler ve vurgulu parçalar."""
        with oturum() as db:
            r = arama.ara(db, q, tur, konu[:200], sayfa)
        return {**r, "adet": r.get("boyut", 20)}

    @api.get("/surumler", response_model=SurumSecenekleri, summary="Sürüm süzgecinin seçenekleri")
    def surumler():
        """Arşivde var olan sürüm numaraları ve son 7 / 30 / 90 günde değişen mevzuat sayıları (veriden)."""
        with oturum() as db:
            return arsiv.surum_secenekleri(db)

    @api.get("/degisiklikler", response_model=list[SonDegisiklik], summary="Son değişiklikler")
    def degisiklikler(gun: int = Query(7, ge=1, le=365, description="Kaç gün geriye")):
        """Son günlerde arşive giren değişiklik sürümleri, yeniden eskiye: hangi maddeler değişti, yeni Resmî Gazete ya da
        Anayasa Mahkemesi notuyla doğrulandı mı, rapora girdi mi. Okuma düzeltmeleri dahil değildir."""
        with oturum() as db:
            return arsiv.degisim_denetimi(db, gun)

    @api.get("/mevzuat/{anahtar}", response_model=ArsivDetayi, summary="Arşivdeki mevzuat", responses=BULUNAMADI)
    def detay(anahtar: str):
        """Künye, sürümler (yeniden eskiye, metinsiz; ekleriyle) ve ilgili konuların kanıtı."""
        with oturum() as db:
            d = arsiv.detay(db, anahtar)
            if d is None:
                raise HTTPException(404, "Mevzuat arşivde yok.")
            satir = next((s for s in arsiv.liste(db, ara=anahtar, boyut=200)["satirlar"] if s["anahtar"] == anahtar), {})
        return {**d, "degisiklik_sayisi": satir.get("degisiklik_sayisi", 0), "son_degisiklik": satir.get("son_degisiklik"),
                "konular": [k["konu"] for k in d["konular"]], "konu_kanitlari": d["konular"]}

    @api.get("/mevzuat/{anahtar}/metin", response_model=ArsivMetni, summary="Sürümün metni", responses=BULUNAMADI)
    def metin(anahtar: str, surum: int | None = Query(None, ge=1, description="Verilmezse son sürüm")):
        with oturum() as db:
            m = arsiv.metin(db, anahtar, surum)
        if m is None:
            raise HTTPException(404, "Mevzuat ya da sürüm bulunamadı.")
        return m

    @api.get("/mevzuat/{anahtar}/fark", response_model=ArsivFarki, summary="İki sürümün farkı", responses=BULUNAMADI)
    def fark(anahtar: str, eski: int = Query(..., ge=1, examples=[1]), yeni: int = Query(..., ge=1, examples=[2])):
        """Madde madde fark: değişen maddede kelime düzeyinde işaret, eklenen ve kaldırılan maddeler, mülga olanlar.
        Sadece boşluk, tablo ayırıcısı ya da dipnot numarası farkı olan madde "ayni" sayılır."""
        with oturum() as db:
            f = arsiv.fark(db, anahtar, eski, yeni)
        if f is None:
            raise HTTPException(404, "Mevzuat ya da sürüm bulunamadı.")
        return f

    @api.get("/mevzuat/{anahtar}/pdf", summary="Sürümün PDF'i", responses=DOSYA, response_class=FileResponse)
    def pdf(anahtar: str, surum: int | None = Query(None, ge=1, description="Verilmezse son sürüm")):
        """Sitenin PDF'i (asıl ya da sitenin o gün ürettiği); yoksa metnin okunduğu PDF."""
        with oturum() as db:
            p = arsiv.surum_pdf(db, anahtar, surum, "site")
        if p is None or not p[0].is_file():
            raise HTTPException(404, "Bu sürümün PDF'i yok.")
        return FileResponse(p[0], media_type="application/pdf", filename=p[1])

    @api.get("/dosya/{sha256}", summary="Dosya (ek, arşivden çıkan dosya)", responses=DOSYA, response_class=FileResponse)
    def dosya(sha256: str):
        """Arşive kayıtlı bir dosyanın kendisi, özetiyle (sürümün `ekler` listesindeki sha256)."""
        with oturum() as db:
            d = arsiv.dosya_bul(db, sha256)
        if d is None or d[0] is None or not d[0].is_file():
            raise HTTPException(404, "Dosya bulunamadı.")
        return FileResponse(d[0], filename=d[1], content_disposition_type="attachment")

    @api.get("/dosya/{sha256}/metin", response_model=EkMetni, summary="Dosyanın okunmuş metni", responses=BULUNAMADI)
    def dosya_metni(sha256: str):
        with oturum() as db:
            d = db.get(ArsivDosyasi, sha256) if len(sha256) == 64 else None
            if d is None:
                raise HTTPException(404, "Dosya bulunamadı.")
            return {"sha256": d.sha256, "ad": d.ad, "bicim": d.bicim, "durum": d.durum, "metin": d.metin}

    return api

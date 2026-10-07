"""Bir tarama çalışması. Önce güncellik kontrolü yapılır, sonra liste okunur, filtreden geçer, en son sadece ilgililerin içeriği indirilir.

Pipeline kaynakları tanımaz. config/kaynaklar.toml dosyasından gelen her kaynağın `tara` ve `icerik`
metotlarını çağırır. Her kaynak kendi checkpoint'inden devam eder. Checkpoint ancak bir tarama
adımı tamamen yazıldıktan sonra ilerler, yarıda kalan iş bir sonraki çalışmada baştan yapılır.
Kayıtlar kaynak ve dis_id ile tekil olduğu için aynı şeyi iki kez işlemek sonucu bozmaz.
"""
# Pipeline boru hattı demek. Bir taramanın baştan sona bütün adımlarını sırayla çalıştıran ana dosya bu.
# Her kaynağı tarar, yeni kayıtları veritabanına yazar, başlığa göre filtreler ve ilgililerin metnini indirir.

import logging
import time
from datetime import date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat import ocr, surum
from mevzuat.db import Calisma, KaynakDurumu, Kayit
from mevzuat.filtre import TUM_DUYURULAR, Konu, eslesmeler, is_kollari
from mevzuat.sources.base import Kaynak, KayitTaslagi

log = logging.getLogger(__name__)

# İki belge indirmesi arasında 1 saniye bekle (siteleri yormamak için).
ISTEK_ARASI_BEKLEME = 1.0


# Tek bir tarama çalışmasını baştan sona yürütür ve sonucunu (Calisma kaydı) geri verir.
def calistir(
    session: Session,
    client: httpx.Client,
    kaynaklar: list[Kaynak],
    konular: list[Konu],
    bugun: date | None = None,
    simdi: datetime | None = None,
    izlenenler: list[surum.IzlenenTanim] | None = None,
) -> Calisma:
    """izlenenler None ise güncel metin takibi (surum.py) hiç çalışmaz. Boş liste ise sadece otomatik takip çalışır."""
    # Tarih verilmediyse bugünü/şimdiyi kullan (testler kendi tarihini verebiliyor).
    bugun = bugun or date.today()
    simdi = simdi or datetime.now()
    # Ayarlarda yazım hatası var mı, başlamadan kontrol et.
    ayarlari_dogrula(kaynaklar, konular, izlenenler or [])

    # Veritabanına "çalışıyor" durumunda yeni bir çalışma satırı aç ve hemen kaydet.
    calisma = Calisma(baslangic=simdi, durum="CALISIYOR", ozet={})
    session.add(calisma)
    session.commit()

    # Her adımın özeti ve hataları burada toplanacak.
    ozet: dict = {}
    hatalar: list[str] = []
    # Yapılacak adımların listesi, (ad, çalıştırılacak fonksiyon). Önce her kaynak için bir tarama adımı.
    # lambda adımı hemen çalıştırmaz, sonra çalıştırılmak üzere paketler. k=k ise döngüdeki o anki kaynağı sabitler.
    adimlar = [
        (k.ad, lambda k=k: _kaynagi_tara(session, client, k, konular, calisma, bugun, simdi))
        for k in kaynaklar
    ]
    # Güncel metin takibi açıksa (mevzuat.gov.tr) onu da adım olarak ekle.
    if izlenenler is not None:
        adimlar.append((surum.KAYNAK_ADI, lambda: surum.takip_et(session, client, izlenenler, konular, calisma, bugun, simdi)))
    # En son adım, ilgili kayıtların içeriğini indir.
    adimlar.append(("icerik", lambda: _icerik_cek(session, client, {k.ad: k for k in kaynaklar}, konular)))

    # Bir kaynağın hatası diğerlerini durdurmaz.
    for ad, adim in adimlar:
        try:
            # Adımı çalıştır, özetini sakla, veritabanına kaydet.
            ozet[ad] = adim()
            session.commit()
        except Exception as e:
            # Hata olduysa yarım kalan değişiklikleri geri al, logla, hatayı listeye yaz ve sıradaki adıma geç.
            session.rollback()
            log.exception("%s adımı başarısız", ad)
            hatalar.append(f"{ad}: {e!r}")

    # Çalışmayı kapat, bitiş zamanı, durum (hata varsa HATALI), özet ve hatalar.
    calisma.bitis = datetime.now()
    calisma.durum = "HATALI" if hatalar else "BASARILI"
    calisma.ozet = ozet
    calisma.hata = "\n".join(hatalar) or None
    session.commit()
    return calisma


# Kaynakların ve takip listesinin kullandığı konu adları gerçekten tanımlı olmalı. Yazım hatasını baştan yakalar.
def ayarlari_dogrula(kaynaklar: list[Kaynak], konular: list[Konu], izlenenler: list = ()) -> None:
    """Ayarlarda geçen konu adları konular.toml'da tanımlı olmalı (yazım hatası yakalanır)."""
    # Tanımlı konu adları kümesi.
    tanimli = {k.ad for k in konular}
    # Her kaynağın varsayılan konularında tanımsız olan varsa hata ver.
    for kaynak in kaynaklar:
        bilinmeyen = set(getattr(kaynak, "varsayilan_konular", ())) - tanimli
        if bilinmeyen:
            raise ValueError(f"{kaynak.ad!r} kaynağında tanımsız konu: {sorted(bilinmeyen)}")
    # Takip listesindeki konular da tanımlı olmalı.
    for izlenen in izlenenler:
        if izlenen.konu and izlenen.konu not in tanimli:
            raise ValueError(f"izlenen_mevzuat.toml: {izlenen.ad!r} için tanımsız konu: {izlenen.konu!r}")


# Tek bir kaynağı tarar, checkpoint'ten devam eder, yeni kayıtları ekler, checkpoint'i ilerletir.
def _kaynagi_tara(session, client, kaynak: Kaynak, konular, calisma, bugun, simdi) -> dict:
    # Bu kaynağın "en son nerede kaldık" bilgisi (ilk kez taranıyorsa None).
    durum = session.get(KaynakDurumu, kaynak.ad)
    yeni = 0
    bilgi: dict = {}
    # Kaynağın tara fonksiyonu sonuçları adım adım veriyor, her adımı işle.
    for adim in kaynak.tara(client, durum.checkpoint if durum else None, bugun, simdi):
        # Adımdaki her kaydı ekle, yeni olanları say. True burada 1 sayılır.
        for taslak in adim.kayitlar:
            yeni += _ekle(session, kaynak, taslak, konular, calisma)
        # Adım bir checkpoint verdiyse kaydet (ilk kezse yeni satır aç, değilse güncelle).
        if adim.checkpoint is not None:
            if durum is None:
                durum = KaynakDurumu(kaynak=kaynak.ad, checkpoint=adim.checkpoint, guncellendi=datetime.now())
                session.add(durum)
            else:
                durum.checkpoint = adim.checkpoint
                durum.guncellendi = datetime.now()
        session.commit()  # adım adım kalıcı, yarıda kesilirse tamamlanan adımlar kaybolmaz
        # Adımın notlarını (ör. "2026-10-01: yayımlandı") topla.
        bilgi.update(adim.bilgi)
    # Özet, kaç yeni kayıt, varsa gün notları.
    return {"yeni": yeni, **({"gunler": bilgi} if bilgi else {})}


# Bir başlığın hangi konulara uyduğunu bulur. Kaynağın "her kaydı ilgili say" konularını da ekler.
def baslik_eslesmeleri(kaynak: Kaynak, baslik: str, konular: list[Konu]) -> dict[str, list[str]]:
    """Başlık filtresi + kaynağın varsayılan konuları. Tarama ve `dene` komutu aynı kuralı kullanır."""
    eslesen = eslesmeler(baslik, konular)
    # Kaynağın varsayılan konularını, başlıkta kelime geçmese de eşleşmiş say.
    for konu in getattr(kaynak, "varsayilan_konular", ()):
        eslesen.setdefault(konu, [TUM_DUYURULAR])
    return eslesen


# Bir kayıt taslağını veritabanına ekler (daha önce görülmediyse). Yeni eklediyse True döner.
def _ekle(session: Session, kaynak: Kaynak, t: KayitTaslagi, konular: list[Konu], calisma: Calisma) -> bool:
    """Kayıt yeniyse ekler ve True döner. Daha önce görüldüyse dokunmaz."""
    # Bu kaynakta bu kimlikle bir kayıt zaten varsa bul.
    mevcut = session.scalar(select(Kayit.id).where(Kayit.kaynak == kaynak.ad, Kayit.dis_id == t.dis_id))
    if mevcut is not None:
        return False
    # Başlığın hangi konulara uyduğunu bul.
    eslesen = baslik_eslesmeleri(kaynak, t.baslik, konular)
    # Yeni kaydı oluştur ve ekle.
    session.add(
        Kayit(
            kaynak=kaynak.ad,
            dis_id=t.dis_id,
            yayin_tarihi=t.yayin_tarihi,
            baslik=t.baslik,
            tur=t.tur,
            bolum=t.bolum,
            sayi=t.sayi,
            mukerrer=t.mukerrer,
            url=t.url,
            kaynakca=t.kaynakca,
            # En az bir konuya uyduysa ilgili.
            ilgili=bool(eslesen),
            eslesmeler=eslesen,
            is_kollari=is_kollari(eslesen, konular),
            # İlgiliyse içeriği indirilmeyi BEKLIYOR, değilse indirmeye GEREK yok.
            icerik_durumu="BEKLIYOR" if eslesen else "GEREKSIZ",
            ilk_gorulme=datetime.now(),
            calisma_id=calisma.id,
        )
    )
    # Veritabanına gönder (kesinleştirme yukarıda commit ile).
    session.flush()
    return True


# İçeriği henüz indirilmemiş ilgili kayıtların metnini indirir (her çalışmada en fazla 50 tane).
def _icerik_cek(
    session: Session, client: httpx.Client, kaynaklar: dict[str, Kaynak], konular: list[Konu], limit: int = 50
) -> dict:
    """Sadece ilgili kayıtların içeriğini indirir. HATA alanlar bir sonraki çalışmada tekrar denenir.

    İçerik okununca filtre içerik üzerinde de çalışır ve kaydın iş kolları genişletilir. Örneğin başlığı
    "Bazı Mallara Uygulanan ÖTV" olan kararın içinde "benzin" geçerse kayda Oto kiralama da eklenir.
    Başlığa göre ilgisiz bulunan kayıtların içeriği hiç indirilmez, bu adım sadece genişletir.
    """
    # Hangi durumdaki kayıtlar indirilecek, bekleyenler ve geçen sefer hata alanlar.
    durumlar = ["BEKLIYOR", "HATA"]
    if ocr.ocr_kullanilabilir():
        durumlar.append("OCR_GEREKLI")  # OCR sonradan kurulduysa eski kayıtlar da okunur
    # Bu durumlardaki, aktif kaynaklara ait en fazla 50 kaydı getir.
    bekleyenler = session.scalars(
        select(Kayit)
        .where(Kayit.icerik_durumu.in_(durumlar), Kayit.kaynak.in_(list(kaynaklar)))
        .limit(limit)
    ).all()

    # Sonuç sayaçları (özette görünür).
    sayac = {"TAMAM": 0, "OCR_ILE_OKUNDU": 0, "OCR_GEREKLI": 0, "HATA": 0}
    for kayit in bekleyenler:
        try:
            # Kaydın kaynağına "bu belgenin metnini getir" de.
            icerik = kaynaklar[kayit.kaynak].icerik(client, kayit.dis_id, kayit.url)
            kayit.icerik = icerik.metin
            # Duruma karar ver, OCR'la mı okundu, OCR lazım ama yapılamadı mı, yoksa normal mi.
            if icerik.ocr_ile:
                kayit.icerik_durumu = "OCR_ILE_OKUNDU"
            elif icerik.ocr_gerekli:
                kayit.icerik_durumu = "OCR_GEREKLI"
            else:
                kayit.icerik_durumu = "TAMAM"
            # Metinde başka konular da geçiyorsa iş kollarını genişlet.
            _icerikten_genislet(kayit, konular)
        except Exception:
            # İndirilemedi, HATA olarak işaretle, bir sonraki çalışmada tekrar denenir.
            log.exception("İçerik alınamadı: %s", kayit.kaynakca)
            kayit.icerik_durumu = "HATA"
        sayac[kayit.icerik_durumu] += 1
        # Her kayıttan sonra kaydet, sonra biraz bekle.
        session.commit()
        time.sleep(ISTEK_ARASI_BEKLEME)
    return sayac


# Belgenin metninde de filtreyi çalıştırır, bulunan yeni konuları/iş kollarını kayda ekler.
def _icerikten_genislet(kayit: Kayit, konular: list[Konu]) -> None:
    if not kayit.icerik:
        return
    # Var olan eşleşmelerin kopyası.
    eslesen = {konu: list(kelimeler) for konu, kelimeler in kayit.eslesmeler.items()}
    # İçerikte bulunan her konu ve kelime için.
    for konu, kelimeler in eslesmeler(kayit.icerik, konular).items():
        mevcut = eslesen.setdefault(konu, [])
        # Zaten bulunmuş kelimeler (sonlarındaki " (içerikte)" eki olmadan).
        gorulen = {m.removesuffix(" (içerikte)") for m in mevcut}
        # Yeni kelimeleri "(içerikte)" etiketiyle ekle (raporda "neden size geldi" kısmında görünür).
        mevcut += [f"{k} (içerikte)" for k in kelimeler if k not in gorulen]
    # JSON sütunlarında yerinde değişiklik SQLAlchemy'ce fark edilmez, yeni nesne atanmalı.
    kayit.eslesmeler = eslesen
    # İş kollarını yeni eşleşmelere göre yeniden hesapla.
    kayit.is_kollari = is_kollari(eslesen, konular)

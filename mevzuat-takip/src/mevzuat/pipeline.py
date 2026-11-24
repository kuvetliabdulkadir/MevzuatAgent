"""Bir tarama çalışması. Önce güncellik kontrolü yapılır, sonra liste okunur, en son her yeni kaydın içeriği indirilir ve
konu kararı içerikle verilir (arşivle aynı puan, puan.py).

Başlığa göre ilk karar kayıt eklenirken verilir (kaçırmak pahalı: başlıkta kelime geçen kayıt içerik ne derse desin ilgili
kalır). İçerik gelince bütün konular puanlanır, eşiği geçen konular eklenir. İçerik alınamazsa başlık kararı geçerlidir.
Rapor içerik adımı bitmemiş (BEKLIYOR) kaydı almaz, yani karar içerik gelmeden kesinleşmez.

Pipeline kaynakları tanımaz. config/kaynaklar.toml dosyasından gelen her kaynağın `tara` ve `icerik`
metotlarını çağırır. Her kaynak kendi checkpoint'inden devam eder. Checkpoint ancak bir tarama
adımı tamamen yazıldıktan sonra ilerler, yarıda kalan iş bir sonraki çalışmada baştan yapılır.
Kayıtlar kaynak ve dis_id ile tekil olduğu için aynı şeyi iki kez işlemek sonucu bozmaz.
"""
# Pipeline boru hattı demek. Bir taramanın baştan sona bütün adımlarını sırayla çalıştıran ana dosya bu.
# Her kaynağı tarar, yeni kayıtları veritabanına yazar, başlığa göre filtreler ve ilgililerin metnini indirir.

import logging
import re
import time
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat import arama, ocr, puan, surum
from mevzuat.db import Calisma, KaynakDurumu, Kayit
from mevzuat.filtre import TUM_DUYURULAR, Konu, eslesmeler, is_kollari, tr_kucuk
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
    # Başka bir kaynağın zaten yakaladığı belge ikinci kez rapora girmez, kayıt ilgisiz olarak tutulur, içeriği indirilmez.
    tekrar = _baska_kaynakta_var(session, kaynak.ad, t)
    if tekrar:
        eslesen = {}
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
            # Başlık en az bir konuya uyduysa şimdilik ilgili, içerik gelince konular puanla genişler.
            ilgili=bool(eslesen),
            eslesmeler=eslesen,
            is_kollari=is_kollari(eslesen, konular),
            # Her yeni kaydın içeriği indirilir, tekrar olanınki indirilmez.
            icerik_durumu="GEREKSIZ" if tekrar else "BEKLIYOR",
            ilk_gorulme=datetime.now(),
            calisma_id=calisma.id,
        )
    )
    # Veritabanına gönder (kesinleştirme yukarıda commit ile).
    session.flush()
    return True


# Başlığı karşılaştırmak için sadece harf ve rakamları bırakır, küçük harfe çevirir. Numara etiketleri ("Tebliğ No:",
# "Sıra No:", "Sayı:") ve duyuru kuyruğu ("Resmî Gazete'de yayımlandı") atılır, numaranın kendisi kalır.
_NO_ETIKETI = re.compile(r"\b(?:tebliğ|sıra|seri|karar)?\s*(?:no|sayı|sayısı|numarası)\s*[:.]", re.IGNORECASE)
_DUYURU_KUYRUGU = re.compile(r"\s*(?:(?:resmi|resmî) gazete[’'`]?de\s*)?yayımlan(?:dı|mıştır)\W*$")
TEKRAR_GUN = 3
TEKRAR_EN_KISA = 20  # bundan kısa sade başlık ("Duyuru") karşılaştırılmaz


def _sade_baslik(baslik: str) -> str:
    kucuk = _DUYURU_KUYRUGU.sub("", tr_kucuk(baslik).strip())
    return re.sub(r"[^0-9a-zçğıöşü]", "", _NO_ETIKETI.sub(" ", kucuk))


# Aynı belge ±3 gün içinde başka bir kaynaktan gelmiş mi (mevzuat.gov.tr Resmî Gazete'deki belgeyi birkaç gün sonra
# ekliyor, GİB "… Resmî Gazete'de yayımlandı" duyurusu yapıyor). Kaynaktan bağımsız, sadece başlık ve tarih.
def _baska_kaynakta_var(session: Session, kaynak_adi: str, t: KayitTaslagi) -> bool:
    aranan = _sade_baslik(t.baslik)
    if len(aranan) < TEKRAR_EN_KISA or t.yayin_tarihi is None:
        return False
    sorgu = select(Kayit.baslik).where(
        Kayit.kaynak != kaynak_adi,
        Kayit.yayin_tarihi.between(t.yayin_tarihi - timedelta(days=TEKRAR_GUN), t.yayin_tarihi + timedelta(days=TEKRAR_GUN)))
    for b in session.scalars(sorgu):
        sade = _sade_baslik(b)
        # Başlıklar bazen sonda farklılaşıyor (karar sayısı yazımı gibi), ikisi de uzunsa ilk 80 harf yeterli.
        if sade == aranan or (min(len(sade), len(aranan)) >= 80 and sade[:80] == aranan[:80]):
            return True
    return False


# İçeriği henüz indirilmemiş kayıtların metnini indirir ve konuları içerikle puanlar (her çalışmada en fazla 100 tane).
def _icerik_cek(
    session: Session, client: httpx.Client, kaynaklar: dict[str, Kaynak], konular: list[Konu], limit: int = 100
) -> dict:
    """Bekleyen kayıtların içeriğini indirir. HATA alanlar bir sonraki çalışmada tekrar denenir.

    İçerik okununca bütün konular arşivle aynı puanla hesaplanır (puan.py), eşiği geçen konular kayda eklenir.
    Örneğin başlığında konu kelimesi olmayan bir tebliğin amaç maddesi ve birçok maddesi altın ticaretini düzenliyorsa
    Kuyum'a gider; metinde bir kez "altın" geçmesi ise yetmez. Başlığa göre ilgili olan kayıt ilgili kalır.
    """
    # Hangi durumdaki kayıtlar indirilecek, bekleyenler ve geçen sefer hata alanlar.
    durumlar = ["BEKLIYOR", "HATA"]
    if ocr.ocr_kullanilabilir():
        durumlar.append("OCR_GEREKLI")  # OCR sonradan kurulduysa eski kayıtlar da okunur
    # Bu durumlardaki, aktif kaynaklara ait kayıtlar, başlığa göre ilgili olanlar önce (rapor onları bekliyor).
    bekleyenler = session.scalars(
        select(Kayit)
        .where(Kayit.icerik_durumu.in_(durumlar), Kayit.kaynak.in_(list(kaynaklar)))
        .order_by(Kayit.ilgili.desc(), Kayit.id)
        .limit(limit)
    ).all()
    agirlik = arama.kelime_agirliklari(session)

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
            # Konuları içerikle puanla, eşiği geçenleri ekle.
            icerikle_puanla(kayit, konular, agirlik)
        except Exception:
            # İndirilemedi, HATA olarak işaretle, bir sonraki çalışmada tekrar denenir.
            log.exception("İçerik alınamadı: %s", kayit.kaynakca)
            kayit.icerik_durumu = "HATA"
        sayac[kayit.icerik_durumu] += 1
        # Her kayıttan sonra kaydet, sonra biraz bekle.
        session.commit()
        time.sleep(ISTEK_ARASI_BEKLEME)
    return sayac


def icerikle_eslesmeler(baslik: str, icerik: str, konular: list[Konu], onceki: dict[str, list[str]] | None = None,
                        agirlik: dict[str, float] | None = None, ek: str = "(içerikte)",
                        belge: puan.Belge | None = None) -> dict[str, list[str]]:
    """Başlık eşleşmelerine (onceki) içerik puanıyla ilgili çıkan konuları ekler. Kelimeler kanıttan gelir ve `ek` ile
    işaretlenir (raporda "neden size geldi" cümleleri bunlarla bulunur). Önceki konular hiç düşmez. Aynı metin birçok
    kez puanlanacaksa hazırlanmış `belge` verilebilir."""
    eslesen = {konu: list(kelimeler) for konu, kelimeler in (onceki or {}).items()}
    if not icerik:
        return eslesen
    belge = belge or puan.belge_hazirla(baslik, icerik)
    for konu in konular:
        p = puan.puanla_hazir(belge, konu, agirlik=agirlik)
        if not p.ilgili:
            continue
        mevcut = eslesen.setdefault(konu.ad, [])
        # Zaten bulunmuş kelimeler (sonlarındaki ek olmadan).
        gorulen = {m.removesuffix(f" {ek}") for m in mevcut}
        for x in p.kanitlar:
            if x.tur != "baslik" and x.kelime not in gorulen:
                gorulen.add(x.kelime)
                mevcut.append(f"{x.kelime} {ek}")
    return eslesen


# İçerik gelince konuları puanlar, kaydın eşleşmelerini, ilgili alanını ve iş kollarını günceller.
def icerikle_puanla(kayit: Kayit, konular: list[Konu], agirlik: dict[str, float] | None = None) -> None:
    if not kayit.icerik:
        return
    # JSON sütunlarında yerinde değişiklik SQLAlchemy'ce fark edilmez, yeni nesne atanıyor.
    kayit.eslesmeler = icerikle_eslesmeler(kayit.baslik, kayit.icerik, konular, kayit.eslesmeler, agirlik)
    kayit.ilgili = bool(kayit.eslesmeler)
    kayit.is_kollari = is_kollari(kayit.eslesmeler, konular)

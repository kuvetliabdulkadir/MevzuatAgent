"""Tarama zamanlaması. Saatler veritabanında durur ve panelden değişir, taramayı tek bir zamanlayıcı süreci başlatır.

systemd timer kullanılmadı, çünkü saati panelden değiştirmek uygulamaya root yetkisi vermek olurdu. Docker'da
`cli zamanlayici` sürekli çalışır.

Zamanlayıcı 30 saniyede bir uyanır ve şunları yapar.
  1. Önceki tarama bittiyse sonucunu log'a yazar.
  2. Saatleri DB'den okur, değiştiyse sonraki hedefi yeniden hesaplar.
  3. Vakti geldiyse günlük işi ayrı bir süreç olarak başlatır, böylece iş çökse ya da bellek şişse de zamanlayıcı ayakta kalır.
  4. Panelden "Şimdi tara" isteği bekliyorsa ve tarama sürmüyorsa onu başlatır, bitince sonucu isteğe yazar.
  5. Nabız yazar, panel bununla zamanlayıcının çalışıp çalışmadığını ve sonraki taramanın ne zaman olduğunu görür, tarama sürerken de.
Planlı çalışmalara 0 ile 10 dakika arasında rastgele gecikme eklenir, devlet sitelerine tam dakikasında yüklenmemek için.

Kaçan çalışma, systemd'deki Persistent ayarının karşılığı. Zamanlayıcı açıldığında son planlı saatten beri hiç çalışma
yoksa ve aradan 12 saatten az geçtiyse hemen bir kez çalışır. Veri zaten kaybolmaz, tarama checkpoint'ten devam eder.
Aynı anda iki tarama olmaz. Günlük iş dosya kilidiyle korunur, planlı taramaları da artık tek süreç başlattığı için çakışma olmaz.

Saatler Türkiye saatidir, uygulama saat dilimini kendisi sabitler (cli.saat_dilimini_sabitle fonksiyonuna bakın).
"""
# "Saat kaçta tarama yapılacak" işini yöneten dosya. Zamanlayıcı hiç kapanmayan bir döngü.
# 30 saniyede bir uyanır, vakit geldiyse taramayı başlatır, panelden "Şimdi tara" denmişse onu başlatır.

import logging
import os
# random, planlı taramaya 0-10 dk arası rastgele gecikme eklemek için.
import random
# subprocess, taramayı ayrı bir program (süreç) olarak başlatmak için.
import subprocess
# sys.executable, şu an çalışan Python'un yolu (aynı Python ile taramayı başlatmak için).
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
# datetime içindeki "time" sınıfını (sadece saat, 06:30) Saat adıyla kullanıyoruz, time modülüyle karışmasın.
from datetime import time as Saat

from sqlalchemy import func, select, update
# IntegrityError, veritabanı kuralı ihlal edilince (ör. aynı anda iki aktif istek) gelen hata.
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from mevzuat.db import Calisma, Kullanici, SistemAyari, TaramaIstegi

log = logging.getLogger("mevzuat.zamanlayici")

# sistem_ayarlari tablosundaki anahtar adları.
SAATLER = "calisma_saatleri"
NABIZ = "zamanlayici_nabiz"
# Hiç ayar yoksa kullanılacak saatler.
VARSAYILAN_SAATLER = "06:30,18:00"
ARALIK = 30  # sn, uyanma sıklığı (nabız, saat değişikliği, manuel istek)
EN_FAZLA_GECIKME = 10 * 60  # sn, planlı çalışmaya rastgele gecikme
# Zamanlayıcı kapalıyken kaçırılan tarama, en fazla 12 saat geriden telafi edilir.
KACAN_PENCERE = timedelta(hours=12)
NABIZ_ESIGI = timedelta(minutes=2)  # nabız bundan eskiyse panel "zamanlayıcı çalışmıyor" der
# Panelden en az 1, en fazla 6 tarama saati girilebilir.
EN_AZ_SAAT, EN_FAZLA_SAAT = 1, 6
# İki tarama saati arasında en az 1 saat olmalı.
EN_AZ_ARA = timedelta(hours=1)


# ---- saatler --------------------------------------------------------------------------------------------

# Saat listesini yazı listesine çevirir, örneğin 06:30 ve 18:00.
def saat_metni(saatler: list[Saat]) -> list[str]:
    return [s.strftime("%H:%M") for s in saatler]


# "06:30,18:00" yazısını saat listesine çevirir (sıralı, tekrarsız).
def calisma_saatleri(metin: str) -> list[Saat]:
    """"06:30,18:00" gibi bir yazıyı saat listesine çevirir. Ortam değişkeni için, gevşek okur. Hatalı yazım zamanlayıcı başlarken hata verir."""
    try:
        return sorted({datetime.strptime(s.strip(), "%H:%M").time() for s in metin.split(",") if s.strip()})
    except ValueError as e:
        raise SystemExit(f"MEVZUAT_CALISMA_SAATLERI hatalı ({metin!r}); örnek: 06:30,18:00") from e


# Panelden girilen saatleri sıkı kurallarla kontrol eder.
def saatleri_dogrula(liste: list[str]) -> list[Saat]:
    """Panelden gelen saatleri doğrular. 1 ile 6 arasında saat olmalı, SS:DD biçiminde yazılmalı ve aralarında, gece yarısını geçerken de, en az 1 saat olmalı."""
    saatler = []
    # Her birini SS:DD olarak okumayı dene.
    for s in liste:
        try:
            saatler.append(datetime.strptime(str(s).strip(), "%H:%M").time())
        except ValueError:
            raise ValueError(f"Saat SS:DD biçiminde olmalı: {s!r}") from None
    # Tekrarları at, sırala.
    saatler = sorted(set(saatler))
    # Sayı 1 ile 6 arasında olmalı.
    if not EN_AZ_SAAT <= len(saatler) <= EN_FAZLA_SAAT:
        raise ValueError(f"En az {EN_AZ_SAAT}, en fazla {EN_FAZLA_SAAT} tarama saati olmalı.")
    # Saatleri gece yarısından itibaren dakikaya çevir, örneğin 06:30 390 dakika olur.
    dakikalar = [s.hour * 60 + s.minute for s in saatler]
    # Ardışık her iki saatin farkına bak, son saat ile ertesi günün ilk saati arası da dahil (+24 saat).
    for once, sonra in zip(dakikalar, [*dakikalar[1:], dakikalar[0] + 24 * 60]):
        if len(dakikalar) > 1 and sonra - once < EN_AZ_ARA.total_seconds() / 60:
            raise ValueError("Tarama saatleri arasında en az 1 saat olmalı (taramalar üst üste binmesin).")
    return saatler


# Saatleri veritabanından okur. Hiç kayıt yoksa .env'deki ya da varsayılan saatleri yazar ve onları verir.
def saatleri_oku(session: Session) -> list[Saat]:
    """DB'deki saatleri okur. İlk seferde MEVZUAT_CALISMA_SAATLERI değişkeninden, o da yoksa 06:30 ve 18:00 olarak aktarılır ve commit edilir."""
    ayar = session.get(SistemAyari, SAATLER)
    if ayar is None:
        saatler = calisma_saatleri(os.environ.get("MEVZUAT_CALISMA_SAATLERI", VARSAYILAN_SAATLER))
        session.add(SistemAyari(anahtar=SAATLER, deger=saat_metni(saatler), surum=1, guncelleme=datetime.now()))
        session.commit()
        return saatler
    return calisma_saatleri(",".join(ayar.deger))


# Panelden gelen yeni saatleri kaydeder. Eski ve yeni hali döner (denetim kaydına yazmak için).
def saatleri_kaydet(session: Session, liste: list[str], surum: int, kullanici_id: int) -> tuple[list[str], list[str]]:
    """Panelden gelen saatleri kaydeder, commit etmez. Eski ve yeni saatleri döner. Form açıldıktan sonra saatler değiştiyse ValueError değil CakismaHatasi verir."""
    # Önce kontrol et.
    yeni = saat_metni(saatleri_dogrula(liste))
    # Ayar satırı yoksa oluşsun diye bir kez oku.
    saatleri_oku(session)
    ayar = session.get(SistemAyari, SAATLER)
    # Kullanıcı formu açtıktan sonra başka biri saatleri değiştirdiyse (sürüm numarası tutmuyor) üstüne yazma.
    if ayar.surum != surum:
        raise CakismaHatasi("Siz düzenlerken tarama saatleri değiştirildi. Sayfayı yenileyip tekrar deneyin.")
    eski = list(ayar.deger)
    # Yeni değeri yaz, sürümü bir artır, kim ne zaman değiştirdi bilgisini güncelle.
    ayar.deger, ayar.surum, ayar.guncelleme, ayar.guncelleyen_id = yeni, ayar.surum + 1, datetime.now(), kullanici_id
    return eski, yeni


# "Başkası senden önce değiştirdi" durumu için özel hata (panel 409 döner).
class CakismaHatasi(Exception):
    pass


# Şu andan sonraki ilk tarama zamanı (bugün kalmadıysa yarının ilk saati).
def sonraki_calisma(simdi: datetime, saatler: list[Saat]) -> datetime:
    """`simdi`den sonraki ilk planlı saat (bugün kalmadıysa yarının ilk saati)."""
    # Bugünün ve yarının bütün saatlerini aday yap, şimdiden sonraki en erkenini seç.
    adaylar = [datetime.combine(simdi.date() + timedelta(days=g), s) for g in (0, 1) for s in saatler]
    return min(a for a in adaylar if a > simdi)


# Şu ana kadarki en son planlı tarama zamanı.
def son_planli(simdi: datetime, saatler: list[Saat]) -> datetime:
    """`simdi`ye kadar (dahil) en son planlı saat."""
    # Bugünün ve dünün saatlerinden, şimdiye kadar geçmiş olanların en geçi.
    adaylar = [datetime.combine(simdi.date() - timedelta(days=g), s) for g in (0, 1) for s in saatler]
    return max(a for a in adaylar if a <= simdi)


# Zamanlayıcı kapalıyken bir tarama kaçırılmış mı diye bakar. Son planlı saat 12 saatten yeniyse ve o saatten sonra çalışma yoksa kaçırılmıştır.
def kacan_calisma_var_mi(simdi: datetime, saatler: list[Saat], son_calisma: datetime | None) -> bool:
    planli = son_planli(simdi, saatler)
    return simdi - planli < KACAN_PENCERE and (son_calisma is None or son_calisma < planli)


# ---- nabız ----------------------------------------------------------------------------------------------

# Zamanlayıcı "hayattayım" kaydı yazar, ne zaman, ne yapıyor, sonraki tarama ne zaman. Panel bunu okur.
def nabiz_yaz(session: Session, simdi: datetime, durum: str, sonraki: datetime | None, saatler: list[Saat]) -> None:
    deger = {"zaman": simdi.isoformat(timespec="seconds"), "durum": durum,
             "sonraki": sonraki.isoformat(timespec="minutes") if sonraki else None, "saatler": saat_metni(saatler)}
    ayar = session.get(SistemAyari, NABIZ)
    # İlk kezse satır oluştur, değilse güncelle.
    if ayar is None:
        session.add(SistemAyari(anahtar=NABIZ, deger=deger, surum=1, guncelleme=simdi))
    else:
        ayar.deger, ayar.guncelleme = deger, simdi
    session.commit()


# Panelin Tarama sayfası için zamanlayıcının durumunu hazırlar.
def zamanlayici_durumu(session: Session, simdi: datetime | None = None) -> dict:
    """Panel için durum bilgisi. Saatler, zamanlayıcının çalışıp çalışmadığı (nabız en fazla 2 dakika önce yazılmışsa çalışıyor sayılır), ne yaptığı ve sonraki tarama."""
    simdi = simdi or datetime.now()
    saatler = saatleri_oku(session)
    ayar = session.get(SistemAyari, SAATLER)
    nabiz = session.get(SistemAyari, NABIZ)
    # Son nabız zamanı.
    son = datetime.fromisoformat(nabiz.deger["zaman"]) if nabiz else None
    # Son nabız 2 dakikadan yeniyse zamanlayıcı çalışıyor sayılır.
    calisiyor = son is not None and simdi - son <= NABIZ_ESIGI
    return {
        "saatler": saat_metni(saatler), "surum": ayar.surum,
        "zamanlayici_calisiyor": calisiyor, "son_nabiz": son.isoformat(timespec="seconds") if son else None,
        "durum": nabiz.deger.get("durum") if calisiyor else None,
        # Zamanlayıcı çalışıyorsa onun hesapladığı (rastgele gecikme dahil), değilse saatlerden.
        "sonraki": (nabiz.deger.get("sonraki") if calisiyor and nabiz.deger.get("sonraki")
                    else sonraki_calisma(simdi, saatler).isoformat(timespec="minutes")),
    }


# ---- panelden "Şimdi tara" ----------------------------------------------------------------------------

# "Aktif" sayılan istek durumları (sırada bekleyen ya da çalışan).
AKTIF_ISTEK = ("BEKLIYOR", "CALISIYOR")
ISTEK_DEGISKENI = "MEVZUAT_TARAMA_ISTEGI"  # günlük iş hangi istek için çalıştığını buradan bilir


# Panelde "Şimdi tara"ya basılınca çağrılır, sadece veritabanına bir istek yazar, taramayı zamanlayıcı başlatır.
def istek_olustur(session: Session, kullanici_id: int, simdi: datetime | None = None) -> TaramaIstegi:
    """Panel sadece istek yazar (commit etmez). Zamanlayıcı çalışmıyorsa, sırada/çalışan istek ya da süren planlı
    tarama varsa ValueError (panel 409 döner)."""
    simdi = simdi or datetime.now()
    durum = zamanlayici_durumu(session, simdi)
    # Zamanlayıcı çalışmıyorsa istek hiç işlenmez, baştan reddet.
    if not durum["zamanlayici_calisiyor"]:
        raise ValueError("Zamanlayıcı çalışmıyor; tarama başlatılamaz. Sunucu yöneticisine bildirin.")
    # Zaten sırada/çalışan bir istek varsa reddet.
    if session.scalar(select(TaramaIstegi).where(TaramaIstegi.durum.in_(AKTIF_ISTEK)).limit(1)):
        raise ValueError("Bir tarama isteği zaten sırada ya da çalışıyor.")
    # Planlı tarama şu an sürüyorsa reddet.
    if durum["durum"] == "tarama":
        raise ValueError("Şu an planlı tarama sürüyor; bitince sonuç Onay Kuyruğu'nda görünür.")
    # Yeni isteği "BEKLIYOR" olarak ekle.
    istek = TaramaIstegi(isteyen_id=kullanici_id, istendi=simdi, durum="BEKLIYOR")
    session.add(istek)
    try:
        session.flush()
    except IntegrityError:
        # Yukarıdaki kontrolle bu satır arasında başka biri de istek açtı (aynı anda tıklama), DB kuralı yakaladı.
        raise ValueError("Bir tarama isteği zaten sırada ya da çalışıyor.") from None
    return istek


# İstek satırına bilgi yazar (çalışma numarası, rapor numarası, durum...).
def istege_yaz(session: Session, istek_id: int, **alanlar) -> None:
    """Günlük iş, panelden istendiyse kendi çalışma kaydını ve raporunu isteğe kendisi yazar (commit eder).
    Böylece aynı anda elle başlatılmış başka bir taramanın sonucu yanlışlıkla bu isteğe bağlanamaz."""
    session.execute(update(TaramaIstegi).where(TaramaIstegi.id == istek_id).values(**alanlar))
    session.commit()


# Tarama süreci bitince isteğin son durumunu belirler, BITTI ya da HATALI.
def istegi_sonuclandir(session: Session, istek: TaramaIstegi, cikis_kodu: int, simdi: datetime) -> None:
    """Günlük iş bitti. Çalışma ve rapor bağlantısını günlük iş zaten yazdı (istege_yaz), burada sadece durum belirlenir."""
    calisma = session.get(Calisma, istek.calisma_id) if istek.calisma_id else None
    istek.bitti = simdi
    # Çalışma kaydı hiç oluşmadıysa tarama başlayamamış.
    if calisma is None:
        istek.durum = "HATALI"
        istek.hata = (f"Tarama başlamadı (çıkış kodu {cikis_kodu}): başka bir tarama sürüyor olabilir ya da "
                      "günlük iş açılırken hata verdi; zamanlayıcı loguna bakın.")
    # Çalışma hatalı bittiyse hatasını isteğe kopyala (en fazla 2000 karakter).
    elif calisma.durum != "BASARILI":
        istek.durum = "HATALI"
        istek.hata = (calisma.hata or f"Çalışma durumu: {calisma.durum}")[:2000]
    else:
        istek.durum = "BITTI"
    session.commit()


# Bir çalışmayı panelin istediği biçime çevirir, kaynak başına yeni kayıt sayısı vb.
def _calisma_json(c: Calisma) -> dict:
    # Özetteki her kaynağın "yeni" sayısı.
    yeni = {ad: v.get("yeni") for ad, v in (c.ozet or {}).items() if isinstance(v, dict) and "yeni" in v}
    return {"id": c.id, "baslangic": c.baslangic.isoformat(timespec="minutes"),
            "bitis": c.bitis.isoformat(timespec="minutes") if c.bitis else None, "durum": c.durum,
            "yeni": yeni, "yeni_toplam": sum(v or 0 for v in yeni.values()), "hata": c.hata}


# Panelin Tarama sayfasının bütün verisi, son istek, son 5 çalışma, zamanlama.
def tarama_durumu(session: Session, simdi: datetime | None = None) -> dict:
    """Panel için son istek, son çalışmalar (kaynak başına yeni kayıt sayısı ve hata) ve zamanlama bilgisi."""
    # En son "Şimdi tara" isteği.
    istek = session.scalar(select(TaramaIstegi).order_by(TaramaIstegi.id.desc()).limit(1))
    istek_json = None
    if istek is not None:
        isteyen = session.get(Kullanici, istek.isteyen_id) if istek.isteyen_id else None
        calisma = session.get(Calisma, istek.calisma_id) if istek.calisma_id else None
        istek_json = {
            "id": istek.id, "durum": istek.durum, "istendi": istek.istendi.isoformat(timespec="seconds"),
            "basladi": istek.basladi.isoformat(timespec="seconds") if istek.basladi else None,
            "bitti": istek.bitti.isoformat(timespec="seconds") if istek.bitti else None,
            "isteyen": isteyen.ad if isteyen else None, "calisma": _calisma_json(calisma) if calisma else None,
            "rapor_id": istek.rapor_id, "hata": istek.hata,
        }
    return {
        "istek": istek_json,
        "son_calismalar": [_calisma_json(c) for c in session.scalars(select(Calisma).order_by(Calisma.id.desc()).limit(5))],
        "zamanlama": zamanlayici_durumu(session, simdi),
    }


# ---- zamanlayıcı döngüsü --------------------------------------------------------------------------------

# Günlük işi ayrı bir Python süreci olarak başlatır ("python -m mevzuat.cli gunluk").
def gunluk_baslat(istek_id: int | None) -> subprocess.Popen:
    """Günlük işi ayrı bir süreç olarak başlatır. Panelden istendiyse istek numarası ortam değişkeniyle geçer."""
    # Mevcut ortam değişkenlerini kopyala (eski istek numarası kalmışsa çıkar).
    ortam = {k: v for k, v in os.environ.items() if k != ISTEK_DEGISKENI}
    # Panelden istendiyse istek numarasını ver.
    if istek_id is not None:
        ortam[ISTEK_DEGISKENI] = str(istek_id)
    # Popen, süreci başlat ama bitmesini bekleme (zamanlayıcı çalışmaya devam etsin).
    return subprocess.Popen([sys.executable, "-m", "mevzuat.cli", "gunluk"], env=ortam)


# Zamanlayıcının kendisi.
class Zamanlayici:
    """Test edilebilsin diye saat, rastgelelik ve süreç başlatma dışarıdan verilebilir. `adim` tek bir uyanışı çalıştırır."""

    def __init__(
        self,
        engine: Engine,
        # Taramayı başlatan fonksiyon (testte sahte olabilir).
        baslat: Callable[[int | None], subprocess.Popen] = gunluk_baslat,
        # Şimdiki zamanı veren fonksiyon (testte sahte olabilir).
        saat: Callable[[], datetime] = datetime.now,
        # Rastgele gecikmeyi veren fonksiyon (0-600 sn).
        gecikme: Callable[[], float] = lambda: random.uniform(0, EN_FAZLA_GECIKME),
    ):
        self.engine, self.baslat, self.saat, self.gecikme = engine, baslat, saat, gecikme
        # Bilinen tarama saatleri.
        self.saatler: list[Saat] = []
        # Bir sonraki taramanın zamanı.
        self.hedef: datetime | None = None
        self.baz: datetime | None = None  # sonraki hedef bu andan sonraki ilk planlı saat (son çalışma / açılış)
        # Şu an çalışan tarama süreci (yoksa None).
        self.surec: subprocess.Popen | None = None
        self.istek_id: int | None = None  # süren tarama panelden istendiyse

    # Sonraki hedefi hesapla, bazdan sonraki ilk planlı saat + rastgele gecikme.
    def _hedefle(self) -> None:
        self.hedef = sonraki_calisma(self.baz, self.saatler) + timedelta(seconds=self.gecikme())
        log.info("Sonraki çalışma: %s", self.hedef.strftime("%d.%m.%Y %H:%M"))

    # Taramayı başlat (önceki hâlâ sürüyorsa atla) ve bir sonraki hedefi belirle.
    def _calistir(self, simdi: datetime, neden: str) -> None:
        if self.surec is not None:
            log.warning("Önceki tarama hâlâ sürüyor; %s çalışması atlandı", neden)
        else:
            log.info("Günlük iş başlıyor (%s)", neden)
            self.surec = self.baslat(None)
        self.baz = simdi
        self._hedefle()

    # Zamanlayıcı açılırken bir kez çalışır, saatleri okur, yarım kalmış istekleri temizler, kaçan tarama var mı bakar.
    def kur(self, hemen: bool = False) -> None:
        simdi = self.saat()
        with Session(self.engine) as session:
            self.saatler = saatleri_oku(session)
            # En son çalışmanın başlangıç zamanı.
            son_calisma = session.scalar(select(func.max(Calisma.baslangic)))
            # Zamanlayıcı tarama sürerken durduysa o istek yarım kaldı, kullanıcı yeniden isteyebilsin.
            session.execute(update(TaramaIstegi).where(TaramaIstegi.durum == "CALISIYOR").values(
                durum="HATALI", bitti=simdi, hata="Zamanlayıcı tarama sürerken yeniden başladı; tekrar deneyin."))
            session.commit()
        log.info("Zamanlayıcı başladı; çalışma saatleri (Türkiye): %s", ", ".join(saat_metni(self.saatler)))
        self.baz = simdi
        # "--hemen" ile başlatıldıysa hemen tara.
        if hemen:
            self._calistir(simdi, "başlarken, --hemen")
        # Kaçan tarama varsa hemen tara.
        elif kacan_calisma_var_mi(simdi, self.saatler, son_calisma):
            self._calistir(simdi, f"kaçan {son_planli(simdi, self.saatler):%H:%M} çalışması")
        # Yoksa sadece bir sonraki hedefi hesapla.
        else:
            self._hedefle()

    # Tek bir uyanış (30 saniyede bir).
    def adim(self) -> None:
        simdi = self.saat()
        with Session(self.engine) as session:
            # Çalışan tarama bittiyse sonucunu yaz. poll() None dönmüyorsa süreç bitmiştir.
            if self.surec is not None and self.surec.poll() is not None:
                log.info("Günlük iş bitti (çıkış kodu %d; 0 = sorunsuz, 1 = yöneticiye uyarı gitti)",
                         self.surec.returncode)
                # Panelden istenmişse isteğin sonucunu belirle.
                if self.istek_id is not None:
                    istegi_sonuclandir(session, session.get(TaramaIstegi, self.istek_id), self.surec.returncode, simdi)
                    self.istek_id = None
                self.surec = None
            # Saatler panelden değiştiyse hedefi yeniden hesapla.
            saatler = saatleri_oku(session)
            if saatler != self.saatler:
                log.info("Çalışma saatleri değişti: %s → %s", ", ".join(saat_metni(self.saatler)),
                         ", ".join(saat_metni(saatler)))
                self.saatler = saatler
                self._hedefle()  # aynı bazdan, değişiklikten önce vakti gelmiş saat kaçmasın
            # Hedef zamanı geldiyse planlı taramayı başlat.
            if simdi >= self.hedef:
                self._calistir(simdi, "planlı")
            # Tarama sürmüyorsa bekleyen "Şimdi tara" isteği var mı bak.
            if self.surec is None:
                self._istegi_al(session, simdi)
            # Son olarak nabız yaz.
            nabiz_yaz(session, simdi, "tarama" if self.surec is not None else "bekliyor", self.hedef, self.saatler)

    # Bekleyen en eski "Şimdi tara" isteğini alıp taramayı başlatır.
    def _istegi_al(self, session: Session, simdi: datetime) -> None:
        istek = session.scalar(select(TaramaIstegi).where(TaramaIstegi.durum == "BEKLIYOR")
                               .order_by(TaramaIstegi.id).limit(1))
        if istek is None:
            return
        # Koşullu güncelleme, istek iki kez alınmasın (iki zamanlayıcı yanlışlıkla çalışsa bile).
        # "Durumu hâlâ BEKLIYOR ise CALISIYOR yap", güncellenen satır sayısı 1 ise isteği biz aldık.
        alindi = session.execute(update(TaramaIstegi)
                                 .where(TaramaIstegi.id == istek.id, TaramaIstegi.durum == "BEKLIYOR")
                                 .values(durum="CALISIYOR", basladi=simdi)).rowcount
        session.commit()
        if alindi != 1:
            return
        log.info("Günlük iş başlıyor (panelden istek #%d)", istek.id)
        try:
            self.surec = self.baslat(istek.id)
        except Exception as e:
            # Süreç hiç açılamadı, istek CALISIYOR'da takılı kalmasın (panel "sürüyor" der, yeni istekler reddedilir).
            log.exception("Günlük iş başlatılamadı (panelden istek #%d)", istek.id)
            istege_yaz(session, istek.id, durum="HATALI", bitti=simdi,
                       hata=f"Tarama süreci başlatılamadı: {e!r}"[:2000])
            return
        self.istek_id = istek.id

    # Sonsuz döngü, kur, sonra 30 saniyede bir adım at.
    def calis(self, hemen: bool = False, uyu: Callable[[float], None] = time.sleep) -> None:
        self.kur(hemen)
        while True:
            try:
                self.adim()
            except Exception:
                # Veritabanı bir an erişilemezse zamanlayıcı ölmesin, nabız eskir, panel uyarır.
                log.exception("Zamanlayıcı adımı başarısız; %d sn sonra tekrar", ARALIK)
            uyu(ARALIK)

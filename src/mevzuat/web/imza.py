"""İmzalı istek (HMAC-SHA256). Dış sistem API anahtarını hiç göndermez, her isteği anahtarla imzalar.

İstek dört başlık taşır.
- X-Anahtar, anahtarın ilk 10 karakteri, hangi anahtarla imzalandığını söyler.
- X-Zaman, isteğin atıldığı an, Unix saniyesi.
- X-Numara, her istekte yeni, tek seferlik rastgele bir değer.
- X-Imza, aşağıdaki metnin anahtarla HMAC-SHA256 özeti, küçük harf hex.

İmzalanan metin satır satır şudur, aralarında \\n var.
    YÖNTEM            GET, POST, PUT
    ADRES             /api/mevzuat, gönderildiği gibi
    SORGU             q=altin&sayfa=1, gönderildiği gibi, yoksa boş satır
    ZAMAN             X-Zaman ile aynı
    NUMARA            X-Numara ile aynı
    GÖVDE ÖZETİ       gövdenin SHA-256 özeti, küçük harf hex, gövde yoksa boş gövdenin özeti

Kopyalanan istek numarası kullanıldığı için reddedilir, değiştirilen istek imzası tutmadığı için reddedilir,
eski istek saat kontrolüne takılır. Anahtarın kendisi ağda hiç görünmez.
"""

import hashlib
import hmac
import re
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mevzuat import panel_ayarlari
from mevzuat.db import ApiAnahtari, IstekNumarasi, Kullanici

# İsteğin saati bizim saatimizden en fazla bu kadar farklı olabilir. İki sunucunun saati biraz kaymış olsa da istek
# geçer, kopyalanan istek yine geçmez çünkü numara bu süre boyunca saklanır.
PENCERE = 300  # saniye
# Bir anahtarla dakikada en fazla bu kadar istek. Anahtar çalınsa bile toplu veri çekilemez.
DAKIKA_SINIRI = 300
# Başlık adları.
ANAHTAR_BASLIGI, ZAMAN_BASLIGI, NUMARA_BASLIGI, IMZA_BASLIGI = "x-anahtar", "x-zaman", "x-numara", "x-imza"
# Numara 16 ile 64 karakter arası harf, rakam, tire ve alt çizgi.
NUMARA_BICIMI = re.compile(r"[A-Za-z0-9_-]{16,64}")
ON_EK_UZUNLUGU = 10


# İmza tutmadığında fırlatılır. sebep denetim kaydına yazılır, mesaj istek atana döner.
class ImzaHatasi(Exception):
    def __init__(self, sebep: str, mesaj: str, kod: int = 401):
        super().__init__(mesaj)
        self.sebep, self.mesaj, self.kod = sebep, mesaj, kod


# İstekte imza başlıklarından biri bile varsa True, o zaman istek imzalı sayılır ve eksiksiz olmalı.
def imzali_mi(basliklar) -> bool:
    return any(basliklar.get(b) for b in (ANAHTAR_BASLIGI, ZAMAN_BASLIGI, NUMARA_BASLIGI, IMZA_BASLIGI))


# İmzalanan metin.
def imza_metni(yontem: str, adres: str, sorgu: str, zaman: str, numara: str, govde: bytes) -> bytes:
    return "\n".join([yontem.upper(), adres, sorgu, zaman, numara, hashlib.sha256(govde).hexdigest()]).encode()


# Anahtarla imza hesaplar. Dış sistemin yaptığı da tam olarak bu.
def imza_hesapla(anahtar: str, yontem: str, adres: str, sorgu: str, zaman: str, numara: str, govde: bytes) -> str:
    return hmac.new(anahtar.encode(), imza_metni(yontem, adres, sorgu, zaman, numara, govde), hashlib.sha256).hexdigest()


# İmzalı isteği doğrular, anahtarın kaydını ve hesabını döner. Tutmazsa ImzaHatasi. Numarayı kaydeder, commit etmez.
def dogrula(session: Session, basliklar, yontem: str, adres: str, sorgu: str, govde: bytes, gizli_anahtar: str,
            simdi: datetime | None = None) -> tuple[ApiAnahtari, Kullanici]:
    simdi = simdi or datetime.now()
    on_ek, zaman, numara, imza = (basliklar.get(b, "").strip() for b in
                                  (ANAHTAR_BASLIGI, ZAMAN_BASLIGI, NUMARA_BASLIGI, IMZA_BASLIGI))
    if not (on_ek and zaman and numara and imza):
        raise ImzaHatasi("eksik_baslik", "İmza başlıkları eksik: X-Anahtar, X-Zaman, X-Numara ve X-Imza gerekli.")
    # Saat kontrolü, eski ya da ileri tarihli istek.
    if not zaman.isdigit() or abs(simdi.timestamp() - int(zaman)) > PENCERE:
        raise ImzaHatasi("zaman", f"X-Zaman geçersiz ya da {PENCERE // 60} dakikadan eski. Sunucu saatini kontrol edin.")
    if not NUMARA_BICIMI.fullmatch(numara):
        raise ImzaHatasi("numara_bicimi", "X-Numara 16-64 karakter olmalı, sadece harf, rakam, tire ve alt çizgi.")
    # Anahtarı ilk harflerinden bul. İlk harfler üretimde benzersiz tutulur.
    kayit = session.scalar(select(ApiAnahtari).where(ApiAnahtari.on_ek == on_ek[:ON_EK_UZUNLUGU]))
    if kayit is None or kayit.iptal is not None or (kayit.son_kullanma and kayit.son_kullanma <= simdi):
        raise ImzaHatasi("anahtar_yok", "API anahtarı geçersiz, süresi dolmuş ya da iptal edilmiş.")
    anahtar = panel_ayarlari.coz(kayit.gizli, gizli_anahtar) if kayit.gizli else None
    if anahtar is None:
        raise ImzaHatasi("imzasiz_anahtar", "Bu anahtar imza için kullanılamıyor, yeni anahtar üretin.")
    beklenen = imza_hesapla(anahtar, yontem, adres, sorgu, zaman, numara, govde)
    if not hmac.compare_digest(beklenen, imza.lower()):
        raise ImzaHatasi("imza", "İmza tutmuyor. Anahtarı ve imzalanan metni kontrol edin.")
    hesap = session.get(Kullanici, kayit.kullanici_id)
    if hesap is None or not hesap.aktif:
        raise ImzaHatasi("hesap_pasif", "API anahtarı geçersiz, süresi dolmuş ya da iptal edilmiş.")
    # Süresi geçen numaraları sil, saat kontrolü onları zaten reddeder.
    session.execute(delete(IstekNumarasi).where(IstekNumarasi.zaman < simdi - timedelta(seconds=2 * PENCERE)))
    # Hız sınırı, son bir dakikadaki istek sayısı.
    son_dakika = session.scalar(select(func.count()).select_from(IstekNumarasi).where(
        IstekNumarasi.anahtar_id == kayit.id, IstekNumarasi.zaman >= simdi - timedelta(seconds=60)))
    if son_dakika >= DAKIKA_SINIRI:
        raise ImzaHatasi("hiz_siniri", f"Dakikada en fazla {DAKIKA_SINIRI} istek gönderilebilir, biraz bekleyin.", 429)
    # Numarayı kaydet. Aynı numara daha önce geldiyse veritabanı reddeder, iki istek aynı anda gelse de.
    try:
        with session.begin_nested():
            session.add(IstekNumarasi(anahtar_id=kayit.id, numara=numara, zaman=simdi))
    except IntegrityError:
        raise ImzaHatasi("tekrar", "Bu istek daha önce kullanıldı. Her istekte yeni bir X-Numara gönderin.") from None
    return kayit, hesap

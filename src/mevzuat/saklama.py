"""Eski raporların ve işlem kayıtlarının silinmesi. Günlük iş her çalışmada çağırır.

Süre .env'deki MEVZUAT_SAKLAMA_GUN, yoksa 30 gün. 0 yazılırsa hiçbir şey silinmez.
Süresi dolunca silinenler.
  - Karara bağlanmış raporlar (GONDERILDI ya da REDDEDILDI), dağıtım mailleri ve rapordaki kalemler, okunmuş metinleriyle.
  - Denetim kaydı, yani girişler, onay ve ret kararları, ayar değişiklikleri.
Silinmeyenler.
  - Onay bekleyen raporlar ve maili henüz bitmemiş raporlar.
  - Rapora girmemiş kayıtlar. Tarama aynı duyuruyu ikinci kez eklemesin ve konu önizlemesi 90 günü görebilsin diye kalır.
  - Takip edilen mevzuatın metin sürümleri, değişiklik farkı bunlardan çıkar.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mevzuat.db import Denetim, Gonderim, Kayit, Rapor, TaramaIstegi
from mevzuat.web.guvenlik import denetle

# Varsayılan saklama süresi, gün.
SAKLAMA_GUN = 30
# Bu durumdaki raporlarla işi bitmiştir, süresi dolunca silinebilir.
BITMIS_DURUMLAR = ("GONDERILDI", "REDDEDILDI")


# .env'deki MEVZUAT_SAKLAMA_GUN, yoksa 30.
def saklama_gunu() -> int:
    return int(os.environ.get("MEVZUAT_SAKLAMA_GUN", SAKLAMA_GUN))


# Hangi tablodan kaç satır silindi.
@dataclass
class Silinenler:
    rapor: int = 0
    kayit: int = 0
    gonderim: int = 0
    denetim: int = 0


# Süresi dolan raporları ve denetim kayıtlarını siler, commit eder.
def eskileri_sil(session: Session, gun: int, simdi: datetime | None = None) -> Silinenler:
    simdi = simdi or datetime.now()
    sonuc = Silinenler()
    if gun <= 0:
        return sonuc
    sinir = simdi - timedelta(days=gun)

    # Raporun işinin bittiği an. Gönderildiyse gönderim, reddedildiyse karar zamanı, ikisi de yoksa oluşturulma.
    bitis = func.coalesce(Rapor.gonderildi, Rapor.karar_zamani, Rapor.olusturuldu)
    idler = list(session.scalars(select(Rapor.id).where(Rapor.durum.in_(BITMIS_DURUMLAR), bitis < sinir)))
    if idler:
        # "Şimdi tara" isteği bu raporu gösteriyorsa bağlantı kaldırılır, isteğin kendisi kalır.
        session.execute(update(TaramaIstegi).where(TaramaIstegi.rapor_id.in_(idler)).values(rapor_id=None))
        sonuc.gonderim = session.execute(delete(Gonderim).where(Gonderim.rapor_id.in_(idler))).rowcount
        # Kalemler de silinir. Bağlantısı kaldırılsaydı "henüz raporlanmadı" sayılıp yeni rapora girerdi.
        sonuc.kayit = session.execute(delete(Kayit).where(Kayit.rapor_id.in_(idler))).rowcount
        sonuc.rapor = session.execute(delete(Rapor).where(Rapor.id.in_(idler))).rowcount

    sonuc.denetim = session.execute(delete(Denetim).where(Denetim.zaman < sinir)).rowcount

    # Silme işleminin kendisi denetime yazılır, bu satır bir sonraki süre dolana kadar kalır.
    if sonuc.rapor or sonuc.denetim:
        denetle(session, "eski_kayitlar_silindi", None, None, saklama_gun=gun, rapor=sonuc.rapor,
                kayit=sonuc.kayit, gonderim=sonuc.gonderim, denetim=sonuc.denetim)
    session.commit()
    return sonuc

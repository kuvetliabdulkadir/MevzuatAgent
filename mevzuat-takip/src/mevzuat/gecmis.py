"""Kaynak ve konu değişiklik geçmişi ve admin kurtarma, yani "bu değişiklikten önceki hale döndür".

Geçmiş ayrı bir tabloda tutulmaz, çünkü her panel değişikliği zaten denetim kaydına eski ve yeni haliyle yazılıyor
(kaynak_yonetimi ve konu_yonetimi). Geri dönüş de bir değişikliktir. Eski hal normal kayıt yolundan, aynı
doğrulamalarla yeniden kaydedilir ve denetime yeni satır olarak düşer, geçmiş silinmez.

`ayar-ice-aktar` komutuyla yapılan toplu değişiklikler bunun dışında kalır, çünkü denetimde öğe başına eski hal yok.
"""
# Paneldeki "Değişiklik geçmişi" penceresi ve admin'in "bu hale döndür" düğmesi bu dosyayı kullanır.

# select, veritabanı sorgusu yazmak için (SQL'in SELECT'i). Session, veritabanıyla konuşma oturumu.
from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat import kaynak_yonetimi, konu_yonetimi
from mevzuat.db import Denetim, KaynakTanimi, KonuTanimi, Kullanici

# Denetim kaydındaki hangi işlem adları kaynaklarla, hangileri konularla ilgili.
KAYNAK_ISLEMLERI = ("kaynak_ekle", "kaynak_degistir", "kaynak_kaldir", "kaynak_geri_getir", "kaynak_geri_al")
KONU_ISLEMLERI = ("konu_ekle", "konu_degistir", "konu_pasif", "konu_aktif", "konu_geri_al")
# İşlem adlarının ekranda görünecek Türkçe karşılıkları.
ISLEM_ADLARI = {
    "kaynak_ekle": "Eklendi", "kaynak_degistir": "Düzenlendi", "kaynak_kaldir": "Kaldırıldı",
    "kaynak_geri_getir": "Geri getirildi", "kaynak_geri_al": "Önceki hale döndürüldü",
    "konu_ekle": "Eklendi", "konu_degistir": "Düzenlendi", "konu_pasif": "Pasifleştirildi",
    "konu_aktif": "Aktif edildi", "konu_geri_al": "Önceki hale döndürüldü",
}
# Kullanıcıya gösterilmeyen (türetilen) alanlar.
_GIZLI = {"id", "ad_kod", "eski_adlar", "tip"}


# Önceki ve sonraki hali karşılaştırıp sadece değişen alanları listeler (ekranda "şu alan şuydu, şu oldu" diye gösterilir).
def farklar(once: dict | None, sonra: dict | None) -> list[dict]:
    """Değişen alanları {alan, once, sonra} listesi olarak döner. Eklemede once None olur ve bütün alanlar döner."""
    # None gelirse boş sözlük say.
    once, sonra = once or {}, sonra or {}
    # Yeni haldeki her alan için, gizli değilse ve eski değerden farklıysa listeye koy.
    return [{"alan": a, "once": once.get(a), "sonra": sonra.get(a)}
            for a in sonra if a not in _GIZLI and once.get(a) != sonra.get(a)]


# Denetim kaydından bir kaynağın/konunun geçmiş satırlarını (yeniden eskiye) toplar.
def _satirlar(session: Session, islemler: tuple[str, ...], anahtar: str, deger) -> list[dict]:
    # Kullanıcı numarasından adına giden sözlük, satırda kimin yaptığını yazabilmek için.
    adlar = dict(session.execute(select(Kullanici.id, Kullanici.ad)).all())
    # İlgili işlemlerin denetim kayıtlarını en yeniden başlayarak çek.
    kayitlar = session.scalars(select(Denetim).where(Denetim.islem.in_(islemler)).order_by(Denetim.id.desc()))
    # Sadece bu kaynağa/konuya ait olanları ekranın istediği biçime çevir.
    return [{
        "id": d.id, "zaman": d.zaman.isoformat(timespec="minutes"), "islem": d.islem,
        "islem_adi": ISLEM_ADLARI.get(d.islem, d.islem), "kim": adlar.get(d.kullanici_id, "—"),
        "farklar": farklar(d.detay.get("once"), d.detay.get("sonra")),
        "geri_alinabilir": "sonra" in d.detay,  # her kayıt geri alınabilir, eklemenin öncesi yani kaldırılmış hal
        "geri_alinan": d.detay.get("geri_alinan"),
    } for d in kayitlar if d.detay.get(anahtar) == deger]


# Bir kaynağın geçmişi (adına göre).
def kaynak_gecmisi(session: Session, ad: str) -> list[dict]:
    return _satirlar(session, KAYNAK_ISLEMLERI, "kaynak", ad)


# Bir konunun geçmişi (numarasına göre, ad değişebildiği için numara kullanılıyor).
def konu_gecmisi(session: Session, konu_id: int) -> list[dict]:
    return _satirlar(session, KONU_ISLEMLERI, "konu_id", konu_id)


# Geri alınmak istenen denetim satırını bulur ve gerçekten bu kaynağa/konuya ait mi diye kontrol eder.
def _denetim(session: Session, denetim_id: int, islemler: tuple[str, ...], anahtar: str, deger) -> Denetim:
    d = session.get(Denetim, denetim_id)
    # Yoksa, başka türden bir işlemse ya da başka bir kaynağa aitse reddet (biri adresi kurcalayıp başka şey geri almasın).
    if d is None or d.islem not in islemler or d.detay.get(anahtar) != deger:
        raise ValueError("Geçmiş kaydı bulunamadı.")
    return d


# Kaynağı, seçilen değişiklikten hemen önceki haline döndürür.
def kaynagi_geri_al(session: Session, kaynak: KaynakTanimi, denetim_id: int, surum: int, kullanici_id: int) -> None:
    """Seçilen değişiklikten ÖNCEKİ hale döndürür. Kaynak eklenmesinin öncesi = kaldırılmış hal."""
    d = _denetim(session, denetim_id, KAYNAK_ISLEMLERI, "kaynak", kaynak.ad)
    # Değişiklikten önceki hal.
    once = d.detay.get("once")
    # Önceki hal yoksa bu bir ekleme kaydıdır. Öncesinde kaynak hiç yoktu, bu da kaldırılmış hale denk gelir.
    if once is None:
        if kaynak.kaldirildi:
            raise ValueError("Kaynak zaten kaldırılmış.")
        kaynak_yonetimi.kaldir_ya_da_geri_getir(kaynak, True, surum, kullanici_id)
        return
    # Eski hali bir form gibi hazırla ve normal kaydetme yolundan geçir (aynı kontroller uygulansın).
    form = kaynak_yonetimi.KaynakFormu(once["etiket"], once["ayarlar"], once["varsayilan_konular"], once["aktif"])
    kaynak_yonetimi.kaynak_degistir(session, kaynak, form, surum, kullanici_id)
    # "Kaldırıldı mı" bilgisi formda yok, ayrıca eski haline getir.
    kaynak.kaldirildi = once["kaldirildi"]


# Konuyu, seçilen değişiklikten hemen önceki haline döndürür. izlenen, takip listesinde kullanılan konu adları.
def konuyu_geri_al(session: Session, konu: KonuTanimi, denetim_id: int, surum: int, kullanici_id: int,
                   izlenen: set[str]) -> None:
    """Seçilen değişiklikten ÖNCEKİ hale döndürür. Konu eklenmesinin öncesi = pasif hal."""
    d = _denetim(session, denetim_id, KONU_ISLEMLERI, "konu_id", konu.id)
    once = d.detay.get("once")
    # Ekleme kaydıysa öncesinde konu yoktu demektir, o yüzden konuyu pasifleştir. Silmiyoruz.
    if once is None:
        konu_yonetimi.aktiflik(session, konu, False, surum, kullanici_id, izlenen)
        return
    # Eski hali normal düzenleme yolundan kaydet.
    # Açıklama alanından önceki kayıtlarda açıklama yok, boş sayılır.
    form = konu_yonetimi.KonuFormu(once["ad"], once["is_kollari"], once["kelimeler"], once["haric"], once["dislanan"],
                                   once.get("aciklama", ""))
    konu_yonetimi.konu_degistir(session, konu, form, surum, kullanici_id)
    # Aktif/pasif durumu da farklıysa onu da eski haline getir (sürüm az önce arttığı için konu.surum veriliyor).
    if konu.aktif != once["aktif"]:
        konu_yonetimi.aktiflik(session, konu, once["aktif"], konu.surum, kullanici_id, izlenen)

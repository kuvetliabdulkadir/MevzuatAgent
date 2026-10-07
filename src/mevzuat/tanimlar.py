"""Kaynak ve konu tanımları veritabanında durur ve panelden yönetilir.

config/kaynaklar.toml ve config/konular.toml sadece ilk kurulum tohumudur. Tablo boşsa bir kez aktarılır,
doluysa dosyaya hiç bakılmaz. Sonradan dosyayla değişiklik yapmak için açıkça `ayar-ice-aktar` komutu
(`ice_aktar`) çalıştırılır. Önce dışa aktarılır, sonra düzenlenip onaylatılır, en son içe aktarılır. Adlar birebir aktarıldığı için mevcut checkpoint'ler (kaynak_durumu) ve
kayıtlar geçerli kalır, tarama kaldığı yerden devam eder.

Pipeline kaynakların nereden geldiğini bilmez. Buradaki `kaynaklari_oku` ve `konulari_oku` toml'dan okunanla
aynı nesneleri üretir, aynı `kaynak_olustur` ve `konu_olustur` doğrulamasından geçer.
"""
# Kaynak ve konu tanımlarının veritabanı ile ayar dosyaları arasındaki köprüsü.
# İlk kurulumda dosyadan veritabanına aktarma, veritabanından okuma, dışa/içe aktarma.

# json, değerleri toml'a yazarken tırnak/liste biçimini doğru üretmek için.
import json
import logging
import os
# replace, dataclass nesnesinin bir alanı değiştirilmiş kopyasını üretir.
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

# migrate, veritabanı tablolarını en son yapıya getiren fonksiyon.
from mevzuat.db import KaynakTanimi, KonuTanimi, migrate
from mevzuat.filtre import Konu, konu_olustur, konu_tanimlari
from mevzuat.pipeline import ayarlari_dogrula
from mevzuat.sources import kaynak_olustur, kaynak_tanimlari
from mevzuat.sources.base import Kaynak

log = logging.getLogger(__name__)

ORTAK = "Ortak"


# Ayar dosyasının yolunu .env'den al, yoksa varsayılanı kullan.
def _ayar_yolu(degisken: str, varsayilan: str) -> Path:
    return Path(os.environ.get(degisken, varsayilan))


# Konu/kaynak tablosu boşsa toml dosyasından doldurur (sadece ilk kurulumda iş yapar).
def tohumla(session: Session, kaynak_yolu: Path, konu_yolu: Path) -> dict[str, int]:
    """Boş tabloyu toml dosyasından doldurur ve commit eder. Aktarılan sayıları döner, {} dönerse ikisi de zaten doluydu.
    Dosya sadece tablo boşsa gerekir, ilk kurulumdan sonra silinse de olur."""
    aktarilan = {}
    simdi = datetime.now()
    # Konular tablosunda hiç satır yoksa dosyadan oku ve hepsini ekle.
    if not session.scalar(select(func.count()).select_from(KonuTanimi)):
        tanimlar = konu_tanimlari(_var_olmali(konu_yolu))
        session.add_all(KonuTanimi(**t, aktif=True, surum=1, guncelleme=simdi) for t in tanimlar)
        aktarilan["konu"] = len(tanimlar)
    # Kaynaklar tablosu boşsa aynısı, dosyadaki sıra korunur (sira=i).
    if not session.scalar(select(func.count()).select_from(KaynakTanimi)):
        tanimlar = kaynak_tanimlari(_var_olmali(kaynak_yolu))
        session.add_all(
            KaynakTanimi(**t, sira=i, kaldirildi=False, surum=1, guncelleme=simdi) for i, t in enumerate(tanimlar)
        )
        aktarilan["kaynak"] = len(tanimlar)
    # Bir şey aktarıldıysa kaydet ve logla.
    if aktarilan:
        session.commit()
        log.info("Ayar dosyalarından veritabanına aktarıldı: %s", aktarilan)
    return aktarilan


# Dosya yoksa anlaşılır bir mesajla programı durdurur.
def _var_olmali(yol: Path) -> Path:
    if not yol.exists():
        raise SystemExit(
            f"Veritabanında tanım yok ve ilk kurulum dosyası bulunamadı: {yol.resolve()}\n"
            "Komutu proje klasöründen çalıştırın ya da MEVZUAT_KAYNAKLAR / MEVZUAT_KONULAR ile tam yol verin."
        )
    return yol


# Her programın başında çağrılır, veritabanını günceller, gerekirse ilk tanımları yükler.
def hazirla(engine: Engine) -> None:
    """Şemayı günceller ve tablolar boşsa ayar dosyalarından doldurur. Tanım kullanan her giriş noktası
    (tarama, günlük iş, dene, panel) bunu çağırır, böylece hepsinde aynı davranış olur."""
    migrate(engine)
    with Session(engine) as session:
        tohumla(session, _ayar_yolu("MEVZUAT_KAYNAKLAR", "config/kaynaklar.toml"),
                _ayar_yolu("MEVZUAT_KONULAR", "config/konular.toml"))


# Taranacak kaynakları veritabanından okuyup çalışır kaynak nesnelerine çevirir.
def kaynaklari_oku(session: Session) -> list[Kaynak]:
    """Taranacak kaynaklar. Aktif ve kaldırılmamış olanlar, tarama sırasıyla."""
    # Aktif olan ve kaldırılmamış kaynaklar, sıra numarasına göre. Baştaki ~ işareti değil demek.
    satirlar = session.scalars(
        select(KaynakTanimi).where(KaynakTanimi.aktif, ~KaynakTanimi.kaldirildi).order_by(KaynakTanimi.sira)
    )
    return [kaynak_olustur(k.ad, k.tip, dict(k.ayarlar), k.etiket, k.varsayilan_konular) for k in satirlar]


# Aktif konuları veritabanından okuyup Konu nesnelerine çevirir.
def konulari_oku(session: Session) -> list[Konu]:
    satirlar = session.scalars(select(KonuTanimi).where(KonuTanimi.aktif).order_by(KonuTanimi.id))
    return [konu_olustur(k.ad, k.kelimeler, k.is_kollari, k.haric, k.dislanan, k.aciklama) for k in satirlar]


# Panelden adı değiştirilen konular için eski addan yeni ada giden sözlük.
def eski_ad_haritasi(session: Session) -> dict[str, str]:
    """Panelden yeniden adlandırılan konuların eski adından şimdiki adına giden sözlük."""
    return {eski: k.ad for k in session.scalars(select(KonuTanimi)) for eski in k.eski_adlar if eski != k.ad}


# Takip listesi dosyasında eski konu adı yazıyorsa onu şimdiki ada çevirir.
def izlenenleri_esle(session: Session, izlenenler: list | None) -> list | None:
    """izlenen_mevzuat.toml dosyasındaki konu adları panelde değiştirilmiş olabilir, eski adı şimdiki ada çevirir.
    Dosya Docker'da salt okunur, panel onu değiştirmez."""
    # Liste yok ya da boşsa olduğu gibi geri ver.
    if not izlenenler:
        return izlenenler
    harita = eski_ad_haritasi(session)
    # Her takip satırının konusunu, eski adsa yenisiyle değiştirilmiş kopya olarak ver.
    return [replace(i, konu=harita.get(i.konu, i.konu)) for i in izlenenler]


# Alıcı grubu formundaki iş kolu seçenekleri, konularda geçen bütün iş kolları + Ortak.
def is_kollari(session: Session) -> list[str]:
    """Aktif konulardaki iş kolları + Ortak (alıcı grubu formunun seçenekleri)."""
    # Dik çizgi iki kümeyi birleştirir.
    return sorted({ik for k in konulari_oku(session) for ik in k.is_kollari} | {ORTAK})


# ---- dışa aktarma (yedek, şirkete onaya gönderme, sunucu taşıma) --------------------------------------

# Bir değeri toml'a yazılacak biçime çevirir ("metin", 5, ["a", "b"], true).
def _deger(v) -> str:
    # JSON'un string/sayı/liste yazımı TOML'la uyumlu, bool küçük harf zaten aynı.
    if isinstance(v, dict):
        raise TypeError(f"İç içe ayar dışa aktarılamaz: {v!r}")
    # ensure_ascii=False, Türkçe harfler ş gibi kodlara dönmesin.
    return json.dumps(v, ensure_ascii=False)


# Bir toml bloğu yazar, "[[kaynak]]" ve altında "ad = değer" satırları.
def _blok(baslik: str, alanlar: dict) -> str:
    satirlar = [f"[[{baslik}]]"]
    satirlar += [f"{ad} = {_deger(v)}" for ad, v in alanlar.items()]
    return "\n".join(satirlar)


# Veritabanındaki kaynak ve konuları iki toml dosyası metni olarak üretir.
def disa_aktar(session: Session) -> tuple[str, str]:
    """DB'deki tanımları kaynaklar.toml ve konular.toml metni olarak döner. Kaldırılan kaynaklar ve pasif konular yazılmaz,
    pasif kaynaklar `aktif = false` ile yazılır. Çıktı tekrar ilk kurulum tohumu olarak kullanılabilir."""
    zaman = datetime.now().strftime("%d.%m.%Y %H:%M")
    # Kaynaklar dosyası, başlık yorumu + her kaldırılmamış kaynak için bir blok.
    kaynaklar = [f"# Veritabanından dışa aktarıldı ({zaman}). Alanların açıklaması: config/kaynaklar.toml"]
    for k in session.scalars(select(KaynakTanimi).where(~KaynakTanimi.kaldirildi).order_by(KaynakTanimi.sira)):
        # Temel alanlar + tipe özel ayarlar (** ile sözlüğü açıyoruz).
        alanlar = {"ad": k.ad, "tip": k.tip, "etiket": k.etiket, **k.ayarlar}
        if k.varsayilan_konular:
            alanlar["varsayilan_konular"] = k.varsayilan_konular
        if not k.aktif:
            alanlar["aktif"] = False
        kaynaklar.append(_blok("kaynak", alanlar))
    # Konular dosyası, sadece aktif konular, haric/dislanan boşsa yazılmaz.
    konular = [f"# Veritabanından dışa aktarıldı ({zaman}). Eşleşme kuralları: config/konular.toml"]
    for k in session.scalars(select(KonuTanimi).where(KonuTanimi.aktif).order_by(KonuTanimi.id)):
        alanlar = {"ad": k.ad, "is_kollari": k.is_kollari, "kelimeler": k.kelimeler}
        # |= sözlüğe ekle.
        alanlar |= {ad: v for ad, v in (("haric", k.haric), ("dislanan", k.dislanan), ("aciklama", k.aciklama)) if v}
        konular.append(_blok("konu", alanlar))
    # Blokları boş satırla ayırıp iki metin olarak ver.
    return "\n\n".join(kaynaklar) + "\n", "\n\n".join(konular) + "\n"


# ---- içe aktarma (düzenlenmiş/onaylanmış toml'u DB'ye uygular) ----------------------------------------

# Düzenlenmiş toml dosyalarını veritabanına uygular, ekler, günceller, dosyada olmayanı kaldırır/pasifleştirir.
def ice_aktar(session: Session, kaynak_yolu: Path, konu_yolu: Path, izlenenler: list = ()) -> dict[str, list[str]]:
    """toml dosyalarını DB'ye uygular ve commit eder. Adı aynı olan güncellenir, yeni olan eklenir, dosyada olmayan kaynak
    kaldırılır ya da konu pasifleşir, silinmez ve geri getirilebilir. Önce iki dosyanın tamamı doğrulanır, hata varsa
    DB'ye hiçbir şey yazılmaz. Değişen satırın `surum` alanı artar, böylece panelde açık kalmış form eski sayılır.
    kaynak_eklenen, kaynak_degisen, kaynak_kaldirilan ve konu_ ile başlayan benzer listelerden oluşan bir sözlük döner."""
    # Önce iki dosyayı da oku ve kontrol et, hatalıysa veritabanına hiç dokunmadan dur.
    konular = konu_tanimlari(_var_olmali(konu_yolu))
    kaynaklar = kaynak_tanimlari(_var_olmali(kaynak_yolu))
    ayarlari_dogrula(
        [kaynak_olustur(t["ad"], t["tip"], t["ayarlar"], t["etiket"], t["varsayilan_konular"]) for t in kaynaklar],
        [konu_olustur(**t) for t in konular],
        izlenenler,
    )
    simdi = datetime.now()
    # Özet için boş listeler, kaynak_eklenen, kaynak_degisen, ..., konu_kaldirilan.
    ozet: dict[str, list[str]] = {f"{tur}_{olay}": [] for tur in ("kaynak", "konu")
                                  for olay in ("eklenen", "degisen", "kaldirilan")}

    # İç yardımcı, satırdaki alanlardan biri bile farklıysa hepsini yaz, sürümü artır, özete "değişen" diye ekle.
    def uygula(satir, alanlar: dict, tur: str, ad: str) -> None:
        if any(getattr(satir, k) != v for k, v in alanlar.items()):
            for k, v in alanlar.items():
                setattr(satir, k, v)
            satir.surum += 1
            satir.guncelleme = simdi
            # None, değişikliği bir kişinin değil dosyanın yaptığını gösterir.
            satir.guncelleyen_id = None
            ozet[f"{tur}_degisen"].append(ad)

    # Konular, veritabanındakileri ada göre sözlüğe al.
    mevcut_konular = {k.ad: k for k in session.scalars(select(KonuTanimi))}
    for t in konular:
        # Dosyadaki konu veritabanında varsa güncelle (pop, sözlükten çıkar, geriye "dosyada olmayanlar" kalsın).
        if t["ad"] in mevcut_konular:
            uygula(mevcut_konular.pop(t["ad"]), {**t, "aktif": True}, "konu", t["ad"])
        # Yoksa yeni konu ekle.
        else:
            session.add(KonuTanimi(**t, aktif=True, surum=1, guncelleme=simdi))
            ozet["konu_eklenen"].append(t["ad"])
    # Dosyada olmayan konuları pasifleştir, özette "değişen" yerine "kaldırılan" olarak göster.
    for ad, satir in mevcut_konular.items():
        if satir.aktif:
            uygula(satir, {"aktif": False}, "konu", ad)
            ozet["konu_degisen"].remove(ad)
            ozet["konu_kaldirilan"].append(ad)

    # Kaynaklar, aynı mantık. Dosyadaki sıra tarama sırası olur.
    mevcut_kaynaklar = {k.ad: k for k in session.scalars(select(KaynakTanimi))}
    for sira, t in enumerate(kaynaklar):
        if t["ad"] in mevcut_kaynaklar:
            uygula(mevcut_kaynaklar.pop(t["ad"]), {**t, "sira": sira, "kaldirildi": False}, "kaynak", t["ad"])
        else:
            session.add(KaynakTanimi(**t, sira=sira, kaldirildi=False, surum=1, guncelleme=simdi))
            ozet["kaynak_eklenen"].append(t["ad"])
    # Dosyada olmayan kaynakları "kaldırıldı" yap (silinmez, panelden geri getirilebilir).
    for ad, satir in mevcut_kaynaklar.items():
        if not satir.kaldirildi:
            uygula(satir, {"kaldirildi": True}, "kaynak", ad)
            ozet["kaynak_degisen"].remove(ad)
            ozet["kaynak_kaldirilan"].append(ad)

    # Hepsini tek seferde kesinleştir.
    session.commit()
    return ozet

# Bu dosya filtrenin kalbi, bir başlığın/metnin hangi konulara (Vergi, MASAK, Döviz...) uyduğunu anahtar kelimelerle bulur.
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# "Harf sayılan karakterler". Kelimenin başında/sonunda başka harf var mı diye bakarken kullanılıyor.
HARF = "a-z0-9çğıöşü"


# Bir konunun tanımı.
@dataclass(frozen=True)
class Konu:
    # Konunun adı (ör. "Vergi").
    ad: str
    # Aranacak anahtar kelimeler (Türkçe küçük harfe çevrilmiş halde).
    kelimeler: tuple[str, ...]
    # Bu konu eşleşirse kayıt hangi iş kollarına gider.
    is_kollari: tuple[str, ...] = ("Ortak",)  # "Kuyum", "Döviz/Altın", "Oto kiralama", "Ortak" …
    # Bu ifadeler metinden silinir, sonra kelime aranır (ör. "altın vuruş" altın sayılmasın).
    haric: tuple[str, ...] = ()  # metinden maskelenir, kelime eşleşmesine katılmaz
    # Metinde bunlardan biri geçerse konu hiç eşleşmez.
    dislanan: tuple[str, ...] = ()  # metinde geçerse bu konu hiç eşleşmez
    # Bu konu bizi neden ilgilendiriyor, eşleşmeye katılmaz, sadece raporda okuyana gösterilir.
    aciklama: str = ""


# Açıklama en fazla bu kadar karakter olabilir.
ACIKLAMA_EN_FAZLA = 1000


# Türkçe'ye uygun küçük harf. Python'un normal lower()'ı İ ve I harflerinde yanlış sonuç veriyor.
def tr_kucuk(metin: str) -> str:
    # Python'un lower() fonksiyonu büyük İ'yi noktalı tuhaf bir harfe, büyük I'yı da i'ye çeviriyor, ikisi de Türkçe için yanlış.
    metin = metin.replace("İ", "i").replace("I", "ı").lower()
    # Şapkalı harfleri düz harfe çevir, böylece kâr ile kar aynı sayılsın.
    metin = metin.translate(str.maketrans("âîû", "aiu"))
    # Fazla boşlukları teke indir.
    return re.sub(r"\s+", " ", metin)


# Konu tanımını kontrol edip Konu nesnesi üretir. Boş ad, kelimesiz ya da iş kolsuz konuya izin vermez.
def konu_olustur(ad: str, kelimeler: list[str], is_kollari: list[str],
                 haric: list[str] = (), dislanan: list[str] = (), aciklama: str = "") -> Konu:
    """Tek doğrulama noktası, toml aktarma, panel kaydı ve tarama hep buradan geçer. Kelimeler girildiği gibi saklanır,
    Türkçe küçük harfe çevirme burada, okurken yapılır."""
    # Küçük yardımcı, listedeki boşlukları kırp, boş kalanları at.
    temiz = lambda liste: tuple(x.strip() for x in liste if x.strip())
    if not ad or not ad.strip():
        raise ValueError("Konu adı boş olamaz.")
    if not temiz(kelimeler):
        raise ValueError("En az bir anahtar kelime girin.")
    if not temiz(is_kollari):
        raise ValueError("En az bir iş kolu seçin.")
    # Açıklamadaki fazla boşlukları sadeleştir, satır sonları kalsın.
    aciklama = "\n".join(" ".join(s.split()) for s in str(aciklama).strip().splitlines()).strip()
    if len(aciklama) > ACIKLAMA_EN_FAZLA:
        raise ValueError(f"Açıklama en fazla {ACIKLAMA_EN_FAZLA} karakter olabilir.")
    # Kelimeleri Türkçe küçük harfe çevirerek konuyu oluştur (arama da küçük harfle yapılıyor).
    return Konu(
        ad=ad.strip(),
        kelimeler=tuple(tr_kucuk(x) for x in temiz(kelimeler)),
        is_kollari=temiz(is_kollari),
        haric=tuple(tr_kucuk(x) for x in temiz(haric)),
        dislanan=tuple(tr_kucuk(x) for x in temiz(dislanan)),
        aciklama=aciklama,
    )


# konular.toml dosyasını okur, her konuyu kontrol eder, liste halinde verir.
def konu_tanimlari(yol: Path) -> list[dict]:
    """konular.toml dosyasını okur ve doğrulanmış tanımları girildiği gibi döner. Her tanımda ad, is_kollari, kelimeler, haric,
    dislanan ve aciklama alanları olur."""
    with open(yol, "rb") as f:
        veri = tomllib.load(f)
    # Her [[konu]] bloğunu sözlüğe çevir, haric ve dislanan yazılmadıysa boş liste, açıklama yazılmadıysa boş.
    tanimlar = [
        {"ad": k["ad"], "is_kollari": list(k["is_kollari"]), "kelimeler": list(k["kelimeler"]),
         "haric": list(k.get("haric", [])), "dislanan": list(k.get("dislanan", [])),
         "aciklama": str(k.get("aciklama", "")).strip()}
        for k in veri["konu"]
    ]
    # Her konuyu bir kez kurmayı dene, hatalıysa hangi konu olduğunu da söyle.
    for t in tanimlar:
        try:
            konu_olustur(**t)
        except ValueError as e:  # dosyada çok konu var, hangisi olduğu yazılmazsa bulunamaz
            raise ValueError(f"{yol.name}, \"{t['ad']}\" konusu: {e}") from None
    # Aynı adla iki konu olamaz.
    adlar = [t["ad"] for t in tanimlar]
    if len(adlar) != len(set(adlar)):
        raise ValueError("Konu adları tekil olmalı")
    return tanimlar


# toml'daki konuları Konu nesnesi olarak verir (gerçek tarama veritabanından okur).
def konulari_yukle(yol: Path) -> list[Konu]:
    """toml dosyasındaki konular. Testler ve ilk kurulum aktarması için, tarama DB'den okur."""
    return [konu_olustur(**t) for t in konu_tanimlari(yol)]


# Metindeki "hariç" ifadeleri boşlukla değiştirir ki kelime aramasında yakalanmasınlar.
def _maskele(metin: str, ifadeler: tuple[str, ...]) -> str:
    for ifade in ifadeler:
        # İfadenin önünde ve arkasında başka harf olmayan (tam kelime) geçişleri sil. re.escape, nokta gibi özel karakterleri düz harf say.
        metin = re.sub(rf"(?<![{HARF}]){re.escape(ifade)}(?![{HARF}])", " ", metin)
    return metin


# Kaynağın varsayılan konusu, başlıkta kelime geçmeden eşleşen kayıtta kelime yerine bu yazılır.
TUM_DUYURULAR = "(kaynağın tüm duyuruları)"


# Asıl eşleştirme burada. Metnin hangi konulara uyduğunu ve her konuda hangi kelimelerin geçtiğini bulur, örneğin Vergi konusunda ötv.
def eslesmeler(metin: str, konular: list[Konu]) -> dict[str, list[str]]:
    """Her konu adı için metinde geçen anahtar kelimeleri döner. Eşleşme yoksa boş sözlük döner."""
    # Metni Türkçe küçük harfe çevir.
    normal = tr_kucuk(metin)
    sonuc = {}
    for konu in konular:
        # Dışlanan bir ifade geçiyorsa bu konuyu atla.
        if any(d in normal for d in konu.dislanan):
            continue
        # Hariç ifadeleri metinden çıkar.
        aranan = _maskele(normal, konu.haric)
        # Kelimenin önünde başka harf olmayan geçişleri ara. Sonu serbest, "vergi" kelimesi "vergisi"ni de yakalar.
        bulunan = [k for k in konu.kelimeler if re.search(rf"(?<![{HARF}]){re.escape(k)}", aranan)]
        # En az bir kelime bulunduysa konu eşleşti.
        if bulunan:
            sonuc[konu.ad] = bulunan
    return sonuc


# Eşleşen konulardan iş kollarını çıkarır, tekrarsız ve alfabetik sırayla.
def is_kollari(eslesen: dict[str, list[str]], konular: list[Konu]) -> list[str]:
    """Eşleşen konuların iş kolları (tekil, sıralı)."""
    # Her konunun iş kolları.
    konu_is_kollari = {k.ad: k.is_kollari for k in konular}
    # Küme ({}) tekrarları otomatik atar, sorted alfabetik dizer.
    return sorted({ik for ad in eslesen for ik in konu_is_kollari[ad]})

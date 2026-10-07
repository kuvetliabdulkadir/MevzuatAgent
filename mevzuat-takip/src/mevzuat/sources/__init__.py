# Bu dosya kaynak tiplerinin "telefon rehberi", ayarlarda yazan tip adını (ör. "wordpress") alıp
# o tipi okuyan Python sınıfını bulur ve kaynağı kurar. Kaynak tanımlarını kontrol eden tek yer de burası.
# tomllib, .toml ayar dosyalarını okumak için Python'un kendi kütüphanesi.
import tomllib
# Path, dosya yollarıyla çalışmak için (C:/... ya da /opt/... fark etmeden).
from pathlib import Path

# Her kaynak tipinin sınıfı ayrı dosyada, burada hepsini içeri alıyoruz.
from mevzuat.sources.base import Kaynak
from mevzuat.sources.gib import GibKaynagi
from mevzuat.sources.html import HtmlKaynagi
from mevzuat.sources.resmi_gazete import ResmiGazeteKaynagi
from mevzuat.sources.rss import RssKaynagi
from mevzuat.sources.tarayici import TarayiciKaynagi
from mevzuat.sources.wordpress import WordPressKaynagi

# config/kaynaklar.toml'daki tip adından o tipi okuyan sınıfa giden sözlük. Yeni bir kaynak tipi yazınca buraya ekle.
# Sözlük, solda ayarda yazılan tip adı, sağda o tipi okuyan sınıf.
TIPLER: dict[str, type] = {
    # Resmî Gazete'nin günlük fihrist sayfası.
    "resmi_gazete": ResmiGazeteKaynagi,
    # WordPress ile yapılmış siteler (MASAK). Sitenin hazır JSON servisinden okunur.
    "wordpress": WordPressKaynagi,
    # GİB'in kendi JSON servisi.
    "gib": GibKaynagi,
    # Düz HTML duyuru listesi + CSS seçiciler (Chromium yok). Panelden eklenebilir.
    "html": HtmlKaynagi,
    # RSS/Atom duyuru akışı. Panelden eklenebilir, adres girilince otomatik bulunur.
    "rss": RssKaynagi,
    # Sadece API'si ve düz HTML'i olmayan siteler için, şu an kullanan kaynak yok (ihtiyaç olursa hazır).
    "tarayici": TarayiciKaynagi,
}


# Bir kaynak tanımını (ad, tip, ayarlar) alıp çalışan bir kaynak nesnesi üretir. Tanım hatalıysa hata verir.
def kaynak_olustur(ad: str, tip: str, ayarlar: dict, etiket: str | None = None,
                   varsayilan_konular: list[str] | tuple[str, ...] = ()) -> Kaynak:
    """Tek doğrulama noktası. toml aktarma, panel kaydı ve tarama hep buradan geçer. Burada kurulamayan
    bir tanım kaydedilemez, dolayısıyla taramaya da giremez. Tanım hatalıysa ValueError verir."""
    # Ad boşsa ya da tip bizim bildiğimiz tiplerden biri değilse hemen dur.
    if not ad or tip not in TIPLER:
        raise ValueError(f"Geçersiz kaynak tanımı: ad={ad!r}, tip={tip!r} (bilinen tipler: {sorted(TIPLER)})")
    # Tipin sınıfını bulup ayarlarla kuruyoruz. Çift yıldız sözlükteki her şeyi parametre olarak verir.
    # Etiket verilmişse onu da ekliyoruz, verilmemişse sınıfın kendi varsayılan etiketi kalır.
    try:
        kaynak = TIPLER[tip](ad=ad, **({"etiket": etiket} if etiket else {}), **ayarlar)
    # TypeError, ayarlarda olmayan/eksik bir alan var demek. Kullanıcıya anlaşılır bir hataya çeviriyoruz.
    except TypeError as e:
        raise ValueError(f"{ad!r} kaynağının ayarları hatalı: {e}") from e
    # Tipten bağımsız ortak ayar, bu kaynağın HER kaydı bu konularla ilgili sayılır.
    kaynak.varsayilan_konular = tuple(varsayilan_konular)
    # Hazır kaynağı geri ver.
    return kaynak


# kaynaklar.toml dosyasını okuyup her kaynağı kontrol eder, düzenli bir liste halinde geri verir.
def kaynak_tanimlari(yol: Path) -> list[dict]:
    """kaynaklar.toml dosyasını okur ve pasifler dahil doğrulanmış tanımları döner. Her tanımda ad, tip, etiket, ayarlar, varsayilan_konular ve aktif alanları olur.
    Hatalı ayar (bilinmeyen tip, eksik alan, tekrar eden ad) çalışmanın ortasında değil, en başta hata verir."""
    # Dosyayı açıp içindeki kaynak bloklarını liste olarak alıyoruz. "rb" ikili okuma demek, tomllib böyle istiyor.
    with open(yol, "rb") as f:
        ham = tomllib.load(f)["kaynak"]

    # Sonuç listesi ve şimdiye kadar gördüğümüz adlar (aynı ad iki kez yazılmasın diye).
    tanimlar = []
    adlar = set()
    # Her kaynak bloğunu tek tek işliyoruz.
    for tanim in ham:
        # Kopyasını alıyoruz ki aşağıdaki pop'lar orijinali bozmasın.
        tanim = dict(tanim)
        # pop, değeri al ve sözlükten çıkar. Geriye sadece tipe özel ayarlar kalsın diye.
        ad, tip = tanim.pop("ad", None), tanim.pop("tip", None)
        # Aynı ad daha önce geçtiyse hata.
        if ad in adlar:
            raise ValueError(f"Kaynak adı tekrar ediyor: {ad!r}")
        adlar.add(ad)
        # aktif yazılmamışsa kaynak açık sayılır.
        aktif = tanim.pop("aktif", True)
        varsayilan_konular = list(tanim.pop("varsayilan_konular", ()))
        etiket = tanim.pop("etiket", None)
        # Kaynağı bir kez kurmayı deniyoruz, hatalıysa burada patlar, tarama sırasında değil.
        kaynak = kaynak_olustur(ad, tip, tanim, etiket, varsayilan_konular)
        # Kontrolden geçen tanımı sonuca ekle. Etiketi kaynaktan alıyoruz (verilmediyse varsayılanı gelir).
        tanimlar.append({"ad": ad, "tip": tip, "etiket": kaynak.etiket, "ayarlar": tanim,
                         "varsayilan_konular": varsayilan_konular, "aktif": aktif})
    return tanimlar


# toml'daki sadece AKTİF kaynakları çalışır halde verir. Gerçek taramada kullanılmaz (o veritabanından okur).
def kaynaklari_yukle(yol: Path) -> list[Kaynak]:
    """toml dosyasındaki aktif kaynaklar. Testler ve ilk kurulum aktarması için, tarama DB'den okur."""
    # Tek satırlık döngü, her aktif tanım için kaynak_olustur çağır, sonuçları listeye koy.
    return [kaynak_olustur(t["ad"], t["tip"], t["ayarlar"], t["etiket"], t["varsayilan_konular"])
            for t in kaynak_tanimlari(yol) if t["aktif"]]

"""Panelden kaynak yönetimi (#10). Kaynak eklenir, düzenlenir, kaldırılır, geri getirilir, kaydetmeden denenir.

Her kayıt `sources.kaynak_olustur` fonksiyonundan geçer, taramayla aynı doğrulama yapılır. Panelden kaydedilemeyen bir
ayar taramaya da giremez. Kaynağın `ad` alanı panelden değiştirilemez, çünkü checkpoint ve kayıtlar ona bağlı.

Güvenlik (SSRF). Sunucu panelden girilen adrese istek atar. Bu yüzden adres sadece https olabilir ve
DNS çözümünden sonra bütün IP'leri genel internette olmalı. localhost, 10.*, 172.16-31.*, 192.168.*,
169.254.* (bulut meta verisi) ve IPv6 yerel adresler reddedilir. Yönlendirme zaten takip edilmez
(http.make_client). Bilinen bir sınır var. Kayıttan sonra alan adının DNS kaydı iç ağa çevrilirse (DNS rebinding)
tarama anında tekrar kontrol yapılmaz. Ama adresi sadece giriş yapmış onaylayıcı ya da admin girebiliyor.
"""
# Paneldeki "Kaynaklar" sayfasının arkasındaki iş, kaynak ekleme/düzenleme/kaldırma, her tipin form alanları,
# adres güvenlik kontrolü ve "kaydetmeden dene" (son 7 günü veritabanına yazmadan tarayıp gösterme).

import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
# urlsplit, adresi parçalarına ayırır (şema, alan adı, port, kullanıcı adı...).
from urllib.parse import urlsplit

import httpx
# case, SQL'de "eğer ... ise 1 değilse 0" ifadesi (ilgili kayıtları saymak için).
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from mevzuat import pipeline
from mevzuat.db import Calisma, Kayit, KaynakDurumu, KaynakTanimi, KonuTanimi
from mevzuat.filtre import Konu, is_kollari
from mevzuat.http import ip_disa_acik_mi, make_client
from mevzuat.sources import TIPLER, kaynak_olustur
from mevzuat.sources.gib import TURLER as GIB_TURLERI


# Paneldeki formun bir alanı (ör. "Liste sayfası adresi").
@dataclass(frozen=True)
class Alan:
    # Ayarlardaki adı.
    ad: str
    # Ekranda görünen adı.
    etiket: str
    tur: str  # "url" | "metin" | "sayi" | "secim_listesi"
    # Alanın altında görünen açıklama.
    aciklama: str = ""
    zorunlu: bool = False
    varsayilan: object = None
    secenekler: dict = field(default_factory=dict)  # secim_listesi için, değer, sonra görünen ad


# Bir kaynak tipinin formu, adı, açıklaması, panelden eklenebilir mi, alanları.
@dataclass(frozen=True)
class TipFormu:
    etiket: str
    aciklama: str
    eklenebilir: bool  # panelden YENİ kaynak eklenebilir mi (RG/GİB tek, sadece düzenlenir)
    alanlar: tuple[Alan, ...] = ()


# Birçok tipte ortak "örtüşme" alanı.
_ORTUSME = Alan("ortusme_gun", "Örtüşme (gün)", "sayi",
                "Her taramada son taramadan bu kadar gün geriye de bakılır (geç yayımlananlar kaçmasın).",
                varsayilan=1)

# Her kaynak tipinin paneldeki formu. Panel, formu buradaki tanıma bakarak çiziyor.
TIP_FORMLARI: dict[str, TipFormu] = {
    "resmi_gazete": TipFormu("Resmî Gazete", "Günlük fihrist (mükerrerler dahil). Tek kaynak; eklenmez.", False),
    "wordpress": TipFormu(
        "WordPress sitesi", "Sitenin WordPress REST API'si (ör. MASAK).", True,
        (Alan("api_url", "API adresi", "url", "Genelde https://site/wp-json/wp/v2/posts (MASAK: …/portal/v2/posts).",
              zorunlu=True), _ORTUSME),
    ),
    "gib": TipFormu(
        "GİB duyuruları", "gib.gov.tr'nin kendi JSON API'si. Tek kaynak; eklenmez.", False,
        (Alan("turler", "Duyuru türleri", "secim_listesi", "Sitedeki sekmeler.", zorunlu=True,
              secenekler={str(k): v for k, v in GIB_TURLERI.items()}), _ORTUSME),
    ),
    "html": TipFormu(
        "Düz HTML sayfası", "Duyuru listesi sayfası; CSS seçicileriyle okunur. Önce Dene ile bulunanları kontrol edin.",
        True,
        (Alan("liste_url", "Liste sayfası adresi", "url", "Duyuruların listelendiği sayfa.", zorunlu=True),
         Alan("oge", "Öğe seçicisi", "metin", "Listedeki her duyurunun kutusu (ör. ul.duyurular li).", zorunlu=True),
         Alan("baslik", "Başlık seçicisi", "metin", "Öğe içinde; metni başlık, linki duyuru adresi (ör. a).",
              zorunlu=True),
         Alan("tarih", "Tarih seçicisi", "metin", "Öğe içinde; 02.10.2026 ya da 2 Ekim 2026 (ör. span.tarih).",
              zorunlu=True),
         Alan("icerik", "İçerik seçicisi", "metin", "Duyuru sayfasında metnin bulunduğu alan (ör. div.icerik).",
              zorunlu=True),
         _ORTUSME),
    ),
    "rss": TipFormu(
        "RSS/Atom akışı", "Sitenin duyuru akışı. Site tasarımı değişse de bozulmaz; varsa HTML'e tercih edilir.", True,
        (Alan("akis_url", "Akış adresi", "url", "RSS ya da Atom adresi (ör. https://site/rss).", zorunlu=True),
         Alan("icerik", "İçerik seçicisi", "metin",
              "İsteğe bağlı: duyuru sayfasında metnin bulunduğu alan. Boşsa ana metin kendiliğinden bulunur."),
         _ORTUSME),
    ),
    "tarayici": TipFormu(
        "Tarayıcı (Playwright)", "JavaScript'le oluşan siteler için yedek; sunucuya Chromium kurulmasını gerektirir. "
        "Panelden eklenmez.", False,
    ),
}
# Yeni bir kaynak tipi yazılıp buraya form eklenmeyi unutulursa program açılırken hata versin.
assert set(TIP_FORMLARI) == set(TIPLER), "Yeni kaynak tipi için TIP_FORMLARI'na form tanımı ekleyin"

# "Dene" düğmesinin sınırları, son 7 gün, en fazla 100 satır, 15 sn zaman aşımı, en fazla 10 MB cevap.
DENE_GUN = 7
DENE_EN_FAZLA = 100  # satır
DENE_ZAMAN_ASIMI = 15.0  # sn
DENE_EN_FAZLA_BAYT = 10 * 1024 * 1024


# "Sen düzenlerken başkası değiştirdi" hatası.
class CakismaHatasi(Exception):
    """Kayıt, form açıldıktan sonra başkası tarafından değiştirilmiş."""


# ---- adres güvenliği ------------------------------------------------------------------------------------

# Alan adını alıp IP listesi veren fonksiyonların türü.
Cozumleyici = Callable[[str], list[str]]


# Gerçek DNS sorgusu.
def _dns(host: str) -> list[str]:
    try:
        return sorted({b[4][0] for b in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except socket.gaierror as e:
        raise ValueError(f"Adres çözümlenemedi: {host}") from e


# İzin verilen standart portlar.
STANDART_PORT = {"https": 443, "http": 80}


# Panelden girilen ya da sayfadan gelen bir adresi kontrol eder, güvenliyse temizlenmiş halini verir.
def guvenli_url(url: str, cozumle: Cozumleyici | None = None, semalar: tuple[str, ...] = ("https",)) -> str:
    """Panelden girilen adresi kontrol eder, sadece https ve sadece genel internet kabul edilir. Geçerliyse temizlenmiş adresi döner.
    `semalar` ile sitenin kendi sayfasındaki duyuru linkleri için http de kabul edilebilir, iç ağ kontrolü yine aynıdır."""
    url = url.strip()
    parca = urlsplit(url)
    # Adres izin verilen bir şemayla, yani https ile başlamalı ve alan adı olmalı.
    if parca.scheme not in semalar or not parca.hostname:
        raise ValueError("Adres https:// ile başlamalı." if semalar == ("https",) else f"Geçersiz adres: {url}")
    # "https://kullanici:parola@site" gibi adreslere izin verme.
    if parca.username or parca.password:
        raise ValueError("Adreste kullanıcı adı/parola olamaz.")
    # Sadece standart port (443/80), başka port genelde iç servis demektir.
    if parca.port not in (None, STANDART_PORT[parca.scheme]):
        raise ValueError("Sadece standart port (https: 443) kullanılabilir.")
    # İlk süzme, adresi çözüp genel internette olduğunu doğrula (hızlı, anlaşılır hata). Asıl SSRF koruması
    # bağlantı anında IP'yi sabitleyen http.SsrfKoruyanTransport'tadır (DNS rebinding'e karşı).
    for ip in (cozumle or _dns)(parca.hostname):
        if not ip_disa_acik_mi(ip):
            raise ValueError(f"İç ağ / özel adreslere istek atılamaz ({parca.hostname} → {ip}).")
    return url


# ---- tanım doğrulama ------------------------------------------------------------------------------------

# Görünen addan kod adı üretir. Türkçe harfleri sadeleştirir, küçük harfe çevirir, boşlukları alt çizgi yapar, bu ad varsa sonuna _2 ekler.
def ad_uret(etiket: str, mevcut: set[str]) -> str:
    """Görünen addan kod adı üretir, örneğin "BDDK Duyurusu" adından "bddk_duyurusu" çıkar. Çakışırsa sonuna "_2", "_3" gibi ek gelir."""
    cevir = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")
    # Harf/rakam dışındaki her şeyi "_" yap, baştaki/sondaki "_"leri at, en fazla 40 karakter, boşsa "kaynak".
    kok = re.sub(r"[^a-z0-9]+", "_", etiket.translate(cevir).lower()).strip("_")[:40] or "kaynak"
    ad, i = kok, 2
    # Bu ad zaten varsa _2, _3 ... dene.
    while ad in mevcut:
        ad, i = f"{kok}_{i}", i + 1
    return ad


# Formdan gelen tipe özel alanları türüne göre kontrol edip temizler.
def ayarlari_temizle(tip: str, ham: dict, cozumle: Cozumleyici | None = None) -> dict:
    """Formdan gelen tipe özel alanlardan kaynağa verilecek ayarları çıkarır. Bilinmeyen alan alınmaz."""
    ayarlar = {}
    # Sadece tipin formunda tanımlı alanlara bak (formda olmayan bir alan gönderilse bile alınmaz).
    for alan in TIP_FORMLARI[tip].alanlar:
        deger = ham.get(alan.ad)
        if isinstance(deger, str):
            deger = deger.strip()
        # Boş bırakıldıysa, zorunluysa hata, varsayılanı varsa onu kullan.
        if deger in (None, "", []):
            if alan.zorunlu:
                raise ValueError(f"'{alan.etiket}' boş olamaz.")
            if alan.varsayilan is not None:
                ayarlar[alan.ad] = alan.varsayilan
            continue
        # Adres alanı, güvenlik kontrolünden geçir.
        if alan.tur == "url":
            ayarlar[alan.ad] = guvenli_url(str(deger), cozumle)
        # Metin alanı, boşlukları sadeleştir, en fazla 300 karakter.
        elif alan.tur == "metin":
            metin = " ".join(str(deger).split())
            if len(metin) > 300:
                raise ValueError(f"'{alan.etiket}' en fazla 300 karakter olabilir.")
            ayarlar[alan.ad] = metin
        # Sayı alanı, tam sayı ve 0-30 arası.
        elif alan.tur == "sayi":
            try:
                sayi = int(deger)
            except (TypeError, ValueError):
                raise ValueError(f"'{alan.etiket}' sayı olmalı.") from None
            if not 0 <= sayi <= 30:
                raise ValueError(f"'{alan.etiket}' 0 ile 30 arasında olmalı.")
            ayarlar[alan.ad] = sayi
        # Seçim listesi, seçilenler gerçekten seçeneklerde var mı.
        elif alan.tur == "secim_listesi":
            secilen = [str(d) for d in (deger if isinstance(deger, list) else [deger])]
            bilinmeyen = set(secilen) - set(alan.secenekler)
            if bilinmeyen:
                raise ValueError(f"'{alan.etiket}' için geçersiz seçim: {sorted(bilinmeyen)}")
            ayarlar[alan.ad] = sorted({int(d) for d in secilen})
    return ayarlar


# Panelden gelen kaynak formu.
@dataclass
class KaynakFormu:
    etiket: str
    ayarlar: dict
    varsayilan_konular: list[str]
    aktif: bool = True


# Formun tamamını kontrol eder, görünen ad, ayarlar, konular ve son olarak kaynağı gerçekten kurmayı dener.
def _dogrula(session: Session, ad: str, tip: str, form: KaynakFormu, cozumle: Cozumleyici) -> KaynakFormu:
    etiket = " ".join(form.etiket.split())
    if not etiket:
        raise ValueError("Görünen ad boş olamaz.")
    if len(etiket) > 200:
        raise ValueError("Görünen ad en fazla 200 karakter olabilir.")
    ayarlar = ayarlari_temizle(tip, form.ayarlar, cozumle)
    # "Her kaydı ilgili say" konuları aktif konular arasında olmalı.
    aktif_konular = set(session.scalars(select(KonuTanimi.ad).where(KonuTanimi.aktif)))
    # dict.fromkeys, tekrarları atıp sırayı koruyan kısa yol.
    konular = list(dict.fromkeys(form.varsayilan_konular))
    tanimsiz = [k for k in konular if k not in aktif_konular]
    if tanimsiz:
        raise ValueError(f"Tanımsız ya da pasif konu: {', '.join(tanimsiz)}")
    kaynak_olustur(ad, tip, ayarlar, etiket, konular)  # taramayla aynı doğrulama
    return KaynakFormu(etiket, ayarlar, konular, form.aktif)


# Kaynağın o anki hali (denetim kaydı ve panel için).
def kaynak_bilgisi(k: KaynakTanimi) -> dict:
    """Denetim kaydı ve panel için kaynağın o anki hali."""
    return {"ad": k.ad, "tip": k.tip, "etiket": k.etiket, "ayarlar": dict(k.ayarlar),
            "varsayilan_konular": list(k.varsayilan_konular), "aktif": k.aktif, "kaldirildi": k.kaldirildi}


# Panelden yeni kaynak ekler.
def kaynak_ekle(session: Session, tip: str, form: KaynakFormu, kullanici_id: int,
                cozumle: Cozumleyici | None = None) -> KaynakTanimi:
    """Yeni kaynak (commit etmez). Kod adı görünen addan üretilir."""
    if tip not in TIP_FORMLARI:
        raise ValueError(f"Bilinmeyen kaynak tipi: {tip}")
    # Resmî Gazete, GİB, tarayıcı tipleri panelden eklenemez.
    if not TIP_FORMLARI[tip].eklenebilir:
        raise ValueError(f"'{TIP_FORMLARI[tip].etiket}' tipinde panelden yeni kaynak eklenemez.")
    # Kod adını üret, formu doğrula.
    mevcut = set(session.scalars(select(KaynakTanimi.ad)))
    ad = ad_uret(form.etiket, mevcut)
    temiz = _dogrula(session, ad, tip, form, cozumle)
    # Tarama sırasında en sona koy.
    sira = (session.scalar(select(func.max(KaynakTanimi.sira))) or 0) + 1
    kaynak = KaynakTanimi(ad=ad, tip=tip, etiket=temiz.etiket, ayarlar=temiz.ayarlar,
                          varsayilan_konular=temiz.varsayilan_konular, sira=sira, aktif=temiz.aktif,
                          kaldirildi=False, surum=1, guncelleme=datetime.now(), guncelleyen_id=kullanici_id)
    session.add(kaynak)
    session.flush()
    return kaynak


# Formu açtıktan sonra kaynak başkası tarafından değiştirildiyse dur.
def _surum_kontrol(kaynak: KaynakTanimi, surum: int) -> None:
    if kaynak.surum != surum:
        raise CakismaHatasi("Siz düzenlerken bu kaynak başkası tarafından değiştirildi. Sayfayı yenileyip tekrar deneyin.")


# Var olan kaynağı düzenler (kod adı ve tip değişmez).
def kaynak_degistir(session: Session, kaynak: KaynakTanimi, form: KaynakFormu, surum: int, kullanici_id: int,
                    cozumle: Cozumleyici | None = None) -> None:
    """Görünen ad, tipe özel ayarlar, varsayılan konular ve aktiflik değişebilir. Ad ve tip değişmez."""
    _surum_kontrol(kaynak, surum)
    temiz = _dogrula(session, kaynak.ad, kaynak.tip, form, cozumle)
    kaynak.etiket, kaynak.ayarlar = temiz.etiket, temiz.ayarlar
    kaynak.varsayilan_konular, kaynak.aktif = temiz.varsayilan_konular, temiz.aktif
    _isaretle(kaynak, kullanici_id)


# Kaynağı kaldırır ya da geri getirir (silmiyoruz, işaretliyoruz).
def kaldir_ya_da_geri_getir(kaynak: KaynakTanimi, kaldir: bool, surum: int, kullanici_id: int) -> None:
    _surum_kontrol(kaynak, surum)
    if kaynak.kaldirildi == kaldir:
        raise ValueError("Kaynak zaten kaldırılmış." if kaldir else "Kaynak zaten listede.")
    kaynak.kaldirildi = kaldir
    _isaretle(kaynak, kullanici_id)


# Değişiklik damgası, sürüm +1, zaman, kim.
def _isaretle(kaynak: KaynakTanimi, kullanici_id: int) -> None:
    kaynak.surum += 1
    kaynak.guncelleme = datetime.now()
    kaynak.guncelleyen_id = kullanici_id


# ---- liste + durum --------------------------------------------------------------------------------------

# Kaynaklar sayfasındaki liste, her kaynağın tanımı + tarama durumu.
def kaynak_listesi(session: Session) -> list[dict]:
    """Panel listesi. Her kaynağın tanımı ve taramadaki durumu, yani checkpoint, son başarılı adım, kayıt sayıları ve son hata."""
    # Her kaynağın durumu, yani checkpoint'i.
    durumlar = {d.kaynak: d for d in session.scalars(select(KaynakDurumu))}
    # Her kaynağın toplam ve ilgili kayıt sayıları.
    sayilar = {ad: (toplam, ilgili or 0) for ad, toplam, ilgili in session.execute(
        select(Kayit.kaynak, func.count(), func.sum(case((Kayit.ilgili, 1), else_=0)))
        .group_by(Kayit.kaynak)
    )}
    # Biten en son çalışma (hata mesajları için).
    son = session.scalar(select(Calisma).where(Calisma.durum != "CALISIYOR").order_by(Calisma.id.desc()).limit(1))
    sonuc = []
    # Önce listede olanlar (kaldırılmamış), sıra numarasına göre.
    for k in session.scalars(select(KaynakTanimi).order_by(KaynakTanimi.kaldirildi, KaynakTanimi.sira)):
        durum = durumlar.get(k.ad)
        toplam, ilgili = sayilar.get(k.ad, (0, 0))
        # Son çalışmanın hata metninde "kaynak_adi: hata" satırı varsa hatayı çek.
        hata = None
        if son and son.hata:
            hata = next((s.split(": ", 1)[1] for s in son.hata.splitlines() if s.startswith(f"{k.ad}: ")), None)
        sonuc.append({
            **kaynak_bilgisi(k), "surum": k.surum, "tip_etiketi": TIP_FORMLARI[k.tip].etiket,
            "checkpoint": durum.checkpoint if durum else None,
            "son_basarili": durum.guncellendi.isoformat(timespec="minutes") if durum else None,
            "toplam_kayit": toplam, "ilgili_kayit": int(ilgili),
            "son_hata": hata, "son_calisma": son.baslangic.isoformat(timespec="minutes") if son else None,
            "guncelleme": k.guncelleme.isoformat(timespec="minutes"),
        })
    return sonuc


# Panelin "yeni kaynak" formunu çizebilmesi için bütün tiplerin form tanımları.
def tip_listesi() -> list[dict]:
    return [{"tip": tip, "etiket": f.etiket, "aciklama": f.aciklama, "eklenebilir": f.eklenebilir,
             "alanlar": [{"ad": a.ad, "etiket": a.etiket, "tur": a.tur, "aciklama": a.aciklama, "zorunlu": a.zorunlu,
                          "varsayilan": a.varsayilan, "secenekler": a.secenekler} for a in f.alanlar]}
            for tip, f in TIP_FORMLARI.items()]


# ---- kaydetmeden dene -----------------------------------------------------------------------------------

# "Dene" düğmesi, kaydedilmemiş kaynağı son 7 gün için tarar, sonuçları (ve hangi konuya takıldığını) gösterir.
def dene(tip: str, form: KaynakFormu, konular: list[Konu], aktif_konular: set[str],
         client: httpx.Client | None = None, cozumle: Cozumleyici | None = None,
         bugun: date | None = None, simdi: datetime | None = None) -> dict:
    """Kaydedilmemiş tanımı son DENE_GUN gün için tarar, DB'ye hiçbir şey yazmaz.
    kayitlar, toplam ve kesildi alanları olan bir sözlük döner. kayitlar listesindeki her öğede baslik, tarih, kaynakca, eslesen ve is_kollari olur."""
    if tip not in TIP_FORMLARI:
        raise ValueError(f"Bilinmeyen kaynak tipi: {tip}")
    # Ayarları ve konuları kontrol et.
    ayarlar = ayarlari_temizle(tip, form.ayarlar, cozumle)
    tanimsiz = [k for k in form.varsayilan_konular if k not in aktif_konular]
    if tanimsiz:
        raise ValueError(f"Tanımsız ya da pasif konu: {', '.join(tanimsiz)}")
    # Geçici bir kaynak nesnesi kur (adı "deneme", veritabanına girmez).
    kaynak = kaynak_olustur("deneme", tip, ayarlar, " ".join(form.etiket.split()) or "Deneme", form.varsayilan_konular)
    bugun, simdi = bugun or date.today(), simdi or datetime.now()
    satirlar, toplam = [], 0
    # İstemci verilmediyse kısa zaman aşımlı ve boyut sınırlı kendi istemcimizi aç (sonra kapatacağız).
    kendi = client is None
    client = client or make_client(zaman_asimi=DENE_ZAMAN_ASIMI, en_fazla_bayt=DENE_EN_FAZLA_BAYT)
    try:
        # 7 gün öncesinden başlayarak tara.
        for adim in kaynak.tara(client, kaynak.geriye_checkpoint(bugun, simdi, DENE_GUN), bugun, simdi):
            for t in adim.kayitlar:
                toplam += 1
                # İlk 100 satırı göster, her birinin hangi konuya takıldığını da hesapla.
                if len(satirlar) < DENE_EN_FAZLA:
                    eslesen = pipeline.baslik_eslesmeleri(kaynak, t.baslik, konular)
                    satirlar.append({"baslik": t.baslik, "tarih": t.yayin_tarihi.isoformat(), "kaynakca": t.kaynakca,
                                     "eslesen": eslesen, "is_kollari": is_kollari(eslesen, konular)})
    # Siteye ulaşılamadıysa anlaşılır hata.
    except httpx.HTTPError as e:
        raise ValueError(f"Kaynağa ulaşılamadı ya da cevap beklenmedik: {e!r}") from e
    # Cevap beklenen yapıda değilse (yanlış tip/ayar) anlaşılır hata.
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Kaynağın cevabı bu tipe uymuyor (ayar yanlış olabilir): {e!r}") from e
    finally:
        if kendi:
            client.close()
    return {"kayitlar": satirlar, "toplam": toplam, "kesildi": toplam > len(satirlar), "gun": DENE_GUN}

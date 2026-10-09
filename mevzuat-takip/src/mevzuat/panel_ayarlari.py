"""Panelden değiştirilebilen işletim ayarları (mail sunucusu, panel adresi, saklama süreleri ...).

Panelde girilen değer .env'deki değerin önüne geçer. Panelde boş bırakılan (ya da sıfırlanan) ayar .env'den okunur,
.env'de de yoksa varsayılan kullanılır. Değerler sistem_ayarlari tablosunda tek satırda (panel_ayarlari) durur,
sadece panelden girilenler yazılır. Panel ve günlük iş ayarı her kullanımda okur, değişiklik için yeniden başlatma gerekmez.

Mail şifresi veritabanında şifrelenmiş durur, anahtar MEVZUAT_GIZLI_ANAHTAR'dan türetilir. Gizli anahtar değişirse
kayıtlı şifre çözülemez, o zaman .env'deki şifre kullanılır ve panelde şifrenin yeniden girilmesi istenir.

Panele taşınmayanlar sunucu kurulumunun parçasıdır, yanlış değer paneli kilitleyebilir ya da güvenliği açabilir:
veritabanı adresi ve şifresi, gizli anahtar, HTTPS, vekil IP'leri, port, OCR ve dosya yolları.
"""

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import formataddr

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from mevzuat.alicilar import adresleri_ayikla
from mevzuat.db import SistemAyari

# sistem_ayarlari tablosundaki satırın anahtarı.
AYAR_ANAHTARI = "panel_ayarlari"
# Panel adresinin biçimi, mail.panel_adresi_ayardan ile aynı kural.
PANEL_ADRESI = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?")


# Panelde değiştirilebilen bir ayar.
@dataclass(frozen=True)
class Alan:
    ad: str
    degisken: str  # .env'deki karşılığı
    tur: str  # "metin" | "sayi" | "evet_hayir" | "adresler" | "sifre" | "adres"
    grup: str
    etiket: str
    aciklama: str
    varsayilan: object = None
    en_az: int | None = None
    en_cok: int | None = None


ALANLAR: tuple[Alan, ...] = (
    Alan("smtp_host", "MEVZUAT_SMTP_HOST", "metin", "Mail sunucusu", "SMTP sunucusu",
         "Ör. smtp.gmail.com. Boşsa mail gönderilmez, sunucuda giden_mailler klasörüne yazılır."),
    Alan("smtp_port", "MEVZUAT_SMTP_PORT", "sayi", "Mail sunucusu", "SMTP portu", "Genelde 587 (STARTTLS).",
         587, 1, 65535),
    Alan("smtp_kullanici", "MEVZUAT_SMTP_KULLANICI", "metin", "Mail sunucusu", "SMTP kullanıcı adı",
         "Gmail'de mail adresinin kendisi."),
    Alan("smtp_sifre", "MEVZUAT_SMTP_SIFRE", "sifre", "Mail sunucusu", "SMTP şifresi",
         "Gmail'de normal şifre değil uygulama şifresi. Kaydedildikten sonra gösterilmez, şifreli saklanır."),
    Alan("smtp_gonderen", "MEVZUAT_SMTP_GONDEREN", "metin", "Mail sunucusu", "Gönderen adresi",
         "Boşsa kullanıcı adı kullanılır. Gmail'de hesabın kendisi ya da onaylanmış takma adı olmalı."),
    Alan("smtp_gonderen_adi", "MEVZUAT_SMTP_GONDEREN_ADI", "metin", "Mail sunucusu", "Gönderen adı",
         "Mailde 'Kimden' kısmında adresle birlikte görünen ad, ör. Mevzuat Takip ya da Uyum Birimi. Boşsa sadece adres görünür."),
    Alan("panel_adresi", "MEVZUAT_PANEL_ADRESI", "adres", "Genel", "Panel adresi",
         "Maillerdeki 'Onay paneline git' ve davet linkleri bu adrese gider, ör. https://mevzuat.firma.com.tr."),
    Alan("admin_alicilari", "MEVZUAT_ADMIN_ALICILARI", "adresler", "Genel", "Uyarı mailleri",
         "Sistem uyarıları ve haftalık özet bu adreslere gider. Virgülle ya da alt alta yazın."),
    Alan("ek_ekle", "MEVZUAT_EK_EKLE", "evet_hayir", "Genel", "PDF belgeleri maile eklensin",
         "Kapalıysa mailde sadece kaynak linki olur.", True),
    Alan("nabiz_gunu", "MEVZUAT_NABIZ_GUNU", "sayi", "Genel", "Haftalık özet günü",
         "0 Pazartesi, 6 Pazar.", 0, 0, 6),
    Alan("mfa_zorunlu", "MEVZUAT_MFA", "evet_hayir", "Güvenlik", "İki adımlı doğrulama zorunlu",
         "Açıksa herkes girişte telefondaki doğrulayıcı uygulamanın kodunu da girer.", False),
    Alan("dokuman_acik", "MEVZUAT_DOKUMAN_ACIK", "evet_hayir", "Güvenlik", "API dokümanı herkese açık",
         "Açıksa /api/dokuman (Swagger) anahtarsız açılır, içinde veri yok, sadece adresler ve açıklamalar var. "
         "İstekler yine API anahtarı ister. Kapalıysa doküman da anahtar ya da panel girişi ister.", True),
    Alan("saklama_gun", "MEVZUAT_SAKLAMA_GUN", "sayi", "Saklama", "Rapor ve denetim saklama süresi (gün)",
         "Bitmiş raporlar ve denetim kaydı bu kadar gün sonra silinir. 0 ise silinmez.", 30, 0, 3650),
    Alan("pasif_silme_gun", "MEVZUAT_PASIF_SILME_GUN", "sayi", "Saklama", "Pasif hesap silme süresi (gün)",
         "Bu kadar gün pasif kalan hesabın kişisel bilgileri silinir. 0 ise silinmez.", 30, 0, 3650),
)
ALAN = {a.ad: a for a in ALANLAR}


# Gizli anahtardan şifreleme anahtarı türetir (gizli anahtar en az 32 karakter, panel açılırken kontrol ediliyor).
def _fernet(gizli_anahtar: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"mevzuat-panel-ayarlari:" + gizli_anahtar.encode()).digest()))


def sifrele(metin: str, gizli_anahtar: str) -> str:
    return _fernet(gizli_anahtar).encrypt(metin.encode()).decode()


# Çözülemezse (gizli anahtar değişmiş) None döner.
def coz(sifreli: str, gizli_anahtar: str) -> str | None:
    try:
        return _fernet(gizli_anahtar).decrypt(sifreli.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# Ham değeri alanın türüne çevirir ve kontrol eder. Boş değer None olur (yani "ayarlanmamış").
def temizle(alan: Alan, ham) -> object:
    if ham is None or (isinstance(ham, str) and not ham.strip()):
        return None
    if alan.tur == "evet_hayir":
        if isinstance(ham, bool):
            return ham
        return str(ham).strip().lower() in ("1", "true", "evet", "on")
    if alan.tur == "sayi":
        try:
            sayi = int(str(ham).strip())
        except ValueError:
            raise ValueError(f"'{alan.etiket}' sayı olmalı.") from None
        if (alan.en_az is not None and sayi < alan.en_az) or (alan.en_cok is not None and sayi > alan.en_cok):
            raise ValueError(f"'{alan.etiket}' {alan.en_az} ile {alan.en_cok} arasında olmalı.")
        return sayi
    if alan.tur == "adresler":
        adresler, hatali = adresleri_ayikla(ham if isinstance(ham, str) else "\n".join(ham))
        if hatali:
            raise ValueError(f"Geçersiz e-posta adresi: {', '.join(hatali)}")
        return adresler or None
    # Satır sonları ve fazla boşluklar teke iner (mail başlığına satır sonu girmesin).
    metin = " ".join(str(ham).split())
    if len(metin) > 300:
        raise ValueError(f"'{alan.etiket}' en fazla 300 karakter olabilir.")
    if alan.tur == "adres":
        if not PANEL_ADRESI.fullmatch(metin):
            raise ValueError(f"'{alan.etiket}' geçersiz, örnek: https://mevzuat.firma.com.tr")
        return metin.rstrip("/")
    return metin


# .env'deki değer, yoksa varsayılan. .env'de hatalı değer varsa varsayılan kullanılır.
def env_degeri(alan: Alan) -> object:
    try:
        deger = temizle(alan, os.environ.get(alan.degisken))
    except ValueError:
        deger = None
    return alan.varsayilan if deger is None else deger


# Veritabanındaki satır, yoksa None.
def satir(session: Session) -> SistemAyari | None:
    return session.get(SistemAyari, AYAR_ANAHTARI)


# O an geçerli ayarlar, her alan için değer. Panelde girilen .env'in önüne geçer.
def etkin(session: Session, gizli_anahtar: str | None = None) -> dict:
    kayitli = (s.deger if (s := satir(session)) else {}) or {}
    gizli_anahtar = gizli_anahtar if gizli_anahtar is not None else os.environ.get("MEVZUAT_GIZLI_ANAHTAR", "")
    sonuc = {}
    for alan in ALANLAR:
        deger = kayitli.get(alan.ad)
        if alan.tur == "sifre" and deger is not None:
            deger = coz(deger, gizli_anahtar) if gizli_anahtar else None
        sonuc[alan.ad] = env_degeri(alan) if deger is None else deger
    return sonuc


# Panelde gösterilecek hali. Şifre hiç gönderilmez, sadece kaynağı (panel / .env / yok) söylenir.
def panel_gorunumu(session: Session, gizli_anahtar: str) -> dict:
    s = satir(session)
    kayitli = (s.deger if s else {}) or {}
    gecerli = etkin(session, gizli_anahtar)
    alanlar = []
    for alan in ALANLAR:
        panelde = kayitli.get(alan.ad) is not None
        env_var = temizle_guvenli(alan, os.environ.get(alan.degisken)) is not None
        satir_ = {"ad": alan.ad, "grup": alan.grup, "etiket": alan.etiket, "aciklama": alan.aciklama, "tur": alan.tur,
                  "en_az": alan.en_az, "en_cok": alan.en_cok, "kaynak": "panel" if panelde else (".env" if env_var else "varsayilan")}
        if alan.tur == "sifre":
            satir_["deger"] = None
            satir_["dolu"] = bool(gecerli[alan.ad])
            satir_["cozulemedi"] = panelde and coz(kayitli[alan.ad], gizli_anahtar) is None
        else:
            satir_["deger"] = gecerli[alan.ad]
        alanlar.append(satir_)
    return {"alanlar": alanlar, "surum": s.surum if s else 0}


def temizle_guvenli(alan: Alan, ham) -> object:
    try:
        return temizle(alan, ham)
    except ValueError:
        return None


# Panelden gelen değişiklikleri uygular. Değeri None olan alan sıfırlanır (.env'e döner), listede olmayan alana dokunulmaz.
# Şifre alanı sadece yeni değer gelince değişir. Değişen alan adlarını döner, commit etmez.
def kaydet(session: Session, degisiklikler: dict, surum: int, kullanici_id: int, gizli_anahtar: str) -> list[str]:
    bilinmeyen = set(degisiklikler) - set(ALAN)
    if bilinmeyen:
        raise ValueError(f"Bilinmeyen ayar: {', '.join(sorted(bilinmeyen))}")
    s = satir(session)
    if (s.surum if s else 0) != surum:
        raise CakismaHatasi("Ayarlar siz düzenlerken başkası tarafından değiştirildi. Sayfayı yenileyip tekrar deneyin.")
    eski = dict((s.deger if s else {}) or {})
    yeni = dict(eski)
    for ad, ham in degisiklikler.items():
        alan = ALAN[ad]
        deger = temizle(alan, ham)
        if deger is None:
            yeni.pop(ad, None)
        elif alan.tur == "sifre":
            yeni[ad] = sifrele(deger, gizli_anahtar)
        else:
            yeni[ad] = deger
    degisen = sorted(ad for ad in ALAN if eski.get(ad) != yeni.get(ad))
    if not degisen:
        return []
    if s is None:
        s = SistemAyari(anahtar=AYAR_ANAHTARI, deger=yeni, surum=1, guncelleme=datetime.now(), guncelleyen_id=kullanici_id)
        session.add(s)
    else:
        s.deger, s.surum, s.guncelleme, s.guncelleyen_id = yeni, s.surum + 1, datetime.now(), kullanici_id
    return degisen


# "Sen düzenlerken başkası değiştirdi" hatası.
class CakismaHatasi(Exception):
    pass


# Geçerli ayarlardan mail göndericisi. SMTP sunucusu yoksa mailler dosyaya yazılır.
def gonderici(ayarlar: dict):
    from pathlib import Path

    from mevzuat.mail import DosyaGonderici, SmtpGonderici

    if not ayarlar["smtp_host"]:
        return DosyaGonderici(Path("giden_mailler"))
    kullanici = ayarlar["smtp_kullanici"] or ""
    adres = ayarlar["smtp_gonderen"] or kullanici
    # Görünen ad varsa "Uyum Birimi <adres@firma.com>" biçiminde.
    gonderen = formataddr((ayarlar["smtp_gonderen_adi"], adres)) if ayarlar.get("smtp_gonderen_adi") else adres
    return SmtpGonderici(host=ayarlar["smtp_host"], port=ayarlar["smtp_port"], kullanici=kullanici,
                         sifre=ayarlar["smtp_sifre"] or "", gonderen=gonderen)

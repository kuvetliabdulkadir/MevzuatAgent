"""Parola ve giriş güvenliği.

- Parolalar Argon2id ile saklanır. Bu yavaş ve bellek isteyen bir hash, sızan DB'den kaba kuvvetle çözmek pahalı olur.
- Art arda 5 hatalı girişte hesap 15 dakika kilitlenir.
- Hata mesajı "kullanıcı yok", "parola yanlış" ya da "kilitli" diye ayrım yapmaz. Olmayan kullanıcı için de
  sahte bir hash doğrulanır, böylece cevap süresinden hangi e-postanın kayıtlı olduğu anlaşılmaz.
- Her deneme denetim tablosuna yazılır.
- İki adımlı doğrulama (MFA) telefondaki doğrulayıcı uygulamanın 6 haneli koduyla yapılır (TOTP, RFC 6238).
  Yanlış kodlar parola denemeleriyle aynı sayaca girer, 5 hatada kilit olur. Aynı kod iki kez kullanılamaz.
- Parola linkini (davet ya da sıfırlama) admin başlatır, kişiye maille tek kullanımlık ve süreli bir link gider. Parolayı kişi
  kendisi belirler, admin hiçbir parolayı görmez. Link DB'de özet olarak durur, yenisi gönderilince eskisi geçersiz olur.
"""
# Panelin giriş güvenliği, parola saklama, giriş deneme, kilitleme, iki adımlı doğrulama (MFA),
# davet/sıfırlama linkleri ve kullanıcı ekleme/pasifleştirme.

# hashlib, SHA-256 özeti üretmek için. hmac.compare_digest, iki yazıyı "süre sızdırmadan" karşılaştırır.
import hashlib
import hmac
import html
import os
# secrets, tahmin edilemeyen rastgele değer (link için) üretir.
import secrets
import time
from datetime import datetime, timedelta

# pyotp, telefondaki doğrulayıcı uygulamayla aynı 6 haneli kodları üretip doğrulayan kütüphane.
import pyotp
# qrcode, MFA kurulumunda telefona okutulan QR kodunu çizer.
import qrcode
import qrcode.image.svg
# argon2, parolaları güvenle "özetlemek" için kullanılan algoritma.
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mevzuat.db import ApiAnahtari, Denetim, Kullanici, ParolaLinki
from mevzuat.mail import Mail

MFA_UYGULAMA_ADI = "Mevzuat Takip"  # doğrulayıcı uygulamada görünen ad
MFA_PENCERE = 1  # ±1 zaman adımı (30 sn), telefon saati biraz kaymış olsa da kod kabul edilir
# Parola en az 12 karakter.
MIN_PAROLA = 12
# 5 hatalı denemede 15 dakika kilit.
MAX_DENEME = 5
KILIT_SURESI = timedelta(minutes=15)
ROLLER = ("admin", "onaylayici", "api")
ROL_ADLARI = {"admin": "Yönetici", "onaylayici": "Onaylayıcı", "api": "API kullanıcısı", "tam": "Tam yetki"}
# API anahtarının rolü olabilecekler. "tam" her şeyi yapar (test, entegrasyon), sadece anahtarlara verilir.
# "api" rolü sadece dokümanı görür, anahtara verilmez.
API_ANAHTARI_ROLLERI = ("tam", "admin", "onaylayici")
API_ANAHTARI_ON_EKI = "mvz_"
# Davet linki 3 gün, sıfırlama linki 1 saat geçerli.
# Bu kadar gün pasif kalan hesap silinir (MEVZUAT_PASIF_SILME_GUN, 0 ise hiç silinmez).
PASIF_SILME_GUN = 30
LINK_SURESI = {"davet": timedelta(days=3), "sifirlama": timedelta(hours=1)}

# Parola özetleyici.
_hasher = PasswordHasher()
# Olmayan kullanıcı için de aynı süre harcansın diye kullanılan sahte özet.
_SAHTE_HASH = _hasher.hash("zamanlama-esitleme-icin-sahte-parola")


# Parolayı özetler (kısa parolayı reddeder).
def parola_hashle(parola: str) -> str:
    if len(parola) < MIN_PAROLA:
        raise ValueError(f"Parola en az {MIN_PAROLA} karakter olmalı.")
    return _hasher.hash(parola)


# Girilen parolanın saklanan özetle uyuşup uyuşmadığına bakar.
def _dogrula(parola_hash: str, parola: str) -> bool:
    try:
        return _hasher.verify(parola_hash, parola)
    except (VerificationError, InvalidHashError):
        return False


# Denetim kaydına bir satır ekler (commit'i çağıran yapar).
def denetle(session: Session, islem: str, kullanici_id: int | None = None, ip: str | None = None, **detay) -> None:
    session.add(Denetim(zaman=datetime.now(), kullanici_id=kullanici_id, islem=islem, detay=detay, ip=ip))


# E-postaya göre kullanıcıyı bulur (büyük/küçük harf fark etmez).
def kullanici_bul(session: Session, eposta: str) -> Kullanici | None:
    return session.scalar(select(Kullanici).where(func.lower(Kullanici.eposta) == eposta.strip().lower()))


# Hatalı deneme, sayacı artır, 5'e ulaşırsa 15 dk kilitle, denetime yaz.
def _hatali_deneme(session: Session, kullanici: Kullanici, ip: str | None, sebep: str) -> None:
    kullanici.basarisiz_giris += 1
    if kullanici.basarisiz_giris >= MAX_DENEME:
        kullanici.kilit_bitis = datetime.now() + KILIT_SURESI
        kullanici.basarisiz_giris = 0
        denetle(session, "hesap_kilitlendi", kullanici.id, ip)
    denetle(session, "giris_basarisiz", kullanici.id, ip, sebep=sebep)
    session.commit()


# Giriş denemesi, e-posta + parola. Başarılıysa kullanıcıyı, değilse None döner.
def giris_dene(
    session: Session, eposta: str, parola: str, ip: str | None, mfa_zorunlu: bool = False
) -> Kullanici | None:
    """Parolayı doğrular. MFA gerekiyorsa (zorunluysa ya da kullanıcı kurmuşsa) giriş henüz tamamlanmamıştır.
    Denetime "parola_dogrulandi" yazılır, oturum ancak kod doğrulanınca açılır (mfa_dogrula)."""
    simdi = datetime.now()
    kullanici = kullanici_bul(session, eposta)

    # Kullanıcı yok, pasif ya da API hesabı (parolayla girilmez), yine de sahte doğrulama yap (süre aynı olsun), sonra reddet.
    if kullanici is None or not kullanici.aktif or kullanici.api_hesabi:
        _dogrula(_SAHTE_HASH, parola)  # süre eşitleme
        denetle(session, "giris_basarisiz", ip=ip, eposta=eposta[:254], sebep="yok_veya_pasif")
        session.commit()
        return None

    # Hesap kilitliyse reddet.
    if kullanici.kilit_bitis and kullanici.kilit_bitis > simdi:
        _dogrula(_SAHTE_HASH, parola)
        denetle(session, "giris_kilitli", kullanici.id, ip)
        session.commit()
        return None

    # Parola yanlışsa hatalı deneme say.
    if not _dogrula(kullanici.parola_hash, parola):
        _hatali_deneme(session, kullanici, ip, "parola")
        return None

    # Özetleme ayarları güçlendirildiyse parolayı yeni ayarla yeniden özetle.
    if _hasher.check_needs_rehash(kullanici.parola_hash):
        kullanici.parola_hash = _hasher.hash(parola)
    # MFA gerekiyorsa giriş henüz bitmedi (kod sorulacak), gerekmiyorsa giriş tamam.
    if mfa_gerekli(kullanici, mfa_zorunlu):
        # Sayaç burada SIFIRLANMAZ, parolayı bilen biri kodu tahmin ederken kilit sayacı birikmeye devam etmeli.
        denetle(session, "parola_dogrulandi", kullanici.id, ip)
    else:
        _giris_tamam(session, kullanici, ip)
    session.commit()
    return kullanici


# Başarılı giriş, sayacı ve kilidi sıfırla, son giriş zamanını yaz, denetime ekle.
def _giris_tamam(session: Session, kullanici: Kullanici, ip: str | None) -> None:
    kullanici.basarisiz_giris = 0
    kullanici.kilit_bitis = None
    kullanici.son_giris = datetime.now()
    denetle(session, "giris", kullanici.id, ip)


# --- iki adımlı doğrulama ------------------------------------------------------------------------

# Oturum çerezine konan "parmak izi", parola/MFA/e-posta değişince değişir, eski oturumlar geçersiz olur.
def oturum_izi(kullanici: Kullanici) -> str:
    """Oturum çerezine yazılan kısa iz. Parola, MFA sırrı (kurulumda ya da sıfırlamada) ya da e-posta değişince iz değişir ve
    o kullanıcının açık oturumları biter. Yedekten geri dönüşte aynı numara başka kişiye denk gelse de çerez geçmez.
    Özet tek yönlüdür. Çerez imzalı ama şifreli değil, yine de içinden parola özeti ya da sır okunamaz."""
    ham = f"{kullanici.id}|{kullanici.eposta}|{kullanici.parola_hash}|{kullanici.mfa_gizli or ''}"
    return hashlib.sha256(ham.encode()).hexdigest()[:32]


# Bu kullanıcıya MFA kodu sorulacak mı. Herkese zorunluysa ya da kullanıcı kendisi kurduysa sorulur.
def mfa_gerekli(kullanici: Kullanici, mfa_zorunlu: bool) -> bool:
    return mfa_zorunlu or kullanici.mfa_aktif


# MFA kurulumunu başlatır, kullanıcıya özel gizli anahtar üretir (QR kodu bundan çizilir).
def mfa_kurulum_baslat(session: Session, kullanici: Kullanici) -> str:
    """Kurulum için sır üretir (sayfa yenilenirse aynı sır kalır, QR değişmez). Aktif MFA'nın sırrı değiştirilemez."""
    if kullanici.mfa_aktif:
        raise ValueError("MFA zaten kurulu.")
    if not kullanici.mfa_gizli:
        kullanici.mfa_gizli = pyotp.random_base32()
        session.commit()
    return kullanici.mfa_gizli


# Telefonla okutulacak QR kodunu SVG resim olarak üretir.
def mfa_qr_svg(kullanici: Kullanici) -> str:
    """Doğrulayıcı uygulamanın okutacağı QR kodu. Satır içi SVG olarak üretilir, dış kaynak ya da resim kullanmaz, CSP'ye uyar."""
    # QR kodunun içine telefondaki uygulamanın tanıdığı otpauth adresi konuyor.
    uri = pyotp.TOTP(kullanici.mfa_gizli).provisioning_uri(name=kullanici.eposta, issuer_name=MFA_UYGULAMA_ADI)
    return qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).to_string(encoding="unicode")


# Girilen 6 haneli kod doğruysa hangi 30 saniyelik dilime ait olduğunu verir.
def _kod_adimi(gizli: str, kod: str, simdi: float) -> int | None:
    """Kod geçerliyse eşleştiği 30 sn'lik zaman adımı, değilse None."""
    kod = kod.strip().replace(" ", "")
    # Tam 6 rakam değilse boşuna hesaplama.
    if len(kod) != 6 or not kod.isdigit():
        return None
    totp = pyotp.TOTP(gizli)
    # Şimdiki 30 saniyelik dilimin numarası.
    adim = int(simdi) // totp.interval
    # Bir önceki, şimdiki ve bir sonraki dilimi dene (telefon saati biraz kaymış olabilir).
    for fark in range(-MFA_PENCERE, MFA_PENCERE + 1):
        if hmac.compare_digest(totp.at((adim + fark) * totp.interval), kod):
            return adim + fark
    return None


# MFA kodunu doğrular, doğruysa girişi tamamlar (kurulumdaysa MFA'yı açar).
def mfa_dogrula(session: Session, kullanici: Kullanici, kod: str, ip: str | None, simdi: float | None = None) -> bool:
    """Kodu doğrular, doğruysa girişi tamamlar. Kullanıcı kurulum aşamasındaysa (mfa_aktif değilse) ilk doğru kod MFA'yı açar."""
    if not kullanici.aktif or not kullanici.mfa_gizli:
        return False
    # Hesap kilitliyse kod bile denenmez.
    if kullanici.kilit_bitis and kullanici.kilit_bitis > datetime.now():
        denetle(session, "giris_kilitli", kullanici.id, ip)
        session.commit()
        return False
    adim = _kod_adimi(kullanici.mfa_gizli, kod, time.time() if simdi is None else simdi)
    if adim is None or (kullanici.mfa_son_adim is not None and adim <= kullanici.mfa_son_adim):
        # İkinci koşul aynı ya da daha eski bir kodun tekrar kullanılması, böylece omuz üstünden görülen kodla giriş engellenir.
        _hatali_deneme(session, kullanici, ip, "mfa_kodu")
        return False
    # Kullanılan dilimi hatırla (aynı kod bir daha geçmesin).
    kullanici.mfa_son_adim = adim
    # Kurulumdaysa MFA'yı aç.
    if not kullanici.mfa_aktif:
        kullanici.mfa_aktif = True
        denetle(session, "mfa_kuruldu", kullanici.id, ip)
    _giris_tamam(session, kullanici, ip)
    session.commit()
    return True


# Telefonunu kaybeden kullanıcının MFA'sını sıfırlar (komut satırından).
def mfa_sifirla(session: Session, kullanici: Kullanici) -> None:
    """Telefonunu kaybeden kullanıcı için (sadece sunucuda, CLI ile). Bir sonraki girişte yeniden kurar."""
    kullanici.mfa_gizli = None
    kullanici.mfa_aktif = False
    kullanici.mfa_son_adim = None
    denetle(session, "mfa_sifirlandi", kullanici.id)
    session.commit()


# Komut satırından kullanıcı ekler (ilk admin böyle eklenir).
def kullanici_ekle(session: Session, eposta: str, ad: str, rol: str, parola: str) -> Kullanici:
    if rol not in ROLLER:
        raise ValueError(f"Rol {ROLLER} içinden olmalı.")
    if "@" not in eposta:
        raise ValueError("Geçerli bir e-posta adresi girin.")
    if kullanici_bul(session, eposta):
        raise ValueError(f"{eposta} zaten kayıtlı.")
    kullanici = Kullanici(
        eposta=eposta.strip().lower(), ad=ad.strip(), rol=rol, parola_hash=parola_hashle(parola),
        aktif=True, basarisiz_giris=0, olusturuldu=datetime.now(),
    )
    session.add(kullanici)
    # flush, kullanıcının numarası oluşsun (denetim kaydına yazmak için).
    session.flush()
    denetle(session, "kullanici_eklendi", kullanici.id, eposta=kullanici.eposta, rol=rol)
    session.commit()
    return kullanici


# --- API anahtarları -----------------------------------------------------------------------------------------

# Yeni anahtar üretir. Arkasında o rolde bir API hesabı açılır. Anahtarın kendisi sadece burada döner, saklanmaz.
def api_anahtari_uret(session: Session, ad: str, rol: str, gun: int | None, olusturan: Kullanici | None,
                      ip: str | None = None) -> tuple[ApiAnahtari, str]:
    """olusturan None ise anahtar sunucuda komut satırından üretilmiştir (panel kullanılmayan kurulum)."""
    ad = " ".join(ad.split())
    if not ad or len(ad) > 100:
        raise ValueError("Anahtara 1-100 karakterlik bir ad verin (ör. Muhasebe entegrasyonu).")
    if rol not in API_ANAHTARI_ROLLERI:
        raise ValueError(f"Anahtarın rolü {API_ANAHTARI_ROLLERI} içinden olmalı.")
    if gun is not None and not 1 <= gun <= 3650:
        raise ValueError("Süre 1 ile 3650 gün arasında olmalı ya da süresiz seçilmeli.")
    simdi = datetime.now()
    anahtar = API_ANAHTARI_ON_EKI + secrets.token_urlsafe(32)
    hesap = Kullanici(eposta=f"api-{secrets.token_hex(8)}@api.anahtari", ad=f"API: {ad}", rol=rol,
                      parola_hash=parola_hashle(secrets.token_urlsafe(32)), aktif=True, api_hesabi=True,
                      basarisiz_giris=0, olusturuldu=simdi)
    session.add(hesap)
    session.flush()
    olusturan_id = olusturan.id if olusturan else None
    kayit = ApiAnahtari(ad=ad, kullanici_id=hesap.id, on_ek=anahtar[:10], ozet=_ozet(anahtar), olusturan_id=olusturan_id,
                        olusturuldu=simdi, son_kullanma=simdi + timedelta(days=gun) if gun else None)
    session.add(kayit)
    session.flush()
    denetle(session, "api_anahtari_uretildi", olusturan_id, ip, anahtar_id=kayit.id, ad=ad, rol=rol, gun=gun)
    return kayit, anahtar


# Gelen anahtarın hesabı. Anahtar yok, iptal edilmiş, süresi dolmuş ya da hesap pasifse None.
def api_anahtari_hesabi(session: Session, anahtar: str) -> tuple[ApiAnahtari, Kullanici] | None:
    if not anahtar.startswith(API_ANAHTARI_ON_EKI) or len(anahtar) > 200:
        return None
    kayit = session.scalar(select(ApiAnahtari).where(ApiAnahtari.ozet == _ozet(anahtar)))
    simdi = datetime.now()
    if kayit is None or kayit.iptal is not None or (kayit.son_kullanma and kayit.son_kullanma <= simdi):
        return None
    hesap = session.get(Kullanici, kayit.kullanici_id)
    if hesap is None or not hesap.aktif:
        return None
    return kayit, hesap


# Geçersiz anahtarın neden geçersiz olduğu (denetim kaydı için): yok, iptal, suresi_doldu, hesap_pasif.
def api_anahtari_durumu(session: Session, anahtar: str) -> str:
    kayit = session.scalar(select(ApiAnahtari).where(ApiAnahtari.ozet == _ozet(anahtar)))
    if kayit is None:
        return "yok"
    if kayit.iptal is not None:
        return "iptal"
    if kayit.son_kullanma and kayit.son_kullanma <= datetime.now():
        return "suresi_doldu"
    return "hesap_pasif"


# Anahtarı iptal eder, arkasındaki hesap pasifleşir (belli gün sonra kişisel bilgi silme işi onu da temizler).
def api_anahtari_iptal(session: Session, kayit: ApiAnahtari, yapan: Kullanici | None, ip: str | None = None) -> None:
    if kayit.iptal is not None:
        return
    simdi = datetime.now()
    kayit.iptal = simdi
    hesap = session.get(Kullanici, kayit.kullanici_id)
    if hesap is not None:
        hesap.aktif, hesap.pasif_tarihi = False, simdi
    denetle(session, "api_anahtari_iptal", yapan.id if yapan else None, ip, anahtar_id=kayit.id, ad=kayit.ad)


# --- kullanıcı yönetimi (panelden, admin) ---------------------------------------------------------------

# Linkin SHA-256 özeti (veritabanında linkin kendisi değil bu saklanır).
def _ozet(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Kişinin kullanılmamış bütün linklerini geçersiz yapar.
def _linkleri_iptal_et(session: Session, kullanici_id: int, simdi: datetime) -> None:
    session.execute(update(ParolaLinki).where(ParolaLinki.kullanici_id == kullanici_id,
                                              ParolaLinki.kullanildi.is_(None)).values(kullanildi=simdi))


# Yeni davet/sıfırlama linki üretir.
def parola_linki_olustur(session: Session, kullanici: Kullanici, tur: str, isteyen_id: int | None,
                         simdi: datetime | None = None) -> tuple[str, datetime]:
    """Yeni link oluşturur, commit etmez. Kişinin önceki kullanılmamış linkleri geçersiz olur. Token ve son geçerlilik zamanını döner."""
    simdi = simdi or datetime.now()
    _linkleri_iptal_et(session, kullanici.id, simdi)
    # 32 baytlık tahmin edilemez değer (linkin sonuna eklenir).
    token = secrets.token_urlsafe(32)
    son = simdi + LINK_SURESI[tur]
    session.add(ParolaLinki(kullanici_id=kullanici.id, ozet=_ozet(token), tur=tur, olusturuldu=simdi,
                            son_gecerlilik=son, isteyen_id=isteyen_id))
    return token, son


# Linkteki değere göre geçerli linki bulur (kullanılmamış, süresi dolmamış, kişi aktif).
def parola_linki_bul(session: Session, token: str, simdi: datetime | None = None) -> ParolaLinki | None:
    """Geçerli linki bulur. Geçerli link kullanılmamış, süresi dolmamış ve kişisi aktif olandır."""
    simdi = simdi or datetime.now()
    link = session.scalar(select(ParolaLinki).where(ParolaLinki.ozet == _ozet(token)))
    if link is None or link.kullanildi is not None or link.son_gecerlilik < simdi:
        return None
    kullanici = session.get(Kullanici, link.kullanici_id)
    return link if kullanici is not None and kullanici.aktif else None


# Linkle gelen kişi yeni parolasını belirler.
def parola_belirle(session: Session, token: str, parola: str, ip: str | None = None,
                   simdi: datetime | None = None) -> Kullanici:
    """Linkle yeni parola belirler ve commit eder. Kilit kalkar, parola özeti değiştiği için açık oturumlar da kapanır."""
    simdi = simdi or datetime.now()
    link = parola_linki_bul(session, token, simdi)
    if link is None:
        raise ValueError("Link geçersiz ya da süresi dolmuş. Yöneticinizden yeni link isteyin.")
    kullanici = session.get(Kullanici, link.kullanici_id)
    kullanici.parola_hash = parola_hashle(parola)
    kullanici.basarisiz_giris = 0
    kullanici.kilit_bitis = None
    # Link bir kez kullanılır, bu ve varsa diğer linkler geçersiz.
    _linkleri_iptal_et(session, kullanici.id, simdi)
    denetle(session, "parola_belirlendi", kullanici.id, ip, tur=link.tur)
    session.commit()
    return kullanici


# Giriş yapmış kişinin kendi parolasını değiştirmesi.
def parola_degistir(session: Session, kullanici: Kullanici, eski: str, yeni: str, ip: str | None = None) -> None:
    """Giriş yapmış kişi kendi parolasını değiştirir. Yanlış eski parola, hatalı giriş sayacına girer."""
    if not _dogrula(kullanici.parola_hash, eski):
        _hatali_deneme(session, kullanici, ip, "parola_degistir")
        raise ValueError("Mevcut parola hatalı.")
    kullanici.parola_hash = parola_hashle(yeni)
    denetle(session, "parola_degistirildi", kullanici.id, ip)
    session.commit()


# Admin bir kullanıcıyı pasifleştirir ya da yeniden açar.
def aktiflik_degistir(session: Session, hedef: Kullanici, aktif: bool, yapan: Kullanici, ip: str | None = None) -> None:
    """Kullanıcıyı pasifleştirir ya da yeniden açar, commit eder. Pasif kalan hesap belli gün sonra silinir (pasifleri_sil)."""
    if hedef.silindi:
        raise ValueError("Bu hesap silinmiş, yeniden açılamaz. Kişiyi yeni kullanıcı olarak ekleyin.")
    if hedef.aktif == aktif:
        return
    if not aktif:
        # Kendini pasifleştiremezsin.
        if hedef.id == yapan.id:
            raise ValueError("Kendi hesabınızı pasifleştiremezsiniz.")
        # Son aktif admin pasifleştirilemez (yoksa kimse kullanıcı yönetemez).
        if hedef.rol == "admin" and session.scalar(select(func.count()).select_from(Kullanici).where(
                Kullanici.rol == "admin", Kullanici.aktif, Kullanici.api_hesabi.is_(False))) <= 1:
            raise ValueError("Son aktif yönetici pasifleştirilemez.")
        # Pasifleşen kişinin açık linkleri geçersiz olsun.
        _linkleri_iptal_et(session, hedef.id, datetime.now())
    hedef.aktif = aktif
    hedef.pasif_tarihi = None if aktif else datetime.now()
    denetle(session, "kullanici_aktif" if aktif else "kullanici_pasif", yapan.id, ip, hedef=hedef.eposta)
    session.commit()


# .env'deki MEVZUAT_PASIF_SILME_GUN, yoksa 30.
def pasif_silme_gunu() -> int:
    return int(os.environ.get("MEVZUAT_PASIF_SILME_GUN", PASIF_SILME_GUN))


# Pasif hesabın silineceği gün, silme kapalıysa None.
def silinme_tarihi(kullanici: Kullanici, gun: int) -> datetime | None:
    if kullanici.aktif or kullanici.silindi or not kullanici.pasif_tarihi or gun <= 0:
        return None
    return kullanici.pasif_tarihi + timedelta(days=gun)


# Belli gün pasif kalan hesapları siler, günlük iş çağırır.
def pasifleri_sil(session: Session, gun: int, simdi: datetime | None = None) -> list[str]:
    """Kişisel bilgiler (ad, e-posta, parola, iki adımlı doğrulama sırrı) ve linkleri silinir, commit eder.
    Satırın kendisi kalır, onay ve denetim kayıtları ona bağlı, "Silinmiş kullanıcı" olarak görünür.
    Silinen e-postaları döner."""
    simdi = simdi or datetime.now()
    if gun <= 0:
        return []
    silinenler = []
    for k in session.scalars(select(Kullanici).where(
            Kullanici.aktif.is_(False), Kullanici.silindi.is_(None),
            Kullanici.pasif_tarihi <= simdi - timedelta(days=gun))):
        silinenler.append(k.eposta)
        session.execute(delete(ParolaLinki).where(ParolaLinki.kullanici_id == k.id))
        k.eposta = f"silindi-{k.id}@silindi.invalid"
        k.ad = "Silinmiş kullanıcı"
        k.parola_hash = "!"  # hiçbir parola bununla uyuşmaz
        k.mfa_gizli, k.mfa_aktif, k.mfa_son_adim = None, False, None
        k.kilit_bitis, k.basarisiz_giris = None, 0
        k.silindi = simdi
        denetle(session, "kullanici_silindi", None, None, kullanici_no=k.id, pasif_gun=gun)
    session.commit()
    return silinenler


# Davet ya da sıfırlama mailini hazırlar (düğme + link + süre + uyarı).
def parola_linki_maili(kullanici: Kullanici, link: str, tur: str, son: datetime, isteyen: str | None) -> Mail:
    # Davet ve sıfırlama için farklı konu ve giriş cümlesi.
    if tur == "davet":
        konu = "Mevzuat Takip paneline davet edildiniz"
        giris = (f"{isteyen or 'Yönetici'} sizi Mevzuat Takip paneline {ROL_ADLARI.get(kullanici.rol, kullanici.rol)} "
                 "olarak ekledi. Giriş yapabilmek için aşağıdaki linkle parolanızı belirleyin.")
    else:
        konu = "Mevzuat Takip parola sıfırlama"
        giris = (f"{isteyen or 'Yönetici'} parolanızın sıfırlanmasını başlattı. Aşağıdaki linkle yeni parolanızı "
                 "belirleyin; eski parolanız o zamana kadar geçerli kalır.")
    sure = f"Link {son:%d.%m.%Y %H:%M}'e kadar geçerlidir ve bir kez kullanılabilir."
    uyari = "Bu isteği beklemiyorsanız linke tıklamayın ve yöneticinize haber verin."
    # Mailin HTML hali. html.escape, isim gibi değerlerde < > işareti varsa zararsız hale getir.
    govde = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.5'>"
        f"<p>Merhaba {html.escape(kullanici.ad)},</p><p>{html.escape(giris)}</p>"
        f"<p><a href='{html.escape(link)}' style='display:inline-block;background:#2f5d8a;color:#ffffff;"
        "text-decoration:none;font-weight:bold;padding:10px 18px;border-radius:4px;'>Parolamı belirle</a></p>"
        f"<p style='font-size:12px;color:#6b6b63'>{html.escape(link)}<br>{html.escape(sure)}<br>{html.escape(uyari)}</p>"
        "</div>"
    )
    # Düz metin hali.
    metin = f"Merhaba {kullanici.ad},\n\n{giris}\n\n{link}\n\n{sure}\n{uyari}"
    return Mail(konu=konu, html=govde, metin=metin, alicilar=[kullanici.eposta])

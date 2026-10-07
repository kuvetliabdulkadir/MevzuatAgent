"""Mail gönderimi. Rapor kodu sadece `MailGonderici` arayüzünü bilir. SMTP sağlayıcısı değişince
(şimdi Gmail, sonra şirket sunucusu) sadece ayarlar değişir.

Ayarlar ortam değişkenlerinden okunur.
  MEVZUAT_SMTP_HOST      yoksa mail gönderilmez, giden_mailler klasörüne dosya olarak yazılır
  MEVZUAT_SMTP_PORT      varsayılan 587 (STARTTLS)
  MEVZUAT_SMTP_KULLANICI
  MEVZUAT_SMTP_SIFRE     Gmail için normal şifre değil "Uygulama Şifresi" girilir, 2 adımlı doğrulama gerekir
  MEVZUAT_SMTP_GONDEREN  varsayılan olarak kullanıcı adı
"""
# Mail atma işi burada. İki yol var, gerçekten SMTP ile göndermek ya da (ayar yoksa) dosyaya yazmak.

import logging
import os
import re
# smtplib, mail sunucusuyla konuşmak için Python'un kendi kütüphanesi. ssl, bağlantıyı şifrelemek için.
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime
# EmailMessage, gerçek bir mail dosyası (başlıklar, gövde, ekler) oluşturmak için.
from email.message import EmailMessage
# formatdate, maildeki tarih satırı. make_msgid, her maile benzersiz bir kimlik (Message-ID) üretir.
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


# Maile eklenecek bir dosya (PDF gibi).
@dataclass(frozen=True)
class Ek:
    dosya_adi: str
    # Dosyanın kendisi (ham baytlar).
    icerik: bytes
    mime: str  # "application/pdf", "text/html" …


# Gönderilecek bir mailin bütün parçaları.
@dataclass
class Mail:
    konu: str
    # Mailin renkli/biçimli hali.
    html: str
    metin: str  # HTML gösteremeyen istemciler için düz metin
    alicilar: list[str]
    ekler: list[Ek] = field(default_factory=list)
    message_id: str | None = None  # verilirse sabit, aynı rapor iki kez giderse alıcıda tek mail görünür


# "Mail gönderebilen her şey" kuralı, gonder(mail) fonksiyonu olsun yeter. SMTP de dosya yazıcı da buna uyuyor.
class MailGonderici(Protocol):
    def gonder(self, mail: Mail) -> None: ...


# Mail nesnesinden gerçek bir e-posta mesajı oluşturur.
def mesaj_olustur(mail: Mail, gonderen: str) -> EmailMessage:
    msg = EmailMessage()
    # Başlık satırları, konu, kimden, kime, tarih, kimlik.
    msg["Subject"] = mail.konu
    msg["From"] = gonderen
    msg["To"] = ", ".join(mail.alicilar)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = mail.message_id or make_msgid(domain="mevzuat-takip")
    # Önce düz metin hali, sonra "alternatif" olarak HTML hali (mail programı uygun olanı gösterir).
    msg.set_content(mail.metin)
    msg.add_alternative(mail.html, subtype="html")
    # Ekleri tek tek ekle. "application/pdf" yazısı ana tür ve alt tür diye ikiye ayrılıyor.
    for ek in mail.ekler:
        ana, alt = ek.mime.split("/", 1)
        msg.add_attachment(ek.icerik, maintype=ana, subtype=alt, filename=ek.dosya_adi)
    return msg


# Gerçek mail sunucusu (SMTP) üzerinden gönderen sınıf.
class SmtpGonderici:
    # Sunucu bilgilerini sakla. deneme, bağlantı sorununda kaç kez deneneceği.
    def __init__(self, host: str, port: int, kullanici: str, sifre: str, gonderen: str, deneme: int = 3):
        self.host, self.port = host, port
        self.kullanici, self.sifre = kullanici, sifre
        self.gonderen = gonderen
        self.deneme = deneme

    def gonder(self, mail: Mail) -> None:
        msg = mesaj_olustur(mail, self.gonderen)
        # 1'den 3'e kadar dene.
        for i in range(1, self.deneme + 1):
            try:
                # Sunucuya bağlan (60 saniye zaman aşımı).
                smtp = smtplib.SMTP(self.host, self.port, timeout=60)
                try:
                    # Bağlantıyı şifreli hale getir, giriş yap, maili gönder.
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.login(self.kullanici, self.sifre)
                    smtp.send_message(msg)
                finally:
                    # QUIT'teki hata maili geri almaz. `with` bloğu onu yükseltiyordu, o zaman gönderilmiş
                    # mail "gönderilemedi" sayılıp TEKRAR gönderilirdi.
                    # Kibarca çıkmayı dene, olmazsa bağlantıyı zorla kapat.
                    try:
                        smtp.quit()
                    except (smtplib.SMTPException, OSError):
                        smtp.close()
                # Gönderildi, çık.
                return
            except smtplib.SMTPAuthenticationError:
                raise  # şifre yanlışsa tekrar denemenin anlamı yok
            # Başka bir mail/ağ hatası.
            except (smtplib.SMTPException, OSError) as e:
                # Son denemeyse hatayı yukarı fırlat.
                if i == self.deneme:
                    raise
                # Değilse logla, biraz bekle (5, 10 sn) ve tekrar dene.
                log.warning("Mail gönderilemedi (deneme %d/%d): %r", i, self.deneme, e)
                time.sleep(5 * i)


# Mail sunucusu ayarlanmamışsa kullanılan sahte gönderici, maili göndermek yerine klasöre dosya olarak yazar.
class DosyaGonderici:
    """Geliştirme için. Maili göndermez, mail istemcisiyle açılabilen .eml dosyası ve .html dosyası olarak yazar."""

    def __init__(self, klasor: Path):
        self.klasor = klasor

    def gonder(self, mail: Mail) -> None:
        # Klasör yoksa oluştur.
        self.klasor.mkdir(parents=True, exist_ok=True)
        # Mikrosaniye, aynı saniyede yazılan iki mail (ör. onay bildirimi + dağıtım) birbirini ezmesin.
        ad = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # .eml dosyası (Outlook/Thunderbird ile açılır) ve tarayıcıda bakmak için .html dosyası yaz.
        (self.klasor / f"{ad}.eml").write_bytes(bytes(mesaj_olustur(mail, "mevzuat-takip@localhost")))
        (self.klasor / f"{ad}.html").write_text(mail.html, encoding="utf-8")
        log.info("Mail dosyaya yazıldı: %s", self.klasor / f"{ad}.eml")


# .env'deki panel adresini okur ve biçimini kontrol eder. Maildeki "Onay paneline git" linki bundan kurulur.
def panel_adresi_ayardan() -> str | None:
    """Maillerdeki linkler için panelin adresi, örneğin https://mevzuat.firma.com.tr. Boşsa link konmaz.
    Linkler isteğin Host başlığından değil buradan kurulur, böylece sahte Host ile başka siteye giden sıfırlama linki üretilemez."""
    adres = os.environ.get("MEVZUAT_PANEL_ADRESI", "").strip()
    # Adres yazılmış ama "http(s)://alan.adi(:port)(/yol)" kalıbına uymuyorsa programı anlaşılır mesajla durdur.
    if adres and not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?", adres):
        raise SystemExit(f"MEVZUAT_PANEL_ADRESI hatalı ({adres!r}); örnek: https://mevzuat.firma.com.tr")
    # Sondaki "/" işaretini at, boşsa None ver.
    return adres.rstrip("/") or None


# Ayarlara bakıp hangi göndericinin kullanılacağına karar verir.
def gonderici_ayardan() -> MailGonderici:
    host = os.environ.get("MEVZUAT_SMTP_HOST")
    # Mail sunucusu yazılmamışsa, dosyaya yaz.
    if not host:
        return DosyaGonderici(Path("giden_mailler"))
    # Yazılmışsa, SMTP göndericisini .env'deki bilgilerle kur. Gönderen yazılmadıysa kullanıcı adı kullanılır.
    kullanici = os.environ["MEVZUAT_SMTP_KULLANICI"]
    return SmtpGonderici(
        host=host,
        port=int(os.environ.get("MEVZUAT_SMTP_PORT", "587")),
        kullanici=kullanici,
        sifre=os.environ["MEVZUAT_SMTP_SIFRE"],
        gonderen=os.environ.get("MEVZUAT_SMTP_GONDEREN", kullanici),
    )

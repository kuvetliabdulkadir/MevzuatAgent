"""Komutları elle çalıştırmak için. Komutlar ve ne yaptıkları aşağıda.

    uv run python -m mevzuat.cli gunluk              # zamanlayıcının çalıştırdığı iş, tarar, rapor çıkarır, sağlığı kontrol eder
    uv run python -m mevzuat.cli calistir            # tam tarama, checkpoint'ten bugüne kadar, DB'ye yazar
    uv run python -m mevzuat.cli ilgili --gun 7      # son 7 günün ilgili kayıtları, rapor önizlemesi gibi
    uv run python -m mevzuat.cli onizle              # rapor önizlemesini rapor_onizleme.html dosyasına yazar, bir şey işaretlemez
    uv run python -m mevzuat.cli panel               # onay paneli, 127.0.0.1:8000 adresinde, gönderim sadece buradan
    uv run python -m mevzuat.cli zamanlayici         # sürekli çalışır, saatler panelden gelir (varsayılan 06:30 ve 18:00), saati gelince gunluk'u başlatır
    uv run python -m mevzuat.cli kullanici-ekle --eposta a@firma.com --ad "Ad Soyad" --rol onaylayici
    uv run python -m mevzuat.cli kullanici-pasif --eposta a@firma.com
    uv run python -m mevzuat.cli dene masak --gun 30 # bir kaynağı dener, DB'ye yazmaz
    uv run python -m mevzuat.cli rg --tarih 2025-12-31 --ilan   # tek günün fihristi, DB'ye yazmaz
    uv run python -m mevzuat.cli ayar-disa-aktar     # DB'deki kaynak ve konu tanımlarını disa_aktarim klasörüne toml olarak yazar
    uv run python -m mevzuat.cli ayar-ice-aktar --kaynaklar k.toml --konular c.toml   # düzenlenmiş toml dosyalarını DB'ye aktarır

Raporun kime gideceği ortam değişkeninde değil, panelden yönetilen alıcı gruplarında durur.

Ortam değişkenleri aşağıda.
    MEVZUAT_DB_URL           varsayılan sqlite:///mevzuat.db
    MEVZUAT_KONULAR          varsayılan config/konular.toml, sadece ilk kurulumda ve tablo boşsa DB'ye bir kez aktarılır (tanimlar.py)
    MEVZUAT_KAYNAKLAR        varsayılan config/kaynaklar.toml, o da sadece ilk kurulumda bir kez aktarılır
    MEVZUAT_ADMIN_ALICILARI  uyarı ve haftalık nabız maillerinin gideceği adresler
    MEVZUAT_GIZLI_ANAHTAR    panel oturum anahtarı, en az 32 karakter
    MEVZUAT_EK_EKLE          1 ya da 0, orijinal PDF'ler eklensin mi, varsayılan 1
    MEVZUAT_PASIF_SILME_GUN  bu kadar gün pasif kalan hesap silinir, 0 ise hiç silinmez, varsayılan 30
    MEVZUAT_SAKLAMA_GUN      bitmiş raporlar ve denetim kaydı bu kadar gün sonra silinir, 0 ise hiç silinmez, varsayılan 30
    SMTP ayarları için mevzuat/mail.py dosyasına bakın.
"""

# Komut satırı aracı, "python -m mevzuat.cli <komut>" ile çalışan bütün komutlar burada (gunluk, panel, zamanlayici...).
# Docker da sunucu da programı bu dosya üzerinden başlatıyor.
# argparse, komut satırındaki "--gun 7" gibi argümanları okur. getpass, parolayı ekrana yansıtmadan sorar.
import argparse
import getpass
import logging
import os
import smtplib
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# load_dotenv, .env dosyasındaki ayarları ortam değişkeni olarak yükler. FileLock, aynı anda iki tarama çalışmasın diye dosya kilidi.
from dotenv import load_dotenv
from filelock import FileLock, Timeout
from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat import gunluk, pipeline, rapor, surum, tanimlar, zamanlama
from mevzuat.db import IzlenenMevzuat, Kayit, MetinSurumu, make_engine, migrate
from mevzuat.filtre import is_kollari
from mevzuat.http import make_client
from mevzuat.mail import DosyaGonderici, Mail
from mevzuat.sources import resmi_gazete
from mevzuat.web import guvenlik
from mevzuat.zamanlama import (  # noqa: F401 (eski içe aktarımlar, testler)
    calisma_saatleri,
    sonraki_calisma,
)


# Ayar dosyasının yolu, dosya yoksa anlaşılır mesajla dur.
def _ayar_dosyasi(degisken: str, varsayilan: str) -> Path:
    yol = Path(os.environ.get(degisken, varsayilan))
    if not yol.exists():
        raise SystemExit(
            f"Ayar dosyası bulunamadı: {yol.resolve()}\n"
            f"Komutu proje klasöründen çalıştırın ya da {degisken} ile tam yol verin."
        )
    return yol


# Veritabanını hazırlar ve kaynak, konu, takip listesi tanımlarını okur (her komutun ortak başlangıcı).
def _tanimlar(engine, izlenenler=None):
    """Kaynak ve konu tanımlarını DB'den okur. Tablo boşsa önce ayar dosyalarından aktarılır.
    Takip listesini de döner, konusu panelde yeniden adlandırıldıysa şimdiki ada çevrilmiş olarak."""
    tanimlar.hazirla(engine)
    with Session(engine) as session:
        return (tanimlar.kaynaklari_oku(session), tanimlar.konulari_oku(session),
                tanimlar.izlenenleri_esle(session, izlenenler))


# Takip listesi dosyasını okur.
def _izlenenler():
    return surum.izlenenleri_yukle(_ayar_dosyasi("MEVZUAT_IZLENEN", "config/izlenen_mevzuat.toml"))


# "calistir" komutu, sadece tarama yapar (rapor/mail yok).
def calistir_komutu(args: argparse.Namespace) -> None:
    # Ayarlar taramadan önce okunur, hatalı ayar varsa hiçbir şey taranmadan durulur.
    engine = make_engine()
    kaynaklar, konular, izlenenler = _tanimlar(engine, _izlenenler())
    # Veritabanı oturumu ve internet istemcisini aç, taramayı çalıştır, sonucu ekrana yaz.
    with Session(engine) as session, make_client() as client:
        calisma = pipeline.calistir(session, client, kaynaklar, konular, izlenenler=izlenenler)
        print(f"Çalışma #{calisma.id}: {calisma.durum}")
        print(calisma.ozet)
        if calisma.hata:
            print("HATALAR:\n" + calisma.hata)


# Panelden değişen ayarlar (mail, panel adresi, süreler), panelde boş olan .env'den. Veritabanına ulaşılamazsa sadece .env.
def _ayarlar() -> dict:
    from mevzuat import panel_ayarlari

    try:
        with Session(make_engine()) as session:
            return panel_ayarlari.etkin(session)
    except Exception as e:
        logging.getLogger(__name__).warning("Panel ayarları okunamadı, .env kullanılıyor: %r", e)
        return {alan.ad: panel_ayarlari.env_degeri(alan) for alan in panel_ayarlari.ALANLAR}


# Ayarlardan mail göndericisi.
def _gonderici(ayarlar: dict):
    from mevzuat import panel_ayarlari

    return panel_ayarlari.gonderici(ayarlar)


# "gunluk" komutu, zamanlayıcının her gün çalıştırdığı asıl iş.
def gunluk_komutu(args: argparse.Namespace) -> None:
    """Zamanlayıcının çağırdığı komut. Önceki çalışma sürüyorsa hiçbir şey yapmadan çıkar."""
    # Kilit dosyası, başka bir tarama sürüyorsa bu çalışma hiçbir şey yapmadan çıkar.
    kilit = FileLock(os.environ.get("MEVZUAT_KILIT", "mevzuat.lock"))
    try:
        # timeout=0, kilit boş değilse bekleme, hemen vazgeç.
        kilit.acquire(timeout=0)
    except Timeout:
        print("Başka bir çalışma sürüyor, bu çalışma atlandı.")
        return
    try:
        # Ayarlar panelden (panelde boş olanlar .env'den).
        izlenenler = _izlenenler()
        a = _ayarlar()
        ayarlar = gunluk.Ayarlar(
            admin_alicilari=a["admin_alicilari"] or [],
            ek_ekle=a["ek_ekle"],
            nabiz_gunu=a["nabiz_gunu"],
            panel_adresi=a["panel_adresi"],
            pasif_silme_gun=a["pasif_silme_gun"],
            saklama_gun=a["saklama_gun"],
        )
        # Panelden "Şimdi tara" ile başlatıldıysa zamanlayıcı istek numarasını bu değişkende verir.
        istek = os.environ.get(zamanlama.ISTEK_DEGISKENI)  # zamanlayıcı panelden istenen tarama için verir
        engine = make_engine()
        kaynaklar, konular, izlenenler = _tanimlar(engine, izlenenler)
        # Günlük işi çalıştır.
        with Session(engine) as session, make_client() as client:
            sonuc = gunluk.calistir(session, client, kaynaklar, konular, _gonderici(a), ayarlar,
                                    izlenenler=izlenenler, istek_id=int(istek) if istek else None)
            # Oturum açıkken yazdırılmalı, commit'ten sonra nesneler DB'den yeniden okunur.
            rapor_bilgisi = f"#{sonuc.rapor.id} onaya sunuldu" if sonuc.rapor else "onaya sunulacak ilgili kayıt yok"
            print(f"Çalışma #{sonuc.calisma.id}: {sonuc.calisma.durum}, rapor: {rapor_bilgisi}, "
                  f"sorun: {len(sonuc.sorunlar)}")
        # Sorun varsa çıkış kodu 1 olsun (zamanlayıcı logunda görünür).
        if sonuc.sorunlar:
            sys.exit(1)  # systemd "failed" görsün, `systemctl status` ve journal'da iz kalır
    # Ne olursa olsun kilidi bırak.
    finally:
        kilit.release()


# "ilgili" komutu, son N günün ilgili kayıtlarını ekrana listeler.
def ilgili_komutu(args: argparse.Namespace) -> None:
    engine = make_engine()
    migrate(engine)
    sinir = date.today() - timedelta(days=args.gun)
    with Session(engine) as session:
        kayitlar = session.scalars(
            select(Kayit).where(Kayit.ilgili, Kayit.yayin_tarihi >= sinir).order_by(Kayit.yayin_tarihi)
        ).all()
        print(f"Son {args.gun} gün — {len(kayitlar)} ilgili kayıt\n")
        for k in kayitlar:
            print(f"■ {k.baslik}")
            print(f"  Etkilenen: {', '.join(k.is_kollari)}")
            print(f"  Konu: {', '.join(f'{ad} ({', '.join(kel)})' for ad, kel in k.eslesmeler.items())}")
            print(f"  İçerik: {k.icerik_durumu}")
            if k.icerik:
                print("  " + k.icerik[:400].replace("\n", "\n  ") + ("…" if len(k.icerik) > 400 else ""))
            print(f"  Kaynak: {k.kaynakca}\n")


# "onizle" komutu, raporun nasıl görüneceğini dosyaya yazar, hiçbir şeyi işaretlemez ya da göndermez.
def onizle_komutu(args: argparse.Namespace) -> None:
    """Onaya sunulmamış kayıtlardan raporun nasıl görüneceğini dosyaya yazar. Hiçbir şey işaretlenmez.
    Gönderim yalnızca onay panelinden yapılır (onayı atlayan bir komut bilerek yok)."""
    engine = make_engine()
    migrate(engine)
    ek_ekle = _ayarlar()["ek_ekle"]
    with Session(engine) as session, make_client() as client:
        kayitlar = rapor.bekleyen_kayitlar(session)
        if not kayitlar:
            print("Onaya sunulmamış ilgili kayıt yok.")
            return
        mail = rapor.rapor_olustur(kayitlar, date.today(), client, ek_ekle,
                                   aciklamalar=rapor.konu_aciklamalari(session))
        Path("rapor_onizleme.html").write_text(mail.html, encoding="utf-8")
        Path("rapor_onizleme.txt").write_text(mail.metin, encoding="utf-8")
        print(mail.konu)
        print(f"rapor_onizleme.html / .txt yazıldı, {len(mail.ekler)} ek (kayıtlar işaretlenmedi)")


# Yeni kullanıcının parolasını alır, önce ortam değişkeninden, yoksa ekranda iki kez sorar.
def _yeni_parola() -> str:
    # Otomasyon için ortamdan, yoksa ekrana yansımadan iki kez sorulur. Komut satırı argümanı olarak
    # ALINMAZ, argümanlar shell geçmişine ve süreç listesine düşer.
    if parola := os.environ.get("MEVZUAT_YENI_PAROLA"):
        return parola
    birinci = getpass.getpass("Parola (en az 12 karakter): ")
    if birinci != getpass.getpass("Parola (tekrar): "):
        raise SystemExit("Parolalar eşleşmiyor.")
    return birinci


# "kullanici-ekle" komutu, komut satırından kullanıcı ekler (ilk admin böyle eklenir).
def kullanici_ekle_komutu(args: argparse.Namespace) -> None:
    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        try:
            k = guvenlik.kullanici_ekle(session, args.eposta, args.ad, args.rol, _yeni_parola())
        except ValueError as e:
            raise SystemExit(str(e)) from e
        print(f"Eklendi: {k.eposta} ({k.rol})")


# "api-anahtari-uret" komutu, panel kullanılmayan kurulumda API anahtarını sunucudan üretir. Anahtar sadece burada bir kez basılır.
def api_anahtari_uret_komutu(args: argparse.Namespace) -> None:
    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        try:
            kayit, anahtar = guvenlik.api_anahtari_uret(session, args.ad, args.rol, args.gun, None)
        except ValueError as e:
            raise SystemExit(str(e)) from e
        session.commit()
        sure = f"{args.gun} gün" if args.gun else "süresiz"
        print(f"Anahtar #{kayit.id} '{kayit.ad}' ({args.rol}, {sure}). Bir daha gösterilmeyecek, şimdi kopyalayın:")
        print(anahtar)


# "api-anahtari-listele" komutu, anahtarları listeler (anahtarların kendisi değil, ilk harfleri).
def api_anahtari_listele_komutu(args: argparse.Namespace) -> None:
    from mevzuat.db import ApiAnahtari, Kullanici

    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        simdi = datetime.now()
        for k in session.scalars(select(ApiAnahtari).order_by(ApiAnahtari.id)):
            hesap = session.get(Kullanici, k.kullanici_id)
            durum = "iptal" if k.iptal else "süresi doldu" if k.son_kullanma and k.son_kullanma <= simdi else "aktif"
            son = f"{k.son_kullanma:%d.%m.%Y}" if k.son_kullanma else "süresiz"
            kullanim = f"{k.son_kullanim:%d.%m.%Y %H:%M}" if k.son_kullanim else "hiç kullanılmadı"
            print(f"#{k.id}  {k.on_ek}…  {k.ad}  rol={hesap.rol if hesap else '-'}  {durum}  geçerlilik={son}  son kullanım={kullanim}")


# "api-anahtari-iptal" komutu, anahtarı iptal eder, anahtar hemen çalışmaz olur.
def api_anahtari_iptal_komutu(args: argparse.Namespace) -> None:
    from mevzuat.db import ApiAnahtari

    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        kayit = session.get(ApiAnahtari, args.id)
        if kayit is None:
            raise SystemExit(f"#{args.id} numaralı anahtar yok.")
        guvenlik.api_anahtari_iptal(session, kayit, None)
        session.commit()
        print(f"Anahtar #{kayit.id} '{kayit.ad}' iptal edildi.")


# "kullanici-pasif" komutu, kullanıcıyı pasifleştirir.
def kullanici_pasif_komutu(args: argparse.Namespace) -> None:
    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        k = guvenlik.kullanici_bul(session, args.eposta)
        if k is None:
            raise SystemExit(f"{args.eposta} bulunamadı.")
        k.aktif = False
        k.pasif_tarihi = k.pasif_tarihi or datetime.now()
        guvenlik.denetle(session, "kullanici_pasif", k.id, eposta=k.eposta)
        session.commit()
        print(f"Pasifleştirildi: {k.eposta} (açık oturumu da bir sonraki istekte kapanır)")


# "mfa-sifirla" komutu, telefonunu kaybeden kullanıcının iki adımlı doğrulamasını sıfırlar.
def mfa_sifirla_komutu(args: argparse.Namespace) -> None:
    """Telefonunu kaybeden kullanıcının MFA'sını siler, kullanıcı bir sonraki girişte yeniden kurar.
    Kimliğini doğrulamadan (örneğin telefonla arayarak) yapmayın. Panelden yapılamaması bilerek böyle."""
    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        k = guvenlik.kullanici_bul(session, args.eposta)
        if k is None:
            raise SystemExit(f"{args.eposta} bulunamadı.")
        guvenlik.mfa_sifirla(session, k)
        print(f"MFA sıfırlandı: {k.eposta} (bir sonraki girişte yeniden kuracak)")


# "mail-dene" komutu, mail ayarları çalışıyor mu diye tek adrese deneme maili atar.
def mail_dene_komutu(args: argparse.Namespace) -> None:
    """Mail ayarını (panelden, panelde boşsa .env'den) denemek için tek bir adrese deneme maili atar, veritabanına yazmaz."""
    a = _ayarlar()
    gonderici = _gonderici(a)
    # Mail sunucusu ayarlı değilse uyar.
    if isinstance(gonderici, DosyaGonderici):
        print("SMTP sunucusu boş: mail gönderilmeyecek, giden_mailler/ klasörüne yazılacak.")
    # Panel adresi yoksa uyar (onay mailinde düğme olmaz).
    if not a["panel_adresi"]:
        print("UYARI: Panel adresi boş. Onay maillerinde \"Onay paneline git\" düğmesi olmaz, "
              "davet/parola linkleri gönderilemez. Panelde Ayarlar sayfasından ya da .env'den girin.")
    simdi = datetime.now().strftime("%d.%m.%Y %H:%M")
    mail = Mail(
        konu=f"Mevzuat Takip — deneme maili ({simdi})",
        html=f"<p>Bu bir deneme mailidir ({simdi}). Mail ayarları çalışıyor; bir şey yapmanıza gerek yok.</p>",
        metin=f"Bu bir deneme mailidir ({simdi}). Mail ayarları çalışıyor; bir şey yapmanıza gerek yok.",
        alicilar=[args.adres],
    )
    # Gönderim süresini ölçmek için başlangıç zamanı.
    basla = time.monotonic()
    try:
        gonderici.gonder(mail)
    # Giriş reddedildiyse Gmail uygulama şifresi hakkında açıklayıcı mesaj ver.
    except smtplib.SMTPAuthenticationError:
        raise SystemExit("SMTP girişi reddedildi. Gmail'de normal şifre çalışmaz: 2 Adımlı Doğrulama açıkken "
                         "\"Uygulama şifreleri\"nden alınan 16 haneli şifreyi MEVZUAT_SMTP_SIFRE'ye yazın.")
    except (smtplib.SMTPException, OSError) as e:
        raise SystemExit(f"Mail gönderilemedi: {e!r}")
    print(f"Gönderildi: {args.adres} ({time.monotonic() - basla:.1f} sn). Gelen kutusunu (ve spam'i) kontrol edin.")


# "gonderim-durum" komutu, gitti mi gitmedi mi belli olmayan bir dağıtım mailini admin elle çözer.
def gonderim_durum_komutu(args: argparse.Namespace) -> None:
    """GONDERILIYOR durumunda takılı kalmış dağıtım mailini yönetici elle çözer, alıcılara sorup karar verir.
    Sadece o mail etkilenir, aynı raporun diğer alıcılarına giden mailler tekrar gitmez."""
    from mevzuat.db import Gonderim, Rapor

    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        g = session.get(Gonderim, args.id)
        if g is None or g.durum != "GONDERILIYOR":
            raise SystemExit(f"Gönderim #{args.id} GONDERILIYOR durumunda değil ({g.durum if g else 'yok'}).")
        r = session.get(Rapor, g.rapor_id)
        # "--gonderildi", mail gitmiş, öyle işaretle.
        if args.gonderildi:
            g.durum, g.gonderildi, g.hata = "GONDERILDI", g.gonderim_denemesi, None
            guvenlik.denetle(session, "elle_gonderildi_isaretlendi", rapor_id=r.id, gonderim_id=g.id)
            session.commit()
            rapor.raporu_tamamla(session, r)
            print(f"Gönderim #{g.id} GONDERILDI olarak işaretlendi. Rapor #{r.id}: {r.durum}")
        # "--tekrar-gonder", mail gitmemiş, tekrar gönder.
        else:
            g.durum, g.hata = "BEKLIYOR", "Yönetici tekrar gönderim istedi"
            guvenlik.denetle(session, "elle_tekrar_gonderim", rapor_id=r.id, gonderim_id=g.id)
            session.commit()
            with make_client() as client:
                a = _ayarlar()
                rapor.dagit(session, client, _gonderici(a), r, a["ek_ekle"])
            session.refresh(g)
            print(f"Gönderim #{g.id}: {g.durum}{' — ' + g.hata if g.hata else ''}. Rapor #{r.id}: {r.durum}")


# "izlenen" komutu, güncel metni takip edilen mevzuatı listeler.
def izlenen_komutu(args: argparse.Namespace) -> None:
    """Güncel metni takip edilen mevzuatı listeler. Neden takipte olduğu, en son ne zaman bakıldığı ve kaç sürümü olduğu görünür."""
    from sqlalchemy import func

    engine = make_engine()
    migrate(engine)
    with Session(engine) as session:
        sayilar = dict(session.execute(
            select(MetinSurumu.anahtar, func.count()).group_by(MetinSurumu.anahtar)
        ).all())
        izlenenler = session.scalars(select(IzlenenMevzuat).order_by(IzlenenMevzuat.neden, IzlenenMevzuat.ad)).all()
        if not izlenenler:
            print("Henüz takipte mevzuat yok (ilk günlük çalışmada config/izlenen_mevzuat.toml'dan eklenir).")
        for i in izlenenler:
            kontrol = i.son_kontrol.strftime("%d.%m.%Y %H:%M") if i.son_kontrol else "hiç"
            print(f"{i.anahtar:>14}  {i.neden:<8}  son kontrol: {kontrol:<16}  sürüm: {sayilar.get(i.anahtar, 0)}  {i.ad}")


# "panel" komutu, web panelini (FastAPI) başlatır.
def panel_komutu(args: argparse.Namespace) -> None:
    import uvicorn

    # Varsayılan 127.0.0.1, panel sadece bu makineden erişilir. Şirket ağına açmak bilinçli bir
    # karar olmalı (--host 0.0.0.0 ya da önüne HTTPS'li bir ters vekil sunucu).
    # Ters vekil arkasında (MEVZUAT_HTTPS=1) gerçek istemci IP'si X-Forwarded-For başlığında gelir,
    # bu başlığa SADECE aynı makinedeki vekilden (127.0.0.1) gelirse güvenilir. Vekil yoksa başlık
    # yok sayılır, dışarıdan herkes sahte IP yazabilir ve denetim kaydındaki IP anlamsızlaşır.
    # Docker'da vekil container dışından (köprü ağ geçidinden) gelir, MEVZUAT_VEKIL_IPLERI ile verilir.
    # HTTPS'li vekilin arkasındaysa gerçek ziyaretçi IP'si başlıktan okunur.
    vekil_arkasinda = os.environ.get("MEVZUAT_HTTPS") == "1"
    # uvicorn, Python web sunucusu. factory=True, uygulamayı ayardan_olustur fonksiyonu kursun.
    uvicorn.run("mevzuat.web:ayardan_olustur", factory=True, host=args.host, port=args.port,
                proxy_headers=vekil_arkasinda,
                forwarded_allow_ips=os.environ.get("MEVZUAT_VEKIL_IPLERI", "127.0.0.1"))


# "zamanlayici" komutu, sürekli çalışan zamanlayıcıyı başlatır.
def zamanlayici_komutu(args: argparse.Namespace) -> None:
    """Sürekli çalışır. Docker'da `zamanlayici` container'ı bunu çalıştırır.
    Saatler DB'de durur ve panelden değişir, ayrıntısı zamanlama.py dosyasında."""
    engine = make_engine()
    migrate(engine)
    zamanlama.Zamanlayici(engine).calis(hemen=args.hemen)


# "rg" komutu, bir günün Resmî Gazete fihristini ekrana yazar (veritabanına yazmaz).
def rg_komutu(args: argparse.Namespace) -> None:
    tarih = date.fromisoformat(args.tarih) if args.tarih else date.today()
    with make_client() as client:
        sonuc = resmi_gazete.gunun_kayitlari(client, tarih, ilanlar_dahil=args.ilan)

    if sonuc.durum is resmi_gazete.Durum.HENUZ_YOK:
        print(f"{tarih} tarihli Resmî Gazete bulunamadı (henüz yayımlanmamış olabilir).")
        return
    if sonuc.durum is resmi_gazete.Durum.YAYIMLANMADI:
        print(f"{tarih} tarihinde Resmî Gazete yayımlanmadı.")
        return

    kayitlar = sonuc.kayitlar
    print(f"Resmî Gazete {tarih} — Sayı {kayitlar[0].sayi} — {len(kayitlar)} kayıt")
    onceki = None
    # Bölüm/alt başlık değiştikçe başlık satırı yaz.
    for k in kayitlar:
        grup = (k.mukerrer, k.bolum, k.alt_baslik)
        if grup != onceki:
            mukerrer = f"[{k.mukerrer}. Mükerrer] " if k.mukerrer else ""
            print(f"\n## {mukerrer}{k.bolum} / {k.alt_baslik}")
            onceki = grup
        print(f"- {k.baslik}\n  {k.url}")


# "dene" komutu, bir kaynağı veritabanına yazmadan dener, konulara takılanları ★ ile gösterir.
def dene_komutu(args: argparse.Namespace) -> None:
    kaynaklar, konular, _ = _tanimlar(make_engine())
    kaynaklar = {k.ad: k for k in kaynaklar}
    if args.kaynak not in kaynaklar:
        raise SystemExit(f"Bilinmeyen kaynak: {args.kaynak!r}. Tanımlı kaynaklar: {', '.join(kaynaklar)}")
    kaynak = kaynaklar[args.kaynak]
    bugun, simdi = date.today(), datetime.now()
    checkpoint = kaynak.geriye_checkpoint(bugun, simdi, args.gun)
    with make_client() as client:
        for adim in kaynak.tara(client, checkpoint, bugun, simdi):
            for t in adim.kayitlar:
                eslesen = pipeline.baslik_eslesmeleri(kaynak, t.baslik, konular)
                isaret = f"  ★ {', '.join(is_kollari(eslesen, konular))}" if eslesen else ""
                print(f"- {t.kaynakca}{isaret}")
            if adim.bilgi:
                print(f"  ({adim.bilgi})")


# "ayar-disa-aktar" komutu, kaynak ve konu tanımlarını toml dosyalarına yazar.
def ayar_disa_aktar_komutu(args: argparse.Namespace) -> None:
    engine = make_engine()
    tanimlar.hazirla(engine)
    with Session(engine) as session:
        kaynak_metni, konu_metni = tanimlar.disa_aktar(session)
    klasor = Path(args.klasor)
    klasor.mkdir(parents=True, exist_ok=True)
    (klasor / "kaynaklar.toml").write_text(kaynak_metni, encoding="utf-8")
    (klasor / "konular.toml").write_text(konu_metni, encoding="utf-8")
    print(f"Yazıldı: {klasor / 'kaynaklar.toml'}, {klasor / 'konular.toml'}")


# "ayar-ice-aktar" komutu, düzenlenmiş toml dosyalarını veritabanına uygular.
def ayar_ice_aktar_komutu(args: argparse.Namespace) -> None:
    """Panelde düzenleme ekranı yokken ya da şirketin onayladığı dosyayı geri yüklerken. Denetime yazılır."""
    engine = make_engine()
    tanimlar.hazirla(engine)
    with Session(engine) as session:
        try:
            ozet = tanimlar.ice_aktar(session, Path(args.kaynaklar), Path(args.konular),
                                      tanimlar.izlenenleri_esle(session, _izlenenler()))
        except ValueError as e:
            raise SystemExit(f"İçe aktarılmadı, veritabanı değişmedi: {e}") from e
        # Sadece gerçekten değişenleri al, varsa denetim kaydına yaz.
        degisti = {k: v for k, v in ozet.items() if v}
        if degisti:
            guvenlik.denetle(session, "ayar_ice_aktar", kaynaklar=args.kaynaklar, konular=args.konular, **degisti)
            session.commit()
        for ad, liste in degisti.items():
            print(f"{ad}: {', '.join(liste)}")
        print("Değişiklik yok." if not degisti else "Sonraki taramadan itibaren geçerli.")


# Saat politikası, uygulamanın saati her zaman Türkiye saatidir, sunucunun saat diliminden bağımsız.
# Kaynakların hepsi Türk kurumları ve "bugünün gazetesi" Türkiye gününe göre, DB'deki zamanlar saat dilimi
# bilgisi olmadan (naive) Türkiye saati olarak tutulur. Türkiye 2016'dan beri yaz saati uygulamadığı için
# bu belirsizlik yaratmaz. Sunucu UTC'de bırakılsa da, admin komutu SSH'tan elle çalıştırsa da saat aynı olur.
SAAT_DILIMI = "Europe/Istanbul"


# Programın saatini her zaman Türkiye saatine sabitler (sunucu UTC'de olsa bile).
def saat_dilimini_sabitle() -> None:
    # Sadece Unix (sunucu). Windows'ta TZ'ye "Europe/Istanbul" yazmak ZARARLI, C kütüphanesi bu biçimi
    # tanımıyor ve saati sessizce UTC'ye çeviriyor (date.today() bir gün geride kalabiliyor).
    # Geliştirme makinesi zaten Türkiye saatinde.
    if hasattr(time, "tzset"):
        os.environ["TZ"] = SAAT_DILIMI
        time.tzset()


# Programın giriş noktası. Ayarları yükler, komutu okur ve ilgili fonksiyonu çalıştırır.
def main() -> None:
    saat_dilimini_sabitle()
    # Çalışma klasöründeki .env okunur (git'e girmez). Ortamda zaten tanımlı değişkenler ezilmez,
    # sunucuda ayarlar systemd/docker ortamından da verilebilir.
    load_dotenv()
    # Windows konsolu cp1254 kullanıyor, yıldız ve uzun tire gibi karakterlerde çıktı çöküyordu.
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    # Log biçimi, zaman, seviye, kaynak, mesaj. httpx'in her istek logu çok gürültülü, sadece uyarıları göster.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Komut satırı tanımı, her alt komut, yardım metni, argümanları ve çalıştıracağı fonksiyon.
    parser = argparse.ArgumentParser(prog="mevzuat")
    alt = parser.add_subparsers(dest="komut", required=True)

    alt.add_parser("gunluk", help="Zamanlayıcı işi: tara + onaya sun + sağlık kontrolü").set_defaults(
        func=gunluk_komutu
    )
    alt.add_parser("calistir", help="Tam tarama yap, sonuçları DB'ye yaz").set_defaults(func=calistir_komutu)

    il = alt.add_parser("ilgili", help="İlgili kayıtları listele (rapor önizlemesi)")
    il.add_argument("--gun", type=int, default=7)
    il.set_defaults(func=ilgili_komutu)

    rg = alt.add_parser("rg", help="Resmî Gazete fihristini listele")
    rg.add_argument("--tarih", help="YYYY-MM-DD (varsayılan: bugün)")
    rg.add_argument("--ilan", action="store_true", help="İlân bölümünü de göster")
    rg.set_defaults(func=rg_komutu)

    alt.add_parser("onizle", help="Onaya sunulmamış kayıtlardan rapor önizlemesi (dosyaya yazar)").set_defaults(
        func=onizle_komutu
    )

    ke = alt.add_parser("kullanici-ekle", help="Panel kullanıcısı ekle (parola güvenli şekilde sorulur)")
    ke.add_argument("--eposta", required=True)
    ke.add_argument("--ad", required=True)
    ke.add_argument("--rol", required=True, choices=guvenlik.ROLLER)
    ke.set_defaults(func=kullanici_ekle_komutu)

    au = alt.add_parser("api-anahtari-uret", help="API anahtarı üret (panel kullanılmıyorsa); anahtar bir kez basılır")
    au.add_argument("--ad", required=True, help="Hangi sistem kullanacak, ör. 'Portal entegrasyonu'")
    au.add_argument("--rol", required=True, choices=guvenlik.API_ANAHTARI_ROLLERI)
    au.add_argument("--gun", type=int, help="Geçerlilik (gün). Yazılmazsa süresiz")
    au.set_defaults(func=api_anahtari_uret_komutu)
    alt.add_parser("api-anahtari-listele", help="API anahtarlarını listele").set_defaults(func=api_anahtari_listele_komutu)
    ai = alt.add_parser("api-anahtari-iptal", help="API anahtarını iptal et")
    ai.add_argument("--id", type=int, required=True, help="Anahtar no (listede #)")
    ai.set_defaults(func=api_anahtari_iptal_komutu)

    kp = alt.add_parser("kullanici-pasif", help="Kullanıcıyı pasifleştir (giriş yapamaz)")
    kp.add_argument("--eposta", required=True)
    kp.set_defaults(func=kullanici_pasif_komutu)

    ms = alt.add_parser("mfa-sifirla", help="Kullanıcının iki adımlı doğrulamasını sıfırla (telefon kaybı)")
    ms.add_argument("--eposta", required=True)
    ms.set_defaults(func=mfa_sifirla_komutu)

    rd = alt.add_parser("gonderim-durum", help="Durumu belirsiz (GONDERILIYOR) dağıtım mailini çöz")
    rd.add_argument("--id", type=int, required=True, help="Gönderim no (uyarı mailinde yazar)")
    secim = rd.add_mutually_exclusive_group(required=True)
    secim.add_argument("--gonderildi", action="store_true", help="Mail gitmiş: gönderildi olarak işaretle")
    secim.add_argument("--tekrar-gonder", action="store_true", help="Mail gitmemiş: tekrar gönder")
    rd.set_defaults(func=gonderim_durum_komutu)

    md = alt.add_parser("mail-dene", help="SMTP ayarını dene: tek adrese deneme maili at")
    md.add_argument("adres", help="Deneme mailinin gideceği adres")
    md.set_defaults(func=mail_dene_komutu)

    pn = alt.add_parser("panel", help="Onay panelini başlat")
    pn.add_argument("--host", default="127.0.0.1")
    pn.add_argument("--port", type=int, default=8000)
    pn.set_defaults(func=panel_komutu)

    alt.add_parser("izlenen", help="Güncel metni takip edilen mevzuatı listele").set_defaults(func=izlenen_komutu)

    zm = alt.add_parser("zamanlayici", help="Sürekli çalışır: paneldeki saatlerde günlük işi başlatır (Docker ve sunucu)")
    zm.add_argument("--hemen", action="store_true", help="Başlar başlamaz bir kez çalıştır, sonra saatleri bekle")
    zm.set_defaults(func=zamanlayici_komutu)

    dn = alt.add_parser("dene", help="Bir kaynağı DB'ye yazmadan dene, eşleşenleri ★ ile göster")
    dn.add_argument("kaynak", help="config/kaynaklar.toml'daki ad (ör. masak)")
    dn.add_argument("--gun", type=int, default=7, help="Kaç gün geriye (varsayılan: 7)")
    dn.set_defaults(func=dene_komutu)

    da = alt.add_parser("ayar-disa-aktar", help="DB'deki kaynak/konu tanımlarını toml olarak yaz (yedek, onaya gönderme)")
    da.add_argument("--klasor", default="disa_aktarim", help="Hedef klasör (varsayılan: disa_aktarim)")
    da.set_defaults(func=ayar_disa_aktar_komutu)

    ia = alt.add_parser("ayar-ice-aktar", help="Düzenlenmiş kaynak/konu toml'unu DB'ye uygula (dosyada olmayan kaldırılır)")
    ia.add_argument("--kaynaklar", default="config/kaynaklar.toml")
    ia.add_argument("--konular", default="config/konular.toml")
    ia.set_defaults(func=ayar_ice_aktar_komutu)

    # Komut satırını oku ve seçilen komutun fonksiyonunu çalıştır.
    args = parser.parse_args()
    args.func(args)


# Dosya doğrudan çalıştırılınca main()'i çağır.
if __name__ == "__main__":
    main()

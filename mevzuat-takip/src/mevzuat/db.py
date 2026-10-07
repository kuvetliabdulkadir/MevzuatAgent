# Veritabanı tabloları burada tarif ediliyor. Her "class" bir tablo, içindeki her satır bir sütun.
# SQLAlchemy (ORM) sayesinde SQL yazmadan Python nesneleriyle tablo satırlarını okuyup yazıyoruz.
# Mapped[str] metin sütunu, Mapped[int] sayı, Mapped[datetime] tarih ve saat demek. Sonunda None yazanlar boş bırakılabilir.
import os
from datetime import date, datetime
from pathlib import Path

# Alembic, tablo yapısı değişince (yeni sütun vb.) veritabanını güncelleyen araç ("migration").
from alembic import command
from alembic.config import Config

from sqlalchemy import JSON, ForeignKey, Index, MetaData, String, Text, UniqueConstraint, create_engine, false, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Geliştirmede SQLite dosyası, sunucuda PostgreSQL.
#   MEVZUAT_DB_URL=postgresql+psycopg://kullanici:sifre@localhost:5432/mevzuat
VARSAYILAN_DB_URL = "sqlite:///mevzuat.db"
GOC_KILIDI = 7_202_610  # migrate(), PostgreSQL danışma kilidi numarası (panel + zamanlayıcı aynı anda açılınca)


# Bütün tabloların ortak atası.
class Base(DeclarativeBase):
    # Constraint'lere öngörülebilir isim, Alembic ileride onları adıyla bulup değiştirebilsin.
    metadata = MetaData(
        naming_convention={
            # ix indeks, uq tekil kural, fk başka tabloya bağlantı, pk birincil anahtar demek.
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# Her kaynak için en son nerede kaldığımızı tutan tablo.
class KaynakDurumu(Base):
    """Kaynak başına 'en son nereye kadar işledim' (checkpoint)."""

    # Veritabanındaki tablo adı.
    __tablename__ = "kaynak_durumu"

    # primary_key bu sütunun satırın kimliği olduğunu söyler, her kaynak için tek satır olur.
    kaynak: Mapped[str] = mapped_column(String(50), primary_key=True)
    # Biçimi kaynağa özel (RG, son tamamlanan gün, WordPress, son başarılı çalışma zamanı).
    checkpoint: Mapped[str] = mapped_column(String(50))
    # Checkpoint en son ne zaman ilerledi (sağlık kontrolü buna bakıyor).
    guncellendi: Mapped[datetime]


# Her tarama çalışmasının tutulduğu tablo.
class Calisma(Base):
    __tablename__ = "calismalar"

    # Otomatik artan numara.
    id: Mapped[int] = mapped_column(primary_key=True)
    baslangic: Mapped[datetime]
    bitis: Mapped[datetime | None]
    durum: Mapped[str] = mapped_column(String(20))  # CALISIYOR / BASARILI / HATALI
    # Kaynak başına özet (kaç yeni kayıt vb.), JSON olarak.
    ozet: Mapped[dict] = mapped_column(JSON, default=dict)
    hata: Mapped[str | None] = mapped_column(Text)


# Panel kullanıcılarının tablosu.
class Kullanici(Base):
    """Panel kullanıcıları. Kendi kendine kayıt olma yok, admin panelden davet maili göndererek ya da komut satırından ekler."""

    __tablename__ = "kullanicilar"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique=True, aynı e-postayla iki kullanıcı olamaz.
    eposta: Mapped[str] = mapped_column(String(254), unique=True)
    ad: Mapped[str] = mapped_column(String(200))
    # Parolanın kendisi değil, geri çevrilemeyen özeti saklanır.
    parola_hash: Mapped[str] = mapped_column(String(200))  # Argon2
    rol: Mapped[str] = mapped_column(String(20))  # "admin" | "onaylayici"
    # Pasif kullanıcı giriş yapamaz. Belli gün pasif kalınca kişisel bilgileri silinir (guvenlik.pasifleri_sil).
    aktif: Mapped[bool] = mapped_column(default=True)
    pasif_tarihi: Mapped[datetime | None]  # pasifleştirildiği an, yeniden açılınca boşalır
    # Hesabın silindiği an. Satır kalır ki onay geçmişindeki "kim" bağlantısı kopmasın, ad ve e-posta silinir.
    silindi: Mapped[datetime | None]
    # Art arda kaç hatalı giriş yapıldı.
    basarisiz_giris: Mapped[int] = mapped_column(default=0, server_default="0")
    kilit_bitis: Mapped[datetime | None]  # art arda hatalı girişte geçici kilit
    son_giris: Mapped[datetime | None]
    olusturuldu: Mapped[datetime]
    # İki adımlı doğrulama (TOTP). Sır kurulum başlarken üretilir ama ilk doğru kod girilene kadar aktif değildir.
    # Oturum çerezine konmaz, çerez imzalı ama şifreli değil, tarayıcıda okunabilir.
    mfa_gizli: Mapped[str | None] = mapped_column(String(64))
    mfa_aktif: Mapped[bool] = mapped_column(default=False, server_default=false())
    mfa_son_adim: Mapped[int | None]  # son kabul edilen kodun 30 sn'lik zaman adımı, aynı kod iki kez kullanılamaz


# Davet ve parola sıfırlama linklerinin tablosu.
class ParolaLinki(Base):
    """Maille giden tek kullanımlık "parolanızı belirleyin" linki. Yeni kullanıcı davetinde ya da admin'in başlattığı
    sıfırlamada gönderilir. Linkin kendisi saklanmaz, sadece SHA-256 özeti saklanır, DB sızsa da link kullanılamaz."""

    __tablename__ = "parola_linkleri"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ForeignKey, bu sütun kullanicilar tablosundaki bir satıra bağlı. index=True, bu sütunla arama hızlı olsun.
    kullanici_id: Mapped[int] = mapped_column(ForeignKey("kullanicilar.id"), index=True)
    ozet: Mapped[str] = mapped_column(String(64), unique=True)
    tur: Mapped[str] = mapped_column(String(20))  # "davet" | "sifirlama"
    olusturuldu: Mapped[datetime]
    # Bu zamandan sonra link geçersiz.
    son_gecerlilik: Mapped[datetime]
    kullanildi: Mapped[datetime | None]  # kullanıldı ya da yenisi gönderildiği için iptal edildi
    # Linki gönderen admin.
    isteyen_id: Mapped[int | None] = mapped_column(ForeignKey("kullanicilar.id"))


# Denetim kaydının tablosu, kim ne zaman ne yaptı.
class Denetim(Base):
    """Kim, ne zaman, ne yaptı. Giriş denemeleri ve onay kararları buraya yazılır."""

    __tablename__ = "denetim"

    id: Mapped[int] = mapped_column(primary_key=True)
    zaman: Mapped[datetime]
    # İşlemi yapan kullanıcı (bilinmiyorsa boş, ör. hatalı giriş).
    kullanici_id: Mapped[int | None] = mapped_column(ForeignKey("kullanicilar.id"))
    islem: Mapped[str] = mapped_column(String(50))  # giris, giris_basarisiz, onay, ret …
    # Ayrıntılar (önce/sonra halleri vb.) JSON olarak.
    detay: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


# Raporların tablosu.
class Rapor(Base):
    """Bir rapor ve onay süreci. Kayıtlar rapor_id ile buna bağlanır.

    Rapor önce ONAY_BEKLIYOR olur. Sorumlu onaylarsa ONAYLANDI olur, bütün dağıtım mailleri gidince GONDERILDI olur.
    Sorumlu reddederse REDDEDILDI olur, rapor dağıtılmaz ve not yazmak zorunludur.
    Onay anında dağıtım planı, yani hangi adresin hangi kalemleri alacağı, `gonderimler` tablosuna dondurulur ve
    her mailin durumu orada tutulur. Gidemeyen mail kaldıkça rapor ONAYLANDI durumunda kalır (rapor.dagit fonksiyonuna bakın).
    Eski sürümde rapor düzeyinde GONDERILIYOR durumu da vardı, yeni raporlar bu durumu kullanmaz.
    """

    __tablename__ = "raporlar"

    id: Mapped[int] = mapped_column(primary_key=True)
    olusturuldu: Mapped[datetime]
    # server_default, eski veritabanlarında sütun eklenirken var olan satırlara yazılan değer.
    durum: Mapped[str] = mapped_column(String(20), server_default="GONDERILDI")
    gonderildi: Mapped[datetime | None]  # dağıtım mailinin gittiği an
    # GONDERILIYOR'a geçildiği an. Bu durumda uzun süre kalan rapor "gönderildi mi bilinmiyor" demektir
    # (mail gitti ama durum yazılamadı ya da süreç çöktü), otomatik tekrar YOK, yönetici karar verir.
    gonderim_denemesi: Mapped[datetime | None]
    # Mail konusu.
    konu: Mapped[str] = mapped_column(String(300))
    # Raporun gittiği bütün adresler.
    alicilar: Mapped[list] = mapped_column(JSON, default=list)
    kayit_sayisi: Mapped[int]
    hata: Mapped[str | None] = mapped_column(Text)
    # Onaylayan/reddeden kişi, ne zaman, hangi notla.
    karar_veren_id: Mapped[int | None] = mapped_column(ForeignKey("kullanicilar.id"))
    karar_zamani: Mapped[datetime | None]
    karar_notu: Mapped[str | None] = mapped_column(Text)  # dağıtım raporunun başına eklenir / ret sebebi


# Taranan her yayının tablosu, duyuru, yönetmelik, karar gibi.
class Kayit(Base):
    __tablename__ = "kayitlar"
    # Aynı kaynakta aynı dis_id ile iki satır olamaz (aynı duyuru iki kez girmesin).
    __table_args__ = (UniqueConstraint("kaynak", "dis_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kaynak: Mapped[str] = mapped_column(String(50))
    dis_id: Mapped[str] = mapped_column(String(500))  # kaynak içinde tekil (RG, URL, WordPress, post id)
    yayin_tarihi: Mapped[date]
    baslik: Mapped[str] = mapped_column(Text)
    tur: Mapped[str] = mapped_column(String(200), default="")  # RG alt başlığı (YÖNETMELİKLER…)
    bolum: Mapped[str] = mapped_column(String(200), default="")
    sayi: Mapped[int | None]
    mukerrer: Mapped[int] = mapped_column(default=0)
    url: Mapped[str] = mapped_column(String(500))  # maile girmez, içerik çekmek için
    kaynakca: Mapped[str] = mapped_column(Text)
    # Başlık (ya da kaynak) en az bir konuya uydu mu.
    ilgili: Mapped[bool]
    eslesmeler: Mapped[dict] = mapped_column(JSON, default=dict)  # konu, sonra eşleşen kelimeler
    # Kayıt anındaki iş kolları. Konu ayarı sonradan değişse de geçmiş raporlar tutarlı kalır.
    is_kollari: Mapped[list] = mapped_column(JSON, default=list)
    # Belgenin okunmuş düz metni.
    icerik: Mapped[str | None] = mapped_column(Text)
    # GEREKSIZ (ilgisiz) / BEKLIYOR / TAMAM / OCR_GEREKLI / HATA
    icerik_durumu: Mapped[str] = mapped_column(String(20))
    # Sistemin bu kaydı ilk gördüğü an.
    ilk_gorulme: Mapped[datetime]
    # Hangi taramada bulundu.
    calisma_id: Mapped[int] = mapped_column(ForeignKey("calismalar.id"))
    rapor_id: Mapped[int | None] = mapped_column(ForeignKey("raporlar.id"))  # None, henüz raporlanmadı
    # Sorumlu onay sırasında bu kalemi dağıtımdan çıkardı (rapora bağlı kalır, gönderilmez).
    haric: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Sadece "güncel metni değişti" kayıtlarında (surum.py), [{bolum, tur, eski, yeni}, …]
    degisiklikler: Mapped[list | None] = mapped_column(JSON)


# Alıcı gruplarının tablosu.
class AliciGrubu(Base):
    """Raporun kime gideceği. Panelden yönetilir. Grup sadece kendi iş kollarındaki kalemleri alır.
    İş kolu belirsiz ya da genel olan "Ortak" kalemleri almak isteyen grup is_kollari alanına "Ortak" ekler.
    Silinmez, pasifleştirilir, böylece geçmiş gönderimler grup adını göstermeye devam eder."""

    __tablename__ = "alici_gruplari"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad: Mapped[str] = mapped_column(String(100), unique=True)
    is_kollari: Mapped[list] = mapped_column(JSON, default=list)
    # Grubun e-posta adresleri.
    adresler: Mapped[list] = mapped_column(JSON, default=list)
    aktif: Mapped[bool] = mapped_column(default=True)
    guncellendi: Mapped[datetime]


# Dağıtım maillerinin tablosu, onaylanan raporun kişi başı her maili bir satır.
class Gonderim(Base):
    """Onaylanmış raporun tek bir dağıtım maili. Aynı kalemleri alan adresler aynı maili paylaşır,
    birden çok gruptaki kişi gruplarının bütün kalemlerini tek mailde alır.

    Mail önce BEKLIYOR olur, sonra GONDERILIYOR, en son GONDERILDI.
      - SMTP hata verdiyse mail kesinlikle gitmemiştir. BEKLIYOR durumuna döner, sonraki çalışmada tekrar denenir.
      - GONDERILIYOR durumunda kalan mailin gidip gitmediği bilinmez. Böyle bir mail kendiliğinden tekrar denenmez,
        yönetici `gonderim-durum` komutuyla karar verir. Böylece bir mail gidip diğeri gitmediğinde sadece
        gitmeyen tekrar denenir, giden ikinci kez gitmez.
    """

    __tablename__ = "gonderimler"

    id: Mapped[int] = mapped_column(primary_key=True)
    rapor_id: Mapped[int] = mapped_column(ForeignKey("raporlar.id"), index=True)
    alicilar: Mapped[list] = mapped_column(JSON, default=list)
    # Bu mailde hangi kalemler var.
    kayit_idler: Mapped[list] = mapped_column(JSON, default=list)
    gruplar: Mapped[list] = mapped_column(JSON, default=list)  # onay anındaki grup adları (gösterim için)
    durum: Mapped[str] = mapped_column(String(20))
    gonderim_denemesi: Mapped[datetime | None]
    gonderildi: Mapped[datetime | None]
    hata: Mapped[str | None] = mapped_column(Text)


# Güncel metni mevzuat.gov.tr'den takip edilen mevzuatın tablosu.
class IzlenenMevzuat(Base):
    """Güncel (konsolide) metni takip edilen mevzuat. Anahtar mevzuat.gov.tr'deki tür.tertip.no (ör. 1.5.4760)."""

    __tablename__ = "izlenen_mevzuat"

    anahtar: Mapped[str] = mapped_column(String(50), primary_key=True)
    ad: Mapped[str] = mapped_column(Text)
    # ayar ise config/izlenen_mevzuat.toml'dan gelmiş, otomatik ise güncellenenler listesinde konu filtresine takılmış
    neden: Mapped[str] = mapped_column(String(20))
    konu: Mapped[str | None] = mapped_column(String(200))  # ayardaki konu (başlıkta kelime geçmese de ilgili)
    eklendi: Mapped[datetime]
    # Metni en son ne zaman kontrol edildi.
    son_kontrol: Mapped[datetime | None]


# Takip edilen mevzuatın metin sürümlerinin tablosu.
class MetinSurumu(Base):
    """Bir mevzuatın güncel metninin bir sürümü. Eski sürümler silinmez (geriye dönük kanıt)."""

    __tablename__ = "metin_surumleri"

    id: Mapped[int] = mapped_column(primary_key=True)
    anahtar: Mapped[str] = mapped_column(ForeignKey("izlenen_mevzuat.anahtar"), index=True)
    cekildi: Mapped[datetime]
    ozet: Mapped[str] = mapped_column(String(64))  # metnin SHA-256 özeti, metnin değişip değişmediğini bununla anlıyoruz
    # Metnin tamamı.
    metin: Mapped[str] = mapped_column(Text)


# Panelden yönetilen kaynak tanımlarının tablosu.
class KaynakTanimi(Base):
    """Taranan kaynak. İlk kurulumda config/kaynaklar.toml dosyasından bir kez aktarılır, sonra panelden yönetilir.
    `ad` hiç değişmez, çünkü kayıtlar ve checkpoint (kaynak_durumu) bu adla tutulur. Kaynak silinmez, `kaldirildi` olarak işaretlenir."""

    __tablename__ = "kaynaklar"

    ad: Mapped[str] = mapped_column(String(50), primary_key=True)
    etiket: Mapped[str] = mapped_column(String(200))  # rapordaki kaynakçada görünen ad
    tip: Mapped[str] = mapped_column(String(30))  # sources.TIPLER anahtarı
    ayarlar: Mapped[dict] = mapped_column(JSON, default=dict)  # tipe özel alanlar (api_url, turler …)
    varsayilan_konular: Mapped[list] = mapped_column(JSON, default=list)  # konu adları
    sira: Mapped[int] = mapped_column(default=0)  # tarama sırası (toml'daki sıra korunur)
    aktif: Mapped[bool] = mapped_column(default=True)
    kaldirildi: Mapped[bool] = mapped_column(default=False)
    surum: Mapped[int] = mapped_column(default=1)  # iki kişi aynı anda düzenlerse çakışmayı yakalamak için
    guncelleme: Mapped[datetime]
    guncelleyen_id: Mapped[int | None] = mapped_column(ForeignKey("kullanicilar.id"))  # None, toml'dan aktarma


# Panelden yönetilen konu tanımlarının tablosu.
class KonuTanimi(Base):
    """Başlık filtresinin konusu. İlk kurulumda config/konular.toml dosyasından bir kez aktarılır, sonra panelden
    yönetilir. Kelimeler girildiği gibi saklanır, küçük harfe çevirme okurken yapılır (filtre.konu_olustur)."""

    __tablename__ = "konular"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad: Mapped[str] = mapped_column(String(200), unique=True)
    is_kollari: Mapped[list] = mapped_column(JSON, default=list)
    kelimeler: Mapped[list] = mapped_column(JSON, default=list)
    haric: Mapped[list] = mapped_column(JSON, default=list)
    dislanan: Mapped[list] = mapped_column(JSON, default=list)
    # Bu konu bizi neden ilgilendiriyor, raporda ve mailde "Neden size geldi" satırının altında görünür.
    aciklama: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Panelden yeniden adlandırılınca eski adlar, izlenen_mevzuat.toml ve eski kayıtlar eski adı kullanmaya devam
    # edebilir, yine bu konuya bağlanır (tanimlar.izlenenleri_esle).
    eski_adlar: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    aktif: Mapped[bool] = mapped_column(default=True)
    surum: Mapped[int] = mapped_column(default=1)
    guncelleme: Mapped[datetime]
    guncelleyen_id: Mapped[int | None] = mapped_column(ForeignKey("kullanicilar.id"))


# Sistem ayarlarının tablosu, tarama saatleri ve zamanlayıcı nabzı burada.
class SistemAyari(Base):
    """Panelden değişen işletim ayarları ve zamanlayıcının nabzı, anahtar ve değer olarak.
    calisma_saatleri anahtarında Türkiye saatiyle ["06:30", "18:00"] gibi bir liste durur. zamanlayici_nabiz anahtarında zamanlayıcının son yazdığı durum durur."""

    __tablename__ = "sistem_ayarlari"

    anahtar: Mapped[str] = mapped_column(String(50), primary_key=True)
    deger: Mapped[dict | list] = mapped_column(JSON)
    surum: Mapped[int] = mapped_column(default=1)
    guncelleme: Mapped[datetime]
    guncelleyen_id: Mapped[int | None] = mapped_column(ForeignKey("kullanicilar.id"))


# Panelden gelen "Şimdi tara" isteklerinin tablosu.
class TaramaIstegi(Base):
    """Paneldeki "Şimdi tara" isteği. Panel sadece isteği yazar, taramayı zamanlayıcı başlatır. Docker'da panel ve zamanlayıcı
    ayrı container'larda çalışır, dosya kilidi birbirini görmez, iki tarama aynı anda çalışmasın diye böyle yapıldı.
    İstek önce BEKLIYOR olur, sonra CALISIYOR, en son BITTI ya da HATALI."""

    __tablename__ = "tarama_istekleri"

    id: Mapped[int] = mapped_column(primary_key=True)
    isteyen_id: Mapped[int | None] = mapped_column(ForeignKey("kullanicilar.id"))
    istendi: Mapped[datetime]
    basladi: Mapped[datetime | None]
    bitti: Mapped[datetime | None]
    durum: Mapped[str] = mapped_column(String(20), index=True)
    calisma_id: Mapped[int | None] = mapped_column(ForeignKey("calismalar.id"))
    rapor_id: Mapped[int | None] = mapped_column(ForeignKey("raporlar.id"))  # tarama onaya yeni rapor sunduysa
    hata: Mapped[str | None] = mapped_column(Text)

    # Aynı anda en fazla bir sırada/çalışan istek, iki kişi aynı anda tıklasa da ikinci kayıt DB'ye giremez.
    # Sabit ifade (1) üzerinde kısmi tekil indeks, PostgreSQL de SQLite de destekler.
    __table_args__ = (Index("uq_tarama_istekleri_tek_aktif", text("(1)"), unique=True,
                            postgresql_where=text("durum IN ('BEKLIYOR', 'CALISIYOR')"),
                            sqlite_where=text("durum IN ('BEKLIYOR', 'CALISIYOR')")),)


# Veritabanı bağlantısını kurar (.env'deki adres, yoksa yerel SQLite dosyası).
def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or os.environ.get("MEVZUAT_DB_URL", VARSAYILAN_DB_URL))


# Alembic'in ayarlarını kodla hazırlar (migration dosyalarının yeri ve veritabanı adresi).
def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    # configparser '%' karakterini özel yorumluyor (URL'deki şifrede olabilir).
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


# Veritabanını en son tablo yapısına getirir (eksik tablo/sütunları ekler). Her açılışta çağrılır.
def migrate(engine: Engine) -> None:
    """Şemayı en son sürüme getirir. Model değişince yeni migration şu komutla üretilir.
        uv run alembic -c alembic.ini revision --autogenerate -m "aciklama"

    Docker'da panel ve zamanlayıcı aynı anda açılır ve ikisi de bunu çağırır. PostgreSQL'de kilitle sıraya girerler,
    ikincisi bekler, sonra şemayı güncel bulur. Kilit yokken ikisi aynı tabloyu kurmaya çalışıyor ve biri çöküyordu.
    """
    cfg = alembic_config(engine.url.render_as_string(hide_password=False))
    # SQLite'ta kilide gerek yok, doğrudan güncelle.
    if engine.dialect.name != "postgresql":
        command.upgrade(cfg, "head")
        return
    # PostgreSQL'de önce kilidi al (diğer süreç bekler), güncelle, kilidi bırak.
    with engine.connect() as kilit:
        kilit.execute(text("SELECT pg_advisory_lock(:k)"), {"k": GOC_KILIDI})
        try:
            command.upgrade(cfg, "head")
        finally:
            kilit.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": GOC_KILIDI})


# Testler için, migration kullanmadan tabloları doğrudan oluştur.
def init_db(engine: Engine) -> None:
    """Sadece testler için. Migration kullanmadan şemayı doğrudan modelden kurar."""
    Base.metadata.create_all(engine)

"""Zamanlayıcının çağırdığı tek iş. Önce tarar, sonra rapor çıkarır, en son sağlığı kontrol eder.

Sessizlik tehlikelidir. "Değişiklik yoksa mail yok" kararı yüzünden sistem bozulduğunda da kimse
mail almaz ve bu durum "değişiklik yok" ile karıştırılır. Bu yüzden iki önlem var.
  - Çalışma hata verirse ya da bir kaynak ilerlemiyorsa yöneticiye uyarı maili gider.
  - Haftada bir yöneticiye "sistem çalışıyor" diyen nabız maili gider. Nabız gelmiyorsa sunucu,
    zamanlayıcı ya da mail tamamen durmuş demektir, bunu ancak bir insan fark edebilir.
"""
# Her gün 06:30 ve 18:00'de çalışan "günlük iş" bu dosyada. Sırası.
# Önce tarar, sonra rapor hazırlayıp onaylayıcıya gönderir, bekleyen dağıtımları dener, sorun varsa yöneticiyi uyarır, Pazartesi de nabız maili atar.

# html.escape, maile yazılan yazıdaki < > & işaretlerini zararsız hale getirir (HTML olarak çalışmasınlar).
import html
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mevzuat import pipeline, rapor, saklama, zamanlama
from mevzuat.db import Calisma, KaynakDurumu, Kayit, Kullanici, Rapor
from mevzuat.filtre import Konu
from mevzuat.mail import Mail, MailGonderici
from mevzuat.sources.base import Kaynak
from mevzuat.web import guvenlik

log = logging.getLogger(__name__)

# Adres boşken onay maili linksiz gider, bu sessizce olmasın (kullanıcı Gmail'de linksiz maili görüp sordu).
PANEL_ADRESI_YOK = ("Rapor #{rapor_id} onay maili \"Onay paneline git\" düğmesi olmadan gitti: MEVZUAT_PANEL_ADRESI "
                    "tanımlı değil. .env'ye panelin adresini yazın (ör. https://mevzuat.firma.com.tr).")


# Günlük işin ayarları (çoğu .env'den gelir).
@dataclass
class Ayarlar:
    # Uyarı ve nabız maillerinin gideceği yönetici adresleri.
    admin_alicilari: list[str]  # rapor alıcıları burada değil, panelden yönetilen alıcı gruplarında
    # Maillere orijinal PDF'ler eklensin mi.
    ek_ekle: bool = True
    nabiz_gunu: int = 0  # 0 yani Pazartesi
    # Checkpoint'i bu kadar süredir ilerlemeyen kaynak "takıldı" sayılır. RG her gün ilerler
    # (bayramda bile "yayımlanmadı" olarak), 2 günden fazla durması bir sorun demektir.
    takilma_esigi: timedelta = timedelta(days=2)
    onay_hatirlatma: timedelta = timedelta(days=1)  # bundan uzun bekleyen rapor için sorumluya hatırlatma
    onay_gecikme_uyarisi: timedelta = timedelta(days=3)  # bundan uzun bekleyen rapor yöneticiye bildirilir
    panel_adresi: str | None = None  # onay maillerindeki "panele git" linki için (MEVZUAT_PANEL_ADRESI)
    pasif_silme_gun: int = 30  # bu kadar gün pasif kalan hesap silinir, 0 ise silinmez (MEVZUAT_PASIF_SILME_GUN)
    saklama_gun: int = 30  # bitmiş raporlar ve denetim kaydı bu kadar gün sonra silinir, 0 ise silinmez (MEVZUAT_SAKLAMA_GUN)


# Günlük işin sonucu, hangi çalışma, hangi rapor, hangi sorunlar, hangi mailler gitti.
@dataclass
class GunlukSonuc:
    calisma: Calisma
    rapor: Rapor | None
    sorunlar: list[str] = field(default_factory=list)
    uyari_gonderildi: bool = False
    nabiz_gonderildi: bool = False


# "Onay bekliyor" mailinin gideceği kişiler, aktif onaylayıcılar, hiç onaylayıcı yoksa aktif adminler.
def onaylayici_adresleri(session: Session) -> list[str]:
    """Bildirimin gideceği adresler. Aktif onaylayıcılar, hiç yoksa aktif yöneticiler."""
    # Önce onaylayıcılara bak, bulunursa onları ver. Bulunamazsa adminlere bak.
    for rol in ("onaylayici", "admin"):
        adresler = list(
            session.scalars(select(Kullanici.eposta).where(Kullanici.rol == rol, Kullanici.aktif, Kullanici.api_hesabi.is_(False))
                            .order_by(Kullanici.id))
        )
        if adresler:
            return adresler
    return []


# Uzun süredir ilerlemeyen (takılmış) kaynakları bulur ve sorun yazısı üretir.
def saglik_kontrolu(session: Session, kaynaklar: list[Kaynak], simdi: datetime, esik: timedelta) -> list[str]:
    sorunlar = []
    for kaynak in kaynaklar:
        durum = session.get(KaynakDurumu, kaynak.ad)
        if durum is None:
            continue  # henüz hiç başarılı çalışmamış, ilk çalışmanın hatası zaten raporlanır
        # Son ilerlemeden bu yana 2 günden fazla geçtiyse sorun.
        if simdi - durum.guncellendi > esik:
            sorunlar.append(
                f"'{kaynak.ad}' kaynağı {durum.guncellendi:%d.%m.%Y %H:%M}'den beri ilerlemiyor "
                f"(checkpoint: {durum.checkpoint}). Site yapısı değişmiş ya da erişim engellenmiş olabilir."
            )
    return sorunlar


# Bu çalışma bugünün ilk çalışması mı diye bakar, hatırlatma ve nabız günde bir kez gitsin diye.
def _gunun_ilk_calismasi(session: Session, calisma: Calisma) -> bool:
    # Bugünün 00:00'ı.
    gun_basi = datetime.combine(calisma.baslangic.date(), datetime.min.time())
    # Bugün başlamış başka çalışma var mı say.
    oncekiler = session.scalar(
        select(func.count()).select_from(Calisma).where(Calisma.baslangic >= gun_basi, Calisma.id != calisma.id)
    )
    return oncekiler == 0


# Satır listesinden basit bir mail oluşturur (uyarı, hatırlatma, nabız için).
def _basit_mail(konu: str, satirlar: list[str], alicilar: list[str]) -> Mail:
    # Her satırı bir paragraf yap (escape ile güvenli).
    govde = "".join(f"<p style='margin:0 0 8px'>{html.escape(s)}</p>" for s in satirlar)
    return Mail(
        konu=konu,
        html=f"<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.5'>{govde}</div>",
        metin="\n\n".join(satirlar),
        alicilar=alicilar,
    )


# Haftalık "sistem çalışıyor" mailini hazırlar, son 7 günün sayıları ve kaynakların son ilerleme zamanı.
def _nabiz_mail(session: Session, simdi: datetime, alicilar: list[str]) -> Mail:
    baslangic = simdi - timedelta(days=7)
    # Son 7 gündeki çalışmalar ve kaçı başarılı.
    calismalar = session.scalars(select(Calisma).where(Calisma.baslangic >= baslangic)).all()
    basarili = sum(1 for c in calismalar if c.durum == "BASARILI")
    # Son 7 günde görülen kayıt sayısı ve ilgili olanlar.
    yeni = session.scalar(select(func.count()).select_from(Kayit).where(Kayit.ilk_gorulme >= baslangic))
    ilgili = session.scalar(
        select(func.count()).select_from(Kayit).where(Kayit.ilk_gorulme >= baslangic, Kayit.ilgili)
    )
    # Son 7 günde gönderilen rapor sayısı.
    raporlar = session.scalar(
        select(func.count()).select_from(Rapor).where(Rapor.gonderildi.is_not(None), Rapor.gonderildi >= baslangic)
    )
    # Her kaynağın durumu.
    kaynaklar = session.scalars(select(KaynakDurumu).order_by(KaynakDurumu.kaynak)).all()
    # Mail satırları. * ile kaynak satırları listeye açılıyor.
    satirlar = [
        "Mevzuat Takip sistemi çalışıyor. Son 7 günün özeti:",
        f"Çalışma: {len(calismalar)} (başarılı: {basarili})",
        f"Taranan yeni kayıt: {yeni}, ilgili bulunan: {ilgili}, gönderilen rapor: {raporlar}",
        *[f"Kaynak '{k.kaynak}': son ilerleme {k.guncellendi:%d.%m.%Y %H:%M} (checkpoint {k.checkpoint})"
          for k in kaynaklar],
        "Bu mail her hafta gelir. Gelmediyse sistem durmuş olabilir; kontrol edin.",
    ]
    return _basit_mail(f"Mevzuat Takip — haftalık durum ({simdi:%d.%m.%Y})", satirlar, alicilar)


# Günlük işin kendisi.
def calistir(
    session: Session,
    client: httpx.Client,
    kaynaklar: list[Kaynak],
    konular: list[Konu],
    gonderici: MailGonderici,
    ayarlar: Ayarlar,
    bugun: date | None = None,
    simdi: datetime | None = None,
    izlenenler: list | None = None,
    istek_id: int | None = None,
) -> GunlukSonuc:
    """`istek_id` tarama panelden "Şimdi tara" ile istendiyse verilir, çalışma ve rapor o isteğe bağlanır."""
    simdi = simdi or datetime.now()
    bugun = bugun or simdi.date()

    # Önce bütün kaynakları tara, kayıtları yaz, içerikleri indir.
    calisma = pipeline.calistir(session, client, kaynaklar, konular, bugun=bugun, simdi=simdi, izlenenler=izlenenler)
    sonuc = GunlukSonuc(calisma=calisma, rapor=None)
    # Panelden "Şimdi tara" ile istendiyse çalışmayı o isteğe bağla (panel sonucu gösterebilsin).
    if istek_id is not None:
        zamanlama.istege_yaz(session, istek_id, calisma_id=calisma.id)

    # Taramada hata olduysa sorun listesine ekle.
    if calisma.hata:
        sonuc.sorunlar.append(f"Çalışma #{calisma.id} hata verdi:\n{calisma.hata}")

    # Sonra henüz raporlanmamış ilgili kayıt varsa rapor yap ve onaylayıcılara onay bekliyor maili at.
    onaylayicilar = onaylayici_adresleri(session)
    try:
        sonuc.rapor = rapor.onaya_sun(session, gonderici, onaylayicilar, bugun=bugun,
                                       panel_adresi=ayarlar.panel_adresi, client=client, ek_ekle=ayarlar.ek_ekle)
    except Exception as e:
        session.rollback()
        log.exception("Rapor hazırlanamadı")
        sonuc.sorunlar.append(f"Rapor hazırlanamadı: {e!r}")
    # Rapor oluştuysa onu da isteğe bağla.
    if istek_id is not None and sonuc.rapor is not None:
        zamanlama.istege_yaz(session, istek_id, rapor_id=sonuc.rapor.id)
    # Onay maili gönderilemediyse sorun yaz, gitti ama panel adresi yoksa (linksiz gitti) onu da sorun yaz.
    if sonuc.rapor and sonuc.rapor.hata:
        sonuc.sorunlar.append(f"Rapor #{sonuc.rapor.id}: {sonuc.rapor.hata}")
    elif sonuc.rapor and onaylayicilar and not ayarlar.panel_adresi:
        sonuc.sorunlar.append(PANEL_ADRESI_YOK.format(rapor_id=sonuc.rapor.id))

    # Onaylanmış ama dağıtım maillerinin bir kısmı gidememiş raporlar, sadece gitmeyenleri tekrar dene.
    for r in rapor.dagitim_bekleyenler(session):
        try:
            if not rapor.dagit(session, client, gonderici, r, ayarlar.ek_ekle) and r.hata:
                sonuc.sorunlar.append(f"Rapor #{r.id} onaylandı ama dağıtılamadı: {r.hata}")
        except rapor.DurumHatasi:
            pass  # o anda panelden karar değişmiş, dokunulmaz

    # Gönderilip gönderilmediği bilinmeyen mailler, otomatik tekrar YOK (çift mail riski), insan karar verir.
    for g in rapor.belirsiz_gonderimler(session):
        sonuc.sorunlar.append(
            f"Rapor #{g.rapor_id}, gönderim #{g.id} ({', '.join(g.alicilar)}) "
            f"{g.gonderim_denemesi:%d.%m.%Y %H:%M}'de gönderilmeye başlandı ama sonucu kaydedilemedi. "
            f"Bu alıcıların posta kutusunu kontrol edin, sonra: "
            f"`mevzuat.cli gonderim-durum --id {g.id} --gonderildi` ya da `--tekrar-gonder`."
        )

    # Uzun süredir onay bekleyen raporlar, sorumluya günde bir hatırlatma, gecikirse yöneticiye uyarı.
    # 1 günden uzun bekleyen raporlar.
    bekleyen = [r for r in rapor.onay_bekleyenler(session) if simdi - r.olusturuldu > ayarlar.onay_hatirlatma]
    # Varsa ve bugünün ilk çalışmasıysa onaylayıcılara hatırlatma maili.
    if bekleyen and onaylayicilar and _gunun_ilk_calismasi(session, calisma):
        _guvenli_gonder(gonderici, _basit_mail(
            f"Hatırlatma: {len(bekleyen)} mevzuat raporu onayınızı bekliyor",
            [f"Rapor #{r.id} ({r.olusturuldu:%d.%m.%Y}): {r.konu}" for r in bekleyen]
            + ["Onaylanmadan rapor kimseye gönderilmez. Mevzuat Takip onay paneline giriş yapın."]
            + ([f"Onay paneli: {ayarlar.panel_adresi}"] if ayarlar.panel_adresi else []),
            onaylayicilar,
        ))
    # 3 günden uzun bekleyenler yöneticiye sorun olarak bildirilir.
    for r in bekleyen:
        if simdi - r.olusturuldu > ayarlar.onay_gecikme_uyarisi:
            sonuc.sorunlar.append(f"Rapor #{r.id} {r.olusturuldu:%d.%m.%Y}'den beri onay bekliyor.")

    # Uzun süredir pasif olan hesapları sil.
    try:
        for eposta in guvenlik.pasifleri_sil(session, ayarlar.pasif_silme_gun, simdi):
            log.info("Pasif hesap silindi: %s", eposta)
    except Exception as e:
        session.rollback()
        log.exception("Pasif hesaplar silinemedi")
        sonuc.sorunlar.append(f"Pasif hesaplar silinemedi: {e!r}")

    # Süresi dolan raporları ve denetim kayıtlarını sil.
    try:
        silinen = saklama.eskileri_sil(session, ayarlar.saklama_gun, simdi)
        if silinen.rapor or silinen.denetim:
            log.info("Eski kayıtlar silindi: %s", silinen)
    except Exception as e:
        session.rollback()
        log.exception("Eski kayıtlar silinemedi")
        sonuc.sorunlar.append(f"Eski kayıtlar silinemedi: {e!r}")

    # Sonra takılan kaynakları sorunlara ekle.
    sonuc.sorunlar += saglik_kontrolu(session, kaynaklar, simdi, ayarlar.takilma_esigi)

    # Son olarak yönetici adresi tanımlıysa yönetici maillerini gönder.
    if ayarlar.admin_alicilari:
        # ...sorun varsa tek bir UYARI mailinde hepsini gönder.
        if sonuc.sorunlar:
            sonuc.uyari_gonderildi = _guvenli_gonder(
                gonderici,
                _basit_mail(f"UYARI: Mevzuat Takip — {len(sonuc.sorunlar)} sorun ({simdi:%d.%m.%Y %H:%M})",
                            sonuc.sorunlar, ayarlar.admin_alicilari),
            )
        # ...bugün nabız günüyse (Pazartesi) ve günün ilk çalışmasıysa NABIZ mailini gönder.
        if simdi.weekday() == ayarlar.nabiz_gunu and _gunun_ilk_calismasi(session, calisma):
            sonuc.nabiz_gonderildi = _guvenli_gonder(gonderici, _nabiz_mail(session, simdi, ayarlar.admin_alicilari))
    # Yönetici adresi yoksa sorunları en azından loga yaz.
    elif sonuc.sorunlar:
        log.error("Yönetici alıcısı tanımlı değil, şu sorunlar kimseye bildirilmedi: %s", sonuc.sorunlar)

    # Her sorunu loga da yaz.
    for s in sonuc.sorunlar:
        log.warning("SORUN: %s", s)
    return sonuc


# Mail göndermeyi dener, hata olursa programı çökertmez, False döner.
def _guvenli_gonder(gonderici: MailGonderici, mail: Mail) -> bool:
    try:
        gonderici.gonder(mail)
        return True
    except Exception:
        # Mail altyapısı tamamen çöktüyse uyarı da gidemez, geriye log ve haftalık nabzın gelmemesi kalır.
        log.exception("Yönetici maili gönderilemedi: %s", mail.konu)
        return False

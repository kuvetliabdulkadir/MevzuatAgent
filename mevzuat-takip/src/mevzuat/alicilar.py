"""Alıcı grupları. Onaylanan raporun kime ve hangi kalemlerle gideceği burada belirlenir.

Kurallar kullanıcıyla 2026-10-02'de netleştirildi.
  - Grup sadece kendi iş kollarındaki kalemleri alır. Hiç kalemi yoksa o gruba mail gitmez.
  - "Ortak" kalemler kendiliğinden herkese gitmez. Hangi grubun alacağına grubu yöneten karar verir,
    bunun için grubun iş kollarına "Ortak" eklenir. İş kolu belirlenemeyen kalem "Ortak" sayılır.
  - Birden çok gruptaki kişi tek mail alır, içinde gruplarının bütün kalemleri olur.
  - Her kişiye ayrı mail gider, To alanında sadece kendisi olur. Böylece alıcılar birbirinin adresini görmez,
    kişiye özel eklenen dış adres (örneğin mali müşavir) çalışan adreslerini öğrenmez. Bcc kullanılmaz, çünkü
    boş To ile Bcc bazı sunucularda spam sayılıyor, ayrıca gönderim takibi kişi başına olmalı.
  - Gruplar panelden yönetilir, admin ve onaylayıcı yönetebilir. Silinmez, pasifleştirilir.
"""
# Kimin hangi maili alacağı burada belirleniyor. Bu dosya rapordaki kalemleri gruplara ve kişilere dağıtan dağıtım planını hesaplar.

import re
from dataclasses import dataclass
from datetime import datetime

# func, SQL fonksiyonları (lower gibi) için.
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mevzuat.db import AliciGrubu, Kayit

# İş kolu belirlenemeyen kalemlerin düştüğü iş kolu adı.
ORTAK = "Ortak"
# Kasıtlı olarak basit, amaç yazım hatasını (boşluk, eksik @, eksik alan adı) yakalamak, adresin
# gerçekten var olup olmadığını ancak mail sunucusu bilir.
# Kalıp, "bir şeyler @ bir şeyler . bir şeyler", aralarında boşluk/virgül yok.
_EPOSTA = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


# Bir kalemin iş kolları, hiç yoksa "Ortak".
def kalem_is_kollari(kayit: Kayit) -> list[str]:
    return kayit.is_kollari or [ORTAK]


# Veritabanındaki aktif grupları ada göre sıralı getirir.
def aktif_gruplar(session: Session) -> list[AliciGrubu]:
    return list(session.scalars(select(AliciGrubu).where(AliciGrubu.aktif).order_by(AliciGrubu.ad)))


# Bir grubun alacağı kalemler, kalemin iş kollarıyla grubun iş kollarında ortak olan varsa. & işareti iki kümenin kesişimi demek.
def grubun_kalemleri(grup: AliciGrubu, kayitlar: list[Kayit]) -> list[Kayit]:
    return [k for k in kayitlar if set(kalem_is_kollari(k)) & set(grup.is_kollari)]


# Planlanan tek bir mail, kime, hangi kalemler, hangi gruplar yüzünden.
@dataclass
class PlanliMail:
    alicilar: list[str]  # yeni planlarda tek adres, eski (çok alıcılı) gönderimler de aynı yapıda
    kayit_idler: list[int]  # rapordaki sırayla
    gruplar: list[str]  # bu adreslerin üyesi olduğu (ve kalem aldığı) gruplar


# Onaylayıcının elle eklediği adreslerin "grup" adı.
KISIYE_OZEL = "Kişiye özel"


# Dağıtım planını çıkarır, her adres için hangi kalemleri alacağı. Sonuç, adres başına bir mail.
def dagitim_plani(gruplar: list[AliciGrubu], kayitlar: list[Kayit], ek_adresler: list[str] = ()) -> list[PlanliMail]:
    """Her adres için ayrı bir mail planlar, içinde adresin gruplarından gelen bütün kalemler olur.
    `ek_adresler` onaylayıcının kişiye özel eklediği adreslerdir, bunlar iş kolundan bağımsız olarak bütün kalemleri alır.

    Örnek. Kuyum grubu {a, b} kalem 1 ve 2'yi, Döviz grubu {b, c} kalem 3'ü alıyorsa a kalem 1 ve 2'yi alır,
    b iki grupta olduğu için tek mailde kalem 1, 2 ve 3'ü alır, c de kalem 3'ü alır. Toplam üç mail gider.
    Kuyum grubundaki a ile b aynı kalemleri alsa da iki mail gider, a'ya ve b'ye ayrı ayrı.
    """
    # Her adresin alacağı kalem numaraları ve üyesi olduğu gruplar.
    adres_kalemleri: dict[str, set[int]] = {}
    adres_gruplari: dict[str, set[str]] = {}
    for grup in gruplar:
        # Bu grubun alacağı kalemlerin numaraları.
        idler = {k.id for k in grubun_kalemleri(grup, kayitlar)}
        # Grubun alacağı kalem yoksa bu gruba mail yok.
        if not idler:
            continue
        # Grubun her adresine bu kalemleri ekle (setdefault, adres ilk kez geçiyorsa boş küme aç).
        for adres in grup.adresler:
            adres_kalemleri.setdefault(adres, set()).update(idler)
            adres_gruplari.setdefault(adres, set()).add(grup.ad)
    # Kişiye özel adresler bütün kalemleri alır.
    for adres in ek_adresler:
        adres_kalemleri.setdefault(adres, set()).update(k.id for k in kayitlar)
        adres_gruplari.setdefault(adres, set()).add(KISIYE_OZEL)

    # Kalemlerin rapordaki sırası (mailde de aynı sırada görünsünler).
    sira = {k.id: i for i, k in enumerate(kayitlar)}
    # Her adres için bir PlanliMail oluştur.
    plan = [
        PlanliMail(
            alicilar=[adres],
            kayit_idler=sorted(idler, key=sira.__getitem__),
            gruplar=sorted(adres_gruplari[adres]),
        )
        for adres, idler in adres_kalemleri.items()
    ]
    # En kapsamlı mail önce, aynı kalemleri alanlar yan yana (panel onları bir arada gösterir).
    return sorted(plan, key=lambda m: (-len(m.kayit_idler), m.kayit_idler, m.alicilar))


# Onaylayıcının ekranda işaretlediği grupları veritabanından bulur.
def secilen_gruplar(session: Session, grup_idler: set[int]) -> list[AliciGrubu]:
    """Onaylayıcının işaretlediği gruplar. Pasif ya da olmayan bir grup seçilmişse hata (eski bir ekran gönderilmiş olabilir)."""
    gruplar = [g for g in aktif_gruplar(session) if g.id in grup_idler]
    # İstenen sayı ile bulunan sayı tutmuyorsa biri silinmiş/pasifleşmiş.
    if len(gruplar) != len(grup_idler):
        raise ValueError("Seçilen gruplardan biri bulunamadı ya da pasif. Sayfayı yenileyip tekrar seçin.")
    return gruplar


# Kutuya yapıştırılan adres yığınını tek tek adreslere ayırır, geçerli ve hatalı olanları ayrı listeler.
def adresleri_ayikla(metin: str) -> tuple[list[str], list[str]]:
    """Kutuya yapıştırılan adresleri geçerli ve hatalı diye ikiye ayırır. Adresler satırla, virgülle ya da noktalı virgülle ayrılmış olabilir.
    Hepsi küçük harfe çevrilir ve tekrarlar atılır, aynı kişi iki kez yazılırsa iki mail almasın."""
    gecerli: list[str] = []
    hatali: list[str] = []
    # Boşluk, virgül ya da noktalı virgülden böl.
    for parca in re.split(r"[\s,;]+", metin):
        adres = parca.strip().lower()
        if not adres:
            continue
        # Kalıba uymuyorsa hatalı listesine (kullanıcının yazdığı haliyle).
        if not _EPOSTA.match(adres):
            hatali.append(parca.strip())
        # Uyuyorsa ve daha önce eklenmediyse geçerli listesine.
        elif adres not in gecerli:
            gecerli.append(adres)
    return gecerli, hatali


# Grubun o anki halini sözlük olarak verir (denetim kaydına "önce/sonra" diye yazmak için).
def grup_bilgisi(grup: AliciGrubu) -> dict:
    """Denetim kaydı için grubun o anki hali."""
    return {"ad": grup.ad, "is_kollari": list(grup.is_kollari), "adresler": list(grup.adresler), "aktif": grup.aktif}


# Panelden gelen grup formunu kontrol edip kaydeder. grup None ise yeni grup oluşturur.
def grup_kaydet(
    session: Session,
    grup: AliciGrubu | None,
    ad: str,
    is_kollari: list[str],
    adres_metni: str,
    aktif: bool,
) -> AliciGrubu:
    """Yeni grup ekler ya da var olanı günceller (commit etmez). Hatalı girişte ValueError."""
    # Addaki fazla boşlukları temizle.
    ad = " ".join(ad.split())
    if not ad:
        raise ValueError("Grup adı boş olamaz.")
    if len(ad) > 100:
        raise ValueError("Grup adı en fazla 100 karakter olabilir.")
    # Büyük küçük harf fark etmeden aynı adda başka grup var mı diye bak.
    ayni_ad = session.scalar(select(AliciGrubu).where(func.lower(AliciGrubu.ad) == ad.lower()))
    # Varsa ve düzenlediğimiz grubun kendisi değilse reddet.
    if ayni_ad is not None and ayni_ad is not grup:
        raise ValueError(f"'{ad}' adında bir grup zaten var.")
    # İş kollarını temizle, tekrarları at, sırala.
    is_kollari = sorted({ik.strip() for ik in is_kollari if ik.strip()})
    if not is_kollari:
        raise ValueError("En az bir iş kolu seçilmeli.")
    # Adresleri ayıkla, hatalı varsa hangileri olduğunu söyle.
    adresler, hatali = adresleri_ayikla(adres_metni)
    if hatali:
        raise ValueError(f"Geçersiz e-posta adresi: {', '.join(hatali)}")
    # Aktif bir grubun en az bir adresi olmalı (yoksa kalemler kimseye gitmez).
    if aktif and not adresler:
        raise ValueError("Aktif grupta en az bir e-posta adresi olmalı.")

    # Yeni grupsa oluşturup veritabanı oturumuna ekle.
    if grup is None:
        grup = AliciGrubu()
        session.add(grup)
    # Alanları güncelle.
    grup.ad, grup.is_kollari, grup.adresler, grup.aktif = ad, is_kollari, adresler, aktif
    grup.guncellendi = datetime.now()
    # flush, değişikliği veritabanına gönder (grup numarası oluşsun) ama henüz kesinleştirme (commit'i çağıran yapar).
    session.flush()
    return grup

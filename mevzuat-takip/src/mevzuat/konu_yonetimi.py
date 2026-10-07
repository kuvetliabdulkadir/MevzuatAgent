"""Panelden konu yönetimi (#10). Konu eklenir, düzenlenir, pasifleştirilir, kaydetmeden önizlenir.

Her kayıt `filtre.konu_olustur` fonksiyonundan geçer, taramayla aynı doğrulama yapılır. Değişiklik sonraki taramalardan itibaren
geçerli olur. Geçmiş kayıtlar yeniden değerlendirilmez, onaylanmış raporlar değişmesin diye.

Yeniden adlandırmada eski ad konunun `eski_adlar` alanına yazılır. izlenen_mevzuat.toml (Docker'da salt okunur) ve
eski kayıtlar eski adı kullanmaya devam eder ama yine bu konuya bağlanır. Kaynakların varsayılan konuları da aynı
işlemde yeni ada çevrilir.

Konu bir kaynağın varsayılan konusuysa ya da izlenen_mevzuat.toml'da kullanılıyorsa pasifleştirilemez.
Yoksa gece taraması "tanımsız konu" hatasıyla hiç başlamaz.
"""
# Paneldeki "Konular" sayfasının arkasındaki iş, konu ekleme/düzenleme/pasifleştirme ve
# "kaydetmeden önce son 90 günde neler değişirdi" önizlemesi.

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat import surum
from mevzuat.db import Kayit, KaynakTanimi, KonuTanimi
from mevzuat.filtre import Konu, eslesmeler, konu_olustur, tr_kucuk

# Önizleme son 90 günün başlıklarına bakar.
ONIZLEME_GUN = 90
ONIZLEME_EN_FAZLA = 200  # liste başına satır
KISA_KELIME = 4  # harf, daha kısa kök başka kelimelerin başına da uyar ("harç", sonra "harcırah")


# "Sen düzenlerken başkası değiştirdi" hatası.
class CakismaHatasi(Exception):
    """Kayıt, form açıldıktan sonra başkası tarafından değiştirilmiş."""


# Panelden gelen konu formunun alanları.
@dataclass
class KonuFormu:
    ad: str
    is_kollari: list[str]
    kelimeler: list[str]
    haric: list[str]
    dislanan: list[str]
    aciklama: str = ""  # bu konu bizi neden ilgilendiriyor, raporda görünür


# Listeyi temizler, fazla boşluk, boş öğe ve (Türkçe büyük/küçük harf fark etmeden) tekrarlar gider.
def _liste(degerler: list[str]) -> list[str]:
    """Boşlukları sadeleştirir, boş olanları ve Türkçe küçük harfe göre tekrar edenleri atar, girilen sırayı korur."""
    sonuc, gorulen = [], set()
    for d in degerler:
        d = " ".join(str(d).split())
        if d and tr_kucuk(d) not in gorulen:
            gorulen.add(tr_kucuk(d))
            sonuc.append(d)
    return sonuc


# Formu temizleyip sınırları kontrol eder, taramadaki kontrolün aynısından geçirir.
def temizle(form: KonuFormu) -> KonuFormu:
    ad = " ".join(form.ad.split())
    if len(ad) > 200:
        raise ValueError("Konu adı en fazla 200 karakter olabilir.")
    temiz = KonuFormu(ad, _liste(form.is_kollari), _liste(form.kelimeler), _liste(form.haric), _liste(form.dislanan))
    # Aşırı uzun listelere/öğelere izin verme (biri sistemi yormak için dev bir liste yollamasın).
    for liste in (temiz.kelimeler, temiz.haric, temiz.dislanan, temiz.is_kollari):
        if len(liste) > 500 or any(len(x) > 200 for x in liste):
            raise ValueError("Liste en fazla 500 öğe, öğe en fazla 200 karakter olabilir.")
    # Taramayla aynı doğrulama, açıklama da burada sadeleşir.
    temiz.aciklama = konu_olustur(temiz.ad, temiz.kelimeler, temiz.is_kollari, temiz.haric, temiz.dislanan,
                                  form.aciklama).aciklama
    return temiz


# Konunun halini sözlük olarak verir (panelde göstermek ve denetim kaydına yazmak için).
def konu_bilgisi(k: KonuTanimi) -> dict:
    return {"id": k.id, "ad": k.ad, "is_kollari": list(k.is_kollari), "kelimeler": list(k.kelimeler),
            "haric": list(k.haric), "dislanan": list(k.dislanan), "aciklama": k.aciklama, "aktif": k.aktif,
            "eski_adlar": list(k.eski_adlar)}


# ---- kullanım bilgisi -----------------------------------------------------------------------------------

# Takip listesi dosyasında (izlenen_mevzuat.toml) kullanılan konu adları.
def izlenen_konulari(yol: Path | None = None) -> set[str]:
    """izlenen_mevzuat.toml'da geçen konu adları (dosya yoksa ya da takip kapalıysa boş)."""
    yol = yol or Path(os.environ.get("MEVZUAT_IZLENEN", "config/izlenen_mevzuat.toml"))
    if not yol.exists():
        return set()
    return {i.konu for i in (surum.izlenenleri_yukle(yol) or []) if i.konu}


# Konunun şimdiki ve eski bütün adları.
def _adlari(k: KonuTanimi) -> set[str]:
    return {k.ad, *k.eski_adlar}


# Konu nerede kullanılıyor, hangi kaynakların "her kaydı ilgili" konusu, takip listesinde var mı.
def kullanimi(session: Session, k: KonuTanimi, izlenen: set[str]) -> dict:
    kaynaklar = [t.etiket for t in session.scalars(select(KaynakTanimi).order_by(KaynakTanimi.sira))
                 if k.ad in t.varsayilan_konular]
    return {"kaynaklar": kaynaklar, "izlenen": bool(_adlari(k) & izlenen)}


# Konular sayfasındaki liste, her konu + son 90 günde kaç kayıtta eşleşti + kullanım bilgisi.
def konu_listesi(session: Session, izlenen: set[str], bugun: date | None = None) -> list[dict]:
    """Panel listesi. Her konunun tanımı, son 90 günde eski adlarıyla birlikte kaç kayıtta eşleştiği ve nerede kullanıldığı."""
    sinir = (bugun or date.today()) - timedelta(days=ONIZLEME_GUN)
    # Son 90 günün ilgili kayıtlarında her konu adının kaç kez geçtiğini say.
    sayilar: dict[str, int] = {}
    for (eslesen,) in session.execute(select(Kayit.eslesmeler).where(Kayit.yayin_tarihi >= sinir, Kayit.ilgili)):
        for ad in eslesen or {}:
            sayilar[ad] = sayilar.get(ad, 0) + 1
    sonuc = []
    # Önce aktifler, sonra pasifler.
    for k in session.scalars(select(KonuTanimi).order_by(KonuTanimi.aktif.desc(), KonuTanimi.id)):
        sonuc.append({**konu_bilgisi(k), "surum": k.surum, "guncelleme": k.guncelleme.isoformat(timespec="minutes"),
                      # Eski adlarla eşleşenleri de say.
                      "eslesme_90": sum(sayilar.get(ad, 0) for ad in _adlari(k)),
                      "kullanim": kullanimi(session, k, izlenen)})
    return sonuc


# ---- uyarılar ve önizleme -------------------------------------------------------------------------------

# Kaydı engellemeyen ama dikkat çeken uyarılar, çok kısa kelime, başka konuda da olan kelime.
def uyarilar(session: Session, form: KonuFormu, konu_id: int | None) -> list[str]:
    """Kaydı engellemeyen uyarılar, örneğin kısa kök ya da başka konuda da geçen kelime. Sadece yeni eklenen kelimelere bakılır,
    konuda zaten olan kelime için her düzenlemede uyarı çıkması gürültü olurdu."""
    mesajlar = []
    mevcut = session.get(KonuTanimi, konu_id) if konu_id is not None else None
    # Konuda daha önce olan kelimeler.
    onceki = {tr_kucuk(k) for k in mevcut.kelimeler} if mevcut else set()
    # Bu düzenlemede yeni eklenenler.
    yeniler = [k for k in form.kelimeler if tr_kucuk(k) not in onceki]
    # 4 harften kısa olanlar ("harç" gibi, "harcırah"ı da yakalar).
    kisa = [k for k in yeniler if len(tr_kucuk(k).replace(" ", "")) < KISA_KELIME]
    if kisa:
        mesajlar.append(f"Kısa kelime ({', '.join(kisa)}): kelime başına uyduğu için başka kelimeleri de yakalayabilir; "
                        "ifade kullanmayı düşünün.")
    # Yeni kelimelerden başka bir aktif konuda da olan varsa uyar.
    kendi = {tr_kucuk(k) for k in yeniler}
    for diger in session.scalars(select(KonuTanimi).where(KonuTanimi.aktif)):
        if diger.id == konu_id:
            continue
        ortak = sorted(kendi & {tr_kucuk(k) for k in diger.kelimeler})
        if ortak:
            mesajlar.append(f"'{diger.ad}' konusunda da var: {', '.join(ortak)}")
    return mesajlar


# "Etkisini gör", kaydedilmemiş yeni tanımı son 90 günün başlıklarına uygular, şimdiki tanımla karşılaştırır.
def onizleme(session: Session, form: KonuFormu, konu_id: int | None, bugun: date | None = None) -> dict:
    """Kaydedilmemiş tanımı son 90 günün başlıklarına uygular ve şimdiki tanımla karşılaştırır.
    Yeni tanımla eşleşecek ama şimdi eşleşmeyen, ve şimdi eşleşen ama düşecek başlıkları, hangi kelimeyle olduğunu da göstererek döner.
    Sadece başlığa bakılır. İçerikten genişletme sadece içeriği indirilen kayıtlarda olur, önizlemede yoktur."""
    form = temizle(form)
    # Yeni (formdaki) tanım.
    yeni = konu_olustur(form.ad, form.kelimeler, form.is_kollari, form.haric, form.dislanan)
    # Eski (veritabanındaki) tanım, yeni konuysa yok.
    mevcut = session.get(KonuTanimi, konu_id) if konu_id is not None else None
    eski = (konu_olustur(mevcut.ad, mevcut.kelimeler, mevcut.is_kollari, mevcut.haric, mevcut.dislanan)
            if mevcut else None)
    # Diğer aktif konular (düşecek bir kayıt başka konuya yine takılıyor mu göstermek için).
    digerleri = [konu_olustur(k.ad, k.kelimeler, k.is_kollari, k.haric, k.dislanan)
                 for k in session.scalars(select(KonuTanimi).where(KonuTanimi.aktif)) if k.id != konu_id]
    sinir = (bugun or date.today()) - timedelta(days=ONIZLEME_GUN)
    eslesecek, dusecek, ayni, taranan = [], [], 0, 0
    # Son 90 günün her kaydı için eski ve yeni tanımla eşleşme durumunu karşılaştır.
    for kayit in session.scalars(select(Kayit).where(Kayit.yayin_tarihi >= sinir)
                                 .order_by(Kayit.yayin_tarihi.desc(), Kayit.id.desc())):
        taranan += 1
        once = _eslesen(kayit.baslik, eski)
        sonra = _eslesen(kayit.baslik, yeni)
        # Durum değişmediyse (ikisinde de eşleşiyor ya da ikisinde de eşleşmiyor) sadece say.
        if bool(once) == bool(sonra):
            ayni += bool(sonra)
            continue
        satir = {"baslik": kayit.baslik, "tarih": kayit.yayin_tarihi.isoformat(), "kaynak": kayit.kaynak,
                 "kelimeler": sonra or once}
        # Yeni tanımla eşleşiyorsa "eşleşecek", eşleşmiyorsa "düşecek" (ve başka konuya takılıyor mu).
        if sonra:
            eslesecek.append(satir)
        else:
            satir["baska_konular"] = sorted(eslesmeler(kayit.baslik, digerleri))
            dusecek.append(satir)
    return {"gun": ONIZLEME_GUN, "taranan": taranan, "ayni_kalan": ayni,
            "eslesecek_sayisi": len(eslesecek), "dusecek_sayisi": len(dusecek),
            "eslesecek": eslesecek[:ONIZLEME_EN_FAZLA], "dusecek": dusecek[:ONIZLEME_EN_FAZLA],
            "uyarilar": uyarilar(session, form, konu_id)}


# Bir başlığın tek bir konuyla eşleşen kelimeleri (konu yoksa boş).
def _eslesen(baslik: str, konu: Konu | None) -> list[str]:
    return eslesmeler(baslik, [konu]).get(konu.ad, []) if konu else []


# ---- kaydetme -------------------------------------------------------------------------------------------

# Bu ad ya da eski adlarından biri başka bir konuda kullanılıyorsa hata verir.
def _ad_bos_mu(session: Session, ad: str, konu: KonuTanimi | None) -> None:
    for diger in session.scalars(select(KonuTanimi)):
        if diger is konu:
            continue
        if tr_kucuk(ad) in {tr_kucuk(a) for a in _adlari(diger)}:
            raise ValueError(f"'{ad}' adı (ya da eski adı) başka bir konuda kullanılıyor: {diger.ad}")


# Değişiklik damgası, sürüm +1, zaman, kim yaptı.
def _isaretle(satir, kullanici_id: int) -> None:
    satir.surum += 1
    satir.guncelleme = datetime.now()
    satir.guncelleyen_id = kullanici_id


# Yeni konu ekler.
def konu_ekle(session: Session, form: KonuFormu, kullanici_id: int) -> KonuTanimi:
    form = temizle(form)
    _ad_bos_mu(session, form.ad, None)
    konu = KonuTanimi(ad=form.ad, is_kollari=form.is_kollari, kelimeler=form.kelimeler, haric=form.haric,
                      dislanan=form.dislanan, aciklama=form.aciklama, eski_adlar=[], aktif=True, surum=1, guncelleme=datetime.now(),
                      guncelleyen_id=kullanici_id)
    session.add(konu)
    session.flush()
    return konu


# Var olan konuyu düzenler. Ad değiştiyse eski adı saklar ve kaynaklardaki referansları yeni ada çevirir.
def konu_degistir(session: Session, konu: KonuTanimi, form: KonuFormu, surum: int, kullanici_id: int) -> None:
    # Formu açtıktan sonra başkası değiştirdiyse üstüne yazma.
    if konu.surum != surum:
        raise CakismaHatasi("Siz düzenlerken bu konu başkası tarafından değiştirildi. Sayfayı yenileyip tekrar deneyin.")
    form = temizle(form)
    # Ad değişiyorsa.
    if form.ad != konu.ad:
        _ad_bos_mu(session, form.ad, konu)
        # Bu konuyu "her kaydı ilgili" olarak kullanan kaynaklarda adı güncelle.
        for kaynak in session.scalars(select(KaynakTanimi)):
            if konu.ad in kaynak.varsayilan_konular:
                kaynak.varsayilan_konular = [form.ad if k == konu.ad else k for k in kaynak.varsayilan_konular]
                _isaretle(kaynak, kullanici_id)
        # Eski adı eski_adlar listesine ekle (yeni adla aynıysa ekleme).
        konu.eski_adlar = [a for a in [*konu.eski_adlar, konu.ad] if a != form.ad]
        konu.ad = form.ad
    # Diğer alanları güncelle.
    konu.is_kollari, konu.kelimeler = form.is_kollari, form.kelimeler
    konu.haric, konu.dislanan = form.haric, form.dislanan
    konu.aciklama = form.aciklama
    _isaretle(konu, kullanici_id)


# Konuyu aktif/pasif yapar. Pasifleştirmeyi tehlikeli durumlarda engeller.
def aktiflik(session: Session, konu: KonuTanimi, aktif: bool, surum: int, kullanici_id: int, izlenen: set[str]) -> None:
    if konu.surum != surum:
        raise CakismaHatasi("Siz düzenlerken bu konu başkası tarafından değiştirildi. Sayfayı yenileyip tekrar deneyin.")
    if konu.aktif == aktif:
        raise ValueError("Konu zaten aktif." if aktif else "Konu zaten pasif.")
    # Pasifleştirme.
    if not aktif:
        kullanim = kullanimi(session, konu, izlenen)
        # Bir kaynak bu konuya bağlıysa engelle (yoksa tarama "tanımsız konu" hatasıyla durur).
        if kullanim["kaynaklar"]:
            raise ValueError(f"Bu konu şu kaynakların 'her kaydı ilgili' konusu: {', '.join(kullanim['kaynaklar'])}. "
                             "Önce kaynaktan çıkarın.")
        # Takip listesinde kullanılıyorsa engelle.
        if kullanim["izlenen"]:
            raise ValueError("Bu konu güncel metni takip edilen mevzuat listesinde (izlenen_mevzuat.toml) kullanılıyor; "
                             "pasifleşirse gece taraması başlamaz. Önce listeden çıkarılması gerekir (yönetici).")
        # Son aktif konuyu pasifleştirmeye izin verme.
        if not any(k.aktif and k.id != konu.id for k in session.scalars(select(KonuTanimi))):
            raise ValueError("En az bir aktif konu kalmalı.")
    # Aktifleştirme, bu ad o arada başka konuya verilmiş olabilir, kontrol et.
    else:
        _ad_bos_mu(session, konu.ad, konu)
    konu.aktif = aktif
    _isaretle(konu, kullanici_id)

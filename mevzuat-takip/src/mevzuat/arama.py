"""Arşivde tam metin araması (Faz 2, karar 14) ve konu filtresinin arşive uygulanması (Faz 3, karar 11).

Arama: kelime ya da "tırnak içinde ifade" yazılır, hangi mevzuatın hangi maddesinde geçtiği vurgulu parçayla gösterilir.
Filtreyle aynı Türkçe ek kuralı kullanılır ("altın" → "altının" bulunur, "altınbaş" bulunmaz; sonuna * yazılırsa başı
tutsun yeter). Aday satırlar PostgreSQL'de tam metin indeksinden (önek araması) gelir, SQLite'ta düz taramadan; ek kuralı
ve vurgu burada uygulanır. Dizin her yeni sürümde o mevzuat için yenilenir, `arsiv-dizin` hepsini baştan kurar.

Konu filtresi: her mevzuatın son sürümü bütün aktif konularla puanlanır (puan.py), sonuç arsiv_konu tablosunda.
Konular değişince `arsiv-filtre` (zamanlayıcı kendiliğinden başlatır) hepsini yeniden hesaplar.

Kelime sıklığı: `arsiv-filtre` önce her konu kelimesinin arşivde kaç mevzuatta geçtiğini sayar (sistem_ayarlari
"kelime_sikligi"). Puan bundan kelime ağırlığı çıkarır (puan.kelime_agirligi); günlük tarama da aynı ağırlığı kullanır.
"""

import html as html_lib
import logging
import re
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from mevzuat import turkce_ek
from mevzuat.db import ArsivArama, ArsivDosyasi, ArsivKonu, ArsivMevzuat, ArsivSurumu
from mevzuat.filtre import Konu, tr_kucuk
from mevzuat.surum import bolumler

log = logging.getLogger(__name__)

ADAY_SINIRI = 4000
SONUC_BOYUTU = 20
MEVZUAT_BASINA_PARCA = 3
PARCA_CEVRESI = 110


# --- dizin ---------------------------------------------------------------------------------------------------

def _satirlar(s: ArsivSurumu, session: Session) -> list[tuple[str, str]]:
    satir = [(ad, parca) for ad, parca in bolumler(s.metin).items()] if s.metin else []
    for e in s.ekler or []:
        d = session.get(ArsivDosyasi, e["sha256"]) if e.get("sha256") else None
        if d is not None and d.metin:
            satir.append((f"Ek: {e.get('ad')}"[:300], " ".join(d.metin.split())))
    if s.arama_metni:
        satir.append(("Sayfa görüntülerinin yazısı (OCR)", " ".join(s.arama_metni.split())))
    return satir


def dizini_guncelle(session: Session, anahtar: str) -> int:
    """Bir mevzuatın arama satırlarını son sürümüne göre yeniden yazar (commit etmez). Yazılan satır sayısı."""
    session.execute(delete(ArsivArama).where(ArsivArama.anahtar == anahtar))
    s = session.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar)
                       .order_by(ArsivSurumu.surum_no.desc()).limit(1))
    if s is None:
        return 0
    n = 0
    for i, (ad, ozgun) in enumerate(_satirlar(s, session)):
        # tr_kucuk boşlukları teke indirir; özgün metin zaten tek boşluklu (bolumler), yerler denk kalır.
        session.add(ArsivArama(anahtar=anahtar, surum_no=s.surum_no, sira=i, bolum=ad[:300], metin=tr_kucuk(ozgun),
                               ozgun=ozgun))
        n += 1
    return n


def dizini_kur(session: Session, ilerle=lambda i, t: None) -> int:
    anahtarlar = list(session.scalars(select(ArsivMevzuat.anahtar).where(ArsivMevzuat.surum_sayisi > 0)))
    toplam = 0
    for i, a in enumerate(anahtarlar, 1):
        toplam += dizini_guncelle(session, a)
        if i % 200 == 0:
            session.commit()
            ilerle(i, len(anahtarlar))
    session.commit()
    return toplam


# --- arama ---------------------------------------------------------------------------------------------------

def sorguyu_coz(sorgu: str) -> list[str]:
    """Sorgu terimleri: "tırnak içi" ifadeler ve tek kelimeler, Türkçe küçük harfle. En fazla 8 terim."""
    sorgu = sorgu.replace("“", '"').replace("”", '"')
    terimler = [tr_kucuk(t).strip() for t in re.findall(r'"([^"]+)"', sorgu)]
    kalan = re.sub(r'"[^"]*"', " ", sorgu)
    terimler += [tr_kucuk(k) for k in kalan.split()]
    temiz: list[str] = []
    for ham in terimler:
        t = " ".join(re.sub(rf"[^{turkce_ek.HARF}\s*\-']", " ", ham).split())
        if len(t.rstrip("*")) >= 2 and t not in temiz:
            temiz.append(t)
    return temiz[:8]


def _aday_kosulu(session: Session, terimler: list[str]):
    if session.bind.dialect.name == "postgresql":
        parcalar = []
        for t in terimler:
            kelimeler = [re.sub(rf"[^{turkce_ek.HARF}]", "", k) for k in t.rstrip("*").split()]
            kelimeler = [k for k in kelimeler if k]
            if kelimeler:
                parcalar.append(" & ".join(f"{k}:*" for k in kelimeler))
        if not parcalar:
            return None
        sorgu = " & ".join(f"({p})" for p in parcalar)
        return func.to_tsvector("simple", ArsivArama.metin).op("@@")(func.to_tsquery("simple", sorgu))
    # SQLite: her terimin kökü düz metinde geçmeli.
    return and_(*[ArsivArama.metin.contains(t.rstrip("*").split()[0], autoescape=True) for t in terimler])


def _vurgula(ozgun: str, normal: str, yerler: list[tuple[int, int]]) -> str:
    """İlk geçişin çevresinden parça, bütün geçişler <mark> içinde (özgün yazımla, HTML kaçışlı)."""
    if not yerler:
        return html_lib.escape(ozgun[:2 * PARCA_CEVRESI])
    yerler = sorted(yerler)
    bas = max(0, yerler[0][0] - PARCA_CEVRESI)
    son = min(len(ozgun), max(e for _, e in yerler[:6]) + PARCA_CEVRESI) if yerler[0][1] - bas < 600 else yerler[0][1] + PARCA_CEVRESI
    son = min(son, bas + 700)
    parcalar, i = [], bas
    for s, e in yerler:
        if s < i or e > son:
            continue
        parcalar.append(html_lib.escape(ozgun[i:s]))
        parcalar.append(f"<mark>{html_lib.escape(ozgun[s:e])}</mark>")
        i = e
    parcalar.append(html_lib.escape(ozgun[i:son]))
    return ("…" if bas else "") + "".join(parcalar) + ("…" if son < len(ozgun) else "")


def _aday_satirlar(session: Session, terimler: list[str], tur: str, konu: str) -> tuple[list, bool]:
    """Veritabanından aday madde satırları (anahtar, bölüm, sıra, normal metin, özgün metin) ve sınırın aşılıp aşılmadığı."""
    q = select(ArsivArama.anahtar, ArsivArama.bolum, ArsivArama.sira, ArsivArama.metin, ArsivArama.ozgun)
    if (kosul := _aday_kosulu(session, terimler)) is not None:
        q = q.where(kosul)
    if tur or konu:
        q = q.join(ArsivMevzuat, ArsivMevzuat.anahtar == ArsivArama.anahtar)
        if tur:
            q = q.where(ArsivMevzuat.tur == tur)
        if konu:
            q = q.join(ArsivKonu, and_(ArsivKonu.anahtar == ArsivArama.anahtar, ArsivKonu.konu == konu,
                                       ArsivKonu.ilgili.is_(True)))
    satirlar = session.execute(q.limit(ADAY_SINIRI + 1)).all()
    return satirlar[:ADAY_SINIRI], len(satirlar) > ADAY_SINIRI


def _maddelerde_bul(adaylar: list, terimler: list[str]) -> dict[str, list[dict]]:
    """Terimlerin hepsinin (ek kuralıyla) geçtiği maddeler, mevzuat mevzuat, vurgulu parçalarıyla."""
    bulunan: dict[str, list[dict]] = {}
    for anahtar, bolum, sira, normal, ozgun in adaylar:
        yerler = [turkce_ek.bul(t, normal) for t in terimler]
        if not all(yerler):
            continue
        hepsi = [y for liste in yerler for y in liste]
        bulunan.setdefault(anahtar, []).append({"bolum": bolum, "sira": sira, "gecis": len(hepsi),
                                                 "parca": _vurgula(ozgun, normal, hepsi)})
    return bulunan


def _baslikta_bul(session: Session, terimler: list[str], tur: str, konu: str) -> set[str]:
    """Başlığında terimlerin hepsi geçen mevzuat (metni olmayanlar dahil)."""
    q = select(ArsivMevzuat.anahtar, ArsivMevzuat.ad)
    for t in terimler:
        kok = t.rstrip("*").split()[0]
        q = q.where(or_(*[ArsivMevzuat.ad.contains(v, autoescape=True)
                          for v in (kok.upper(), kok.replace("i", "İ").replace("ı", "I").upper(), kok)]))
    if tur:
        q = q.where(ArsivMevzuat.tur == tur)
    if konu:
        q = q.where(ArsivMevzuat.anahtar.in_(select(ArsivKonu.anahtar).where(ArsivKonu.konu == konu,
                                                                            ArsivKonu.ilgili.is_(True))))
    return {a for a, ad in session.execute(q.limit(2000)) if all(turkce_ek.var_mi(t, tr_kucuk(ad)) for t in terimler)}


def _sonuc(m: ArsivMevzuat, maddeler: list[dict], baslikta: bool) -> dict:
    maddeler = sorted(maddeler, key=lambda p: (-p["gecis"], p["sira"]))
    return {"anahtar": m.anahtar, "ad": m.ad, "tur": m.tur, "rg_tarihi": m.rg_tarihi.isoformat() if m.rg_tarihi else None,
            "baslikta": baslikta, "madde_sayisi": len(maddeler), "gecis": sum(p["gecis"] for p in maddeler),
            "parcalar": [{"bolum": p["bolum"], "parca": p["parca"]} for p in maddeler[:MEVZUAT_BASINA_PARCA]]}


def ara(session: Session, sorgu: str, tur: str = "", konu: str = "", sayfa: int = 1, boyut: int = SONUC_BOYUTU) -> dict:
    """Arşivde arama. Sonuç mevzuat mevzuat (başlıkta geçenler önce, sonra çok geçenler), her birinde terimlerin geçtiği
    maddelerden vurgulu parçalar. `konu` verilirse sadece o konuyla ilgili mevzuat."""
    terimler = sorguyu_coz(sorgu)
    sayfa = max(1, sayfa)
    if not terimler:
        return {"sorgu": sorgu, "terimler": [], "toplam": 0, "sayfa": 1, "boyut": boyut, "sonuclar": [], "eksik": False}
    adaylar, eksik = _aday_satirlar(session, terimler, tur, konu)
    bulunan = _maddelerde_bul(adaylar, terimler)
    baslikta = _baslikta_bul(session, terimler, tur, konu)
    sirali = sorted(set(bulunan) | baslikta,
                    key=lambda a: (a not in baslikta, -sum(p["gecis"] for p in bulunan.get(a, [])), a))
    secili = sirali[(sayfa - 1) * boyut: sayfa * boyut]
    kunyeler = {m.anahtar: m for m in session.scalars(select(ArsivMevzuat).where(ArsivMevzuat.anahtar.in_(secili)))}
    sonuclar = [_sonuc(kunyeler[a], bulunan.get(a, []), a in baslikta) for a in secili if a in kunyeler]
    return {"sorgu": sorgu, "terimler": terimler, "toplam": len(sirali), "sayfa": sayfa, "boyut": boyut,
            "sonuclar": sonuclar, "eksik": eksik}


# --- konu filtresi ---------------------------------------------------------------------------------------------

def konulari_uygula(session: Session, konular: list[Konu], anahtar: str | None = None, simdi: datetime | None = None,
                    ilerle=lambda i, t: None) -> dict:
    """Son sürümleri konularla puanlar, arsiv_konu'ya yazar. `anahtar` verilirse sadece o mevzuat. Artık olmayan konuların
    satırları silinir. Dönüş: {konu: ilgili sayısı}."""
    from mevzuat import puan

    simdi = simdi or datetime.now()
    agirlik = kelime_agirliklari(session)
    adlar = [k.ad for k in konular]
    q = (select(ArsivMevzuat, ArsivSurumu).join(ArsivSurumu, and_(ArsivSurumu.anahtar == ArsivMevzuat.anahtar,
                                                                  ArsivSurumu.surum_no == ArsivMevzuat.surum_sayisi)))
    if anahtar:
        q = q.where(ArsivMevzuat.anahtar == anahtar)
        session.execute(delete(ArsivKonu).where(ArsivKonu.anahtar == anahtar))
    else:
        session.execute(delete(ArsivKonu))
    sayac = {a: 0 for a in adlar}
    anahtarlar = list(session.scalars(select(ArsivMevzuat.anahtar).where(ArsivMevzuat.anahtar.in_(
        select(q.subquery().c.anahtar)))))
    kokler = [k.rstrip("*")[:max(3, len(k.rstrip("*")) - 2)] for konu in konular for k in konu.kelimeler]
    # Okuma ve yazma aynı oturumda: satırlar 200'lük partilerle okunur (akış halinde okurken yazmak PostgreSQL'de sorun).
    for bas in range(0, len(anahtarlar), 200):
        parti = anahtarlar[bas:bas + 200]
        for m, s in session.execute(q.where(ArsivMevzuat.anahtar.in_(parti))).all():
            metin = s.metin or ""
            # Hızlı ön kontrol: hiçbir konu kökü geçmiyorsa puanlamaya gerek yok.
            normal = tr_kucuk(m.ad + " " + metin)
            if not any(k in normal for k in kokler):
                continue
            belge = puan.belge_hazirla(m.ad, metin)
            for k in konular:
                p = puan.puanla_hazir(belge, k, agirlik=agirlik)
                if p.puan <= 0:
                    continue
                session.add(ArsivKonu(anahtar=m.anahtar, konu=k.ad, surum_no=s.surum_no, puan=p.puan, ilgili=p.ilgili,
                                      kanit=[{"kelime": x.kelime, "bolum": x.bolum, "tur": x.tur, "parca": x.parca}
                                             for x in p.kanitlar], hesaplandi=simdi))
                sayac[k.ad] += int(p.ilgili)
        session.flush()
        ilerle(min(bas + 200, len(anahtarlar)), len(anahtarlar))
    session.commit()
    return sayac


# --- konu değişikliği takibi -------------------------------------------------------------------------------------

FILTRE_SURUMU = 2  # puanlama kuralı değişince artırılır, arşiv yeniden filtrelenir
IMZA_ANAHTARI = "arsiv_filtre_imzasi"


def filtre_imzasi(konular: list[Konu]) -> str:
    import hashlib
    import json

    from mevzuat import puan

    veri = [FILTRE_SURUMU, puan.ESIK, [[k.ad, list(k.kelimeler), list(k.haric), list(k.dislanan)] for k in konular]]
    return hashlib.sha256(json.dumps(veri, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def filtre_guncel_mi(session: Session, konular: list[Konu]) -> bool:
    from mevzuat.db import SistemAyari

    ayar = session.get(SistemAyari, IMZA_ANAHTARI)
    return ayar is not None and (ayar.deger or {}).get("imza") == filtre_imzasi(konular)


def dizin_eksik_mi(session: Session) -> bool:
    surumlu = session.scalar(select(func.count()).select_from(ArsivMevzuat).where(ArsivMevzuat.surum_sayisi > 0)) or 0
    dizinli = session.scalar(select(func.count(func.distinct(ArsivArama.anahtar)))) or 0
    return dizinli < surumlu


def tum_arsivi_filtrele(session: Session, konular: list[Konu], ilerle=lambda i, t: None) -> dict:
    from mevzuat.db import SistemAyari

    simdi = datetime.now()
    kelime_sikligi_hesapla(session, konular, simdi)
    sayac = konulari_uygula(session, konular, simdi=simdi, ilerle=ilerle)
    deger = {"imza": filtre_imzasi(konular), "hesaplandi": simdi.isoformat(timespec="seconds"), "ilgili": sayac}
    ayar = session.get(SistemAyari, IMZA_ANAHTARI)
    if ayar is None:
        session.add(SistemAyari(anahtar=IMZA_ANAHTARI, deger=deger, surum=1, guncelleme=simdi))
    else:
        ayar.deger, ayar.guncelleme, ayar.surum = deger, simdi, ayar.surum + 1
    session.commit()
    return sayac


# --- kelime sıklığı -----------------------------------------------------------------------------------------

SIKLIK_ANAHTARI = "kelime_sikligi"


def _son_metinler(session: Session):
    """Her mevzuatın son sürümünün (ad, metin) çifti, 200'lük partilerle (bellek ve PostgreSQL imleci için)."""
    anahtarlar = list(session.scalars(select(ArsivMevzuat.anahtar).where(ArsivMevzuat.surum_sayisi > 0)
                                      .order_by(ArsivMevzuat.anahtar)))
    q = (select(ArsivMevzuat.ad, ArsivSurumu.metin).join(ArsivSurumu, and_(
        ArsivSurumu.anahtar == ArsivMevzuat.anahtar, ArsivSurumu.surum_no == ArsivMevzuat.surum_sayisi)))
    for bas in range(0, len(anahtarlar), 200):
        yield from session.execute(q.where(ArsivMevzuat.anahtar.in_(anahtarlar[bas:bas + 200]))).all()


def kelime_sikligi_hesapla(session: Session, konular: list[Konu], simdi: datetime | None = None) -> dict:
    """Her konu kelimesinin arşivde kaç mevzuatta (başlık + son sürüm metni) geçtiğini sayar ve saklar. Konunun hariç
    ifadeleri ve ortak deyimler maskelenir, eşleşme filtreyle aynıdır. Aynı kelime iki konuda varsa büyük sayı alınır."""
    from mevzuat.db import SistemAyari
    from mevzuat.filtre import _maskele, hazirla

    simdi = simdi or datetime.now()
    ciftler = {(k, konu.haric) for konu in konular for k in konu.kelimeler}
    sayi: dict[tuple, int] = dict.fromkeys(ciftler, 0)
    toplam = 0
    for ad, metin in _son_metinler(session):
        toplam += 1
        normal = hazirla(f"{ad}\n{metin or ''}")
        maskeli: dict[tuple, str] = {}
        for k, haric in ciftler:
            kok = k.rstrip("*")[:max(3, len(k.rstrip("*")) - 2)]
            if kok not in normal:
                continue
            if haric not in maskeli:
                maskeli[haric] = _maskele(normal, haric) if haric else normal
            if turkce_ek.var_mi(k, maskeli[haric]):
                sayi[(k, haric)] += 1
    belge: dict[str, int] = {}
    for (k, _), n in sayi.items():
        belge[k] = max(n, belge.get(k, 0))
    deger = {"toplam": toplam, "belge": belge, "hesaplandi": simdi.isoformat(timespec="seconds")}
    ayar = session.get(SistemAyari, SIKLIK_ANAHTARI)
    if ayar is None:
        session.add(SistemAyari(anahtar=SIKLIK_ANAHTARI, deger=deger, surum=1, guncelleme=simdi))
    else:
        ayar.deger, ayar.guncelleme, ayar.surum = deger, simdi, ayar.surum + 1
    session.commit()
    return deger


def kelime_agirliklari(session: Session) -> dict[str, float]:
    """Saklanan sıklıktan kelime → ağırlık. Sıklık hesaplanmamışsa (arşiv boş) boş sözlük, yani her kelime 1."""
    from mevzuat import puan
    from mevzuat.db import SistemAyari

    ayar = session.get(SistemAyari, SIKLIK_ANAHTARI)
    if ayar is None or not (ayar.deger or {}).get("toplam"):
        return {}
    toplam = ayar.deger["toplam"]
    return {k: puan.kelime_agirligi(n, toplam) for k, n in ayar.deger.get("belge", {}).items()}

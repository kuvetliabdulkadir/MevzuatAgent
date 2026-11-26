"""Konu puanlaması (Faz 3, karar 10, 11, 17, 18). LLM yok, kaynaktan bağımsız, tek eşik, kademe yok.

Bir belgenin (başlık + metin) bir konuya ne kadar ilgili olduğu kelimelerin NEREDE ve NE KADAR geçtiğine bakılarak
hesaplanır. Puan arka plandadır; kullanıcı sadece "ilgili / değil" görür (karar 18), yanında "neden" (kanıt).

Bölümler metnin kendisinden çıkarılır (madde yapısı varsa), kaynağa özel kural yoktur (karar 17):
  başlık                     her farklı kelime BASLIK_PUANI
  amaç / kapsam (Madde 1-2)  ağırlık 3     belgenin neyi düzenlediği burada yazar
  tanımlar                   ağırlık 2
  gövde                      ağırlık 1
  dayanak, yürürlük, yürütme ağırlık 0,2   "5549 sayılı Kanuna dayanılarak" gibi atıflar konuyu ilgili yapmaz
Değişiklik metinlerinin Madde 1-2'si ("... ibaresi ... şeklinde değiştirilmiştir") amaç sayılmaz, "amaç/kapsam" yazıyorsa sayılır.
Madde yapısı yoksa (duyuru, mahkeme kararı, liste) ilk GIRIS_KELIME kelime amaç, kalanı gövde sayılır; yayılma puanı
verilmez, yoğunluk YAPISIZ_YOGUNLUK_EN_FAZLA ile sınırlıdır. Yani böyle bir metin, başlığında kelime yoksa ancak girişinde
de konu geçiyorsa ilgili olur.

Kelime ağırlığı (belge sıklığı): arşivde çok belgede geçen kelime ("ithalat" %13, "gümrük" %15) metinde daha az puan
getirir, başlıkta değil. Ağırlık arşivden hesaplanır (arama.kelime_agirliklari), arşiv yoksa her kelime 1 sayılır.

Puan = başlık + yoğunluk (10.000 kelimede ağırlıklı geçiş, üst sınırlı) + yayılma (kaç ayrı maddede geçtiği) +
amaç/kapsam bonusu - dışlanan ifade cezası. Uzun toplu metinler (KDV Genel Uygulama Tebliği, 182 bin kelime, 213 geçiş)
yoğunlukta geride kalsa da yayılmadan puan alır. Eşik test setiyle ayarlanır (filtre_olcum.py).
"""

import math
import re
from dataclasses import dataclass, field

from mevzuat import turkce_ek
from mevzuat.filtre import Konu, _maskele, genel_dislama, hazirla
from mevzuat.surum import bolumler

ESIK = 10.0
BASLIK_PUANI = 12.0
BASLIK_EN_FAZLA = 18.0
YOGUNLUK_EN_FAZLA = 12.0
TEK_MADDE_YOGUNLUK_EN_FAZLA = 6.0  # en az 3 maddelik metinde geçişlerin hepsi tek maddedeyse
YAPISIZ_YOGUNLUK_EN_FAZLA = 8.0
GIRIS_KELIME = 60
YOGUNLUK_OLCEGI = 4.0  # 10.000 kelimede ağırlıklı geçiş başına
YAYILMA_PUANI = 1.5  # geçtiği her ayrı madde
YAYILMA_EN_FAZLA = 12.0
AMAC_BONUSU = 6.0
AMAC_EN_AZ_GECIS = 2.0  # amaç/kapsamda ağırlıklı geçiş; tek geçiş ("ithalatçı", "tüketiciler") bonus vermez
AMAC_EN_AZ_KELIME = 1.5  # ya da farklı kelimelerin ağırlık toplamı
YOGUNLUK_EN_AZ_GECIS = 3.0  # bundan az ağırlıklı geçiş yoğunluk puanı almaz
YOGUNLUK_EN_AZ_KELIME = 1000  # kısa metinde tek geçiş yoğunluğu tavana çıkarmasın
YAYILMA_ORAN_CARPANI = 4.0  # yayılma, geçtiği maddelerin toplam maddeye oranıyla ölçeklenir
DISLANAN_CEZASI = 25.0
# Kelime ağırlığı: belge oranı SIKLIK_TABANI ve altındaysa 1, arttıkça logaritmik azalır, en az AGIRLIK_EN_AZ.
SIKLIK_TABANI = 0.01
AGIRLIK_EN_AZ = 0.3
KANIT_EN_FAZLA = 8

AGIRLIK = {"amac": 3.0, "tanim": 2.0, "govde": 1.0, "dayanak": 0.2, "baslangic": 1.0}

_DAYANAK = re.compile(r"dayanılarak|dayanak|yürürlüğe girer|yürürlükten kaldır|hükümlerini .{0,60}yürütür|yürütme", re.IGNORECASE)
_TANIM = re.compile(r"tanımlar|kısaltmalar|ifade eder|anlamına gelir|geçen (?:deyim|terim|kavram)", re.IGNORECASE)
_MADDE_NO = re.compile(r"Madde (\d+)")
_AMAC = re.compile(r"amaç|amac|kapsa", re.IGNORECASE)
_DEGISIKLIK = re.compile(r"değiştirilmiştir|eklenmiştir|yürürlükten kaldırılmıştır|ibaresi|aşağıdaki şekilde", re.IGNORECASE)


def kelime_agirligi(belge_sayisi: int, toplam: int) -> float:
    """Kelimenin geçtiği belge sayısından ağırlığı. Az belgede geçen ayırt edici kelime 1, her yerde geçen AGIRLIK_EN_AZ."""
    if toplam <= 0:
        return 1.0
    oran = (belge_sayisi + 1) / (toplam + 1)
    if oran <= SIKLIK_TABANI:
        return 1.0
    return max(AGIRLIK_EN_AZ, math.log(1 / oran) / math.log(1 / SIKLIK_TABANI))


@dataclass
class Kanit:
    kelime: str
    bolum: str
    tur: str  # baslik / amac / tanim / govde / dayanak
    parca: str  # geçtiği yerin çevresi (orijinal yazım)


@dataclass
class Puan:
    konu: str
    puan: float
    ilgili: bool
    kanitlar: list[Kanit] = field(default_factory=list)
    ayrinti: dict = field(default_factory=dict)


def _bolum_turu(ad: str, metin: str) -> str:
    if ad == "Başlangıç":
        return "baslangic"
    m = _MADDE_NO.match(ad)
    bas = metin[:300]
    if _DAYANAK.search(bas) and len(metin) < 1500:
        return "dayanak"
    if _TANIM.search(bas):
        return "tanim"
    if m and int(m.group(1)) <= 2 and not ad.startswith(("Geçici", "Ek ")) and (
            _AMAC.search(bas) or not _DEGISIKLIK.search(metin)):
        return "amac"
    return "govde"


def _parca(metin: str, bas: int, son: int, cevre: int = 90) -> str:
    a, b = max(0, bas - cevre), min(len(metin), son + cevre)
    return ("…" if a else "") + " ".join(metin[a:b].split()) + ("…" if b < len(metin) else "")


@dataclass
class Belge:
    """Puanlamaya hazırlanmış belge: bölümler bir kez ayrılır, Türkçe küçük harfe çevrilir, ortak deyimler maskelenir.
    Konuya özel olan sadece hariç ifadelerin maskelenmesidir (puanla_hazir)."""
    baslik: str
    baslik_normal: str
    bolumler: list[tuple[str, str, str, str]]  # (ad, tür, özgün, normal)
    kelime_sayisi: int
    yapisiz: bool = False  # madde yapısı yok: giriş + metin


def _yapisiz_bolumler(metin: str) -> dict[str, tuple[str, str]]:
    kelimeler = metin.split()
    giris, kalan = " ".join(kelimeler[:GIRIS_KELIME]), " ".join(kelimeler[GIRIS_KELIME:])
    return {"Giriş": (giris, "amac"), **({"Metin": (kalan, "govde")} if kalan else {})}


def belge_hazirla(baslik: str, metin: str) -> Belge:
    bolum_listesi = bolumler(metin) if metin else {}
    yapisiz = len(bolum_listesi) <= 1 and bool(metin)
    ayrilmis = _yapisiz_bolumler(metin) if yapisiz else {ad: (p, _bolum_turu(ad, p)) for ad, p in bolum_listesi.items()}
    liste, kelime = [], 0
    for ad, (parca, tur) in ayrilmis.items():
        kelime += len(parca.split())
        normal = hazirla(parca)
        liste.append((ad, tur, parca if len(normal) == len(parca) else normal, normal))
    return Belge(baslik, hazirla(baslik), liste, kelime, yapisiz)


def _hizli_var(konu: Konu, metin: str) -> bool:
    return any(k.rstrip("*")[:max(3, len(k.rstrip("*")) - 2)] in metin for k in konu.kelimeler)


@dataclass
class _Gecisler:
    """Metin bölümlerinde konu kelimelerinin geçişleri (puanın metin kısmının girdisi)."""
    agirlikli: float = 0.0
    maddeler: dict = field(default_factory=dict)  # madde adı → içindeki en ağırlıklı kelimenin ağırlığı
    amac_gecis: float = 0.0
    amac_kelime: dict = field(default_factory=dict)


def _bolumlerde_say(belge: Belge, konu: Konu, kanitlar: list[Kanit], agirlik: dict[str, float]) -> _Gecisler:
    g = _Gecisler()
    for ad, tur, ozgun, ortak in belge.bolumler:
        if not _hizli_var(konu, ortak):
            continue
        normal = _maskele(ortak, konu.haric)
        for k in konu.kelimeler:
            if not (yerler := turkce_ek.bul(k, normal)):
                continue
            w = agirlik.get(k, 1.0)
            g.agirlikli += AGIRLIK[tur] * len(yerler) * w
            if tur == "dayanak":
                continue  # atıf: ağırlığı düşük sayılır, yayılmaya ve kanıta girmez
            g.maddeler[ad] = max(w, g.maddeler.get(ad, 0.0))
            if tur == "amac":
                g.amac_gecis += len(yerler) * w
                g.amac_kelime[k] = w
            if len(kanitlar) < KANIT_EN_FAZLA:
                kanitlar.append(Kanit(k, ad, tur, _parca(ozgun, *yerler[0])))
    return g


def _yogunluk_puani(g: _Gecisler, belge: Belge, oran: float, toplam_madde: int) -> float:
    puan = 0.0
    if g.agirlikli >= YOGUNLUK_EN_AZ_GECIS:
        puan = g.agirlikli / max(belge.kelime_sayisi, YOGUNLUK_EN_AZ_KELIME) * 10_000 / YOGUNLUK_OLCEGI
    if belge.yapisiz:
        return min(YAPISIZ_YOGUNLUK_EN_FAZLA, puan)
    # Az yoğun ama metne yayılmış uzun toplu metinler (KDV Genel Uygulama Tebliği): log ölçekli toplam, yayılma oranıyla.
    if g.agirlikli >= 10 and oran >= 0.25:
        puan = max(puan, 2.5 * math.log2(1 + g.agirlikli))
    tek_madde = len(g.maddeler) < 2 and toplam_madde >= 3
    return min(TEK_MADDE_YOGUNLUK_EN_FAZLA if tek_madde else YOGUNLUK_EN_FAZLA, puan)


def puanla_hazir(belge: Belge, konu: Konu, esik: float = ESIK, agirlik: dict[str, float] | None = None) -> Puan:
    """agirlik: kelime → belge sıklığı ağırlığı (kelime_agirligi); verilmezse her kelime 1."""
    agirlik = agirlik or {}
    kanitlar: list[Kanit] = []
    b_norm = _maskele(belge.baslik_normal, konu.haric)
    baslik_kelimeleri = [k for k in konu.kelimeler if turkce_ek.var_mi(k, b_norm)]
    for k in baslik_kelimeleri[:3]:
        s, e = turkce_ek.bul(k, b_norm)[0]
        kanitlar.append(Kanit(k, "Başlık", "baslik", belge.baslik[max(0, s - 60):e + 60]))
    baslik_puani = min(BASLIK_EN_FAZLA, BASLIK_PUANI * len(baslik_kelimeleri))

    g = _bolumlerde_say(belge, konu, kanitlar, agirlik)
    toplam_madde = max(1, sum(1 for _, t, _, _ in belge.bolumler if t not in ("dayanak", "baslangic")))
    oran = len(g.maddeler) / toplam_madde
    yogunluk_puani = _yogunluk_puani(g, belge, oran, toplam_madde)
    yayilma_puani = 0.0 if belge.yapisiz else (
        min(YAYILMA_EN_FAZLA, YAYILMA_PUANI * sum(g.maddeler.values())) * min(1.0, oran * YAYILMA_ORAN_CARPANI))
    amac_var = g.amac_gecis >= AMAC_EN_AZ_GECIS or sum(g.amac_kelime.values()) >= AMAC_EN_AZ_KELIME
    bonus = AMAC_BONUSU if amac_var else 0.0
    ceza = DISLANAN_CEZASI if any(d in b_norm for d in konu.dislanan) else 0.0
    # Bütün konulara ortak dışlama (filtre.genel_dislama): puan ne olursa olsun ilgili değil.
    if genel_dislama(belge.baslik):
        ceza = max(ceza, 10_000.0)
    puan = round(baslik_puani + yogunluk_puani + yayilma_puani + bonus - ceza, 2)
    return Puan(konu.ad, puan, puan >= esik, kanitlar, {
        "baslik": baslik_puani, "yogunluk": round(yogunluk_puani, 2), "yayilma": round(yayilma_puani, 2), "amac": bonus,
        "ceza": ceza, "gecis": round(g.agirlikli, 1), "madde": len(g.maddeler), "oran": round(oran, 2),
        "kelime": belge.kelime_sayisi})


def puanla(baslik: str, metin: str, konu: Konu, esik: float = ESIK, agirlik: dict[str, float] | None = None) -> Puan:
    return puanla_hazir(belge_hazirla(baslik, metin), konu, esik, agirlik)


def ilgili_konular(baslik: str, metin: str, konular: list[Konu], agirlik: dict[str, float] | None = None) -> list[Puan]:
    belge = belge_hazirla(baslik, metin)
    return [p for p in (puanla_hazir(belge, k, agirlik=agirlik) for k in konular) if p.ilgili]

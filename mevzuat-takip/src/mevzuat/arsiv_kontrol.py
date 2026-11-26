"""Arşivin teslim kontrolü (otopsi) ve taban katlaması. Kaynaktan ve veritabanından bağımsız (SQLite ve PostgreSQL).

Kontrol (uv run python -m mevzuat.cli arsiv-kontrol): veritabanını ve dosyaları baştan sona okur, sadece okur.
Ölçütler iki türlü:
  zorunlu          teslimden önce 0 olmalı (cid kalıntısı, yedek karakter, eksik dosya, sürüm tutarsızlığı, bekleyen,
                   açıklamasız hata, eski okuyucuyla okunmuş son sürüm, OCR sınırı notu, NFC dışı metin, kontrol karakteri)
  gözden geçir     listelenir, tek tek bakılır (kusurlu sürümler, sitede olmayanlar, inmeyen ekler, görünümü olmayan
                   ekler, muhtemel mükerrer kayıtlar, cümle ortasında kırılan ya da kelime içi kopması çok olan metinler)
Rapor arsiv/kontrol/kontrol-<zaman>.json ve .md olarak yazılır.

Katlama (arsiv-katla --onay, karar 19): her mevzuatın en son sürümü v1 olur, öncekiler silinir. Teslimden önce,
deneme çalıştırmalarından kalan sürümler için, tek seferlik. Önce veritabanı yedeği alınmalı.
"""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mevzuat import arsiv_cekim, metin_duzen as md
from mevzuat.db import ArsivDosyasi, ArsivMevzuat, ArsivSurumu

_KONTROL_KARAKTERI = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_KOPMA = re.compile(rf"[{md.HARF}]{{2,}} (?!o\b)[{md.HARF}] [{md.HARF}]{{2,}}")
LISTE_SINIRI = 300


def _cumle_ortasi_orani(metin: str) -> float:
    satirlar = [s for s in metin.split("\n") if s.strip()]
    if len(satirlar) < 5:
        return 0.0
    kirik = sum(1 for a, b in zip(satirlar, satirlar[1:], strict=False)
                if re.search(rf"[{md.HARF},]$", md.kucuk(a.rstrip())) and re.match(rf"[{md.HARF}]", md.kucuk(b.lstrip())))
    return kirik / len(satirlar)


class Rapor:
    """Kontrol sonuçlarını toplar: zorunlu ölçütler, gözden geçirilecekler (her biri en fazla LISTE_SINIRI örnek) ve sayılar.
    Diskte olması gereken dosya yolları da burada birikir (eksik ve sahipsiz dosya kontrolü için)."""

    def __init__(self):
        self.zorunlu: dict[str, list] = {}
        self.gozden_gecir: dict[str, list] = {}
        self.sayilar: dict = {}
        self.dosya_yollari: set[str] = set()

    @staticmethod
    def _ekle(hedef: dict[str, list], ad: str, deger) -> None:
        liste = hedef.setdefault(ad, [])
        if len(liste) < LISTE_SINIRI:
            liste.append(deger)

    # Zorunlu ölçütü bozan bulgu (teslimden önce 0 olmalı).
    def hata(self, ad: str, deger) -> None:
        self._ekle(self.zorunlu, ad, deger)

    # Tek tek gözden geçirilecek bulgu.
    def bak(self, ad: str, deger) -> None:
        self._ekle(self.gozden_gecir, ad, deger)

    def dosya(self, yol: str | None) -> None:
        if yol:
            self.dosya_yollari.add(yol)

    def sozluk(self) -> dict:
        return {"zaman": datetime.now().isoformat(timespec="seconds"), "zorunlu": self.zorunlu,
                "gozden_gecir": self.gozden_gecir, "sayilar": self.sayilar,
                "sayac": {"zorunlu": {k: len(v) for k, v in self.zorunlu.items()},
                          "gozden_gecir": {k: len(v) for k, v in self.gozden_gecir.items()}},
                "hatasiz": not any(self.zorunlu.values())}


# Kaydın durumu bu ise zorunlu bulgu.
_DURUM_BULGUSU = {"BEKLIYOR": "metni_hic_alinmamis", "OCR_GEREKLI": "ocr_yapilamamis"}


# Kayıtların durumu: metni hiç alınmamış, hata, sitede yok, OCR yapılamamış.
def _kayitlari_denetle(r: Rapor, kayitlar: dict[str, ArsivMevzuat]) -> None:
    r.sayilar.update(mevzuat=len(kayitlar), durum=dict(Counter(m.durum for m in kayitlar.values())),
                     tur=dict(Counter(m.tur for m in kayitlar.values())),
                     kalkti=sum(1 for m in kayitlar.values() if m.kalkti))
    for m in kayitlar.values():
        if m.kalkti:
            continue
        if m.durum in _DURUM_BULGUSU:
            r.hata(_DURUM_BULGUSU[m.durum], m.anahtar)
        elif m.durum == "HATA":
            r.hata("hata", {"anahtar": m.anahtar, "hata": (m.son_hata or "")[:200]})
        elif m.durum in ("SITEDE_YOK", "BOS"):
            r.bak("sitede_yok", {"anahtar": m.anahtar, "ad": m.ad[:100], "neden": m.son_hata})


# Sürüm numaraları 1, 2, 3 ... diye kesintisiz ve kaydın sürüm sayısıyla aynı mı.
def _surumleri_denetle(r: Rapor, session: Session, kayitlar: dict[str, ArsivMevzuat]) -> None:
    numaralar: dict[str, list[int]] = {}
    for anahtar, no in session.execute(select(ArsivSurumu.anahtar, ArsivSurumu.surum_no)):
        numaralar.setdefault(anahtar, []).append(no)
    for anahtar, liste in numaralar.items():
        m = kayitlar.get(anahtar)
        if m is None:
            r.hata("kaydi_olmayan_surum", anahtar)
        elif sorted(liste) != list(range(1, len(liste) + 1)) or m.surum_sayisi != max(liste):
            r.hata("surum_numarasi_tutarsiz", anahtar)
    for m in kayitlar.values():
        if m.durum == "TAMAM" and m.anahtar not in numaralar:
            r.hata("tamam_ama_surumsuz", m.anahtar)
    r.sayilar.update(
        surum=sum(len(v) for v in numaralar.values()),
        birden_cok_surumlu=sum(1 for v in numaralar.values() if len(v) > 1),
        surum_turu=dict(session.execute(select(ArsivSurumu.surum_turu, func.count())
                                        .group_by(ArsivSurumu.surum_turu)).all()))


# Metinde kalmaması gereken okuma kusurları (zorunlu ölçüt adı, koşul).
_METIN_KUSURLARI = (
    ("cid_kalintisi", lambda t: "(cid:" in t),
    ("yedek_karakter", lambda t: "�" in t),
    ("ocr_siniri_notu", lambda t: "taranmış sayfayla sınırlandı" in t),
    ("kontrol_karakteri", lambda t: _KONTROL_KARAKTERI.search(t) is not None),
    ("nfc_disi", lambda t: unicodedata.normalize("NFC", t) != t),
)


def _metni_denetle(r: Rapor, v: ArsivSurumu) -> None:
    t = v.metin
    if v.okuyucu_surumu < arsiv_cekim.OKUYUCU_SURUMU:
        r.hata("eski_okuyucuyla_okunmus", v.anahtar)
    for ad, kosul in _METIN_KUSURLARI:
        if kosul(t):
            r.hata(ad, v.anahtar)
    if v.kalite == "kusurlu":
        r.bak("kusurlu", {"anahtar": v.anahtar, "notlar": (v.kalite_notlari or [])[:5]})
    if v.okuma in ("pdf_metin", "ocr", "karisik") and _cumle_ortasi_orani(t) > 0.3:
        r.bak("cumle_ortasi_kirik", v.anahtar)
    if len(_KOPMA.findall(md.kucuk(t))) >= 20:
        r.bak("kelime_ici_kopma", v.anahtar)


# Sürümün sayfa, PDF, ek ve resim dosyalarını rapora yazar; okunamamış sayfa ve inmeyen ek/resim bulgusu çıkarır.
def _dosyalari_topla(r: Rapor, v: ArsivSurumu, sayfa_yontem: Counter, ek_durum: Counter) -> None:
    for p in v.sayfalar or []:
        sayfa_yontem[p.get("yontem")] += 1
        if p.get("yontem") == "okunamadi":
            r.hata("okunamamis_sayfa", {"anahtar": v.anahtar, "sayfa": p.get("no"), "uyari": p.get("uyari")})
        r.dosya(p.get("goruntu"))
    r.dosya(v.pdf_yolu)
    r.dosya(v.site_pdf_yolu)
    for e in v.ekler or []:
        ek_durum[e.get("durum") or ("indi" if e.get("sha256") else "?")] += 1
        r.dosya(e.get("yol"))
        if e.get("durum") not in ("indi", None):
            r.bak("inmeyen_ek", {"anahtar": v.anahtar, "ad": e.get("ad"), "durum": e.get("durum"),
                                 "denenen": [d.get("url") for d in e.get("denenen") or []][:4]})
    for res in v.resimler or []:
        r.dosya(res.get("yol"))
        if not res.get("yol") and res.get("durum") != "indi":
            r.bak("inmeyen_resim", {"anahtar": v.anahtar, "src": res.get("src")})


# Yürürlükteki her mevzuatın son sürümü: metin kusurları, kalite, sayfalar, ekler, resimler.
def _son_surumleri_denetle(r: Rapor, session: Session) -> None:
    q = (select(ArsivSurumu).join(ArsivMevzuat, ArsivMevzuat.anahtar == ArsivSurumu.anahtar)
         .where(ArsivSurumu.surum_no == ArsivMevzuat.surum_sayisi, ArsivMevzuat.kalkti.is_(None))
         .execution_options(yield_per=200))
    okuma, kalite, sayfa_yontem, ek_durum = Counter(), Counter(), Counter(), Counter()
    for v in session.scalars(q):
        okuma[v.okuma] += 1
        kalite[v.kalite] += 1
        _metni_denetle(r, v)
        _dosyalari_topla(r, v, sayfa_yontem, ek_durum)
    r.sayilar.update(okuma=dict(okuma), kalite=dict(kalite), sayfa_yontemi=dict(sayfa_yontem), ek_durumu=dict(ek_durum))


# İşlenmiş dosyalar (ek, arşivden çıkan, resim) ve diskteki dosyalar: görünümsüz, eksik, sahipsiz, SHA (istenirse).
def _dosyalari_denetle(r: Rapor, session: Session, klasor: Path, sha_dogrula: bool) -> None:
    okunus = Counter()
    for d in session.scalars(select(ArsivDosyasi).execution_options(yield_per=500)):
        r.dosya(d.yol)
        # Arşiv dosyasından çıkanlar ve ek sayfalarının görüntüleri de bu dosyanın parçası (panelde "Ekler" gösterir);
        # sayılmazsa sahipsiz görünürlerdi (2026-11-26: 1.558 ek sayfa görüntüsü yanlışlıkla sahipsiz sayılıyordu).
        for i in d.ic or []:
            r.dosya(i.get("yol"))
        for p in d.sayfalar or []:
            r.dosya(p.get("goruntu"))
        okunus[(d.bicim, d.durum)] += 1
        if d.durum != "tamam":
            r.bak("gorunumu_olmayan_dosya", {"sha256": d.sha256[:16], "ad": d.ad, "bicim": d.bicim, "notlar": d.notlar[:2]})
        yol = klasor / d.yol
        if sha_dogrula and yol.is_file() and hashlib.sha256(yol.read_bytes()).hexdigest() != d.sha256:
            r.hata("sha_tutmayan_dosya", d.yol)
    r.sayilar["dosya_okuma"] = {f"{b}/{d}": n for (b, d), n in okunus.items()}
    for y in r.dosya_yollari:
        if not (klasor / y).is_file():
            r.hata("eksik_dosya", y)
    disk = {p.relative_to(klasor).as_posix() for alt in ("pdf", "ekler", "resim", "goruntu") if (klasor / alt).exists()
            for p in (klasor / alt).rglob("*") if p.is_file() and not p.name.endswith(".yaziliyor")}
    r.sayilar.update(diskte_dosya=len(disk), sahipsiz_dosya=len(disk - r.dosya_yollari))


# Muhtemel mükerrer: aynı ad, aynı RG tarihi ve sayısı.
def _mukerrerleri_denetle(r: Rapor, kayitlar: dict[str, ArsivMevzuat]) -> None:
    gruplar: dict[tuple, list[str]] = {}
    for m in kayitlar.values():
        if m.rg_tarihi and m.rg_sayisi:
            gruplar.setdefault((m.ad.strip().upper(), m.rg_tarihi, m.rg_sayisi), []).append(m.anahtar)
    for (ad, tarih, sayi), anahtarlar in gruplar.items():
        if len(anahtarlar) > 1:
            r.bak("muhtemel_mukerrer", {"anahtarlar": anahtarlar, "ad": ad[:100], "rg": f"{tarih}/{sayi}"})


def kontrol(session: Session, klasor: Path, sha_dogrula: bool = False) -> dict:
    r = Rapor()
    kayitlar = {m.anahtar: m for m in session.scalars(select(ArsivMevzuat))}
    _kayitlari_denetle(r, kayitlar)
    _surumleri_denetle(r, session, kayitlar)
    _son_surumleri_denetle(r, session)
    _dosyalari_denetle(r, session, klasor, sha_dogrula)
    _mukerrerleri_denetle(r, kayitlar)
    return r.sozluk()


def rapor_metni(r: dict) -> str:
    satirlar = [f"# Arşiv teslim kontrolü ({r['zaman']})", "",
                f"**Sonuç: {'zorunlu ölçütlerin hepsi sağlandı' if r['hatasiz'] else 'zorunlu ölçütlerde eksik var'}**", "",
                "## Sayılar", ""]
    for k, v in r["sayilar"].items():
        satirlar.append(f"- {k}: {v}")
    for baslik, alan in (("Zorunlu (0 olmalı)", "zorunlu"), ("Gözden geçirilecek", "gozden_gecir")):
        satirlar += ["", f"## {baslik}", ""]
        if not r[alan]:
            satirlar.append("- yok")
        for k, v in r[alan].items():
            satirlar.append(f"- **{k}**: {len(v)}{' (ilk ' + str(LISTE_SINIRI) + ')' if len(v) >= LISTE_SINIRI else ''}")
            for x in v[:20]:
                satirlar.append(f"  - {json.dumps(x, ensure_ascii=False, default=str)[:300]}")
    return "\n".join(satirlar) + "\n"


def rapor_yaz(r: dict, klasor: Path) -> tuple[Path, Path]:
    hedef = klasor / "kontrol"
    hedef.mkdir(parents=True, exist_ok=True)
    ad = f"kontrol-{datetime.now():%Y%m%d-%H%M%S}"
    j, m = hedef / f"{ad}.json", hedef / f"{ad}.md"
    j.write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), "utf-8")
    m.write_text(rapor_metni(r), "utf-8")
    return j, m


# Karar 19: teslim tabanı. Her mevzuatın son sürümü v1 olur, öncekiler silinir. Dönüş: (katlanan mevzuat, silinen sürüm).
def katla(session: Session) -> tuple[int, int]:
    cok = session.execute(select(ArsivSurumu.anahtar).group_by(ArsivSurumu.anahtar)
                          .having(func.count() > 1)).scalars().all()
    silinen = 0
    for anahtar in cok:
        son = session.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar)
                             .order_by(ArsivSurumu.surum_no.desc()).limit(1))
        silinen += session.execute(delete(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar,
                                                             ArsivSurumu.id != son.id)).rowcount or 0
        son.surum_no, son.surum_turu = 1, "ilk"
        session.execute(update(ArsivMevzuat).where(ArsivMevzuat.anahtar == anahtar).values(surum_sayisi=1))
    # Tek sürümlü olanların türü de "ilk" olmalı.
    session.execute(update(ArsivSurumu).where(ArsivSurumu.surum_no == 1, ArsivSurumu.surum_turu != "ilk")
                    .values(surum_turu="ilk"))
    session.commit()
    return len(cok), silinen

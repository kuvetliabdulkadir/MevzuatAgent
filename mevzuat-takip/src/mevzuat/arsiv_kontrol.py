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
    kirik = sum(1 for a, b in zip(satirlar, satirlar[1:])
                if re.search(rf"[{md.HARF},]$", md.kucuk(a.rstrip())) and re.match(rf"[{md.HARF}]", md.kucuk(b.lstrip())))
    return kirik / len(satirlar)


def kontrol(session: Session, klasor: Path, sha_dogrula: bool = False) -> dict:
    sonuc: dict = {"zaman": datetime.now().isoformat(timespec="seconds"), "zorunlu": {}, "gozden_gecir": {}, "sayilar": {}}
    z, g, s = sonuc["zorunlu"], sonuc["gozden_gecir"], sonuc["sayilar"]

    def ekle(hedef: dict, ad: str, deger) -> None:
        hedef.setdefault(ad, [])
        if len(hedef[ad]) < LISTE_SINIRI:
            hedef[ad].append(deger)

    # Kayıtlar.
    kayitlar = {m.anahtar: m for m in session.scalars(select(ArsivMevzuat))}
    s["mevzuat"] = len(kayitlar)
    s["durum"] = dict(Counter(m.durum for m in kayitlar.values()))
    s["tur"] = dict(Counter(m.tur for m in kayitlar.values()))
    s["kalkti"] = sum(1 for m in kayitlar.values() if m.kalkti)
    for m in kayitlar.values():
        if m.kalkti:
            continue
        if m.durum == "BEKLIYOR":
            ekle(z, "metni_hic_alinmamis", m.anahtar)
        elif m.durum == "HATA":
            ekle(z, "hata", {"anahtar": m.anahtar, "hata": (m.son_hata or "")[:200]})
        elif m.durum in ("SITEDE_YOK", "BOS"):
            ekle(g, "sitede_yok", {"anahtar": m.anahtar, "ad": m.ad[:100], "neden": m.son_hata})
        elif m.durum == "OCR_GEREKLI":
            ekle(z, "ocr_yapilamamis", m.anahtar)

    # Sürümler: tutarlılık, son sürüm kalitesi.
    numaralar: dict[str, list[int]] = {}
    for anahtar, no in session.execute(select(ArsivSurumu.anahtar, ArsivSurumu.surum_no)):
        numaralar.setdefault(anahtar, []).append(no)
    for anahtar, liste in numaralar.items():
        m = kayitlar.get(anahtar)
        if m is None:
            ekle(z, "kaydi_olmayan_surum", anahtar)
        elif sorted(liste) != list(range(1, len(liste) + 1)) or m.surum_sayisi != max(liste):
            ekle(z, "surum_numarasi_tutarsiz", anahtar)
    for m in kayitlar.values():
        if m.durum == "TAMAM" and m.anahtar not in numaralar:
            ekle(z, "tamam_ama_surumsuz", m.anahtar)
    s["surum"] = sum(len(v) for v in numaralar.values())
    s["birden_cok_surumlu"] = sum(1 for v in numaralar.values() if len(v) > 1)
    s["surum_turu"] = dict(session.execute(select(ArsivSurumu.surum_turu, func.count()).group_by(ArsivSurumu.surum_turu)).all())

    son_q = (select(ArsivSurumu).join(ArsivMevzuat, ArsivMevzuat.anahtar == ArsivSurumu.anahtar)
             .where(ArsivSurumu.surum_no == ArsivMevzuat.surum_sayisi, ArsivMevzuat.kalkti.is_(None))
             .execution_options(yield_per=200))
    okuma, kalite, sayfa_yontem, ek_durum, ek_okuma = Counter(), Counter(), Counter(), Counter(), Counter()
    dosya_yollari: set[str] = set()
    for v in session.scalars(son_q):
        okuma[v.okuma] += 1
        kalite[v.kalite] += 1
        t = v.metin
        if v.okuyucu_surumu < arsiv_cekim.OKUYUCU_SURUMU:
            ekle(z, "eski_okuyucuyla_okunmus", v.anahtar)
        if "(cid:" in t:
            ekle(z, "cid_kalintisi", v.anahtar)
        if "�" in t:
            ekle(z, "yedek_karakter", v.anahtar)
        if "taranmış sayfayla sınırlandı" in t:
            ekle(z, "ocr_siniri_notu", v.anahtar)
        if _KONTROL_KARAKTERI.search(t):
            ekle(z, "kontrol_karakteri", v.anahtar)
        if unicodedata.normalize("NFC", t) != t:
            ekle(z, "nfc_disi", v.anahtar)
        if v.kalite == "kusurlu":
            ekle(g, "kusurlu", {"anahtar": v.anahtar, "notlar": (v.kalite_notlari or [])[:5]})
        for p in v.sayfalar or []:
            sayfa_yontem[p.get("yontem")] += 1
            if p.get("yontem") == "okunamadi":
                ekle(z, "okunamamis_sayfa", {"anahtar": v.anahtar, "sayfa": p.get("no"), "uyari": p.get("uyari")})
            if p.get("goruntu"):
                dosya_yollari.add(p["goruntu"])
        if v.okuma in ("pdf_metin", "ocr", "karisik") and _cumle_ortasi_orani(t) > 0.3:
            ekle(g, "cumle_ortasi_kirik", v.anahtar)
        if len(_KOPMA.findall(md.kucuk(t))) >= 20:
            ekle(g, "kelime_ici_kopma", v.anahtar)
        for yol in (v.pdf_yolu, v.site_pdf_yolu):
            if yol:
                dosya_yollari.add(yol)
        for e in v.ekler or []:
            ek_durum[e.get("durum") or ("indi" if e.get("sha256") else "?")] += 1
            if e.get("yol"):
                dosya_yollari.add(e["yol"])
            if e.get("durum") not in ("indi", None):
                ekle(g, "inmeyen_ek", {"anahtar": v.anahtar, "ad": e.get("ad"), "durum": e.get("durum"),
                                       "denenen": [d.get("url") for d in e.get("denenen") or []][:4]})
        for r in v.resimler or []:
            if r.get("yol"):
                dosya_yollari.add(r["yol"])
            elif r.get("durum") != "indi":
                ekle(g, "inmeyen_resim", {"anahtar": v.anahtar, "src": r.get("src")})
    s["okuma"], s["kalite"], s["sayfa_yontemi"] = dict(okuma), dict(kalite), dict(sayfa_yontem)
    s["ek_durumu"] = dict(ek_durum)

    # Dosya kayıtları.
    for d in session.scalars(select(ArsivDosyasi).execution_options(yield_per=500)):
        dosya_yollari.add(d.yol)
        ek_okuma[(d.bicim, d.durum)] += 1
        if d.durum != "tamam":
            ekle(g, "gorunumu_olmayan_dosya", {"sha256": d.sha256[:16], "ad": d.ad, "bicim": d.bicim, "notlar": d.notlar[:2]})
        if sha_dogrula:
            yol = klasor / d.yol
            if yol.is_file() and hashlib.sha256(yol.read_bytes()).hexdigest() != d.sha256:
                ekle(z, "sha_tutmayan_dosya", d.yol)
    s["dosya_okuma"] = {f"{b}/{d}": n for (b, d), n in ek_okuma.items()}

    # Diskteki dosyalar.
    eksik = [y for y in dosya_yollari if not (klasor / y).is_file()]
    for y in eksik:
        ekle(z, "eksik_dosya", y)
    disk = {p.relative_to(klasor).as_posix() for alt in ("pdf", "ekler", "resim", "goruntu") if (klasor / alt).exists()
            for p in (klasor / alt).rglob("*") if p.is_file() and not p.name.endswith(".yaziliyor")}
    s["diskte_dosya"] = len(disk)
    s["sahipsiz_dosya"] = len(disk - dosya_yollari)

    # Muhtemel mükerrer: aynı ad, aynı RG tarihi ve sayısı.
    gruplar: dict[tuple, list[str]] = {}
    for m in kayitlar.values():
        if m.rg_tarihi and m.rg_sayisi:
            gruplar.setdefault((m.ad.strip().upper(), m.rg_tarihi, m.rg_sayisi), []).append(m.anahtar)
    for (ad, tarih, sayi), anahtarlar in gruplar.items():
        if len(anahtarlar) > 1:
            ekle(g, "muhtemel_mukerrer", {"anahtarlar": anahtarlar, "ad": ad[:100], "rg": f"{tarih}/{sayi}"})

    sonuc["sayac"] = {"zorunlu": {k: len(v) for k, v in z.items()}, "gozden_gecir": {k: len(v) for k, v in g.items()}}
    sonuc["hatasiz"] = not any(z.values())
    return sonuc


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

"""Konu filtresinin test setleriyle ölçümü (Faz 3, karar 13, 18). `uv run python -m mevzuat.cli filtre-olc`

İki test seti var, eşik ikisinde birlikte seçilir:
- Arşiv seti (tests/fixtures/filtre_test_seti.json): her kayıt için konu, mevzuat anahtarı ve "ilgili mi" etiketi.
  Metinler arşivden okunur; test setinde olup arşivde olmayan kayıt atlanır.
- Günlük set (tests/fixtures/filtre_gunluk_seti.json.gz): günlük taramanın kayıtları metinleriyle (kısa duyuru, mahkeme
  kararı, liste, değişen mevzuat). Her kayıt bütün aktif konularla ölçülür, `ilgili` listesinde olmayan konu ilgisizdir.
Eski filtre (başlıkta kelime) ile puan (puan.py, arşivden kelime ağırlıklarıyla) farklı eşiklerde karşılaştırılır:
doğruluk (ilgili denenlerin ne kadarı gerçekten ilgili), kapsama (gerçekten ilgililerin ne kadarı yakalandı), F2 (kapsamayı
iki kat önemser, "kaçırmak pahalı"). Konular veritabanından okunur.

2026-11-24 yerel ölçüm (Claude etiketi; arşiv 199 kayıt / 60 ilgili, günlük 69 kayıt × 7 konu / 27 ilgili), eşik 10:
arşiv 0,68 / 0,87 / F2 0,82 (eski başlık filtresi 0,75 / 0,68 / 0,69), günlük 0,76 / 0,96 / F2 0,92.
"""

import gzip
import json
from pathlib import Path

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from mevzuat import arama, filtre, puan
from mevzuat.db import ArsivMevzuat, ArsivSurumu

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures"
TEST_SETI = FIXTURES / "filtre_test_seti.json"
GUNLUK_SETI = FIXTURES / "filtre_gunluk_seti.json.gz"
ESIKLER = (6, 8, 10, 12, 14, 16, 20, 24, 30)


def _olc(kayitlar: list[dict], secili) -> dict:
    tp = sum(1 for k in kayitlar if secili(k) and k["ilgili"])
    fp = sum(1 for k in kayitlar if secili(k) and not k["ilgili"])
    fn = sum(1 for k in kayitlar if not secili(k) and k["ilgili"])
    d = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f2 = 5 * d * r / (4 * d + r) if d + r else 0.0
    return {"dogru": tp, "yanlis": fp, "kacan": fn, "dogruluk": round(d, 2), "kapsama": round(r, 2), "f2": round(f2, 2)}


def _arsiv_olculen(session: Session, konu: dict, agirlik: dict, yol: Path) -> list[dict]:
    kayitlar = json.loads(yol.read_text(encoding="utf-8"))["kayitlar"]
    anahtarlar = list({k["anahtar"] for k in kayitlar})
    metin = {m.anahtar: (m.ad, s.metin or "") for m, s in session.execute(
        select(ArsivMevzuat, ArsivSurumu).join(ArsivSurumu, and_(ArsivSurumu.anahtar == ArsivMevzuat.anahtar,
                                                                 ArsivSurumu.surum_no == ArsivMevzuat.surum_sayisi))
        .where(ArsivMevzuat.anahtar.in_(anahtarlar)))}
    olculen = []
    for k in kayitlar:
        if k["konu"] not in konu or k["anahtar"] not in metin:
            continue
        ad, m = metin[k["anahtar"]]
        p = puan.puanla(ad, m, konu[k["konu"]], agirlik=agirlik)
        olculen.append({**k, "puan": p.puan, "eski": k["konu"] in filtre.eslesmeler(ad, [konu[k["konu"]]])})
    return olculen


def _gunluk_olculen(konu: dict, agirlik: dict, yol: Path) -> list[dict]:
    if not yol.exists():
        return []
    with gzip.open(yol, "rt", encoding="utf-8") as f:
        kayitlar = json.load(f)["kayitlar"]
    olculen = []
    for k in kayitlar:
        belge = puan.belge_hazirla(k["baslik"], k["metin"])
        eski = filtre.eslesmeler(k["baslik"], list(konu.values()))
        for ad, ko in konu.items():
            p = puan.puanla_hazir(belge, ko, agirlik=agirlik)
            olculen.append({"konu": ad, "ad": f"{k['id']} {k['baslik']}", "ilgili": ad in k["ilgili"], "puan": p.puan,
                            "eski": ad in eski})
    return olculen


def _ozet(olculen: list[dict]) -> dict:
    sonuc = {"kayit": len(olculen), "ilgili": sum(k["ilgili"] for k in olculen),
             "eski": _olc(olculen, lambda k: k["eski"]),
             "yeni": {e: _olc(olculen, lambda k, e=e: k["puan"] >= e) for e in ESIKLER}, "konu": {}}
    for ad in sorted({k["konu"] for k in olculen}):
        alt = [k for k in olculen if k["konu"] == ad]
        sonuc["konu"][ad] = {"eski": _olc(alt, lambda k: k["eski"]), "yeni": _olc(alt, lambda k: k["puan"] >= puan.ESIK),
                             "yanlis": [k["ad"][:100] for k in alt if k["puan"] >= puan.ESIK and not k["ilgili"]][:10],
                             "kacan": [k["ad"][:100] for k in alt if k["puan"] < puan.ESIK and k["ilgili"]][:10]}
    return sonuc


def olc(session: Session, konular: list[filtre.Konu], yol: Path = TEST_SETI, gunluk_yol: Path = GUNLUK_SETI) -> dict:
    konu = {k.ad: k for k in konular}
    agirlik = arama.kelime_agirliklari(session)
    arsiv = _arsiv_olculen(session, konu, agirlik, yol)
    gunluk = _gunluk_olculen(konu, agirlik, gunluk_yol)
    return {"esik": puan.ESIK, "agirlikli": bool(agirlik), "arsiv": _ozet(arsiv), "gunluk": _ozet(gunluk),
            "birlikte": {e: _olc(arsiv + gunluk, lambda k, e=e: k["puan"] >= e) for e in ESIKLER}}


def _set_metni(baslik: str, s: dict) -> list[str]:
    satir = ["", f"# {baslik}: {s['kayit']} ölçüm, {s['ilgili']} ilgili", "                 doğruluk kapsama   F2",
             f"eski (başlık)      {s['eski']['dogruluk']:5}  {s['eski']['kapsama']:5}  {s['eski']['f2']:5}"]
    for e, o in s["yeni"].items():
        satir.append(f"yeni eşik {e:<3}      {o['dogruluk']:5}  {o['kapsama']:5}  {o['f2']:5}")
    for ad, o in s["konu"].items():
        satir += ["", f"## {ad}: eski {o['eski']['dogruluk']}/{o['eski']['kapsama']}, yeni {o['yeni']['dogruluk']}/{o['yeni']['kapsama']}"]
        satir += [f"   yanlış ilgili: {x}" for x in o["yanlis"]] + [f"   kaçan: {x}" for x in o["kacan"]]
    return satir


def rapor_metni(s: dict) -> str:
    satir = [f"Şu anki eşik: {s['esik']}. Kelime ağırlığı: {'arşivden' if s['agirlikli'] else 'yok (arsiv-filtre çalışmamış)'}",
             "", "Birlikte F2: " + ", ".join(f"{e}: {o['f2']}" for e, o in s["birlikte"].items())]
    satir += _set_metni("Arşiv seti", s["arsiv"]) + _set_metni("Günlük set", s["gunluk"])
    return "\n".join(satir)

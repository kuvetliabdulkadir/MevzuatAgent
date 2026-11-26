"""Değişen mevzuatın tam karşılaştırma sayfası. Rapor mailindeki "Değişikliklerin tamamını gör" bağlantısı buraya gelir.

Maili alanların çoğunun panel hesabı yok, sayfa girişsiz açılır. Bağlantı imzalıdır (rapor.karsilastirma_imzasi,
MEVZUAT_GIZLI_ANAHTAR): adresteki mevzuat ya da sürüm elle değiştirilirse imza tutmaz ve sayfa açılmaz. Gösterilen sadece
mevzuat.gov.tr'deki kamuya açık metnin iki sürümü ve farkı. Sayfa sunucuda üretilir, script yoktur, metin kaçışlıdır;
fark HTML'i arsiv_fark'tan gelir (kaçışlı metin + <ins>/<del>).
"""

import hmac
import html

from sqlalchemy import select
from sqlalchemy.orm import Session

from mevzuat import arsiv_fark
from mevzuat.db import ArsivMevzuat, ArsivSurumu
from mevzuat.rapor import karsilastirma_imzasi

# Sayfa kendi stilini satır içinde taşır; başka hiçbir şey yüklenmez, çerçeveye alınamaz, arama motoruna girmez.
BASLIKLAR = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "private, max-age=600",
    "Referrer-Policy": "no-referrer",
}

_STIL = """
body{font-family:Segoe UI,Arial,sans-serif;background:#f7f7f4;color:#1c1c1a;margin:0}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 4px}.ust{color:#6b6b63;font-size:13px;margin-bottom:16px}
.sayac{background:#fff;border:1px solid #e6e6e0;border-radius:6px;padding:10px 14px;font-size:14px;margin-bottom:16px}
.bolum{background:#fff;border:1px solid #e6e6e0;border-left:4px solid #1f4e5a;border-radius:6px;padding:12px 14px;margin:10px 0}
.bolum h2{font-size:14px;margin:0 0 6px}.tur{font-weight:normal;color:#6b6b63;font-size:12px;margin-left:6px}
.metin{font-family:Georgia,serif;font-size:15px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
del{background:#fbe3df;color:#8a2b1d}ins{background:#dcf2e1;color:#1d5c2e;text-decoration:none;font-weight:600}
.eski{color:#6b6b63;font-size:13px;margin-top:8px;border-top:1px dashed #e6e6e0;padding-top:6px}
.not{color:#6b6b63;font-size:12px;margin-top:20px}a{color:#2f5d8a}
"""
TUR_ADI = {"degisti": "değişti", "eklendi": "eklendi", "kaldirildi": "kaldırıldı", "mulga": "mülga edildi"}


def imza_dogru(anahtar: str, surum: int, imza: str, gizli: str) -> bool:
    return bool(gizli) and hmac.compare_digest(karsilastirma_imzasi(anahtar, surum, gizli), imza or "")


def _onceki(session: Session, anahtar: str, surum_no: int) -> ArsivSurumu | None:
    """Karşılaştırılacak önceki sürüm: okuma düzeltmesi olmayan en yakın eski sürüm (panelle aynı kural)."""
    return session.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar, ArsivSurumu.surum_no < surum_no,
                                                    ArsivSurumu.surum_turu != "okuma_duzeltmesi")
                          .order_by(ArsivSurumu.surum_no.desc()).limit(1))


def sayfa(session: Session, anahtar: str, surum_no: int) -> str | None:
    """Sayfanın HTML'i. Mevzuat ya da sürüm yoksa None."""
    m = session.get(ArsivMevzuat, anahtar)
    yeni = session.scalar(select(ArsivSurumu).where(ArsivSurumu.anahtar == anahtar, ArsivSurumu.surum_no == surum_no))
    eski = _onceki(session, anahtar, surum_no)
    if m is None or yeni is None or eski is None:
        return None
    f = arsiv_fark.fark(eski.metin, yeni.metin)
    e = lambda x: html.escape(str(x), quote=True)
    sayac = f["sayac"]
    ozet = " · ".join(f"{n} {TUR_ADI[t]}" for t, n in sayac.items() if n) or "maddelerde fark yok"
    parcalar = []
    for b in f["bolumler"]:
        if b["tur"] == "ayni":
            continue
        # arsiv_fark.fark: "html" kaçışlı metin + <ins>/<del>; diğer alanlar ham metin, burada kaçışlanır.
        onceki = (f'<div class="eski">v{eski.surum_no} hali: {e(b["eski"])}</div>'
                  if b["tur"] in ("degisti", "mulga") and b.get("eski") else "")
        parcalar.append(f'<section class="bolum"><h2>{e(b["bolum"])}<span class="tur">{TUR_ADI.get(b["tur"], e(b["tur"]))}'
                        f'</span></h2><div class="metin">{b["html"]}</div>{onceki}</section>')
    tarih = lambda s: s.cekildi.strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>{e(m.ad[:120])} — değişiklikler</title><style>{_STIL}</style></head>
<body><main>
<h1>{e(m.ad)}</h1>
<div class="ust">{e(m.anahtar)} · v{eski.surum_no} ({tarih(eski)}) → v{yeni.surum_no} ({tarih(yeni)}) ·
<a href="{e(m.url)}" rel="noopener noreferrer">mevzuat.gov.tr'de aç</a></div>
<div class="sayac"><strong>Değişiklikler:</strong> {ozet}. Silinen kısım <del>kırmızı ve üstü çizili</del>, eklenen kısım
<ins>yeşil</ins>. Değişmeyen maddeler gösterilmiyor.</div>
{''.join(parcalar) or '<p>Bu iki sürüm arasında maddelerde fark yok (fark eklerde ya da biçimde).</p>'}
<p class="not">Metinler mevzuat.gov.tr'den alınmıştır; resmî metin Resmî Gazete'dedir. Bu sayfa rapor mailindeki bağlantıyla açılır.</p>
</main></body></html>"""

"""Rapora giren (ilgili) kaydın belgesi: PDF kopyası ve tablo/görselleri (belge_gorselleri). Günlük içerik adımı
(pipeline.icerikleri_indir) ve sonradan doldurma (cli rapor-belgeleri-doldur) buradan yazar; rapor, mail, panel ve
arşivdeki "rapora giren belgeler" buradan okur."""

import hashlib
import logging
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mevzuat import belge_gorselleri
from mevzuat.db import Kayit, KayitBelgesi, KayitGorseli

log = logging.getLogger(__name__)

BELGE_SINIRI = 25 * 1024 * 1024  # bundan büyük PDF'in kopyası tutulmaz (görselleri yine çıkarılır)


def sakla(session: Session, kayit: Kayit, belge: bytes, ocr_sayfalari: dict[int, str] | None = None,
          mime: str = "application/pdf") -> int:
    """Kaydın eski görsellerini silip yenilerini yazar, belgenin kopyasını saklar. Görsel sayısını döner."""
    gorseller = belge_gorselleri.cikar(belge, ocr_sayfalari)
    session.execute(delete(KayitGorseli).where(KayitGorseli.kayit_id == kayit.id))
    for sira, g in enumerate(gorseller, start=1):
        session.add(KayitGorseli(kayit_id=kayit.id, sira=sira, sayfa=g.sayfa, tur=g.tur, aciklama=g.aciklama[:200],
                                 tablo=g.tablo, png=g.png, genislik=g.genislik, yukseklik=g.yukseklik))
    if len(belge) <= BELGE_SINIRI:
        session.merge(KayitBelgesi(kayit_id=kayit.id, sha256=hashlib.sha256(belge).hexdigest(), mime=mime,
                                   boyut=len(belge), veri=belge, alindi=datetime.now()))
    return len(gorseller)


def gorseller(session: Session, kayit_id: int) -> list[KayitGorseli]:
    return list(session.scalars(select(KayitGorseli).where(KayitGorseli.kayit_id == kayit_id)
                                .order_by(KayitGorseli.sira)))


def belgesi_var_mi(session: Session, kayit_id: int) -> bool:
    return session.scalar(select(KayitBelgesi.kayit_id).where(KayitBelgesi.kayit_id == kayit_id)) is not None


def belge(session: Session, kayit_id: int) -> KayitBelgesi | None:
    return session.get(KayitBelgesi, kayit_id)


# --- arşivde "rapora giren belgeler" ---------------------------------------------------------------
# Arşiv sadece mevzuat.gov.tr'deki mevzuatı tutar; Resmî Gazete'deki kurul kararı, ilan, duyuru gibi rapora giren
# belgeler kayitlar tablosundadır. Burada rapora bağlanmış (rapor_id dolu) kayıtlar aranır ve gösterilir.

SAYFA_BOYU = 20


def gorsel_sozlugu(g: KayitGorseli, adres: str) -> dict:
    from mevzuat.rapor import tablo_hucreleri

    return {"id": g.id, "sayfa": g.sayfa, "tur": g.tur, "aciklama": g.aciklama,
            "satirlar": tablo_hucreleri(g.tablo)[0] if g.tablo else None,
            "url": f"{adres}/{g.id}" if g.png else None, "genislik": g.genislik, "yukseklik": g.yukseklik}


def _sql_kalibi(kelime: str) -> str:
    """ILIKE ön süzgeci: Türkçe harfler (İ/ı, ç, ş…) ve şapkalı olabilen a/i/u veritabanının harf kuralına bırakılmaz,
    tek karakter joker olur; kesin eşleşme Python'da tr_kucuk ile bakılır."""
    kalip = ""
    for c in kelime:
        if c in "\\%_":
            kalip += "\\" + c
        else:
            kalip += c if c.isascii() and c not in "aiuAIU" else "_"
    return "%" + kalip + "%"


def _parca(metin: str, kelime: str) -> str | None:
    import html

    from mevzuat.filtre import tr_kucuk

    duz = " ".join(metin.split())
    yer = tr_kucuk(duz).find(kelime)  # tr_kucuk harf sayısını korur
    if yer < 0:
        return None
    bas, son = max(0, yer - 120), min(len(duz), yer + len(kelime) + 120)
    return (("…" if bas else "") + html.escape(duz[bas:yer]) + "<mark>" + html.escape(duz[yer:yer + len(kelime)])
            + "</mark>" + html.escape(duz[yer + len(kelime):son]) + ("…" if son < len(duz) else ""))


def liste(session: Session, q: str = "", sayfa: int = 1) -> dict:
    """Rapora giren belgeler, yeniden eskiye. `q` başlıkta, kaynakçada ya da tam metinde aranır (bütün kelimeler)."""
    from sqlalchemy import and_, or_

    from mevzuat.filtre import tr_kucuk

    kelimeler = [tr_kucuk(k) for k in q.split() if k.strip()][:8]
    sorgu = select(Kayit.id).where(Kayit.rapor_id.is_not(None))
    for k in kelimeler:
        kalip = _sql_kalibi(k)
        sorgu = sorgu.where(or_(Kayit.baslik.ilike(kalip, escape="\\"), Kayit.kaynakca.ilike(kalip, escape="\\"),
                                Kayit.icerik.ilike(kalip, escape="\\")))
    adaylar = list(session.scalars(sorgu.order_by(Kayit.yayin_tarihi.desc(), Kayit.id.desc())))
    satirlar = []
    for kayit_id in adaylar:
        k = session.get(Kayit, kayit_id)
        parca = None
        if kelimeler:
            tum = tr_kucuk(f"{k.baslik}\n{k.kaynakca}\n{k.icerik or ''}")
            if not all(x in tum for x in kelimeler):
                continue
            if not all(x in tr_kucuk(k.baslik) for x in kelimeler):
                parca = _parca(k.icerik or "", kelimeler[0])
        satirlar.append((k, parca))
    toplam = len(satirlar)
    sayfa = max(1, sayfa)
    secilen = satirlar[(sayfa - 1) * SAYFA_BOYU:sayfa * SAYFA_BOYU]
    gorsel_sayisi = dict(session.execute(
        select(KayitGorseli.kayit_id, func.count()).where(KayitGorseli.kayit_id.in_([k.id for k, _ in secilen]))
        .group_by(KayitGorseli.kayit_id)).all()) if secilen else {}
    belgeli = set(session.scalars(select(KayitBelgesi.kayit_id).where(
        and_(KayitBelgesi.kayit_id.in_([k.id for k, _ in secilen]))))) if secilen else set()
    return {"toplam": toplam, "boyut": SAYFA_BOYU, "satirlar": [_ozet(k, parca, gorsel_sayisi.get(k.id, 0), k.id in belgeli)
                                                              for k, parca in secilen]}


def _ozet(k: Kayit, parca: str | None, gorsel_sayisi: int, belge_var: bool) -> dict:
    from mevzuat.rapor import tur_adi

    return {"id": k.id, "baslik": k.baslik, "tur_adi": tur_adi(k), "kaynak": k.kaynak,
            "yayin_tarihi": k.yayin_tarihi.isoformat(), "rapor_id": k.rapor_id, "haric": k.haric,
            "is_kollari": k.is_kollari, "parca": parca, "gorsel_sayisi": gorsel_sayisi, "belge_var": belge_var,
            "ocr": k.icerik_durumu == "OCR_ILE_OKUNDU"}


def detay(session: Session, kayit_id: int, gorsel_adresi: str) -> dict | None:
    """Rapora giren belgenin tam metni (okunamayan satırlar ayıklanmış), tablo/görselleri, kaynakçası."""
    from mevzuat.metin_duzen import okunamayanlari_ayikla
    from mevzuat.rapor import kisa_kaynakca, linksiz

    k = session.get(Kayit, kayit_id)
    if k is None or k.rapor_id is None:
        return None
    metin = okunamayanlari_ayikla((k.icerik or "").replace("\r\n", "\n"), ocr=k.icerik_durumu == "OCR_ILE_OKUNDU")
    return {**_ozet(k, None, 0, belgesi_var_mi(session, k.id)),
            "kaynakca": kisa_kaynakca(k), "eslesmeler": k.eslesmeler,
            "url": k.url if k.url.startswith(("https://", "http://")) else None,
            "metin": linksiz(metin), "okunamadi": k.icerik_durumu in ("OCR_GEREKLI", "HATA"),
            "gorseller": [gorsel_sozlugu(g, gorsel_adresi) for g in gorseller(session, k.id)]}

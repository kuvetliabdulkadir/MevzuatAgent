import os
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest

from mevzuat import ocr
from mevzuat.db import Kayit
from mevzuat.filtre import konulari_yukle
from mevzuat.icerik import belge_icerigi
from mevzuat.pipeline import icerikle_puanla

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


from conftest import turkce_ocr_var as _turkce_ocr_var  # noqa: E402


@pytest.mark.ocr
def test_govdesi_gorsel_pdf_ocr_ile_okunur():
    if not _turkce_ocr_var():
        pytest.skip("Tesseract + Türkçe dil dosyası yok (MEVZUAT_TESSDATA ayarlı mı?)")
    icerik = belge_icerigi((FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes(), "application/pdf")
    assert icerik.ocr_ile and not icerik.ocr_gerekli
    assert "MADDE 1" in icerik.metin
    assert "benzin" in icerik.metin
    # Tablodaki tutarlar (görüntüden elle kontrol edildi)
    for tutar in ["7,9000", "11,3600", "14,8277", "8,9000", "12,3600", "15,5437"]:
        assert tutar in icerik.metin


def _otv_karari(icerik: str) -> Kayit:
    return Kayit(
        baslik="Bazı Mallara Uygulanan Özel Tüketim Vergisi Tutarlarının Yeniden Belirlenmesi Hakkında Karar",
        eslesmeler={"Vergi": ["özel tüketim vergisi"]}, is_kollari=["Ortak"], ilgili=True, icerik=icerik,
        yayin_tarihi=date(2026, 10, 1), ilk_gorulme=datetime(2026, 10, 1),
    )


def test_icerik_is_kollarini_genisletir():
    konular = konulari_yukle(ROOT / "config" / "konular.toml")
    # Kararın konusu akaryakıt: giriş ve tablo benzin/motorin tutarlarıyla dolu.
    kayit = _otv_karari("MADDE 1- (1) 4760 sayılı Kanuna ekli (I) sayılı listedeki kurşunsuz benzin 95 oktan, motorin ve "
                        "diğer benzin türü malların özel tüketim vergisi tutarları aşağıdaki şekilde belirlenmiştir. "
                        "2710.12.45.00.11 Kurşunsuz benzin 95 oktan 15,5437 2710.19.43.00.11 Motorin 12,3600 "
                        "2710.12.45.00.12 Kurşunsuz benzin 98 oktan 15,8277 MADDE 2- (1) Bu Karar yayımı tarihinde yürürlüğe girer. "
                        "MADDE 3- (1) Bu Karar hükümlerini Cumhurbaşkanı yürütür.")
    icerikle_puanla(kayit, konular)
    assert kayit.is_kollari == ["Ortak", "Oto kiralama"]
    assert "benzin (içerikte)" in kayit.eslesmeler["Araç kiralama ve taşıtlar"]
    assert kayit.eslesmeler["Vergi"][0] == "özel tüketim vergisi"  # başlık kelimesi yerinde, tekrar eklenmedi

    once = dict(kayit.eslesmeler)
    icerikle_puanla(kayit, konular)  # ikinci kez çalışınca kopya oluşmaz
    assert kayit.eslesmeler == once


def test_icerikte_bir_kez_gecmesi_yetmez():
    konular = konulari_yukle(ROOT / "config" / "konular.toml")
    govde = " ".join(f"MADDE {i}- (1) Tütün mamullerinin vergi tutarları ve uygulama esasları şöyledir." for i in range(1, 9))
    kayit = _otv_karari(govde + " MADDE 9- (1) Bu madde benzin için uygulanmaz.")
    icerikle_puanla(kayit, konular)
    assert "Araç kiralama ve taşıtlar" not in kayit.eslesmeler
    assert kayit.ilgili and kayit.is_kollari == ["Ortak"]  # başlıktaki konu düşmez


def test_ocr_yoksa_ocr_gerekli_kalir():
    icerik = belge_icerigi((FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes(), "application/pdf")
    assert icerik.ocr_gerekli and not icerik.ocr_ile

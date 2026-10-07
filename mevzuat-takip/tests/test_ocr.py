import os
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest

from mevzuat import ocr
from mevzuat.db import Kayit
from mevzuat.filtre import konulari_yukle
from mevzuat.icerik import belge_icerigi
from mevzuat.pipeline import _icerikten_genislet

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


def test_icerik_is_kollarini_genisletir():
    konular = konulari_yukle(ROOT / "config" / "konular.toml")
    kayit = Kayit(
        baslik="Bazı Mallara Uygulanan Özel Tüketim Vergisi Tutarlarının Yeniden Belirlenmesi Hakkında Karar",
        eslesmeler={"Vergi": ["özel tüketim vergisi"]},
        is_kollari=["Ortak"],
        icerik="MADDE 1- … kurşunsuz benzin 95 oktan türü malların özel tüketim vergisi tutarları …",
        yayin_tarihi=date(2026, 10, 1), ilk_gorulme=datetime(2026, 10, 1),
    )
    _icerikten_genislet(kayit, konular)
    assert kayit.is_kollari == ["Ortak", "Oto kiralama"]
    assert kayit.eslesmeler["Araç kiralama ve taşıtlar"] == ["benzin (içerikte)"]
    assert kayit.eslesmeler["Vergi"] == ["özel tüketim vergisi"]  # tekrar eklenmedi

    _icerikten_genislet(kayit, konular)  # ikinci kez çalışınca kopya oluşmaz
    assert kayit.eslesmeler["Araç kiralama ve taşıtlar"] == ["benzin (içerikte)"]


def test_ocr_yoksa_ocr_gerekli_kalir():
    icerik = belge_icerigi((FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes(), "application/pdf")
    assert icerik.ocr_gerekli and not icerik.ocr_ile

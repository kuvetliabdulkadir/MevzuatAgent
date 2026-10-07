import pytest
from pathlib import Path

from mevzuat.icerik import belge_icerigi, html_coz

FIXTURES = Path(__file__).parent / "fixtures"


def test_cp1254_htm_dogru_cozulur():
    veri = (FIXTURES / "rg_20261001-1-1_cp1254.htm").read_bytes()
    # Header'da charset yok (RG'de olduğu gibi); <meta>'dan okunmalı.
    metin = html_coz(veri, header_charset=None)
    assert "Ticaret Bakanlığından" in metin
    assert "�" not in metin


def test_htm_metni_cikarilir():
    veri = (FIXTURES / "rg_20261001-1-1_cp1254.htm").read_bytes()
    icerik = belge_icerigi(veri, "text/html")
    assert "Taşınmaz Ticareti" in icerik.metin
    assert "MADDE 1" in icerik.metin
    assert not icerik.ocr_gerekli


def test_taranmis_pdf_ocr_gerekli_isaretlenir():
    veri = (FIXTURES / "rg_20261001-3-2_taranmis.pdf").read_bytes()
    icerik = belge_icerigi(veri, "application/pdf")
    assert icerik.ocr_gerekli
    assert "11821" in icerik.metin  # başlık kısmı metin olarak var


def test_govdesi_gorsel_pdf_ocr_gerekli_isaretlenir():
    # Her sayfada başlık metni var (sayfa eşiğini geçer) ama kararın gövdesi görüntü.
    veri = (FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes()
    icerik = belge_icerigi(veri, "application/pdf")
    assert icerik.ocr_gerekli
    assert "MADDE" not in icerik.metin


KARISIK = FIXTURES / "rg_20260918-8_karisik.pdf"  # 6 sayfa: metin + 3. ve 4. sayfa görüntü


def test_karisik_pdf_ocr_yoksa_eksik_isaretlenir():
    icerik = belge_icerigi(KARISIK.read_bytes(), "application/pdf")
    assert icerik.ocr_gerekli and not icerik.ocr_ile
    assert len(icerik.metin) > 1000  # metin kısmı yine de var


@pytest.mark.ocr
def test_karisik_pdf_sadece_gorsel_sayfalar_ocrlanir(monkeypatch):
    from mevzuat import ocr

    from conftest import turkce_ocr_var

    if not turkce_ocr_var():
        pytest.skip("Tesseract + Türkçe dil dosyası yok (MEVZUAT_TESSDATA ayarlı mı?)")
    istenen = []
    gercek = ocr.sayfalari_ocr

    def kaydet(veri, klasor, sayfa_nolari=None):
        istenen.append(sayfa_nolari)
        return gercek(veri, klasor, sayfa_nolari)

    monkeypatch.setattr(ocr, "sayfalari_ocr", kaydet)
    oncesi = belge_icerigi(KARISIK.read_bytes(), "application/pdf")
    assert istenen == [[2, 3]]  # sadece görsel sayfalar, tüm belge değil
    assert oncesi.ocr_ile and not oncesi.ocr_gerekli


def test_ocr_sayfa_siniri(monkeypatch):
    """Çok sayfalı taranmış belgede OCR ilk N sayfayla sınırlanır ve bu metinde açıkça yazar
    (02.10.2026: 219 sayfalık SGK tebliği tek başına raporu ~20 dk geciktiriyordu)."""
    from mevzuat import icerik as icerik_modulu
    from mevzuat import ocr

    istenen = []

    def sahte_ocr(veri, klasor, sayfa_nolari):
        istenen.extend(sayfa_nolari)
        return {i: f"sayfa {i + 1} metni" for i in sayfa_nolari}

    monkeypatch.setattr(ocr, "ocr_kullanilabilir", lambda: True)
    monkeypatch.setattr(ocr, "sayfalari_ocr", sahte_ocr)
    monkeypatch.setattr(icerik_modulu, "OCR_SAYFA_SINIRI", 1)
    veri = (FIXTURES / "rg_20261001-3-2_taranmis.pdf").read_bytes()  # 2 sayfa, tamamı görüntü

    sonuc = belge_icerigi(veri, "application/pdf")
    assert istenen == [0]
    assert sonuc.ocr_ile and "sayfa 1 metni" in sonuc.metin and "sayfa 2" not in sonuc.metin
    assert "Belge 2 sayfa; otomatik okuma ilk 1 taranmış sayfayla sınırlandı" in sonuc.metin

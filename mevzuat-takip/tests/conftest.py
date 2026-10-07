import pytest

from mevzuat import ocr


@pytest.fixture(autouse=True)
def ocr_varsayilan_kapali(request, monkeypatch):
    """Testler makinede Tesseract olup olmamasına göre farklı sonuç vermesin.
    Gerçek OCR'ı deneyen testler @pytest.mark.ocr ile işaretlenir."""
    if "ocr" not in request.keywords:
        monkeypatch.setattr(ocr, "tesseract_yolu", lambda: None)


def turkce_ocr_var() -> bool:
    """Gerçek OCR testleri için: Tesseract VE Türkçe dil dosyası var mı?"""
    import os
    import subprocess

    yol = ocr.tesseract_yolu()
    if not yol:
        return False
    komut = [yol, "--list-langs"]
    if tessdata := os.environ.get("MEVZUAT_TESSDATA"):
        komut += ["--tessdata-dir", tessdata]
    return "tur" in subprocess.run(komut, capture_output=True, text=True).stdout.split()


@pytest.fixture(autouse=True)
def gercek_tarayici_yok(monkeypatch):
    """Testler gerçek Chromium açmaz, internete çıkmaz. Tarayıcı kaynağını deneyen testler
    tarayici.sayfa_html'i kaydedilmiş sayfalarla değiştirir."""
    from mevzuat.sources import tarayici

    def yasak(url, bekle):
        raise RuntimeError(f"Testte gerçek tarayıcı açılmaz: {url}")

    monkeypatch.setattr(tarayici, "sayfa_html", yasak)

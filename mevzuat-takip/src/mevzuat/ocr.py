"""Taranmış PDF'ler için OCR (Tesseract, Türkçe).

Önemli, OCR çıktısı güvenilir değildir. 1 Ekim 2026'daki ölçüm (tessdata_best, 300 DPI) şöyleydi.
  - ÖTV kararında tablodaki 6 tutarın 6'sı da doğru okundu, ama "(I) sayılı liste" ifadesi "(D)" ya da "(DD)" diye okundu.
  - Malvarlığı dondurma listesinde "(KTJ)" yerine "(KT)" okundu, Kiril ve Arapça isimler anlamsız çıktı.
Bu yüzden OCR metni sınıflandırma için, yani hangi iş koluna ait olduğunu bulmak için kullanılır. Raporda "otomatik okunmuş
taslak" olarak işaretlenir. Hukuki dayanak her zaman orijinal belgedir.

Kurulum aşağıda.
  Linux     apt install tesseract-ocr tesseract-ocr-tur
  Windows   winget install UB-Mannheim.TesseractOCR, sonra tur.traineddata (tessdata_best) indirilir
Ayar olarak MEVZUAT_TESSERACT çalıştırılabilir dosyayı, MEVZUAT_TESSDATA dil dosyalarının klasörünü gösterir.
"""
# OCR resimdeki yazıyı okuyup metne çevirmek demek. Resmî Gazete PDF'lerinin çoğu taranmış resim olduğu için şart.
# Bu dosya PDF sayfalarını resme çevirip Tesseract programına okutuyor.

import logging
# os.environ, ortam değişkenlerini (.env'deki ayarları) okumak için.
import os
# shutil.which, bir programın bilgisayarda kurulu olup olmadığını ve yerini bulur.
import shutil
# subprocess, başka bir programı (Tesseract) çalıştırıp çıktısını almak için.
import subprocess
# cache, fonksiyonun sonucunu hatırlar, ikinci çağrıda tekrar hesaplamaz.
from functools import cache
from pathlib import Path

# pypdfium2, PDF sayfalarını resme çeviren kütüphane (Chrome'un PDF motoru).
import pypdfium2

log = logging.getLogger(__name__)

# Resim çözünürlüğü, 300 nokta/inç. Daha düşük olursa OCR daha çok hata yapıyor.
DPI = 300
# Tesseract'a "Türkçe oku" demek için dil kodu.
DIL = "tur"
SAYFA_ZAMAN_ASIMI = 120  # saniye
# Windows'ta Tesseract genelde buraya kurulur, ayar yoksa burada da ararız.
WINDOWS_VARSAYILAN = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


# Tesseract programının yerini bulur. Bir kez bulur, sonra hatırlar, @cache bunu sağlıyor.
@cache
def tesseract_yolu() -> str | None:
    """Kullanılabilir Tesseract yolu. Yoksa None döner ve sistem OCR olmadan çalışmaya devam eder."""
    # Önce .env'deki ayara, yoksa sistemde kurulu "tesseract" komutuna bak.
    aday = os.environ.get("MEVZUAT_TESSERACT") or shutil.which("tesseract")
    # Hâlâ yoksa Windows'un standart kurulum yerine bak.
    if not aday and WINDOWS_VARSAYILAN.exists():
        aday = str(WINDOWS_VARSAYILAN)
    # Hiç yoksa uyar ama çökme, taranmış PDF'ler "OCR gerekli" diye işaretlenip geçilir.
    if not aday:
        log.warning("Tesseract bulunamadı; taranmış PDF'ler OCR_GEREKLI olarak kalacak.")
        return None
    return aday


# OCR yapılabilir mi, yani Tesseract bulundu mu.
def ocr_kullanilabilir() -> bool:
    return tesseract_yolu() is not None


# Tesseract'ı çalıştırmak için komut satırını hazırlar, "tesseract resim.png stdout -l tur --psm 6".
def _komut(goruntu_yolu: str) -> list[str]:
    # stdout sonucu dosyaya değil ekrana yazdırır, biz oradan okuyoruz. --psm 6 sayfayı düz bir yazı bloğu gibi okutur.
    komut = [tesseract_yolu(), goruntu_yolu, "stdout", "-l", DIL, "--psm", "6"]
    # Türkçe dil dosyası özel bir klasördeyse onu da söyle.
    if tessdata := os.environ.get("MEVZUAT_TESSDATA"):
        komut += ["--tessdata-dir", tessdata]
    return komut


# PDF'in istenen sayfalarını tek tek resme çevirip OCR'dan geçirir. Sonuç, {sayfa no, okunan metin}.
def sayfalari_ocr(veri: bytes, gecici_klasor: Path, sayfa_nolari: list[int] | None = None) -> dict[int, str]:
    """İstenen sayfaları görüntüye çevirip OCR'dan geçirir. Sayfa numaraları 0'dan başlar, None verilirse bütün sayfalar okunur."""
    # PDF'i bellekten aç.
    pdf = pypdfium2.PdfDocument(veri)
    sonuc_metinleri = {}
    try:
        # Sayfa listesi verilmediyse bütün sayfalar.
        for i in sayfa_nolari if sayfa_nolari is not None else range(len(pdf)):
            # Geçici klasörde "sayfa_3.png" gibi bir resim dosyası adı.
            goruntu = gecici_klasor / f"sayfa_{i + 1}.png"
            # Sayfayı 300 DPI resme çevirip kaydet (PDF'in kendi birimi 72 DPI, o yüzden 300/72 kat büyütüyoruz).
            pdf[i].render(scale=DPI / 72).to_pil().save(goruntu)
            # Tesseract'ı çalıştır, çıktısını yakala, 120 saniyeden uzun sürerse kes.
            sonuc = subprocess.run(_komut(str(goruntu)), capture_output=True, timeout=SAYFA_ZAMAN_ASIMI)
            # Tesseract hata koduyla bittiyse hata mesajının ilk 300 karakteriyle dur.
            if sonuc.returncode != 0:
                raise RuntimeError(f"Tesseract hata verdi: {sonuc.stderr.decode('utf-8', 'replace')[:300]}")
            # Windows'ta Tesseract satır sonu olarak \r\n verir, HTML'de satır aralığı ikiye katlanıyordu.
            sonuc_metinleri[i] = sonuc.stdout.decode("utf-8").replace("\r\n", "\n").strip()
    # Ne olursa olsun PDF'i kapat (bellek boşalsın).
    finally:
        pdf.close()
    return sonuc_metinleri

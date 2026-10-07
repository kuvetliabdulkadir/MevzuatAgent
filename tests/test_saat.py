import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset sadece Unix'te (sunucu Linux)")
def test_sunucu_utc_olsa_da_uygulama_turkiye_saatinde():
    """Saat politikası: sunucu UTC'de bırakılsa da uygulamanın 'şimdi'si ve 'bugün'ü Türkiye'ye göre."""
    kod = (
        "import time; from mevzuat.cli import saat_dilimini_sabitle; "
        "saat_dilimini_sabitle(); print(time.strftime('%z'))"
    )
    cikti = subprocess.run([sys.executable, "-c", kod], env={"TZ": "UTC"}, capture_output=True, text=True, check=True)
    assert cikti.stdout.strip() == "+0300"


@pytest.mark.skipif(hasattr(time, "tzset"), reason="sadece Windows (geliştirme makinesi)")
def test_windowsta_saate_dokunulmaz():
    """Windows'ta TZ=Europe/Istanbul C kütüphanesince tanınmıyor ve saati UTC'ye çeviriyordu (yakalanan hata).
    C kütüphanesi TZ'yi ilk saat okumasında bir kez okur; bu yüzden ayar, süreçte saat okunmadan ÖNCE yapılmalı."""
    sabitlenmis = "from mevzuat.cli import saat_dilimini_sabitle; saat_dilimini_sabitle(); "
    olc = "import datetime; print(datetime.datetime.now().astimezone().utcoffset())"
    def calistir(kod):
        return subprocess.run([sys.executable, "-c", kod], capture_output=True, text=True, check=True).stdout.strip()
    assert calistir(sabitlenmis + olc) == calistir(olc)


# ---- Docker zamanlayıcısı (container'da systemd yok) ---------------------------------------------

def test_zamanlayici_sonraki_calisma():
    from datetime import datetime

    from mevzuat.cli import calisma_saatleri, sonraki_calisma

    saatler = calisma_saatleri("18:00, 06:30")  # sıra ve boşluk önemli değil
    assert [s.strftime("%H:%M") for s in saatler] == ["06:30", "18:00"]
    assert sonraki_calisma(datetime(2026, 10, 2, 5, 0), saatler) == datetime(2026, 10, 2, 6, 30)
    assert sonraki_calisma(datetime(2026, 10, 2, 6, 30), saatler) == datetime(2026, 10, 2, 18, 0)  # tam saatte: bir sonraki
    assert sonraki_calisma(datetime(2026, 10, 2, 19, 0), saatler) == datetime(2026, 10, 3, 6, 30)  # gün devri
    assert sonraki_calisma(datetime(2026, 12, 31, 23, 59), saatler) == datetime(2027, 1, 1, 6, 30)  # yıl devri


def test_zamanlayici_hatali_saat_baslarken_durur():
    from mevzuat.cli import calisma_saatleri

    with pytest.raises(SystemExit, match="MEVZUAT_CALISMA_SAATLERI"):
        calisma_saatleri("6.30")

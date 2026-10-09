import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from filelock import FileLock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mevzuat import gunluk, pipeline
from mevzuat.db import Gonderim, KaynakDurumu, Kullanici, Rapor, init_db
from mevzuat.filtre import konulari_yukle
from mevzuat.mail import Mail
from mevzuat.sources import kaynaklari_yukle
from mevzuat.sources import resmi_gazete as rg

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FIHRIST = (FIXTURES / "rg_fihrist_2026-10-01.html").read_text(encoding="utf-8")
PDF = (FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes()
PAZARTESI = datetime(2026, 9, 28, 6, 30)  # 28 Eylül 2026 Pazartesi
PERSEMBE = datetime(2026, 10, 1, 6, 30)


@pytest.fixture(autouse=True)
def beklemesiz(monkeypatch):
    monkeypatch.setattr(rg, "ISTEK_ARASI_BEKLEME", 0)
    monkeypatch.setattr(pipeline, "ISTEK_ARASI_BEKLEME", 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        s.add(Kullanici(eposta="sorumlu@firma.com", ad="Sorumlu", parola_hash="x", rol="onaylayici",
                        aktif=True, basarisiz_giris=0, olusturuldu=datetime(2026, 9, 1)))
        s.commit()
        yield s


@pytest.fixture
def konular():
    return konulari_yukle(ROOT / "config" / "konular.toml")


@pytest.fixture
def kaynaklar(tmp_path):
    # Sadece RG: testler MASAK'ın sahte cevabına bağlı olmasın.
    yol = tmp_path / "k.toml"
    yol.write_text('[[kaynak]]\nad = "resmi_gazete"\ntip = "resmi_gazete"\n', encoding="utf-8")
    return kaynaklari_yukle(yol)


class Posta:
    def __init__(self):
        self.giden: list[Mail] = []

    def gonder(self, mail: Mail) -> None:
        self.giden.append(mail)

    def konular(self) -> list[str]:
        return [m.konu for m in self.giden]


def _client(fihrist_var: bool = True, site_cokuk: bool = False) -> httpx.Client:
    def handler(request):
        url = str(request.url)
        if site_cokuk:
            return httpx.Response(503)
        if "/fihrist" in url:
            if "mukerrer=" in url or not fihrist_var:
                return httpx.Response(302, headers={"Location": "/"})
            return httpx.Response(200, text=FIHRIST)
        if url.endswith(".pdf"):
            return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _ayarlar(**kw) -> gunluk.Ayarlar:
    return gunluk.Ayarlar(admin_alicilari=["admin@firma.com"], **kw)


def test_site_coktuyse_yoneticiye_uyari(session, kaynaklar, konular):
    posta = Posta()
    with _client(site_cokuk=True) as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PERSEMBE)

    assert sonuc.calisma.durum == "HATALI"
    assert sonuc.uyari_gonderildi
    uyari = [m for m in posta.giden if m.konu.startswith("UYARI")]
    assert len(uyari) == 1 and uyari[0].alicilar == ["admin@firma.com"]
    assert "503" in uyari[0].metin


def test_takilan_kaynak_tespit_edilir(session, kaynaklar, konular):
    # Checkpoint 3 gün önce ilerlemiş, bugün de gazete "henüz yok" → kaynak takılmış görünür.
    session.add(KaynakDurumu(kaynak="resmi_gazete", checkpoint="2026-10-01", guncellendi=PERSEMBE - timedelta(days=3)))
    session.commit()
    posta = Posta()
    with _client(fihrist_var=False) as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PERSEMBE)

    assert any("ilerlemiyor" in s for s in sonuc.sorunlar)
    assert any(m.konu.startswith("UYARI") for m in posta.giden)


def test_nabiz_haftada_bir_gunun_ilk_calismasinda(session, kaynaklar, konular):
    posta = Posta()
    with _client() as c:
        sabah = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PAZARTESI)
        aksam = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(),
                                simdi=PAZARTESI.replace(hour=18))
        ertesi = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(),
                                 simdi=PAZARTESI + timedelta(days=1))

    assert (sabah.nabiz_gonderildi, aksam.nabiz_gonderildi, ertesi.nabiz_gonderildi) == (True, False, False)
    nabiz = [m for m in posta.giden if "haftalık durum" in m.konu]
    assert len(nabiz) == 1
    assert "Mevzuat Takip sistemi çalışıyor" in nabiz[0].metin


def test_kilit_varken_ikinci_calisma_atlanir(tmp_path):
    kilit_yolu = tmp_path / "mevzuat.lock"
    with FileLock(str(kilit_yolu)):
        sonuc = subprocess.run(
            [sys.executable, "-m", "mevzuat.cli", "gunluk"],
            capture_output=True, text=True, encoding="utf-8",
            env={**__import__("os").environ, "MEVZUAT_KILIT": str(kilit_yolu), "PYTHONIOENCODING": "utf-8"},
            cwd=ROOT, timeout=60,
        )
    assert "Başka bir çalışma sürüyor" in sonuc.stdout
    assert sonuc.returncode == 0


def test_normal_gun_rapor_onaya_sunulur_dagitilmaz(session, kaynaklar, konular):
    posta = Posta()
    with _client() as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta,
                                _ayarlar(panel_adresi="https://mevzuat.firma.com.tr"), simdi=PERSEMBE)

    assert sonuc.calisma.durum == "BASARILI"
    assert sonuc.sorunlar == []
    assert sonuc.rapor.durum == "ONAY_BEKLIYOR"
    # Tek mail: sorumluya onay bildirimi. Dağıtım listesine onaysız hiçbir şey gitmez.
    assert len(posta.giden) == 1
    assert posta.giden[0].konu.startswith("Onay bekliyor:")
    assert posta.giden[0].alicilar == ["sorumlu@firma.com"]
    # Onaylayıcı belgelerin kendisini de görür (kullanıcı kararı, 2026-10-05).
    assert posta.giden[0].ekler and all(e.mime == "application/pdf" for e in posta.giden[0].ekler)
    assert "Onay paneline git" in posta.giden[0].html


def test_panel_adresi_yoksa_linksiz_onay_maili_yoneticiye_bildirilir(session, kaynaklar, konular):
    posta = Posta()
    with _client() as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PERSEMBE)

    assert sonuc.rapor.durum == "ONAY_BEKLIYOR"
    assert any("MEVZUAT_PANEL_ADRESI" in s for s in sonuc.sorunlar)
    assert sonuc.uyari_gonderildi


def test_onaylayici_yoksa_sorun_sayilir(session, kaynaklar, konular):
    session.query(Kullanici).delete()
    session.commit()
    posta = Posta()
    with _client() as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PERSEMBE)
    assert any("Onaylayıcı kullanıcı yok" in s for s in sonuc.sorunlar)


def _onayli_rapor(session, gonderim_durumu: str, **gonderim) -> tuple[Rapor, Gonderim]:
    r = Rapor(olusturuldu=PERSEMBE - timedelta(hours=5), durum="ONAYLANDI", konu="Eski rapor",
              alicilar=["kuyum@firma.com"], kayit_sayisi=0)
    session.add(r)
    session.flush()
    g = Gonderim(rapor_id=r.id, alicilar=["kuyum@firma.com"], kayit_idler=[], gruplar=["Kuyum"],
                 durum=gonderim_durumu, **gonderim)
    session.add(g)
    session.commit()
    return r, g


def test_onaylanip_dagitilamayan_rapor_tekrar_denenir(session, kaynaklar, konular):
    r, g = _onayli_rapor(session, "BEKLIYOR", hata="Gönderilemedi: SMTP kapalı")
    posta = Posta()
    with _client(fihrist_var=False) as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PERSEMBE)
    assert g.durum == "GONDERILDI" and r.durum == "GONDERILDI"
    assert [m.alicilar for m in posta.giden if m.konu.startswith("Mevzuat Raporu")] == [["kuyum@firma.com"]]
    assert not any("dağıtılamadı" in s for s in sonuc.sorunlar)


def test_bekleyen_onay_hatirlatilir_gecikirse_yoneticiye_bildirilir(session, kaynaklar, konular):
    session.add(Rapor(olusturuldu=PERSEMBE - timedelta(days=4), durum="ONAY_BEKLIYOR", konu="Eski rapor",
                      alicilar=[], kayit_sayisi=1))
    session.commit()
    posta = Posta()
    with _client(fihrist_var=False) as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PERSEMBE)

    hatirlatma = [m for m in posta.giden if m.konu.startswith("Hatırlatma")]
    assert len(hatirlatma) == 1 and hatirlatma[0].alicilar == ["sorumlu@firma.com"]
    assert any("onay bekliyor" in s for s in sonuc.sorunlar)
    assert any(m.konu.startswith("UYARI") for m in posta.giden)


def test_gunluk_komutu_ucdan_uca_cokmez(tmp_path):
    """CLI'nin kendisi: sonuç yazdırılırken DB oturumu kapalı olduğu için çöküyordu (canlı denemede bulundu)."""
    import os

    env = {
        **os.environ, "PYTHONIOENCODING": "utf-8",
        "MEVZUAT_DB_URL": f"sqlite:///{tmp_path / 'm.db'}", "MEVZUAT_KILIT": str(tmp_path / "m.lock"),
        "MEVZUAT_KAYNAKLAR": str(tmp_path / "k.toml"), "MEVZUAT_IZLENEN": str(tmp_path / "i.toml"),
    }
    (tmp_path / "i.toml").write_text("aktif = false\n", encoding="utf-8")  # testte internete çıkılmaz
    (tmp_path / "k.toml").write_text('[[kaynak]]\nad = "bos"\ntip = "wordpress"\netiket = "X"\n'
                                     'api_url = "http://127.0.0.1:9/yok"\naktif = false\n', encoding="utf-8")
    sonuc = subprocess.run([sys.executable, "-m", "mevzuat.cli", "gunluk"], capture_output=True, text=True,
                           encoding="utf-8", env=env, cwd=ROOT, timeout=120)
    assert "Traceback" not in sonuc.stderr, sonuc.stderr
    assert "Çalışma #1: BASARILI, rapor: onaya sunulacak ilgili kayıt yok" in sonuc.stdout


def test_belirsiz_gonderim_yoneticiye_sorulur_tekrar_gonderilmez(session, kaynaklar, konular):
    r, g = _onayli_rapor(session, "GONDERILIYOR", gonderim_denemesi=datetime.now() - timedelta(hours=2))
    posta = Posta()
    with _client(fihrist_var=False) as c:
        sonuc = gunluk.calistir(session, c, kaynaklar, konular, posta, _ayarlar(), simdi=PERSEMBE)

    belirsiz = [s for s in sonuc.sorunlar if "sonucu kaydedilemedi" in s]
    assert len(belirsiz) == 1
    assert f"gonderim-durum --id {g.id}" in belirsiz[0] and "kuyum@firma.com" in belirsiz[0]
    assert not any("dağıtılamadı" in s for s in sonuc.sorunlar)  # aynı sorun iki kez yazılmaz
    assert not any(m.konu.startswith("Mevzuat Raporu") for m in posta.giden)  # dağıtım maili gitmedi
    assert g.durum == "GONDERILIYOR" and r.durum == "ONAYLANDI"


def test_gonderim_durum_komutu_sadece_o_maili_cozer(tmp_path, monkeypatch):
    """Belirsiz gönderimin elle çözümü: --gonderildi işaretler, --tekrar-gonder sadece o maili yeniden yollar."""
    import argparse

    from mevzuat import cli
    from mevzuat.db import Denetim, make_engine, migrate

    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("MEVZUAT_DB_URL", url)
    engine = make_engine(url)
    migrate(engine)
    with Session(engine) as s:
        r = Rapor(olusturuldu=PERSEMBE, durum="ONAYLANDI", konu="R", alicilar=[], kayit_sayisi=0)
        s.add(r)
        s.flush()
        gitti = Gonderim(rapor_id=r.id, alicilar=["a@firma.com"], kayit_idler=[], gruplar=["A"], durum="GONDERILDI",
                         gonderildi=PERSEMBE)
        takili = Gonderim(rapor_id=r.id, alicilar=["b@firma.com"], kayit_idler=[], gruplar=["B"],
                          durum="GONDERILIYOR", gonderim_denemesi=PERSEMBE)
        s.add_all([gitti, takili])
        s.commit()
        rapor_id, takili_id = r.id, takili.id

    posta = Posta()
    monkeypatch.setattr(cli, "_gonderici", lambda ayarlar: posta)
    cli.gonderim_durum_komutu(argparse.Namespace(id=takili_id, gonderildi=False, tekrar_gonder=True))
    assert [m.alicilar for m in posta.giden] == [["b@firma.com"]]  # a'ya tekrar gitmedi
    with Session(engine) as s:
        assert s.get(Gonderim, takili_id).durum == "GONDERILDI" and s.get(Rapor, rapor_id).durum == "GONDERILDI"
        assert s.query(Denetim).filter_by(islem="elle_tekrar_gonderim").count() == 1

    # Artık GONDERILIYOR değil: komut reddeder, hiçbir şey göndermez.
    with pytest.raises(SystemExit, match="GONDERILIYOR durumunda değil"):
        cli.gonderim_durum_komutu(argparse.Namespace(id=takili_id, gonderildi=True, tekrar_gonder=False))
    assert len(posta.giden) == 1


@pytest.mark.parametrize("adres, sonuc", [
    ("", None),
    ("https://mevzuat.firma.com.tr", "https://mevzuat.firma.com.tr"),
    ("http://10.0.0.5:8000/panel", "http://10.0.0.5:8000/panel"),
])
def test_panel_adresi_ayari(monkeypatch, adres, sonuc):
    from mevzuat.mail import panel_adresi_ayardan
    monkeypatch.setenv("MEVZUAT_PANEL_ADRESI", adres)
    assert panel_adresi_ayardan() == sonuc


@pytest.mark.parametrize("adres", ["mevzuat.firma.com.tr", "javascript:alert(1)", 'https://x.com/"><script>', "https://x.com/?a=1"])
def test_panel_adresi_hatali(monkeypatch, adres):
    from mevzuat.mail import panel_adresi_ayardan
    monkeypatch.setenv("MEVZUAT_PANEL_ADRESI", adres)
    with pytest.raises(SystemExit, match="MEVZUAT_PANEL_ADRESI"):
        panel_adresi_ayardan()

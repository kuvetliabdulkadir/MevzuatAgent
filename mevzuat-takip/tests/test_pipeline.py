import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from mevzuat import pipeline
from mevzuat.db import Calisma, KaynakDurumu, Kayit, init_db
from mevzuat.filtre import konulari_yukle
from mevzuat.sources import kaynaklari_yukle
from mevzuat.sources import mevzuat_gov
from mevzuat.sources import resmi_gazete as rg

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FIHRIST = (FIXTURES / "rg_fihrist_2026-10-01.html").read_text(encoding="utf-8")
BAYRAM = (FIXTURES / "rg_fihrist_2026-08-30_yayimlanmadi.html").read_text(encoding="utf-8")
PDF = (FIXTURES / "rg_20261001-3-2_taranmis.pdf").read_bytes()
HTM = (FIXTURES / "rg_20261001-1-1_cp1254.htm").read_bytes()
MASAK_POSTS = json.loads((FIXTURES / "masak_posts.json").read_text(encoding="utf-8"))
GIB_DUYURULAR = json.loads((FIXTURES / "gib_duyurular_mevzuat.json").read_text(encoding="utf-8"))

RG = "resmi_gazete"
MASAK = "masak"
GIB = "gib_mevzuat"
EKIM_1 = date(2026, 10, 1)
SIMDI = datetime(2026, 10, 1, 7, 0)


@pytest.fixture(autouse=True)
def beklemesiz(monkeypatch):
    monkeypatch.setattr(rg, "ISTEK_ARASI_BEKLEME", 0)
    monkeypatch.setattr(mevzuat_gov, "ISTEK_ARASI_BEKLEME", 0)
    monkeypatch.setattr(pipeline, "ISTEK_ARASI_BEKLEME", 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def konular():
    return konulari_yukle(ROOT / "config" / "konular.toml")


@pytest.fixture
def kaynaklar():
    return kaynaklari_yukle(ROOT / "config" / "kaynaklar.toml")


class SahteSite:
    """Gerçek sayfalarla cevap veren sahte HTTP katmanı. `fihristler`: tarih → html | None (302)."""

    def __init__(self, fihristler: dict[str, str | None], masak_hata: bool = False):
        self.fihristler = fihristler
        self.masak_hata = masak_hata
        self.istekler: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.istekler.append(url)
        if "/fihrist" in url:
            if "mukerrer=" in url:
                return httpx.Response(302, headers={"Location": "/"})
            html = self.fihristler.get(request.url.params["tarih"])
            if html is None:
                return httpx.Response(302, headers={"Location": "/"})
            return httpx.Response(200, text=html)
        if "masak" in url:
            if self.masak_hata:
                return httpx.Response(500)
            if request.url.path.rstrip("/").endswith("/posts"):
                return httpx.Response(200, json=MASAK_POSTS, headers={"X-WP-TotalPages": "1"})
            return httpx.Response(200, json={"content": {"rendered": "<p>Duyuru metni.</p>"}})
        if "gib.gov.tr/api" in url:
            if url.split("?")[0].endswith("/listPublish"):
                return httpx.Response(200, json=GIB_DUYURULAR)
            return httpx.Response(200, json={"resultContainer": {"description": "<p>Duyuru metni.</p>"}})
        if "mevzuat.gov.tr" in url:
            # Ana sayfa güvenlik anahtarını verir, arama servisi bu testlerde yeni mevzuat döndürmez.
            if request.url.path == "/":
                return httpx.Response(200, text='<input name="antiforgerytoken" type="hidden" value="anahtar" />')
            return httpx.Response(200, json={"draw": 1, "recordsTotal": 0, "recordsFiltered": 0, "data": []})
        if url.endswith(".pdf"):
            return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        if url.endswith(".htm"):
            return httpx.Response(200, content=HTM, headers={"content-type": "text/html"})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=False)


def _checkpoint(session, kaynak):
    return session.get(KaynakDurumu, kaynak).checkpoint


def _sayi(session, **filtre):
    return session.scalar(select(func.count()).select_from(Kayit).filter_by(**filtre))


def test_ilk_calisma(session, kaynaklar, konular):
    site = SahteSite({"2026-10-01": FIHRIST})
    with site.client() as c:
        calisma = pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)

    assert calisma.durum == "BASARILI", calisma.hata
    assert calisma.ozet["resmi_gazete"]["yeni"] == 18  # ilanlar hariç
    assert calisma.ozet["masak"]["yeni"] == 10
    assert calisma.ozet[GIB]["yeni"] == 1  # ilk çalışma: sadece bugünün duyurusu
    assert _checkpoint(session, RG) == "2026-10-01"
    assert _checkpoint(session, GIB) == SIMDI.isoformat()

    otv = session.scalar(select(Kayit).where(Kayit.baslik.contains("Özel Tüketim Vergisi")))
    assert otv.is_kollari == ["Ortak"]
    assert "Vergi" in otv.eslesmeler

    basliklar = [k.baslik for k in session.scalars(select(Kayit).where(Kayit.ilgili))]
    assert any("Malvarlığının Dondurulması" in b for b in basliklar)
    assert any("Özel Tüketim Vergisi" in b for b in basliklar)


def test_icerik_sadece_ilgililer_icin_indirilir(session, kaynaklar, konular):
    site = SahteSite({"2026-10-01": FIHRIST})
    with site.client() as c:
        pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)

    ilgili = _sayi(session, ilgili=True)
    belge_istekleri = [u for u in site.istekler if "/eskiler/" in u or "/posts/" in u or "/findBySlug" in u]
    toplam = 18 + 10 + 1  # RG + MASAK + GİB
    assert 0 < len(belge_istekleri) == ilgili < toplam
    assert _sayi(session, icerik_durumu="BEKLIYOR") == 0
    assert _sayi(session, ilgili=False, icerik_durumu="GEREKSIZ") == toplam - ilgili
    # Taranmış PDF'ler OCR_GEREKLI diye işaretlenir, sessizce "tamam" sayılmaz.
    assert _sayi(session, kaynak=RG, ilgili=True, icerik_durumu="TAMAM") == 0


def test_ayni_gun_tekrar_calisinca_kayit_cogalmaz(session, kaynaklar, konular):
    site = SahteSite({"2026-10-01": FIHRIST})
    with site.client() as c:
        pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)
        ikinci = pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)

    assert ikinci.ozet["resmi_gazete"]["yeni"] == 0
    assert ikinci.ozet["masak"]["yeni"] == 0
    # GİB: checkpoint'ten bir gün geriye örtüşme → 30.09 duyurusu eklenir; 01.10 tekrar eklenmez.
    assert ikinci.ozet[GIB]["yeni"] == 1
    assert _sayi(session) == 18 + 10 + 2


def test_bugun_henuz_cikmadiysa_checkpoint_ilerlemez(session, kaynaklar, konular):
    site = SahteSite({"2026-10-01": FIHRIST})  # 2 Ekim yok → 302
    with site.client() as c:
        pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)
        calisma = pipeline.calistir(session, c, kaynaklar, konular, bugun=date(2026, 10, 2), simdi=SIMDI)

    assert calisma.durum == "BASARILI"
    assert calisma.ozet["resmi_gazete"]["gunler"]["2026-10-02"] == "henuz_yok"
    assert _checkpoint(session, RG) == "2026-10-01"


def test_kacirilan_gunler_telafi_edilir(session, kaynaklar, konular):
    session.add(KaynakDurumu(kaynak=RG, checkpoint="2026-09-29", guncellendi=SIMDI))
    session.commit()
    site = SahteSite({"2026-09-29": BAYRAM, "2026-09-30": BAYRAM, "2026-10-01": FIHRIST})
    with site.client() as c:
        calisma = pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)

    assert list(calisma.ozet["resmi_gazete"]["gunler"]) == ["2026-09-29", "2026-09-30", "2026-10-01"]
    assert calisma.ozet["resmi_gazete"]["gunler"]["2026-09-30"] == "yayimlanmadi"
    assert _checkpoint(session, RG) == "2026-10-01"


def test_gecmis_gun_302_hata_verir_checkpoint_ilerlemez(session, kaynaklar, konular):
    session.add(KaynakDurumu(kaynak=RG, checkpoint="2026-09-30", guncellendi=SIMDI))
    session.commit()
    site = SahteSite({"2026-10-01": FIHRIST})  # 30 Eylül 302 → beklenmeyen durum
    with site.client() as c:
        calisma = pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)

    assert calisma.durum == "HATALI"
    assert "2026-09-30" in calisma.hata
    assert _checkpoint(session, RG) == "2026-09-30"


def test_bir_kaynagin_hatasi_digerini_durdurmaz(session, kaynaklar, konular):
    site = SahteSite({"2026-10-01": FIHRIST}, masak_hata=True)
    with site.client() as c:
        calisma = pipeline.calistir(session, c, kaynaklar, konular, bugun=EKIM_1, simdi=SIMDI)

    assert calisma.durum == "HATALI"
    assert "masak" in calisma.hata
    assert calisma.ozet["resmi_gazete"]["yeni"] == 18
    assert session.get(KaynakDurumu, MASAK) is None  # MASAK checkpoint'i ilerlemedi
    assert session.scalar(select(func.count()).select_from(Calisma)) == 1

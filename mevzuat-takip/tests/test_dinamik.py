"""Sistemin koda dokunmadan genişleyebildiğini doğrulayan testler: yeni kaynak, hatalı ayar, şema."""

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mevzuat import pipeline
from mevzuat.db import Base, KaynakDurumu, Kayit, init_db, migrate
from mevzuat.filtre import konulari_yukle
from mevzuat.sources import kaynaklari_yukle

ROOT = Path(__file__).parents[1]
MASAK_POSTS = json.loads((ROOT / "tests" / "fixtures" / "masak_posts.json").read_text(encoding="utf-8"))


def _toml(tmp_path: Path, icerik: str) -> Path:
    yol = tmp_path / "kaynaklar.toml"
    yol.write_text(icerik, encoding="utf-8")
    return yol


def test_yeni_kaynak_sadece_ayarla_eklenir(tmp_path, monkeypatch):
    """Kod değişmeden, ayar dosyasına eklenen ikinci bir WordPress sitesi taranır."""
    monkeypatch.setattr(pipeline, "ISTEK_ARASI_BEKLEME", 0)
    yol = _toml(tmp_path, """
[[kaynak]]
ad = "yeni_kurum"
tip = "wordpress"
etiket = "Yeni Kurum Duyurusu"
api_url = "https://yeni-kurum.gov.tr/wp-json/wp/v2/posts"
""")

    def handler(request):
        assert request.url.host == "yeni-kurum.gov.tr"
        if request.url.path.endswith("/posts"):
            return httpx.Response(200, json=MASAK_POSTS, headers={"X-WP-TotalPages": "1"})
        return httpx.Response(200, json={"content": {"rendered": "<p>metin</p>"}})

    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session, httpx.Client(transport=httpx.MockTransport(handler)) as client:
        calisma = pipeline.calistir(
            session, client, kaynaklari_yukle(yol), konulari_yukle(ROOT / "config" / "konular.toml"),
            bugun=date(2026, 10, 1), simdi=datetime(2026, 10, 1, 7),
        )
        assert calisma.durum == "BASARILI", calisma.hata
        assert calisma.ozet["yeni_kurum"]["yeni"] == 10
        kayit = session.scalar(select(Kayit).where(Kayit.kaynak == "yeni_kurum"))
        assert kayit.kaynakca.startswith("Yeni Kurum Duyurusu, ")
        assert session.get(KaynakDurumu, "yeni_kurum") is not None


def test_pasif_kaynak_yuklenmez(tmp_path):
    yol = _toml(tmp_path, """
[[kaynak]]
ad = "resmi_gazete"
tip = "resmi_gazete"
aktif = false
""")
    assert kaynaklari_yukle(yol) == []


@pytest.mark.parametrize("icerik, mesaj", [
    ('[[kaynak]]\nad = "x"\ntip = "bilinmeyen"', "Geçersiz kaynak"),
    ('[[kaynak]]\nad = "x"\ntip = "wordpress"\netiket = "X"', "ayarları hatalı"),  # api_url eksik
    ('[[kaynak]]\nad = "x"\ntip = "resmi_gazete"\n[[kaynak]]\nad = "x"\ntip = "resmi_gazete"', "tekrar"),
])
def test_hatali_ayar_en_basta_hata_verir(tmp_path, icerik, mesaj):
    with pytest.raises(ValueError, match=mesaj):
        kaynaklari_yukle(_toml(tmp_path, icerik))


def test_gercek_ayar_dosyalari_yuklenir():
    kaynaklar = kaynaklari_yukle(ROOT / "config" / "kaynaklar.toml")
    assert {k.ad for k in kaynaklar} == {"resmi_gazete", "masak", "gib_mevzuat", "mevzuat_gov_yeni"}
    konular = konulari_yukle(ROOT / "config" / "konular.toml")
    assert {ik for k in konular for ik in k.is_kollari} == {"Kuyum", "Döviz/Altın", "Oto kiralama", "Ortak"}


def test_migrationlar_modelle_birebir_ayni(tmp_path):
    """Model değişip migration üretilmeyi unutulursa bu test kırılır."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migrate(engine)
    with engine.connect() as conn:
        farklar = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert farklar == [], f"Modelde migration'a yansımamış değişiklik var: {farklar}"


def test_varsayilan_konu_basliktan_bagimsiz_ilgili_yapar(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ISTEK_ARASI_BEKLEME", 0)
    yol = _toml(tmp_path, """
[[kaynak]]
ad = "masak"
tip = "wordpress"
etiket = "MASAK Duyurusu"
api_url = "https://masak.hmb.gov.tr/portal/v2/posts"
varsayilan_konular = ["MASAK / suç gelirleri / yaptırımlar"]
""")

    def handler(request):
        if request.url.path.endswith("/posts"):
            return httpx.Response(200, json=MASAK_POSTS, headers={"X-WP-TotalPages": "1"})
        return httpx.Response(200, json={"content": {"rendered": "<p>metin</p>"}})

    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session, httpx.Client(transport=httpx.MockTransport(handler)) as client:
        pipeline.calistir(
            session, client, kaynaklari_yukle(yol), konulari_yukle(ROOT / "config" / "konular.toml"),
            bugun=date(2026, 10, 1), simdi=datetime(2026, 10, 1, 7),
        )
        fatf = session.scalar(select(Kayit).where(Kayit.baslik.contains("FATF")))
        assert fatf.ilgili
        assert fatf.is_kollari == ["Ortak"]


def test_varsayilan_konuda_yazim_hatasi_yakalanir(tmp_path):
    yol = _toml(tmp_path, """
[[kaynak]]
ad = "masak"
tip = "wordpress"
etiket = "MASAK"
api_url = "https://x"
varsayilan_konular = ["MASAK yanlis yazilmis"]
""")
    with pytest.raises(ValueError, match="tanımsız konu"):
        pipeline.ayarlari_dogrula(kaynaklari_yukle(yol), konulari_yukle(ROOT / "config" / "konular.toml"))

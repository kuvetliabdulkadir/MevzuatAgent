from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from mevzuat import saklama
from mevzuat.db import Calisma, Denetim, Gonderim, Kayit, Rapor, TaramaIstegi, init_db

SIMDI = datetime(2026, 11, 20, 7)


def _rapor(s: Session, durum: str, olusturuldu: datetime, **alanlar) -> Rapor:
    r = Rapor(olusturuldu=olusturuldu, durum=durum, konu="Mevzuat raporu", kayit_sayisi=1, **alanlar)
    s.add(r)
    s.flush()
    return r


def _kayit(s: Session, dis_id: str, rapor: Rapor | None, ilgili: bool = True) -> Kayit:
    k = Kayit(kaynak="resmi_gazete", dis_id=dis_id, yayin_tarihi=date(2026, 10, 1), baslik="Başlık", url=dis_id,
              kaynakca="RG", ilgili=ilgili, icerik="metin" if ilgili else None,
              icerik_durumu="TAMAM" if ilgili else "GEREKSIZ", ilk_gorulme=datetime(2026, 10, 1, 7), calisma_id=1,
              rapor_id=rapor.id if rapor else None)
    s.add(k)
    return k


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        s.add(Calisma(id=1, baslangic=datetime(2026, 10, 1, 7), durum="BASARILI", ozet={}))
        s.commit()
        yield s


def test_suresi_dolan_bitmis_raporlar_kalemleriyle_silinir(session):
    eski = SIMDI - timedelta(days=40)
    gonderilen = _rapor(session, "GONDERILDI", eski, gonderildi=eski)
    reddedilen = _rapor(session, "REDDEDILDI", eski, karar_zamani=eski, karar_notu="ilgisiz")
    yeni = _rapor(session, "GONDERILDI", SIMDI - timedelta(days=5), gonderildi=SIMDI - timedelta(days=5))
    bekleyen = _rapor(session, "ONAY_BEKLIYOR", eski)
    maili_bitmemis = _rapor(session, "ONAYLANDI", eski, karar_zamani=eski)
    for i, r in enumerate([gonderilen, reddedilen, yeni, bekleyen, maili_bitmemis]):
        _kayit(session, f"k{i}", r)
    _kayit(session, "ilgisiz", None, ilgili=False)
    session.add(Gonderim(rapor_id=gonderilen.id, alicilar=["a@firma.com"], kayit_idler=[], gruplar=[], durum="GONDERILDI"))
    session.add(TaramaIstegi(istendi=eski, durum="BITTI", rapor_id=gonderilen.id))
    session.add(Denetim(zaman=eski, islem="giris", detay={}))
    session.add(Denetim(zaman=SIMDI - timedelta(days=1), islem="giris", detay={}))
    session.commit()

    sonuc = saklama.eskileri_sil(session, 30, SIMDI)

    assert sonuc == saklama.Silinenler(rapor=2, kayit=2, gonderim=1, denetim=1)
    kalan = set(session.scalars(select(Rapor.id)))
    assert kalan == {yeni.id, bekleyen.id, maili_bitmemis.id}
    assert set(session.scalars(select(Kayit.dis_id))) == {"k2", "k3", "k4", "ilgisiz"}
    assert session.scalar(select(func.count()).select_from(Gonderim)) == 0
    # İstek kalır, sadece silinen rapora bağlantısı kalkar.
    assert session.scalar(select(TaramaIstegi.rapor_id)) is None
    # Yeni denetim kaydı kalır, silme işleminin kendisi de yazılır.
    islemler = list(session.scalars(select(Denetim.islem).order_by(Denetim.id)))
    assert islemler == ["giris", "eski_kayitlar_silindi"]


def test_sure_dolmadan_ya_da_kapaliyken_silinmez(session):
    eski = SIMDI - timedelta(days=20)
    _rapor(session, "GONDERILDI", eski, gonderildi=eski)
    session.add(Denetim(zaman=eski, islem="giris", detay={}))
    session.commit()

    assert saklama.eskileri_sil(session, 30, SIMDI) == saklama.Silinenler()
    assert saklama.eskileri_sil(session, 0, SIMDI + timedelta(days=999)) == saklama.Silinenler()
    assert session.scalar(select(func.count()).select_from(Rapor)) == 1
    assert session.scalar(select(func.count()).select_from(Denetim)) == 1


def test_sure_ayardan_okunur(monkeypatch):
    monkeypatch.delenv("MEVZUAT_SAKLAMA_GUN", raising=False)
    assert saklama.saklama_gunu() == 30
    monkeypatch.setenv("MEVZUAT_SAKLAMA_GUN", "15")
    assert saklama.saklama_gunu() == 15

import re
from datetime import date, datetime, timedelta
from email import message_from_bytes
from pathlib import Path

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from mevzuat import rapor
from mevzuat.db import (AliciGrubu, Calisma, Gonderim, Kayit, KonuTanimi, Kullanici, Rapor, alembic_config, init_db,
                        migrate)
from mevzuat.filtre import TUM_DUYURULAR
from mevzuat.mail import Ek, Mail, mesaj_olustur

FIXTURES = Path(__file__).parent / "fixtures"
PDF = (FIXTURES / "rg_20261001-3-3_govde_gorsel.pdf").read_bytes()
GUN = date(2026, 10, 1)


def _kayit(**alanlar) -> Kayit:
    varsayilan = dict(
        kaynak="resmi_gazete", dis_id="https://www.resmigazete.gov.tr/eskiler/2026/10/20261001-3-3.pdf",
        yayin_tarihi=GUN, baslik="Bazı Mallara Uygulanan Özel Tüketim Vergisi Tutarlarının Yeniden Belirlenmesi",
        tur="CUMHURBAŞKANI KARARLARI", bolum="", sayi=33387, mukerrer=0,
        url="https://www.resmigazete.gov.tr/eskiler/2026/10/20261001-3-3.pdf",
        kaynakca="Resmî Gazete, 01.10.2026, Sayı: 33387, CUMHURBAŞKANI KARARLARI: Bazı Mallara …",
        ilgili=True, eslesmeler={"Vergi": ["özel tüketim vergisi"], "Araç kiralama ve taşıtlar": ["benzin (içerikte)"]},
        is_kollari=["Ortak", "Oto kiralama"],
        icerik="MADDE 1- (1) … kurşunsuz benzin 95 oktan türü malların özel tüketim vergisi tutarları, "
               "aşağıdaki tabloda gösterildiği şekilde tespit edilmiştir.\n7,9000 TL/Litre 11,3600 TL/Litre",
        icerik_durumu="OCR_ILE_OKUNDU", ilk_gorulme=datetime(2026, 10, 1, 7), calisma_id=1,
    )
    varsayilan.update(alanlar)
    return Kayit(**varsayilan)


def _client() -> httpx.Client:
    def handler(request):
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=PDF)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


class SahteGonderici:
    def __init__(self, hata: Exception | None = None):
        self.giden: list[Mail] = []
        self.hata = hata

    def gonder(self, mail: Mail) -> None:
        if self.hata:
            raise self.hata
        self.giden.append(mail)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        s.add(Calisma(id=1, baslangic=datetime(2026, 10, 1, 7), durum="BASARILI", ozet={}))
        s.commit()
        yield s


def test_one_cikanlar_eslesen_cumleleri_bulur():
    k = _kayit()
    cumleler = rapor.one_cikanlar(k.icerik, k.eslesmeler)
    assert len(cumleler) == 1
    assert "kurşunsuz benzin" in cumleler[0]


def test_rapor_icerigi():
    with _client() as c:
        mail = rapor.rapor_olustur([_kayit()], GUN, c)

    assert mail.konu == "Mevzuat Raporu — 01.10.2026 — 1 kalem (Ortak, Oto kiralama)"
    for parca in ["Etkilenen", "Ortak, Oto kiralama", "Kaynakça:", "Resmî Gazete, 01.10.2026, Sayı: 33387",
                  "Cumhurbaşkanı Kararı", "kurşunsuz benzin 95 oktan", "Taranmış belgeden otomatik okundu"]:
        assert parca in mail.html, parca
    assert "Taranmış belgeden otomatik okundu" in mail.metin
    assert "MADDE 1-" not in mail.html  # ham metin mailde yok; özet "MADDE 1-" önekisiz
    assert [e.dosya_adi for e in mail.ekler] == ["01_20261001_20261001-3-3.pdf"]


def test_mailde_link_yok():
    k = _kayit(icerik="Ayrıntılar https://masak.hmb.gov.tr/rehber adresinde ve www.ornek.gov.tr sitesinde.",
               icerik_durumu="TAMAM")
    with _client() as c:
        mail = rapor.rapor_olustur([k], GUN, c)
    for govde in (mail.html, mail.metin):
        assert not re.search(r"https?://|www\.|href=", govde, re.IGNORECASE)
    assert "[bağlantı kaldırıldı]" in mail.html


def test_kaynagin_tum_duyurularina_bagli_konunun_aciklamasi_yazilmaz():
    aciklamalar = {"Vergi": "Vergi açıklaması.", "Döviz ve kambiyo": "Döviz açıklaması tekrar etmemeli."}
    kayit = _kayit(eslesmeler={"Vergi": ["özel tüketim vergisi"], "Döviz ve kambiyo": [TUM_DUYURULAR]})
    assert rapor.neden_onemli(kayit, aciklamalar) == [("Vergi", "Vergi açıklaması.")]
    mail = rapor.rapor_olustur([kayit], GUN, None, ek_ekle=False, aciklamalar=aciklamalar)
    for govde in (mail.html, mail.metin):
        assert "Vergi açıklaması." in govde and "tekrar etmemeli" not in govde
    # Sadece kaynak bağıyla gelen kalemde "Neden önemli" hiç çıkmaz.
    yalniz_kaynak = _kayit(eslesmeler={"Vergi": [TUM_DUYURULAR], "Döviz ve kambiyo": [TUM_DUYURULAR]})
    mail = rapor.rapor_olustur([yalniz_kaynak], GUN, None, ek_ekle=False, aciklamalar=aciklamalar)
    assert "Neden önemli" not in mail.html and "Neden önemli" not in mail.metin


def test_konu_aciklamasi_neden_onemli_satirinda():
    aciklamalar = {"Vergi": "ÖTV tutarları <b>doğrudan</b> fiyatlara yansır.\nAyrıntı: https://ornek.gov.tr/x",
                   "Döviz ve kambiyo": "Bu kayıtta eşleşmedi, görünmemeli."}
    mail = rapor.rapor_olustur([_kayit()], GUN, None, ek_ekle=False, aciklamalar=aciklamalar)
    assert "Neden önemli" in mail.html and "<strong>Vergi:</strong> ÖTV tutarları &lt;b&gt;doğrudan&lt;/b&gt;" in mail.html
    assert "fiyatlara yansır.<br>Ayrıntı:" in mail.html  # satır sonu korunur
    satirlar = mail.metin.splitlines()
    neden = next(i for i, s in enumerate(satirlar) if s.startswith("Neden size geldi:"))
    assert satirlar[neden + 1] == "Neden önemli (Vergi): ÖTV tutarları <b>doğrudan</b> fiyatlara yansır."
    for govde in (mail.html, mail.metin):
        assert "görünmemeli" not in govde and "Araç kiralama ve taşıtlar:" not in govde  # açıklamasız konu satırı yok
        assert not re.search(r"https?://|www\.|href=", govde, re.IGNORECASE)  # açıklamada da link yok
    yalin = rapor.rapor_olustur([_kayit()], GUN, None, ek_ekle=False)
    assert "Neden önemli" not in yalin.html and "Neden önemli" not in yalin.metin
    # Açıklama yokken düz metin eskisiyle aynı, "Neden size geldi" satırı tek başına durur.
    satirlar = yalin.metin.splitlines()
    neden = next(i for i, s in enumerate(satirlar) if s.startswith("Neden size geldi:"))
    assert satirlar[neden].endswith("(benzin (içerikte))") and satirlar[neden + 1].startswith("Kaynakça:")


def test_html_kacislanir():
    k = _kayit(baslik="<script>alert(1)</script> Tebliğ", icerik="<b>kalın</b> özel tüketim vergisi metni burada.")
    mail = rapor.rapor_olustur([k], GUN, None, ek_ekle=False)
    assert "<script>" not in mail.html
    assert "&lt;script&gt;" in mail.html


def test_sigmayan_belge_yerine_kaynak_linki(monkeypatch):
    """Boyut sınırını aşan belge eklenmez; yerine sadece o belgenin resmî adresi konur (içerik yine linksiz)."""
    monkeypatch.setattr(rapor, "EK_TOPLAM_SINIR", len(PDF))
    birinci, ikinci = _kayit(), _kayit(url="https://www.resmigazete.gov.tr/eskiler/2026/10/20261002-2.pdf")
    with _client() as c:
        mail = rapor.rapor_olustur([birinci, ikinci], GUN, c)
    assert len(mail.ekler) == 1
    for govde in (mail.html, mail.metin):
        assert "Kalem 2: https://www.resmigazete.gov.tr/eskiler/2026/10/20261002-2.pdf" in re.sub(r"<[^>]+>", "", govde)
        assert set(re.findall(r"https?://[^\s\"'<)]+", govde)) == {ikinci.url}


def test_ek_kapali_ise_ek_yok():
    with _client() as c:
        mail = rapor.rapor_olustur([_kayit()], GUN, c, ek_ekle=False)
    assert mail.ekler == []


def test_icerigi_beklenen_kayit_rapora_girmez(session):
    session.add(_kayit(icerik_durumu="BEKLIYOR"))
    session.commit()
    assert rapor.bekleyen_kayitlar(session) == []


def test_mime_mesaji():
    mail = Mail("Konu", "<p>html</p>", "metin", ["a@b.com"], [Ek("belge.pdf", b"%PDF-1.4", "application/pdf")])
    msg = message_from_bytes(bytes(mesaj_olustur(mail, "gonderen@x.com")))
    turler = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in turler and "text/html" in turler and "application/pdf" in turler
    assert msg["Subject"] == "Konu"


def test_migration_mevcut_veriyi_korur(tmp_path):
    """0001 şemasında veri varken 0002'ye geçiş veriyi bozmamalı (sunucudaki gerçek senaryo)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'eski.db'}")
    url = engine.url.render_as_string(hide_password=False)
    command.upgrade(alembic_config(url), "0001")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO calismalar (id, baslangic, durum, ozet) VALUES (1, '2026-10-01', 'BASARILI', '{}')"))
        conn.execute(text(
            "INSERT INTO kayitlar (kaynak, dis_id, yayin_tarihi, baslik, tur, bolum, mukerrer, url, kaynakca, ilgili,"
            " eslesmeler, is_kollari, icerik_durumu, ilk_gorulme, calisma_id) VALUES ('rg', 'u1', '2026-10-01',"
            " 'Başlık', '', '', 0, 'u1', 'k', 1, '{}', '[]', 'TAMAM', '2026-10-01', 1)"
        ))
    migrate(engine)
    with engine.connect() as conn:
        satir = conn.execute(text("SELECT baslik, rapor_id FROM kayitlar")).one()
    assert satir == ("Başlık", None)


def test_one_cikanlar_satirlari_birlestirir():
    # OCR satırları cümleyi ortadan böler; öne çıkan bölüm yarım cümle olmamalı.
    metin = ("MADDE 1- (1) 4760 sayılı Kanuna ekli listede yer alan kurşunsuz\nbenzin 95 oktan türü malların\n"
             "tutarları tespit edilmiştir.\nMADDE 2- Bu Karar yayımı tarihinde yürürlüğe girer.")
    cumleler = rapor.one_cikanlar(metin, {"Araç": ["benzin (içerikte)"]})
    assert cumleler == ["MADDE 1- (1) 4760 sayılı Kanuna ekli listede yer alan kurşunsuz benzin 95 oktan türü "
                        "malların tutarları tespit edilmiştir."]


def test_windows_satir_sonlari_temizlenir():
    k = _kayit(icerik="satır bir\r\n\r\nsatır iki\r\n\r\n\r\n\r\nözel tüketim vergisi")
    kalem = rapor._kalem(1, k)
    assert "\r" not in kalem.metin and "\n\n\n" not in kalem.metin


def test_kisaltmada_cumle_bolunmez():
    metin = "MADDE 1- 2710.12.45.00.11 G.T.İ.P. numarası ile yer alan kurşunsuz benzin tutarları tespit edilmiştir."
    assert rapor.one_cikanlar(metin, {"Araç": ["benzin"]}) == [metin]


# ---- onay akışı ------------------------------------------------------------------------------

def _uc_kayit(session) -> list[Kayit]:
    kayitlar = [
        _kayit(),
        _kayit(dis_id="x2", url="https://x/2.htm", baslik="Kıymetli Maden Tebliği", icerik_durumu="TAMAM",
               is_kollari=["Kuyum"]),
        _kayit(dis_id="x3", url="https://x/3.htm", baslik="Alakasız Yönetmelik", icerik_durumu="TAMAM"),
    ]
    session.add_all(kayitlar + [_kayit(dis_id="ilgisiz", ilgili=False, icerik_durumu="GEREKSIZ")])
    session.add(Kullanici(id=7, eposta="sorumlu@firma.com", ad="Sorumlu", parola_hash="x", rol="onaylayici",
                          aktif=True, basarisiz_giris=0, olusturuldu=datetime(2026, 10, 1)))
    _grup(session, "Herkes", ["Kuyum", "Ortak", "Oto kiralama"], ["herkes@firma.com"])
    session.commit()
    return kayitlar


def _grup(session, ad: str, is_kollari: list[str], adresler: list[str], aktif: bool = True) -> AliciGrubu:
    g = AliciGrubu(ad=ad, is_kollari=is_kollari, adresler=adresler, aktif=aktif, guncellendi=datetime(2026, 10, 1))
    session.add(g)
    session.flush()
    return g


def test_onaya_sun_linksiz_bildirim_gonderir(session):
    _uc_kayit(session)
    posta = SahteGonderici()
    r = rapor.onaya_sun(session, posta, ["sorumlu@firma.com"], bugun=GUN)

    assert r.durum == "ONAY_BEKLIYOR" and r.kayit_sayisi == 3
    assert len(rapor.rapor_kayitlari(session, r)) == 3
    bildirim = posta.giden[0]
    assert bildirim.alicilar == ["sorumlu@firma.com"] and bildirim.konu.startswith("Onay bekliyor:")
    assert "onay paneline" in bildirim.metin
    for govde in (bildirim.html, bildirim.metin):
        assert not re.search(r"https?://|www\.|href=", govde, re.IGNORECASE)
    assert rapor.onaya_sun(session, posta, ["sorumlu@firma.com"], bugun=GUN) is None  # kayıtlar zaten raporda


def test_onay_bildirimi_tam_icerik_ve_tek_link_panel(session):
    """Onaylayıcı ne onayladığını mailden de görür (dağıtım maili gibi); tek link panelin kendi adresi."""
    _uc_kayit(session)
    posta = SahteGonderici()
    r = rapor.onaya_sun(session, posta, ["sorumlu@firma.com"], bugun=GUN, panel_adresi="https://mevzuat.firma.com.tr/")
    bildirim = posta.giden[0]
    link = f"https://mevzuat.firma.com.tr/?rapor={r.id}"
    for govde in (bildirim.html, bildirim.metin):
        assert f"Rapor #{r.id}" in govde and "Kıymetli Maden Tebliği" in govde and "Kaynakça" in govde
        assert set(re.findall(r"https?://[^\s\"'<)]+", govde)) == {link}  # içerikteki linkler yine kaldırılır
    assert f'href="{link}"' in bildirim.html and "Onay paneline git" in bildirim.html
    assert bildirim.ekler == [] and bildirim.konu == f"Onay bekliyor: {r.konu}"

    dagitim = rapor.rapor_olustur(rapor.rapor_kayitlari(session, r), GUN, None, ek_ekle=False)
    assert "Onayınızı bekliyor" not in dagitim.html and "ONAYINIZI BEKLİYOR" not in dagitim.metin


def test_onay_bildirimi_ve_dagitim_konu_aciklamasini_veritabanindan_alir(session):
    _uc_kayit(session)
    session.add(KonuTanimi(ad="Vergi", is_kollari=["Ortak"], kelimeler=["vergi"], aciklama="ÖTV fiyatları etkiler.",
                           guncelleme=datetime(2026, 10, 1)))
    session.commit()
    posta = SahteGonderici()
    r = rapor.onaya_sun(session, posta, ["sorumlu@firma.com"], bugun=GUN)
    assert "Neden önemli" in posta.giden[0].metin and "ÖTV fiyatları etkiler." in posta.giden[0].metin
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k.id for k in rapor.rapor_kayitlari(session, r)}, notu=None)
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert len(posta.giden) == 2 and "ÖTV fiyatları etkiler." in posta.giden[1].metin


def test_onay_bildirimi_belgeleri_ekler(session):
    _uc_kayit(session)
    posta = SahteGonderici()
    with _client() as c:
        rapor.onaya_sun(session, posta, ["sorumlu@firma.com"], bugun=GUN, client=c)
    bildirim = posta.giden[0]
    assert bildirim.ekler and all(e.icerik == PDF for e in bildirim.ekler)
    assert "ekteki PDF belgelerde" in bildirim.html and "ekteki PDF belgelerde" in bildirim.metin

    r = rapor.onay_bekleyenler(session)[0]
    with _client() as c:  # MEVZUAT_EK_EKLE=0
        assert rapor.onay_bildirimi(r, rapor.rapor_kayitlari(session, r), ["s@firma.com"], client=c, ek_ekle=False).ekler == []


def test_onay_cikarilan_kalem_dagitilmaz(session):
    kayitlar = _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["sorumlu@firma.com"], bugun=GUN)
    dahil = {kayitlar[0].id, kayitlar[1].id}  # "Alakasız Yönetmelik" çıkarıldı

    rapor.karar_ver(session, r, 7, onay=True, dahil_idler=dahil, notu="Vergi kalemine dikkat.")
    assert r.durum == "ONAYLANDI" and r.karar_veren_id == 7
    posta = SahteGonderici()
    with _client() as c:
        assert rapor.dagit(session, c, posta, r)

    assert r.durum == "GONDERILDI" and r.gonderildi is not None
    mail = posta.giden[0]
    assert mail.alicilar == ["herkes@firma.com"]
    assert "2 kalem" in mail.konu
    assert "Kıymetli Maden Tebliği" in mail.html and "Alakasız Yönetmelik" not in mail.html
    assert "Sorumlunun notu:" in mail.html and "Vergi kalemine dikkat." in mail.html
    assert kayitlar[2].haric and not kayitlar[0].haric


def test_ret_not_zorunlu_ve_dagitilmaz(session):
    kayitlar = _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["sorumlu@firma.com"], bugun=GUN)
    with pytest.raises(ValueError, match="sebep"):
        rapor.karar_ver(session, r, 7, onay=False, dahil_idler=set(), notu="  ")
    rapor.karar_ver(session, r, 7, onay=False, dahil_idler=set(), notu="Hepsi ilgisiz.")
    assert r.durum == "REDDEDILDI" and r.karar_notu == "Hepsi ilgisiz."
    with pytest.raises(rapor.DurumHatasi):
        rapor.dagit(session, None, SahteGonderici(), r)
    assert not any(k.haric for k in kayitlar)


def test_hic_kalem_secmeden_onay_olmaz(session):
    _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["sorumlu@firma.com"], bugun=GUN)
    with pytest.raises(ValueError, match="En az bir kalem"):
        rapor.karar_ver(session, r, 7, onay=True, dahil_idler={99999}, notu=None)
    assert r.durum == "ONAY_BEKLIYOR"


def test_ayni_rapora_iki_kez_karar_verilemez(session):
    kayitlar = _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["sorumlu@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={kayitlar[0].id}, notu=None)
    with pytest.raises(rapor.DurumHatasi):
        rapor.karar_ver(session, r, 7, onay=False, dahil_idler=set(), notu="geç kalan ret")
    assert r.durum == "ONAYLANDI"


def test_dagitim_basarisizsa_onaylandi_kalir_sonra_tekrar_denenir(session):
    kayitlar = _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["sorumlu@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k.id for k in kayitlar}, notu=None)

    assert not rapor.dagit(session, None, SahteGonderici(OSError("SMTP kapalı")), r)
    assert r.durum == "ONAYLANDI" and "SMTP kapalı" in r.hata
    assert rapor.dagitim_bekleyenler(session) == [r]

    assert rapor.dagit(session, None, SahteGonderici(), r, ek_ekle=False)
    assert r.durum == "GONDERILDI" and r.hata is None
    assert rapor.dagitim_bekleyenler(session) == []


def test_onaylayici_yoksa_rapor_yine_olusur(session):
    _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), [], bugun=GUN)
    assert r.durum == "ONAY_BEKLIYOR" and "Onaylayıcı" in r.hata


def test_dosya_gonderici_ayni_saniyede_ezmez(tmp_path):
    from mevzuat.mail import DosyaGonderici

    g = DosyaGonderici(tmp_path)
    for i in range(3):
        g.gonder(Mail(f"Konu {i}", "<p>x</p>", "x", ["a@b.com"]))
    assert len(list(tmp_path.glob("*.eml"))) == 3


# ---- rapor içeriği: özet, tablo, yürürlük, tür, kaynakça --------------------------------------

OTV_OCR = """1 Ekim 2026 PERŞEMBE Resmi Gazete Sayı : 33387
CUMHURBAŞKANI KARARI
Karar Sayısı: 11822
Bazı mallara uygulanan özel tüketim vergisi tutarlarının yeniden belirlenmesine dair
ekli Kararın yürürlüğe konulmasına, 4760 sayılı Özel Tüketim Vergisi Kanununun 12 nci
maddesi gereğince karar verilmiştir.
KARAR
MADDE 1- (1) 6/6/2002 tarihli ve 4760 sayılı Özel Tüketim Vergisi Kanununa ekli
(I) sayılı listenin (A) cetvelinde yer alan kurşunsuz benzin 95 oktan türü malların özel tüketim vergisi
tutarları, aşağıdaki tabloda gösterildiği şekilde tespit edilmiştir.
31/10/2026 tarihine kadar | 1/11/2026-30/11/2026 | 1/12/2026 ve sonrası
7,9000 TL/Litre 11,3600 TL/Litre 14,8277 TL/Litre
MADDE 2- (1) 4760 sayılı Kanuna ekli (I) sayılı listenin (A) cetvelinde yer alan kurşunsuz
benzin 98 oktan türü malların özel tüketim vergisi tutarları tespit edilmiştir.
MADDE 3- (1) Bu Karar yayımı tarihinde yürürlüğe girer.
MADDE 4- (1) Bu Karar hükümlerini Hazine ve Maliye Bakanı yürütür."""


def test_ozet_madde_1_ve_tablosu():
    assert rapor.ozet_cumlesi(OTV_OCR, "Bazı Mallara Uygulanan ÖTV …").startswith("6/6/2002 tarihli")
    assert rapor.ozet_tablosu(OTV_OCR) == [
        "31/10/2026 tarihine kadar | 1/11/2026-30/11/2026 | 1/12/2026 ve sonrası",
        "7,9000 TL/Litre 11,3600 TL/Litre 14,8277 TL/Litre",
    ]


def test_tablo_yoksa_bos_ve_madde_yoksa_ilk_anlamli_cumle():
    duyuru = "BMGK Tarafından Güncelleme Yapılmıştır. Dışişleri Bakanlığından alınan yazı ekinde belirtildiği üzere listedeki bir organizasyonun bilgileri güncellenmiştir."
    assert rapor.ozet_tablosu(duyuru) == []
    assert rapor.ozet_cumlesi(duyuru, "BMGK Tarafından Güncelleme Yapılmıştır").startswith("Dışişleri Bakanlığından")


@pytest.mark.parametrize("metin, beklenen", [
    ("MADDE 3- (1) Bu Karar yayımı tarihinde yürürlüğe girer.", "Yayım tarihinde (01.10.2026)"),
    ("MADDE 5- Bu Yönetmelik yayımını izleyen günde yürürlüğe girer.", "Yayımını izleyen gün (02.10.2026)"),
    ("MADDE 9- Bu Tebliğ 1/1/2027 tarihinde yürürlüğe girer.", "01.01.2027"),
    ("Yok bir şey.", None),
])
def test_yururluk(metin, beklenen):
    assert rapor.yururluk(metin, GUN) == beklenen


@pytest.mark.parametrize("hukum", [
    "Bu Yönetmeliğin; a) 5 inci maddesi 1/1/2027 tarihinde, b) diğer hükümleri yayımı tarihinde yürürlüğe girer.",
    # ";" ile ayrılmış kademeli hüküm: a) kısmı kaybolmamalı
    "Bu Yönetmeliğin; a) 5 inci maddesi 1/1/2027 tarihinde; b) diğer hükümleri yayımı tarihinde yürürlüğe girer.",
])
def test_kademeli_yururluk_tek_tarihe_indirgenmez(hukum):
    metin = f"MADDE 11- Geçiş hükmü. MADDE 12- (1) {hukum} MADDE 13- Bu Yönetmelik hükümlerini Bakan yürütür."
    sonuc = rapor.yururluk(metin, GUN)
    assert sonuc == hukum
    assert "1/1/2027" in sonuc and "yayımı tarihinde" in sonuc


def test_tur_adi_ve_kisa_kaynakca():
    baslik = "Bazı Mallara Uygulanan Özel Tüketim Vergisi Tutarlarının Yeniden Belirlenmesi Hakkında Karar (Karar Sayısı: 11822)"
    k = _kayit(baslik=baslik, kaynakca=f"Resmî Gazete, 01.10.2026, Sayı: 33387, CUMHURBAŞKANI KARARLARI: {baslik}")
    assert rapor.tur_adi(k) == "Cumhurbaşkanı Kararı"
    assert rapor.kisa_kaynakca(k) == "Resmî Gazete, 01.10.2026, Sayı: 33387 — Cumhurbaşkanı Kararı (Karar Sayısı: 11822)"
    assert rapor.tur_adi(_kayit(tur="HÂKİMLER VE SAVCILAR KURULU KARARI")) == "Hâkimler Ve Savcılar Kurulu Kararı"
    masak = _kayit(kaynak="masak", tur="Duyuru", baslik="FATF Raporu", kaynakca="MASAK Duyurusu, 23.09.2026: FATF Raporu")
    assert rapor.tur_adi(masak) == "Duyuru"
    assert rapor.kisa_kaynakca(masak) == "MASAK Duyurusu, 23.09.2026"


def test_one_cikanlarda_kalip_ve_ozet_tekrari_yok():
    k = _kayit(icerik=OTV_OCR, eslesmeler={"Vergi": ["özel tüketim vergisi"], "Araç": ["benzin (içerikte)"]})
    kalem = rapor._kalem(1, k)
    assert all("karar verilmiştir" not in c for c in kalem.one_cikanlar)
    assert all("95 oktan" not in c for c in kalem.one_cikanlar)  # özetle aynı cümle
    assert any("98 oktan" in c for c in kalem.one_cikanlar)
    assert kalem.yururluk == "Yayım tarihinde (01.10.2026)"


def test_amac_maddesi_noktali_virgulle_baslasa_da_ozetlenir():
    # Gerçek tebliğden (1416 sayılı Kanun burs tebliği): ";" cümle bölücüsüyle özet boş çıkıyordu.
    metin = ("Amaç\nMADDE 1-\n(1) Bu Tebliğin amacı; yükseköğretim kurumlarının öğretim elemanı ihtiyacını "
             "karşılamak üzere yurt dışında öğrenim görenlere yapılacak ödemeleri belirlemektir.\nKapsam\nMADDE 2- (1) …")
    assert rapor.ozet_cumlesi(metin, "1416 Sayılı Kanun … Tebliğ").startswith("Bu Tebliğin amacı; yükseköğretim")


def test_yururlukte_sonraki_madde_basligi_yapismaz():
    metin = ("Yürürlük\nMADDE 5- (1) Bu Yönetmelik 2026-2027 eğitim-öğretim yılı başında yürürlüğe girer.\n"
             "Yürütme\nMADDE 6- (1) Bu Yönetmelik hükümlerini Rektör yürütür.")
    assert rapor.yururluk(metin, GUN) == "Bu Yönetmelik 2026-2027 eğitim-öğretim yılı başında yürürlüğe girer."


def test_kisaltma_yuklemi_korur():
    cumle = ("Birleşmiş Milletler Güvenlik Konseyinin 1267 (1999), 1988 (2011), 1989 (2011) ve 2253 (2015) sayılı "
             "kararlarıyla listelenen kişi, kuruluş veya organizasyonların tasarrufunda bulunan malvarlığının "
             "dondurulmasına ilişkin 30/9/2013 tarihli ve 2013/5428 sayılı Bakanlar Kurulu Kararının eki (1) sayılı "
             "listenin “B-DEAŞ ve El Kaide ile Bağlantılı Tüzel Kişi, Kuruluş veya Organizasyonlar” başlıklı "
             "bölümünde yer alan bir organizasyona ilişkin bilgiler ekli listede gösterildiği şekilde güncellenmiştir.")
    kisa = rapor._kisalt(cumle, 350)
    assert len(kisa) <= 360
    assert kisa.startswith("Birleşmiş Milletler") and kisa.endswith("güncellenmiştir.")
    assert " … " in kisa


# ---- dağıtım en fazla bir kez -----------------------------------------------------------------

def _onayli_rapor(session) -> Rapor:
    kayitlar = _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["sorumlu@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k.id for k in kayitlar}, notu=None)
    return r


def test_mail_gitti_ama_durum_yazilamadi_otomatik_tekrar_yok(session, monkeypatch):
    """Notta belirtilen senaryo: mail gider, ardından DB'ye GONDERILDI yazılamaz."""
    r = _onayli_rapor(session)
    posta = SahteGonderici()
    gercek_commit = session.commit
    gonderildi_mi = {"evet": False}

    def gonderimden_sonra_cok():
        if gonderildi_mi["evet"]:
            raise OSError("DB bağlantısı koptu")
        gercek_commit()

    class IzleyenPosta(SahteGonderici):
        def gonder(self, mail):
            super().gonder(mail)
            gonderildi_mi["evet"] = True

    posta = IzleyenPosta()
    monkeypatch.setattr(session, "commit", gonderimden_sonra_cok)
    with pytest.raises(OSError):
        rapor.dagit(session, None, posta, r, ek_ekle=False)
    monkeypatch.setattr(session, "commit", gercek_commit)
    session.rollback()

    session.refresh(r)
    (g,) = rapor.gonderimler(session, r)
    assert g.durum == "GONDERILIYOR" and r.durum == "ONAYLANDI" and len(posta.giden) == 1
    assert rapor.belirsiz_gonderimler(session, esik=timedelta(0)) == [g]  # yöneticiye sorulur
    # Günlük işin tekrar deneme adımı bu maili TEKRAR GÖNDERMEZ.
    assert not rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert len(posta.giden) == 1 and r.hata is None


def test_ayni_anda_iki_dagitimdan_sadece_biri_gonderir(session):
    r = _onayli_rapor(session)
    engine = session.get_bind()
    posta = SahteGonderici()
    with Session(engine) as ikinci:
        r2 = ikinci.get(Rapor, r.id)  # günlük işin gördüğü kopya, hâlâ ONAYLANDI
        eski_gonderim = rapor.gonderimler(ikinci, r2)[0]  # o da BEKLIYOR görüyor
        assert rapor.dagit(session, None, posta, r, ek_ekle=False)
        # Eski kopyayla sahiplenmeye çalışan ikinci süreç: koşullu güncelleme tutmaz, mail gitmez.
        sonuc = rapor._gonderimi_yap(ikinci, None, posta, r2, eski_gonderim,
                                     {k.id: k for k in rapor.rapor_kayitlari(ikinci, r2)}, False, {}, {})
        assert sonuc is None
        with pytest.raises(rapor.DurumHatasi):  # artık güncel durumu (GONDERILDI) görüyor
            rapor.dagit(ikinci, None, posta, r2, ek_ekle=False)
    assert len(posta.giden) == 1


def test_smtp_hatasinda_geri_alinir_tekrar_denenebilir(session):
    r = _onayli_rapor(session)
    assert not rapor.dagit(session, None, SahteGonderici(OSError("bağlanamadı")), r, ek_ekle=False)
    assert r.durum == "ONAYLANDI" and rapor.dagitim_bekleyenler(session) == [r]


def test_message_id_rapora_ozel_ve_sabit(session):
    r = _onayli_rapor(session)
    posta = SahteGonderici()
    rapor.dagit(session, None, posta, r, ek_ekle=False)
    (g,) = rapor.gonderimler(session, r)
    assert posta.giden[0].message_id == f"<mevzuat-rapor-{r.id}-{g.id}@mevzuat-takip>"
    msg = message_from_bytes(bytes(mesaj_olustur(posta.giden[0], "g@x.com")))
    assert msg["Message-ID"] == f"<mevzuat-rapor-{r.id}-{g.id}@mevzuat-takip>"


def test_smtp_quit_hatasi_gonderilmis_maili_tekrar_gondertmez(monkeypatch):
    import smtplib

    from mevzuat.mail import SmtpGonderici

    gonderilen = []

    class SahteSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): gonderilen.append(msg)
        def quit(self): raise smtplib.SMTPServerDisconnected("QUIT sırasında koptu")
        def close(self): pass

    monkeypatch.setattr(smtplib, "SMTP", SahteSMTP)
    SmtpGonderici("h", 587, "k", "s", "g@x.com").gonder(Mail("K", "<p>x</p>", "x", ["a@b.com"]))
    assert len(gonderilen) == 1


# ---- alıcı grupları ------------------------------------------------------------------------------

from mevzuat import alicilar  # noqa: E402


def _is_kollu_kayitlar(session) -> list[Kayit]:
    """k1 Kuyum, k2 Döviz/Altın, k3 iş kolu yok (= Ortak)."""
    kayitlar = [
        _kayit(dis_id="k1", url="https://x/1.htm", baslik="Kuyum Tebliği", icerik_durumu="TAMAM", is_kollari=["Kuyum"]),
        _kayit(dis_id="k2", url="https://x/2.htm", baslik="Döviz Tebliği", icerik_durumu="TAMAM", is_kollari=["Döviz/Altın"]),
        _kayit(dis_id="k3", url="https://x/3.htm", baslik="MASAK Duyurusu", icerik_durumu="TAMAM", is_kollari=[]),
    ]
    session.add_all(kayitlar)
    session.commit()
    return kayitlar


def test_plan_grup_sadece_kendi_is_kolunu_ve_cok_gruplu_kisi_tek_mail(session):
    k1, k2, k3 = _is_kollu_kayitlar(session)
    kuyum = _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com", "b@firma.com"])
    doviz = _grup(session, "Döviz", ["Döviz/Altın"], ["b@firma.com", "c@firma.com"])

    plan = alicilar.dagitim_plani([kuyum, doviz], [k1, k2, k3])

    kime = {a: m for m in plan for a in m.alicilar}
    assert kime["a@firma.com"].kayit_idler == [k1.id]
    assert kime["b@firma.com"].kayit_idler == [k1.id, k2.id]  # iki grupta: birleşim, tek mail
    assert kime["b@firma.com"].gruplar == ["Döviz", "Kuyum"]
    assert kime["c@firma.com"].kayit_idler == [k2.id]
    assert len(plan) == 3
    assert not any(k3.id in m.kayit_idler for m in plan)  # Ortak kendiliğinden kimseye gitmez


def test_plan_ortak_secen_grup_ortak_kalemleri_alir_herkese_ayri_mail(session):
    k1, k2, k3 = _is_kollu_kayitlar(session)
    yonetim = _grup(session, "Yönetim", ["Kuyum", "Döviz/Altın", "Ortak"], ["mudur@firma.com", "ortak@firma.com"])
    uyum = _grup(session, "Uyum", ["Ortak"], ["uyum@firma.com"])

    plan = alicilar.dagitim_plani([yonetim, uyum], [k1, k2, k3])

    assert [(m.alicilar, m.kayit_idler) for m in plan] == [
        (["mudur@firma.com"], [k1.id, k2.id, k3.id]),  # aynı kalemler de olsa kişi başı ayrı mail
        (["ortak@firma.com"], [k1.id, k2.id, k3.id]),
        (["uyum@firma.com"], [k3.id]),
    ]


def test_hic_gruba_gitmeyecek_rapor_onaylanamaz(session):
    k1, *_ = _is_kollu_kayitlar(session)
    _grup(session, "Döviz", ["Döviz/Altın"], ["c@firma.com"])
    _grup(session, "Pasif Kuyum", ["Kuyum"], ["a@firma.com"], aktif=False)
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    with pytest.raises(ValueError, match="hiçbir alıcı grubuna gitmiyor"):
        rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None)
    assert r.durum == "ONAY_BEKLIYOR" and rapor.gonderimler(session, r) == []


def test_gruplu_dagitim_her_adres_sadece_kendi_kalemlerini_alir(session):
    k1, k2, k3 = _is_kollu_kayitlar(session)
    _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com"])
    _grup(session, "Döviz", ["Döviz/Altın", "Ortak"], ["c@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id, k2.id, k3.id}, notu="Not herkese.")
    posta = SahteGonderici()
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)

    mailler = {tuple(m.alicilar): m for m in posta.giden}
    assert set(mailler) == {("a@firma.com",), ("c@firma.com",)}
    a, c = mailler[("a@firma.com",)], mailler[("c@firma.com",)]
    assert "Kuyum Tebliği" in a.html and "Döviz Tebliği" not in a.html and "MASAK" not in a.html
    assert "Döviz Tebliği" in c.html and "MASAK Duyurusu" in c.html and "Kuyum Tebliği" not in c.html
    assert "1 kalem" in a.konu and "2 kalem" in c.konu
    assert "Not herkese." in a.html and "Not herkese." in c.html
    assert r.durum == "GONDERILDI" and r.alicilar == ["a@firma.com", "c@firma.com"]


def test_bir_mail_gidemezse_digeri_gider_sonra_sadece_gitmeyen_denenir(session):
    k1, k2, _ = _is_kollu_kayitlar(session)
    _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com"])
    _grup(session, "Döviz", ["Döviz/Altın"], ["c@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id, k2.id}, notu=None)

    class SeciciPosta(SahteGonderici):
        def gonder(self, mail):
            if "c@firma.com" in mail.alicilar and self.hata:
                raise self.hata
            self.giden.append(mail)

    posta = SeciciPosta(OSError("alıcı sunucusu reddetti"))
    assert not rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert [m.alicilar for m in posta.giden] == [["a@firma.com"]]
    assert r.durum == "ONAYLANDI" and "c@firma.com" in r.hata and rapor.dagitim_bekleyenler(session) == [r]
    durumlar = {tuple(g.alicilar): g.durum for g in rapor.gonderimler(session, r)}
    assert durumlar == {("a@firma.com",): "GONDERILDI", ("c@firma.com",): "BEKLIYOR"}

    posta.hata = None  # sunucu düzeldi; sonraki çalışma
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert [m.alicilar for m in posta.giden] == [["a@firma.com"], ["c@firma.com"]]  # a'ya ikinci kez gitmedi
    assert r.durum == "GONDERILDI" and r.hata is None


def test_onaydan_sonra_grup_degisse_de_onaydaki_plan_gider(session):
    k1, *_ = _is_kollu_kayitlar(session)
    kuyum = _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None)
    kuyum.adresler = ["yeni@firma.com"]
    session.commit()
    posta = SahteGonderici()
    rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert posta.giden[0].alicilar == ["a@firma.com"]


def test_eski_surumde_onaylanmis_plansiz_rapor_dagitimda_planlanir(session):
    kayitlar = _uc_kayit(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    r.durum = "ONAYLANDI"  # alıcı grupları gelmeden onaylanmış: gönderim satırı yok
    kayitlar[2].haric = True
    session.commit()
    posta = SahteGonderici()
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert posta.giden[0].alicilar == ["herkes@firma.com"] and "2 kalem" in posta.giden[0].konu


def test_ayni_pdf_birden_cok_maile_tek_indirmeyle_eklenir(session):
    pdf_kayit = _kayit(is_kollari=["Ortak"])  # PDF'li kalem
    kuyum = _kayit(dis_id="k1", url="https://x/1.htm", baslik="Kuyum", icerik_durumu="TAMAM", is_kollari=["Kuyum"])
    session.add_all([pdf_kayit, kuyum])
    _grup(session, "A", ["Ortak"], ["a@firma.com"])
    _grup(session, "B", ["Ortak", "Kuyum"], ["b@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={pdf_kayit.id, kuyum.id}, notu=None)

    istekler = []

    def handler(request):
        istekler.append(str(request.url))
        return httpx.Response(200, content=PDF)

    posta = SahteGonderici()
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert rapor.dagit(session, c, posta, r)
    assert len(posta.giden) == 2 and all(m.ekler for m in posta.giden)
    assert len(istekler) == 1


def test_adresleri_ayikla():
    gecerli, hatali = alicilar.adresleri_ayikla("A@Firma.com\n b@firma.com, a@firma.com ; yanlis-adres\n\nc@x")
    assert gecerli == ["a@firma.com", "b@firma.com"]
    assert hatali == ["yanlis-adres", "c@x"]


def test_grup_kaydet_dogrulama(session):
    with pytest.raises(ValueError, match="iş kolu"):
        alicilar.grup_kaydet(session, None, "Kuyum", [], "a@firma.com", True)
    with pytest.raises(ValueError, match="Geçersiz e-posta"):
        alicilar.grup_kaydet(session, None, "Kuyum", ["Kuyum"], "a@firma.com\nyanlis", True)
    with pytest.raises(ValueError, match="en az bir e-posta"):
        alicilar.grup_kaydet(session, None, "Kuyum", ["Kuyum"], "  ", True)
    g = alicilar.grup_kaydet(session, None, "  Kuyum   Ekibi ", ["Kuyum"], "a@firma.com", True)
    assert g.ad == "Kuyum Ekibi"
    with pytest.raises(ValueError, match="zaten var"):
        alicilar.grup_kaydet(session, None, "kuyum ekibi", ["Ortak"], "b@firma.com", True)
    # Kendi adıyla güncellemek serbest; pasif grup adressiz olabilir.
    alicilar.grup_kaydet(session, g, "Kuyum Ekibi", ["Kuyum", "Ortak"], "", False)
    assert g.is_kollari == ["Kuyum", "Ortak"] and not g.aktif


def test_icerigi_alinamayan_kalem_iki_mail_biciminde_de_aciklanir():
    """Savunma hazırlığında bulundu: düz metin mailde HATA kalemi açıklamasız görünüyordu."""
    k = _kayit(icerik=None, icerik_durumu="HATA", url="https://x/1.htm")
    mail = rapor.rapor_olustur([k], GUN, None, ek_ekle=False)
    for govde in (mail.html, mail.metin):
        assert "Belgenin içeriği alınamadı" in govde


# ---- onay ekranında alıcı seçimi -----------------------------------------------------------------

def test_plan_kisiye_ozel_adres_butun_kalemleri_alir_grupla_birlesir(session):
    k1, k2, k3 = _is_kollu_kayitlar(session)
    kuyum = _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com", "mudur@firma.com"])

    plan = alicilar.dagitim_plani([kuyum], [k1, k2, k3], ["mudur@firma.com", "avukat@firma.com"])

    kime = {a: m for m in plan for a in m.alicilar}
    assert kime["a@firma.com"].kayit_idler == [k1.id]
    # mudur hem grupta hem kişiye özel: tek mail, bütün kalemler
    assert kime["mudur@firma.com"].kayit_idler == [k1.id, k2.id, k3.id]
    assert kime["avukat@firma.com"].kayit_idler == [k1.id, k2.id, k3.id]
    assert kime["mudur@firma.com"] is not kime["avukat@firma.com"]  # dış adres çalışan adresini görmesin
    assert kime["avukat@firma.com"].gruplar == [alicilar.KISIYE_OZEL]
    assert kime["mudur@firma.com"].gruplar == [alicilar.KISIYE_OZEL, "Kuyum"]


def test_onaylayicinin_isaretini_kaldirdigi_grup_mail_almaz_ek_adres_alir(session):
    k1, k2, _ = _is_kollu_kayitlar(session)
    kuyum = _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com"])
    _grup(session, "Döviz", ["Döviz/Altın"], ["c@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id, k2.id}, notu=None,
                    grup_idler={kuyum.id}, ek_adresler=["Avukat@Firma.com"])
    posta = SahteGonderici()
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)

    kime = {tuple(m.alicilar): m for m in posta.giden}
    assert set(kime) == {("a@firma.com",), ("avukat@firma.com",)}  # c@ (Döviz) seçilmedi
    assert "Döviz Tebliği" in kime[("avukat@firma.com",)].html and "Kuyum Tebliği" in kime[("avukat@firma.com",)].html


def test_secimde_hatali_adres_ve_pasif_grup_reddedilir(session):
    k1, *_ = _is_kollu_kayitlar(session)
    pasif = _grup(session, "Eski", ["Kuyum"], ["e@firma.com"], aktif=False)
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    with pytest.raises(ValueError, match="Geçersiz e-posta adresi: yanlis"):
        rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None, grup_idler=set(), ek_adresler=["yanlis"])
    with pytest.raises(ValueError, match="pasif"):
        rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None, grup_idler={pasif.id})
    with pytest.raises(ValueError, match="hiçbir alıcı grubuna gitmiyor"):
        rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None, grup_idler=set())
    assert r.durum == "ONAY_BEKLIYOR"
    # Grup seçmeden sadece kişiye özel adresle onaylanabilir.
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None, grup_idler=set(), ek_adresler=["x@firma.com"])
    assert r.alicilar == ["x@firma.com"]


# ---- kişi başı ayrı mail -------------------------------------------------------------------------

def test_ayni_kalemleri_alan_iki_kisiye_iki_ayri_mail_to_basliginda_tek_adres(session):
    k1, *_ = _is_kollu_kayitlar(session)
    _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com", "b@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None, ek_adresler=["musavir@disari.com"])
    posta = SahteGonderici()
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)

    assert sorted(m.alicilar[0] for m in posta.giden) == ["a@firma.com", "b@firma.com", "musavir@disari.com"]
    assert len({m.message_id for m in posta.giden}) == 3
    for m in posta.giden:
        msg = message_from_bytes(bytes(mesaj_olustur(m, "g@x.com")))
        assert msg["To"] == m.alicilar[0] and "Bcc" not in msg and "Cc" not in msg
        assert "Kuyum Tebliği" in m.html
    assert [g.alicilar for g in rapor.gonderimler(session, r)] == [["a@firma.com"], ["b@firma.com"], ["musavir@disari.com"]]
    assert r.alicilar == ["a@firma.com", "b@firma.com", "musavir@disari.com"]


def test_ayni_kalemli_mail_bir_kez_hazirlanir(session, monkeypatch):
    k1, *_ = _is_kollu_kayitlar(session)
    _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com", "b@firma.com", "c@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None)
    asil = rapor.rapor_olustur
    cagri = []
    monkeypatch.setattr(rapor, "rapor_olustur", lambda *a, **k: cagri.append(1) or asil(*a, **k))
    posta = SahteGonderici()
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert len(posta.giden) == 3 and len(cagri) == 1
    assert len({id(m) for m in posta.giden}) == 3  # her kişinin Mail nesnesi ayrı (alıcı listesi paylaşılmaz)


def test_ayni_gruptan_bir_kisiye_gidemezse_sadece_o_tekrar_denenir(session):
    k1, *_ = _is_kollu_kayitlar(session)
    _grup(session, "Kuyum", ["Kuyum"], ["a@firma.com", "b@firma.com"])
    session.commit()
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    rapor.karar_ver(session, r, 7, onay=True, dahil_idler={k1.id}, notu=None)

    class SeciciPosta(SahteGonderici):
        def gonder(self, mail):
            if mail.alicilar == ["b@firma.com"] and self.hata:
                raise self.hata
            self.giden.append(mail)

    posta = SeciciPosta(OSError("posta kutusu dolu"))
    assert not rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert [m.alicilar for m in posta.giden] == [["a@firma.com"]]
    posta.hata = None
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert [m.alicilar for m in posta.giden] == [["a@firma.com"], ["b@firma.com"]]


def test_eski_cok_alicili_bekleyen_gonderim_eskisi_gibi_tek_mailde_gider(session):
    k1, *_ = _is_kollu_kayitlar(session)
    r = rapor.onaya_sun(session, SahteGonderici(), ["s@firma.com"], bugun=GUN)
    r.durum = "ONAYLANDI"
    session.add(Gonderim(rapor_id=r.id, alicilar=["a@firma.com", "b@firma.com"], kayit_idler=[k1.id],
                         gruplar=["Kuyum"], durum="BEKLIYOR"))
    session.commit()
    posta = SahteGonderici()
    assert rapor.dagit(session, None, posta, r, ek_ekle=False)
    assert [m.alicilar for m in posta.giden] == [["a@firma.com", "b@firma.com"]]

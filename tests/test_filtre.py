from pathlib import Path

import pytest

from mevzuat.filtre import Konu, eslesmeler, is_kollari, konulari_yukle, tr_kucuk

KONULAR_DOSYASI = Path(__file__).parents[1] / "config" / "konular.toml"


@pytest.fixture(scope="module")
def konular():
    return konulari_yukle(KONULAR_DOSYASI)


def test_turkce_kucuk_harf():
    assert tr_kucuk("İSTANBUL IĞDIR") == "istanbul ığdır"
    assert tr_kucuk("İLÂN  BÖLÜMÜ") == "ilan bölümü"


def test_ekler_eslesir():
    konu = [Konu("K", kelimeler=("altın",))]
    assert eslesmeler("Altının Ayarı Hakkında", konu) == {"K": ["altın"]}
    assert eslesmeler("ALTINLAR", konu)


def test_kelime_ortasinda_eslesmez():
    konu = [Konu("K", kelimeler=("altın",))]
    assert eslesmeler("Malatına", konu) == {}


def test_tomldaki_aciklama_okunur_eslesmeyi_etkilemez(tmp_path):
    yol = tmp_path / "konular.toml"
    yol.write_text('[[konu]]\nad = "K"\nis_kollari = ["Ortak"]\nkelimeler = ["altın"]\n'
                   'aciklama = "Kuyumcuyu doğrudan ilgilendirir.\\nİkinci satır."\n'
                   '[[konu]]\nad = "L"\nis_kollari = ["Ortak"]\nkelimeler = ["döviz"]\n', encoding="utf-8")
    k, l = konulari_yukle(yol)
    assert k.aciklama == "Kuyumcuyu doğrudan ilgilendirir.\nİkinci satır." and l.aciklama == ""
    assert eslesmeler("Kuyumcuyu ilgilendiren altın tebliği", [k]) == {"K": ["altın"]}


def test_haric_ifade_maskelenir():
    konu = [Konu("K", kelimeler=("altın",), haric=("altında",))]
    assert eslesmeler("Kurul gözetimi altında yürütülür", konu) == {}
    assert eslesmeler("Altın piyasası denetim altında", konu) == {"K": ["altın"]}


def test_gercek_basliklar(konular):
    # 1 Ekim 2026 Resmî Gazete'den; başlıklarında "altın/kuyum" geçmeyen ama ilgili kalemler.
    bmgk = (
        "Birleşmiş Milletler Güvenlik Konseyinin … Listelenen Kişi, Kuruluş veya Organizasyonların "
        "Tasarrufunda Bulunan Malvarlığının Dondurulması Hakkındaki … Karar"
    )
    otv = "Bazı Mallara Uygulanan Özel Tüketim Vergisi Tutarlarının Yeniden Belirlenmesi Hakkında Karar"
    assert "MASAK / suç gelirleri / yaptırımlar" in eslesmeler(bmgk, konular)
    assert "Vergi" in eslesmeler(otv, konular)


def test_bilinen_yanlis_pozitifler_yok(konular):
    harcirah = "6245 Sayılı Harcırah Kanununun 46 ncı Maddesinin … Hakkında Karar"
    kamulastirma = "Bazı Taşınmazların Acele Kamulaştırılması Hakkında Karar"
    assert eslesmeler(harcirah, konular) == {}
    assert eslesmeler(kamulastirma, konular) == {}


def test_90_gunluk_testte_bulunan_yanlis_pozitifler(konular):
    for baslik in [
        "Danıştay Altıncı Dairesine Ait Karar",
        "380 kV Altınkaya-Sinop Enerji İletim Hattı Projesi Kapsamında Bazı Taşınmazların … Kamulaştırılması",
        "Afyonkarahisar, Ankara, Gümüşhane, Isparta İllerinde … Kamulaştırılması Hakkında Karar",
        "Aydın Adnan Menderes Üniversitesi İş Sağlığı ve Güvenliği Eğitim, Uygulama ve Araştırma Merkezi Yönetmeliği",
    ]:
        assert eslesmeler(baslik, konular) == {}, baslik


def test_90_gunluk_testte_bulunan_dogru_pozitifler(konular):
    for baslik, konu in [
        ("Kıymetli Maden Standartları ve Rafinerileri Hakkında Tebliğ’de Değişiklik", "Kıymetli madenler ve kuyumculuk"),
        ("Altın Cinsinden Fiziki Varlıkların Finansal Sisteme Kazandırılması Hakkında Tebliğ", "Kıymetli madenler ve kuyumculuk"),
        ("Mali Suçları Araştırma Kurulu Genel Tebliği (Sıra No: 34)", "MASAK / suç gelirleri / yaptırımlar"),
        ("Malvarlığının Dondurulması Kararı (Karar Sayısı: 2026/1)", "MASAK / suç gelirleri / yaptırımlar"),
        ("Vergi Usul Kanunu Genel Tebliği (Sıra No: 538)’nde Değişiklik", "Vergi"),
    ]:
        assert konu in eslesmeler(baslik, konular), baslik


def test_is_kollari(konular):
    altin = eslesmeler("Kıymetli Maden Standartları Hakkında Tebliğ", konular)
    assert is_kollari(altin, konular) == ["Döviz/Altın", "Kuyum"]
    arac = eslesmeler("Motorlu Taşıtlar Vergisi Genel Tebliği", konular)
    assert "Oto kiralama" in is_kollari(arac, konular)
    assert is_kollari({}, konular) == []


def test_tcmb_ic_yonetmelikleri_dovize_dusmez(konular):
    assert eslesmeler("Türkiye Cumhuriyet Merkez Bankası Disiplin Yönetmeliği", konular) == {}
    assert "Döviz ve kambiyo" in eslesmeler(
        "Firmaların Yurt Dışı Kaynaklı Dövizlerinin Türk Lirasına Dönüşümünün Desteklenmesi Hakkında Tebliğ", konular
    )


def test_arac_kiralama_yonetmeligi(konular):
    e = eslesmeler("Motorlu Kara Taşıtlarının Kiralanması Hakkında Yönetmelik", konular)
    assert is_kollari(e, konular) == ["Oto kiralama"]

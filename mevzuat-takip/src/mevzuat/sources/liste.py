"""Duyuru listesi sayfası olan kaynakların ortak kısmı, CSS seçicileriyle okunur.

Sayfanın nasıl indirildiği tipe göre değişir. `html` tipinde düz HTML httpx ile, `tarayici` tipinde JavaScript'le
oluşan sayfa Chromium ile indirilir. Liste ayrıştırma, tarih okuma, tarama ve içerik çıkarma ikisinde aynıdır.

Sadece listenin ilk sayfası okunur. Günlük çalışmada bu yeterli, ama iki çalışma arasında bir sayfadan
fazla duyuru çıkarsa eskiler kaçabilir. Bu durumda çalışma özetine uyarı yazılır.
"""
# Kısaca, bir duyuru listesi sayfasının HTML'ini alıp içinden "başlık, link, tarih" üçlülerini çıkaran ortak kod.
# CSS seçici, HTML içinde bir parçayı tarif eden adres demek. Örneğin "div.duyuru a" duyuru kutusundaki linki gösterir.

# logging, ekrana/loga uyarı yazmak için.
import logging
# re, düzenli ifadeler (regex). Metin içinde "02.10.2026" gibi kalıpları bulmak için.
import re
from collections.abc import Iterator
from dataclasses import dataclass
# timedelta, "1 gün", "7 gün" gibi süreler.
from datetime import date, datetime, timedelta
# urljoin, "/duyuru/5" gibi yarım linki sitenin adresiyle birleştirip tam adres yapar. urlparse, adresi parçalarına ayırır.
from urllib.parse import urljoin, urlparse

import httpx
# selectolax, HTML'i hızlıca okuyup CSS seçiciyle içinden parça bulmamızı sağlayan kütüphane.
from selectolax.parser import HTMLParser

# tr_kucuk Türkçe'ye uygun küçük harfe çevirir, büyük İ küçük i olur, büyük I küçük ı olur.
from mevzuat.filtre import tr_kucuk
# belge_icerigi, PDF ya da HTML baytlarını metne çevirir. html_metni, HTML'i düz metne çevirir.
from mevzuat.icerik import Icerik, belge_icerigi, html_metni
from mevzuat.sources.base import KayitTaslagi, TaramaAdimi

# Bu dosyaya ait log yazıcısı. Loglarda "mevzuat.sources.liste" adıyla görünür.
log = logging.getLogger(__name__)

# Ay adından ay numarasına giden sözlük, ocak 1, şubat 2 diye gider. enumerate sırayla numara verir, start=1 sayesinde 1'den başlar.
AYLAR = {ay: i for i, ay in enumerate(
    ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"],
    start=1)}
# Rakamlı tarih kalıbı, gün . ay . yıl (ayraç nokta, eğik çizgi ya da tire olabilir). Ör. 02.10.2026, 2/10/2026.
# Kalıbın başındaki ve sonundaki ek kısımlar önünde ve arkasında başka rakam olmasın demek, böylece 123.10.20261 gibi şeyler yakalanmaz.
_SAYISAL = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
# Ayların üç harfli kısaltmaları, TCMB akışı "1 Eki 2026" yazıyor.
AY_KISALTMALARI = {ay[:3]: no for ay, no in AYLAR.items()}
# Yazıyla tarih kalıbı, "2 ekim 2026" ya da "2 eki. 2026". Önce tam adlar denenir, "mart" yerine "mar" yakalanmasın.
_YAZIYLA = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join([*AYLAR, *AY_KISALTMALARI]) + r")\.?\s+(\d{4})(?!\d)")


# Listeden okunan tek bir duyuru, başlık, adres, tarih.
@dataclass(frozen=True)
class Oge:
    baslik: str
    url: str
    tarih: date


# Sitedeki tarih yazısını (ör. "Yayın tarihi: 2 Ekim 2026") gerçek bir tarihe çevirir. Okuyamazsa None.
def tarih_oku(metin: str, bicim: str | None = None) -> date | None:
    """Tarih düğümünün metnini tarihe çevirir. Önce verilen biçim tam eşleşme olarak denenir, sonra metnin içinde
    "02.10.2026", "2.10.2026", "02-10-2026" ya da "2 Ekim 2026" gibi biçimler aranır. Bulunamazsa None döner."""
    # Fazla boşlukları ve satır sonlarını tek boşluğa indir.
    metin = " ".join(metin.split())
    # Ayarda özel bir tarih biçimi verilmişse (ör. "%d.%m.%Y") önce onu dene.
    if bicim:
        try:
            return datetime.strptime(metin, bicim).date()
        # Uymadıysa sorun değil, aşağıdaki genel kalıplara geç.
        except ValueError:
            pass
    try:
        # := "bul ve aynı anda m'ye ata". Rakamlı tarih bulunduysa m[1]=gün, m[2]=ay, m[3]=yıl.
        if m := _SAYISAL.search(metin):
            return date(int(m[3]), int(m[2]), int(m[1]))
        # Yoksa yazıyla tarihi ara (önce Türkçe küçük harfe çevirip).
        if m := _YAZIYLA.search(tr_kucuk(metin)):
            return date(int(m[3]), AYLAR.get(m[2]) or AY_KISALTMALARI[m[2]], int(m[1]))
    # Kalıba uyuyor ama 31 Şubat gibi takvimde olmayan bir günse tarih yok say.
    except ValueError:  # 31.02.2026 gibi
        return None
    # Hiçbir kalıp tutmadı.
    return None


# Başlığın linkini bulur. Başlığın kendisi link değilse yukarı doğru (onu saran kutulara) bakar.
def _link(baslik_dugumu, kutu) -> str | None:
    """Başlığın adresi. Başlık düğümünün kendi href'i varsa o, yoksa onu saran en yakın link alınır, öğenin kendisi de olabilir.
    Bazı sitelerde bütün kutu bir linktir ve başlık içinde düz metindir, örneğin Rekabet Kurumu'nda <a><table>… yapısı var."""
    # Başlık düğümünden başla.
    dugum = baslik_dugumu
    # Kutunun dışına çıkana kadar yukarı tırman.
    while dugum is not None:
        # Bu düğüm bir <a> (link) ise href'ini al, değilse boş.
        href = (dugum.attributes.get("href") or "").strip() if dugum.tag == "a" else ""
        # Gerçek bir adres olmalı, javascript, # ve mailto ile başlayanlar link sayılmaz.
        if href and not href.startswith(("javascript:", "#", "mailto:")):
            return href
        # Duyuru kutusunun kendisine geldik ve hâlâ link yoksa bu kutuda link yok demektir.
        if dugum.mem_id == kutu.mem_id:
            return None
        # Bir üst düğüme çık.
        dugum = dugum.parent
    return None


# Liste sayfasının HTML'inden bütün duyuruları (başlık, tam adres, tarih) çıkarır.
def parse_liste(html: str, taban_url: str, oge: str, baslik: str, tarih: str,
                tarih_bicimi: str | None = None) -> list[Oge]:
    # Bulunan duyurular ve daha önce gördüğümüz adresler (aynısı iki kez eklenmesin).
    sonuc = []
    gorulen = set()
    # "oge" seçicisine uyan her kutu bir duyuru adayı.
    for kutu in HTMLParser(html).css(oge):
        # Kutunun içinde başlığı ve tarihi bul. css_first ilk eşleşeni verir.
        baslik_dugumu, tarih_dugumu = kutu.css_first(baslik), kutu.css_first(tarih)
        # Başlık varsa linkini bul.
        href = _link(baslik_dugumu, kutu) if baslik_dugumu is not None else None
        # Linki ya da tarihi olmayan kutu duyuru değildir, atla.
        if href is None or tarih_dugumu is None:
            continue  # öğe seçicisi başka kutuları da yakalayabilir (ör. sayfa düzeni ızgarası)
        link = baslik_dugumu
        # Yarım linki tam adrese çevir.
        url = urljoin(taban_url, href)
        # Tarih yazısını gerçek tarihe çevir.
        gun = tarih_oku(tarih_dugumu.text(), tarih_bicimi)
        # Aynı duyuru zaten eklendiyse ya da tarih okunamadıysa atla.
        if url in gorulen or gun is None:
            continue  # iç içe kutular aynı duyuruyu iki kez verebilir, tarihsiz kutu duyuru değildir
        gorulen.add(url)
        # Başlıktaki fazla boşlukları temizleyip listeye ekle.
        sonuc.append(Oge(baslik=" ".join(link.text().split()), url=url, tarih=gun))
    return sonuc


# Duyuru sayfasından sadece asıl metnin olduğu kısmı (menü, alt bilgi hariç) alıp düz metne çevirir.
def parse_icerik(html: str, secici: str) -> str:
    dugum = HTMLParser(html).css_first(secici)
    # Metin alanı bulunamadıysa site tasarımı değişmiş demektir, sessizce boş dönmek yerine hata.
    if dugum is None:
        raise ValueError(f"İçerik alanı bulunamadı ({secici!r}); site tasarımı değişmiş olabilir")
    return html_metni(dugum.html)


# Liste tipli bütün kaynakların ortak sınıfı. html ve tarayici tipleri bundan türer.
class ListeKaynagi:
    """Alt sınıf sadece sayfanın nasıl indirileceğini söyler, bunun için `_liste_html` ve `_detay_html` yazılır."""

    # Kaynak kurulurken çalışan fonksiyon, ayarları alıp kontrol eder ve nesneye kaydeder.
    def __init__(
        self,
        # Kaynağın kısa adı ve raporda görünen adı.
        ad: str,
        etiket: str,
        # Duyuru listesinin adresi.
        liste_url: str,
        # Seçiciler, her duyurunun kutusu, kutudaki başlık, kutudaki tarih, duyuru sayfasındaki metin alanı.
        oge: str,
        baslik: str,
        tarih: str,
        icerik: str,
        # İsteğe bağlı özel tarih biçimi.
        tarih_bicimi: str | None = None,
        # Kaç gün geriye bakılsın (aşağıda açıklama var).
        ortusme_gun: int = 1,
    ):
        # Zorunlu alanların hepsini sırayla kontrol et.
        for alan, deger in (("liste_url", liste_url), ("oge", oge), ("baslik", baslik), ("tarih", tarih),
                            ("icerik", icerik)):
            # Boş bırakıldıysa hata ver.
            if not str(deger).strip():
                raise ValueError(f"{alan} boş olamaz")
            # Adres dışındakiler CSS seçici, boş bir sayfada deneyerek yazımı doğru mu bakıyoruz.
            if alan != "liste_url":
                try:
                    HTMLParser("<html></html>").css(deger)
                except ValueError as e:
                    raise ValueError(f"{alan} seçicisi geçersiz: {deger!r}") from e
        # Kontrol geçti, ayarları nesnenin üstüne kaydet.
        self.ad = ad
        self.etiket = etiket
        self.liste_url = liste_url
        self.oge_secici = oge
        self.baslik_secici = baslik
        self.tarih_secici = tarih
        self.icerik_secici = icerik
        self.tarih_bicimi = tarih_bicimi
        # Liste sadece gün bilgisi veriyor, checkpoint gününden bu kadar geriye bakılır.
        # Tekrar gelen kayıtlar dis_id (duyuru adresi) ile elenir.
        self.ortusme = timedelta(days=ortusme_gun)

    # Liste sayfasını indirmek alt sınıfın işi. Burada yazılmadıysa çağrılınca hata verir.
    def _liste_html(self, client: httpx.Client) -> str:
        raise NotImplementedError

    # Tek duyuru sayfasını indirmek de alt sınıfın işi.
    def _detay_html(self, client: httpx.Client, url: str) -> str:
        raise NotImplementedError

    # Duyuru doğrudan bir PDF dosyasıysa onu indirir. html tipi buna adres kontrolü ekliyor.
    def _belge_indir(self, client: httpx.Client, url: str) -> httpx.Response:
        """Doğrudan PDF olan duyurunun ham dosyasını indirir. Alt sınıf adres kontrolü ekleyebilir, html tipi guvenli_url ekliyor."""
        response = client.get(url)
        response.raise_for_status()
        return response

    # Asıl tarama, listeyi oku, checkpoint'ten sonraki duyuruları seç, bir adım olarak ver.
    def tara(
        self, client: httpx.Client, checkpoint: str | None, bugun: date, simdi: datetime
    ) -> Iterator[TaramaAdimi]:
        # Liste sayfasını indir ve içinden duyuruları çıkar.
        ogeler = parse_liste(self._liste_html(client), self.liste_url, self.oge_secici, self.baslik_secici,
                             self.tarih_secici, self.tarih_bicimi)
        # Hiç duyuru bulunamadıysa.
        if not ogeler:
            # Sessizce "yeni duyuru yok" demek yerine hata, seçiciler büyük ihtimalle bozuldu. Checkpoint
            # ilerlemez, çalışma HATALI olur, yöneticiye sağlık uyarısı gider, panelde kaynakta kırmızı uyarı çıkar.
            raise ValueError(f"{self.ad}: listede hiç duyuru okunamadı; seçicileri kontrol edin")

        # İlk çalıştırmada geçmiş taranmaz, bugünden başlanır.
        # esik, bundan eski duyuruların alınmayacağı tarih. Checkpoint varsa ondan 1 gün öncesi, yoksa bugün.
        esik = (date.fromisoformat(checkpoint) if checkpoint else bugun) - (self.ortusme if checkpoint else timedelta())
        bilgi = {}
        # Listedeki EN ESKİ duyuru bile eşikten yeniyse, liste sayfası dolmuş olabilir, arada kaçan duyuru olabilir.
        if min(o.tarih for o in ogeler) >= esik and checkpoint:
            log.warning("%s: listenin tamamı yeni; ilk sayfadan eski duyurular kaçmış olabilir", self.ad)
            # Bu uyarı çalışma özetine yazılır, yönetici görür.
            bilgi = {"uyari": "liste sayfası doldu, eski duyurular kaçmış olabilir"}
        # Eşikten yeni her duyuru için bir kayıt taslağı hazırla.
        kayitlar = [
            KayitTaslagi(
                # Duyurunun adresi aynı zamanda kimliği (aynısı tekrar gelirse veritabanı yakalar).
                dis_id=o.url,
                yayin_tarihi=o.tarih,
                baslik=o.baslik,
                url=o.url,
                # Raporda görünecek kaynak satırı, "Rekabet Kurumu, 02.10.2026: Başlık".
                kaynakca=f"{self.etiket}, {o.tarih:%d.%m.%Y}: {o.baslik}",
                tur="Duyuru",
            )
            for o in ogeler
            if o.tarih >= esik
        ]
        # Tek bir adım olarak ver. Checkpoint bugüne ilerler, bir dahaki tarama bugünden devam eder.
        yield TaramaAdimi(kayitlar, checkpoint=bugun.isoformat(), bilgi=bilgi)

    # Tek bir duyurunun tam metnini getirir.
    def icerik(self, client: httpx.Client, dis_id: str, url: str) -> Icerik:
        # Adres .pdf ile bitiyorsa duyuru bir sayfa değil, doğrudan PDF dosyası.
        if urlparse(url).path.lower().endswith(".pdf"):
            # Bazı duyurular sayfa değil doğrudan PDF (ör. TCMB'de konuşma metinleri), içerik seçicisi uygulanamaz,
            # tarayıcı da gerekmez. Dosya indirilir, PDF okuyucusundan geçer (taranmışsa OCR).
            response = self._belge_indir(client, url)
            return belge_icerigi(response.content, response.headers.get("content-type", ""))
        # Normal durum, duyuru sayfasını indir, metin alanını çıkar.
        return Icerik(parse_icerik(self._detay_html(client, url), self.icerik_secici))

    # "N gün öncesinden başla" denemeleri için checkpoint değeri, N gün önceki tarih (ör. "2026-09-05").
    def geriye_checkpoint(self, bugun: date, simdi: datetime, gun: int) -> str:
        return (bugun - timedelta(days=gun)).isoformat()

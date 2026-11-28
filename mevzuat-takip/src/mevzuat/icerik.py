"""Belgelerden düz metin çıkarma (HTML ve PDF)."""
# Bir belgeyi (web sayfası ya da PDF) alıp içindeki yazıyı düz metin olarak çıkaran fonksiyonlar.
# PDF resimse OCR'a gönderilir.

# io.BytesIO, bellekteki baytları dosya gibi açmak için (pdfplumber dosya istiyor).
import io
import re
# tempfile, iş bitince kendiliğinden silinen geçici klasör (OCR resimleri için).
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# pdfplumber, PDF'teki seçilebilir yazıyı çıkarır.
import pdfplumber
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from mevzuat import ocr

# Bir PDF sayfasından bundan az metin çıkıyorsa sayfa büyük ihtimalle taranmış görüntüdür.
OCR_ESIGI = 30
# RG CB kararı PDF'lerinde başlık (tarih, sayı, karar sayısı, imza) metin, kararın gövdesi ise
# görüntü. Bu durumda her sayfada biraz metin olur ama toplam ~150 karakteri geçmez.
# Gerçek bir karar metni bundan çok daha uzundur, kısa çıkarsa OCR gerekli sayılır.
BELGE_MIN_METIN = 500
# OCR en fazla bu kadar sayfa okur (sayfa başına ~5 sn). İşlemin asıl hükmü (Madde 1, yürürlük) baştadır,
# 200+ sayfalık eklerin (ör. SGK Sağlık Uygulama Tebliği, 02.10.2026, 219 taranmış sayfa) tamamını okumak
# sabah raporunu dakikalarca geciktirir ve filtre/özet için gerekmez. Tamamı için orijinal belgeye bakılır.
OCR_SAYFA_SINIRI = 30
# Karışık belge, asıl metin seçilebilir ama bazı sayfalar (harita, imzalı ek, liste) görüntü.
# 14 günlük ölçümde bu sayfalarda ~128 karakter metin (başlık) ve sayfanın ~%39'u görüntüydü.
# Böyle sayfalar tek tek OCR'dan geçirilir, belgenin geri kalanı olduğu gibi kalır.
KARISIK_METIN_ESIGI = 200
KARISIK_RESIM_ORANI = 0.25

# pdf_metni'ne sayfa sınırı verilmediğini belirten işaret (None "sınırsız" anlamına geliyor).
_VARSAYILAN_SINIR = object()

# Eski PDF'lerde yazı katmanı var ama font haritası yok. Çıkan metin "(cid:5)(cid:7)..." gibi çöptür.
_CID = re.compile(r"\(cid:\d+\)")
# Metnin bundan fazlası (cid:N) ise sayfa okunmamış sayılır, OCR'a gider.
CID_ORANI = 0.2


# Sayfa yazısı çöp mü (okunamayan font kodları ya da anlamsız simgeler).
def bozuk_yazi_mi(metin: str) -> bool:
    if not metin:
        return False
    cid_uzunluk = sum(len(b) for b in _CID.findall(metin))
    if cid_uzunluk / len(metin) > CID_ORANI:
        return True
    # Unicode "yedek karakter" (U+FFFD) ve özel kullanım alanı harfleri de bozuk font işaretidir.
    kotu = sum(1 for c in metin if c == "�" or "" <= c <= "")
    return kotu / len(metin) > CID_ORANI


# Sayfanın yazısı, bozuksa boş (böylece taranmış sayfa gibi OCR'a gider).
def temiz_sayfa_yazisi(metin: str | None) -> str:
    metin = (metin or "").strip()
    return "" if bozuk_yazi_mi(metin) else metin


# HTML'in başındaki <meta charset="windows-1254"> gibi satırdan harf kodlamasını yakalayan kalıp.
META_CHARSET = re.compile(rb"""charset\s*=\s*["']?([\w-]+)""", re.IGNORECASE)


# Okunan bir belgenin sonucu.
@dataclass(frozen=True)
class Icerik:
    # Belgenin düz metni.
    metin: str
    ocr_gerekli: bool = False  # metin eksik ve OCR yapılamadı
    ocr_ile: bool = False  # metin OCR ile okundu, taslaktır, rakam/isim hatası olabilir
    # PDF belgenin kendisi ve OCR'la okunan sayfaların metni (0'dan sayfa no). İlgili kayıtta tablo/görsel çıkarmak ve
    # belgenin kopyasını saklamak için (pipeline.belge_gorsellerini_sakla); metne ve filtreye etkisi yok.
    belge: bytes | None = field(default=None, repr=False, compare=False)
    ocr_sayfalari: dict[int, str] = field(default_factory=dict, repr=False, compare=False)


# Ham baytları doğru harf kodlamasıyla yazıya çevirir.
def html_coz(veri: bytes, header_charset: str | None = None) -> str:
    # RG .htm dosyaları HTTP header'ında charset göndermiyor, sadece <meta>'da Windows-1254 yazıyor.
    # httpx bu durumda UTF-8 varsayıyor ve Türkçe karakterler bozuluyor, o yüzden kendimiz çözüyoruz.
    charset = header_charset
    # Sunucu kodlamayı söylemediyse sayfanın ilk 4 KB'ındaki <meta> satırına bak, orada da yoksa utf-8 say.
    if not charset:
        eslesme = META_CHARSET.search(veri[:4096])
        charset = eslesme.group(1).decode("ascii") if eslesme else "utf-8"
    # Çözülemeyen karakter olursa çökme, yerine bir yer tutucu işaret koy.
    return veri.decode(charset, errors="replace")


# HTML'i düz metne çevirir, kodları (script/style) atar, satırları temizler.
def html_metni(html: str) -> str:
    tree = HTMLParser(html)
    # JavaScript ve stil blokları yazı değil, sil.
    for node in tree.css("script, style"):
        node.decompose()
    # Sayfanın gövdesini al (yoksa kökü).
    govde = tree.body or tree.root
    # Her etiketi ayrı satır yaparak metni çıkar.
    metin = govde.text(separator="\n") if govde else ""
    # Her satırdaki fazla boşlukları temizle.
    satirlar = (re.sub(r"\s+", " ", s).strip() for s in metin.splitlines())
    # Boş satırları atıp birleştir.
    return "\n".join(s for s in satirlar if s)


# Ana metni taşıyan bloklar, kutunun doğrudan altındaki bu etiketlerin metni sayılır (menü/yan sütun linkleri değil).
_BLOKLAR = {"p", "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "pre", "dl", "font", "span", "strong", "b"}
_ANA_METIN_EN_AZ = 100  # karakter


# Seçici verilmemiş bir sayfada "asıl yazının olduğu kutuyu" tahmin eder, en çok düz yazı içeren kutu kazanır.
def ana_icerik_dugumu(agac: HTMLParser, en_az: int = _ANA_METIN_EN_AZ):
    """Sayfanın ana metninin bulunduğu kutu, seçici verilmemiş duyuru sayfaları için. Kendi doğrudan metni ve doğrudan
    altındaki paragraf, liste ve tablo metni en uzun olan kutu seçilir. Menü ve yan sütunlar link listesi olduğu için düşük puan
    alır. Yeterince metin yoksa None döner."""
    # Şimdiye kadarki en iyi kutu ve puanı. En az 100 karakter şartı için başlangıç puanı 99.
    en_iyi, en_iyi_puan = None, en_az - 1
    # Aday kutular, article, main, section, div, tablo hücresi.
    for dugum in agac.css("article, main, section, div, td"):
        # Kutunun doğrudan kendi yazısı.
        puan = len(dugum.text(deep=False, strip=True))
        # İçindeki paragraf/liste/tablo gibi blokların yazısını ekle, ama link yazılarını düş (menüler link doludur).
        for cocuk in dugum.iter():
            if cocuk.tag in _BLOKLAR:
                linkler = sum(len(a.text(strip=True)) for a in cocuk.css("a"))
                puan += max(0, len(cocuk.text(strip=True)) - linkler)
        # Daha yüksek puanlıysa yeni en iyi bu.
        if puan > en_iyi_puan:
            en_iyi, en_iyi_puan = dugum, puan
    return en_iyi


# Gizli öğeleri tanımak için, stilde "display:none" / "visibility:hidden" ve bilinen gizli sınıf adları.
_GIZLI_STIL = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden")
_GIZLI_SINIF = {"modal", "hidden", "d-none", "sr-only", "visually-hidden"}


# Ekranda görünmeyen öğeleri sayfadan siler (uzun KVKK metni gibi şeyler "asıl yazı" sanılmasın).
def gizlileri_at(agac: HTMLParser) -> None:
    """Ekranda görünmeyen öğeleri (açılır pencere, gizli KVKK metni gibi) ağaçtan çıkarır, uzun oldukları için
    ana metin sanılmasınlar."""
    # Gizli olabilecek öğeleri tara.
    for d in agac.css("[hidden], [aria-hidden], [style], [class]"):
        stil = (d.attributes.get("style") or "").lower()
        siniflar = set((d.attributes.get("class") or "").split())
        # hidden özelliği, aria-hidden="true", gizleyen stil ya da gizli sınıf varsa sil.
        if ("hidden" in d.attributes or d.attributes.get("aria-hidden") == "true" or _GIZLI_STIL.search(stil)
                or siniflar & _GIZLI_SINIF):
            d.decompose()


# Seçicisiz sayfalarda asıl metni çıkarır (RSS kaynakları kullanıyor).
def ana_metin(html: str) -> str:
    """Seçicisi olmayan sayfanın içeriği. Ana metin kutusu bulunursa o, bulunamazsa menüler dahil bütün sayfa okunur.
    nav, header ve footer atılmaz, çünkü bazı kurum sitelerinde asıl metin bozuk HTML yüzünden <header> içinde kalıyor."""
    agac = HTMLParser(html)
    # Kod bloklarını sil.
    for dugum in agac.css("script, style, noscript"):
        dugum.decompose()
    # Gizli öğeleri sil.
    gizlileri_at(agac)
    # Asıl metin kutusunu bul, bulunamazsa bütün sayfayı kullan.
    dugum = ana_icerik_dugumu(agac)
    return html_metni(dugum.html if dugum is not None else agac.html)


# Bir PDF sayfasının ne kadarı resimle kaplı (0 ile 1 arası oran).
def _resim_orani(sayfa) -> float:
    alan = sayfa.width * sayfa.height
    # Her resmin genişlik × yüksekliğini topla, sayfa alanına böl, 1'i geçmesin.
    return min(1.0, sum(max(0, (r["x1"] - r["x0"]) * (r["bottom"] - r["top"])) for r in sayfa.images) / alan)


# Hangi sayfalar OCR'a gidecek. (sayfaların yazı katmanı, OCR'lanacak sayfa numaraları; hepsi ya da sadece resimliler)
def ocr_secimi(veri: bytes) -> tuple[list[str], list[int]]:
    sayfalar: list[str] = []
    tum_belge_ocr = False
    gorsel_sayfalar: list[int] = []  # karışık belgede OCR'lanacak sayfalar
    with pdfplumber.open(io.BytesIO(veri)) as pdf:
        for i, sayfa in enumerate(pdf.pages):
            metin = temiz_sayfa_yazisi(sayfa.extract_text())
            # Sayfada neredeyse hiç yazı yok, belge taranmış, hepsini OCR'la.
            if len(metin) < OCR_ESIGI:
                tum_belge_ocr = True
            # Az yazı + çok resim, karışık sayfa, sadece bu sayfayı OCR'la.
            elif len(metin) < KARISIK_METIN_ESIGI and _resim_orani(sayfa) > KARISIK_RESIM_ORANI:
                gorsel_sayfalar.append(i)
            sayfalar.append(metin)
    # Toplam metin çok kısaysa (sadece başlık/imza okunmuş) yine bütün belgeyi OCR'la.
    if len("\n\n".join(s for s in sayfalar if s)) < BELGE_MIN_METIN:
        tum_belge_ocr = True
    return sayfalar, list(range(len(sayfalar))) if tum_belge_ocr else gorsel_sayfalar


# PDF'in metnini çıkarır. Seçilebilir yazı yoksa (taranmış belge) ya da bazı sayfalar resimse OCR yapar.
def pdf_metni(veri: bytes, sayfa_siniri: int | None | object = _VARSAYILAN_SINIR) -> Icerik:
    """sayfa_siniri, OCR'ın okuyacağı en fazla sayfa. Verilmezse OCR_SAYFA_SINIRI, None verilirse sınır yok (arşiv)."""
    if sayfa_siniri is _VARSAYILAN_SINIR:
        sayfa_siniri = OCR_SAYFA_SINIRI
    # Sayfaların yazı katmanı ve OCR'lanacak sayfalar (hepsi ya da sadece resimli olanlar).
    sayfalar, okunacak = ocr_secimi(veri)
    tum_belge_ocr = bool(sayfalar) and okunacak == list(range(len(sayfalar)))
    tum_metin = "\n\n".join(s for s in sayfalar if s)

    # OCR'a gerek yok, okunan metni ver.
    if not okunacak:
        return Icerik(tum_metin, belge=veri)
    # OCR lazım ama Tesseract yok.
    if not ocr.ocr_kullanilabilir():
        # Metnin tamamı ya da bir kısmı görüntüde ve okunamıyor, raporda "metin eksik" görünmeli.
        return Icerik(tum_metin, ocr_gerekli=True, belge=veri)

    # 30 sayfa sınırı aşıldıysa ilk 30 sayfayı al.
    sinir_asildi = sayfa_siniri is not None and len(okunacak) > sayfa_siniri
    if sayfa_siniri is not None:
        okunacak = okunacak[:sayfa_siniri]
    # Geçici klasörde OCR yap (klasör iş bitince silinir).
    with tempfile.TemporaryDirectory() as klasor:
        okunan = ocr.sayfalari_ocr(veri, Path(klasor), okunacak)
    # Bütün belge OCR'landıysa metin sadece OCR çıktısı.
    if tum_belge_ocr:
        metin = "\n\n".join(m for m in okunan.values() if m)
    # Karışık belgede sadece resimli sayfaların metnini OCR çıktısıyla değiştir.
    else:
        for i, sayfa_metni in okunan.items():
            sayfalar[i] = sayfa_metni
        metin = "\n\n".join(s for s in sayfalar if s)
    # Sınır aşıldıysa metnin sonuna not düş.
    if sinir_asildi:
        metin += (f"\n\n[Belge {len(sayfalar)} sayfa; otomatik okuma ilk {sayfa_siniri} taranmış sayfayla "
                  "sınırlandı. Tamamı için orijinal belgeye bakın.]")
    # "OCR ile okundu" işaretiyle ver (raporda "otomatik okunmuş" yazar).
    return Icerik(metin, ocr_ile=True, belge=veri, ocr_sayfalari=dict(okunan))


# İndirilen bir belge PDF mi HTML mi, karar verip uygun okuyucuya gönderir.
def belge_icerigi(veri: bytes, content_type: str, header_charset: str | None = None) -> Icerik:
    # Sunucu "pdf" dediyse ya da dosya "%PDF-" ile başlıyorsa PDF'tir.
    if "pdf" in content_type.lower() or veri[:5] == b"%PDF-":
        return pdf_metni(veri)
    # Değilse HTML say.
    return Icerik(html_metni(html_coz(veri, header_charset)))

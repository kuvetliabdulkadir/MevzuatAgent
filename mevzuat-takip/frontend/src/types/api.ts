// Sunucunun (src/mevzuat/web/__init__.py) döndürdüğü veri tipleri. Alan adları API ile birebir aynı.

// TypeScript "tip" tanımları, kod çalışırken bir şey yapmaz, sadece verinin hangi alanlara sahip olduğunu tarif eder.
// Yanlış alan adı yazılırsa derleme hata verir. Dik çizgi ya bu ya şu demek, soru işareti ya da null o alanın boş olabileceğini söyler.
// Kullanıcı rolleri, rapor durumları ve mail (gönderim) durumları.
export type Rol = 'admin' | 'onaylayici' | 'api';
export type RaporDurumu = 'ONAY_BEKLIYOR' | 'ONAYLANDI' | 'GONDERILDI' | 'REDDEDILDI' | 'GONDERILIYOR';
export type GonderimDurumu = 'BEKLIYOR' | 'GONDERILIYOR' | 'GONDERILDI';

// Giriş yapmış kullanıcı ve yetkileri (menü bunlara göre çizilir).
export interface Kullanici {
  id: number;
  ad: string;
  eposta: string;
  rol: Rol;
  mfa_aktif: boolean;
  karar_verebilir: boolean;
  grup_yonetebilir: boolean;
  ayar_yonetebilir: boolean; // kaynak ve konu yönetimi
  kurtarma_yapabilir: boolean; // kaynak/konu geçmişinden önceki hale döndürme (admin)
  api_kullanicisi: boolean; // sadece API dokümanını görür
}

// GET /api/oturum cevabı.
export interface OturumBilgisi {
  csrf: string;
  kullanici: Kullanici | null;
  mfa: 'kod' | 'kurulum' | null; // parola doğru, iki adımlı doğrulama bekleniyor
  mail_kapali: boolean; // SMTP ayarsız, mailler gönderilmiyor, sunucuda dosyaya yazılıyor
}

// Giriş adımı cevabı, sonra ne gelecek.
export interface GirisCevabi {
  sonraki: 'panel' | 'kod' | 'kurulum';
}

// Rapor listesindeki satır.
export interface RaporOzeti {
  id: number;
  konu: string;
  durum: RaporDurumu;
  olusturuldu: string;
  karar_zamani: string | null;
  kayit_sayisi: number;
  hata: string | null;
}

// Metni değişen mevzuatta tek bir madde farkı.
export interface Degisiklik {
  bolum: string;
  tur: string;
  eski: string;
  yeni: string;
  cikan?: string[]; // bu parçada çıkarılan ifadeler (eski kayıtlarda yok)
  giren?: string[]; // bu parçada eklenen ifadeler
}

// Rapordaki tek kalem (belge).
export interface Kalem {
  id: number;
  no: number;
  tur_adi: string;
  baslik: string;
  kaynak: string;
  yayin_tarihi: string;
  is_kollari: string[];
  eslesmeler: Record<string, string[]>;
  ozet: string | null;
  ozet_tablosu: string[];
  yururluk: string | null;
  one_cikanlar: string[];
  metin: string | null;
  kisaltildi: boolean;
  ocr: boolean;
  okunamadi: boolean;
  degisiklikler: Degisiklik[] | null;
  degisiklik_ozeti?: string | null; // "Madde 6 değişti; Ek Madde 1 eklendi"
  arsiv_surumu?: { anahtar: string; surum: number } | null;
  hakkinda?: { ad: string; metin: string }[]; // "Mevzuat hakkında": tür/RG, Amaç, Kapsam, Dayanak, arşivdeki geçmişi // arşivdeki yeni sürüm (tam karşılaştırma için)
  kaynakca: string;
  url: string | null; // belgenin resmî adresi
  nedenler: { konu: string; aciklama: string }[]; // eşleşen konuların açıklamaları
  haric: boolean;
  gidecek: { ad: string; kisi: number }[];
}

// Onaylanmış raporun tek bir dağıtım maili.
export interface Gonderim {
  id: number;
  gruplar: string[];
  alicilar: string[];
  durum: GonderimDurumu;
  kalemler: number[];
  gonderildi: string | null;
  hata: string | null;
  notu: string | null;
}

// Onay ekranında seçilebilen aktif alıcı grubu.
export interface RaporGrubu {
  id: number;
  ad: string;
  adresler: string[];
  is_kollari: string[];
}

// GET /api/raporlar/{id} cevabı.
export interface RaporDetayi {
  rapor: RaporOzeti & {
    karar_notu: string | null;
    karar_veren: string | null;
    gonderildi: string | null;
    alicilar: string[];
  };
  kalemler: Kalem[];
  gonderimler: Gonderim[];
  gruplar: RaporGrubu[];
  adres_onerileri: string[];
}

// Alıcı grubu.
export interface AliciGrubu {
  id: number;
  ad: string;
  is_kollari: string[];
  adresler: string[];
  aktif: boolean;
  guncellendi: string;
}

// GET /api/gruplar cevabı.
export interface GrupListesi {
  gruplar: AliciGrubu[];
  is_kollari: string[];
  kapsanmayan: string[];
}

// Ekranda gösterilen başarı/hata mesajı.
export interface Mesaj {
  tur: 'basari' | 'hata';
  mesaj: string;
}

// ---- kaynaklar (#10)

// Kaynak formundaki tek bir alanın tanımı.
export interface KaynakAlani {
  ad: string;
  etiket: string;
  tur: 'url' | 'metin' | 'sayi' | 'secim_listesi';
  aciklama: string;
  zorunlu: boolean;
  varsayilan: unknown;
  secenekler: Record<string, string>;
}

// Bir kaynak tipi ve formu.
export interface KaynakTipi {
  tip: string;
  etiket: string;
  aciklama: string;
  eklenebilir: boolean; // panelden yeni kaynak eklenebilir mi (RG/GİB tek kaynak)
  alanlar: KaynakAlani[];
}

// GET /api/kaynak-tipleri cevabı.
export interface KaynakTipleri {
  tipler: KaynakTipi[];
  konular: string[]; // aktif konu adları (varsayılan konu seçimi için)
}

// Kaynaklar listesindeki kaynak (tanım + tarama durumu).
export interface Kaynak {
  ad: string; // kod adı, değişmez
  tip: string;
  tip_etiketi: string;
  etiket: string;
  ayarlar: Record<string, unknown>;
  varsayilan_konular: string[];
  aktif: boolean;
  kaldirildi: boolean;
  surum: number;
  checkpoint: string | null;
  son_basarili: string | null;
  toplam_kayit: number;
  ilgili_kayit: number;
  son_hata: string | null;
  son_calisma: string | null;
  guncelleme: string;
}

// Kaynak ekleme/düzenleme/deneme isteğinin gövdesi.
export interface KaynakIstegi {
  tip: string;
  etiket: string;
  ayarlar: Record<string, unknown>;
  varsayilan_konular: string[];
  aktif: boolean;
  surum?: number;
}

// "Dene" sonucu.
export interface DeneSonucu {
  kayitlar: { baslik: string; tarih: string; kaynakca: string; eslesen: Record<string, string[]>; is_kollari: string[] }[];
  toplam: number;
  kesildi: boolean;
  gun: number;
}

// ---- konular (#10)

// Konu tanımı (+ son 90 gün eşleşme sayısı ve nerede kullanıldığı).
export interface KonuTanimi {
  id: number;
  ad: string;
  is_kollari: string[];
  kelimeler: string[];
  haric: string[]; // metinden önce maskelenir (ör. "altında", yer adları)
  dislanan: string[]; // başlıkta geçerse konu hiç eşleşmez
  aciklama: string; // bu konu bizi neden ilgilendiriyor, raporda görünür
  aktif: boolean;
  eski_adlar: string[];
  surum: number;
  guncelleme: string;
  eslesme_90: number;
  kullanim: { kaynaklar: string[]; izlenen: boolean };
}

// GET /api/konular cevabı.
export interface KonuListesi {
  konular: KonuTanimi[];
  gun: number;
  is_kolu_secenekleri: string[];
}

// Konu ekleme/düzenleme/önizleme isteğinin gövdesi.
export interface KonuIstegi {
  ad: string;
  is_kollari: string[];
  kelimeler: string[];
  haric: string[];
  dislanan: string[];
  aciklama: string;
  surum?: number;
  id?: number;
}

// Önizlemedeki tek başlık satırı.
export interface OnizlemeSatiri {
  baslik: string;
  tarih: string;
  kaynak: string;
  kelimeler: string[];
  baska_konular?: string[];
}

// "Etkisini gör" sonucu.
export interface KonuOnizleme {
  gun: number;
  taranan: number;
  ayni_kalan: number;
  eslesecek_sayisi: number;
  dusecek_sayisi: number;
  eslesecek: OnizlemeSatiri[];
  dusecek: OnizlemeSatiri[];
  uyarilar: string[];
}

// ---- değişiklik geçmişi (#10)

// Değişiklik geçmişindeki tek satır.
export interface GecmisKaydi {
  id: number;
  zaman: string;
  islem: string;
  islem_adi: string;
  kim: string;
  farklar: { alan: string; once: unknown; sonra: unknown }[];
  geri_alinabilir: boolean;
  geri_alinan: number | null;
}

// ---- zamanlama (tarama saatleri panelden)

// Zamanlayıcı durumu ve tarama saatleri.
export interface ZamanlamaDurumu {
  saatler: string[]; // "06:30" (Türkiye saati)
  surum: number;
  zamanlayici_calisiyor: boolean; // nabız 2 dk'dan yeni mi
  son_nabiz: string | null;
  durum: 'bekliyor' | 'tarama' | null;
  sonraki: string;
}

// ---- panelden "Şimdi tara"

// Bir tarama çalışmasının özeti.
export interface CalismaOzeti {
  id: number;
  baslangic: string;
  bitis: string | null;
  durum: 'CALISIYOR' | 'BASARILI' | 'HATALI';
  yeni: Record<string, number>; // kaynak, sonra yeni kayıt
  yeni_toplam: number;
  hata: string | null;
}

// GET /api/tarama/durum cevabı.
export interface TaramaDurumu {
  istek: {
    id: number;
    durum: 'BEKLIYOR' | 'CALISIYOR' | 'BITTI' | 'HATALI';
    istendi: string;
    basladi: string | null;
    bitti: string | null;
    isteyen: string | null;
    calisma: CalismaOzeti | null;
    rapor_id: number | null;
    hata: string | null;
  } | null;
  son_calismalar: CalismaOzeti[];
  zamanlama: ZamanlamaDurumu;
}

// --- kullanıcı yönetimi ve denetim kaydı (admin)

// Kullanıcılar sayfasındaki satır.
export interface YonetilenKullanici {
  id: number;
  ad: string;
  eposta: string;
  rol: Rol;
  aktif: boolean;
  son_giris: string | null;
  olusturuldu: string;
  kilitli: boolean;
  davet_bekliyor: boolean; // davet maili gitti, kişi henüz parolasını belirlemedi
  silinecek: string | null; // pasif hesabın silineceği an, silme kapalıysa ya da aktifse null
}

// GET /api/kullanicilar cevabı.
export interface KullaniciListesi {
  kullanicilar: YonetilenKullanici[];
  panel_adresi_var: boolean; // yoksa davet/sıfırlama linki gönderilemez
  pasif_silme_gun: number; // bu kadar gün pasif kalan hesap silinir, 0 ise silinmez
}

// Kullanıcı işlemlerinin cevabı (mesaj + güncel liste).
export interface KullaniciIslemCevabi {
  mesaj: Mesaj;
  kullanicilar: YonetilenKullanici[];
}

// Denetim kaydındaki tek satır.
export interface DenetimKaydi {
  id: number;
  zaman: string;
  kullanici_id: number | null;
  kullanici: string | null;
  islem: string;
  detay: Record<string, unknown>;
  ip: string | null;
}

// GET /api/denetim cevabı.
export interface DenetimListesi {
  kayitlar: DenetimKaydi[];
  islemler: string[];
  kullanicilar: { id: number; ad: string }[];
}

// Parola linki kontrolünün cevabı (kimin linki).
export interface ParolaLinkiBilgisi {
  eposta: string;
  ad: string;
  tur: 'davet' | 'sifirlama';
}

// --- kaynak tipini adresten bulma (POST /kaynaklar/bul)

// Kaynak bulmada tek bir yolun sonucu.
export interface BulmaAdimi {
  yol: string; // "WordPress API" | "RSS/Atom akışı" | "Düz HTML listesi"
  sonuc: 'bulundu' | 'yok';
  not: string;
}

// "Bul" sonucu.
export interface BulmaSonucu {
  bulundu: boolean;
  tip?: string;
  tip_etiketi?: string;
  ayarlar?: Record<string, unknown>;
  aciklama: string;
  onerilen_ad: string;
  ornekler?: { baslik: string; tarih: string; url: string }[];
  icerik_ornegi?: string | null; // ilk duyurunun sayfasından okunan metin (doğru alan mı, kullanıcı görsün)
  adimlar: BulmaAdimi[];
}

// Sol menünün bir öğesi (veritabanından, /api/menu). adres doluysa yeni sekmede açılan bağlantıdır.
export interface MenuOgesi {
  anahtar: string;
  etiket: string;
  aciklama: string;
  ikon: string;
  adres: string | null;
}

// Panelden değiştirilen bir ayar (GET /api/ayarlar). kaynak, değerin nereden geldiği. Şifrenin değeri hiç gelmez.
export interface AyarAlani {
  ad: string;
  grup: string;
  etiket: string;
  aciklama: string;
  tur: 'metin' | 'sayi' | 'evet_hayir' | 'adresler' | 'sifre' | 'adres';
  en_az: number | null;
  en_cok: number | null;
  kaynak: 'panel' | '.env' | 'varsayilan';
  deger: string | number | boolean | string[] | null;
  dolu?: boolean;
  cozulemedi?: boolean;
}

export interface AyarlarCevabi {
  alanlar: AyarAlani[];
  surum: number;
  mesaj?: Mesaj;
}

// Bir API anahtarı (anahtarın kendisi sadece üretilirken bir kez gelir).
export interface ApiAnahtari {
  id: number;
  ad: string;
  rol: 'tam' | 'admin' | 'onaylayici' | null;
  on_ek: string;
  durum: 'aktif' | 'iptal' | 'suresi_doldu';
  olusturan: string | null;
  olusturuldu: string;
  son_kullanma: string | null;
  son_kullanim: string | null;
  iptal: string | null;
}

export interface ApiAnahtarlariCevabi {
  anahtarlar: ApiAnahtari[];
  baslik?: string;
  anahtar?: string;
  mesaj?: Mesaj;
}

// Mevzuat arşivi (/panel/api/arsiv/...).
export type ArsivIstekDurumu = 'BEKLIYOR' | 'CALISIYOR' | 'DURDURULUYOR' | 'BITTI' | 'DURDURULDU' | 'HATALI';
export type ArsivMetinDurumu = 'BEKLIYOR' | 'TAMAM' | 'OCR_GEREKLI' | 'SITEDE_YOK' | 'HATA';

export interface ArsivIlerleme {
  asama: 'liste' | 'metin' | 'bitti';
  tur?: string | null;
  turler: string[];
  yeniden: boolean;
  liste: Record<string, { alinan: number; toplam: number | null; yeni?: number; geri_gelen?: number; kalkan?: number; eksik?: string }>;
  metin: {
    toplam: number; biten: number; yeni_surum: number; okuma_duzeltmesi?: number; ayni?: number; hata: number;
    sitede_yok?: number; ocr_gerekli: number; kusurlu?: number;
  };
  son?: { anahtar: string; ad: string };
  guncellendi?: string;
  gunluk?: string | null;
  istek_araligi?: number | null;
}

export interface ArsivIstegi {
  id: number;
  durum: ArsivIstekDurumu;
  istendi: string;
  basladi: string | null;
  bitti: string | null;
  isteyen: string | null;
  secenekler: { turler?: string[]; yeniden?: boolean; anahtarlar?: string[]; esik?: string; devam_eden?: number };
  ilerleme: ArsivIlerleme | null;
  hata: string | null;
}

export interface ArsivDurumu {
  istek: ArsivIstegi | null;
  turler: { kod: string; ad: string; sayilar: Partial<Record<ArsivMetinDurumu, number>>; toplam: number; kalkan: number }[];
  toplam: number;
  surum_toplam: number;
  baslangic: string | null;
  zamanlayici_calisiyor: boolean;
}

export interface ArsivKunye {
  anahtar: string;
  ad: string;
  tur: string;
  tur_adi: string;
  url: string;
  bicim: 'html' | 'pdf';
  rg_tarihi: string | null;
  rg_sayisi: number | null;
  mulga: boolean;
  kalkti: string | null;
  durum: ArsivMetinDurumu;
  surum_sayisi: number;
  degisiklik_sayisi?: number; // gerçek değişiklik sürümü sayısı (listede)
  son_degisiklik?: string | null; // son değişiklik sürümünün alındığı an (listede)
  son_cekim: string | null;
  son_hata: string | null;
  ilk_gorulme: string;
  kalite: 'temiz' | 'kusurlu' | null;
  // Konu filtresine göre ilgili olduğu konular (listede).
  konular?: string[];
}

export interface ArsivListesi {
  toplam: number;
  sayfa: number;
  boyut: number;
  satirlar: ArsivKunye[];
}

export interface ArsivEkIcDosya {
  ad: string;
  sha256: string | null;
  boyut: number | null;
  bicim: string | null;
  durum: string | null;
}

export interface ArsivEk {
  ad: string;
  boyut: number | null;
  sha256: string | null;
  // indi / sitede_yok / ulasilamadi / kaynakta_bozuk
  durum: string;
  bicim: string | null;
  // tamam / gorunum_yok / hata
  okuma: string | null;
  gorunum_var: boolean;
  not: string | null;
  denenen: { url: string; sonuc: string }[];
  ic: ArsivEkIcDosya[];
}

export type ArsivSurumTuru = 'ilk' | 'degisiklik' | 'okuma_duzeltmesi';

export interface ArsivSurumu {
  surum_no: number;
  cekildi: string;
  okuma: string;
  uzunluk: number;
  surum_turu: ArsivSurumTuru;
  kalite: 'temiz' | 'kusurlu';
  kalite_notlari: string[];
  okuyucu_surumu: number;
  pdf: boolean;
  site_pdf: boolean;
  site_pdf_turu: 'asil' | 'uretilen' | null;
  gorunum_var: boolean;
  sayfa_sayisi: number;
  sayfa_yontemleri: Record<string, number>;
  resim_sayisi: number;
  ekler: ArsivEk[];
  rg_yeni: { tarih: string; sayi: string }[];
}

export interface ArsivKanit {
  kelime: string;
  bolum: string;
  tur: 'baslik' | 'amac' | 'tanim' | 'govde' | 'dayanak' | string;
  parca: string;
}

export interface ArsivDetayi extends Omit<ArsivKunye, 'konular'> {
  surumler: ArsivSurumu[];
  konular: { konu: string; kanit: ArsivKanit[] }[];
}

export interface ArsivAramaSonucu {
  sorgu: string;
  terimler: string[];
  toplam: number;
  sayfa: number;
  boyut: number;
  eksik: boolean;
  sonuclar: {
    anahtar: string;
    ad: string;
    tur: string;
    rg_tarihi: string | null;
    baslikta: boolean;
    madde_sayisi: number;
    gecis: number;
    // parca sunucuda kaçışlanmış HTML, sadece <mark> içerir.
    parcalar: { bolum: string; parca: string }[];
  }[];
}

export interface ArsivKonulari {
  konular: { ad: string; ilgili: number }[];
  toplam_ilgili: number;
  guncel: boolean;
  hesaplandi: string | null;
}

export interface ArsivMetni {
  anahtar: string;
  surum_no: number;
  cekildi: string;
  okuma: string;
  metin: string;
}

export interface ArsivSayfa {
  no: number;
  yontem: string;
  karakter: number;
  uyari: string[];
  goruntu: string | null;
  tablo_yapi: number;
  tablo_goruntu: number;
}

export interface ArsivGorunumu {
  anahtar: string;
  surum_no: number;
  okuma: string;
  gorunum: string;
  sayfalar: ArsivSayfa[];
}

export interface ArsivFarkBolumu {
  bolum: string;
  tur: 'ayni' | 'degisti' | 'eklendi' | 'kaldirildi' | 'mulga';
  html: string;
  eski: string | null;
  yeni: string | null;
}

export interface ArsivFarki {
  anahtar: string;
  eski: number;
  yeni: number;
  eski_cekildi: string;
  yeni_cekildi: string;
  yeni_surum_turu: ArsivSurumTuru;
  sayac: { degisti: number; eklendi: number; kaldirildi: number; mulga: number };
  bolumler: ArsivFarkBolumu[];
}

export interface ArsivEkGorunumu {
  sha256: string;
  ad: string;
  bicim: string;
  boyut: number;
  durum: string;
  notlar: string[];
  gorunum: string | null;
  ic: ArsivEkIcDosya[];
  sayfalar: ArsivSayfa[];
}

export interface ArsivGunlukKaydi {
  zaman: string;
  anahtar: string;
  sonuc: string;
  sure?: number;
  okuma?: string;
  karakter?: number;
  kalite?: string;
  kusurlar?: string[];
  uyarilar?: string[];
  adimlar?: { adim: string; sure: number; [k: string]: unknown }[];
  hata?: string;
  neden?: string;
  sayfa?: number;
  ocr_sayfa?: number;
  gorsel_sayfa?: number;
  ek?: number;
  ek_inmeyen?: number;
  resim?: number;
}

export interface ArsivGunlugu {
  istek: ArsivIstegi;
  ozet: {
    sonuc: Record<string, number>;
    okuma: Record<string, number>;
    kusurlu: string[];
    hata: { anahtar: string; hata: string }[];
    ek_inmeyen: number;
    en_yavas: { anahtar: string; sure: number }[];
    istek: number;
    sayfa: number;
    ocr_sayfa: number;
    gorsel_sayfa: number;
    tablo_yapi: number;
    tablo_goruntu: number;
  };
  kayitlar: ArsivGunlukKaydi[];
  toplam: number;
}

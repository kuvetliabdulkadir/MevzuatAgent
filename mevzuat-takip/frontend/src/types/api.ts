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

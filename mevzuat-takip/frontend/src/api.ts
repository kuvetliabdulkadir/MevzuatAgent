// Panelin sunucuyla konuştuğu tek yer. Oturum, tarayıcının otomatik gönderdiği HttpOnly çerezdedir,
// JavaScript oturum bilgisine dokunmaz. Veri değiştiren her istek X-CSRF-Token başlığını taşır.

// Sunucunun verdiği CSRF anahtarı, veri değiştiren her isteğe eklenir.
let csrfToken = '';

// Sunucudan hata gelince fırlatılan hata türü (HTTP durum koduyla birlikte).
export class ApiHatasi extends Error {
  durum: number;
  constructor(mesaj: string, durum: number) {
    super(mesaj);
    this.durum = durum;
  }
}

// CSRF anahtarını günceller.
export function csrfAyarla(token: string) {
  csrfToken = token;
}

// Panel kullanılırken oturum düşerse (süre doldu, kullanıcı pasifleştirildi) giriş ekranına dönülür.
let oturumDustu: () => void = () => {};
// Oturum düşünce çağrılacak fonksiyonu kaydeder (App bunu "giriş ekranına dön" olarak ayarlıyor).
export function oturumDusunce(geriCagri: () => void) {
  oturumDustu = geriCagri;
}

// Bütün isteklerin ortak fonksiyonu, isteği gönderir, hatayı anlaşılır mesaja çevirir, cevabı verir.
async function istek<T>(yontem: string, yol: string, govde?: unknown): Promise<T> {
  const basliklar: Record<string, string> = { Accept: 'application/json' };
  // GET değilse gövdenin JSON olduğunu ve CSRF anahtarını başlığa yaz.
  if (yontem !== 'GET') {
    basliklar['Content-Type'] = 'application/json';
    basliklar['X-CSRF-Token'] = csrfToken;
  }
  // İsteği gönder. credentials, oturum çerezi de gitsin.
  const cevap = await fetch(`/api${yol}`, {
    method: yontem,
    headers: basliklar,
    body: govde === undefined ? undefined : JSON.stringify(govde),
    credentials: 'same-origin',
  });
  // Cevabı JSON olarak oku (okunamazsa boş nesne).
  const veri = await cevap.json().catch(() => ({}));
  // Hata kodu geldiyse.
  if (!cevap.ok) {
    // FastAPI hata mesajını detail alanında yazı olarak verir, doğrulama hatasında ise liste gelir.
    const detay = typeof veri.detail === 'string' ? veri.detail : 'İstek işlenemedi.';
    // 401 (oturum yok) ve giriş isteği değilse, oturum düştü, giriş ekranına dön.
    if (cevap.status === 401 && !yol.startsWith('/giris')) oturumDustu();
    throw new ApiHatasi(detay, cevap.status);
  }
  // Giriş/çıkışta oturum yenilenir, token da değişir.
  if (veri && typeof veri.csrf === 'string') csrfToken = veri.csrf;
  return veri as T;
}

// Kısa yollar, api.get('/raporlar'), api.post('/giris', {...}), api.put(...).
export const api = {
  get: <T>(yol: string) => istek<T>('GET', yol),
  post: <T>(yol: string, govde: unknown = {}) => istek<T>('POST', yol, govde),
  put: <T>(yol: string, govde: unknown) => istek<T>('PUT', yol, govde),
};

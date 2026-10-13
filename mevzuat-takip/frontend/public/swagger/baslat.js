// Swagger sayfasını başlatır. Doküman herkese açıksa (varsayılan) Swagger doğrudan açılır, anahtar sadece istek atarken
// Authorize'a girilir. Kapalıysa panel oturumuyla (admin, API kullanıcısı) ya da API anahtarıyla açılır.
// Anahtar sadece bu sekmede (sessionStorage) tutulur, sekme kapanınca silinir.
const KUTU = document.getElementById('anahtar-girisi');
const SAKLAMA = 'mevzuat_api_anahtari';
const TANIM = '/api/dokuman/openapi.json';

function sakla(anahtar) {
  try {
    if (anahtar) sessionStorage.setItem(SAKLAMA, anahtar);
    else sessionStorage.removeItem(SAKLAMA);
  } catch (e) { /* tarayıcı izin vermiyorsa her açılışta yeniden sorulur */ }
}

function oku() {
  try { return sessionStorage.getItem(SAKLAMA); } catch (e) { return null; }
}

// Bayt dizisini küçük harf hex yazıya çevirir.
function hex(tampon) {
  return Array.from(new Uint8Array(tampon), (b) => b.toString(16).padStart(2, '0')).join('');
}

// İsteği anahtarla imzalar (HMAC-SHA256), anahtarın kendisi gönderilmez. Sunucudaki web/imza.py ile aynı metin.
async function imzala(istek, anahtar) {
  const kodla = new TextEncoder();
  const adres = new URL(istek.url, window.location.origin);
  const zaman = String(Math.floor(Date.now() / 1000));
  const numara = hex(crypto.getRandomValues(new Uint8Array(16)));
  const govde = typeof istek.body === 'string' ? istek.body : '';
  const govdeOzeti = hex(await crypto.subtle.digest('SHA-256', kodla.encode(govde)));
  const metin = [(istek.method || 'GET').toUpperCase(), adres.pathname, adres.search.slice(1), zaman, numara, govdeOzeti].join('\n');
  const hmacAnahtari = await crypto.subtle.importKey('raw', kodla.encode(anahtar), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const imza = hex(await crypto.subtle.sign('HMAC', hmacAnahtari, kodla.encode(metin)));
  istek.headers = istek.headers || {};
  delete istek.headers.Authorization;
  Object.assign(istek.headers, { 'X-Anahtar': anahtar.slice(0, 10), 'X-Zaman': zaman, 'X-Numara': numara, 'X-Imza': imza });
  return istek;
}

// Authorize'a girilen anahtar istekte "Bearer ..." olarak gelir, imzalanıp başlıktan silinir.
function authorizeAnahtari(istek) {
  const deger = (istek.headers && istek.headers.Authorization) || '';
  return deger.startsWith('Bearer ') ? deger.slice(7).trim() : null;
}

function eleman(etiket, sinif, yazi) {
  const e = document.createElement(etiket);
  if (sinif) e.className = sinif;
  if (yazi) e.textContent = yazi;
  return e;
}

// Dokümanı açar. tanimIstegi sadece dokümanın kendisini (openapi.json) yüklerken uygulanır (anahtar ya da oturum).
// "Try it out" istekleri tarayıcı çerezi taşımaz ve Authorize'daki anahtarla imzalanır, anahtarın kendisi gitmez.
// Authorize'dan çıkış yapılınca istekler gerçekten 401 alır, test eden gerçek davranışı görür.
function swaggerAc(tanimIstegi) {
  return window.SwaggerUIBundle({
    url: TANIM,
    dom_id: '#swagger-ui',
    deepLinking: true,
    persistAuthorization: false,
    requestInterceptor: (istek) => {
      if (istek.url.endsWith(TANIM)) return tanimIstegi(istek);
      istek.credentials = 'omit';
      const anahtar = authorizeAnahtari(istek);
      return anahtar ? imzala(istek, anahtar) : istek;
    },
  });
}

// Anahtar formu. Anahtar sayfaya HTML olarak yazılmaz, sadece metin olarak okunur.
function anahtarIste(hata) {
  const form = eleman('form', 'ag-form');
  form.append(
    eleman('h1', 'ag-baslik', 'Mevzuat Takip API'),
    eleman('p', 'ag-metin', 'Dokümanı görmek için size verilen API anahtarını girin. Panel hesabınız varsa önce panele giriş yapın.'),
  );
  // Parola kutusu değil, yoksa tarayıcı bu siteye kayıtlı panel parolasını içine doldurur ve anahtarla karışır.
  const girdi = eleman('input', 'ag-girdi');
  girdi.type = 'text';
  girdi.autocomplete = 'off';
  girdi.spellcheck = false;
  girdi.name = 'api-anahtari';
  girdi.placeholder = 'mvz_...';
  girdi.setAttribute('aria-label', 'API anahtarı');
  const dugme = eleman('button', 'ag-dugme', 'Dokümanı aç');
  dugme.type = 'submit';
  const satir = eleman('div', 'ag-satir');
  satir.append(girdi, dugme);
  form.append(satir);
  if (hata) form.append(eleman('p', 'ag-hata', hata));
  form.addEventListener('submit', (olay) => {
    olay.preventDefault();
    // Yapıştırılan yazının içinden anahtarı ayıklar, öndeki ve arkadaki boşluk ya da fazlalık sorun olmaz.
    const bulunan = girdi.value.match(/mvz_[A-Za-z0-9_-]{20,}/);
    if (girdi.value.includes('…')) anahtarIste('Listede görünen kısaltmayı değil, anahtar üretilirken gösterilen tam anahtarı girin.');
    else if (!bulunan) anahtarIste('Anahtar mvz_ ile başlamalı. Üretilirken gösterilen anahtarın tamamını yapıştırın.');
    else anahtarla(bulunan[0]);
  });
  KUTU.replaceChildren(form);
  girdi.focus();
}

// Anahtarı dener. Geçerliyse doküman anahtarla açılır, değilse form hata mesajıyla yeniden çıkar.
function anahtarla(anahtar) {
  imzala({ url: TANIM, method: 'GET', headers: {} }, anahtar)
    .then((istek) => fetch(TANIM, { headers: istek.headers }))
    .then((cevap) => {
      if (!cevap.ok) throw new Error('gecersiz');
      sakla(anahtar);
      const cikis = eleman('button', 'ag-cikis', 'Anahtarı unut');
      cikis.type = 'button';
      cikis.addEventListener('click', () => { sakla(null); window.location.reload(); });
      const bilgi = eleman('div', 'ag-bilgi');
      bilgi.append(eleman('span', '', "API anahtarıyla açıldı. Try it out istekleri Authorize'daki anahtarla imzalanır, çıkış yapılırsa 401 alır."), cikis);
      KUTU.replaceChildren(bilgi);
      const ui = swaggerAc((istek) => imzala(istek, anahtar));
      // Authorize'a anahtar girilmiş gelir, ayrıca girmeye gerek kalmaz.
      ui.preauthorizeApiKey('HTTPBearer', anahtar);
    })
    .catch(() => {
      sakla(null);
      anahtarIste('Anahtar geçersiz, süresi dolmuş ya da iptal edilmiş.');
    });
}

// Kapalı dokümanda panel oturumuna ya da anahtara bakılır.
function kapaliDokuman() {
  return fetch('/panel/api/oturum', { credentials: 'same-origin' })
    .then((cevap) => cevap.json())
    .then((oturum) => {
      // Panel oturumu dokümanı görebiliyorsa (admin, API kullanıcısı) oturumla açılır. Göremiyorsa (onaylayıcı) anahtar istenir.
      if (oturum.kullanici && (oturum.kullanici.rol === 'admin' || oturum.kullanici.rol === 'api')) {
        swaggerAc((istek) => istek);
        return;
      }
      const kayitli = oku();
      if (kayitli) anahtarla(kayitli);
      else anahtarIste();
    });
}

// Önce doküman herkese açık mı diye bakılır (çerezsiz, anahtarsız istek).
fetch(TANIM, { credentials: 'omit' })
  .then((cevap) => {
    if (!cevap.ok) return kapaliDokuman();
    swaggerAc((istek) => istek);
    return null;
  })
  .catch(() => anahtarIste('Sunucuya ulaşılamadı. Sayfayı yenileyin.'));

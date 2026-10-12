// Mevzuat Takip API'sine imzalı istek, Node.js 18 ve üstü, ek paket gerekmez.
// Çalıştırma, MEVZUAT_ADRES=https://mevzuat.firma.com.tr MEVZUAT_ANAHTAR=mvz_... node imza.mjs
// Bu kod sunucuda çalışır. Anahtar tarayıcıdaki koda konmaz.
import { createHash, createHmac, randomBytes } from 'node:crypto';

// İsteği imzalar ve gönderir. Anahtarın kendisi gönderilmez, sadece ilk 10 karakteri.
export async function istek(adres, anahtar, yontem, yol, veri) {
  const govde = veri === undefined ? '' : JSON.stringify(veri);
  const parca = new URL(yol, adres);
  const zaman = String(Math.floor(Date.now() / 1000));
  const numara = randomBytes(16).toString('hex');
  const govdeOzeti = createHash('sha256').update(govde, 'utf8').digest('hex');
  const metin = [yontem, parca.pathname, parca.search.slice(1), zaman, numara, govdeOzeti].join('\n');
  const imza = createHmac('sha256', anahtar).update(metin, 'utf8').digest('hex');
  const basliklar = { 'X-Anahtar': anahtar.slice(0, 10), 'X-Zaman': zaman, 'X-Numara': numara, 'X-Imza': imza,
    Accept: 'application/json' };
  if (veri !== undefined) basliklar['Content-Type'] = 'application/json';
  const cevap = await fetch(parca, { method: yontem, headers: basliklar, body: veri === undefined ? undefined : govde });
  if (!cevap.ok) throw new Error(`${cevap.status} ${await cevap.text()}`);
  return cevap.json();
}

const adres = process.env.MEVZUAT_ADRES;
const anahtar = process.env.MEVZUAT_ANAHTAR;
const liste = await istek(adres, anahtar, 'GET', '/api/v1/mevzuat?sadece_ilgili=false&adet=3');
console.log('Node.js, mevzuat:', liste.toplam);
const onizleme = await istek(adres, anahtar, 'POST', '/api/v1/konular/onizleme',
  { ad: 'Deneme', is_kollari: ['Ortak'], kelimeler: ['vergi'], haric: [], dislanan: [] });
console.log('Node.js, konu önizleme:', onizleme.taranan);

# Mevzuat Takip API — portala bağlantı

Bu sayfa şirket portalını Mevzuat Takip'e bağlayacak ekip içindir. Bütün adresler, alanlar ve cevap örnekleri
dokümanda: `https://mevzuat.firma.com.tr/api/dokuman` (adresi kendi kurulumunuzla değiştirin).

## Size verilenler

| Ne | Nereden |
|---|---|
| Sunucu adresi | Bilgi işlem, ör. `https://mevzuat.firma.com.tr` |
| API anahtarı (`mvz_...`) | Sistem yöneticisi panelden ya da `api-anahtari-uret` komutuyla üretir, bir kez gösterilir |
| Doküman (Swagger) | `/api/dokuman`. Aynı sayfadan HTML ya da OpenAPI JSON olarak indirilir |
| Hazır imza kodu | `deploy/imza-ornekleri/`: C# (`Imza.cs`), Java (`Imza.java`), Node.js (`imza.mjs`), Python (`imza.py`) |

## Kurallar

- **Anahtar portalın sunucusunda durur**, tarayıcıdaki koda (JavaScript) konmaz. İstekler portalın sunucusundan atılır.
- **Anahtar istekte gönderilmez.** Her istek anahtarla imzalanır, sunucu imzayı kontrol eder.
- Her istekte **yeni bir `X-Numara`** üretilir. Aynı numara ikinci kez reddedilir. Kopyalanan istek bu yüzden çalışmaz.
- Portal sunucusunun **saati doğru** olmalı (NTP). 5 dakikadan fazla kayan istek reddedilir.
- Bir anahtarla **dakikada en fazla 300 istek**. Fazlası 429 alır.
- Adresler tarayıcıda yeni sekmede açılmaz (401). Sadece doküman açılır.

## İmza

Her isteğe dört başlık eklenir:

| Başlık | Değer |
|---|---|
| `X-Anahtar` | Anahtarın ilk 10 karakteri |
| `X-Zaman` | Şimdiki zaman, Unix saniyesi |
| `X-Numara` | Her istekte yeni, 16-64 karakter rastgele değer (harf, rakam, `-`, `_`) |
| `X-Imza` | Aşağıdaki metnin anahtarla HMAC-SHA256 özeti, küçük harf hex |

İmzalanan metin altı satırdır, aralarında `\n` var:

```
GET                                   yöntem
/api/mevzuat                          adres, gönderildiği gibi
q=altin&adet=10                       sorgu, gönderildiği gibi, yoksa boş satır
1791875000                            X-Zaman
3f9c2a7d0b1e4c5a8d6f7e9b0a1c2d3e      X-Numara
e3b0c44298fc1c149afbf4c8996fb924...   gövdenin SHA-256 özeti, gövde yoksa boş metnin özeti
```

Bunu yapan fonksiyon örnek dosyalarda hazır, kendi kodunuza kopyalamanız yeterli. Örnek Python ile:

```python
from imza import istek
sonuc = istek("https://mevzuat.firma.com.tr", ANAHTAR, "GET", "/api/mevzuat?sadece_ilgili=true&adet=20")
```

Komut satırından tek istek denemek için: `python imza.py GET /api/kaynaklar` (`MEVZUAT_ADRES` ve `MEVZUAT_ANAHTAR`
ortam değişkenleriyle).

## Portalın çoğunlukla kullanacağı adresler

| İstek | Ne verir |
|---|---|
| `GET /api/mevzuat` | Mevzuat listesi, en yeni önce. `q` (başlıkta arama), `sadece_ilgili`, `baslangic`, `bitis`, `kaynak`, `konu`, `sayfa`, `adet` (en fazla 100) |
| `GET /api/mevzuat/{id}` | Tek kaydın ayrıntısı, metni ve eşleşen kelimeler |
| `GET /api/konular` | Konular, iş kolları, anahtar kelimeler |
| `GET /api/kaynaklar` | Taranan siteler ve son tarama zamanları |
| `GET /api/raporlar?durum=ONAY_BEKLIYOR` | Onay bekleyen raporlar |
| `GET /api/raporlar/{id}` | Raporun kalemleri |
| `POST /api/raporlar/{id}/karar` | Onay ya da ret. Onayda seçilen kalemler alıcı gruplarına mail olarak gider |
| `POST /api/tarama`, `GET /api/tarama/durum` | Şimdi tara ve durumunu izle |

Kaynak, konu, alıcı grubu, kullanıcı ve ayar işlemleri de `/api` altında, hepsi dokümanda.

Ne yapılabileceğini anahtarın rolü belirler. **Onaylayıcı** rapor onaylar ama ayarlara giremez, **Yönetici** ayarları
yönetir ama rapor onaylayamaz, **Tam yetki** her şeyi yapar (test ve entegrasyon için).

## Hata cevapları

Hepsi `{"detail": "Türkçe açıklama"}` biçimindedir.

| Kod | Ne zaman |
|---|---|
| 400 | İstek iş kuralına uymuyor (kalem seçilmemiş, adres bozuk, sebep yazılmamış) |
| 401 | İmza yok ya da tutmuyor, numara tekrar kullanılmış, saat kaymış, anahtar süresi dolmuş ya da iptal |
| 403 | Anahtarın rolü bu işi yapamaz |
| 404 | Kayıt ya da adres yok |
| 409 | Kayıt bu arada değişti (`surum` eski) ya da iş zaten yapıldı |
| 422 | Alan eksik ya da biçimi yanlış |
| 429 | Dakikada 300'den fazla istek |

401 alındığında sebebi `detail` alanında yazar. Her istek sistemin denetim kaydına düşer, sorun olursa isteğin zamanı
bildirilirse sunucu tarafında bulunur.

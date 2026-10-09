# API test rehberi

Mevzuat Takip API'sini baştan sona denemek için adım adım senaryolar. Her adımda adres, gönderilecek gövde ve
beklenen sonuç var. Adımlar sırayla yapılırsa bir öncekinin çıktısı (rapor no, kalem no, grup no) bir sonrakinde
kullanılır.

Örneklerde sunucu `https://mevzuat.firma.com.tr` yazıyor, kendi adresinizle değiştirin. Bütün adresler, gövdeler ve
cevap örnekleri dokümanda da var: `https://mevzuat.firma.com.tr/api/dokuman`.

## 0. Hazırlık

### Anahtar almak

Sistem yöneticisi test için **Tam yetki** rolünde, süreli bir anahtar üretir. İki yol var, sonuç aynı:

- Panel: Bilgi İşlem hesabıyla giriş → **API anahtarları** → ad "Test ekibi", rol "Tam yetki", süre 7 gün → **Üret**.
- Sunucuda komutla (panel kullanılmıyorsa):

  ```sh
  # Docker'lı kurulum
  docker compose exec panel python -m mevzuat.cli api-anahtari-uret --ad "Test ekibi" --rol tam --gun 7
  # Docker'sız kurulum (KURULUM-DOCKERSIZ.md'deki $M)
  $M api-anahtari-uret --ad "Test ekibi" --rol tam --gun 7
  ```

Anahtar `mvz_` ile başlar ve **sadece bir kez** gösterilir, hemen kopyalayın. Kaybolursa yenisi üretilir. Rol
denemeleri (bölüm 9) için ayrıca bir **Onaylayıcı** ve bir **Yönetici** anahtarı üretin.

### İstek biçimi

Her istekte anahtar başlıkta gider, gövde JSON'dur:

```
Authorization: Bearer mvz_xxxxxxxxxxxxxxxx
Content-Type: application/json
```

Aşağıdaki curl örnekleri için bir kez şunu yazın:

```sh
A="https://mevzuat.firma.com.tr"
K="Authorization: Bearer mvz_xxxxxxxxxxxxxxxx"
```

### Swagger'dan denemek

1. `/api/dokuman` adresini açın, Swagger doğrudan gelir.
2. Sağ üstteki **Authorize**'a anahtarı girin (`mvz_...`, başına "Bearer" yazmadan) → **Authorize** → **Close**.
3. Bir adresi açın → **Try it out** → **Execute**.
4. **Authorize → Logout** yapınca istekler anahtarsız gider ve **401** alır. Bu doğru davranıştır (bölüm 9'da deneniyor).

Postman ya da başka bir araç kullanılacaksa dokümanın **OpenAPI JSON** dosyası (`/api/dokuman/indir?bicim=json`)
içe aktarılabilir.

## 1. Bağlantı ve anahtar

| # | İstek | Beklenen |
|---|---|---|
| 1.1 | `GET /api/v1/kaynaklar` (anahtarla) | 200, taranan kaynakların listesi |
| 1.2 | Aynı istek, başlıksız | 401 `{"detail": "Giriş yapmanız gerekiyor."}` |
| 1.3 | Aynı istek, anahtarın son harfi değiştirilmiş | 401 "API anahtarı geçersiz, süresi dolmuş ya da iptal edilmiş." |

```sh
curl -s -H "$K" "$A/api/v1/kaynaklar"
curl -s -o /dev/null -w "%{http_code}\n" "$A/api/v1/kaynaklar"     # 401
```

## 2. Mevzuat arama ve detay

| # | İstek | Beklenen |
|---|---|---|
| 2.1 | `GET /api/v1/mevzuat` | 200, `toplam`, `sayfa`, `adet`, `kayitlar` (en yeni önce) |
| 2.2 | `GET /api/v1/mevzuat?q=altın&adet=10` | Başlığında "altın" geçenler. Büyük/küçük harf ve Türkçe harf fark etmez ("ALTIN" da aynı sonucu verir) |
| 2.3 | `GET /api/v1/mevzuat?sadece_ilgili=true&baslangic=2026-10-01&bitis=2026-10-08` | Sadece şirketi ilgilendiren, bu tarihler arasındakiler |
| 2.4 | `GET /api/v1/mevzuat?kaynak=resmi_gazete` | Sadece o kaynağın kayıtları (ad `/api/v1/kaynaklar`'dan) |
| 2.5 | `GET /api/v1/mevzuat?konu=Vergi` | O konuya uyanlar (konu adları `/api/v1/konular`'dan) |
| 2.6 | `GET /api/v1/mevzuat?adet=101` | 422, adet en fazla 100 |
| 2.7 | `GET /api/v1/mevzuat?sayfa=2&adet=5` | 6.–10. kayıtlar, `toplam` 2.1'dekiyle aynı |
| 2.8 | `GET /api/v1/mevzuat/{id}` (2.1'den bir `id`) | 200, özet alanları + metin, eşleşen kelimeler |
| 2.9 | `GET /api/v1/mevzuat/999999` | 404 |

```sh
curl -s -G -H "$K" "$A/api/v1/mevzuat" --data-urlencode "q=altın" -d adet=10
```

Kontrol: her kaydın `url`'si resmî kaynağa gider; rapora girmiş kayıtta `rapor` alanı dolu (`id`, `durum`).

## 3. Kategoriler

| # | İstek | Beklenen |
|---|---|---|
| 3.1 | `GET /api/v1/konular` | 200, konular, iş kolları, anahtar kelimeler |
| 3.2 | `GET /api/v1/kaynaklar` | 200, her kaynağın adı, son başarılı tarama zamanı, kayıt sayısı |

## 4. Raporlar ve onay

Raporlar her taramadan sonra oluşur ve onay bekler. Onaylanınca alıcı gruplarına mail gider.

| # | İstek | Beklenen |
|---|---|---|
| 4.1 | `GET /api/v1/raporlar?durum=ONAY_BEKLIYOR` | Onay bekleyen raporlar. Rapor `id`'sini not edin |
| 4.2 | `GET /api/v1/raporlar/{rapor_id}` | `kalemler` listesi. Göndermek istediğiniz kalemlerin `id`'sini not edin |
| 4.3 | `GET /api/v1/gruplar` | Alıcı grupları. Grup `id`'lerini not edin |
| 4.4 | `POST /api/v1/raporlar/{rapor_id}/karar` (aşağıdaki gövde) | 200 `{"tur": "basari", "mesaj": "Onaylandı ve N adrese gönderildi."}` |
| 4.5 | `GET /api/v1/raporlar/{rapor_id}` | `durum` `GONDERILDI`, seçilmeyen kalemlerde `gonderildi: false`, `karar_notu` dolu |
| 4.6 | 4.4'ü aynen tekrar | 409, zaten karar verilmiş |

Onay gövdesi (`dahil` kalem id'leri, `gruplar` grup id'leri, `ek_adresler` gruplarda olmayan kişiler):

```json
{"karar": "onayla", "dahil": [101, 102], "notu": "Vergi kalemine dikkat.", "gruplar": [1], "ek_adresler": ["test@firma.com.tr"]}
```

```sh
curl -s -X POST -H "$K" -H "Content-Type: application/json" "$A/api/v1/raporlar/12/karar" \
  -d '{"karar":"onayla","dahil":[101,102],"notu":"Deneme","gruplar":[1],"ek_adresler":["test@firma.com.tr"]}'
```

Kontrol: `ek_adresler`'e yazdığınız adrese mail geldi mi, mailde her kalemin resmî linki ve not var mı, gönderen adı
doğru mu (Ayarlar → gönderen adı).

Hatalı onaylar (başka bir ONAY_BEKLIYOR raporla):

| # | Gövde | Beklenen |
|---|---|---|
| 4.7 | `{"karar": "onayla", "dahil": []}` | 400, en az bir kalem seçilmeli |
| 4.8 | `{"karar": "reddet", "notu": ""}` | 400, reddederken sebep zorunlu |
| 4.9 | `{"karar": "onayla", "dahil": [101], "gruplar": [], "ek_adresler": []}` | 400, kalemler hiç kimseye gitmiyor |
| 4.10 | `{"karar": "onayla", "dahil": [101], "ek_adresler": ["bozuk-adres"]}` | 400, geçersiz e-posta |
| 4.11 | `{"karar": "reddet", "notu": "Kapsam dışı."}` | 200, rapor `REDDEDILDI`, mail gitmez |

### Ek gönderim

Onaylanmış rapordaki bir kalemi sonradan başka kişilere göndermek için:

| # | İstek | Beklenen |
|---|---|---|
| 4.12 | `POST /api/v1/raporlar/{rapor_id}/ek-gonderim` gövde `{"dahil": [103], "notu": "Size de gelsin.", "gruplar": [2], "ek_adresler": []}` | 200 "Ek gönderim hazırlandı ve N adrese gönderildi." |
| 4.13 | Aynı istek, reddedilmiş ya da onay bekleyen raporla | 409 |
| 4.14 | `GET /api/denetim?islem=ek_gonderim` (Tam yetki/Yönetici) | Ek gönderim kaydı, kimin yaptığı "API: Test ekibi" |

## 5. Tarama

| # | İstek | Beklenen |
|---|---|---|
| 5.1 | `GET /api/v1/zamanlama` | Tarama saatleri, `zamanlayici_calisiyor: true`, sonraki tarama zamanı |
| 5.2 | `POST /api/v1/tarama` (gövdesiz) | 200, `istek.durum` `BEKLIYOR` |
| 5.3 | 5.2'yi hemen tekrar | 409, tarama zaten sırada |
| 5.4 | `GET /api/v1/tarama/durum` (30 sn arayla) | `BEKLIYOR` → `CALISIYOR` → `BITTI` (hata olursa `HATALI` ve `hata` alanı). `son_calismalar[0]` yeni kayıt sayısını verir |
| 5.5 | `PUT /api/v1/zamanlama` gövde `{"saatler": ["06:30", "18:00"], "surum": <5.1'deki surum>}` | 200, yeni saatler |
| 5.6 | 5.5'i eski `surum` ile tekrar | 409, kayıt bu arada değişti, önce yeniden okuyun |

Not: 5.1'de `zamanlayici_calisiyor: false` ise zamanlayıcı servisi kapalıdır ve 5.2 409 döner. Bu sunucu tarafı
bir sorundur, sistem yöneticisine bildirin.

## 6. Kaynaklar ve konular

| # | İstek | Beklenen |
|---|---|---|
| 6.1 | `GET /api/v1/kaynak-tipleri` | Eklenebilecek kaynak tipleri ve alanları |
| 6.2 | `POST /api/v1/kaynaklar/bul` gövde `{"adres": "https://www.tcmb.gov.tr"}` | Sitede bulunan RSS/liste önerileri |
| 6.3 | `POST /api/v1/kaynaklar/dene` (aşağıdaki gövde) | Kaydetmeden çekilen örnek kayıtlar |
| 6.4 | `POST /api/v1/kaynaklar` (aynı gövde) | 200, yeni kaynak, `surum: 1` |
| 6.5 | `PUT /api/v1/kaynaklar/{ad}` gövdeye `"surum": 1` ekli, etiket değişik | 200, `surum: 2` |
| 6.6 | `POST /api/v1/kaynaklar/{ad}/kaldir` gövde `{"surum": 2}` | 200, `kaldirildi: true` |
| 6.7 | `POST /api/v1/kaynaklar/{ad}/geri-getir` gövde `{"surum": 3}` | 200, kaynak geri geldi |
| 6.8 | `GET /api/kaynaklar/{ad}/gecmis` | Yapılan her değişiklik, kim yaptı |

```json
{"tip": "rss", "etiket": "TCMB Basın Duyuruları", "ayarlar": {"akis_url": "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Bottom+Menu/Diger/RSS/Basin+Duyurulari"}, "varsayilan_konular": [], "aktif": true}
```

| # | İstek | Beklenen |
|---|---|---|
| 6.9 | `POST /api/v1/konular/onizleme` (aşağıdaki gövde) | Son 90 günde bu kelimelere uyacak başlıklar, kaydetmez |
| 6.10 | `POST /api/v1/konular` (aynı gövde) | 200, yeni konu |
| 6.11 | `PUT /api/v1/konular/{konu_id}` kelime eklenmiş, `surum` ile | 200 |
| 6.12 | `POST /api/v1/konular/{konu_id}/pasif` gövde `{"surum": ...}` | 200, `aktif: false`. Güncel metni takip edilen bir mevzuatın kullandığı konu pasifleşmez (400) |
| 6.13 | `POST /api/konular/{konu_id}/geri-al` gövde `{"denetim_id": <gecmis'ten>, "surum": ...}` | 200, konu o anki haline döner |

```json
{"ad": "API deneme konusu", "is_kollari": ["Ortak"], "kelimeler": ["deneme kelimesi"], "haric": [], "dislanan": [], "aciklama": "Test için, sonra pasifleştirilir."}
```

## 7. Alıcı grupları

| # | İstek | Beklenen |
|---|---|---|
| 7.1 | `POST /api/v1/gruplar` gövde `{"ad": "Test Grubu", "is_kollari": ["Kuyum"], "adresler": ["test@firma.com.tr"], "aktif": true}` | 200, yeni grup |
| 7.2 | `PUT /api/v1/gruplar/{grup_id}` adres eklenmiş | 200 |
| 7.3 | `POST /api/v1/gruplar` `"adresler": ["bozuk"]` | 400, geçersiz adres |
| 7.4 | `GET /api/v1/gruplar` | `kapsanmayan`: hiçbir grubun almadığı iş kolları |

## 8. Ayarlar, kullanıcılar, denetim

| # | İstek | Beklenen |
|---|---|---|
| 8.1 | `GET /api/ayarlar` | Her ayarın değeri ve nereden geldiği (`kaynak`: panel, .env, varsayılan). SMTP parolası görünmez |
| 8.2 | `PUT /api/ayarlar` gövde `{"degisiklikler": {"smtp_gonderen_adi": "Uyum Birimi"}, "surum": <8.1'deki>}` | 200, "1 ayar kaydedildi." Yeniden başlatma gerekmez |
| 8.3 | `PUT /api/ayarlar` gövde `{"degisiklikler": {"smtp_gonderen_adi": null}, "surum": ...}` | Ayar .env'deki değere döner |
| 8.4 | `POST /api/ayarlar/mail-dene` gövde `{"adres": "test@firma.com.tr"}` | 200, deneme maili gelir. SMTP hatalıysa 400 ve sebebi |
| 8.5 | `GET /api/kullanicilar` | Panel kullanıcıları (API anahtarları listede yok) |
| 8.6 | `POST /api/kullanicilar` gövde `{"eposta": "yeni@firma.com.tr", "ad": "Ad Soyad", "rol": "onaylayici"}` | 200, davet maili gider (panel adresi ayarlı değilse kişi eklenir ama mesaj davetin gitmediğini söyler) |
| 8.7 | `POST /api/kullanicilar/{id}/aktiflik` gövde `{"aktif": false}` | 200, kişi pasif, giriş yapamaz |
| 8.8 | `GET /api/denetim` | Son işlemler. Anahtarla yapılan her istek `api_istegi` olarak, yapan "API: Test ekibi" |

## 9. Yetki ve güvenlik

Her rolün yapabildikleri:

| İşlem | Tam yetki | Onaylayıcı | Yönetici |
|---|---|---|---|
| Mevzuat, konu, kaynak, rapor okuma (`/api/v1`) | ✓ | ✓ | ✓ |
| Rapor onay/ret, ek gönderim | ✓ | ✓ | 403 |
| Tarama, kaynak, konu, alıcı grubu | ✓ | ✓ | ✓ |
| Ayarlar, kullanıcılar, denetim kaydı | ✓ | 403 | ✓ |

| # | İstek | Beklenen |
|---|---|---|
| 9.1 | Onaylayıcı anahtarıyla `GET /api/ayarlar` | 403 |
| 9.2 | Yönetici anahtarıyla `POST /api/v1/raporlar/{id}/karar` | 403, denetimde `yetkisiz_karar_denemesi` |
| 9.3 | Swagger'da Authorize → Logout, sonra herhangi bir istek | 401 |
| 9.4 | Panelde anahtarı **İptal et**, sonra aynı anahtarla istek | 401 |
| 9.5 | Süresi geçmiş anahtarla istek | 401 |
| 9.6 | API anahtarıyla `GET /api/api-anahtarlari` | 403 "API anahtarları sadece panelden yönetilir." |

## Hata cevapları

Hepsi `{"detail": "Türkçe açıklama"}` biçimindedir.

| Kod | Ne zaman |
|---|---|
| 400 | İstek iş kuralına uymuyor (kalem seçilmemiş, adres bozuk, sebep yazılmamış) |
| 401 | Anahtar yok, yanlış, süresi dolmuş ya da iptal |
| 403 | Anahtarın rolü bu işi yapamaz |
| 404 | Kayıt yok |
| 409 | Kayıt bu arada değişti (`surum` eski) ya da iş zaten yapıldı |
| 422 | Alan eksik ya da biçimi yanlış (ör. `adet=101`, tarih biçimi) |

## Sonuç bildirme

Her adım için: adım no, istek (adres + gövde), gelen kod ve cevap, beklenenden farkı. Sorun olan adımlarda denetim
kaydındaki (`GET /api/denetim`) ilgili satırın zamanı da yazılırsa sunucu tarafında bulunması kolaylaşır.

# Mevzuat Takip Ajanı

Resmî kaynaklarda yayımlanan mevzuatı ve duyuruları her gün otomatik toplayan, firmayı ilgilendirenleri seçen,
belgelerin içeriğini okuyup her kalemin altında resmî kaynak linki olan bir rapor hazırlayan ve **bir sorumlunun onayından sonra** raporu ilgili ekiplere
mail ile dağıtan sistem.

Ayrıca mevzuat.gov.tr'de **yürürlükteki bütün mevzuatın sürümlü arşivini** tutar (19 bini aşkın kanun, yönetmelik, tebliğ,
CB kararı …; metin, ekler, PDF'ler, taranmış sayfalar): değişen mevzuat yeni sürüm olur, önceki sürümle madde madde
karşılaştırılır, firmayı ilgilendiren değişiklik rapora girer.

Bir kuyumculuk firmasında staj projesi olarak geliştirildi (Ekim-Kasım 2026). İş kolları: **Kuyum, Döviz/Altın, Oto kiralama** ve hepsini ilgilendiren **Ortak**.

> **Durum:** Canlıda çalışıyor (Docker, Ubuntu 24.04, HTTPS). Arşivin tam taraması 25.11.2026'da bitti. 550 test geçiyor (Linux).

---

## Ne yapar

```text
Zamanlayıcı (varsayılan 06:30 + 18:00 Türkiye saati, panelden değişir) ya da panelden "Şimdi tara"
   │
   ▼
Kaynaklar ── Resmî Gazete (günlük fihrist) · MASAK (WordPress API) · GİB (sitenin JSON API'si)
   │         · Mevzuat Bilgi Sistemi (yeni eklenen mevzuat)
   │         panelden eklenenler (düz HTML / RSS, tip adresten otomatik bulunur)
   │         + mevzuat.gov.tr "bugün güncellenenler" → ARŞİV (yeni sürüm, v1↔v2, v2↔v3 madde madde fark)
   ▼
İçerik: HTML · PDF · taranmış PDF için OCR (Tesseract, Türkçe)
   ▼
Konu filtresi: başlık + içerik puanı (bölüm ağırlıklı, Türkçe ek kuralı), arşivle aynı kural
   ▼
Rapor: mevzuat hakkında (amaç, kapsam, dayanak), madde özeti, tablolar, yürürlük tarihi, "neden size geldi",
       değişen mevzuatta eski/yeni işaretli + tam karşılaştırma bağlantısı, orijinal PDF ekte
   ▼
ONAY BEKLİYOR → onaylayıcıya mail → panelde incele, kalem çıkar, not yaz → onayla / reddet
   ▼
Alıcı grupları (iş koluna göre) → her adrese ayrı mail, bir mail en fazla bir kez
   ▼
Sağlık kontrolü → sorun varsa yöneticiye UYARI, her pazartesi NABIZ maili
```

### Öne çıkanlar

- **Kaçırma yok:** her kaynak kendi kaldığı yerden (checkpoint) devam eder. Sunucu kapalı kaldıysa kaçan günler telafi edilir, kayıt çoğalmaz.
- **Bir kaynak çökerse diğerleri devam eder.** Geçici hatalarda 3 deneme (2-4-8 sn).
- **İnsan onayı zorunlu:** sistem kendiliğinden kimseye rapor göndermez.
- **Doğru kişiye doğru kalem:** her grup sadece kendi iş kollarının kalemlerini alır. Birden çok gruptaki kişi tek mail alır.
- **Kod yazmadan genişletme:** kaynak, konu, anahtar kelime, iş kolu, alıcı grubu ve tarama saatleri panelden yönetilir.
  Yeni kaynak eklerken sadece adres girilir, sistem tipi (WordPress / RSS / düz HTML) ve seçicileri kendisi bulur.
  Konu değiştirmeden önce son 90 günde neyin eklenip düşeceği gösterilir.
- **Güncel metin takibi:** mevzuat.gov.tr'deki metin değişince eski/yeni farkı madde madde onaya gelir. Takipte olmayan
  mevzuatın değişikliği de, değişen maddeleri bir konuya uyuyorsa rapora girer. Sadece okuma iyileşmesi (okuma düzeltmesi),
  boşluk ve dipnot numarası kayması değişiklik sayılmaz.
- **Tek filtre:** günlük tarama, arşiv ve değişen mevzuat aynı puanı kullanır (başlık, amaç/kapsam, tanımlar, gövde ayrı
  ağırlıklı; "altın" → "altının" evet, "Altınbaş" hayır). Üniversitelerin iç yönetmelikleri hiçbir konuya girmez.
- **Yapay zekâ yok, uydurma yok:** özet ve "mevzuat hakkında" metnin kendi cümleleridir. OCR metni dayanak sayılmaz, orijinal PDF her zaman ekte.

### Arşiv

- **Kapsam:** mevzuat.gov.tr'nin listelediği bütün türler (kanun, KHK, tüzük, CB kararnamesi, yönetmelik, tebliğ, CB kararı,
  CB genelgesi, mülga). Metin HTML yapısıyla; Word/Excel/PowerPoint/RTF ekleri ve zip/rar içleri okunur (LibreOffice);
  PDF'ler sayfa sayfa, taranmışsa OCR; harita ve tablo sayfalarının görüntüsü saklanır; sitenin PDF'i ayrıca saklanır.
- **Sürüm:** değişen metin v2, v3 olur, eski sürüm silinmez. Kaynak aynı kalıp okuma iyileştiyse "okuma düzeltmesi" (rapora girmez).
- **Panel:** adında ve metninde arama (Türkçe ek kuralıyla), konu, tür, durum ve sürüm süzgeci (veriden: v1, v2 …; son
  7/30/90 günde değişen); mevzuat penceresinde Metin / Orijinal PDF / Değişiklikler (iki sürüm seçilir; tam metin,
  sadece değişenler ya da yan yana) / Ekler.
- **Güncel tutma:** günlük taramada "bugün güncellenenler" ve takipteki mevzuat; haftada bir gece liste yenileme (yeni
  mevzuat eklenir, siteden kalkan işaretlenir). Panelden "yeniden çek" (hepsini baştan) ayrıca EVET onayı ister.
- **Denetim:** `arsiv-degisim --gun 7` son değişiklikleri yeni RG / Anayasa Mahkemesi notuna göre "doğrulandı" ya da
  "elle bak" diye listeler; `arsiv-kontrol` teslim kontrolü (eksik dosya, okunamayan sayfa, kusurlu kayıt).
- **Rapordan karşılaştırma:** mailde "Değişikliklerin tamamını gör" bağlantısı girişsiz açılır, MEVZUAT_GIZLI_ANAHTAR ile
  imzalıdır (adres değiştirilirse açılmaz).
- **Taşınabilir:** dosya yolları arşiv klasörüne göreli (`MEVZUAT_ARSIV_KLASORU`). Teslim paketi: arşiv tablolarının
  `pg_dump`'ı + `arsiv/` klasörü + `KURULUM.md` (boş kuruluma geri yükleme adım adım) + SHA256 özetleri.

## Ekran ve roller

| Rol | Ne yapar |
|---|---|
| **Onaylayıcı** | Raporları onaylar/reddeder, alıcı gruplarını, kaynakları, konuları yönetir, "Şimdi tara" der |
| **Admin** | Kurtarma rolü: kullanıcı davet eder, parola linki gönderir, yanlış değişikliği geçmişten geri alır, ayarları ve API anahtarlarını yönetir. **Rapor onaylayamaz** (görev ayrılığı) |
| **API kullanıcısı** | Dış geliştirici: panelde sadece API dokümanını görür, istek atmak için API anahtarı kullanır |

Panel sayfaları: Raporlar (onay) · Alıcı grupları · Kaynaklar · Konular · Tarama · Arşiv · Kullanıcılar · Denetim kaydı · Ayarlar · API anahtarları.
Sol menü veritabanından gelir (`menu_ogeleri`), her kullanıcı yetkisine uyan öğeleri görür.

**API:** Panelde yapılabilen her şey API'den de yapılır, şirket portalı bizim arayüz yerine bunu kullanabilir.
Dış sistemlerin bütün adresleri `/api` altındadır (panelin kendi arayüzü `/panel/api` kullanır): mevzuat, raporlar ve onay, konular, kaynaklar, alıcı grupları, tarama, kullanıcılar, ayarlar, denetim kaydı. Doküman (Swagger) `/api/dokuman` adresinde, doğrudan açılır (`MEVZUAT_DOKUMAN_ACIK=0` ise anahtar ister), HTML ya
da OpenAPI JSON olarak da indirilir. Anahtar panelden ya da `api-anahtari-uret` komutuyla üretilir.
**Anahtarın kendisi gönderilmez**, her istek anahtarla imzalanır (HMAC-SHA256, `X-Anahtar`, `X-Zaman`, `X-Numara`, `X-Imza`).
Kopyalanan istek (aynı numara), değiştirilen istek (imza tutmaz) ve 5 dakikadan eski istek reddedilir, anahtar başına dakikada
en fazla 300 istek. Python, Node.js, C# ve Java örnekleri: [deploy/imza-ornekleri](deploy/imza-ornekleri).
Portal ekibine verilecek tek sayfalık bağlantı belgesi: [deploy/PORTAL-BAGLANTI.md](deploy/PORTAL-BAGLANTI.md).
API adresleri tarayıcıda yeni sekmede açılmaz, panel oturumu açık olsa da. `MEVZUAT_API_IMZA=0` sadece deneme içindir,
o zaman anahtar `Authorization: Bearer` ile gönderilir. Anahtar seçilen rolün (tam yetki, admin ya da onaylayıcı) yetkisiyle çalışır,
süreli ya da süresiz olur, iptal edilebilir, her isteği denetim kaydına yazılır, kendisi değil özeti saklanır.
Her adresin gövdesi, cevabı ve hata kodları dokümanda (`/api/dokuman`).

**Ayarlar:** Mail sunucusu, panel adresi, uyarı adresleri, PDF eki, MFA zorunluluğu ve saklama süreleri panelden değişir,
yeniden başlatma gerekmez. Panelde girilen değer `.env`'in önüne geçer. Mail şifresi veritabanında şifreli saklanır.

## Teknoloji

| Katman | Kullanılan |
|---|---|
| Dil, paket yönetimi | Python 3.12, uv |
| Çekme, ayrıştırma | httpx, selectolax (Playwright sadece isteğe bağlı yedek tip) |
| PDF, OCR | pdfplumber, pypdfium2, Tesseract (Türkçe) |
| Ekler | LibreOffice (doc/xls/ppt/rtf/odt), python-docx/openpyxl, 7-Zip / bsdtar (rar, 7z), zip |
| Veritabanı | PostgreSQL 16 (sunucu, arama için GIN tam metin dizini), SQLite (geliştirme), SQLAlchemy 2, Alembic (22 migration) |
| Fark | difflib (madde ve kelime bazında) |
| Mail | smtplib, Jinja2 (HTML + düz metin) |
| Backend | FastAPI (JSON API), imzalı HttpOnly çerez oturumu |
| Panel | React, Vite, TypeScript, Tailwind 4 (derlenmiş `dist` depoda, sunucuda Node gerekmez) |
| Kimlik | Argon2, TOTP iki adımlı doğrulama, CSRF, hesap kilidi |
| Dağıtım | Docker Compose (db, panel, zamanlayıcı, yedek) |
| Test | pytest, gerçek sitelerden kaydedilmiş sayfalar (internetsiz) |

## Güvenlik

- Kayıt olma yok. İlk admin komut satırından, diğerleri panelden davet linkiyle (admin parola görmez).
- Argon2 parola özeti (en az 12 karakter), 5 hatalı girişte 15 dk kilit, TOTP iki adımlı doğrulama.
- Oturum çerezi HttpOnly + SameSite=Strict + Secure, CSRF başlığı, CSP ve güvenlik başlıkları.
- Görev ayrılığı: onay/ret sadece onaylayıcıda. Her önemli işlem denetim kaydında (kim, ne zaman, IP, önce/sonra).
- Panelden kaynak eklerken SSRF koruması: sadece https, iç ağ adresi yasak, IP sabitleme, indirme boyutu sınırı.
- Mail ve panel çıktılarında kaçış (XSS). Panel sadece 127.0.0.1'de, dışarıya HTTPS'li ters vekil ile açılır.
- Pasif kalan hesabın kişisel bilgileri belli gün sonra silinir (`MEVZUAT_PASIF_SILME_GUN`, varsayılan 30).
- Gönderilmiş ya da reddedilmiş raporlar ve denetim kaydı belli gün sonra silinir (`MEVZUAT_SAKLAMA_GUN`, varsayılan 30).
  Onay bekleyen raporlara dokunulmaz.
- Sırlar `.env`'de, depoda sadece `.env.example`.

## Kurulum (Docker)

Sunucuda Docker yoksa: [`deploy/KURULUM-DOCKERSIZ.md`](deploy/KURULUM-DOCKERSIZ.md) (PostgreSQL + uv + systemd,
temiz Debian 12'de `deploy/prova-dockersiz.sh` ile adım adım denenir).

```sh
cp .env.example .env
# .env: MEVZUAT_DB_SIFRE, MEVZUAT_GIZLI_ANAHTAR, SMTP ayarları, MEVZUAT_PANEL_ADRESI
docker compose up -d --build
docker compose exec panel python -m mevzuat.cli kullanici-ekle --eposta admin@firma.com.tr --ad "Ad Soyad" --rol admin
```

Ayrıntı: [`deploy/DOCKER.md`](deploy/DOCKER.md).

Testler Linux'ta: `docker build --target test .` (bir test bile kalırsa derleme durur).

## Geliştirme

```sh
uv sync
cp .env.example .env                     # SQLite ile çalışır, SMTP boşsa mailler giden_mailler/ klasörüne yazılır
uv run python -m pytest -q
uv run python -m mevzuat.cli panel       # API + panel
cd frontend && npm install && npm run dev  # arayüz geliştirme (5173, /api vekil). Değişince: npm run build
```

### Komutlar (`uv run python -m mevzuat.cli ...`)

| Komut | Ne yapar |
|---|---|
| `zamanlayici` | Sürekli çalışır, paneldeki saatlerde günlük işi başlatır |
| `gunluk` | Bir kez: tara, onaya sun, sağlık kontrolü |
| `dene <kaynak> --gun 30` | Kaynağı veritabanına yazmadan dener, eşleşenleri ★ ile gösterir |
| `kullanici-ekle`, `kullanici-pasif`, `mfa-sifirla` | Kullanıcı işlemleri |
| `mail-dene` | SMTP ayarını dener |
| `gonderim-durum --id N` | Durumu belirsiz kalan bir maili çözer |
| `ayar-disa-aktar`, `ayar-ice-aktar` | Kaynak ve konu tanımlarını dosyaya yazar / dosyadan uygular |
| `api-anahtari-uret`, `api-anahtari-listele`, `api-anahtari-iptal` | API anahtarı işlemleri (panel kullanılmıyorsa) |
| `calistir`, `onizle` | Sadece tara (mail yok) / bekleyen kayıtlardan rapor önizlemesi (dosyaya) |
| `izlenen` | Takipteki mevzuat, son kontrol, arşivdeki sürüm sayısı |
| `arsiv-kur` | Arşiv işi (panelden de başlar); `--anahtar ...` sadece verilen mevzuat |
| `arsiv-durum` | Tür başına kaç mevzuat |
| `arsiv-degisim --gun 7` | Son günlerin değişiklikleri: RG/AYM notuyla doğrulandı mı, rapor kaydı açıldı mı |
| `arsiv-kontrol [--sha]` | Teslim kontrolü (sadece okur), rapor `arsiv/kontrol/` altına |
| `arsiv-dizin`, `arsiv-filtre` | Arama dizinini kurar / konu filtresini arşivin tamamına uygular (konular değişince zamanlayıcı kendisi başlatır) |
| `filtre-olc` | Konu filtresini test setleriyle ölçer |

## Klasörler

```text
src/mevzuat/
  cli.py               komut satırı girişi
  gunluk.py            günlük iş: tara → onaya sun → sağlık
  pipeline.py          tarama motoru (checkpoint, kaynak başına yalıtım)
  sources/             kaynak tipleri: resmi_gazete, wordpress, gib, html, rss, tarayici
  filtre.py            Türkçe normalizasyon, konu ve iş kolu eşleştirme, ortak dışlama
  turkce_ek.py         Türkçe ek kuralı ("altın" → "altının" evet, "Altınbaş" hayır)
  puan.py              bölüm ağırlıklı konu puanı (günlük tarama ve arşiv aynı)
  icerik.py, ocr.py    HTML / PDF / OCR
  surum.py             güncel metin takibi: arşive çeker, madde farkı, değişen mevzuat kaydı
  arsiv.py             arşiv işi (liste, metin, haftalık), panel verisi
  arsiv_cekim.py       bir mevzuatın çekimi ve sürümlenmesi
  html_okuma.py, pdf_okuma.py, ek_okuma.py, metin_duzen.py   okuyucular (yapı, OCR, ekler, metin onarımı)
  arsiv_fark.py        iki sürümün madde ve kelime farkı
  arama.py             arşivde tam metin arama, konu filtresinin arşive uygulanması
  arsiv_kontrol.py     teslim kontrolü
  rapor.py, mail.py    rapor, onay akışı, dağıtım
  alicilar.py          alıcı grupları, kişi başı dağıtım planı
  kaynak_bulucu.py     adresten kaynak tipini ve seçicileri bulur
  web/                 FastAPI panel API ve güvenlik, portal.py dış sistem API'si (/api)
  panel_ayarlari.py    panelden değişen ayarlar (.env'in önüne geçer)
  migrations/          Alembic şema geçişleri
frontend/              React panel (dist derlenmiş hâli)
config/                ilk kurulum tohumu: kaynaklar, konular, izlenen mevzuat
deploy/                kurulum rehberleri (Docker ve Docker'sız), servis dosyaları, yedek betikleri, imza örnekleri
tests/                 testler ve gerçek sayfalardan alınmış örnekler
```

## Rakamlar

8 konu · 24 tablo · 22 migration · 550 test ·
90 günlük gerçek veride 716 Resmî Gazete başlığından 85'i ilgili · Resmî Gazete PDF'lerinin %82'si taranmış görüntü.

Arşiv (26.11.2026): 19.352 mevzuat (19.350'sinin metni; 3'ünü site vermiyor) · ~17 GB dosya (PDF 8,1 GB, sayfa görüntüleri
4,5 GB, ekler 3,5 GB) · 41.883 dosya · 3.993 mevzuatta 28.561 OCR sayfası · arama dizininde 362 bin madde ·
tam tarama sunucuda ~2 gün (istekler arası 1,5 sn).

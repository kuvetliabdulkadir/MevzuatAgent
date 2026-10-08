# Mevzuat Takip Ajanı

Resmî kaynaklarda yayımlanan mevzuatı ve duyuruları her gün otomatik toplayan, firmayı ilgilendirenleri seçen,
belgelerin içeriğini okuyup her kalemin altında resmî kaynak linki olan bir rapor hazırlayan ve **bir sorumlunun onayından sonra** raporu ilgili ekiplere
mail ile dağıtan sistem.

Bir kuyumculuk firmasında staj projesi olarak geliştirildi (Ekim 2026). İş kolları: **Kuyum, Döviz/Altın, Oto kiralama** ve hepsini ilgilendiren **Ortak**.

> **Durum:** Canlıda çalışıyor (Docker, Ubuntu 24.04, HTTPS). 362 test geçiyor.

---

## Ne yapar

```text
Zamanlayıcı (varsayılan 06:30 + 18:00 Türkiye saati, panelden değişir) ya da panelden "Şimdi tara"
   │
   ▼
Kaynaklar ── Resmî Gazete (günlük fihrist) · MASAK (WordPress API) · GİB (sitenin JSON API'si)
   │         panelden eklenenler (düz HTML / RSS, tip adresten otomatik bulunur)
   │         + mevzuat.gov.tr güncel metin takibi (21 mevzuat, madde bazında fark)
   ▼
Başlık filtresi (konular + anahtar kelimeler, Türkçe karakter duyarsız) → ilgisizin içeriği indirilmez
   ▼
İçerik: HTML · PDF · taranmış PDF için OCR (Tesseract, Türkçe)
   ▼
Rapor: madde özeti, tablolar, yürürlük tarihi, "neden size geldi", orijinal PDF ekte
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
- **Güncel metin takibi:** mevzuat.gov.tr'deki metin değişince eski/yeni farkı madde madde onaya gelir.
- **Yapay zekâ yok, uydurma yok:** özet kurallarla çıkarılır. OCR metni dayanak sayılmaz, orijinal PDF her zaman ekte.

## Ekran ve roller

| Rol | Ne yapar |
|---|---|
| **Onaylayıcı** | Raporları onaylar/reddeder, alıcı gruplarını, kaynakları, konuları yönetir, "Şimdi tara" der |
| **Admin** | Kurtarma rolü: kullanıcı davet eder, parola linki gönderir, yanlış değişikliği geçmişten geri alır. **Rapor onaylayamaz** (görev ayrılığı) |

Panel sayfaları: Raporlar (onay) · Alıcı grupları · Kaynaklar · Konular · Tarama · Kullanıcılar · Denetim kaydı.

## Teknoloji

| Katman | Kullanılan |
|---|---|
| Dil, paket yönetimi | Python 3.12, uv |
| Çekme, ayrıştırma | httpx, selectolax (Playwright sadece isteğe bağlı yedek tip) |
| PDF, OCR | pdfplumber, pypdfium2, Tesseract (Türkçe) |
| Veritabanı | PostgreSQL 16 (sunucu), SQLite (geliştirme), SQLAlchemy 2, Alembic (15 migration) |
| Fark | difflib (madde bazında) |
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

## Klasörler

```text
src/mevzuat/
  cli.py               komut satırı girişi
  gunluk.py            günlük iş: tara → onaya sun → sağlık
  pipeline.py          tarama motoru (checkpoint, kaynak başına yalıtım)
  sources/             kaynak tipleri: resmi_gazete, wordpress, gib, html, rss, tarayici
  filtre.py            Türkçe normalizasyon, konu ve iş kolu eşleştirme
  icerik.py, ocr.py    HTML / PDF / OCR
  surum.py             mevzuat.gov.tr güncel metin takibi ve madde farkı
  rapor.py, mail.py    rapor, onay akışı, dağıtım
  alicilar.py          alıcı grupları, kişi başı dağıtım planı
  kaynak_bulucu.py     adresten kaynak tipini ve seçicileri bulur
  web/                 FastAPI panel API ve güvenlik
  migrations/          Alembic şema geçişleri
frontend/              React panel (dist derlenmiş hâli)
config/                ilk kurulum tohumu: kaynaklar, konular, izlenen mevzuat
deploy/                Docker kurulum rehberi, yedek betikleri
tests/                 testler ve gerçek sayfalardan alınmış örnekler
```

## Rakamlar

4 kaynak (3 tarama + güncel metin) · 8 konu · 21 takip edilen mevzuat · 15 tablo · 15 migration · 362 test ·
90 günlük gerçek veride 716 Resmî Gazete başlığından 85'i ilgili · Resmî Gazete PDF'lerinin %82'si taranmış görüntü.

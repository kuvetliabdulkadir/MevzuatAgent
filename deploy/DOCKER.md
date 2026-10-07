# Mevzuat Takip — Docker ile çalıştırma

Sunucuya sadece Docker kurulur. Veritabanı, panel, zamanlayıcı ve gecelik yedek ayrı container'larda çalışır.

## 1. Yapı

```text
compose.yaml
├── db           postgres:16-bookworm      veritabanı; port dışarı AÇILMAZ, veri "pgdata" biriminde
├── panel        mevzuat:latest            web panel; sadece 127.0.0.1:8000 (bu makine)
├── zamanlayici  mevzuat:latest            her gün 06:30 ve 18:00 (Türkiye) günlük taramayı başlatır
└── yedek        postgres:16-bookworm      açılışta + her gece 03:00 pg_dump → ./yedekler (30 gün)

Dockerfile (python:3.12-slim-bookworm = Debian 12)
├── taban     Tesseract + Türkçe, uv, bağımlılıklar, kod, derlenmiş arayüz
├── test      bütün testler Linux'ta (docker build --target test .)
└── uygulama  root olmayan "mevzuat" kullanıcısı, panel komutu
```

## 2. İlk kurulum

```sh
cp .env.example .env
# .env'de doldurun:
#   MEVZUAT_DB_SIFRE      → openssl rand -hex 16      (sadece harf/rakam: bağlantı adresine girer)
#   MEVZUAT_GIZLI_ANAHTAR → openssl rand -hex 32
#   SMTP ayarları (boşsa mailler giden_mailler birimine dosya olarak yazılır)
docker compose up -d --build
docker compose ps                     # dördü de "Up", db ve panel "(healthy)"
docker compose logs yedek             # "yedek: /yedekler/mevzuat_….dump" → ilk yedek alındı
```

İlk kullanıcı (parola terminalde sorulur, ekranda görünmez):

```sh
docker compose exec panel python -m mevzuat.cli kullanici-ekle --eposta admin@firma.com --ad "Ad Soyad" --rol admin
```

Diğer kullanıcıları (onaylayıcı vb.) admin panelden ekler: **Kullanıcılar → Kullanıcı ekle**. Kişiye davet maili gider,
parolasını linkle kendisi belirler (`MEVZUAT_PANEL_ADRESI` ve mail ayarları dolu olmalı). Parolasını unutan için aynı
sayfada **Parola linki gönder**. Komut satırı yolu da durur:

```sh
docker compose exec panel python -m mevzuat.cli kullanici-ekle --eposta onay@firma.com --ad "Ad Soyad" --rol onaylayici
```

Panel: http://127.0.0.1:8000 . Şirket ağından erişim için önüne HTTPS'li vekil (nginx ya da Caddy) konur ve `.env`'de `MEVZUAT_HTTPS=1` yapılır. Onay maillerindeki "Onay paneline git" düğmesi için `.env`'de `MEVZUAT_PANEL_ADRESI` de bu adresle doldurulur.

## 3. Günlük kullanım

| İş | Komut |
|---|---|
| Durum | `docker compose ps` |
| Loglar (canlı) | `docker compose logs -f panel` · `docker compose logs -f zamanlayici` |
| Taramayı şimdi çalıştır | Panel → Tarama → **Şimdi tara** (ya da `docker compose exec zamanlayici python -m mevzuat.cli gunluk`) |
| Tarama saatlerini değiştir | Panel → Tarama → Tarama saatleri (zamanlayıcı 30 sn içinde yeni saatlere geçer) |
| Bir kaynağı dene (DB'ye yazmaz) | `docker compose exec panel python -m mevzuat.cli dene masak --gun 30` |
| Konu/kaynak ayarı | Panel → Kaynaklar / Konular (veritabanında; `config/` sadece ilk kurulum tohumu) |
| Kod güncellendi | `git pull && docker compose up -d --build` (veritabanı şeması açılışta otomatik güncellenir) |
| Durdur / başlat | `docker compose stop` · `docker compose start` |
| Kaldır (veri KALIR) | `docker compose down` |
| Kaldır + VERİYİ SİL | `docker compose down -v`  ⚠ geri alınamaz |
| Yedek | Kendiliğinden: `yedek` container'ı her gece 03:00 → `./yedekler/` (30 gün). Elle şimdi: `docker compose restart yedek` |
| Yedekten geri dön | Bölüm 3.1 |
| Container'a gir | `docker compose exec panel bash` |
| Testleri Linux'ta çalıştır | `docker build --target test .` |

### 3.1 Yedek ve geri dönüş

- `yedek` container'ı `deploy/yedek.sh`'ı kullanır: yedeği önce geçici adla yazar, sonra
  `pg_restore --list` ile okunabildiğini doğrular, 30 günden eskileri siler. Saat: `.env`'de `MEVZUAT_YEDEK_SAATI=03:00`.
- **Sunucu dışına kopyalayın.** `./yedekler` aynı diskte: disk bozulursa yedek de gider. Bilgi işlemin yedek
  sistemine bu klasörü ekletin ya da her gece başka bir makineye kopyalayın (ör. `rsync`).
- Yedekte kullanıcıların e-postaları ve parola özetleri var: dosyalar sadece sahibine okunur (`chmod 600`).

Geri yükleme (DİKKAT: var olan veritabanının üzerine yazar):

```sh
docker compose stop panel zamanlayici
docker compose exec -T db pg_restore -U mevzuat -d mevzuat --clean --if-exists < yedekler/mevzuat_<zaman>.dump
docker compose start panel zamanlayici
```

## 4. Canlı demo (hoca önünde, ~5 dakika)

```sh
# 1) Linux'ta olduğumuzu göster (container içinden)
docker compose exec panel cat /etc/os-release        # Debian GNU/Linux 12 (bookworm)
docker compose exec panel uname -a                    # Linux çekirdeği
docker compose exec panel python --version            # Python 3.12
docker compose exec panel tesseract --list-langs      # OCR, Türkçe (tur)
docker compose exec panel id                          # uid=10001(mevzuat): root DEĞİL

# 2) İmaj ve container'lar
docker images mevzuat
docker compose ps

# 3) Bütün testleri Linux'ta çalıştır
docker build --target test .

# 4) Bir kaynağı canlı dene: başlıklar ve ★ ile filtreye takılanlar
docker compose exec panel python -m mevzuat.cli dene resmi_gazete --gun 2

# 5) Paneli tarayıcıda göster: http://127.0.0.1:8000  (giriş → onay kuyruğu → rapor → alıcı grupları)

# 6) Loglar
docker compose logs --tail 20 zamanlayici

# 7) Durdur / başlat (veri kaybolmaz)
docker compose stop && docker compose start
```

Tek container komutlarıyla (compose'suz) aynı şeyler: `docker ps` · `docker logs mevzuat-panel-1` ·
`docker exec -it mevzuat-panel-1 bash` · `docker stop/start mevzuat-panel-1`.

## 5. Neden böyle? (kısa gerekçeler)

- **`python:3.12-slim-bookworm`:** hedef sunucu Debian 12; aynı aile → "bende çalışıyordu" farkı olmaz. `slim` = gereksiz paket yok, küçük imaj.
- **Önce `pyproject.toml` + `uv.lock`, sonra kod kopyalanıyor:** Docker katman önbelleği. Sadece kod değişince bağımlılıklar yeniden kurulmaz (derleme saniyeler sürer).
- **`uv sync --frozen`:** kilit dosyasındaki sürümler birebir kurulur; dosya ile `pyproject.toml` uyuşmazsa derleme durur.
- **Test aşaması ayrı:** `docker build --target test` bütün testleri gerçek Linux'ta, gerçek Tesseract ile çalıştırır; çalışan imaja test araçları girmez.
- **Root olmayan kullanıcı (uid 10001):** uygulamada bir açık olsa bile container içinde sistem dosyalarını değiştiremez.
- **Port `127.0.0.1:8000`:** panel sadece bu makineden erişilir; dışarıya ancak HTTPS'li vekil üzerinden.
- **Veritabanı portu yok:** PostgreSQL'e sadece compose ağındaki container'lar ulaşır.
- **`config/` birim olarak bağlı (salt okunur):** ayar değişikliği için imaj derlemek gerekmez; container ayarı değiştiremez.
- **Zamanlayıcı ayrı container ve günlük işi ayrı süreçte başlatıyor:** tarama çökse de zamanlayıcı ayakta kalır. Kaçan çalışma için ayrı telafi gerekmez: tarama checkpoint'ten devam eder.
- **`restart: unless-stopped`:** sunucu yeniden başlarsa container'lar kendiliğinden kalkar.
- **Chromium yok:** Playwright'lı kaynak tipi kullanılmıyor; gerekirse `Dockerfile`'daki nota göre eklenir (~400 MB).
- **Arayüz derlenmiş geliyor:** imajda Node.js yok (`frontend/dist` depoda).

## 6. Sorun giderme

| Belirti | Bakılacak yer |
|---|---|
| `MEVZUAT_DB_SIFRE tanımlayın` hatası | `.env`'de `MEVZUAT_DB_SIFRE` yok |
| `ports are not available ... 8000` | 8000'i başka program kullanıyor: `.env`'e `MEVZUAT_PANEL_PORT=8800` |
| Panel "unhealthy" | `docker compose logs panel` (çoğunlukla `MEVZUAT_GIZLI_ANAHTAR` 32 karakterden kısa) |
| Şifre değiştirdim, db bağlanmıyor | PostgreSQL şifreyi sadece ilk kurulumda alır; birim silinmeden değişmez |
| Tarama hiç çalışmadı | Panel → Tarama: "Zamanlayıcı çalışmıyor" mu? `docker compose logs zamanlayici` → "Sonraki çalışma" satırı |

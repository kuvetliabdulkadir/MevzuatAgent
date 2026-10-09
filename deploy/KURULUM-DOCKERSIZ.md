# Mevzuat Takip — Docker'sız kurulum (Linux)

Sunucuda Docker yoksa sistem doğrudan kurulur: PostgreSQL, Python (uv ile) ve üç systemd servisi
(panel, zamanlayıcı, gecelik yedek). Kod aynıdır, Docker'lı kurulumdan farkı sadece çalıştırma biçimidir.
Docker varsa [`SIRKET-KURULUM.md`](SIRKET-KURULUM.md) daha kısadır.

Bu rehber `deploy/prova-dockersiz.sh` ile temiz bir Debian 12 container'ında adım adım denenir (en alttaki "Prova").
Komutlar `root` ile çalıştırılır. `firma` geçen yerleri kendi değerlerinizle değiştirin.

---

## 0. Bilgi işlemden istenecekler

| Ne | Neden |
|---|---|
| Debian 12 ya da Ubuntu 22.04/24.04, en az 2 GB RAM, 10 GB disk | OCR (taranmış PDF okuma) bellek ister |
| Alan adı (ör. `mevzuat.firma.com.tr`) ve HTTPS sertifikası | Panele ve API'ye güvenli erişim |
| Önünde HTTPS'li ters vekil (nginx, Caddy ya da şirketin vekili) | Panel sadece sunucunun içinde dinler |
| SMTP bilgileri (sunucu, port, kullanıcı, şifre, gönderen) | Onay, dağıtım, uyarı mailleri. Kurulumdan sonra paneldeki Ayarlar sayfasından da girilir |
| Sunucudan dışarıya 443: `www.resmigazete.gov.tr`, `masak.hmb.gov.tr`, `gib.gov.tr`, `cdn.gib.gov.tr`, `www.mevzuat.gov.tr`, `www.tcmb.gov.tr`, `github.com`, `astral.sh`, `pypi.org`, `files.pythonhosted.org`, `apt.postgresql.org` | Tarama, kod ve paket kurulumu |
| Sunucudan mail sunucusuna 587 | Mail gönderimi |
| Yedek klasörünün sunucu dışına kopyalanması | Disk arızasında aynı diskteki yedek de gider |

Node.js **gerekmez**: panel arayüzü depoda derlenmiş halde durur (`frontend/dist`).

## 1. Sistem paketleri ve saat dilimi

```sh
apt-get update
apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-tur ca-certificates curl git openssl
timedatectl set-timezone Europe/Istanbul      # önerilir, loglar okunur olsun. Uygulama buna bağlı değil
```

Kontrol: `tesseract --list-langs` çıktısında `tur` olmalı.

## 2. PostgreSQL 16

Sistem 16 ile test edildiği için PostgreSQL'in resmî deposu kullanılır (Debian ve Ubuntu'da aynı komutlar).

```sh
apt-get install -y postgresql-common
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
apt-get install -y postgresql-16
SIFRE=$(openssl rand -hex 24)
echo "veritabanı parolası: $SIFRE"          # 5. adımda lazım
runuser -u postgres -- psql -c "CREATE ROLE mevzuat LOGIN PASSWORD '$SIFRE';"
runuser -u postgres -- psql -c "CREATE DATABASE mevzuat OWNER mevzuat ENCODING 'UTF8' TEMPLATE template0;"
```

PostgreSQL varsayılan olarak sadece bu makineden bağlantı kabul eder, öyle kalmalı.

## 3. uv (Python ve paket yöneticisi)

Servis dosyaları uv'yi `/usr/local/bin/uv` yolunda bekler. Python'u da uv kurar, sistemin Python'u kullanılmaz.

```sh
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
uv --version
```

## 4. Servis kullanıcısı ve kod

Uygulama kendi kullanıcısıyla çalışır (root değil). Depo özel olduğu için sunucuya **salt okunur deploy key** eklenir.

```sh
useradd --system --create-home --home-dir /opt/mevzuat --shell /usr/sbin/nologin mevzuat
runuser -u mevzuat -- mkdir -p -m 700 /opt/mevzuat/.ssh
runuser -u mevzuat -- ssh-keygen -t ed25519 -f /opt/mevzuat/.ssh/mevzuat_deploy -N ""
cat /opt/mevzuat/.ssh/mevzuat_deploy.pub
# Çıkan satır GitHub'da: depo → Settings → Deploy keys → Add deploy key ("Allow write access" İŞARETLENMEZ)

runuser -u mevzuat -- git -c core.sshCommand="ssh -i /opt/mevzuat/.ssh/mevzuat_deploy -o StrictHostKeyChecking=accept-new" \
  clone -b projeler git@github.com:kuvetliabdulkadir/MevzuatAgent.git /opt/mevzuat/depo
runuser -u mevzuat -- git -C /opt/mevzuat/depo config core.sshCommand "ssh -i /opt/mevzuat/.ssh/mevzuat_deploy"
ln -s /opt/mevzuat/depo/mevzuat-takip /opt/mevzuat/mevzuat-takip
```

Uygulamanın klasörü bundan sonra `/opt/mevzuat/mevzuat-takip`. Servis dosyaları bu yolu bekler.

## 5. Ayarlar (`.env`)

```sh
cd /opt/mevzuat/mevzuat-takip
cp .env.example .env && chown mevzuat:mevzuat .env && chmod 600 .env
openssl rand -hex 32        # MEVZUAT_GIZLI_ANAHTAR için
```

Doldurulacaklar (her ayar dosyada **bir kez** yazılmalı):

| Ayar | Değer |
|---|---|
| `MEVZUAT_DB_URL` | `postgresql+psycopg://mevzuat:<2. adımdaki parola>@localhost:5432/mevzuat` |
| `MEVZUAT_GIZLI_ANAHTAR` | Yukarıdaki komutun çıktısı. Yeni üretilir, kimseyle paylaşılmaz. Sonradan değişirse paneldeki mail şifresi yeniden girilir |
| `MEVZUAT_HTTPS` | `1` |
| `MEVZUAT_MFA` | `1` (iki adımlı doğrulama zorunlu, önerilir) |
| `MEVZUAT_DOKUMAN_ACIK` | `1` (varsayılan). Swagger anahtarsız açılır, istekler yine anahtar ister. `0` ise doküman da anahtar ister |
| `MEVZUAT_PANEL_ADRESI` | Panelin adresi, ör. `https://mevzuat.firma.com.tr` |
| `MEVZUAT_SMTP_*`, `MEVZUAT_ADMIN_ALICILARI` | Mail ve uyarı adresleri. İsterseniz boş bırakıp kurulumdan sonra panelde **Ayarlar**'dan girin |

`MEVZUAT_TESSERACT` / `MEVZUAT_TESSDATA` Linux'ta **gerekmez**. Panelde değişebilen ayarlar (mail, panel adresi, uyarı
adresleri, PDF eki, MFA, saklama süreleri) panelde girilince buradakinin önüne geçer. Veritabanı, gizli anahtar,
HTTPS ve port sadece burada değişir.

## 6. Bağımlılıklar

```sh
cd /opt/mevzuat/mevzuat-takip
runuser -u mevzuat -- env HOME=/opt/mevzuat /usr/local/bin/uv sync --frozen --no-dev
```

`--frozen`: tam olarak `uv.lock`'taki sürümler kurulur. `--no-dev`: test araçları kurulmaz.

## 7. İlk deneme

Tablolar ilk çalıştırmada kendiliğinden oluşur. Kaynakları veritabanına yazmadan deneyin:

```sh
cd /opt/mevzuat/mevzuat-takip
M="runuser -u mevzuat -- env HOME=/opt/mevzuat /usr/local/bin/uv run --frozen --no-dev python -m mevzuat.cli"
$M dene resmi_gazete --gun 3
$M dene mevzuat_gov_yeni --gun 10
$M dene gib_mevzuat --gun 30
$M mail-dene bilgi-islem@firma.com.tr       # SMTP boşsa giden_mailler/ klasörüne yazar
```

Her biri başlık listesi basmalı, firmayı ilgilendirenler ★ ile işaretlenir. Hata varsa sunucunun dışarı erişimini
kontrol edin (0. adım).

## 8. İlk kullanıcı

Kayıt olma sayfası yoktur. İlk admin sunucudan eklenir (parola terminalde sorulur, en az 12 karakter), diğerlerini
admin panelden davet eder.

```sh
$M kullanici-ekle --eposta bilgi-islem@firma.com.tr --ad "Ad Soyad" --rol admin
```

## 9. Panel ve zamanlayıcı servisleri

```sh
cp deploy/mevzuat-panel.service deploy/mevzuat-zamanlayici.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mevzuat-panel mevzuat-zamanlayici
```

Kontrol:

```sh
curl -s http://127.0.0.1:8000/saglik               # "ok"
journalctl -u mevzuat-zamanlayici -n 20             # "Zamanlayıcı başladı", "Sonraki çalışma: ..."
```

Tarama saatleri panelden değişir (Tarama sayfası, varsayılan 06:30 ve 18:00). Planlı tarama, siteleri tam dakikasında
yormamak için saatten sonraki 10 dakika içinde başlar. Hemen tarama için panelde **Şimdi tara**.

## 10. HTTPS'li ters vekil

Panel sadece `127.0.0.1:8000`'de dinler. Şirket ağından (ya da internetten) erişim vekil üzerinden HTTPS ile olur.

**nginx** (şirkette zaten varsa):

```nginx
server {
    listen 443 ssl;
    server_name mevzuat.firma.com.tr;
    # ssl_certificate ve ssl_certificate_key satırları (şirket sertifikası ya da certbot)
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Caddy** (vekil yoksa en kolayı): `apt-get install -y caddy`, `deploy/Caddyfile.ornek` dosyasını `/etc/caddy/Caddyfile`
olarak kopyalayıp alan adını değiştirin, `systemctl reload caddy`.

Denetim kaydına gerçek kullanıcı IP'si `X-Forwarded-For` başlığından yazılır. Güvenlik duvarında dışarıya sadece 443
açık olmalı (8000 ve 5432 kapalı).

## 11. Şirket portalından erişim

Panel ve API kendi adresinde çalışır (ör. `https://mevzuat.firma.com.tr`). Şirket portalına bu adreslere **bağlantı**
konur:

| Bağlantı | Kim kullanır |
|---|---|
| `https://mevzuat.firma.com.tr/` | Panel: onaylayıcı ve admin |
| `https://mevzuat.firma.com.tr/api/dokuman` | API dokümanı (Swagger). Doğrudan açılır, panel hesabı gerekmez. İstek denemek için sağ üstteki **Authorize**'a API anahtarı girilir. `MEVZUAT_DOKUMAN_ACIK=0` ise doküman da anahtar ister |

Dokümanda sistemin bütün işlemleri var: mevzuat arama ve detayı (sürümlü, `/api/v1`), rapor onayı ve gönderimi,
tarama, kaynak, konu, alıcı grubu, ayarlar. Portal bizim arayüz yerine bunları kullanabilir.
Anahtar her istekte `Authorization: Bearer mvz_...` biçiminde gönderilir.

API anahtarlarını admin panelde **API Anahtarları** sayfasından üretir ve iptal eder. Panel kullanılmıyorsa sunucudan:

```sh
$M api-anahtari-uret --ad "Portal entegrasyonu" --rol admin --gun 365   # anahtar bir kez basılır
$M api-anahtari-listele
$M api-anahtari-iptal --id 3
```

Anahtar seçilen rolün (admin ya da onaylayıcı) panelde yapabildiği her şeyi API'den yapar, panel hiç kullanılmasa da. Dış sistem anahtarı her istekte
`Authorization: Bearer mvz_...` biçiminde gönderir. Doküman aynı sayfadan HTML (internetsiz açılır) ya da OpenAPI JSON (Postman'e
yüklenir) olarak da indirilir.

İki sınır var. Bilgi işlem farklı bir şey isterse kod tarafında eklenir:
- Panel bir **alt yol** altında (ör. `portal.firma.com/mevzuat/`) çalışmaz, kendi alt alanını (ör. `mevzuat.firma.com.tr`) ister.
- Panel güvenlik gereği başka bir sayfanın **içine gömülemez** (iframe). Portal yeni sekmede açar.

## 12. Gecelik yedek

```sh
install -d -o postgres -g postgres -m 700 /var/backups/mevzuat
cp deploy/mevzuat-yedek.service deploy/mevzuat-yedek.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mevzuat-yedek.timer
systemctl start mevzuat-yedek.service && journalctl -u mevzuat-yedek.service -n 5    # ilk yedek hemen
```

Yedekler her gece 02:30'da alınır, 30 gün saklanır. **Sunucunun dışına da kopyalayın.** `.env` dosyasını da ayrıca
güvenli bir yerde saklayın, yedekteki mail şifresi ondaki gizli anahtarla çözülür.

Geri yükleme denemesi (canlı veritabanına dokunmaz):

```sh
SON=$(ls -t /var/backups/mevzuat/mevzuat_*.dump | head -1)
runuser -u postgres -- createdb mevzuat_deneme && runuser -u postgres -- pg_restore -d mevzuat_deneme "$SON"
runuser -u postgres -- psql -d mevzuat_deneme -c "SELECT count(*) FROM kayitlar;" && runuser -u postgres -- dropdb mevzuat_deneme
```

Gerçek geri yükleme: servisleri durdurun (`systemctl stop mevzuat-panel mevzuat-zamanlayici`), veritabanını silip
yeniden oluşturun (2. adımdaki `CREATE DATABASE`), `pg_restore -d mevzuat <dosya>` çalıştırıp servisleri başlatın.

## 13. Panelde ilk işler

Panel adresine girin: giriş, iki adımlı doğrulama kurulumu, sonra
1. **Ayarlar**: mail sunucusu, panel adresi, uyarı adresleri. **Deneme maili** ile kontrol edin.
2. **Kullanıcılar**: onaylayıcıyı davet edin. Dış geliştirici varsa **API kullanıcısı** rolüyle.
3. **Alıcı grupları**: raporun kime gideceği.
4. **Tarama → Şimdi tara**: ilk tarama.

## 14. Kontrol listesi

- [ ] `systemctl status mevzuat-panel mevzuat-zamanlayici` → active (running), `systemctl list-timers` → `mevzuat-yedek.timer`
- [ ] Panel `https://` adresinden açılıyor, iki adımlı doğrulama istiyor
- [ ] Panel → Tarama: "Zamanlayıcı çalışıyor", sonraki tarama saati doğru
- [ ] Ayarlar → deneme maili geldi
- [ ] "Şimdi tara" bitti, Kaynaklar sayfasında hata yok
- [ ] `/api/dokuman` açılıyor, Authorize'a anahtar girmeden istek atınca 401, anahtarla 200
- [ ] `/var/backups/mevzuat/` altında yedek var ve sunucu dışına kopyalanıyor

---

## Güncelleme

```sh
systemctl start mevzuat-yedek.service                         # önce yedek
runuser -u mevzuat -- git -C /opt/mevzuat/depo pull --ff-only
cd /opt/mevzuat/mevzuat-takip
runuser -u mevzuat -- env HOME=/opt/mevzuat /usr/local/bin/uv sync --frozen --no-dev
cp deploy/*.service deploy/*.timer /etc/systemd/system/ && systemctl daemon-reload
systemctl restart mevzuat-panel mevzuat-zamanlayici          # veritabanı değişiklikleri açılışta kendiliğinden uygulanır
```

## Sorun giderme

| Belirti | Bakılacak yer |
|---|---|
| Panel açılmıyor | `systemctl status mevzuat-panel`, `curl http://127.0.0.1:8000/saglik`, vekilin logu |
| Panelde "Zamanlayıcı çalışmıyor" | `systemctl status mevzuat-zamanlayici`, `journalctl -u mevzuat-zamanlayici -n 50` |
| Yöneticiye "UYARI" maili geldi | Mailin içinde sebep yazar, ayrıntı `journalctl -u mevzuat-zamanlayici` |
| Bir kaynak ilerlemiyor | Panel → Kaynaklar → kartta kırmızı hata, düzenle → **Dene**. Sunucuda `$M dene <kaynak> --gun 7` |
| Mail gitmiyor | Panel → Ayarlar → deneme maili, çıkan hata mesajı |
| OCR çalışmıyor (`OCR_GEREKLI` kalan kayıtlar) | `tesseract --list-langs` içinde `tur` var mı |
| Tarayıcıda "Arayüz derlenmemiş" | `frontend/dist/index.html` yok, `git -C /opt/mevzuat/depo status` |
| Hesap kilitlendi | 15 dakika bekleyin (5 hatalı denemeden sonra kendiliğinden açılır) |
| Telefon kayboldu (MFA) | `$M mfa-sifirla --eposta ...` |

---

## Prova (bu rehberin doğrulanması)

`deploy/prova-dockersiz.sh` bu rehberin adımlarını temiz bir Debian 12 container'ında sırayla çalıştırır ve kontrol
eder. Container'da systemd çalışmadığı için servisler elle (unit dosyalarındaki `ExecStart` ile) başlatılır, unit
dosyaları `systemd-analyze verify` ile doğrulanır. Kod GitHub'dan değil arşivden gelir. Gerçek mail gönderilmez.

Geliştirme makinesinde (Docker gerekir), proje klasöründen:

```sh
git archive --format=tar -o /tmp/kod.tar HEAD
docker run --rm -v /tmp/kod.tar:/prova/kod.tar:ro -v "$PWD/deploy/prova-dockersiz.sh:/prova/prova.sh:ro" debian:12 bash /prova/prova.sh
```

Son satır `PROVA BAŞARILI` olmalı.

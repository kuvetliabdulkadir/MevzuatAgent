#!/bin/bash
# deploy/KURULUM.md'nin provası: temiz Debian 12 container'ında rehberin adımlarını sırayla çalıştırır, her adımı kontrol eder.
# Kullanım ve container'ın systemd'siz olmasından doğan farklar: KURULUM.md → "Prova".
# Beklenen: /prova/kod.tar (git archive). Gerçek sitelere bağlanır; mail göndermez.
set -euo pipefail

ADIM=""
adim() { ADIM="$1"; printf '\n== %s\n' "$1"; }
basarisiz() {
  echo "PROVA BAŞARISIZ — $ADIM: $1"
  if [ -f /tmp/panel.log ]; then echo "-- panel logunun sonu:"; tail -30 /tmp/panel.log; fi
  exit 1
}
kontrol() { local aciklama="$1"; shift; if "$@"; then echo "  ✓ $aciklama"; else basarisiz "$aciklama"; fi; }
trap 'echo "PROVA BAŞARISIZ — $ADIM: komut hata verdi (satır $LINENO)"' ERR

export DEBIAN_FRONTEND=noninteractive

adim "1. Sistem paketleri ve saat dilimi"
apt-get update -qq
apt-get install -y -qq --no-install-recommends tesseract-ocr tesseract-ocr-tur ca-certificates curl git openssl >/dev/null
apt-get install -y -qq --no-install-recommends procps >/dev/null   # sadece prova: pkill (container imajında yok)
# Container'da timedatectl yok; aynı sonucu doğrudan veririz.
ln -sf /usr/share/zoneinfo/Europe/Istanbul /etc/localtime && echo Europe/Istanbul > /etc/timezone
kontrol "Tesseract Türkçe dili var" bash -c 'tesseract --list-langs 2>/dev/null | grep -qx tur'
kontrol "saat dilimi +03" bash -c '[ "$(date +%z)" = "+0300" ]'

adim "2. PostgreSQL 16"
apt-get install -y -qq postgresql-common >/dev/null
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null 2>&1
apt-get install -y -qq postgresql-16 >/dev/null 2>&1
pg_ctlcluster 16 main start   # container'da systemd yok; gerçek sunucuda servis kendiliğinden başlar
SIFRE=$(openssl rand -base64 24 | tr -d '/+=')
runuser -u postgres -- psql -qc "CREATE ROLE mevzuat LOGIN PASSWORD '$SIFRE';"
runuser -u postgres -- psql -qc "CREATE DATABASE mevzuat OWNER mevzuat ENCODING 'UTF8' TEMPLATE template0;"
kontrol "PostgreSQL 16 çalışıyor" bash -c 'runuser -u postgres -- psql -Atc "show server_version" | grep -q "^16"'

adim "3. uv"
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1
kontrol "uv /usr/local/bin/uv yolunda" test -x /usr/local/bin/uv

adim "4. Servis kullanıcısı ve kod"
useradd --system --create-home --home-dir /opt/mevzuat --shell /usr/sbin/nologin mevzuat
# Rehberde: deploy key ile GitHub'dan clone (depo/mevzuat-takip). Provada kod arşivden aynı yere açılır.
mkdir -p /opt/mevzuat/depo/mevzuat-takip && tar -x -C /opt/mevzuat/depo/mevzuat-takip -f /prova/kod.tar
ln -s /opt/mevzuat/depo/mevzuat-takip /opt/mevzuat/mevzuat-takip
chown -R mevzuat:mevzuat /opt/mevzuat
export UYG=/opt/mevzuat/mevzuat-takip
kontrol "kod yerinde" test -f $UYG/pyproject.toml
kontrol "dosyalarda CRLF yok" bash -c '! grep -rlI $'"'"'\r'"'"' /opt/mevzuat/depo --include=*.py --include=*.service --include=*.timer --include=*.toml --include=*.sh | grep -q .'

adim "5. Ayarlar (.env)"
cp $UYG/.env.example $UYG/.env
chown mevzuat:mevzuat $UYG/.env && chmod 600 $UYG/.env
ayarla() { sed -i "s|^#\? *$1=.*|$1=$2|" $UYG/.env; }
ayarla MEVZUAT_DB_URL "postgresql+psycopg://mevzuat:$SIFRE@localhost:5432/mevzuat"
ayarla MEVZUAT_ADMIN_ALICILARI "bilgi-islem@ornek-firma.com.tr"
ayarla MEVZUAT_GIZLI_ANAHTAR "$(openssl rand -base64 48 | tr -d '\n')"
ayarla MEVZUAT_HTTPS 1
ayarla MEVZUAT_MFA 1
ayarla MEVZUAT_PANEL_ADRESI "https://localhost"
kontrol ".env sadece servis kullanıcısına açık (600)" bash -c '[ "$(stat -c %a:%U $UYG/.env)" = "600:mevzuat" ]'

adim "6. Bağımlılıklar"
cd $UYG
runuser -u mevzuat -- env HOME=/opt/mevzuat /usr/local/bin/uv sync --frozen --no-dev 2>&1 | tail -1
kontrol "test araçları kurulmadı (--no-dev)" bash -c '! ls .venv/lib/python*/site-packages | grep -qi "^pytest"'

M="runuser -u mevzuat -- env HOME=/opt/mevzuat /usr/local/bin/uv run --frozen --no-dev python -m mevzuat.cli"

adim "7. İlk deneme: her kaynak"
for k in "resmi_gazete 3" "masak 30" "gib_mevzuat 30" "mevzuat_gov_yeni 10"; do
  set -- $k
  CIKTI=$($M dene "$1" --gun "$2" 2>&1 | grep -v " INFO ")
  echo "$CIKTI" | head -3
  kontrol "$1: kayıt okundu" bash -c "echo \"\$0\" | grep -q '^- '" "$CIKTI"
done
CIKTI=$(cd $UYG && $M mail-dene bilgi-islem@ornek-firma.com.tr 2>&1 | grep -v " INFO ")
echo "$CIKTI"
kontrol "mail-dene: deneme maili yazıldı (SMTP boş)" bash -c "echo \"\$0\" | grep -q '^Gönderildi' && ls $UYG/giden_mailler/*.eml >/dev/null" "$CIKTI"

adim "8. Panel kullanıcıları"
env MEVZUAT_YENI_PAROLA=prova-parolasi-2026 $M kullanici-ekle --eposta sorumlu@ornek-firma.com.tr --ad "Ayşe Sorumlu" --rol onaylayici 2>&1 | grep -v " INFO "
env MEVZUAT_YENI_PAROLA=prova-parolasi-2026 $M kullanici-ekle --eposta bilgi-islem@ornek-firma.com.tr --ad "Bilgi İşlem" --rol admin 2>&1 | grep -v " INFO "
kontrol "iki kullanıcı DB'de" bash -c '[ "$(runuser -u postgres -- psql -d mevzuat -Atc "select count(*) from kullanicilar")" = 2 ]'

adim "9. Zamanlayıcı ve panel servisleri"
apt-get install -y -qq systemd >/dev/null 2>&1
cp deploy/mevzuat-zamanlayici.service deploy/mevzuat-panel.service \
   deploy/mevzuat-yedek.service deploy/mevzuat-yedek.timer /etc/systemd/system/
kontrol "servislerin klasörü rehberdeki yol" grep -qx "WorkingDirectory=$UYG" /etc/systemd/system/mevzuat-panel.service
kontrol "unit dosyaları geçerli" systemd-analyze verify /etc/systemd/system/mevzuat-*.service /etc/systemd/system/mevzuat-*.timer
# systemctl yerine unit'teki ExecStart'ı birebir, aynı kullanıcı ve klasörle çalıştırırız.
calistir() { local unit=$1; shift; cd $UYG && runuser -u "$(sed -n 's/^User=//p' /etc/systemd/system/$unit)" -- \
  env HOME=/opt/mevzuat TZ=Europe/Istanbul $(sed -n 's/^ExecStart=//p' /etc/systemd/system/$unit) "$@"; }
calistir mevzuat-panel.service > /tmp/panel.log 2>&1 &
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8000/saglik >/dev/null 2>&1 && break; sleep 1; done
kontrol "panel sağlık: ok" bash -c '[ "$(curl -s http://127.0.0.1:8000/saglik)" = ok ]'
# Günlük iş: zamanlayıcının başlattığı komutun aynısı (elle; panelden "Şimdi tara" da bunu çalıştırır).
set +e; (cd $UYG && $M gunluk) > /tmp/gunluk.log 2>&1; KOD=$?; set -e
grep -a "Çalışma #" /tmp/gunluk.log || tail -5 /tmp/gunluk.log
kontrol "günlük iş başarılı (çıkış kodu 0)" [ "$KOD" = 0 ]
kontrol "GİB taraması çalıştı" bash -c '[ "$(runuser -u postgres -- psql -d mevzuat -Atc "select count(*) from kaynak_durumu where kaynak='"'"'gib_mevzuat'"'"'")" = 1 ]'
# Zamanlayıcı servisi: saatler DB'de; 30 sn'de bir nabız yazar (panel "çalışıyor mu" buradan görür).
calistir mevzuat-zamanlayici.service > /tmp/zamanlayici.log 2>&1 &
for i in $(seq 1 60); do grep -q "Sonraki çalışma" /tmp/zamanlayici.log 2>/dev/null && break; sleep 1; done
grep -a "Zamanlayıcı başladı\|Sonraki çalışma" /tmp/zamanlayici.log || tail -5 /tmp/zamanlayici.log
for i in $(seq 1 60); do [ -n "$(runuser -u postgres -- psql -d mevzuat -Atc "select deger from sistem_ayarlari where anahtar='zamanlayici_nabiz'")" ] && break; sleep 1; done
NABIZ=$(runuser -u postgres -- psql -d mevzuat -Atc "select deger from sistem_ayarlari where anahtar='zamanlayici_nabiz'")
echo "  nabız: $NABIZ"
kontrol "zamanlayıcı nabız yazıyor" bash -c "echo '$NABIZ' | grep -q '\"zaman\"'"
kontrol "tarama saatleri DB'de (varsayılan 06:30, 18:00)" [ "$(runuser -u postgres -- psql -d mevzuat -Atc "select deger from sistem_ayarlari where anahtar='calisma_saatleri'")" = '["06:30", "18:00"]' ]

adim "10. HTTPS'li ters vekil (Caddy)"
apt-get install -y -qq caddy >/dev/null 2>&1
# Prova adı: localhost (gerçek kurulumda şirketin verdiği ad)
sed 's/^mevzuat.firma.local {/localhost {/' deploy/Caddyfile.ornek > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 && echo "  ✓ Caddyfile geçerli" || basarisiz "Caddyfile geçersiz"
HOME=/root caddy start --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1   # container'da systemctl yok
for i in $(seq 1 20); do curl -skf https://localhost/saglik >/dev/null 2>&1 && break; sleep 1; done
kontrol "panel HTTPS üzerinden açılıyor" bash -c '[ "$(curl -sk https://localhost/saglik)" = ok ]'
kontrol "oturum çerezi Secure (MEVZUAT_HTTPS=1)" bash -c 'curl -sk -D - -o /dev/null https://localhost/api/oturum | grep -i "^set-cookie" | grep -qi secure'
curl -sk -D /tmp/ana_baslik -o /tmp/ana https://localhost/
kontrol "arayüz açılıyor (derlenmiş React; sunucuda Node yok)" grep -q '<div id="root">' /tmp/ana
kontrol "arayüz sıkı CSP ile geliyor (sadece kendi scriptleri)" grep -qi "^content-security-policy: .*script-src 'self'" /tmp/ana_baslik
# Gerçek istemci IP'si denetim kaydına yazılıyor mu? Vekile container'ın kendi ağ adresinden bağlanırız (127.0.0.1'den
# bağlansak test ayırt edemezdi). Caddy, istemcinin uydurduğu X-Forwarded-For'u atıp gerçek adresi yazmalı.
DIS_IP=$(awk -v h="$(cat /etc/hostname)" '$2==h {print $1; exit}' /etc/hosts)
V="curl -sk -c /tmp/c -b /tmp/c --resolve localhost:443:$DIS_IP"
# Arayüzün yaptığını yap: CSRF token'ı /api/oturum'dan al, JSON'u X-CSRF-Token başlığıyla gönder.
csrf() { $V https://localhost/api/oturum | sed -n 's/.*"csrf":"\([^"]*\)".*/\1/p'; }
jpost() { $V -H "X-CSRF-Token: $(csrf)" -H "Content-Type: application/json" -H "X-Forwarded-For: 10.20.30.40" -d "$2" "https://localhost$1"; }
durum() { $V -o /dev/null -w '%{http_code}' "https://localhost$1"; }
CEVAP=$(jpost /api/giris '{"eposta":"sorumlu@ornek-firma.com.tr","parola":"prova-parolasi-2026"}')
kontrol "MFA açık: parola sonrası kurulum adımı" bash -c "echo '$CEVAP' | grep -q '\"sonraki\":\"kurulum\"'"
kontrol "kod girilmeden oturum açılmaz (API 401)" [ "$(durum /api/raporlar)" = 401 ]
kontrol "kurulumda QR resmi (SVG)" bash -c "$V -D - -o /dev/null https://localhost/api/giris/mfa-qr.svg | grep -qi '^content-type: image/svg+xml'"
# Telefondaki uygulamanın yaptığını yap: DB'deki sırdan güncel kodu üret.
GIZLI=$(runuser -u postgres -- psql -d mevzuat -Atc "select mfa_gizli from kullanicilar where eposta='sorumlu@ornek-firma.com.tr'")
KOD=$(runuser -u mevzuat -- env HOME=/opt/mevzuat /usr/local/bin/uv run --frozen --no-dev python -c "import pyotp; print(pyotp.TOTP('$GIZLI').now())")
CEVAP=$(jpost /api/giris/mfa-kurulum "{\"kod\":\"$KOD\"}")
kontrol "kod sonrası oturum açık" bash -c "echo '$CEVAP' | grep -q '\"sonraki\":\"panel\"'"
kontrol "panel API'si açık (raporlar 200)" [ "$(durum /api/raporlar)" = 200 ]
# Alıcı grubu .env'de değil, panelde tanımlanır (rehber 8. adım).
CEVAP=$(jpost /api/gruplar '{"ad":"Kuyum Ekibi","is_kollari":["Kuyum","Ortak"],"adresler":["Kuyum@ornek-firma.com.tr"],"aktif":true}')
echo "  grup cevabı: $CEVAP"
GRUP=$(runuser -u postgres -- psql -d mevzuat -Atc "select is_kollari::text || ' ' || adresler::text from alici_gruplari where ad='Kuyum Ekibi'")
echo "  grup: $GRUP"
kontrol "alıcı grubu panelden eklendi (PostgreSQL'de)" [ "$GRUP" = '["Kuyum", "Ortak"] ["kuyum@ornek-firma.com.tr"]' ]
kontrol "grup ekleme denetim kaydında" bash -c '[ "$(runuser -u postgres -- psql -d mevzuat -Atc "select count(*) from denetim where islem='"'"'grup_ekle'"'"'")" = 1 ]'
kontrol "grup listesinde görünüyor" bash -c "$V https://localhost/api/gruplar | grep -q 'Kuyum Ekibi'"
# Kaynak ve konu tanımları veritabanında, panelden yönetilir; config/*.toml sadece ilk kurulum tohumu (rehber 8. adım).
SAY() { runuser -u postgres -- psql -d mevzuat -Atc "$1"; }
icerir() { printf '%s' "$1" | grep -qF -- "$2"; }   # cevapta tırnak/kesme olabilir: bash -c "echo '...'" kullanılmaz
kontrol "ilk kurulumda toml'dan aktarıldı (4 kaynak, 8 konu)" [ "$(SAY 'select count(*) from kaynaklar')/$(SAY 'select count(*) from konular')" = "4/8" ]
MENU=$($V https://localhost/api/menu)
kontrol "menü veritabanından geliyor" icerir "$MENU" '"anahtar":"raporlar"'
kontrol "onaylayıcının menüsünde API dokümanı yok" bash -c '! printf "%s" "$0" | grep -q api_dokumani' "$MENU"
kontrol "API dokümanı sayfası açık" [ "$(curl -sk -o /dev/null -w '%{http_code}' https://localhost/api/dokuman)" = 200 ]
kontrol "API tanımı oturumsuz ve anahtarsız kapalı" [ "$(curl -sk -o /dev/null -w '%{http_code}' https://localhost/api/dokuman/openapi.json)" = 401 ]
kontrol "onaylayıcı oturumuyla API tanımı kapalı" [ "$(durum /api/dokuman/openapi.json)" = 403 ]
KONU='{"ad":"Rekabet","is_kollari":["Ortak"],"kelimeler":["rekabet","soruşturma"],"haric":[],"dislanan":[]}'
CEVAP=$(jpost /api/konular/onizleme "$KONU")
echo "  konu önizleme: $(echo "$CEVAP" | head -c 160)"
kontrol "konu önizlemesi hesaplandı" icerir "$CEVAP" '"taranan":'
kontrol "önizleme hiçbir şey kaydetmedi" [ "$(SAY "select count(*) from konular where ad='Rekabet'")" = 0 ]
jpost /api/konular "$KONU" >/dev/null
kontrol "konu panelden eklendi + denetimde" [ "$(SAY "select count(*) from konular k join denetim d on d.islem='konu_ekle' where k.ad='Rekabet'")" = 1 ]
KAYNAK='{"tip":"html","etiket":"Rekabet Kurumu Duyurusu","ayarlar":{"liste_url":"https://www.rekabet.gov.tr/tr/Duyurular","oge":"div.icerik01 > a","baslik":"td.tablotitle","tarih":"td[align=right]","icerik":"div.icerik01"},"varsayilan_konular":[]}'
CEVAP=$(jpost /api/kaynaklar/dene "$KAYNAK")
echo "  kaynak denemesi: $(echo "$CEVAP" | head -c 160)"
kontrol "kaynak kaydetmeden denendi (gerçek site)" icerir "$CEVAP" '"toplam":'
CEVAP=$(jpost /api/kaynaklar "$(echo "$KAYNAK" | sed 's|https://www.rekabet.gov.tr/tr/Duyurular|https://localhost/api/oturum|')")
kontrol "iç ağ adresi kaynak olarak kaydedilemez (SSRF)" icerir "$CEVAP" 'İç ağ'
CEVAP=$(jpost /api/kaynaklar "$KAYNAK")
kontrol "kaynak panelden eklendi (kod adı üretildi)" icerir "$CEVAP" '"ad":"rekabet_kurumu_duyurusu"'
# Panelden "Şimdi tara": panel istek yazar, arka plandaki zamanlayıcı (9. adım) 30 sn içinde alıp günlük işi çalıştırır.
CEVAP=$(jpost /api/tarama '{}')
kontrol "panelden tarama istendi" icerir "$CEVAP" '"durum":"BEKLIYOR"'
kontrol "ikinci istek reddedilir (zaten sırada)" icerir "$(jpost /api/tarama '{}')" 'zaten sırada'
for i in $(seq 1 120); do
  ISTEK=$(SAY "select durum from tarama_istekleri order by id desc limit 1")
  [ "$ISTEK" = BITTI ] || [ "$ISTEK" = HATALI ] && break; sleep 5
done
echo "  istek: $ISTEK — $(SAY "select coalesce(hata, '') from tarama_istekleri order by id desc limit 1")"
grep -a "panelden istek\|Günlük iş bitti" /tmp/zamanlayici.log | tail -2
kontrol "zamanlayıcı isteği aldı, tarama bitti (BITTI)" [ "$ISTEK" = BITTI ]
kontrol "panelden eklenen kaynak taramaya girdi" [ "$(SAY "select count(*) from kaynak_durumu where kaynak='rekabet_kurumu_duyurusu'")" = 1 ]
kontrol "sonuç panelde (çalışma bağlandı)" icerir "$($V https://localhost/api/tarama/durum)" '"durum":"BITTI"'
SON_IP=$(runuser -u postgres -- psql -d mevzuat -Atc "select ip from denetim where islem='giris' order by id desc limit 1")
echo "  istemci: $DIS_IP, denetim kaydındaki IP: $SON_IP"
kontrol "denetimde gerçek istemci IP'si (vekilin 127.0.0.1'i ya da uydurulan 10.20.30.40 değil)" [ "$SON_IP" = "$DIS_IP" ]
kontrol "panel doğrudan dışarıdan erişilemez (8000)" bash -c "! curl -s --max-time 3 http://$DIS_IP:8000/saglik >/dev/null"

adim "11. Gecelik yedek + geri yükleme denemesi"
install -d -o postgres -g postgres -m 700 /var/backups/mevzuat
calistir mevzuat-yedek.service
SON=$(ls -t /var/backups/mevzuat/mevzuat_*.dump | head -1)
kontrol "yedek dosyası 600 izinli" bash -c "[ \"\$(stat -c %a '$SON')\" = 600 ]"
runuser -u postgres -- createdb mevzuat_geri_yukleme_testi
runuser -u postgres -- pg_restore -d mevzuat_geri_yukleme_testi "$SON"
ASIL=$(runuser -u postgres -- psql -d mevzuat -Atc "select count(*) from kayitlar")
GERI=$(runuser -u postgres -- psql -d mevzuat_geri_yukleme_testi -Atc "select count(*) from kayitlar")
echo "  kayitlar: canlı $ASIL, geri yüklenen $GERI"
kontrol "geri yüklenen yedek canlıyla aynı" [ "$ASIL" = "$GERI" ]
runuser -u postgres -- dropdb mevzuat_geri_yukleme_testi

adim "Ek: saat politikası — sunucu UTC'de bırakılsa da Türkiye saati"
ln -sf /usr/share/zoneinfo/UTC /etc/localtime && echo UTC > /etc/timezone
kontrol "sunucu artık UTC" bash -c '[ "$(date +%z)" = "+0000" ]'
SAAT=$(runuser -u mevzuat -- env -u TZ HOME=/opt/mevzuat /usr/local/bin/uv run --frozen --no-dev python -c \
  "import time; from mevzuat.cli import saat_dilimini_sabitle; saat_dilimini_sabitle(); print(time.strftime('%z'))")
kontrol "uygulama (unit dışında, elle çalıştırılsa da) +0300" [ "$SAAT" = "+0300" ]
# Zamanlayıcı, sunucu UTC iken ve TZ verilmeden başlatılsa da Türkiye saatine göre hedefler: sonraki tarama 06:30 ya da
# 18:00'den sonraki 10 dk içinde olmalı (rastgele gecikme), UTC karşılığı 03:30/15:00 değil.
pkill -f "mevzuat.cli zamanlayici" || true
(cd $UYG && runuser -u mevzuat -- env -u TZ HOME=/opt/mevzuat /usr/local/bin/uv run --frozen --no-dev \
  python -m mevzuat.cli zamanlayici) > /tmp/zamanlayici_utc.log 2>&1 &
for i in $(seq 1 60); do grep -q "Sonraki çalışma" /tmp/zamanlayici_utc.log 2>/dev/null && break; sleep 1; done
SONRAKI=$(grep -a -o "Sonraki çalışma: .*" /tmp/zamanlayici_utc.log | tail -1)
echo "  UTC sunucuda zamanlayıcı: $SONRAKI"
kontrol "zamanlayıcı Türkiye saatiyle hedefliyor (06:30-06:40 ya da 18:00-18:10)" bash -c "echo '$SONRAKI' | grep -Eq ' (06:3[0-9]|06:40|18:0[0-9]|18:10)$'"
pkill -f "mevzuat.cli zamanlayici" || true

trap - ERR
printf '\nPROVA BAŞARILI\n'

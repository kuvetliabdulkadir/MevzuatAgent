#!/bin/sh
# Mevzuat Takip — PostgreSQL yedeği. Docker'daki yedek container'ı (yedek-docker.sh) her gece çalıştırır.
# Geri yükleme: deploy/DOCKER.md, "Yedek ve geri dönüş" bölümü.
set -eu

HEDEF=${MEVZUAT_YEDEK_KLASORU:-/var/backups/mevzuat}
SAKLA_GUN=${MEVZUAT_YEDEK_SAKLA_GUN:-30}
DOSYA="$HEDEF/mevzuat_$(date +%Y%m%d_%H%M%S).dump"

mkdir -p "$HEDEF"
# -Fc: sıkıştırılmış, pg_restore ile seçerek geri yüklenebilen biçim.
# Önce geçici adla yaz: yarıda kesilen yedek "tamam" görünmesin.
pg_dump -Fc "${PGDATABASE:-mevzuat}" -f "$DOSYA.yaziliyor"
mv "$DOSYA.yaziliyor" "$DOSYA"
chmod 600 "$DOSYA"   # yedekte kullanıcıların parola özetleri ve e-postaları var

# Yedeğin okunabildiğini doğrula (bozuk yedek, yedek yokla aynıdır).
pg_restore --list "$DOSYA" > /dev/null

# Eski yedekleri sil (sadece bu script'in ürettikleri).
find "$HEDEF" -name 'mevzuat_*.dump' -mtime +"$SAKLA_GUN" -delete
echo "yedek: $DOSYA ($(du -h "$DOSYA" | cut -f1))"

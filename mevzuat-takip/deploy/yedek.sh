#!/bin/sh
# Mevzuat Takip — PostgreSQL yedeği. Docker'da yedek container'ı, Docker'sız kurulumda mevzuat-yedek.timer her gece çalıştırır.
# Geri yükleme: deploy/DOCKER.md (Docker) ya da deploy/KURULUM-DOCKERSIZ.md (Docker'sız), "Yedek" bölümü.
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

# Arşivin dosyaları (PDF, ek, resim, sayfa görüntüsü, günlük) veritabanı yedeğine girmez. Dosyalar içeriklerinin özetiyle
# adlandırılır ve hiç değiştirilmez, bu yüzden sadece yeni ve değişen (günlük) dosyalar kopyalanır; ilk seferde ~10 GB.
# Arşiv silinmez kuralı (karar 19) gereği yedekten de silinmez. MEVZUAT_ARSIV_KAYNAK boşsa (Docker'sız kurulumda
# arşiv başka yerdeyse orayı verin) atlanır.
ARSIV=${MEVZUAT_ARSIV_KAYNAK:-}
if [ -n "$ARSIV" ] && [ -d "$ARSIV" ]; then
  mkdir -p "$HEDEF/arsiv"
  cp -a -u "$ARSIV/." "$HEDEF/arsiv/"
  echo "arşiv yedeği: $HEDEF/arsiv ($(du -sh "$HEDEF/arsiv" | cut -f1))"
fi

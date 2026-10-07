#!/bin/sh
# Docker Compose'daki `yedek` servisi: açılışta bir yedek alır, sonra her gece MEVZUAT_YEDEK_SAATI'nde
# (varsayılan 03:00, Türkiye saati) deploy/yedek.sh'ı çalıştırır. Yedekler sunucuda ./yedekler klasörüne düşer;
# oradan sunucu dışına kopyalanmalı (disk bozulursa sunucudaki yedek de gider).
set -eu

SAAT=${MEVZUAT_YEDEK_SAATI:-03:00}

# Veritabanı yeni açıldıysa hazır olmasını bekle.
until pg_isready -q; do sleep 2; done

while true; do
  # Hata yedek servisini düşürmesin: hata loga yazılır, sonraki gece tekrar denenir.
  sh /deploy/yedek.sh || echo "YEDEK BAŞARISIZ: $(date '+%F %T')" >&2
  simdi=$(date +%s)
  hedef=$(date -d "$SAAT" +%s)
  [ "$hedef" -gt "$simdi" ] || hedef=$(date -d "tomorrow $SAAT" +%s)
  sleep $((hedef - simdi))
done

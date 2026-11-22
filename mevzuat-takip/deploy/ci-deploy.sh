#!/usr/bin/env bash
# GitLab CI deploy adımı: sunucudaki shell runner çalıştırır (.gitlab-ci.yml → deploy).
#
#   bash deploy/ci-deploy.sh <kaynak klasör> <hedef klasör>
#
# 1) Kodu hedefe kopyalar (.env, yedekler/ ve giden_mailler/ korunur).
# 2) Veritabanı ayaktaysa deploy öncesi yedek alır (yedekler/deploy_*.dump, son 10 tutulur).
#    Panel açılışta şemayı kendisi güncellediği için imaja geri dönüş tek başına yetmeyebilir;
#    şema da geri alınacaksa bu yedek pg_restore ile yüklenir (bkz. DOCKER.md 3.1).
# 3) Çalışan imajı mevzuat:onceki olarak saklar, yeni imajı derleyip başlatır.
# 4) Panel sağlıklı olmazsa mevzuat:onceki'ye döner ve hata ile çıkar (pipeline kırmızı).
set -euo pipefail

KAYNAK=${1:?kaynak klasör gerekli}
HEDEF=${2:?hedef klasör gerekli}
SAGLIK_BEKLEME_SN=${SAGLIK_BEKLEME_SN:-180}
SAKLANACAK_DEPLOY_YEDEGI=10

if [ ! -f "$HEDEF/.env" ]; then
  echo "HATA: $HEDEF/.env yok. Sunucuda elle oluşturulur (bkz. .env.example)." >&2
  exit 1
fi

# Önce container'ın kendi healthcheck'i, sonra ters vekilin (nginx) gideceği yoldan, sunucu tarafından
# 127.0.0.1:<port>/saglik. İkincisi güvenlik duvarı ya da port eşlemesi sorunlarını da yakalar.
saglik_bekle() {
  local id durum adres
  for _ in $(seq 1 $((SAGLIK_BEKLEME_SN / 5))); do
    id=$(docker compose ps -q panel 2>/dev/null || true)
    durum=$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo "yok")
    if [ "$durum" = "healthy" ]; then
      adres=$(docker compose port panel 8000 2>/dev/null || true)
      if [ -n "$adres" ] && curl -fsS -m 5 -o /dev/null "http://$adres/saglik"; then
        echo "Panel sağlıklı: http://$adres/saglik"
        return 0
      fi
    fi
    sleep 5
  done
  return 1
}

echo "==> Kod kopyalanıyor: $HEDEF"
rsync -a --delete \
  --exclude '.env' \
  --exclude 'yedekler/' \
  --exclude 'giden_mailler/' \
  "$KAYNAK"/ "$HEDEF"/
chmod 750 "$HEDEF"

cd "$HEDEF"

if [ -n "$(docker compose ps -q --status running db 2>/dev/null || true)" ]; then
  YEDEK="yedekler/deploy_$(date +%Y%m%d_%H%M%S).dump"
  echo "==> Deploy öncesi veritabanı yedeği: $YEDEK"
  (umask 077 && docker compose exec -T db pg_dump -U mevzuat -d mevzuat -Fc > "$YEDEK")
  ls -1t yedekler/deploy_*.dump | tail -n +$((SAKLANACAK_DEPLOY_YEDEGI + 1)) | xargs -r rm -f
else
  echo "==> Veritabanı çalışmıyor (ilk kurulum?), deploy öncesi yedek atlandı."
fi

if docker image inspect mevzuat:latest >/dev/null 2>&1; then
  docker image tag mevzuat:latest mevzuat:onceki
fi

echo "==> İmaj derleniyor ve başlatılıyor"
docker compose build
docker compose up -d --remove-orphans

echo "==> Panelin sağlıklı olması bekleniyor (en çok ${SAGLIK_BEKLEME_SN} sn)"
if saglik_bekle; then
  echo "==> Deploy tamam."
  docker compose ps
  docker image prune -f >/dev/null
  exit 0
fi

echo "HATA: panel sağlıklı olmadı. Son loglar:" >&2
docker compose logs --tail 80 panel >&2 || true
if docker image inspect mevzuat:onceki >/dev/null 2>&1; then
  echo "==> Önceki imaja dönülüyor" >&2
  docker image tag mevzuat:onceki mevzuat:latest
  docker compose up -d --no-build
fi
exit 1

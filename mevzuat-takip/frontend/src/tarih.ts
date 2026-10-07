// Sunucu zamanları "2026-10-02T14:30" (Türkiye saati, saat dilimsiz) gönderir, olduğu gibi gösterilir,
// tarayıcının saat dilimine çevrilmez.
// Sunucudan gelen "2026-10-02T14:30" biçimini "02.10.2026 14:30" yapar, değer yoksa çizgi koyar.
export function tarihSaat(iso: string | null): string {
  if (!iso) return '—';
  // Tarih ve saat kısmını ayır.
  const [gun, saat] = iso.split('T');
  // Tarihi yıl, ay, gün diye parçala.
  const [y, a, g] = gun.split('-');
  // Saat varsa ilk 5 karakterini (14:30) ekle.
  return saat ? `${g}.${a}.${y} ${saat.slice(0, 5)}` : `${g}.${a}.${y}`;
}

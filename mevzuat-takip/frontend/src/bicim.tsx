// Birçok sayfada kullanılan küçük görünüm parçaları, durum rozeti ve mesaj kutusu.
import React from 'react';
// lucide-react, hazır ikon kütüphanesi.
import { CheckCircle2, Clock, Send, XCircle } from 'lucide-react';
import { RaporDurumu, GonderimDurumu } from './types/api';

// Her durum için rozetin yazısı, renkleri (Tailwind sınıfları) ve ikonu.
const ROZETLER: Record<RaporDurumu | GonderimDurumu, { etiket: string; sinif: string; ikon: React.ElementType }> = {
  ONAY_BEKLIYOR: { etiket: 'Onay Bekliyor', sinif: 'text-amber-800 bg-amber-50 border-amber-300', ikon: Clock },
  ONAYLANDI: { etiket: 'Onaylandı, gönderiliyor', sinif: 'text-sky-800 bg-sky-50 border-sky-300', ikon: Send },
  GONDERILDI: { etiket: 'Gönderildi', sinif: 'text-emerald-800 bg-emerald-50 border-emerald-300', ikon: CheckCircle2 },
  REDDEDILDI: { etiket: 'Reddedildi', sinif: 'text-rose-800 bg-rose-50 border-rose-300', ikon: XCircle },
  GONDERILIYOR: { etiket: 'Durumu belirsiz', sinif: 'text-rose-800 bg-rose-50 border-rose-300', ikon: Clock },
  BEKLIYOR: { etiket: 'Bekliyor', sinif: 'text-amber-800 bg-amber-50 border-amber-300', ikon: Clock },
};

// Durum rozeti bileşeni, verilen duruma göre renkli küçük etiket çizer.
export const DurumRozeti: React.FC<{ durum: RaporDurumu | GonderimDurumu }> = ({ durum }) => {
  const r = ROZETLER[durum];
  const Ikon = r.ikon;
  // Rozetin HTML'i, ikon + yazı.
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-semibold border whitespace-nowrap ${r.sinif}`}>
      <Ikon className="w-3.5 h-3.5" />
      {r.etiket}
    </span>
  );
};

// Başarı (yeşil) ya da hata (kırmızı) mesaj kutusu. role, ekran okuyucular için.
export const MesajKutusu: React.FC<{ tur: 'basari' | 'hata'; children: React.ReactNode }> = ({ tur, children }) => (
  <div
    role={tur === 'hata' ? 'alert' : 'status'}
    className={`p-3 rounded-lg border text-xs ${
      tur === 'hata' ? 'bg-rose-50 border-rose-300 text-rose-900' : 'bg-emerald-50 border-emerald-300 text-emerald-900'
    }`}
  >
    {children}
  </div>
);

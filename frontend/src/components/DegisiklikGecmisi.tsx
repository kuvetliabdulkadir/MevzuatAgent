// Kaynak/konu formunun altındaki "Değişiklik geçmişi" bölümü, admin'e "önceki hale döndür" düğmesi.
import React, { useState } from 'react';
import { History, RotateCcw } from 'lucide-react';
import { api } from '../api';
import { GecmisKaydi } from '../types/api';
import { MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';

// Alan adlarının ekranda görünen karşılıkları.
const ALAN_ADLARI: Record<string, string> = {
  etiket: 'Görünen ad', ayarlar: 'Ayarlar', varsayilan_konular: 'Her kaydı ilgili say', aktif: 'Açık/aktif',
  kaldirildi: 'Kaldırıldı', ad: 'Ad', is_kollari: 'İş kolları', kelimeler: 'Kelimeler', haric: 'Hariç', dislanan: 'Dışlanan',
  aciklama: 'Neden önemli',
};

// Bir değeri okunur yazıya çevirir. true evet olur, liste virgülle yazılır.
function deger(v: unknown): string {
  if (v === true) return 'evet';
  if (v === false) return 'hayır';
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return v.length ? v.join(', ') : '—';
  return String(v);
}

// Liste alanlarında eklenen ve çıkarılanlar, ayarlarda değişen anahtarlar, diğerlerinde eski ve yeni değer gösterilir.
// İki değer arasındaki farkı yazıya çevirir. Listede eklenen ve çıkanları, ayarlarda değişen anahtarları, diğerlerinde eski ve yeni değeri gösterir.
function fark(once: unknown, sonra: unknown): string {
  if (Array.isArray(once) && Array.isArray(sonra)) {
    const eklenen = sonra.filter((x) => !once.includes(x));
    const cikan = once.filter((x) => !sonra.includes(x));
    if (!eklenen.length && !cikan.length) return 'sıra değişti';
    return [eklenen.length ? `+ ${eklenen.join(', ')}` : '', cikan.length ? `− ${cikan.join(', ')}` : ''].filter(Boolean).join('  ');
  }
  if (once && sonra && typeof once === 'object' && typeof sonra === 'object') {
    const o = once as Record<string, unknown>;
    const y = sonra as Record<string, unknown>;
    return [...new Set([...Object.keys(o), ...Object.keys(y)])]
      .filter((k) => JSON.stringify(o[k]) !== JSON.stringify(y[k]))
      .map((k) => `${k}: ${deger(o[k])} → ${deger(y[k])}`).join('; ');
  }
  return once === undefined ? deger(sonra) : `${deger(once)} → ${deger(sonra)}`;
}

// Kaynak/konu formunun altında. Herkes görür, "önceki hale döndür" sadece admin'e (kurtarma rolü).
export const DegisiklikGecmisi: React.FC<{
  yol: string; // '/kaynaklar/masak' ya da '/konular/3'
  surum: number;
  kurtarmaYapabilir: boolean;
  onGeriAlindi: (sonuc: unknown) => void;
}> = ({ yol, surum, kurtarmaYapabilir, onGeriAlindi }) => {
  // Geçmiş kayıtları, hata, hangi satır için onay soruluyor, işlem sürüyor mu.
  const [kayitlar, setKayitlar] = useState<GecmisKaydi[] | null>(null);
  const [hata, setHata] = useState('');
  const [sorulan, setSorulan] = useState<number | null>(null);
  const [bekliyor, setBekliyor] = useState(false);

  // Geçmişi ilk açılışta bir kez yükle.
  const yukle = () => {
    if (kayitlar !== null) return;
    api.get<{ gecmis: GecmisKaydi[] }>(`${yol}/gecmis`).then((v) => setKayitlar(v.gecmis)).catch((e) => setHata(e.message));
  };

  // "Döndür" onaylanınca sunucuya geri alma isteği gönder.
  const geriAl = async (id: number) => {
    setHata('');
    setBekliyor(true);
    try {
      onGeriAlindi(await api.post(`${yol}/geri-al`, { denetim_id: id, surum }));
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'Geri alınamadı.');
      setBekliyor(false);
    }
  };

  return (
    // <details>, tıklanınca açılan bölüm, açılınca geçmişi yükler.
    <details onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && yukle()} className="rounded-lg border border-paper-300">
      <summary className="px-3 py-2 cursor-pointer font-semibold text-stone-700 flex items-center gap-1.5 select-none">
        <History className="w-3.5 h-3.5 text-stone-500" /> Değişiklik geçmişi
      </summary>
      {/* Hata, yükleniyor ve boş durum yazıları. */}
      <div className="px-3 pb-3 space-y-2">
        {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
        {kayitlar === null && !hata && <p className="text-stone-500">Yükleniyor…</p>}
        {kayitlar?.length === 0 && <p className="text-stone-500">Panelden yapılmış değişiklik yok (ilk kurulumdan beri aynı).</p>}
        {/* Geçmiş satırları, işlem, kim, ne zaman, değişen alanlar. */}
        {kayitlar && kayitlar.length > 0 && (
          <div className="max-h-64 overflow-y-auto divide-y divide-paper-200 border border-paper-200 rounded-sm bg-white">
            {kayitlar.map((g) => (
              <div key={g.id} className="p-2.5 space-y-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span><strong>{g.islem_adi}</strong> · {g.kim} · {tarihSaat(g.zaman)}</span>
                  {/* Döndür düğmesi (sadece admin ve geri alınabilir satırlarda). */}
                  {kurtarmaYapabilir && g.geri_alinabilir && sorulan !== g.id && (
                    <button type="button" onClick={() => setSorulan(g.id)}
                      className="flex items-center gap-1 px-2 py-1 rounded-sm border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer">
                      <RotateCcw className="w-3 h-3" /> Bundan önceki hale döndür
                    </button>
                  )}
                </div>
                {g.farklar.map((f) => (
                  <div key={f.alan} className="text-[11px] text-stone-600 wrap-break-word">
                    <span className="text-stone-500">{ALAN_ADLARI[f.alan] ?? f.alan}:</span> {fark(f.once, f.sonra)}
                  </div>
                ))}
                {/* Döndürmeden önce satırın içinde onay sorusu. */}
                {sorulan === g.id && (
                  <div className="p-2 rounded-sm bg-amber-50 border border-amber-300 text-amber-900 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <span>Bu değişiklikten önceki hal kaydedilecek (yeni bir değişiklik olarak; geçmiş silinmez).</span>
                    <div className="flex gap-2 shrink-0">
                      <button type="button" onClick={() => setSorulan(null)} className="px-2 py-1 rounded-sm hover:bg-white cursor-pointer">Vazgeç</button>
                      <button type="button" disabled={bekliyor} onClick={() => geriAl(g.id)}
                        className="px-2 py-1 rounded-sm bg-petrol text-white font-semibold disabled:opacity-60 cursor-pointer">Döndür</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </details>
  );
};

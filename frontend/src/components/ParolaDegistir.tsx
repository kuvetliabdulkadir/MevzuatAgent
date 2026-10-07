import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api';
import { Mesaj } from '../types/api';
import { MesajKutusu } from '../bicim';

// Giriş yapmış herkes kendi parolasını değiştirir (mevcut parola istenir).
export const ParolaDegistir: React.FC<{ eposta: string; onKapat: () => void }> = ({ eposta, onKapat }) => {
  // Formdaki alanlar (eski, yeni, tekrar), ekrandaki mesaj ve "gönderiliyor" durumu.
  const [eski, setEski] = useState('');
  const [yeni, setYeni] = useState('');
  const [tekrar, setTekrar] = useState('');
  const [mesaj, setMesaj] = useState<Mesaj | null>(null);
  const [bekliyor, setBekliyor] = useState(false);

  // Esc tuşuna basılınca pencereyi kapat.
  useEffect(() => {
    const tus = (e: KeyboardEvent) => { if (e.key === 'Escape') onKapat(); };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat]);

  // Form gönderilince, iki yeni parola aynı mı bak, sunucuya gönder, sonucu mesaj olarak göster.
  const gonder = async (e: React.FormEvent) => {
    // Tarayıcının sayfayı yenilemesini engelle (formun varsayılan davranışı).
    e.preventDefault();
    if (yeni !== tekrar) {
      setMesaj({ tur: 'hata', mesaj: 'İki yeni parola aynı değil.' });
      return;
    }
    setBekliyor(true);
    try {
      setMesaj(await api.post<Mesaj>('/parolam', { eski, yeni }));
      setEski(''); setYeni(''); setTekrar('');
    } catch (err) {
      setMesaj({ tur: 'hata', mesaj: err instanceof Error ? err.message : 'Değiştirilemedi.' });
    } finally {
      setBekliyor(false);
    }
  };

  // Giriş kutularının ortak görünümü.
  const girdi = 'w-full p-2.5 rounded-lg border border-paper-300 bg-white';
  // Pencere sayfanın en dışına çizilir.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/60 p-2 sm:p-4">
      <div role="dialog" aria-modal="true" className="bg-white rounded-xl shadow-xl border border-paper-300 w-full max-w-sm overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-paper-300 flex items-center justify-between bg-paper-50">
          <h3 className="font-semibold text-stone-900 text-sm">Parolamı değiştir</h3>
          <button onClick={onKapat} aria-label="Kapat" className="text-stone-400 hover:text-stone-600 text-sm font-bold cursor-pointer p-1">×</button>
        </div>
        {/* Form, mevcut parola, yeni parola, tekrar. */}
        <form onSubmit={gonder} className="p-4 sm:p-6 space-y-3 text-xs">
          {/* Gizli e-posta alanı, tarayıcının parola yöneticisi hangi hesap olduğunu anlasın. */}
          <input type="email" value={eposta} autoComplete="username" readOnly hidden />
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Mevcut parola</span>
            <input type="password" required autoComplete="current-password" value={eski} onChange={(e) => setEski(e.target.value)} className={girdi} />
          </label>
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Yeni parola <span className="font-normal text-stone-500">(en az 12 karakter)</span></span>
            <input type="password" required minLength={12} autoComplete="new-password" value={yeni} onChange={(e) => setYeni(e.target.value)} className={girdi} />
          </label>
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Yeni parola (tekrar)</span>
            <input type="password" required minLength={12} autoComplete="new-password" value={tekrar} onChange={(e) => setTekrar(e.target.value)} className={girdi} />
          </label>
          {/* Sonuç mesajı ve düğmeler. */}
          {mesaj && <MesajKutusu tur={mesaj.tur}>{mesaj.mesaj}</MesajKutusu>}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onKapat} className="px-4 py-2 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer">Kapat</button>
            <button type="submit" disabled={bekliyor} className="px-4 py-2 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark disabled:opacity-50 cursor-pointer">
              {bekliyor ? 'Kaydediliyor…' : 'Değiştir'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
};

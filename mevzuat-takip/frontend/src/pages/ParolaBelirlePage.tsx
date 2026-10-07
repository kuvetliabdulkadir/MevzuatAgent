import React, { useEffect, useState } from 'react';
import { KeyRound } from 'lucide-react';
import { api } from '../api';
import { Mesaj, ParolaLinkiBilgisi } from '../types/api';
import { MesajKutusu } from '../bicim';

// Davet ya da sıfırlama mailindeki linkle açılan sayfa. Giriş gerektirmez, link tek kullanımlıktır.
export const ParolaBelirlePage: React.FC<{ token: string; onBitti: () => void }> = ({ token, onBitti }) => {
  // Link bilgisi (kimin linki), formdaki parolalar, hata, sonuç ve "gönderiliyor" durumu.
  const [bilgi, setBilgi] = useState<ParolaLinkiBilgisi | null>(null);
  const [parola, setParola] = useState('');
  const [tekrar, setTekrar] = useState('');
  const [hata, setHata] = useState('');
  const [sonuc, setSonuc] = useState<Mesaj | null>(null);
  const [bekliyor, setBekliyor] = useState(false);

  // Sayfa açılınca linkin geçerli olup olmadığını sunucuya sor.
  useEffect(() => {
    api.post<ParolaLinkiBilgisi>('/parola-linki/kontrol', { token })
      .then(setBilgi)
      .catch((e) => setHata(e.message));
  }, [token]);

  // Form gönderilince, iki parola aynı mı bak, sunucuya kaydettir.
  const gonder = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    if (parola !== tekrar) {
      setHata('İki parola aynı değil.');
      return;
    }
    setBekliyor(true);
    try {
      setSonuc(await api.post<Mesaj>('/parola-linki/kullan', { token, parola }));
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Parola kaydedilemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  const girdi = 'w-full p-2.5 rounded-lg border border-paper-300 bg-white text-sm focus:outline-hidden focus:ring-2 focus:ring-petrol/20';
  return (
    <div className="min-h-screen bg-paper-100 flex items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-4">
        {/* Logo ve başlık. */}
        <div className="flex items-center gap-2.5 justify-center">
          <div className="w-10 h-10 rounded-lg bg-petrol flex items-center justify-center text-white font-bold">MT</div>
          <div>
            <h1 className="font-bold text-stone-900 tracking-tight">Mevzuat Takip</h1>
            <p className="text-[11px] text-stone-500 font-mono">Parola belirleme</p>
          </div>
        </div>

        {/* Duruma göre, kaydedildiyse sonuç, link geçerliyse form, hata varsa hata, yoksa "kontrol ediliyor". */}
        <div className="bg-white rounded-xl border border-paper-300 shadow-xs p-6 space-y-4 text-xs">
          {sonuc ? (
            <>
              <MesajKutusu tur={sonuc.tur}>{sonuc.mesaj}</MesajKutusu>
              <button onClick={onBitti} className="w-full py-2.5 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark cursor-pointer">
                Giriş ekranına git
              </button>
            </>
          ) : bilgi ? (
            <form onSubmit={gonder} className="space-y-4">
              <h2 className="flex items-center gap-2 font-semibold text-stone-900 text-sm">
                <KeyRound className="w-4 h-4 text-petrol" />
                {bilgi.tur === 'davet' ? 'Hoş geldiniz, parolanızı belirleyin' : 'Yeni parolanızı belirleyin'}
              </h2>
              <p className="text-stone-600">{bilgi.ad} — <span className="font-mono">{bilgi.eposta}</span></p>
              {/* Parola yöneticisi kullanıcı adını bilsin diye gizli e-posta alanı */}
              <input type="email" value={bilgi.eposta} autoComplete="username" readOnly hidden />
              <label className="block space-y-1">
                <span className="block font-semibold text-stone-700">Yeni parola <span className="font-normal text-stone-500">(en az 12 karakter)</span></span>
                <input type="password" required minLength={12} autoComplete="new-password" value={parola}
                  onChange={(e) => setParola(e.target.value)} className={girdi} />
              </label>
              <label className="block space-y-1">
                <span className="block font-semibold text-stone-700">Tekrar</span>
                <input type="password" required minLength={12} autoComplete="new-password" value={tekrar}
                  onChange={(e) => setTekrar(e.target.value)} className={girdi} />
              </label>
              {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
              <button type="submit" disabled={bekliyor}
                className="w-full py-2.5 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark disabled:opacity-50 cursor-pointer">
                {bekliyor ? 'Kaydediliyor…' : 'Parolamı kaydet'}
              </button>
            </form>
          ) : hata ? (
            <>
              <MesajKutusu tur="hata">{hata}</MesajKutusu>
              <button onClick={onBitti} className="w-full py-2.5 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer">
                Giriş ekranına git
              </button>
            </>
          ) : (
            <p className="text-center text-stone-500">Link kontrol ediliyor…</p>
          )}
        </div>
      </div>
    </div>
  );
};

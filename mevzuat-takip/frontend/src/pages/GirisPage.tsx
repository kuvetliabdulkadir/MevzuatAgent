// Giriş sayfası. Önce parola, sonra MFA kurulmamışsa kurulum, kurulmuşsa 6 haneli kod sorulur.
import React, { useEffect, useState } from 'react';
import { KeyRound, ShieldCheck, Smartphone } from 'lucide-react';
import { api, ApiHatasi } from '../api';
import { GirisCevabi } from '../types/api';
import { MesajKutusu } from '../bicim';

// Giriş adımları.
type Adim = 'parola' | 'kod' | 'kurulum';

interface GirisPageProps {
  baslangic: Adim; // sayfa yenilendiğinde MFA adımında kalınmış olabilir
  onGirisTamam: () => void;
}

// Giriş kutularının ortak görünümü.
const girdi =
  'w-full p-2.5 rounded-lg border border-paper-300 bg-white text-sm focus:outline-hidden focus:ring-2 focus:ring-petrol/20';

export const GirisPage: React.FC<GirisPageProps> = ({ baslangic, onGirisTamam }) => {
  // Hangi adımdayız, formdaki değerler, MFA gizli anahtarı, hata mesajı, istek sürüyor mu.
  const [adim, setAdim] = useState<Adim>(baslangic);
  const [eposta, setEposta] = useState('');
  const [parola, setParola] = useState('');
  const [kod, setKod] = useState('');
  const [gizli, setGizli] = useState('');
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);

  // Kurulum adımında, elle girilebilecek sır metni. QR ayrıca <img> olarak sunucudan gelir.
  useEffect(() => {
    if (adim !== 'kurulum') return;
    api
      .get<{ gizli: string }>('/giris/mfa-kurulum')
      .then((v) => setGizli(v.gizli))
      .catch(() => setAdim('parola'));
  }, [adim]);

  // Sunucunun cevabına göre sonraki adıma geç ("panel" ise giriş tamam).
  const sonrakiAdim = (cevap: GirisCevabi) => {
    if (cevap.sonraki === 'panel') onGirisTamam();
    else {
      setKod('');
      setAdim(cevap.sonraki);
    }
  };

  // Form gönderilince, adıma göre parolayı ya da kodu sunucuya gönder.
  const gonder = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    setBekliyor(true);
    try {
      if (adim === 'parola') {
        sonrakiAdim(await api.post<GirisCevabi>('/giris', { eposta, parola }));
        setParola('');
      } else {
        sonrakiAdim(await api.post<GirisCevabi>(adim === 'kod' ? '/giris/kod' : '/giris/mfa-kurulum', { kod }));
      }
    } catch (err) {
      // 401, kod girme süresi doldu, baştan başla.
      if (err instanceof ApiHatasi && err.durum === 401) {
        setAdim('parola'); // kod için süre doldu
        setHata('Süre doldu, tekrar giriş yapın.');
      } else {
        setHata(err instanceof Error ? err.message : 'Bağlantı hatası.');
      }
    } finally {
      setBekliyor(false);
    }
  };

  return (
    <div className="min-h-screen bg-paper-100 flex items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-4">
        {/* Logo ve başlık. */}
        <div className="flex items-center gap-2.5 justify-center">
          <div className="w-10 h-10 rounded-lg bg-petrol flex items-center justify-center text-white font-bold">MT</div>
          <div>
            <h1 className="font-bold text-stone-900 tracking-tight">Mevzuat Takip</h1>
            <p className="text-[11px] text-stone-500 font-mono">Onay Paneli</p>
          </div>
        </div>

        <form onSubmit={gonder} className="bg-white rounded-xl border border-paper-300 shadow-xs p-6 space-y-4 text-xs">
          {/* 1. adım, e-posta ve parola. */}
          {adim === 'parola' && (
            <>
              <h2 className="flex items-center gap-2 font-semibold text-stone-900 text-sm">
                <KeyRound className="w-4 h-4 text-petrol" /> Giriş
              </h2>
              <label className="block space-y-1">
                <span className="block font-semibold text-stone-700">E-posta</span>
                <input type="email" required autoComplete="username" value={eposta}
                  onChange={(e) => setEposta(e.target.value)} className={girdi} />
              </label>
              <label className="block space-y-1">
                <span className="block font-semibold text-stone-700">Parola</span>
                <input type="password" required autoComplete="current-password" value={parola}
                  onChange={(e) => setParola(e.target.value)} className={girdi} />
              </label>
            </>
          )}

          {/* MFA kurulumu, QR kodu ve elle girilecek anahtar. */}
          {adim === 'kurulum' && (
            <>
              <h2 className="flex items-center gap-2 font-semibold text-stone-900 text-sm">
                <Smartphone className="w-4 h-4 text-petrol" /> İki adımlı doğrulamayı kurun
              </h2>
              <p className="text-stone-600 leading-relaxed">
                Telefonunuzdaki doğrulayıcı uygulamayla (Google Authenticator, Microsoft Authenticator vb.) bu kodu
                okutun, sonra uygulamanın gösterdiği 6 haneli kodu girin.
              </p>
              {/* QR resmi doğrudan sunucudan gelir. */}
              <img src="/api/giris/mfa-qr.svg" alt="Doğrulayıcı uygulama için QR kodu"
                className="w-48 h-48 mx-auto border border-paper-300 rounded-lg p-2 bg-white" />
              {gizli && (
                <p className="text-center text-stone-500">
                  QR okutamıyorsanız elle girin:<br />
                  <span className="font-mono text-stone-800 select-all">{gizli}</span>
                </p>
              )}
            </>
          )}

          {/* Kurulmuş MFA, kod başlığı. */}
          {adim === 'kod' && (
            <h2 className="flex items-center gap-2 font-semibold text-stone-900 text-sm">
              <ShieldCheck className="w-4 h-4 text-petrol" /> Doğrulama kodu
            </h2>
          )}

          {/* Kod kutusu (kurulumda ve kod adımında). */}
          {adim !== 'parola' && (
            <label className="block space-y-1">
              <span className="block font-semibold text-stone-700">Uygulamadaki 6 haneli kod</span>
              <input inputMode="numeric" autoComplete="one-time-code" required maxLength={7} value={kod}
                onChange={(e) => setKod(e.target.value)} className={`${girdi} font-mono tracking-widest text-center`} autoFocus />
            </label>
          )}

          {/* Hata mesajı ve düğmeler. */}
          {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}

          <button type="submit" disabled={bekliyor}
            className="w-full py-2.5 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark disabled:opacity-60 cursor-pointer">
            {bekliyor ? 'Bekleyin…' : adim === 'parola' ? 'Giriş yap' : 'Doğrula'}
          </button>
          {adim !== 'parola' && (
            <button type="button" onClick={() => { setAdim('parola'); setHata(''); }}
              className="w-full text-stone-500 hover:text-stone-800 cursor-pointer">
              Başka hesapla giriş
            </button>
          )}
        </form>
      </div>
    </div>
  );
};

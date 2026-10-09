// "Ayarlar" sayfası (sadece admin). Eskiden sadece sunucudaki .env'den değişen ayarlar burada değişir.
// Panelde girilen değer .env'in önüne geçer, "Sıfırla" ile .env'deki değere dönülür. Kaydedince hemen geçerli olur.
import React, { useCallback, useEffect, useState } from 'react';
import { Info, RotateCcw, Save, Send, Settings } from 'lucide-react';
import { api } from '../api';
import { AyarAlani, AyarlarCevabi, Mesaj } from '../types/api';
import { MesajKutusu } from '../bicim';

type Deger = string | number | boolean | string[] | null;

// Değerin nereden geldiğini gösteren küçük etiket.
const KAYNAK_ETIKETI: Record<AyarAlani['kaynak'], [string, string]> = {
  panel: ['Panelden', 'bg-petrol/10 text-petrol border-petrol/30'],
  '.env': ['Sunucu .env', 'bg-amber-50 text-amber-800 border-amber-300'],
  varsayilan: ['Varsayılan', 'bg-stone-100 text-stone-500 border-stone-300'],
};

export const AyarlarPage: React.FC = () => {
  // Sunucudaki ayarlar, düzenlenen değerler (sadece değişenler), mesaj, istek sürüyor mu, deneme maili adresi.
  const [veri, setVeri] = useState<AyarlarCevabi | null>(null);
  const [degisen, setDegisen] = useState<Record<string, Deger>>({});
  const [mesaj, setMesaj] = useState<Mesaj | null>(null);
  const [bekliyor, setBekliyor] = useState(false);
  const [denemeAdresi, setDenemeAdresi] = useState('');

  const yukle = useCallback(() => {
    api.get<AyarlarCevabi>('/ayarlar').then((v) => { setVeri(v); setDegisen({}); })
      .catch((e) => setMesaj({ tur: 'hata', mesaj: e.message }));
  }, []);
  useEffect(yukle, [yukle]);

  // Kaydet, sadece değişen ayarlar gönderilir. Değeri null olan ayar sıfırlanır (.env'e döner).
  const kaydet = async () => {
    if (!veri) return;
    setBekliyor(true);
    setMesaj(null);
    try {
      const cevap = await api.put<AyarlarCevabi>('/ayarlar', { degisiklikler: degisen, surum: veri.surum });
      setVeri(cevap);
      setDegisen({});
      if (cevap.mesaj) setMesaj(cevap.mesaj);
    } catch (e) {
      setMesaj({ tur: 'hata', mesaj: e instanceof Error ? e.message : 'Kaydedilemedi.' });
    } finally {
      setBekliyor(false);
    }
  };

  // Geçerli (kaydedilmiş) mail ayarıyla deneme maili.
  const mailDene = async () => {
    setBekliyor(true);
    setMesaj(null);
    try {
      setMesaj(await api.post<Mesaj>('/ayarlar/mail-dene', { adres: denemeAdresi }));
    } catch (e) {
      setMesaj({ tur: 'hata', mesaj: e instanceof Error ? e.message : 'Gönderilemedi.' });
    } finally {
      setBekliyor(false);
    }
  };

  const gruplar = veri ? [...new Set(veri.alanlar.map((a) => a.grup))] : [];
  const degisiklikVar = Object.keys(degisen).length > 0;

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Başlık ve Kaydet düğmesi. */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-paper-300">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight flex items-center gap-2">
            <Settings className="w-5 h-5 text-petrol" /> Ayarlar
          </h2>
          <p className="text-xs text-stone-500 mt-0.5">Kaydedince hemen geçerli olur, sunucuyu yeniden başlatmak gerekmez.</p>
        </div>
        <button onClick={kaydet} disabled={!degisiklikVar || bekliyor}
          className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white text-xs font-semibold hover:bg-petrol-dark disabled:opacity-50 shadow-xs cursor-pointer">
          <Save className="w-4 h-4" />
          <span>{bekliyor ? 'Kaydediliyor…' : `Kaydet${degisiklikVar ? ` (${Object.keys(degisen).length})` : ''}`}</span>
        </button>
      </div>

      {/* Hangi ayarların burada olduğu, hangilerinin olmadığı. */}
      <div className="p-3.5 sm:p-4 rounded-xl bg-paper-200 border border-paper-300 text-xs text-stone-600 flex items-start gap-3">
        <Info className="w-5 h-5 text-petrol shrink-0 mt-0.5" />
        <div className="space-y-1 leading-relaxed">
          <p>Burada girilen değer sunucudaki <strong>.env</strong> dosyasının önüne geçer. <strong>Sıfırla</strong> denince .env'deki değere dönülür.</p>
          <p>Veritabanı bağlantısı, oturum gizli anahtarı, HTTPS ve port gibi sunucu kurulum ayarları güvenlik gereği burada değil, sadece sunucuda değişir.</p>
        </div>
      </div>

      {mesaj && <MesajKutusu tur={mesaj.tur}>{mesaj.mesaj}</MesajKutusu>}

      {/* Gruplara göre ayarlar. */}
      {veri && gruplar.map((grup) => (
        <section key={grup} className="bg-white rounded-xl border border-paper-300 shadow-xs">
          <h3 className="px-4 sm:px-5 py-3 border-b border-paper-200 text-xs font-semibold text-stone-700 uppercase tracking-wider font-mono">{grup}</h3>
          <div className="divide-y divide-paper-200">
            {veri.alanlar.filter((a) => a.grup === grup).map((a) => (
              <AyarSatiri key={a.ad} alan={a} degisti={a.ad in degisen} deger={a.ad in degisen ? degisen[a.ad] : a.deger}
                onDegistir={(d) => setDegisen({ ...degisen, [a.ad]: d })}
                onGeriAl={() => { const { [a.ad]: _, ...kalan } = degisen; setDegisen(kalan); }}
                onSifirla={() => setDegisen({ ...degisen, [a.ad]: null })} />
            ))}
          </div>
          {/* Mail grubunun altında deneme maili. */}
          {grup === 'Mail sunucusu' && (
            <div className="px-4 sm:px-5 py-3 border-t border-paper-200 bg-paper-50 rounded-b-xl flex flex-col sm:flex-row gap-2 sm:items-center text-xs">
              <span className="text-stone-600 sm:mr-2">Kaydedilmiş ayarla deneme maili:</span>
              <input type="email" value={denemeAdresi} onChange={(e) => setDenemeAdresi(e.target.value)} placeholder="ad@firma.com"
                aria-label="Deneme maili adresi" className="flex-1 min-w-0 p-2 rounded-lg border border-paper-300 bg-white font-mono" />
              <button onClick={mailDene} disabled={bekliyor || !denemeAdresi || degisiklikVar}
                title={degisiklikVar ? 'Önce değişiklikleri kaydedin' : undefined}
                className="flex items-center justify-center gap-1 px-3 py-2 rounded-lg border border-paper-300 bg-white text-stone-700 font-semibold hover:bg-paper-200 disabled:opacity-50 cursor-pointer">
                <Send className="w-3.5 h-3.5" /> Gönder
              </button>
            </div>
          )}
        </section>
      ))}
    </div>
  );
};

// Tek bir ayarın satırı, adı, açıklaması, nereden geldiği ve düzenleme kutusu.
const AyarSatiri: React.FC<{
  alan: AyarAlani;
  deger: Deger;
  degisti: boolean;
  onDegistir: (d: Deger) => void;
  onGeriAl: () => void;
  onSifirla: () => void;
}> = ({ alan: a, deger, degisti, onDegistir, onGeriAl, onSifirla }) => {
  const [etiket, sinif] = KAYNAK_ETIKETI[a.kaynak];
  const girdi = 'w-full p-2 rounded-lg border border-paper-300 bg-white text-xs';
  const id = `ayar-${a.ad}`;
  return (
    <div className={`px-4 sm:px-5 py-3 grid grid-cols-1 md:grid-cols-[1fr_minmax(0,1.2fr)] gap-2 md:gap-4 ${degisti ? 'bg-amber-50/40' : ''}`}>
      <div className="space-y-0.5">
        <label htmlFor={id} className="text-xs font-semibold text-stone-800 flex flex-wrap items-center gap-2">
          {a.etiket}
          <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-sm border ${sinif}`}>{etiket}</span>
          {degisti && <span className="text-[10px] font-medium text-amber-800">kaydedilmedi</span>}
        </label>
        <p className="text-[11px] text-stone-500 leading-snug">{a.aciklama}</p>
        {a.cozulemedi && (
          <p className="text-[11px] text-rose-700">Kayıtlı şifre çözülemedi (sunucunun gizli anahtarı değişmiş). Şifreyi yeniden girin.</p>
        )}
      </div>
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          {a.tur === 'evet_hayir' ? (
            <label className="inline-flex items-center gap-2 text-xs text-stone-700 cursor-pointer pt-1.5">
              <input id={id} type="checkbox" checked={Boolean(deger)} onChange={(e) => onDegistir(e.target.checked)} className="w-4 h-4 accent-petrol" />
              {deger ? 'Açık' : 'Kapalı'}
            </label>
          ) : a.tur === 'adresler' ? (
            <textarea id={id} rows={2} value={Array.isArray(deger) ? deger.join('\n') : (deger as string) ?? ''}
              onChange={(e) => onDegistir(e.target.value)} className={`${girdi} font-mono`} />
          ) : a.tur === 'sifre' ? (
            <input id={id} type="password" autoComplete="new-password" value={typeof deger === 'string' ? deger : ''}
              onChange={(e) => (e.target.value ? onDegistir(e.target.value) : onGeriAl())}
              placeholder={a.dolu ? '•••••••• kayıtlı — değiştirmek için yazın' : 'Şifre yok'} className={girdi} />
          ) : (
            <input id={id} type={a.tur === 'sayi' ? 'number' : 'text'} min={a.en_az ?? undefined} max={a.en_cok ?? undefined}
              value={deger === null || deger === undefined ? '' : String(deger)}
              onChange={(e) => onDegistir(e.target.value)} className={`${girdi} ${a.tur === 'sayi' ? '' : 'font-mono'}`} />
          )}
        </div>
        {/* Kaydedilmemiş değişikliği geri al ya da panel değerini sıfırla (.env'e dön). */}
        {degisti ? (
          <button type="button" onClick={onGeriAl} title="Değişikliği geri al"
            className="p-2 rounded-lg border border-paper-300 text-stone-500 hover:bg-paper-200 cursor-pointer shrink-0">
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        ) : a.kaynak === 'panel' && (
          <button type="button" onClick={onSifirla} title=".env'deki değere dön"
            className="px-2 py-2 rounded-lg border border-paper-300 text-[11px] text-stone-600 hover:bg-paper-200 cursor-pointer shrink-0">
            Sıfırla
          </button>
        )}
      </div>
    </div>
  );
};

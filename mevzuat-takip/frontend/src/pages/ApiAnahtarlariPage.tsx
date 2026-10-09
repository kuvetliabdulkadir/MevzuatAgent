// "API Anahtarları" sayfası (sadece admin). Dış sistemlerin API'ye erişmesi için anahtar üretilir, iptal edilir.
// Anahtar sadece üretildiği an bir kez gösterilir, sunucuda kendisi değil özeti saklanır.
import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Ban, Copy, KeyRound, Plus, Shield } from 'lucide-react';
import { api } from '../api';
import { ApiAnahtari, ApiAnahtarlariCevabi, Mesaj } from '../types/api';
import { MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';
import { OnayPenceresi, OnaySorusu } from '../components/OnayPenceresi';
import { DokumanIndir } from './ApiKarsilamaPage';

const ROL_ADI = { tam: 'Tam yetki', admin: 'Yönetici', onaylayici: 'Onaylayıcı' } as const;
const DURUM: Record<ApiAnahtari['durum'], [string, string]> = {
  aktif: ['Aktif', 'bg-emerald-50 text-emerald-800 border-emerald-300'],
  iptal: ['İptal', 'bg-stone-100 text-stone-500 border-stone-300'],
  suresi_doldu: ['Süresi doldu', 'bg-amber-50 text-amber-800 border-amber-300'],
};

export const ApiAnahtarlariPage: React.FC = () => {
  // Liste, mesaj, yeni anahtar penceresi, az önce üretilen anahtar (bir kez gösterilir), onay sorusu.
  const [veri, setVeri] = useState<ApiAnahtarlariCevabi | null>(null);
  const [mesaj, setMesaj] = useState<Mesaj | null>(null);
  const [yeniAcik, setYeniAcik] = useState(false);
  const [yeniAnahtar, setYeniAnahtar] = useState<string | null>(null);
  const [soru, setSoru] = useState<(OnaySorusu & { calistir: () => void }) | null>(null);

  const yukle = useCallback(() => {
    api.get<ApiAnahtarlariCevabi>('/api-anahtarlari').then(setVeri).catch((e) => setMesaj({ tur: 'hata', mesaj: e.message }));
  }, []);
  useEffect(yukle, [yukle]);

  // İptal, önce onay penceresi sorar.
  const iptal = (a: ApiAnahtari) => setSoru({
    baslik: `'${a.ad}' anahtarı iptal edilsin mi?`,
    metin: 'Bu anahtarı kullanan sistem hemen erişimini kaybeder. İptal geri alınamaz, gerekirse yeni anahtar üretilir.',
    dugme: 'İptal et',
    tehlikeli: true,
    calistir: async () => {
      setSoru(null);
      try {
        const c = await api.post<ApiAnahtarlariCevabi>(`/api-anahtarlari/${a.id}/iptal`);
        setVeri((v) => ({ ...(v ?? {}), anahtarlar: c.anahtarlar }));
        if (c.mesaj) setMesaj(c.mesaj);
      } catch (e) {
        setMesaj({ tur: 'hata', mesaj: e instanceof Error ? e.message : 'İptal edilemedi.' });
      }
    },
  });

  const baslik = veri?.baslik ?? 'Authorization: Bearer';
  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-paper-300">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">API Anahtarları</h2>
          <p className="text-xs text-stone-500 mt-0.5">Dış sistemlerin panele girmeden API'yi kullanması için.</p>
        </div>
        <button onClick={() => { setMesaj(null); setYeniAnahtar(null); setYeniAcik(true); }}
          className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white text-xs font-semibold hover:bg-petrol-dark shadow-xs cursor-pointer">
          <Plus className="w-4 h-4" /> <span>Anahtar üret</span>
        </button>
      </div>

      <div className="p-3.5 sm:p-4 rounded-xl bg-paper-200 border border-paper-300 text-xs text-stone-600 flex items-start gap-3">
        <Shield className="w-5 h-5 text-petrol shrink-0 mt-0.5" />
        <div className="space-y-1 leading-relaxed">
          <p>Dış sistem her istekte anahtarı <code className="font-mono bg-white px-1 rounded-sm">{baslik} mvz_...</code> biçiminde gönderir. API Dokümanı sayfasında <strong>Authorize</strong> düğmesine girilince istekler o anahtarla atılır.</p>
          <p><strong>Tam yetki</strong> anahtarı her şeyi yapar (rapor onayı, kullanıcılar, ayarlar dahil), test ve entegrasyon içindir. Yönetici ve onaylayıcı anahtarı o rolün panelde yapabildiğini yapar.</p>
          <p>Her istek denetim kaydına yazılır. Anahtar sadece üretildiği an gösterilir; kaybedilirse iptal edilip yenisi üretilir.</p>
          <p>Panel hesabı olmayan biri de dokümanı <strong>/api/dokuman</strong> adresinde açabilir, istek denemek için anahtarını sağ üstteki Authorize'a girer. Ya da dokümanı indirip anahtarla birlikte gönderin:</p>
        </div>
      </div>
      <DokumanIndir />

      {/* Az önce üretilen anahtar, bir kez gösterilir. */}
      {yeniAnahtar && <YeniAnahtarKutusu anahtar={yeniAnahtar} onKapat={() => setYeniAnahtar(null)} />}
      {mesaj && <MesajKutusu tur={mesaj.tur}>{mesaj.mesaj}</MesajKutusu>}

      {veri && (veri.anahtarlar.length === 0 ? (
        <p className="text-xs text-stone-500">Henüz anahtar yok.</p>
      ) : (
        <div className="bg-white rounded-xl border border-paper-300 shadow-xs overflow-x-auto">
          <table className="w-full text-left text-xs min-w-[760px]">
            <thead className="bg-paper-100 border-b border-paper-300 text-stone-600 uppercase font-mono text-[11px]">
              <tr>
                <th className="px-4 py-3 font-semibold">Anahtar</th>
                <th className="px-4 py-3 font-semibold">Rol</th>
                <th className="px-4 py-3 font-semibold">Üretildi</th>
                <th className="px-4 py-3 font-semibold">Geçerlilik</th>
                <th className="px-4 py-3 font-semibold">Son kullanım</th>
                <th className="px-4 py-3 font-semibold">Durum</th>
                <th className="px-4 py-3 font-semibold text-right">İşlem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-paper-200">
              {veri.anahtarlar.map((a) => {
                const [durum, sinif] = DURUM[a.durum];
                return (
                  <tr key={a.id} className={a.durum === 'aktif' ? '' : 'opacity-60'}>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-stone-900">{a.ad}</div>
                      <div className="text-[11px] font-mono text-stone-500">{a.on_ek}…</div>
                    </td>
                    <td className="px-4 py-3 text-stone-700">{a.rol ? ROL_ADI[a.rol] : '—'}</td>
                    <td className="px-4 py-3 text-stone-600">{tarihSaat(a.olusturuldu)}<div className="text-[11px] text-stone-400">{a.olusturan}</div></td>
                    <td className="px-4 py-3 text-stone-600">{a.son_kullanma ? tarihSaat(a.son_kullanma) : 'Süresiz'}</td>
                    <td className="px-4 py-3 text-stone-600">{a.son_kullanim ? tarihSaat(a.son_kullanim) : 'Hiç kullanılmadı'}</td>
                    <td className="px-4 py-3"><span className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border whitespace-nowrap ${sinif}`}>{durum}</span></td>
                    <td className="px-4 py-3 text-right">
                      {a.durum === 'aktif' && (
                        <button onClick={() => iptal(a)}
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-rose-300 text-rose-700 hover:bg-rose-50 cursor-pointer">
                          <Ban className="w-3.5 h-3.5" /> İptal et
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}

      {soru && <OnayPenceresi {...soru} onEvet={soru.calistir} onVazgec={() => setSoru(null)} />}
      {yeniAcik && (
        <YeniAnahtarFormu onKapat={() => setYeniAcik(false)}
          onUretildi={(c) => { setYeniAcik(false); setVeri((v) => ({ ...(v ?? {}), anahtarlar: c.anahtarlar })); setYeniAnahtar(c.anahtar ?? null); if (c.mesaj) setMesaj(c.mesaj); }} />
      )}
    </div>
  );
};

// Üretilen anahtarı bir kez gösteren kutu, kopyala düğmesiyle.
const YeniAnahtarKutusu: React.FC<{ anahtar: string; onKapat: () => void }> = ({ anahtar, onKapat }) => {
  const [kopyalandi, setKopyalandi] = useState(false);
  const kopyala = () => {
    navigator.clipboard.writeText(anahtar).then(() => setKopyalandi(true)).catch(() => setKopyalandi(false));
  };
  return (
    <div className="p-4 rounded-xl border-2 border-amber-400 bg-amber-50 space-y-2 text-xs">
      <div className="flex items-center gap-2 font-semibold text-amber-900"><KeyRound className="w-4 h-4" /> Yeni anahtar — şimdi kopyalayın, bir daha gösterilmeyecek</div>
      <div className="flex flex-col sm:flex-row gap-2">
        <code className="flex-1 min-w-0 break-all p-2.5 rounded-lg bg-white border border-amber-300 font-mono text-stone-900 select-all">{anahtar}</code>
        <button onClick={kopyala} className="flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-white border border-amber-300 text-amber-900 font-semibold hover:bg-amber-100 cursor-pointer">
          <Copy className="w-3.5 h-3.5" /> {kopyalandi ? 'Kopyalandı' : 'Kopyala'}
        </button>
        <button onClick={onKapat} className="px-3 py-2 rounded-lg border border-amber-300 text-amber-900 hover:bg-amber-100 cursor-pointer">Kaydettim, kapat</button>
      </div>
    </div>
  );
};

// Yeni anahtar penceresi, ad, rol, süre.
const YeniAnahtarFormu: React.FC<{ onKapat: () => void; onUretildi: (c: ApiAnahtarlariCevabi) => void }> = ({ onKapat, onUretildi }) => {
  const [ad, setAd] = useState('');
  const [rol, setRol] = useState<'tam' | 'onaylayici' | 'admin'>('tam');
  const [gun, setGun] = useState<number | null>(90);
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);

  useEffect(() => {
    const tus = (e: KeyboardEvent) => { if (e.key === 'Escape') onKapat(); };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat]);

  const uret = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    setBekliyor(true);
    try {
      onUretildi(await api.post<ApiAnahtarlariCevabi>('/api-anahtarlari', { ad, rol, gun }));
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Üretilemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/60 p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true" className="bg-white rounded-xl shadow-xl border border-paper-300 w-full max-w-md overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-paper-300 flex items-center justify-between bg-paper-50">
          <h3 className="font-semibold text-stone-900 text-sm">Yeni API anahtarı</h3>
          <button onClick={onKapat} aria-label="Kapat" className="text-stone-400 hover:text-stone-600 text-sm font-bold cursor-pointer p-1">×</button>
        </div>
        <form onSubmit={uret} className="p-4 sm:p-6 space-y-4 text-xs">
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Ad (hangi sistem kullanacak)</span>
            <input type="text" required maxLength={100} value={ad} onChange={(e) => setAd(e.target.value)}
              placeholder="ör. Muhasebe entegrasyonu" className="w-full p-2.5 rounded-lg border border-paper-300 bg-white" />
          </label>
          <fieldset className="space-y-1.5">
            <legend className="font-semibold text-stone-700">Rol</legend>
            {(['tam', 'onaylayici', 'admin'] as const).map((r) => (
              <label key={r} className="flex items-start gap-2 p-2 rounded-sm bg-paper-100 hover:bg-paper-200 cursor-pointer">
                <input type="radio" name="rol" checked={rol === r} onChange={() => setRol(r)} className="accent-petrol mt-0.5" />
                <span>
                  <span className="font-medium text-stone-800">{ROL_ADI[r]}</span>
                  <span className="block text-stone-500">{r === 'tam'
                    ? 'Her şey: raporları onaylar ve gönderir, kullanıcılar, ayarlar, denetim, kaynak, konu. Test ve entegrasyon için.'
                    : r === 'onaylayici'
                      ? 'Raporları okur, onaylar/reddeder ve gönderir; kaynak, konu, grup, tarama.'
                      : 'Kullanıcılar, ayarlar, denetim kaydı, kaynak ve konu. Rapor onaylayamaz.'}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Geçerlilik</span>
            <select value={gun ?? ''} onChange={(e) => setGun(e.target.value ? Number(e.target.value) : null)}
              className="w-full p-2.5 rounded-lg border border-paper-300 bg-white">
              <option value="30">30 gün</option>
              <option value="90">90 gün</option>
              <option value="365">1 yıl</option>
              <option value="">Süresiz</option>
            </select>
          </label>
          {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onKapat} className="px-4 py-2 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer">Vazgeç</button>
            <button type="submit" disabled={bekliyor}
              className="px-4 py-2 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark disabled:opacity-50 cursor-pointer">
              {bekliyor ? 'Üretiliyor…' : 'Üret'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
};

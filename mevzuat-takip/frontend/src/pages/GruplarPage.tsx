// "Alıcı Grupları" sayfası, grupların listesi ve ekleme/düzenleme penceresi.
import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Edit2, Mail, Plus, Shield } from 'lucide-react';
import { api } from '../api';
import { AliciGrubu, GrupListesi } from '../types/api';
import { MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';

// Gruplar silinmez, pasifleştirilir, geçmiş gönderimlerde adı görünmeye devam eder ve denetim izi bozulmaz.
export const GruplarPage: React.FC = () => {
  // Sunucudan gelen liste, mesajlar ve düzenlenen grup. 'yeni' yeni grup ekleniyor demek, null pencere kapalı demek.
  const [veri, setVeri] = useState<GrupListesi | null>(null);
  const [hata, setHata] = useState('');
  const [basari, setBasari] = useState('');
  const [duzenlenen, setDuzenlenen] = useState<AliciGrubu | 'yeni' | null>(null);

  // Grupları sunucudan çeker.
  const yukle = useCallback(() => {
    api.get<GrupListesi>('/gruplar').then(setVeri).catch((e) => setHata(e.message));
  }, []);
  useEffect(yukle, [yukle]);

  // Grup kaydedilince, pencereyi kapat, mesaj göster, listeyi yenile.
  const kaydedildi = (grup: AliciGrubu) => {
    setDuzenlenen(null);
    setBasari(`'${grup.ad}' grubu kaydedildi.`);
    yukle();
  };

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Başlık ve Yeni Grup düğmesi. */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4 pb-4 border-b border-paper-300">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">Alıcı Grupları</h2>
          <p className="text-xs text-stone-500 mt-0.5">Onaylanan raporun kime, hangi iş kollarının kalemleriyle gideceği.</p>
        </div>
        <button onClick={() => { setBasari(''); setDuzenlenen('yeni'); }}
          className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white text-xs font-semibold hover:bg-petrol-dark shadow-xs transition-all cursor-pointer">
          <Plus className="w-4 h-4" />
          <span>Yeni Grup</span>
        </button>
      </div>

      {/* Kuralların kısa açıklaması. */}
      <div className="p-3.5 sm:p-4 rounded-xl bg-paper-200 border border-paper-300 text-xs text-stone-700 flex items-start gap-3">
        <Shield className="w-5 h-5 text-petrol shrink-0 mt-0.5" />
        <div className="space-y-1 leading-relaxed text-stone-600">
          <p>Her grup sadece seçtiği <strong>iş kollarının</strong> kalemlerini alır; hiç kalemi yoksa o gruba mail gitmez.</p>
          <p>Genel düzenlemeler (MASAK, vergi usulü vb.) <strong>Ortak</strong> iş kolundadır ve sadece Ortak'ı seçen gruplara gider.</p>
          <p>Birden çok gruptaki kişi tek mail alır. Değişiklikler bundan sonra onaylanacak raporlara uygulanır.</p>
        </div>
      </div>

      {/* Mesajlar ve hiçbir gruba gitmeyen iş kolu uyarısı. */}
      {basari && <MesajKutusu tur="basari">{basari}</MesajKutusu>}
      {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
      {veri && veri.kapsanmayan.length > 0 && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 border border-amber-300 text-xs text-amber-900">
          <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
          <span>Şu iş kollarının kalemleri hiçbir aktif gruba gitmiyor: <strong>{veri.kapsanmayan.join(', ')}</strong></span>
        </div>
      )}

      {/* Grup tablosu, ad, iş kolları, adresler, durum ve düzenle düğmesi. */}
      {veri && (
        <div className="bg-white rounded-xl border border-paper-300 shadow-xs overflow-hidden">
          {veri.gruplar.length === 0 ? (
            <p className="p-8 text-center text-xs text-stone-500">
              Henüz alıcı grubu yok. Grup tanımlanmadan onaylanan rapor kimseye gönderilemez.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs min-w-[760px]">
                <thead className="bg-paper-100 border-b border-paper-300 text-stone-600 uppercase font-mono text-[11px]">
                  <tr>
                    <th className="px-5 py-3 font-semibold">Grup</th>
                    <th className="px-5 py-3 font-semibold">İş kolları</th>
                    <th className="px-5 py-3 font-semibold">Adresler</th>
                    <th className="px-5 py-3 font-semibold text-right">Durum</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-paper-200">
                  {veri.gruplar.map((g) => (
                    <tr key={g.id} className={`align-top hover:bg-paper-50 ${g.aktif ? '' : 'opacity-60'}`}>
                      <td className="px-5 py-4">
                        <div className="font-semibold text-stone-900">{g.ad}</div>
                        <div className="text-[10px] text-stone-500 mt-0.5">Güncellendi: {tarihSaat(g.guncellendi)}</div>
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex flex-wrap gap-1">
                          {g.is_kollari.map((ik) => (
                            <span key={ik} className="px-2 py-0.5 rounded-sm text-[10px] font-medium bg-petrol/10 text-petrol border border-petrol/20">{ik}</span>
                          ))}
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex flex-col gap-1">
                          {g.adresler.map((a) => (
                            <span key={a} className="inline-flex items-center gap-1.5 text-[11px] font-mono text-stone-700">
                              <Mail className="w-3 h-3 text-stone-400 shrink-0" />{a}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-5 py-4 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <span className={`px-3 py-1 rounded-full text-xs font-semibold border ${
                            g.aktif ? 'bg-emerald-50 text-emerald-800 border-emerald-300' : 'bg-stone-100 text-stone-500 border-stone-300'
                          }`}>
                            {g.aktif ? 'Aktif' : 'Pasif'}
                          </span>
                          <button onClick={() => { setBasari(''); setDuzenlenen(g); }} title="Düzenle" aria-label={`${g.ad} grubunu düzenle`}
                            className="p-1.5 rounded-sm text-stone-400 hover:text-petrol hover:bg-paper-200 transition-colors cursor-pointer">
                            <Edit2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Düzenlenen grup varsa form penceresini aç. */}
      {duzenlenen && veri && (
        <GrupFormu
          key={duzenlenen === 'yeni' ? 'yeni' : duzenlenen.id}
          grup={duzenlenen === 'yeni' ? null : duzenlenen}
          secenekler={veri.is_kollari}
          onKapat={() => setDuzenlenen(null)}
          onKaydedildi={kaydedildi}
        />
      )}
    </div>
  );
};

// Grup ekleme/düzenleme penceresi.
const GrupFormu: React.FC<{
  grup: AliciGrubu | null;
  secenekler: string[];
  onKapat: () => void;
  onKaydedildi: (grup: AliciGrubu) => void;
}> = ({ grup, secenekler, onKapat, onKaydedildi }) => {
  // Formun alanları (düzenlemede grubun mevcut değerleriyle başlar).
  const [ad, setAd] = useState(grup?.ad ?? '');
  const [isKollari, setIsKollari] = useState<string[]>(grup?.is_kollari ?? []);
  const [adresler, setAdresler] = useState((grup?.adresler ?? []).join('\n'));
  const [aktif, setAktif] = useState(grup?.aktif ?? true);
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);

  // Esc ile kapanır.
  useEffect(() => {
    const tus = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onKapat();
    };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat]);

  // Bir iş kolunun kutusu tıklanınca listeye ekle ya da çıkar.
  const isKolu = (ik: string) =>
    setIsKollari(isKollari.includes(ik) ? isKollari.filter((x) => x !== ik) : [...isKollari, ik]);

  // Kaydet, düzenlemedeysek PUT, yeni gruptaysa POST isteği.
  const kaydet = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    setBekliyor(true);
    const govde = {
      ad,
      is_kollari: isKollari,
      // Satır, virgül ya da noktalı virgülle ayrılmış adresler, ayrıntılı doğrulama sunucuda.
      adresler: adresler.split(/[\s,;]+/).filter(Boolean),
      aktif,
    };
    try {
      onKaydedildi(grup ? await api.put<AliciGrubu>(`/gruplar/${grup.id}`, govde) : await api.post<AliciGrubu>('/gruplar', govde));
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Kaydedilemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/60 p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true"
        className="bg-white rounded-xl shadow-xl border border-paper-300 w-full max-w-md max-h-[92vh] flex flex-col overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-paper-300 flex items-center justify-between bg-paper-50 shrink-0">
          <h3 className="font-semibold text-stone-900 text-sm truncate pr-2">{grup ? `Grubu düzenle: ${grup.ad}` : 'Yeni alıcı grubu'}</h3>
          <button onClick={onKapat} aria-label="Kapat" className="text-stone-400 hover:text-stone-600 text-sm font-bold cursor-pointer p-1 shrink-0">×</button>
        </div>

        {/* Form alanları, ad, iş kolları, adresler, aktif. */}
        <form onSubmit={kaydet} className="p-4 sm:p-6 space-y-4 text-xs overflow-y-auto">
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Grup adı</span>
            <input type="text" required maxLength={100} value={ad} onChange={(e) => setAd(e.target.value)}
              placeholder="Örn: Kuyum Ekibi" className="w-full p-2.5 rounded-lg border border-paper-300 bg-white" />
          </label>

          <fieldset className="space-y-1.5">
            <legend className="font-semibold text-stone-700">İş kolları <span className="font-normal text-stone-500">— grup sadece bunların kalemlerini alır</span></legend>
            {secenekler.map((ik) => (
              <label key={ik} className="flex items-center gap-2 p-2 rounded-sm bg-paper-100 hover:bg-paper-200 cursor-pointer">
                <input type="checkbox" checked={isKollari.includes(ik)} onChange={() => isKolu(ik)} className="accent-petrol" />
                <span className="font-medium text-stone-800">{ik}</span>
                {ik === 'Ortak' && <span className="text-stone-500">(MASAK, vergi usulü gibi genel düzenlemeler)</span>}
              </label>
            ))}
          </fieldset>

          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">E-posta adresleri <span className="font-normal text-stone-500">(her satıra bir adres)</span></span>
            <textarea rows={5} value={adresler} onChange={(e) => setAdresler(e.target.value)}
              className="w-full p-2.5 rounded-lg border border-paper-300 bg-white font-mono" />
          </label>

          <label className="flex items-start gap-2 cursor-pointer">
            <input type="checkbox" checked={aktif} onChange={(e) => setAktif(e.target.checked)} className="mt-0.5 accent-petrol" />
            <span><strong>Aktif</strong> <span className="text-stone-500">— pasif grup rapor almaz; geçmiş gönderimlerde adı görünmeye devam eder</span></span>
          </label>

          {/* Hata ve düğmeler. */}
          {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}

          <div className="pt-3 border-t border-paper-200 flex flex-col-reverse sm:flex-row justify-end gap-2">
            <button type="button" onClick={onKapat} className="w-full sm:w-auto px-4 py-2 text-stone-600 hover:bg-paper-200 rounded-lg cursor-pointer">Vazgeç</button>
            <button type="submit" disabled={bekliyor}
              className="w-full sm:w-auto px-4 py-2 bg-petrol text-white rounded-lg font-semibold hover:bg-petrol-dark disabled:opacity-60 cursor-pointer">
              {bekliyor ? 'Kaydediliyor…' : 'Kaydet'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
};

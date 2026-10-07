// "Kullanıcılar" sayfası (sadece admin), kullanıcı ekleme, parola linki gönderme, pasifleştirme.
import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { KeyRound, Plus, Shield, UserCheck, UserMinus, UserPlus } from 'lucide-react';
import { api } from '../api';
import { KullaniciIslemCevabi, KullaniciListesi, Mesaj, Rol, YonetilenKullanici } from '../types/api';
import { MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';
import { OnayPenceresi, OnaySorusu } from '../components/OnayPenceresi';

// Rol adlarının ekranda görünen karşılıkları.
const ROL_ADI: Record<Rol, string> = { admin: 'Yönetici', onaylayici: 'Onaylayıcı' };

// Sadece admin görür. Parolayı admin bilmez, kişiye maille link gider, parolasını kendisi belirler.
// Kullanıcı önce pasifleştirilir, belli gün pasif kalınca hesabı silinir. Onay geçmişinde "Silinmiş kullanıcı" olarak görünür.
export const KullanicilarPage: React.FC<{ benimId: number }> = ({ benimId }) => {
  // Liste, mesaj, yeni kullanıcı penceresi açık mı, hangi satırda işlem sürüyor, ekrandaki onay sorusu.
  const [veri, setVeri] = useState<KullaniciListesi | null>(null);
  const [mesaj, setMesaj] = useState<Mesaj | null>(null);
  const [yeniAcik, setYeniAcik] = useState(false);
  const [bekleyen, setBekleyen] = useState<number | null>(null);
  const [soru, setSoru] = useState<(OnaySorusu & { calistir: () => void }) | null>(null);

  // Kullanıcıları sunucudan çeker.
  const yukle = useCallback(() => {
    api.get<KullaniciListesi>('/kullanicilar').then(setVeri).catch((e) => setMesaj({ tur: 'hata', mesaj: e.message }));
  }, []);
  useEffect(yukle, [yukle]);

  // Bir kullanıcı üzerinde işlem yapar (link gönder, aktif/pasif) ve listeyi cevaptaki güncel haliyle değiştirir.
  const islem = async (k: YonetilenKullanici, yol: string, govde: unknown) => {
    setSoru(null);
    setMesaj(null);
    setBekleyen(k.id);
    try {
      const cevap = await api.post<Partial<KullaniciIslemCevabi>>(yol, govde);
      if (cevap.kullanicilar && veri) setVeri({ ...veri, kullanicilar: cevap.kullanicilar });
      if (cevap.mesaj) setMesaj(cevap.mesaj);
    } catch (e) {
      setMesaj({ tur: 'hata', mesaj: e instanceof Error ? e.message : 'İşlem yapılamadı.' });
    } finally {
      setBekleyen(null);
    }
  };

  // "Parola linki gönder" düğmesi, önce onay penceresi sorar.
  const linkGonder = (k: YonetilenKullanici) => setSoru({
    baslik: k.son_giris ? 'Parola linki gönderilsin mi?' : 'Davet yeniden gönderilsin mi?',
    metin: `${k.eposta} adresine parolasını belirleyeceği bir link gider. Daha önce gönderilmiş link geçersiz olur.`,
    dugme: 'Gönder',
    calistir: () => islem(k, `/kullanicilar/${k.id}/parola-linki`, {}),
  });
  // "Pasifleştir / Yeniden aç" düğmesi, önce onay penceresi sorar.
  const silmeGun = veri?.pasif_silme_gun ?? 0;
  const aktiflik = (k: YonetilenKullanici) => setSoru(k.aktif ? {
    baslik: `${k.ad} pasifleştirilsin mi?`,
    metin: 'Panele giremez, açık oturumu hemen kapanır ve bekleyen linkleri geçersiz olur. '
      + (silmeGun > 0
        ? `${silmeGun} gün içinde yeniden açılmazsa hesabı tamamen silinir (ad, e-posta, parola), geçmiş işlemlerinde "Silinmiş kullanıcı" yazar.`
        : 'Geçmişteki işlemlerinde adı görünmeye devam eder; istediğiniz zaman yeniden açabilirsiniz.'),
    dugme: 'Pasifleştir',
    tehlikeli: true,
    calistir: () => islem(k, `/kullanicilar/${k.id}/aktiflik`, { aktif: false }),
  } : {
    baslik: `${k.ad} yeniden açılsın mı?`,
    metin: 'Eski parolasıyla tekrar giriş yapabilir. Parolasını hatırlamıyorsa açtıktan sonra parola linki gönderin.',
    dugme: 'Yeniden aç',
    calistir: () => islem(k, `/kullanicilar/${k.id}/aktiflik`, { aktif: true }),
  });

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Başlık ve Kullanıcı ekle düğmesi (panel adresi tanımlı değilse kapalı). */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-paper-300">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">Kullanıcılar</h2>
          <p className="text-xs text-stone-500 mt-0.5">Panele kimlerin girebileceği. Parolaları kişiler kendileri belirler.</p>
        </div>
        <button onClick={() => { setMesaj(null); setYeniAcik(true); }} disabled={!veri?.panel_adresi_var}
          className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white text-xs font-semibold hover:bg-petrol-dark disabled:opacity-50 shadow-xs cursor-pointer">
          <Plus className="w-4 h-4" />
          <span>Kullanıcı ekle</span>
        </button>
      </div>

      {/* Kuralların kısa açıklaması. */}
      <div className="p-3.5 sm:p-4 rounded-xl bg-paper-200 border border-paper-300 text-xs text-stone-600 flex items-start gap-3">
        <Shield className="w-5 h-5 text-petrol shrink-0 mt-0.5" />
        <div className="space-y-1 leading-relaxed">
          <p>Yeni kullanıcıya <strong>davet maili</strong> gider; parolasını linkle kendisi belirler (link 3 gün geçerli).</p>
          <p>Parolasını unutan için <strong>parola linki gönder</strong>: 1 saat geçerli, tek kullanımlık. Eski parola link kullanılana kadar çalışır.</p>
          {silmeGun > 0 ? (
            <p>Pasifleştirilen hesap <strong>{silmeGun} gün</strong> içinde yeniden açılmazsa tamamen silinir; geçmişteki onaylarında "Silinmiş kullanıcı" yazar.</p>
          ) : (
            <p>Kullanıcılar silinmez, <strong>pasifleştirilir</strong>; geçmişteki onaylarında adı görünmeye devam eder.</p>
          )}
        </div>
      </div>

      {/* Panel adresi yoksa uyarı, son işlemin mesajı. */}
      {veri && !veri.panel_adresi_var && (
        <MesajKutusu tur="hata">
          Sunucuda MEVZUAT_PANEL_ADRESI tanımlı değil; davet ve parola linki gönderilemez. Sunucu yöneticisine bildirin.
        </MesajKutusu>
      )}
      {mesaj && <MesajKutusu tur={mesaj.tur}>{mesaj.mesaj}</MesajKutusu>}

      {/* Kullanıcı tablosu, ad, rol, son giriş, durum, işlemler. */}
      {veri && (
        <div className="bg-white rounded-xl border border-paper-300 shadow-xs overflow-x-auto">
          <table className="w-full text-left text-xs min-w-[720px]">
            <thead className="bg-paper-100 border-b border-paper-300 text-stone-600 uppercase font-mono text-[11px]">
              <tr>
                <th className="px-5 py-3 font-semibold">Kullanıcı</th>
                <th className="px-5 py-3 font-semibold">Rol</th>
                <th className="px-5 py-3 font-semibold">Son giriş</th>
                <th className="px-5 py-3 font-semibold">Durum</th>
                <th className="px-5 py-3 font-semibold text-right">İşlem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-paper-200">
              {veri.kullanicilar.map((k) => (
                <tr key={k.id} className={`hover:bg-paper-50 ${k.aktif ? '' : 'opacity-60'}`}>
                  <td className="px-5 py-3">
                    <div className="font-semibold text-stone-900">{k.ad}{k.id === benimId && <span className="ml-1.5 text-[10px] text-stone-500">(siz)</span>}</div>
                    <div className="text-[11px] font-mono text-stone-500">{k.eposta}</div>
                  </td>
                  <td className="px-5 py-3">
                    <span className="inline-flex items-center gap-1 text-stone-700">
                      {k.rol === 'admin' ? <Shield className="w-3.5 h-3.5 text-petrol" /> : <UserCheck className="w-3.5 h-3.5 text-amber-700" />}
                      {ROL_ADI[k.rol]}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-stone-600">{tarihSaat(k.son_giris)}</td>
                  <td className="px-5 py-3">
                    <DurumEtiketi k={k} />
                    {k.silinecek && <div className="mt-1 text-[11px] text-rose-700">{tarihSaat(k.silinecek)} silinecek</div>}
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center justify-end gap-1.5">
                      {/* Aktif kullanıcıya link gönder düğmesi. */}
                      {k.aktif && (
                        <button onClick={() => linkGonder(k)} disabled={bekleyen === k.id || !veri.panel_adresi_var}
                          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 disabled:opacity-50 cursor-pointer">
                          <KeyRound className="w-3.5 h-3.5" />
                          {k.son_giris ? 'Parola linki gönder' : 'Daveti yeniden gönder'}
                        </button>
                      )}
                      {/* Kendisi dışındakilere pasifleştir / yeniden aç düğmesi. */}
                      {k.id !== benimId && (
                        <button onClick={() => aktiflik(k)} disabled={bekleyen === k.id}
                          className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg border disabled:opacity-50 cursor-pointer ${
                            k.aktif ? 'border-rose-300 text-rose-700 hover:bg-rose-50' : 'border-emerald-300 text-emerald-700 hover:bg-emerald-50'}`}>
                          {k.aktif ? <UserMinus className="w-3.5 h-3.5" /> : <UserPlus className="w-3.5 h-3.5" />}
                          {k.aktif ? 'Pasifleştir' : 'Yeniden aç'}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Onay penceresi ve yeni kullanıcı formu. */}
      {soru && <OnayPenceresi {...soru} onEvet={soru.calistir} onVazgec={() => setSoru(null)} />}

      {yeniAcik && (
        <YeniKullaniciFormu onKapat={() => setYeniAcik(false)}
          onEklendi={(c) => { setYeniAcik(false); setMesaj(c.mesaj); if (veri) setVeri({ ...veri, kullanicilar: c.kullanicilar }); }} />
      )}
    </div>
  );
};

// Durum etiketi, Pasif / Geçici kilitli / Davet bekliyor / Aktif.
const DurumEtiketi: React.FC<{ k: YonetilenKullanici }> = ({ k }) => {
  const [etiket, sinif] = !k.aktif ? ['Pasif', 'bg-stone-100 text-stone-500 border-stone-300']
    : k.kilitli ? ['Geçici kilitli', 'bg-rose-50 text-rose-800 border-rose-300']
      : k.davet_bekliyor ? ['Davet bekliyor', 'bg-amber-50 text-amber-800 border-amber-300']
        : ['Aktif', 'bg-emerald-50 text-emerald-800 border-emerald-300'];
  return <span className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border whitespace-nowrap ${sinif}`}>{etiket}</span>;
};

// Yeni kullanıcı penceresi, ad, e-posta, rol.
const YeniKullaniciFormu: React.FC<{ onKapat: () => void; onEklendi: (c: KullaniciIslemCevabi) => void }> = ({
  onKapat, onEklendi,
}) => {
  const [ad, setAd] = useState('');
  const [eposta, setEposta] = useState('');
  const [rol, setRol] = useState<Rol>('onaylayici');
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);

  useEffect(() => {
    const tus = (e: KeyboardEvent) => { if (e.key === 'Escape') onKapat(); };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat]);

  // Kaydet, kullanıcıyı ekle, sunucu davet mailini kendisi gönderir.
  const kaydet = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    setBekliyor(true);
    try {
      onEklendi(await api.post<KullaniciIslemCevabi>('/kullanicilar', { ad, eposta, rol }));
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Eklenemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  const girdi = 'w-full p-2.5 rounded-lg border border-paper-300 bg-white';
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/60 p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true" className="bg-white rounded-xl shadow-xl border border-paper-300 w-full max-w-md overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-paper-300 flex items-center justify-between bg-paper-50">
          <h3 className="font-semibold text-stone-900 text-sm">Yeni kullanıcı</h3>
          <button onClick={onKapat} aria-label="Kapat" className="text-stone-400 hover:text-stone-600 text-sm font-bold cursor-pointer p-1">×</button>
        </div>
        {/* Form alanları. */}
        <form onSubmit={kaydet} className="p-4 sm:p-6 space-y-4 text-xs">
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Ad soyad</span>
            <input type="text" required maxLength={200} value={ad} onChange={(e) => setAd(e.target.value)} className={girdi} />
          </label>
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">E-posta</span>
            <input type="email" required maxLength={254} value={eposta} onChange={(e) => setEposta(e.target.value)} className={girdi} />
          </label>
          {/* Rol seçimi ve her rolün açıklaması. */}
          <fieldset className="space-y-1.5">
            <legend className="font-semibold text-stone-700">Rol</legend>
            {(['onaylayici', 'admin'] as Rol[]).map((r) => (
              <label key={r} className="flex items-start gap-2 p-2 rounded-sm bg-paper-100 hover:bg-paper-200 cursor-pointer">
                <input type="radio" name="rol" checked={rol === r} onChange={() => setRol(r)} className="accent-petrol mt-0.5" />
                <span>
                  <span className="font-medium text-stone-800">{ROL_ADI[r]}</span>
                  <span className="block text-stone-500">{r === 'onaylayici'
                    ? 'Raporları onaylar/reddeder; kaynak, konu, grup ve tarama ayarlarını yönetir.'
                    : 'Kurtarma: kullanıcılar, parola linkleri, denetim kaydı, geri alma. Rapor onaylayamaz.'}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {/* Hata ve düğmeler. */}
          {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onKapat} className="px-4 py-2 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer">Vazgeç</button>
            <button type="submit" disabled={bekliyor}
              className="px-4 py-2 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark disabled:opacity-50 cursor-pointer">
              {bekliyor ? 'Ekleniyor…' : 'Ekle ve davet gönder'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
};

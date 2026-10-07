// "Denetim Kaydı" sayfası (sadece admin), kim, ne zaman, ne yaptı. İşleme ve kişiye göre süzülebilir.
import React, { useCallback, useEffect, useState } from 'react';
import { History } from 'lucide-react';
import { api } from '../api';
import { DenetimKaydi, DenetimListesi } from '../types/api';
import { MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';

// Sunucudaki işlem adlarının okunur karşılıkları. Listede olmayan yeni bir işlem kendi adıyla görünür.
// İşlem adlarının ekranda görünen karşılıkları.
const ISLEM_ADI: Record<string, string> = {
  giris: 'Giriş', giris_basarisiz: 'Hatalı giriş', giris_kilitli: 'Kilitli hesapla giriş denemesi',
  hesap_kilitlendi: 'Hesap geçici kilitlendi', parola_dogrulandi: 'Parola doğru (kod bekleniyor)',
  mfa_kuruldu: 'İki adımlı doğrulama kuruldu', mfa_sifirlandi: 'İki adımlı doğrulama sıfırlandı',
  onay: 'Rapor onaylandı', ret: 'Rapor reddedildi',
  elle_tekrar_gonderim: 'Mail elle tekrar gönderildi', elle_gonderildi_isaretlendi: 'Mail "gönderildi" işaretlendi',
  grup_degistir: 'Alıcı grubu değişti',
  kaynak_ekle: 'Kaynak eklendi', kaynak_degistir: 'Kaynak değişti', kaynak_kaldir: 'Kaynak kaldırıldı',
  kaynak_geri_getir: 'Kaynak geri getirildi', kaynak_geri_al: 'Kaynak önceki hale döndürüldü',
  konu_ekle: 'Konu eklendi', konu_degistir: 'Konu değişti', konu_aktif: 'Konu açıldı', konu_pasif: 'Konu kapatıldı',
  konu_geri_al: 'Konu önceki hale döndürüldü', ayar_ice_aktar: 'Ayar dosyası içe aktarıldı',
  tarama_istegi: '"Şimdi tara" istendi', tarama_saatleri: 'Tarama saatleri değişti',
  kullanici_eklendi: 'Kullanıcı kaydı oluştu', kullanici_eklendi_panel: 'Kullanıcı eklendi',
  kullanici_pasif: 'Kullanıcı pasifleştirildi', kullanici_aktif: 'Kullanıcı yeniden açıldı',
  parola_linki_davet: 'Davet maili gönderildi', parola_linki_sifirlama: 'Parola linki gönderildi',
  parola_belirlendi: 'Parola linkle belirlendi', parola_degistirildi: 'Parola değiştirildi',
  yetkisiz_karar_denemesi: 'Yetkisiz onay denemesi', yetkisiz_grup_denemesi: 'Yetkisiz grup denemesi',
  yetkisiz_ayar_denemesi: 'Yetkisiz ayar denemesi', yetkisiz_kurtarma_denemesi: 'Yetkisiz geri alma denemesi',
  yetkisiz_yonetim_denemesi: 'Yetkisiz yönetim denemesi',
};
const islemAdi = (islem: string) => ISLEM_ADI[islem] ?? islem;
// Hatalı giriş, kilit ve yetkisiz denemeler kırmızı gösterilir.
const UYARI = /basarisiz|kilit|yetkisiz/;

// Detay, önce/sonra gibi büyük alanlar kısaltılır, tamamı satıra tıklayınca görünür.
function detayOzeti(d: Record<string, unknown>): string {
  return Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && !(typeof v === 'object' && Object.keys(v as object).length > 6))
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ');
}

export const DenetimPage: React.FC = () => {
  // Sayfadaki veriler, seçili süzgeçler, açık satır, "daha eski var mı", hata.
  const [veri, setVeri] = useState<DenetimListesi | null>(null);
  const [islem, setIslem] = useState('');
  const [kisi, setKisi] = useState('');
  const [acik, setAcik] = useState<number | null>(null);
  const [devamVar, setDevamVar] = useState(false);
  const [hata, setHata] = useState('');

  // Seçili süzgeçlere göre sunucuya gidecek adresi kurar.
  const sorgu = (once?: number) => {
    const p = new URLSearchParams();
    if (islem) p.set('islem', islem);
    if (kisi) p.set('kullanici_id', kisi);
    if (once) p.set('once', String(once));
    return `/denetim?${p}`;
  };

  // Süzgeç değişince listeyi yeniden yükle. 100 kayıt geldiyse daha eskisi de olabilir.
  const yukle = useCallback(() => {
    setHata('');
    api.get<DenetimListesi>(sorgu())
      .then((v) => { setVeri(v); setDevamVar(v.kayitlar.length === 100); })
      .catch((e) => setHata(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [islem, kisi]);
  useEffect(yukle, [yukle]);

  // "Daha eski kayıtlar", listedeki son kaydın numarasından öncekileri getirip listeye ekle.
  const dahaEski = () => {
    if (!veri || veri.kayitlar.length === 0) return;
    api.get<DenetimListesi>(sorgu(veri.kayitlar[veri.kayitlar.length - 1].id))
      .then((v) => { setVeri({ ...veri, kayitlar: [...veri.kayitlar, ...v.kayitlar] }); setDevamVar(v.kayitlar.length === 100); })
      .catch((e) => setHata(e.message));
  };

  const secim = 'p-2 rounded-lg border border-paper-300 bg-white text-xs';
  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Başlık. */}
      <div className="pb-4 border-b border-paper-300">
        <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight flex items-center gap-2">
          <History className="w-5 h-5 text-petrol" /> Denetim Kaydı
        </h2>
        <p className="text-xs text-stone-500 mt-0.5">Kim, ne zaman, ne yaptı: girişler, onaylar, ayar değişiklikleri, yetkisiz denemeler. Kayıtlar silinmez.</p>
      </div>

      {/* Süzgeç kutuları, işlem ve kişi. */}
      <div className="flex flex-col sm:flex-row gap-2">
        <select value={islem} onChange={(e) => setIslem(e.target.value)} className={secim} aria-label="İşleme göre süz">
          <option value="">Bütün işlemler</option>
          {veri?.islemler.map((i) => <option key={i} value={i}>{islemAdi(i)}</option>)}
        </select>
        <select value={kisi} onChange={(e) => setKisi(e.target.value)} className={secim} aria-label="Kişiye göre süz">
          <option value="">Bütün kişiler</option>
          {veri?.kullanicilar.map((k) => <option key={k.id} value={k.id}>{k.ad}</option>)}
        </select>
      </div>

      {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}

      {/* Kayıt tablosu. */}
      {veri && (
        <div className="bg-white rounded-xl border border-paper-300 shadow-xs overflow-x-auto">
          {veri.kayitlar.length === 0 ? (
            <p className="p-8 text-center text-xs text-stone-500">Bu süzgeçle kayıt yok.</p>
          ) : (
            <table className="w-full text-left text-xs min-w-[720px]">
              <thead className="bg-paper-100 border-b border-paper-300 text-stone-600 uppercase font-mono text-[11px]">
                <tr>
                  <th className="px-4 py-3 font-semibold">Zaman</th>
                  <th className="px-4 py-3 font-semibold">Kişi</th>
                  <th className="px-4 py-3 font-semibold">İşlem</th>
                  <th className="px-4 py-3 font-semibold">Ayrıntı</th>
                  <th className="px-4 py-3 font-semibold">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-paper-200">
                {veri.kayitlar.map((d) => <Satir key={d.id} d={d} acik={acik === d.id} onTikla={() => setAcik(acik === d.id ? null : d.id)} />)}
              </tbody>
            </table>
          )}
        </div>
      )}
      {/* 100'den fazla kayıt varsa "daha eski" düğmesi. */}
      {devamVar && (
        <button onClick={dahaEski} className="w-full py-2 rounded-lg border border-paper-300 text-xs text-stone-700 hover:bg-paper-200 cursor-pointer">
          Daha eski kayıtlar
        </button>
      )}
    </div>
  );
};

// Tablodaki tek satır, tıklanınca altında ayrıntının tamamı açılır.
const Satir: React.FC<{ d: DenetimKaydi; acik: boolean; onTikla: () => void }> = ({ d, acik, onTikla }) => (
  <>
    <tr onClick={onTikla} className={`align-top cursor-pointer hover:bg-paper-50 ${UYARI.test(d.islem) ? 'bg-rose-50/50' : ''}`}>
      <td className="px-4 py-2.5 whitespace-nowrap text-stone-600 font-mono">{tarihSaat(d.zaman)}</td>
      <td className="px-4 py-2.5 text-stone-800">{d.kullanici ?? '—'}</td>
      <td className={`px-4 py-2.5 font-medium ${UYARI.test(d.islem) ? 'text-rose-800' : 'text-stone-900'}`}>{islemAdi(d.islem)}</td>
      <td className="px-4 py-2.5 text-stone-600 max-w-md truncate">{detayOzeti(d.detay)}</td>
      <td className="px-4 py-2.5 text-stone-500 font-mono">{d.ip ?? '—'}</td>
    </tr>
    {/* Açıksa ayrıntı satırı (JSON olarak). */}
    {acik && (
      <tr className="bg-paper-50">
        <td colSpan={5} className="px-4 py-3">
          <pre className="text-[11px] text-stone-700 whitespace-pre-wrap break-all font-mono">{JSON.stringify(d.detay, null, 2)}</pre>
        </td>
      </tr>
    )}
  </>
);

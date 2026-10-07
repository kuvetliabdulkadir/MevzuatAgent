// "Konular" sayfası, konu kartları, pasif konular ve konu ekleme/düzenleme penceresi ("Etkisini gör" önizlemesiyle).
import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Edit2, Eye, Hash, Layers, Minus, Plus, Power, Shield, X } from 'lucide-react';
import { api } from '../api';
import { KonuIstegi, KonuListesi, KonuOnizleme, KonuTanimi, OnizlemeSatiri } from '../types/api';
import { MesajKutusu } from '../bicim';
import { DegisiklikGecmisi } from '../components/DegisiklikGecmisi';
import { basaKaydir } from '../kaydir';

// Konular silinmez, pasifleştirilir. Değişiklik sonraki taramalardan itibaren geçerlidir, geçmiş kayıtlar ve
// onaylanmış raporlar değişmez. Kaydetmeden önce önizleme zorunlu, son 90 günün başlıklarında ne değişeceği görülür.
export const KeywordsPage: React.FC<{ kurtarmaYapabilir: boolean }> = ({ kurtarmaYapabilir }) => {
  // Liste, mesajlar, düzenlenen konu ve pasifleştirme onayı sorulan konu.
  const [veri, setVeri] = useState<KonuListesi | null>(null);
  const [hata, setHata] = useState('');
  const [basari, setBasari] = useState('');
  const [duzenlenen, setDuzenlenen] = useState<KonuTanimi | 'yeni' | null>(null);
  const [pasifSorulan, setPasifSorulan] = useState<number | null>(null);

  // Konuları sunucudan çeker.
  const yukle = useCallback(() => {
    api.get<KonuListesi>('/konular').then(setVeri).catch((e) => setHata(e.message));
  }, []);
  useEffect(yukle, [yukle]);

  // Konuyu aktif/pasif yapar, mesaj gösterir, sayfayı başa kaydırır, listeyi yeniler.
  const aktiflik = async (k: KonuTanimi, aktif: boolean) => {
    setHata('');
    setBasari('');
    try {
      await api.post<KonuTanimi>(`/konular/${k.id}/${aktif ? 'aktif' : 'pasif'}`, { surum: k.surum });
      setBasari(aktif ? `'${k.ad}' yeniden aktif.` : `'${k.ad}' pasifleştirildi; sonraki taramalarda aranmayacak.`);
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'İşlem yapılamadı.');
    }
    setPasifSorulan(null);
    basaKaydir();
    yukle();
  };

  // Aktif ve pasif konuları ayır.
  const aktifler = veri?.konular.filter((k) => k.aktif) ?? [];
  const pasifler = veri?.konular.filter((k) => !k.aktif) ?? [];

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Başlık ve Yeni Konu düğmesi. */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4 pb-4 border-b border-paper-300">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">Konular ve Anahtar Kelimeler</h2>
          <p className="text-xs text-stone-500 mt-0.5">Taranan başlıklar bu kelimelerle süzülür; eşleşen kayıt onaya gelir.</p>
        </div>
        <button onClick={() => { setBasari(''); setDuzenlenen('yeni'); }}
          className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white text-xs font-semibold hover:bg-petrol-dark shadow-xs transition-all cursor-pointer">
          <Plus className="w-4 h-4" />
          <span>Yeni Konu</span>
        </button>
      </div>

      {/* Eşleşme kurallarının kısa açıklaması. */}
      <div className="p-3.5 sm:p-4 rounded-xl bg-paper-200 border border-paper-300 text-xs text-stone-700 flex items-start gap-3">
        <Shield className="w-5 h-5 text-petrol shrink-0 mt-0.5" />
        <div className="space-y-1 leading-relaxed text-stone-600">
          <p>Kelime, başlıktaki bir kelimenin <strong>başında</strong> geçmelidir ve ekleri kapsar: "altın" → "altının". Büyük/küçük harf ve İ/ı farkı yok sayılır.</p>
          <p><strong>Hariç</strong>: önce metinden silinir (ör. "altında", "Altınordu"). <strong>Dışlanan</strong>: başlıkta geçerse konu hiç eşleşmez.</p>
          <p>Kaydetmeden önce son {veri?.gun ?? 90} günün başlıklarında ne değişeceği gösterilir. Değişiklik sonraki taramalardan itibaren geçerlidir.</p>
        </div>
      </div>

      {/* Mesajlar. */}
      {basari && <MesajKutusu tur="basari">{basari}</MesajKutusu>}
      {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}

      {/* Aktif konuların kartları. */}
      {veri && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-5">
          {aktifler.map((k) => (
            <KonuKarti key={k.id} konu={k} gun={veri.gun}
              onDuzenle={() => { setBasari(''); setDuzenlenen(k); }}
              pasifSoruluyor={pasifSorulan === k.id}
              onPasifSor={() => setPasifSorulan(k.id)}
              onPasifVazgec={() => setPasifSorulan(null)}
              onPasif={() => aktiflik(k, false)} />
          ))}
        </div>
      )}

      {/* Pasif konular listesi ve "Aktif et" düğmesi. */}
      {pasifler.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-stone-700 uppercase tracking-wider font-mono">Pasif konular</p>
          <div className="bg-white rounded-xl border border-paper-300 divide-y divide-paper-200">
            {pasifler.map((k) => (
              <div key={k.id} className="p-3 sm:px-5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                <div className="min-w-0">
                  <span className="font-semibold text-stone-800">{k.ad}</span>
                  <div className="text-[11px] text-stone-500">{k.kelimeler.length} kelime · {k.is_kollari.join(', ')}</div>
                </div>
                <button onClick={() => aktiflik(k, true)}
                  className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer shrink-0">
                  <Power className="w-3.5 h-3.5" />
                  Aktif et
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Düzenlenen konu varsa form penceresi. */}
      {duzenlenen && veri && (
        <KonuFormu
          key={duzenlenen === 'yeni' ? 'yeni' : duzenlenen.id}
          konu={duzenlenen === 'yeni' ? null : duzenlenen}
          isKoluSecenekleri={veri.is_kolu_secenekleri}
          onKapat={() => setDuzenlenen(null)}
          onKaydedildi={(k) => { setDuzenlenen(null); setBasari(`'${k.ad}' kaydedildi; sonraki taramadan itibaren geçerli.`); yukle(); }}
          kurtarmaYapabilir={kurtarmaYapabilir}
        />
      )}
    </div>
  );
};

// Tek bir konunun kartı, ad, iş kolları, son 90 günde kaç kayıt, kelimeler, düzenle/pasifleştir düğmeleri.
const KonuKarti: React.FC<{
  konu: KonuTanimi;
  gun: number;
  onDuzenle: () => void;
  pasifSoruluyor: boolean;
  onPasifSor: () => void;
  onPasifVazgec: () => void;
  onPasif: () => void;
}> = ({ konu: k, gun, onDuzenle, pasifSoruluyor, onPasifSor, onPasifVazgec, onPasif }) => (
  <div className="bg-white rounded-xl border border-paper-300 p-4 sm:p-5 flex flex-col justify-between space-y-3">
    <div className="space-y-3">
      {/* Üst satır, ikon, ad (ve eski adı), düzenle ve pasifleştir düğmeleri. */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-paper-200 flex items-center justify-center shrink-0">
            <Layers className="w-4 h-4 text-petrol" />
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-stone-900 text-sm leading-snug wrap-break-word">{k.ad}</h3>
            {k.eski_adlar.length > 0 && <div className="text-[11px] text-stone-500">Eski adı: {k.eski_adlar.join(', ')}</div>}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={onDuzenle} title="Düzenle" aria-label={`${k.ad} konusunu düzenle`}
            className="p-1.5 rounded-sm text-stone-400 hover:text-petrol hover:bg-paper-200 transition-colors cursor-pointer">
            <Edit2 className="w-3.5 h-3.5" />
          </button>
          <button onClick={onPasifSor} title="Pasifleştir" aria-label={`${k.ad} konusunu pasifleştir`}
            className="p-1.5 rounded-sm text-stone-400 hover:text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer">
            <Power className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* İş kolları ve son 90 gündeki eşleşme sayısı. */}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {k.is_kollari.map((ik) => (
          <span key={ik} className="px-2 py-0.5 rounded-sm font-medium bg-petrol/10 text-petrol border border-petrol/20">{ik}</span>
        ))}
        <span className="text-stone-600 bg-paper-100 border border-paper-200 px-2 py-0.5 rounded-sm">
          Son {gun} gün: <strong>{k.eslesme_90}</strong> kayıt
        </span>
      </div>

      {/* Kelimeler (ilk 18'i, fazlası sayı olarak). */}
      <div className="flex flex-wrap gap-1">
        {k.kelimeler.slice(0, 18).map((w) => (
          <span key={w} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[11px] bg-paper-100 border border-paper-200 text-stone-700">
            <Hash className="w-3 h-3 text-stone-400" />{w}
          </span>
        ))}
        {k.kelimeler.length > 18 && <span className="text-[11px] text-stone-500 px-1">+{k.kelimeler.length - 18} kelime</span>}
      </div>
      {/* Açıklama, bu konu bizi neden ilgilendiriyor. */}
      {k.aciklama ? (
        <p className="text-xs text-stone-700 whitespace-pre-line border-l-2 border-gold-light/60 pl-2">{k.aciklama}</p>
      ) : (
        <p className="text-[11px] text-stone-400 italic">Neden önemli olduğu yazılmamış.</p>
      )}
      {/* Hariç/dışlanan sayıları ve konunun nerede kullanıldığı. */}
      {(k.haric.length > 0 || k.dislanan.length > 0) && (
        <div className="text-[11px] text-stone-500">
          {k.haric.length > 0 && <>Hariç: {k.haric.length}</>}
          {k.haric.length > 0 && k.dislanan.length > 0 && ' · '}
          {k.dislanan.length > 0 && <>Dışlanan: {k.dislanan.length}</>}
        </div>
      )}
      {(k.kullanim.kaynaklar.length > 0 || k.kullanim.izlenen) && (
        <div className="text-[11px] text-stone-500">
          {k.kullanim.kaynaklar.length > 0 && <>Her kaydı ilgili sayan kaynak: {k.kullanim.kaynaklar.join(', ')}. </>}
          {k.kullanim.izlenen && <>Güncel metni takip edilen mevzuatta kullanılıyor.</>}
        </div>
      )}
    </div>

    {/* Pasifleştirme onayı (kartın içinde). */}
    {pasifSoruluyor && (
      <div className="p-2.5 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-900 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <span>Pasifleştirilsin mi? Sonraki taramalarda bu kelimeler aranmaz; geri açılabilir.</span>
        <div className="flex gap-2 shrink-0">
          <button onClick={onPasifVazgec} className="px-3 py-1 rounded-sm text-stone-600 hover:bg-white cursor-pointer">Vazgeç</button>
          <button onClick={onPasif} className="px-3 py-1 rounded-sm bg-rose-600 text-white font-semibold hover:bg-rose-700 cursor-pointer">Pasifleştir</button>
        </div>
      </div>
    )}
  </div>
);

// Yaz + Enter (ya da virgül) ile ekle, × ile çıkar. Yapıştırılan çok satırlı metin satır satır eklenir.
// Etiket kutusu, yazıp Enter'a basınca kelime ekler, × ile çıkarır, yapıştırılan liste satır satır eklenir.
const EtiketKutusu: React.FC<{ etiket: string; aciklama: string; degerler: string[]; onDegis: (d: string[]) => void }> = ({
  etiket, aciklama, degerler, onDegis,
}) => {
  const [girdi, setGirdi] = useState('');
  // Yeni kelimeleri ekle (Türkçe küçük harfe göre tekrarları atarak).
  const ekle = (metin: string) => {
    const yeniler = metin.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);
    const kucuk = (x: string) => x.toLocaleLowerCase('tr');
    const mevcut = new Set(degerler.map(kucuk));
    const eklenecek = yeniler.filter((x) => !mevcut.has(kucuk(x)) && (mevcut.add(kucuk(x)), true));
    if (eklenecek.length) onDegis([...degerler, ...eklenecek]);
    setGirdi('');
  };
  return (
    <div className="space-y-1">
      <span className="block font-semibold text-stone-700">{etiket} <span className="font-normal text-stone-500">— {aciklama}</span></span>
      <div className="p-2 rounded-lg border border-paper-300 bg-white flex flex-wrap gap-1.5 focus-within:ring-2 focus-within:ring-petrol/20">
        {/* Eklenmiş kelimeler (her birinde × düğmesi). */}
        {degerler.map((d) => (
          <span key={d} className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-sm bg-paper-100 border border-paper-200 text-stone-800">
            {d}
            <button type="button" onClick={() => onDegis(degerler.filter((x) => x !== d))} aria-label={`${d} çıkar`}
              className="p-0.5 rounded-sm text-stone-400 hover:text-rose-600 cursor-pointer">
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
        {/* Yazı kutusu, Enter/virgül ekler, boşken Backspace son kelimeyi siler, odaktan çıkınca yazılanı ekler. */}
        <input value={girdi} onChange={(e) => setGirdi(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); ekle(girdi); }
            if (e.key === 'Backspace' && !girdi && degerler.length) onDegis(degerler.slice(0, -1));
          }}
          onBlur={() => girdi.trim() && ekle(girdi)}
          onPaste={(e) => { const m = e.clipboardData.getData('text'); if (/[\n,;]/.test(m)) { e.preventDefault(); ekle(m); } }}
          placeholder={degerler.length ? '' : 'Yazıp Enter’a basın'} maxLength={200}
          className="flex-1 min-w-40 p-1 outline-hidden bg-transparent" />
      </div>
    </div>
  );
};

// Konu ekleme/düzenleme penceresi.
const KonuFormu: React.FC<{
  konu: KonuTanimi | null;
  isKoluSecenekleri: string[];
  onKapat: () => void;
  onKaydedildi: (k: KonuTanimi) => void;
  kurtarmaYapabilir: boolean;
}> = ({ konu, isKoluSecenekleri, onKapat, onKaydedildi, kurtarmaYapabilir }) => {
  // Formun alanları, önizleme sonucu, hata, istek sürüyor mu.
  const [ad, setAd] = useState(konu?.ad ?? '');
  const [isKollari, setIsKollari] = useState<string[]>(konu?.is_kollari ?? []);
  const [yeniIsKolu, setYeniIsKolu] = useState('');
  const [kelimeler, setKelimeler] = useState<string[]>(konu?.kelimeler ?? []);
  const [haric, setHaric] = useState<string[]>(konu?.haric ?? []);
  const [dislanan, setDislanan] = useState<string[]>(konu?.dislanan ?? []);
  const [aciklama, setAciklama] = useState(konu?.aciklama ?? '');
  const [onizleme, setOnizleme] = useState<KonuOnizleme | null>(null);
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);

  useEffect(() => {
    const tus = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onKapat();
    };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat]);

  // Form değişince eski önizleme geçersiz olur (kaydetmeden önce yenisi alınmalı).
  // Form değişince eski önizleme geçersiz, kaydetmeden önce yenisi alınmalı.
  const degis = <T,>(ayarla: (v: T) => void) => (v: T) => { ayarla(v); setOnizleme(null); };
  // İş kolu seçenekleri (seçili olup listede olmayanlar da dahil).
  const secenekler = [...new Set([...isKoluSecenekleri, ...isKollari])];
  const isKolu = (ik: string) => degis(setIsKollari)(isKollari.includes(ik) ? isKollari.filter((x) => x !== ik) : [...isKollari, ik]);

  // Sunucuya gönderilecek gövde (düzenlemede sürüm ve numara da eklenir).
  const govde = (): KonuIstegi => ({
    ad, is_kollari: isKollari, kelimeler, haric, dislanan, aciklama,
    ...(konu ? { surum: konu.surum, id: konu.id } : {}),
  });

  // "Etkisini gör", önizlemeyi sunucudan iste.
  const onizle = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    setBekliyor(true);
    try {
      setOnizleme(await api.post<KonuOnizleme>('/konular/onizleme', govde()));
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Önizleme alınamadı.');
    } finally {
      setBekliyor(false);
    }
  };

  // "Onayla ve kaydet", düzenlemede PUT, yeni konuda POST.
  const kaydet = async () => {
    setHata('');
    setBekliyor(true);
    try {
      onKaydedildi(konu ? await api.put<KonuTanimi>(`/konular/${konu.id}`, govde()) : await api.post<KonuTanimi>('/konular', govde()));
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Kaydedilemedi.');
      setBekliyor(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/60 p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true"
        className="bg-white rounded-xl shadow-xl border border-paper-300 w-full max-w-2xl max-h-[94vh] flex flex-col overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-paper-300 flex items-center justify-between bg-paper-50 shrink-0">
          <h3 className="font-semibold text-stone-900 text-sm truncate pr-2">{konu ? `Konuyu düzenle: ${konu.ad}` : 'Yeni konu'}</h3>
          <button onClick={onKapat} aria-label="Kapat" className="text-stone-400 hover:text-stone-600 text-sm font-bold cursor-pointer p-1 shrink-0">×</button>
        </div>

        {/* Form, ad, iş kolları, kelimeler, hariç, dışlanan. */}
        <form onSubmit={onizle} className="p-4 sm:p-6 space-y-4 text-xs overflow-y-auto">
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Konu adı</span>
            <input type="text" required maxLength={200} value={ad} onChange={(e) => degis(setAd)(e.target.value)}
              placeholder="Örn: Gümrük ve dış ticaret" className="w-full p-2.5 rounded-lg border border-paper-300 bg-white" />
            {/* Ad değiştiyse eski adın saklanacağını söyle. */}
            {konu && ad.trim() !== konu.ad && (
              <span className="block text-stone-500">Eski ad saklanır: takip listesi ve eski kayıtlar bu konuya bağlı kalır.</span>
            )}
          </label>

          <fieldset className="space-y-1.5">
            <legend className="font-semibold text-stone-700">İş kolları <span className="font-normal text-stone-500">— eşleşen kayıt bu iş kollarının alıcı gruplarına gider</span></legend>
            <div className="flex flex-wrap gap-1.5">
              {secenekler.map((ik) => (
                <label key={ik} className="flex items-center gap-2 px-2.5 py-1.5 rounded-sm bg-paper-100 hover:bg-paper-200 cursor-pointer">
                  <input type="checkbox" checked={isKollari.includes(ik)} onChange={() => isKolu(ik)} className="accent-petrol" />
                  <span>{ik}</span>
                </label>
              ))}
            </div>
            {/* Yeni iş kolu ekleme kutusu. */}
            <div className="flex gap-2">
              <input value={yeniIsKolu} onChange={(e) => setYeniIsKolu(e.target.value)} maxLength={100} placeholder="Yeni iş kolu"
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); if (yeniIsKolu.trim()) { isKolu(yeniIsKolu.trim()); setYeniIsKolu(''); } } }}
                className="flex-1 p-2 rounded-lg border border-paper-300 bg-white" />
              <button type="button" onClick={() => { if (yeniIsKolu.trim()) { isKolu(yeniIsKolu.trim()); setYeniIsKolu(''); } }}
                className="px-3 py-2 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer">Ekle</button>
            </div>
          </fieldset>

          <EtiketKutusu etiket="Anahtar kelimeler" aciklama="kelime başından aranır, ekleri kapsar" degerler={kelimeler} onDegis={degis(setKelimeler)} />
          <EtiketKutusu etiket="Hariç" aciklama="önce metinden silinir (alakasız ama aynı kökle başlayan kelimeler)" degerler={haric} onDegis={degis(setHaric)} />
          <EtiketKutusu etiket="Dışlanan" aciklama="başlıkta geçerse bu konu hiç eşleşmez" degerler={dislanan} onDegis={degis(setDislanan)} />

          {/* Bu konu bizi neden ilgilendiriyor, eşleşmeyi etkilemez, raporda ve mailde görünür. */}
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Neden önemli <span className="font-normal text-stone-500">— raporda ve mailde bu konuya takılan her belgenin altında görünür</span></span>
            <textarea rows={3} maxLength={1000} value={aciklama} onChange={(e) => degis(setAciklama)(e.target.value)}
              placeholder="Örn: Kuyumcular 5549 sayılı Kanunda yükümlüdür, kimlik tespiti ve şüpheli işlem bildirimi kuralları doğrudan uygulanır."
              className="w-full p-2.5 rounded-lg border border-paper-300 bg-white resize-y" />
            <span className="block text-right text-stone-400">{aciklama.length}/1000</span>
          </label>

          {/* Hata, önizleme sonucu ve (düzenlemede) değişiklik geçmişi. */}
          {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
          {onizleme && <OnizlemeSonucu sonuc={onizleme} />}
          {konu && (
            <DegisiklikGecmisi yol={`/konular/${konu.id}`} surum={konu.surum} kurtarmaYapabilir={kurtarmaYapabilir}
              onGeriAlindi={(k) => onKaydedildi(k as KonuTanimi)} />
          )}

          {/* Düğmeler, önizleme alınmadıysa "Etkisini gör", alındıysa "Onayla ve kaydet". */}
          <div className="pt-3 border-t border-paper-200 flex flex-col-reverse sm:flex-row justify-end gap-2">
            <button type="button" onClick={onKapat} className="w-full sm:w-auto px-4 py-2 text-stone-600 hover:bg-paper-200 rounded-lg cursor-pointer">Vazgeç</button>
            {onizleme ? (
              <button type="button" onClick={kaydet} disabled={bekliyor}
                className="w-full sm:w-auto px-4 py-2 bg-petrol text-white rounded-lg font-semibold hover:bg-petrol-dark disabled:opacity-60 cursor-pointer">
                {bekliyor ? 'Kaydediliyor…' : 'Onayla ve kaydet'}
              </button>
            ) : (
              <button type="submit" disabled={bekliyor}
                className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 bg-petrol text-white rounded-lg font-semibold hover:bg-petrol-dark disabled:opacity-60 cursor-pointer">
                <Eye className="w-3.5 h-3.5" />
                {bekliyor ? 'Hesaplanıyor…' : 'Etkisini gör'}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
};

// Önizleme sonucu, kaç yeni eşleşme, kaç düşen, uyarılar ve başlık listeleri.
const OnizlemeSonucu: React.FC<{ sonuc: KonuOnizleme }> = ({ sonuc }) => (
  <div className="space-y-2 p-3 rounded-lg bg-paper-50 border border-paper-300">
    <p className="text-stone-700">
      Son {sonuc.gun} günün {sonuc.taranan} başlığına göre: <strong className="text-emerald-700">+{sonuc.eslesecek_sayisi}</strong> yeni
      eşleşme, <strong className="text-rose-700">−{sonuc.dusecek_sayisi}</strong> düşen, {sonuc.ayni_kalan} aynı kalan.
      <span className="block text-stone-500">Sadece başlıklara bakılır; içerikten gelen eşleşmeler önizlemede yok. Geçmiş kayıtlar değişmez.</span>
    </p>
    {sonuc.uyarilar.map((u) => (
      <div key={u} className="flex items-start gap-2 p-2 rounded-sm bg-amber-50 border border-amber-300 text-amber-900">
        <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" /><span>{u}</span>
      </div>
    ))}
    <BaslikListesi baslik="Yeni eşleşecek" ikon={<Plus className="w-3.5 h-3.5 text-emerald-600" />} satirlar={sonuc.eslesecek} toplam={sonuc.eslesecek_sayisi} />
    <BaslikListesi baslik="Artık eşleşmeyecek" ikon={<Minus className="w-3.5 h-3.5 text-rose-600" />} satirlar={sonuc.dusecek} toplam={sonuc.dusecek_sayisi} />
    {sonuc.eslesecek_sayisi === 0 && sonuc.dusecek_sayisi === 0 && (
      <p className="text-stone-500">Son {sonuc.gun} günün başlıklarında fark yok.</p>
    )}
  </div>
);

// Önizlemedeki başlık listesi (boşsa hiçbir şey çizmez).
const BaslikListesi: React.FC<{ baslik: string; ikon: React.ReactNode; satirlar: OnizlemeSatiri[]; toplam: number }> = ({
  baslik, ikon, satirlar, toplam,
}) => satirlar.length === 0 ? null : (
  <div className="space-y-1">
    <p className="font-semibold text-stone-700">{baslik} ({toplam}{toplam > satirlar.length ? `, ilk ${satirlar.length}` : ''})</p>
    <div className="max-h-48 overflow-y-auto border border-paper-300 rounded-lg divide-y divide-paper-200 bg-white">
      {satirlar.map((s, i) => (
        <div key={i} className="px-3 py-2 flex items-start gap-2">
          <span className="mt-0.5 shrink-0">{ikon}</span>
          <div className="min-w-0">
            <div className="text-stone-900 wrap-break-word">{s.baslik}</div>
            <div className="text-[11px] text-stone-500">
              {s.tarih} · {s.kaynak} · {s.kelimeler.join(', ')}
              {s.baska_konular && (s.baska_konular.length > 0
                ? ` · başka konudan yine ilgili: ${s.baska_konular.join(', ')}`
                : ' · başka hiçbir konuya uymuyor')}
            </div>
          </div>
        </div>
      ))}
    </div>
  </div>
);

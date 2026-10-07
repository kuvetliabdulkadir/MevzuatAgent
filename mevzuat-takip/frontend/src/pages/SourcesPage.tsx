// "Kaynaklar" sayfası, kaynak kartları, kaldırılanlar ve kaynak ekleme/düzenleme penceresi (adresten otomatik bulma ve "Dene" ile).
import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, CheckCircle2, Edit2, Globe, Play, Plus, RotateCcw, Search, Shield, Star, Trash2 } from 'lucide-react';
import { api } from '../api';
import { BulmaSonucu, DeneSonucu, Kaynak, KaynakAlani, KaynakIstegi, KaynakTipi, KaynakTipleri } from '../types/api';
import { MesajKutusu } from '../bicim';
import { DegisiklikGecmisi } from '../components/DegisiklikGecmisi';
import { basaKaydir } from '../kaydir';
import { tarihSaat } from '../tarih';

// Kaynaklar silinmez, kaldırılır, kayıtları ve kaldığı yer (checkpoint) durur, geri getirilince kaldığı yerden devam eder.
// Değişiklikler bir sonraki taramadan itibaren geçerlidir.
export const SourcesPage: React.FC<{ kurtarmaYapabilir: boolean }> = ({ kurtarmaYapabilir }) => {
  // Kaynak listesi, tip formları, mesajlar, düzenlenen kaynak, kaldırma onayı sorulan kaynak.
  const [kaynaklar, setKaynaklar] = useState<Kaynak[] | null>(null);
  const [tipler, setTipler] = useState<KaynakTipleri | null>(null);
  const [hata, setHata] = useState('');
  const [basari, setBasari] = useState('');
  const [duzenlenen, setDuzenlenen] = useState<Kaynak | 'yeni' | null>(null);
  const [kaldirilacak, setKaldirilacak] = useState<string | null>(null);

  // Kaynakları sunucudan çeker.
  const yukle = useCallback(() => {
    api.get<{ kaynaklar: Kaynak[] }>('/kaynaklar').then((v) => setKaynaklar(v.kaynaklar)).catch((e) => setHata(e.message));
  }, []);
  // Sayfa açılınca, kaynakları ve tiplerin form tanımlarını yükle.
  useEffect(() => {
    yukle();
    api.get<KaynakTipleri>('/kaynak-tipleri').then(setTipler).catch((e) => setHata(e.message));
  }, [yukle]);

  // Ortak işlem yardımcısı, isteği yap, mesajı göster ({ad} yerine kaynağın adını koy), sayfayı başa kaydır, listeyi yenile.
  const islem = async (mesaj: string, istek: () => Promise<Kaynak>) => {
    setHata('');
    setBasari('');
    try {
      const k = await istek();
      setBasari(mesaj.replace('{ad}', k.etiket));
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'İşlem yapılamadı.');
    }
    setKaldirilacak(null);
    basaKaydir();
    yukle();
  };

  // Aç/kapat anahtarı, kaynağı aynı ayarlarla ama aktifliği ters çevrilmiş olarak kaydet.
  const acKapat = (k: Kaynak) =>
    islem(k.aktif ? "'{ad}' kapatıldı; sonraki taramalarda taranmayacak." : "'{ad}' açıldı.", () =>
      api.put<Kaynak>(`/kaynaklar/${k.ad}`, { ...istekten(k), aktif: !k.aktif }));

  // Listede olanlar, kaldırılanlar ve panelden eklenebilen bir tip var mı.
  const listede = kaynaklar?.filter((k) => !k.kaldirildi) ?? [];
  const kaldirilan = kaynaklar?.filter((k) => k.kaldirildi) ?? [];
  const eklenebilir = tipler?.tipler.some((t) => t.eklenebilir) ?? false;

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Başlık ve (eklenebilir tip varsa) Yeni Kaynak düğmesi. */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4 pb-4 border-b border-paper-300">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">Kaynaklar</h2>
          <p className="text-xs text-stone-500 mt-0.5">Taranan siteler. Değişiklik bir sonraki taramadan itibaren geçerli olur.</p>
        </div>
        {eklenebilir && (
          <button onClick={() => { setBasari(''); setDuzenlenen('yeni'); }}
            className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white text-xs font-semibold hover:bg-petrol-dark shadow-xs transition-all cursor-pointer">
            <Plus className="w-4 h-4" />
            <span>Yeni Kaynak</span>
          </button>
        )}
      </div>

      {/* Kısa açıklama kutusu. */}
      <div className="p-3.5 sm:p-4 rounded-xl bg-paper-200 border border-paper-300 text-xs text-stone-700 flex items-start gap-3">
        <Shield className="w-5 h-5 text-petrol shrink-0 mt-0.5" />
        <div className="space-y-1 leading-relaxed text-stone-600">
          <p>Yeni kaynak için sitenin <strong>adresini girmeniz yeterli</strong>: sistem nasıl okunacağını (veri servisi, RSS, sayfa listesi) kendisi bulur ve örnek duyuruları gösterir.</p>
          <p>Kaydetmeden önce <strong>Dene</strong> ile son 7 günde ne bulduğuna bakın; deneme hiçbir şey kaydetmez.</p>
          <p>Adresler sadece <strong>https://</strong> olabilir; şirket içi ağ adreslerine istek atılmaz.</p>
          <p>Resmî Gazete ve GİB tek kaynaktır: düzenlenebilir, yenisi eklenmez.</p>
        </div>
      </div>

      {/* Mesajlar. */}
      {basari && <MesajKutusu tur="basari">{basari}</MesajKutusu>}
      {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}

      {/* Kaynak kartları. */}
      {kaynaklar && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {listede.map((k) => (
            <KaynakKarti key={k.ad} kaynak={k}
              onDuzenle={() => { setBasari(''); setDuzenlenen(k); }}
              onAcKapat={() => acKapat(k)}
              kaldirSoruluyor={kaldirilacak === k.ad}
              onKaldirSor={() => setKaldirilacak(k.ad)}
              onKaldirVazgec={() => setKaldirilacak(null)}
              onKaldir={() => islem("'{ad}' kaldırıldı. Aşağıdan geri getirebilirsiniz.",
                () => api.post<Kaynak>(`/kaynaklar/${k.ad}/kaldir`, { surum: k.surum }))} />
          ))}
          {listede.length === 0 && (
            <p className="p-8 text-center text-xs text-stone-500 bg-white rounded-xl border border-paper-300">Taranan kaynak yok.</p>
          )}
        </div>
      )}

      {/* Kaldırılan kaynaklar ve "Geri getir" düğmesi. */}
      {kaldirilan.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-stone-700 uppercase tracking-wider font-mono">Kaldırılanlar</p>
          <div className="bg-white rounded-xl border border-paper-300 divide-y divide-paper-200">
            {kaldirilan.map((k) => (
              <div key={k.ad} className="p-3 sm:px-5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                <div className="min-w-0">
                  <span className="font-semibold text-stone-800">{k.etiket}</span>
                  <span className="text-stone-500 font-mono ml-2">{k.ad}</span>
                  <div className="text-[11px] text-stone-500">{k.toplam_kayit} kayıt duruyor · kaldırıldı: {tarihSaat(k.guncelleme)}</div>
                </div>
                <button onClick={() => islem("'{ad}' geri getirildi; kaldığı yerden taranacak.",
                  () => api.post<Kaynak>(`/kaynaklar/${k.ad}/geri-getir`, { surum: k.surum }))}
                  className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer shrink-0">
                  <RotateCcw className="w-3.5 h-3.5" />
                  Geri getir
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Düzenlenen kaynak varsa form penceresi. */}
      {duzenlenen && tipler && (
        <KaynakFormu
          key={duzenlenen === 'yeni' ? 'yeni' : duzenlenen.ad}
          kaynak={duzenlenen === 'yeni' ? null : duzenlenen}
          tipler={tipler}
          onKapat={() => setDuzenlenen(null)}
          onKaydedildi={(k) => { setDuzenlenen(null); setBasari(`'${k.etiket}' kaydedildi.`); yukle(); }}
          kurtarmaYapabilir={kurtarmaYapabilir}
        />
      )}
    </div>
  );
};

// Kaynağın mevcut halinden sunucuya gidecek istek gövdesini üretir.
function istekten(k: Kaynak): KaynakIstegi {
  return { tip: k.tip, etiket: k.etiket, ayarlar: k.ayarlar, varsayilan_konular: k.varsayilan_konular, aktif: k.aktif, surum: k.surum };
}

// Tek bir kaynağın kartı.
const KaynakKarti: React.FC<{
  kaynak: Kaynak;
  onDuzenle: () => void;
  onAcKapat: () => void;
  kaldirSoruluyor: boolean;
  onKaldirSor: () => void;
  onKaldirVazgec: () => void;
  onKaldir: () => void;
}> = ({ kaynak: k, onDuzenle, onAcKapat, kaldirSoruluyor, onKaldirSor, onKaldirVazgec, onKaldir }) => {
  // Kartta gösterilecek adres (tipine göre api_url, akis_url ya da liste_url).
  const adres = [k.ayarlar.api_url, k.ayarlar.akis_url, k.ayarlar.liste_url].find((a): a is string => typeof a === 'string') ?? null;
  return (
    <div className={`p-4 sm:p-5 rounded-xl border bg-white flex flex-col justify-between space-y-3 ${
      k.aktif ? 'border-paper-300' : 'border-dashed border-stone-300 opacity-75'}`}>
      <div className="space-y-3">
        {/* Üst satır, ikon, ad, adres, düzenle, kaldır ve aç/kapat düğmeleri. */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0 flex-1">
            <div className="w-8 h-8 rounded-lg bg-paper-200 flex items-center justify-center shrink-0">
              <Globe className="w-4 h-4 text-petrol" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="font-semibold text-stone-900 text-sm leading-snug wrap-break-word">{k.etiket}</h3>
              <div className="text-[11px] text-stone-500 font-mono truncate">{k.ad}{adres && ` · ${adres}`}</div>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button onClick={onDuzenle} title="Düzenle" aria-label={`${k.etiket} kaynağını düzenle`}
              className="p-1.5 rounded-sm text-stone-400 hover:text-petrol hover:bg-paper-200 transition-colors cursor-pointer">
              <Edit2 className="w-3.5 h-3.5" />
            </button>
            <button onClick={onKaldirSor} title="Kaldır" aria-label={`${k.etiket} kaynağını kaldır`}
              className="p-1.5 rounded-sm text-stone-400 hover:text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
            <button type="button" onClick={onAcKapat} role="switch" aria-checked={k.aktif}
              title={k.aktif ? 'Taramayı kapat' : 'Taramayı aç'} aria-label={k.aktif ? 'Taramayı kapat' : 'Taramayı aç'}
              className={`ml-1 relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                k.aktif ? 'bg-petrol' : 'bg-stone-300'}`}>
              <span className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-xs transition ${
                k.aktif ? 'translate-x-4' : 'translate-x-0'}`} />
            </button>
          </div>
        </div>

        {/* Tip, kapalı etiketi, son tarama zamanı, kayıt sayıları. */}
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="px-2.5 py-0.5 rounded-full font-medium bg-petrol/10 text-petrol border border-petrol/20">{k.tip_etiketi}</span>
          {!k.aktif && <span className="px-2 py-0.5 rounded-sm bg-stone-100 text-stone-600 border border-stone-300 font-semibold">Kapalı</span>}
          <span className="text-stone-600 bg-paper-100 border border-paper-200 px-2 py-0.5 rounded-sm">
            Son tarama: {k.son_basarili ? tarihSaat(k.son_basarili) : 'henüz yok'}
          </span>
          <span className="text-stone-600 bg-paper-100 border border-paper-200 px-2 py-0.5 rounded-sm">
            {k.toplam_kayit} kayıt · {k.ilgili_kayit} ilgili
          </span>
        </div>

        {/* "Her kaydı ilgili say" konuları. */}
        {k.varsayilan_konular.length > 0 && (
          <div className="text-[11px] text-stone-600">
            <strong>Her kaydı ilgili:</strong> {k.varsayilan_konular.join(', ')}
          </div>
        )}

        {/* Son taramada bu kaynakta hata olduysa kırmızı kutu. */}
        {k.son_hata && (
          <div className="flex items-start gap-2 p-2.5 rounded-lg bg-rose-50 border border-rose-300 text-[11px] text-rose-900">
            <AlertTriangle className="w-3.5 h-3.5 text-rose-600 shrink-0 mt-0.5" />
            <span className="wrap-break-word min-w-0">
              <strong>Son taramada hata</strong>{k.son_calisma && ` (${tarihSaat(k.son_calisma)})`}: {k.son_hata}
            </span>
          </div>
        )}
      </div>

      {/* Kaldırma onayı (kartın içinde). */}
      {kaldirSoruluyor && (
        <div className="p-2.5 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-900 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <span>Kaldırılsın mı? Taranmaz; kayıtları durur, geri getirilebilir.</span>
          <div className="flex gap-2 shrink-0">
            <button onClick={onKaldirVazgec} className="px-3 py-1 rounded-sm text-stone-600 hover:bg-white cursor-pointer">Vazgeç</button>
            <button onClick={onKaldir} className="px-3 py-1 rounded-sm bg-rose-600 text-white font-semibold hover:bg-rose-700 cursor-pointer">Kaldır</button>
          </div>
        </div>
      )}
    </div>
  );
};

// Formdaki ayar değeri, seçim listesinde seçili anahtarlar, diğerlerinde metin.
// Formdaki bir alanın başlangıç değeri (seçim listesiyse liste, değilse yazı).
function baslangicDegeri(alan: KaynakAlani, kaynak: Kaynak | null): string | string[] {
  const deger = kaynak?.ayarlar[alan.ad] ?? alan.varsayilan;
  if (alan.tur === 'secim_listesi') return Array.isArray(deger) ? deger.map(String) : [];
  return deger === null || deger === undefined ? '' : String(deger);
}

// Kaynak ekleme/düzenleme penceresi.
const KaynakFormu: React.FC<{
  kaynak: Kaynak | null;
  tipler: KaynakTipleri;
  onKapat: () => void;
  onKaydedildi: (k: Kaynak) => void;
  kurtarmaYapabilir: boolean;
}> = ({ kaynak, tipler, onKapat, onKaydedildi, kurtarmaYapabilir }) => {
  // Panelden eklenebilen tipler, seçili tip ve onun form tanımı.
  const eklenebilir = tipler.tipler.filter((t) => t.eklenebilir);
  const [tip, setTip] = useState(kaynak?.tip ?? eklenebilir[0]?.tip ?? '');
  const tipBilgisi: KaynakTipi | undefined = tipler.tipler.find((t) => t.tip === tip);
  const [etiket, setEtiket] = useState(kaynak?.etiket ?? '');
  // Tipe özel alanların değerleri.
  const [degerler, setDegerler] = useState<Record<string, string | string[]>>(() =>
    Object.fromEntries((tipBilgisi?.alanlar ?? []).map((a) => [a.ad, baslangicDegeri(a, kaynak)])));
  // "Her kaydı ilgili say" konuları, hata, kaydediliyor mu, deneme sonucu, deneniyor mu.
  const [konular, setKonular] = useState<string[]>(kaynak?.varsayilan_konular ?? []);
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);
  const [deneme, setDeneme] = useState<DeneSonucu | null>(null);
  const [deniyor, setDeniyor] = useState(false);
  // Yeni kaynak, önce adres girilir, tip ve ayarlar kendiliğinden bulunur, ayar alanları "gelişmiş"te.
  const [asama, setAsama] = useState<'adres' | 'form'>(kaynak ? 'form' : 'adres');
  const [adres, setAdres] = useState('');
  const [bulma, setBulma] = useState<BulmaSonucu | null>(null);
  const [ariyor, setAriyor] = useState(false);
  const [gelismis, setGelismis] = useState(kaynak !== null);

  useEffect(() => {
    const tus = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onKapat();
    };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat]);

  // Tip değişince alanları o tipin varsayılanlarıyla sıfırla.
  const tipSec = (yeni: string) => {
    setTip(yeni);
    const bilgi = tipler.tipler.find((t) => t.tip === yeni);
    setDegerler(Object.fromEntries((bilgi?.alanlar ?? []).map((a) => [a.ad, baslangicDegeri(a, null)])));
    setDeneme(null);
  };

  // "Bul", adresi sunucuya gönder, tip bulunduysa formu bulunan ayarlarla doldurup forma geç.
  const bul = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    setBulma(null);
    setAriyor(true);
    try {
      const s = await api.post<BulmaSonucu>('/kaynaklar/bul', { adres });
      setBulma(s);
      if (!etiket && s.onerilen_ad) setEtiket(s.onerilen_ad);
      if (s.bulundu && s.tip) {
        const bilgi = tipler.tipler.find((t) => t.tip === s.tip);
        setTip(s.tip);
        setDegerler(Object.fromEntries((bilgi?.alanlar ?? []).map((a) => {
          const bulunan = s.ayarlar?.[a.ad];
          return [a.ad, bulunan === undefined || bulunan === null ? baslangicDegeri(a, null) : String(bulunan)];
        })));
        setDeneme(null);
        setGelismis(false);
        setAsama('form');
      }
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Adres denenemedi.');
    } finally {
      setAriyor(false);
    }
  };

  // "Ayarları elle gir", gelişmiş ayarları açıp forma geç.
  const elleAyarla = () => {
    setHata('');
    setGelismis(true);
    setAsama('form');
  };

  // Sunucuya gidecek gövde (boş alanlar gönderilmez).
  const govde = (): KaynakIstegi => ({
    tip,
    etiket,
    ayarlar: Object.fromEntries(Object.entries(degerler).filter(([, v]) => v !== '')),
    varsayilan_konular: konular,
    aktif: kaynak?.aktif ?? true,
    ...(kaynak ? { surum: kaynak.surum } : {}),
  });

  // "Dene", kaydetmeden son 7 günü tarat.
  const dene = async () => {
    setHata('');
    setDeneme(null);
    setDeniyor(true);
    try {
      setDeneme(await api.post<DeneSonucu>('/kaynaklar/dene', govde()));
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'Denenemedi.');
    } finally {
      setDeniyor(false);
    }
  };

  // "Kaydet", düzenlemede PUT, yeni kaynakta POST.
  const kaydet = async (e: React.FormEvent) => {
    e.preventDefault();
    setHata('');
    setBekliyor(true);
    try {
      onKaydedildi(kaynak ? await api.put<Kaynak>(`/kaynaklar/${kaynak.ad}`, govde()) : await api.post<Kaynak>('/kaynaklar', govde()));
    } catch (err) {
      setHata(err instanceof Error ? err.message : 'Kaydedilemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  // Bir alanın değerini değiştir (eski deneme sonucu geçersiz olur).
  const ayarla = (ad: string, deger: string | string[]) => {
    setDegerler({ ...degerler, [ad]: deger });
    setDeneme(null);
  };
  // Listede varsa çıkar, yoksa ekle.
  const tersle = <T,>(liste: T[], x: T) => (liste.includes(x) ? liste.filter((y) => y !== x) : [...liste, x]);
  // Kaynakta kayıtlı ama artık pasif/tanımsız konu, listede görünsün ki kaldırılabilsin.
  const konuSecenekleri = [...tipler.konular, ...konular.filter((k) => !tipler.konular.includes(k))];

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/60 p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true"
        className="bg-white rounded-xl shadow-xl border border-paper-300 w-full max-w-2xl max-h-[94vh] flex flex-col overflow-hidden">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-paper-300 flex items-center justify-between bg-paper-50 shrink-0">
          <h3 className="font-semibold text-stone-900 text-sm truncate pr-2">{kaynak ? `Kaynağı düzenle: ${kaynak.etiket}` : 'Yeni kaynak'}</h3>
          <button onClick={onKapat} aria-label="Kapat" className="text-stone-400 hover:text-stone-600 text-sm font-bold cursor-pointer p-1 shrink-0">×</button>
        </div>

        {/* 1. aşama, adres girme ve "Bul". 2. aşama, form. */}
        {asama === 'adres' ? (
          <form onSubmit={bul} className="p-4 sm:p-6 space-y-4 text-xs overflow-y-auto">
            <label className="block space-y-1">
              <span className="block font-semibold text-stone-700">Sitenin adresi</span>
              <span className="block text-stone-500">Duyuruların listelendiği sayfa (ör. https://www.kurum.gov.tr/duyurular). Biliyorsanız RSS ya da API adresi de olur.</span>
              <input type="url" required autoFocus value={adres} onChange={(e) => setAdres(e.target.value)} placeholder="https://"
                className="w-full p-2.5 rounded-lg border border-paper-300 bg-white font-mono" />
            </label>
            <p className="text-stone-500">Sırayla denenir: <strong>WordPress veri servisi</strong> → <strong>RSS/Atom akışı</strong> → <strong>sayfadaki duyuru listesi</strong>. İlk uyan seçilir; hiçbir şey kaydedilmez.</p>
            {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
            {bulma && !bulma.bulundu && <BulunamadiKutusu sonuc={bulma} />}
            <div className="pt-3 border-t border-paper-200 flex flex-col-reverse sm:flex-row justify-between gap-2">
              <button type="button" onClick={elleAyarla} className="w-full sm:w-auto px-4 py-2 text-stone-600 hover:bg-paper-200 rounded-lg cursor-pointer">
                Ayarları elle gir (gelişmiş)
              </button>
              <button type="submit" disabled={ariyor}
                className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 bg-petrol text-white rounded-lg font-semibold hover:bg-petrol-dark disabled:opacity-60 cursor-pointer">
                <Search className="w-3.5 h-3.5" />
                {ariyor ? 'Aranıyor… (birkaç saniye)' : 'Bul'}
              </button>
            </div>
          </form>
        ) : (
        <form onSubmit={kaydet} className="p-4 sm:p-6 space-y-4 text-xs overflow-y-auto">
          {/* Düzenlemede tip ve kod adı, yeni kaynakta bulma sonucu ve "Başka adres dene / Ayarları göster" düğmeleri. */}
          {kaynak ? (
            <div className="text-stone-600">
              Tip: <strong>{tipBilgisi?.etiket}</strong> · Kod adı: <span className="font-mono">{kaynak.ad}</span>
              <span className="text-stone-500"> (değişmez; kayıtlar buna bağlı)</span>
            </div>
          ) : (
            <>
              {bulma?.bulundu && <BulunduKutusu sonuc={bulma} />}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <button type="button" onClick={() => { setAsama('adres'); setDeneme(null); }}
                  className="text-petrol hover:underline cursor-pointer">← Başka adres dene</button>
                <button type="button" onClick={() => setGelismis(!gelismis)} className="text-stone-600 hover:underline cursor-pointer">
                  {gelismis ? 'Ayarları gizle' : `Ayarları göster / düzenle (gelişmiş)${tipBilgisi ? ` — ${tipBilgisi.etiket}` : ''}`}
                </button>
              </div>
            </>
          )}
          {/* Gelişmişte tip seçimi. */}
          {!kaynak && gelismis && (
            <label className="block space-y-1">
              <span className="block font-semibold text-stone-700">Kaynak tipi</span>
              <select value={tip} onChange={(e) => tipSec(e.target.value)} className="w-full p-2.5 rounded-lg border border-paper-300 bg-white">
                {eklenebilir.map((t) => <option key={t.tip} value={t.tip}>{t.etiket}</option>)}
              </select>
              {tipBilgisi && <span className="block text-stone-500">{tipBilgisi.aciklama}</span>}
            </label>
          )}

          {/* Görünen ad. */}
          <label className="block space-y-1">
            <span className="block font-semibold text-stone-700">Görünen ad <span className="font-normal text-stone-500">— rapordaki kaynakçada yazar</span></span>
            <input type="text" required maxLength={200} value={etiket} onChange={(e) => setEtiket(e.target.value)}
              placeholder="Örn: BDDK Duyurusu" className="w-full p-2.5 rounded-lg border border-paper-300 bg-white" />
          </label>

          {/* Gelişmişte tipe özel alanlar. */}
          {gelismis && tipBilgisi?.alanlar.map((alan) => (
            <AlanGirdisi key={alan.ad} alan={alan} deger={degerler[alan.ad] ?? ''} onDegis={(v) => ayarla(alan.ad, v)} />
          ))}

          {/* "Her kaydı ilgili say" konu kutuları. */}
          <fieldset className="space-y-1.5">
            <legend className="font-semibold text-stone-700">
              Her kaydı ilgili say <span className="font-normal text-stone-500">— başlığında kelime geçmese de bu konulara girer (ör. MASAK)</span>
            </legend>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
              {konuSecenekleri.map((konu) => (
                <label key={konu} className="flex items-center gap-2 p-2 rounded-sm bg-paper-100 hover:bg-paper-200 cursor-pointer">
                  <input type="checkbox" checked={konular.includes(konu)} onChange={() => { setKonular(tersle(konular, konu)); setDeneme(null); }}
                    className="accent-petrol" />
                  <span className="text-stone-800">{konu}</span>
                  {!tipler.konular.includes(konu) && <span className="text-rose-700">(pasif)</span>}
                </label>
              ))}
            </div>
          </fieldset>

          {/* Hata, deneme sonucu ve (düzenlemede) değişiklik geçmişi. */}
          {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
          {deneme && <DenemeSonucu sonuc={deneme} />}
          {kaynak && (
            <DegisiklikGecmisi yol={`/kaynaklar/${kaynak.ad}`} surum={kaynak.surum} kurtarmaYapabilir={kurtarmaYapabilir}
              onGeriAlindi={(k) => onKaydedildi(k as Kaynak)} />
          )}

          {/* Düğmeler, Dene, Vazgeç, Kaydet. */}
          <div className="pt-3 border-t border-paper-200 flex flex-col-reverse sm:flex-row justify-between gap-2">
            <button type="button" onClick={dene} disabled={deniyor || !tip}
              className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg border border-petrol text-petrol font-semibold hover:bg-petrol/5 disabled:opacity-60 cursor-pointer">
              <Play className="w-3.5 h-3.5" />
              {deniyor ? 'Deneniyor…' : 'Dene (son 7 gün, kaydetmez)'}
            </button>
            <div className="flex flex-col-reverse sm:flex-row gap-2">
              <button type="button" onClick={onKapat} className="w-full sm:w-auto px-4 py-2 text-stone-600 hover:bg-paper-200 rounded-lg cursor-pointer">Vazgeç</button>
              <button type="submit" disabled={bekliyor}
                className="w-full sm:w-auto px-4 py-2 bg-petrol text-white rounded-lg font-semibold hover:bg-petrol-dark disabled:opacity-60 cursor-pointer">
                {bekliyor ? 'Kaydediliyor…' : 'Kaydet'}
              </button>
            </div>
          </div>
        </form>
        )}
      </div>
    </div>,
    document.body,
  );
};

// "Bulundu" kutusu, hangi tip bulundu, okunan son duyurular ve ilk duyurunun metninden bir parça.
const BulunduKutusu: React.FC<{ sonuc: BulmaSonucu }> = ({ sonuc }) => (
  <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-300 text-emerald-950 space-y-2">
    <div className="flex items-start gap-2">
      <CheckCircle2 className="w-4 h-4 text-emerald-700 shrink-0 mt-0.5" />
      <div><strong>Bulundu: {sonuc.tip_etiketi}.</strong> {sonuc.aciklama}</div>
    </div>
    {sonuc.ornekler && sonuc.ornekler.length > 0 && (
      <div>
        <div className="font-semibold text-emerald-900 mb-1">Okunan son duyurular:</div>
        <ul className="max-h-40 overflow-y-auto bg-white/70 rounded-sm border border-emerald-200 divide-y divide-emerald-100">
          {sonuc.ornekler.map((o) => (
            <li key={o.url} className="px-2 py-1.5 flex gap-2">
              <span className="text-stone-500 font-mono shrink-0">{o.tarih.split('-').reverse().join('.')}</span>
              <span className="text-stone-900 wrap-break-word min-w-0">{o.baslik}</span>
            </li>
          ))}
        </ul>
      </div>
    )}
    {sonuc.icerik_ornegi && (
      <div>
        <div className="font-semibold text-emerald-900 mb-1">İlk duyurunun sayfasından okunan metin (doğru alan mı?):</div>
        <p className="bg-white/70 rounded-sm border border-emerald-200 px-2 py-1.5 text-stone-700 whitespace-pre-line max-h-28 overflow-y-auto">
          {sonuc.icerik_ornegi}
        </p>
      </div>
    )}
  </div>
);

// "Bulunamadı" kutusu, her yolun neden olmadığı.
const BulunamadiKutusu: React.FC<{ sonuc: BulmaSonucu }> = ({ sonuc }) => (
  <div className="p-3 rounded-lg bg-amber-50 border border-amber-300 text-amber-950 space-y-2">
    <div className="flex items-start gap-2">
      <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
      <span>{sonuc.aciklama}</span>
    </div>
    <ul className="space-y-0.5 pl-6 list-disc text-amber-900">
      {sonuc.adimlar.map((a) => <li key={a.yol}><strong>{a.yol}:</strong> {a.not}</li>)}
    </ul>
  </div>
);

// Tipe özel tek bir form alanı, seçim listesi ya da yazı/sayı/adres kutusu.
const AlanGirdisi: React.FC<{ alan: KaynakAlani; deger: string | string[]; onDegis: (v: string | string[]) => void }> = ({
  alan, deger, onDegis,
}) => {
  const baslik = (
    <span className="block font-semibold text-stone-700">
      {alan.etiket}{alan.zorunlu && <span className="text-rose-600"> *</span>}
      {alan.aciklama && <span className="block font-normal text-stone-500">{alan.aciklama}</span>}
    </span>
  );
  if (alan.tur === 'secim_listesi') {
    const secili = Array.isArray(deger) ? deger : [];
    return (
      <fieldset className="space-y-1.5">
        <legend>{baslik}</legend>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(alan.secenekler).map(([deg, ad]) => (
            <label key={deg} className="flex items-center gap-2 px-2.5 py-1.5 rounded-sm bg-paper-100 hover:bg-paper-200 cursor-pointer">
              <input type="checkbox" checked={secili.includes(deg)} className="accent-petrol"
                onChange={() => onDegis(secili.includes(deg) ? secili.filter((x) => x !== deg) : [...secili, deg])} />
              <span>{ad}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }
  return (
    <label className="block space-y-1">
      {baslik}
      <input type={alan.tur === 'sayi' ? 'number' : alan.tur === 'url' ? 'url' : 'text'} value={typeof deger === 'string' ? deger : ''}
        required={alan.zorunlu} min={alan.tur === 'sayi' ? 0 : undefined} max={alan.tur === 'sayi' ? 30 : undefined}
        placeholder={alan.tur === 'url' ? 'https://' : undefined}
        onChange={(e) => onDegis(e.target.value)}
        maxLength={alan.tur === 'metin' ? 300 : undefined} spellCheck={alan.tur === 'metin' ? false : undefined}
        className={`w-full p-2.5 rounded-lg border border-paper-300 bg-white ${alan.tur === 'sayi' ? '' : 'font-mono'}`} />
    </label>
  );
};

// Deneme sonucu, kaç kayıt bulundu, hangileri konulara takıldı (★ ile).
const DenemeSonucu: React.FC<{ sonuc: DeneSonucu }> = ({ sonuc }) => {
  if (sonuc.toplam === 0) {
    return (
      <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 border border-amber-300 text-amber-900">
        <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
        <span>Son {sonuc.gun} günde hiç kayıt bulunamadı. Ayar yanlış olabilir ya da kaynak bu sürede bir şey yayımlamamış.</span>
      </div>
    );
  }
  const ilgili = sonuc.kayitlar.filter((k) => Object.keys(k.eslesen).length > 0).length;
  return (
    <div className="space-y-2">
      <p className="text-stone-700">
        Son {sonuc.gun} günde <strong>{sonuc.toplam}</strong> kayıt bulundu, gösterilenlerden <strong>{ilgili}</strong> tanesi
        konulara takılıyor (★). {sonuc.kesildi && `İlk ${sonuc.kayitlar.length} tanesi gösteriliyor.`}
      </p>
      <div className="max-h-64 overflow-y-auto border border-paper-300 rounded-lg divide-y divide-paper-200">
        {sonuc.kayitlar.map((k, i) => {
          const eslesti = Object.keys(k.eslesen).length > 0;
          return (
            <div key={i} className={`px-3 py-2 ${eslesti ? 'bg-amber-50/60' : ''}`}>
              <div className="flex items-start gap-2">
                {eslesti ? <Star className="w-3.5 h-3.5 text-gold shrink-0 mt-0.5 fill-current" /> : <span className="w-3.5 shrink-0" />}
                <div className="min-w-0">
                  <div className="text-stone-900 wrap-break-word">{k.baslik}</div>
                  <div className="text-[11px] text-stone-500">
                    {k.tarih}
                    {eslesti && ` · ${Object.keys(k.eslesen).join(', ')} · ${k.is_kollari.join(', ')}`}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

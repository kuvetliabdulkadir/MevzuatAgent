// Arşivde "Rapora giren diğer belgeler": arşiv türü olmayan (mevzuat.gov.tr'de bulunmayan) ama rapora giren belgeler,
// örneğin Resmî Gazete'deki kurul kararları, ilanlar ve kurum duyuruları. Tam metin, tablo/görseller, saklanan PDF.
import React, { useEffect, useState } from 'react';
import { FileText, Loader2, Search, X } from 'lucide-react';
import { api } from '../api';
import { RaporBelgeleri, RaporBelgesi } from '../types/api';
import { tarihSaat } from '../tarih';
import { BelgeGorselleri } from '../components/BelgeGorselleri';

export const RaporBelgeleriKarti: React.FC<{ aranan: string }> = ({ aranan }) => {
  const [ara, setAra] = useState(aranan);
  const [sorgu, setSorgu] = useState(aranan);
  const [sayfa, setSayfa] = useState(1);
  const [veri, setVeri] = useState<RaporBelgeleri | null>(null);
  const [hata, setHata] = useState('');
  const [yukleniyor, setYukleniyor] = useState(false);
  const [secili, setSecili] = useState<number | null>(null);

  // Arşiv listesinde arama yapılınca burada da aynı sorgu aranır.
  useEffect(() => { setAra(aranan); setSorgu(aranan); setSayfa(1); }, [aranan]);
  useEffect(() => {
    setYukleniyor(true);
    const q = new URLSearchParams({ q: sorgu, sayfa: String(sayfa) });
    api.get<RaporBelgeleri>(`/arsiv/rapor-belgeleri?${q}`).then((v) => { setVeri(v); setHata(''); })
      .catch((e) => setHata(e.message)).finally(() => setYukleniyor(false));
  }, [sorgu, sayfa]);
  const sayfaSayisi = veri ? Math.max(1, Math.ceil(veri.toplam / veri.boyut)) : 1;

  return (
    <div className="bg-white rounded-xl border border-paper-300 overflow-hidden text-xs">
      <div className="px-4 sm:px-5 py-3 border-b border-paper-200 space-y-2">
        <div>
          <p className="font-semibold text-stone-900 text-sm">Rapora giren diğer belgeler</p>
          <p className="text-stone-500">
            Arşivde olmayan ama rapora giren belgeler: Resmî Gazete'deki kurul kararları, ilanlar, kurum duyuruları.
            Metinleri, tabloları ve alındıkları andaki PDF kopyalarıyla.
          </p>
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setSayfa(1); setSorgu(ara); }}>
          <div className="flex-1 flex items-center gap-1.5 border border-paper-300 rounded-lg px-2.5">
            <Search className="w-3.5 h-3.5 text-stone-400" />
            <input value={ara} onChange={(e) => setAra(e.target.value)} placeholder="Başlıkta, kaynakçada ya da metinde ara (15074, motorin…)"
              className="flex-1 py-2 outline-none bg-transparent" />
          </div>
          <button type="submit" className="px-3 py-2 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark cursor-pointer">Ara</button>
        </form>
      </div>
      {hata && <p className="px-5 py-3 text-rose-700">{hata}</p>}
      {yukleniyor && !veri && <p className="px-5 py-4 text-stone-500 flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Yükleniyor…</p>}
      {veri && (
        <div className="divide-y divide-paper-200">
          {veri.satirlar.length === 0 && <p className="px-5 py-4 text-stone-500">{sorgu ? 'Sonuç yok.' : 'Henüz rapora giren belge yok.'}</p>}
          {veri.satirlar.map((r) => (
            <button key={r.id} type="button" onClick={() => setSecili(r.id)}
              className="w-full text-left px-4 sm:px-5 py-2.5 hover:bg-paper-100 cursor-pointer space-y-0.5">
              <span className="block font-medium text-stone-800">{r.baslik}</span>
              <span className="block text-stone-500">
                {r.tur_adi} · {tarihSaat(r.yayin_tarihi)} · Rapor #{r.rapor_id}{r.haric && ' (dağıtımdan çıkarıldı)'}
                {r.is_kollari.length > 0 && ` · ${r.is_kollari.join(', ')}`}
                {r.gorsel_sayisi > 0 && ` · ${r.gorsel_sayisi} tablo/görsel`}{r.belge_var && ' · PDF saklı'}
              </span>
              {/* Parça sunucuda HTML kaçışlı üretilir, sadece <mark> içerir (kayit_belgesi._parca). */}
              {r.parca && <span className="block arsiv-arama-parcasi" dangerouslySetInnerHTML={{ __html: r.parca }} />}
            </button>
          ))}
        </div>
      )}
      {veri && veri.toplam > veri.boyut && (
        <div className="px-4 sm:px-5 py-2.5 border-t border-paper-200 flex items-center justify-between text-stone-600">
          <span>{veri.toplam.toLocaleString('tr-TR')} belge</span>
          <span className="flex items-center gap-2">
            <button type="button" disabled={sayfa <= 1} onClick={() => setSayfa((s) => s - 1)} className="px-2 py-1 rounded border border-paper-300 disabled:opacity-40 cursor-pointer">Önceki</button>
            {sayfa} / {sayfaSayisi}
            <button type="button" disabled={sayfa >= sayfaSayisi} onClick={() => setSayfa((s) => s + 1)} className="px-2 py-1 rounded border border-paper-300 disabled:opacity-40 cursor-pointer">Sonraki</button>
          </span>
        </div>
      )}
      {secili !== null && <RaporBelgesiPenceresi kayitId={secili} onKapat={() => setSecili(null)} />}
    </div>
  );
};

const RaporBelgesiPenceresi: React.FC<{ kayitId: number; onKapat: () => void }> = ({ kayitId, onKapat }) => {
  const [b, setB] = useState<RaporBelgesi | null>(null);
  const [hata, setHata] = useState('');
  useEffect(() => {
    api.get<RaporBelgesi>(`/arsiv/rapor-belgeleri/${kayitId}`).then(setB).catch((e) => setHata(e.message));
  }, [kayitId]);
  useEffect(() => {
    const kapat = (e: KeyboardEvent) => { if (e.key === 'Escape') onKapat(); };
    window.addEventListener('keydown', kapat);
    return () => window.removeEventListener('keydown', kapat);
  }, [onKapat]);
  return (
    <div className="fixed inset-0 z-50 bg-stone-950/50 flex items-stretch sm:items-center justify-center p-0 sm:p-6" onClick={onKapat}>
      <div role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}
        className="bg-white w-full max-w-4xl sm:rounded-xl border border-paper-300 flex flex-col max-h-full text-xs">
        <div className="px-4 sm:px-5 py-3 border-b border-paper-200 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-semibold text-stone-900 text-sm">{b?.baslik ?? 'Belge'}</p>
            {b && (
              <p className="text-stone-500">
                {b.tur_adi} · {tarihSaat(b.yayin_tarihi)} · <a href={`/?rapor=${b.rapor_id}`} className="text-petrol underline">Rapor #{b.rapor_id}</a>
                {b.is_kollari.length > 0 && ` · ${b.is_kollari.join(', ')}`}
              </p>
            )}
          </div>
          <button type="button" onClick={onKapat} aria-label="Kapat" className="p-1 rounded hover:bg-paper-200 cursor-pointer shrink-0"><X className="w-4 h-4" /></button>
        </div>
        {hata && <p className="px-5 py-3 text-rose-700">{hata}</p>}
        {!b && !hata && <p className="px-5 py-3 text-stone-500 flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Yükleniyor…</p>}
        {b && (
          <div className="overflow-y-auto flex-1 px-4 sm:px-5 py-3 space-y-3">
            <div className="flex flex-wrap gap-3 text-[11px]">
              {b.belge_var && (
                <a href={`/panel/api/kayit-belgesi/${b.id}`} target="_blank" rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 font-medium text-sky-800 underline"><FileText className="w-3.5 h-3.5" /> Saklanan PDF</a>
              )}
              {b.url && <a href={b.url} target="_blank" rel="noopener noreferrer" className="font-medium text-sky-800 underline">Kaynağa git ↗</a>}
            </div>
            <p className="text-[11px] text-stone-500"><strong>Kaynakça:</strong> {b.kaynakca}</p>
            <BelgeGorselleri gorseller={b.gorseller} />
            {b.okunamadi && (
              <div className="p-2 rounded-sm bg-amber-50 border border-amber-200 text-amber-900">Belgenin metni okunamadı; saklanan PDF'e ya da kaynağa bakın.</div>
            )}
            {b.metin && (
              <div>
                <strong className="block mb-1">Metin{b.ocr && <span className="text-amber-700"> — taranmış belgeden otomatik okundu, rakam ve isimleri PDF'ten doğrulayın</span>}</strong>
                <pre className="whitespace-pre-wrap [overflow-wrap:anywhere] font-sans bg-paper-100 p-3 rounded-sm border border-paper-200">{b.metin}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

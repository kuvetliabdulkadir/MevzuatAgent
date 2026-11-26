// Arşivde bir mevzuatın penceresi: sürüm şeridi, Metin (düzenli görünüm), Orijinal (sitenin PDF'i), Değişiklikler
// (madde madde fark, silinenler, görüntü karşılaştırma) ve Ekler (görünüm ve orijinal indirme). Karar 20-27.
// Panelin CSP'si satır içi stile izin vermez: yakınlaştırma ve saydamlık hazır sınıflarla yapılır.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronDown, ChevronUp, Download, ExternalLink, FileText, Image as ImageIcon, Loader2, Minus, Paperclip, Plus, X,
} from 'lucide-react';
import { api } from '../api';
import {
  ArsivDetayi, ArsivEk, ArsivEkGorunumu, ArsivEkIcDosya, ArsivFarki, ArsivGorunumu, ArsivSayfa, ArsivSurumu,
} from '../types/api';
import { tarihSaat } from '../tarih';

const TABAN = '/panel/api/arsiv';

const OKUMA_ADI: Record<string, string> = {
  html: 'HTML metin', 'html+resim': 'Sayfa resimlerinden okundu (OCR)', 'html+ek': 'Asıl metin ekte',
  pdf_metin: "PDF'in yazısından okundu", ocr: 'OCR ile okundu (taslak)', karisik: 'PDF, bazı sayfalar OCR',
  okunamadi: 'PDF okunamadı', ocr_gerekli: 'Taranmış PDF, okunamadı',
};
const SURUM_TURU: Record<string, string> = { ilk: 'ilk', degisiklik: 'değişiklik', okuma_duzeltmesi: 'okuma düzeltmesi' };
const EK_DURUMU: Record<string, string> = {
  indi: 'indi', sitede_yok: 'sitede yok', ulasilamadi: 'ulaşılamadı', kaynakta_bozuk: 'kaynakta bozuk bağlantı',
};
const YONTEM_ADI: Record<string, string> = {
  yazi: 'yazı', ocr: 'OCR', ocr_goruntu: 'görüntü + OCR', gorsel: 'görüntü', okunamadi: 'okunamadı',
};

function boyutMetni(b: number | null | undefined): string {
  if (b == null) return '';
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / 1024 / 1024).toLocaleString('tr-TR', { maximumFractionDigits: 1 })} MB`;
}

// Görünüm HTML'i sunucuda güvenli etiketlerden kurulur (html_okuma.py), burada sadece gösterilir.
const HtmlGorunum: React.FC<{ html: string; onBuyut: (src: string) => void; className?: string }> = ({ html, onBuyut, className }) => (
  <div
    className={`arsiv-metin ${className ?? ''}`}
    onClick={(e) => {
      const hedef = e.target as HTMLElement;
      if (hedef.tagName === 'IMG') onBuyut((hedef as HTMLImageElement).src);
    }}
    dangerouslySetInnerHTML={{ __html: html }}
  />
);

// `ilkSekme` ve `ilkSurum` verilirse pencere o sekmede, o sürüm seçili açılır (rapordan "tam karşılaştırma":
// Değişiklikler sekmesinde seçili sürüm ile bir önceki karşılaştırılır).
export const MevzuatPenceresi: React.FC<{
  anahtar: string; onKapat: () => void; ilkSekme?: 'metin' | 'degisiklik'; ilkSurum?: number;
}> = ({ anahtar, onKapat, ilkSekme, ilkSurum }) => {
  const [detay, setDetay] = useState<ArsivDetayi | null>(null);
  const [surumNo, setSurumNo] = useState<number | null>(null);
  const [sekme, setSekme] = useState<'metin' | 'orijinal' | 'degisiklik' | 'ekler'>(ilkSekme ?? 'metin');
  const [buyuk, setBuyuk] = useState<string | null>(null);
  const [hata, setHata] = useState('');

  useEffect(() => {
    api.get<ArsivDetayi>(`/arsiv/mevzuat/${encodeURIComponent(anahtar)}`)
      .then((d) => {
        setDetay(d);
        const istenen = d.surumler.find((s) => s.surum_no === ilkSurum);
        // Mevzuat en az bir kez gerçekten değiştiyse (okuma düzeltmesi sayılmaz) pencere doğrudan Değişiklikler'de,
        // son değişiklik sürümü ile bir önceki karşılaştırılmış açılır; hiç değişmediyse Metin'de, son sürümle.
        // Açan yer (rapor, süzgeç) sekme ya da sürüm verdiyse o geçerli. surumler yeniden eskiye sıralı.
        const sonDegisiklik = d.surumler.find((s) => s.surum_turu === 'degisiklik');
        const degisiklikte = !ilkSekme && !!sonDegisiklik && d.surumler.length > 1;
        setSurumNo(istenen?.surum_no ?? (degisiklikte ? sonDegisiklik.surum_no : d.surumler[0]?.surum_no) ?? null);
        if (d.surumler.length < 2) setSekme('metin');
        else if (degisiklikte) setSekme('degisiklik');
      })
      .catch((e) => setHata(e.message));
  }, [anahtar, ilkSurum, ilkSekme]);
  useEffect(() => {
    const tus = (e: KeyboardEvent) => { if (e.key === 'Escape') { if (buyuk) setBuyuk(null); else onKapat(); } };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat, buyuk]);
  const surum = detay?.surumler.find((s) => s.surum_no === surumNo) ?? null;
  const ekSayisi = surum?.ekler.length ?? 0;

  return (
    <div className="fixed inset-0 z-50 bg-stone-950/50 flex items-stretch justify-center p-0 sm:p-4" onClick={onKapat}>
      <div role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}
        className="bg-white w-full max-w-6xl sm:rounded-xl border border-paper-300 flex flex-col max-h-full text-xs">
        <div className="px-4 sm:px-5 py-3 border-b border-paper-200 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-semibold text-stone-900 text-sm">{detay?.ad ?? anahtar}</p>
            {detay && (
              <p className="text-stone-500">
                {detay.tur_adi} · RG {tarihSaat(detay.rg_tarihi)}{detay.rg_sayisi ? ` / ${detay.rg_sayisi}` : ''} · {detay.anahtar}
                {detay.mulga && ' · mülga'}
                {' · '}<a href={detay.url} target="_blank" rel="noopener noreferrer" className="text-petrol underline inline-flex items-center gap-0.5">
                  sitede aç <ExternalLink className="w-3 h-3" /></a>
              </p>
            )}
            {detay && detay.konular.length > 0 && <IlgiliKonular konular={detay.konular} />}
            {detay?.kalkti && (
              <p className="mt-1 inline-block px-2 py-0.5 rounded bg-rose-100 text-rose-800 font-semibold">
                Siteden kalktı ({tarihSaat(detay.kalkti)}). Son metin aşağıda.
              </p>
            )}
          </div>
          <button type="button" onClick={onKapat} aria-label="Kapat" className="p-1 rounded hover:bg-paper-200 cursor-pointer"><X className="w-4 h-4" /></button>
        </div>
        {hata && <p className="px-5 py-3 text-rose-700">{hata}</p>}
        {detay && detay.surumler.length === 0 && (
          <p className="px-5 py-4 text-stone-600">
            {detay.durum === 'SITEDE_YOK' ? 'Sitede metin yok.' : detay.durum === 'BEKLIYOR' ? 'Metni henüz alınmadı.' : detay.durum}
            {' '}{detay.son_hata ?? ''}
          </p>
        )}
        {detay && detay.surumler.length > 0 && (
          <>
            <SurumSeridi surumler={detay.surumler} secili={surumNo} onSec={setSurumNo} />
            <div className="px-4 sm:px-5 border-b border-paper-200 flex gap-1 overflow-x-auto">
              {([['metin', 'Metin'], ['orijinal', 'Orijinal'],
                ...(detay.surumler.length > 1 ? [['degisiklik', 'Değişiklikler']] : []),
                ['ekler', `Ekler${ekSayisi ? ` (${ekSayisi})` : ''}`]] as [typeof sekme, string][]).map(([k, ad]) => (
                <button key={k} type="button" onClick={() => setSekme(k)}
                  className={`px-3 py-2 -mb-px border-b-2 font-semibold whitespace-nowrap cursor-pointer ${sekme === k ? 'border-petrol text-petrol' : 'border-transparent text-stone-500 hover:text-stone-800'}`}>
                  {ad}
                </button>
              ))}
            </div>
            <div className="overflow-y-auto flex-1">
              {surum && sekme === 'metin' && <MetinSekmesi anahtar={anahtar} surum={surum} onBuyut={setBuyuk} />}
              {surum && sekme === 'orijinal' && <OrijinalSekmesi detay={detay} surum={surum} />}
              {surum && sekme === 'degisiklik' && <DegisiklikSekmesi anahtar={anahtar} detay={detay} secili={surum.surum_no} onBuyut={setBuyuk} />}
              {surum && sekme === 'ekler' && <EklerSekmesi surum={surum} onBuyut={setBuyuk} />}
            </div>
          </>
        )}
      </div>
      {buyuk && <Buyutec src={buyuk} onKapat={() => setBuyuk(null)} />}
    </div>
  );
};

const KANIT_YERI: Record<string, string> = { baslik: 'başlıkta', amac: 'amaç/kapsamda', tanim: 'tanımlarda', govde: 'metinde' };

// Konu filtresine göre ilgili olduğu konular ve "neden" (kanıt: hangi kelime, nerede, hangi cümlede). Puan gösterilmez (karar 18).
const IlgiliKonular: React.FC<{ konular: ArsivDetayi['konular'] }> = ({ konular }) => (
  <div className="mt-1.5 flex flex-wrap items-start gap-1.5">
    <span className="text-stone-500 py-0.5">İlgili:</span>
    {konular.map((k) => (
      <details key={k.konu} className="group">
        <summary className="list-none cursor-pointer px-1.5 py-0.5 rounded bg-petrol/10 text-petrol font-semibold hover:bg-petrol/20">
          {k.konu} <span className="font-normal text-stone-500 group-open:hidden">· neden?</span>
        </summary>
        <ul className="mt-1 mb-1 p-2 rounded-lg border border-paper-300 bg-paper-50 space-y-1 max-w-2xl">
          {k.kanit.map((x, i) => (
            <li key={i} className="text-stone-700">
              <span className="font-semibold">"{x.kelime}"</span> {KANIT_YERI[x.tur] ?? x.tur}
              {x.tur !== 'baslik' && <span className="text-stone-500"> ({x.bolum})</span>}: <span className="text-stone-600">{x.parca}</span>
            </li>
          ))}
        </ul>
      </details>
    ))}
  </div>
);

// Sürüm şeridi: v1 · v2 · v3, tarihleriyle. Okuma düzeltmeleri soluk.
const SurumSeridi: React.FC<{ surumler: ArsivSurumu[]; secili: number | null; onSec: (n: number) => void }> = ({ surumler, secili, onSec }) => {
  const sirali = [...surumler].sort((a, b) => a.surum_no - b.surum_no);
  return (
    <div className="px-4 sm:px-5 py-2 border-b border-paper-200 flex items-center gap-1.5 overflow-x-auto">
      <span className="text-stone-500 shrink-0">Sürümler:</span>
      {sirali.map((s, i) => (
        <React.Fragment key={s.surum_no}>
          {i > 0 && <span className="text-stone-300">─</span>}
          <button type="button" onClick={() => onSec(s.surum_no)}
            title={`${SURUM_TURU[s.surum_turu] ?? s.surum_turu} · ${OKUMA_ADI[s.okuma] ?? s.okuma}${s.kalite === 'kusurlu' ? ' · kusurlu: ' + s.kalite_notlari.join('; ') : ''}`}
            className={`shrink-0 px-2 py-1 rounded-lg border text-left cursor-pointer ${
              secili === s.surum_no ? 'border-petrol bg-petrol/10 text-petrol' : 'border-paper-300 hover:bg-paper-100'
            } ${s.surum_turu === 'okuma_duzeltmesi' ? 'opacity-60' : ''}`}>
            <span className="font-semibold">v{s.surum_no}</span>
            <span className="text-stone-500"> · yakalandı {tarihSaat(s.cekildi)}</span>
            {s.rg_yeni.length > 0 && <span className="text-stone-600"> · RG {s.rg_yeni.slice(0, 2).map((r) => `${r.tarih}/${r.sayi}`).join(', ')}</span>}
            {s.surum_turu === 'okuma_duzeltmesi' && <span className="text-stone-500"> · okuma düzeltmesi</span>}
            {s.kalite === 'kusurlu' && <span className="ml-1 px-1 rounded bg-amber-100 text-amber-800">kusurlu</span>}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
};

const MetinSekmesi: React.FC<{ anahtar: string; surum: ArsivSurumu; onBuyut: (s: string) => void }> = ({ anahtar, surum, onBuyut }) => {
  const [veri, setVeri] = useState<ArsivGorunumu | null>(null);
  const [hata, setHata] = useState('');
  useEffect(() => {
    setVeri(null);
    api.get<ArsivGorunumu>(`/arsiv/mevzuat/${encodeURIComponent(anahtar)}/gorunum?surum=${surum.surum_no}`)
      .then(setVeri).catch((e) => setHata(e.message));
  }, [anahtar, surum.surum_no]);
  const yontemler = Object.entries(surum.sayfa_yontemleri).filter(([, n]) => n > 0);
  return (
    <div className="px-4 sm:px-8 py-4 space-y-3">
      <p className="text-stone-500">
        {OKUMA_ADI[surum.okuma] ?? surum.okuma} · {surum.uzunluk.toLocaleString('tr-TR')} karakter
        {surum.sayfa_sayisi > 0 && <> · {surum.sayfa_sayisi} sayfa ({yontemler.map(([y, n]) => `${n} ${YONTEM_ADI[y] ?? y}`).join(', ')})</>}
        {surum.resim_sayisi > 0 && <> · {surum.resim_sayisi} resim</>}
      </p>
      {surum.kalite === 'kusurlu' && (
        <p className="p-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-900">
          Okuma kusurlu: {surum.kalite_notlari.join('; ')}. Orijinal belgeye bakınız.
        </p>
      )}
      {(surum.okuma === 'ocr' || surum.okuma === 'karisik' || surum.okuma === 'html+resim') && (
        <p className="text-stone-500">OCR ile okunan kısımlar taslaktır, rakam ve isimlerde hata olabilir. Hukuki dayanak orijinal belgedir.</p>
      )}
      {hata && <p className="text-rose-700">{hata}</p>}
      {!veri && !hata && <p className="text-stone-500 flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Yükleniyor…</p>}
      {veri && <HtmlGorunum html={veri.gorunum} onBuyut={onBuyut} />}
    </div>
  );
};

const OrijinalSekmesi: React.FC<{ detay: ArsivDetayi; surum: ArsivSurumu }> = ({ detay, surum }) => {
  const pdf = `${TABAN}/mevzuat/${encodeURIComponent(detay.anahtar)}/pdf?surum=${surum.surum_no}&tur=site`;
  return (
    <div className="px-4 sm:px-5 py-3 space-y-2 h-full flex flex-col">
      <div className="flex flex-wrap items-center gap-2">
        {surum.site_pdf && (
          <a href={`${pdf}&indir=1`} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark">
            <Download className="w-3.5 h-3.5" /> {surum.site_pdf_turu === 'uretilen' ? "Sitenin ürettiği PDF'i indir" : "Orijinal PDF'i indir"}
          </a>
        )}
        {surum.pdf && surum.site_pdf_turu !== 'asil' && (
          <a href={`${TABAN}/mevzuat/${encodeURIComponent(detay.anahtar)}/pdf?surum=${surum.surum_no}&tur=metin&indir=1`}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-paper-300 font-semibold hover:bg-paper-100">
            <Download className="w-3.5 h-3.5" /> Metnin okunduğu PDF
          </a>
        )}
        <a href={detay.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-petrol underline">
          Sitedeki sayfası <ExternalLink className="w-3 h-3" />
        </a>
        {surum.site_pdf_turu === 'uretilen' && (
          <span className="text-stone-500">Sitede kayıtlı PDF yok; bu PDF'i site, sürümün alındığı gün HTML'den üretti.</span>
        )}
      </div>
      {surum.site_pdf ? (
        <iframe title="Orijinal PDF" src={pdf} className="w-full flex-1 min-h-[70vh] border border-paper-300 rounded-lg" />
      ) : (
        <p className="text-stone-600">Bu sürümün PDF'i yok. Orijinal metin sitedeki sayfasındadır; arşivdeki "Metin" görünümü o sayfadan alındı.</p>
      )}
    </div>
  );
};

const FARK_ADI: Record<string, { ad: string; renk: string }> = {
  degisti: { ad: 'değişti', renk: 'bg-sky-100 text-sky-800 border-sky-300' },
  eklendi: { ad: 'eklendi', renk: 'bg-emerald-100 text-emerald-800 border-emerald-300' },
  kaldirildi: { ad: 'kaldırıldı', renk: 'bg-rose-100 text-rose-800 border-rose-300' },
  mulga: { ad: 'mülga', renk: 'bg-stone-200 text-stone-700 border-stone-300' },
  ayni: { ad: '', renk: 'border-transparent' },
};

// Rapor penceresinde kalemin içinde açılan ayrıntılı karşılaştırma: arşivdeki Değişiklikler sekmesinin aynısı (sürüm
// seçimi, sadece değişenler / tam metin / yan yana, değişiklikler arasında gezinme), ayrı pencere açmadan.
export const AyrintiliKarsilastirma: React.FC<{ anahtar: string; surum: number }> = ({ anahtar, surum }) => {
  const [detay, setDetay] = useState<ArsivDetayi | null>(null);
  const [hata, setHata] = useState('');
  useEffect(() => {
    api.get<ArsivDetayi>(`/arsiv/mevzuat/${encodeURIComponent(anahtar)}`).then(setDetay).catch((e) => setHata(e.message));
  }, [anahtar]);
  if (hata) return <p className="text-rose-700 text-xs">{hata}</p>;
  if (!detay) return <p className="text-stone-500 text-xs flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Yükleniyor…</p>;
  return (
    <div className="border border-paper-300 rounded-lg bg-white text-xs max-h-[70vh] overflow-y-auto">
      <DegisiklikSekmesi anahtar={anahtar} detay={detay} secili={surum} onBuyut={(src) => window.open(src, '_blank', 'noopener')} />
    </div>
  );
};

const DegisiklikSekmesi: React.FC<{ anahtar: string; detay: ArsivDetayi; secili: number; onBuyut: (s: string) => void }> = ({ anahtar, detay, secili, onBuyut }) => {
  const [duzeltmeler, setDuzeltmeler] = useState(false);
  const adaylar = useMemo(() => [...detay.surumler].sort((a, b) => a.surum_no - b.surum_no)
    .filter((s) => duzeltmeler || s.surum_turu !== 'okuma_duzeltmesi' || s.surum_no === secili), [detay, duzeltmeler, secili]);
  // Seçili sürüm bir öncekiyle karşılaştırılır. Öncesi yoksa (v1) bir sonrakiyle: v1 seçilince "v1 → v2" görünür,
  // boş "iki farklı sürüm seçin" uyarısı çıkmaz.
  const [varsayilanEski, varsayilanYeni] = useMemo(() => {
    const onceki = adaylar.filter((s) => s.surum_no < secili);
    if (onceki.length) return [onceki[onceki.length - 1].surum_no, secili];
    const sonraki = adaylar.find((s) => s.surum_no > secili);
    return [secili, sonraki ? sonraki.surum_no : secili];
  }, [adaylar, secili]);
  const [eski, setEski] = useState<number | undefined>(varsayilanEski);
  const [yeni, setYeni] = useState<number>(varsayilanYeni);
  const [kip, setKip] = useState<'degisen' | 'tam' | 'yanyana'>('degisen');
  const [eskileriGizle, setEskileriGizle] = useState(false);
  const [fark, setFark] = useState<ArsivFarki | null>(null);
  const [hata, setHata] = useState('');
  const [konum, setKonum] = useState(0);
  const kutular = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => { setYeni(varsayilanYeni); setEski(varsayilanEski); }, [varsayilanYeni, varsayilanEski]);
  useEffect(() => {
    if (eski === undefined || eski === yeni) { setFark(null); return; }
    setFark(null);
    api.get<ArsivFarki>(`/arsiv/mevzuat/${encodeURIComponent(anahtar)}/fark?eski=${eski}&yeni=${yeni}`)
      .then((f) => { setFark(f); setKonum(0); setHata(''); }).catch((e) => setHata(e.message));
  }, [anahtar, eski, yeni]);

  const gosterilen = (fark?.bolumler ?? []).filter((b) => (kip === 'tam' || b.tur !== 'ayni') && !(eskileriGizle && b.tur === 'kaldirildi'));
  const degisenler = gosterilen.map((b, i) => (b.tur === 'ayni' ? -1 : i)).filter((i) => i >= 0);
  const git = (yon: number) => {
    if (!degisenler.length) return;
    const yeniKonum = (konum + yon + degisenler.length) % degisenler.length;
    setKonum(yeniKonum);
    kutular.current[degisenler[yeniKonum]]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  const sec = (deger: number | undefined, ayarla: (n: number) => void) => (
    <select value={deger ?? ''} onChange={(e) => ayarla(Number(e.target.value))} className="border border-paper-300 rounded-lg px-2 py-1 bg-white">
      {adaylar.map((s) => <option key={s.surum_no} value={s.surum_no}>v{s.surum_no} · {tarihSaat(s.cekildi)}{s.surum_turu === 'okuma_duzeltmesi' ? ' (okuma düzeltmesi)' : ''}</option>)}
    </select>
  );

  return (
    <div className="px-4 sm:px-5 py-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {sec(eski, setEski)} <span className="text-stone-400">→</span> {sec(yeni, setYeni)}
        <span className="flex rounded-lg border border-paper-300 overflow-hidden">
          {([['degisen', 'Sadece değişenler'], ['tam', 'Tam metin'], ['yanyana', 'Yan yana']] as const).map(([k, ad]) => (
            <button key={k} type="button" onClick={() => setKip(k)}
              className={`px-2.5 py-1 cursor-pointer ${kip === k ? 'bg-petrol text-white' : 'hover:bg-paper-100'}`}>{ad}</button>
          ))}
        </span>
        <label className="flex items-center gap-1 cursor-pointer"><input type="checkbox" checked={eskileriGizle} onChange={(e) => setEskileriGizle(e.target.checked)} /> kaldırılanları gizle</label>
        <label className="flex items-center gap-1 cursor-pointer"><input type="checkbox" checked={duzeltmeler} onChange={(e) => setDuzeltmeler(e.target.checked)} /> okuma düzeltmelerini de göster</label>
      </div>
      {hata && <p className="text-rose-700">{hata}</p>}
      {eski === yeni && <p className="text-stone-500">Karşılaştırmak için iki farklı sürüm seçin.</p>}
      {fark && (
        <>
          <div className="sticky top-0 z-10 bg-white/95 py-1.5 flex flex-wrap items-center gap-2 border-b border-paper-200">
            <span className="font-semibold text-stone-800">
              {fark.sayac.degisti} değişti · {fark.sayac.eklendi} eklendi · {fark.sayac.kaldirildi} kaldırıldı
              {fark.sayac.mulga > 0 && ` · ${fark.sayac.mulga} mülga`}
            </span>
            {fark.yeni_surum_turu === 'okuma_duzeltmesi' && <span className="text-stone-500">(okuma düzeltmesi: mevzuat değişmedi, okuma iyileşti)</span>}
            <span className="ml-auto flex items-center gap-1">
              <button type="button" onClick={() => git(-1)} className="p-1 rounded border border-paper-300 hover:bg-paper-100 cursor-pointer" aria-label="Önceki değişiklik"><ChevronUp className="w-3.5 h-3.5" /></button>
              <span className="text-stone-500">{degisenler.length ? `${konum + 1}/${degisenler.length}` : '0'}</span>
              <button type="button" onClick={() => git(1)} className="p-1 rounded border border-paper-300 hover:bg-paper-100 cursor-pointer" aria-label="Sonraki değişiklik"><ChevronDown className="w-3.5 h-3.5" /></button>
            </span>
          </div>
          {gosterilen.length === 0 && <p className="text-stone-500">Metinde değişiklik yok (sadece ekler ya da resimler değişmiş olabilir).</p>}
          <div className="space-y-2">
            {gosterilen.map((b, i) => (
              <div key={`${b.bolum}-${i}`} ref={(el) => { kutular.current[i] = el; }}
                title={b.tur === 'degisti' && b.eski ? `v${fark.yeni}'de değişti. Önceki hali: ${b.eski.slice(0, 400)}` : undefined}
                className={`rounded-lg ${b.tur === 'ayni' ? 'px-3 py-1' : `border-l-4 px-3 py-2 ${b.tur === 'kaldirildi' ? 'border-rose-400 bg-rose-50/60' : b.tur === 'eklendi' ? 'border-emerald-400' : b.tur === 'mulga' ? 'border-stone-400' : 'border-sky-400'}`}`}>
                {b.tur !== 'ayni' && (
                  <p className="mb-1 flex items-center gap-2">
                    <span className="font-semibold text-stone-800">{b.bolum}</span>
                    <span className={`px-1.5 rounded border ${FARK_ADI[b.tur].renk}`}>
                      {b.tur === 'kaldirildi' ? `v${fark.yeni}'de kaldırıldı` : b.tur === 'mulga' ? 'Mülga' : FARK_ADI[b.tur].ad}
                    </span>
                  </p>
                )}
                {kip === 'yanyana' && b.tur !== 'ayni' ? (
                  <div className="grid sm:grid-cols-2 gap-3">
                    <div className="arsiv-fark text-stone-600"><p className="text-[11px] text-stone-400 mb-0.5">v{fark.eski}</p>{b.eski ?? '—'}</div>
                    <div className="arsiv-fark"><p className="text-[11px] text-stone-400 mb-0.5">v{fark.yeni}</p><span dangerouslySetInnerHTML={{ __html: b.html }} /></div>
                  </div>
                ) : (
                  <div className="arsiv-fark" dangerouslySetInnerHTML={{ __html: b.html }} />
                )}
              </div>
            ))}
          </div>
          {eski !== undefined && <GoruntuKarsilastirma anahtar={anahtar} eski={eski} yeni={yeni} onBuyut={onBuyut} />}
        </>
      )}
    </div>
  );
};

// Harita ve taranmış sayfa görüntülerinin karşılaştırması (karar 24): yan yana ya da üst üste, saydamlık sürgüsüyle.
const SAYDAMLIK = ['opacity-0', 'opacity-10', 'opacity-20', 'opacity-30', 'opacity-40', 'opacity-50', 'opacity-60', 'opacity-70', 'opacity-80', 'opacity-90', 'opacity-100'];

const GoruntuKarsilastirma: React.FC<{ anahtar: string; eski: number; yeni: number; onBuyut: (s: string) => void }> = ({ anahtar, eski, yeni, onBuyut }) => {
  const [ciftler, setCiftler] = useState<{ no: number; eski: string; yeni: string }[]>([]);
  const [kip, setKip] = useState<'yanyana' | 'ustuste'>('yanyana');
  const [oran, setOran] = useState(5);
  useEffect(() => {
    const al = (n: number) => api.get<ArsivGorunumu>(`/arsiv/mevzuat/${encodeURIComponent(anahtar)}/gorunum?surum=${n}`);
    Promise.all([al(eski), al(yeni)]).then(([a, b]) => {
      const goruntu = (s: ArsivSayfa[]) => new Map(s.filter((p) => p.goruntu).map((p) => [p.no, p.goruntu as string]));
      const ea = goruntu(a.sayfalar), yb = goruntu(b.sayfalar);
      setCiftler([...yb.entries()].filter(([no, y]) => ea.has(no) && ea.get(no) !== y)
        .map(([no, y]) => ({ no, eski: ea.get(no) as string, yeni: y })));
    }).catch(() => setCiftler([]));
  }, [anahtar, eski, yeni]);
  if (!ciftler.length) return null;
  const adres = (yol: string) => `${TABAN}/goruntu?yol=${encodeURIComponent(yol)}`;
  return (
    <div className="pt-3 border-t border-paper-200 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-semibold text-stone-800 flex items-center gap-1.5"><ImageIcon className="w-4 h-4" /> Değişen sayfa görüntüleri ({ciftler.length})</p>
        <span className="flex rounded-lg border border-paper-300 overflow-hidden">
          {([['yanyana', 'Yan yana'], ['ustuste', 'Üst üste']] as const).map(([k, ad]) => (
            <button key={k} type="button" onClick={() => setKip(k)} className={`px-2.5 py-1 cursor-pointer ${kip === k ? 'bg-petrol text-white' : 'hover:bg-paper-100'}`}>{ad}</button>
          ))}
        </span>
        {kip === 'ustuste' && (
          <label className="flex items-center gap-1.5">v{eski}
            <input type="range" min={0} max={10} value={oran} onChange={(e) => setOran(Number(e.target.value))} />v{yeni}
          </label>
        )}
      </div>
      {ciftler.map((c) => (
        <div key={c.no}>
          <p className="text-stone-500 mb-1">s. {c.no}</p>
          {kip === 'yanyana' ? (
            <div className="grid sm:grid-cols-2 gap-2">
              <img src={adres(c.eski)} alt={`v${eski} s. ${c.no}`} className="w-full border border-paper-300 cursor-zoom-in" onClick={() => onBuyut(adres(c.eski))} />
              <img src={adres(c.yeni)} alt={`v${yeni} s. ${c.no}`} className="w-full border border-paper-300 cursor-zoom-in" onClick={() => onBuyut(adres(c.yeni))} />
            </div>
          ) : (
            <div className="relative border border-paper-300">
              <img src={adres(c.eski)} alt={`v${eski} s. ${c.no}`} className="w-full block" />
              <img src={adres(c.yeni)} alt={`v${yeni} s. ${c.no}`} className={`w-full absolute inset-0 ${SAYDAMLIK[oran]}`} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

const EklerSekmesi: React.FC<{ surum: ArsivSurumu; onBuyut: (s: string) => void }> = ({ surum, onBuyut }) => {
  if (!surum.ekler.length) return <p className="px-5 py-4 text-stone-500">Bu sürümde ek yok.</p>;
  return (
    <div className="px-4 sm:px-5 py-3 space-y-2">
      <p className="text-stone-500">"İndir" her zaman sitenin verdiği orijinal dosyayı verir. "Görünüm" panelde okunur haliyle gösterir.</p>
      <div className="divide-y divide-paper-200 border border-paper-300 rounded-lg">
        {surum.ekler.map((e, i) => <EkSatiri key={`${e.ad}-${i}`} ek={e} onBuyut={onBuyut} />)}
      </div>
    </div>
  );
};

const EkSatiri: React.FC<{ ek: ArsivEk; onBuyut: (s: string) => void }> = ({ ek, onBuyut }) => {
  const [acik, setAcik] = useState(false);
  const indi = ek.durum === 'indi' && ek.sha256;
  return (
    <div className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Paperclip className="w-3.5 h-3.5 text-stone-400" />
        <span className="font-medium text-stone-800 break-all">{ek.ad}</span>
        <span className="text-stone-500">{ek.bicim ?? ''}{ek.boyut ? ` · ${boyutMetni(ek.boyut)}` : ''}</span>
        {!indi && <span className="px-1.5 rounded bg-rose-100 text-rose-800">{EK_DURUMU[ek.durum] ?? ek.durum}</span>}
        {indi && ek.okuma && ek.okuma !== 'tamam' && <span className="px-1.5 rounded bg-amber-100 text-amber-800">görünüm yok</span>}
        {indi && (
          <span className="ml-auto flex gap-1.5">
            {(ek.gorunum_var || ek.ic.length > 0) && (
              <button type="button" onClick={() => setAcik((a) => !a)} className="inline-flex items-center gap-1 px-2 py-1 rounded border border-paper-300 hover:bg-paper-100 cursor-pointer">
                <FileText className="w-3.5 h-3.5" /> {acik ? 'Kapat' : ek.ic.length ? 'İçindekiler' : 'Görünüm'}
              </button>
            )}
            <a href={`${TABAN}/dosya/${ek.sha256}?indir=1`} className="inline-flex items-center gap-1 px-2 py-1 rounded bg-petrol text-white hover:bg-petrol-dark">
              <Download className="w-3.5 h-3.5" /> İndir
            </a>
          </span>
        )}
      </div>
      {ek.not && <p className="text-stone-500 mt-0.5">{ek.not}</p>}
      {!indi && ek.denenen.length > 0 && (
        <details className="mt-1 text-stone-500">
          <summary className="cursor-pointer">Denenen adresler ({ek.denenen.length})</summary>
          <ul className="mt-1 space-y-0.5">{ek.denenen.map((d, i) => <li key={i} className="break-all">{d.url} → {d.sonuc}</li>)}</ul>
        </details>
      )}
      {acik && ek.sha256 && <EkGorunumu ozet={ek.sha256} onBuyut={onBuyut} />}
    </div>
  );
};

const EkGorunumu: React.FC<{ ozet: string; onBuyut: (s: string) => void; derinlik?: number }> = ({ ozet, onBuyut, derinlik = 0 }) => {
  const [veri, setVeri] = useState<ArsivEkGorunumu | null>(null);
  const [hata, setHata] = useState('');
  useEffect(() => { api.get<ArsivEkGorunumu>(`/arsiv/dosya/${ozet}/gorunum`).then(setVeri).catch((e) => setHata(e.message)); }, [ozet]);
  if (hata) return <p className="text-rose-700 mt-2">{hata}</p>;
  if (!veri) return <p className="text-stone-500 mt-2 flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Yükleniyor…</p>;
  return (
    <div className="mt-2 border border-paper-200 rounded-lg p-3 bg-paper-50 space-y-2">
      {veri.notlar.length > 0 && <p className="text-stone-500">{veri.notlar.join(' · ')}</p>}
      {veri.ic.length > 0 && (
        <div className="divide-y divide-paper-200 border border-paper-200 rounded bg-white">
          {veri.ic.map((d, i) => <IcDosyaSatiri key={`${d.ad}-${i}`} dosya={d} onBuyut={onBuyut} derinlik={derinlik} />)}
        </div>
      )}
      {veri.gorunum ? (
        <div className="max-h-[70vh] overflow-auto bg-white border border-paper-200 rounded p-3">
          <HtmlGorunum html={veri.gorunum} onBuyut={onBuyut} />
        </div>
      ) : veri.bicim === 'pdf' ? (
        <iframe title={veri.ad} src={`${TABAN}/dosya/${veri.sha256}`} className="w-full min-h-[60vh] border border-paper-300 rounded" />
      ) : veri.bicim === 'resim' ? (
        <img src={`${TABAN}/dosya/${veri.sha256}`} alt={veri.ad} className="max-w-full cursor-zoom-in" onClick={() => onBuyut(`${TABAN}/dosya/${veri.sha256}`)} />
      ) : veri.ic.length === 0 && (
        <p className="text-stone-600">Bu dosyanın görünümü yok{veri.durum === 'hata' ? ' (okunamadı)' : ''}. Orijinalini indirerek açabilirsiniz.</p>
      )}
    </div>
  );
};

const IcDosyaSatiri: React.FC<{ dosya: ArsivEkIcDosya; onBuyut: (s: string) => void; derinlik: number }> = ({ dosya, onBuyut, derinlik }) => {
  const [acik, setAcik] = useState(false);
  return (
    <div className="px-2.5 py-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="break-all">{dosya.ad}</span>
        <span className="text-stone-500">{dosya.bicim}{dosya.boyut ? ` · ${boyutMetni(dosya.boyut)}` : ''}</span>
        {dosya.durum && dosya.durum !== 'tamam' && <span className="px-1.5 rounded bg-amber-100 text-amber-800">görünüm yok</span>}
        {dosya.sha256 && (
          <span className="ml-auto flex gap-1.5">
            {derinlik < 3 && (
              <button type="button" onClick={() => setAcik((a) => !a)} className="px-2 py-0.5 rounded border border-paper-300 hover:bg-paper-100 cursor-pointer">{acik ? 'Kapat' : 'Görünüm'}</button>
            )}
            <a href={`${TABAN}/dosya/${dosya.sha256}?indir=1`} className="px-2 py-0.5 rounded bg-petrol text-white hover:bg-petrol-dark">İndir</a>
          </span>
        )}
      </div>
      {acik && dosya.sha256 && <EkGorunumu ozet={dosya.sha256} onBuyut={onBuyut} derinlik={derinlik + 1} />}
    </div>
  );
};

// Tam ekran görüntü, yakınlaştırmalı (harita, taranmış tablo, sayfa resmi).
const OLCEKLER = ['w-full', 'w-[150%]', 'w-[200%]', 'w-[300%]', 'w-[400%]'];

const Buyutec: React.FC<{ src: string; onKapat: () => void }> = ({ src, onKapat }) => {
  const [olcek, setOlcek] = useState(0);
  const degistir = useCallback((d: number) => setOlcek((o) => Math.max(0, Math.min(OLCEKLER.length - 1, o + d))), []);
  return (
    <div className="fixed inset-0 z-[60] bg-stone-950/90 flex flex-col" onClick={onKapat}>
      <div className="flex items-center justify-end gap-2 p-2 text-white" onClick={(e) => e.stopPropagation()}>
        <button type="button" onClick={() => degistir(-1)} aria-label="Uzaklaştır" className="p-1.5 rounded bg-white/10 hover:bg-white/20 cursor-pointer"><Minus className="w-4 h-4" /></button>
        <button type="button" onClick={() => degistir(1)} aria-label="Yakınlaştır" className="p-1.5 rounded bg-white/10 hover:bg-white/20 cursor-pointer"><Plus className="w-4 h-4" /></button>
        <a href={src} target="_blank" rel="noopener noreferrer" className="p-1.5 rounded bg-white/10 hover:bg-white/20" aria-label="Yeni sekmede aç"><ExternalLink className="w-4 h-4" /></a>
        <button type="button" onClick={onKapat} aria-label="Kapat" className="p-1.5 rounded bg-white/10 hover:bg-white/20 cursor-pointer"><X className="w-4 h-4" /></button>
      </div>
      <div className="flex-1 overflow-auto" onClick={(e) => e.stopPropagation()}>
        <img src={src} alt="" className={`${OLCEKLER[olcek]} max-w-none mx-auto bg-white`} onClick={() => degistir(1)} />
      </div>
    </div>
  );
};

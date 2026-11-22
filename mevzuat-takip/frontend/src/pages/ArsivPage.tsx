// "Arşiv" sayfası. mevzuat.gov.tr'deki yürürlükteki mevzuatın arşivi, işi başlatma/durdurma, ilerleme, liste ve metin.
import React, { useCallback, useEffect, useState } from 'react';
import { Archive, Loader2, Play, ScrollText, Search, Square, X } from 'lucide-react';
import { api } from '../api';
import { ArsivDurumu, ArsivGunlugu, ArsivListesi, ArsivMetinDurumu } from '../types/api';
import { MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';
import { MevzuatPenceresi } from './ArsivMevzuat';

// Metin durumlarının ekrandaki adı ve rengi.
const METIN_DURUMU: Record<ArsivMetinDurumu, { ad: string; renk: string }> = {
  TAMAM: { ad: 'Alındı', renk: 'text-emerald-700' },
  BEKLIYOR: { ad: 'Sırada', renk: 'text-sky-700' },
  OCR_GEREKLI: { ad: 'Taranmış PDF (OCR yapılamadı)', renk: 'text-amber-700' },
  SITEDE_YOK: { ad: 'Sitede yok', renk: 'text-stone-500' },
  HATA: { ad: 'Hata', renk: 'text-rose-700' },
};
const DURUM_SIRASI: ArsivMetinDurumu[] = ['TAMAM', 'BEKLIYOR', 'OCR_GEREKLI', 'SITEDE_YOK', 'HATA'];
const AKTIF = ['BEKLIYOR', 'CALISIYOR', 'DURDURULUYOR'];

export const ArsivPage: React.FC<{ ayarYonetebilir: boolean }> = ({ ayarYonetebilir }) => {
  const [durum, setDurum] = useState<ArsivDurumu | null>(null);
  const [hata, setHata] = useState('');
  const [seciliAnahtar, setSeciliAnahtar] = useState<string | null>(null);
  const [gunlukIstek, setGunlukIstek] = useState<number | null>(null);
  const suruyor = !!durum?.istek && AKTIF.includes(durum.istek.durum);

  // Durumu sunucudan çeker.
  const yukle = useCallback(() => {
    api.get<ArsivDurumu>('/arsiv/durum').then((d) => { setDurum(d); setHata(''); }).catch((e) => setHata(e.message));
  }, []);
  // İş sürerken 5 sn'de bir, yoksa 30 sn'de bir sorgulanır.
  useEffect(() => {
    yukle();
    const t = window.setInterval(yukle, suruyor ? 5_000 : 30_000);
    return () => window.clearInterval(t);
  }, [yukle, suruyor]);

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="pb-4 border-b border-paper-300">
        <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">Arşiv</h2>
        <p className="text-xs text-stone-500 mt-0.5">
          mevzuat.gov.tr'de yürürlükte olan bütün mevzuat, metinleri ve ekleriyle. Değişen metin yeni sürüm olarak saklanır, eskisi silinmez.
        </p>
      </div>
      {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
      {durum && <IsKarti durum={durum} baslatabilir={ayarYonetebilir} suruyor={suruyor} onDegisti={setDurum} onGunluk={setGunlukIstek} />}
      {durum && <SayilarKarti durum={durum} />}
      {durum && durum.toplam > 0 && <Liste turler={durum.turler} onSec={setSeciliAnahtar} />}
      {seciliAnahtar && <MevzuatPenceresi anahtar={seciliAnahtar} onKapat={() => setSeciliAnahtar(null)} />}
      {gunlukIstek !== null && <GunlukPenceresi istekId={gunlukIstek} onSec={setSeciliAnahtar} onKapat={() => setGunlukIstek(null)} />}
    </div>
  );
};

// İşi başlatma/durdurma ve ilerleme kartı.
const IsKarti: React.FC<{
  durum: ArsivDurumu; baslatabilir: boolean; suruyor: boolean; onDegisti: (d: ArsivDurumu) => void; onGunluk: (id: number) => void;
}> = ({ durum, baslatabilir, suruyor, onDegisti, onGunluk }) => {
  const [secili, setSecili] = useState<string[]>(durum.turler.map((t) => t.kod));
  const [yeniden, setYeniden] = useState(false);
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);
  const { istek } = durum;
  const ilerleme = istek?.ilerleme;

  const gonder = async (yol: string, govde?: unknown) => {
    setHata('');
    setBekliyor(true);
    try {
      onDegisti(await api.post<ArsivDurumu>(yol, govde));
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'İşlem yapılamadı.');
    } finally {
      setBekliyor(false);
    }
  };
  const turAdi = (kod: string) => durum.turler.find((t) => t.kod === kod)?.ad ?? kod;

  return (
    <div className="bg-white rounded-xl border border-paper-300 p-4 sm:p-5 space-y-3 text-xs">
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-stone-800 text-sm">Arşivi oluştur / güncelle</p>
          <p className="text-stone-500">
            Seçilen türlerin listesi siteden alınır; metinler yapısıyla, ekler, sayfa resimleri ve sitenin PDF'i indirilir, taranmış
            sayfalar OCR ile okunur. Hepsi için 1-3 gün sürebilir (siteyi yormamak için istekler arası en az 1,5 sn). Yarıda kalırsa
            kendiliğinden ya da yeniden başlatınca kaldığı yerden devam eder.
          </p>
        </div>
        {baslatabilir && (suruyor ? (
          <button type="button" onClick={() => gonder('/arsiv/durdur')} disabled={bekliyor || istek?.durum === 'DURDURULUYOR'}
            className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg border border-rose-300 text-rose-800 font-semibold hover:bg-rose-50 disabled:opacity-50 cursor-pointer shrink-0">
            <Square className="w-4 h-4" /> {istek?.durum === 'DURDURULUYOR' ? 'Duruyor…' : 'Durdur'}
          </button>
        ) : (
          <button type="button" onClick={() => gonder('/arsiv/baslat', { turler: secili, yeniden })}
            disabled={bekliyor || !durum.zamanlayici_calisiyor || secili.length === 0}
            className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark disabled:opacity-50 cursor-pointer shrink-0">
            <Play className="w-4 h-4" /> Başlat
          </button>
        ))}
      </div>

      {/* Seçenekler, iş sürmüyorken. */}
      {baslatabilir && !suruyor && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {durum.turler.map((t) => (
              <label key={t.kod} className="flex items-center gap-1.5 text-stone-700 cursor-pointer">
                <input type="checkbox" checked={secili.includes(t.kod)}
                  onChange={(e) => setSecili((s) => (e.target.checked ? [...s, t.kod] : s.filter((x) => x !== t.kod)))} />
                {t.ad}
              </label>
            ))}
          </div>
          <label className="flex items-start gap-1.5 text-stone-700 cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={yeniden} onChange={(e) => setYeniden(e.target.checked)} />
            <span>Arşivdekileri de yeniden çek <span className="text-stone-500">(değişen metnin yeni sürümü olur; seçilen türlerin hepsi baştan çekildiği için uzun sürer)</span></span>
          </label>
        </div>
      )}
      {!durum.zamanlayici_calisiyor && <p className="text-rose-700">Zamanlayıcı çalışmadığı için arşiv işi başlatılamaz.</p>}
      {hata && <p className="text-rose-700">{hata}</p>}

      {/* Son işin durumu ve ilerlemesi. */}
      {istek && (
        <div className={`p-3 rounded-lg border space-y-1.5 ${
          istek.durum === 'HATALI' ? 'bg-rose-50 border-rose-300 text-rose-900'
            : istek.durum === 'BITTI' ? 'bg-emerald-50 border-emerald-300 text-emerald-900'
              : istek.durum === 'DURDURULDU' ? 'bg-stone-50 border-stone-300 text-stone-800'
                : 'bg-sky-50 border-sky-300 text-sky-900'}`}>
          <p className="font-semibold flex items-center gap-1.5">
            {suruyor && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {{ BEKLIYOR: 'Sırada: zamanlayıcı en geç 30 saniye içinde başlatacak.', CALISIYOR: 'Arşiv işi sürüyor.',
              DURDURULUYOR: 'Durduruluyor (sıradaki mevzuattan sonra).', BITTI: 'Bitti.', DURDURULDU: 'Durduruldu.',
              HATALI: 'Tamamlanamadı.' }[istek.durum]}
          </p>
          <p>
            #{istek.id} · {istek.isteyen ?? '—'} · istendi {tarihSaat(istek.istendi)}
            {istek.basladi && <> · başladı {tarihSaat(istek.basladi)}</>}{istek.bitti && <> · bitti {tarihSaat(istek.bitti)}</>}
            {' · '}{(istek.secenekler.turler ?? []).map(turAdi).join(', ') || 'bütün türler'}{istek.secenekler.yeniden ? ' (yeniden)' : ''}
          </p>
          {ilerleme && (
            <>
              <p>
                Liste: {Object.entries(ilerleme.liste).map(([k, v]) => `${turAdi(k)} ${v.alinan}/${v.toplam ?? '?'}${v.eksik ? ' (eksik)' : ''}`).join(' · ') || '—'}
              </p>
              {ilerleme.asama !== 'liste' && (
                <>
                  <IlerlemeCubugu biten={ilerleme.metin.biten} toplam={ilerleme.metin.toplam} />
                  <p>
                    Metin: {ilerleme.metin.biten} / {ilerleme.metin.toplam} · yeni sürüm {ilerleme.metin.yeni_surum}
                    {!!ilerleme.metin.okuma_duzeltmesi && <> · okuma düzeltmesi {ilerleme.metin.okuma_duzeltmesi}</>}
                    {!!ilerleme.metin.ayni && <> · değişmedi {ilerleme.metin.ayni}</>}
                    {' · '}kusurlu {ilerleme.metin.kusurlu ?? 0} · OCR yapılamadı {ilerleme.metin.ocr_gerekli} · sitede yok {ilerleme.metin.sitede_yok ?? 0}
                    {' · '}hata {ilerleme.metin.hata}
                    {!!ilerleme.istek_araligi && ilerleme.istek_araligi > 1.6 && <> · <span className="text-amber-700">site yavaş, istek aralığı {ilerleme.istek_araligi} sn</span></>}
                  </p>
                </>
              )}
              {suruyor && ilerleme.son && <p className="text-stone-600 truncate">Son: {ilerleme.son.ad}</p>}
            </>
          )}
          {istek.hata && <p className="wrap-break-word">{istek.hata}</p>}
          {ilerleme?.gunluk && (
            <button type="button" onClick={() => onGunluk(istek.id)} className="inline-flex items-center gap-1 text-petrol underline cursor-pointer">
              <ScrollText className="w-3.5 h-3.5" /> İş günlüğü
            </button>
          )}
        </div>
      )}
    </div>
  );
};

// Satır içi stil kullanılmaz (panelin CSP'si style-src 'self'), tarayıcının kendi ilerleme çubuğu.
const IlerlemeCubugu: React.FC<{ biten: number; toplam: number }> = ({ biten, toplam }) => {
  const oran = toplam ? Math.min(100, Math.round((biten / toplam) * 100)) : 0;
  return <progress className="w-full h-2 accent-petrol" value={biten} max={toplam || 1} aria-label={`%${oran}`} />;
};

// Tür başına sayılar.
const SayilarKarti: React.FC<{ durum: ArsivDurumu }> = ({ durum }) => (
  <div className="bg-white rounded-xl border border-paper-300 overflow-hidden text-xs">
    <div className="px-4 sm:px-5 py-3 border-b border-paper-200 flex flex-col sm:flex-row sm:items-center justify-between gap-1">
      <p className="font-semibold text-stone-800 flex items-center gap-1.5"><Archive className="w-4 h-4 text-stone-500" /> Arşivde</p>
      <p className="text-stone-500">
        {durum.toplam.toLocaleString('tr-TR')} mevzuat · {durum.surum_toplam.toLocaleString('tr-TR')} sürüm
        {durum.baslangic && <> · arşiv başlangıcı {tarihSaat(durum.baslangic)}</>}
      </p>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px]">
        <thead className="bg-paper-100 text-stone-500 text-left">
          <tr>
            <th className="px-4 py-2 font-medium">Tür</th>
            <th className="px-3 py-2 font-medium text-right">Toplam</th>
            {DURUM_SIRASI.map((d) => (
              <th key={d} className="px-3 py-2 font-medium text-right">{METIN_DURUMU[d].ad}</th>
            ))}
            <th className="px-3 py-2 font-medium text-right">Kalkan</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-paper-200">
          {durum.turler.map((t) => (
            <tr key={t.kod}>
              <td className="px-4 py-1.5 text-stone-800">{t.ad}</td>
              <td className="px-3 py-1.5 text-right font-semibold">{t.toplam.toLocaleString('tr-TR')}</td>
              {DURUM_SIRASI.map((d) => (
                <td key={d} className={`px-3 py-1.5 text-right ${t.sayilar[d] ? METIN_DURUMU[d].renk : 'text-stone-300'}`}>
                  {(t.sayilar[d] ?? 0).toLocaleString('tr-TR')}
                </td>
              ))}
              <td className="px-3 py-1.5 text-right text-stone-600">{t.kalkan}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </div>
);

// Arama ve süzmeli liste.
const Liste: React.FC<{ turler: ArsivDurumu['turler']; onSec: (anahtar: string) => void }> = ({ turler, onSec }) => {
  const [ara, setAra] = useState('');
  const [aranan, setAranan] = useState('');
  const [tur, setTur] = useState('');
  const [durum, setDurum] = useState('');
  const [sayfa, setSayfa] = useState(1);
  const [liste, setListe] = useState<ArsivListesi | null>(null);
  const [hata, setHata] = useState('');

  useEffect(() => {
    const q = new URLSearchParams({ ara: aranan, tur, durum, sayfa: String(sayfa) });
    api.get<ArsivListesi>(`/arsiv/liste?${q}`).then((l) => { setListe(l); setHata(''); }).catch((e) => setHata(e.message));
  }, [aranan, tur, durum, sayfa]);
  const sayfaSayisi = liste ? Math.max(1, Math.ceil(liste.toplam / liste.boyut)) : 1;

  return (
    <div className="bg-white rounded-xl border border-paper-300 overflow-hidden text-xs">
      <form className="px-4 sm:px-5 py-3 border-b border-paper-200 flex flex-col sm:flex-row gap-2"
        onSubmit={(e) => { e.preventDefault(); setSayfa(1); setAranan(ara); }}>
        <div className="flex-1 flex items-center gap-1.5 border border-paper-300 rounded-lg px-2.5">
          <Search className="w-3.5 h-3.5 text-stone-400" />
          <input value={ara} onChange={(e) => setAra(e.target.value)} placeholder="Ad ya da anahtar (1.5.4760) ara"
            className="flex-1 py-2 outline-none bg-transparent" />
        </div>
        <select value={tur} onChange={(e) => { setTur(e.target.value); setSayfa(1); }} className="border border-paper-300 rounded-lg px-2 py-2 bg-white">
          <option value="">Bütün türler</option>
          {turler.map((t) => <option key={t.kod} value={t.kod}>{t.ad}</option>)}
        </select>
        <select value={durum} onChange={(e) => { setDurum(e.target.value); setSayfa(1); }} className="border border-paper-300 rounded-lg px-2 py-2 bg-white">
          <option value="">Bütün durumlar</option>
          {DURUM_SIRASI.map((d) => <option key={d} value={d}>{METIN_DURUMU[d].ad}</option>)}
          <option value="KUSURLU">Okuması kusurlu</option>
          <option value="KALKTI">Siteden kalkan</option>
        </select>
        <button type="submit" className="px-3 py-2 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark cursor-pointer">Ara</button>
      </form>
      {hata && <p className="px-5 py-3 text-rose-700">{hata}</p>}
      {liste && (
        <>
          <div className="divide-y divide-paper-200">
            {liste.satirlar.length === 0 && <p className="px-5 py-4 text-stone-500">Sonuç yok.</p>}
            {liste.satirlar.map((m) => (
              <button key={m.anahtar} type="button" onClick={() => onSec(m.anahtar)}
                className="w-full text-left px-4 sm:px-5 py-2.5 hover:bg-paper-100 cursor-pointer flex flex-col sm:flex-row sm:items-start justify-between gap-1">
                <span className="text-stone-800 min-w-0">
                  <span className="font-medium">{m.ad}</span>
                  <span className="block text-stone-500">
                    {m.tur_adi} · RG {tarihSaat(m.rg_tarihi)}{m.rg_sayisi ? ` / ${m.rg_sayisi}` : ''} · {m.anahtar}
                    {m.mulga && ' · mülga'}{m.kalkti && ` · siteden kalktı (${tarihSaat(m.kalkti)})`}
                  </span>
                </span>
                <span className={`shrink-0 ${(METIN_DURUMU[m.durum] ?? METIN_DURUMU.HATA).renk}`}>
                  {(METIN_DURUMU[m.durum] ?? { ad: m.durum }).ad}{m.surum_sayisi > 0 && ` · v${m.surum_sayisi}`}
                  {m.kalite === 'kusurlu' && <span className="ml-1 px-1 rounded bg-amber-100 text-amber-800">kusurlu</span>}
                </span>
              </button>
            ))}
          </div>
          <div className="px-4 sm:px-5 py-2.5 border-t border-paper-200 flex items-center justify-between text-stone-600">
            <span>{liste.toplam.toLocaleString('tr-TR')} sonuç</span>
            <span className="flex items-center gap-2">
              <button type="button" disabled={sayfa <= 1} onClick={() => setSayfa((s) => s - 1)} className="px-2 py-1 rounded border border-paper-300 disabled:opacity-40 cursor-pointer">Önceki</button>
              {sayfa} / {sayfaSayisi}
              <button type="button" disabled={sayfa >= sayfaSayisi} onClick={() => setSayfa((s) => s + 1)} className="px-2 py-1 rounded border border-paper-300 disabled:opacity-40 cursor-pointer">Sonraki</button>
            </span>
          </div>
        </>
      )}
    </div>
  );
};

// Bir arşiv işinin günlüğü: özet ve mevzuat başına kayıtlar (karar 22).
const GunlukPenceresi: React.FC<{ istekId: number; onSec: (a: string) => void; onKapat: () => void }> = ({ istekId, onSec, onKapat }) => {
  const [sadece, setSadece] = useState('uyari');
  const [veri, setVeri] = useState<ArsivGunlugu | null>(null);
  const [hata, setHata] = useState('');
  useEffect(() => {
    setVeri(null);
    api.get<ArsivGunlugu>(`/arsiv/gunluk/${istekId}?sadece=${sadece}&adet=300`).then(setVeri).catch((e) => setHata(e.message));
  }, [istekId, sadece]);
  const o = veri?.ozet;
  return (
    <div className="fixed inset-0 z-50 bg-stone-950/50 flex items-stretch sm:items-center justify-center p-0 sm:p-6" onClick={onKapat}>
      <div role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}
        className="bg-white w-full max-w-5xl sm:rounded-xl border border-paper-300 flex flex-col max-h-full text-xs">
        <div className="px-4 sm:px-5 py-3 border-b border-paper-200 flex items-center justify-between gap-3">
          <p className="font-semibold text-stone-900 text-sm">İş #{istekId} günlüğü</p>
          <div className="flex items-center gap-2">
            <select value={sadece} onChange={(e) => setSadece(e.target.value)} className="border border-paper-300 rounded-lg px-2 py-1 bg-white">
              <option value="uyari">Uyarılı ve hatalı olanlar</option>
              <option value="hata">Sadece hatalar</option>
              <option value="">Hepsi (son 300)</option>
            </select>
            <button type="button" onClick={onKapat} aria-label="Kapat" className="p-1 rounded hover:bg-paper-200 cursor-pointer"><X className="w-4 h-4" /></button>
          </div>
        </div>
        {hata && <p className="px-5 py-3 text-rose-700">{hata}</p>}
        {!veri && !hata && <p className="px-5 py-3 text-stone-500 flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Yükleniyor…</p>}
        {o && (
          <div className="px-4 sm:px-5 py-3 border-b border-paper-200 space-y-1 text-stone-700">
            <p>Sonuç: {Object.entries(o.sonuc).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'} · {o.istek.toLocaleString('tr-TR')} istek</p>
            <p>Okuma: {Object.entries(o.okuma).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}</p>
            <p>Sayfa {o.sayfa} · OCR {o.ocr_sayfa} · görüntü {o.gorsel_sayfa} · tablo (yapı/görüntü) {o.tablo_yapi}/{o.tablo_goruntu} · inmeyen ek {o.ek_inmeyen}</p>
            {o.kusurlu.length > 0 && <p>Kusurlu: {o.kusurlu.slice(0, 30).join(', ')}{o.kusurlu.length > 30 ? ' …' : ''}</p>}
            {o.en_yavas.length > 0 && <p>En yavaşlar: {o.en_yavas.map((y) => `${y.anahtar} (${Math.round(y.sure)} sn)`).join(', ')}</p>}
          </div>
        )}
        {veri && (
          <div className="overflow-y-auto flex-1 divide-y divide-paper-200">
            {veri.kayitlar.length === 0 && <p className="px-5 py-4 text-stone-500">Kayıt yok.</p>}
            {[...veri.kayitlar].reverse().map((k, i) => (
              <div key={`${k.anahtar}-${i}`} className="px-4 sm:px-5 py-2">
                <p className="flex flex-wrap items-center gap-2">
                  <button type="button" onClick={() => onSec(k.anahtar)} className="font-semibold text-petrol underline cursor-pointer">{k.anahtar}</button>
                  <span className={k.sonuc === 'hata' ? 'text-rose-700 font-semibold' : 'text-stone-600'}>{k.sonuc}</span>
                  {k.okuma && <span className="text-stone-500">{k.okuma}</span>}
                  {k.kalite === 'kusurlu' && <span className="px-1 rounded bg-amber-100 text-amber-800">kusurlu</span>}
                  <span className="text-stone-400">{tarihSaat(k.zaman)}{k.sure != null && ` · ${k.sure} sn`}</span>
                </p>
                {k.hata && <p className="text-rose-700 break-all">{k.hata}</p>}
                {k.neden && <p className="text-stone-600">{k.neden}</p>}
                {(k.kusurlar ?? []).length > 0 && <p className="text-amber-800">{k.kusurlar?.join('; ')}</p>}
                {(k.uyarilar ?? []).map((u, j) => <p key={j} className="text-stone-500 break-all">{u}</p>)}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// "Tarama" sayfası, Şimdi tara düğmesi, zamanlayıcının durumu, tarama saatleri ve son taramalar.
import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Clock, Loader2, Play, Plus, X } from 'lucide-react';
import { api } from '../api';
import { CalismaOzeti, TaramaDurumu, ZamanlamaDurumu } from '../types/api';
import { MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';

// Tarama saatleri veritabanında, zamanlayıcı servisi 30 sn'de bir okur. Saat değişikliği bir sonraki taramadan geçerlidir.
export const TaramaPage: React.FC<{ ayarYonetebilir: boolean }> = ({ ayarYonetebilir }) => {
  // Sunucudan gelen tarama durumu ve hata mesajı.
  const [tarama, setTarama] = useState<TaramaDurumu | null>(null);
  const [hata, setHata] = useState('');
  // Zamanlama bilgisi ve "Şimdi tara" isteği sürüyor mu.
  const durum = tarama?.zamanlama ?? null;
  const istekSuruyor = tarama?.istek?.durum === 'BEKLIYOR' || tarama?.istek?.durum === 'CALISIYOR';

  // Durumu sunucudan çeker.
  const yukle = useCallback(() => {
    api.get<TaramaDurumu>('/tarama/durum').then((d) => { setTarama(d); setHata(''); }).catch((e) => setHata(e.message));
  }, []);
  // Tarama isteği sürerken 5 sn'de bir, yoksa 30 sn'de bir sorgulanır.
  useEffect(() => {
    yukle();
    const t = window.setInterval(yukle, istekSuruyor ? 5_000 : 30_000);
    return () => window.clearInterval(t);
  }, [yukle, istekSuruyor]);

  // "Şimdi tara"ya basılınca sunucuya istek gönderir.
  const simdiTara = async () => {
    setHata('');
    try {
      setTarama(await api.post<TaramaDurumu>('/tarama'));
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'Tarama başlatılamadı.');
      yukle();
    }
  };

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="pb-4 border-b border-paper-300">
        <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">Tarama</h2>
        <p className="text-xs text-stone-500 mt-0.5">Kaynaklar her gün bu saatlerde (Türkiye saati) taranır; ilgili kayıtlar onaya gelir.</p>
      </div>
      {/* Hata, Şimdi tara kartı, zamanlayıcı kartı, saatler kartı, son taramalar. */}
      {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
      {tarama && durum && (
        <SimdiTaraKarti tarama={tarama} baslatabilir={ayarYonetebilir} suruyor={istekSuruyor} onTara={simdiTara} />
      )}
      {durum && <ZamanlayiciKarti durum={durum} />}
      {durum && (
        <SaatlerKarti key={durum.surum} durum={durum} duzenlenebilir={ayarYonetebilir}
          onKaydedildi={(z) => setTarama((t) => (t ? { ...t, zamanlama: z } : t))} />
      )}
      {tarama && tarama.son_calismalar.length > 0 && <SonCalismalar calismalar={tarama.son_calismalar} />}
    </div>
  );
};

// Bir taramanın özet yazısı, "5 yeni kayıt (resmi_gazete 3, masak 2)".
function yeniOzeti(c: CalismaOzeti): string {
  const parcalar = Object.entries(c.yeni).filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n}`);
  return parcalar.length ? `${c.yeni_toplam} yeni kayıt (${parcalar.join(', ')})` : 'yeni kayıt yok';
}

// Panel taramayı kendisi yapmaz, istek yazar, zamanlayıcı 30 sn içinde alır (iki tarama üst üste binmesin).
// "Şimdi tara" kartı.
const SimdiTaraKarti: React.FC<{ tarama: TaramaDurumu; baslatabilir: boolean; suruyor: boolean; onTara: () => void }> = ({
  tarama, baslatabilir, suruyor, onTara,
}) => {
  const { istek, zamanlama } = tarama;
  // Düğme şu durumlarda kapalı, zamanlayıcı çalışmıyor, istek sürüyor ya da planlı tarama sürüyor.
  const kapali = !zamanlama.zamanlayici_calisiyor || suruyor || zamanlama.durum === 'tarama';
  return (
    <div className="bg-white rounded-xl border border-paper-300 p-4 sm:p-5 space-y-3 text-xs">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <p className="font-semibold text-stone-800 text-sm">Şimdi tara</p>
          <p className="text-stone-500">Bütün kaynakları planlı saati beklemeden tarar; ilgili kayıt varsa onaya sunar. Birkaç dakika sürebilir.</p>
        </div>
        {/* Düğme (sadece yetkisi olana). */}
        {baslatabilir && (
          <button type="button" onClick={onTara} disabled={kapali}
            className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-petrol text-white font-semibold hover:bg-petrol-dark disabled:opacity-50 cursor-pointer shrink-0">
            {suruyor ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {suruyor ? 'Tarama sürüyor…' : 'Şimdi tara'}
          </button>
        )}
      </div>
      {/* Neden başlatılamadığına dair açıklamalar. */}
      {!zamanlama.zamanlayici_calisiyor && <p className="text-rose-700">Zamanlayıcı çalışmadığı için tarama başlatılamaz.</p>}
      {zamanlama.zamanlayici_calisiyor && zamanlama.durum === 'tarama' && !suruyor && (
        <p className="text-stone-600">Şu an planlı tarama sürüyor; bitince sonuç Onay Kuyruğu'nda görünür.</p>
      )}
      {/* Son isteğin durumu, sırada / sürüyor / bitti (sonuçla) / hatalı. */}
      {istek && (
        <div className={`p-3 rounded-lg border ${
          istek.durum === 'HATALI' ? 'bg-rose-50 border-rose-300 text-rose-900'
            : istek.durum === 'BITTI' ? 'bg-emerald-50 border-emerald-300 text-emerald-900'
              : 'bg-sky-50 border-sky-300 text-sky-900'}`}>
          {istek.durum === 'BEKLIYOR' && <>Sırada: zamanlayıcı en geç 30 saniye içinde başlatacak ({istek.isteyen}, {tarihSaat(istek.istendi)}).</>}
          {istek.durum === 'CALISIYOR' && <>Tarama sürüyor (başladı: {tarihSaat(istek.basladi)}). Bu sayfa kendiliğinden güncellenir.</>}
          {istek.durum === 'BITTI' && istek.calisma && (
            <>Bitti ({tarihSaat(istek.bitti)}): {yeniOzeti(istek.calisma)}
              {istek.rapor_id ? <>; <strong>rapor #{istek.rapor_id} onaya sunuldu</strong> (Onay Kuyruğu).</>
                : istek.calisma.yeni_toplam > 0 ? '; hiçbiri takip edilen konulara takılmadı, onaya sunulacak rapor yok.'
                  : '; onaya sunulacak rapor yok.'}</>
          )}
          {istek.durum === 'HATALI' && <>Tarama tamamlanamadı ({tarihSaat(istek.bitti)}): {istek.hata}</>}
        </div>
      )}
    </div>
  );
};

// Son 5 taramanın listesi.
const SonCalismalar: React.FC<{ calismalar: CalismaOzeti[] }> = ({ calismalar }) => (
  <div className="bg-white rounded-xl border border-paper-300 overflow-hidden text-xs">
    <p className="px-4 sm:px-5 py-3 font-semibold text-stone-800 border-b border-paper-200">Son taramalar</p>
    <div className="divide-y divide-paper-200">
      {calismalar.map((c) => (
        <div key={c.id} className="px-4 sm:px-5 py-2.5 flex flex-col sm:flex-row sm:items-start justify-between gap-1">
          <span className="text-stone-700">#{c.id} · {tarihSaat(c.baslangic)} · {yeniOzeti(c)}</span>
          <span className={c.durum === 'BASARILI' ? 'text-emerald-700' : c.durum === 'HATALI' ? 'text-rose-700 wrap-break-word' : 'text-sky-700'}>
            {c.durum === 'BASARILI' ? 'Başarılı' : c.durum === 'HATALI' ? `Hata: ${c.hata ?? ''}` : 'Sürüyor'}
          </span>
        </div>
      ))}
    </div>
  </div>
);

// Zamanlayıcı kartı, çalışıyor mu, sonraki tarama ne zaman.
const ZamanlayiciKarti: React.FC<{ durum: ZamanlamaDurumu }> = ({ durum }) => (
  <div className="bg-white rounded-xl border border-paper-300 p-4 sm:p-5 space-y-3 text-xs">
    {durum.zamanlayici_calisiyor ? (
      <div className="flex items-center gap-2 text-emerald-800">
        <CheckCircle2 className="w-4 h-4 text-emerald-600" />
        <strong>Zamanlayıcı çalışıyor</strong>
        <span className="text-stone-500">— {durum.durum === 'tarama' ? 'şu an tarama sürüyor' : 'sıradaki taramayı bekliyor'}</span>
      </div>
    ) : (
      <ZamanlayiciUyarisi durum={durum} />
    )}
    <div className="flex items-center gap-2 text-stone-700">
      <Clock className="w-4 h-4 text-stone-500" />
      Sonraki planlı tarama: <strong>{tarihSaat(durum.sonraki)}</strong>
      <span className="text-stone-500">(devlet sitelerine aynı anda yüklenmemek için saatten sonraki 10 dk içinde başlar)</span>
    </div>
  </div>
);

// Zamanlayıcı çalışmıyor uyarısı (bu sayfada ve App'te her sayfanın üstünde kullanılıyor).
export const ZamanlayiciUyarisi: React.FC<{ durum: ZamanlamaDurumu }> = ({ durum }) => (
  <div role="alert" className="flex items-start gap-2 p-3 rounded-lg bg-rose-50 border border-rose-300 text-xs text-rose-900">
    <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
    <span>
      <strong>Zamanlayıcı çalışmıyor</strong> — planlı taramalar yapılmıyor
      {durum.son_nabiz ? ` (son haber: ${tarihSaat(durum.son_nabiz)})` : ''}. Sunucu yöneticisine bildirin
      (<span className="font-mono">mevzuat-zamanlayici</span> servisi / Docker'da <span className="font-mono">zamanlayici</span> container'ı).
    </span>
  </div>
);

// Tarama saatleri kartı, saatleri görme ve (yetkisi olan için) değiştirme.
const SaatlerKarti: React.FC<{
  durum: ZamanlamaDurumu;
  duzenlenebilir: boolean;
  onKaydedildi: (d: ZamanlamaDurumu) => void;
}> = ({ durum, duzenlenebilir, onKaydedildi }) => {
  // Ekrandaki saatler, mesajlar, kaydediliyor mu, sunucudakinden farklı mı.
  const [saatler, setSaatler] = useState<string[]>(durum.saatler);
  const [hata, setHata] = useState('');
  const [basari, setBasari] = useState('');
  const [bekliyor, setBekliyor] = useState(false);
  const degisti = JSON.stringify([...saatler].sort()) !== JSON.stringify(durum.saatler);

  // Kaydet, saatleri sunucuya gönder, sürüm numarasıyla birlikte (başkası değiştirdiyse sunucu reddeder).
  const kaydet = async () => {
    setHata('');
    setBasari('');
    setBekliyor(true);
    try {
      const yeni = await api.put<ZamanlamaDurumu>('/zamanlama', { saatler, surum: durum.surum });
      onKaydedildi(yeni);
      setBasari('Tarama saatleri kaydedildi; zamanlayıcı en geç 30 saniye içinde yeni saatlere geçer.');
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'Kaydedilemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  return (
    <div className="bg-white rounded-xl border border-paper-300 p-4 sm:p-5 space-y-3 text-xs">
      <p className="font-semibold text-stone-800">Tarama saatleri <span className="font-normal text-stone-500">— en az 1, en fazla 6; aralarında en az 1 saat</span></p>
      <div className="flex flex-wrap gap-2">
        {/* Her saat için bir saat kutusu ve (birden fazlaysa) çıkar düğmesi. */}
        {saatler.map((s, i) => (
          <span key={i} className="inline-flex items-center gap-1 pl-2 pr-1 py-1 rounded-lg bg-paper-100 border border-paper-300">
            <input type="time" value={s} disabled={!duzenlenebilir} aria-label={`Tarama saati ${i + 1}`}
              onChange={(e) => setSaatler(saatler.map((x, j) => (j === i ? e.target.value : x)))}
              className="bg-transparent font-mono text-stone-900 outline-hidden" />
            {duzenlenebilir && saatler.length > 1 && (
              <button type="button" onClick={() => setSaatler(saatler.filter((_, j) => j !== i))} aria-label="Saati çıkar"
                className="p-0.5 rounded-sm text-stone-400 hover:text-rose-600 cursor-pointer"><X className="w-3.5 h-3.5" /></button>
            )}
          </span>
        ))}
        {/* En fazla 6 saat, Saat ekle düğmesi. */}
        {duzenlenebilir && saatler.length < 6 && (
          <button type="button" onClick={() => setSaatler([...saatler, '12:00'])}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-dashed border-paper-300 text-stone-600 hover:bg-paper-100 cursor-pointer">
            <Plus className="w-3.5 h-3.5" /> Saat ekle
          </button>
        )}
      </div>
      {/* Mesajlar ve Kaydet / Vazgeç düğmeleri. */}
      {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
      {basari && <MesajKutusu tur="basari">{basari}</MesajKutusu>}
      {duzenlenebilir && (
        <div className="flex gap-2">
          <button type="button" onClick={kaydet} disabled={bekliyor || !degisti}
            className="px-4 py-2 bg-petrol text-white rounded-lg font-semibold hover:bg-petrol-dark disabled:opacity-50 cursor-pointer">
            {bekliyor ? 'Kaydediliyor…' : 'Saatleri kaydet'}
          </button>
          {degisti && (
            <button type="button" onClick={() => setSaatler(durum.saatler)} className="px-3 py-2 text-stone-600 hover:bg-paper-200 rounded-lg cursor-pointer">
              Vazgeç
            </button>
          )}
        </div>
      )}
    </div>
  );
};

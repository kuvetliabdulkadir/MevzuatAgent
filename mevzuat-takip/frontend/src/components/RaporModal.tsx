// Rapor penceresi, onaylayıcının raporu incelediği, kalem çıkardığı, alıcıları seçtiği ve onay/ret verdiği ekran.
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Building2, CheckCircle, Plus, Send, UserCheck, Users, X, XCircle } from 'lucide-react';
import { api } from '../api';
import { Gonderim, Kalem, Kullanici, Mesaj, RaporDetayi, RaporGrubu } from '../types/api';
import { dagitimPlani, epostaGecerliMi, grubunKalemleri } from '../dagitim';
import { DurumRozeti, MesajKutusu } from '../bicim';
import { tarihSaat } from '../tarih';

// Pencereye verilen bilgiler.
interface RaporModalProps {
  raporId: number;
  kullanici: Kullanici;
  onKapat: () => void;
  onKararVerildi: (mesaj: Mesaj) => void;
}

export const RaporModal: React.FC<RaporModalProps> = ({ raporId, kullanici, onKapat, onKararVerildi }) => {
  // Rapor ayrıntısı, gönderilecek (işaretli) kalemler, not, hata, istek sürüyor mu, seçili gruplar, kişiye özel adresler.
  const [detay, setDetay] = useState<RaporDetayi | null>(null);
  const [dahil, setDahil] = useState<Set<number>>(new Set());
  const [notu, setNotu] = useState('');
  const [hata, setHata] = useState('');
  const [bekliyor, setBekliyor] = useState(false);
  const [seciliGruplar, setSeciliGruplar] = useState<Set<number>>(new Set());
  const [ekAdresler, setEkAdresler] = useState<string[]>([]);
  // Onaylanmış rapordan başka kişilere başka kalemleri gönderme modu.
  const [ekMod, setEkMod] = useState(false);

  // Pencere açılınca raporu sunucudan çek, bütün kalemleri ve kalem alacak grupları işaretli başlat.
  useEffect(() => {
    api
      .get<RaporDetayi>(`/raporlar/${raporId}`)
      .then((d) => {
        setDetay(d);
        setDahil(new Set(d.kalemler.map((k) => k.id))); // varsayılan, hepsi gönderilir
        // Öneri, bu rapordan en az bir kalem alacak gruplar işaretli gelir.
        setSeciliGruplar(new Set(d.gruplar.filter((g) => grubunKalemleri(g, d.kalemler).length > 0).map((g) => g.id)));
      })
      .catch((e) => setHata(e.message));
  }, [raporId]);

  // Esc ile kapanır.
  useEffect(() => {
    const tus = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onKapat();
    };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onKapat]);

  // Rapor onay bekliyor mu, bu kullanıcı karar verebilir mi.
  const bekleyen = detay?.rapor.durum === 'ONAY_BEKLIYOR';
  const kararVerebilir = bekleyen && kullanici.karar_verebilir;
  // Onaylanmış raporda onaylayıcı ek gönderim yapabilir, kalem ve alıcı seçimi onaydaki gibi.
  const ekGonderebilir = kullanici.karar_verebilir && (detay?.rapor.durum === 'ONAYLANDI' || detay?.rapor.durum === 'GONDERILDI');
  const secimModu = kararVerebilir || ekMod;

  // Ek gönderim modunu açar, henüz gönderilmemiş kalemler işaretli, grup ve adres seçimi boş başlar.
  const ekModuAc = () => {
    if (!detay) return;
    setDahil(new Set(detay.kalemler.filter((k) => k.haric).map((k) => k.id)));
    setSeciliGruplar(new Set());
    setEkAdresler([]);
    setNotu('');
    setHata('');
    setEkMod(true);
  };

  // Bir kümede varsa çıkarır, yoksa ekler (kalem ve grup işaretleri için).
  const tersle = (kume: Set<number>, id: number) => {
    const yeni = new Set(kume);
    if (yeni.has(id)) yeni.delete(id);
    else yeni.add(id);
    return yeni;
  };

  // Onayla/Reddet, kararı ve seçimleri sunucuya gönder.
  const karar = async (tur: 'onayla' | 'reddet') => {
    setHata('');
    setBekliyor(true);
    try {
      const cevap = await api.post<Mesaj>(`/raporlar/${raporId}/karar`, {
        karar: tur, dahil: [...dahil], notu, gruplar: [...seciliGruplar], ek_adresler: ekAdresler,
      });
      onKararVerildi(cevap);
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'İstek işlenemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  // Ek gönderim, seçilen kalemleri seçilen gruplara ve adreslere gönder.
  const ekGonder = async () => {
    setHata('');
    setBekliyor(true);
    try {
      const cevap = await api.post<Mesaj>(`/raporlar/${raporId}/ek-gonderim`, {
        dahil: [...dahil], notu, gruplar: [...seciliGruplar], ek_adresler: ekAdresler,
      });
      onKararVerildi(cevap);
    } catch (e) {
      setHata(e instanceof Error ? e.message : 'İstek işlenemedi.');
    } finally {
      setBekliyor(false);
    }
  };

  // Onaylanırsa oluşacak dağıtım, seçili kalemler × seçili gruplar + kişiye özel adresler.
  // Önizleme hesabı, işaretli kalemler, seçili gruplar ve bunlardan çıkan dağıtım planı (kaç kişi, kaç farklı mail).
  const dahilKalemler = detay?.kalemler.filter((k) => dahil.has(k.id)) ?? [];
  const seciliGrupListesi = detay?.gruplar.filter((g) => seciliGruplar.has(g.id)) ?? [];
  const plan = dagitimPlani(dahilKalemler, seciliGrupListesi, ekAdresler);
  const kisiSayisi = plan.reduce((toplam, mail) => toplam + mail.alicilar.length, 0);

  // Kalem kartında "Gidecek:" satırı için o kalemi alacak gruplar ve kişiye özel adres sayısı.
  const kalemAlicilari = (k: Kalem): string[] => [
    ...seciliGrupListesi
      .filter((g) => grubunKalemleri(g, [k]).length > 0)
      .map((g) => `${g.ad} (${g.adresler.length} kişi)`),
    ...(ekAdresler.length > 0 ? [`kişiye özel (${ekAdresler.length})`] : []),
  ];

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/60 p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true"
        className="bg-white rounded-xl shadow-2xl border border-paper-300 w-full max-w-5xl max-h-[94vh] flex flex-col overflow-hidden">
        {/* Üst kısım, rapor numarası, durum rozeti, konu ve kapat düğmesi. */}
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-paper-300 flex items-start justify-between bg-paper-50 shrink-0 gap-3">
          <div className="space-y-1 min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500 font-mono">
              <span>Rapor #{raporId}</span>
              {detay && <DurumRozeti durum={detay.rapor.durum} />}
            </div>
            <h2 className="text-sm sm:text-base font-semibold text-stone-900 leading-snug sm:leading-6 wrap-break-word">
              {detay?.rapor.konu ?? (hata ? 'Rapor açılamadı' : 'Yükleniyor…')}
            </h2>
          </div>
          <button onClick={onKapat} aria-label="Kapat"
            className="text-stone-400 hover:text-stone-700 p-1.5 rounded-lg hover:bg-paper-200 transition-colors cursor-pointer shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Pencerenin gövdesi (kayan kısım). */}
        <div className="p-3.5 sm:p-6 overflow-y-auto space-y-4 text-xs sm:text-sm">
          {/* Rapor yüklendiyse, karar bilgisi, uyarılar, kalemler, gönderim tablosu, alıcı seçimi, not kutusu. */}
          {detay && (
            <>
              {/* Karar verilmişse kim, ne zaman, hangi notla. */}
              {detay.rapor.karar_veren && (
                <div className="p-3 bg-stone-100 rounded-lg border border-stone-200 text-xs text-stone-700 space-y-1">
                  <div>Karar: <strong>{detay.rapor.karar_veren}</strong>, {tarihSaat(detay.rapor.karar_zamani)}
                    {detay.rapor.gonderildi && <> · Gönderildi: {tarihSaat(detay.rapor.gonderildi)}</>}</div>
                  {detay.rapor.karar_notu && <div><strong>Not:</strong> {detay.rapor.karar_notu}</div>}
                </div>
              )}
              {detay.rapor.hata && <MesajKutusu tur="hata">{detay.rapor.hata}</MesajKutusu>}
              {/* Admin bakıyorsa, görev ayrılığı uyarısı (karar veremez). */}
              {bekleyen && !kullanici.karar_verebilir && (
                <div className="p-3 bg-amber-50 border border-amber-300 rounded-lg text-xs text-amber-900 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
                  <span><strong>Görevler ayrılığı:</strong> Bu rapor onaylayıcının kararını bekliyor; yönetici hesabıyla karar verilemez.</span>
                </div>
              )}
              {/* Onaylayıcıya kısa açıklama. */}
              {kararVerebilir && (
                <p className="text-xs text-stone-600">
                  Gönderilmesini istemediğiniz kalemlerin işaretini kaldırın. Her kalem altında yazan alıcı gruplarına
                  mail olarak gider, her kalemin altında belgenin resmî kaynak linki olur. Her kişiye ayrı mail gider, alıcılar birbirinin adresini görmez (birden çok gruptaki kişi tek mail alır); PDF belgelerin orijinali ek olarak konur. İşaretini kaldırdığınız kalemleri sonra başka kişilere ayrıca gönderebilirsiniz.
                </p>
              )}
              {/* Ek gönderim modunda kısa açıklama. */}
              {ekMod && (
                <div className="p-3 bg-sky-50 border border-sky-200 rounded-lg text-xs text-sky-900">
                  <strong>Ek gönderim:</strong> Göndermek istediğiniz kalemleri işaretleyin, aşağıdan grup ya da kişi seçin.
                  Daha önce gönderilmemiş kalemler işaretli gelir. Mailler sadece bu seçime gider, önceki alıcılara tekrar gitmez.
                </div>
              )}
              {/* OCR ile okunmuş metin varsa uyarı. */}
              {detay.kalemler.some((k) => k.ocr) && (
                <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900">
                  „Otomatik okunmuş taslak“ işaretli metinler taranmış belgelerden okunmuştur; rakam, liste numarası ve isimlerde hata olabilir.
                </div>
              )}

              {/* Her kalem için bir kart. */}
              {detay.kalemler.map((k) => (
                <KalemKarti key={k.id} kalem={k} secilebilir={secimModu} secili={dahil.has(k.id)}
                  bekleyen={bekleyen} alicilar={kalemAlicilari(k)} onDegistir={() => setDahil(tersle(dahil, k.id))} />
              ))}

              {/* Onaylanmışsa dağıtım (gönderim) tablosu. */}
              {detay.gonderimler.length > 0 && <GonderimTablosu detay={detay} />}

              {/* Onaylayıcı için alıcı seçimi (onayda ve ek gönderimde). */}
              {secimModu && (
                <AliciSecimi
                  gruplar={detay.gruplar}
                  kalemler={dahilKalemler}
                  secili={seciliGruplar}
                  onGrup={(id) => setSeciliGruplar(tersle(seciliGruplar, id))}
                  ekAdresler={ekAdresler}
                  onEkle={(a) => setEkAdresler([...ekAdresler, a])}
                  onCikar={(a) => setEkAdresler(ekAdresler.filter((x) => x !== a))}
                  oneriler={detay.adres_onerileri}
                  kisiSayisi={kisiSayisi}
                  mailSayisi={plan.length}
                />
              )}

              {/* Not kutusu. */}
              {secimModu && (
                <div className="space-y-1.5">
                  <label className="text-xs font-semibold text-stone-700 block" htmlFor="karar-notu">
                    {ekMod
                      ? 'Not (isteğe bağlı; bu gönderimin mailinin başında görünür)'
                      : 'Not (isteğe bağlı; onaylarsanız raporun başında görünür, reddederken zorunludur)'}
                  </label>
                  <textarea id="karar-notu" rows={3} maxLength={2000} value={notu} onChange={(e) => setNotu(e.target.value)}
                    className="w-full text-xs p-3 rounded-lg border border-paper-300 bg-white focus:outline-hidden focus:ring-2 focus:ring-petrol/20" />
                </div>
              )}
            </>
          )}
          {hata && <MesajKutusu tur="hata">{hata}</MesajKutusu>}
        </div>

        {/* Alt çubuk, Kapat, Reddet ve Onayla düğmeleri (onay düğmesi kimseye gitmeyecekse kapalı). */}
        <div className="px-4 sm:px-6 py-3 sm:py-4 bg-paper-50 border-t border-paper-300 flex flex-col-reverse sm:flex-row sm:items-center justify-between gap-3 shrink-0">
          <button onClick={onKapat}
            className="w-full sm:w-auto px-4 py-2 text-xs font-medium text-stone-600 hover:text-stone-900 rounded-lg hover:bg-paper-200 transition-colors cursor-pointer">
            Kapat
          </button>
          {/* Onaylanmış raporda ek gönderim düğmesi. */}
          {ekGonderebilir && !ekMod && (
            <button onClick={ekModuAc}
              className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-petrol bg-white border border-petrol/40 hover:bg-petrol/5 cursor-pointer">
              <Send className="w-3.5 h-3.5 shrink-0" />
              <span>Başka kişilere gönder</span>
            </button>
          )}
          {ekMod && (
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-3 w-full sm:w-auto">
              <button onClick={() => setEkMod(false)} disabled={bekliyor}
                className="px-4 py-2 rounded-lg text-xs font-medium text-stone-600 bg-white border border-paper-300 hover:bg-paper-200 disabled:opacity-60 cursor-pointer">
                Vazgeç
              </button>
              <button onClick={ekGonder} disabled={bekliyor || plan.length === 0}
                className="flex items-center justify-center gap-1.5 px-5 py-2 rounded-lg text-xs font-semibold text-white bg-petrol hover:bg-petrol-dark shadow-xs disabled:opacity-60 cursor-pointer">
                <span>{bekliyor ? 'Gönderiliyor…' : `Seçilen ${dahil.size} kalemi ${kisiSayisi} kişiye gönder`}</span>
                <Send className="w-3.5 h-3.5 ml-1 text-gold-light shrink-0" />
              </button>
            </div>
          )}
          {kararVerebilir && (
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-3 w-full sm:w-auto">
              <button onClick={() => karar('reddet')} disabled={bekliyor}
                className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-rose-700 bg-rose-50 border border-rose-300 hover:bg-rose-100 disabled:opacity-60 cursor-pointer">
                <XCircle className="w-4 h-4 shrink-0" />
                <span>Raporu reddet</span>
              </button>
              <button onClick={() => karar('onayla')} disabled={bekliyor || plan.length === 0}
                className="flex items-center justify-center gap-1.5 px-5 py-2 rounded-lg text-xs font-semibold text-white bg-petrol hover:bg-petrol-dark shadow-xs disabled:opacity-60 cursor-pointer">
                <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
                <span>{bekliyor ? 'Gönderiliyor…' : `Seçilen ${dahil.size} kalemi onayla, ${kisiSayisi} kişiye gönder`}</span>
                <Send className="w-3.5 h-3.5 ml-1 text-gold-light shrink-0" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
};

// Tek bir kalemin kartı, işaret kutusu, başlık, iş kolları, eşleşen kelimeler, kime gideceği, özet, yürürlük, metin.
const KalemKarti: React.FC<{
  kalem: Kalem;
  secilebilir: boolean;
  secili: boolean;
  bekleyen: boolean;
  alicilar: string[];
  onDegistir: () => void;
}> = ({ kalem: k, secilebilir, secili, bekleyen, alicilar, onDegistir }) => (
  <div className={`rounded-xl border p-4 space-y-3 ${secilebilir && !secili ? 'border-stone-200 bg-stone-50 opacity-70' : 'border-paper-300 bg-white'}`}>
    <label className={`flex gap-3 items-start ${secilebilir ? 'cursor-pointer' : ''}`}>
      {/* Onaylayıcıya işaret kutusu (gönderilsin/gönderilmesin). */}
      {secilebilir && (
        <input type="checkbox" checked={secili} onChange={onDegistir} className="mt-1 w-4 h-4 accent-petrol shrink-0"
          aria-label={`Kalem ${k.no}: ${k.baslik}`} />
      )}
      <div className="space-y-1.5 min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500 font-mono">
          <span>{k.no}</span>
          <span className="text-stone-300">•</span>
          <span>{k.tur_adi}</span>
          <span className="text-stone-300">•</span>
          <span>{tarihSaat(k.yayin_tarihi)}</span>
          {/* Etiketler, metni değişen mevzuat, rapordan çıkarılmış kalem. */}
          {k.degisiklikler && (
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-300">
              GÜNCEL METİN DEĞİŞTİ
            </span>
          )}
          {!bekleyen && k.haric && (
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-rose-50 text-rose-800 border border-rose-300">
              GÖNDERİLMEDİ
            </span>
          )}
        </div>
        <h3 className="font-semibold text-stone-900 wrap-break-word">{k.baslik}</h3>
        {/* İş kolları ve eşleşen konu/kelimeler. */}
        <div className="flex flex-wrap gap-1.5">
          {k.is_kollari.map((ik) => (
            <span key={ik} className="bg-petrol/10 text-petrol border border-petrol/20 px-2 py-0.5 rounded-sm text-[11px] font-medium">{ik}</span>
          ))}
          {Object.entries(k.eslesmeler).map(([konu, kelimeler]) => (
            <span key={konu} className="bg-gold-light/15 text-gold-dark border border-gold-light/30 px-2 py-0.5 rounded-sm text-[11px]">
              {konu}: {kelimeler.join(', ')}
            </span>
          ))}
        </div>
        {/* Seçim yapılıyorsa ve işaretliyse, kime gidecek (ya da kimseye gitmiyor uyarısı). */}
        {secilebilir && secili && (
          alicilar.length > 0 ? (
            <div className="flex items-center gap-1.5 text-xs text-stone-600">
              <Users className="w-3.5 h-3.5 text-stone-400" />
              <span>Gidecek: {alicilar.join(', ')}</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 text-xs text-rose-700">
              <AlertTriangle className="w-3.5 h-3.5" /> Bu kalem şu anki seçimle kimseye gitmiyor.
            </div>
          )
        )}
      </div>
    </label>

    {/* Eşleşen konuların açıklaması, bu konu bizi neden ilgilendiriyor. */}
    {k.nedenler.length > 0 && (
      <div className="p-2.5 rounded-sm bg-gold-light/10 border border-gold-light/30 text-xs text-stone-800 space-y-1">
        <strong className="block text-gold-dark">Neden önemli</strong>
        {k.nedenler.map((n) => (
          <p key={n.konu} className="whitespace-pre-line">
            {k.nedenler.length > 1 && <strong>{n.konu}: </strong>}{n.aciklama}
          </p>
        ))}
      </div>
    )}

    {/* Metni değişen mevzuatta, madde madde eski (kırmızı) ve yeni (yeşil) hali. */}
    {k.degisiklikler && (
      <div className="space-y-2">
        <p className="text-[11px] font-semibold text-stone-500 uppercase font-mono">
          Güncel metindeki değişiklikler (değişen kısım «» içinde)
        </p>
        {k.degisiklikler.map((d, i) => (
          <div key={i} className="space-y-1">
            <div className="text-xs font-semibold text-stone-800">{d.bolum} — {d.tur}</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              <pre className="text-xs font-sans whitespace-pre-wrap [overflow-wrap:anywhere] bg-rose-50/40 p-2.5 rounded-sm border border-rose-200 text-stone-700">
                {d.eski || '(yok)'}
              </pre>
              <pre className="text-xs font-sans whitespace-pre-wrap [overflow-wrap:anywhere] bg-emerald-50/40 p-2.5 rounded-sm border border-emerald-200 text-stone-800">
                {d.yeni || '(kaldırıldı)'}
              </pre>
            </div>
          </div>
        ))}
      </div>
    )}

    {/* Özet, özet tablosu, yürürlük, öne çıkan cümleler. */}
    {k.ozet && (
      <div className="text-xs text-stone-800"><strong>Özet:</strong> {k.ozet}
        {k.ozet_tablosu.length > 0 && (
          <pre className="mt-1.5 text-[11px] whitespace-pre-wrap [overflow-wrap:anywhere] bg-paper-100 p-2.5 rounded-sm border border-paper-200">{k.ozet_tablosu.join('\n')}</pre>
        )}
      </div>
    )}
    {k.yururluk && <div className="text-xs text-stone-800"><strong>Yürürlük:</strong> {k.yururluk}</div>}
    {k.one_cikanlar.length > 0 && (
      <div className="text-xs text-stone-800 space-y-0.5">
        <strong className="block">Öne çıkan bölümler:</strong>
        {k.one_cikanlar.map((c, i) => <div key={i} className="pl-2.5">• {c}</div>)}
      </div>
    )}
    {/* Belgenin metni (tıklanınca açılır). */}
    {k.metin && (
      <details className="text-xs">
        <summary className="cursor-pointer text-stone-500">
          Metin{k.ocr && <strong className="text-amber-700"> — otomatik okunmuş taslak</strong>}{k.kisaltildi && ' (ilk bölüm)'}
        </summary>
        <pre className="mt-1.5 whitespace-pre-wrap [overflow-wrap:anywhere] font-sans bg-paper-100 p-3 rounded-sm border border-paper-200 max-h-72 overflow-y-auto">{k.metin}</pre>
      </details>
    )}
    {/* Metin okunamadıysa uyarı ve kaynakça. */}
    {k.okunamadi && (
      <div className="p-2 rounded-sm bg-amber-50 border border-amber-200 text-xs text-amber-900">
        Belgenin metni okunamadı; orijinal belgeye kaynakçadan ulaşın.
      </div>
    )}
    <div className="text-[11px] text-stone-500">
      <strong>Kaynakça:</strong> {k.kaynakca}
      {/* Belgenin resmî sayfası yeni sekmede açılır. */}
      {k.url && (
        <a href={k.url} target="_blank" rel="noopener noreferrer"
          className="ml-2 font-medium text-sky-800 underline hover:text-sky-950">Kaynağa git ↗</a>
      )}
    </div>
  </div>
);

// Satır başına bir mail (yeni raporlarda bir kişi), aynı kalemleri alanlar bir başlık altında toplanır.
// Onaylanmış raporun gönderim tablosu, aynı kalemleri alanlar bir başlık altında, her satır bir kişiye giden mail.
const GonderimTablosu: React.FC<{ detay: RaporDetayi }> = ({ detay }) => {
  // Gönderimleri kalem kümesine göre grupla.
  const paketler = new Map<string, Gonderim[]>();
  for (const g of detay.gonderimler) {
    const anahtar = `${g.kalemler.join(',')}|${g.notu ?? ''}`;
    paketler.set(anahtar, [...(paketler.get(anahtar) ?? []), g]);
  }
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold text-stone-700 uppercase tracking-wider font-mono">Dağıtım (kişi başı ayrı mail)</p>
      <div className="overflow-x-auto border border-paper-300 rounded-lg">
        <table className="w-full text-left text-xs min-w-[640px]">
          <thead className="bg-paper-100 text-stone-600 font-mono text-[11px] uppercase">
            <tr>
              <th className="px-3 py-2">#</th><th className="px-3 py-2">Alıcı</th><th className="px-3 py-2">Gruplar</th>
              <th className="px-3 py-2">Durum</th>
            </tr>
          </thead>
          {[...paketler.values()].map((liste) => (
            <tbody key={liste[0].id} className="divide-y divide-paper-200 border-t border-paper-300">
              <tr className="bg-paper-50">
                <td colSpan={4} className="px-3 py-1.5 text-stone-600">
                  Kalemler: <strong>{liste[0].kalemler.join(', ')}</strong> · {liste.length} mail
                  {liste[0].notu && <> · Not: {liste[0].notu}</>}
                </td>
              </tr>
              {liste.map((g) => (
                <tr key={g.id} className="align-top">
                  <td className="px-3 py-2 font-mono">{g.id}</td>
                  <td className="px-3 py-2 font-mono text-stone-600">{g.alicilar.join(', ')}</td>
                  <td className="px-3 py-2">{g.gruplar.join(', ')}</td>
                  <td className="px-3 py-2 space-y-1">
                    <DurumRozeti durum={g.durum} />
                    {g.gonderildi && <div className="text-stone-500">{tarihSaat(g.gonderildi)}</div>}
                    {g.hata && <div className="text-rose-700">{g.hata}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </div>
  );
};

// Onay ekranında alıcı seçimi, gruplar (iş koluna göre önerilmiş) + kişiye özel adresler + özet.
// Alıcı seçimi bölümü, gruplar, kişiye özel adresler ve özet.
const AliciSecimi: React.FC<{
  gruplar: RaporGrubu[];
  kalemler: Kalem[];
  secili: Set<number>;
  onGrup: (id: number) => void;
  ekAdresler: string[];
  onEkle: (adres: string) => void;
  onCikar: (adres: string) => void;
  oneriler: string[];
  kisiSayisi: number;
  mailSayisi: number;
}> = ({ gruplar, kalemler, secili, onGrup, ekAdresler, onEkle, onCikar, oneriler, kisiSayisi, mailSayisi }) => {
  // Adres kutusundaki yazı ve uyarı.
  const [girdi, setGirdi] = useState('');
  const [uyari, setUyari] = useState('');

  // Kişiye özel adres ekle, biçimi kontrol et, aynısı yoksa ekle.
  const ekle = () => {
    const adres = girdi.trim().toLowerCase();
    if (!epostaGecerliMi(adres)) {
      setUyari('Geçerli bir e-posta adresi yazın.');
      return;
    }
    if (!ekAdresler.includes(adres)) onEkle(adres);
    setGirdi('');
    setUyari('');
  };

  return (
    <div className="p-4 bg-paper-100 rounded-xl border border-paper-300 space-y-4">
      {/* 1. Alıcı grupları, her grup için işaret kutusu ve hangi kalemleri alacağı. */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-stone-800 font-semibold text-xs">
          <Building2 className="w-4 h-4 text-petrol" />
          <span>1. Alıcı grupları <span className="font-normal text-stone-500">— iş koluna göre önerildi; her grup sadece kendi iş kolunun kalemlerini alır</span></span>
        </div>
        {gruplar.length === 0 ? (
          <p className="text-xs text-stone-500">Tanımlı aktif grup yok. Kişiye özel adres ekleyebilir ya da Alıcı Grupları sayfasından grup tanımlayabilirsiniz.</p>
        ) : (
          <div className="space-y-1.5">
            {gruplar.map((g) => {
              const nolar = grubunKalemleri(g, kalemler).map((k) => k.no);
              return (
                <label key={g.id} className="flex flex-wrap items-center gap-2 p-2 rounded-lg bg-white border border-paper-300 cursor-pointer text-xs">
                  <input type="checkbox" checked={secili.has(g.id)} onChange={() => onGrup(g.id)} className="w-4 h-4 accent-petrol" />
                  <span className="font-medium text-stone-800">{g.ad}</span>
                  <span className="text-stone-500">({g.adresler.length} kişi)</span>
                  <span className="sm:ml-auto text-stone-500">
                    {nolar.length > 0 ? `→ kalem ${nolar.join(', ')}` : 'seçili kalemlerden alacağı yok'}
                  </span>
                </label>
              );
            })}
          </div>
        )}
      </div>

      {/* 2. Kişiye özel adresler, eklenenler ve ekleme kutusu (bilinen adresler öneri olarak çıkar). */}
      <div className="space-y-2 pt-3 border-t border-paper-200">
        <div className="flex items-center gap-2 text-stone-800 font-semibold text-xs">
          <UserCheck className="w-4 h-4 text-amber-700" />
          <span>2. Kişiye özel <span className="font-normal text-stone-500">— gruptan bağımsız; seçili bütün kalemleri alır</span></span>
        </div>
        {ekAdresler.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {ekAdresler.map((a) => (
              <span key={a} className="inline-flex items-center gap-1 pl-2 pr-1 py-1 rounded-lg bg-white border border-amber-300 text-xs font-mono text-stone-800">
                {a}
                <button type="button" onClick={() => onCikar(a)} aria-label={`${a} adresini çıkar`}
                  className="p-0.5 rounded-sm text-stone-400 hover:text-rose-700 hover:bg-rose-50 cursor-pointer">
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <input type="email" list="adres-onerileri" value={girdi} placeholder="ad@firma.com"
            onChange={(e) => setGirdi(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                ekle();
              }
            }}
            aria-label="Kişiye özel e-posta adresi"
            className="flex-1 min-w-0 p-2 rounded-lg border border-paper-300 bg-white font-mono text-xs focus:outline-hidden focus:ring-2 focus:ring-petrol/20" />
          <button type="button" onClick={ekle}
            className="flex items-center gap-1 px-3 py-2 rounded-lg bg-white border border-paper-300 text-xs font-semibold text-stone-700 hover:bg-paper-200 cursor-pointer">
            <Plus className="w-3.5 h-3.5" /> Ekle
          </button>
          <datalist id="adres-onerileri">
            {oneriler.map((a) => <option key={a} value={a} />)}
          </datalist>
        </div>
        {uyari && <p className="text-xs text-rose-700">{uyari}</p>}
      </div>

      {/* Özet, kaç kişiye mail gidecek (ya da kimseye gitmiyor uyarısı). */}
      <div className={`text-xs font-semibold ${mailSayisi > 0 ? 'text-stone-800' : 'text-rose-700'}`}>
        {mailSayisi > 0
          ? `Özet: ${kisiSayisi} kişinin her birine ayrı mail gidecek` +
            (mailSayisi > 1 ? ` (kişilere göre ${mailSayisi} farklı kalem seçimi).` : ' (hepsi aynı kalemleri alır).')
          : 'Seçili kalemler şu an kimseye gitmiyor: grup işaretleyin ya da kişiye özel adres ekleyin.'}
      </div>
    </div>
  );
};

// "Onay Kuyruğu" sayfası, onay bekleyen raporlar ve son kararlar listesi.
import React, { useState } from 'react';
import { CheckCircle2, Eye, Filter, AlertTriangle } from 'lucide-react';
import { Kullanici, RaporOzeti } from '../types/api';
import { DurumRozeti } from '../bicim';
import { tarihSaat } from '../tarih';

// Bu sayfaya App'ten verilen bilgiler.
interface RaporlarPageProps {
  kullanici: Kullanici;
  bekleyenler: RaporOzeti[];
  gecmis: RaporOzeti[];
  onSec: (raporId: number) => void;
}

type Filtre = 'bekleyen' | 'gecmis';

export const RaporlarPage: React.FC<RaporlarPageProps> = ({ kullanici, bekleyenler, gecmis, onSec }) => {
  // Hangi sekme seçili (bekleyenler / geçmiş) ve gösterilecek liste.
  const [filtre, setFiltre] = useState<Filtre>('bekleyen');
  const liste = filtre === 'bekleyen' ? bekleyenler : gecmis;

  // Sekme düğmesi üreten küçük yardımcı.
  const sekme = (id: Filtre, etiket: string) => (
    <button
      onClick={() => setFiltre(id)}
      className={`px-3 py-1.5 rounded-md font-medium transition-all whitespace-nowrap shrink-0 cursor-pointer ${
        filtre === id ? 'bg-white text-stone-900 shadow-xs font-semibold' : 'text-stone-600 hover:text-stone-900'
      }`}
    >
      {etiket}
    </button>
  );

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Başlık ve sekmeler. */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 sm:gap-4 pb-4 border-b border-paper-300">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">Mevzuat Raporu Onay Kuyruğu</h2>
          <p className="text-xs text-stone-500 mt-0.5">
            Günlük taramalarda bulunan ve firmanızı ilgilendiren kayıtlar rapor olarak onaya sunulur. Onaylanmadan kimseye gönderilmez.
          </p>
        </div>
        <div className="flex items-center gap-1.5 bg-paper-200 p-1 rounded-lg border border-paper-300 text-xs overflow-x-auto max-w-full">
          <Filter className="w-3.5 h-3.5 text-stone-400 ml-2 shrink-0" />
          {sekme('bekleyen', `Onay Bekleyenler (${bekleyenler.length})`)}
          {sekme('gecmis', `Son Kararlar (${gecmis.length})`)}
        </div>
      </div>

      <div className="space-y-4">
        {/* Liste boşsa bilgi kutusu, doluysa her rapor için bir kart. */}
        {liste.length === 0 ? (
          <div className="bg-white rounded-xl border border-paper-300 p-12 text-center space-y-2">
            <CheckCircle2 className="w-10 h-10 text-emerald-500 mx-auto" />
            <h3 className="font-semibold text-stone-800 text-sm">
              {filtre === 'bekleyen' ? 'Onay bekleyen rapor yok' : 'Henüz karar verilmiş rapor yok'}
            </h3>
            <p className="text-xs text-stone-500 max-w-sm mx-auto">
              Taramada ilgili yeni bir kayıt bulunduğunda rapor burada listelenir ve size mail ile bildirilir.
            </p>
          </div>
        ) : (
          liste.map((r) => {
            const bekliyor = r.durum === 'ONAY_BEKLIYOR';
            return (
              <div key={r.id} className="bg-white rounded-xl border border-paper-300 p-5 shadow-xs space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                  <div className="space-y-1.5 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500 font-mono">
                      <span>Rapor #{r.id}</span>
                      <span className="text-stone-300">•</span>
                      <span>{r.kayit_sayisi} kalem</span>
                    </div>
                    <h3 className="text-base font-semibold text-stone-900 wrap-break-word">{r.konu}</h3>
                  </div>
                  <DurumRozeti durum={r.durum} />
                </div>

                {/* Raporda hata varsa (ör. onay maili gidemedi) sarı uyarı. */}
                {r.hata && (
                  <div className="flex items-start gap-2 p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900">
                    <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
                    <span>{r.hata}</span>
                  </div>
                )}

                {/* Alt kısım, tarihler ve İncele düğmesi. */}
                <div className="pt-3 border-t border-paper-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
                  <div className="flex flex-wrap items-center gap-4 text-stone-500">
                    <span>Oluşturuldu: {tarihSaat(r.olusturuldu)}</span>
                    {r.karar_zamani && <span>Karar: {tarihSaat(r.karar_zamani)}</span>}
                  </div>
                  <button
                    onClick={() => onSec(r.id)}
                    className={`w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg font-semibold transition-all shadow-xs cursor-pointer ${
                      bekliyor ? 'bg-petrol text-white hover:bg-petrol-dark' : 'bg-paper-200 text-stone-800 hover:bg-paper-300'
                    }`}
                  >
                    <Eye className="w-4 h-4" />
                    <span>{bekliyor && kullanici.karar_verebilir ? 'İncele & Karar Ver' : 'İncele'}</span>
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

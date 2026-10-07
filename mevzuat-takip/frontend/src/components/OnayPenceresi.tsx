import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, HelpCircle } from 'lucide-react';

// Onay penceresine verilen bilgiler, başlık, metin, düğme yazısı, kırmızı mı.
export interface OnaySorusu {
  baslik: string;
  metin: string;
  dugme: string; // ör. "Pasifleştir"
  tehlikeli?: boolean; // kırmızı düğme (geri alınması zahmetli işlemler)
}

// Tarayıcının kendi confirm() kutusu yerine panelin tasarımında onay penceresi. Esc ya da dışına tıklama, vazgeç.
export const OnayPenceresi: React.FC<OnaySorusu & { onEvet: () => void; onVazgec: () => void }> = ({
  baslik, metin, dugme, tehlikeli = false, onEvet, onVazgec,
}) => {
  // Vazgeç düğmesine erişmek için referans (pencere açılınca odak oraya gitsin).
  const vazgecDugmesi = useRef<HTMLButtonElement>(null);

  // Pencere açılınca, odağı Vazgeç'e ver, Esc tuşuna basılırsa vazgeç, pencere kapanınca dinlemeyi bırak.
  useEffect(() => {
    vazgecDugmesi.current?.focus(); // Enter'a yanlışlıkla basılırsa işlem yapılmasın
    const tus = (e: KeyboardEvent) => { if (e.key === 'Escape') onVazgec(); };
    window.addEventListener('keydown', tus);
    return () => window.removeEventListener('keydown', tus);
  }, [onVazgec]);

  // Tehlikeliyse ünlem, değilse soru işareti ikonu.
  const Ikon = tehlikeli ? AlertTriangle : HelpCircle;
  // createPortal, pencereyi sayfanın en dışına (body) çizer, başka kutuların altında kalmasın.
  return createPortal(
    <div className="fixed inset-0 z-60 flex items-center justify-center bg-stone-950/60 p-4" onClick={onVazgec}>
      {/* Pencerenin kendisi (içine tıklamak arkaya geçmesin diye stopPropagation). */}
      <div role="alertdialog" aria-modal="true" aria-labelledby="onay-baslik" onClick={(e) => e.stopPropagation()}
        className="bg-white rounded-xl shadow-xl border border-paper-300 w-full max-w-sm overflow-hidden">
        <div className="p-5 flex gap-3">
          <div className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${
            tehlikeli ? 'bg-rose-50 text-rose-700' : 'bg-petrol/10 text-petrol'}`}>
            <Ikon className="w-5 h-5" />
          </div>
          <div className="space-y-1.5">
            <h3 id="onay-baslik" className="font-semibold text-stone-900 text-sm">{baslik}</h3>
            <p className="text-xs text-stone-600 leading-relaxed">{metin}</p>
          </div>
        </div>
        {/* Düğmeler, Vazgeç ve asıl işlem. */}
        <div className="px-5 py-3 bg-paper-50 border-t border-paper-300 flex justify-end gap-2 text-xs">
          <button ref={vazgecDugmesi} type="button" onClick={onVazgec}
            className="px-4 py-2 rounded-lg border border-paper-300 text-stone-700 hover:bg-paper-200 cursor-pointer">
            Vazgeç
          </button>
          <button type="button" onClick={onEvet}
            className={`px-4 py-2 rounded-lg text-white font-semibold cursor-pointer ${
              tehlikeli ? 'bg-rose-700 hover:bg-rose-800' : 'bg-petrol hover:bg-petrol-dark'}`}>
            {dugme}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};

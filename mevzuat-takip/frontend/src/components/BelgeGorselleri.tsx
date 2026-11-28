// Rapora giren belgedeki tablolar (yazılı PDF'te gerçek tablo) ve taranmış tablo/görsel bölümlerinin görüntüsü.
// Rapor penceresi ve arşivdeki "rapora giren belgeler" penceresi kullanır.
import React from 'react';
import { BelgeGorseli } from '../types/api';

export const BelgeGorselleri: React.FC<{ gorseller: BelgeGorseli[]; baslik?: string }> = ({ gorseller, baslik = 'Belgedeki tablo ve görseller' }) => {
  if (gorseller.length === 0) return null;
  return (
    <div className="text-xs text-stone-800 space-y-2">
      <strong className="block">{baslik}:</strong>
      {gorseller.map((g) => (
        <figure key={g.id} className="space-y-1">
          <figcaption className="text-[11px] text-stone-500">{g.aciklama}</figcaption>
          {g.satirlar ? (
            <div className="overflow-x-auto">
              <table className="border-collapse text-[11px]">
                <tbody>
                  {g.satirlar.map((satir, i) => (
                    <tr key={i}>
                      {satir.map(([metin, yayilma], j) => (
                        <td key={j} colSpan={yayilma > 1 ? yayilma : undefined}
                          className="border border-paper-300 px-1.5 py-0.5 align-top">{metin}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : g.url ? (
            <a href={g.url} target="_blank" rel="noopener noreferrer" title="Büyük aç">
              <img src={g.url} alt={g.aciklama} loading="lazy"
                width={g.genislik ?? undefined} height={g.yukseklik ?? undefined}
                className="max-w-full h-auto border border-paper-200 rounded-sm bg-white" />
            </a>
          ) : null}
        </figure>
      ))}
    </div>
  );
};

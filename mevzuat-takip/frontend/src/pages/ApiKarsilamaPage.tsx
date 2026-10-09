// API kullanıcısının karşılama sayfası. Panelde başka bir şey görmez, dokümana ve anahtar kullanımına yönlendirilir.
import React from 'react';
import { BookOpen, Download, ExternalLink, KeyRound } from 'lucide-react';

export const ApiKarsilamaPage: React.FC = () => (
  <div className="max-w-2xl space-y-4">
    <div className="pb-4 border-b border-paper-300">
      <h2 className="text-lg sm:text-xl font-bold text-stone-900 tracking-tight">API erişimi</h2>
      <p className="text-xs text-stone-500 mt-0.5">Mevzuat Takip API'sinin dokümanı ve nasıl kullanılacağı.</p>
    </div>
    <a href="/api/dokuman" target="_blank" rel="noopener noreferrer"
      className="flex items-center justify-between gap-3 p-4 rounded-xl bg-white border border-paper-300 hover:border-petrol shadow-xs">
      <span className="flex items-center gap-3">
        <BookOpen className="w-5 h-5 text-petrol" />
        <span>
          <span className="block text-sm font-semibold text-stone-900">API Dokümanı (Swagger)</span>
          <span className="block text-xs text-stone-500">Bütün uç noktalar, istek ve cevap biçimleri</span>
        </span>
      </span>
      <ExternalLink className="w-4 h-4 text-stone-400" />
    </a>
    <DokumanIndir />
    <div className="p-4 rounded-xl bg-paper-200 border border-paper-300 text-xs text-stone-700 space-y-2 leading-relaxed">
      <div className="flex items-center gap-2 font-semibold text-stone-900"><KeyRound className="w-4 h-4 text-petrol" /> İstek atmak için</div>
      <p>Yöneticiden bir <strong>API anahtarı</strong> isteyin. Dokümanda sağ üstteki <strong>Authorize</strong> düğmesine anahtarı girin, "Try it out" ile istekler o anahtarla atılır.</p>
      <p>Kendi sisteminizden çağırırken anahtarı her istekte başlıkta gönderin:</p>
      <pre className="p-2.5 rounded-lg bg-white border border-paper-300 font-mono text-[11px] overflow-x-auto">{`curl -H "Authorization: Bearer mvz_..." ${window.location.origin}/api/raporlar`}</pre>
    </div>
  </div>
);

// Dokümanı dosya olarak indirme bağlantıları. HTML internetsiz açılır, JSON Postman gibi araçlara yüklenir.
export const DokumanIndir: React.FC = () => (
  <div className="flex flex-col sm:flex-row gap-2">
    <a href="/api/dokuman/indir?bicim=html" download
      className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-paper-300 bg-white text-xs font-semibold text-stone-700 hover:bg-paper-200">
      <Download className="w-3.5 h-3.5" /> Dokümanı indir (HTML, internetsiz açılır)
    </a>
    <a href="/api/dokuman/indir?bicim=json" download
      className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-paper-300 bg-white text-xs font-semibold text-stone-700 hover:bg-paper-200">
      <Download className="w-3.5 h-3.5" /> OpenAPI JSON (Postman'e yüklenir)
    </a>
  </div>
);

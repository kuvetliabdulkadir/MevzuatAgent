// Sol menü, kullanıcının rolüne göre görebileceği sayfalar.
import React from 'react';
import { Clock, FileCheck2, Globe, History, Info, Layers, Mail, Users, X } from 'lucide-react';
import { Kullanici } from '../types/api';

// Menüdeki sekmelerin adları.
export type ActiveTab = 'raporlar' | 'gruplar' | 'kaynaklar' | 'konular' | 'tarama' | 'kullanicilar' | 'denetim';

interface SidebarProps {
  kullanici: Kullanici;
  activeTab: ActiveTab;
  onTabChange: (tab: ActiveTab) => void;
  bekleyenSayisi: number;
  isMobileOpen?: boolean;
  onCloseMobile?: () => void;
}

// Menü öğeleri, kod adı, yazısı, açıklaması, ikonu ve bu kullanıcıya görünüp görünmeyeceği.
export const Sidebar: React.FC<SidebarProps> = ({
  kullanici,
  activeTab,
  onTabChange,
  bekleyenSayisi,
  isMobileOpen = false,
  onCloseMobile,
}) => {
  const menu = [
    { id: 'raporlar' as ActiveTab, label: 'Onay Kuyruğu', desc: 'Mevzuat raporları', icon: FileCheck2,
      badge: bekleyenSayisi, gorunur: true },
    { id: 'gruplar' as ActiveTab, label: 'Alıcı Grupları', desc: 'Kime, hangi iş kolu', icon: Mail,
      gorunur: kullanici.grup_yonetebilir },
    { id: 'kaynaklar' as ActiveTab, label: 'Kaynaklar', desc: 'Taranan siteler', icon: Globe,
      gorunur: kullanici.ayar_yonetebilir },
    { id: 'konular' as ActiveTab, label: 'Konular', desc: 'Anahtar kelimeler', icon: Layers,
      gorunur: kullanici.ayar_yonetebilir },
    { id: 'tarama' as ActiveTab, label: 'Tarama', desc: 'Saatler ve durum', icon: Clock, gorunur: true },
    { id: 'kullanicilar' as ActiveTab, label: 'Kullanıcılar', desc: 'Ekle, parola, pasifleştir', icon: Users,
      gorunur: kullanici.kurtarma_yapabilir },
    { id: 'denetim' as ActiveTab, label: 'Denetim Kaydı', desc: 'Kim, ne zaman, ne yaptı', icon: History,
      gorunur: kullanici.kurtarma_yapabilir },
  ];

  // Bir sekmeye tıklanınca, sekmeyi değiştir, telefonda menüyü kapat.
  const sec = (tab: ActiveTab) => {
    onTabChange(tab);
    onCloseMobile?.();
  };

  // Menünün içeriği (masaüstü ve telefon sürümü aynı içeriği kullanıyor).
  const icerik = (
    <div className="flex flex-col justify-between min-h-full">
      <div>
        {/* Üstte, rol başlığı ve telefonda kapat düğmesi. */}
        <div className="px-3 pb-3 mb-2 border-b border-paper-200 flex items-center justify-between">
          <p className="text-[11px] font-semibold text-stone-500 uppercase tracking-wider font-mono">
            {kullanici.rol === 'admin' ? 'YÖNETİCİ KONSOLU' : 'ONAYLAYICI KONSOLU'}
          </p>
          {onCloseMobile && (
            <button onClick={onCloseMobile} aria-label="Menüyü Kapat"
              className="lg:hidden p-1 rounded-md text-stone-400 hover:text-stone-700 hover:bg-paper-200 cursor-pointer">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Sekmeler, sadece görünür olanlar, seçili olan koyu renkli. */}
        <nav className="space-y-1">
          {menu.filter((m) => m.gorunur).map((item) => {
            const Icon = item.icon;
            const aktif = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => sec(item.id)}
                className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-left transition-all cursor-pointer ${
                  aktif ? 'bg-petrol text-white shadow-xs' : 'text-stone-700 hover:bg-paper-200 hover:text-stone-900'
                }`}
              >
                <div className="flex items-center gap-3">
                  <Icon className={`w-4 h-4 shrink-0 ${aktif ? 'text-gold-light' : 'text-stone-500'}`} />
                  <div>
                    <div className="text-xs font-semibold leading-tight">{item.label}</div>
                    <div className={`text-[10px] leading-tight ${aktif ? 'text-stone-300' : 'text-stone-500'}`}>
                      {item.desc}
                    </div>
                  </div>
                </div>
                {/* Bekleyen rapor sayısı rozeti. */}
                {item.badge !== undefined && item.badge > 0 && (
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                    aktif ? 'bg-brick text-white' : 'bg-brick-light/10 text-brick border border-brick-light/30'
                  }`}>
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Altta, görev ayrılığı açıklaması (role göre farklı yazı). */}
      <div className="p-3 rounded-lg bg-paper-200 border border-paper-300 text-[11px] text-stone-600 space-y-1.5 mt-4">
        <div className="flex items-center gap-1.5 font-semibold text-stone-800">
          <Info className="w-3.5 h-3.5 text-petrol shrink-0" />
          <span>Görevler Ayrılığı</span>
        </div>
        <p className="text-[10px] leading-normal text-stone-500">
          {kullanici.karar_verebilir
            ? 'Raporları inceleyip onaylama veya reddetme yetkiniz var. Onaylanan rapor alıcı gruplarına mail olarak gider.'
            : 'Yönetici olarak kullanıcıları ve denetim kaydını yönetir, yanlış ayarları geri alırsınız. Onay/ret kararını onaylayıcı verir.'}
        </p>
      </div>
    </div>
  );

  // Masaüstünde sabit menü, telefonda soldan kayan menü.
  return (
    <>
      {/* Sayfa uzayınca menü yerinde kalır (başlığın altına yapışık), menü ekrana sığmazsa kendi içinde kayar. */}
      <aside className="hidden lg:block sticky top-16 self-start w-64 bg-paper-50 border-r border-paper-300 h-[calc(100dvh-4rem)] overflow-y-auto overscroll-contain p-4 shrink-0">
        {icerik}
      </aside>
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-72 max-w-[85vw] bg-paper-50 border-r border-paper-300 p-4 shadow-2xl h-dvh overflow-y-auto overscroll-contain transition-transform duration-300 ease-in-out lg:hidden ${
          isMobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {icerik}
      </aside>
    </>
  );
};

// Sayfanın en üstündeki çubuk, logo, kullanıcının rolü, adı, "parolamı değiştir" ve "çıkış" düğmeleri.
import React, { useState } from 'react';
import { KeyRound, LogOut, Menu, Shield, UserCheck, X } from 'lucide-react';
import { Kullanici } from '../types/api';
import { ParolaDegistir } from './ParolaDegistir';

// Bu bileşene dışarıdan verilen bilgiler (props).
interface NavbarProps {
  kullanici: Kullanici;
  bekleyenSayisi: number;
  onCikis: () => void;
  isMobileMenuOpen?: boolean;
  onToggleMobileMenu?: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  kullanici,
  bekleyenSayisi,
  onCikis,
  isMobileMenuOpen = false,
  onToggleMobileMenu,
}) => {
  // Kullanıcı admin mi, parola değiştirme penceresi açık mı, adının baş harfleri (yuvarlak avatar için).
  const admin = kullanici.rol === 'admin';
  const [parolaAcik, setParolaAcik] = useState(false);
  const basHarfler = kullanici.ad.split(' ').map((p) => p.charAt(0)).join('').slice(0, 2).toLocaleUpperCase('tr');

  return (
    <header className="h-16 bg-white border-b border-paper-300 px-3 sm:px-6 flex items-center justify-between sticky top-0 z-20 shadow-xs select-none">
      {/* Sol taraf, telefonda menü düğmesi + logo ve başlık. */}
      <div className="flex items-center gap-2 sm:gap-3 shrink-0">
        {/* Telefonda görünen menü aç/kapat düğmesi. */}
        <button
          type="button"
          onClick={onToggleMobileMenu}
          className="lg:hidden p-2 rounded-lg text-stone-600 hover:text-stone-900 hover:bg-paper-200 transition-colors cursor-pointer"
          aria-label="Menüyü Aç/Kapat"
        >
          {isMobileMenuOpen ? <X className="w-5 h-5 text-petrol" /> : <Menu className="w-5 h-5" />}
        </button>

        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-petrol flex items-center justify-center text-white font-bold text-sm shadow-xs shrink-0">
            MT
          </div>
          <div>
            <h1 className="font-bold text-stone-900 text-sm tracking-tight leading-tight">Mevzuat Takip</h1>
            <p className="hidden md:block text-[11px] text-stone-500 font-mono leading-tight">
              Kuyumculuk Mevzuatı & Uyum Takip Sistemi
            </p>
          </div>
        </div>
      </div>

      {/* Sağ taraf, rol rozeti, kullanıcı bilgisi, düğmeler. */}
      <div className="flex items-center gap-2 sm:gap-4 shrink-0">
        {/* Rol rozeti (onaylayıcıda bekleyen rapor sayısıyla). */}
        <span
          className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border ${
            admin ? 'bg-petrol/10 text-petrol border-petrol/30' : 'bg-amber-50 text-amber-900 border-amber-300'
          }`}
        >
          {admin ? <Shield className="w-3.5 h-3.5" /> : <UserCheck className="w-3.5 h-3.5" />}
          {admin ? 'Yönetici' : kullanici.api_kullanicisi ? 'API kullanıcısı' : 'Onaylayıcı'}
          {!admin && bekleyenSayisi > 0 && (
            <span className="ml-1 px-1.5 bg-amber-600 text-white text-[10px] rounded-full">{bekleyenSayisi}</span>
          )}
        </span>

        {/* Avatar, ad, e-posta ve iki düğme. */}
        <div className="flex items-center gap-2 pl-2 sm:pl-3 border-l border-paper-300">
          <div
            className={`w-8 h-8 rounded-full border flex items-center justify-center text-xs font-bold shrink-0 ${
              admin ? 'bg-petrol/10 text-petrol border-petrol/30' : 'bg-amber-100 text-amber-900 border-amber-300'
            }`}
          >
            {basHarfler}
          </div>
          <div className="hidden lg:block text-left">
            <p className="text-xs font-semibold text-stone-800 leading-tight">{kullanici.ad}</p>
            <p className="text-[10px] text-stone-500 font-mono">{kullanici.eposta}</p>
          </div>
          {/* Parolamı değiştir düğmesi. */}
          <button
            type="button"
            onClick={() => setParolaAcik(true)}
            title="Parolamı değiştir"
            className="ml-1 p-2 rounded-lg text-stone-500 hover:text-petrol hover:bg-paper-200 transition-colors cursor-pointer"
            aria-label="Parolamı değiştir"
          >
            <KeyRound className="w-4 h-4" />
          </button>
          {/* Çıkış düğmesi. */}
          <button
            type="button"
            onClick={onCikis}
            title="Çıkış"
            className="ml-1 p-2 rounded-lg text-stone-500 hover:text-rose-700 hover:bg-rose-50 transition-colors cursor-pointer"
            aria-label="Çıkış"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
      {/* Parola değiştirme penceresi (açıksa). */}
      {parolaAcik && <ParolaDegistir eposta={kullanici.eposta} onKapat={() => setParolaAcik(false)} />}
    </header>
  );
};

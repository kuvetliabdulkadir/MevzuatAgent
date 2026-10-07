// Uygulamanın ana bileşeni, giriş yapılmış mı bakar, giriş ekranını ya da paneli (menü + sayfalar) gösterir.
// useState, bileşenin hafızası (değişince ekran yeniden çizilir). useEffect, bileşen açılınca/değişince çalışan iş.
import React, { useCallback, useEffect, useState } from 'react';
// Sunucuyla konuşan fonksiyonlar.
import { api, csrfAyarla, oturumDusunce } from './api';
// Sunucudan gelen verilerin tipleri.
import { Kullanici, Mesaj, OturumBilgisi, RaporOzeti, ZamanlamaDurumu } from './types/api';
import { MesajKutusu } from './bicim';
import { Navbar } from './components/Navbar';
import { Sidebar, ActiveTab } from './components/Sidebar';
import { RaporModal } from './components/RaporModal';
import { GirisPage } from './pages/GirisPage';
import { RaporlarPage } from './pages/RaporlarPage';
import { GruplarPage } from './pages/GruplarPage';
import { SourcesPage } from './pages/SourcesPage';
import { KeywordsPage } from './pages/KeywordsPage';
import { TaramaPage, ZamanlayiciUyarisi } from './pages/TaramaPage';
import { KullanicilarPage } from './pages/KullanicilarPage';
import { DenetimPage } from './pages/DenetimPage';
import { ParolaBelirlePage } from './pages/ParolaBelirlePage';

// Davet ve sıfırlama mailindeki link parola anahtarını adreste taşır. Anahtar adresten hemen silinir, tarayıcı geçmişinde kalmasın.
// Adreste parola linki varsa onu alır ve adres çubuğundan siler.
function maildekiParolaLinki(): string | null {
  const token = new URLSearchParams(window.location.search).get('parola');
  if (token) window.history.replaceState(null, '', window.location.pathname);
  return token;
}

const ACILISTAKI_PAROLA_LINKI = maildekiParolaLinki(); // modül yüklenirken bir kez (StrictMode çift çağrısından etkilenmez)

// En üst bileşen, hangi ekranın gösterileceğine karar verir.
export function App() {
  // Maildeki parola linki, oturum bilgisi ve bağlantı hatası.
  const [parolaLinki, setParolaLinki] = useState<string | null>(ACILISTAKI_PAROLA_LINKI);
  const [oturum, setOturum] = useState<OturumBilgisi | null>(null);
  const [baglantiHatasi, setBaglantiHatasi] = useState('');

  // Açılışta ve giriş/çıkışta, kim giriş yapmış, sunucudan sorulur (oturum çerezde, JS göremez).
  const oturumuYukle = useCallback(() => {
    api
      .get<OturumBilgisi>('/oturum')
      .then((o) => {
        csrfAyarla(o.csrf);
        setOturum(o);
        setBaglantiHatasi('');
      })
      .catch(() => setBaglantiHatasi('Sunucuya ulaşılamıyor. Bir süre sonra sayfayı yenileyin.'));
  }, []);

  // Açılışta oturumu yükle, oturum düşerse de yeniden yüklensin diye kaydet.
  useEffect(() => {
    oturumDusunce(oturumuYukle);
    oturumuYukle();
  }, [oturumuYukle]);

  // Sırayla şuna bakılır. Sunucuya ulaşılamıyorsa hata, parola linkiyle gelindiyse parola belirleme sayfası,
  // oturum henüz gelmediyse "Yükleniyor", giriş yapılmamışsa giriş sayfası, yoksa panel.
  if (baglantiHatasi) {
    return <div className="min-h-screen flex items-center justify-center p-4"><MesajKutusu tur="hata">{baglantiHatasi}</MesajKutusu></div>;
  }
  if (parolaLinki && oturum !== null) {
    return <ParolaBelirlePage token={parolaLinki} onBitti={() => { setParolaLinki(null); oturumuYukle(); }} />;
  }
  if (oturum === null) {
    return <div className="min-h-screen flex items-center justify-center text-xs text-stone-500">Yükleniyor…</div>;
  }
  if (oturum.kullanici === null) {
    return <GirisPage baslangic={oturum.mfa ?? 'parola'} onGirisTamam={oturumuYukle} />;
  }
  return <Panel kullanici={oturum.kullanici} mailKapali={oturum.mail_kapali} onCikis={() => api.post('/cikis').finally(oturumuYukle)} />;
}

// Onay mailindeki düğme paneli rapor numarasıyla açar. Adres giriş ve iki adımlı doğrulama boyunca değişmez,
// panel açılınca o rapor doğrudan gösterilir.
// Adreste rapor numarası varsa onu verir. Onay mailindeki düğme bunu kullanıyor.
function maildekiRapor(): number | null {
  const id = Number(new URLSearchParams(window.location.search).get('rapor'));
  return Number.isInteger(id) && id > 0 ? id : null;
}

// Giriş yapılmış kullanıcının paneli, üst çubuk, sol menü ve seçili sayfa.
const Panel: React.FC<{ kullanici: Kullanici; mailKapali: boolean; onCikis: () => void }> = ({ kullanici, mailKapali, onCikis }) => {
  // Seçili menü sekmesi, telefonda menü açık mı, rapor listesi, açık rapor penceresi, ekrandaki mesaj.
  const [activeTab, setActiveTab] = useState<ActiveTab>('raporlar');
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [raporlar, setRaporlar] = useState<{ bekleyenler: RaporOzeti[]; gecmis: RaporOzeti[] }>({ bekleyenler: [], gecmis: [] });
  const [seciliRapor, setSeciliRapor] = useState<number | null>(maildekiRapor);
  const [mesaj, setMesaj] = useState<Mesaj | null>(null);

  // Rapor listesini sunucudan çeker.
  const raporlariYukle = useCallback(() => {
    api.get<typeof raporlar>('/raporlar').then(setRaporlar).catch((e) => setMesaj({ tur: 'hata', mesaj: e.message }));
  }, []);
  useEffect(raporlariYukle, [raporlariYukle]);
  // Rapor açıldıktan sonra rapor numarası adresten silinir, sayfa yenilenince ya da çıkıp girince rapor yeniden açılmasın.
  useEffect(() => {
    if (window.location.search) window.history.replaceState(null, '', window.location.pathname);
  }, []);

  // Zamanlayıcı çalışmıyorsa (nabız eski) her sayfada uyarı, planlı taramalar yapılmıyor demektir.
  // Zamanlayıcının durumunu dakikada bir sorar (çalışmıyorsa her sayfada uyarı çıkar).
  const [zamanlama, setZamanlama] = useState<ZamanlamaDurumu | null>(null);
  useEffect(() => {
    const yukle = () => api.get<ZamanlamaDurumu>('/zamanlama').then(setZamanlama).catch(() => {});
    yukle();
    // 60 saniyede bir tekrar sor, sayfa kapanınca zamanlayıcıyı durdur.
    const t = window.setInterval(yukle, 60_000);
    return () => window.clearInterval(t);
  }, []);

  // Rapor penceresinde karar verilince, pencereyi kapat, mesajı göster, listeyi yenile.
  const kararVerildi = (m: Mesaj) => {
    setSeciliRapor(null);
    setMesaj(m);
    raporlariYukle();
  };

  // Panelin görünümü.
  return (
    <div className="min-h-screen bg-paper-100 flex flex-col font-sans">
      {/* Üst çubuk. */}
      <Navbar
        kullanici={kullanici}
        bekleyenSayisi={raporlar.bekleyenler.length}
        onCikis={onCikis}
        isMobileMenuOpen={isMobileMenuOpen}
        onToggleMobileMenu={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
      />

      <div className="flex flex-1 overflow-x-clip relative">
        {/* Telefonda menü açıkken arkadaki karartma, tıklanınca menü kapanır. */}
        {isMobileMenuOpen && (
          <div onClick={() => setIsMobileMenuOpen(false)} className="fixed inset-0 bg-stone-950/50 z-30 lg:hidden" />
        )}
        {/* Sol menü. */}
        <Sidebar
          kullanici={kullanici}
          activeTab={activeTab}
          onTabChange={(t) => { setActiveTab(t); setMesaj(null); }}
          bekleyenSayisi={raporlar.bekleyenler.length}
          isMobileOpen={isMobileMenuOpen}
          onCloseMobile={() => setIsMobileMenuOpen(false)}
        />

        {/* Sayfanın ana alanı. */}
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-3 sm:p-5 lg:p-8 min-w-0 max-w-7xl mx-auto w-full space-y-4">
          {/* Zamanlayıcı çalışmıyor uyarısı (Tarama sayfasında zaten var, orada tekrar gösterme). */}
          {zamanlama && !zamanlama.zamanlayici_calisiyor && activeTab !== 'tarama' && <ZamanlayiciUyarisi durum={zamanlama} />}
          {/* Mail sunucusu ayarsızsa kırmızı uyarı. */}
          {mailKapali && (
            <MesajKutusu tur="hata">
              Mail gönderimi kapalı: mail sunucusu (MEVZUAT_SMTP_HOST) ayarlı değil. Onay ve dağıtım mailleri kimseye
              gitmiyor, sunucudaki giden_mailler/ klasörüne yazılıyor. Sunucu yöneticisine bildirin.
            </MesajKutusu>
          )}
          {/* Son işlemin mesajı. */}
          {mesaj && <MesajKutusu tur={mesaj.tur}>{mesaj.mesaj}</MesajKutusu>}
          {/* Seçili sekmeye göre sayfa, yetkisi olmayana sayfa gösterilmez. */}
          {activeTab === 'raporlar' && (
            <RaporlarPage kullanici={kullanici} bekleyenler={raporlar.bekleyenler} gecmis={raporlar.gecmis}
              onSec={(id) => { setMesaj(null); setSeciliRapor(id); }} />
          )}
          {activeTab === 'gruplar' && kullanici.grup_yonetebilir && <GruplarPage />}
          {activeTab === 'kaynaklar' && kullanici.ayar_yonetebilir && <SourcesPage kurtarmaYapabilir={kullanici.kurtarma_yapabilir} />}
          {activeTab === 'konular' && kullanici.ayar_yonetebilir && <KeywordsPage kurtarmaYapabilir={kullanici.kurtarma_yapabilir} />}
          {activeTab === 'tarama' && <TaramaPage ayarYonetebilir={kullanici.ayar_yonetebilir} />}
          {activeTab === 'kullanicilar' && kullanici.kurtarma_yapabilir && <KullanicilarPage benimId={kullanici.id} />}
          {activeTab === 'denetim' && kullanici.kurtarma_yapabilir && <DenetimPage />}
        </main>
      </div>

      {/* Seçili rapor varsa rapor penceresini aç. */}
      {seciliRapor !== null && (
        <RaporModal key={seciliRapor} raporId={seciliRapor} kullanici={kullanici}
          onKapat={() => setSeciliRapor(null)} onKararVerildi={kararVerildi} />
      )}
    </div>
  );
};

export default App;

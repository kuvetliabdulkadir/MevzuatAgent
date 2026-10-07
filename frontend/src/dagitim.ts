// Onay ekranındaki dağıtım önizlemesi. Kural sunucudaki alicilar.dagitim_plani ile aynı.
//  - işaretli her grup sadece kendi iş kollarındaki kalemleri alır,
//  - kişiye özel eklenen adres seçili bütün kalemleri alır,
//  - bir kişi birden çok yerden gelse de tek mail alır, her kişiye ayrı mail gider (To'da sadece kendisi).
// Önizleme, aynı kalemleri alan kişileri bir arada gösterir (`PlanliMail` yani bir içerik, birden çok kişi).
// Asıl plan onay anında sunucuda hesaplanır, burası sadece onaylayıcıya ne olacağını gösterir.
import { Kalem, RaporGrubu } from './types/api';

// Onaylayıcının elle eklediği adreslerin grup adı.
export const KISIYE_OZEL = 'Kişiye özel';

// Önizlemedeki tek bir mail, alıcıları ve içindeki kalem numaraları.
export interface PlanliMail {
  alicilar: string[];
  kalemNolari: number[];
}

// Grubun alacağı kalemler, kalemin iş kollarından biri grubun iş kollarında varsa.
export function grubunKalemleri(grup: RaporGrubu, kalemler: Kalem[]): Kalem[] {
  return kalemler.filter((k) => k.is_kollari.some((ik) => grup.is_kollari.includes(ik)));
}

// Dağıtım önizlemesi, kim hangi kalemleri alacak (sunucudaki hesabın aynısı).
export function dagitimPlani(kalemler: Kalem[], gruplar: RaporGrubu[], ekAdresler: string[]): PlanliMail[] {
  // Her adres ve alacağı kalem numaraları.
  const adresKalemleri = new Map<string, Set<number>>();
  // Bir adrese kalem numaraları ekleyen küçük yardımcı.
  const ekle = (adres: string, nolar: number[]) => {
    const kume = adresKalemleri.get(adres) ?? new Set<number>();
    nolar.forEach((n) => kume.add(n));
    adresKalemleri.set(adres, kume);
  };
  // Her grubun kalemlerini, grubun her adresine ekle.
  for (const grup of gruplar) {
    const nolar = grubunKalemleri(grup, kalemler).map((k) => k.no);
    if (nolar.length > 0) grup.adresler.forEach((a) => ekle(a, nolar));
  }
  // Kişiye özel adresler bütün kalemleri alır.
  ekAdresler.forEach((a) => ekle(a, kalemler.map((k) => k.no)));

  // Aynı kalem kümesini alan adresleri önizlemede bir araya topla (gönderimde yine kişi başı ayrı mail).
  const paketler = new Map<string, PlanliMail>();
  for (const [adres, kume] of adresKalemleri) {
    const nolar = [...kume].sort((x, y) => x - y);
    const anahtar = nolar.join(',');
    const mail = paketler.get(anahtar) ?? { alicilar: [], kalemNolari: nolar };
    mail.alicilar.push(adres);
    paketler.set(anahtar, mail);
  }
  return [...paketler.values()];
}

// Kişiye özel adres kutusu için basit biçim kontrolü, asıl doğrulama sunucuda.
// Adres biçimi kontrolü, "bir şey@bir şey.bir şey".
export function epostaGecerliMi(adres: string): boolean {
  return /^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$/.test(adres);
}

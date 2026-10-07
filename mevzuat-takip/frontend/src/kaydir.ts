// Sayfanın üstündeki başarı/hata mesajı görünsün diye (liste uzunsa kullanıcı aşağıda olabilir).
// Sayfanın ana alanını (main) yumuşakça en üste kaydırır.
export function basaKaydir() {
  document.querySelector('main')?.scrollTo({ top: 0, behavior: 'smooth' });
}

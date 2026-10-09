"""API dokümanı: grupların sırası ve panel adreslerinin başlığı, açıklaması, yetkisi, hata cevapları.

Panelin kendi arayüzü bu adresleri kullanır. Portala açılanlar web/portal.py'de /api/v1 altına da eklenir, başlık ve
açıklamaları buradan alır. Açıklamalar
koddan ayrı burada durur, uç nokta değişince buradaki satır da güncellenir (test, bütün uç noktaların belgelendiğini kontrol eder).
"""

from fastapi.routing import APIRoute

# Dokümandaki grupların sırası ve açıklamaları.
ETIKETLER = [
    {"name": "Mevzuat", "description": "Mevzuat arama ve detayı (/api/v1)"},
    {"name": "Kategoriler", "description": "Konular ve kaynakların listesi (/api/v1)"},
    {"name": "Raporlar", "description": "Raporların okunması (/api/v1)"},
    {"name": "Raporlar ve onay", "description": "Onay, ret, ek gönderim (/api/v1)"},
    {"name": "Tarama", "description": "Tarama saatleri ve şimdi tara (/api/v1)"},
    {"name": "Kaynaklar", "description": "Taranan sitelerin yönetimi (/api/v1, geçmiş ve geri alma panelin)"},
    {"name": "Konular", "description": "Başlık filtresinin konuları ve anahtar kelimeleri (/api/v1, geçmiş ve geri alma panelin)"},
    {"name": "Alıcı grupları", "description": "Raporların kime gideceği (/api/v1)"},
    {"name": "Ayarlar", "description": "Mail sunucusu, panel adresi, saklama süreleri (panelin, sürümsüz)"},
    {"name": "Kullanıcılar", "description": "Panel kullanıcıları (panelin, sürümsüz)"},
    {"name": "Denetim kaydı", "description": "Kim, ne zaman, ne yaptı (panelin, sürümsüz)"},
    {"name": "Menü", "description": "Panel menüsü (panelin, sürümsüz)"},
]

# Dokümanda gösterilmeyen, sadece panel arayüzünün kullandığı adresler. API anahtarıyla gerekmezler (giriş, iki adımlı
# doğrulama, parola) ya da API anahtarıyla kullanılamazlar (anahtar yönetimi). Raporlar /api/v1/raporlar'dan okunur.
GIZLI = {
    ("GET", "/api/oturum"), ("POST", "/api/giris"), ("GET", "/api/giris/mfa-kurulum"), ("GET", "/api/giris/mfa-qr.svg"),
    ("POST", "/api/giris/mfa-kurulum"), ("POST", "/api/giris/kod"), ("POST", "/api/cikis"), ("POST", "/api/parolam"),
    ("POST", "/api/parola-linki/kontrol"), ("POST", "/api/parola-linki/kullan"),
    ("GET", "/api/api-anahtarlari"), ("POST", "/api/api-anahtarlari"), ("POST", "/api/api-anahtarlari/{anahtar_id}/iptal"),
    ("GET", "/api/raporlar"), ("GET", "/api/raporlar/{rapor_id}"),
    # Konu ve kaynak listeleri /api/v1/konular ve /api/v1/kaynaklar'dan okunur.
    ("GET", "/api/kaynaklar"), ("GET", "/api/konular"),
}

# Başarılı cevap örnekleri (deneme sisteminin gerçek cevaplarından, kısaltılmış).
_MESAJ = {"tur": "basari", "mesaj": "Onaylandı ve 2 adrese gönderildi."}
_GRUP = {"id": 1, "ad": "Kuyum Ekibi", "is_kollari": ["Kuyum", "Ortak"], "adresler": ["kuyum@firma.com.tr"], "aktif": True,
         "guncellendi": "2026-10-08T14:18"}
_ZAMANLAMA = {"saatler": ["06:30", "18:00"], "surum": 1, "zamanlayici_calisiyor": True, "son_nabiz": "2026-10-08T14:20",
              "durum": "bekliyor", "sonraki": "2026-10-08T18:04"}
_TARAMA = {"istek": {"id": 3, "durum": "BEKLIYOR", "istendi": "2026-10-08T14:25"},
           "son_calismalar": [{"id": 12, "baslangic": "2026-10-08T06:34", "bitis": "2026-10-08T06:35", "durum": "BASARILI",
                               "yeni": {"resmi_gazete": 14}, "yeni_toplam": 14, "hata": None}],
           "zamanlama": _ZAMANLAMA}
_KAYNAK = {"ad": "resmi_gazete", "tip": "resmi_gazete", "etiket": "Resmî Gazete", "ayarlar": {}, "varsayilan_konular": [],
           "aktif": True, "kaldirildi": False, "surum": 1, "tip_etiketi": "Resmî Gazete", "checkpoint": "2026-10-08",
           "son_basarili": "2026-10-08T06:35", "toplam_kayit": 412, "ilgili_kayit": 37, "son_hata": None,
           "son_calisma": "2026-10-08T06:34", "guncelleme": "2026-10-03T10:00"}
_KONU = {"id": 1, "ad": "Kıymetli madenler ve kuyumculuk", "is_kollari": ["Kuyum", "Döviz/Altın"],
         "kelimeler": ["altın", "kıymetli maden", "kuyum"], "haric": ["altında", "altıncı"], "dislanan": [],
         "aciklama": "Kuyum işletmelerini doğrudan etkiler.", "aktif": True, "eski_adlar": [], "surum": 2,
         "guncelleme": "2026-10-08T14:18", "eslesme_90": 9, "kullanim": {"kaynaklar": [], "izlenen": True}}
_AYAR = {"ad": "smtp_host", "grup": "Mail sunucusu", "etiket": "SMTP sunucusu", "aciklama": "Ör. smtp.gmail.com.",
         "tur": "metin", "en_az": None, "en_cok": None, "kaynak": ".env", "deger": "smtp.gmail.com"}
_KULLANICI = {"id": 1, "ad": "Ayşe Onaylayıcı", "eposta": "onay@firma.com.tr", "rol": "onaylayici", "aktif": True,
              "son_giris": "2026-10-08T14:24", "olusturuldu": "2026-10-01T10:00", "silinecek": None, "kilitli": False,
              "davet_bekliyor": False}
ORNEK = {
    ("POST", "/api/raporlar/{rapor_id}/karar"): _MESAJ,
    ("POST", "/api/raporlar/{rapor_id}/ek-gonderim"): {"tur": "basari", "mesaj": "Ek gönderim hazırlandı ve 1 adrese gönderildi."},
    ("GET", "/api/gruplar"): {"gruplar": [_GRUP], "is_kollari": ["Döviz/Altın", "Kuyum", "Ortak", "Oto kiralama"],
                              "kapsanmayan": ["Oto kiralama"]},
    ("POST", "/api/gruplar"): _GRUP,
    ("PUT", "/api/gruplar/{grup_id}"): _GRUP,
    ("GET", "/api/zamanlama"): _ZAMANLAMA,
    ("PUT", "/api/zamanlama"): _ZAMANLAMA,
    ("GET", "/api/tarama/durum"): _TARAMA,
    ("POST", "/api/tarama"): _TARAMA,
    ("GET", "/api/kaynaklar"): {"kaynaklar": [_KAYNAK]},
    ("POST", "/api/kaynaklar"): _KAYNAK,
    ("PUT", "/api/kaynaklar/{ad}"): _KAYNAK,
    ("GET", "/api/konular"): {"konular": [_KONU], "gun": 90, "is_kolu_secenekleri": ["Döviz/Altın", "Kuyum", "Ortak"]},
    ("POST", "/api/konular"): _KONU,
    ("PUT", "/api/konular/{konu_id}"): _KONU,
    ("GET", "/api/ayarlar"): {"alanlar": [_AYAR], "surum": 3},
    ("PUT", "/api/ayarlar"): {"alanlar": [_AYAR], "surum": 4, "mesaj": {"tur": "basari", "mesaj": "2 ayar kaydedildi."}},
    ("POST", "/api/ayarlar/mail-dene"): {"tur": "basari", "mesaj": "Deneme maili bilgi-islem@firma.com.tr adresine gönderildi."},
    ("GET", "/api/kullanicilar"): {"kullanicilar": [_KULLANICI], "panel_adresi_var": True, "pasif_silme_gun": 30},
    ("POST", "/api/kullanicilar"): {"mesaj": {"tur": "basari", "mesaj": "yeni@firma.com.tr eklendi; davet maili gönderildi."},
                                    "kullanicilar": [_KULLANICI]},
    ("GET", "/api/denetim"): {"kayitlar": [{"id": 82, "zaman": "2026-10-08T16:14", "kullanici_id": 13,
                                            "kullanici": "API: Portal", "islem": "onay",
                                            "detay": {"rapor_id": 12, "dahil": [101, 102]}, "ip": "10.0.0.20"}],
                              "islemler": ["onay", "api_istegi"], "kullanicilar": [{"id": 13, "ad": "API: Portal"}]},
    ("GET", "/api/menu"): [{"anahtar": "raporlar", "etiket": "Onay Kuyruğu", "aciklama": "Mevzuat raporları",
                            "ikon": "FileCheck2", "adres": None}],
}


def _hata(aciklama: str, ornek: str) -> dict:
    return {"description": aciklama, "content": {"application/json": {"example": {"detail": ornek}}}}


H401 = {401: _hata("API anahtarı yok ya da geçersiz", "Giriş yapmanız gerekiyor.")}
H403 = {403: _hata("Rolün bu işleme yetkisi yok", "Bu işlem için yetkiniz yok.")}
H404 = {404: _hata("Kayıt yok", "Rapor bulunamadı.")}
H409 = {409: _hata("Kayıt bu arada değişti ya da işlem zaten yapıldı", "Bu rapor için zaten karar verilmiş.")}


def H400(ornek: str) -> dict:
    return {400: _hata("İş kuralına uymayan istek, sebebi detail alanında", ornek)}


HERKES = "Giriş yapmış herkes ya da her API anahtarı."
ONAY = "Onaylayıcı ya da tam yetki."
GRUP = "Onaylayıcı, yönetici ya da tam yetki."
AYAR = "Onaylayıcı, yönetici ya da tam yetki."
YONETICI = "Yönetici ya da tam yetki."
GIRIS_YOK = "Giriş gerekmez."

# (yöntem, yol) → (başlık, açıklama, yetki, hata cevapları)
BELGE: dict[tuple[str, str], tuple[str, str, str, dict]] = {
    # --- oturum ve giriş (panel arayüzü için, API anahtarıyla gerekmez)
    ("GET", "/api/oturum"): ("Oturum bilgisi", "Giriş yapan kullanıcı ve yetkileri, iki adımlı doğrulama adımı, CSRF anahtarı. "
                             "Giriş yoksa `kullanici` null döner.", GIRIS_YOK, {}),
    ("POST", "/api/giris"): ("Giriş", "E-posta ve parolayla giriş. `sonraki` alanı `panel`, `kod` (doğrulama kodu girilecek) ya da "
                             "`kurulum` (iki adımlı doğrulama kurulacak) olur. 5 hatalı denemede hesap 15 dakika kilitlenir.",
                             GIRIS_YOK, H400("E-posta veya parola hatalı ya da hesap geçici olarak kilitli.")),
    ("GET", "/api/giris/mfa-kurulum"): ("Doğrulama kurulum bilgisi", "İki adımlı doğrulamayı kuracak kullanıcı için gizli anahtar "
                                        "(elle girmek isteyenler için).", "Parolası doğrulanmış, kurulum bekleyen kullanıcı.", H401),
    ("GET", "/api/giris/mfa-qr.svg"): ("Doğrulama QR kodu", "Telefondaki doğrulayıcı uygulamaya okutulacak QR kodu (SVG).",
                                       "Parolası doğrulanmış, kurulum bekleyen kullanıcı.", H401),
    ("POST", "/api/giris/mfa-kurulum"): ("Doğrulama kurulumunu tamamla", "Uygulamadaki ilk kod girilir, doğruysa oturum açılır.",
                                         "Parolası doğrulanmış, kurulum bekleyen kullanıcı.", {**H401, **H400("Kod hatalı.")}),
    ("POST", "/api/giris/kod"): ("Doğrulama kodu", "İki adımlı doğrulaması kurulu kullanıcının 6 haneli kodu, doğruysa oturum açılır.",
                                 "Parolası doğrulanmış kullanıcı.", {**H401, **H400("Kod hatalı.")}),
    ("POST", "/api/cikis"): ("Çıkış", "Oturumu kapatır.", GIRIS_YOK, {}),
    ("POST", "/api/parolam"): ("Parolamı değiştir", "Kendi parolasını değiştirir, en az 12 karakter. Diğer açık oturumlar kapanır.",
                               "Giriş yapmış kullanıcı.", {**H401, **H400("Mevcut parola hatalı.")}),
    ("POST", "/api/parola-linki/kontrol"): ("Parola linkini kontrol et", "Davet ya da sıfırlama mailindeki link hâlâ geçerli mi.",
                                           GIRIS_YOK, H400("Link geçersiz ya da süresi dolmuş.")),
    ("POST", "/api/parola-linki/kullan"): ("Parola linkiyle parola belirle", "Davet ya da sıfırlama linkiyle yeni parola belirlenir, "
                                          "link tek kullanımlıktır.", GIRIS_YOK, H400("Link geçersiz ya da süresi dolmuş.")),
    ("GET", "/api/menu"): ("Menü", "Sol menü, veritabanından (`menu_ogeleri`). Sadece istekte bulunanın yetkisine uyan aktif öğeler, "
                           "sırasıyla. `adres` doluysa öğe bağlantıdır.", HERKES, H401),
    # --- raporlar
    ("GET", "/api/raporlar"): ("Rapor listesi", "Onay bekleyenler (`bekleyenler`) ve son 50 karar verilmiş rapor (`gecmis`).",
                               HERKES, H401),
    ("GET", "/api/raporlar/{rapor_id}"): ("Rapor detayı", "Raporun kalemleri (özet, yürürlük, eşleşen konular, metin, resmî link), "
                                          "aktif alıcı grupları ve onaydan sonra dağıtım mailleri (`gonderimler`).",
                                          HERKES, {**H401, **H404}),
    ("POST", "/api/raporlar/{rapor_id}/karar"): (
        "Raporu onayla ya da reddet",
        "Onayda `dahil` gönderilecek kalem numaraları (kalemin `id`'si), `gruplar` alıcı grubu numaraları (null ise iş koluna uyan "
        "bütün aktif gruplar), `ek_adresler` kişiye özel adresler. Onaydan hemen sonra mailler gönderilir, cevapta sonuç yazar. "
        "Retde `notu` zorunlu. Aynı rapora ikinci karar 409 alır.",
        ONAY, {**H401, **H403, **H404, **H409, **H400("En az bir kalem seçilmeli. Hiçbiri gönderilmeyecekse raporu reddedin.")}),
    ("POST", "/api/raporlar/{rapor_id}/ek-gonderim"): (
        "Ek gönderim", "Onaylanmış ya da gönderilmiş rapordan seçilen kalemleri başka gruplara ya da kişilere gönderir "
        "(ilk onayda seçilmeyenler dahil). Önceki alıcılara tekrar mail gitmez. `notu` sadece bu maillerde görünür.",
        ONAY, {**H401, **H403, **H404, **H409, **H400("Seçilen kalemler hiçbir alıcı grubuna gitmiyor.")}),
    # --- alıcı grupları
    ("GET", "/api/gruplar"): ("Alıcı grupları", "Raporların kime gideceği. Her grup kendi iş kollarındaki kalemleri alır.",
                              GRUP, {**H401, **H403}),
    ("POST", "/api/gruplar"): ("Alıcı grubu ekle", "Ad, iş kolları ve e-posta adresleri.", GRUP,
                               {**H401, **H403, **H400("Geçersiz e-posta adresi: ...")}),
    ("PUT", "/api/gruplar/{grup_id}"): ("Alıcı grubunu düzenle", "Grubun bütün alanları yeniden gönderilir, pasifleştirmek için "
                                        "`aktif: false`.", GRUP, {**H401, **H403, **H404, **H400("Geçersiz e-posta adresi: ...")}),
    # --- kaynaklar
    ("GET", "/api/kaynak-tipleri"): ("Kaynak tipleri", "Eklenebilecek kaynak tipleri ve her tipin form alanları "
                                     "(WordPress, düz HTML, RSS ...).", AYAR, {**H401, **H403}),
    ("GET", "/api/kaynaklar"): ("Kaynaklar", "Taranan siteler, son tarama durumu, kayıt sayıları.", AYAR, {**H401, **H403}),
    ("POST", "/api/kaynaklar"): ("Kaynak ekle", "Yeni kaynak. Kod adı etiketten üretilir. Adres sadece https olabilir, iç ağ "
                                 "adresleri reddedilir.", AYAR, {**H401, **H403, **H400("İç ağ / özel adrese bağlantı engellendi (ic.firma.local → 10.0.0.5).")}),
    ("POST", "/api/kaynaklar/dene"): ("Kaynağı kaydetmeden dene", "Son 7 günde bulunacak duyuruları ve konulara uyanları gösterir, "
                                      "hiçbir şey kaydetmez.", AYAR, {**H401, **H403, **H400("deneme: listede hiç duyuru okunamadı; seçicileri kontrol edin")}),
    ("POST", "/api/kaynaklar/bul"): ("Adresten kaynak bul", "Bir sitenin adresinden uygun okuma yolunu (WordPress, RSS, HTML listesi) "
                                     "kendisi bulur, kaydetmez.", AYAR, {**H401, **H403}),
    ("PUT", "/api/kaynaklar/{ad}"): ("Kaynağı düzenle", "`surum` zorunlu.", AYAR, {**H401, **H403, **H404, **H409}),
    ("POST", "/api/kaynaklar/{ad}/kaldir"): ("Kaynağı kaldır", "Kaynak taranmaz, silinmez, geri getirilebilir.", AYAR,
                                            {**H401, **H403, **H404, **H409}),
    ("POST", "/api/kaynaklar/{ad}/geri-getir"): ("Kaldırılan kaynağı geri getir", "", AYAR, {**H401, **H403, **H404, **H409}),
    ("GET", "/api/kaynaklar/{ad}/gecmis"): ("Kaynağın değişiklik geçmişi", "Kim, ne zaman, neyi değiştirdi (önce ve sonra).",
                                            AYAR, {**H401, **H403, **H404}),
    ("POST", "/api/kaynaklar/{ad}/geri-al"): ("Kaynağı önceki hale döndür", "`denetim_id` değişikliğinden önceki hale döner.",
                                             YONETICI, {**H401, **H403, **H404, **H409}),
    # --- konular
    ("GET", "/api/konular"): ("Konular", "Başlık filtresinin konuları: anahtar kelimeler, hariç tutulan ifadeler, iş kolları, açıklama.",
                              AYAR, {**H401, **H403}),
    ("POST", "/api/konular/onizleme"): ("Konu önizleme", "Kaydetmeden önce son 90 günün başlıklarında hangilerinin yeni eşleşeceğini "
                                        "ya da düşeceğini gösterir.", AYAR, {**H401, **H403, **H400("En az bir anahtar kelime girin.")}),
    ("POST", "/api/konular"): ("Konu ekle", "Ad, iş kolları, anahtar kelimeler, hariç tutulan ifadeler ve açıklama.", AYAR,
                              {**H401, **H403, **H400("En az bir anahtar kelime girin.")}),
    ("PUT", "/api/konular/{konu_id}"): ("Konuyu düzenle", "`surum` zorunlu.", AYAR, {**H401, **H403, **H404, **H409}),
    ("POST", "/api/konular/{konu_id}/pasif"): ("Konuyu pasifleştir", "Konu süzmede kullanılmaz, silinmez.", AYAR,
                                              {**H401, **H403, **H404, **H409}),
    ("POST", "/api/konular/{konu_id}/aktif"): ("Konuyu yeniden aç", "", AYAR, {**H401, **H403, **H404, **H409}),
    ("GET", "/api/konular/{konu_id}/gecmis"): ("Konunun değişiklik geçmişi", "", AYAR, {**H401, **H403, **H404}),
    ("POST", "/api/konular/{konu_id}/geri-al"): ("Konuyu önceki hale döndür", "", YONETICI, {**H401, **H403, **H404, **H409}),
    # --- tarama
    ("GET", "/api/zamanlama"): ("Tarama saatleri ve zamanlayıcı", "Planlı tarama saatleri (Türkiye saati), zamanlayıcı çalışıyor mu, "
                                "sonraki tarama. Planlı tarama saatten sonraki 10 dakika içinde başlar.", HERKES, H401),
    ("PUT", "/api/zamanlama"): ("Tarama saatlerini değiştir", "En az 1, en fazla 6 saat, aralarında en az 1 saat. `surum` zorunlu.",
                                AYAR, {**H401, **H403, **H409, **H400("Tarama saatleri arasında en az 1 saat olmalı.")}),
    ("GET", "/api/tarama/durum"): ("Şimdi tara isteğinin durumu", "Son isteğin durumu: BEKLIYOR, CALISIYOR, BITTI ya da HATALI.",
                                   HERKES, H401),
    ("POST", "/api/tarama"): ("Şimdi tara", "Taramayı hemen başlatır (zamanlayıcı 30 saniye içinde alır). Bir tarama sıradayken ya da "
                              "sürerken ikinci istek reddedilir.", AYAR,
                              {**H401, **H403, 409: _hata("Tarama zaten sırada ya da sürüyor, ya da zamanlayıcı çalışmıyor",
                                                                 "Bir tarama isteği zaten sırada ya da çalışıyor.")}),
    # --- kullanıcılar ve denetim
    ("GET", "/api/kullanicilar"): ("Kullanıcılar", "Panel kullanıcıları, rolleri, son girişleri, durumları.", YONETICI,
                                   {**H401, **H403}),
    ("POST", "/api/kullanicilar"): ("Kullanıcı ekle", "Kişiye davet maili gider, parolasını linkle kendisi belirler.", YONETICI,
                                    {**H401, **H403, **H400("x@firma.com zaten kayıtlı.")}),
    ("POST", "/api/kullanicilar/{kullanici_id}/parola-linki"): ("Parola linki gönder", "Davet (hiç giriş yapmamışsa) ya da sıfırlama "
                                                                "linki maili.", YONETICI, {**H401, **H403, **H404}),
    ("POST", "/api/kullanicilar/{kullanici_id}/aktiflik"): ("Kullanıcıyı pasifleştir ya da aç", "Pasif kullanıcı giremez, açık "
                                                            "oturumu kapanır. Son aktif yönetici pasifleştirilemez.", YONETICI,
                                                            {**H401, **H403, **H404, **H400("Son aktif yönetici pasifleştirilemez.")}),
    ("GET", "/api/denetim"): ("Denetim kaydı", "En yeni 100 kayıt (kim, ne zaman, hangi IP, ne yaptı). `islem` ve `kullanici_id` ile "
                              "süzülür, `once` ile daha eskiler alınır.", YONETICI, {**H401, **H403}),
    # --- ayarlar ve API anahtarları
    ("GET", "/api/ayarlar"): ("Ayarlar", "Mail sunucusu, panel adresi, uyarı adresleri, saklama süreleri. Her ayarın değeri ve nereden "
                              "geldiği (`panel`, `.env`, `varsayilan`). Mail şifresi hiç gönderilmez.", YONETICI, {**H401, **H403}),
    ("PUT", "/api/ayarlar"): ("Ayarları kaydet", "`degisiklikler` içinde sadece değişen ayarlar gönderilir, değeri null olan ayar "
                              "sıfırlanır (.env'deki değere döner). Hemen geçerli olur.", YONETICI,
                              {**H401, **H403, **H409, **H400("'SMTP portu' 1 ile 65535 arasında olmalı.")}),
    ("POST", "/api/ayarlar/mail-dene"): ("Deneme maili", "Kayıtlı mail ayarıyla tek bir adrese deneme maili atar.", YONETICI,
                                         {**H401, **H403, **H400("Tek bir geçerli e-posta adresi yazın.")}),
    ("GET", "/api/api-anahtarlari"): ("API anahtarları", "Anahtarların listesi (anahtarın kendisi değil ilk harfleri), durum, son "
                                      "kullanım.", "Panel oturumuyla yönetici. API anahtarıyla erişilemez.", {**H401, **H403}),
    ("POST", "/api/api-anahtarlari"): ("API anahtarı üret", "Anahtar sadece bu cevapta bir kez döner. `gun` boşsa süresiz.",
                                       "Panel oturumuyla yönetici. API anahtarıyla erişilemez.", {**H401, **H403}),
    ("POST", "/api/api-anahtarlari/{anahtar_id}/iptal"): ("API anahtarını iptal et", "Anahtar hemen çalışmaz olur.",
                                                          "Panel oturumuyla yönetici. API anahtarıyla erişilemez.",
                                                          {**H401, **H403, **H404}),
}


# Panel router'ının uç noktalarına başlık, açıklama ve hata cevaplarını yazar. Belgesi olmayan uç nokta olursa hata verir.
def belgele(yollar: list, gizli: set[tuple[str, str]] = frozenset()) -> None:
    """gizli, dokümanda /api/v1 altında görünen panel adresleri, burada tekrar gösterilmez."""
    eksik = []
    for yol in yollar:
        if not isinstance(yol, APIRoute) or not yol.include_in_schema:
            continue
        if all((yontem, yol.path) in GIZLI | gizli for yontem in yol.methods):
            yol.include_in_schema = False
            continue
        for yontem in yol.methods:
            belge = BELGE.get((yontem, yol.path))
            if belge is None:
                eksik.append(f"{yontem} {yol.path}")
                continue
            baslik, aciklama, yetki, hatalar = belge
            yol.summary = baslik
            yol.description = f"{aciklama}\n\n**Yetki:** {yetki}".strip()
            yol.responses = {**yol.responses, **hatalar}
            if (ornek := ORNEK.get((yontem, yol.path))) is not None:
                yol.responses[200] = {"description": "Başarılı", "content": {"application/json": {"example": ornek}}}
    if eksik:
        raise RuntimeError(f"Panel API dokümanında belgesi olmayan uç nokta: {', '.join(eksik)} (web/panel_belgesi.py)")

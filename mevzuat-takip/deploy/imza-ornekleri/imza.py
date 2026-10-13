# Mevzuat Takip API'sine imzalı istek, Python 3.9 ve üstü, ek paket gerekmez.
# Örnek, MEVZUAT_ADRES=https://mevzuat.firma.com.tr MEVZUAT_ANAHTAR=mvz_... python imza.py
# Tek istek, python imza.py GET "/api/mevzuat?q=altın&adet=10"
#            python imza.py POST /api/raporlar/12/karar '{"karar": "reddet", "notu": "Kapsam dışı."}'
# Aynı isteği iki kez gönderip ikincisinin reddedildiğini görmek için sona --tekrar eklenir.
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit


# İsteği imzalar, başlıkları döner. Anahtarın kendisi gönderilmez, sadece ilk 10 karakteri.
def imzala(anahtar, yontem, yol, govde=b""):
    parca = urlsplit(yol)
    zaman = str(int(time.time()))
    numara = secrets.token_hex(16)
    metin = "\n".join([yontem, parca.path, parca.query, zaman, numara, hashlib.sha256(govde).hexdigest()])
    imza = hmac.new(anahtar.encode(), metin.encode(), hashlib.sha256).hexdigest()
    return {"X-Anahtar": anahtar[:10], "X-Zaman": zaman, "X-Numara": numara, "X-Imza": imza,
            "Accept": "application/json"}


# İsteği imzalar ve gönderir, cevabı JSON olarak döner. Hata kodunda urllib.error.HTTPError fırlatır.
def istek(adres, anahtar, yontem, yol, veri=None):
    govde = json.dumps(veri).encode() if veri is not None else b""
    basliklar = imzala(anahtar, yontem, yol, govde)
    if veri is not None:
        basliklar["Content-Type"] = "application/json"
    istek_ = urllib.request.Request(adres + yol, data=govde or None, method=yontem, headers=basliklar)
    with urllib.request.urlopen(istek_) as cevap:
        return json.load(cevap)


# Hazır istek gönderir, kodu ve cevabı döner. Aynı başlıklarla tekrar göndermek için kullanılır.
def gonder(adres, yontem, yol, govde, basliklar):
    istek_ = urllib.request.Request(adres + yol, data=govde or None, method=yontem, headers=basliklar)
    try:
        with urllib.request.urlopen(istek_) as cevap:
            return cevap.status, cevap.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# Komut satırından tek istek. Türkçe harfler adreste kodlanır, imza da kodlanmış adresle atılır.
def komut(adres, anahtar, arglar):
    tekrar = "--tekrar" in arglar
    arglar = [a for a in arglar if a != "--tekrar"]
    yontem, yol = arglar[0].upper(), arglar[1]
    # Windows'ta Git Bash /api/... yazısını C:/Program Files/Git/api/... yapar, baştaki fazlalık atılır.
    if re.match(r"[A-Za-z]:/", yol):
        bas = yol.find("/panel/api/")
        yol = yol[bas if bas >= 0 else yol.index("/api/"):]
    parca = urlsplit(yol)
    yol = quote(parca.path, safe="/%") + ("?" + quote(parca.query, safe="=&%+") if parca.query else "")
    govde = json.dumps(json.loads(arglar[2])).encode() if len(arglar) > 2 else b""
    basliklar = imzala(anahtar, yontem, yol, govde)
    if govde:
        basliklar["Content-Type"] = "application/json"
    for _ in range(2 if tekrar else 1):
        kod, metin = gonder(adres, yontem, yol, govde, basliklar)
        try:
            metin = json.dumps(json.loads(metin), ensure_ascii=False, indent=2)
        except ValueError:
            pass
        print(kod)
        print(metin)


if __name__ == "__main__":
    adres, anahtar = os.environ["MEVZUAT_ADRES"].rstrip("/"), os.environ["MEVZUAT_ANAHTAR"]
    if len(sys.argv) > 2:
        komut(adres, anahtar, sys.argv[1:])
        sys.exit()
    liste = istek(adres, anahtar, "GET", "/api/mevzuat?sadece_ilgili=false&adet=3")
    print("Python, mevzuat:", liste["toplam"])
    onizleme = istek(adres, anahtar, "POST", "/api/konular/onizleme",
                     {"ad": "Deneme", "is_kollari": ["Ortak"], "kelimeler": ["vergi"], "haric": [], "dislanan": []})
    print("Python, konu önizleme:", onizleme["taranan"])

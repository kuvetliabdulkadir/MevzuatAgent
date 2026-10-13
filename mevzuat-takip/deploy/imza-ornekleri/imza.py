# Mevzuat Takip API'sine imzalı istek, Python 3.9 ve üstü, ek paket gerekmez.
# Çalıştırma, MEVZUAT_ADRES=https://mevzuat.firma.com.tr MEVZUAT_ANAHTAR=mvz_... python imza.py
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.request
from urllib.parse import urlsplit


# İsteği imzalar ve gönderir. Anahtarın kendisi gönderilmez, sadece ilk 10 karakteri.
def istek(adres, anahtar, yontem, yol, veri=None):
    govde = json.dumps(veri).encode() if veri is not None else b""
    parca = urlsplit(yol)
    zaman = str(int(time.time()))
    numara = secrets.token_hex(16)
    metin = "\n".join([yontem, parca.path, parca.query, zaman, numara, hashlib.sha256(govde).hexdigest()])
    imza = hmac.new(anahtar.encode(), metin.encode(), hashlib.sha256).hexdigest()
    basliklar = {"X-Anahtar": anahtar[:10], "X-Zaman": zaman, "X-Numara": numara, "X-Imza": imza,
                 "Accept": "application/json"}
    if veri is not None:
        basliklar["Content-Type"] = "application/json"
    istek_ = urllib.request.Request(adres + yol, data=govde or None, method=yontem, headers=basliklar)
    with urllib.request.urlopen(istek_) as cevap:
        return json.load(cevap)


if __name__ == "__main__":
    adres, anahtar = os.environ["MEVZUAT_ADRES"], os.environ["MEVZUAT_ANAHTAR"]
    liste = istek(adres, anahtar, "GET", "/api/mevzuat?sadece_ilgili=false&adet=3")
    print("Python, mevzuat:", liste["toplam"])
    onizleme = istek(adres, anahtar, "POST", "/api/konular/onizleme",
                 {"ad": "Deneme", "is_kollari": ["Ortak"], "kelimeler": ["vergi"], "haric": [], "dislanan": []})
    print("Python, konu önizleme:", onizleme["taranan"])

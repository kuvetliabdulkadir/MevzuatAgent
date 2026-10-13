// Mevzuat Takip API'sine imzalı istek, Java 11 ve üstü, ek paket gerekmez.
// Çalıştırma, MEVZUAT_ADRES=https://mevzuat.firma.com.tr MEVZUAT_ANAHTAR=mvz_... java Imza.java
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public class Imza {
    static String hex(byte[] baytlar) {
        StringBuilder sb = new StringBuilder();
        for (byte b : baytlar) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    // İsteği imzalar ve gönderir, cevabı JSON metni olarak döner. Anahtarın kendisi gönderilmez, sadece ilk 10 karakteri.
    static String istek(HttpClient http, String adres, String anahtar, String yontem, String yol, String jsonGovde) throws Exception {
        String govde = jsonGovde == null ? "" : jsonGovde;
        URI parca = URI.create(adres).resolve(yol);
        String sorgu = parca.getRawQuery() == null ? "" : parca.getRawQuery();
        String zaman = String.valueOf(System.currentTimeMillis() / 1000);
        byte[] rastgele = new byte[16];
        new SecureRandom().nextBytes(rastgele);
        String numara = hex(rastgele);
        String govdeOzeti = hex(MessageDigest.getInstance("SHA-256").digest(govde.getBytes(StandardCharsets.UTF_8)));
        String metin = String.join("\n", yontem, parca.getRawPath(), sorgu, zaman, numara, govdeOzeti);
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(anahtar.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        String imza = hex(mac.doFinal(metin.getBytes(StandardCharsets.UTF_8)));
        HttpRequest.Builder istek = HttpRequest.newBuilder(parca)
            .header("X-Anahtar", anahtar.substring(0, 10))
            .header("X-Zaman", zaman)
            .header("X-Numara", numara)
            .header("X-Imza", imza)
            .header("Accept", "application/json");
        if (jsonGovde == null) {
            istek.method(yontem, HttpRequest.BodyPublishers.noBody());
        } else {
            istek.header("Content-Type", "application/json")
                .method(yontem, HttpRequest.BodyPublishers.ofString(govde, StandardCharsets.UTF_8));
        }
        HttpResponse<String> cevap = http.send(istek.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (cevap.statusCode() >= 400) throw new RuntimeException(cevap.statusCode() + " " + cevap.body());
        return cevap.body();
    }

    public static void main(String[] args) throws Exception {
        String adres = System.getenv("MEVZUAT_ADRES");
        String anahtar = System.getenv("MEVZUAT_ANAHTAR");
        HttpClient http = HttpClient.newHttpClient();
        String liste = istek(http, adres, anahtar, "GET", "/api/mevzuat?sadece_ilgili=false&adet=3", null);
        System.out.println("Java, mevzuat: " + liste.replaceAll("(?s).*\"toplam\":(\\d+).*", "$1"));
        String onizleme = istek(http, adres, anahtar, "POST", "/api/konular/onizleme",
            "{\"ad\":\"Deneme\",\"is_kollari\":[\"Ortak\"],\"kelimeler\":[\"vergi\"],\"haric\":[],\"dislanan\":[]}");
        System.out.println("Java, konu önizleme: " + onizleme.replaceAll("(?s).*\"taranan\":(\\d+).*", "$1"));
    }
}

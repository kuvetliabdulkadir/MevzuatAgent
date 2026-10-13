// Mevzuat Takip API'sine imzalı istek, .NET 6 ve üstü, ek paket gerekmez.
// .NET 10 ile tek dosya olarak çalışır, MEVZUAT_ADRES=https://mevzuat.firma.com.tr MEVZUAT_ANAHTAR=mvz_... dotnet run Imza.cs
// Eski sürümlerde MevzuatImza sınıfı projeye kopyalanır.
using System.Security.Cryptography;
using System.Text;

var adres = Environment.GetEnvironmentVariable("MEVZUAT_ADRES")!;
var anahtar = Environment.GetEnvironmentVariable("MEVZUAT_ANAHTAR")!;
using var http = new HttpClient();
var liste = await MevzuatImza.Istek(http, adres, anahtar, "GET", "/api/mevzuat?sadece_ilgili=false&adet=3", null);
Console.WriteLine("C#, mevzuat: " + System.Text.Json.JsonDocument.Parse(liste).RootElement.GetProperty("toplam"));
var onizleme = await MevzuatImza.Istek(http, adres, anahtar, "POST", "/api/konular/onizleme",
    "{\"ad\":\"Deneme\",\"is_kollari\":[\"Ortak\"],\"kelimeler\":[\"vergi\"],\"haric\":[],\"dislanan\":[]}");
Console.WriteLine("C#, konu önizleme: " + System.Text.Json.JsonDocument.Parse(onizleme).RootElement.GetProperty("taranan"));

static class MevzuatImza
{
    static string Hex(byte[] baytlar) => Convert.ToHexString(baytlar).ToLowerInvariant();

    // İsteği imzalar ve gönderir, cevabı JSON metni olarak döner. Anahtarın kendisi gönderilmez, sadece ilk 10 karakteri.
    public static async Task<string> Istek(HttpClient http, string adres, string anahtar, string yontem, string yol, string? jsonGovde)
    {
        var govde = jsonGovde ?? "";
        var parca = new Uri(new Uri(adres), yol);
        var zaman = DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString();
        var numara = Hex(RandomNumberGenerator.GetBytes(16));
        var govdeOzeti = Hex(SHA256.HashData(Encoding.UTF8.GetBytes(govde)));
        var metin = string.Join("\n", yontem, parca.AbsolutePath, parca.Query.TrimStart('?'), zaman, numara, govdeOzeti);
        var imza = Hex(HMACSHA256.HashData(Encoding.UTF8.GetBytes(anahtar), Encoding.UTF8.GetBytes(metin)));
        using var istek = new HttpRequestMessage(new HttpMethod(yontem), parca);
        istek.Headers.Add("X-Anahtar", anahtar[..10]);
        istek.Headers.Add("X-Zaman", zaman);
        istek.Headers.Add("X-Numara", numara);
        istek.Headers.Add("X-Imza", imza);
        if (jsonGovde != null) istek.Content = new StringContent(govde, Encoding.UTF8, "application/json");
        using var cevap = await http.SendAsync(istek);
        var metni = await cevap.Content.ReadAsStringAsync();
        if (!cevap.IsSuccessStatusCode) throw new Exception($"{(int)cevap.StatusCode} {metni}");
        return metni;
    }
}

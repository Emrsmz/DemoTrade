# CTrading Algoritması — Sıfırdan Anlatım

> Bu belge, CTrading sinyal analizcisinin nasıl çalıştığını **amatör birine
> anlatır gibi**, parça parça açıklar. Hem algoritmanın **mekaniğini** hem de
> 90 günlük testlerden **ne öğrendiğimizi** içerir.
>
> ⚠️ **Uyarı:** Bu araç yalnızca eğitim ve bilgilendirme amaçlıdır, yatırım
> tavsiyesi değildir. Program emir göndermez; sadece sinyal basar. Her zaman
> kendi araştırmanı yap.

---

## İçindekiler

1. [En tepeden bakış: bu program ne yapıyor?](#0-en-tepeden-bakış-bu-program-ne-yapıyor)
2. [Ham madde: "mum" (candle) nedir?](#1-ham-madde-mum-candle-nedir)
3. [6 gösterge: programın "danışmanları"](#2-6-gösterge-programın-danışmanları)
4. [ADX Rejim Filtresi — en zekice parça](#3-adx-rejim-filtresi--algoritmanın-en-zekice-parçası)
5. [1 Saatlik Trend Filtresi (HTF)](#4-1-saatlik-trend-filtresi-htf--büyük-resme-bak)
6. [Neden sadece ALIM (long-only)?](#5-neden-sadece-alim-long-only)
7. [Puanlama ve "güç" seviyeleri](#6-puanlama-ve-güç-seviyeleri)
8. [Conviction Gates (kanaat kapıları)](#7-conviction-gates-kanaat-kapıları--testlerle-ayarlanmış-filtreler)
9. [Giriş zamanlaması ve "tazelik"](#8-giriş-zamanlaması-ve-tazelik)
10. [Çıkış mantığı — "kazananı koştur"](#9-çıkış-mantığı--kazananı-koştur)
11. [Baştan sona bir coinin yolculuğu (örnek)](#10-baştan-sona-bir-coinin-yolculuğu-örnek)
12. [EN ÖNEMLİ KISIM: Bu projeden ne öğrendik?](#11-en-önemli-kısım-bu-projeden-ne-öğrendik)
13. [Tek paragrafta özet](#tek-paragrafta-özet)

---

## 0. En tepeden bakış: bu program ne yapıyor?

Bir cümleyle: **Borsadan fiyat verisi çekiyor, 6 tane teknik göstergeyi
koşturuyor, bunların kaç tanesinin "AL" dediğini sayıyor, ve yeterince çok
gösterge hemfikirse sana "şu coini şimdi al" diyor.** İşlemi kendisi yapmıyor —
sadece sinyal basıyor, sen 1-5 dakika sonra elle alıyorsun.

Önemli bir nokta: program **sadece ALIM (long) yapar**. Asla "sat/short aç"
demez. Neden böyle olduğunu aşağıda anlatacağız, ama aklında tut.

Şimdi bunu katman katman açalım.

---

## 1. Ham madde: "mum" (candle) nedir?

Her şey **mumlardan** başlıyor. Bir mum, belirli bir zaman diliminde fiyatın ne
yaptığını özetleyen 5 sayıdır (buna OHLCV denir):

- **O**pen — o dilimin başındaki fiyat
- **H**igh — o dilimde görülen en yüksek fiyat
- **L**ow — en düşük fiyat
- **C**lose — dilimin sonundaki fiyat
- **V**olume — o dilimde el değiştiren miktar (işlem hacmi)

Biz **15 dakikalık** mumlar kullanıyoruz. Yani her mum 15 dakikayı özetler.
Program her coin için son **100 mum** çeker (yaklaşık son 25 saatlik veri).

**Neden 15 dakika, neden 1 dakika değil?** Çünkü kısa zaman dilimleri çok
"gürültülü"dür — fiyat sürekli zıplar, göstergeler sürekli yanlış alarm verir.
15 dakika daha sakindir, sinyaller daha güvenilirdir. Özellikle ADX denen filtre
(birazdan göreceğiz) çok kısa zaman dilimlerinde "kafayı yer" (whipsaw).

Bu veriyi **30 coin** için aynı anda, paralel olarak çekiyoruz (8 iş parçacığı
ile, hızlı olsun diye). Coin listesi sabit: BTC, ETH, BNB, SOL, XRP... gibi en
popüler 30 USDT paritesi.

---

## 2. 6 gösterge: programın "danışmanları"

Şöyle düşün: 6 tane uzman danışmanın var, her biri farklı bir şeye bakıyor. Her
biri ya "AL" der, ya "SAT" der, ya da susar. Program **long-only** olduğu için
sadece "AL" oylarını sayar, "SAT" oylarını çöpe atar.

Bu 6 danışman **iki ayrı okula** ayrılır. Bunu anlamak kritik:

### A) TREND ailesi — "momentumun üstüne atla"
Bunlar şunu der: *"Fiyat yükseliyorsa, yükselmeye devam eder. Trene atla."*
- **MACD**
- **EMA Kesişimi**
- **Hacim Patlaması (VOL)**
- **VWAP**

### B) REVERSION ailesi — "aşırılığı tersine oyna"
Bunlar şunu der: *"Fiyat çok düştüyse, geri sekecek. Ucuzdan al."*
- **RSI**
- **Bollinger Bantları (BB)**

Bu iki okul **birbirinin tam zıttıdır**. Birazdan göreceğin ADX filtresi tam da
bu çelişkiyi çözmek için var. Önce danışmanları tek tek tanıyalım.

### 2.1. RSI (Relative Strength Index) — "aşırı mı satıldı?"
**Ailesi: REVERSION**

0-100 arası bir sayı üretir, son fiyat hareketlerinin hızını ölçer.
- RSI **30'un altı** → "aşırı satım" (satıcılar yoruldu, sekme gelebilir) → **AL**
- RSI **70'in üstü** → "aşırı alım" (alıcılar yoruldu, geri çekilme gelebilir) → SAT

Kodda: `RSI_PERIOD = 14`, `RSI_OVERSOLD = 30`, `RSI_OVERBOUGHT = 70`. Son 14 muma bakar.

> **Dikkat:** Canlı sistemde RSI'ın AL oyu **kapalı** (aşağıdaki "conviction
> gates" kısmında açıklanıyor — test bu oyların para kaybettirdiğini gösterdi).

### 2.2. MACD (Moving Average Convergence Divergence) — "momentum dönüyor mu?"
**Ailesi: TREND**

İki ortalama karşılaştırır: hızlı (12 mum) ve yavaş (26 mum). Aralarındaki fark
"MACD çizgisi"dir. Bunun 9 mumluk ortalaması da "sinyal çizgisi"dir.
- MACD çizgisi sinyal çizgisini **yukarı keserse** → momentum yukarı → **AL**
- Aşağı keserse → SAT

Kodda `12, 26, 9` parametreleri. Ayrıca histogram (iki çizginin farkı) yeterince
büyük olmalı (bkz. conviction gates).

### 2.3. Bollinger Bantları (BB) — "fiyat gerildi mi?"
**Ailesi: REVERSION**

20 mumluk ortalamanın etrafına, oynaklığa göre genişleyip daralan bir "tüp"
çizer (üst + alt bant, ortalamadan ±2 standart sapma).
- Fiyat **alt banda değerse** → aşağı gerilmiş → **AL** (sekme bekle)
- **Üst banda değerse** → yukarı gerilmiş → SAT

Kodda `BB_PERIOD = 20`, `BB_STD = 2`. RSI gibi AL oyu canlı sistemde **kapalı**.

### 2.4. EMA Kesişimi (9/21) — "yeni trend başladı mı?"
**Ailesi: TREND**

İki hareketli ortalama: hızlı (9 mum) ve yavaş (21 mum). EMA, son fiyatlara daha
çok ağırlık veren bir ortalamadır.
- Hızlı EMA yavaşı **yukarı keserse** → yeni yükseliş başlıyor → **AL**
- Aşağı keserse → SAT

İki EMA arası ne kadar açıksa, trend o kadar net demektir.

### 2.5. Hacim Patlaması (VOL) — "bu harekete inanan var mı?"
**Ailesi: TREND**

Son 20 mumun ortalama hacmine bakar. Şu anki mumun hacmi bu ortalamanın belli
bir katından fazlaysa "patlama" var demektir.
- Hacim patlaması + **yeşil mum** (fiyat yükseldi) → güçlü alım → **AL**
- Hacim patlaması + **kırmızı mum** (fiyat düştü) → güçlü satım → SAT

**Neden işe yarar?** Ani hacim, bir hareketin arkasında gerçek bir kararlılık
olduğunu gösterir. Kodda eşik `VOL_MULTIPLIER = 3.8`: hacim ortalamanın **3.8
katından** fazla olmalı (başlangıçta 2.0'dı, testlerde 3.8'e yükselttik — daha
az ama daha kaliteli sinyal).

### 2.6. VWAP (Volume Weighted Average Price) — "çoğunluk hangi fiyattan aldı?"
**Ailesi: TREND**

VWAP, hacme göre ağırlıklandırılmış ortalama fiyattır — *"katılımcıların çoğu
gerçekte hangi fiyattan işlem yaptı"* seviyesidir.
- Fiyat VWAP'ın **üstündeyse** → alıcılar kontrolde → **AL** eğilimi
- Altındaysa → satıcılar kontrolde → SAT

Son 20 muma bakar. Önemli ayar: bir AL oyu için fiyatın VWAP'ın **en az %1.5
üstünde** olması gerekir (`VWAP_LONG_MIN_DIST_PCT = 1.5`). "Azıcık üstünde"
yetmez, net bir şekilde üstünde olmalı.

---

## 3. ADX Rejim Filtresi — algoritmanın en zekice parçası

Hatırla: TREND ailesi ile REVERSION ailesi **birbirinin zıttı**.

Düşün: güçlü bir **düşüş trendi** var. RSI "aşırı satıldı, AL!" diyor (reversion
mantığı). Ama bu, **düşen bıçağı tutmak** gibi — fiyat düşmeye devam eder ve sen
kaybedersin. İki aileyi körü körüne karıştırırsan sürekli kendi kendinle
çelişirsin.

**Çözüm: ADX (Average Directional Index).**

ADX, trendin **yönünü değil, gücünü** ölçer (0-100 arası). "Şu an piyasa trendde
mi, yoksa yatay mı gidiyor?" sorusuna cevap verir. Biz onu bir **hakem** gibi
kullanıyoruz: hangi ailenin oy kullanabileceğine ADX karar verir.

| ADX değeri | Rejim | Kime güveniriz |
|---|---|---|
| **≥ 25** | TREND (piyasa trendde) | Sadece **TREND ailesi** (MACD, EMA, VOL, VWAP). Reversion susturulur. |
| **< 20** | RANGE (piyasa yatay) | Sadece **REVERSION ailesi** (RSI, BB). Trend susturulur. |
| **20-25** | NEUTRAL (kararsız) | Hepsi oy kullanır |

**Kritik nokta:** ADX'in kendisi "AL/SAT" oyu vermez. Sadece *hangi danışmanların
konuşma hakkı olduğunu* seçer. Trend güçlüyse trend takipçilerini, piyasa
yataysa tersine oynayanları dinleriz. Böylece sistem kendi kendiyle çelişmeyi
bırakır.

### +DI / −DI: yön teyidi
ADX'in iki yardımcı çizgisi vardır: **+DI** (yukarı hareket gücü) ve **−DI**
(aşağı hareket gücü). TREND rejimindeyken ekstra bir kontrol var: bir AL sinyali
için **+DI ≥ −DI** olmalı. Yani "trend yukarı baskın" demeli. −DI üstteyse
(aşağı baskın), AL reddedilir. Bu, trende karşı alım yapmayı engeller.

---

## 4. 1 Saatlik Trend Filtresi (HTF) — "büyük resme bak"

15 dakikalık bir sinyal güzel görünebilir ama **büyük resim** ters olabilir.
Düşün: 15 dakikalıkta küçük bir sıçrama var ama 1 saatlik grafikte coin açık bir
şekilde düşüşte. Bu sıçramayı almak genelde kaybettirir.

Bu yüzden bir 15dk sinyali tüm testleri geçtikten sonra, program o coin için **1
saatlik grafiği** de çeker ve trendine bakar (1 saatlik 20 ve 50 EMA'sından):
- 1h trend **YUKARI veya NÖTR** → AL sinyaline izin var
- 1h trend açıkça **AŞAĞI** → AL **iptal edilir**

Kodda: `htf_allows` fonksiyonu. Bu, "trende karşı işlem" denen, trade
kayıtlarında en çok para kaybettiren hatayı kesip atmak için eklendi.

Akıllı bir detay: bu 1 saatlik kontrol sadece *zaten sinyal üretmiş* birkaç coin
için yapılır (30 coinin hepsi için değil), böylece fazladan çok az istek atılır.

---

## 5. Neden sadece ALIM (long-only)?

Program asla short açmaz. İki neden var:
1. **90 günlük testte short'lar net zarardı** — para kaybettiriyorlardı.
2. **Spot piyasada gerçekten short yapamazsın** zaten.

Bu yüzden `analyze_symbol` fonksiyonu sadece "BUY" döndürür. Açıkça düşüşte olan
bir coin hiç sinyal üretmez — sessizce atlanır.

---

## 6. Puanlama ve "güç" seviyeleri

ADX hangi ailenin oy kullanacağına karar verdikten sonra, o ailedeki **uygun
(eligible)** göstergelerden kaç tanesi "AL" dedi diye sayıyoruz. Bu sayı =
**score** (puan).

Sonra bu puanı bir **güç seviyesine** çeviriyoruz (`strength_for`):

| Kaç gösterge hemfikir? | Seviye | Gösterilir mi? |
|---|---|---|
| `MIN_AGREE`'den az (3'ten az) | **WEAK** (zayıf) | ❌ gizli |
| En az 3, ama hepsi değil | **MODERATE** ⚡ | ✅ |
| Uygun göstergelerin **hepsi** hemfikir | **STRONG** 🔥 | ✅ |

Kodda `MIN_AGREE = 3`. Yani **en az 3 gösterge AL demezse coin ekranda
görünmez.** Bu yüzden ekran çoğu zaman boş olabilir — bu normaldir, "nadir ama
kaliteli kurulum" mantığı.

**Akıllı detay:** "hepsi hemfikir" oransaldır. Trend rejiminde sadece 4 gösterge
uygun olduğu için 4/4 = STRONG. Range rejiminde sadece 2 gösterge var, 2/2 =
STRONG. ADX zaten doğru aileyi seçtiği için, üstüne yüksek oran şartı koymak
ekranı boşaltırdı — o yüzden mutlak bir taban (3) kullanıyoruz.

---

## 7. Conviction Gates (kanaat kapıları) — testlerle ayarlanmış filtreler

Bu, projenin en çok emek verdiğimiz kısmı. Göstergeler "ham" halleriyle iyi
değildi. 90 günlük testler şunu gösterdi:
- **RSI ve Bollinger'ın AL oyları her değerde para kaybettiriyor** → kapattık.
- **Trend göstergeleri (VWAP, MACD, VOL) eşikten ne kadar uzaklaşırsa o kadar
  güçleniyor** → eşikleri yükselttik.

Bunun için 5 "kapı" koyduk (hepsi `CT_` ile başlayan ortam değişkenleriyle
ayarlanabilir):

| Kapı | Değer | Anlamı |
|---|---|---|
| `CT_REVERSION_LONG` | `0` (kapalı) | RSI & Bollinger AL oylarını say**ma** (kaybettiren oylar) |
| `CT_VWAP_MIN` | `1.5` | VWAP AL'ı ancak fiyat VWAP'ın **%1.5+ üstündeyse** sayılır |
| `CT_MACD_MIN` | `0.08` | MACD kesişimi, histogram fiyatın **%0.08'inden** küçükse sayılmaz |
| `CT_VOL_MULT` | `3.8` | Hacim patlaması = ortalamanın **3.8 katı** |
| `CT_MIN_AGREE` | `3` | En az **3 gösterge** hemfikir olmalı |

Bu kombinasyona **"P1A3"** dedik. Testlerde **ilk kez** kâr beklentisi pozitif
çıkan ayar buydu. Eski "gevşek" haline dönmek istersen değerleri
`1 / 0 / 0 / 2.0 / 2` yaparsın.

---

## 8. Giriş zamanlaması ve "tazelik"

Bir sinyalin **ne zaman tetiklendiği** önemli. Kesişim 2 saat önce olduysa, o
tren çoktan kalkmıştır.

Kodda `CROSS_FRESHNESS_CANDLES = 1`. Yani bir kesişim/patlama ancak **son 1 mum
içinde** (son ~15 dakika) olduysa "taze" sayılır ve gösterilir. Bu, "tetiklenme"
ile "senin gerçekten alman" arasındaki kaymayı en aza indirir.

- **Triggered** (tetiklenme): sinyalin oluştuğu mumun zamanı
- **Entry** (giriş): "şimdi gir" — sen 1-5 dk içinde elle alacaksın

---

## 9. Çıkış mantığı — "kazananı koştur"

İlk versiyonda çıkış **sabit bir zamanlayıcıydı** (45 dk tut, çık gibi). Ama
testler bunun kötü olduğunu gösterdi: kazanan işlemleri erken kesiyordu.

Yeni mantık **trende dayalı** (validasyondan geçen `htf` çıkışı):
> **1 saatlik trend senin lehine olduğu sürece tut. Trend tersine döndüğünde
> (long için 1h trend AŞAĞI olduğunda) çık.**

Bir de **güvenlik tavanı** var: `MAX_HOLD_MINUTES = 1440` (24 saat). Bir
pozisyonu en fazla bu kadar tut. Bu aynı zamanda trade kaydına yazılan "kapanış
zamanı"dır.

Ekranda şöyle görürsün: *"hold while 1h trend ↑ — exit when it flips ↓ — latest
exit ... (max-hold 24h cap)"*.

> **Not:** Stop-loss'u program **asla otomatik koymaz.** Bu bilinçli bir kural —
> stop-loss'u sen elle ayarlıyorsun.

---

## 10. Baştan sona bir coinin yolculuğu (örnek)

Diyelim SOL/USDT'yi tarıyoruz:

1. **Veri çek:** SOL'un son 100 tane 15dk mumunu indir.
2. **Rejimi belirle:** ADX'i hesapla. Diyelim ADX = 31 → **TREND rejimi**. Demek
   ki sadece MACD, EMA, VOL, VWAP oy kullanabilir.
3. **Yön teyidi:** +DI ≥ −DI mi? Evet → devam. (Hayır olsaydı AL reddedilirdi.)
4. **Göstergeleri koştur:** MACD bullish kesişim var (histogram %0.12 > 0.08 ✓),
   VWAP fiyat %2.1 üstte (> 1.5 ✓), VOL 4.1x patlama (> 3.8 ✓), EMA kesişmedi
   (sus). → **3 AL oyu**.
5. **Puan & güç:** score = 3, uygun gösterge = 4. 3 ≥ MIN_AGREE(3) ama 4 değil →
   **MODERATE** ⚡.
6. **Tazelik:** kesişim son 1 mumda mı? Evet → geçerli.
7. **1h filtresi:** SOL'un 1 saatlik trendi YUKARI mı? Evet → AL'a izin var.
   (AŞAĞI olsaydı iptal.)
8. **Sonuç:** Ekrana yaz: "⚡ MODERATE BUY │ SOL/USDT │ fiyat │ 3/4 signals",
   giriş = şimdi, çıkış = 1h trend dönene kadar tut.
9. **Kaydet:** `trade_log.ods`'a ekle, Telegram'a gönder.

Eğer 1h trend aşağı olsaydı, ya da sadece 2 gösterge AL deseydi, ya da hacim
3.8x'e ulaşmasaydı → SOL hiç görünmezdi.

---

## 11. EN ÖNEMLİ KISIM: Bu projeden ne öğrendik?

Mekanik güzel ama dürüst sonuçlar şunlar — ve bunlar algoritma yazmaktan **daha
değerli derslerdir**:

### Ders 1: Çıkış stratejisi, giriş kenarını (edge) yaratamaz
Çıkış stratejilerini A/B test ettik (timer / vwap / htf / ema). `htf` (kazananı
koştur) açık ara en iyisiydi: zararı **−%23'ten −%10'a** yarıladı, kazanma
oranını %26'dan %38'e çıkardı. **AMA:** her çıkış modunda işlem başına beklenti
yine **~−%0.33** kaldı.

> **Ders:** İyi bir çıkış, ne kadar kanadığını ve nadir kazananı ne kadar
> koşturduğunu belirler. Ama girişlerinde gerçek bir avantaj yoksa, çıkış bunu
> yaratamaz. Önce giriş kenarını çöz.

### Ders 2: İşlem maliyeti her şeyi yer
Gidiş-dönüş işlem maliyeti yaklaşık **%0.3**. Girişlerimizin beklentisi bunun
etrafında salınıyordu — yani **maliyet düşülünce kenar neredeyse sıfır.** Çok
işlem yapmak (timer modu 2719 işlem) seni maliyetle öldürür. Az ve seçici olmak
(P1A3 sadece 56 işlem) daha iyiydi.

### Ders 3: Short'lar zarardı, long-only daha iyi
Test net konuştu: short tarafı net negatifti. Tüm short kodunu sildik.
**Ders:** Bir fikir mantıklı görünse de (her iki yönden kâr!), veri aksini
söylüyorsa veriyi dinle.

### Ders 4: "Düşük kazanma oranı / yüksek ödeme" bir stildir
Sistemimiz %25-38 kazanıyor — yani **işlemlerin çoğunu kaybediyor.** Ama birkaç
büyük kazanan (NEAR +%58 gibi) toplamı taşıyor. Bu, trend takibinin doğasıdır.
Kabul edebilmen gereken psikolojik bir şey: çok sayıda küçük kayıp + az sayıda
büyük kazanç.

### Ders 5: Tek coin bağımlılığı yapısal bir tehlike
P1A3 ayarımız 90 günde +%0.098/işlem pozitif çıktı. **Ama** tüm kârı **3 coin**
taşıyordu (XLM, TON, INJ = +%59). **Sadece XLM'i çıkarınca → −%0.094/işlem,
maliyet öncesi bile negatif.**

`robust_sweep.py` ile bunu test ettik: en iyi coini çıkardığında (exTop)
beklenti **her ayarda negatifti.** Yani bu kırılganlık parametre ayarıyla
düzelmiyor — stilin doğasında var.

> **Ders:** "Toplam pozitif" yanıltıcı olabilir. Birkaç şanslı vuruşa mı
> dayanıyor, yoksa geniş tabana mı? Bir-iki kazananı çıkar ve hâlâ pozitif mi
> diye bak. Bu, "aşırı uyum"u (overfit) anlamanın en pratik yolu.

### Ders 6: Parametreler overfit DEĞİLDİ (iyi haber)
Aynı sweep iyi bir şey de gösterdi: parametreleri biraz oynattığımızda (VWAP
1.0→2.0, VOL 3.0→4.6) sonuç **geniş bir pozitif platoda** kaldı — bıçak sırtı bir
tepe değil. Bu, parametrelerin sadece o veriye uydurulmadığını, gerçek bir şey
yakaladığını gösterir.

Hatta bir "bedava iyileştirme" bulduk: merkezi **VOL=4.2, MACD=0.10**'a
kaydırınca beklenti ~6.4 katına çıktı (+%0.631/işlem) ve **ilk kez** toplam
getiri (+%27.78) Buy&Hold'u (+%4.56) geçti. AMA bu, aynı 90 günlük pencerede
seçildiği için "in-sample seçim önyargısı" taşıyor — başka bir zaman penceresinde
doğrulamadan canlıya almadık.

### Ders 7: En acı ama en değerli ders — "sadece tut" çoğu zaman kazanıyordu
Karşılaştırma ölçütümüz **Buy & Hold** (al ve bekle): 90 günde ortalama
**+%4.56**. Bizim en iyi gerçekçi konfigümüz bunu zar zor geçti ve o da
kırılgandı.

> **Ders:** Aktif bir strateji, hiçbir şey yapmamaktan (sadece alıp beklemekten)
> iyi olmalı — yoksa onca emek, risk ve maliyet boşa. Çoğu strateji bu testi
> geçemez. Bizimki de net geçemedi. Bunu kabul edip "henüz canlıya alma" demek,
> kendini kandırıp para kaybetmekten iyidir.

---

## Tek paragrafta özet

> Program 30 coinin 15dk mumlarını çeker. ADX ile piyasanın trendde mi yatay mı
> olduğunu belirler ve sadece uygun gösterge ailesine oy hakkı verir (trend
> ailesi: MACD/EMA/VOL/VWAP; reversion ailesi: RSI/BB). Testle ayarlanmış katı
> kanaat kapılarından (VWAP %1.5+, VOL 3.8x, MACD %0.08, reversion kapalı) geçen
> ve en az 3 göstergenin AL dediği, üstelik 1 saatlik trende ters düşmeyen, taze
> (son 15dk) sinyalleri gösterir. Sadece long. Çıkış, 1h trend dönene kadar tut
> (24 saat tavanlı). Sonuç: dürüstçe, girişlerin maliyet sonrası kenarı neredeyse
> sıfır; en iyi konfig Buy&Hold'u zar zor ve kırılgan biçimde geçiyor — bu yüzden
> henüz canlıya alınmadı.

---

## İlgili dosyalar

| Dosya | Görevi |
|---|---|
| `signal_analyzer.py` | **Çekirdek.** Göstergeler, ADX rejim filtresi, kanaat kapıları, puanlama, canlı pano. |
| `backtest.py` | **Simülatör.** Stratejiyi mum mum geriye oynatır (lookahead-safe). |
| `trade_logger.py` | `trade_log.ods` okur/yazar (manuel notları korur). |
| `performance.py` | Trade kaydından kazanma oranı / kâr istatistiği. |
| `telegram_notifier.py` | Telegram bildirimleri + `/tara`, `/istatistik` komut botu. |
| `robust_sweep.py` | Parametre komşuluğu (OAT) sağlamlık taraması — overfit testi. |
| `signal_last.py` | Son N günün eşik üstü sinyallerini geriye doldurur (validasyon). |
| `STATUS.md` | Tek doğruluk kaynağı: dosya haritası, bulgular, yol haritası. |

---

_Eğitim amaçlı demo proje. Yatırım tavsiyesi değildir._

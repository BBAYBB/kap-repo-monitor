# KAP + TEFAS Fon Günlük Monitörü

Her gün tek bir HTML e-postayla iki soruya cevap verir:

1. **KAP:** Yatırım fonları bugün hangi kurumlarla hangi borsa dışı repo /
   ters repo işlemlerini yaptı; önceki işleme göre oran / karşı kurum /
   tutar ne değişti?
2. **TEFAS:** ONK, PAL, OBR, GRO ve GPL fonlarının portföy dağılımında
   önceki işlem gününe göre 0,25 yüzde puandan büyük hangi değişiklikler
   oldu?

Veriler SQLite'ta saklanır, her çalışmada önceki günlerle karşılaştırılır.
Rapor sırası: ① Önemli Değişiklikler (KAP + TEFAS) ② TEFAS Dağılım
Değişiklikleri ③ KAP Günlük İşlemler + haftalık takip ④ Haftalık Özet.

## Nasıl çalışır?

1. KAP'ın bildirim-sorgu sayfasının kendi kullandığı resmi JSON API'si
   sorgulanır (scraping yok):
   - `POST https://www.kap.org.tr/tr/api/disclosure/funds/byCriteria`
     — fon bildirimi listesi. Filtre: `fundTypeList=["YF"]` (Yatırım Fonları) +
     KAP'ın "Borsa Dışı Repo - Ters Repo Sözleşmesi" konusuna atadığı iki konu
     kodu (`config.py` içinde).
   - `GET https://www.kap.org.tr/tr/api/notification/attachment-detail/{no}`
     — bildirim detayı (açıklama metni).
2. Repo bilgileri (oran, karşı kurum, vade, tutar) KAP'ta yapılandırılmış alan
   değildir; her portföy şirketinin serbest metnidir. Program bu metni
   muhafazakâr kurallarla ayrıştırır; **bulunamayan alanlar "Belirtilmemiş"
   olarak bırakılır, asla tahmin edilmez.**
3. Her bildirim, KAP'ın benzersiz bildirim numarası (disclosure index) PRIMARY
   KEY olacak şekilde `data/kap_data.db` (SQLite) dosyasına kaydedilir —
   aynı bildirim iki kez kaydedilemez.
4. Bugünün işlemleri, aynı fonun önceki işlemleriyle (işlem türü + karşı kurum
   + vade eşleşmesine göre) karşılaştırılır; oran (bp cinsinden), karşı kurum,
   vade, tutar ve işlem türü değişiklikleri tespit edilir. Güvenilir eşleşme
   yoksa değişiklik iddia edilmez; ilk kez görülen fon "Yeni işlem" olarak
   işaretlenir.
5. HTML rapor e-posta ile gönderilir: ① Önemli Değişiklikler ② Günün İşlemleri
   ③ Son 1 Haftalık Fon Takibi ④ Kısa Piyasa Özeti. Her satırda ilgili KAP
   bildirimine tıklanabilir link vardır.
6. KAP'a erişilemezse eski veriler yeni veriymiş gibi **raporlanmaz**; açık bir
   "VERİ ALINAMADI" e-postası gönderilir ve hata loglanır.

## Proje yapısı

```
kap-repo-monitor/
│
├── main.py             # giriş noktası (normal / --test / --preview)
├── kap_service.py      # KAP API erişimi (liste + detay, retry'lı)
├── analyzer.py         # metin ayrıştırma + değişiklik analizi
├── database.py         # SQLite katmanı (duplicate koruması)
├── email_service.py    # HTML rapor üretimi + SMTP gönderim (retry'lı)
├── config.py           # tüm ayarlar (URL'ler, konu kodları, süreler)
├── run_kap_report.bat  # Task Scheduler'ın çalıştıracağı dosya
├── requirements.txt
├── .env.example        # SMTP ayar şablonu (kopyalayıp .env yapın)
├── .gitignore
├── README.md
├── data/kap_data.db    # (ilk çalışmada otomatik oluşur)
└── logs/kap_monitor.log# (ilk çalışmada otomatik oluşur)
```

## TEFAS fon dağılım takibi

KAP takibine ek olarak **ONK, PAL, OBR, GRO, GPL** fonlarının TEFAS'taki
günlük portföy varlık dağılımları izlenir ve bir dağılım kaleminin ağırlığı
önceki mevcut veri gününe göre **0,25 yüzde puandan (pp) fazla** değiştiyse
(|Δ| > 0,25 pp; tam 0,25 raporlanmaz; eşik `config.py` → `TEFAS_THRESHOLD_PP`) günlük e-postada raporlanır. Artışlar,
azalışlar, yeni açılan (%0 → anlamlı oran) ve kapanan (→ %0) pozisyonlar ayrı
işaretlenir; günün en büyük hareketleri özetlenir.

**Veri kaynağı:** TEFAS'ın kendi sitesinin kullandığı resmi JSON API'si —
`POST https://www.tefas.gov.tr/api/funds/dagilimSiraliGetirT` (fon detay
sayfasının yaptığı gerçek istek incelenerek birebir alınmıştır). Kategori
adları TEFAS fon detay sayfasındaki "Fon Varlık Dağılımı" tablosuyla birebir
doğrulanmış eşlemeden gelir (`tefas_service.py` içindeki `CATEGORY_LABELS`);
eşlemede olmayan bir kalem gelirse ham kodu gösterilir, isim uydurulmaz.

Önemli davranışlar:

- Karşılaştırma **takvim gününe göre değil, son iki mevcut veri gününe göre**
  yapılır (pazartesi ↔ cuma; hafta sonu veri yoktur ve hata sayılmaz).
- TEFAS o gün için veri yayınlamadıysa raporda **"TEFAS verisi henüz
  güncellenmedi (son veri: ...)"** notu düşülür; eski veri yeni veriymiş gibi
  asla kullanılmaz.
- Tüm günlük dağılımlar `tefas_allocations` tablosunda tarih bazında saklanır;
  ileride 1 hafta / 1 ay / 3 aylık analiz için geçmiş birikir.
- tefas.gov.tr **F5 bot koruması** kullanır; program tek oturum açar, istekler
  arasında bekler (`TEFAS_REQUEST_SLEEP_SECONDS`) ve engellenirse bunu açık
  hata olarak raporlar ("Request Rejected"). Görev günde bir kez çalıştığı
  için normalde engellenme yaşanmaz; yaşanırsa sonraki denemede kendiliğinden
  açılır.
- Takip edilen fon listesi ve eşik `config.py` içindedir: `TEFAS_FUNDS`,
  `TEFAS_THRESHOLD_PP`.
- Ayrıca haftalık KAP ayrıntı tablosundaki her fonun altına, o fonun
  **1 önceki iş günündeki TEFAS Repo / Ters-Repo portföy ağırlığı** yazılır
  (bu fonlar için de TEFAS'tan veri çekilir; veri yoksa "bulunamadı" notu
  düşülür). Tablonun her satırında da işlem gününün TEFAS ağırlığı gösterilir.
- Her ayrıntı fonunun altına **son 3 ayın Repo/Ters-Repo ağırlık grafiği**
  (PNG, e-postaya gömülü) eklenir. Grafik geçmişi veritabanında birikir;
  ilk çalışmada eksik geçmiş TEFAS'tan ~1 aylık parçalar halinde bir kez
  doldurulur. Grafikler `matplotlib` gerektirir (requirements.txt'te vardır);
  kurulu değilse rapor grafiksiz gönderilir, program çökmez.
- Not: TEFAS hız sınırına takıldığında hata yerine **boş yanıt**
  döndürebilmektedir; program boş yanıtı da şüpheli sayar ve bekleyip yeniden
  dener (eski/boş veri asla "değişiklik yok" gibi raporlanmaz).

## Fon kapsamı (filtreler)

Rapor şu anda **yalnızca Ak Portföy, İş Portföy ve Garanti Portföy'ün döviz
cinsi fonlarını** kapsar; TL fonları/repoları asla rapora girmez. Bu davranış
`config.py` içindeki üç listeyle yönetilir:

```python
FUND_TITLE_INCLUDE_ANY = ["AK PORTFÖY", "İŞ PORTFÖY", "GARANTİ PORTFÖY"]
FUND_TITLE_REQUIRE_ANY = ["DÖVİZ"]   # unvanında DÖVİZ geçmeyen fon alınmaz
FUND_TITLE_EXCLUDE_ANY = ["(TL)"]
EXCLUDE_TL_CURRENCY = True           # tutarı TL yazılmış kayıtlar da elenir
```

Kapsamı genişletmek için `FUND_TITLE_INCLUDE_ANY` listesine şirket ekleyin
(örn. `"YAPI KREDİ PORTFÖY"`) veya tüm şirketler için `[]` yapın. Filtreler
hem yeni çekilen verilere hem veritabanından okunan geçmişe uygulanır; yani
`config.py`'yi değiştirmek raporu anında etkiler, veritabanını silmek gerekmez.

## Yerel rapor sitesi (e-posta olmadan takip)

E-posta kurulumu **zorunlu değildir.** Program her çalışmada raporu bir web
sayfası olarak da kaydeder:

- `reports/son_rapor.html` — her zaman en güncel rapor
- `reports/rapor_YYYY-MM-DD.html` — günlük arşiv
- `reports/index.html` — ana sayfa: "En güncel raporu aç" + arşiv listesi

`run_kap_report.bat` rapor üretildikten sonra `son_rapor.html`'i varsayılan
tarayıcıda otomatik açar (istemiyorsanız bat dosyasındaki son satırı silin).
Yani Task Scheduler 08:00'de çalıştığında rapor kendiliğinden ekranınıza gelir.
`.env`'de SMTP ayarlı değilse program e-postayı sessizce atlar, hata vermez.

**Başka cihazlardan erişim:** `reports` klasörü yalnızca bu bilgisayardadır.
Her yerden erişilebilen otomatik site için aşağıdaki **GitHub Pages**
bölümüne bakın; pratik bir alternatif de proje klasörünü OneDrive/Google
Drive içine koymaktır (raporlar senkronize olur).

## GitHub Pages: her yerden erişilen, kendini güncelleyen site

Bu kurulumla rapor her sabah 08:00'de (TR) **GitHub'ın bulutunda** üretilir ve
`https://KULLANICIADI.github.io/kap-repo-monitor/` adresinde yayınlanır.
Bilgisayarınızın açık olması gerekmez; telefon dahil her cihazdan girersiniz.

> **Bilmeniz gerekenler:** (1) Ücretsiz GitHub hesaplarında Pages sitesi
> HERKESE AÇIKTIR — linki bilen görebilir (içerik zaten kamuya açık KAP/TEFAS
> verisidir). (2) TEFAS'ın bot koruması yurt dışı bulut sunucularını
> engelleyebilir; engellerse sayfada "VERİ ALINAMADI" görürsünüz ve bu yol
> yerine yerel/OneDrive yöntemine dönmek gerekebilir.

Kurulum (bir kez, ~15 dakika):

1. https://github.com adresinde ücretsiz hesap açın.
2. Sağ üstten **+ → New repository** → Repository name: `kap-repo-monitor`,
   görünürlük **Public** → **Create repository**.
3. Açılan sayfada **"uploading an existing file"** bağlantısına tıklayın ve bu
   projenin İÇİNDEKİ her şeyi (`.github` klasörü dahil — gizli klasörleri de
   seçtiğinizden emin olun; en kolayı tüm klasör içeriğini sürükleyip
   bırakmak) yükleyin → **Commit changes**.
   - Web arayüzü `.github` klasörünü yüklemekte sorun çıkarırsa: repo
     sayfasında **Add file → Create new file**, dosya adı olarak
     `.github/workflows/daily-report.yml` yazın ve bu projedeki aynı adlı
     dosyanın içeriğini yapıştırın.
4. Repo sayfasında **Settings → Pages** → "Build and deployment" altında
   Source: **Deploy from a branch**, Branch: **main**, klasör: **/docs** →
   **Save**.
5. **Actions** sekmesi → soldan **Gunluk KAP + TEFAS Raporu** → sağda
   **Run workflow** → **Run workflow**. (İlk çalıştırma izin isterse
   "I understand... enable them" düğmesiyle Actions'ı etkinleştirin.)
6. 5-10 dakika sonra siteniz hazır:
   `https://KULLANICIADI.github.io/kap-repo-monitor/`
   Ana sayfada "En güncel raporu aç" düğmesi ve günlük arşiv listesi bulunur.

Bundan sonrası otomatiktir: hafta içi her sabah 08:00'de (TR) rapor bulutta
üretilir, veritabanı repoda birikir ve site güncellenir. Saati değiştirmek
için `.github/workflows/daily-report.yml` içindeki `cron` satırını düzenleyin
(UTC yazılır; 08:00 TR = `0 5 * * 1-5`).

## Streamlit sitesi: etkileşimli web uygulaması

`streamlit_app.py`, aynı veriyi sekmeli ve etkileşimli bir web uygulaması
olarak sunar: özet metrikler, ① Önemli Değişiklikler ② TEFAS Dağılım
③ KAP İşlemleri (+ haftalık tablolar ve 3 aylık grafikler) ④ Haftalık Özet.

**Mimari:** Site KAP/TEFAS'a kendisi istek atmaz; veriyi `data/kap_data.db`
dosyasından okur. Veritabanını her sabah GitHub Actions doldurup repoya
commit eder; Streamlit Cloud yeni commit'i görünce uygulamayı otomatik
yeniden başlatır. Yani site hem anında açılır hem kendiliğinden güncellenir,
TEFAS bot korumasına da hiç takılmaz.

Yerelde denemek için:

```bat
pip install -r requirements.txt
streamlit run streamlit_app.py
```

**Streamlit Community Cloud'da yayınlama** (GitHub kurulumundan sonra, ~5 dk):

1. https://share.streamlit.io adresine gidin → **Sign in with GitHub**.
2. **Create app** (veya New app) → "Deploy a public app from GitHub".
3. Repository: `KULLANICIADI/kap-repo-monitor`, Branch: `main`,
   Main file path: `streamlit_app.py` → **Deploy**.
4. Birkaç dakika içinde siteniz hazır olur; adres şu biçimdedir:
   `https://UYGULAMA-ADI.streamlit.app` (adı deploy ekranında
   özelleştirebilirsiniz). Bu linke telefon dahil her cihazdan girilir.

Not: İlk deploy'da veritabanı repoda yoksa site "Veritabanı bulunamadı"
uyarısı gösterir — Actions'ın ilk çalışmasını başlatın (Actions sekmesi →
Run workflow); commit gelince site kendini yeniler. GitHub Pages sitesi
(docs/) ile Streamlit sitesi aynı repodan birlikte çalışabilir; isterseniz
yalnızca birini kullanın.

## Kurulum (Windows, sıfırdan)

### 1. Python kurulumu

1. https://www.python.org/downloads/ adresinden Python 3.10+ indirin.
2. Kurulumda **"Add python.exe to PATH"** kutusunu mutlaka işaretleyin.
3. Doğrulama: `cmd` açın ve şunu çalıştırın:
   ```bat
   python --version
   ```

### 2. Proje klasörü ve sanal ortam

Proje klasörünü örneğin `C:\kap-repo-monitor` konumuna koyun, sonra:

```bat
cd C:\kap-repo-monitor
python -m venv .venv
```

### 3. Sanal ortamı aktive edin

```bat
.venv\Scripts\activate
```

(Komut satırının başında `(.venv)` görünmelidir.)

### 4. Bağımlılıkları kurun

```bat
pip install -r requirements.txt
```

### 5. .env dosyasını oluşturun

```bat
copy .env.example .env
notepad .env
```

### 6. SMTP bilgilerini girin

`.env` içindeki alanları doldurun:

| Değişken | Açıklama |
|---|---|
| `SMTP_SERVER` | SMTP sunucusu (Gmail: `smtp.gmail.com`, Outlook: `smtp-mail.outlook.com`) |
| `SMTP_PORT` | Genellikle `587` |
| `SMTP_USERNAME` | E-posta adresiniz |
| `SMTP_PASSWORD` | Şifre — **Gmail için normal şifre çalışmaz**: Google Hesabı → Güvenlik → 2 Adımlı Doğrulama → Uygulama Şifreleri'nden 16 haneli uygulama şifresi oluşturun |
| `EMAIL_FROM` | Gönderen adres (genellikle SMTP_USERNAME ile aynı) |
| `EMAIL_TO` | Raporun gideceği adres(ler), virgülle ayrılabilir |

### 7. Test maili gönderin

```bat
python main.py --test
```

KAP'tan son 1 haftanın verisi çekilir ve **[TEST]** konulu örnek rapor size
gönderilir. (İlk çalışma, tüm bildirim detaylarını çektiği için birkaç dakika
sürebilir; sonraki çalışmalar yalnızca yeni bildirimleri çeker.)

E-posta ayarlarını yapmadan raporu görmek isterseniz:

```bat
python main.py --preview
```

Rapor `report_preview.html` dosyasına yazılır; tarayıcıda açıp bakabilirsiniz.

### 8. Normal çalıştırma

```bat
python main.py
```

Günün bildirimlerini çeker, veritabanını günceller ve günlük raporu gönderir.

### 9. Windows Task Scheduler kurulumu (her sabah 08:00)

**Kolay yol — tek komut** (`cmd`'yi *yönetici olarak* açın):

```bat
schtasks /Create /TN "KAP Repo Raporu" /TR "C:\kap-repo-monitor\run_kap_report.bat" /SC DAILY /ST 08:00
```

Saati değiştirmek için `/ST 08:00` değerini değiştirin (bilgisayarın yerel
saatidir), veya görevi Task Scheduler arayüzünden düzenleyin.

**Sabah çalıştırma hakkında:** Sabah 08:00'de o günün KAP bildirimleri henüz
yayımlanmamış olur. Bu durumda rapor otomatik olarak **son yayın gününü**
(dün; pazartesi sabahı cumayı) kapsar ve bunu en üstte açıkça belirtir
(`config.py` → `REPORT_USE_LATEST_DAY`). Dünün TEFAS dağılım verisi de akşam
yayımlandığı için sabah raporunda hazırdır.

**Arayüzle yapmak isterseniz:**

1. Başlat → "Task Scheduler" (Görev Zamanlayıcısı) yazıp açın.
2. Sağda **Create Basic Task** → Ad: `KAP Repo Raporu` → İleri.
3. Trigger: **Daily**, saat **08:00** → İleri.
4. Action: **Start a program** → Program: `C:\kap-repo-monitor\run_kap_report.bat` → İleri → Son.
5. (Önerilir) Görevin özelliklerinde **"Run task as soon as possible after a
   scheduled start is missed"** kutusunu işaretleyin — bilgisayar 08:00'de
   kapalıysa açıldığında çalışır.

Bilgisayarı sürekli açık tutmanız gerekmez; program yalnızca zamanlanmış saatte
çalışır ve işi bitince kapanır. `run_kap_report.bat`, sanal ortam varsa onu
otomatik kullanır.

## Sorun giderme

- **E-posta gitmiyor** → `logs/kap_monitor.log` dosyasına bakın. Gmail'de
  "Username and Password not accepted" hatası = uygulama şifresi gerekiyor
  (adım 6). Program gönderimi 3 kez dener.
- **KAP'a erişilemiyor / HTTP 403** → KAP zaman zaman yoğunluk yaşayabilir;
  program 3 deneme yapar, başarısız olursa "VERİ ALINAMADI" maili atar ve
  ertesi gün normal şekilde tekrar dener. Kalıcı 403 alıyorsanız KAP bot
  koruması sıkılaşmış olabilir; bu durumda `pip install cloudscraper` kurup
  `kap_service.py`'de `requests` yerine kullanmak gerekebilir (bugüne kadar
  gerekmemiştir).
- **Rapor boş görünüyor** → O gün KAP'ta kriterlere uyan bildirim
  yayımlanmamış olabilir (hafta sonları normaldir); rapor bunu açıkça yazar.
- **Veritabanını sıfırlamak** → programı kapatıp `data\kap_data.db` dosyasını
  silin; sonraki çalışmada son 1 hafta yeniden çekilir.

## Notlar

- Oran değişimleri baz puan (bp) cinsinden hesaplanır: 0,01 yüzde puan = 1 bp
  (%41,25 → %41,50 = **+25 bp**).
- "İşlem tarihi" bildirim metninden çıkarılır ve bildirim tarihinden ayrı
  saklanır; metinde işlem tarihi yoksa tabloda "Belirtilmemiş" görünür.
- Bu rapor bilgilendirme amaçlıdır; yatırım tavsiyesi değildir.

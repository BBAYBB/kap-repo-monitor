"""
KAP Fon Repo/Ters Repo Monitörü - Yapılandırma

Tüm ayarlar bu dosyada toplanmıştır. SMTP bilgileri .env dosyasından okunur
(bkz. .env.example). Bu dosyada gizli bilgi TUTULMAZ.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Proje kök dizini (bu dosyanın bulunduğu klasör)
BASE_DIR = Path(__file__).resolve().parent

# .env dosyasını yükle
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# KAP API AYARLARI
# ---------------------------------------------------------------------------
# KAP'ın bildirim-sorgu sayfasının kendi kullandığı resmi JSON endpoint'leri.
# (Sayfa üzerinden filtreli arama yapılıp giden istek incelenerek doğrulanmıştır.)
KAP_BASE_URL = "https://www.kap.org.tr"
KAP_FUNDS_QUERY_URL = f"{KAP_BASE_URL}/tr/api/disclosure/funds/byCriteria"
KAP_DETAIL_URL = f"{KAP_BASE_URL}/tr/api/notification/attachment-detail/{{index}}"
KAP_NOTIFICATION_LINK = f"{KAP_BASE_URL}/tr/Bildirim/{{index}}"

# Fon grubu: "Yatırım Fonları" -> KAP API'de fundTypeList = ["YF"]
KAP_FUND_TYPE_LIST = ["YF"]

# Konu: "Borsa Dışı Repo - Ters Repo Sözleşmesi"
# KAP bu konuya iki ayrı konu OID'si atamıştır (farklı taksonomi sürümleri).
# Bu değerler KAP arayüzünün gönderdiği gerçek isteğin birebir kopyasıdır.
KAP_SUBJECT_LIST = [
    "8aca490d502dd03b01502ddc1678004d",
    "4028328d537aad0a015383d773c44584",
]

# HTTP istek ayarları
KAP_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Referer": f"{KAP_BASE_URL}/tr/bildirim-sorgu",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
KAP_TIMEOUT_SECONDS = 30          # tek bir HTTP isteği için zaman aşımı
KAP_RETRY_COUNT = 3               # KAP istekleri için deneme sayısı
KAP_RETRY_WAIT_SECONDS = 10       # denemeler arası bekleme
KAP_DETAIL_SLEEP_SECONDS = 0.4    # detay istekleri arasında nazik bekleme

# ---------------------------------------------------------------------------
# ANALİZ / RAPOR AYARLARI
# ---------------------------------------------------------------------------
LOOKBACK_DAYS = 7                 # haftalık takip penceresi (gün)

# ---------------------------------------------------------------------------
# FON FİLTRELERİ
# ---------------------------------------------------------------------------
# Yalnızca fon unvanında aşağıdaki ifadelerden en az biri geçen fonlar
# raporlanır. Boş liste ([]) = tüm portföy şirketleri.
FUND_TITLE_INCLUDE_ANY = [
    "AK PORTFÖY",
    "İŞ PORTFÖY",
    "GARANTİ PORTFÖY",
]

# Fon unvanında şu ifadelerden en az biri geçmek ZORUNDA (boş liste = koşul
# yok). Döviz fonları KAP'ta unvanda "(DÖVİZ)", "(DÖVİZ-AVRO)",
# "(DÖVİZ-POUND)" ... şeklinde işaretlenir; bu koşul TL fonlarını dışarıda
# bırakır.
FUND_TITLE_REQUIRE_ANY = ["DÖVİZ"]

# Unvanında şu ifadeler geçen fonlar ASLA raporlanmaz.
FUND_TITLE_EXCLUDE_ANY = ["(TL)"]

# Ek güvenlik: bildirim metninde işlem tutarı TL/TRY olarak yazılmışsa o
# kayıt rapora alınmaz (TL repoların kesinlikle gelmemesi için).
EXCLUDE_TL_CURRENCY = True

# Haftalık KAP fon takibi bölümünde AYRINTILI tablo gösterimi:
# - Aşağıdaki fonlar için her zaman ayrıntılı tablo gösterilir.
KAP_WEEKLY_DETAIL_ALWAYS = ["ODN", "ONK", "OBR"]
# - Aşağıdaki fonlar için ayrıntılı tablo gösterilmez (kompakt listede kalır).
KAP_WEEKLY_DETAIL_EXCLUDE = ["AZZ"]

# ---------------------------------------------------------------------------
# TEFAS FON DAĞILIM TAKİBİ
# ---------------------------------------------------------------------------
# Portföy dağılımı takip edilecek fonlar
TEFAS_FUNDS = ["ONK", "PAL", "OBR", "GRO", "GPL"]

# Alarm eşiği (yüzde puan). Kural: |yeni - eski| > eşik ise raporlanır.
# Eşiğe tam eşit değişim RAPORLANMAZ ("...'dan fazla" kuralı).
TEFAS_THRESHOLD_PP = 0.25

# Karşılaştırma ve haftalık özet için geriye dönük gün sayısı
TEFAS_LOOKBACK_DAYS = 10

# Haftalık ayrıntı fonları için e-postaya eklenen grafiğin penceresi (gün)
TEFAS_CHART_DAYS = 92

# TEFAS istek ayarları (bot koruması nazik davranmayı gerektirir)
TEFAS_RETRY_COUNT = 3
TEFAS_RETRY_WAIT_SECONDS = 60      # engellenme genellikle birkaç dakikada açılır
TEFAS_REQUEST_SLEEP_SECONDS = 10   # fonlar arası bekleme

# ---------------------------------------------------------------------------
# VERİTABANI
# ---------------------------------------------------------------------------
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "kap_data.db"

# ---------------------------------------------------------------------------
# YEREL RAPOR SİTESİ
# ---------------------------------------------------------------------------
# Her çalışmada rapor bu klasöre HTML olarak kaydedilir:
#   reports/son_rapor.html     -> her zaman en güncel rapor
#   reports/rapor_YYYY-MM-DD.html -> günlük arşiv
#   reports/index.html         -> arşiv listesi (yerel takip sitesi ana sayfası)
# E-posta ayarlanmamış olsa bile rapor burada her gün birikir.
# GitHub Actions, siteyi GitHub Pages'in yayınladığı docs/ klasörüne yazmak
# için REPORTS_DIR ortam değişkenini kullanır (bkz. .github/workflows/).
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", str(BASE_DIR / "reports")))
REPORTS_INDEX_MAX = 90   # index'te listelenecek en fazla gün

# ---------------------------------------------------------------------------
# LOG
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "kap_monitor.log"

# ---------------------------------------------------------------------------
# E-POSTA (.env'den okunur)
# ---------------------------------------------------------------------------
SMTP_SERVER = os.getenv("SMTP_SERVER", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
# Birden fazla alıcı virgülle ayrılabilir: a@x.com,b@y.com
EMAIL_TO = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

EMAIL_RETRY_COUNT = 3             # e-posta gönderimi deneme sayısı
EMAIL_RETRY_WAIT_SECONDS = 30     # denemeler arası bekleme

# ---------------------------------------------------------------------------
# GÜNLÜK OTOMASYON
# ---------------------------------------------------------------------------
# Programın kendisi zamanlama YAPMAZ; Windows Task Scheduler tarafından
# çalıştırılır (bkz. README.md). Aşağıdaki değer yalnızca dokümantasyon ve
# kurulum komutu üretmek içindir. Görev saatini değiştirmek isterseniz
# Task Scheduler'daki görevi güncellemeniz yeterlidir.
DAILY_RUN_TIME = "08:00"

# Sabah çalıştırma modu: rapor saati geldiğinde o gün henüz KAP bildirimi
# yayımlanmamışsa (örn. sabah 08:00'de), "bugün" yerine SON YAYIN GÜNÜ
# (dün / cuma) raporlanır ve raporda bu açıkça belirtilir. False yapılırsa
# yalnızca rapor günü yayımlanan bildirimler raporlanır.
REPORT_USE_LATEST_DAY = True

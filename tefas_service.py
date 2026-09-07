"""
TEFAS veri erişim katmanı.

Veri kaynağı: TEFAS'ın (tefas.gov.tr) kendi sitesinin kullandığı resmi JSON
API'si:

    POST https://www.tefas.gov.tr/api/funds/dagilimSiraliGetirT

Bu endpoint fon bazında GÜNLÜK portföy varlık dağılımını (yüzde) döndürür.
İstek gövdesi ve kolon anahtarları, TEFAS fon detay sayfasının
(tefas.gov.tr/tr/fon-detayli-analiz/{FON}) yaptığı gerçek istek incelenerek
birebir alınmıştır.

ÖNEMLİ - Kolon anahtarları ve resmi kategori adları:
API, kategori adlarını kısaltma anahtarlarla döndürür (tr, osdb, vmd, ...).
Aşağıdaki CATEGORY_LABELS eşlemesi, TEFAS fon detay sayfasındaki "Fon Varlık
Dağılımı" tablosunda görünen RESMİ kategori adlarının, aynı günün API
yanıtındaki değerlerle birebir eşleştirilmesiyle doğrulanmıştır (ONK, PAL,
AAL, AAV fonları üzerinde). UI'da doğrulanamayan az sayıda anahtar, TEFAS'ın
doğrulanmış adlandırma kalıbı izlenerek adlandırılmıştır ve kod içinde
işaretlidir. Eşlemede olmayan bir anahtar gelirse raporda ham anahtar kodu
gösterilir - asla isim uydurulmaz.

DİKKAT - Bot koruması: tefas.gov.tr, F5 (TSPD) bot koruması kullanır ve sık/
alışılmadık istekleri "Request Rejected" sayfasıyla engeller. Bu modül bu
yüzden: tek oturum (Session) kullanır, önce siteden çerez alır, istekler
arasında bekler ve engellenme durumunu açıkça raporlar.
"""
import json
import logging
import time
from datetime import date, timedelta

import requests

import config

logger = logging.getLogger(__name__)


class TefasAccessError(Exception):
    """TEFAS'a erişilemediğinde veya beklenmeyen yanıt geldiğinde fırlatılır."""


TEFAS_BASE_URL = "https://www.tefas.gov.tr"
TEFAS_ALLOCATION_URL = f"{TEFAS_BASE_URL}/api/funds/dagilimSiraliGetirT"
TEFAS_WARMUP_URL = f"{TEFAS_BASE_URL}/tr/fon-verileri"

TEFAS_HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Origin": TEFAS_BASE_URL,
    "Referer": f"{TEFAS_BASE_URL}/tr/fon-verileri",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
}

# API kolon anahtarı -> TEFAS resmi kategori adı.
# "UI" ile işaretliler fon detay sayfası tablosunda birebir doğrulandı;
# "kalıp" ile işaretliler TEFAS'ın doğrulanmış adlandırma kalıbından türetildi.
CATEGORY_LABELS = {
    "hs": "Hisse Senedi",                                # UI (AAV)
    "dt": "Devlet Tahvili",                              # UI (AAL)
    "hb": "Hazine Bonosu",                               # UI (AAL)
    "fb": "Finansman Bonosu",                            # UI (AAL)
    "ost": "Özel Sektör Tahvili",                        # UI (AAL)
    "bb": "Banka Bonosu",                                # kalıp
    "vdm": "Varlığa Dayalı Menkul Kıymetler",            # UI (AAL)
    "eut": "Eurobond",                                   # kalıp
    "kibd": "Döviz Cinsi Kamu İç Borçlanma Araçları",    # UI (ONK, PAL)
    "osdb": "Özel Sektör Dış Borçlanma Araçları",        # UI (ONK, PAL)
    "kba": "Kamu Dış Borçlanma Araçları",                # UI (ONK, PAL)
    "dot": "Dövize Ödenebilir Bono",                     # kalıp
    "db": "Dövize Ödenebilir Tahvil",                    # kalıp
    "tpp": "Takasbank Para Piyasası",                    # UI (AAL, PAL)
    "bpp": "BIST Para Piyasası",                         # kalıp
    "btaa": "BIST Taahhütlü Alım",                       # kalıp
    "btas": "BIST Taahhütlü Satım",                      # kalıp
    "r": "Repo",                                         # UI (PAL)
    "tr": "Ters-Repo",                                   # UI (ONK, AAL, PAL)
    "vm": "Vadeli Mevduat",                              # kalıp
    "vmtl": "Mevduat (TL)",                              # UI (AAL, PAL)
    "vmd": "Mevduat (Döviz)",                            # UI (ONK, PAL)
    "vmau": "Mevduat (Altın)",                           # kalıp
    "kh": "Katılma Hesabı",                              # kalıp
    "khtl": "Katılma Hesabı (TL)",                       # UI (AAL)
    "khd": "Katılma Hesabı (Döviz)",                     # UI (ONK)
    "khau": "Katılma Hesabı (Altın)",                    # kalıp
    "kks": "Kamu Kira Sertifikaları",                    # kalıp
    "kkstl": "Kamu Kira Sertifikaları (TL)",             # UI (AAL)
    "kksd": "Kamu Kira Sertifikaları (Döviz)",           # UI (PAL)
    "kksyd": "Kamu Kira Sertifikaları (Yurt Dışı)",      # kalıp
    "osks": "Özel Sektör Kira Sertifikaları",            # UI (AAL)
    "oksyd": "Özel Sektör Kira Sertifikaları (Yurt Dışı)",  # kalıp
    "km": "Kıymetli Madenler",                           # kalıp
    "kmbyf": "Kıymetli Madenler Borsa Yatırım Fonu",     # kalıp
    "kmkba": "Kıymetli Maden Kamu Borçlanma Araçları",   # kalıp
    "kmkks": "Kıymetli Maden Kamu Kira Sertifikaları",   # kalıp
    "ymk": "Yabancı Menkul Kıymet",                      # kalıp
    "yba": "Yabancı Borçlanma Aracı",                    # kalıp
    "ybkb": "Yabancı Kamu Borçlanma Araçları",           # kalıp
    "ybosb": "Yabancı Özel Sektör Borçlanma Araçları",   # UI (ONK, PAL)
    "yhs": "Yabancı Hisse Senedi",                       # kalıp
    "ybyf": "Yabancı Borsa Yatırım Fonu",                # kalıp
    "fkb": "Fon Katılma Belgesi",                        # kalıp
    "yyf": "Yatırım Fonları Katılma Payları",            # UI (AAV, PAL)
    "byf": "Borsa Yatırım Fonu Katılma Payları",         # kalıp
    "gykb": "Gayrimenkul Yatırım Fonu Katılma Payları",  # kalıp
    "gyy": "Gayrimenkul Yatırımları",                    # kalıp
    "gsykb": "Girişim Sermayesi Yatırım Fonu Katılma Payları",  # kalıp
    "gsyy": "Girişim Sermayesi Yatırımları",             # kalıp
    "t": "Türev Araçlar",                                # kalıp
    "vint": "Vadeli İşlemler Nakit Teminatları",         # UI (AAL, AAV, PAL)
    "gas": "Gayrimenkul Sertifikası",                    # kalıp
    "d": "Diğer",                                        # kalıp
}

# Dağılım kalemi OLMAYAN yanıt alanları
_NON_CATEGORY_FIELDS = {"fonKodu", "fonUnvan", "tarih", "bilFiyat"}


def label_for(key):
    """Anahtar için resmi kategori adı; eşlemede yoksa ham anahtarı döndürür
    (asla isim uydurulmaz)."""
    return CATEGORY_LABELS.get(key, f"{key} (TEFAS kalem kodu)")


def _new_session():
    s = requests.Session()
    s.headers.update(TEFAS_HEADERS)
    try:
        # F5/TSPD çerezlerini almak için siteye normal bir sayfa isteği
        s.get(TEFAS_WARMUP_URL, timeout=config.KAP_TIMEOUT_SECONDS,
              headers={"Accept": "text/html,*/*"})
    except requests.RequestException as exc:
        logger.warning("TEFAS ısınma isteği başarısız (devam ediliyor): %s", exc)
    return s


def _is_waf_block(text):
    return "Request Rejected" in text or "requested URL was rejected" in text


def fetch_fund_allocations(session, fund_code, from_date: date, to_date: date):
    """Bir fonun tarih aralığındaki günlük dağılım satırlarını döndürür.

    Dönen: list[dict] -> {fund_code, fund_title, date ('YYYY-MM-DD'),
                          items: {key: pct}}  (yalnızca None olmayan kalemler)
    """
    body = {
        "fonTipi": "YAT", "fonKodu": None, "aramaMetni": None,
        "fonTurKod": None, "fonGrubu": None, "sfonTurKod": None,
        "fonTurAciklama": None, "kurucuKod": None,
        "basTarih": from_date.strftime("%Y%m%d"),
        "bitTarih": to_date.strftime("%Y%m%d"),
        "basSira": 1, "bitSira": 100000, "dil": "TR",
        "sFonTurKod": "", "fonKod": fund_code, "fonGrup": "",
        "fonUnvanTip": "",
    }
    last_err = None
    for attempt in range(1, config.TEFAS_RETRY_COUNT + 1):
        try:
            resp = session.post(TEFAS_ALLOCATION_URL, data=json.dumps(body),
                                timeout=config.KAP_TIMEOUT_SECONDS)
            if resp.status_code == 200 and not _is_waf_block(resp.text):
                data = resp.json()
                if data.get("errorCode"):
                    raise TefasAccessError(
                        f"TEFAS hata döndü: {data.get('errorMessage')}")
                # ÖNEMLİ: TEFAS hız sınırına takılınca hata yerine BOŞ liste
                # döndürebiliyor (gözlemlendi). Boş yanıtı hemen kabul etmek,
                # 'veri yok' ile 'sınırlandın' durumlarını karıştırır; bu
                # yüzden boş yanıtta da bekleyip tekrar denenir.
                if not data.get("resultList") and attempt < config.TEFAS_RETRY_COUNT:
                    last_err = "boş yanıt (olası hız sınırı)"
                    logger.warning(
                        "TEFAS %s: boş yanıt, hız sınırı olabilir; "
                        "deneme %d/%d", fund_code, attempt,
                        config.TEFAS_RETRY_COUNT)
                    time.sleep(config.TEFAS_RETRY_WAIT_SECONDS)
                    continue
                rows = []
                for item in data.get("resultList", []):
                    items = {}
                    for k, v in item.items():
                        if k in _NON_CATEGORY_FIELDS or v is None:
                            continue
                        if isinstance(v, (int, float)):
                            items[k] = float(v)
                    rows.append({
                        "fund_code": item.get("fonKodu"),
                        "fund_title": (item.get("fonUnvan") or "").strip(),
                        "date": item.get("tarih"),
                        "items": items,
                    })
                return rows
            if _is_waf_block(resp.text):
                last_err = "TEFAS bot koruması isteği engelledi (Request Rejected)"
            else:
                last_err = f"HTTP {resp.status_code}"
            logger.warning("TEFAS isteği başarısız (%s), deneme %d/%d",
                           last_err, attempt, config.TEFAS_RETRY_COUNT)
        except (requests.RequestException, ValueError) as exc:
            last_err = str(exc)
            logger.warning("TEFAS isteği hatası: %s, deneme %d/%d",
                           exc, attempt, config.TEFAS_RETRY_COUNT)
        if attempt < config.TEFAS_RETRY_COUNT:
            time.sleep(config.TEFAS_RETRY_WAIT_SECONDS)
    raise TefasAccessError(f"TEFAS sorgusu başarısız ({fund_code}): {last_err}")


def fetch_history(session, fund_code, from_date: date, to_date: date):
    """Uzun bir tarih aralığını TEFAS'ın ~1 aylık istek sınırına uyacak
    şekilde 28 günlük parçalara bölerek çeker (grafik geçmişi doldurma)."""
    rows = []
    cur = from_date
    while cur <= to_date:
        end = min(cur + timedelta(days=27), to_date)
        try:
            rows.extend(fetch_fund_allocations(session, fund_code, cur, end))
        except TefasAccessError as exc:
            logger.warning("TEFAS geçmiş parçası alınamadı (%s %s-%s): %s",
                           fund_code, cur, end, exc)
        cur = end + timedelta(days=1)
        if cur <= to_date:
            time.sleep(config.TEFAS_REQUEST_SLEEP_SECONDS)
    return rows


def fetch_all(today: date, funds=None):
    """Verilen fonların (varsayılan: config.TEFAS_FUNDS) son günlerdeki
    dağılımlarını çeker.

    Dönen: (rows, errors) - rows: fetch_fund_allocations çıktılarının
    birleşimi; errors: fon bazında hata mesajları (kısmi başarısızlıkta
    diğer fonlar etkilenmez).
    """
    fund_list = list(funds) if funds is not None else list(config.TEFAS_FUNDS)
    from_date = today - timedelta(days=config.TEFAS_LOOKBACK_DAYS)
    session = _new_session()
    all_rows, errors = [], {}
    for i, fund in enumerate(fund_list):
        if i > 0:
            time.sleep(config.TEFAS_REQUEST_SLEEP_SECONDS)
        try:
            rows = fetch_fund_allocations(session, fund, from_date, today)
            logger.info("TEFAS %s: %d günlük dağılım satırı alındı",
                        fund, len(rows))
            all_rows.extend(rows)
        except TefasAccessError as exc:
            logger.error("TEFAS %s alınamadı: %s", fund, exc)
            errors[fund] = str(exc)
    return all_rows, errors

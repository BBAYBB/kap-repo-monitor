"""
KAP veri erişim katmanı.

KAP'ın bildirim-sorgu sayfasının kendi kullandığı resmi JSON API'si üzerinden:
  1) POST /tr/api/disclosure/funds/byCriteria  -> bildirim listesi
  2) GET  /tr/api/notification/attachment-detail/{disclosureIndex} -> bildirim detayı

Bildirim detayındaki repo bilgileri (oran, karşı kurum, vade, tutar) KAP'ta
yapılandırılmış alanlar DEĞİLDİR; her portföy yönetim şirketinin serbest metin
olarak yazdığı "Açıklamalar" bölümündedir. Bu modül yalnızca ham veriyi çeker;
metin ayrıştırma analyzer.py'dedir.
"""
import json
import logging
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)


class KapAccessError(Exception):
    """KAP'a erişilemediğinde veya beklenmeyen yanıt geldiğinde fırlatılır."""


def _post_with_retry(url, payload):
    last_err = None
    for attempt in range(1, config.KAP_RETRY_COUNT + 1):
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers=config.KAP_REQUEST_HEADERS,
                timeout=config.KAP_TIMEOUT_SECONDS,
            )
            if resp.status_code == 200:
                return resp.json()
            last_err = f"HTTP {resp.status_code}"
            logger.warning("KAP isteği başarısız (%s), deneme %d/%d",
                           last_err, attempt, config.KAP_RETRY_COUNT)
        except (requests.RequestException, ValueError) as exc:
            last_err = str(exc)
            logger.warning("KAP isteği hatası: %s, deneme %d/%d",
                           exc, attempt, config.KAP_RETRY_COUNT)
        if attempt < config.KAP_RETRY_COUNT:
            time.sleep(config.KAP_RETRY_WAIT_SECONDS)
    raise KapAccessError(f"KAP sorgusu başarısız: {url} ({last_err})")


def _get_with_retry(url):
    last_err = None
    for attempt in range(1, config.KAP_RETRY_COUNT + 1):
        try:
            resp = requests.get(
                url,
                headers=config.KAP_REQUEST_HEADERS,
                timeout=config.KAP_TIMEOUT_SECONDS,
            )
            if resp.status_code == 200:
                return resp.json()
            last_err = f"HTTP {resp.status_code}"
        except (requests.RequestException, ValueError) as exc:
            last_err = str(exc)
        if attempt < config.KAP_RETRY_COUNT:
            time.sleep(config.KAP_RETRY_WAIT_SECONDS)
    raise KapAccessError(f"KAP detay isteği başarısız: {url} ({last_err})")


def fetch_disclosure_list(from_date: date, to_date: date):
    """Verilen tarih aralığındaki Yatırım Fonları / Borsa Dışı Repo - Ters Repo
    Sözleşmesi bildirimlerinin listesini döndürür.

    Dönen her öğe KAP'ın kendi liste kaydıdır; en önemli alanlar:
      publishDate ("dd.mm.yyyy HH:MM:SS"), fundCode, kapTitle, summary,
      disclosureIndex (benzersiz bildirim numarası).
    """
    payload = {
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "fundTypeList": list(config.KAP_FUND_TYPE_LIST),
        "mkkMemberOidList": [],
        "fundOidList": [],
        "passiveFundOidList": [],
        "disclosureClass": "",
        "isLate": "",
        "subjectList": list(config.KAP_SUBJECT_LIST),
        "discIndex": [],
        "fromSrc": False,
        "srcCategory": "",
    }
    data = _post_with_retry(config.KAP_FUNDS_QUERY_URL, payload)
    if not isinstance(data, list):
        raise KapAccessError("KAP liste yanıtı beklenen formatta değil (liste bekleniyordu).")
    logger.info("KAP listesi alındı: %d bildirim (%s - %s)",
                len(data), from_date, to_date)
    return data


def extract_explanation_text(disclosure_body) -> str:
    """Bildirim detayındaki HTML gövdeden serbest 'Açıklamalar' metnini çıkarır.

    KAP şablonunda açıklama metni 'text-block-value' sınıflı öğede bulunur;
    bazı bildirimlerde ise yalnızca 'note-editable' (editör içeriği) doludur.
    Hiçbiri yoksa boş string döner - asla tahmin edilmez.
    """
    if disclosure_body is None:
        return ""
    if isinstance(disclosure_body, list):
        html = "".join(str(x) for x in disclosure_body)
    else:
        html = str(disclosure_body)
    if not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")

    for selector in ("text-block-value", "note-editable"):
        for el in soup.find_all(class_=selector):
            text = el.get_text(" ", strip=True)
            if text:
                return " ".join(text.split())
    return ""


def fetch_disclosure_detail_text(disclosure_index: int) -> str:
    """Tek bir bildirimin serbest açıklama metnini döndürür."""
    url = config.KAP_DETAIL_URL.format(index=disclosure_index)
    data = _get_with_retry(url)
    if not isinstance(data, list) or not data:
        return ""
    return extract_explanation_text(data[0].get("disclosureBody"))


def fetch_week(today: date):
    """Son LOOKBACK_DAYS günün bildirimlerini (liste + açıklama metinleri) çeker.

    Dönen değer: list[dict] - her biri şu alanları taşır:
      disclosure_index, fund_code, fund_title, summary, publish_datetime (ham str),
      raw_text (açıklama metni), kap_link
    """
    from_date = today - timedelta(days=config.LOOKBACK_DAYS)
    raw_list = fetch_disclosure_list(from_date, today)

    records = []
    seen = set()
    for item in raw_list:
        idx = item.get("disclosureIndex")
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        records.append({
            "disclosure_index": int(idx),
            "fund_code": (item.get("fundCode") or "").strip(),
            "fund_title": (item.get("kapTitle") or "").strip(),
            "summary": " ".join((item.get("summary") or "").split()),
            "publish_datetime": (item.get("publishDate") or "").strip(),
            "kap_link": config.KAP_NOTIFICATION_LINK.format(index=idx),
            "raw_text": None,  # detay aşamasında doldurulur
        })
    return records


def fill_detail_texts(records, skip_indexes=None):
    """raw_text'i boş olan kayıtlar için KAP'tan detay metnini çeker.

    skip_indexes: veritabanında zaten metni olan bildirimler (tekrar çekilmez).
    Tek tek bildirim detayı alınamazsa o kayıt için raw_text "" bırakılır ve
    loglanır; tüm süreç durdurulmaz.
    """
    skip = skip_indexes or set()
    fetched = 0
    for rec in records:
        if rec["disclosure_index"] in skip:
            continue
        try:
            rec["raw_text"] = fetch_disclosure_detail_text(rec["disclosure_index"])
            fetched += 1
        except KapAccessError as exc:
            logger.error("Bildirim detayı alınamadı (index=%s): %s",
                         rec["disclosure_index"], exc)
            rec["raw_text"] = ""
        time.sleep(config.KAP_DETAIL_SLEEP_SECONDS)
    logger.info("Detay metni çekilen bildirim sayısı: %d", fetched)
    return records

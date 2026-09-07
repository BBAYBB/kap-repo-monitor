"""
SQLite veri katmanı.

Her KAP bildirimi disclosure_index (KAP'ın benzersiz bildirim numarası)
PRIMARY KEY olarak saklanır; aynı bildirim ikinci kez asla kaydedilmez
(INSERT OR IGNORE). Ham açıklama metni de saklanır, böylece ayrıştırma
kuralları geliştirilirse geçmiş veriler yeniden analiz edilebilir.
"""
import logging
import sqlite3
from datetime import datetime

import config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS disclosures (
    disclosure_index    INTEGER PRIMARY KEY,   -- KAP bildirim no (benzersiz)
    fund_code           TEXT,                  -- fon kodu (örn. TMV)
    fund_title          TEXT,                  -- fon unvanı
    management_company  TEXT,                  -- portföy yönetim şirketi
    summary             TEXT,                  -- KAP özet başlığı
    publish_datetime    TEXT,                  -- KAP bildirim tarihi/saati
    trade_date          TEXT,                  -- işlem tarihi (ISO, metinden)
    maturity_date       TEXT,                  -- vade tarihi (ISO, metinden)
    term_days           INTEGER,               -- vade (gün)
    direction           TEXT,                  -- 'Repo' / 'Ters Repo'
    counterparty        TEXT,                  -- karşı kurum
    rate                REAL,                  -- faiz oranı (%)
    amount              REAL,                  -- işlem tutarı
    currency            TEXT,                  -- tutar para birimi
    kap_link            TEXT,                  -- KAP bildirim linki
    raw_text            TEXT,                  -- ham açıklama metni
    first_seen          TEXT                   -- bu programın ilk gördüğü an
);
CREATE INDEX IF NOT EXISTS idx_disclosures_fund ON disclosures(fund_code);
CREATE INDEX IF NOT EXISTS idx_disclosures_publish ON disclosures(publish_datetime);

CREATE TABLE IF NOT EXISTS tefas_allocations (
    fund_code   TEXT NOT NULL,                 -- fon kodu (örn. ONK)
    fund_title  TEXT,                          -- fon unvanı
    date        TEXT NOT NULL,                 -- veri tarihi (YYYY-MM-DD)
    item_key    TEXT NOT NULL,                 -- TEFAS API kalem anahtarı
    item_label  TEXT,                          -- resmi kategori adı
    pct         REAL,                          -- portföy oranı (%)
    source      TEXT,                          -- veri kaynağı
    fetched_at  TEXT,                          -- verinin çekildiği an
    PRIMARY KEY (fund_code, date, item_key)
);
CREATE INDEX IF NOT EXISTS idx_tefas_fund_date ON tefas_allocations(fund_code, date);
"""

_COLUMNS = [
    "disclosure_index", "fund_code", "fund_title", "management_company",
    "summary", "publish_datetime", "trade_date", "maturity_date", "term_days",
    "direction", "counterparty", "rate", "amount", "currency", "kap_link",
    "raw_text", "first_seen",
]


def get_connection():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def known_indexes(conn):
    """Veritabanında zaten kayıtlı bildirim numaraları."""
    rows = conn.execute("SELECT disclosure_index FROM disclosures").fetchall()
    return {row["disclosure_index"] for row in rows}


def save_records(conn, records):
    """Ayrıştırılmış kayıtları kaydeder. Var olan bildirimler atlanır.
    Dönen: yeni eklenen kayıt sayısı."""
    now = datetime.now().isoformat(timespec="seconds")
    inserted = 0
    for rec in records:
        row = {col: rec.get(col) for col in _COLUMNS}
        row["first_seen"] = now
        cur = conn.execute(
            f"INSERT OR IGNORE INTO disclosures ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join(':' + c for c in _COLUMNS)})",
            row,
        )
        inserted += cur.rowcount
    conn.commit()
    logger.info("Veritabanına eklenen yeni bildirim: %d", inserted)
    return inserted


def save_tefas_rows(conn, rows, label_fn):
    """TEFAS dağılım satırlarını kaydeder.

    Aynı (fon, tarih, kalem) için INSERT OR REPLACE kullanılır; TEFAS bir
    günün verisini sonradan revize ederse güncel değer saklanır. fetched_at,
    satırın İLK görüldüğü anı korur (yenilik tespiti için).
    Dönen: bu çağrıda ilk kez görülen (fund_code, date) çiftleri kümesi.
    """
    from datetime import datetime as _dt
    now = _dt.now().isoformat(timespec="seconds")
    new_fund_dates = set()
    for row in rows:
        if not row.get("fund_code") or not row.get("date"):
            continue
        existing_dates = {
            r["date"] for r in conn.execute(
                "SELECT DISTINCT date FROM tefas_allocations WHERE fund_code = ?",
                (row["fund_code"],),
            )
        }
        if row["date"] not in existing_dates:
            new_fund_dates.add((row["fund_code"], row["date"]))
        for key, pct in row.get("items", {}).items():
            prev = conn.execute(
                "SELECT fetched_at FROM tefas_allocations "
                "WHERE fund_code = ? AND date = ? AND item_key = ?",
                (row["fund_code"], row["date"], key),
            ).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO tefas_allocations "
                "(fund_code, fund_title, date, item_key, item_label, pct, "
                " source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["fund_code"], row["fund_title"], row["date"], key,
                 label_fn(key), pct, "tefas.gov.tr/api/funds/dagilimSiraliGetirT",
                 prev["fetched_at"] if prev else now),
            )
    conn.commit()
    return new_fund_dates


def load_tefas_allocations(conn, fund_code, since_date_iso):
    """Bir fonun since_date (ISO) sonrası dağılımlarını tarih bazında döndürür.
    Dönen: dict[date_iso] -> {item_key: pct}"""
    rows = conn.execute(
        "SELECT date, item_key, pct FROM tefas_allocations "
        "WHERE fund_code = ? AND date >= ? ORDER BY date",
        (fund_code, since_date_iso),
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["date"], {})[r["item_key"]] = r["pct"]
    return out


def load_records_since(conn, since_date):
    """publish tarihi >= since_date olan tüm kayıtları dict listesi döndürür.

    publish_datetime 'dd.mm.yyyy HH:MM:SS' formatında saklandığı için
    karşılaştırma Python tarafında yapılır.
    """
    rows = conn.execute("SELECT * FROM disclosures").fetchall()
    out = []
    for row in rows:
        rec = dict(row)
        try:
            pd = datetime.strptime(rec["publish_datetime"], "%d.%m.%Y %H:%M:%S")
        except (TypeError, ValueError):
            continue
        if pd.date() >= since_date:
            out.append(rec)
    return out

"""
Streamlit sitesi için veri hazırlama katmanı.

Site, KAP/TEFAS'a KENDİSİ istek ATMAZ: veriyi, GitHub Actions'ın (veya yerel
çalıştırmanın) her sabah doldurduğu data/kap_data.db dosyasından okur. Böylece
sayfa anında açılır ve TEFAS bot korumasına takılma riski olmaz. Buradaki
fonksiyonlar saf Python'dur ve Streamlit olmadan test edilebilir.
"""
import logging
from datetime import date, datetime, timedelta

import analyzer
import charts
import config
import database
import email_service
import main as pipeline

logger = logging.getLogger(__name__)


def _pub_day(rec):
    pd = analyzer.publish_date_of(rec)
    return pd.date() if pd else None


def build_site_data(today=None):
    """Veritabanından sitenin tüm bölümlerini üretir.

    today: test amaçlı gün sabitleme; normalde bugünün tarihi kullanılır.

    Dönen dict:
      ok (bool), error (str|None), report_day (date|None),
      generated_at (str|None)  - veritabanındaki en son veri çekim anı,
      metrics (dict), sections (dict[str, html str])
    """
    if not config.DB_PATH.exists():
        return {"ok": False, "error": "Veritabanı bulunamadı. GitHub Actions "
                "ilk çalışmasını tamamladığında (veya program bir kez "
                "çalıştırıldığında) veriler burada görünecek.",
                "report_day": None, "generated_at": None,
                "metrics": {}, "sections": {}}

    conn = database.get_connection()
    try:
        return _build(conn, today)
    finally:
        conn.close()


def _build(conn, today=None):
    today = today or date.today()
    since = today - timedelta(days=config.LOOKBACK_DAYS)

    window = [
        r for r in database.load_records_since(conn, since)
        if analyzer.fund_matches_filters(r.get("fund_title"))
        and analyzer.record_passes_currency_filter(r)
    ]
    if not window:
        return {"ok": False, "error": "Son 1 haftaya ait KAP verisi "
                "veritabanında yok. Günlük veri toplama çalışması henüz "
                "koşmamış olabilir.", "report_day": None,
                "generated_at": _last_fetch(conn),
                "metrics": {}, "sections": {}}

    # Rapor günü = penceredeki son yayın günü (sabah saatlerinde "bugün"
    # henüz boş olacağı için e-postadaki davranışın aynısı)
    days = sorted({d for d in (_pub_day(r) for r in window) if d},
                  reverse=True)
    report_day = days[0]
    today_records = [r for r in window if _pub_day(r) == report_day]
    history_records = [r for r in window
                       if _pub_day(r) not in (None, report_day)]

    change_results = analyzer.detect_changes(today_records, history_records)
    tracking = analyzer.weekly_fund_tracking(window)
    summary = analyzer.market_summary(today_records, change_results)

    # --- TEFAS (tamamen DB'den; ağ isteği yok) ---
    since_iso = (today - timedelta(days=config.TEFAS_LOOKBACK_DAYS)).isoformat()
    funds, weekly, fund_changes, seen_keys = {}, {}, {}, set()
    for fund in config.TEFAS_FUNDS:
        alloc = database.load_tefas_allocations(conn, fund, since_iso)
        for day_items in alloc.values():
            seen_keys.update(day_items)
        latest, prev, changes = analyzer.tefas_daily_changes(
            alloc, config.TEFAS_THRESHOLD_PP)
        row = conn.execute(
            "SELECT fund_title FROM tefas_allocations WHERE fund_code = ? "
            "ORDER BY date DESC LIMIT 1", (fund,)).fetchone()
        funds[fund] = {
            "title": row["fund_title"] if row else "",
            "latest": latest, "prev": prev, "changes": changes,
            # Sitede son iki MEVCUT gün karşılaştırılır ve günler açıkça
            # yazılır; "bugün güncellendi mi" kavramı e-postaya özgüdür.
            "updated_today": bool(latest and prev),
        }
        if latest and prev:
            fund_changes[fund] = changes
        first, last, moves = analyzer.tefas_weekly_summary(
            alloc, config.TEFAS_THRESHOLD_PP)
        if first:
            weekly[fund] = (first, last, moves)

    import tefas_service
    tefas = {
        "funds": funds, "weekly": weekly,
        "biggest": analyzer.tefas_biggest_moves(fund_changes),
        "errors": {}, "threshold": config.TEFAS_THRESHOLD_PP,
        "labels": {k: tefas_service.label_for(k) for k in seen_keys},
    }
    tefas["kap_weights"], tefas["kap_alloc"] = pipeline.build_kap_tefas_weights(
        conn, today, email_service.weekly_detail_fund_codes(tracking))

    # --- Grafikler (DB'deki birikmiş geçmişten) ---
    chart_since = (today - timedelta(days=config.TEFAS_CHART_DAYS)).isoformat()
    chart_images = {}
    if charts.MATPLOTLIB_AVAILABLE:
        for fund in email_service.weekly_detail_fund_codes(tracking):
            alloc = database.load_tefas_allocations(conn, fund, chart_since)
            series = {d: {"tr": it.get("tr"), "r": it.get("r")}
                      for d, it in alloc.items()}
            png = charts.render_repo_weight_chart(fund, series)
            if png:
                chart_images[fund] = png

    # --- HTML bölümleri (e-postayla aynı, test edilmiş üreticiler) ---
    weekly_html = pipeline._embed_charts(
        email_service._weekly_tracking_html(tracking, tefas,
                                            set(chart_images)),
        chart_images)
    sections = {
        "onemli_kap": email_service._important_changes_html(change_results),
        "onemli_tefas": email_service._important_tefas_html(tefas),
        "tefas": email_service._tefas_section_html(tefas),
        "kap_gunluk": email_service._daily_table_html(change_results),
        "kap_haftalik": weekly_html,
        "haftalik_ozet": email_service._combined_weekly_html(tracking, tefas),
        "ozet": email_service._summary_html(summary),
    }

    biggest = tefas["biggest"]
    max_move = None
    for kind in ("increase", "decrease", "new", "closed"):
        e = biggest.get(kind)
        if e and (max_move is None or abs(e[1]["delta"]) > abs(max_move[1])):
            max_move = (e[0], e[1]["delta"])
    metrics = {
        "n_records": summary["n_records"],
        "n_funds": summary["n_funds"],
        "n_ters_repo": summary["n_ters_repo"],
        "n_repo": summary["n_repo"],
        "n_rate_changed": summary["n_funds_rate_changed"],
        "max_tefas_move": max_move,  # (fon, delta) | None
    }
    return {"ok": True, "error": None, "report_day": report_day,
            "generated_at": _last_fetch(conn), "metrics": metrics,
            "sections": sections}


def _last_fetch(conn):
    """Veritabanındaki en son veri çekim anı (sitenin 'son güncelleme'si)."""
    try:
        a = conn.execute(
            "SELECT MAX(first_seen) AS m FROM disclosures").fetchone()["m"]
        b = conn.execute(
            "SELECT MAX(fetched_at) AS m FROM tefas_allocations").fetchone()["m"]
        stamps = [s for s in (a, b) if s]
        if not stamps:
            return None
        dt = datetime.fromisoformat(max(stamps))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return None

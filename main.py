"""
KAP Fon Repo/Ters Repo Günlük Monitörü - ana giriş noktası.

Kullanım:
  python main.py            Normal günlük çalışma: KAP'ı sorgular, veritabanını
                            günceller, günlük HTML raporu e-posta ile gönderir.
  python main.py --test     Aynı akış; ancak "bugün" için bildirim olmasa bile
                            son 1 haftalık veriyle TEST işaretli rapor gönderir.
  python main.py --preview  E-posta GÖNDERMEZ; raporu report_preview.html
                            dosyasına yazar (SMTP ayarlarını yapmadan denemek için).

Windows Task Scheduler ile her gün otomatik çalıştırma için README.md'ye bakın.
"""
import argparse
import logging
import sys
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler

import analyzer
import charts
import config
import database
import email_service
import kap_service
import tefas_service
from kap_service import KapAccessError


def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)


logger = logging.getLogger("main")


def build_kap_tefas_weights(conn, today, detail_funds):
    """Haftalık KAP ayrıntı tablosundaki fonlar için 1 ÖNCEKİ İŞ GÜNÜNÜN
    TEFAS'taki Repo / Ters-Repo portföy ağırlıklarını hazırlar.

    'Önceki iş günü' = fonun TEFAS'ta rapor gününden ÖNCEKİ son mevcut veri
    günü (hafta sonu/tatilde otomatik olarak cumaya düşer). Veri yoksa fon
    için kayıt üretilmez; raporda 'bulunamadı' notu çıkar - tahmin edilmez.
    """
    from datetime import timedelta as _td

    weights = {}
    alloc_by_fund = {}
    since_iso = (today - _td(days=config.TEFAS_LOOKBACK_DAYS)).isoformat()
    today_iso = today.isoformat()
    for fund in detail_funds:
        alloc = database.load_tefas_allocations(conn, fund, since_iso)
        # Tablodaki her işlem satırının gününe eşlenecek günlük repo/ters repo
        # ağırlıkları (yalnızca tr ve r kalemleri)
        alloc_by_fund[fund] = {
            day: {"tr": items.get("tr"), "r": items.get("r")}
            for day, items in alloc.items()
        }
        prev_days = [d for d in sorted(alloc) if d < today_iso]
        if not prev_days:
            continue
        day = prev_days[-1]
        items = alloc[day]
        weights[fund] = {"date": day,
                         "tr": items.get("tr"),
                         "r": items.get("r")}
    return weights, alloc_by_fund


def build_charts(conn, today, detail_funds):
    """Haftalık ayrıntı fonları için son 3 aylık TEFAS Repo/Ters-Repo ağırlık
    grafiklerini üretir.

    Grafik penceresi veritabanındaki birikmiş geçmişten okunur; pencere
    başında eksik geçmiş varsa TEFAS'tan bir kez parça parça doldurulur
    (sonraki günlerde günlük çekimler geçmişi zaten biriktirir).

    Dönen: dict[fund] -> PNG bytes
    """
    from datetime import timedelta as _td

    if not charts.MATPLOTLIB_AVAILABLE or not detail_funds:
        return {}
    window_start = today - _td(days=config.TEFAS_CHART_DAYS)
    since_iso = window_start.isoformat()

    session = None
    images = {}
    for fund in detail_funds:
        alloc = database.load_tefas_allocations(conn, fund, since_iso)
        days = sorted(alloc)
        # Pencere başında belirgin boşluk varsa geçmişi bir defalık doldur
        need_backfill = (not days
                         or days[0] > (window_start + _td(days=7)).isoformat())
        if need_backfill:
            if session is None:
                session = tefas_service._new_session()
            backfill_end = (date.fromisoformat(days[0]) - _td(days=1)
                            if days else today)
            logger.info("TEFAS grafik geçmişi dolduruluyor: %s (%s - %s)",
                        fund, window_start, backfill_end)
            rows = tefas_service.fetch_history(session, fund,
                                               window_start, backfill_end)
            database.save_tefas_rows(conn, rows, tefas_service.label_for)
            alloc = database.load_tefas_allocations(conn, fund, since_iso)

        series = {day: {"tr": items.get("tr"), "r": items.get("r")}
                  for day, items in alloc.items()}
        png = charts.render_repo_weight_chart(fund, series)
        if png:
            images[fund] = png
    return images


def build_tefas_data(conn, today, is_test=False, extra_funds=None):
    """TEFAS dağılımlarını çeker, kaydeder ve rapor için analiz paketi üretir.

    TEFAS tarafındaki bir hata KAP raporunu engellemez; hatalar pakete
    yazılır ve raporda açıkça gösterilir. Hiçbir durumda eski veri yeni
    veriymiş gibi sunulmaz: bir fonun son veri günü bu çalışmada YENİ
    görülmediyse o fon için değişiklik raporlanmaz, "henüz güncellenmedi"
    notu düşülür.
    """
    from datetime import timedelta as _td

    # Alarm takibindeki fonlara ek olarak, haftalık KAP ayrıntı tablosundaki
    # fonların TEFAS repo/ters repo ağırlıkları için de veri çekilir.
    fetch_funds = list(config.TEFAS_FUNDS)
    for f in (extra_funds or []):
        if f and f not in fetch_funds:
            fetch_funds.append(f)

    try:
        rows, errors = tefas_service.fetch_all(today, funds=fetch_funds)
    except Exception as exc:  # beklenmeyen durumda tüm fonlar hatalı sayılır
        logger.exception("TEFAS çekimi beklenmeyen şekilde başarısız")
        rows, errors = [], {f: str(exc) for f in fetch_funds}

    new_fund_dates = database.save_tefas_rows(conn, rows,
                                              tefas_service.label_for)
    since_iso = (today - _td(days=config.TEFAS_LOOKBACK_DAYS)).isoformat()

    titles = {r["fund_code"]: r["fund_title"] for r in rows if r.get("fund_code")}
    funds, weekly, fund_changes = {}, {}, {}
    seen_keys = set()
    for fund in config.TEFAS_FUNDS:
        alloc = database.load_tefas_allocations(conn, fund, since_iso)
        for day_items in alloc.values():
            seen_keys.update(day_items)
        latest, prev, changes = analyzer.tefas_daily_changes(
            alloc, config.TEFAS_THRESHOLD_PP)
        updated_today = bool(latest) and (
            (fund, latest) in new_fund_dates or is_test)
        if not titles.get(fund):
            row = conn.execute(
                "SELECT fund_title FROM tefas_allocations WHERE fund_code = ? "
                "ORDER BY date DESC LIMIT 1", (fund,)).fetchone()
            titles[fund] = row["fund_title"] if row else ""
        funds[fund] = {
            "title": titles.get(fund, ""),
            "latest": latest, "prev": prev,
            "changes": changes if updated_today else [],
            "updated_today": updated_today,
        }
        if updated_today:
            fund_changes[fund] = changes
        first, last, moves = analyzer.tefas_weekly_summary(
            alloc, config.TEFAS_THRESHOLD_PP)
        if first:
            weekly[fund] = (first, last, moves)
        for c in changes:
            seen_keys.add(c["key"])

    labels = {k: tefas_service.label_for(k) for k in seen_keys}
    labels.update({k: v for k, v in tefas_service.CATEGORY_LABELS.items()})
    return {
        "funds": funds,
        "weekly": weekly,
        "biggest": analyzer.tefas_biggest_moves(fund_changes),
        "errors": errors,
        "labels": labels,
        "threshold": config.TEFAS_THRESHOLD_PP,
    }


def run(is_test=False, preview_only=False):
    today = date.today()
    since = today - timedelta(days=config.LOOKBACK_DAYS)

    conn = database.get_connection()

    # ------------------------------------------------------------------
    # 1) KAP'tan son 1 haftanın bildirim listesini çek
    # ------------------------------------------------------------------
    try:
        records = kap_service.fetch_week(today)
    except KapAccessError as exc:
        logger.error("KAP verisi alınamadı: %s", exc)
        # Eski veriler yeni veriymiş gibi raporlanmaz; açık hata maili gönderilir.
        subject = (f"KAP Fon Repo/Ters Repo Raporu – "
                   f"{today.strftime('%d.%m.%Y')} – VERİ ALINAMADI")
        body = email_service.build_error_html(today, str(exc))
        save_report_site(body, {}, today, archive=False)
        if not preview_only and email_configured():
            try:
                email_service.send_email(subject, body)
            except RuntimeError as mail_exc:
                logger.error("Hata maili de gönderilemedi: %s", mail_exc)
        return 1

    # ------------------------------------------------------------------
    # 1b) Fon filtrelerini uygula (config: şirket + döviz koşulu, TL hariç)
    # ------------------------------------------------------------------
    before = len(records)
    records = [r for r in records if analyzer.fund_matches_filters(r["fund_title"])]
    logger.info("Fon filtresi: %d bildirimden %d tanesi kapsamda "
                "(Ak/İş/Garanti Portföy + döviz).", before, len(records))

    # ------------------------------------------------------------------
    # 2) Yeni bildirimlerin detay metnini çek (var olanlar atlanır)
    # ------------------------------------------------------------------
    known = database.known_indexes(conn)
    new_records = [r for r in records if r["disclosure_index"] not in known]
    logger.info("Listelenen bildirim: %d, veritabanında olmayan: %d",
                len(records), len(new_records))
    kap_service.fill_detail_texts(new_records)

    # ------------------------------------------------------------------
    # 3) Ayrıştır ve kaydet (duplicate koruması: PRIMARY KEY + OR IGNORE)
    # ------------------------------------------------------------------
    for rec in new_records:
        analyzer.parse_record(rec)
    database.save_records(conn, new_records)

    # ------------------------------------------------------------------
    # 4) Analiz penceresini veritabanından oku
    # ------------------------------------------------------------------
    # Filtreler DB'den okurken de uygulanır; böylece config'te filtre
    # değiştirildiğinde eski kayıtlar da yeni kurala göre elenir/aday olur.
    window = [
        r for r in database.load_records_since(conn, since)
        if analyzer.fund_matches_filters(r.get("fund_title"))
        and analyzer.record_passes_currency_filter(r)
    ]

    def pub_day(rec):
        pd = analyzer.publish_date_of(rec)
        return pd.date() if pd else None

    today_records = [r for r in window if pub_day(r) == today]

    report_day = today
    report_note = None
    if not today_records and (is_test or config.REPORT_USE_LATEST_DAY):
        # Sabah çalıştırmalarında (örn. 08:00) o günün bildirimleri henüz
        # yayımlanmamış olur; bu durumda SON YAYIN GÜNÜ raporlanır ve bu
        # raporda açıkça belirtilir. Eski veri "bugünün verisi" gibi sunulmaz.
        days = sorted({pub_day(r) for r in window if pub_day(r)}, reverse=True)
        if days:
            report_day = days[0]
            today_records = [r for r in window if pub_day(r) == report_day]
            report_note = (f"Bu çalışma anında {today.strftime('%d.%m.%Y')} "
                           f"tarihli KAP bildirimi bulunmadığından rapor, son "
                           f"yayın günü olan {report_day.strftime('%d.%m.%Y')} "
                           f"tarihli bildirimleri kapsamaktadır.")
            logger.info("Son yayın günü raporlanıyor: %s", report_day)

    history_records = [
        r for r in window
        if r not in today_records and pub_day(r) is not None
    ]

    # ------------------------------------------------------------------
    # 5) Değişiklik tespiti + haftalık takip + özet
    # ------------------------------------------------------------------
    change_results = analyzer.detect_changes(today_records, history_records)
    tracking = analyzer.weekly_fund_tracking(window)
    summary = analyzer.market_summary(today_records, change_results)

    # ------------------------------------------------------------------
    # 5b) TEFAS portföy dağılım takibi (ONK, PAL, OBR, GRO, GPL) +
    #     haftalık KAP ayrıntı fonlarının repo/ters repo ağırlıkları
    # ------------------------------------------------------------------
    detail_funds = email_service.weekly_detail_fund_codes(tracking)
    tefas = build_tefas_data(conn, today, is_test=is_test,
                             extra_funds=detail_funds)
    tefas["kap_weights"], tefas["kap_alloc"] = build_kap_tefas_weights(
        conn, today, detail_funds)
    chart_images = build_charts(conn, today, detail_funds)

    # ------------------------------------------------------------------
    # 6) Raporu üret ve gönder
    # ------------------------------------------------------------------
    subject = (("[TEST] " if is_test else "")
               + f"KAP Fon Repo/Ters Repo Günlük Raporu – "
                 f"{today.strftime('%d.%m.%Y')}")
    body = email_service.build_report_html(
        today, change_results, tracking, summary, is_test=is_test,
        tefas=tefas, chart_funds=set(chart_images), note=report_note,
    )

    # Rapor HER ZAMAN yerel rapor sitesine yazılır (reports/son_rapor.html +
    # günlük arşiv + index). E-posta yalnızca .env'de SMTP ayarlıysa gönderilir;
    # ayarlı değilse program hata vermez - takip yerel sayfadan yapılır.
    save_report_site(body, chart_images, today)

    if preview_only:
        logger.info("Önizleme modu: e-posta gönderilmedi.")
        return 0

    if email_configured():
        email_service.send_email(subject, body, inline_images={
            f"chart_{fund}": png for fund, png in chart_images.items()})
    else:
        logger.info("SMTP ayarlanmamış; e-posta atlanıyor. Rapor: %s",
                    config.REPORTS_DIR / "son_rapor.html")
    return 0


def _embed_charts(html_body, chart_images):
    """cid: referanslarını tarayıcının gösterebileceği data: URI'lerine
    çevirir (e-postada cid + gömülü resim, dosyada data URI kullanılır)."""
    import base64

    for fund, png in (chart_images or {}).items():
        b64 = base64.b64encode(png).decode("ascii")
        html_body = html_body.replace(
            f'src="cid:chart_{fund}"', f'src="data:image/png;base64,{b64}"')
    return html_body


def _rebuild_index(reports_dir):
    """reports/index.html - yerel takip sitesinin ana sayfası: en güncel
    rapora büyük bir bağlantı + günlük arşiv listesi."""
    files = sorted(reports_dir.glob("rapor_*.html"), reverse=True)
    files = files[:config.REPORTS_INDEX_MAX]
    items = []
    for f in files:
        day = f.stem.replace("rapor_", "")
        try:
            from datetime import date as _date
            label = _date.fromisoformat(day).strftime("%d.%m.%Y")
        except ValueError:
            label = day
        items.append(f'<li style="margin:6px 0;"><a href="{f.name}" '
                     f'style="color:#1a5eb8;">{label} raporu</a></li>')
    html = f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<title>KAP + TEFAS Fon Monitörü</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#f4f6f9;
             margin:0;padding:24px;">
<div style="max-width:700px;margin:0 auto;background:#fff;padding:24px;
            border:1px solid #dde3ec;">
<h1 style="color:#1a3c6e;font-size:20px;margin:0 0 14px 0;">
KAP + TEFAS Fon Monitörü</h1>
<p><a href="son_rapor.html" style="display:inline-block;background:#1a3c6e;
color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;
font-weight:bold;">En güncel raporu aç</a></p>
<h2 style="color:#1a3c6e;font-size:16px;border-bottom:2px solid #1a3c6e;
padding-bottom:4px;">Günlük arşiv</h2>
<ul style="padding-left:18px;">{''.join(items) or '<li>Henüz rapor yok.</li>'}</ul>
<p style="color:#999;font-size:11px;">Bu sayfalar bilgisayarınızda otomatik
üretilir; her çalıştırmada güncellenir.</p>
</div></body></html>"""
    (reports_dir / "index.html").write_text(html, encoding="utf-8")


_PRINT_TOOLBAR = """
<style>
@media print {
  .no-print { display: none !important; }
  body { background: #ffffff !important; padding: 0 !important; }
  tr { page-break-inside: avoid; }
  img { max-width: 100% !important; }
}
</style>
<div class="no-print" style="max-width:1000px;margin:0 auto 10px auto;
     text-align:right;font-family:Segoe UI,Arial,sans-serif;">
  <button onclick="window.print()" style="background:#1a3c6e;color:#ffffff;
    border:none;padding:9px 16px;border-radius:6px;font-size:13px;
    font-weight:bold;cursor:pointer;">PDF olarak indir</button>
  <div style="font-size:11px;color:#888;margin-top:3px;">
    Açılan pencerede yazıcı olarak "PDF olarak kaydet" seçin.</div>
</div>
"""


def _add_print_button(html_body):
    """Rapor sayfasına 'PDF olarak indir' düğmesi ekler (yalnızca web
    sayfasında; e-postaya eklenmez çünkü e-posta istemcileri JS çalıştırmaz).
    Düğme tarayıcının yazdırma penceresini açar; oradan PDF kaydedilir."""
    marker = "<body"
    idx = html_body.find(marker)
    if idx == -1:
        return _PRINT_TOOLBAR + html_body
    end = html_body.find(">", idx)
    return html_body[:end + 1] + _PRINT_TOOLBAR + html_body[end + 1:]


def save_report_site(html_body, chart_images, today, archive=True):
    """Raporu yerel rapor sitesine yazar ve dosya yolunu döndürür."""
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    final_html = _add_print_button(_embed_charts(html_body, chart_images))
    latest = config.REPORTS_DIR / "son_rapor.html"
    latest.write_text(final_html, encoding="utf-8")
    if archive:
        dated = config.REPORTS_DIR / f"rapor_{today.isoformat()}.html"
        dated.write_text(final_html, encoding="utf-8")
    _rebuild_index(config.REPORTS_DIR)
    logger.info("Rapor yerel siteye yazıldı: %s", latest)
    return latest


def email_configured():
    return bool(config.SMTP_SERVER and config.EMAIL_FROM and config.EMAIL_TO)


def main():
    parser = argparse.ArgumentParser(
        description="KAP Yatırım Fonları Borsa Dışı Repo/Ters Repo monitörü"
    )
    parser.add_argument("--test", action="store_true",
                        help="Test raporu gönder (kurulum doğrulaması için)")
    parser.add_argument("--preview", action="store_true",
                        help="E-posta gönderme; raporu HTML dosyasına yaz")
    args = parser.parse_args()

    setup_logging()
    logger.info("=== Çalışma başladı (test=%s, preview=%s) ===",
                args.test, args.preview)
    try:
        code = run(is_test=args.test, preview_only=args.preview)
    except Exception:
        logger.exception("Beklenmeyen hata")
        code = 1
    logger.info("=== Çalışma bitti (çıkış kodu %d) ===", code)
    return code


if __name__ == "__main__":
    sys.exit(main())

"""
HTML rapor üretimi ve e-posta gönderimi.

Rapor sırası (kullanıcı gereksinimi):
  1. Önemli Değişiklikler   (BUGÜN NE DEĞİŞTİ?)
  2. Günün İşlemleri        (tablo)
  3. Son 1 Haftalık Fon Takibi
  4. Kısa Piyasa Özeti      (yalnızca sayısal, verilerden)
"""
import html
import logging
import smtplib
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import analyzer
import config

logger = logging.getLogger(__name__)

_STYLE_TABLE = (
    "border-collapse:collapse;width:100%;font-size:13px;"
    "font-family:Segoe UI,Arial,sans-serif;"
)
_STYLE_TH = (
    "background:#1a3c6e;color:#ffffff;padding:6px 8px;text-align:left;"
    "border:1px solid #c9d4e4;white-space:nowrap;"
)
_STYLE_TD = "padding:6px 8px;border:1px solid #c9d4e4;vertical-align:top;"
_STYLE_TD_NUM = _STYLE_TD + "text-align:right;white-space:nowrap;"
_STYLE_H2 = (
    "font-family:Segoe UI,Arial,sans-serif;color:#1a3c6e;font-size:17px;"
    "margin:26px 0 8px 0;border-bottom:2px solid #1a3c6e;padding-bottom:4px;"
)


def _esc(value):
    return html.escape(str(value)) if value is not None else ""


def _kap_link(rec):
    return (f'<a href="{_esc(rec.get("kap_link"))}" '
            f'style="color:#1a5eb8;text-decoration:none;">KAP Bildirimi</a>')


def _or_missing(value):
    return _esc(value) if value else "Belirtilmemiş"


def _direction_html(direction):
    """İşlem türü hücresi: Repo yeşil, Ters Repo kırmızı."""
    if direction == "Repo":
        return '<span style="color:#0a7a3d;font-weight:bold;">Repo</span>'
    if direction == "Ters Repo":
        return '<span style="color:#b02a2a;font-weight:bold;">Ters Repo</span>'
    return _or_missing(direction)


def _change_cell(result):
    if result is None:
        return "—"
    if result["status"] == "new":
        return '<span style="color:#0a7a3d;font-weight:bold;">Yeni işlem</span>'
    if result["status"] == "changed":
        parts = []
        for c in result["changes"]:
            if c["field"] == "rate":
                bp = c["bp"]
                color = "#b02a2a" if bp > 0 else "#0a7a3d"
                parts.append(f'<span style="color:{color};font-weight:bold;">'
                             f'{"+" if bp >= 0 else ""}{bp} bp</span>')
            elif c["field"] == "counterparty":
                parts.append("Kurum değişti")
            elif c["field"] == "amount":
                parts.append("Tutar değişti")
            elif c["field"] == "direction":
                parts.append("İşlem türü değişti")
        return ", ".join(parts)
    if result["status"] == "same":
        return "Değişiklik yok"
    return "—"


def _row_html(rec, change_result=None):
    cells = [
        f'<td style="{_STYLE_TD}">{analyzer.fmt_date(rec.get("trade_date"))}</td>',
        f'<td style="{_STYLE_TD}">{_esc(rec.get("fund_title"))}</td>',
        f'<td style="{_STYLE_TD}">{_or_missing(rec.get("fund_code"))}</td>',
        f'<td style="{_STYLE_TD}">{_or_missing(rec.get("management_company"))}</td>',
        f'<td style="{_STYLE_TD}">{_direction_html(rec.get("direction"))}</td>',
        f'<td style="{_STYLE_TD}">{_or_missing(rec.get("counterparty"))}</td>',
        f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_rate(rec.get("rate"))}</td>',
        f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_term(rec.get("term_days"))}</td>',
        f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_amount(rec.get("amount"), rec.get("currency"))}</td>',
        f'<td style="{_STYLE_TD}">{_change_cell(change_result)}</td>',
        f'<td style="{_STYLE_TD}">{_kap_link(rec)}</td>',
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _important_changes_html(change_results):
    changed = [res for res in change_results if res["status"] == "changed"]

    def sort_key(res):
        # Önce oran değişimleri (mutlak bp'ye göre büyükten küçüğe),
        # sonra karşı kurum, sonra diğerleri.
        bps = [abs(c["bp"]) for c in res["changes"] if c["field"] == "rate"]
        has_cp = any(c["field"] == "counterparty" for c in res["changes"])
        return (0 if bps else (1 if has_cp else 2), -(max(bps) if bps else 0))

    items = []
    seen = set()
    for res in sorted(changed, key=sort_key):
        rec = res["record"]
        fund = _esc(rec.get("fund_code") or rec.get("fund_title"))
        labels = "; ".join(_esc(c["label"]) for c in res["changes"])
        # Aynı fonun birebir aynı değişikliği (KAP'ta mükerrer gönderilen
        # bildirimler) listede yalnızca bir kez gösterilir.
        dedupe_key = (fund, labels)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(
            f'<li style="margin:6px 0;"><b>{fund}</b> '
            f'({_esc(rec.get("fund_title"))}): {labels} &nbsp;{_kap_link(rec)}</li>'
        )
    if not items:
        return ('<p style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
                'background:#eef5ee;border:1px solid #b9d8b9;padding:10px;">'
                'Bugün önemli bir oran, karşı kurum, vade veya tutar değişikliği '
                'tespit edilmedi.</p>')
    return ('<ul style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            'padding-left:18px;">' + "".join(items) + "</ul>")


def _daily_table_html(change_results):
    if not change_results:
        return ('<p style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;">'
                'Bugün kriterlere uyan yeni bildirim yayımlanmadı.</p>')
    headers = ["İşlem Tarihi", "Fon", "Fon Kodu", "Portföy Yönetim Şirketi",
               "İşlem", "Karşı Kurum", "Oran", "Vade", "Tutar", "Değişiklik", "Link"]
    head = "".join(f'<th style="{_STYLE_TH}">{h}</th>' for h in headers)

    # Tablo her zaman İŞLEM TARİHİNE göre sıralanır (aynı gün içinde fon
    # koduna, sonra işlem türüne göre). İşlem tarihi belirtilmemişse bildirim
    # günü kullanılır.
    def sort_key(res):
        rec = res["record"]
        date_iso = rec.get("trade_date")
        if not date_iso:
            pd = analyzer.publish_date_of(rec)
            date_iso = pd.date().isoformat() if pd else "9999-12-31"
        return (date_iso, rec.get("fund_code") or "", rec.get("direction") or "")

    ordered = sorted(change_results, key=sort_key)
    rows = "".join(_row_html(res["record"], res) for res in ordered)
    return (f'<div style="overflow-x:auto;"><table style="{_STYLE_TABLE}">'
            f"<tr>{head}</tr>{rows}</table></div>")


def weekly_detail_fund_codes(tracking):
    """Haftalık KAP takibinde ayrıntılı tablo alacak fon kodları.
    (main.py bu listeyi TEFAS repo/ters repo ağırlıklarını çekmek için de
    kullanır - iki taraf aynı seçim mantığını paylaşır.)"""
    def fund_of(t):
        return (t.get("fund_code") or "").strip()

    def has_change(t):
        return bool(t["summary"]) and ("→" in t["summary"]
                                       or "karşı kurumlar" in t["summary"])

    return [fund_of(t) for t in tracking
            if fund_of(t) not in config.KAP_WEEKLY_DETAIL_EXCLUDE
            and (fund_of(t) in config.KAP_WEEKLY_DETAIL_ALWAYS
                 or has_change(t))]


def _tefas_weight_line(tefas, fund_code):
    """Fonun 1 önceki iş günü TEFAS'taki Repo / Ters-Repo portföy ağırlığı.
    Veri yoksa açık not döner; asla tahmin edilmez."""
    if tefas is None:
        return ""
    weights = (tefas.get("kap_weights") or {}).get(fund_code)
    if not weights or not weights.get("date"):
        return ('<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;'
                'color:#888;margin:4px 0 0 0;">TEFAS portföy ağırlığı: önceki '
                'iş günü verisi bulunamadı.</p>')
    tr_v = weights.get("tr")
    r_v = weights.get("r")
    parts = [
        '<span style="color:#b02a2a;font-weight:bold;">Ters-Repo</span> '
        + (analyzer.fmt_pct_tr(tr_v) if tr_v is not None else "%0"),
        '<span style="color:#0a7a3d;font-weight:bold;">Repo</span> '
        + (analyzer.fmt_pct_tr(r_v) if r_v is not None else "%0"),
    ]
    return ('<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;'
            'color:#444;margin:4px 0 0 0;">'
            f"TEFAS portföy ağırlığı ({analyzer.fmt_date(weights['date'])}): "
            + " &nbsp;|&nbsp; ".join(parts) + "</p>")


def _tefas_row_weight_cell(tefas, fund_code, date_iso, direction):
    """İşlem satırının gününde fonun TEFAS'taki Repo/Ters-Repo ağırlığı.

    Satırın işlem türüne göre ilgili kalem (Ters Repo -> 'tr', Repo -> 'r')
    o günün TEFAS verisinden okunur. O gün için TEFAS verisi yoksa en yakın
    ÖNCEKİ mevcut gün kullanılır ve tarihi parantez içinde belirtilir.
    Hiç veri yoksa '—' gösterilir; asla tahmin edilmez."""
    if tefas is None or not date_iso or direction not in ("Repo", "Ters Repo"):
        return "—"
    alloc = (tefas.get("kap_alloc") or {}).get(fund_code) or {}
    if not alloc:
        return "—"
    key = "tr" if direction == "Ters Repo" else "r"
    day_used = date_iso if date_iso in alloc else None
    if day_used is None:
        earlier = [d for d in sorted(alloc) if d < date_iso]
        if not earlier:
            return "—"
        day_used = earlier[-1]
    v = alloc[day_used].get(key)
    val = analyzer.fmt_pct_tr(v) if v is not None else "%0"
    if day_used != date_iso:
        val += (f' <span style="color:#888;font-size:11px;">'
                f"({analyzer.fmt_date(day_used)})</span>")
    return val


def _chart_html(fund_code, chart_funds):
    """Fonun son 3 aylık TEFAS Repo/Ters-Repo ağırlık grafiği (varsa)."""
    if not chart_funds or fund_code not in chart_funds:
        return ""
    return (f'<div style="margin:8px 0;"><img src="cid:chart_{fund_code}" '
            f'alt="{fund_code} TEFAS Repo/Ters-Repo ağırlık grafiği (son 3 ay)" '
            'style="max-width:100%;border:1px solid #dde3ec;"></div>')


def _weekly_tracking_html(tracking, tefas=None, chart_funds=None):
    if not tracking:
        return ('<p style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;">'
                'Son 1 haftada birden fazla gün işlem bildiren fon bulunmuyor.</p>')

    # Raporu gereksiz şişirmemek için (ve e-posta boyutu sınırları nedeniyle)
    # ayrıntılı tablo yalnızca hafta içinde DEĞİŞİKLİK görülen fonlara verilir;
    # hafta boyunca sabit kalan fonlar tek satırlık kompakt listede özetlenir.
    # config.KAP_WEEKLY_DETAIL_ALWAYS fonları her durumda ayrıntılı gösterilir;
    # config.KAP_WEEKLY_DETAIL_EXCLUDE fonları hiçbir zaman ayrıntılı
    # gösterilmez (kompakt listede kalır).
    detail_codes = set(weekly_detail_fund_codes(tracking))
    changed = [t for t in tracking
               if (t.get("fund_code") or "").strip() in detail_codes]
    stable = [t for t in tracking if t not in changed]

    blocks = []
    for t in changed:
        fund_code = (t.get("fund_code") or "").strip()
        headers = ["Tarih", "İşlem", "Karşı Kurum", "Oran", "Vade", "Tutar",
                   "Fon İçindeki Ağırlık (TEFAS)", "Link"]
        head = "".join(f'<th style="{_STYLE_TH}">{h}</th>' for h in headers)
        rows = []
        for rec in t["rows"]:
            pd = analyzer.publish_date_of(rec)
            day = (analyzer.fmt_date(rec.get("trade_date"))
                   if rec.get("trade_date")
                   else (pd.strftime("%d.%m.%Y") if pd else "Belirtilmemiş"))
            date_iso = rec.get("trade_date") or (
                pd.date().isoformat() if pd else None)
            weight_cell = _tefas_row_weight_cell(
                tefas, fund_code, date_iso, rec.get("direction"))
            rows.append(
                "<tr>"
                f'<td style="{_STYLE_TD}">{day}</td>'
                f'<td style="{_STYLE_TD}">{_direction_html(rec.get("direction"))}</td>'
                f'<td style="{_STYLE_TD}">{_or_missing(rec.get("counterparty"))}</td>'
                f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_rate(rec.get("rate"))}</td>'
                f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_term(rec.get("term_days"))}</td>'
                f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_amount(rec.get("amount"), rec.get("currency"))}</td>'
                f'<td style="{_STYLE_TD_NUM}">{weight_cell}</td>'
                f'<td style="{_STYLE_TD}">{_kap_link(rec)}</td>'
                "</tr>"
            )
        summary_html = ""
        if t["summary"]:
            summary_html = (
                '<p style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;'
                'margin:6px 0 0 0;"><b>Haftalık değişim:</b> '
                f'{_esc(t["summary"])}</p>'
            )
        blocks.append(
            '<h3 style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;'
            'color:#333;margin:18px 0 6px 0;">'
            f'{_esc(t.get("fund_code") or "")} – {_esc(t.get("fund_title"))}</h3>'
            f'<div style="overflow-x:auto;"><table style="{_STYLE_TABLE}">'
            f"<tr>{head}</tr>{''.join(rows)}</table></div>"
            f'{_tefas_weight_line(tefas, fund_code)}'
            f"{_chart_html(fund_code, chart_funds)}"
            f"{summary_html}"
        )

    if not changed:
        blocks.append(
            '<p style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;">'
            "Son 1 haftada fon bazında oran, karşı kurum veya tutar değişikliği "
            "görülmedi.</p>"
        )
    if stable:
        stable_items = ", ".join(
            f"<b>{_esc(t.get('fund_code') or t.get('fund_title'))}</b> "
            f"({_esc(analyzer.fmt_rate(t['rows'][-1].get('rate')))})"
            for t in stable
        )
        blocks.append(
            '<p style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;'
            'color:#444;margin-top:14px;"><b>Hafta boyunca koşulları değişmeyen '
            f"fonlar:</b> {stable_items}</p>"
        )
    return "".join(blocks)


def _summary_html(summary):
    lines = [
        f"Bugün <b>{summary['n_funds']}</b> fon toplam <b>{summary['n_records']}</b> "
        f"bildirim yayımladı.",
        f"İşlemlerin <b>{summary['n_ters_repo']}</b> adedi ters repo, "
        f"<b>{summary['n_repo']}</b> adedi repo"
        + (f", <b>{summary['n_unknown_direction']}</b> adedinin türü metinden "
           f"belirlenemedi." if summary["n_unknown_direction"] else "."),
        f"Oranı değişen fon sayısı: <b>{summary['n_funds_rate_changed']}</b>.",
        f"Karşı kurum değiştiren fon sayısı: <b>{summary['n_funds_cp_changed']}</b>.",
        f"İlk kez işlem bildiren fon sayısı: <b>{summary['n_new_funds']}</b>.",
    ]
    if summary["max_bp"] is not None:
        sign = "+" if summary["max_bp"] >= 0 else ""
        lines.append(
            f"En büyük oran değişimi: <b>{_esc(summary['max_bp_fund'])}</b> "
            f"({sign}{summary['max_bp']} bp)."
        )
    items = "".join(f'<li style="margin:4px 0;">{line}</li>' for line in lines)
    return ('<ul style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            f'padding-left:18px;">{items}</ul>')


def _status_cell(change):
    if change["kind"] == "new":
        return '<span style="color:#0a7a3d;font-weight:bold;">Yeni pozisyon</span>'
    if change["kind"] == "closed":
        return '<span style="color:#b02a2a;font-weight:bold;">Pozisyon kapandı</span>'
    if change["delta"] > 0:
        return '<span style="color:#b02a2a;">&#128314; Arttı</span>'
    return '<span style="color:#0a7a3d;">&#128315; Azaldı</span>'


def _fmt_delta_html(delta):
    color = "#b02a2a" if delta > 0 else "#0a7a3d"
    return (f'<span style="color:{color};font-weight:bold;">'
            f"{analyzer.fmt_pp(delta)}</span>")


def _threshold_label(tefas):
    """Eşiği Türkçe biçimde yazar (0.25 -> '0,25')."""
    t = (tefas or {}).get("threshold", 0.25)
    return f"{t:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _tefas_label(tefas, key):
    return _esc(tefas["labels"].get(key, key))


def _tefas_change_line(tefas, change):
    if change["kind"] == "new":
        return (f"{_tefas_label(tefas, change['key'])}: Yeni pozisyon, "
                f"%0 → {analyzer.fmt_pct_tr(change['new'])} "
                f"({_fmt_delta_html(change['delta'])})")
    if change["kind"] == "closed":
        return (f"{_tefas_label(tefas, change['key'])}: Pozisyon kapandı, "
                f"{analyzer.fmt_pct_tr(change['old'])} → %0 "
                f"({_fmt_delta_html(change['delta'])})")
    return (f"{_tefas_label(tefas, change['key'])}: "
            f"{analyzer.fmt_pct_tr(change['old'])} → "
            f"{analyzer.fmt_pct_tr(change['new'])} "
            f"({_fmt_delta_html(change['delta'])})")


def _important_tefas_html(tefas):
    if tefas is None:
        return ""
    items = []
    for fund in sorted(tefas["funds"]):
        info = tefas["funds"][fund]
        if not info.get("updated_today"):
            continue
        for c in info["changes"]:
            items.append(f'<li style="margin:6px 0;"><b>{_esc(fund)}</b>: '
                         f"{_tefas_change_line(tefas, c)}</li>")
    if not items:
        return ('<p style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
                'background:#eef5ee;border:1px solid #b9d8b9;padding:10px;">'
                f"TEFAS tarafında eşik üstü (&gt;{_threshold_label(tefas)} pp) "
                "dağılım değişikliği tespit edilmedi.</p>")
    return ('<ul style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            'padding-left:18px;">' + "".join(items) + "</ul>")


def _tefas_section_html(tefas):
    if tefas is None:
        return ('<p style="font-family:Segoe UI,Arial,sans-serif;'
                'font-size:14px;">TEFAS takibi bu çalışmada yapılamadı.</p>')
    parts = []

    # Veri güncellenmedi / hata notları
    notes = []
    for fund in sorted(tefas["funds"]):
        info = tefas["funds"][fund]
        if not info.get("latest"):
            notes.append(f"<b>{_esc(fund)}</b>: TEFAS verisi bulunamadı.")
        elif not info.get("updated_today"):
            notes.append(f"<b>{_esc(fund)}</b>: TEFAS verisi henüz "
                         f"güncellenmedi (son veri: "
                         f"{analyzer.fmt_date(info['latest'])}).")
    for fund, msg in sorted(tefas.get("errors", {}).items()):
        notes.append(f"<b>{_esc(fund)}</b>: TEFAS'a erişilemedi.")
    if notes:
        parts.append('<p style="background:#fff3cd;border:1px solid #e0c869;'
                     'padding:8px;font-family:Segoe UI,Arial,sans-serif;'
                     'font-size:13px;">' + "<br>".join(notes) + "</p>")

    # Detaylı değişiklik tablosu
    rows = []
    for fund in sorted(tefas["funds"]):
        info = tefas["funds"][fund]
        if not info.get("updated_today"):
            continue
        for c in info["changes"]:
            rows.append(
                "<tr>"
                f'<td style="{_STYLE_TD}">{_esc(fund)}</td>'
                f'<td style="{_STYLE_TD}">{_tefas_label(tefas, c["key"])}</td>'
                f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_pct_tr(c["old"])}</td>'
                f'<td style="{_STYLE_TD_NUM}">{analyzer.fmt_pct_tr(c["new"])}</td>'
                f'<td style="{_STYLE_TD_NUM}">{_fmt_delta_html(c["delta"])}</td>'
                f'<td style="{_STYLE_TD}">{_status_cell(c)}</td>'
                "</tr>"
            )
    if rows:
        headers = ["Fon", "Dağılım Kalemi", "Önceki", "Güncel", "Değişim", "Durum"]
        head = "".join(f'<th style="{_STYLE_TH}">{h}</th>' for h in headers)
        # Karşılaştırılan günler
        day_info = []
        for fund in sorted(tefas["funds"]):
            info = tefas["funds"][fund]
            if info.get("updated_today") and info.get("prev"):
                day_info.append(
                    f"{_esc(fund)}: {analyzer.fmt_date(info['prev'])} → "
                    f"{analyzer.fmt_date(info['latest'])}")
        parts.append(
            '<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;'
            'color:#666;">Karşılaştırılan veri günleri — '
            + "; ".join(day_info) + "</p>"
            f'<div style="overflow-x:auto;"><table style="{_STYLE_TABLE}">'
            f"<tr>{head}</tr>{''.join(rows)}</table></div>"
        )
    elif not notes:
        parts.append('<p style="font-family:Segoe UI,Arial,sans-serif;'
                     f'font-size:14px;">Eşik üstü (&gt;{_threshold_label(tefas)} '
                     "pp) değişiklik yok.</p>")

    # Fon bazında özet
    fund_lines = []
    for fund in sorted(tefas["funds"]):
        info = tefas["funds"][fund]
        if not info.get("updated_today"):
            continue
        if info["changes"]:
            items = "".join(f'<li style="margin:3px 0;">'
                            f"{_tefas_change_line(tefas, c)}</li>"
                            for c in info["changes"])
            body = f'<ul style="margin:4px 0;padding-left:18px;">{items}</ul>'
        else:
            body = ('<p style="margin:4px 0;color:#555;">Anlamlı değişiklik '
                    'yok.</p>')
        fund_lines.append(
            '<h4 style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            f'margin:10px 0 2px 0;">{_esc(fund)} – '
            f'{_esc(info.get("title") or "")}</h4>'
            f'<div style="font-family:Segoe UI,Arial,sans-serif;'
            f'font-size:13px;">{body}</div>'
        )
    if fund_lines:
        parts.append("".join(fund_lines))

    # En büyük değişiklikler
    biggest = tefas.get("biggest") or {}
    big_lines = []
    label_map = [("increase", "En büyük artış"), ("decrease", "En büyük azalış"),
                 ("new", "En büyük yeni pozisyon"),
                 ("closed", "En büyük kapanan pozisyon")]
    for kind, label in label_map:
        entry = biggest.get(kind)
        if entry:
            fund, c = entry
            big_lines.append(
                f'<li style="margin:4px 0;"><b>{label}:</b> {_esc(fund)} – '
                f"{_tefas_label(tefas, c['key'])} {_fmt_delta_html(c['delta'])}"
                "</li>")
    if big_lines:
        parts.append(
            '<h4 style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            'margin:14px 0 4px 0;">En Büyük TEFAS Değişiklikleri</h4>'
            '<ul style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;'
            'padding-left:18px;">' + "".join(big_lines) + "</ul>")

    return "".join(parts)


def _combined_weekly_html(tracking, tefas):
    """KAP + TEFAS verilerinden yalnızca hesaplanabilir haftalık cümleler."""
    sentences = []
    if tefas is not None:
        for fund in sorted(tefas.get("weekly", {})):
            first, last, moves = tefas["weekly"][fund]
            if not moves:
                continue
            frags = [f"{_tefas_label(tefas, m['key'])} ağırlığı "
                     f"{analyzer.fmt_pp(m['delta'])} "
                     f"({analyzer.fmt_pct_tr(m['old'])} → "
                     f"{analyzer.fmt_pct_tr(m['new'])})"
                     for m in moves[:4]]
            sentences.append(
                f"<b>{_esc(fund)}</b> fonunda {analyzer.fmt_date(first)} – "
                f"{analyzer.fmt_date(last)} arasında: " + "; ".join(frags) + ".")
    for t in tracking:
        if t["summary"] and "→" in t["summary"]:
            sentences.append(f"<b>{_esc(t.get('fund_code') or '')}</b> (KAP): "
                             f"{_esc(t['summary'])}")
    if not sentences:
        return ('<p style="font-family:Segoe UI,Arial,sans-serif;'
                'font-size:14px;">Bu hafta KAP ve TEFAS tarafında eşik üstü '
                'net değişim hesaplanmadı.</p>')
    items = "".join(f'<li style="margin:5px 0;">{s}</li>' for s in sentences)
    return ('<ul style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            f'padding-left:18px;">{items}</ul>')


def build_report_html(report_date, change_results, tracking, summary,
                      is_test=False, note=None, tefas=None, chart_funds=None):
    """Tam HTML raporu üretir."""
    test_banner = ""
    if is_test:
        test_banner = (
            '<p style="background:#fff3cd;border:1px solid #e0c869;padding:8px;'
            'font-family:Segoe UI,Arial,sans-serif;font-size:13px;">'
            "Bu bir <b>TEST</b> raporudur; son 1 haftalık gerçek KAP verisiyle "
            "üretilmiştir.</p>"
        )
    note_html = ""
    if note:
        note_html = (
            '<p style="background:#e8f0fb;border:1px solid #b6cdea;padding:8px;'
            'font-family:Segoe UI,Arial,sans-serif;font-size:13px;">'
            f"{_esc(note)}</p>"
        )
    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:16px;background:#f4f6f9;">
<div style="max-width:1000px;margin:0 auto;background:#ffffff;
            padding:20px;border:1px solid #dde3ec;">
<h1 style="font-family:Segoe UI,Arial,sans-serif;font-size:20px;
           color:#1a3c6e;margin:0 0 4px 0;">
KAP Fon Repo/Ters Repo Günlük Raporu – {report_date.strftime("%d.%m.%Y")}</h1>
<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#666;
          margin:0 0 12px 0;">
Kaynak: KAP (Kamuyu Aydınlatma Platformu) – Yatırım Fonları /
Borsa Dışı Repo - Ters Repo Sözleşmesi bildirimleri.
KAP bildiriminde yer almayan hiçbir değer tahmin edilmez;
bulunamayan alanlar "Belirtilmemiş" olarak gösterilir.</p>
{test_banner}{note_html}
<h2 style="{_STYLE_H2}">1. Önemli Değişiklikler</h2>
<h3 style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#333;
           margin:10px 0 4px 0;">KAP – Repo/Ters Repo</h3>
{_important_changes_html(change_results)}
<h3 style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#333;
           margin:14px 0 4px 0;">TEFAS – Portföy Dağılımı</h3>
{_important_tefas_html(tefas)}
<h2 style="{_STYLE_H2}">2. TEFAS Fon Dağılım Değişiklikleri</h2>
{_tefas_section_html(tefas)}
<h2 style="{_STYLE_H2}">3. KAP Günlük Repo/Ters Repo İşlemleri</h2>
{_daily_table_html(change_results)}
<h3 style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#333;
           margin:16px 0 4px 0;">Son 1 Haftalık KAP Fon Takibi</h3>
{_weekly_tracking_html(tracking, tefas, chart_funds)}
<h2 style="{_STYLE_H2}">4. Haftalık Özet</h2>
{_combined_weekly_html(tracking, tefas)}
{_summary_html(summary)}
<p style="font-family:Segoe UI,Arial,sans-serif;font-size:11px;color:#999;
          margin-top:20px;">Bu rapor otomatik oluşturulmuştur.
Yatırım tavsiyesi değildir.</p>
</div></body></html>"""


def build_error_html(report_date, error_message):
    """KAP verisi alınamadığında gönderilecek açık hata raporu.
    Eski veriler ASLA yeni veriymiş gibi raporlanmaz."""
    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"></head>
<body style="font-family:Segoe UI,Arial,sans-serif;padding:16px;">
<h1 style="color:#b02a2a;font-size:18px;">
KAP Fon Repo/Ters Repo Raporu – {report_date.strftime("%d.%m.%Y")} – VERİ ALINAMADI</h1>
<p>Bugünkü KAP sorgusu başarısız olduğu için rapor üretilemedi.
Eski veriler yeni veriymiş gibi <b>raporlanmadı</b>.</p>
<p><b>Hata:</b> {_esc(error_message)}</p>
<p>Program yarınki zamanlanmış çalışmada otomatik olarak tekrar deneyecektir.
Ayrıntılar için log dosyasına bakabilirsiniz (logs/kap_monitor.log).</p>
</body></html>"""


def send_email(subject, html_body, inline_images=None):
    """SMTP ile e-posta gönderir; başarısız olursa tekrar dener.

    inline_images: {content_id: png_bytes} - HTML içinde <img src="cid:...">
    ile referans verilen gömülü resimler (grafikler).
    """
    missing = [name for name, val in (
        ("SMTP_SERVER", config.SMTP_SERVER),
        ("EMAIL_FROM", config.EMAIL_FROM),
        ("EMAIL_TO", config.EMAIL_TO),
    ) if not val]
    if missing:
        raise RuntimeError(
            ".env dosyasında eksik e-posta ayarı: " + ", ".join(missing)
        )

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = ", ".join(config.EMAIL_TO)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)
    for cid, png in (inline_images or {}).items():
        img = MIMEImage(png, _subtype="png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline",
                       filename=f"{cid}.png")
        msg.attach(img)

    last_err = None
    for attempt in range(1, config.EMAIL_RETRY_COUNT + 1):
        try:
            with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT,
                              timeout=60) as server:
                if config.SMTP_USE_TLS:
                    server.starttls()
                if config.SMTP_USERNAME:
                    server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
                server.sendmail(config.EMAIL_FROM, config.EMAIL_TO,
                                msg.as_string())
            logger.info("E-posta gönderildi: %s -> %s", subject, config.EMAIL_TO)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            last_err = exc
            logger.warning("E-posta gönderilemedi (deneme %d/%d): %s",
                           attempt, config.EMAIL_RETRY_COUNT, exc)
            if attempt < config.EMAIL_RETRY_COUNT:
                time.sleep(config.EMAIL_RETRY_WAIT_SECONDS)
    raise RuntimeError(f"E-posta {config.EMAIL_RETRY_COUNT} denemede "
                       f"gönderilemedi: {last_err}")

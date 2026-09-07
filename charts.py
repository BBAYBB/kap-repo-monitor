"""
E-posta için grafik üretimi (matplotlib).

Haftalık KAP ayrıntı tablosundaki her fon için, fonun son 3 aydaki TEFAS
Repo / Ters-Repo portföy ağırlıklarının çizgi grafiği PNG olarak üretilir ve
e-postaya gömülü resim (inline, cid) olarak eklenir. E-posta istemcileri
JavaScript çalıştırmadığı için grafikler resim olarak gömülmek zorundadır.

matplotlib kurulu değilse program çökmez; grafik üretilmez ve raporda not
düşülür.
"""
import io
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")  # GUI gerektirmeyen arka uç (Task Scheduler uyumlu)
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    MATPLOTLIB_AVAILABLE = False
    logger.warning("matplotlib kurulu değil; grafikler üretilemeyecek. "
                   "Kurulum: pip install matplotlib")

# Renkler rapordaki kodlamayla tutarlı: Ters-Repo kırmızı, Repo yeşil
COLOR_TERS_REPO = "#b02a2a"
COLOR_REPO = "#0a7a3d"


def _fmt_pct(value):
    """%33,8 biçiminde Türkçe yüzde etiketi."""
    s = f"{value:.1f}".replace(".", ",")
    if s.endswith(",0"):
        s = s[:-2]
    return f"%{s}"


def _draw_panel(ax, days, values, label, color):
    """Tek seri için bir panel: kendi y ölçeği, son değer etiketi, sakin grid.

    (Repo ~-%2,5 iken Ters-Repo %30'larda olabildiği için iki seri ASLA aynı
    eksene çizilmez; her biri kendi ölçeğinde ayrı panelde gösterilir - aksi
    halde küçük ölçekli seri düz bir çizgi gibi görünüp okunamaz oluyor.)
    """
    ys = [float("nan") if v is None else v for v in values]
    ax.plot(days, ys, color=color, linewidth=2.0)
    ax.fill_between(days, ys, 0, color=color, alpha=0.08)
    ax.axhline(0, color="#999999", linewidth=0.8)

    ax.set_title(label, loc="left", fontsize=10, color=color,
                 fontweight="bold", pad=4)
    ax.grid(True, axis="y", linewidth=0.4, alpha=0.35)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _pos: _fmt_pct(v)))
    ax.tick_params(labelsize=9)

    # Son değer etiketi (seçici doğrudan etiket: her noktaya değil, yalnız sona)
    known = [(d, v) for d, v in zip(days, values) if v is not None]
    if known:
        last_day, last_val = known[-1]
        ax.annotate(_fmt_pct(last_val), xy=(last_day, last_val),
                    xytext=(6, 0), textcoords="offset points",
                    fontsize=10, fontweight="bold", color=color,
                    va="center")
        # Etiket sağ kenara taşmasın diye x eksenine küçük pay bırak
        ax.set_xlim(days[0], days[-1] + (days[-1] - days[0]) * 0.06)
        # Değer aralığına dikey nefes payı
        vmin = min(v for _, v in known)
        vmax = max(v for _, v in known)
        pad = max((vmax - vmin) * 0.15, 0.5)
        lo = min(vmin - pad, 0 if vmin >= 0 else vmin - pad)
        hi = max(vmax + pad, 0 if vmax <= 0 else vmax + pad)
        ax.set_ylim(lo, hi)


def render_repo_weight_chart(fund_code, series):
    """Fonun TEFAS Repo/Ters-Repo ağırlık serisini PNG (bytes) olarak çizer.

    series: dict[date_iso] -> {"tr": pct|None, "r": pct|None}
    Her seri KENDİ ölçeğinde ayrı panelde çizilir (ortak x ekseni). Veri
    olmayan günler (hafta sonu/tatil) atlanır; bir kalemin belirli bir günde
    değeri yoksa (None) o gün çizilmez, çizgi kesik görünür - değer
    uydurulmaz. Hiç çizilecek veri yoksa None döner.
    """
    if not MATPLOTLIB_AVAILABLE or not series:
        return None

    days, tr_vals, r_vals = [], [], []
    for day in sorted(series):
        try:
            dt = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        vals = series[day]
        days.append(dt)
        tr_vals.append(vals.get("tr"))
        r_vals.append(vals.get("r"))

    panels = []
    if any(v is not None for v in tr_vals):
        panels.append(("Ters-Repo", tr_vals, COLOR_TERS_REPO))
    if any(v is not None for v in r_vals):
        panels.append(("Repo", r_vals, COLOR_REPO))
    if not days or not panels:
        return None

    fig, axes = plt.subplots(
        len(panels), 1, figsize=(9.2, 2.3 * len(panels) + 0.6),
        dpi=110, sharex=True,
    )
    if len(panels) == 1:
        axes = [axes]
    fig.suptitle(f"{fund_code} – TEFAS Portföy Ağırlığı (son 3 ay)",
                 fontsize=11, y=0.99)

    for ax, (label, values, color) in zip(axes, panels):
        _draw_panel(ax, days, values, label, color)

    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    axes[-1].xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

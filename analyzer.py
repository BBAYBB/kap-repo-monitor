"""
Bildirim metni ayrıştırma ve değişiklik analizi.

KAP'taki "Borsa Dışı Repo - Ters Repo Sözleşmesi" bildirimlerinde işlem
bilgileri (oran, karşı kurum, vade, tutar) yapılandırılmış alanlar değildir;
her portföy yönetim şirketi kendi kalıbıyla serbest metin yazar. Bu modül bu
metinleri MUHAFAZAKÂR kurallarla ayrıştırır:

  - Emin olunamayan hiçbir alan doldurulmaz (None kalır -> raporda
    "Belirtilmemiş" gösterilir).
  - Derecelendirme kuruluşları (JCR, Fitch, Moody's, ...) asla karşı kurum
    olarak alınmaz.
  - Değişiklik tespiti yalnızca iki kayıtta da alan doluysa ve kayıtlar
    güvenilir şekilde eşleştirilebildiyse yapılır.
"""
import logging
import re
from datetime import datetime, date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metin ayrıştırma
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")

# Derecelendirme kuruluşu / kredi notu bağlamı işaretleri.
_RATING_MARKERS = (
    "derecelendirme", "tarafından belirlenen", "kredi not", "kredi rating",
    "rating notu", "uzun vadeli ulusal", "jcr", "fitch", "moody", "turkrating",
    "standard & poor", "s&p",
)

_AGENCY_WORDS = (
    "derecelendirme", "jcr", "fitch", "moody", "turkrating", "rating",
    "standard", "s&p", "dünya kredi",
)

# Şirket adı yakalama: "... A.Ş." / "... T.A.Ş." / "... A.O." ile biten öbekler
_ENTITY_RE = re.compile(
    r"((?:[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü&.\-']*\.?\s+){0,6}?"
    r"(?:T\.?\s?A\.Ş\.?|A\.Ş\.?|A\.O\.?))"
)

# "Odeabank ile ...", "ODEABANK arasında" gibi A.Ş. eki olmadan yazılan bankalar
_BARE_BANK_RE = re.compile(
    r"\b([A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü]*(?:bank|Bank|BANK)[\wÇĞİÖŞÜçğıöşü]*)"
    r"\s+(?:ile|arasında)\b"
)

# "İnfo Yatırım üzerinden ... repo" kalıbı (kurum adı 1-4 büyük harfli kelime)
_VIA_RE = re.compile(
    r"\b((?:[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.&\-']*\s+){1,4}?)üzerinden\b"
)

_AMOUNT_RE = re.compile(
    r"\b(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s*(TL|TRY|USD|EUR|GBP)\b"
)

_PCT_BEFORE_RE = re.compile(r"%\s*(\d{1,3}(?:[.,]\d{1,4})?)")
_PCT_AFTER_RE = re.compile(r"(\d{1,3}(?:[.,]\d{1,4})?)\s*%")


def _to_float(num_str):
    """Türkçe/İngilizce ondalık gösterimini float'a çevirir."""
    s = num_str.strip()
    if "," in s and "." in s:
        # 1.234,56 -> nokta binlik, virgül ondalık
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_dates(text):
    """Metindeki tarihleri rollerine göre ayırır.

    Dönen: (trade_date, maturity_date) - datetime.date veya None.
    Kurallar (bağlama göre):
      "X tarihinden Y tarihine kadar"    -> X işlem/başlangıç, Y vade
      "X başlangıçlı Y vadeli"           -> X işlem, Y vade
      "X tarihinde(,) Y vadeli"          -> X işlem, Y vade
      "X'da/de/te Y vadeli"              -> X işlem, Y vade
      tek başına "Y vadeli"              -> yalnız vade (işlem tarihi yok)
    """
    trade = None
    maturity = None
    for m in _DATE_RE.finditer(text):
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        after = text[m.end():m.end() + 22].lower()
        if re.match(r"\s*(?:tarihine|vade)", after):
            if maturity is None:
                maturity = d
        elif re.match(r"\s*(?:tarihinden|tarihinde|başlangıçlı|'?[dt][ae])", after):
            if trade is None:
                trade = d
        else:
            # Rol belirsiz: sadece hiçbir işlem tarihi yoksa ve bu tarih
            # vade tarihinden önce geliyorsa başlangıç kabul etme; tahmin yok.
            continue
    return trade, maturity


def _parse_rate(text):
    """Faiz oranını (%) bulur. Birden fazla yüzde varsa 'oran'/'faiz'
    kelimesine en yakın olanı seçer; hiç seçilemezse None döner."""
    candidates = []  # (position, value)
    for m in _PCT_BEFORE_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None:
            candidates.append((m.start(), v))
    for m in _PCT_AFTER_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None and all(abs(m.start() - p) > 2 for p, _ in candidates):
            candidates.append((m.start(), v))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][1]
    # 'oran' veya 'faiz' kelimesine en yakın adayı seç
    keyword_positions = [m.start() for m in re.finditer(r"\boran|\bfaiz", text.lower())]
    if not keyword_positions:
        return candidates[0][1]
    best = min(
        candidates,
        key=lambda c: min(abs(c[0] - k) for k in keyword_positions),
    )
    return best[1]


def _parse_counterparty(text, fund_title=""):
    """Karşı kurumu bulur. Derecelendirme kuruluşlarını ve fonun/PYŞ'nin
    kendisini eler. Güvenle bulunamazsa None döner."""
    # Kredi notu bölümünü at: ilk derecelendirme işaretinden sonrası dikkate alınmaz
    lower = text.lower()
    cut = len(text)
    for marker in _RATING_MARKERS:
        pos = lower.find(marker)
        if pos != -1:
            cut = min(cut, pos)
    head = text[:cut]

    def is_valid(name):
        n = name.lower()
        if any(w in n for w in _AGENCY_WORDS):
            return False
        if "portföy" in n or re.search(r"\bfonu?\b", n):
            return False
        return True

    for m in _ENTITY_RE.finditer(head):
        name = " ".join(m.group(1).split())
        if is_valid(name):
            return name.rstrip(",")

    m = _BARE_BANK_RE.search(head)
    if m and is_valid(m.group(1)):
        return m.group(1)

    m = _VIA_RE.search(head)
    if m:
        name = " ".join(m.group(1).split())
        # "üzerinden" öncesinde fon adı/parantez varsa alma; kısa kurum adı bekle
        if is_valid(name) and 2 <= len(name) <= 45 and "(" not in name:
            return name

    # "... derecelendirme notuna sahip X A.Ş. vasıtasıyla ters repo işlemi
    # yapılmıştır" kalıbı (örn. Azimut Portföy). Kurum adı derecelendirme
    # ifadesinden SONRA geldiği için tüm metinde aranır; 'vasıtasıyla/
    # aracılığıyla/üzerinden' sözcüğü işlemin bu kurumla yapıldığını belirtir.
    for m in _ENTITY_RE.finditer(text):
        name = " ".join(m.group(1).split())
        follow = text[m.end():m.end() + 20].lower()
        if re.match(r"\s*(?:vasıtasıyla|aracılığıyla|üzerinden)", follow) and is_valid(name):
            return name.rstrip(",")

    # Son çare: kredi notu belirtilen kurum. SPK düzenlemesi gereği bu
    # bildirimlerde karşı kurumun derecelendirme notu açıklanır; dolayısıyla
    # "X A.Ş.'nin Fitch tarafından belirlenen ... notu ..." kalıbındaki X,
    # işlemin karşı kurumudur. (Derecelendirme kuruluşunun kendisi is_valid
    # filtresiyle elenir.)
    for m in _ENTITY_RE.finditer(text):
        name = " ".join(m.group(1).split())
        if not is_valid(name):
            continue
        follow = text[m.end():m.end() + 90].lower()
        if re.match(r"\s*'?\s*n[iı]n\b", follow) and re.search(
                r"kredi|rating|not[uü]", follow):
            return name.rstrip(",")
    return None


def _parse_amount(text):
    """İşlem tutarını (para birimiyle yazılmış) bulur.
    Dönen: (amount_float, currency) veya (None, None)."""
    for m in _AMOUNT_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None:
            return v, m.group(2)
    return None, None


def _parse_direction(text, summary=""):
    """İşlem türü: 'Ters Repo' / 'Repo' / None.

    Açıklama metni esas alınır (KAP özet başlığı çoğu zaman "Repo/Ters Repo"
    şeklinde her iki türü birden içerir); metinde repo geçmiyorsa başlığa
    bakılır."""
    for source in (text, summary):
        s = (source or "").lower()
        if "repo" not in s:
            continue
        if "ters rep" in s:  # "ters repo" ve "ters rep" yazım varyasyonları
            return "Ters Repo"
        return "Repo"
    return None


def parse_management_company(fund_title):
    """Fon unvanından portföy yönetim şirketini çıkarır:
    'YAPI KREDİ PORTFÖY ... FON' -> 'Yapı Kredi Portföy'."""
    title = " ".join((fund_title or "").split())
    m = re.search(r"^(.*?PORTFÖY)", title, re.IGNORECASE)
    if not m:
        return None
    # KAP'taki yazımı olduğu gibi koru (str.title() Türkçe İ/ı harflerini bozar)
    return m.group(1)


def parse_record(rec):
    """kap_service kaydını ayrıştırıp analiz alanlarını ekler.

    Eklenen alanlar: direction, counterparty, rate, trade_date, maturity_date,
    term_days, amount, currency, management_company
    Bulunamayan her alan None kalır (raporda 'Belirtilmemiş').
    """
    text = rec.get("raw_text") or ""
    summary = rec.get("summary") or ""

    trade, maturity = _parse_dates(text)
    if trade is None and summary:
        # Bazı şirketler (örn. TEB) işlem tarihini yalnızca bildirim başlığında
        # yazar: "Borsa Dışı Repo / Ters Repo İşlemleri - 31.08.2026".
        # Bu da KAP bildiriminin kendi verisidir; tahmin değildir.
        m = _DATE_RE.search(summary)
        if m:
            try:
                cand = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                if maturity is None or cand <= maturity:
                    trade = cand
            except ValueError:
                pass
    term_days = None
    if trade and maturity:
        delta = (maturity - trade).days
        if delta >= 0:
            term_days = delta

    amount, currency = _parse_amount(text)

    rec.update({
        "direction": _parse_direction(text, summary),
        "counterparty": _parse_counterparty(text, rec.get("fund_title", "")),
        "rate": _parse_rate(text),
        "trade_date": trade.isoformat() if trade else None,
        "maturity_date": maturity.isoformat() if maturity else None,
        "term_days": term_days,
        "amount": amount,
        "currency": currency,
        "management_company": parse_management_company(rec.get("fund_title")),
    })
    return rec


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def publish_date_of(rec):
    """'01.09.2026 20:13:39' -> datetime; ayrıştırılamazsa None."""
    s = (rec.get("publish_datetime") or "").strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def effective_date(rec):
    """Analizde kullanılacak gün: mümkünse işlem tarihi, yoksa bildirim günü."""
    if rec.get("trade_date"):
        try:
            return date.fromisoformat(rec["trade_date"])
        except ValueError:
            pass
    pd = publish_date_of(rec)
    return pd.date() if pd else None


def bp_change(old_rate, new_rate):
    """Oran değişimini baz puan (bp) cinsinden döndürür.
    0,01 yüzde puan = 1 bp; örn. %41,25 -> %41,50 = +25 bp."""
    return round((new_rate - old_rate) * 100)


def fmt_rate(rate):
    if rate is None:
        return "Belirtilmemiş"
    s = f"{rate:.2f}".rstrip("0").rstrip(".")
    return "%" + s.replace(".", ",")


def fmt_amount(amount, currency):
    if amount is None:
        return "Belirtilmemiş"
    if amount >= 1_000_000:
        s = f"{amount / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{s} mn {currency or ''}".strip()
    s = f"{amount:,.0f}".replace(",", ".")
    return f"{s} {currency or ''}".strip()


def fmt_amount_precise(amount, currency):
    """Değişiklik etiketlerinde kullanılan tam hassasiyetli tutar gösterimi
    (yuvarlama yüzünden iki farklı tutarın aynı görünmesini önler)."""
    if amount is None:
        return "Belirtilmemiş"
    s = f"{amount:,.2f}"
    s = s.replace(",", "@").replace(".", ",").replace("@", ".")
    if s.endswith(",00"):
        s = s[:-3]
    return f"{s} {currency or ''}".strip()


def fmt_term(term_days):
    if term_days is None:
        return "Belirtilmemiş"
    return f"{term_days} gün"


def fmt_date(iso_str):
    if not iso_str:
        return "Belirtilmemiş"
    try:
        return date.fromisoformat(iso_str).strftime("%d.%m.%Y")
    except ValueError:
        return iso_str


def fund_matches_filters(fund_title):
    """config'teki fon filtrelerini uygular (KAP unvanları büyük harflidir;
    karşılaştırma birebir 'içeriyor mu' şeklindedir)."""
    import config
    t = " ".join((fund_title or "").split())
    if config.FUND_TITLE_INCLUDE_ANY and not any(
            s in t for s in config.FUND_TITLE_INCLUDE_ANY):
        return False
    if config.FUND_TITLE_REQUIRE_ANY and not any(
            s in t for s in config.FUND_TITLE_REQUIRE_ANY):
        return False
    if any(s in t for s in config.FUND_TITLE_EXCLUDE_ANY):
        return False
    return True


def record_passes_currency_filter(rec):
    """EXCLUDE_TL_CURRENCY açıksa, metninde tutarı TL/TRY yazılmış kayıtları
    eler. (Para birimi yazılmamışsa kayıt elenmez; döviz fonu filtresi zaten
    unvan üzerinden uygulanır.)"""
    import config
    if not config.EXCLUDE_TL_CURRENCY:
        return True
    return (rec.get("currency") or "").upper() not in ("TL", "TRY")


_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosucgiosu")


def norm_counterparty(name):
    """Karşı kurum adını karşılaştırma için normalize eder.

    Aynı banka KAP'ta farklı yazımlarla geçebilir ('Odeabank A.Ş.',
    'Odea Bank A.Ş.', 'ODEABANK'). Yazım farkı yüzünden yanlış 'karşı kurum
    değişti' uyarısı üretmemek için karşılaştırmalar normalize edilmiş ada
    göre yapılır; raporda ise KAP'taki yazım olduğu gibi gösterilir.
    """
    if not name:
        return None
    s = name.lower().translate(_TR_MAP)
    s = re.sub(r"[^a-z0-9]", "", s)          # boşluk/nokta/kesme işaretleri
    s = re.sub(r"(tas|as|ao)$", "", s)        # T.A.Ş. / A.Ş. / A.O. ekleri
    return s or None


def same_counterparty(a, b):
    """İki karşı kurum adının aynı kurumu ifade edip etmediği."""
    na, nb = norm_counterparty(a), norm_counterparty(b)
    return na is not None and na == nb


# ---------------------------------------------------------------------------
# Eşleştirme ve değişiklik tespiti
# ---------------------------------------------------------------------------

def _match_score(new, old):
    """Bugünkü işlemle önceki işlemin aynı işlem dizisinin devamı olma
    olasılığını puanlar. Yanlış eşleşmeden kaçınmak için işlem türü
    uyuşmuyorsa eşleştirme yapılmaz."""
    if new.get("direction") and old.get("direction") and new["direction"] != old["direction"]:
        return -1
    score = 0
    if new.get("counterparty") and old.get("counterparty"):
        score += 2 if same_counterparty(new["counterparty"], old["counterparty"]) else 0
    if new.get("term_days") is not None and old.get("term_days") is not None:
        score += 1 if new["term_days"] == old["term_days"] else 0
    if new.get("currency") and old.get("currency"):
        score += 1 if new["currency"] == old["currency"] else 0
    return score


def _diff_fields(new, old):
    """İki eşleşmiş kayıt arasında alan alan değişiklikleri döndürür.
    Yalnızca iki tarafta da DOLU olan alanlar karşılaştırılır."""
    changes = []
    if new.get("rate") is not None and old.get("rate") is not None and new["rate"] != old["rate"]:
        bp = bp_change(old["rate"], new["rate"])
        changes.append({
            "field": "rate",
            "label": f"Oran {fmt_rate(old['rate'])} → {fmt_rate(new['rate'])} "
                     f"({'+' if bp >= 0 else ''}{bp} bp)",
            "bp": bp,
        })
    if (new.get("counterparty") and old.get("counterparty")
            and not same_counterparty(new["counterparty"], old["counterparty"])):
        changes.append({
            "field": "counterparty",
            "label": f"Karşı kurum değişti: {old['counterparty']} → {new['counterparty']}",
        })
    # NOT: Vade (gün) farkları bilinçli olarak DEĞİŞİKLİK sayılmaz - kullanıcı
    # tercihi. Vade bilgisi tablolarda kolon olarak gösterilmeye devam eder;
    # eşleştirme puanlamasında da kullanılır, ama "Önemli Değişiklikler"e girmez.
    if (new.get("amount") is not None and old.get("amount") is not None
            and new.get("currency") == old.get("currency")
            and new["amount"] != old["amount"]):
        changes.append({
            "field": "amount",
            "label": f"Tutar {fmt_amount_precise(old['amount'], old['currency'])} → "
                     f"{fmt_amount_precise(new['amount'], new['currency'])}",
        })
    if new.get("direction") and old.get("direction") and new["direction"] != old["direction"]:
        changes.append({
            "field": "direction",
            "label": f"İşlem türü {old['direction']} → {new['direction']}",
        })
    return changes


def detect_changes(today_records, history_records):
    """Bugünkü kayıtları fon bazında geçmişle karşılaştırır.

    history_records: bugünden ÖNCE yayımlanmış (son 1 hafta) kayıtlar.
    Dönen: list[dict] -> {record, status, changes, prev}
      status: 'new'      -> fon geçmişte hiç görülmemiş (Yeni işlem)
              'changed'  -> güvenilir eşleşme var ve değişiklik tespit edildi
              'same'     -> güvenilir eşleşme var, önemli değişiklik yok
              'unmatched'-> eşleşme güvenilir değil; değişiklik iddiası yok
    """
    hist_by_fund = {}
    for r in history_records:
        key = r.get("fund_code") or r.get("fund_title")
        hist_by_fund.setdefault(key, []).append(r)

    results = []
    for rec in today_records:
        key = rec.get("fund_code") or rec.get("fund_title")
        history = hist_by_fund.get(key, [])
        if not history:
            results.append({"record": rec, "status": "new", "changes": [], "prev": None})
            continue

        # En güncel yayın gününe ait geçmiş kayıtlar arasından en iyi eşleşme
        history_sorted = sorted(
            history, key=lambda r: publish_date_of(r) or datetime.min, reverse=True,
        )
        scored = [(_match_score(rec, old), old) for old in history_sorted]
        scored = [(s, old) for s, old in scored if s >= 0]
        if not scored:
            results.append({"record": rec, "status": "unmatched", "changes": [], "prev": None})
            continue
        best_score = max(s for s, _ in scored)
        best = next(old for s, old in scored if s == best_score)

        # Aynı fonun aynı gün birden çok farklı işlemi olabilir (farklı vade /
        # karşı kurum). Eşleşme sinyali yoksa (skor 0) ve fonun geçmiş gününde
        # birden fazla kayıt varsa değişiklik iddia etme.
        prev_day = (publish_date_of(best) or datetime.min).date()
        same_day_count = sum(
            1 for r in history
            if (publish_date_of(r) or datetime.min).date() == prev_day
        )
        if best_score == 0 and same_day_count > 1:
            results.append({"record": rec, "status": "unmatched", "changes": [], "prev": None})
            continue

        changes = _diff_fields(rec, best)
        results.append({
            "record": rec,
            "status": "changed" if changes else "same",
            "changes": changes,
            "prev": best,
        })
    return results


# ---------------------------------------------------------------------------
# Haftalık fon takibi ve piyasa özeti
# ---------------------------------------------------------------------------

def weekly_fund_tracking(all_records):
    """Fon bazında son 1 haftalık işlem geçmişi ve haftalık değişim özeti.

    Dönen: list[dict] -> {fund_code, fund_title, rows, summary}
    Yalnızca haftada birden fazla günü olan fonlar dahil edilir.
    """
    by_fund = {}
    for r in all_records:
        key = r.get("fund_code") or r.get("fund_title")
        by_fund.setdefault(key, []).append(r)

    tracking = []
    for key, records in sorted(by_fund.items()):
        records = sorted(
            records, key=lambda r: (publish_date_of(r) or datetime.min,
                                    r.get("disclosure_index", 0)),
        )
        days = {(publish_date_of(r) or datetime.min).date() for r in records}
        if len(days) < 2:
            continue

        # Haftalık karşılaştırma İŞLEM SERİSİ bazında yapılır: aynı işlem türü
        # + aynı karşı kurum. Aksi halde bir fonun repo işlemi ile ters repo
        # işlemi, ya da iki farklı bankayla yaptığı işlemler birbirine
        # kıyaslanır ve yanıltıcı "değişim" çıkar.
        series = {}
        for r in records:
            skey = (r.get("direction") or "?",
                    norm_counterparty(r.get("counterparty")) or "?")
            series.setdefault(skey, []).append(r)

        parts = []
        for (direction, _cp_norm), srecs in sorted(series.items()):
            cp = srecs[-1].get("counterparty") or "?"
            sdays = {(publish_date_of(r) or datetime.min).date() for r in srecs}
            if len(sdays) < 2:
                continue
            first, last = srecs[0], srecs[-1]
            prefix = direction if direction != "?" else "İşlem"
            if cp != "?":
                prefix += f" ({cp})"
            if first.get("rate") is not None and last.get("rate") is not None:
                if first["rate"] != last["rate"]:
                    bp = bp_change(first["rate"], last["rate"])
                    parts.append(
                        f"{prefix}: oran {fmt_rate(first['rate'])} → "
                        f"{fmt_rate(last['rate'])} ({'+' if bp >= 0 else ''}{bp} bp)."
                    )
                else:
                    parts.append(
                        f"{prefix}: oran hafta boyunca {fmt_rate(last['rate'])} "
                        f"seviyesinde sabit."
                    )
            if (first.get("amount") is not None and last.get("amount") is not None
                    and first.get("currency") == last.get("currency")
                    and first["amount"] != last["amount"]):
                parts.append(
                    f"{prefix}: tutar "
                    f"{fmt_amount_precise(first['amount'], first['currency'])} → "
                    f"{fmt_amount_precise(last['amount'], last['currency'])}."
                )

        # Hafta içinde karşı kurum değişimi: aynı işlem türünde birden fazla
        # karşı kurum görülüyorsa ve günler örtüşmüyorsa belirt.
        by_dir = {}
        for r in records:
            if r.get("counterparty"):
                by_dir.setdefault(r.get("direction") or "?", []).append(r)
        for direction, drecs in sorted(by_dir.items()):
            cps, seen_norm = [], set()
            for r in drecs:
                nc = norm_counterparty(r["counterparty"])
                if nc not in seen_norm:
                    seen_norm.add(nc)
                    cps.append(r["counterparty"])
            if len(cps) > 1:
                parts.append(
                    f"{direction if direction != '?' else 'İşlem'}: hafta içinde "
                    f"çalışılan karşı kurumlar: {', '.join(cps)}."
                )
        tracking.append({
            "fund_code": records[-1].get("fund_code"),
            "fund_title": records[-1].get("fund_title"),
            "rows": records,
            "summary": " ".join(parts) if parts else None,
        })
    return tracking


# ---------------------------------------------------------------------------
# TEFAS portföy dağılım analizi
# ---------------------------------------------------------------------------

def fmt_pp(value):
    """+0,25 pp / -0,30 pp biçiminde yüzde puan gösterimi."""
    s = f"{abs(value):.2f}".replace(".", ",")
    sign = "+" if value > 0 else "-"
    return f"{sign}{s} pp"


def fmt_pct_tr(value):
    """%25,45 biçiminde oran gösterimi."""
    s = f"{value:.2f}".replace(".", ",")
    return f"%{s}"


def tefas_daily_changes(alloc_by_date, threshold_pp):
    """Bir fonun son mevcut günü ile bir önceki mevcut gününü karşılaştırır.

    Karşılaştırma TAKVİM gününe göre değil, SON MEVCUT VERİ günlerine göredir
    (pazartesi <-> cuma gibi). Kural: |yeni - eski| > threshold_pp (kesin
    eşitlik raporlanmaz). Kayan nokta hatasına karşı karşılaştırma yüzde
    puanın yüzde biri (0,01 pp) hassasiyetinde tam sayıyla yapılır.

    Dönen: (latest_date, prev_date, changes) - changes: list[dict]
      {key, old, new, delta, kind: 'increase'|'decrease'|'new'|'closed'}
    Veri yetersizse (tek gün / hiç veri) (latest_date, None, []) döner.
    """
    days = sorted(alloc_by_date.keys())
    if not days:
        return None, None, []
    if len(days) < 2:
        return days[-1], None, []
    latest, prev = days[-1], days[-2]
    cur, old = alloc_by_date[latest], alloc_by_date[prev]

    threshold_cents = round(threshold_pp * 100)
    changes = []
    for key in sorted(set(cur) | set(old)):
        new_v = cur.get(key) or 0.0
        old_v = old.get(key) or 0.0
        delta_cents = round(new_v * 100) - round(old_v * 100)
        if abs(delta_cents) <= threshold_cents:
            continue
        delta = delta_cents / 100.0
        if round(old_v * 100) == 0 and round(new_v * 100) != 0:
            kind = "new"          # yeni pozisyon (%0 -> anlamlı oran)
        elif round(new_v * 100) == 0 and round(old_v * 100) != 0:
            kind = "closed"       # pozisyon kapandı (-> %0)
        elif delta > 0:
            kind = "increase"
        else:
            kind = "decrease"
        changes.append({"key": key, "old": old_v, "new": new_v,
                        "delta": delta, "kind": kind})
    changes.sort(key=lambda c: -abs(c["delta"]))
    return latest, prev, changes


def tefas_biggest_moves(fund_changes):
    """Tüm fonlar arasında günün en büyük hareketleri.

    fund_changes: dict[fund_code] -> changes listesi (tefas_daily_changes)
    Dönen: {'increase': (fund, change)|None, 'decrease': ..., 'new': ...,
            'closed': ...}
    """
    best = {"increase": None, "decrease": None, "new": None, "closed": None}
    for fund, changes in fund_changes.items():
        for c in changes:
            kind = c["kind"]
            bucket = kind if kind in ("new", "closed") else (
                "increase" if c["delta"] > 0 else "decrease")
            if best[bucket] is None or abs(c["delta"]) > abs(best[bucket][1]["delta"]):
                best[bucket] = (fund, c)
    return best


def tefas_weekly_summary(alloc_by_date, threshold_pp):
    """Penceredeki İLK mevcut gün ile SON mevcut gün arasındaki net değişim.
    Yalnızca |Δ| > threshold_pp kalemler döner."""
    days = sorted(alloc_by_date.keys())
    if len(days) < 2:
        return None, None, []
    first, last = days[0], days[-1]
    cur, old = alloc_by_date[last], alloc_by_date[first]
    threshold_cents = round(threshold_pp * 100)
    out = []
    for key in sorted(set(cur) | set(old)):
        new_v = cur.get(key) or 0.0
        old_v = old.get(key) or 0.0
        delta_cents = round(new_v * 100) - round(old_v * 100)
        if abs(delta_cents) > threshold_cents:
            out.append({"key": key, "old": old_v, "new": new_v,
                        "delta": delta_cents / 100.0})
    out.sort(key=lambda c: -abs(c["delta"]))
    return first, last, out


def market_summary(today_records, change_results):
    """Yalnızca verilerden çıkarılabilecek sayısal özet."""
    funds = {r.get("fund_code") or r.get("fund_title") for r in today_records}
    n_repo = sum(1 for r in today_records if r.get("direction") == "Repo")
    n_ters = sum(1 for r in today_records if r.get("direction") == "Ters Repo")
    n_unknown = len(today_records) - n_repo - n_ters

    rate_changes = [c for res in change_results for c in res["changes"] if c["field"] == "rate"]
    cp_changes = [res for res in change_results
                  if any(c["field"] == "counterparty" for c in res["changes"])]
    funds_rate_changed = {
        (res["record"].get("fund_code") or res["record"].get("fund_title"))
        for res in change_results if any(c["field"] == "rate" for c in res["changes"])
    }

    max_bp = None
    max_bp_fund = None
    for res in change_results:
        for c in res["changes"]:
            if c["field"] == "rate":
                if max_bp is None or abs(c["bp"]) > abs(max_bp):
                    max_bp = c["bp"]
                    max_bp_fund = (res["record"].get("fund_code")
                                   or res["record"].get("fund_title"))

    return {
        "n_records": len(today_records),
        "n_funds": len(funds),
        "n_repo": n_repo,
        "n_ters_repo": n_ters,
        "n_unknown_direction": n_unknown,
        "n_funds_rate_changed": len(funds_rate_changed),
        "n_funds_cp_changed": len({
            (res["record"].get("fund_code") or res["record"].get("fund_title"))
            for res in cp_changes
        }),
        "n_new_funds": sum(1 for res in change_results if res["status"] == "new"),
        "max_bp": max_bp,
        "max_bp_fund": max_bp_fund,
    }

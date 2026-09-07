"""
KAP + TEFAS Fon Monitörü - Streamlit sitesi.

Çalıştırma (yerel):   streamlit run streamlit_app.py
Yayınlama (bulut):    Streamlit Community Cloud, GitHub reposundan bu dosyayı
                      otomatik bulur (varsayılan giriş dosyası adıdır).

Site veriyi data/kap_data.db'den okur; KAP/TEFAS'a istek atmaz. Veritabanını
her sabah GitHub Actions doldurur ve repoya commit eder; Streamlit Cloud yeni
commit'te uygulamayı otomatik yeniden başlatır, yani site kendiliğinden
güncellenir.
"""
import streamlit as st

import analyzer
import config
import webapp_data

st.set_page_config(
    page_title="KAP + TEFAS Fon Monitörü",
    page_icon="📈",
    layout="wide",
)


@st.cache_data(ttl=900, show_spinner="Veriler hazırlanıyor...")
def get_site_data():
    return webapp_data.build_site_data()


data = get_site_data()

# ---------------------------------------------------------------- kenar çubuğu
with st.sidebar:
    st.header("KAP + TEFAS Fon Monitörü")
    st.caption(
        "Kaynaklar: KAP (Borsa Dışı Repo - Ters Repo Sözleşmesi bildirimleri) "
        "ve TEFAS (fon portföy dağılımları). Kapsam: Ak / İş / Garanti "
        "Portföy döviz fonları. Bildirimde olmayan hiçbir değer tahmin "
        "edilmez; eksik alanlar 'Belirtilmemiş' gösterilir."
    )
    st.markdown(f"**TEFAS alarm eşiği:** > {config.TEFAS_THRESHOLD_PP:.2f} pp"
                .replace(".", ","))
    st.markdown("**TEFAS takip fonları:** " + ", ".join(config.TEFAS_FUNDS))
    if data.get("generated_at"):
        st.markdown(f"**Veri son güncelleme:** {data['generated_at']}")
    if st.button("Verileri yeniden yükle"):
        get_site_data.clear()
        st.rerun()
    st.caption("Bu site yatırım tavsiyesi değildir.")

# --------------------------------------------------------------------- başlık
if not data["ok"]:
    st.title("KAP + TEFAS Fon Monitörü")
    st.warning(data["error"])
    st.stop()

report_day = data["report_day"]
st.title(f"KAP + TEFAS Fon Monitörü – {report_day.strftime('%d.%m.%Y')}")
st.caption("Rapor günü, son KAP yayın günüdür; TEFAS karşılaştırmaları son "
           "iki mevcut veri günü arasındadır ve günler tablolarda açıkça "
           "yazılıdır.")

m = data["metrics"]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Bildirim", m["n_records"])
c2.metric("Fon", m["n_funds"])
c3.metric("Ters Repo / Repo", f"{m['n_ters_repo']} / {m['n_repo']}")
c4.metric("Oranı değişen fon", m["n_rate_changed"])
if m.get("max_tefas_move"):
    fund, delta = m["max_tefas_move"]
    c5.metric("En büyük TEFAS hareketi", fund,
              delta=analyzer.fmt_pp(delta))
else:
    c5.metric("En büyük TEFAS hareketi", "—")

# -------------------------------------------------------------------- sekmeler
tab1, tab2, tab3, tab4 = st.tabs([
    "① Önemli Değişiklikler",
    "② TEFAS Dağılım",
    "③ KAP İşlemleri",
    "④ Haftalık Özet",
])

S = data["sections"]

with tab1:
    st.subheader("KAP – Repo/Ters Repo")
    st.markdown(S["onemli_kap"], unsafe_allow_html=True)
    st.subheader("TEFAS – Portföy Dağılımı")
    st.markdown(S["onemli_tefas"], unsafe_allow_html=True)

with tab2:
    st.markdown(S["tefas"], unsafe_allow_html=True)

with tab3:
    st.subheader("Günün İşlemleri")
    st.markdown(S["kap_gunluk"], unsafe_allow_html=True)
    st.subheader("Son 1 Haftalık Fon Takibi")
    st.markdown(S["kap_haftalik"], unsafe_allow_html=True)

with tab4:
    st.markdown(S["haftalik_ozet"], unsafe_allow_html=True)
    st.markdown(S["ozet"], unsafe_allow_html=True)

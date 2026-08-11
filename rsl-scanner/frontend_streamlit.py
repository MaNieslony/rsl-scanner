import streamlit as st
import requests
import pandas as pd
from io import StringIO

API = "http://localhost:8000"

STATIC_PRESETS = {
    "DAX 40": "ADS.DE,AIR.DE,ALV.DE,BAS.DE,BAYN.DE,BMW.DE,BNR.DE,CON.DE,1COV.DE,DTG.DE,DB1.DE,DBK.DE,DHL.DE,DTE.DE,ENR.DE,EOAN.DE,FRE.DE,FME.DE,HNR1.DE,HEI.DE,HEN3.DE,IFX.DE,LIN,MBG.DE,MRK.DE,MTX.DE,MUV2.DE,NTGY.DE,PAH3.DE,P911.DE,QIA.DE,RHM.DE,RWE.DE,SAP.DE,SHL.DE,SIE.DE,SY1.DE,VNA.DE,VOW3.DE,ZAL.DE",
    "US Large Cap": "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,BRK-B,JPM,V,UNH,XOM,JNJ,PG,MA,HD,COST,ABBV,KO,MRK,LLY,CVX,BAC,PFE,TMO,WMT,CSCO,ACN,NFLX",
    "ETFs": "SPY,QQQ,IWM,EFA,EEM,GLD,TLT,HYG,LQD,VNQ,XLK,XLF,XLE,XLV,XLY,XLP,ARKK,SOXX,XBI,MCHI",
}

@st.cache_data(ttl=60 * 60 * 24, show_spinner="S&P 500 Ticker von Wikipedia laden…")
def load_sp500() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text), header=0)
    df = tables[0]
    tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
    return tickers

st.set_page_config(page_title="RSL Scanner", page_icon="📈", layout="wide")
st.title("📈 RSL Scanner")
st.caption("Relative Stärke nach Robert A. Levy · aktueller Kurs ÷ Ø der letzten 26 Wochenschlusskurse")

with st.sidebar:
    st.header("Einstellungen")

    preset = st.selectbox("Preset laden", ["— kein Preset —"] + list(STATIC_PRESETS.keys()) + ["S&P 500"])

    if preset == "S&P 500":
        try:
            sp500 = load_sp500()
            default_tickers = ",".join(sp500)
            st.success(f"✅ {len(sp500)} Ticker geladen ({pd.Timestamp.now().strftime('%d.%m.%Y')})")
            st.info("Scan dauert 5–10 Minuten.")
        except Exception as e:
            st.error(f"Fehler beim Laden: {e}")
            default_tickers = ""
    elif preset in STATIC_PRESETS:
        default_tickers = STATIC_PRESETS[preset]
    else:
        default_tickers = st.session_state.get("ticker_input", "")

    ticker_input = st.text_area(
        "Ticker-Symbole",
        value=default_tickers,
        height=160,
        help="Komma- oder zeilengetrennt, Yahoo Finance Format"
    )

    weeks = st.selectbox(
        "Wochen-Basis",
        options=[26, 27, 13, 52],
        format_func=lambda x: {
            26: "26 Wochen (Levy-Standard)",
            27: "27 Wochen (inkl. aktuell)",
            13: "13 Wochen",
            52: "52 Wochen",
        }[x]
    )

    st.divider()
    st.subheader("Filter")
    only_strong = st.toggle("Nur RSL > 1.0")
    top_n = st.selectbox(
        "Zeige Top",
        [10, 20, 50, 100, 200, 0],
        format_func=lambda x: "Alle" if x == 0 else f"Top {x}",
        index=3
    )
    sectors = st.multiselect("Sektor-Filter", options=[
        "Technology", "Health Care", "Financials", "Consumer Discretionary",
        "Industrials", "Communication Services", "Consumer Staples",
        "Energy", "Utilities", "Real Estate", "Materials"
    ])

    scan_btn = st.button("🚀 Scan starten", type="primary", use_container_width=True)

# --- Ticker-Vorschau vor dem Scan ---
if ticker_input and not scan_btn and "results" not in st.session_state:
    tickers_preview = [t.strip().upper() for t in ticker_input.replace("\n", ",").split(",") if t.strip()]
    if tickers_preview:
        st.info(f"**{len(tickers_preview)} Ticker geladen** – bereit zum Scannen.")
        with st.expander("Ticker anzeigen"):
            st.write(", ".join(tickers_preview))

# --- Scan mit Fortschrittsbalken ---
if scan_btn:
    tickers = [t.strip().upper() for t in ticker_input.replace("\n", ",").split(",") if t.strip()]
    if not tickers:
        st.warning("Keine Ticker eingegeben.")
    else:
        st.session_state.pop("results", None)
        progress_bar = st.progress(0, text="Scan läuft…")
        status_text = st.empty()
        results = []

        try:
            with requests.post(
                f"{API}/scan/stream",
                json={"tickers": tickers, "weeks": weeks},
                stream=True,
                timeout=1800
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    import json
                    msg = json.loads(line)
                    if msg["type"] == "progress":
                        done, total = msg["done"], msg["total"]
                        pct = done / total
                        progress_bar.progress(pct, text=f"Scanne {done}/{total} Ticker…")
                    elif msg["type"] == "result":
                        msg.pop("type")
                        results.append(msg)

            progress_bar.progress(1.0, text="Fertig ✅")
            status_text.caption(f"{len(results)} von {len(tickers)} erfolgreich gescannt")
            st.session_state["results"] = results
            st.session_state["scan_info"] = f"{len(results)} von {len(tickers)} erfolgreich"

        except requests.exceptions.ConnectionError:
            st.error("Backend nicht erreichbar – läuft uvicorn auf Port 8000?")
        except Exception as e:
            st.error(f"Fehler: {e}")

# --- Ergebnisse ---
if "results" in st.session_state and st.session_state["results"]:
    results = st.session_state["results"]

    df = pd.DataFrame(results)
    if only_strong:
        df = df[df["rsl"] > 1.0]
    if sectors:
        df = df[df["sector"].isin(sectors)]
    df = df.sort_values("rsl", ascending=False).reset_index(drop=True)
    if top_n > 0:
        df = df.head(top_n)

    total_orig = len(results)
    strong = sum(1 for r in results if r["rsl"] > 1)
    avg_rsl = sum(r["rsl"] for r in results) / total_orig
    top1 = results[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Gescannt", total_orig)
    c2.metric("Stärker > 1.0", strong)
    c3.metric("Schwächer", total_orig - strong)
    c4.metric("Ø RSL", f"{avg_rsl:.3f}")
    c5.metric("RSL #1", f"{top1['ticker']} · {top1['rsl']:.3f}")

    st.divider()

    display = df[["ticker", "name", "sector", "rsl", "change_1d", "change_1w", "change_1m"]].copy()
    display.insert(0, "Rang", range(1, len(display) + 1))
    display.columns = ["Rang", "Symbol", "Name", "Sektor", "RSL", "%1T", "%1W", "%1M"]

    def color_rsl(val):
        if isinstance(val, float):
            return f"color: {'#16a34a' if val >= 1.0 else '#dc2626'}; font-weight: 600"
        return ""

    def color_pct(val):
        if isinstance(val, float):
            return f"color: {'#16a34a' if val >= 0 else '#dc2626'}"
        return ""

    styled = (
        display.style
        .applymap(color_rsl, subset=["RSL"])
        .applymap(color_pct, subset=["%1T", "%1W", "%1M"])
        .format({
            "RSL": "{:.4f}",
            "%1T": "{:+.2f}%",
            "%1W": "{:+.2f}%",
            "%1M": "{:+.2f}%",
        })
    )

    st.dataframe(styled, use_container_width=True, height=620, hide_index=True)

    csv = display.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇ CSV exportieren",
        data=csv,
        file_name=f"rsl_scan_{pd.Timestamp.now().date()}.csv",
        mime="text/csv",
    )
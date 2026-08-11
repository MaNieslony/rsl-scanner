from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import json
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

US_MARKET_TZ = ZoneInfo("America/New_York")


class ScanRequest(BaseModel):
    tickers: list[str]
    weeks: int = 26


class RSLResult(BaseModel):
    ticker: str
    name: str
    sector: str
    rsl: float
    rsl_pct: float
    price: float
    currency: str
    change_1d: float
    change_1w: float
    change_1m: float


def _normalize_close_series(hist: pd.DataFrame) -> pd.Series:
    if hist is None or hist.empty:
        return pd.Series(dtype=float)

    # Für Match zum Screenshot bewusst normalen Schlusskurs verwenden.
    if "Close" in hist.columns:
        close_col = "Close"
    elif "Adj Close" in hist.columns:
        close_col = "Adj Close"
    else:
        return pd.Series(dtype=float)

    closes = hist[close_col].dropna().astype(float)
    if closes.empty:
        return pd.Series(dtype=float)

    idx = pd.to_datetime(closes.index)

    # ✅ IMMER tz entfernen
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(US_MARKET_TZ).tz_localize(None)
    else:
        idx = idx.tz_localize(None)

    closes.index = pd.DatetimeIndex(idx).normalize()
    closes = closes.groupby(level=0).last().sort_index()
    return closes


def _calc_change_1d(closes: pd.Series, today_ny: pd.Timestamp) -> float:
    if len(closes) < 2:
        return 0.0

    last_trade_day = closes.index[-1].normalize()
    if last_trade_day < today_ny.normalize():
        return 0.0

    prev_close = float(closes.iloc[-2])
    last_close = float(closes.iloc[-1])
    if prev_close == 0:
        return 0.0

    return ((last_close / prev_close) - 1.0) * 100.0


def _calc_change_1w(closes: pd.Series) -> float:
    if len(closes) < 5:
        return 0.0

    current = float(closes.iloc[-1])
    base = float(closes.iloc[-5])
    if base == 0:
        return 0.0

    return ((current / base) - 1.0) * 100.0


def _calc_change_1m(closes: pd.Series, today_ny: pd.Timestamp) -> float:
    if closes.empty:
        return 0.0

    target_date = (today_ny.normalize() - pd.DateOffset(months=1)).normalize()
    base_series = closes.loc[closes.index <= target_date]
    if base_series.empty:
        return 0.0

    current = float(closes.iloc[-1])
    base = float(base_series.iloc[-1])
    if base == 0:
        return 0.0

    return ((current / base) - 1.0) * 100.0


def _calc_rsl(closes: pd.Series, weeks: int) -> Optional[float]:
    # Weekly resampling → Freitag Close
    weekly = closes.resample("W-FRI").last().dropna()

    # Unfertige Woche entfernen (wichtig!)
    now = pd.Timestamp.now().normalize()
    last_week = weekly.index[-1]

    if last_week > now:
        weekly = weekly.iloc[:-1]

    if len(weekly) < weeks:
        return None

    current = float(weekly.iloc[-1])

    # Durchschnitt der letzten N Wochen (inkl. aktueller Woche)
    avg = float(weekly.iloc[-weeks:].mean())

    if avg == 0:
        return None

    return current / avg


def fetch_ticker(ticker: str, weeks: int):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="18mo", interval="1d", auto_adjust=False)
        closes = _normalize_close_series(hist)

        min_len = max(int(weeks) * 5, 30)
        if closes.empty or len(closes) < min_len:
            print(f"[WARN] {ticker}: zu wenige Daten ({len(closes)})")
            return None

        today_ny = pd.Timestamp(datetime.now(US_MARKET_TZ)).tz_localize(None).normalize()
        current = float(closes.iloc[-1])

        rsl = _calc_rsl(closes, weeks)
        if rsl is None:
            print(f"[WARN] {ticker}: RSL konnte nicht berechnet werden")
            return None

        change_1d = _calc_change_1d(closes, today_ny)
        change_1w = _calc_change_1w(closes)
        change_1m = _calc_change_1m(closes, today_ny)

        sector = "—"
        name = ticker
        currency = ""

        try:
            info = tk.info
            if isinstance(info, dict):
                sector = info.get("sector", "—") or "—"
                name = info.get("shortName") or info.get("longName") or ticker
        except Exception as exc:
            print(f"[INFO] {ticker}: tk.info nicht verfügbar ({exc})")

        try:
            fast_info = tk.fast_info
            if hasattr(fast_info, "get"):
                currency = fast_info.get("currency", "") or ""
            else:
                currency = getattr(fast_info, "currency", "") or ""
        except Exception as exc:
            print(f"[INFO] {ticker}: tk.fast_info nicht verfügbar ({exc})")

        return RSLResult(
            ticker=ticker,
            name=name,
            sector=sector,
            rsl=round(float(rsl), 4),
            rsl_pct=round((float(rsl) - 1.0) * 100.0, 2),
            price=round(current, 2),
            currency=currency,
            change_1d=round(float(change_1d), 2),
            change_1w=round(float(change_1w), 2),
            change_1m=round(float(change_1m), 2),
        )

    except Exception as exc:
        print(f"[ERROR] {ticker}: {exc}")
        return None


@app.post("/scan/stream")
def scan_stream(req: ScanRequest):
    def generate():
        total = len(req.tickers)
        results = []

        for i, ticker in enumerate(req.tickers):
            result = fetch_ticker(ticker, req.weeks)
            if result is not None:
                results.append(result.model_dump())

            progress = {"type": "progress", "done": i + 1, "total": total}
            yield json.dumps(progress) + "\n"

        results.sort(key=lambda x: x["rsl"], reverse=True)

        for row in results:
            yield json.dumps({"type": "result", **row}) + "\n"

    return StreamingResponse(generate(), media_type="text/plain")


@app.get("/health")
def health():
    return {"status": "ok"}
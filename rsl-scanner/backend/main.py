from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
    weeks: int = Field(default=26, ge=2, le=60)


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


def _normalize_close_series(hist: pd.DataFrame) -> tuple[pd.Series, Optional[ZoneInfo]]:
    """Liefert die Tagesschlusskurse mit tz-freiem Datumsindex in Börsenzeit.

    Rückgabe: (closes, exchange_tz). exchange_tz ist die Zeitzone der Börse
    (z.B. Europe/Berlin für .DE, America/New_York für US-Werte).
    """
    empty = pd.Series(dtype=float)
    if hist is None or hist.empty:
        return empty, None

    # Bewusst normalen (nicht dividendenbereinigten) Schlusskurs verwenden.
    if "Close" in hist.columns:
        close_col = "Close"
    elif "Adj Close" in hist.columns:
        close_col = "Adj Close"
    else:
        return empty, None

    closes = hist[close_col].dropna().astype(float)
    if closes.empty:
        return empty, None

    idx = pd.DatetimeIndex(pd.to_datetime(closes.index))
    exchange_tz = idx.tz

    # Zeitzone entfernen, OHNE vorher umzurechnen: yfinance liefert Tageskerzen
    # um 00:00 Börsenzeit. Eine Umrechnung nach New York würde z.B. XETRA-Kurse
    # (00:00 Berlin = 18:00 NY am Vortag) auf den Vortag verschieben.
    if exchange_tz is not None:
        idx = idx.tz_localize(None)

    closes.index = idx.normalize()
    closes = closes.groupby(level=0).last().sort_index()
    return closes, exchange_tz


def _pct_change(current: float, base: float) -> float:
    if base == 0:
        return 0.0
    return ((current / base) - 1.0) * 100.0


def _calc_change_1d(closes: pd.Series) -> float:
    """Veränderung letzter Schlusskurs ggü. vorherigem Handelstag.

    Auch am Wochenende/Feiertag wird die Veränderung des letzten Handelstags
    angezeigt (statt 0 %).
    """
    if len(closes) < 2:
        return 0.0
    return _pct_change(float(closes.iloc[-1]), float(closes.iloc[-2]))


def _calc_change_1w(closes: pd.Series) -> float:
    """Veränderung über 5 Handelstage (aktueller Kurs vs. Kurs vor 5 Handelstagen)."""
    if len(closes) < 6:
        return 0.0
    return _pct_change(float(closes.iloc[-1]), float(closes.iloc[-6]))


def _calc_change_1m(closes: pd.Series) -> float:
    """Veränderung ggü. dem Schlusskurs einen Kalendermonat vor dem letzten Handelstag."""
    if closes.empty:
        return 0.0

    target_date = closes.index[-1] - pd.DateOffset(months=1)
    base_series = closes.loc[closes.index <= target_date]
    if base_series.empty:
        return 0.0

    return _pct_change(float(closes.iloc[-1]), float(base_series.iloc[-1]))


def _calc_rsl(closes: pd.Series, weeks: int, today: pd.Timestamp) -> Optional[float]:
    """RSL nach Levy auf Basis abgeschlossener Wochen.

    RSL = letzter Wochenschlusskurs / Ø der letzten `weeks` Wochenschlusskurse
    (der letzte Wochenschluss ist im Durchschnitt enthalten).

    `today` ist das heutige Datum in Börsenzeit (tz-frei, normalisiert).
    """
    if closes.empty:
        return None

    # Wochenschluss = letzter Handelstag der Woche (bei Feiertag am Freitag
    # automatisch der Donnerstag). Label ist jeweils der Freitag.
    weekly = closes.resample("W-FRI").last().dropna()
    if weekly.empty:
        return None

    # Unfertige Woche entfernen: Eine Woche gilt erst als abgeschlossen, wenn
    # ihr Freitag in Börsenzeit vorbei ist. Vorher (auch Freitag intraday)
    # wäre der "Wochenschluss" nur ein Zwischenkurs.
    if weekly.index[-1] >= today:
        weekly = weekly.iloc[:-1]

    if len(weekly) < weeks:
        return None

    current = float(weekly.iloc[-1])
    avg = float(weekly.iloc[-weeks:].mean())
    if avg == 0:
        return None

    return current / avg


def fetch_ticker(ticker: str, weeks: int):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="18mo", interval="1d", auto_adjust=False)
        closes, exchange_tz = _normalize_close_series(hist)

        min_len = max(int(weeks) * 5, 30)
        if closes.empty or len(closes) < min_len:
            print(f"[WARN] {ticker}: zu wenige Daten ({len(closes)})")
            return None

        today = pd.Timestamp(datetime.now(exchange_tz or US_MARKET_TZ)).tz_localize(None).normalize()
        current = float(closes.iloc[-1])

        rsl = _calc_rsl(closes, weeks, today)
        if rsl is None:
            print(f"[WARN] {ticker}: RSL konnte nicht berechnet werden")
            return None

        change_1d = _calc_change_1d(closes)
        change_1w = _calc_change_1w(closes)
        change_1m = _calc_change_1m(closes)

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
    # Leere Einträge und Duplikate entfernen, Reihenfolge beibehalten
    tickers = list(dict.fromkeys(t.strip().upper() for t in req.tickers if t.strip()))

    def generate():
        total = len(tickers)
        results = []

        for i, ticker in enumerate(tickers):
            result = fetch_ticker(ticker, req.weeks)
            if result is not None:
                results.append(result.model_dump())

            progress = {"type": "progress", "done": i + 1, "total": total}
            yield json.dumps(progress) + "\n"

        results.sort(key=lambda x: x["rsl"], reverse=True)

        for row in results:
            yield json.dumps({"type": "result", **row}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/health")
def health():
    return {"status": "ok"}
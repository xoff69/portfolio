from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from dataclasses import dataclass
import yfinance as yf

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@dataclass
class Position:
    name: str
    ticker: str
    quantity: float
    buying_price: float


portfolio = [
    Position("Veolia", "VIE.PA", 10, 34.07),
    Position("Eurofins Scientific", "ERF.PA", 5, 66.39),
    Position("Ayvens", "AYV.PA", 30, 11.40),
    Position("Compagnie des Alpes", "CDA.PA", 7, 27.95),
    Position("Elis", "ELIS.PA", 7, 27.89),
]


def get_prices(tickers):
    data = yf.download(tickers, period="1d", group_by="ticker", progress=False)
    prices = {}

    for ticker in tickers:
        try:
            prices[ticker] = data[ticker]["Close"].iloc[-1]
        except Exception:
            prices[ticker] = None

    return prices

@app.get("/test")
def test():
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    tickers = [p.ticker for p in portfolio]
    prices = get_prices(tickers)

    enriched = []
    total_invested = 0
    total_current = 0

    for p in portfolio:
        current_price = prices[p.ticker]
        invested = p.quantity * p.buying_price
        current_value = p.quantity * current_price if current_price else 0
        profit = current_value - invested if current_price else 0
        performance = (profit / invested * 100) if current_price else 0

        total_invested += invested
        total_current += current_value

        enriched.append({
            "name": p.name,
            "price": round(current_price, 2) if current_price else "N/A",
            "invested": round(invested, 2),
            "current": round(current_value, 2),
            "profit": round(profit, 2),
            "performance": round(performance, 2)
        })

    total_profit = total_current - total_invested
    total_perf = (total_profit / total_invested) * 100

    return templates.TemplateResponse("index.html", {
        "request": request,
        "positions": enriched,
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "total_profit": round(total_profit, 2),
        "total_perf": round(total_perf, 2)
    })
import yfinance as yf
import ta
import sqlite3
import threading
import time
import requests
import pandas as pd

from datetime import datetime, UTC
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, jsonify
from dataclasses import dataclass

# ---------------- CONFIG ----------------

PAIRS=["EURUSD=X","GBPUSD=X","USDJPY=X"]

INTERVAL_MINUTES=15
TIMEFRAME="15m"
PERIOD="1d"

CALGARY_TZ=ZoneInfo("America/Edmonton")

app=Flask(__name__)

# ---------------- PORTFOLIO ----------------

@dataclass
class Position:
    name:str
    ticker:str
    quantity:float
    buying_price:float

def load_portfolio():

    p=[]

    with open("portfolio.txt") as f:

        for line in f:

            name,ticker,q,b=line.strip().split(",")

            p.append(Position(name,ticker,float(q),float(b)))

    return p

portfolio=load_portfolio()

# ---------------- LLM ----------------

def load_prompt(filename):
    with open(f"prompts/{filename}", "r", encoding="utf-8") as f:
        return f.read()

def ask_llm(prompt):
    # Forcer réponse en français
    french_prompt = f"Réponds uniquement en français. {prompt}"
    
    r = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "llama3",
            "prompt": french_prompt,
            "stream": False
        })

    return r.json()["response"]

# ---------------- FOREX DATA ----------------

def get_forex_data(pair):
    try:
        df = yf.download(pair, period=PERIOD, interval=TIMEFRAME)
        
        if df.empty:
            return None
            
        # S'assurer que close est 1-dimensionnel
        close = df["Close"].squeeze()
        
        # Vérifier qu'on a assez de données pour les indicateurs
        if len(close) < 20:
            return None
            
        df["RSI"] = ta.momentum.RSIIndicator(close=close).rsi()
        df["MACD"] = ta.trend.MACD(close=close).macd()

        return df.dropna()
    except Exception as e:
        print(f"Erreur forex data: {e}")
        return None

# ---------------- FOREX ROUTE ----------------

@app.route("/forex")
def forex():
    pair = request.args.get("pair", PAIRS[0])
    print(f"Loading forex data for: {pair}")
    
    df = get_forex_data(pair)
    
    if df is None:
        error_msg = f"<div style='padding:20px;background:#fee;border:1px solid #f00;'>Erreur: Impossible de charger les données forex pour {pair}</div>"
        return error_msg

    print(f"Forex data loaded: {len(df)} rows")
    
    candles = []
    for i, row in df.iterrows():
        candles.append({
            "x": i.strftime("%H:%M"),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"])
        })

    # Nettoyer les données RSI et MACD
    rsi_raw = df["RSI"].dropna()
    macd_raw = df["MACD"].dropna()
    
    rsi = [round(float(x), 2) for x in rsi_raw if not pd.isna(x)]
    macd = [round(float(x), 5) for x in macd_raw if not pd.isna(x)]
    
    labels = [d["x"] for d in candles]
    
    print(f"Final data - Candles: {len(candles)}, RSI: {len(rsi)}, MACD: {len(macd)}")

    return render_template("forex.html",
        pair=pair,
        candles=candles,
        labels=labels,
        rsi=rsi,
        macd=macd
    )

# ---------------- PORTFOLIO ----------------

@app.route("/portfolio")
def portfolio_view():
    tickers = [p.ticker for p in portfolio]
    data = yf.download(
        tickers,
        period="1d",
        group_by="ticker",
        progress=False)

    rows = []
    total_invested = 0
    total_value = 0

    for p in portfolio:
        price = float(data[p.ticker]["Close"].iloc[-1])
        invested = p.quantity * p.buying_price
        value = p.quantity * price
        profit = value - invested
        perf = profit / invested * 100

        rows.append({
            "name": p.name,
            "qty": p.quantity,
            "price": round(price, 2),
            "profit": round(profit, 2),
            "perf": round(perf, 2)
        })

        total_invested += invested
        total_value += value

    global_performance = ((total_value - total_invested) / total_invested) * 100

    # Charger le prompt depuis le fichier
    prompt_template = load_prompt("portfolio_analysis.txt")
    prompt = prompt_template.format(
        portfolio_data=rows,
        total_invested=total_invested,
        total_value=total_value,
        global_performance=round(global_performance, 2)
    )

    ai = ask_llm(prompt)

    return render_template("portfolio.html", rows=rows, ai=ai)

# ---------------- TIME ROUTE ----------------

@app.route("/time")
def get_time():
    calgary_time = datetime.now(CALGARY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    return jsonify({"calgary_time": calgary_time})

# ---------------- DASHBOARD ----------------

@app.route("/")
def dashboard():
    calgary_time = datetime.now(CALGARY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    return render_template("dashboard.html", pairs=PAIRS, calgary_time=calgary_time)

# ---------------- START ----------------

if __name__=="__main__":

    app.run(port=5000)
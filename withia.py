import yfinance as yf
import ta
import sqlite3
import threading
import time
import requests

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template_string, render_template, request
from dataclasses import dataclass

# ---------------- CONFIG ----------------

PAIRS=["EURUSD=X","GBPUSD=X","USDJPY=X"]

INTERVAL_MINUTES=15
TIMEFRAME="15m"
PERIOD="1d"

CALGARY_TZ=ZoneInfo("America/Edmonton")

# Variable globale pour stocker l'heure du dernier refresh
last_refresh_time = None

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

def ask_llm(prompt):
    r=requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model":"llama3",
            "prompt":prompt,
            "stream":False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9
            }
        })
    return r.json()["response"]

# ---------------- PROMPT LOADER ----------------

def load_prompt(filename):
    with open(f"prompts/{filename}", "r", encoding="utf-8") as f:
        return f.read()

# ---------------- FOREX DATA ----------------

def get_forex_data(pair, timeframe=TIMEFRAME):

    df=yf.download(pair,period=PERIOD,interval=timeframe)
    
    # S'assurer que close est une Series 1D
    close = df["Close"].squeeze()
    if close.ndim > 1:
        close = close.iloc[:, 0]  # Prendre la première colonne si 2D

    df["RSI"]=ta.momentum.RSIIndicator(close).rsi()
    df["MACD"]=ta.trend.MACD(close).macd()

    return df.dropna()

# ---------------- FOREX ROUTE ----------------

@app.route("/forex")

def forex():
    global last_refresh_time
    
    # Mettre à jour l'heure du dernier refresh
    last_refresh_time = datetime.now(CALGARY_TZ)

    pair=request.args.get("pair",PAIRS[0])
    timeframe=request.args.get("timeframe",TIMEFRAME)
    show_rsi=request.args.get("rsi","true").lower() == "true"
    show_macd=request.args.get("macd","true").lower() == "true"
    show_support=request.args.get("support","false").lower() == "true"

    df=get_forex_data(pair, timeframe)

    candles=[]

    for i,row in df.iterrows():

        candles.append({
            "x":i.strftime("%H:%M"),
            "o":float(row["Open"]),
            "h":float(row["High"]),
            "l":float(row["Low"]),
            "c":float(row["Close"])
        })

    rsi=list(df["RSI"])
    macd=list(df["MACD"])
    labels=[d["x"] for d in candles]

    # Construire le HTML conditionnel
    rsi_canvas = '<canvas id="rsi" style="margin-top: 20px;"></canvas>' if show_rsi else ''
    macd_canvas = '<canvas id="macd" style="margin-top: 20px;"></canvas>' if show_macd else ''
    
    rsi_chart = f'''
    new Chart(
    document.getElementById("rsi"),
    {{
    type:'line',
    data:{{
    labels: {labels},
    datasets:[{{
    label:'RSI',
    data: {rsi},
    borderColor:'orange',
    backgroundColor: 'rgba(255, 165, 0, 0.1)',
    fill: true
    }}]
    }},
    options: {{
        responsive: true,
        scales: {{
            y: {{
                min: 0,
                max: 100,
                grid: {{
                    color: function(context) {{
                        if (context.tick.value === 30 || context.tick.value === 70) {{
                            return '#ff6b6b';
                        }}
                        return '#e0e0e0';
                    }}
                }}
            }}
        }}
    }}
    }}
    )''' if show_rsi else ''
    
    macd_chart = f'''
    new Chart(
    document.getElementById("macd"),
    {{
    type:'line',
    data:{{
    labels: {labels},
    datasets:[{{
    label:'MACD',
    data: {macd},
    borderColor:'green',
    backgroundColor: 'rgba(0, 128, 0, 0.1)',
    fill: true
    }}]
    }},
    options: {{
        responsive: true
    }}
    }}
    )''' if show_macd else ''

    # Formatter l'heure du dernier refresh
    refresh_str = last_refresh_time.strftime("%Y-%m-%d %H:%M:%S %Z") if last_refresh_time else "Jamais"
    
    return render_template('forex.html',
                         pair=pair,
                         timeframe=timeframe,
                         refresh_time=refresh_str,
                         show_rsi=show_rsi,
                         show_macd=show_macd,
                         candles=candles,
                         rsi=rsi,
                         macd=macd,
                         labels=labels)


# ---------------- PORTFOLIO ----------------

@app.route("/portfolio")

def portfolio_view():

    tickers=[p.ticker for p in portfolio]
    
    # Télécharger les données avec gestion d'erreur
    try:
        data=yf.download(
            tickers,
            period="1d",
            group_by="ticker",
            progress=False)
    except Exception as e:
        print(f"Error downloading data: {e}")
        return f"<div>Erreur lors du téléchargement des données: {e}</div>"

    rows=[]

    total_invested=0
    total_value=0

    for p in portfolio:

        try:
            # Essayer d'obtenir le prix actuel
            if len(tickers) == 1:
                # Si un seul ticker, la structure de données est différente
                price = float(data["Close"].iloc[-1])
            else:
                # Si plusieurs tickers, utiliser la structure groupée
                if p.ticker in data.columns.get_level_values(0):
                    price = float(data[p.ticker]["Close"].iloc[-1])
                else:
                    print(f"Warning: Ticker {p.ticker} not found in data")
                    continue
        except (KeyError, IndexError, ValueError, AttributeError) as e:
            print(f"Warning: Cannot get price for {p.ticker} ({p.name}): {e}")
            continue  # Skip this position if we can't get the price

        invested=p.quantity*p.buying_price
        value=p.quantity*price

        profit=value-invested

        perf=profit/invested*100

        rows.append({
            "name":p.name,
            "qty":p.quantity,
            "price":round(price,2),
            "profit":round(profit,2),
            "perf":round(perf,2)
        })

        total_invested+=invested
        total_value+=value

    # Charger et formater le prompt
    prompt_template = load_prompt('portfolio_analysis.txt')
    prompt = prompt_template.format(
        rows=rows,
        total_invested=total_invested,
        total_value=total_value
    )

    ai = ask_llm(prompt)

    return render_template('portfolio.html', rows=rows, ai=ai)

# ---------------- DASHBOARD ----------------

@app.route("/")
def dashboard():
    return render_template('dashboard.html', pairs=PAIRS)

# ---------------- START ----------------

if __name__=="__main__":
    app.run(port=5000)
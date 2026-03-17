import yfinance as yf
import ta
import sqlite3
import threading
import time
import requests
import pandas as pd

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
    try:
        r=requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model":"llama3",
                "prompt":prompt,
                "stream":False
            },
            timeout=30)
        return r.json()["response"]
    except Exception as e:
        print(f"Info: Service LLM non disponible - {e}")
        return "<div class='ai'><h3>Analyse IA du Portfolio</h3><p>📊 Analyse automatique disponible après démarrage du service LLM local</p><p>💡 Pour l'instant, consultez les données du tableau ci-dessus</p></div>"

# ---------------- FOREX DATA ----------------

def get_forex_data(pair, timeframe=TIMEFRAME):
    try:
        print(f"Téléchargement de {pair} avec timeframe {timeframe}...")
        df = yf.download(pair, period=PERIOD, interval=timeframe, progress=False)
        
        if df.empty:
            print(f"Aucune donnée reçue pour {pair}")
            return None
        
        # Nettoyer les données et s'assurer qu'elles sont dans le bon format
        df = df.dropna()
        
        # Vérifier qu'on a assez de données
        if len(df) < 14:
            print(f"Pas assez de données pour {pair} ({len(df)} points)")
            return None
        
        # S'assurer que les colonnes existent et sont des types numériques corrects
        required_cols = ['Open', 'High', 'Low', 'Close']
        for col in required_cols:
            if col not in df.columns:
                print(f"Colonne manquante: {col}")
                return None
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Calculer les indicateurs techniques avec gestion d'erreurs robuste
        try:
            close_prices = df['Close'].astype(float)
            
            # Calcul RSI (Relative Strength Index)
            rsi_indicator = ta.momentum.RSIIndicator(close=close_prices, window=14)
            df['RSI'] = rsi_indicator.rsi()
            
            # Calcul MACD (Moving Average Convergence Divergence)  
            macd_indicator = ta.trend.MACD(close=close_prices, window_slow=26, window_fast=12, window_sign=9)
            df['MACD'] = macd_indicator.macd()
            
            # Remplacer les valeurs NaN par des valeurs par défaut
            df['RSI'] = df['RSI'].fillna(50)
            df['MACD'] = df['MACD'].fillna(0)
            
            print(f"Données chargées avec succès: {len(df)} points")
            
        except Exception as indicator_error:
            print(f"Erreur calcul indicateurs: {indicator_error}")
            # En cas d'erreur, créer des indicateurs par défaut
            df['RSI'] = 50
            df['MACD'] = 0
        
        return df
        
    except Exception as e:
        print(f"Erreur téléchargement données forex: {e}")
        return None

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
    
    if df is None or df.empty:
        error_msg = f"<div style='padding:20px;color:red;'>❌ Erreur: Impossible de charger les données pour {pair}<br>Vérifiez votre connexion internet et réessayez</div>"
        return error_msg
    
    candles = []
    for i, row in df.iterrows():
        # S'assurer que toutes les valeurs sont des nombres valides
        try:
            candle = {
                "x": i.strftime("%H:%M"),
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"])
            }
            candles.append(candle)
        except (ValueError, TypeError) as e:
            print(f"Erreur conversion valeurs: {e}")
            continue
    
    if not candles:
        error_msg = f"<div style='padding:20px;color:red;'>❌ Erreur: Aucune donnée valide pour {pair}</div>"
        return error_msg
    
    # Préparer les données des indicateurs en s'assurant qu'elles sont valides
    rsi_values = []
    macd_values = []
    labels = []
    
    for i, row in df.iterrows():
        try:
            labels.append(i.strftime("%H:%M"))
            
            # RSI: valeur entre 0 et 100, défaut à 50 si invalide
            rsi_val = float(row.get("RSI", 50))
            if pd.isna(rsi_val) or rsi_val < 0 or rsi_val > 100:
                rsi_val = 50
            rsi_values.append(rsi_val)
            
            # MACD: peut être positif ou négatif, défaut à 0 si invalide
            macd_val = float(row.get("MACD", 0))
            if pd.isna(macd_val):
                macd_val = 0
            macd_values.append(macd_val)
            
        except (ValueError, TypeError):
            labels.append(i.strftime("%H:%M"))
            rsi_values.append(50)
            macd_values.append(0)
    
    # Formatter l'heure du dernier refresh
    refresh_str = last_refresh_time.strftime("%Y-%m-%d %H:%M:%S %Z") if last_refresh_time else "Jamais"
    
    print(f"Envoi de {len(candles)} chandelles, {len(rsi_values)} valeurs RSI, {len(macd_values)} valeurs MACD")
    
    return render_template('forex.html',
                         pair=pair,
                         timeframe=timeframe,
                         refresh_time=refresh_str,
                         show_rsi=show_rsi,
                         show_macd=show_macd,
                         candles=candles,
                         rsi=rsi_values,
                         macd=macd_values,
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
        print(f"Erreur téléchargement données portfolio: {e}")
        return f"<div style='color:red;padding:20px'>Erreur lors du téléchargement des données: {e}</div>"
    
    rows=[]
    total_invested=0
    total_value=0
    
    for p in portfolio:
        try:
            # Gestion des différentes structures de données
            if len(tickers) == 1:
                price = float(data["Close"].iloc[-1])
            else:
                if p.ticker in data.columns.get_level_values(0):
                    price = float(data[p.ticker]["Close"].iloc[-1])
                else:
                    print(f"Ticker {p.ticker} non trouvé")
                    continue
        except (KeyError, IndexError, ValueError, AttributeError) as e:
            print(f"Erreur prix pour {p.ticker}: {e}")
            continue
        
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
    
    prompt=f"""
Tu es un analyste de portefeuille professionnel. Réponds entièrement en français.

Portefeuille:
{rows}

Total investi: {total_invested:.2f}
Valeur totale: {total_value:.2f}

Retourne SEULEMENT du HTML comme ceci:

<div class="ai">
<h3>Analyse IA du Portfolio</h3>
<p>Résumé du marché en français...</p>
<ul>
<li>Action X : ACHETER</li>
<li>Action Y : CONSERVER</li> 
<li>Action Z : VENDRE</li>
</ul>
<p>Niveau de risque...</p>
</div>

Analyse les performances et donne des conseils en français.
"""
    
    ai=ask_llm(prompt)

    html="""
<style>
table{
border-collapse:collapse;
width:100%;
}

th,td{
padding:10px;
border-bottom:1px solid #ddd;
}

.green{color:green}
.red{color:red}

.ai{
background:#f4f6f8;
padding:20px;
border-radius:10px;
margin-top:20px
}
</style>

<h2>Portfolio</h2>

<table>
<tr>
<th>Name</th>
<th>Qty</th>
<th>Price</th>
<th>Profit</th>
<th>%</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.name}}</td>
<td>{{r.qty}}</td>
<td>{{r.price}}</td>

<td class="{% if r.profit>0 %}green{% else %}red{% endif %}">
{{r.profit}}
</td>

<td class="{% if r.perf>0 %}green{% else %}red{% endif %}">
{{r.perf}} %
</td>
</tr>
{% endfor %}
</table>

<div>
{{ai|safe}}
</div>
"""

    return render_template_string(html,rows=rows,ai=ai)

# ---------------- DASHBOARD ----------------

@app.route("/")
def dashboard():
    return render_template('dashboard.html', pairs=PAIRS)

# ---------------- START ----------------

if __name__=="__main__":
    app.run(port=5000)
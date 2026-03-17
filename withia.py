import yfinance as yf
import ta
import sqlite3
import threading
import time
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template_string, render_template, request
from dataclasses import dataclass

# ---------------- CONFIG ----------------

PAIRS=["EURUSD=X","GBPUSD=X","USDJPY=X"]

INTERVAL_MINUTES=15
TIMEFRAME="1d"  # Par défaut 1 jour pour avoir plus de données
PERIOD="5d"     # 5 jours de données au lieu d'1 jour

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

def generate_demo_forex_data(pair, timeframe):
    """Génère des données forex de démonstration réalistes"""
    from datetime import timedelta
    
    print(f"Génération de données de démonstration pour {pair}...")
    
    # Valeurs de base pour différentes paires
    base_values = {
        "EURUSD=X": 1.0850,
        "GBPUSD=X": 1.2650, 
        "USDJPY=X": 149.50
    }
    
    base_price = base_values.get(pair, 1.0850)
    
    # Générer 30 points de données
    num_points = 30
    dates = []
    data = []
    
    # Générer les dates selon le timeframe
    start_date = datetime.now()
    if timeframe in ['15m', '30m']:
        delta = timedelta(minutes=int(timeframe.replace('m', '')))
    elif timeframe == '1h':
        delta = timedelta(hours=1)
    elif timeframe == '1d':
        delta = timedelta(days=1)
    elif timeframe == '1wk':
        delta = timedelta(weeks=1)
    else:
        delta = timedelta(hours=1)
    
    current_price = base_price
    
    for i in range(num_points):
        # Simulation de mouvement de prix réaliste
        volatility = 0.002  # 0.2% de volatilité
        change = np.random.normal(0, volatility)
        current_price = current_price * (1 + change)
        
        # Générer OHLC
        high_change = abs(np.random.normal(0, volatility))
        low_change = abs(np.random.normal(0, volatility))
        
        open_price = current_price
        high_price = current_price * (1 + high_change)
        low_price = current_price * (1 - low_change)
        close_price = current_price
        
        date = start_date - (delta * (num_points - i))
        dates.append(date)
        
        data.append({
            'Open': open_price,
            'High': high_price, 
            'Low': low_price,
            'Close': close_price
        })
    
    # Créer DataFrame
    df = pd.DataFrame(data, index=dates)
    
    # Calculer RSI et MACD
    close_prices = df['Close'].astype(float)
    
    if len(close_prices) >= 14:
        rsi_indicator = ta.momentum.RSIIndicator(close=close_prices, window=14)
        df['RSI'] = rsi_indicator.rsi()
    else:
        df['RSI'] = 50
        
    if len(close_prices) >= 26:
        macd_indicator = ta.trend.MACD(close=close_prices, window_slow=26, window_fast=12, window_sign=9)
        df['MACD'] = macd_indicator.macd()
    else:
        df['MACD'] = 0
    
    # Remplacer NaN
    df['RSI'] = df['RSI'].fillna(50)
    df['MACD'] = df['MACD'].fillna(0)
    
    print(f"Données de démonstration générées: {len(df)} points")
    return df

def get_forex_data(pair, timeframe=TIMEFRAME):
    """Récupère les données forex avec fallback vers données de démonstration"""
    try:
        print(f"Tentative de téléchargement de {pair} avec timeframe {timeframe}...")
        
        # Ajuster la période selon l'intervalle pour avoir assez de données
        if timeframe in ['1m', '2m', '5m', '15m', '30m']:
            period = '1d'
        elif timeframe in ['60m', '90m', '1h']:
            period = '5d'
        elif timeframe in ['1d']:
            period = '1mo'  # 1 mois de données journalières
        elif timeframe in ['1wk']:
            period = '1y'   # 1 an de données hebdomadaires
        else:
            period = '5d'
        
        # Essayer avec un timeout court pour ne pas attendre
        df = yf.download(pair, period=period, interval=timeframe, progress=False, timeout=10)
        
        if df.empty:
            print(f"Aucune donnée reçue via Yahoo Finance pour {pair}")
            raise ValueError("Données vides")
        
        # Nettoyer les données
        df = df.dropna()
        
        if len(df) < 2:
            print(f"Pas assez de données réelles ({len(df)} points)")
            raise ValueError("Données insuffisantes")
        
        # Calculer les indicateurs
        required_cols = ['Open', 'High', 'Low', 'Close']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Colonne manquante: {col}")
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        close_prices = df['Close'].astype(float)
        
        # Calcul RSI
        if len(close_prices) >= 14:
            rsi_indicator = ta.momentum.RSIIndicator(close=close_prices, window=14)
            df['RSI'] = rsi_indicator.rsi()
        else:
            df['RSI'] = 50
        
        # Calcul MACD
        if len(close_prices) >= 26:
            macd_indicator = ta.trend.MACD(close=close_prices, window_slow=26, window_fast=12, window_sign=9)
            df['MACD'] = macd_indicator.macd()
        else:
            df['MACD'] = 0
        
        df['RSI'] = df['RSI'].fillna(50)
        df['MACD'] = df['MACD'].fillna(0)
        
        print(f"✅ Données réelles chargées avec succès: {len(df)} points")
        return df
        
    except Exception as e:
        print(f"⚠️ Impossible d'obtenir des données réelles pour {pair}: {e}")
        print("🎭 Utilisation de données de démonstration...")
        
        # Fallback vers données de démonstration
        try:
            return generate_demo_forex_data(pair, timeframe)
        except Exception as demo_error:
            print(f"❌ Erreur lors de la génération de données demo: {demo_error}")
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
    
    df = get_forex_data(pair, timeframe)
    
    if df is None or df.empty:
        error_msg = f"""
        <div style='padding:20px; color:#856404; background-color:#fff3cd; border:1px solid #ffeaa7; border-radius:8px;'>
            <h4>⚠️ Données demo pour {pair}</h4>
            <p>• Les données forex réelles ne sont pas accessibles actuellement</p>
            <p>• Cela peut arriver si les marchés sont fermés ou selon votre région</p>
            <p>• Les graphiques ci-dessous utilisent des données de démonstration</p>
        </div>
        """
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
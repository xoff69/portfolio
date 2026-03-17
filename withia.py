import yfinance as yf
import ta
import sqlite3
import threading
import time
import requests

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template_string, request
from dataclasses import dataclass

# ---------------- CONFIG ----------------

PAIRS=["EURUSD=X","GBPUSD=X","USDJPY=X"]

INTERVAL_MINUTES=15
TIMEFRAME="15m"
PERIOD="1d"

CALGARY_TZ=ZoneInfo("America/Edmonton")

app=Flask(__name__)

# Variable globale pour stocker l'heure du dernier refresh
last_refresh_time = None

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
        print(f"Erreur LLM: {e}")
        return "<div class='ai'><h3>Analyse IA</h3><p>Service d'analyse temporairement indisponible</p></div>"

# ---------------- FOREX DATA ----------------

def get_forex_data(pair, timeframe=TIMEFRAME):
    try:
        df=yf.download(pair,period=PERIOD,interval=timeframe)
        
        # S'assurer que close est une Series 1D
        close = df["Close"].squeeze()
        if close.ndim > 1:
            close = close.iloc[:, 0]
        
        df["RSI"]=ta.momentum.RSIIndicator(close).rsi()
        df["MACD"]=ta.trend.MACD(close).macd()
        
        return df.dropna()
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
    
    df=get_forex_data(pair, timeframe)
    
    if df is None or df.empty:
        return "<div style='padding:20px;color:red;'>Erreur: Impossible de charger les données pour {}</div>".format(pair)
    
    candles=[]
    for i,row in df.iterrows():
        candles.append({
            "x":i.strftime("%H:%M"),
            "o":float(row["Open"]),
            "h":float(row["High"]),
            "l":float(row["Low"]),
            "c":float(row["Close"])
        })
    
    rsi=list(df["RSI"].fillna(0))
    macd=list(df["MACD"].fillna(0))
    labels=[d["x"] for d in candles]
    
    # Formatter l'heure du dernier refresh
    refresh_str = last_refresh_time.strftime("%Y-%m-%d %H:%M:%S %Z") if last_refresh_time else "Jamais"
    
    # Construire les canvas conditionnels
    rsi_canvas = '<canvas id="rsi" style="margin-top: 20px;"></canvas>' if show_rsi else ''
    macd_canvas = '<canvas id="macd" style="margin-top: 20px;"></canvas>' if show_macd else ''
    
    html=f"""
<div style="padding: 20px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
        <h3 style="color: #495057; margin: 0;">{pair} - {timeframe}</h3>
        <div style="color: #6c757d; font-size: 14px; font-style: italic;">
            Dernier rafraîchissement (Calgary): {refresh_str}
        </div>
    </div>
    
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        <canvas id="candles"></canvas>
    </div>
    
    {rsi_canvas}
    {macd_canvas}
</div>

<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial"></script>
<script>

// Données des chandelles
const candleData = {candles}

// Graphique principal des chandelles
new Chart(
document.getElementById("candles"),
{{
type:'candlestick',
data:{{
datasets:[{{
label:'Prix',
data:candleData,
backgroundColor: function(context) {{
    const candle = candleData[context.dataIndex];
    return candle.c >= candle.o ? '#26a69a' : '#ef5350';
}}
}}]
}},
options: {{
    responsive: true,
    plugins: {{
        title: {{
            display: true,
            text: '{pair} - Prix'
        }}
    }}
}}
}})

// Graphique RSI (conditionnel)
{('new Chart(document.getElementById("rsi"), {{ type: "line", data: {{ labels: ' + str(labels) + ', datasets: [{{ label: "RSI", data: ' + str(rsi) + ', borderColor: "orange", backgroundColor: "rgba(255, 165, 0, 0.1)", fill: true }}] }}, options: {{ responsive: true, scales: {{ y: {{ min: 0, max: 100 }} }} }} }})') if show_rsi else ''}

// Graphique MACD (conditionnel)  
{('new Chart(document.getElementById("macd"), {{ type: "line", data: {{ labels: ' + str(labels) + ', datasets: [{{ label: "MACD", data: ' + str(macd) + ', borderColor: "green", backgroundColor: "rgba(0, 128, 0, 0.1)", fill: true }}] }}, options: {{ responsive: true }} }})') if show_macd else ''}

</script>
"""

    return html

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

    html="""

<html>

<head>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>

body{
font-family:Arial;
margin:40px
}

.tabs{
margin-bottom:20px
}

.tab{
display:inline-block;
padding:10px 20px;
background:#eee;
cursor:pointer;
margin-right:10px
}

</style>

<script>

function openTab(name){

document.getElementById("forex").style.display="none"
document.getElementById("portfolio").style.display="none"

document.getElementById(name).style.display="block"

}

</script>

</head>

<body>

<h1>Trading Dashboard</h1>

<div class="tabs">
<div class="tab" onclick="openTab('forex')">📈 Forex</div>
<div class="tab" onclick="openTab('portfolio')">💼 Portfolio</div>
</div>

<div id="forex">
    <div style="margin-bottom: 20px; padding: 20px; background: #f8f9fa; border-radius: 8px;">
        <div style="margin-bottom: 15px;">
            <label style="margin-right: 10px; font-weight: bold;">Paire de devises:</label>
            <select id="pairSelect" onchange="loadForex()" style="padding: 8px; border-radius: 4px;">
                {% for p in pairs %}
                <option>{{p}}</option>
                {% endfor %}
            </select>
        </div>
        
        <div style="margin-bottom: 15px;">
            <label style="margin-right: 10px; font-weight: bold;">Période:</label>
            <select id="timeframeSelect" onchange="loadForex()" style="padding: 8px; border-radius: 4px;">
                <option value="15m">15 minutes</option>
                <option value="30m">30 minutes</option>
                <option value="1h">1 heure</option>
                <option value="4h">4 heures</option>
                <option value="1d" selected>1 jour</option>
            </select>
        </div>
        
        <div>
            <label style="margin-right: 10px; font-weight: bold;">Indicateurs:</label>
            <label style="margin-right: 15px;"><input type="checkbox" id="showRSI" checked onchange="loadForex()"> RSI</label>
            <label><input type="checkbox" id="showMACD" checked onchange="loadForex()"> MACD</label>
        </div>
        
        <div style="margin-top: 10px; font-size: 14px; color: #666;">
            <span id="last-refresh">Dernier rafraîchissement: En cours...</span>
        </div>
    </div>
    
    <div id="forex_content">Sélectionnez une paire de devises...</div>
</div>

<div id="portfolio" style="display:none">

<iframe src="/portfolio" width="100%" height="700"></iframe>

</div>

<script>

function loadForex(){
    const pair = document.getElementById('pairSelect').value;
    const timeframe = document.getElementById('timeframeSelect').value;
    const showRSI = document.getElementById('showRSI').checked;
    const showMACD = document.getElementById('showMACD').checked;
    
    const params = new URLSearchParams({
        pair: pair,
        timeframe: timeframe,
        rsi: showRSI,
        macd: showMACD
    });
    
    fetch(`/forex?${params}`)
    .then(r=>r.text())
    .then(html=>{
        document.getElementById("forex_content").innerHTML=html;
        updateRefreshTime();
    })
    .catch(err => {
        console.error('Erreur:', err);
        document.getElementById("forex_content").innerHTML = '<div style="color:red;padding:20px;">Erreur lors du chargement</div>';
    });
}

function updateRefreshTime() {
    const now = new Date();
    const calgaryTime = new Date(now.toLocaleString("en-US", {timeZone: "America/Edmonton"}));
    document.getElementById('last-refresh').textContent = 'Dernier rafraîchissement: ' + calgaryTime.toLocaleString('fr-CA');
}

// Rafraîchissement automatique toutes les 5 minutes
setInterval(function() {
    if(document.getElementById('forex').style.display !== 'none') {
        loadForex();
    }
}, 5 * 60 * 1000);

loadForex()

</script>

</body>

</html>

"""

    return render_template_string(html,pairs=PAIRS)

# ---------------- START ----------------

if __name__=="__main__":

    app.run(port=5000)
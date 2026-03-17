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
            "stream":False
        })

    return r.json()["response"]

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

    html=f'''

<div style="padding: 20px;">
    <h3 style="color: #495057; margin-bottom: 20px;">{pair} - {timeframe}</h3>
    
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        <canvas id="candles"></canvas>
    </div>
    
    {rsi_canvas}
    {macd_canvas}
</div>

<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial"></script>

<script>

const candleData = {candles}

new Chart(
document.getElementById("candles"),
{{
type:'candlestick',
data:{{
datasets:[{{
label:'Price',
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

{rsi_chart}

{macd_chart}

</script>

'''

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

    prompt=f"""

You are a professional portfolio analyst.

Portfolio:

{rows}

Total invested: {total_invested}
Total value: {total_value}

Return ONLY HTML like this:

<div class="ai">

<h3>Portfolio AI Analysis</h3>

<p>Market summary...</p>

<ul>
<li>Stock X : BUY</li>
<li>Stock Y : HOLD</li>
<li>Stock Z : SELL</li>
</ul>

<p>Risk level...</p>

</div>

"""

    # ai=ask_llm(prompt)  # Commenté temporairement
    ai = "<div class='ai'><h3>Portfolio AI Analysis</h3><p>Analyse AI temporairement désactivée</p></div>"

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
font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
margin:0;
padding:0;
background:#f5f7fa;
}

.header {
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
color: white;
padding: 20px 40px;
margin: 0;
box-shadow: 0 2px 10px rgba(0,0,0,0.1);
}

.header h1 {
margin: 0;
font-size: 28px;
font-weight: 300;
}

.container {
max-width: 1200px;
margin: 0 auto;
padding: 40px;
}

.tabs{
margin-bottom:30px;
border-bottom: 3px solid #e1e8ed;
padding-bottom: 0;
}

.tab{
display:inline-block;
padding:15px 30px;
background:transparent;
cursor:pointer;
margin-right:5px;
font-weight: 500;
color: #657786;
border: none;
border-bottom: 3px solid transparent;
transition: all 0.3s ease;
font-size: 16px;
border-radius: 8px 8px 0 0;
}

.tab:hover{
background: #f1f3f4;
color: #1da1f2;
transform: translateY(-2px);
}

.tab.active{
color: #1da1f2;
border-bottom: 3px solid #1da1f2;
background: white;
box-shadow: 0 -2px 8px rgba(29, 161, 242, 0.1);
}

.tab-content {
background: white;
padding: 30px;
border-radius: 12px;
box-shadow: 0 4px 20px rgba(0,0,0,0.08);
min-height: 500px;
transition: opacity 0.3s ease;
}

.tab-content.hidden {
opacity: 0;
pointer-events: none;
position: absolute;
z-index: -1;
}

.forex-controls {
margin-bottom: 20px;
padding: 20px;
background: #f8f9fa;
border-radius: 8px;
display: flex;
flex-wrap: wrap;
align-items: center;
gap: 20px;
}

.forex-controls label {
font-weight: 500;
color: #495057;
}

.forex-controls select {
padding: 12px 20px;
border: 2px solid #e1e8ed;
border-radius: 8px;
font-size: 16px;
background: white;
cursor: pointer;
transition: border-color 0.3s ease;
}

.forex-controls select:focus {
outline: none;
border-color: #1da1f2;
box-shadow: 0 0 0 3px rgba(29, 161, 242, 0.1);
}

.indicators {
display: flex;
gap: 15px;
flex-wrap: wrap;
}

.indicator-checkbox {
display: flex;
align-items: center;
gap: 8px;
padding: 8px 12px;
background: white;
border-radius: 6px;
border: 1px solid #dee2e6;
transition: all 0.3s ease;
}

.indicator-checkbox:hover {
background: #e3f2fd;
border-color: #1da1f2;
}

.indicator-checkbox input[type="checkbox"] {
accent-color: #1da1f2;
}

.indicator-checkbox label {
margin: 0 !important;
cursor: pointer;
font-size: 14px;
}

</style>

<script>

function openTab(name){
    // Cacher tous les contenus d'onglet
    const contents = document.querySelectorAll('.tab-content');
    contents.forEach(content => {
        content.classList.add('hidden');
    });
    
    // Désactiver tous les onglets
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.classList.remove('active');
    });
    
    // Afficher le contenu sélectionné
    document.getElementById(name).classList.remove('hidden');
    
    // Activer l'onglet cliqué
    event.target.classList.add('active');
}

// Initialiser le premier onglet comme actif
window.onload = function() {
    document.querySelector('.tab').classList.add('active');
}

</script>

</head>

<body>

<div class="header">
    <h1>🚀 Trading Dashboard</h1>
</div>

<div class="container">

<div class="tabs">
    <div class="tab" onclick="openTab('forex')">📈 Forex</div>
    <div class="tab" onclick="openTab('portfolio')">💼 Portfolio</div>
</div>

<div id="forex" class="tab-content">
    <div class="forex-controls">
        <div>
            <label>Paire de devises:</label>
            <select id="pairSelect" onchange="loadForex()">
                {% for p in pairs %}
                <option>{{p}}</option>
                {% endfor %}
            </select>
        </div>
        
        <div>
            <label>Période:</label>
            <select id="timeframeSelect" onchange="loadForex()">
                <option value="15m">15 minutes</option>
                <option value="30m">30 minutes</option>
                <option value="1h">1 heure</option>
                <option value="4h">4 heures</option>
                <option value="1d" selected>1 jour</option>
                <option value="1wk">1 semaine</option>
            </select>
        </div>
        
        <div class="indicators">
            <label style="margin-right: 10px;">Indicateurs:</label>
            
            <div class="indicator-checkbox">
                <input type="checkbox" id="showRSI" checked onchange="loadForex()">
                <label for="showRSI">RSI</label>
            </div>
            
            <div class="indicator-checkbox">
                <input type="checkbox" id="showMACD" checked onchange="loadForex()">
                <label for="showMACD">MACD</label>
            </div>
            
            <div class="indicator-checkbox">
                <input type="checkbox" id="showSupport" onchange="loadForex()">
                <label for="showSupport">Support/Résistance</label>
            </div>
        </div>
    </div>
    <div id="forex_content"><div style="text-align: center; padding: 40px; color: #6c757d;">Sélectionnez une paire de devises pour afficher le graphique</div></div>
</div>

<div id="portfolio" class="tab-content hidden">
    <iframe src="/portfolio" width="100%" height="700" style="border: none; border-radius: 8px;"></iframe>
</div>

</div>

<script>

function loadForex(){
    const pair = document.getElementById('pairSelect').value;
    const timeframe = document.getElementById('timeframeSelect').value;
    const showRSI = document.getElementById('showRSI').checked;
    const showMACD = document.getElementById('showMACD').checked;
    const showSupport = document.getElementById('showSupport').checked;
    
    const params = new URLSearchParams({
        pair: pair,
        timeframe: timeframe,
        rsi: showRSI,
        macd: showMACD,
        support: showSupport
    });
    
    fetch(`/forex?${params}`)
    .then(r=>r.text())
    .then(html=>{
        document.getElementById("forex_content").innerHTML=html
    })
    .catch(err => {
        console.error('Erreur lors du chargement des données forex:', err);
        document.getElementById("forex_content").innerHTML = '<div style="text-align: center; padding: 20px; color: #dc3545;">Erreur lors du chargement des données</div>';
    });
}

function openTab(name){
    // Cacher tous les contenus d'onglet
    const contents = document.querySelectorAll('.tab-content');
    contents.forEach(content => {
        content.classList.add('hidden');
    });
    
    // Désactiver tous les onglets
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.classList.remove('active');
    });
    
    // Afficher le contenu sélectionné
    document.getElementById(name).classList.remove('hidden');
    
    // Activer l'onglet cliqué
    event.target.classList.add('active');
    
    // Charger les données forex si c'est l'onglet forex
    if(name === 'forex') {
        loadForex();
    }
}

// Initialiser le premier onglet comme actif
window.onload = function() {
    document.querySelector('.tab').classList.add('active');
    loadForex(); // Charger les données forex par défaut
}

</script>

</body>

</html>

"""

    return render_template_string(html,pairs=PAIRS)

# ---------------- START ----------------

if __name__=="__main__":

    app.run(port=5000)
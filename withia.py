import yfinance as yf
import ta
import sqlite3
import threading
import time
import requests

from datetime import datetime, UTC
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

def get_forex_data(pair):

    df=yf.download(pair,period=PERIOD,interval=TIMEFRAME)

    # S'assurer que close est 1-dimensionnel
    close=df["Close"].squeeze()

    df["RSI"]=ta.momentum.RSIIndicator(close=close).rsi()
    df["MACD"]=ta.trend.MACD(close=close).macd()

    return df.dropna()

# ---------------- FOREX ROUTE ----------------

@app.route("/forex")

def forex():

    pair=request.args.get("pair",PAIRS[0])

    df=get_forex_data(pair)

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

    html="""

<h3>{{pair}}</h3>

<canvas id="candles"></canvas>

<canvas id="rsi"></canvas>

<canvas id="macd"></canvas>

<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial"></script>

<script>

const candleData = {{candles|tojson}}

new Chart(
document.getElementById("candles"),
{
type:'candlestick',
data:{
datasets:[{
label:'Price',
data:candleData
}]
}
})

new Chart(
document.getElementById("rsi"),
{
type:'line',
data:{
labels: {{labels|tojson}},
datasets:[{
label:'RSI',
data: {{rsi|tojson}},
borderColor:'orange'
}]
}
})

new Chart(
document.getElementById("macd"),
{
type:'line',
data:{
labels: {{labels|tojson}},
datasets:[{
label:'MACD',
data: {{macd|tojson}},
borderColor:'green'
}]
}
})

</script>

"""

    return render_template_string(
        html,
        pair=pair,
        candles=candles,
        labels=labels,
        rsi=rsi,
        macd=macd
    )

# ---------------- PORTFOLIO ----------------

@app.route("/portfolio")

def portfolio_view():

    tickers=[p.ticker for p in portfolio]

    data=yf.download(
        tickers,
        period="1d",
        group_by="ticker",
        progress=False)

    rows=[]

    total_invested=0
    total_value=0

    for p in portfolio:

        price=float(data[p.ticker]["Close"].iloc[-1])

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

<div class="tab" onclick="openTab('forex')">Forex</div>
<div class="tab" onclick="openTab('portfolio')">Portfolio</div>

</div>

<div id="forex">

<select onchange="loadForex(this.value)">

{% for p in pairs %}
<option>{{p}}</option>
{% endfor %}

</select>

<div id="forex_content"></div>

</div>

<div id="portfolio" style="display:none">

<iframe src="/portfolio" width="100%" height="700"></iframe>

</div>

<script>

function loadForex(pair){

fetch("/forex?pair="+pair)
.then(r=>r.text())
.then(html=>{
document.getElementById("forex_content").innerHTML=html
})

}

loadForex("EURUSD=X")

</script>

</body>

</html>

"""

    return render_template_string(html,pairs=PAIRS)

# ---------------- START ----------------

if __name__=="__main__":

    app.run(port=5000)
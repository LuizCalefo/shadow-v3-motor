from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import pandas as pd
import json
import os
from openai import OpenAI

app = Flask(__name__)
CORS(app)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_KEY
)
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")

@app.route('/ping')
def ping():
    return jsonify({"status": "motor_aquecido"})

@app.route('/radar-mensal')
def radar_mensal():
    prompt = "Resumo direto (2 linhas) do cenário do Ouro e Bitcoin baseado nos juros do FED. Seja institucional."
    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return jsonify({"radar": res.choices[0].message.content.strip()})
    except:
        return jsonify({"radar": "Radar Macro temporariamente indisponível."})

@app.route('/analisar')
def analisar():
    ativo = request.args.get('ativo', 'XAU')
    simbolo = "BTC/USD" if ativo == "BTC" else "XAU/USD"

    # Coleta Rápida
    url_dados = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}"
    resp_dados = requests.get(url_dados).json()
    
    url_dxy = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1h&outputsize=5&apikey={TWELVEDATA_KEY}"
    resp_dxy = requests.get(url_dxy).json()

    if "values" not in resp_dados:
        return jsonify({"erro": f"Falha de conexão com provedor de liquidez do {ativo}"}), 500

    df = pd.DataFrame(resp_dados["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    poc_estimado = round(df['close'].mean(), 2)

    dxy_atual = round(float(pd.DataFrame(resp_dxy["values"]).iloc[0]['close']), 2) if "values" in resp_dxy else 100.0

    candle_atual = df.iloc[-1]
    candle_anterior = df.iloc[-2]
    preco_atual = round(candle_atual['close'], 2)
    ema_9_atual = round(candle_atual['ema_9'], 2)
    ema_21_atual = round(candle_atual['ema_21'], 2)

    zona_resistencia = round(df['high'].iloc[-20:].max(), 2)
    zona_suporte = round(df['low'].iloc[-20:].min(), 2)

    status_setup = False
    tp1 = tp2 = tp3 = sl = 0.0
    estrategia_detectada = ""

    # FATOR DE VOLATILIDADE: Multiplica a distância dos alvos dependendo do ativo
    # Ouro move $1 a $10. Bitcoin move $100 a $1000.
    mult = 100 if ativo == "BTC" else 1

    # VARREDURA OMNI ULTRA-RÁPIDA
    # 1. Scalper Institucional (Cruzamento 9x21)
    if candle_anterior['ema_9'] <= candle_anterior['ema_21'] and ema_9_atual > ema_21_atual:
        status_setup = True
        estrategia_detectada = "SCALPER 15M"
        sl = round(candle_atual['low'] - (0.50 * mult), 2)
        tp1 = round(preco_atual + (1.50 * mult), 2)
        tp2 = round(preco_atual + (3.00 * mult), 2)
        tp3 = round(preco_atual + (5.00 * mult), 2)
    
    # 2. Golden Pullback
    elif candle_anterior['close'] > candle_anterior['ema_21'] and candle_atual['low'] <= ema_21_atual and candle_atual['close'] > ema_21_atual:
        status_setup = True
        estrategia_detectada = "PULLBACK EMA21"
        sl = round(zona_suporte - (1.00 * mult), 2)
        tp1 = round(preco_atual + (2.00 * mult), 2)
        tp2 = round(preco_atual + (4.00 * mult), 2)
        tp3 = round(preco_atual + (7.00 * mult), 2)

    nome_ativo = "BTCUSD" if ativo == "BTC" else "XAUUSD"

    resultado_motor = {
        "ativo": nome_ativo,
        "estrategia_ativa": estrategia_detectada if status_setup else "MONITORANDO FLUXO",
        "preco_atual": preco_atual,
        "dxy_atual": dxy_atual,
        "poc_volume": poc_estimado,
        "data_hora": resp_dados["values"][0]["datetime"]
    }

    if status_setup:
        resultado_motor["status"] = "SETUP_CONFIRMADO"
        resultado_motor["entrada"] = preco_atual
        resultado_motor["stop_loss"] = sl
        resultado_motor["tp1"] = tp1
        resultado_motor["tp2"] = tp2
        resultado_motor["tp3"] = tp3
        
        try:
            prompt = f"Gatilho: {estrategia_detectada} no {nome_ativo}. Preço: {preco_atual}, SL: {sl}. Explique a assimetria dessa entrada em 1 frase."
            res = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.1)
            resultado_motor["explicacao_ia"] = res.choices[0].message.content.strip()
        except:
            resultado_motor["explicacao_ia"] = f"Gatilho validado por algoritmo quantitativo. Foco na absorção do nível {sl}."
    else:
        resultado_motor["status"] = "SEM_SETUP"
        resultado_motor["explicacao_ia"] = "Ausência de assimetria no livro de ordens. Varredura contínua em andamento."

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
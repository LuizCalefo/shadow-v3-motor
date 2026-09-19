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
    prompt = "Resumo direto (2 linhas) do cenário do Ouro e Bitcoin baseado nos juros do FED."
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
    modo_teste = request.args.get('teste', 'false')
    simbolo = "BTC/USD" if ativo == "BTC" else "XAU/USD"

    # MODO TESTE FORÇADO (Para você ver o painel tático)
    if modo_teste == 'true':
        nome_ativo = "BTCUSD" if ativo == "BTC" else "XAUUSD"
        return jsonify({
            "ativo": nome_ativo,
            "status": "SETUP_CONFIRMADO",
            "estrategia_ativa": "TESTE DE SISTEMA",
            "preco_atual": 1500.50,
            "dxy_atual": 100.5,
            "poc_volume": 1500.00,
            "data_hora": "TESTE",
            "entrada": 1500.50,
            "stop_loss": 1490.00,
            "tp1": 1510.00,
            "tp2": 1520.00,
            "tp3": 1530.00,
            "probabilidade": "99.9%",
            "explicacao_estrategia": "Este é um disparo de teste. Serve para validar se o painel tático e as notificações estão sendo exibidos corretamente no seu aparelho.",
            "pontos_confianca": "150 pontos (Equivalente ao TP1)"
        })

    url_dados = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}"
    resp_dados = requests.get(url_dados).json()
    
    url_dxy = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1h&outputsize=5&apikey={TWELVEDATA_KEY}"
    resp_dxy = requests.get(url_dxy).json()

    if "values" not in resp_dados:
        return jsonify({"erro": f"Mercado fechado ou sem liquidez no momento para {ativo}."}), 500

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

    status_setup = False
    tp1 = tp2 = tp3 = sl = 0.0
    estrategia_detectada = ""
    probabilidade_base = 0
    explicacao_estrategia = ""
    pontos_calc = 0

    mult = 100 if ativo == "BTC" else 1

    if ema_9_atual > ema_21_atual:
        distancia_ema21 = candle_atual['low'] - ema_21_atual
        if - (1.00 * mult) <= distancia_ema21 <= (2.00 * mult):
            status_setup = True
            estrategia_detectada = "PULLBACK FLEXÍVEL (COMPRA)"
            probabilidade_base = 78
            sl = round(ema_21_atual - (1.50 * mult), 2)
            tp1 = round(preco_atual + (1.50 * mult), 2)
            tp2 = round(preco_atual + (3.00 * mult), 2)
            tp3 = round(preco_atual + (5.00 * mult), 2)
            explicacao_estrategia = "O preço corrigiu até a zona de valor (Média de 21) durante uma tendência de alta. As instituições costumam defender e comprar forte nessa região exata."
            pontos_calc = int((tp1 - preco_atual) * 100) if ativo == "XAU" else int(tp1 - preco_atual)
            
        elif candle_anterior['close'] < candle_anterior['ema_9'] and preco_atual > ema_9_atual:
            status_setup = True
            estrategia_detectada = "MOMENTUM SCALPER (COMPRA)"
            probabilidade_base = 65
            sl = round(candle_atual['low'] - (0.60 * mult), 2)
            tp1 = round(preco_atual + (1.20 * mult), 2)
            tp2 = round(preco_atual + (2.50 * mult), 2)
            tp3 = round(preco_atual + (4.00 * mult), 2)
            explicacao_estrategia = "Rompimento agressivo superando a média de curto prazo (9). Isso indica entrada repentina de volume de grandes players. Operação rápida."
            pontos_calc = int((tp1 - preco_atual) * 100) if ativo == "XAU" else int(tp1 - preco_atual)

    else:
        if candle_atual['high'] >= ema_9_atual and candle_atual['close'] < ema_9_atual:
            status_setup = True
            estrategia_detectada = "REJEIÇÃO DE TOPO (VENDA)"
            probabilidade_base = 60
            sl = round(candle_atual['high'] + (1.00 * mult), 2)
            tp1 = round(preco_atual - (1.50 * mult), 2)
            tp2 = round(preco_atual - (3.00 * mult), 2)
            tp3 = round(preco_atual - (5.00 * mult), 2)
            explicacao_estrategia = "O preço tentou subir, mas foi violentamente rejeitado na zona da Média Móvel, confirmando a força dos vendedores. Cenário ideal para entrar vendido."
            pontos_calc = int((preco_atual - tp1) * 100) if ativo == "XAU" else int(preco_atual - tp1)

    nome_ativo = "BTCUSD" if ativo == "BTC" else "XAUUSD"
    probabilidade_final = round(probabilidade_base + (dxy_atual % 1), 1) if status_setup else 0

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
        resultado_motor["probabilidade"] = f"{probabilidade_final}%"
        resultado_motor["explicacao_estrategia"] = explicacao_estrategia
        resultado_motor["pontos_confianca"] = f"Alvo Seguro: {pontos_calc} pontos (Busque o TP1)"
    else:
        resultado_motor["status"] = "SEM_SETUP"

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
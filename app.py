from flask import Flask, jsonify
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

@app.route('/analisar-ouro')
def analisar_ouro():
    # 1. Buscar dados do XAUUSD
    url_gold = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1h&outputsize=50&apikey={TWELVEDATA_KEY}"
    resp_gold = requests.get(url_gold).json()

    # 2. Buscar dados do DXY
    url_dxy = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1h&outputsize=5&apikey={TWELVEDATA_KEY}"
    resp_dxy = requests.get(url_dxy).json()

    if "values" not in resp_gold:
        return jsonify({"erro": "Falha ao buscar dados do Ouro"}), 500

    df = pd.DataFrame(resp_gold["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

    dxy_atual = 100.0
    if "values" in resp_dxy:
        df_dxy = pd.DataFrame(resp_dxy["values"])
        dxy_atual = round(float(df_dxy.iloc[0]['close']), 2)

    candle_atual = df.iloc[-1]
    candle_anterior = df.iloc[-2]
    preco_atual = round(candle_atual['close'], 2)
    ema_atual = round(candle_atual['ema_21'], 2)

    # Mapeamento de Zonas de Liquidez (Máximas e Mínimas recentes)
    zona_resistencia = round(df['high'].iloc[-15:].max(), 2)
    zona_suporte = round(df['low'].iloc[-15:].min(), 2)

    # Regras Estratégicas
    tendencia_alta = candle_anterior['close'] > candle_anterior['ema_21']
    toque_na_media = candle_atual['low'] <= ema_atual
    fechou_acima = candle_atual['close'] > ema_atual
    setup_pullback = tendencia_alta and toque_na_media and fechou_acima

    resultado_motor = {
        "ativo": "XAUUSD",
        "preco_atual": preco_atual,
        "media_movel": ema_atual,
        "dxy_atual": dxy_atual,
        "liquidez_superior": zona_resistencia,
        "liquidez_inferior": zona_suporte
    }

    if setup_pullback:
        resultado_motor["status"] = "SETUP_CONFIRMADO"
        resultado_motor["direcao"] = "COMPRA"
        resultado_motor["entrada"] = preco_atual
        resultado_motor["stop_loss"] = round(zona_suporte - 1.00, 2)
        resultado_motor["take_profit"] = round(preco_atual + ((preco_atual - resultado_motor["stop_loss"]) * 1.8), 2)
        resultado_motor["raciocinio_motor"] = "O preço realizou um pullback técnico exato na EMA 21, respeitando o suporte da zona de liquidez inferior e rejeitando continuação de baixa."
    else:
        resultado_motor["status"] = "SEM_SETUP"
        resultado_motor["direcao"] = "AGUARDANDO"
        resultado_motor["entrada"] = 0.0
        resultado_motor["stop_loss"] = 0.0
        resultado_motor["take_profit"] = 0.0
        resultado_motor["raciocinio_motor"] = f"Preço cotado a {preco_atual}. O motor está monitorando o teste da EMA 21 ({ema_atual}) e aguardando varredura na zona de liquidez de suporte ({zona_suporte}) antes de gatilhar ordens."

    # Prompt Inteligente focado em explicar o "Porquê"
    prompt = f"""
    Você é o algoritmo mestre de tomada de decisão da T3 Quant, especialista em XAUUSD.
    Com base nos dados técnicos abaixo, escreva um relatório de 3 frases curtas e diretas respondendo:
    1. O que o robô está pensando em fazer agora?
    2. Qual é o racional técnico/macro (considerando o DXY e zonas de liquidez)?
    Dados: {json.dumps(resultado_motor)}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        resultado_motor["explicacao_ia"] = chat_completion.choices[0].message.content.strip()
    except Exception as e:
        resultado_motor["explicacao_ia"] = resultado_motor["raciocinio_motor"]

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
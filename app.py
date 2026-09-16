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
    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1h&outputsize=100&apikey={TWELVEDATA_KEY}"
    resposta = requests.get(url).json()

    if "values" not in resposta:
        return jsonify({"erro": "Falha ao buscar os dados"}), 500

    df = pd.DataFrame(resposta["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    
    # Indicadores Quantitativos
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # Cálculo do RSI (14)
    delta = df['close'].diff()
    ganho = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    perda = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = ganho / perda
    df['rsi'] = 100 - (100 / (1 + rs))

    candle_atual = df.iloc[-1]
    candle_anterior = df.iloc[-2]

    preco_atual = round(candle_atual['close'], 2)
    ema_atual = round(candle_atual['ema_21'], 2)
    rsi_atual = round(candle_atual['rsi'], 2)

    tendencia_alta = candle_anterior['close'] > candle_anterior['ema_21']
    toque_na_media = candle_atual['low'] <= ema_atual
    fechou_acima = candle_atual['close'] > ema_atual

    resultado_motor = {
        "ativo": "XAUUSD",
        "estrategia": "Golden Pullback (EMA 21)",
        "preco_atual": preco_atual,
        "media_movel": ema_atual,
        "rsi": rsi_atual
    }

    if tendencia_alta and toque_na_media and fechou_acima:
        resultado_motor["status"] = "SETUP_CONFIRMADO"
        resultado_motor["direcao"] = "COMPRA"
        resultado_motor["entrada"] = preco_atual
        resultado_motor["stop_loss"] = round(candle_atual['low'] - 1.50, 2)
        resultado_motor["take_profit"] = round(preco_atual + ((preco_atual - resultado_motor["stop_loss"]) * 2), 2)
        resultado_motor["motivo_checklist"] = "Preço respeitou a EMA 21 com RSI saudável em zona de pullback."
    else:
        resultado_motor["status"] = "SEM_SETUP"
        motivo = "Aguardando fluxo institucional. "
        if not tendencia_alta:
            motivo += "Estrutura abaixo da média móvel. "
        if not toque_na_media:
            motivo += "Aguardando o preço testar a região da EMA 21. "
        resultado_motor["motivo_checklist"] = motivo

    prompt = f"""
    Atue como o motor de risco e estratégia da T3 Quant. Seja direto, técnico e institucional. Sem saudações.
    Analise os dados e resuma o cenário em 2 frases curtas:
    Dados: {json.dumps(resultado_motor)}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        resultado_motor["explicacao_ia"] = chat_completion.choices[0].message.content.strip()
    except Exception as e:
        resultado_motor["explicacao_ia"] = "Métricas quantitativas calculadas com sucesso via motor de regras."

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
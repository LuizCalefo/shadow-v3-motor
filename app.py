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

# --- ROTA DE AQUECIMENTO (PING) ---
@app.route('/ping')
def ping():
    return jsonify({"status": "motor_aquecido", "mensagem": "Servidor acordado e pronto para operar!"})

@app.route('/analisar-ouro')
def analisar_ouro():
    estrategia_escolhida = request.args.get('estrategia', 'pullback')

    url_gold = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1h&outputsize=50&apikey={TWELVEDATA_KEY}"
    resp_gold = requests.get(url_gold).json()

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

    zona_resistencia = round(df['high'].iloc[-15:].max(), 2)
    zona_suporte = round(df['low'].iloc[-15:].min(), 2)

    status_setup = False
    if estrategia_escolhida == "pullback":
        tendencia_alta = candle_anterior['close'] > candle_anterior['ema_21']
        toque_na_media = candle_atual['low'] <= ema_atual
        fechou_acima = candle_atual['close'] > ema_atual
        status_setup = tendencia_alta and toque_na_media and fechou_acima
    elif estrategia_escolhida == "breakout":
        maxima_recente = df['high'].iloc[-10:-1].max()
        status_setup = candle_atual['close'] > maxima_recente
    elif estrategia_escolhida == "smart_money":
        status_setup = candle_atual['low'] <= zona_suporte and candle_atual['close'] > zona_suporte

    resultado_motor = {
        "ativo": "XAUUSD",
        "estrategia_ativa": estrategia_escolhida.upper(),
        "preco_atual": preco_atual,
        "media_movel": ema_atual,
        "dxy_atual": dxy_atual,
        "liquidez_superior": zona_resistencia,
        "liquidez_inferior": zona_suporte
    }

    if status_setup:
        resultado_motor["status"] = "SETUP_CONFIRMADO"
        resultado_motor["direcao"] = "COMPRA"
        resultado_motor["entrada"] = preco_atual
        resultado_motor["stop_loss"] = round(zona_suporte - 1.20, 2)
        resultado_motor["take_profit"] = round(preco_atual + 5.00, 2)
    else:
        resultado_motor["status"] = "SEM_SETUP"
        resultado_motor["direcao"] = "AGUARDANDO"
        resultado_motor["entrada"] = 0.0
        resultado_motor["stop_loss"] = 0.0
        resultado_motor["take_profit"] = 0.0

    prompt = f"""
    Você é o motor quantitativo sênior da T3 Quant, especialista em XAUUSD.
    Escreva uma análise técnica detalhada do que o robô está pensando e inclua as 2 principais notícias macroeconômicas de hoje (ex: CPI, Payroll) que afetam o ouro, com horários e expectativas.
    Dados técnicos: {json.dumps(resultado_motor)}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        resultado_motor["explicacao_ia"] = chat_completion.choices[0].message.content.strip()
    except Exception as e:
        resultado_motor["explicacao_ia"] = f"Estratégia {estrategia_escolhida} em monitoramento contínuo."

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
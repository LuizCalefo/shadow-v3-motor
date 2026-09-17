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
    return jsonify({"status": "motor_aquecido", "mensagem": "Servidor acordado!"})

@app.route('/analisar-ouro')
def analisar_ouro():
    estrategia_escolhida = request.args.get('estrategia', 'pullback')
    
    # Se for Scalper, usa gráfico de 15 minutos. Senão, usa 1 hora.
    intervalo = "15min" if estrategia_escolhida == "scalper" else "1h"

    url_gold = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={intervalo}&outputsize=50&apikey={TWELVEDATA_KEY}"
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
    
    # Indicadores
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

    dxy_atual = 100.0
    if "values" in resp_dxy:
        df_dxy = pd.DataFrame(resp_dxy["values"])
        dxy_atual = round(float(df_dxy.iloc[0]['close']), 2)

    candle_atual = df.iloc[-1]
    candle_anterior = df.iloc[-2]
    preco_atual = round(candle_atual['close'], 2)
    ema_9_atual = round(candle_atual['ema_9'], 2)
    ema_21_atual = round(candle_atual['ema_21'], 2)

    zona_resistencia = round(df['high'].iloc[-15:].max(), 2)
    zona_suporte = round(df['low'].iloc[-15:].min(), 2)

    status_setup = False
    tp1, tp2, tp3, sl = 0.0, 0.0, 0.0, 0.0

    # LÓGICAS DAS ESTRATÉGIAS
    if estrategia_escolhida == "pullback":
        tendencia_alta = candle_anterior['close'] > candle_anterior['ema_21']
        toque_na_media = candle_atual['low'] <= ema_21_atual
        fechou_acima = candle_atual['close'] > ema_21_atual
        status_setup = tendencia_alta and toque_na_media and fechou_acima
        sl = round(zona_suporte - 1.50, 2)
        tp1 = round(preco_atual + 2.00, 2)
        tp2 = round(preco_atual + 4.00, 2)
        tp3 = round(preco_atual + 6.00, 2)

    elif estrategia_escolhida == "breakout":
        status_setup = candle_atual['close'] > zona_resistencia
        sl = round(ema_21_atual - 1.00, 2)
        tp1 = round(preco_atual + 3.00, 2)
        tp2 = round(preco_atual + 6.00, 2)
        tp3 = round(preco_atual + 9.00, 2)
        
    elif estrategia_escolhida == "scalper":
        # Estratégia rápida: Cruzamento da EMA 9 sobre a 21 nos 15 minutos com explosão
        cruzamento_alta = candle_anterior['ema_9'] <= candle_anterior['ema_21'] and ema_9_atual > ema_21_atual
        status_setup = cruzamento_alta
        sl = round(candle_atual['low'] - 0.50, 2) # Stop bem curto
        tp1 = round(preco_atual + 1.00, 2) # Alvo curto
        tp2 = round(preco_atual + 2.00, 2)
        tp3 = round(preco_atual + 3.50, 2)

    resultado_motor = {
        "ativo": "XAUUSD",
        "estrategia_ativa": estrategia_escolhida.upper(),
        "timeframe": intervalo,
        "preco_atual": preco_atual,
        "dxy_atual": dxy_atual,
        "liquidez_superior": zona_resistencia,
        "liquidez_inferior": zona_suporte
    }

    if status_setup:
        resultado_motor["status"] = "SETUP_CONFIRMADO"
        resultado_motor["entrada"] = preco_atual
        resultado_motor["stop_loss"] = sl
        resultado_motor["tp1"] = tp1
        resultado_motor["tp2"] = tp2
        resultado_motor["tp3"] = tp3
    else:
        resultado_motor["status"] = "SEM_SETUP"

    # Pedindo IA para separar Raciocínio de Notícias usando marcadores
    prompt = f"""
    Você é a IA do Trading Shadow, analista de Ouro.
    Escreva a resposta dividida em duas partes separadas por '|||'.
    Parte 1: O que o robô está pensando agora analisando os dados: {json.dumps(resultado_motor)}.
    Parte 2: Liste as 2 principais notícias macroeconômicas ou eventos (ex: CPI, Fed) programados para o Dólar/Ouro hoje, com horário e impacto esperado.
    Seja técnico e direto.
    """
    
    try:
        chat_completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        resposta_ia = chat_completion.choices[0].message.content.strip()
        
        # Separando a resposta em IA e Notícias
        partes = resposta_ia.split("|||")
        resultado_motor["explicacao_ia"] = partes[0].strip() if len(partes) > 0 else resposta_ia
        resultado_motor["noticias_macro"] = partes[1].strip() if len(partes) > 1 else "Agenda macroeconômica não disponível no momento."
        
    except Exception as e:
        resultado_motor["explicacao_ia"] = "Modo técnico ativo. Monitoramento de liquidez em andamento."
        resultado_motor["noticias_macro"] = "Falha ao buscar agenda de notícias."

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
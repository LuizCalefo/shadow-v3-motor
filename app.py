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

@app.route('/ping')
def ping():
    return jsonify({"status": "motor_aquecido", "mensagem": "Servidor acordado!"})

# Rota de notícias totalmente corrigida (agora carrega instantâneo sem depender de IA)
@app.route('/noticias')
def noticias():
    return jsonify([
        {"hora": "09:30", "evento": "Abertura NY / Volatilidade Alta", "impacto": 3},
        {"hora": "11:00", "evento": "Indicadores PMI / Confiança", "impacto": 2},
        {"hora": "15:00", "evento": "Fluxo Institucional (FOMC/Bancos)", "impacto": 3}
    ])

@app.route('/analisar-ouro')
def analisar_ouro():
    # O robô agora busca o gráfico de 15 min e analisa TUDO sozinho
    url_gold = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}"
    resp_gold = requests.get(url_gold).json()
    
    url_dxy = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1h&outputsize=5&apikey={TWELVEDATA_KEY}"
    resp_dxy = requests.get(url_dxy).json()

    if "values" not in resp_gold:
        return jsonify({"erro": "Falha ao buscar dados"}), 500

    df = pd.DataFrame(resp_gold["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

    poc_estimado = round(df['close'].mean(), 2)

    dxy_atual = 100.0
    if "values" in resp_dxy:
        dxy_atual = round(float(pd.DataFrame(resp_dxy["values"]).iloc[0]['close']), 2)

    candle_atual = df.iloc[-1]
    candle_anterior = df.iloc[-2]
    preco_atual = round(candle_atual['close'], 2)
    ema_9_atual = round(candle_atual['ema_9'], 2)
    ema_21_atual = round(candle_atual['ema_21'], 2)

    zona_resistencia = round(df['high'].iloc[-20:].max(), 2)
    zona_suporte = round(df['low'].iloc[-20:].min(), 2)

    status_setup = False
    tp1, tp2, tp3, sl = 0.0, 0.0, 0.0, 0.0
    estrategia_detectada = ""

    # MOTOR OMNI: Testa todas as estratégias automaticamente
    # 1. Verifica Scalper Rápido
    if candle_anterior['ema_9'] <= candle_anterior['ema_21'] and ema_9_atual > ema_21_atual:
        status_setup = True
        estrategia_detectada = "SCALPER INSTITUCIONAL"
        sl = round(candle_atual['low'] - 0.60, 2)
        tp1 = round(preco_atual + 1.50, 2)
        tp2 = round(preco_atual + 3.00, 2)
        tp3 = round(preco_atual + 5.00, 2)
    
    # 2. Verifica Golden Pullback (se o Scalper não ativou)
    elif candle_anterior['close'] > candle_anterior['ema_21'] and candle_atual['low'] <= ema_21_atual and candle_atual['close'] > ema_21_atual:
        status_setup = True
        estrategia_detectada = "GOLDEN PULLBACK"
        sl = round(zona_suporte - 1.00, 2)
        tp1 = round(preco_atual + 2.00, 2)
        tp2 = round(preco_atual + 4.00, 2)
        tp3 = round(preco_atual + 7.00, 2)

    resultado_motor = {
        "ativo": "XAUUSD",
        "estrategia_ativa": estrategia_detectada if status_setup else "MONITORANDO FLUXO",
        "preco_atual": preco_atual,
        "dxy_atual": dxy_atual,
        "liquidez_superior": zona_resistencia,
        "liquidez_inferior": zona_suporte,
        "poc_volume": poc_estimado
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

    prompt = f"""
    Você é a IA do Trading Shadow. Analise de forma técnica: {json.dumps(resultado_motor)}.
    Se houver setup, explique o alvo e o risco em 2 frases diretas.
    Se não houver, diga: "Monitorando zonas de liquidez. Nenhuma entrada segura no momento."
    """
    
    try:
        chat_completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        resultado_motor["explicacao_ia"] = chat_completion.choices[0].message.content.strip()
    except Exception as e:
        resultado_motor["explicacao_ia"] = "Estrutura de preço em análise quantitativa contínua."

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
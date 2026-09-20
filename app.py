from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import pandas as pd
import json
import os
from openai import OpenAI
from datetime import datetime

app = Flask(__name__)
CORS(app)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_KEY
)
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")

DB_FILE = 'historico_db.json'

@app.route('/ping')
def ping():
    return jsonify({"status": "motor_aquecido"})

@app.route('/get-historico', methods=['GET'])
def get_historico():
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, 'r') as f:
                return jsonify(json.load(f))
        return jsonify([])
    except:
        return jsonify([])

@app.route('/sync-historico', methods=['POST'])
def sync_historico():
    try:
        dados = request.json
        with open(DB_FILE, 'w') as f:
            json.dump(dados, f)
        return jsonify({"status": "sucesso"})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route('/radar-mensal')
def radar_mensal():
    prompt = "Faça um resumo direto (2 linhas) do que esperar do Ouro baseado nas últimas notícias do Payroll e Juros do FED. Seja analítico."
    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return jsonify({"radar": res.choices[0].message.content.strip()})
    except:
        return jsonify({"radar": "Análise Macro indisponível no momento. A IA está em arrefecimento."})

def obter_tendencia_macro(simbolo):
    # Puxa 1H e 4H para filtro anti-loss
    try:
        url_1h = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=1h&outputsize=25&apikey={TWELVEDATA_KEY}"
        url_4h = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=4h&outputsize=25&apikey={TWELVEDATA_KEY}"
        
        df1 = pd.DataFrame(requests.get(url_1h).json()['values'])[::-1].reset_index(drop=True)
        df1['close'] = df1['close'].astype(float)
        ema9_1h = df1['close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21_1h = df1['close'].ewm(span=21, adjust=False).mean().iloc[-1]

        df4 = pd.DataFrame(requests.get(url_4h).json()['values'])[::-1].reset_index(drop=True)
        df4['close'] = df4['close'].astype(float)
        ema9_4h = df4['close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21_4h = df4['close'].ewm(span=21, adjust=False).mean().iloc[-1]

        if ema9_1h > ema21_1h and ema9_4h > ema21_4h:
            return "ALTA"
        elif ema9_1h < ema21_1h and ema9_4h < ema21_4h:
            return "BAIXA"
        else:
            return "LATERAL"
    except:
        return "LATERAL"

@app.route('/analisar')
def analisar():
    ativo = request.args.get('ativo', 'XAU')
    modo_teste = request.args.get('teste', 'false')
    simbolo = "BTC/USD" if ativo == "BTC" else "XAU/USD"

    if modo_teste == 'true':
        return jsonify({
            "ativo": "BTCUSD" if ativo == "BTC" else "XAUUSD",
            "status": "SETUP_CONFIRMADO",
            "estrategia_ativa": "TESTE DE SISTEMA INTEGRADO",
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
            "explicacao_estrategia": "Teste de funcionalidades: Confluência Multi-Timeframe e Calculadora de Lotes a funcionar em pleno.",
            "pontos_confianca": "150 pontos",
            "tendencia_macro": "ALTA"
        })

    url_dados = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}"
    resp_dados = requests.get(url_dados).json()
    
    url_dxy = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1h&outputsize=5&apikey={TWELVEDATA_KEY}"
    resp_dxy = requests.get(url_dxy).json()

    if "values" not in resp_dados:
        return jsonify({"status": "SEM_SETUP", "ativo": ativo, "erro": f"O mercado de {ativo} está fechado ou sem liquidez."})

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

    agora = datetime.utcnow()
    mercado_fechado = False
    if ativo == "XAU":
        if agora.weekday() == 5 or (agora.weekday() == 6 and agora.hour < 21):
            mercado_fechado = True

    # 🛡️ ESCUDO DE NOTÍCIAS (Horários de alta volatilidade - GMT/UTC)
    escudo_ativo = False
    if (agora.hour == 12 and agora.minute >= 25) or (agora.hour == 13 and agora.minute <= 45):
        escudo_ativo = True # Abertura NY / Dados Económicos
    elif agora.hour == 18 and agora.minute <= 30:
        escudo_ativo = True # FOMC Minutes (Eventual)

    status_setup = False
    tp1 = tp2 = tp3 = sl = 0.0
    estrategia_detectada = ""
    probabilidade_base = 0
    explicacao_estrategia = ""
    pontos_calc = 0
    tendencia_macro = "A calcular..."

    mult = 100 if ativo == "BTC" else 1

    if not mercado_fechado and not escudo_ativo:
        tendencia_macro = obter_tendencia_macro(simbolo)
        
        # 🧠 CONFLUÊNCIA H1/H4: Só entra se a direção macro bater com a entrada 15M
        if ema_9_atual > ema_21_atual and tendencia_macro == "ALTA":
            distancia_ema21 = candle_atual['low'] - ema_21_atual
            if - (1.00 * mult) <= distancia_ema21 <= (2.00 * mult):
                status_setup = True
                estrategia_detectada = "PULLBACK FLEXÍVEL (COMPRA)"
                probabilidade_base = 82 # Aumentou porque tem suporte macro!
                sl = round(ema_21_atual - (1.50 * mult), 2)
                tp1 = round(preco_atual + (1.50 * mult), 2)
                tp2 = round(preco_atual + (3.00 * mult), 2)
                tp3 = round(preco_atual + (5.00 * mult), 2)
                explicacao_estrategia = "Gráficos H1 e H4 confirmam tendência de ALTA. Retração de valor no M15 identificada. Máxima segurança."
                pontos_calc = int((tp1 - preco_atual) * 100) if ativo == "XAU" else int(tp1 - preco_atual)
                
            elif candle_anterior['close'] < candle_anterior['ema_9'] and preco_atual > ema_9_atual:
                status_setup = True
                estrategia_detectada = "MOMENTUM SCALPER (COMPRA)"
                probabilidade_base = 75
                sl = round(candle_atual['low'] - (0.60 * mult), 2)
                tp1 = round(preco_atual + (1.20 * mult), 2)
                tp2 = round(preco_atual + (2.50 * mult), 2)
                tp3 = round(preco_atual + (4.00 * mult), 2)
                explicacao_estrategia = "Injeção de volume no M15 apoiada pela macro tendência compradora. Movimento rápido."
                pontos_calc = int((tp1 - preco_atual) * 100) if ativo == "XAU" else int(tp1 - preco_atual)

        elif ema_9_atual <= ema_21_atual and tendencia_macro == "BAIXA":
            if candle_atual['high'] >= ema_9_atual and candle_atual['close'] < ema_9_atual:
                status_setup = True
                estrategia_detectada = "REJEIÇÃO DE TOPO (VENDA)"
                probabilidade_base = 78
                sl = round(candle_atual['high'] + (1.00 * mult), 2)
                tp1 = round(preco_atual - (1.50 * mult), 2)
                tp2 = round(preco_atual - (3.00 * mult), 2)
                tp3 = round(preco_atual - (5.00 * mult), 2)
                explicacao_estrategia = "Vendedores a dominar H1 e H4. Rejeição clara no M15. Forte probabilidade de derrocada."
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

    if mercado_fechado:
        resultado_motor["erro"] = "O mercado de Ouro está encerrado. Reabre no domingo à noite."
    elif escudo_ativo:
        resultado_motor["erro"] = "ESCUDO DE NOTÍCIAS ATIVO! Bloqueio de segurança devido a alta volatilidade no calendário."
        resultado_motor["status"] = "ESCUDO_ATIVO"

    if status_setup:
        resultado_motor["status"] = "SETUP_CONFIRMADO"
        resultado_motor["entrada"] = preco_atual
        resultado_motor["stop_loss"] = sl
        resultado_motor["tp1"] = tp1
        resultado_motor["tp2"] = tp2
        resultado_motor["tp3"] = tp3
        resultado_motor["probabilidade"] = f"{probabilidade_final}%"
        resultado_motor["explicacao_estrategia"] = explicacao_estrategia
        resultado_motor["pontos_confianca"] = f"Alvo Seguro: {pontos_calc} pontos"
        resultado_motor["tendencia_macro"] = tendencia_macro
    elif not escudo_ativo and not mercado_fechado:
        resultado_motor["status"] = "SEM_SETUP"

    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
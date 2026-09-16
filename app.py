from flask import Flask, jsonify
from flask_cors import CORS
import requests
import pandas as pd
from google import genai
import json
import os

app = Flask(__name__)
CORS(app)

# O código agora puxa as chaves diretamente do cofre do Render (Environment Variables)
# NÃO COLOQUE SUAS CHAVES REAIS AQUI NO TEXTO!
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")

cliente = genai.Client(api_key=GEMINI_KEY)

@app.route('/analisar-ouro')
def analisar_ouro():
    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1h&outputsize=50&apikey={TWELVEDATA_KEY}"
    resposta = requests.get(url).json()

    if "values" not in resposta:
        return jsonify({"erro": "Falha ao buscar os dados"}), 500

    df = pd.DataFrame(resposta["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

    candle_atual = df.iloc[-1]
    candle_anterior = df.iloc[-2]

    preco_atual = round(candle_atual['close'], 2)
    ema_atual = round(candle_atual['ema_21'], 2)

    tendencia_alta = candle_anterior['close'] > candle_anterior['ema_21']
    toque_na_media = candle_atual['low'] <= ema_atual
    fechou_acima = candle_atual['close'] > ema_atual

    resultado_motor = {
        "ativo": "XAUUSD",
        "estrategia": "Golden Pullback (EMA 21)",
        "preco_atual": preco_atual,
        "media_movel": ema_atual
    }

    if tendencia_alta and toque_na_media and fechou_acima:
        resultado_motor["status"] = "SETUP_CONFIRMADO"
        resultado_motor["direcao"] = "COMPRA"
        resultado_motor["entrada"] = preco_atual
        resultado_motor["stop_loss"] = round(candle_atual['low'] - 1.00, 2)
        resultado_motor["take_profit"] = round(preco_atual + ((preco_atual - resultado_motor["stop_loss"]) * 2), 2)
        resultado_motor["motivo_checklist"] = "Preço tocou na média de 21 e rejeitou a queda, confirmando o pullback."
    else:
        resultado_motor["status"] = "SEM_SETUP"
        motivo = "Aguardando estrutura. "
        if not tendencia_alta:
            motivo += "O mercado não está em tendência de alta clara. "
        if not toque_na_media:
            motivo += "O preço ainda não retornou para testar a média de 21 períodos. "
        if toque_na_media and not fechou_acima:
            motivo += "O preço tocou na média, mas fechou abaixo dela (rompeu suporte). "
        resultado_motor["motivo_checklist"] = motivo

    prompt = f"""
    Você é o gerador de análises do Shadow V3. Sem saudações. Não invente números.
    Leia este JSON e explique o cenário em 3 frases curtas. 
    Se o status for SEM_SETUP, explique o que falta acontecer.
    Se for SETUP_CONFIRMADO, explique a entrada, stop e alvo.
    Dados: {json.dumps(resultado_motor)}
    """
    
    resposta_ia = cliente.models.generate_content(
        model='gemini-1.5-flash',
        contents=prompt,
    )

    resultado_motor["explicacao_ia"] = resposta_ia.text.strip()
    return jsonify(resultado_motor)

if __name__ == '__main__':
    app.run(port=5000)
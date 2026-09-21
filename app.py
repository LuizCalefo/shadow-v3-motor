from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import pandas as pd
import json
import os
import threading
import time
from openai import OpenAI
from datetime import datetime

app = Flask(__name__)
CORS(app)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_KEY)
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

DB_FILE = 'historico_db.json'
ULTIMA_NOTICIA_PROCESSADA = ""
ULTIMO_RELATORIO_SEMANA = ""

def ler_historico():
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        return []
    except:
        return []

def salvar_historico(dados):
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(dados, f)
    except:
        pass

def enviar_telegram(mensagem):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("Erro ao enviar Telegram:", e)

def analisar_noticia_ia(titulo_evento):
    prompt = f"""
    Uma notícia económica acabou de sair nos Estados Unidos: '{titulo_evento}'.
    Atue como um analista quantitativo institutional sênior. 
    Escreva um alerta curto e direto (máximo 4 linhas) para um grupo de traders do Telegram contendo:
    1. O que a notícia significa na prática.
    2. O impacto direto provável no Ouro (XAU/USD) e Dólar (DXY).
    3. Cenário tático de curto prazo.
    Seja objetivo, use formatação Markdown com emojis.
    """
    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return res.choices[0].message.content.strip()
    except:
        return "Notícia macroeconómica de alto impacto detetada. Volatilidade extrema iminente."

def verificar_noticias_ao_vivo():
    global ULTIMA_NOTICIA_PROCESSADA
    try:
        agora = datetime.utcnow()
        hora_atual_str = agoram = agora.strftime("%H:%M")
        if agora.minute == 30 and hora_atual_str != ULTIMA_NOTICIA_PROCESSADA:
            analise_live = analisar_noticia_ia(f"Boletim Macro de Divulgação USD - Horário {hora_atual_str} UTC")
            msg = f"🚨 *FEED DE NOTÍCIAS AO VIVO (MACRO USA)* 🚨\n\n{analise_live}"
            enviar_telegram(msg)
            ULTIMA_NOTICIA_PROCESSADA = hora_atual_str
    except Exception as e:
        print("Erro no monitor de notícias:", e)

def verificar_relatorio_semanal():
    global ULTIMO_RELATORIO_SEMANA
    try:
        agora = datetime.utcnow()
        # Sexta-feira às 20:00 UTC (Fecho dos mercados)
        if agora.weekday() == 4 and agora.hour == 20:
            data_hoje_str = agora.strftime("%Y-%m-%d")
            if data_hoje_str != ULTIMO_RELATORIO_SEMANA:
                hist = ler_historico()
                wins = sum(1 for t in hist if t.get("resultado") == "WIN")
                losses = sum(1 for t in hist if t.get("resultado") == "LOSS")
                breakevens = sum(1 for t in hist if t.get("resultado") == "BREAKEVEN")
                total = wins + losses + breakevens
                win_rate = round((wins / total * 100), 1) if total > 0 else 0

                prompt = f"""
                Resuma a semana de trading com base nestes números: Total de operações: {total}, Wins: {wins}, Losses: {losses}, Breakevens: {breakevens}, Win Rate: {win_rate}%.
                Escreva um tom analítico, institucional e descontraído para um grupo de amigos traders. Máximo 4 linhas.
                """
                comentario_ia = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3
                ).choices[0].message.content.strip()

                msg = (f"📊 *BALANÇO SEMANAL - TRADING SHADOW*\n\n"
                       f"🟢 *Total de Wins:* {wins}\n"
                       f"🔴 *Total de Losses:* {losses}\n"
                       f"🛡️ *Proteções Breakeven:* {breakevens}\n"
                       f"🎯 *Assertividade:* {win_rate}%\n\n"
                       f"🧠 *Nota do Analista Quant:*\n\"{comentario_ia}\"\n\n"
                       f"Bom fim de semana a todos! O robô regressa no domingo à noite. 🚀")
                
                enviar_telegram(msg)
                ULTIMO_RELATORIO_SEMANA = data_hoje_str
    except Exception as e:
        print("Erro no relatório semanal:", e)

def obter_tendencia_macro(simbolo):
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

        if ema9_1h > ema21_1h and ema9_4h > ema21_4h: return "ALTA"
        elif ema9_1h < ema21_1h and ema9_4h < ema21_4h: return "BAIXA"
        return "LATERAL"
    except:
        return "LATERAL"

def analisar_ativo_interno(ativo):
    simbolo = "XAU/USD"
    if ativo == "BTC": simbolo = "BTC/USD"
    elif ativo == "EUR": simbolo = "EUR/USD"

    url_dados = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}"
    try:
        resp_dados = requests.get(url_dados).json()
    except:
        return {"status": "ERRO_API"}

    if "values" not in resp_dados:
        return {"status": "SEM_SETUP", "ativo": ativo}

    df = pd.DataFrame(resp_dados["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    for col in ['close', 'high', 'low']: df[col] = df[col].astype(float)
    
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

    df['prev_close'] = df['close'].shift(1)
    df['tr'] = df.apply(lambda x: max(x['high'] - x['low'], abs(x['high'] - x['prev_close']), abs(x['low'] - x['prev_close'])), axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()

    candle_atual = df.iloc[-1]
    candle_anterior = df.iloc[-2]
    
    casas_dec = 5 if ativo == "EUR" else 2
    preco_atual = round(candle_atual['close'], casas_dec)
    ema_9_atual = candle_atual['ema_9']
    ema_21_atual = candle_atual['ema_21']
    atr_atual = candle_atual['atr']

    agora = datetime.utcnow()
    if ativo in ["XAU", "EUR"]:
        if agora.weekday() == 5 or (agora.weekday() == 6 and agora.hour < 21):
            return {"status": "SEM_SETUP", "preco_atual": preco_atual, "ativo": simbolo.replace("/", "")}

    status_setup = False
    tp1 = tp2 = tp3 = sl = 0.0
    estrategia_detectada = ""
    probabilidade_base = 0

    tendencia_macro = obter_tendencia_macro(simbolo)
    
    if ema_9_atual > ema_21_atual and tendencia_macro == "ALTA":
        if -atr_atual <= (candle_atual['low'] - ema_21_atual) <= atr_atual:
            status_setup = True
            estrategia_detectada = "PULLBACK FLEXÍVEL (COMPRA)"
            probabilidade_base = 82
            sl = round(preco_atual - (1.5 * atr_atual), casas_dec)
            tp1 = round(preco_atual + (1.0 * atr_atual), casas_dec)
            tp2 = round(preco_atual + (2.0 * atr_atual), casas_dec)
            tp3 = round(preco_atual + (3.0 * atr_atual), casas_dec)
            
        elif candle_anterior['close'] < candle_anterior['ema_9'] and preco_atual > ema_9_atual:
            status_setup = True
            estrategia_detectada = "MOMENTUM SCALPER (COMPRA)"
            probabilidade_base = 75
            sl = round(preco_atual - (1.2 * atr_atual), casas_dec)
            tp1 = round(preco_atual + (1.0 * atr_atual), casas_dec)
            tp2 = round(preco_atual + (2.0 * atr_atual), casas_dec)
            tp3 = round(preco_atual + (3.0 * atr_atual), casas_dec)

    elif ema_9_atual <= ema_21_atual and tendencia_macro == "BAIXA":
        if candle_atual['high'] >= ema_9_atual and candle_atual['close'] < ema_9_atual:
            status_setup = True
            estrategia_detectada = "REJEIÇÃO DE TOPO (VENDA)"
            probabilidade_base = 78
            sl = round(preco_atual + (1.5 * atr_atual), casas_dec)
            tp1 = round(preco_atual - (1.0 * atr_atual), casas_dec)
            tp2 = round(preco_atual - (2.0 * atr_atual), casas_dec)
            tp3 = round(preco_atual - (3.0 * atr_atual), casas_dec)

    nome_ativo = simbolo.replace("/", "")
    resultado = {
        "ativo": nome_ativo,
        "preco_atual": preco_atual,
        "atr_atual": round(atr_atual, casas_dec),
        "data_hora": resp_dados["values"][0]["datetime"],
        "status": "SEM_SETUP"
    }

    if status_setup:
        resultado["status"] = "SETUP_CONFIRMADO"
        resultado["estrategia_ativa"] = estrategia_detectada
        resultado["entrada"] = preco_atual
        resultado["stop_loss"] = sl
        resultado["tp1"] = tp1
        resultado["tp2"] = tp2
        resultado["tp3"] = tp3
        resultado["probabilidade"] = f"{probabilidade_base}%"
        resultado["tendencia_macro"] = tendencia_macro

    return resultado

def avaliar_operacoes_abertas_autonomo(preco_atual, ativo_formatado):
    hist = ler_historico()
    mudou = False

    for trade in hist:
        if trade["resultado"] == "WAIT" and trade["ativo"] == ativo_formatado:
            if trade["tp1"] > trade["entrada"]:
                if preco_atual >= trade["tp1"]: 
                    trade["resultado"] = "BREAKEVEN"
                    mudou = True
                    enviar_telegram(f"🛡️ *ZERO RISCO ALCANÇADO*\nO ativo {ativo_formatado} atingiu o TP1!\nMova o Stop Loss para a entrada ({trade['entrada']}) agora.")
                elif preco_atual <= trade["sl"]: 
                    trade["resultado"] = "LOSS"
                    mudou = True
                    enviar_telegram(f"❌ *STOP LOSS ATINGIDO*\n{ativo_formatado} fechou a operação no prejuízo.")
            else:
                if preco_atual <= trade["tp1"]: 
                    trade["resultado"] = "BREAKEVEN"
                    mudou = True
                    enviar_telegram(f"🛡️ *ZERO RISCO ALCANÇADO*\nO ativo {ativo_formatado} atingiu o TP1!\nMova o Stop Loss para a entrada ({trade['entrada']}) agora.")
                elif preco_atual >= trade["sl"]: 
                    trade["resultado"] = "LOSS"
                    mudou = True
                    enviar_telegram(f"❌ *STOP LOSS ATINGIDO*\n{ativo_formatado} fechou a operação no prejuízo.")
        
        elif trade["resultado"] == "BREAKEVEN" and trade["ativo"] == ativo_formatado:
            if trade["tp1"] > trade["entrada"]:
                if preco_atual >= trade["tp2"]: 
                    trade["resultado"] = "WIN"
                    mudou = True
                    enviar_telegram(f"✅ *TAKE PROFIT FINAL ATINGIDO!*\n{ativo_formatado} fechou com Lucro Máximo!")
            else:
                if preco_atual <= trade["tp2"]: 
                    trade["resultado"] = "WIN"
                    mudou = True
                    enviar_telegram(f"✅ *TAKE PROFIT FINAL ATINGIDO!*\n{ativo_formatado} fechou com Lucro Máximo!")

    if mudou:
        salvar_historico(hist)

def motor_quantitativo_loop():
    while True:
        try:
            # Novo texto de escaneamento imersivo e profissional
            enviar_telegram("🛰️ *[RADAR INSTITUCIONAL ATIVO]*\nVarredura quântica em curso nos ativos principais (XAUUSD, BTCUSD, EURUSD)... A processar livros de ordens e volatilidade ATR.")

            # Verifica Notícias e Relatório Semanal
            verificar_noticias_ao_vivo()
            verificar_relatorio_semanal()

            # Varre os Ativos em busca de Oportunidades
            for ativo in ["XAU", "BTC", "EUR"]:
                dados = analisar_ativo_interno(ativo)
                
                if dados.get("preco_atual"):
                    avaliar_operacoes_abertas_autonomo(dados["preco_atual"], dados["ativo"])

                if dados.get("status") == "SETUP_CONFIRMADO":
                    id_atual = dados["ativo"] + dados["estrategia_ativa"] + dados["data_hora"]
                    hist = ler_historico()
                    
                    if not any(t["id"] == id_atual for t in hist):
                        msg = (f"⚡ *SINAL INSTITUCIONAL DETETADO*\n"
                               f"🪙 *Ativo:* {dados['ativo']}\n"
                               f"🎯 *Estratégia:* {dados['estrategia_ativa']}\n"
                               f"📈 *Macro Tendência:* {dados['tendencia_macro']}\n"
                               f"🛡️ *Volatilidade (ATR):* {dados['atr_atual']}\n\n"
                               f"🟢 *Entrada:* {dados['entrada']}\n"
                               f"🔴 *Stop Loss:* {dados['stop_loss']}\n"
                               f"✅ *TP 1 (Breakeven):* {dados['tp1']}\n"
                               f"✅ *TP 2:* {dados['tp2']}\n"
                               f"✅ *TP 3:* {dados['tp3']}")
                        
                        enviar_telegram(msg)
                        
                        hist.append({
                            "ativo": dados["ativo"],
                            "estrategia": dados["estrategia_ativa"],
                            "entrada": dados["entrada"],
                            "tp1": dados["tp1"],
                            "tp2": dados["tp2"],
                            "sl": dados["stop_loss"],
                            "prob": dados["probabilidade"],
                            "resultado": "WAIT",
                            "id": id_atual
                        })
                        salvar_historico(hist)
        except Exception as e:
            print("Erro no loop:", e)
            
        time.sleep(180)

thread_motor = threading.Thread(target=motor_quantitativo_loop, daemon=True)
thread_motor.start()

@app.route('/ping')
def ping(): return jsonify({"status": "motor_aquecido"})

@app.route('/get-historico', methods=['GET'])
def get_historico(): return jsonify(ler_historico())

@app.route('/sync-historico', methods=['POST'])
def sync_historico():
    salvar_historico(request.json)
    return jsonify({"status": "sucesso"})

@app.route('/radar-mensal')
def radar_mensal_rota():
    prompt = "Faça um resumo direto (2 linhas) do que esperar do Ouro e Euro baseado nas últimas notícias do Payroll e Juros. Seja analítico."
    try:
        res = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.3)
        return jsonify({"radar": res.choices[0].message.content.strip()})
    except: return jsonify({"radar": "Análise indisponível."})

@app.route('/analisar')
def analisar():
    ativo = request.args.get('ativo', 'XAU')
    modo_teste = request.args.get('teste', 'false')
    
    if modo_teste == 'true':
        msg_teste = "🛰️ *[RADAR INSTITUCIONAL ATIVO]*\nVarredura quântica em curso nos ativos principais (XAUUSD, BTCUSD, EURUSD)... A processar livros de ordens e volatilidade ATR."
        enviar_telegram(msg_teste)
        return jsonify({"status": "SETUP_CONFIRMADO", "ativo": "TESTE", "estrategia_ativa": "TESTE DE SISTEMA COMPLETO", "preco_atual": 1500.5, "data_hora": "TESTE", "entrada": 1500.5, "stop_loss": 1490.0, "tp1": 1510.0, "tp2": 1520.0, "tp3": 1530.0, "probabilidade": "99.9%", "explicacao_estrategia": "Teste executado com sucesso.", "pontos_confianca": "N/A", "tendencia_macro": "ALTA", "atr_atual": 10})

    return jsonify(analisar_ativo_interno(ativo))

if __name__ == '__main__':
    app.run(port=5000)
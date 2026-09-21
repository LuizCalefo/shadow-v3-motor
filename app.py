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
import pytz

app = Flask(__name__)
CORS(app)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_KEY)
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

FUSO_LISBOA = pytz.timezone('Europe/Lisbon')

DB_FILE = 'historico_db.json'
ULTIMA_NOTICIA_PROCESSADA = ""
ULTIMO_RELATORIO_DIARIO = ""
ULTIMO_RELATORIO_SEMANAL = ""
CACHE_MACRO = {}

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
        print("Erro Telegram:", e)

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
        return "⚠️ Notícia macroeconómica de alto impacto detetada. Volatilidade extrema iminente nos mercados."

def verificar_noticias_ao_vivo():
    global ULTIMA_NOTICIA_PROCESSADA
    try:
        agora = datetime.now(FUSO_LISBOA)
        hora_atual = agora.strftime("%H")
        # Janela elástica para não perder a notícia devido aos ciclos de 5 minutos
        if 30 <= agora.minute <= 35 and hora_atual != ULTIMA_NOTICIA_PROCESSADA:
            analise_live = analisar_noticia_ia(f"Boletim Macro de Divulgação USD - Horário {hora_atual}:30 (Lisboa)")
            msg = f"🚨 *FEED DE NOTÍCIAS AO VIVO (MACRO USA)* 🚨\n\n{analise_live}"
            enviar_telegram(msg)
            ULTIMA_NOTICIA_PROCESSADA = hora_atual
    except Exception as e:
        print("Erro ao verificar notícias:", e)

def compilar_estatisticas(hist_filtrado):
    wins = losses = zeros = pontos_totais = 0
    alvos = {"TP1": 0, "TP3": 0} # TP2 não fecha trade, é apenas trailing stop
    
    for t in hist_filtrado:
        res = t.get("resultado")
        if res == "WIN":
            wins += 1
            alvo_atingido = t.get("alvo", "TP1")
            if alvo_atingido in alvos:
                alvos[alvo_atingido] += 1
        elif res == "LOSS":
            losses += 1
        elif res == "ZERO":
            zeros += 1
        
        pontos_totais += t.get("pontos", 0.0)

    total = wins + losses + zeros
    win_rate = round((wins / total * 100), 1) if total > 0 else 0
    return wins, losses, zeros, round(pontos_totais, 1), alvos, win_rate

def verificar_relatorios():
    global ULTIMO_RELATORIO_DIARIO, ULTIMO_RELATORIO_SEMANAL
    try:
        agora = datetime.now(FUSO_LISBOA)
        data_hoje_str = agora.strftime("%Y-%m-%d")
        
        hist = ler_historico()
        hist_fechado = [t for t in hist if t.get("estado_fechado") == True]

        # RELATÓRIO DIÁRIO (Todos os dias úteis entre 21:50 e 21:59)
        if agora.hour == 21 and 50 <= agora.minute <= 59 and data_hoje_str != ULTIMO_RELATORIO_DIARIO:
            hist_hoje = [t for t in hist_fechado if t.get("data_fecho") == data_hoje_str]
            if hist_hoje:
                w, l, z, pts, alvs, wr = compilar_estatisticas(hist_hoje)
                msg_diaria = (f"📅 *FECHO DO DIA (DIÁRIO)*\n\n"
                              f"⚖️ *Placar:* {w} Wins | {l} Loss | {z} Zero-a-Zero\n"
                              f"💰 *Resultado de Pontos:* {pts} pts\n"
                              f"🎯 *Alvos Atingidos:* {alvs['TP1']}x TP1 | {alvs['TP3']}x TP3\n\n"
                              f"O motor quantitativo entra agora em modo de consolidação noturna. Até amanhã! 🌙")
                enviar_telegram(msg_diaria)
            ULTIMO_RELATORIO_DIARIO = data_hoje_str

        # RELATÓRIO SEMANAL (Sexta-feira entre 22:00 e 22:10)
        if agora.weekday() == 4 and agora.hour == 22 and 0 <= agora.minute <= 10 and data_hoje_str != ULTIMO_RELATORIO_SEMANAL:
            semana_atual = agora.isocalendar()[1]
            hist_semana = []
            for t in hist_fechado:
                d_fecho = t.get("data_fecho")
                if d_fecho:
                    try:
                        if datetime.strptime(d_fecho, "%Y-%m-%d").isocalendar()[1] == semana_atual:
                            hist_semana.append(t)
                    except:
                        pass
            
            if hist_semana:
                w, l, z, pts, alvs, wr = compilar_estatisticas(hist_semana)
                prompt = f"Resumo quantitativo semanal: {w} Wins, {l} Losses, {z} Breakevens, Lucro total: {pts} pontos, Assertividade: {wr}%. Analise o resultado de forma profissional e encorajadora para uma equipa de traders de forma breve (3 linhas)."
                
                try:
                    comentario_ia = client.chat.completions.create(
                        model="llama-3.1-8b-instant",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3
                    ).choices[0].message.content.strip()
                except:
                    comentario_ia = "Semana finalizada com dados consolidados. Gestão de risco aplicada perfeitamente."

                msg_semanal = (f"📊 *BALANÇO SEMANAL - TRADING SHADOW*\n\n"
                               f"🟢 *Total de Wins:* {w}\n"
                               f"🔴 *Total de Losses:* {l}\n"
                               f"🛡️ *Zero-a-Zero:* {z}\n"
                               f"🔥 *PONTOS TOTAIS:* {pts} pts\n"
                               f"🎯 *Assertividade Geral:* {wr}%\n"
                               f"🏆 *Alvos Atingidos:* TP1 ({alvs['TP1']}) | TP3 ({alvs['TP3']})\n\n"
                               f"🧠 *Nota do Fundo:*\n\"{comentario_ia}\"\n\n"
                               f"Bom fim de semana a todos! Mercados fechados. 🚀")
                enviar_telegram(msg_semanal)
            ULTIMO_RELATORIO_SEMANAL = data_hoje_str
    except Exception as e:
        print("Erro nos relatórios:", e)

def obter_tendencia_macro(simbolo):
    agora_timestamp = time.time()
    if simbolo in CACHE_MACRO and (agora_timestamp - CACHE_MACRO[simbolo]['timestamp']) < 3600:
        return CACHE_MACRO[simbolo]['tendencia']

    try:
        url_1h = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=1h&outputsize=25&apikey={TWELVEDATA_KEY}"
        url_4h = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=4h&outputsize=25&apikey={TWELVEDATA_KEY}"
        
        req_1h = requests.get(url_1h).json()
        req_4h = requests.get(url_4h).json()
        
        if "values" not in req_1h or "values" not in req_4h:
            return CACHE_MACRO[simbolo]['tendencia'] if simbolo in CACHE_MACRO else "LATERAL"
            
        df1 = pd.DataFrame(req_1h['values'])[::-1].reset_index(drop=True)
        df1['close'] = df1['close'].astype(float)
        ema9_1h = df1['close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21_1h = df1['close'].ewm(span=21, adjust=False).mean().iloc[-1]

        df4 = pd.DataFrame(req_4h['values'])[::-1].reset_index(drop=True)
        df4['close'] = df4['close'].astype(float)
        ema9_4h = df4['close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21_4h = df4['close'].ewm(span=21, adjust=False).mean().iloc[-1]

        tendencia = "LATERAL"
        if ema9_1h > ema21_1h and ema9_4h > ema21_4h: tendencia = "ALTA"
        elif ema9_1h < ema21_1h and ema9_4h < ema21_4h: tendencia = "BAIXA"
        
        CACHE_MACRO[simbolo] = {'tendencia': tendencia, 'timestamp': agora_timestamp}
        return tendencia
    except:
        return CACHE_MACRO[simbolo]['tendencia'] if simbolo in CACHE_MACRO else "LATERAL"

def calcular_lote(ativo, entrada, stop_loss):
    risco_dolares = 10.0 # 1% de $1000
    distancia = abs(entrada - stop_loss)
    try:
        if ativo == "XAU/USD": return round(risco_dolares / (distancia * 100), 2)
        elif ativo == "EUR/USD": return round(risco_dolares / (distancia * 100000), 2)
        elif ativo == "BTC/USD": return round(risco_dolares / (distancia * 1), 3)
    except: return 0.01
    return 0.01

def analisar_ativo_interno(ativo):
    simbolo = "XAU/USD"
    if ativo == "BTC": simbolo = "BTC/USD"
    elif ativo == "EUR": simbolo = "EUR/USD"

    agora = datetime.now(FUSO_LISBOA)
    # Proteção de fim de semana (Ignora sinais entre Sexta 21:00 e Domingo 22:00)
    if ativo in ["XAU", "EUR"]:
        if (agora.weekday() == 4 and agora.hour >= 21) or agora.weekday() == 5 or (agora.weekday() == 6 and agora.hour < 22):
            return {"status": "MERCADO_FECHADO"}

    is_killzone = 8 <= agora.hour <= 17
    zona_operacional = "🟢 Killzone Institucional (Londres/NY)" if is_killzone else "🔴 Baixo Volume / Ásia"

    url_dados = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}"
    try:
        resp_dados = requests.get(url_dados).json()
    except: return {"status": "ERRO_API"}

    if "code" in resp_dados and "values" not in resp_dados:
        print("Aviso API TwelveData:", resp_dados.get("message"))
        return {"status": "ERRO_API"}

    if "values" not in resp_dados: return {"status": "SEM_SETUP", "ativo": ativo}

    df = pd.DataFrame(resp_dados["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    for col in ['close', 'high', 'low', 'open']: df[col] = df[col].astype(float)
    
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

    status_setup = False
    tp1 = tp2 = tp3 = sl = 0.0
    estrategia = ""
    prob = 0
    tendencia = obter_tendencia_macro(simbolo)
    
    if ema_9_atual > ema_21_atual and tendencia == "ALTA":
        if -atr_atual <= (candle_atual['low'] - ema_21_atual) <= atr_atual:
            status_setup, estrategia = True, "PULLBACK FLEXÍVEL (COMPRA)"
            prob = 82 if is_killzone else 70
        elif candle_anterior['close'] < candle_anterior['ema_9'] and preco_atual > ema_9_atual:
            status_setup, estrategia = True, "MOMENTUM SCALPER (COMPRA)"
            prob = 75 if is_killzone else 65
            
        if status_setup:
            sl = round(preco_atual - (1.5 * atr_atual), casas_dec)
            tp1, tp2, tp3 = [round(preco_atual + (m * atr_atual), casas_dec) for m in [1.0, 2.0, 3.0]]

    elif ema_9_atual <= ema_21_atual and tendencia == "BAIXA":
        if candle_atual['high'] >= ema_9_atual and candle_atual['close'] < ema_9_atual:
            status_setup, estrategia = True, "REJEIÇÃO DE TOPO (VENDA)"
            prob = 78 if is_killzone else 68
            sl = round(preco_atual + (1.5 * atr_atual), casas_dec)
            tp1, tp2, tp3 = [round(preco_atual - (m * atr_atual), casas_dec) for m in [1.0, 2.0, 3.0]]

    resultado = {"ativo": simbolo.replace("/", ""), "preco_atual": preco_atual, "atr_atual": round(atr_atual, casas_dec), "status": "SEM_SETUP"}
    if status_setup:
        resultado.update({"status": "SETUP_CONFIRMADO", "estrategia_ativa": estrategia, "entrada": preco_atual, 
                          "stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "probabilidade": f"{prob}%", 
                          "tendencia_macro": tendencia, "zona_operacional": zona_operacional, 
                          "lote_sugerido": calcular_lote(simbolo, preco_atual, sl), "data_hora": resp_dados["values"][0]["datetime"]})
    return resultado

def fechar_trade_contabilidade(trade, resultado, alvo, preco_saida):
    trade["resultado"] = resultado
    trade["estado_fechado"] = True
    trade["data_fecho"] = datetime.now(FUSO_LISBOA).strftime("%Y-%m-%d")
    trade["alvo"] = alvo
    
    is_compra = trade["tp1"] > trade["entrada"]
    diff = preco_saida - trade["entrada"] if is_compra else trade["entrada"] - preco_saida
    
    if "EUR" in trade["ativo"]: pts = diff * 100000
    elif "XAU" in trade["ativo"]: pts = diff * 100
    else: pts = diff
    
    trade["pontos"] = round(pts, 1)

def avaliar_operacoes_abertas_autonomo(preco_atual, ativo_formatado):
    hist = ler_historico()
    mudou = False

    for trade in hist:
        if trade.get("estado_fechado"): continue

        is_compra = trade["tp1"] > trade["entrada"]

        if trade["resultado"] == "WAIT" and trade["ativo"] == ativo_formatado:
            if is_compra:
                if preco_atual >= trade["tp3"]:
                    fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"])
                    mudou = True
                    enviar_telegram(f"🎯 *TAKE PROFIT 3 ALCANÇADO (+{trade['pontos']} pts)*\nO ativo *{ativo_formatado}* esmagou o TP3! Lucro máximo. 🚀")
                elif preco_atual >= trade["tp1"]: 
                    trade["resultado"] = "BREAKEVEN" 
                    mudou = True
                    enviar_telegram(f"🏆 *VITÓRIA GARANTIDA (TP1 ATINGIDO)*\nO ativo *{ativo_formatado}* cravou o TP1! Risco anulado. 🛡️")
                elif preco_atual <= trade["sl"]: 
                    fechar_trade_contabilidade(trade, "LOSS", "SL", trade["sl"])
                    mudou = True
                    enviar_telegram(f"❌ *STOP LOSS ATINGIDO ({trade['pontos']} pts)*\n{ativo_formatado} fechou. Gestão de risco aplicada.")
            else: 
                if preco_atual <= trade["tp3"]:
                    fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"])
                    mudou = True
                    enviar_telegram(f"🎯 *TAKE PROFIT 3 ALCANÇADO (+{trade['pontos']} pts)*\nO ativo *{ativo_formatado}* esmagou o TP3! Lucro máximo. 🚀")
                elif preco_atual <= trade["tp1"]: 
                    trade["resultado"] = "BREAKEVEN"
                    mudou = True
                    enviar_telegram(f"🏆 *VITÓRIA GARANTIDA (TP1 ATINGIDO)*\nO ativo *{ativo_formatado}* cravou o TP1! Risco anulado. 🛡️")
                elif preco_atual >= trade["sl"]: 
                    fechar_trade_contabilidade(trade, "LOSS", "SL", trade["sl"])
                    mudou = True
                    enviar_telegram(f"❌ *STOP LOSS ATINGIDO ({trade['pontos']} pts)*\n{ativo_formatado} fechou. Gestão de risco aplicada.")
        
        elif trade["resultado"] == "BREAKEVEN" and trade["ativo"] == ativo_formatado:
            if is_compra:
                if preco_atual >= trade["tp3"]: 
                    fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"])
                    mudou = True
                    enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou com potencial máximo!")
                elif preco_atual >= trade["tp2"]:
                    trade["resultado"] = "TRAILING_TP1"
                    mudou = True
                    enviar_telegram(f"🔥 *TRAILING STOP (TP2 ATINGIDO)*\n{ativo_formatado} rompeu TP2! Stop avançou para TP1. Lucro bloqueado! 💰")
                elif preco_atual <= trade["entrada"]: 
                    fechar_trade_contabilidade(trade, "ZERO", "ZERO", trade["entrada"])
                    mudou = True
                    enviar_telegram(f"⚖️ *SAÍDA NO ZERO-A-ZERO*\n{ativo_formatado} recuou e fechou na entrada. Zero prejuízo.")
            else:
                if preco_atual <= trade["tp3"]: 
                    fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"])
                    mudou = True
                    enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou com potencial máximo!")
                elif preco_atual <= trade["tp2"]:
                    trade["resultado"] = "TRAILING_TP1"
                    mudou = True
                    enviar_telegram(f"🔥 *TRAILING STOP (TP2 ATINGIDO)*\n{ativo_formatado} rompeu TP2! Stop avançou para TP1. Lucro bloqueado! 💰")
                elif preco_atual >= trade["entrada"]: 
                    fechar_trade_contabilidade(trade, "ZERO", "ZERO", trade["entrada"])
                    mudou = True
                    enviar_telegram(f"⚖️ *SAÍDA NO ZERO-A-ZERO*\n{ativo_formatado} recuou e fechou na entrada. Zero prejuízo.")

        elif trade["resultado"] == "TRAILING_TP1" and trade["ativo"] == ativo_formatado:
            if is_compra:
                if preco_atual >= trade["tp3"]: 
                    fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"])
                    mudou = True
                    enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou com potencial máximo!")
                elif preco_atual <= trade["tp1"]: 
                    fechar_trade_contabilidade(trade, "WIN", "TP1", trade["tp1"])
                    mudou = True
                    enviar_telegram(f"💵 *SAÍDA NO TRAILING STOP (+{trade['pontos']} pts)*\n{ativo_formatado} fechou no TP1. Lucro assegurado! 🛡️")
            else:
                if preco_atual <= trade["tp3"]: 
                    fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"])
                    mudou = True
                    enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou com potencial máximo!")
                elif preco_atual >= trade["tp1"]: 
                    fechar_trade_contabilidade(trade, "WIN", "TP1", trade["tp1"])
                    mudou = True
                    enviar_telegram(f"💵 *SAÍDA NO TRAILING STOP (+{trade['pontos']} pts)*\n{ativo_formatado} fechou no TP1. Lucro assegurado! 🛡️")

    if mudou:
        salvar_historico(hist)

def motor_quantitativo_loop():
    while True:
        try:
            verificar_noticias_ao_vivo()
            verificar_relatorios()

            for ativo in ["XAU", "BTC", "EUR"]:
                dados = analisar_ativo_interno(ativo)
                
                if dados.get("status") == "MERCADO_FECHADO" or dados.get("status") == "ERRO_API":
                    continue

                if dados.get("preco_atual"):
                    avaliar_operacoes_abertas_autonomo(dados["preco_atual"], dados["ativo"])

                if dados.get("status") == "SETUP_CONFIRMADO":
                    id_atual = dados["ativo"] + dados["estrategia_ativa"] + dados["data_hora"]
                    hist = ler_historico()
                    
                    if not any(t["id"] == id_atual for t in hist):
                        msg = (f"⚡ *SINAL INSTITUCIONAL DETETADO*\n"
                               f"🪙 *Ativo:* {dados['ativo']}\n"
                               f"🎯 *Estratégia:* {dados['estrategia_ativa']}\n"
                               f"📊 *Sessão:* {dados['zona_operacional']}\n"
                               f"📈 *Macro Tendência:* {dados['tendencia_macro']}\n"
                               f"🛡️ *Volatilidade (ATR):* {dados['atr_atual']}\n\n"
                               f"🟢 *Entrada:* {dados['entrada']}\n"
                               f"🔴 *Stop Loss:* {dados['stop_loss']}\n"
                               f"✅ *TP 1 (Breakeven):* {dados['tp1']}\n"
                               f"✅ *TP 2:* {dados['tp2']}\n"
                               f"✅ *TP 3:* {dados['tp3']}\n\n"
                               f"🧮 *Lote Sugerido ($1.000 / 1% Risco):* {dados['lote_sugerido']}")
                        
                        enviar_telegram(msg)
                        
                        hist.append({
                            "ativo": dados["ativo"],
                            "estrategia": dados["estrategia_ativa"],
                            "entrada": dados["entrada"],
                            "tp1": dados["tp1"],
                            "tp2": dados["tp2"],
                            "tp3": dados["tp3"],
                            "sl": dados["stop_loss"],
                            "resultado": "WAIT",
                            "estado_fechado": False,
                            "id": id_atual
                        })
                        salvar_historico(hist)
        except Exception as e:
            print("Erro no loop principal:", e)
            
        time.sleep(300)

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

if __name__ == '__main__':
    # PORTA OBRIGATÓRIA PARA O RENDER NÃO BLOQUEAR A APLICAÇÃO
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
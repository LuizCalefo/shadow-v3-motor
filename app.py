from flask import Flask, jsonify, request, render_template_string
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
import telebot

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

bot = telebot.TeleBot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

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
    if not bot or not TELEGRAM_CHAT_ID: return
    try:
        bot.send_message(TELEGRAM_CHAT_ID, mensagem, parse_mode="Markdown")
    except Exception as e:
        print("Erro Telegram:", e)

# ==========================================
# COMANDOS INTERATIVOS DO TELEGRAM
# ==========================================
if bot:
    @bot.message_handler(commands=['status'])
    def cmd_status(message):
        agora = datetime.now(FUSO_LISBOA)
        is_killzone = 8 <= agora.hour <= 17
        zona = "🟢 Killzone Institucional (Londres/NY)" if is_killzone else "🔴 Baixo Volume / Ásia"
        tendencia_xau = obter_tendencia_macro("XAU/USD")
        
        msg = (f"🤖 *MOTOR QUANTITATIVO ONLINE*\n\n"
               f"🕒 *Hora local:* {agora.strftime('%H:%M')} (Lisboa)\n"
               f"📊 *Sessão Atual:* {zona}\n"
               f"📈 *Macro Tendência Ouro:* {tendencia_xau}\n"
               f"📡 *Varredura:* Ativa (Ciclos de 5 min)")
        bot.reply_to(message, msg, parse_mode="Markdown")

    @bot.message_handler(commands=['abertas'])
    def cmd_abertas(message):
        hist = ler_historico()
        abertas = [t for t in hist if not t.get("estado_fechado")]
        
        if not abertas:
            bot.reply_to(message, "💤 *Nenhuma operação aberta neste momento.* O mercado está a ser monitorizado.", parse_mode="Markdown")
            return
            
        msg = "📂 *OPERAÇÕES EM ANDAMENTO:*\n\n"
        for t in abertas:
            estado = t.get('resultado', 'WAIT')
            icone = "⏳" if estado == "WAIT" else "🛡️" if estado == "BREAKEVEN" else "🔥"
            msg += f"{icone} *{t['ativo']}* ({t['estrategia']})\n  └ Entrada: {t['entrada']} | Fase: {estado}\n\n"
        
        bot.reply_to(message, msg, parse_mode="Markdown")

    @bot.message_handler(commands=['placar'])
    def cmd_placar(message):
        agora = datetime.now(FUSO_LISBOA)
        data_hoje_str = agora.strftime("%Y-%m-%d")
        
        hist = ler_historico()
        hist_hoje = [t for t in hist if t.get("estado_fechado") and t.get("data_fecho") == data_hoje_str]
        
        if not hist_hoje:
            bot.reply_to(message, "📉 *Nenhuma operação foi fechada hoje até agora.*", parse_mode="Markdown")
            return
            
        w, l, z, pts, alvs, wr = compilar_estatisticas(hist_hoje)
        msg = (f"📊 *PLACAR DE HOJE AO VIVO*\n\n"
               f"⚖️ *Placar:* {w} Wins | {l} Loss | {z} Zero\n"
               f"💰 *Pontos Capturados:* {pts} pts\n"
               f"🎯 *Assertividade:* {wr}%\n")
        bot.reply_to(message, msg, parse_mode="Markdown")

    @bot.message_handler(commands=['varrer'])
    def cmd_varrer(message):
        bot.reply_to(message, "🔍 *Forçando varredura imediata dos servidores de liquidez...* aguarde.", parse_mode="Markdown")
        resumos = []
        for ativo in ["XAU", "BTC", "EUR"]:
            dados = analisar_ativo_interno(ativo)
            status = dados.get('status')
            if status == "SETUP_CONFIRMADO":
                resumos.append(f"🟢 *{ativo}:* Oportunidade detetada!")
            elif status == "MERCADO_FECHADO":
                resumos.append(f"💤 *{ativo}:* Mercado Fechado.")
            else:
                resumos.append(f"🔴 *{ativo}:* Sem setup no momento.")
        
        bot.send_message(message.chat.id, "\n".join(resumos), parse_mode="Markdown")

# ==========================================
# MOTOR QUANTITATIVO
# ==========================================

def analisar_noticia_ia(titulo_evento):
    prompt = f"Uma notícia económica saiu: '{titulo_evento}'. Escreva alerta curto (máx 4 linhas) para traders: Significado, Impacto Ouro/Dólar, Cenário tático. Use emojis."
    try:
        res = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.3)
        return res.choices[0].message.content.strip()
    except: return "⚠️ Notícia macroeconómica detetada. Volatilidade extrema iminente."

def verificar_noticias_ao_vivo():
    global ULTIMA_NOTICIA_PROCESSADA
    try:
        agora = datetime.now(FUSO_LISBOA)
        hora_atual = agora.strftime("%H")
        if 30 <= agora.minute <= 35 and hora_atual != ULTIMA_NOTICIA_PROCESSADA:
            analise_live = analisar_noticia_ia(f"Boletim Macro USD - Horário {hora_atual}:30 (Lisboa)")
            enviar_telegram(f"🚨 *FEED DE NOTÍCIAS AO VIVO (MACRO USA)* 🚨\n\n{analise_live}")
            ULTIMA_NOTICIA_PROCESSADA = hora_atual
    except: pass

def compilar_estatisticas(hist_filtrado):
    wins = losses = zeros = pontos_totais = 0
    alvos = {"TP1": 0, "TP3": 0} 
    for t in hist_filtrado:
        res = t.get("resultado")
        if res == "WIN":
            wins += 1
            alvo_atingido = t.get("alvo", "TP1")
            if alvo_atingido in alvos: alvos[alvo_atingido] += 1
        elif res == "LOSS": losses += 1
        elif res == "ZERO": zeros += 1
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

        if agora.hour == 21 and 50 <= agora.minute <= 59 and data_hoje_str != ULTIMO_RELATORIO_DIARIO:
            hist_hoje = [t for t in hist_fechado if t.get("data_fecho") == data_hoje_str]
            if hist_hoje:
                w, l, z, pts, alvs, wr = compilar_estatisticas(hist_hoje)
                msg_diaria = (f"📅 *FECHO DO DIA (DIÁRIO)*\n\n⚖️ *Placar:* {w} Wins | {l} Loss | {z} Zero\n💰 *Pontos:* {pts} pts\n🎯 *Alvos:* {alvs['TP1']}x TP1 | {alvs['TP3']}x TP3\n\nAté amanhã! 🌙")
                enviar_telegram(msg_diaria)
            ULTIMO_RELATORIO_DIARIO = data_hoje_str

        if agora.weekday() == 4 and agora.hour == 22 and 0 <= agora.minute <= 10 and data_hoje_str != ULTIMO_RELATORIO_SEMANAL:
            semana_atual = agora.isocalendar()[1]
            hist_semana = []
            for t in hist_fechado:
                d_fecho = t.get("data_fecho")
                if d_fecho:
                    try:
                        if datetime.strptime(d_fecho, "%Y-%m-%d").isocalendar()[1] == semana_atual:
                            hist_semana.append(t)
                    except: pass
            if hist_semana:
                w, l, z, pts, alvs, wr = compilar_estatisticas(hist_semana)
                prompt = f"Resumo quantitativo semanal: {w} Wins, {l} Losses, {z} Breakevens, Lucro total: {pts} pontos, Assertividade: {wr}%. Analise de forma profissional e encorajadora (3 linhas)."
                try: comentario_ia = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.3).choices[0].message.content.strip()
                except: comentario_ia = "Semana finalizada com dados consolidados. Gestão de risco aplicada."
                msg_semanal = (f"📊 *BALANÇO SEMANAL - TRADING SHADOW*\n\n🟢 *Wins:* {w} | 🔴 *Losses:* {l} | 🛡️ *Zeros:* {z}\n🔥 *PONTOS TOTAIS:* {pts} pts\n🎯 *Assertividade:* {wr}%\n🏆 *Alvos Atingidos:* TP1 ({alvs['TP1']}) | TP3 ({alvs['TP3']})\n\n🧠 *Nota do Fundo:*\n\"{comentario_ia}\"\n\nBom fim de semana! 🚀")
                enviar_telegram(msg_semanal)
            ULTIMO_RELATORIO_SEMANAL = data_hoje_str
    except: pass

def obter_tendencia_macro(simbolo):
    agora_timestamp = time.time()
    if simbolo in CACHE_MACRO and (agora_timestamp - CACHE_MACRO[simbolo]['timestamp']) < 3600:
        return CACHE_MACRO[simbolo]['tendencia']
    try:
        req_1h = requests.get(f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=1h&outputsize=25&apikey={TWELVEDATA_KEY}").json()
        req_4h = requests.get(f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=4h&outputsize=25&apikey={TWELVEDATA_KEY}").json()
        if "values" not in req_1h or "values" not in req_4h: return CACHE_MACRO[simbolo]['tendencia'] if simbolo in CACHE_MACRO else "LATERAL"
        df1 = pd.DataFrame(req_1h['values'])[::-1].reset_index(drop=True)
        df1['close'] = df1['close'].astype(float)
        ema9_1h, ema21_1h = df1['close'].ewm(span=9, adjust=False).mean().iloc[-1], df1['close'].ewm(span=21, adjust=False).mean().iloc[-1]
        df4 = pd.DataFrame(req_4h['values'])[::-1].reset_index(drop=True)
        df4['close'] = df4['close'].astype(float)
        ema9_4h, ema21_4h = df4['close'].ewm(span=9, adjust=False).mean().iloc[-1], df4['close'].ewm(span=21, adjust=False).mean().iloc[-1]
        tendencia = "ALTA" if ema9_1h > ema21_1h and ema9_4h > ema21_4h else "BAIXA" if ema9_1h < ema21_1h and ema9_4h < ema21_4h else "LATERAL"
        CACHE_MACRO[simbolo] = {'tendencia': tendencia, 'timestamp': agora_timestamp}
        return tendencia
    except: return CACHE_MACRO[simbolo]['tendencia'] if simbolo in CACHE_MACRO else "LATERAL"

def calcular_lote(ativo, entrada, stop_loss):
    distancia = abs(entrada - stop_loss)
    try:
        if ativo == "XAU/USD": return round(10.0 / (distancia * 100), 2)
        elif ativo == "EUR/USD": return round(10.0 / (distancia * 100000), 2)
        elif ativo == "BTC/USD": return round(10.0 / (distancia * 1), 3)
    except: return 0.01
    return 0.01

def analisar_ativo_interno(ativo):
    simbolo = "XAU/USD"
    if ativo == "BTC": simbolo = "BTC/USD"
    elif ativo == "EUR": simbolo = "EUR/USD"

    agora = datetime.now(FUSO_LISBOA)
    if ativo in ["XAU", "EUR"]:
        if (agora.weekday() == 4 and agora.hour >= 21) or agora.weekday() == 5 or (agora.weekday() == 6 and agora.hour < 22):
            return {"status": "MERCADO_FECHADO"}

    is_killzone = 8 <= agora.hour <= 17
    zona_operacional = "🟢 Killzone Institucional (Londres/NY)" if is_killzone else "🔴 Baixo Volume / Ásia"

    try: resp_dados = requests.get(f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}").json()
    except: return {"status": "ERRO_API"}
    if "code" in resp_dados and "values" not in resp_dados: return {"status": "ERRO_API"}
    if "values" not in resp_dados: return {"status": "SEM_SETUP", "ativo": ativo}

    df = pd.DataFrame(resp_dados["values"])
    df = df.iloc[::-1].reset_index(drop=True) 
    for col in ['close', 'high', 'low', 'open']: df[col] = df[col].astype(float)
    
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['prev_close'] = df['close'].shift(1)
    df['tr'] = df.apply(lambda x: max(x['high'] - x['low'], abs(x['high'] - x['prev_close']), abs(x['low'] - x['prev_close'])), axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()

    candle_atual, candle_anterior = df.iloc[-1], df.iloc[-2]
    casas_dec = 5 if ativo == "EUR" else 2
    preco_atual, ema_9_atual, ema_21_atual, atr_atual = round(candle_atual['close'], casas_dec), candle_atual['ema_9'], candle_atual['ema_21'], candle_atual['atr']

    status_setup, tp1, tp2, tp3, sl, estrategia, prob = False, 0.0, 0.0, 0.0, 0.0, "", 0
    tendencia = obter_tendencia_macro(simbolo)
    
    if ema_9_atual > ema_21_atual and tendencia == "ALTA":
        if -atr_atual <= (candle_atual['low'] - ema_21_atual) <= atr_atual:
            status_setup, estrategia, prob = True, "PULLBACK FLEXÍVEL (COMPRA)", 82 if is_killzone else 70
        elif candle_anterior['close'] < candle_anterior['ema_9'] and preco_atual > ema_9_atual:
            status_setup, estrategia, prob = True, "MOMENTUM SCALPER (COMPRA)", 75 if is_killzone else 65
        if status_setup:
            sl = round(preco_atual - (1.5 * atr_atual), casas_dec)
            tp1, tp2, tp3 = [round(preco_atual + (m * atr_atual), casas_dec) for m in [1.0, 2.0, 3.0]]

    elif ema_9_atual <= ema_21_atual and tendencia == "BAIXA":
        if candle_atual['high'] >= ema_9_atual and candle_atual['close'] < ema_9_atual:
            status_setup, estrategia, prob = True, "REJEIÇÃO DE TOPO (VENDA)", 78 if is_killzone else 68
            sl = round(preco_atual + (1.5 * atr_atual), casas_dec)
            tp1, tp2, tp3 = [round(preco_atual - (m * atr_atual), casas_dec) for m in [1.0, 2.0, 3.0]]

    resultado = {"ativo": simbolo.replace("/", ""), "preco_atual": preco_atual, "atr_atual": round(atr_atual, casas_dec), "status": "SEM_SETUP"}
    if status_setup:
        resultado.update({"status": "SETUP_CONFIRMADO", "estrategia_ativa": estrategia, "entrada": preco_atual, "stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "probabilidade": f"{prob}%", "tendencia_macro": tendencia, "zona_operacional": zona_operacional, "lote_sugerido": calcular_lote(simbolo, preco_atual, sl), "data_hora": resp_dados["values"][0]["datetime"]})
    return resultado

def fechar_trade_contabilidade(trade, resultado, alvo, preco_saida):
    trade["resultado"], trade["estado_fechado"], trade["data_fecho"], trade["alvo"] = resultado, True, datetime.now(FUSO_LISBOA).strftime("%Y-%m-%d"), alvo
    is_compra = trade["tp1"] > trade["entrada"]
    diff = preco_saida - trade["entrada"] if is_compra else trade["entrada"] - preco_saida
    pts = diff * 100000 if "EUR" in trade["ativo"] else diff * 100 if "XAU" in trade["ativo"] else diff
    trade["pontos"] = round(pts, 1)

def avaliar_operacoes_abertas_autonomo(preco_atual, ativo_formatado):
    hist = ler_historico()
    mudou = False
    for trade in hist:
        if trade.get("estado_fechado"): continue
        is_compra = trade["tp1"] > trade["entrada"]

        if trade["resultado"] == "WAIT" and trade["ativo"] == ativo_formatado:
            if is_compra:
                if preco_atual >= trade["tp3"]: fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"]); mudou = True; enviar_telegram(f"🎯 *TAKE PROFIT 3 ALCANÇADO (+{trade['pontos']} pts)*\n*{ativo_formatado}* esmagou o TP3! 🚀")
                elif preco_atual >= trade["tp1"]: trade["resultado"] = "BREAKEVEN"; mudou = True; enviar_telegram(f"🏆 *VITÓRIA GARANTIDA (TP1)*\n*{ativo_formatado}* cravou o TP1! Risco anulado. 🛡️")
                elif preco_atual <= trade["sl"]: fechar_trade_contabilidade(trade, "LOSS", "SL", trade["sl"]); mudou = True; enviar_telegram(f"❌ *STOP LOSS ATINGIDO ({trade['pontos']} pts)*\n{ativo_formatado} fechou.")
            else: 
                if preco_atual <= trade["tp3"]: fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"]); mudou = True; enviar_telegram(f"🎯 *TAKE PROFIT 3 ALCANÇADO (+{trade['pontos']} pts)*\n*{ativo_formatado}* esmagou o TP3! 🚀")
                elif preco_atual <= trade["tp1"]: trade["resultado"] = "BREAKEVEN"; mudou = True; enviar_telegram(f"🏆 *VITÓRIA GARANTIDA (TP1)*\n*{ativo_formatado}* cravou o TP1! Risco anulado. 🛡️")
                elif preco_atual >= trade["sl"]: fechar_trade_contabilidade(trade, "LOSS", "SL", trade["sl"]); mudou = True; enviar_telegram(f"❌ *STOP LOSS ATINGIDO ({trade['pontos']} pts)*\n{ativo_formatado} fechou.")
        
        elif trade["resultado"] == "BREAKEVEN" and trade["ativo"] == ativo_formatado:
            if is_compra:
                if preco_atual >= trade["tp3"]: fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"]); mudou = True; enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou!")
                elif preco_atual >= trade["tp2"]: trade["resultado"] = "TRAILING_TP1"; mudou = True; enviar_telegram(f"🔥 *TRAILING STOP (TP2)*\n{ativo_formatado} rompeu TP2! Stop no TP1. 💰")
                elif preco_atual <= trade["entrada"]: fechar_trade_contabilidade(trade, "ZERO", "ZERO", trade["entrada"]); mudou = True; enviar_telegram(f"⚖️ *SAÍDA NO ZERO-A-ZERO*\n{ativo_formatado} recuou e fechou na entrada.")
            else:
                if preco_atual <= trade["tp3"]: fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"]); mudou = True; enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou!")
                elif preco_atual <= trade["tp2"]: trade["resultado"] = "TRAILING_TP1"; mudou = True; enviar_telegram(f"🔥 *TRAILING STOP (TP2)*\n{ativo_formatado} rompeu TP2! Stop no TP1. 💰")
                elif preco_atual >= trade["entrada"]: fechar_trade_contabilidade(trade, "ZERO", "ZERO", trade["entrada"]); mudou = True; enviar_telegram(f"⚖️ *SAÍDA NO ZERO-A-ZERO*\n{ativo_formatado} recuou e fechou na entrada.")

        elif trade["resultado"] == "TRAILING_TP1" and trade["ativo"] == ativo_formatado:
            if is_compra:
                if preco_atual >= trade["tp3"]: fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"]); mudou = True; enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou!")
                elif preco_atual <= trade["tp1"]: fechar_trade_contabilidade(trade, "WIN", "TP1", trade["tp1"]); mudou = True; enviar_telegram(f"💵 *SAÍDA NO TRAILING STOP (+{trade['pontos']} pts)*\n{ativo_formatado} fechou no TP1. 🛡️")
            else:
                if preco_atual <= trade["tp3"]: fechar_trade_contabilidade(trade, "WIN", "TP3", trade["tp3"]); mudou = True; enviar_telegram(f"🚀 *ALVO FINAL (TP3) ATINGIDO! (+{trade['pontos']} pts)* \n{ativo_formatado} fechou!")
                elif preco_atual >= trade["tp1"]: fechar_trade_contabilidade(trade, "WIN", "TP1", trade["tp1"]); mudou = True; enviar_telegram(f"💵 *SAÍDA NO TRAILING STOP (+{trade['pontos']} pts)*\n{ativo_formatado} fechou no TP1. 🛡️")
    if mudou: salvar_historico(hist)

def motor_quantitativo_loop():
    while True:
        try:
            verificar_noticias_ao_vivo()
            verificar_relatorios()
            for ativo in ["XAU", "BTC", "EUR"]:
                dados = analisar_ativo_interno(ativo)
                if dados.get("status") in ["MERCADO_FECHADO", "ERRO_API"]: continue
                if dados.get("preco_atual"): avaliar_operacoes_abertas_autonomo(dados["preco_atual"], dados["ativo"])
                if dados.get("status") == "SETUP_CONFIRMADO":
                    id_atual = dados["ativo"] + dados["estrategia_ativa"] + dados["data_hora"]
                    hist = ler_historico()
                    if not any(t["id"] == id_atual for t in hist):
                        msg = (f"⚡ *SINAL INSTITUCIONAL DETETADO*\n🪙 *Ativo:* {dados['ativo']}\n🎯 *Estratégia:* {dados['estrategia_ativa']}\n📊 *Sessão:* {dados['zona_operacional']}\n📈 *Macro:* {dados['tendencia_macro']} | 🛡️ *ATR:* {dados['atr_atual']}\n\n🟢 *Entrada:* {dados['entrada']}\n🔴 *SL:* {dados['stop_loss']}\n✅ *TP1:* {dados['tp1']} | *TP2:* {dados['tp2']} | *TP3:* {dados['tp3']}\n\n🧮 *Lote ($1k/1%):* {dados['lote_sugerido']}")
                        enviar_telegram(msg)
                        hist.append({"ativo": dados["ativo"], "estrategia": dados["estrategia_ativa"], "entrada": dados["entrada"], "tp1": dados["tp1"], "tp2": dados["tp2"], "tp3": dados["tp3"], "sl": dados["stop_loss"], "resultado": "WAIT", "estado_fechado": False, "id": id_atual})
                        salvar_historico(hist)
        except Exception as e: print("Erro no loop principal:", e)
        time.sleep(300)

# ==========================================
# THREADS
# ==========================================
threading.Thread(target=motor_quantitativo_loop, daemon=True).start()
if bot:
    threading.Thread(target=bot.infinity_polling, daemon=True).start()

# ==========================================
# ROTA DO PAINEL WEB (DASHBOARD)
# ==========================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Shadow | Institutional Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background-color: #0b0f19; color: #f3f4f6; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 1000px; margin: auto; }
        header { text-align: center; margin-bottom: 30px; border-bottom: 1px solid #1f2937; padding-bottom: 20px; }
        h1 { color: #10b981; margin: 0; font-size: 24px; letter-spacing: 1px; }
        p { color: #9ca3af; font-size: 14px; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .card { background-color: #111827; border: 1px solid #1f2937; padding: 20px; border-radius: 8px; text-align: center; }
        .card h3 { margin: 0; font-size: 14px; color: #9ca3af; text-transform: uppercase; }
        .card .value { font-size: 22px; font-weight: bold; margin-top: 10px; color: #f3f4f6; }
        .chart-container { background-color: #111827; border: 1px solid #1f2937; padding: 20px; border-radius: 8px; margin-bottom: 30px; }
        table { width: 100%; border-collapse: collapse; background-color: #111827; border-radius: 8px; overflow: hidden; border: 1px solid #1f2937; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #1f2937; font-size: 14px; }
        th { background-color: #1f2937; color: #10b981; text-transform: uppercase; font-size: 12px; }
        .badge-win { color: #10b981; font-weight: bold; }
        .badge-loss { color: #ef4444; font-weight: bold; }
        .badge-wait { color: #f59e0b; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🛡️ TRADING SHADOW | QUANT DESK</h1>
            <p>Painel de Controlo e Desempenho Institucional em Tempo Real</p>
        </header>

        <div class="cards">
            <div class="card">
                <h3>Total de Pontos</h3>
                <div class="value" id="total-pontos">0.0 pts</div>
            </div>
            <div class="card">
                <h3>Taxa de Acerto (WinRate)</h3>
                <div class="value" id="win-rate">0%</div>
            </div>
            <div class="card">
                <h3>Ordens Fechadas</h3>
                <div class="value" id="total-trades">0</div>
            </div>
        </div>

        <div class="chart-container">
            <canvas id="equityChart" height="100"></canvas>
        </div>

        <div style="background-color: #111827; border: 1px solid #1f2937; padding: 20px; border-radius: 8px;">
            <h3 style="margin-top:0; color:#f3f4f6; font-size:16px;">Histórico de Operações</h3>
            <table>
                <thead>
                    <tr>
                        <th>Ativo</th>
                        <th>Estratégia</th>
                        <th>Entrada</th>
                        <th>Resultado</th>
                        <th>Pontos</th>
                    </tr>
                </thead>
                <tbody id="tabela-corpo">
                    <tr><td colspan="5" style="text-align:center;">A carregar dados...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function carregarDados() {
            try {
                let res = await fetch('/get-historico');
                let hist = await res.json();
                
                let fechados = hist.filter(t => t.estado_fechado);
                let totalPts = fechados.reduce((acc, t) => acc + (t.pontos || 0), 0);
                let wins = fechados.filter(t => t.resultado === 'WIN').length;
                let wr = fechados.length > 0 ? ((wins / fechados.length) * 100).toFixed(1) : 0;

                document.getElementById('total-pontos').innerText = totalPts.toFixed(1) + " pts";
                document.getElementById('win-rate').innerText = wr + "%";
                document.getElementById('total-trades').innerText = fechados.length;

                // Preencher Tabela
                let tbody = document.getElementById('tabela-corpo');
                tbody.innerHTML = "";
                if (hist.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">Sem registos recentes.</td></tr>';
                    return;
                }

                // Linha do tempo para o gráfico
                let labels = [];
                let dataPoints = [];
                let acumulado = 0;

                hist.slice().reverse().forEach(t => {
                    if(t.estado_fechado) {
                        acumulado += (t.pontos || 0);
                        labels.push(t.data_fecho || 'Data N/D');
                        dataPoints.push(acumulado);
                    }

                    let tr = document.createElement('tr');
                    let badgeClass = t.resultado === 'WIN' ? 'badge-win' : t.resultado === 'LOSS' ? 'badge-loss' : 'badge-wait';
                    tr.innerHTML = `
                        <td><b>${t.ativo}</b></td>
                        <td>${t.estrategia}</td>
                        <td>${t.entrada}</td>
                        <td><span class="${badgeClass}">${t.resultado}</span></td>
                        <td>${t.pontos || 0} pts</td>
                    `;
                    tbody.appendChild(tr);
                });

                // Desenhar Gráfico
                const ctx = document.getElementById('equityChart').getContext('2d');
                new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels.length ? labels : ['Início'],
                        datasets: [{
                            label: 'Curva de Capital (Pontos Acumulados)',
                            data: dataPoints.length ? dataPoints : [0],
                            borderColor: '#10b981',
                            backgroundColor: 'rgba(16, 185, 129, 0.1)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.3
                        }]
                    },
                    options: {
                        responsive: true,
                        plugins: { legend: { labels: { color: '#9ca3af' } } },
                        scales: {
                            x: { ticks: { color: '#9ca3af' }, grid: { color: '#1f2937' } },
                            y: { ticks: { color: '#9ca3af' }, grid: { color: '#1f2937' } }
                        }
                    }
                });

            } catch (e) {
                console.error("Erro ao carregar painel:", e);
            }
        }
        carregarDados();
    </script>
</body>
</html>
"""

@app.route('/dashboard')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/ping')
def ping(): return jsonify({"status": "motor_aquecido_com_dashboard"})

@app.route('/get-historico', methods=['GET'])
def get_historico(): return jsonify(ler_historico())

@app.route('/sync-historico', methods=['POST'])
def sync_historico():
    salvar_historico(request.json)
    return jsonify({"status": "sucesso"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
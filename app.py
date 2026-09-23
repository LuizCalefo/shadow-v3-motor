from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import pandas as pd
import sqlite3
import os
import threading
import time
from datetime import datetime, date
import pytz
import telebot
from groq import Groq

app = Flask(__name__)
CORS(app)

# ==========================================
# CONFIGURAÇÕES E VARIÁVEIS DE AMBIENTE
# ==========================================
GROQ_KEY = os.environ.get("GROQ_API_KEY")
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

FUSO_LISBOA = pytz.timezone('Europe/Lisbon')
DB_FILE = 'trading_shadow.db'
BLOQUEIO_NOTICIAS = False

bot = telebot.TeleBot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

# ==========================================
# GESTÃO SEGURA DE BASE DE DADOS (SQLITE)
# ==========================================
def executar_query(query, params=(), fetch=False):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        if fetch: conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch:
            resultado = cursor.fetchall()
            conn.close()
            return [dict(row) for row in resultado]
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erro SQL: {e}")
        return [] if fetch else None

def inicializar_bd():
    executar_query('''
        CREATE TABLE IF NOT EXISTS historico (
            id TEXT PRIMARY KEY,
            ativo TEXT,
            estrategia TEXT,
            entrada REAL,
            entrada_max REAL,
            tp1 REAL,
            tp2 REAL,
            tp3 REAL,
            sl REAL,
            resultado TEXT,
            estado_fechado INTEGER,
            data_fecho TEXT,
            pontos REAL,
            direcao TEXT
        )
    ''')

inicializar_bd()

def enviar_telegram(mensagem):
    if not bot or not TELEGRAM_CHAT_ID: return
    try:
        bot.send_message(TELEGRAM_CHAT_ID, mensagem, parse_mode="Markdown")
    except Exception as e:
        print("Erro Telegram:", e)

# ==========================================
# ANÁLISE TÉCNICA E IA (GROQ)
# ==========================================
def calcular_indicadores_institucionais(df):
    df['swing_high'] = df['high'].rolling(window=10, center=True).max()
    df['swing_low'] = df['low'].rolling(window=10, center=True).min()
    df['liquidez_topo'] = df['swing_high'].ffill()
    df['liquidez_fundo'] = df['swing_low'].ffill()
    df['fvg_alta'] = (df['low'] > df['high'].shift(2))
    df['fvg_baixa'] = (df['high'] < df['low'].shift(2))
    return df

def obter_dados_mercado(simbolo):
    url = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=100&apikey={TWELVEDATA_KEY}"
    try:
        req = requests.get(url, timeout=10)
        dados = req.json()
        if "values" not in dados: return None
        
        df = pd.DataFrame(dados["values"])
        df = df.iloc[::-1].reset_index(drop=True)
        for col in ['close', 'high', 'low', 'open']: df[col] = df[col].astype(float)
        return calcular_indicadores_institucionais(df)
    except:
        return None

def gerar_argumento_ia(ativo, direcao, estrategia, atr):
    if not GROQ_KEY: return f"Análise técnica {estrategia} validada."
    try:
        client = Groq(api_key=GROQ_KEY)
        prompt = f"Age como um trader institucional. Setup de {direcao} no ativo {ativo} usando {estrategia}. Canal atual: {atr}. Escreve 2 frases curtas justificando a entrada com base na captura de liquidez e preenchimento de FVG. Sem hashtags."
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192", temperature=0.3, max_tokens=100
        )
        return chat.choices[0].message.content.strip()
    except:
        return "Captura de liquidez identificada. Projeção ativada."

def analisar_ativo(ativo):
    simbolo = "XAU/USD" if ativo == "XAU" else "BTC/USD" if ativo == "BTC" else "EUR/USD"
    casas_dec = 5 if ativo == "EUR" else 2

    trades_abertos = executar_query("SELECT id FROM historico WHERE ativo=? AND estado_fechado=0", (ativo,), fetch=True)
    if trades_abertos: return {"status": "AGUARDAR"}

    df = obter_dados_mercado(simbolo)
    if df is None or len(df) < 5: return {"status": "ERRO"}

    vela_atual, vela_1, vela_2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    preco = round(vela_atual['close'], casas_dec)
    
    tamanho_canal = df['high'].iloc[-5:].max() - df['low'].iloc[-5:].min()
    if tamanho_canal == 0: tamanho_canal = 0.001 
    
    status_setup, direcao, estrategia = False, "", ""
    sl, tp1, tp2, tp3, entrada, entrada_max = 0, 0, 0, 0, 0, 0

    sweep_fundo = vela_2['low'] <= vela_atual['liquidez_fundo'] or vela_1['low'] <= vela_atual['liquidez_fundo']
    sweep_topo = vela_2['high'] >= vela_atual['liquidez_topo'] or vela_1['high'] >= vela_atual['liquidez_topo']
    
    if sweep_fundo and vela_atual['fvg_alta']:
        status_setup, direcao, estrategia = True, "COMPRA", "SMC/ICT: MANIPULAÇÃO DE FUNDO"
        entrada = preco
        entrada_max = round(preco - (tamanho_canal * 0.2), casas_dec) 
        sl = round(entrada - (tamanho_canal * 2), casas_dec)
        tp1 = round(entrada + (tamanho_canal * 1), casas_dec)
        tp2 = round(entrada + (tamanho_canal * 2), casas_dec)
        tp3 = round(entrada + (tamanho_canal * 3), casas_dec)
        
    elif sweep_topo and vela_atual['fvg_baixa']:
        status_setup, direcao, estrategia = True, "VENDA", "SMC/ICT: MANIPULAÇÃO DE TOPO"
        entrada = preco
        entrada_max = round(preco + (tamanho_canal * 0.2), casas_dec)
        sl = round(entrada + (tamanho_canal * 2), casas_dec)
        tp1 = round(entrada - (tamanho_canal * 1), casas_dec)
        tp2 = round(entrada - (tamanho_canal * 2), casas_dec)
        tp3 = round(entrada - (tamanho_canal * 3), casas_dec)

    if status_setup:
        return {
            "status": "SETUP_CONFIRMADO", "id": f"{ativo}_{int(time.time())}", "ativo": ativo, 
            "direcao": direcao, "estrategia_ativa": estrategia, "entrada": entrada, 
            "entrada_max": entrada_max, "stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
            "atr_atual": round(tamanho_canal, casas_dec)
        }
    return {"status": "SEM_SETUP"}

# ==========================================
# GESTÃO ATIVA: ACOMPANHAMENTO E RELATÓRIO
# ==========================================
def gerir_operacoes_abertas():
    abertas = executar_query("SELECT * FROM historico WHERE estado_fechado=0", fetch=True)
    if not abertas: return

    for trade in abertas:
        ativo = trade['ativo']
        simbolo = "XAU/USD" if ativo == "XAU" else "BTC/USD" if ativo == "BTC" else "EUR/USD"
        df = obter_dados_mercado(simbolo)
        if df is None: continue
        preco = df.iloc[-1]['close']
        is_compra = trade['direcao'] == "COMPRA"
        fase = trade['resultado']
        
        if fase == "WAIT":
            if (is_compra and preco <= trade['entrada']) or (not is_compra and preco >= trade['entrada']):
                executar_query("UPDATE historico SET resultado='ACTIVE' WHERE id=?", (trade['id'],))
                enviar_telegram(f"🔔 *ORDEM ATIVADA* | {ativo}\nO preço bateu na zona de entrada ({trade['entrada']}).")
                
        elif fase == "ACTIVE":
            if (is_compra and preco <= trade['sl']) or (not is_compra and preco >= trade['sl']):
                executar_query("UPDATE historico SET resultado='LOSS', estado_fechado=1 WHERE id=?", (trade['id'],))
                enviar_telegram(f"🔴 *RELATÓRIO: LOSS* | {ativo}\nO preço atingiu o Stop Loss ({trade['sl']}). Operação fechada.")
            elif (is_compra and preco >= trade['tp1']) or (not is_compra and preco <= trade['tp1']):
                executar_query("UPDATE historico SET resultado='BREAKEVEN' WHERE id=?", (trade['id'],))
                enviar_telegram(f"🟡 *ATUALIZAÇÃO* | {ativo}\n✅ TP1 Atingido ({trade['tp1']})!\nLucro parcial garantido. Mova o SL para a entrada.")

        elif fase == "BREAKEVEN":
            if (is_compra and preco >= trade['tp2']) or (not is_compra and preco <= trade['tp2']):
                executar_query("UPDATE historico SET resultado='WIN', estado_fechado=1 WHERE id=?", (trade['id'],))
                enviar_telegram(f"🟢 *RELATÓRIO: WIN* | {ativo}\n🎯 Alvo Máximo (TP2) cravado em {trade['tp2']}! Trade encerrado.")
            elif (is_compra and preco <= trade['entrada']) or (not is_compra and preco >= trade['entrada']):
                executar_query("UPDATE historico SET resultado='ZERO', estado_fechado=1 WHERE id=?", (trade['id'],))
                enviar_telegram(f"⚪ *RELATÓRIO: 0a0* | {ativo}\nO mercado reverteu após o TP1. Saída no ponto de entrada ({trade['entrada']}).")

def enviar_relatorio_diario():
    hoje_inicio = int(datetime.combine(date.today(), datetime.min.time()).timestamp())
    trades = executar_query("SELECT * FROM historico", fetch=True)
    trades_hoje = [t for t in trades if int(t['id'].split('_')[1]) >= hoje_inicio]
    
    if not trades_hoje: return
    
    wins = sum(1 for t in trades_hoje if t['resultado'] == 'WIN')
    losses = sum(1 for t in trades_hoje if t['resultado'] == 'LOSS')
    zeros = sum(1 for t in trades_hoje if t['resultado'] == 'ZERO')
    abertas = sum(1 for t in trades_hoje if t['estado_fechado'] == 0)
    total_fechadas = wins + losses + zeros
    winrate = (wins / total_fechadas * 100) if total_fechadas > 0 else 0

    msg = (f"📊 *FECHO DE SESSÃO TÁTICO*\n📅 Data: {date.today().strftime('%d/%m/%Y')}\n\n"
           f"📈 *Performance:*\n✅ Wins (TP2): {wins}\n❌ Losses (SL): {losses}\n"
           f"🛡️ Protegidas (0a0): {zeros}\n⏳ Em Aberto: {abertas}\n\n"
           f"🎯 *Winrate:* {winrate:.1f}%")
    enviar_telegram(msg)

# ==========================================
# LOOP PRINCIPAL (100% AUTÓNOMO)
# ==========================================
def motor_quantitativo_loop():
    relatorio_enviado_hoje = False
    
    while True:
        try:
            agora = datetime.now(FUSO_LISBOA)
            if agora.hour == 0: relatorio_enviado_hoje = False
            
            if agora.hour == 22 and agora.minute == 0 and not relatorio_enviado_hoje:
                enviar_relatorio_diario()
                relatorio_enviado_hoje = True

            if not BLOQUEIO_NOTICIAS:
                gerir_operacoes_abertas() 
                for ativo in ["XAU", "BTC", "EUR"]:
                    dados = analisar_ativo(ativo)
                    if dados.get("status") == "SETUP_CONFIRMADO":
                        ia_text = gerar_argumento_ia(dados['ativo'], dados['direcao'], dados['estrategia_ativa'], dados['atr_atual'])
                        
                        msg = (f"⚡ *SINAL VIP INSTITUCIONAL*\n🪙 Ativo: {dados['ativo']}\n"
                               f"🔄 Direção: *{dados['direcao']}*\n\n"
                               f"🎯 Zona de Ação: {dados['entrada_max']} até {dados['entrada']}\n"
                               f"🔴 Stop Loss: {dados['stop_loss']}\n"
                               f"✅ TP 1: {dados['tp1']}\n🚀 TP 2: {dados['tp2']}\n\n"
                               f"🧠 *Lógica Institucional:*\n{ia_text}")
                        
                        enviar_telegram(msg)
                        executar_query('''INSERT INTO historico (id, ativo, estrategia, entrada, entrada_max, tp1, tp2, tp3, sl, resultado, estado_fechado, direcao)
                                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'WAIT', 0, ?)''', 
                                       (dados['id'], dados['ativo'], dados['estrategia_ativa'], dados['entrada'], dados['entrada_max'], dados['tp1'], dados['tp2'], dados['tp3'], dados['stop_loss'], dados['direcao']))
        except Exception as e:
            print("Erro:", e)
        time.sleep(60)

threading.Thread(target=motor_quantitativo_loop, daemon=True).start()

# ==========================================
# ROTAS API / WEB DASHBOARD
# ==========================================
@app.route('/analisar', methods=['GET'])
def analisar_api():
    ativo = request.args.get('ativo', 'XAU')
    if request.args.get('teste', 'false') == 'true':
        enviar_telegram(f"🔔 *TESTE DE SISTEMA* 🔔\nConexão estabelecida com sucesso no ativo {ativo}.")
        return jsonify({"status": "SETUP_CONFIRMADO", "ativo": ativo, "estrategia_ativa": "TESTE", "entrada": 0, "stop_loss": 0, "tp1": 0})
    return jsonify(analisar_ativo(ativo))

@app.route('/get-historico', methods=['GET'])
def get_historico(): 
    return jsonify(executar_query("SELECT * FROM historico ORDER BY rowid DESC LIMIT 50", fetch=True))

@app.route('/radar-mensal', methods=['GET'])
def radar_mensal(): return jsonify({"radar": "SMC & Fimathe Integrados. Sistema 100% autónomo."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
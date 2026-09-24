import os
import sqlite3
import time
import logging
import json
import threading
from datetime import datetime
import numpy as np
import pandas as pd
import pytz
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, jsonify
from flask_cors import CORS
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
import websocket
from dotenv import load_dotenv

# Carrega as chaves secretas do teu ficheiro .env automaticamente!
load_dotenv()

# ==========================================
# ⚙️ 1. CONFIGURAÇÕES GERAIS E LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

GROQ_KEY = os.environ.get("GROQ_API_KEY")
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_KEY) if GROQ_KEY else None
bot = telebot.TeleBot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

FUSO_LISBOA = pytz.timezone('Europe/Lisbon')
DB_FILE = 'trading_shadow.db'
CACHE_MACRO = {}

# ==========================================
# 🗄️ 2. BASE DE DADOS TRANSACIONAL (SQLITE)
# ==========================================
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def inicializar_bd():
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS historico (
                id TEXT PRIMARY KEY,
                ativo TEXT,
                estrategia TEXT,
                direcao TEXT,
                entrada REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                sl REAL,
                resultado TEXT,
                estado_fechado INTEGER,
                data_fecho TEXT,
                pontos REAL,
                vpoc REAL
            )
        ''')
        conn.commit()
    logger.info("Base de dados inicializada com sucesso.")

inicializar_bd()

def inserir_trade(trade):
    with get_db_connection() as conn:
        conn.execute('''
            INSERT OR IGNORE INTO historico 
            (id, ativo, estrategia, direcao, entrada, tp1, tp2, tp3, sl, resultado, estado_fechado, vpoc, pontos)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade["id"], trade["ativo"], trade["estrategia"], trade["direcao"], 
            trade["entrada"], trade["tp1"], trade["tp2"], trade["tp3"], trade["sl"], 
            "WAIT", 0, trade.get("vpoc", 0.0), 0.0
        ))
        conn.commit()

def atualizar_trade(trade_id, resultado, estado_fechado, data_fecho, pontos):
    with get_db_connection() as conn:
        conn.execute('''
            UPDATE historico SET resultado = ?, estado_fechado = ?, data_fecho = ?, pontos = ? WHERE id = ?
        ''', (resultado, estado_fechado, data_fecho, pontos, trade_id))
        conn.commit()

def obter_trades_abertas():
    with get_db_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM historico WHERE estado_fechado = 0").fetchall()]

# ==========================================
# 🧠 3. MOTOR QUANTITATIVO E VOLUME PROFILE
# ==========================================
def calcular_vpoc(df, periodos=20):
    try:
        df_recente = df.tail(periodos).copy()
        if 'volume' not in df_recente.columns: return None
        df_recente['price_bin'] = pd.cut(df_recente['close'], bins=12)
        perfil_volume = df_recente.groupby('price_bin')['volume'].sum()
        return round(perfil_volume.idxmax().mid, 2)
    except Exception as e:
        logger.error(f"Erro no cálculo do VPOC: {e}")
        return None

def requisicao_api(url, max_tentativas=3):
    for _ in range(max_tentativas):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200: return resp.json()
        except: time.sleep(2)
    return None

def obter_tendencia_macro(simbolo):
    agora = time.time()
    if simbolo in CACHE_MACRO and (agora - CACHE_MACRO[simbolo]['timestamp']) < 3600:
        return CACHE_MACRO[simbolo]['tendencia']
        
    url = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=4h&outputsize=25&apikey={TWELVEDATA_KEY}"
    dados = requisicao_api(url)
    if not dados or "values" not in dados: return "LATERAL"
    
    df = pd.DataFrame(dados['values'])[::-1].reset_index(drop=True)
    df['close'] = df['close'].astype(float)
    ema9, ema21 = df['close'].ewm(span=9).mean().iloc[-1], df['close'].ewm(span=21).mean().iloc[-1]
    
    tend = "ALTA" if ema9 > ema21 else "BAIXA"
    CACHE_MACRO[simbolo] = {'tendencia': tend, 'timestamp': agora}
    return tend

def analisar_ativo(ativo, simbolo):
    agora = datetime.now(FUSO_LISBOA)
    # Filtro para não operar nos fins de semana (fecho de sexta até abertura de domingo à noite)
    if ativo in ["XAU", "EUR"] and (agora.weekday() == 5 or (agora.weekday() == 4 and agora.hour >= 21) or (agora.weekday() == 6 and agora.hour < 22)):
        return None

    dados = requisicao_api(f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=50&apikey={TWELVEDATA_KEY}")
    if not dados or "values" not in dados: return None

    df = pd.DataFrame(dados["values"])[::-1].reset_index(drop=True)
    for c in ['close', 'high', 'low', 'open']: df[c] = df[c].astype(float)
    if 'volume' in df.columns: df['volume'] = df['volume'].astype(float)
    
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
    df['atr'] = df['tr'].rolling(14).mean()

    candle = df.iloc[-1]
    casas_dec = 5 if ativo == "EUR" else 2
    preco, ema9, ema21, atr = round(candle['close'], casas_dec), candle['ema_9'], candle['ema_21'], candle['atr']
    tendencia = obter_tendencia_macro(simbolo)
    vpoc = calcular_vpoc(df)
    
    setup = None
    # Lógica de Setup
    if ema9 > ema21 and tendencia == "ALTA":
        if -atr <= (candle['low'] - ema21) <= atr:
            sl = round(preco - (1.5 * atr), casas_dec)
            setup = {"direcao": "COMPRA", "estrategia": "Pullback Institucional"}
    elif ema9 <= ema21 and tendencia == "BAIXA":
        if candle['high'] >= ema9 and candle['close'] < ema9:
            sl = round(preco + (1.5 * atr), casas_dec)
            setup = {"direcao": "VENDA", "estrategia": "Rejeição de Liquidez"}

    if setup:
        mult = 1 if setup["direcao"] == "COMPRA" else -1
        setup.update({
            "ativo": ativo,
            "entrada": preco,
            "sl": sl,
            "tp1": round(preco + (mult * atr * 1.0), casas_dec),
            "tp2": round(preco + (mult * atr * 2.0), casas_dec),
            "tp3": round(preco + (mult * atr * 3.0), casas_dec),
            "vpoc": vpoc if vpoc else 0.0,
            "data_hora": dados["values"][0]["datetime"]
        })
        setup["id"] = f"{ativo}_{setup['direcao']}_{setup['data_hora'].replace(' ', '_')}"
        return setup
    return None

def verificar_setups():
    for ativo, simbolo in [("XAU", "XAU/USD"), ("BTC", "BTC/USD"), ("EUR", "EUR/USD")]:
        try:
            setup = analisar_ativo(ativo, simbolo)
            if setup:
                with get_db_connection() as conn:
                    existe = conn.execute("SELECT 1 FROM historico WHERE id = ?", (setup["id"],)).fetchone()
                
                if not existe:
                    inserir_trade(setup)
                    vpoc_str = f"<code>{setup['vpoc']}</code>" if setup['vpoc'] else "Indisponível"
                    emoji_dir = "🟢 COMPRA" if setup["direcao"] == "COMPRA" else "🔴 VENDA"
                    
                    msg = (
                        f"⚡ <b>𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐒𝐈𝐆𝐍𝐀𝐋 | {setup['ativo']}</b> ⚡\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 <b>Setup:</b> {setup['estrategia']}\n"
                        f"📈 <b>Direção:</b> {emoji_dir}\n\n"
                        f"📍 <b>Entrada:</b> <code>{setup['entrada']}</code>\n"
                        f"🛑 <b>Stop Loss:</b> <code>{setup['sl']}</code>\n\n"
                        f"✅ <b>TP 1:</b> <code>{setup['tp1']}</code> (RR 1:1)\n"
                        f"✅ <b>TP 2:</b> <code>{setup['tp2']}</code> (RR 1:2)\n"
                        f"✅ <b>TP 3:</b> <code>{setup['tp3']}</code> (RR 1:3)\n\n"
                        f"📊 <b>Filtro VPOC (Volume):</b> {vpoc_str}\n"
                        f"<i>Gestão de Risco: Arriscar max 1% do capital.</i>\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton("📈 Ver Gráfico (TradingView)", url=f"https://www.tradingview.com/chart/?symbol={ativo}USD"))
                    enviar_telegram(msg, markup)
        except Exception as e:
            logger.error(f"Erro a analisar {ativo}: {e}")

# ==========================================
# ⚡ 4. WEBSOCKETS (Gestão ao Milissegundo)
# ==========================================
def gerir_trade_tick(ativo, preco_live):
    abertas = obter_trades_abertas()
    hoje = datetime.now(FUSO_LISBOA).strftime("%Y-%m-%d")

    for t in abertas:
        if t["ativo"] != ativo: continue
        is_compra = t["direcao"] == "COMPRA"
        fechar, resultado, p_saida = False, "", 0.0

        if is_compra:
            if preco_live >= t["tp3"]: fechar, resultado, p_saida = True, "WIN", t["tp3"]
            elif preco_live <= t["sl"] and t["resultado"] == "WAIT": fechar, resultado, p_saida = True, "LOSS", t["sl"]
            elif preco_live <= t["entrada"] and t["resultado"] == "BREAKEVEN": fechar, resultado, p_saida = True, "ZERO", t["entrada"]
            elif preco_live >= t["tp1"] and t["resultado"] == "WAIT":
                atualizar_trade(t["id"], "BREAKEVEN", 0, None, 0)
                enviar_telegram(f"🛡️ <b>BREAKEVEN (TP1 Alcançado)</b>\nAtivo: <b>{t['ativo']}</b>\nO teu Stop Loss foi movido para a entrada. Risco nulo.")
        else:
            if preco_live <= t["tp3"]: fechar, resultado, p_saida = True, "WIN", t["tp3"]
            elif preco_live >= t["sl"] and t["resultado"] == "WAIT": fechar, resultado, p_saida = True, "LOSS", t["sl"]
            elif preco_live >= t["entrada"] and t["resultado"] == "BREAKEVEN": fechar, resultado, p_saida = True, "ZERO", t["entrada"]
            elif preco_live <= t["tp1"] and t["resultado"] == "WAIT":
                atualizar_trade(t["id"], "BREAKEVEN", 0, None, 0)
                enviar_telegram(f"🛡️ <b>BREAKEVEN (TP1 Alcançado)</b>\nAtivo: <b>{t['ativo']}</b>\nO teu Stop Loss foi movido para a entrada. Risco nulo.")

        if fechar:
            diff = abs(t["entrada"] - p_saida)
            pts = diff * 100 if "XAU" in ativo else diff * 100000 if "EUR" in ativo else diff
            if resultado == "LOSS": pts = -pts
            atualizar_trade(t["id"], resultado, 1, hoje, round(pts, 1))
            icone = "🎯 WIN (Take Profit 3)" if resultado == "WIN" else "❌ LOSS (Stop Loss)" if resultado == "LOSS" else "⚖️ ZERO (Saiu no 0 a 0)"
            enviar_telegram(f"{icone}\nAtivo: <b>{t['ativo']}</b>\nPontos capturados: {round(pts, 1)}")

def on_ws_message(ws, message):
    try:
        dados = json.loads(message)
        if dados.get('event') == 'price':
            ativo = dados['symbol'].replace('/USD', '')
            preco_live = float(dados['price'])
            gerir_trade_tick(ativo, preco_live)
    except Exception as e:
        logger.error(f"Erro WS Parse: {e}")

def on_ws_open(ws):
    sub_msg = {"action": "subscribe", "params": {"symbols": "XAU/USD,EUR/USD,BTC/USD"}}
    ws.send(json.dumps(sub_msg))
    logger.info("📡 WebSocket Institucional conectado! A ler Ticks em tempo real.")

def iniciar_stream_precos():
    URL_WS = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={TWELVEDATA_KEY}"
    while True: # Loop de auto-reconnect
        try:
            ws = websocket.WebSocketApp(URL_WS, on_open=on_ws_open, on_message=on_ws_message)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            logger.warning(f"WebSocket desconectado. A tentar novamente em 5s... Erro: {e}")
        time.sleep(5)

# ==========================================
# 🤖 5. IA MACRO E BOLETIM MATINAL
# ==========================================
def emitir_boletim_macro():
    if not bot: return
    try:
        prompt = (
            "Atue como um Analista Quantitativo Chefe de Wall Street. Escreva um boletim matinal de 4 tópicos curtos e diretos "
            "para traders de Ouro (XAU/USD) e Forex (EUR/USD). Foque-se na força do Dólar hoje, sentimento de risco global "
            "e eventos macro. Use linguagem técnica e emojis."
        )
        res = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.3)
        analise = res.choices[0].message.content.strip()
        
        msg = (
            "🌅 <b>BOLETIM MACRO INSTITUCIONAL | ABERTURA LONDRES</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{analise}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Gestão de Risco é inegociável. Boa sessão a todos.</i> 💼"
        )
        enviar_telegram(msg)
        logger.info("Boletim matinal enviado com sucesso.")
    except Exception as e:
        logger.error(f"Falha ao emitir boletim IA: {e}")

# ==========================================
# 📱 6. TELEGRAM BOT (UI Premium)
# ==========================================
def enviar_telegram(mensagem, reply_markup=None):
    if not bot or not TELEGRAM_CHAT_ID: return
    try: bot.send_message(TELEGRAM_CHAT_ID, text=mensagem, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e: logger.error(f"Erro Telegram: {e}")

def criar_teclado_padrao():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📂 Abertas", callback_data="cmd_abertas"), InlineKeyboardButton("📊 Placar", callback_data="cmd_placar"))
    markup.row(InlineKeyboardButton("⚙️ Status do Fundo", callback_data="cmd_status"), InlineKeyboardButton("🔍 Forçar Varredura", callback_data="cmd_varrer"))
    return markup

if bot:
    @bot.message_handler(commands=['start', 'menu'])
    def cmd_start(message):
        bot.reply_to(message, "🎩 <b>BEM-VINDO AO MOTOR INSTITUCIONAL VIP</b>\nSelecione uma opção:", parse_mode="HTML", reply_markup=criar_teclado_padrao())

    @bot.callback_query_handler(func=lambda call: True)
    def handle_query(call):
        agora = datetime.now(FUSO_LISBOA)
        if call.data == "cmd_status":
            zona = "🟢 Killzone Institucional" if 8 <= agora.hour <= 17 else "🔴 Asiática (Baixa Liquidez)"
            msg = f"<b>⚙️ STATUS DO MOTOR QUANT</b>\n\n🕒 Hora: {agora.strftime('%H:%M')} (PT)\n📊 Sessão: {zona}\n📡 WebSockets: 🟢 ONLINE"
            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=criar_teclado_padrao())
        elif call.data == "cmd_abertas":
            abertas = obter_trades_abertas()
            msg = "💤 <b>Nenhuma operação aberta.</b>" if not abertas else "📂 <b>OPERAÇÕES EM ANDAMENTO:</b>\n\n" + "".join([f"{'⏳' if t['resultado']=='WAIT' else '🛡️'} <b>{t['ativo']}</b> ({t['direcao']})\n └ Entrada: {t['entrada']} | Fase: {t['resultado']}\n\n" for t in abertas])
            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=criar_teclado_padrao())
        elif call.data == "cmd_varrer":
            bot.answer_callback_query(call.id, "A iniciar varredura...")
            verificar_setups()

# ==========================================
# ⏰ 7. SCHEDULER & FLASK INIT
# ==========================================
scheduler = BackgroundScheduler(timezone=FUSO_LISBOA)
scheduler.add_job(verificar_setups, 'cron', minute='0,15,30,45') # Entradas ao fecho da vela M15
scheduler.add_job(emitir_boletim_macro, 'cron', day_of_week='mon-fri', hour=8, minute=0) # IA às 08h
scheduler.start()

@app.route('/ping', methods=['GET'])
def ping(): return jsonify({"status": "Servidor Quantitativo Online"})

if __name__ == '__main__':
    logger.info("A arrancar Motor Institucional...")
    
    # 1. Inicia o Bot do Telegram
    if bot: threading.Thread(target=bot.infinity_polling, daemon=True).start()
    
    # 2. Inicia o WebSocket para gestão instantânea de trades
    threading.Thread(target=iniciar_stream_precos, daemon=True).start()
    
    # 3. Inicia o Servidor Web (mantém o bot vivo em cloud hostings)
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
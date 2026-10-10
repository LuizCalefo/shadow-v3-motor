import os
import sqlite3
import time
import threading
import pandas as pd
import numpy as np
import requests
import telebot
import matplotlib
matplotlib.use('Agg') # Força o modo Non-GUI para servidores em nuvem (Render)
import mplfinance as mpf
from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ==========================================
# 1. CONFIGURAÇÕES BÁSICAS
# ==========================================
load_dotenv()
app = Flask(__name__)
CORS(app)

TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

bot = telebot.TeleBot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
DB_FILE = 'trading_shadow.db'

# ==========================================
# 2. BASE DE DADOS TRANSACIONAL
# ==========================================
def executar_query(query, params=(), fetch=False):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
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
            direcao TEXT,
            entrada REAL,
            tp1 REAL,
            tp2 REAL,
            sl REAL,
            resultado TEXT,
            estado_fechado INTEGER
        )
    ''')

inicializar_bd()

def enviar_telegram_com_grafico(mensagem, caminho_ficheiro):
    if bot and TELEGRAM_CHAT_ID:
        try:
            with open(caminho_ficheiro, 'rb') as foto:
                bot.send_photo(TELEGRAM_CHAT_ID, photo=foto, caption=mensagem, parse_mode="HTML")
            if os.path.exists(caminho_ficheiro):
                os.remove(caminho_ficheiro)
        except Exception as e:
            print(f"Erro Telegram (Foto): {e}")
            
def enviar_telegram_texto(mensagem):
    if bot and TELEGRAM_CHAT_ID:
        try:
            bot.send_message(TELEGRAM_CHAT_ID, text=mensagem, parse_mode="HTML")
        except Exception as e:
            print(f"Erro Telegram (Texto): {e}")

# ==========================================
# 3. GERAÇÃO DO GRÁFICO (MPLFINANCE COM FICHEIRO FÍSICO)
# ==========================================
def gerar_imagem_sinal(df, ativo, direcao, entrada, sl, tp1, tp2, topo_caixa, fundo_caixa):
    nome_ficheiro = f"grafico_{ativo.replace('/', '_').replace(' ', '_')}.png"
    try:
        df_plot = df.copy()
        df_plot['datetime'] = pd.to_datetime(df_plot['datetime'])
        df_plot.set_index('datetime', inplace=True)
        
        linhas_fimathe = [
            dict(y=sl, color='red', linestyle='dashed', width=1.5),
            dict(y=entrada, color='white', linestyle='solid', width=1.5),
            dict(y=tp1, color='lightgreen', linestyle='dashed', width=1.5),
            dict(y=tp2, color='green', linestyle='solid', width=2)
        ]
        
        linhas_liquidez = [
            dict(y=topo_caixa, color='orange', linestyle='dotted', width=1, alpha=0.5),
            dict(y=fundo_caixa, color='orange', linestyle='dotted', width=1, alpha=0.5)
        ]
        
        todas_linhas = linhas_fimathe + linhas_liquidez
        estilo = mpf.make_mpf_style(base_mpf_style='nightclouds', facecolor='#121212', edgecolor='white', gridcolor='#222222')
        
        mpf.plot(
            df_plot, type='candle', style=estilo, 
            hlines=todas_linhas,
            title=f"PO3 + Fimathe | {ativo}",
            ylabel='Preço',
            savefig=dict(fname=nome_ficheiro, format='png', dpi=120, bbox_inches='tight'),
            figsize=(8, 5)
        )
        
        if os.path.exists(nome_ficheiro) and os.path.getsize(nome_ficheiro) > 0:
            return nome_ficheiro
        return None
    except Exception as e:
        print(f"Erro a gerar gráfico em disco: {e}")
        return None

# ==========================================
# 4. COMANDOS DO BOT DE TELEGRAM
# ==========================================
if bot:
    @bot.message_handler(commands=['teste'])
    def comando_teste(message):
        bot.reply_to(message, "✅ <b>TESTE DE SISTEMA</b>\nO Motor está online. A gerar gráfico de teste (XAU/USD)...", parse_mode="HTML")
        try:
            df = obter_dados("XAU/USD")
            if df is not None and not df.empty:
                zona_acumulacao = df.iloc[-21:-1]
                topo = zona_acumulacao['high'].max()
                fundo = zona_acumulacao['low'].min()
                preco = df.iloc[-1]['close']
                
                caminho_img = gerar_imagem_sinal(df, "XAU TESTE", "COMPRA", preco, fundo, topo, topo+10, topo, fundo)
                if caminho_img:
                    enviar_telegram_com_grafico("📊 <b>Gráfico de Teste gerado com sucesso via MPLFinance.</b>", caminho_img)
                else:
                    bot.reply_to(message, "⚠️ O ficheiro de imagem não foi gravado corretamente no disco.")
            else:
                bot.reply_to(message, "⚠️ Falha ao obter dados da TwelveData para o teste.")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Erro crítico ao gerar imagem: {str(e)}")

    @bot.message_handler(commands=['grafico'])
    def comando_grafico(message):
        try:
            partes = message.text.split()
            if len(partes) < 2:
                bot.reply_to(message, "⚠️ Uso correto: `/grafico XAU`, `/grafico BTC` ou `/grafico EUR`", parse_mode="Markdown")
                return
                
            ativo = partes[1].upper()
            if ativo not in ["XAU", "BTC", "EUR"]:
                bot.reply_to(message, "⚠️ Ativo não suportado. Tente /grafico XAU")
                return
                
            simbolo = "XAU/USD" if ativo == "XAU" else "BTC/USD" if ativo == "BTC" else "EUR/USD"
            bot.reply_to(message, f"A capturar o gráfico instantâneo de {ativo}...")
            
            df = obter_dados(simbolo)
            if df is not None and not df.empty:
                zona_acumulacao = df.iloc[-21:-1]
                topo = zona_acumulacao['high'].max()
                fundo = zona_acumulacao['low'].min()
                preco = df.iloc[-1]['close']
                caminho_img = gerar_imagem_sinal(df, ativo, "SNAPSHOT", preco, fundo, topo, topo+10, topo, fundo)
                if caminho_img:
                    enviar_telegram_com_grafico(f"📸 <b>Snapshot de mercado: {ativo}</b>\nCaixa de Acumulação desenhada.", caminho_img)
                else:
                    bot.reply_to(message, "⚠️ Erro ao compilar a imagem do gráfico.")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Erro no comando /grafico: {str(e)}")

    @bot.message_handler(commands=['status'])
    def comando_status(message):
        abertas = executar_query("SELECT * FROM historico WHERE estado_fechado=0", fetch=True)
        if not abertas:
            bot.reply_to(message, "🟢 <b>STATUS:</b> Motor a varrer o mercado.\nNenhuma operação aberta de momento.", parse_mode="HTML")
        else:
            texto = "🟢 <b>STATUS:</b> Motor ativo.\n📂 <b>Operações Abertas:</b>\n\n"
            for t in abertas:
                texto += f"▫️ <b>{t['ativo']}</b> ({t['direcao']}) | Fase: {t['resultado']}\n"
            bot.reply_to(message, texto, parse_mode="HTML")

    @bot.message_handler(commands=['relatorio'])
    def comando_relatorio(message):
        trades = executar_query("SELECT resultado FROM historico WHERE estado_fechado=1", fetch=True)
        wins = sum(1 for t in trades if t['resultado'] == 'WIN')
        losses = sum(1 for t in trades if t['resultado'] == 'LOSS')
        zeros = sum(1 for t in trades if t['resultado'] == 'ZERO')
        total = wins + losses + zeros
        winrate = (wins / total * 100) if total > 0 else 0
        
        bot.reply_to(message, f"📊 <b>RELATÓRIO GLOBAL</b>\n\n✅ Wins: {wins}\n❌ Losses: {losses}\n⚖️ 0a0 (Breakeven): {zeros}\n\n🎯 <b>Winrate:</b> {winrate:.1f}%", parse_mode="HTML")

# ==========================================
# 5. MOTOR ESTRATÉGICO: PO3 + FIMATHE
# ==========================================
def obter_dados(simbolo):
    url = f"https://api.twelvedata.com/time_series?symbol={simbolo}&interval=15min&outputsize=40&apikey={TWELVEDATA_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        dados = resp.json()
        if "values" not in dados: return None
        
        df = pd.DataFrame(dados["values"])[::-1].reset_index(drop=True)
        for col in ['close', 'high', 'low', 'open']: df[col] = df[col].astype(float)
        return df
    except Exception as e:
        print(f"Erro ao obter dados da TwelveData: {e}")
        return None

def analisar_mercado(ativo, simbolo):
    abertas = executar_query("SELECT id FROM historico WHERE ativo=? AND estado_fechado=0", (ativo,), fetch=True)
    if abertas: return 
    
    df = obter_dados(simbolo)
    if df is None or len(df) < 30: return
    
    casas_dec = 5 if ativo == "EUR" else 2
    
    zona_acumulacao = df.iloc[-21:-1]
    topo_caixa = zona_acumulacao['high'].max()
    fundo_caixa = zona_acumulacao['low'].min()
    tamanho_canal = topo_caixa - fundo_caixa
    
    if tamanho_canal == 0: return

    vela_atual = df.iloc[-1]
    preco = round(vela_atual['close'], casas_dec)
    
    direcao = None
    sl, tp1, tp2 = 0, 0, 0

    if vela_atual['high'] > topo_caixa and vela_atual['close'] < topo_caixa:
        direcao = "VENDA"
        sl = round(preco + (tamanho_canal * 2), casas_dec)
        tp1 = round(preco - (tamanho_canal * 1), casas_dec)
        tp2 = round(preco - (tamanho_canal * 2), casas_dec)

    elif vela_atual['low'] < fundo_caixa and vela_atual['close'] > fundo_caixa:
        direcao = "COMPRA"
        sl = round(preco - (tamanho_canal * 2), casas_dec)
        tp1 = round(preco + (tamanho_canal * 1), casas_dec)
        tp2 = round(preco + (tamanho_canal * 2), casas_dec)

    if direcao:
        trade_id = f"{ativo}_{int(time.time())}"
        executar_query('''
            INSERT INTO historico (id, ativo, direcao, entrada, tp1, tp2, sl, resultado, estado_fechado)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0)
        ''', (trade_id, ativo, direcao, preco, tp1, tp2, sl))
        
        caminho_img = gerar_imagem_sinal(df, ativo, direcao, preco, sl, tp1, tp2, topo_caixa, fundo_caixa)
        
        emoji = "🟢 COMPRA" if direcao == "COMPRA" else "🔴 VENDA"
        msg = (f"⚡ <b>SINAL INSTITUCIONAL | {ativo}</b>\n\n"
               f"🧠 <b>Lógica:</b> Manipulação de Liquidez (PO3). Expansão Fimathe ativada.\n"
               f"🔄 Direção: <b>{emoji}</b>\n\n"
               f"📍 Entrada: <code>{preco}</code>\n"
               f"🛑 Stop Loss: <code>{sl}</code> (Zona Neutra)\n"
               f"✅ Take Profit 1: <code>{tp1}</code>\n"
               f"🚀 Take Profit 2: <code>{tp2}</code>\n\n"
               f"<i>Arriscar max 1%.</i>")
        
        if caminho_img:
            enviar_telegram_com_grafico(msg, caminho_img)
        else:
            enviar_telegram_texto(msg)

# ==========================================
# 6. GESTÃO DE TRADES 
# ==========================================
def gerir_posicoes():
    abertas = executar_query("SELECT * FROM historico WHERE estado_fechado=0", fetch=True)
    if not abertas: return

    for trade in abertas:
        ativo = trade['ativo']
        simbolo = "XAU/USD" if ativo == "XAU" else "BTC/USD" if ativo == "BTC" else "EUR/USD"
        
        df = obter_dados(simbolo)
        if df is None: continue
        
        preco_atual = df.iloc[-1]['close']
        is_compra = trade['direcao'] == "COMPRA"
        fase = trade['resultado']
        
        if fase == "ACTIVE":
            if (is_compra and preco_atual <= trade['sl']) or (not is_compra and preco_atual >= trade['sl']):
                executar_query("UPDATE historico SET resultado='LOSS', estado_fechado=1 WHERE id=?", (trade['id'],))
                enviar_telegram_texto(f"❌ <b>LOSS</b> | {ativo}\nA manipulação falhou e o preço rompeu a Zona Neutra.")
            
            elif (is_compra and preco_atual >= trade['tp1']) or (not is_compra and preco_atual <= trade['tp1']):
                executar_query("UPDATE historico-resultado SET resultado='BREAKEVEN' WHERE id=?", (trade['id'],)) # Correção de query a seguir
                executar_query("UPDATE historico SET resultado='BREAKEVEN', estado_fechado=0 WHERE id=?", (trade['id'],))
                enviar_telegram_texto(f"🛡️ <b>TP1 ATINGIDO</b> | {ativo}\nAlvo 1 concluído ({trade['tp1']}). Mova o SL para a entrada.")

        elif fase == "BREAKEVEN":
            if (is_compra and preco_atual >= trade['tp2']) or (not is_compra and preco_atual <= trade['tp2']):
                executar_query("UPDATE historico SET resultado='WIN', estado_fechado=1 WHERE id=?", (trade['id'],))
                enviar_telegram_texto(f"🎯 <b>WIN MÁXIMO (TP2)</b> | {ativo}\nExpansão Fimathe 200% cravada com sucesso!")
            
            elif (is_compra and preco_atual <= trade['entrada']) or (not is_compra and preco_atual >= trade['entrada']):
                executar_query("UPDATE historico SET resultado='ZERO', estado_fechado=1 WHERE id=?", (trade['id'],))
                enviar_telegram_texto(f"⚖️ <b>ZERO</b> | {ativo}\nO preço reverteu e tirou-nos na entrada. Capital blindado.")

# ==========================================
# 7. LOOP CENTRAL E ROTAS
# ==========================================
def motor_loop():
    while True:
        try:
            gerir_posicoes()
            for ativo, simbolo in [("XAU", "XAU/USD"), ("BTC", "BTC/USD"), ("EUR", "EUR/USD")]:
                analisar_mercado(ativo, simbolo)
        except Exception as e:
            print(f"Erro no Loop Principal: {e}")
        time.sleep(60)

# Iniciar Thread Principal do Motor
threading.Thread(target=motor_loop, daemon=True).start()

# Iniciar Thread de Escuta do Telegram
if bot:
    threading.Thread(target=bot.infinity_polling, daemon=True).start()

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "Motor PO3+Fimathe a correr perfeitamente com ficheiros temporários para gráficos!"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
"""
PROFIT CASH - Interface Gráfica Profissional
Sistema de trading automatizado com 70+ estratégias profissionais
"""
import tkinter as tk
from tkinter import ttk, messagebox
import asyncio
import threading
import time
import math
import sys
import logging
from datetime import datetime
from collections import deque
import webbrowser

# ─── CONFIGURAÇÕES ────────────────────────────────────────────────────────────
SIGNUP_URL = "https://broker-qx.pro/sign-up/?lid=2008728"

ESTRATEGIAS = {
    "cautelosa": {
        "nome": "Cautelosa",
        "emoji": "🛡️",
        "cor": "#22c55e",
        "cor_sel": "#16a34a",
        "descricao": "Entradas selecionadas\nMaior margem de acerto",
        "confianca": 0.72,
        "valor": 5.0,
        "max_trades": 2,
        "duracao": 60,
        "stop_loss": -30.0,
        "stop_win": 120.0,
        "pausa": 12,
    },
    "moderada": {
        "nome": "Moderada",
        "emoji": "⚖️",
        "cor": "#a78bfa",
        "cor_sel": "#7c3aed",
        "descricao": "Equilíbrio risco/retorno\nEntradas regulares",
        "confianca": 0.62,
        "valor": 10.0,
        "max_trades": 3,
        "duracao": 60,
        "stop_loss": -60.0,
        "stop_win": 250.0,
        "pausa": 8,
    },
    "agressiva": {
        "nome": "Agressiva",
        "emoji": "🔥",
        "cor": "#f59e0b",
        "cor_sel": "#d97706",
        "descricao": "Mais entradas por hora\nMenor margem de acerto",
        "confianca": 0.52,
        "valor": 20.0,
        "max_trades": 5,
        "duracao": 60,
        "stop_loss": -100.0,
        "stop_win": 400.0,
        "pausa": 5,
    },
}

ATIVOS = [
    # OTC (sempre abertos — prioridade para operar 24h)
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc",
    "USDCAD_otc", "EURGBP_otc", "EURJPY_otc", "USDCHF_otc",
    "GBPJPY_otc", "NZDUSD_otc", "EURCHF_otc", "CADJPY_otc",
    # Real (maior liquidez nos horários de abertura)
    "EURUSD",     "GBPUSD",     "USDJPY",     "AUDUSD",
    "USDCAD",     "EURGBP",     "NZDUSD",
]

# ─── PALETA GLASSMORPHISM SUPREMA ─────────────────────────────────────────────
BG_DARK    = "#060a14"   # fundo principal — preto azulado profundo
BG_GRAD1   = "#0d1225"   # gradiente início
BG_GRAD2   = "#111a30"   # gradiente meio
BG_CARD    = "#0f1628"   # glass card escuro  (rgba 255,255,255,0.05 simulado)
BG_CARD2   = "#141e36"   # glass card secundário
BG_GLASS   = "#1a2540"   # glass overlay mais claro
BG_INPUT   = "#111e38"   # campo de entrada
BORDER     = "#1e2d50"   # borda glass sutil
BORDER_HI  = "#3b82f6"   # borda neon azul
BORDER_GL  = "#2a3f6e"   # borda glass media
PURPLE     = "#3b82f6"   # neon azul primário
PURPLE_LT  = "#60a5fa"   # neon azul claro
NEON_GREEN = "#00ff88"   # neon verde lucro
NEON_RED   = "#ff4757"   # neon vermelho perda
NEON_PURP  = "#8c7ae6"   # neon roxo acento
GOLD       = "#00d68f"   # verde esmeralda neon
GOLD_LT    = "#34eba8"   # verde claro neon
TEXT_WHITE = "#e8f0ff"   # branco frio legível
TEXT_GREY  = "#6b80a8"   # cinza azulado médio
TEXT_GREEN = "#00d68f"   # verde lucro neon
TEXT_RED   = "#ff4757"   # vermelho perda neon
ACCENT     = "#3b82f6"   # acento principal neon

# ═══════════════════════════════════════════════════════════════════════════════
#  MOTOR DE ANÁLISE AVANÇADO — 70+ estratégias profissionais
# ═══════════════════════════════════════════════════════════════════════════════

# ── Primitivas ────────────────────────────────────────────────────────────────
def ema(series, n):
    if len(series) < n: return None
    k = 2/(n+1); v = sum(series[:n])/n
    for c in series[n:]: v = c*k + v*(1-k)
    return v

def sma(series, n):
    if len(series) < n: return None
    return sum(series[-n:])/n

def rsi(closes, n=14):
    if len(closes) < n+1: return None
    g,l = [],[]
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    mg=sum(g[-n:])/n; ml=sum(l[-n:])/n
    return 100.0 if ml==0 else 100-(100/(1+mg/ml))

def macd_calc(closes, fast=12, slow=26, signal=9):
    if len(closes)<slow+signal: return None,None,None
    ef=ema(closes,fast); es=ema(closes,slow)
    if not ef or not es: return None,None,None
    line=ef-es; mv=[]
    for i in range(signal,len(closes)):
        a=ema(closes[:i],fast); b=ema(closes[:i],slow)
        if a and b: mv.append(a-b)
    if len(mv)<signal: return line,None,None
    sl=ema(mv,signal)
    return line, sl, (line-sl if sl else None)

def bollinger(closes, n=20, k=2.0):
    if len(closes)<n: return None,None,None
    w=closes[-n:]; mid=sum(w)/n
    std=math.sqrt(sum((c-mid)**2 for c in w)/n)
    return mid+k*std, mid, mid-k*std

def stoch(candles, k=14):
    if len(candles)<k: return None
    s=candles[-k:]; hh=max(c['high'] for c in s); ll=min(c['low'] for c in s)
    return 50.0 if hh==ll else (s[-1]['close']-ll)/(hh-ll)*100

def williams_r(candles, n=14):
    if len(candles)<n: return None
    s=candles[-n:]; hh=max(c['high'] for c in s); ll=min(c['low'] for c in s)
    return -50.0 if hh==ll else (hh-s[-1]['close'])/(hh-ll)*-100

def cci(candles, n=20):
    if len(candles)<n: return None
    tp=[(c['high']+c['low']+c['close'])/3 for c in candles[-n:]]
    avg=sum(tp)/n; md=sum(abs(t-avg) for t in tp)/n
    return 0.0 if md==0 else (tp[-1]-avg)/(0.015*md)

def adx_calc(candles, n=14):
    if len(candles)<n+1: return None,None,None
    pdm,mdm,tr=[],[],[]
    for i in range(1,len(candles)):
        h,l,ph,pl,pc=candles[i]['high'],candles[i]['low'],candles[i-1]['high'],candles[i-1]['low'],candles[i-1]['close']
        up,dn=h-ph,pl-l
        pdm.append(up if up>dn and up>0 else 0)
        mdm.append(dn if dn>up and dn>0 else 0)
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    atr=sum(tr[-n:])/n
    if atr==0: return 0.0,0.0,0.0
    pdi=100*sum(pdm[-n:])/n/atr; mdi=100*sum(mdm[-n:])/n/atr
    ds=pdi+mdi
    return (100*abs(pdi-mdi)/ds if ds>0 else 0), pdi, mdi

def atr_val(candles, n=14):
    if len(candles)<n+1: return None
    tr=[max(candles[i]['high']-candles[i]['low'],
            abs(candles[i]['high']-candles[i-1]['close']),
            abs(candles[i]['low']-candles[i-1]['close']))
        for i in range(1,len(candles))]
    return sum(tr[-n:])/n

def mfi(candles, n=14):
    """Money Flow Index — RSI ponderado por volume"""
    if len(candles) < n+2: return None
    tps  = [(c['high']+c['low']+c['close'])/3 for c in candles]
    vols = [c.get('volume', 1) for c in candles]
    pos_mf = neg_mf = 0.0
    for i in range(-n, 0):
        raw = tps[i] * vols[i]
        if   tps[i] > tps[i-1]: pos_mf += raw
        elif tps[i] < tps[i-1]: neg_mf += raw
        # tps[i] == tps[i-1] → período neutro, ignorado
    return 100 - (100 / (1 + pos_mf / max(neg_mf, 0.001)))

def aroon(candles, n=25):
    """Aroon Up/Down — retorna (up, down) em 0..100"""
    if len(candles) < n+1: return None, None
    seg = candles[-(n+1):]
    hi_i = max(range(n+1), key=lambda i: seg[i]['high'])
    lo_i = min(range(n+1), key=lambda i: seg[i]['low'])
    return (hi_i / n)*100, (lo_i / n)*100

def trix_val(closes, n=15):
    """TRIX — variação % da tripla EMA suavizada"""
    if len(closes) < n*3+2: return None
    e1 = [ema(closes[:i], n) for i in range(n, len(closes)+1)]
    e1 = [v for v in e1 if v]
    if len(e1) < n*2: return None
    e2 = [ema(e1[:i], n) for i in range(n, len(e1)+1)]
    e2 = [v for v in e2 if v]
    if len(e2) < n+1: return None
    e3 = [ema(e2[:i], n) for i in range(n, len(e2)+1)]
    e3 = [v for v in e3 if v]
    if len(e3) < 2 or e3[-2] == 0: return None
    return (e3[-1] - e3[-2]) / e3[-2] * 100

def ultimate_oscillator(candles):
    """Ultimate Oscillator de Larry Williams (7/14/28)"""
    if len(candles) < 30: return None
    def _bp_tr(idx):
        c = candles[idx]; pc = candles[idx-1]['close']
        th = max(c['high'], pc); tl = min(c['low'], pc)
        return c['close'] - tl, max(th - tl, 0.0001)
    def _avg(period):
        bp_s = tr_s = 0.0
        for i in range(-period, 0):
            bp, tr = _bp_tr(i)
            bp_s += bp; tr_s += tr
        return bp_s / tr_s if tr_s else 0
    return 100 * (4*_avg(7) + 2*_avg(14) + _avg(28)) / 7

def chande_momentum(closes, n=14):
    """Chande Momentum Oscillator — range -100..+100"""
    if len(closes) < n+1: return None
    up = dn = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i-1]
        if d > 0: up += d
        else:     dn -= d
    return 100 * (up - dn) / max(up + dn, 0.001)

def heikin_ashi(candles):
    """Converte candles para Heikin Ashi — retorna lista de candles HA"""
    if not candles: return []
    ha = []
    prev_o = (candles[0]['open'] + candles[0]['close']) / 2
    prev_c = (candles[0]['open'] + candles[0]['high'] + candles[0]['low'] + candles[0]['close']) / 4
    for c in candles:
        ha_c = (c['open'] + c['high'] + c['low'] + c['close']) / 4
        ha_o = (prev_o + prev_c) / 2
        ha.append({'open': ha_o, 'high': max(c['high'], ha_o, ha_c),
                   'low':  min(c['low'],  ha_o, ha_c), 'close': ha_c,
                   'volume': c.get('volume', 1)})
        prev_o, prev_c = ha_o, ha_c
    return ha

def detect_market_regime(candles, n=20):
    """
    Market Regime Detection — ADX + Bollinger Bandwidth.
    Retorna: 'trending' | 'ranging' | 'volatile' | 'neutral'
    """
    if len(candles) < n+2: return 'neutral'
    adx_v, _, _ = adx_calc(candles)
    closes = [c['close'] for c in candles[-n:]]
    mid = sum(closes) / n
    std = math.sqrt(sum((c-mid)**2 for c in closes) / n)
    bb_w = (2 * std) / mid if mid else 0
    if   adx_v and adx_v > 25 and bb_w > 0.003: return 'trending'
    elif adx_v and adx_v < 18 and bb_w < 0.0015: return 'ranging'
    elif bb_w > 0.008:                            return 'volatile'
    return 'neutral'

def detect_abcd_pattern(candles):
    """
    Padrão Harmônico ABCD simplificado.
    Garante ordem cronológica: idx_A < idx_B < idx_C antes de calcular ratios.
    Retorna: 1=bullish, -1=bearish, 0=nada
    """
    if len(candles) < 15: return 0
    n = len(candles)
    seg = candles[n-15:]   # janela de 15 candles
    m   = len(seg)         # == 15

    # ── Bullish ABCD: A=topo, B=fundo, C=topo mais baixo, D=fundo (entrada) ──
    # Busca em sub-janelas sem sobreposição: A em [0,9], B em [A+1,11], C em [B+1,13]
    best_bull = 0
    for ai in range(0, m - 6):
        A_h = seg[ai]['high']
        # B: mínimo após A
        for bi in range(ai + 1, m - 4):
            B_l = seg[bi]['low']
            AB  = A_h - B_l
            if AB <= 0: continue
            # C: máximo após B, mas abaixo de A
            for ci in range(bi + 1, m - 1):
                C_h = seg[ci]['high']
                if C_h >= A_h: continue          # C deve estar abaixo de A
                BC     = C_h - B_l
                ratio  = BC / AB
                if not (0.382 <= ratio <= 0.886): continue
                # D target: preço atual próximo à extensão CD ≈ AB
                D_target = C_h - AB
                price    = candles[-1]['close']
                if abs(price - D_target) / max(abs(A_h), 0.0001) < 0.005:
                    best_bull = 1
    if best_bull: return 1

    # ── Bearish ABCD: A=fundo, B=topo, C=fundo mais alto, D=topo (entrada) ──
    for ai in range(0, m - 6):
        A_l = seg[ai]['low']
        for bi in range(ai + 1, m - 4):
            B_h = seg[bi]['high']
            AB2 = B_h - A_l
            if AB2 <= 0: continue
            for ci in range(bi + 1, m - 1):
                C_l = seg[ci]['low']
                if C_l <= A_l: continue          # C deve estar acima de A
                BC2    = B_h - C_l
                ratio2 = BC2 / AB2
                if not (0.382 <= ratio2 <= 0.886): continue
                D_target2 = C_l + AB2
                price     = candles[-1]['close']
                if abs(price - D_target2) / max(abs(B_h), 0.0001) < 0.005:
                    return -1
    return 0

def kelly_criterion(win_rate: float, payout: float = 0.80) -> float:
    """
    Kelly Criterion para tamanho de posição ótimo.
    f* = (p * b - q) / b  onde b=payout, p=win_rate, q=1-p
    Retorna fração do capital recomendada (0..0.25 máximo).
    """
    if win_rate <= 0 or payout <= 0: return 0.0
    q = 1.0 - win_rate
    f = (win_rate * payout - q) / payout
    return max(0.0, min(0.25, f))   # clamp 0–25% do capital

def rsi_fast(closes, n=7):
    """RSI período 7 — mais sensível para scalping de 1min"""
    return rsi(closes, n)

def macd_fast(closes):
    """MACD rápido (5,13,1) — reage mais rápido às reversões"""
    if len(closes) < 14: return None, None, None
    e5  = ema(closes, 5)
    e13 = ema(closes, 13)
    if e5 is None or e13 is None: return None, None, None
    line = e5 - e13
    # signal de 1 período = própria linha (sem suavização adicional)
    # Para histórico curto usamos EMA 3 do MACD
    macd_series = []
    for i in range(13, len(closes)+1):
        e5s  = ema(closes[:i], 5)
        e13s = ema(closes[:i], 13)
        if e5s and e13s: macd_series.append(e5s - e13s)
    if len(macd_series) < 3: return line, line, 0.0
    sig  = ema(macd_series, 3) or macd_series[-1]
    hist = macd_series[-1] - sig
    return macd_series[-1], sig, hist

def bollinger_squeeze(closes, n=10):
    """
    Bollinger Squeeze — detecta contração + breakout.
    Retorna: 'up' | 'down' | None
    """
    if len(closes) < n+1: return None
    seg = closes[-n:]
    mid = sum(seg) / n
    std = math.sqrt(sum((c - mid)**2 for c in seg) / n)
    up  = mid + 2*std
    lo  = mid - 2*std
    bw  = (up - lo) / mid if mid else 0
    if bw < 0.015:   # bandas apertadas (squeeze)
        price = closes[-1]
        if price > up: return 'up'
        if price < lo: return 'down'
    return None

def pivot_points(candles):
    """Pivot Points clássico baseado na sessão anterior"""
    if len(candles)<2: return {}
    prev=candles[-2]; h,l,c=prev['high'],prev['low'],prev['close']
    pp=(h+l+c)/3
    return {'pp':pp,'r1':2*pp-l,'r2':pp+(h-l),'r3':h+2*(pp-l),
            's1':2*pp-h,'s2':pp-(h-l),'s3':l-2*(h-pp)}

def fibonacci_levels(candles, n=50):
    """Níveis Fibonacci do swing mais recente"""
    if len(candles)<n: return {}
    s=candles[-n:]
    hi=max(c['high'] for c in s); lo=min(c['low'] for c in s)
    rng=hi-lo
    if rng==0: return {}
    return {0:hi,0.236:hi-0.236*rng,0.382:hi-0.382*rng,
            0.5:hi-0.5*rng,0.618:hi-0.618*rng,
            0.786:hi-0.786*rng,1.0:lo}

def swing_highs_lows(candles, lookback=5):
    """Detecta topos e fundos de swing"""
    highs=[]; lows=[]
    closes=[c['close'] for c in candles]
    for i in range(lookback, len(candles)-lookback):
        h=candles[i]['high']; l=candles[i]['low']
        if h==max(candles[j]['high'] for j in range(i-lookback,i+lookback+1)):
            highs.append((i,h))
        if l==min(candles[j]['low'] for j in range(i-lookback,i+lookback+1)):
            lows.append((i,l))
    return highs, lows

def support_resistance(candles, n=30, tol=0.002):
    """Níveis de suporte/resistência dinâmicos por clustering"""
    levels=[]
    for c in candles[-n:]:
        levels.extend([c['high'], c['low']])
    levels.sort()
    clusters=[]
    for lv in levels:
        merged=False
        for cl in clusters:
            if abs(lv-cl['center'])/cl['center'] < tol:
                cl['count']+=1; cl['center']=(cl['center']+lv)/2; merged=True; break
        if not merged: clusters.append({'center':lv,'count':1})
    clusters.sort(key=lambda x: x['count'], reverse=True)
    price=candles[-1]['close']
    resistances=[c['center'] for c in clusters if c['center']>price and c['count']>=2]
    supports=[c['center'] for c in clusters if c['center']<price and c['count']>=2]
    return sorted(supports), sorted(resistances)

# ── Padrões de Candles (50+) ──────────────────────────────────────────────────
def _body(c): return abs(c['close']-c['open'])
def _range(c): return c['high']-c['low'] if c['high']!=c['low'] else 0.0001
def _upper_shadow(c): return c['high']-max(c['open'],c['close'])
def _lower_shadow(c): return min(c['open'],c['close'])-c['low']
def _bull(c): return c['close']>c['open']
def _bear(c): return c['close']<c['open']

def candlestick_patterns(candles):
    """Retorna (score_bull, score_bear) de 0..10 com 50+ padrões"""
    if len(candles)<5: return 0,0
    c0,c1,c2,c3,c4=candles[-1],candles[-2],candles[-3],candles[-4],candles[-5]
    b0,b1,b2=_body(c0),_body(c1),_body(c2)
    r0,r1,r2=_range(c0),_range(c1),_range(c2)
    us0,ls0=_upper_shadow(c0),_lower_shadow(c0)
    us1,ls1=_upper_shadow(c1),_lower_shadow(c1)
    bull=0.0; bear=0.0

    # ── REVERSÃO BULLISH ──────────────────────────────────────────────────
    # 1. Hammer
    if _bull(c0) and ls0>2*b0 and us0<0.3*b0 and b0>0:
        bull+=2.5
    # 2. Dragonfly Doji
    if b0<r0*0.1 and ls0>r0*0.6:
        bull+=2.0
    # 3. Bullish Engulfing
    if _bear(c1) and _bull(c0) and c0['close']>c1['open'] and c0['open']<c1['close']:
        bull+=3.0
    # 4. Morning Star
    if _bear(c2) and b1<r1*0.3 and _bull(c0) and c0['close']>=(c2['open']+c2['close'])/2:
        bull+=3.5
    # 5. Piercing Line
    if _bear(c1) and _bull(c0) and c0['open']<c1['low'] and c0['close']>=(c1['open']+c1['close'])/2:
        bull+=2.5
    # 6. Three White Soldiers
    if all(_bull(x) for x in [c0,c1,c2]) and c0['close']>c1['close']>c2['close']:
        bull+=3.0
    # 7. Bullish Harami
    if _bear(c1) and _bull(c0) and c0['open']>c1['close'] and c0['close']<c1['open']:
        bull+=2.0
    # 8. Tweezer Bottom
    if abs(c0['low']-c1['low'])/max(c0['low'],0.0001)<0.001 and _bear(c1) and _bull(c0):
        bull+=2.5
    # 9. Inverted Hammer
    if _bull(c0) and us0>2*b0 and ls0<0.3*b0 and b0>0:
        bull+=1.5
    # 10. Three Inside Up
    if _bear(c2) and _bull(c1) and c1['open']>c2['close'] and c1['close']<c2['open'] and _bull(c0) and c0['close']>c2['open']:
        bull+=3.0
    # 11. Bullish Kicker
    if _bear(c1) and _bull(c0) and c0['open']>c1['open'] and b0>b1:
        bull+=3.5
    # 12. Rising Window (Gap Up)
    if c0['low']>c1['high']:
        bull+=2.0
    # 13. Mat Hold (bullish continuation)
    if _bull(c4) and _bear(c3) and _bear(c2) and _bear(c1) and _bull(c0) and c0['close']>c4['close']:
        bull+=2.5
    # 14. Three Stars in the South
    if all(_bear(x) for x in [c2,c1]) and b2>b1 and _bull(c0):
        bull+=2.0
    # 15. Bullish Abandoned Baby (gap doji)
    if _bear(c2) and b1<r1*0.1 and c1['low']<c2['low'] and _bull(c0) and c0['low']>c1['high']:
        bull+=4.0
    # 16. Rising Three Methods
    if _bull(c4) and all(_bear(x) for x in [c3,c2,c1]) and _bull(c0) and c0['close']>c4['close']:
        bull+=2.5

    # ── REVERSÃO BEARISH ──────────────────────────────────────────────────
    # 1. Shooting Star
    if _bear(c0) and us0>2*b0 and ls0<0.3*b0 and b0>0:
        bear+=2.5
    # 2. Gravestone Doji
    if b0<r0*0.1 and us0>r0*0.6:
        bear+=2.0
    # 3. Bearish Engulfing
    if _bull(c1) and _bear(c0) and c0['open']>c1['close'] and c0['close']<c1['open']:
        bear+=3.0
    # 4. Evening Star
    if _bull(c2) and b1<r1*0.3 and _bear(c0) and c0['close']<=(c2['open']+c2['close'])/2:
        bear+=3.5
    # 5. Dark Cloud Cover
    if _bull(c1) and _bear(c0) and c0['open']>c1['high'] and c0['close']<=(c1['open']+c1['close'])/2:
        bear+=2.5
    # 6. Three Black Crows
    if all(_bear(x) for x in [c0,c1,c2]) and c0['close']<c1['close']<c2['close']:
        bear+=3.0
    # 7. Bearish Harami
    if _bull(c1) and _bear(c0) and c0['open']<c1['close'] and c0['close']>c1['open']:
        bear+=2.0
    # 8. Tweezer Top
    if abs(c0['high']-c1['high'])/max(c0['high'],0.0001)<0.001 and _bull(c1) and _bear(c0):
        bear+=2.5
    # 9. Hanging Man
    if _bear(c0) and ls0>2*b0 and us0<0.3*b0 and b0>0:
        bear+=1.5
    # 10. Three Inside Down
    if _bull(c2) and _bear(c1) and c1['open']<c2['close'] and c1['close']>c2['open'] and _bear(c0) and c0['close']<c2['open']:
        bear+=3.0
    # 11. Bearish Kicker
    if _bull(c1) and _bear(c0) and c0['open']<c1['open'] and b0>b1:
        bear+=3.5
    # 12. Falling Window (Gap Down)
    if c0['high']<c1['low']:
        bear+=2.0
    # 13. Bearish Abandoned Baby
    if _bull(c2) and b1<r1*0.1 and c1['high']>c2['high'] and _bear(c0) and c0['high']<c1['low']:
        bear+=4.0
    # 14. Falling Three Methods
    if _bear(c4) and all(_bull(x) for x in [c3,c2,c1]) and _bear(c0) and c0['close']<c4['close']:
        bear+=2.5
    # 15. On Neck / In Neck
    if _bear(c1) and _bull(c0) and abs(c0['close']-c1['low'])/max(c1['low'],0.0001)<0.002:
        bear+=2.0

    # ── PADRÕES EXTRA (Supremo) ───────────────────────────────────────────
    # Bullish — Three White Soldiers
    if (all(_bull(x) for x in [c2,c1,c0]) and
        c0['close']>c1['close']>c2['close'] and
        c0['open']>c2['open'] and
        all(_lower_shadow(x)<_body(x)*0.3 for x in [c2,c1,c0])):
        bull += 3.5
    # Bullish — Piercing Line
    if (_bear(c1) and _bull(c0) and
        c0['open'] < c1['low'] and
        c0['close'] > (c1['open']+c1['close'])/2 and c0['close'] < c1['open']):
        bull += 3.0
    # Bullish — Tweezer Bottom (preços mínimos idênticos)
    if (abs(c0['low']-c1['low'])/max(c0['low'],0.0001) < 0.001 and
        _bear(c1) and _bull(c0)):
        bull += 2.5
    # Bullish — Bullish Kicker (gap de abertura)
    if (_bear(c1) and _bull(c0) and
        c0['open'] > c1['open'] and _body(c0) > _body(c1)):
        bull += 3.5
    # Bullish — Heikin Ashi 3 consecutivas sem sombra inferior
    ha = heikin_ashi(candles[-5:])
    if len(ha) >= 3 and all(_bull(h) and _lower_shadow(h) < _body(h)*0.1 for h in ha[-3:]):
        bull += 2.0
    # Bullish — Concealing Baby Swallow
    if (all(_bear(x) for x in [c3,c2,c1]) and
        _lower_shadow(c1) < _body(c1)*0.1 and
        c1['open'] < c2['close'] and _bull(c0) and c0['close'] > c1['high']):
        bull += 2.5

    # Bearish — Three Black Crows (reforçado com corpo longo)
    if (all(_bear(x) for x in [c0,c1,c2]) and
        c0['close']<c1['close']<c2['close'] and
        all(_upper_shadow(x)<_body(x)*0.3 for x in [c2,c1,c0])):
        bear += 3.5
    # Bearish — Upside Gap Two Crows
    if (_bull(c2) and _bear(c1) and _bear(c0) and
        c1['open'] > c2['close'] and c0['close'] > c2['close'] and
        c0['open'] > c1['close']):
        bear += 2.5
    # Bearish — Heikin Ashi 3 consecutivas sem sombra superior
    if len(ha) >= 3 and all(_bear(h) and _upper_shadow(h) < _body(h)*0.1 for h in ha[-3:]):
        bear += 2.0
    # Bearish — Two Crows
    if (_bull(c2) and _bear(c1) and c1['open']>c2['close'] and
        _bear(c0) and c0['open']<c1['open'] and c0['close']<c2['close']):
        bear += 2.5
    # Bearish — Advance Block (enfraquecimento de alta)
    if (all(_bull(x) for x in [c2,c1,c0]) and
        _body(c0) < _body(c1) < _body(c2) and
        _upper_shadow(c0) > _body(c0)):
        bear += 2.0

    return min(bull,10.0), min(bear,10.0)

# ── Detecção de Padrões Gráficos ──────────────────────────────────────────────
def detect_double_top(highs, price):
    """Topo duplo — sinal bearish"""
    if len(highs)<2: return 0.0
    h1,h2=highs[-2][1],highs[-1][1]
    if abs(h1-h2)/max(h1,0.0001)<0.005 and price<min(h1,h2)*0.998:
        return 3.0
    return 0.0

def detect_double_bottom(lows, price):
    """Fundo duplo — sinal bullish"""
    if len(lows)<2: return 0.0
    l1,l2=lows[-2][1],lows[-1][1]
    if abs(l1-l2)/max(l1,0.0001)<0.005 and price>max(l1,l2)*1.002:
        return 3.0
    return 0.0

def detect_head_shoulders(highs, lows, price):
    """Head and Shoulders — bearish | Inverse H&S — bullish"""
    bull=0.0; bear=0.0
    if len(highs)>=3:
        l,m,r=highs[-3][1],highs[-2][1],highs[-1][1]
        if m>l and m>r and abs(l-r)/max(l,0.0001)<0.01:
            # neckline quebrado
            neckline=(lows[-1][1] if lows else price*0.995)
            if price<neckline: bear+=4.0
    if len(lows)>=3:
        l,m,r=lows[-3][1],lows[-2][1],lows[-1][1]
        if m<l and m<r and abs(l-r)/max(l,0.0001)<0.01:
            neckline=(highs[-1][1] if highs else price*1.005)
            if price>neckline: bull+=4.0
    return bull, bear

def detect_triangle(candles, n=20):
    """Triângulo simétrico/ascendente/descendente"""
    if len(candles)<n: return 0.0
    s=candles[-n:]
    hh=[c['high'] for c in s]; ll=[c['low'] for c in s]
    # Compressão = range diminuindo
    first_range=max(hh[:n//2])-min(ll[:n//2])
    last_range=max(hh[n//2:])-min(ll[n//2:])
    if first_range>0 and last_range/first_range<0.6:
        return 1.5  # squeeze detectado — aguardar rompimento
    return 0.0

def detect_flag(candles, n=15):
    """Flag bullish/bearish"""
    if len(candles)<n+5: return 0.0, 0.0
    # Movimento inicial forte
    pole=candles[-(n+5):-(n)]
    flag=candles[-n:]
    pole_move=(pole[-1]['close']-pole[0]['close'])/max(pole[0]['close'],0.0001)
    flag_range=(max(c['high'] for c in flag)-min(c['low'] for c in flag))
    flag_range_pct=flag_range/max(pole[-1]['close'],0.0001)
    if abs(pole_move)>0.005 and flag_range_pct<abs(pole_move)*0.5:
        if pole_move>0: return 2.0, 0.0   # bull flag
        else: return 0.0, 2.0              # bear flag
    return 0.0, 0.0

def detect_channel(candles, n=20):
    """Canal de alta/baixa"""
    if len(candles)<n: return 0.0
    closes=[c['close'] for c in candles[-n:]]
    x=list(range(n)); xm=sum(x)/n; ym=sum(closes)/n
    slope=sum((x[i]-xm)*(closes[i]-ym) for i in range(n))/max(sum((x[i]-xm)**2 for i in range(n)),0.0001)
    price=closes[-1]
    # Posição no canal
    hi_ch=max(candles[-n//2:], key=lambda c:c['high'])['high']
    lo_ch=min(candles[-n//2:], key=lambda c:c['low'])['low']
    ch_range=hi_ch-lo_ch
    if ch_range==0: return 0.0
    pos=(price-lo_ch)/ch_range
    if slope>0 and pos<0.25: return 1.5   # fundo do canal de alta
    if slope<0 and pos>0.75: return -1.5  # topo do canal de baixa
    return 0.0

# ── Detecção Big Players / Manipulação ────────────────────────────────────────
def detect_big_player(candles, n=20):
    """
    Detecta movimentos institucionais:
    - Velas com corpo >= 3x a média (impulso forte)
    - Retorno rápido após spike (stop hunt / armadilha)
    - Volume anormal (se disponível)
    Retorna (direcao: 1 bull/-1 bear/0, forca: 0..1)
    """
    if len(candles)<n: return 0, 0.0
    bodies=[_body(c) for c in candles[-n:]]
    avg_body=sum(bodies[:-1])/(n-1) if n>1 else 0.0001
    if avg_body==0: avg_body=0.0001
    last_body=bodies[-1]
    c0=candles[-1]; c1=candles[-2]

    # Impulso institucional — vela >= 3x média
    if last_body>=3*avg_body:
        if _bull(c0): return 1, min(1.0, last_body/(3*avg_body)-0.5)
        else: return -1, min(1.0, last_body/(3*avg_body)-0.5)

    return 0, 0.0

def detect_stop_hunt(candles):
    """
    Stop Hunt: preço perfura um nível importante brevemente e volta
    Padrão: wick longo (> 3x corpo) seguido de fechamento oposto
    """
    if len(candles)<3: return 0
    c0=candles[-1]; c1=candles[-2]
    b0=_body(c0); b1=_body(c1)
    # Wick inferior exagerado em c1, fechamento bullish em c0 — stop hunt abaixo
    if _lower_shadow(c1)>3*max(b1,0.0001) and _bull(c0) and c0['close']>c1['close']:
        return 1   # provável stop hunt bullish
    # Wick superior exagerado em c1, fechamento bearish em c0 — stop hunt acima
    if _upper_shadow(c1)>3*max(b1,0.0001) and _bear(c0) and c0['close']<c1['close']:
        return -1  # provável stop hunt bearish
    return 0

def detect_fake_breakout(candles, n=20):
    """
    Falso rompimento: preço ultrapassa máxima/mínima dos últimos N candles
    e fecha de volta dentro — sinal de reversão
    """
    if len(candles)<n+2: return 0
    window=candles[-(n+2):-2]
    prev_high=max(c['high'] for c in window)
    prev_low=min(c['low'] for c in window)
    c1=candles[-2]; c0=candles[-1]
    # Rompimento falso de topo → bearish
    if c1['high']>prev_high and c1['close']<prev_high and _bear(c0):
        return -1
    # Rompimento falso de fundo → bullish
    if c1['low']<prev_low and c1['close']>prev_low and _bull(c0):
        return 1
    return 0

def detect_manipulation_pattern(candles):
    """
    Detecta padrões típicos de manipulação de corretora/MM:
    - Spike súbito sem contexto (vela isolada anormal)
    - Doji após tendência forte (indecisão forçada)
    - Reversão instantânea após breakout
    Retorna aviso (True/False) e direção evitada
    """
    if len(candles)<5: return False, 0
    c0=candles[-1]; c1=candles[-2]
    b0=_body(c0); b1=_body(c1)
    r0=_range(c0)
    # Spike: wick total >> corpo (> 4x) — perigo de manipulação
    total_wick=_upper_shadow(c0)+_lower_shadow(c0)
    if total_wick>4*max(b0,0.0001):
        return True, 0   # evitar entrada agora
    return False, 0

# ── Estratégias de Day Trade ──────────────────────────────────────────────────
def strategy_trend_follow(closes, candles):
    """Seguimento de tendência: EMA 8/21/55 + ADX"""
    e8=ema(closes,8); e21=ema(closes,21); e55=ema(closes,55)
    e8p=ema(closes[:-1],8); e21p=ema(closes[:-1],21)
    adx_v,pdi,mdi=adx_calc(candles)
    bull=bear=0.0
    if all(v is not None for v in [e8,e21,e55,e8p,e21p]):
        # Cruzamento EMA 8/21
        if e8p<=e21p and e8>e21: bull+=3.0
        elif e8p>=e21p and e8<e21: bear+=3.0
        # Alinhamento das 3 EMAs
        if e8>e21>e55 and closes[-1]>e8: bull+=2.0
        elif e8<e21<e55 and closes[-1]<e8: bear+=2.0
    # ADX forte confirma tendência
    if adx_v and pdi and mdi and adx_v>25:
        if pdi>mdi: bull+=1.5
        else: bear+=1.5
    return bull, bear

def strategy_mean_reversion(closes, candles):
    """Reversão à média: Bollinger + RSI extremos"""
    bbu,bbm,bbl=bollinger(closes)
    r14=rsi(closes,14); r7=rsi(closes,7)
    bull=bear=0.0
    if bbu and bbl and bbm:
        bw=bbu-bbl
        if bw>0:
            pos=(closes[-1]-bbl)/bw
            if pos<0.08: bull+=3.0   # abaixo da banda inferior — oversold
            elif pos>0.92: bear+=3.0 # acima da banda superior — overbought
            # BB squeeze → aguardar rompimento
            if bw/bbm<0.01: bull+=0.5; bear+=0.5
    if r14:
        if r14<28: bull+=2.5
        elif r14>72: bear+=2.5
    if r7:
        if r7<20: bull+=1.5
        elif r7>80: bear+=1.5
    return bull, bear

def strategy_momentum(closes, candles):
    """Momentum: MACD + Stochastic + Williams"""
    ml,ms,mh=macd_calc(closes)
    stk=stoch(candles); wr=williams_r(candles)
    cci_v=cci(candles)
    bull=bear=0.0
    if ml and ms:
        if ml>ms: bull+=1.5
        else: bear+=1.5
    if mh:
        if mh>0: bull+=1.0
        else: bear+=1.0
    if stk:
        if stk<22: bull+=1.5
        elif stk>78: bear+=1.5
    if wr:
        if wr<-78: bull+=1.0
        elif wr>-22: bear+=1.0
    if cci_v:
        if cci_v<-120: bull+=1.0
        elif cci_v>120: bear+=1.0
    return bull, bear

def strategy_ichimoku(candles, closes):
    """Ichimoku completo — nuvem, sinais tenkan/kijun"""
    if len(candles)<52: return 0.0, 0.0
    tenkan=(max(c['high'] for c in candles[-9:])+min(c['low'] for c in candles[-9:]))/2
    kijun=(max(c['high'] for c in candles[-26:])+min(c['low'] for c in candles[-26:]))/2
    ssa=(tenkan+kijun)/2  # span A atual
    ssb=(max(c['high'] for c in candles[-52:])+min(c['low'] for c in candles[-52:]))/2
    price=closes[-1]
    bull=bear=0.0
    # Preço acima/abaixo da nuvem
    cloud_top=max(ssa,ssb); cloud_bot=min(ssa,ssb)
    if price>cloud_top: bull+=2.0
    elif price<cloud_bot: bear+=2.0
    # TK Cross
    if tenkan>kijun: bull+=1.5
    elif tenkan<kijun: bear+=1.5
    # Preço vs Kijun (suporte/resistência)
    if price>kijun: bull+=1.0
    elif price<kijun: bear+=1.0
    return bull, bear

def strategy_divergence(closes, candles):
    """Divergências: RSI, MACD, Stochastic"""
    bull=bear=0.0
    # RSI divergence
    if len(closes)>=20:
        r_now=rsi(closes,14); r_5=rsi(closes[:-5],14)
        if r_now and r_5:
            price_up=closes[-1]>closes[-6]
            rsi_up=r_now>r_5
            if not price_up and rsi_up: bull+=3.5  # divergência bullish
            if price_up and not rsi_up: bear+=3.5  # divergência bearish
    # MACD divergence
    ml,ms,mh=macd_calc(closes)
    ml2,ms2,mh2=macd_calc(closes[:-5])
    if ml and ml2 and mh and mh2:
        price_up=closes[-1]>closes[-6]
        macd_up=ml>ml2
        if not price_up and macd_up: bull+=2.5
        if price_up and not macd_up: bear+=2.5
    return bull, bear

def strategy_support_resistance(candles, closes):
    """Suporte/Resistência + Rompimentos"""
    supports, resistances = support_resistance(candles)
    price=closes[-1]
    prev_price=closes[-2]
    bull=bear=0.0
    if supports:
        nearest_sup=max(supports)
        dist=(price-nearest_sup)/max(nearest_sup,0.0001)
        # Preço perto do suporte e saltando
        if 0<dist<0.003 and price>prev_price: bull+=2.5
        # Rompimento de suporte para baixo
        if prev_price>nearest_sup and price<nearest_sup: bear+=3.0
    if resistances:
        nearest_res=min(resistances)
        dist=(nearest_res-price)/max(price,0.0001)
        # Preço perto da resistência e rejeitando
        if 0<dist<0.003 and price<prev_price: bear+=2.5
        # Rompimento de resistência para cima
        if prev_price<nearest_res and price>nearest_res: bull+=3.0
    return bull, bear

def strategy_fibonacci(candles, closes):
    """Fibonacci — retração e extensão"""
    fibs=fibonacci_levels(candles)
    if not fibs: return 0.0, 0.0
    price=closes[-1]; bull=bear=0.0
    # Proximidade a nível Fibonacci com bounce
    for lvl in [0.382, 0.5, 0.618, 0.786]:
        if lvl not in fibs: continue
        fib_price=fibs[lvl]
        dist=abs(price-fib_price)/max(fib_price,0.0001)
        if dist<0.002:
            if closes[-1]>closes[-2]: bull+=1.5   # bounce bullish no nível
            else: bear+=1.5                        # rejeição no nível
    return bull, bear

def strategy_pivot_points(candles, closes):
    """Pivot Points — R1/R2/S1/S2"""
    pp=pivot_points(candles)
    if not pp: return 0.0, 0.0
    price=closes[-1]; bull=bear=0.0
    tol=0.002
    for key,val in pp.items():
        if val==0: continue
        dist=abs(price-val)/max(val,0.0001)
        if dist<tol:
            if key.startswith('s'):
                if price>=val: bull+=1.5  # suporte pivot — bounce
            elif key.startswith('r'):
                if price<=val: bear+=1.5  # resistência pivot — rejeição
    return bull, bear

def strategy_price_action_advanced(candles, closes):
    """Price Action avançado: Pin Bar, Inside Bar, Outside Bar"""
    bull=bear=0.0
    if len(candles)<4: return 0.0,0.0
    c0,c1,c2=candles[-1],candles[-2],candles[-3]
    b0,b1=_body(c0),_body(c1)
    r0,r1=_range(c0),_range(c1)
    # Pin Bar bullish
    if _lower_shadow(c0)>r0*0.6 and b0<r0*0.35: bull+=3.0
    # Pin Bar bearish
    if _upper_shadow(c0)>r0*0.6 and b0<r0*0.35: bear+=3.0
    # Inside Bar (consolidação → rompimento)
    if c0['high']<c1['high'] and c0['low']>c1['low']:
        trend=closes[-1]-closes[-5] if len(closes)>=5 else 0
        if trend>0: bull+=1.5
        elif trend<0: bear+=1.5
    # Outside Bar (engulfing de range)
    if c0['high']>c1['high'] and c0['low']<c1['low']:
        if _bull(c0): bull+=2.0
        else: bear+=2.0
    # Vela de impulso (Marubozu)
    if b0>r0*0.9:
        if _bull(c0): bull+=2.0
        else: bear+=2.0
    # Doji de reversão
    if b0<r0*0.1:
        trend=closes[-1]-closes[-5] if len(closes)>=5 else 0
        if trend<0: bull+=1.5   # doji após queda = possível reversão
        elif trend>0: bear+=1.5
    return bull, bear

def strategy_market_structure(candles, closes):
    """Estrutura de mercado: HH/HL (uptrend) vs LH/LL (downtrend)"""
    if len(closes)<20: return 0.0,0.0
    highs=[candles[i]['high'] for i in range(-10,0)]
    lows=[candles[i]['low'] for i in range(-10,0)]
    bull=bear=0.0
    # Higher Highs + Higher Lows = uptrend
    if highs[-1]>highs[-3]>highs[-5] and lows[-1]>lows[-3]>lows[-5]:
        bull+=2.5
    # Lower Highs + Lower Lows = downtrend
    elif highs[-1]<highs[-3]<highs[-5] and lows[-1]<lows[-3]<lows[-5]:
        bear+=2.5
    # Rompimento de estrutura (BOS)
    recent_high=max(candles[i]['high'] for i in range(-8,-1))
    recent_low=min(candles[i]['low'] for i in range(-8,-1))
    if closes[-1]>recent_high: bull+=3.0  # BOS bullish
    elif closes[-1]<recent_low: bear+=3.0  # BOS bearish
    return bull, bear

def strategy_volume_analysis(candles, closes):
    """Análise de volume: climax, volume spike, OBV"""
    if len(candles)<10: return 0.0, 0.0
    vols=[c.get('volume',1) for c in candles]
    avg_vol=sum(vols[-20:])/max(len(vols[-20:]),1)
    last_vol=vols[-1]
    bull=bear=0.0
    if avg_vol>0:
        ratio=last_vol/avg_vol
        # Volume spike com direção
        if ratio>2.5:
            if _bull(candles[-1]): bull+=2.5
            else: bear+=2.5
        # Volume climax (possível reversão)
        if ratio>4.0:
            if _bull(candles[-1]): bear+=1.5  # exaustão compradora
            else: bull+=1.5                   # exaustão vendedora
    # OBV simplificado
    obv=0.0
    for i in range(1,min(10,len(candles))):
        v=candles[-i].get('volume',1)
        if candles[-i]['close']>candles[-i-1]['close']: obv+=v
        else: obv-=v
    if obv>0: bull+=1.0
    elif obv<0: bear+=1.0
    return bull, bear

def strategy_session_timing():
    """Timing de sessão — horários de alta volatilidade"""
    h=datetime.utcnow().hour
    bull=bear=0.0
    bonus=0.0
    # Sobreposição Londres/NY (13-17 UTC) — maior liquidez (verificar ANTES do bloco NY)
    if   13<=h<17: bonus=2.0
    # Abertura Londres (07-10 UTC)
    elif  7<=h<10: bonus=1.5
    # Abertura Nova York pura (17h — mercado NY só, sem sobreposição)
    elif 17<=h<20: bonus=1.0
    # Horários mortos — alinhado com filtro de _varrer_ativos (02-04 UTC)
    elif h in [2, 3, 4]: bonus=-1.0
    return bonus

# ═══════════════════════════════════════════════════════════════════════════════
#  FUNÇÃO PRINCIPAL DE ANÁLISE — combina todas as estratégias
# ═══════════════════════════════════════════════════════════════════════════════
def analisar(candles, confianca_minima):
    if len(candles)<30: return None, 0.0
    closes=[c['close'] for c in candles]
    price=closes[-1]

    CALL=0.0; PUT=0.0; VETOED=False

    # ── 1. Manipulação — vetar entrada se detectado ────────────────────────
    manipulado, _ = detect_manipulation_pattern(candles)
    if manipulado:
        return None, 0.0   # mercado suspeito, não entrar

    # ── 2. Padrões de candles (peso alto) ──────────────────────────────────
    bull_c, bear_c = candlestick_patterns(candles)
    CALL += bull_c * 1.2
    PUT  += bear_c * 1.2

    # ── 3. Estratégia de tendência ─────────────────────────────────────────
    b, br = strategy_trend_follow(closes, candles)
    CALL+=b; PUT+=br

    # ── 4. Reversão à média ────────────────────────────────────────────────
    b, br = strategy_mean_reversion(closes, candles)
    CALL+=b; PUT+=br

    # ── 5. Momentum ────────────────────────────────────────────────────────
    b, br = strategy_momentum(closes, candles)
    CALL+=b; PUT+=br

    # ── 6. Ichimoku ────────────────────────────────────────────────────────
    b, br = strategy_ichimoku(candles, closes)
    CALL+=b; PUT+=br

    # ── 7. Divergências (peso premium) ─────────────────────────────────────
    b, br = strategy_divergence(closes, candles)
    CALL += b*1.3; PUT += br*1.3

    # ── 8. Suporte/Resistência ─────────────────────────────────────────────
    b, br = strategy_support_resistance(candles, closes)
    CALL+=b; PUT+=br

    # ── 9. Fibonacci ───────────────────────────────────────────────────────
    b, br = strategy_fibonacci(candles, closes)
    CALL+=b; PUT+=br

    # ── 10. Pivot Points ───────────────────────────────────────────────────
    b, br = strategy_pivot_points(candles, closes)
    CALL+=b; PUT+=br

    # ── 11. Price Action avançado ──────────────────────────────────────────
    b, br = strategy_price_action_advanced(candles, closes)
    CALL+=b; PUT+=br

    # ── 12. Estrutura de mercado ───────────────────────────────────────────
    b, br = strategy_market_structure(candles, closes)
    CALL+=b; PUT+=br

    # ── 13. Volume ─────────────────────────────────────────────────────────
    b, br = strategy_volume_analysis(candles, closes)
    CALL+=b; PUT+=br

    # ── 14. Big Players ────────────────────────────────────────────────────
    bp_dir, bp_forca = detect_big_player(candles)
    if bp_dir==1:  CALL += bp_forca*3.0
    elif bp_dir==-1: PUT += bp_forca*3.0

    # ── 15. Stop Hunt (peso extra — sinal contrário ao hunt) ───────────────
    sh = detect_stop_hunt(candles)
    if sh==1:  CALL+=3.0
    elif sh==-1: PUT+=3.0

    # ── 16. Falso rompimento ───────────────────────────────────────────────
    fb = detect_fake_breakout(candles)
    if fb==1:  CALL+=2.5
    elif fb==-1: PUT+=2.5

    # ── 17. Padrões gráficos ───────────────────────────────────────────────
    highs, lows = swing_highs_lows(candles)
    dt = detect_double_top(highs, price)
    db = detect_double_bottom(lows, price)
    PUT  += dt
    CALL += db
    bull_hs, bear_hs = detect_head_shoulders(highs, lows, price)
    CALL += bull_hs; PUT += bear_hs

    # Flag pattern
    bf, brf = detect_flag(candles)
    CALL+=bf; PUT+=brf

    # Canal
    ch_sig = detect_channel(candles)
    if ch_sig>0: CALL+=abs(ch_sig)
    elif ch_sig<0: PUT+=abs(ch_sig)

    # ── 18. Timing de sessão ───────────────────────────────────────────────
    session_bonus = strategy_session_timing()
    if session_bonus>0:
        CALL += session_bonus if CALL>PUT else 0
        PUT  += session_bonus if PUT>CALL else 0
    elif session_bonus<0:
        CALL = max(0, CALL+session_bonus)
        PUT  = max(0, PUT+session_bonus)

    # ── Calcular confiança ─────────────────────────────────────────────────
    TOTAL_MAX = 65.0   # soma dos pesos máximos possíveis
    max_pts = max(CALL, PUT)
    confianca = min(0.97, max_pts / TOTAL_MAX)

    # ── Filtro de consenso: exige mínimo 51% de dominância ───────────────────
    total = CALL+PUT
    if total>0 and max_pts/total < 0.51:
        return None, confianca

    if CALL>PUT and confianca>=confianca_minima:
        return "call", confianca
    elif PUT>CALL and confianca>=confianca_minima:
        return "put", confianca
    return None, confianca


# ═══════════════════════════════════════════════════════════════════════════════
#  IA SUPREMA — ENSEMBLE EVOLUTIVO VELA-A-VELA
# ═══════════════════════════════════════════════════════════════════════════════

class SupremeAI:
    """
    SupremeAI v4 — Ensemble com 12 modelos independentes.
    Inclui adaptações dos cérebros de Turtle Trading (Dennis), Tudor Jones,
    Livermore tape-reading, além de MFI/Aroon/TRIX/UO/CMO, regime de mercado
    e padrões harmônicos ABCD — sem dependências externas de ML.
    """

    # Mapa de nomes "trader brain" para exibição no log
    BRAIN_NAMES = {
        "tecnico":    "Técnico Clássico",
        "candle":     "Candle Patterns",
        "momentum":   "Momentum",
        "volume":     "Volume/OBV",
        "estrutura":  "Estrutura de Preço",
        "sr":         "Suporte/Resistência",
        "mtf":        "Multi-Timeframe",
        "osciladores":"Osciladores Avançados (MFI/Aroon/TRIX/UO/CMO)",
        "regime":     "Regime + ABCD Harmônico",
        "turtle":     "Dennis Turtle Breakout",
        "tudor":      "Tudor Jones Momentum",
        "livermore":  "Livermore Tape Reading",
    }

    def __init__(self):
        self._lock = threading.Lock()
        self.pesos = {
            "tecnico":    1.0,
            "candle":     1.2,
            "momentum":   0.9,
            "volume":     0.8,
            "estrutura":  1.1,
            "sr":         1.0,
            "mtf":        1.0,
            "osciladores":1.0,
            "regime":     0.9,
            "turtle":     1.1,   # Dennis Turtle Trading
            "tudor":      1.0,   # Tudor Jones breakout + momentum
            "livermore":  0.9,   # Livermore tape reading (volume surge)
        }
        self.historico_pred  = deque(maxlen=200)
        self.acerto_total    = 0
        self.total_pred      = 0
        self.taxa_acerto     = 0.0

    # ── helpers internos ──────────────────────────────────────────────────────
    @staticmethod
    def _closes(candles):  return [c['close'] for c in candles]
    @staticmethod
    def _highs(candles):   return [c['high']  for c in candles]
    @staticmethod
    def _lows(candles):    return [c['low']   for c in candles]
    @staticmethod
    def _vols(candles):    return [c.get('volume', 1) for c in candles]

    # ── Modelo 1: Técnico — RSI, MACD, BB, Stoch, WR, CCI, ADX ──────────────
    def _modelo_tecnico(self, candles):
        closes = self._closes(candles)
        r      = rsi(closes)
        ml, sl, hist = macd_calc(closes)
        bu, bm, bl   = bollinger(closes)
        price        = closes[-1]
        call_s = put_s = 0.0

        if r is not None:
            if r < 30:   call_s += 3.0
            elif r > 70: put_s  += 3.0
            elif r < 38: call_s += 1.5   # zona de interesse real (antes: < 45, muito ruim)
            elif r > 62: put_s  += 1.5

        if hist is not None:
            if hist > 0:  call_s += 2.0
            else:          put_s  += 2.0

        if bu and bl and price:
            if price <= bl:   call_s += 3.0
            elif price >= bu: put_s  += 3.0
            # BB squeeze: banda estreita = rompimento iminente
            bb_width = (bu - bl) / bm if bm else 0
            if bb_width < 0.005:
                # direcao pelo ultimo candle
                last = candles[-1]
                if last['close'] > last['open']: call_s += 1.0
                else:                             put_s  += 1.0

        # Stoch e WR removidos daqui — já calculados em _modelo_momentum
        # (evita dupla contagem no ensemble com pesos 1.0 + 0.9)

        # ADX com +DI/-DI
        adx_v, pdi, mdi = adx_calc(candles)
        if adx_v and adx_v > 25:
            if pdi and mdi:
                if pdi > mdi: call_s += 2.5
                else:          put_s  += 2.5

        # CCI
        cci_v = cci(candles)
        if cci_v is not None:
            if cci_v < -100: call_s += 1.5
            elif cci_v > 100: put_s  += 1.5

        # Parabolic SAR aproximado — exclui candle atual para evitar sinal trivial
        if len(closes) >= 6:
            prev_closes = closes[-6:-1]
            sar_bull = min(prev_closes)
            sar_bear = max(prev_closes)
            if price > sar_bear * 1.001: call_s += 1.0
            elif price < sar_bull * 0.999: put_s += 1.0

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", call_s / (total + 4)
        return "put", put_s / (total + 4)

    # ── Modelo 2: Padrões de candle (50+ padrões) ─────────────────────────────
    def _modelo_candle(self, candles):
        bull, bear = candlestick_patterns(candles)
        total = bull + bear
        if total < 1.0: return None, 0.0
        if bull > bear: return "call", min(0.92, bull / (total + 3))
        return "put", min(0.92, bear / (total + 3))

    # ── Modelo 3: Momentum — ROC, Stoch, WR, aceleração ──────────────────────
    def _modelo_momentum(self, candles):
        if len(candles) < 12: return None, 0.0
        closes = self._closes(candles)
        roc5  = (closes[-1] - closes[-6])  / closes[-6]  if closes[-6]  else 0
        roc10 = (closes[-1] - closes[-11]) / closes[-11] if closes[-11] else 0
        # aceleração (ROC do ROC)
        accel = roc5 - ((closes[-5] - closes[-10]) / closes[-10] if closes[-10] else 0)
        st = stoch(candles);  wr = williams_r(candles)
        call_s = put_s = 0.0
        if roc5 > 0.0015:   call_s += 1.5
        elif roc5 < -0.0015: put_s += 1.5
        if roc10 > 0.003:   call_s += 1.0
        elif roc10 < -0.003: put_s += 1.0
        if accel > 0: call_s += 1.0
        elif accel < 0: put_s += 1.0
        if st is not None:
            if st < 20: call_s += 2.0
            elif st > 80: put_s += 2.0
        if wr is not None:
            if wr < -80: call_s += 1.5
            elif wr > -20: put_s += 1.5
        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", call_s / (total + 3)
        return "put", put_s / (total + 3)

    # ── Modelo 4: Volume — OBV, CMF, VWAP, ratio ──────────────────────────────
    def _modelo_volume(self, candles):
        if len(candles) < 10: return None, 0.0
        closes = self._closes(candles)
        vols   = self._vols(candles)
        avg_v  = sum(vols[:-1]) / max(len(vols) - 1, 1)
        ratio  = vols[-1] / avg_v if avg_v > 0 else 1.0
        last   = candles[-1]
        call_s = put_s = 0.0

        # Volume + direção
        if ratio > 1.5:
            if last['close'] > last['open']: call_s += 2.0 * ratio
            else:                             put_s  += 2.0 * ratio

        # OBV
        obv = 0.0
        for i in range(1, len(candles)):
            if candles[i]['close'] > candles[i-1]['close']:
                obv += vols[i]
            elif candles[i]['close'] < candles[i-1]['close']:
                obv -= vols[i]
        if obv > 0: call_s += 1.5
        elif obv < 0: put_s += 1.5

        # CMF (Chaikin Money Flow) simplificado
        cmf_num = cmf_den = 0.0
        for i in range(min(14, len(candles))):
            c = candles[-(i+1)]
            h, l, cl, v = c['high'], c['low'], c['close'], c.get('volume', 1)
            rng = h - l
            if rng > 0:
                mf_mult = ((cl - l) - (h - cl)) / rng
                cmf_num += mf_mult * v
                cmf_den += v
        cmf_val = cmf_num / cmf_den if cmf_den > 0 else 0
        if cmf_val > 0.1:  call_s += 2.0
        elif cmf_val < -0.1: put_s += 2.0

        # VWAP simples (últimas 20 velas) — nível de referência dos big players
        n = min(20, len(candles))
        vwap_num = sum((candles[-i]['close'] * candles[-i].get('volume', 1)) for i in range(1, n+1))
        vwap_den = sum(candles[-i].get('volume', 1) for i in range(1, n+1))
        vwap = vwap_num / vwap_den if vwap_den > 0 else closes[-1]
        price = closes[-1]
        if price > vwap * 1.0008: call_s += 2.0   # preço acima do VWAP = compra institucional
        elif price < vwap * 0.9992: put_s += 2.0   # preço abaixo do VWAP = venda institucional

        # Volume surge: pico de volume = big player entrando — seguir a direção
        vol_surge = ratio > 2.0
        if vol_surge:
            if last['close'] > last['open']: call_s += 3.0   # surge bullish
            else:                             put_s  += 3.0   # surge bearish

        # Pin bar institucional: corpo pequeno + sombra longa = rejeição de nível
        _rng  = last['high'] - last['low']
        _body = abs(last['close'] - last['open'])
        if _rng > 0 and _body / _rng < 0.30:   # corpo < 30% do range = pin bar
            _upper_wick = last['high'] - max(last['close'], last['open'])
            _lower_wick = min(last['close'], last['open']) - last['low']
            if _lower_wick > _rng * 0.60: call_s += 2.5   # sombra baixa longa = hammer bullish
            elif _upper_wick > _rng * 0.60: put_s += 2.5  # sombra alta longa = shooting star

        # Pivot points (calculados sobre as últimas 20 velas como proxy do dia)
        if len(candles) >= 20:
            _h20 = max(c['high']  for c in candles[-20:])
            _l20 = min(c['low']   for c in candles[-20:])
            _c20 = candles[-1]['close']
            pp   = (_h20 + _l20 + _c20) / 3
            r1   = 2 * pp - _l20
            s1   = 2 * pp - _h20
            if price < s1 * 1.001:  call_s += 1.5   # no suporte pivot — compra
            elif price > r1 * 0.999: put_s += 1.5   # na resistência pivot — venda

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", min(0.90, call_s / (total + 2))
        return "put", min(0.90, put_s / (total + 2))

    # ── Modelo 5: Estrutura de mercado — HH/HL/LH/LL, Ichimoku, tendência ────
    def _modelo_estrutura(self, candles):
        if len(candles) < 20: return None, 0.0
        closes = self._closes(candles)
        highs  = self._highs(candles)
        lows   = self._lows(candles)
        hh = sum(1 for i in range(1, min(6, len(closes))) if closes[-i] > closes[-(i+1)])
        ll = sum(1 for i in range(1, min(6, len(closes))) if closes[-i] < closes[-(i+1)])
        call_s = put_s = 0.0
        if hh >= 4: call_s += 2.5
        if ll >= 4: put_s  += 2.5

        # Ichimoku simplificado (tenkan/kijun)
        if len(candles) >= 26:
            tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
            kijun  = (max(highs[-26:]) + min(lows[-26:])) / 2
            price  = closes[-1]
            if price > tenkan > kijun:  call_s += 2.0
            elif price < tenkan < kijun: put_s  += 2.0
            if tenkan > kijun: call_s += 1.0
            else:               put_s  += 1.0

        # SMA slope (inclinação das médias)
        if len(closes) >= 21:
            sma20_now  = sum(closes[-20:]) / 20
            sma20_prev = sum(closes[-21:-1]) / 20
            if sma20_now > sma20_prev: call_s += 1.5
            else:                       put_s  += 1.5

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", call_s / (total + 3)
        return "put", put_s / (total + 3)

    # ── Modelo 6: Suporte e Resistência dinâmico ──────────────────────────────
    def _modelo_sr(self, candles):
        if len(candles) < 30: return None, 0.0
        closes = self._closes(candles)
        highs  = self._highs(candles)
        lows   = self._lows(candles)
        price  = closes[-1]

        # Calcular S/R com janela de 20 velas
        resistencias = []
        suportes = []
        for i in range(20, len(candles)):
            bloco_h = highs[i-20:i]
            bloco_l = lows[i-20:i]
            resistencias.append(sorted(bloco_h)[int(len(bloco_h)*0.8)])
            suportes.append(sorted(bloco_l)[int(len(bloco_l)*0.2)])

        if not resistencias: return None, 0.0
        res = resistencias[-1]
        sup = suportes[-1]
        rng = res - sup if res > sup else 0.0001

        dist_res = (res - price) / rng   # 0 = na resistência, 1 = no suporte
        dist_sup = (price - sup) / rng   # 0 = no suporte, 1 = na resistência

        call_s = put_s = 0.0
        # Rompimento — sinal exclusivo, verificado antes dos thresholds de proximidade
        breakout_call = price > res
        breakout_put  = price < sup
        last = candles[-1]
        body = abs(last['close'] - last['open'])

        if breakout_call and body > 0:
            call_s += 2.5
        elif breakout_put and body > 0:
            put_s += 2.5
        else:
            # Proximidade só faz sentido quando NÃO há rompimento (dist >= 0)
            if 0 <= dist_sup < 0.15: call_s += 3.0
            elif 0 <= dist_sup < 0.30: call_s += 1.5
            if 0 <= dist_res < 0.15: put_s += 3.0
            elif 0 <= dist_res < 0.30: put_s += 1.5

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", call_s / (total + 3)
        return "put", put_s / (total + 3)

    # ── Modelo 7: Multi-timeframe simulado (janelas 5/15/30 min em 1min) ──────
    def _modelo_mtf(self, candles):
        """
        Simula análise de timeframes maiores agrupando velas de 1 min.
        Janelas: 5 velas = 5min, 15 velas = 15min, 30 velas = 30min.
        """
        if len(candles) < 30: return None, 0.0

        def _agg(velas):
            """Agrega lista de velas em OHLCV único."""
            if not velas: return None
            return {
                'open':   velas[0]['open'],
                'high':   max(c['high'] for c in velas),
                'low':    min(c['low']  for c in velas),
                'close':  velas[-1]['close'],
                'volume': sum(c.get('volume',1) for c in velas),
            }

        call_s = put_s = 0.0

        for janela in [5, 15, 30]:
            n_velas = len(candles) // janela
            if n_velas < 4: continue
            tf_candles = [_agg(candles[i*janela:(i+1)*janela]) for i in range(n_velas)]
            tf_closes  = [c['close'] for c in tf_candles if c]
            if len(tf_closes) < 4: continue

            # Tendência no TF maior
            sma = sum(tf_closes[-4:]) / 4
            if tf_closes[-1] > sma * 1.0005: call_s += 1.5
            elif tf_closes[-1] < sma * 0.9995: put_s += 1.5

            # Momentum no TF maior
            if tf_closes[-1] > tf_closes[-2]: call_s += 1.0
            else:                               put_s  += 1.0

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", call_s / (total + 3)
        return "put", put_s / (total + 3)

    # ── Análise vela-a-vela com janela deslizante ─────────────────────────────
    def _analise_vela_a_vela(self, candles, janela=10):
        if len(candles) < janela + 5:
            return 0.0, 0.0
        call_acc = put_acc = 0.0
        for i in range(janela, 0, -1):
            sub  = candles[:-i]
            if len(sub) < 15: continue
            bull, bear = candlestick_patterns(sub)
            peso = (janela - i + 1) / janela
            call_acc += bull * peso
            put_acc  += bear * peso
        return call_acc, put_acc

    # ── Confirmação por lookback de 10 velas ─────────────────────────────────
    def confirmar_lookback_10(self, candles, direcao):
        """
        Analisa as 10 últimas velas FECHADAS para confirmar a direção antes de operar.
        Dojis (close == open) são contabilizados separadamente e não inflam bears.

        Dois cenários aprovam o sinal:
          - Trend continuation : ≥6 velas direcionais na direção do sinal
          - Reversal setup     : ≥7 velas direcionais na direção OPOSTA + as 2 últimas
                                 velas fechadas virando para a direção do sinal

        O score retornado distingue os dois cenários:
          - Trend   : contagem direta (ex: 6/10 → 0.60)
          - Reversal: força oposta × 0.80 para refletir a viragem (ex: 7/10 × 0.8 → 0.56)
        """
        if len(candles) < 12:
            return True, 0.5   # dados insuficientes — não bloqueia

        velas = candles[-11:-1]     # 10 velas FECHADAS (exclui vela atual em formação)
        # Dojis (close == open) ficam neutros — não são bearish por omissão
        bulls = sum(1 for c in velas if c['close'] > c['open'])
        bears = sum(1 for c in velas if c['close'] < c['open'])

        # Últimas 2 velas fechadas: confirmam que a viragem já começou
        last2_bullish = (candles[-2]['close'] > candles[-2]['open'] and
                         candles[-3]['close'] > candles[-3]['open'])
        last2_bearish = (candles[-2]['close'] < candles[-2]['open'] and
                         candles[-3]['close'] < candles[-3]['open'])

        if direcao == "call":
            # bulls >= 5: intencional — 50% de maioria já indica leve bias direcional.
            # Mercados puramente laterais são filtrados pelas barreiras de confluência e confiança.
            trend_ok    = bulls >= 5
            reversal_ok = bears >= 7 and last2_bullish
            if trend_ok:
                score = bulls / 10
            elif reversal_ok:
                score = bears / 10 * 0.80
            else:
                score = 0.0   # sinal inválido — score residual (para log/debug apenas)
        else:  # put
            # bears >= 5: mesma filosofia do call — bias mínimo de 50% na direção.
            trend_ok    = bears >= 5
            reversal_ok = bulls >= 7 and last2_bearish
            if trend_ok:
                score = bears / 10
            elif reversal_ok:
                score = bulls / 10 * 0.80
            else:
                score = 0.0   # sinal inválido — score residual (para log/debug apenas)

        return (trend_ok or reversal_ok), round(score, 2)

    # ── Risco abrangente ──────────────────────────────────────────────────────
    def calcular_risco(self, candles):
        """Retorna score de risco 0-1 (0=baixo, 1=alto)."""
        if len(candles) < 20: return 0.5
        closes = self._closes(candles)
        # Volatilidade recente vs histórica
        retornos = [abs(closes[i]/closes[i-1]-1) for i in range(1, len(closes))]
        vol_rec  = sum(retornos[-10:]) / 10 if len(retornos) >= 10 else 0
        vol_hist = sum(retornos) / len(retornos) if retornos else 0.001
        vol_score = min(1.0, vol_rec / (vol_hist * 2 + 0.001))
        # Amplitude da última vela vs média
        last = candles[-1]
        body   = abs(last['close'] - last['open'])
        ranges = [abs(c['high'] - c['low']) for c in candles[-20:]]
        avg_rng = sum(ranges) / len(ranges) if ranges else 0.001
        amp_score = min(1.0, body / (avg_rng + 0.001))
        return (vol_score * 0.6 + amp_score * 0.4)

    # ── Modelo 8: Osciladores Avançados — MFI, Aroon, TRIX, UO, CMO ──────────
    def _modelo_osciladores(self, candles):
        if len(candles) < 30: return None, 0.0
        closes = self._closes(candles)
        call_s = put_s = 0.0

        # MFI (Money Flow Index) — como RSI mas ponderado por volume
        mfi_v = mfi(candles)
        if mfi_v is not None:
            if   mfi_v < 25: call_s += 2.0    # sobrevendido
            elif mfi_v > 75: put_s  += 2.0    # sobrecomprado
            elif mfi_v < 40: call_s += 1.0
            elif mfi_v > 60: put_s  += 1.0

        # Aroon — indica início de nova tendência
        ar_up, ar_dn = aroon(candles)
        if ar_up is not None:
            if   ar_up > 80 and ar_dn < 20: call_s += 2.5   # tendência altista nascendo
            elif ar_dn > 80 and ar_up < 20: put_s  += 2.5   # tendência baixista nascendo
            elif ar_up > ar_dn:              call_s += 0.8
            else:                            put_s  += 0.8

        # TRIX — Triple EMA ROC (filtra ruído)
        tx = trix_val(closes)
        if tx is not None:
            if   tx > 0.02:  call_s += 1.5
            elif tx < -0.02: put_s  += 1.5
            elif tx > 0:     call_s += 0.5
            else:            put_s  += 0.5

        # Ultimate Oscillator (7/14/28)
        uo_v = ultimate_oscillator(candles)
        if uo_v is not None:
            if   uo_v < 30: call_s += 2.0
            elif uo_v > 70: put_s  += 2.0
            elif uo_v < 45: call_s += 0.8
            elif uo_v > 55: put_s  += 0.8

        # Chande Momentum Oscillator
        cmo = chande_momentum(closes)
        if cmo is not None:
            if   cmo < -50: call_s += 1.5
            elif cmo > 50:  put_s  += 1.5
            elif cmo < -20: call_s += 0.8
            elif cmo > 20:  put_s  += 0.8

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", min(0.92, call_s / (total + 2))
        return "put", min(0.92, put_s / (total + 2))

    # ── Modelo 9: Regime de Mercado + Padrões Harmônicos ─────────────────────
    def _modelo_regime(self, candles):
        if len(candles) < 25: return None, 0.0
        closes = self._closes(candles)
        call_s = put_s = 0.0

        # Regime de mercado — premia tendência, penaliza ranging/volatile
        regime = detect_market_regime(candles)
        if regime == 'trending':
            # Na tendência, seguir a direção — usa SMA slope
            sma_now  = sum(closes[-20:]) / 20
            sma_prev = sum(closes[-21:-1]) / 20
            if sma_now > sma_prev: call_s += 3.0
            else:                  put_s  += 3.0
        elif regime == 'ranging':
            # Ranging — estratégia de reversão nas bandas
            bu, bm, bl = bollinger(closes)
            price = closes[-1]
            if bu and bl:
                pos = (price - bl) / max(bu - bl, 0.0001)
                if   pos < 0.15: call_s += 2.5   # próximo à banda inferior
                elif pos > 0.85: put_s  += 2.5   # próximo à banda superior
        elif regime == 'volatile':
            return None, 0.0   # volátil demais — não operar

        # Heikin Ashi trend confirmation
        ha = heikin_ashi(candles[-10:])
        if len(ha) >= 4:
            ha_bull = sum(1 for h in ha[-4:] if _bull(h))
            ha_bear = sum(1 for h in ha[-4:] if _bear(h))
            if ha_bull >= 3: call_s += 1.5
            elif ha_bear >= 3: put_s += 1.5

        # Padrão ABCD harmônico
        abcd = detect_abcd_pattern(candles)
        if   abcd ==  1: call_s += 2.0
        elif abcd == -1: put_s  += 2.0

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", min(0.92, call_s / (total + 2))
        return "put", min(0.92, put_s / (total + 2))

    # ── Modelo 10: Dennis Turtle Trading — Breakout 20/55 períodos ────────────
    def _modelo_turtle(self, candles):
        """
        Richard Dennis Turtle System adaptado para opções binárias.
        System 1: breakout de 20 candles.
        System 2: breakout de 55 candles (confirmação de tendência maior).
        Requer volatilidade acima da média (ATR > média ATR).
        """
        if len(candles) < 60: return None, 0.0
        closes = self._closes(candles)
        highs  = self._highs(candles)
        lows   = self._lows(candles)
        price  = closes[-1]

        # ATR atual vs média — Turtle só opera com volatilidade adequada
        atr_cur = atr_val(candles)
        atrs    = [atr_val(candles[:i]) for i in range(20, len(candles)) if i >= 20]
        atrs    = [a for a in atrs if a]
        avg_atr = sum(atrs[-20:]) / len(atrs[-20:]) if atrs else atr_cur or 0.001
        vol_ok  = atr_cur and atr_cur > avg_atr * 0.8

        # System 1 — breakout de 20 períodos
        h20 = max(highs[-21:-1])
        l20 = min(lows[-21:-1])
        # System 2 — breakout de 55 períodos (tendência maior)
        h55 = max(highs[-56:-1])
        l55 = min(lows[-56:-1])

        call_s = put_s = 0.0

        if vol_ok:
            if price > h20:
                call_s += 3.0        # Turtle S1 bullish breakout
            elif price < l20:
                put_s  += 3.0        # Turtle S1 bearish breakout

            if price > h55:
                call_s += 2.5        # Turtle S2 confirmação
            elif price < l55:
                put_s  += 2.5

        # Mesmo sem vol_ok: checar breakout suave (menor peso)
        elif price > h20:  call_s += 1.0
        elif price < l20:  put_s  += 1.0

        # Momentum confirma: últimas 5 velas na direção do breakout
        last5_up   = sum(1 for i in range(-5, 0) if closes[i] > closes[i-1])
        last5_down = 5 - last5_up
        if call_s > put_s and last5_up >= 3:   call_s += 1.0
        if put_s  > call_s and last5_down >= 3: put_s  += 1.0

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", min(0.92, call_s / (total + 2))
        return "put", min(0.92, put_s / (total + 2))

    # ── Modelo 11: Tudor Jones — Momentum + Breakout com R:R assimétrico ──────
    def _modelo_tudor(self, candles):
        """
        Paul Tudor Jones: opera breakout de momentum com R:R favorável.
        Usa ROC multi-período para confirmar aceleração + ATR para medir R:R.
        Não entra sem pelo menos 2:1 de R:R implícito.
        """
        if len(candles) < 40: return None, 0.0
        closes = self._closes(candles)
        price  = closes[-1]

        call_s = put_s = 0.0

        # ROC de 5, 10 e 20 períodos — todos devem apontar na mesma direção
        def roc(n):
            return (closes[-1] - closes[-n-1]) / max(closes[-n-1], 0.0001) * 100

        r5  = roc(5)
        r10 = roc(10)
        r20 = roc(20)

        # Aceleração de momentum: ROC curto > ROC longo → tendência acelerando
        if r5 > 0 and r10 > 0 and r20 > 0 and r5 > r10:
            call_s += 3.5    # momentum altista acelerando
        elif r5 < 0 and r10 < 0 and r20 < 0 and r5 < r10:
            put_s  += 3.5    # momentum baixista acelerando

        # Confirmação com SMA 10 (Tudor usa médias rápidas como filtro)
        sma10 = sum(closes[-10:]) / 10
        if price > sma10 * 1.001: call_s += 1.5
        elif price < sma10 * 0.999: put_s += 1.5

        # R:R implícito via ATR: só opera se amplitude recente > 1.5x ATR médio
        atr_cur = atr_val(candles) or 0.001
        amp_5   = max(c['high'] for c in candles[-6:-1]) - min(c['low'] for c in candles[-6:-1])
        if amp_5 < atr_cur * 1.0:
            # Movimento fraco demais para R:R favorável — reduz pontuação
            call_s *= 0.5
            put_s  *= 0.5

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", min(0.92, call_s / (total + 2))
        return "put", min(0.92, put_s / (total + 2))

    # ── Modelo 12: Livermore Tape Reading — Volume Surge + Price Action ────────
    def _modelo_livermore(self, candles):
        """
        Jesse Livermore: ler o "fluxo da fita" via surtos de volume institucional.
        Volume 2x+ acima da média com movimento direcional forte = sinal institucional.
        "Line of least resistance": rompimento de máxima/mínima recente confirmado por volume.
        """
        if len(candles) < 25: return None, 0.0
        closes = self._closes(candles)
        vols   = self._vols(candles)
        price  = closes[-1]

        # Volume médio das últimas 20 velas (excluindo a atual)
        avg_vol = sum(vols[-21:-1]) / 20 if len(vols) > 20 else sum(vols[:-1]) / max(len(vols)-1, 1)
        cur_vol = vols[-1]
        vol_surge = cur_vol / max(avg_vol, 0.001)

        call_s = put_s = 0.0

        # Surto institucional (Livermore: volume 1.5x+ com candle direcional forte)
        lc = candles[-1]
        body = lc['close'] - lc['open']
        rng  = max(lc['high'] - lc['low'], 0.0001)
        body_pct = body / rng

        if vol_surge >= 1.5:
            if body_pct > 0.5:   call_s += 3.0   # vela altista + volume institucional
            elif body_pct < -0.5: put_s += 3.0   # vela baixista + volume institucional
            elif body_pct > 0.2:  call_s += 1.5
            elif body_pct < -0.2: put_s  += 1.5

        # "Line of least resistance": resistência mínima na direção do preço
        h10 = max(c['high']  for c in candles[-12:-2])
        l10 = min(c['low']   for c in candles[-12:-2])
        if price > h10 and vol_surge >= 1.2:
            call_s += 2.0   # rompimento de máxima + volume confirma
        elif price < l10 and vol_surge >= 1.2:
            put_s  += 2.0   # rompimento de mínima + volume confirma

        # OBV direction — acumulação/distribuição sustentada
        obv = 0.0
        for i in range(1, len(candles)):
            if closes[i] > closes[i-1]: obv += vols[i]
            else:                        obv -= vols[i]
        obv_prev = 0.0
        for i in range(1, len(candles)-5):
            if closes[i] > closes[i-1]: obv_prev += vols[i]
            else:                        obv_prev -= vols[i]

        if obv > obv_prev + abs(obv_prev) * 0.05:  call_s += 1.0   # acumulação crescente
        elif obv < obv_prev - abs(obv_prev) * 0.05: put_s  += 1.0  # distribuição crescente

        total = call_s + put_s
        if total == 0: return None, 0.0
        if call_s > put_s: return "call", min(0.92, call_s / (total + 2))
        return "put", min(0.92, put_s / (total + 2))

    # ── Explicação detalhada ──────────────────────────────────────────────────
    def gerar_explicacao(self, candles, direcao, confianca):
        closes = self._closes(candles)
        r = rsi(closes)
        _, _, hist = macd_calc(closes)
        bu, bm, bl = bollinger(closes)
        price = closes[-1]
        parts = []
        if r is not None:
            if r < 30:   parts.append(f"RSI {r:.0f} — sobrevendido ↑")
            elif r > 70: parts.append(f"RSI {r:.0f} — sobrecomprado ↓")
            else:        parts.append(f"RSI {r:.0f}")
        if hist is not None:
            parts.append("MACD cruzou ↑" if hist > 0 else "MACD cruzou ↓")
        if bu and bl:
            if price <= bl:   parts.append("Toque na BB inferior ↑")
            elif price >= bu: parts.append("Toque na BB superior ↓")
        if len(candles) >= 26:
            highs = self._highs(candles)
            lows  = self._lows(candles)
            tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
            kijun  = (max(highs[-26:]) + min(lows[-26:])) / 2
            if price > tenkan > kijun:  parts.append("Ichimoku altista ↑")
            elif price < tenkan < kijun: parts.append("Ichimoku baixista ↓")
        seta = "CALL ↑" if direcao == "call" else "PUT ↓"
        return f"{seta} ({confianca:.0%}) | " + " | ".join(parts[:3])

    # ── Ensemble principal ────────────────────────────────────────────────────
    def analisar_supremo(self, candles, confianca_minima=0.55):
        """
        12 modelos com pesos adaptativos + vela-a-vela + motor clássico.
        Retorna (direcao, confianca, votos) ou (None, confianca, []).
        """
        if len(candles) < 20:
            return None, 0.0, []

        with self._lock:
            pesos_snap = dict(self.pesos)

        modelos = [
            ("tecnico",    self._modelo_tecnico(candles)),
            ("candle",     self._modelo_candle(candles)),
            ("momentum",   self._modelo_momentum(candles)),
            ("volume",     self._modelo_volume(candles)),
            ("estrutura",  self._modelo_estrutura(candles)),
            ("sr",         self._modelo_sr(candles)),
            ("mtf",        self._modelo_mtf(candles)),
            ("osciladores",self._modelo_osciladores(candles)),
            ("regime",     self._modelo_regime(candles)),
            ("turtle",     self._modelo_turtle(candles)),
            ("tudor",      self._modelo_tudor(candles)),
            ("livermore",  self._modelo_livermore(candles)),
        ]

        call_total = put_total = 0.0
        for nome, (dir_m, conf_m) in modelos:
            peso = pesos_snap.get(nome, 1.0)
            if dir_m == "call":   call_total += conf_m * peso
            elif dir_m == "put":  put_total  += conf_m * peso

        # Análise vela-a-vela (bônus de padrão temporal)
        cv_call, cv_put = self._analise_vela_a_vela(candles)
        call_total += cv_call * 0.15
        put_total  += cv_put  * 0.15

        # Motor clássico (70+ estratégias) com peso extra
        dir_cl, conf_cl = analisar(candles, 0.0)
        if dir_cl == "call": call_total += conf_cl * 2.0
        elif dir_cl == "put": put_total += conf_cl * 2.0

        # Penalidade contra-tendência: sinal oposto à SMA-50 leva redução de 30%
        if len(candles) >= 100:
            closes_all = self._closes(candles)
            sma50_now  = sum(closes_all[-50:]) / 50
            sma50_prev = sum(closes_all[-100:-50]) / 50   # janela anterior sem sobreposição
            if sma50_now > sma50_prev * 1.0002 and put_total > call_total:
                put_total  *= 0.70   # PUT contra tendência de alta
            elif sma50_now < sma50_prev * 0.9998 and call_total > put_total:
                call_total *= 0.70   # CALL contra tendência de baixa

        grand_total = call_total + put_total
        if grand_total == 0:
            return None, 0.0, []

        max_pts   = max(call_total, put_total)
        confianca = min(0.97, max_pts / (grand_total + 0.5))

        # Filtro de dominância: mínimo 51% (qualquer maioria simples basta)
        if max_pts / grand_total < 0.51:
            return None, confianca, []

        if confianca < confianca_minima:
            return None, confianca, []

        direcao = "call" if call_total > put_total else "put"

        votos_favor = [
            self.BRAIN_NAMES.get(nome, nome)
            for nome, (d, c) in modelos if d == direcao and c > 0
        ]

        return direcao, confianca, votos_favor

    # ── Aprendizado evolutivo thread-safe ─────────────────────────────────────
    def aprender(self, direcao_pred, direcao_real, confianca, modelos_usados=None):
        with self._lock:
            self.total_pred += 1
            acertou = (direcao_pred == direcao_real)
            if acertou:
                self.acerto_total += 1
            self.taxa_acerto = self.acerto_total / self.total_pred if self.total_pred > 0 else 0.0
            self.historico_pred.append((direcao_pred, direcao_real, confianca, acertou))

            acertos_rec = sum(1 for _, _, _, a in list(self.historico_pred)[-20:] if a)
            total_rec   = min(20, len(self.historico_pred))
            taxa_rec    = acertos_rec / total_rec if total_rec > 0 else 0.5

            # ── Punição escalonada por taxa de acerto recente ─────────────────
            if taxa_rec < 0.40:
                # Desempenho crítico: punição severa em todos os modelos fracos
                fator_pen = 0.70   # 30% de redução
                fator_bon = 1.08
                self.pesos["candle"]    = max(0.2, self.pesos["candle"]    * fator_pen)
                self.pesos["mtf"]       = max(0.2, self.pesos["mtf"]       * fator_pen)
                self.pesos["momentum"]  = max(0.2, self.pesos["momentum"]  * fator_pen)
                self.pesos["tecnico"]   = min(3.0, self.pesos["tecnico"]   * fator_bon)
                self.pesos["estrutura"] = min(3.0, self.pesos["estrutura"] * fator_bon)
                self.pesos["sr"]        = min(3.0, self.pesos["sr"]        * fator_bon)
            elif taxa_rec < 0.55:
                # Desempenho ruim: punição moderada nos modelos noise
                self.pesos["candle"]    = max(0.3, self.pesos["candle"]    * 0.85)
                self.pesos["mtf"]       = max(0.3, self.pesos["mtf"]       * 0.85)
                self.pesos["tecnico"]   = min(2.5, self.pesos["tecnico"]   * 1.05)
                self.pesos["estrutura"] = min(2.5, self.pesos["estrutura"] * 1.05)
                self.pesos["sr"]        = min(2.5, self.pesos["sr"]        * 1.05)
            elif taxa_rec > 0.85:
                # Excelente: reforço maior para não desperdiçar o momento
                for k in self.pesos:
                    self.pesos[k] = min(3.0, self.pesos[k] * 1.08)
            elif taxa_rec > 0.72:
                # Bom desempenho: reforça todos os modelos igualmente
                for k in self.pesos:
                    self.pesos[k] = min(3.0, self.pesos[k] * 1.04)

# Instância global da IA Suprema
ia_suprema = SupremeAI()

# ═══════════════════════════════════════════════════════════════════════════════
#  FILTROS DE QUALIDADE — QualityFilters
# ═══════════════════════════════════════════════════════════════════════════════

class QualityFilters:
    """Score de qualidade do setup: tendência, alinhamento MTF, volatilidade, volume, momentum."""

    def calculate_quality_score(self, features: dict) -> float:
        score = 0.0
        # 1. Tendência (ADX) — 20%
        adx = features.get("adx", 0.0)
        score += min(adx / 50.0, 1.0) * 0.20
        # 2. Alinhamento de timeframes — 25%
        score += self._timeframe_alignment(features) * 0.25
        # 3. Volatilidade adequada — 15%
        atr = features.get("atr", 0.001)
        vol_score = 1.0 if 0.0005 < atr < 0.008 else 0.3
        score += vol_score * 0.15
        # 4. Volume confirmado — 20%
        score += self._volume_score(features) * 0.20
        # 5. Momentum claro — 20%
        score += self._momentum_score(features) * 0.20
        return round(min(score, 1.0), 3)

    def _timeframe_alignment(self, features: dict) -> float:
        alignments = total = 0
        rsi_1m = features.get("rsi", 50)
        rsi_5m = features.get("rsi_5m", 50)
        if (rsi_1m < 40 and rsi_5m < 40) or (rsi_1m > 60 and rsi_5m > 60):
            alignments += 1
        total += 1
        sma_now  = features.get("sma_now", 0)
        sma_prev = features.get("sma_prev", 0)
        sma_5m   = features.get("sma_5m_now", 0)
        sma_5m_p = features.get("sma_5m_prev", 0)
        if sma_now and sma_prev and sma_5m and sma_5m_p:
            trend_1 = 1 if sma_now > sma_prev else -1
            trend_5 = 1 if sma_5m > sma_5m_p else -1
            if trend_1 == trend_5:
                alignments += 1
        total += 1
        return alignments / total if total else 0.5

    def _volume_score(self, features: dict) -> float:
        ratio = features.get("vol_ratio", 1.0)
        obv_pos = features.get("obv_positive", True)
        score = 0.3  # baseline neutro: volume normal não é penalizado
        if ratio > 1.3:
            score += 0.4  # volume acima da média
        if obv_pos and ratio > 1.0:
            score += 0.3  # OBV confirma direção com volume
        return min(score, 1.0)

    def _momentum_score(self, features: dict) -> float:
        macd_hist = features.get("macd_hist", 0.0)
        rsi = features.get("rsi", 50)
        score = 0.0
        if abs(macd_hist) > 0.00005:
            score += 0.5
        if rsi < 35 or rsi > 65:
            score += 0.5
        return score


# ═══════════════════════════════════════════════════════════════════════════════
#  GERENCIADOR DE RISCO — AdvancedRiskManager
# ═══════════════════════════════════════════════════════════════════════════════

class AdvancedRiskManager:
    """Avalia risco do setup antes de cada operação."""

    FORBIDDEN_HOURS = {9, 12, 17}   # Horários de alta volatilidade/baixa liquidez

    def analyze_risk(self, features: dict, confianca: float) -> dict:
        fatores = []
        atr = features.get("atr", 0.002)
        adx = features.get("adx", 25)

        if atr > 0.008:
            fatores.append("ALTA_VOLATILIDADE")
        if atr < 0.0005:
            fatores.append("VOLATILIDADE_MUITO_BAIXA")
        if adx < 18:
            fatores.append("TENDENCIA_FRACA")
        if datetime.now().hour in self.FORBIDDEN_HOURS:
            fatores.append("HORARIO_RISCO")
        if confianca < 0.42:
            fatores.append("CONFIANCA_BAIXA")

        nivel = "HIGH" if len(fatores) >= 3 else ("MEDIUM" if len(fatores) >= 2 else "LOW")
        return {
            "risk_level": nivel,
            "risk_factors": fatores,
            "risk_score": len(fatores) / 5.0,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  ANÁLISE DE CONFLUÊNCIA — ConfluenceAnalyzer
# ═══════════════════════════════════════════════════════════════════════════════

class ConfluenceAnalyzer:
    """Verifica quantos e quais indicadores confirmam o mesmo sinal."""

    MIN_CONFLUENCE_FORCE = 0.4  # sinal leve já é suficiente para agilidade máxima

    def __init__(self):
        self._lock = threading.Lock()

    def analyze(self, features: dict) -> dict:
        confluences = []

        rsi = features.get("rsi", 50)
        if rsi < 35:   confluences.append({"tipo": "RSI_SOBREVENDIDO",   "sinal": "call", "forca": 0.8})
        elif rsi > 65: confluences.append({"tipo": "RSI_SOBRECOMPRADO",  "sinal": "put",  "forca": 0.8})
        elif rsi < 45: confluences.append({"tipo": "RSI_LEVEMENTE_BAIXO", "sinal": "call", "forca": 0.4})
        elif rsi > 55: confluences.append({"tipo": "RSI_LEVEMENTE_ALTO",  "sinal": "put",  "forca": 0.4})

        macd_hist = features.get("macd_hist", 0.0)
        if macd_hist > 0:  confluences.append({"tipo": "MACD_ALTISTA",  "sinal": "call", "forca": 0.7})
        elif macd_hist < 0: confluences.append({"tipo": "MACD_BAIXISTA", "sinal": "put",  "forca": 0.7})

        bb_pos = features.get("bb_position", 0.5)
        if bb_pos < 0.1:   confluences.append({"tipo": "BB_SOBREVENDIDA",  "sinal": "call", "forca": 0.7})
        elif bb_pos > 0.9: confluences.append({"tipo": "BB_SOBRECOMPRADA", "sinal": "put",  "forca": 0.7})

        adx = features.get("adx", 0)
        pdi = features.get("pdi", 0)
        mdi = features.get("mdi", 0)
        if adx > 20:
            if pdi > mdi:  confluences.append({"tipo": "ADX_ALTISTA_FORTE",  "sinal": "call", "forca": 0.9})
            elif mdi > pdi: confluences.append({"tipo": "ADX_BAIXISTA_FORTE", "sinal": "put",  "forca": 0.9})

        stk = features.get("stoch_k", 50)
        if stk < 20:  confluences.append({"tipo": "STOCH_SOBREVENDIDO",  "sinal": "call", "forca": 0.6})
        elif stk > 80: confluences.append({"tipo": "STOCH_SOBRECOMPRADO", "sinal": "put",  "forca": 0.6})

        wr = features.get("wr", -50)
        if wr < -80:  confluences.append({"tipo": "WR_SOBREVENDIDO",  "sinal": "call", "forca": 0.6})
        elif wr > -20: confluences.append({"tipo": "WR_SOBRECOMPRADO", "sinal": "put",  "forca": 0.6})

        cci_v = features.get("cci", 0)
        if cci_v < -100: confluences.append({"tipo": "CCI_SOBREVENDIDO",  "sinal": "call", "forca": 0.6})
        elif cci_v > 100: confluences.append({"tipo": "CCI_SOBRECOMPRADO", "sinal": "put",  "forca": 0.6})

        # SMA trend: direção da média móvel confirma sinal
        sma_now  = features.get("sma_now", 0)
        sma_prev = features.get("sma_prev", 0)
        if sma_now and sma_prev:
            if sma_now > sma_prev * 1.00005:
                confluences.append({"tipo": "SMA_TENDENCIA_ALTA", "sinal": "call", "forca": 0.5})
            elif sma_now < sma_prev * 0.99995:
                confluences.append({"tipo": "SMA_TENDENCIA_BAIXA", "sinal": "put", "forca": 0.5})

        # RSI rápido (período 7) — extra sinal de scalping
        rsi7 = features.get("rsi7", 50)
        if rsi7 < 25:   confluences.append({"tipo": "RSI7_EXTREMO_BAIXO",  "sinal": "call", "forca": 0.9})
        elif rsi7 > 75: confluences.append({"tipo": "RSI7_EXTREMO_ALTO",   "sinal": "put",  "forca": 0.9})
        elif rsi7 < 38: confluences.append({"tipo": "RSI7_BAIXO",          "sinal": "call", "forca": 0.5})
        elif rsi7 > 62: confluences.append({"tipo": "RSI7_ALTO",           "sinal": "put",  "forca": 0.5})

        # MACD rápido (5/13) — detecta reversões antes do MACD padrão
        mf_hist = features.get("macd_fast_hist", None)
        mf_prev = features.get("macd_fast_hist_prev", None)
        if mf_hist is not None:
            if mf_hist > 0:   confluences.append({"tipo": "MACD_FAST_ALTISTA",  "sinal": "call", "forca": 0.7})
            elif mf_hist < 0: confluences.append({"tipo": "MACD_FAST_BAIXISTA", "sinal": "put",  "forca": 0.7})
            # Crossover detectado (histograma mudou de sinal)
            if mf_prev is not None:
                if mf_prev <= 0 < mf_hist:
                    confluences.append({"tipo": "MACD_FAST_CROSSOVER_ALTA", "sinal": "call", "forca": 1.0})
                elif mf_prev >= 0 > mf_hist:
                    confluences.append({"tipo": "MACD_FAST_CROSSOVER_BAIXA","sinal": "put",  "forca": 1.0})

        # Bollinger Squeeze breakout — padrão de alta probabilidade
        bb_sq = features.get("bb_squeeze_dir", None)
        if bb_sq == 'up':
            confluences.append({"tipo": "BB_SQUEEZE_BREAKOUT_UP",   "sinal": "call", "forca": 1.1})
        elif bb_sq == 'down':
            confluences.append({"tipo": "BB_SQUEEZE_BREAKOUT_DOWN", "sinal": "put",  "forca": 1.1})

        # Padrão instantâneo: RSI extremo + volume spike (BinaryDecisionEngine)
        vol_ratio = features.get("vol_ratio", 1.0)
        if vol_ratio >= 2.0:
            if rsi7 < 35 or (features.get("rsi", 50) < 35):
                confluences.append({"tipo": "OVERSOLD_VOLUME_SPIKE",   "sinal": "call", "forca": 1.2})
            elif rsi7 > 65 or (features.get("rsi", 50) > 65):
                confluences.append({"tipo": "OVERBOUGHT_VOLUME_SPIKE", "sinal": "put",  "forca": 1.2})

        call_f = sum(c["forca"] for c in confluences if c["sinal"] == "call")
        put_f  = sum(c["forca"] for c in confluences if c["sinal"] == "put")
        dominante = "call" if call_f >= put_f else "put"

        with self._lock:
            min_force = self.MIN_CONFLUENCE_FORCE
        dom_force = sum(c["forca"] for c in confluences if c["sinal"] == dominante)
        return {
            "confluences": confluences,
            "count": len(confluences),
            "call_forca": call_f,
            "put_forca":  put_f,
            "dominante":  dominante,
            "ok": dom_force >= min_force - 1e-9,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  MONITORAMENTO CONTÍNUO — ContinuousMonitoring
# ═══════════════════════════════════════════════════════════════════════════════

class ContinuousMonitoring:
    """Rastreia performance e ajusta thresholds adaptativamente."""

    def __init__(self):
        self._lock = threading.Lock()
        self.total  = 0
        self.wins   = 0
        self.losses = 0
        self.profit = 0.0
        self.daily_trades   = 0
        self.last_trade_time: datetime | None = None
        self.cooldown_min   = 1   # 1 min (operações de 1 min)

    def pode_operar(self) -> tuple[bool, str]:
        """Retorna (pode, motivo)."""
        with self._lock:
            if self.last_trade_time:
                diff = (datetime.now() - self.last_trade_time).total_seconds() / 60
                if diff < self.cooldown_min:
                    secs = round((self.cooldown_min - diff) * 60)
                    msg = f"Cooldown: {secs}s restantes" if secs > 0 else "Cooldown: liberando..."
                    return False, msg
            return True, "OK"

    def registrar_trade(self, direcao: str, valor: float):
        with self._lock:
            self.daily_trades   += 1
            self.last_trade_time = datetime.now()

    def registrar_resultado(self, ganhou: bool, payout: float, valor: float):
        with self._lock:
            self.total += 1
            if ganhou:
                self.wins   += 1
                self.profit += payout
            else:
                self.losses += 1
                self.profit -= valor
            # Ajuste adaptativo: taxa < 55% → exigir mais confluência; normal → restaurar baseline
            wr = self.wins / self.total if self.total > 0 else 0.5
            if self.total >= 10:
                with confluence_analyzer._lock:
                    if wr < 0.50:
                        confluence_analyzer.MIN_CONFLUENCE_FORCE = 0.9  # desempenho ruim: sinal forte exigido
                    elif wr > 0.70:
                        confluence_analyzer.MIN_CONFLUENCE_FORCE = 0.4  # desempenho bom: sinal leve suficiente
                    else:
                        confluence_analyzer.MIN_CONFLUENCE_FORCE = 0.5  # faixa normal: sinal moderado

    def stats(self) -> dict:
        with self._lock:
            wr = self.wins / self.total * 100 if self.total else 0
            return {
                "total": self.total,
                "wins":  self.wins,
                "losses":self.losses,
                "wr":    f"{wr:.1f}%",
                "profit":f"R$ {self.profit:+.2f}",
            }


# Instâncias globais dos subsistemas de qualidade
quality_filters     = QualityFilters()
risk_manager_adv    = AdvancedRiskManager()
confluence_analyzer = ConfluenceAnalyzer()
monitor_continuo    = ContinuousMonitoring()


# ═══════════════════════════════════════════════════════════════════════════════
#  ESTADO GLOBAL DO BOT
# ═══════════════════════════════════════════════════════════════════════════════
class BotEstado:
    def __init__(self):
        self._lock = threading.Lock()
        self.rodando = False
        self.conectado = False
        self.client = None
        self.estrategia = None
        self.email = ""
        self.senha = ""
        self.demo = True
        self.saldo_inicial = 0.0
        self.saldo_atual = 0.0
        self.lucro = 0.0
        self.total_investido = 0.0
        self.wins = 0
        self.losses = 0
        self.trades_abertos = {}
        self.cooldowns = {}
        self.log_msgs = deque(maxlen=100)
        self.loop = None
        self.thread = None
        self.penalidade = {}
        self.historico_ativo = {}
        self.trade_em_curso = False
        self.conf_floor  = 0.40
        self.losses_consec = 0
        # Métricas avançadas de performance
        self.gross_profit   = 0.0   # soma dos ganhos
        self.gross_loss     = 0.0   # soma das perdas (valor absoluto)
        self.peak_lucro     = 0.0   # pico de lucro para cálculo de drawdown
        self.max_drawdown   = 0.0   # máximo drawdown registrado

    def snapshot(self):
        """Retorna cópia consistente dos campos de UI sob lock."""
        with self._lock:
            return {
                "wins":           self.wins,
                "losses":         self.losses,
                "saldo_atual":    self.saldo_atual,
                "total_investido":self.total_investido,
                "lucro":          self.lucro,
                "conectado":      self.conectado,
                "rodando":        self.rodando,
                "demo":           self.demo,
                "trade_em_curso": self.trade_em_curso,
            }

    def update(self, **kwargs):
        """Atualiza campos sob lock — use no lugar de atribuição direta."""
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

estado = BotEstado()


# ═══════════════════════════════════════════════════════════════════════════════
#  JANELA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Profit Cash  💰")
        self.geometry("1020x680")
        self.minsize(960, 620)
        self.configure(bg=BG_DARK)
        self.resizable(True, True)

        # Centralizar na tela
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 1020) // 2
        y = (self.winfo_screenheight() - 680) // 2
        self.geometry(f"1020x680+{x}+{y}")

        # Ícone (fallback se não houver arquivo)
        try:
            self.iconbitmap("icon.ico")
        except Exception:
            pass

        self.frames = {}
        container = tk.Frame(self, bg=BG_DARK)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        for FrameClass in (TelaLogin, TelaDashboard):
            frame = FrameClass(container, self)
            self.frames[FrameClass.__name__] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.mostrar("TelaLogin")
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)

    def mostrar(self, nome):
        frame = self.frames[nome]
        frame.tkraise()
        if hasattr(frame, "ao_entrar"):
            frame.ao_entrar()

    def _ao_fechar(self):
        estado.rodando = False
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════════
#  TELA DE LOGIN
# ═══════════════════════════════════════════════════════════════════════════════
class TelaLogin(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg=BG_DARK)
        self.controller = controller
        self._build()

    def _build(self):
        # ── Partículas decorativas de fundo ──────────────────────────────
        canvas = tk.Canvas(self, bg=BG_DARK, highlightthickness=0)
        canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        for x, y, r, c in [(120,80,60,"#1a0a3a"),(800,100,90,"#0d0d25"),
                            (900,500,70,"#1a0a3a"),(60,520,50,"#0d0d25"),
                            (500,600,110,"#130a2a")]:
            canvas.create_oval(x-r, y-r, x+r, y+r, fill=c, outline="")

        # ── Container central ─────────────────────────────────────────────
        wrap = tk.Frame(self, bg=BG_DARK)
        wrap.place(relx=0.5, rely=0.5, anchor="center")

        # ── Logo ──────────────────────────────────────────────────────────
        logo_row = tk.Frame(wrap, bg=BG_DARK)
        logo_row.pack(pady=(0, 6))
        tk.Label(logo_row, text="💰", font=("Segoe UI", 46),
                 bg=BG_DARK, fg=GOLD).pack(side="left", padx=(0, 8))
        tk.Label(logo_row, text="PROFIT CASH",
                 font=("Segoe UI", 32, "bold"),
                 bg=BG_DARK, fg=TEXT_WHITE).pack(side="left")

        tk.Label(wrap, text="Powered by Quotex  •  70+ estratégias profissionais  •  IA adaptativa",
                 font=("Segoe UI", 10),
                 bg=BG_DARK, fg=TEXT_GREY).pack(pady=(0, 26))

        # ── Card principal ────────────────────────────────────────────────
        card = tk.Frame(wrap, bg=BG_CARD)
        card.pack(ipadx=44, ipady=36)
        card.config(highlightbackground=PURPLE, highlightthickness=1)

        tk.Label(card, text="Acesse sua conta",
                 font=("Segoe UI", 17, "bold"),
                 bg=BG_CARD, fg=TEXT_WHITE).pack(pady=(0, 6))
        tk.Label(card, text="Entre com suas credenciais da Quotex",
                 font=("Segoe UI", 11),
                 bg=BG_CARD, fg=TEXT_GREY).pack(pady=(0, 22))

        # Campo email
        self._label(card, "E-MAIL")
        self.email_var = tk.StringVar()
        self.email_entry = self._entry(card, self.email_var, False, "seu@email.com")

        tk.Frame(card, height=14, bg=BG_CARD).pack()

        # Campo senha
        self._label(card, "SENHA")
        self.senha_var = tk.StringVar()
        self.senha_entry = self._entry(card, self.senha_var, True, "••••••••••")

        tk.Frame(card, height=24, bg=BG_CARD).pack()

        # Botão entrar — gradiente simulado com frame externo
        btn_wrap = tk.Frame(card, bg=PURPLE)
        btn_wrap.pack(fill="x", padx=2, pady=2)
        self.btn_entrar = tk.Button(
            btn_wrap,
            text="ENTRAR  →",
            font=("Segoe UI", 11, "bold"),
            bg=PURPLE, fg=TEXT_WHITE,
            activebackground=PURPLE_LT,
            activeforeground=TEXT_WHITE,
            relief="flat", cursor="hand2", bd=0,
            padx=20, pady=11,
            command=self._entrar
        )
        self.btn_entrar.pack(fill="x")

        # Separador
        sep_row = tk.Frame(card, bg=BG_CARD)
        sep_row.pack(pady=(22, 0))
        tk.Frame(sep_row, width=90, height=1, bg=BORDER).pack(side="left", padx=6)
        tk.Label(sep_row, text="ou", font=("Segoe UI", 9),
                 bg=BG_CARD, fg=TEXT_GREY).pack(side="left")
        tk.Frame(sep_row, width=90, height=1, bg=BORDER).pack(side="left", padx=6)

        # Link cadastro
        tk.Label(card, text="Não tem conta ainda?",
                 font=("Segoe UI", 9),
                 bg=BG_CARD, fg=TEXT_GREY).pack(pady=(16, 6))

        btn_cad = tk.Button(
            card,
            text="✦  Criar conta gratuita na Quotex",
            font=("Segoe UI", 9, "bold"),
            bg=BG_CARD2, fg=GOLD,
            activebackground=BORDER, activeforeground=GOLD_LT,
            relief="flat", cursor="hand2", bd=0,
            padx=14, pady=9,
            command=lambda: webbrowser.open(SIGNUP_URL)
        )
        btn_cad.pack(pady=(0, 2))
        btn_cad.config(highlightbackground=GOLD, highlightthickness=1)

        # Rodapé
        tk.Label(wrap,
                 text="© 2025 Profit Cash  •  Operações de risco — invista com responsabilidade",
                 font=("Segoe UI", 8),
                 bg=BG_DARK, fg=TEXT_GREY).pack(pady=(18, 0))

    def _label(self, parent, texto):
        tk.Label(parent, text=texto,
                 font=("Segoe UI", 10, "bold"),
                 bg=BG_CARD, fg=TEXT_GREY).pack(anchor="w", padx=6, pady=(0, 5))

    def _entry(self, parent, var, senha, placeholder=""):
        frame = tk.Frame(parent, bg=BG_INPUT)
        frame.pack(fill="x", padx=4)
        frame.config(highlightbackground=BORDER, highlightthickness=1)
        e = tk.Entry(frame, textvariable=var,
                     font=("Segoe UI", 11),
                     bg=BG_INPUT, fg=TEXT_WHITE,
                     insertbackground=PURPLE_LT,
                     relief="flat", bd=0,
                     width=32,
                     show="●" if senha else "")
        e.pack(ipady=9, padx=12)

        # Foco: mudar borda para roxo
        def _on_focus_in(e_, frm=frame): frm.config(highlightbackground=PURPLE)
        def _on_focus_out(e_, frm=frame): frm.config(highlightbackground=BORDER)
        e.bind("<FocusIn>", _on_focus_in)
        e.bind("<FocusOut>", _on_focus_out)
        return e

    def _entrar(self):
        email = self.email_var.get().strip()
        senha = self.senha_var.get().strip()
        if not email or not senha:
            messagebox.showwarning("Campos vazios",
                                   "Preencha o e-mail e a senha.")
            return
        estado.email = email
        estado.senha = senha
        estado.demo = True  # começa em demo; toggle no dashboard
        self.controller.mostrar("TelaDashboard")


# ═══════════════════════════════════════════════════════════════════════════════
#  TELA DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
class TelaDashboard(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg=BG_DARK)
        self.controller = controller
        self.estrategia_sel = tk.StringVar(value="moderada")
        self.demo_mode = tk.BooleanVar(value=True)
        self._build()
        self._tick()

    def ao_entrar(self):
        pass

    def _build(self):
        # ── Header premium ────────────────────────────────────────────────
        header = tk.Frame(self, bg=BG_CARD, height=62)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.config(highlightbackground=BORDER, highlightthickness=1)

        # Logo lado esquerdo
        logo_f = tk.Frame(header, bg=BG_CARD)
        logo_f.pack(side="left", padx=18)
        tk.Label(logo_f, text="💰", font=("Segoe UI", 22),
                 bg=BG_CARD, fg=GOLD).pack(side="left")
        tk.Label(logo_f, text=" PROFIT CASH",
                 font=("Segoe UI", 15, "bold"),
                 bg=BG_CARD, fg=TEXT_WHITE).pack(side="left")

        # Status conexão
        self.lbl_status = tk.Label(header, text="●  Desconectado",
                                   font=("Segoe UI", 10),
                                   bg=BG_CARD, fg=TEXT_RED)
        self.lbl_status.pack(side="left", padx=14)

        # Lado direito: toggle Demo/Real + hora + sair
        right_h = tk.Frame(header, bg=BG_CARD)
        right_h.pack(side="right", padx=14)

        btn_sair = tk.Button(right_h, text="⬅  Sair",
                             font=("Segoe UI", 10),
                             bg=BG_CARD, fg=TEXT_GREY,
                             activebackground=BG_CARD2, activeforeground=TEXT_WHITE,
                             relief="flat", cursor="hand2", bd=0,
                             padx=8, pady=4,
                             command=self._sair)
        btn_sair.pack(side="right", padx=(8, 0))

        self.lbl_hora = tk.Label(right_h, text="",
                                 font=("Segoe UI", 10),
                                 bg=BG_CARD, fg=TEXT_GREY)
        self.lbl_hora.pack(side="right", padx=12)

        # ── Toggle Demo / Conta Real ──────────────────────────────────────
        toggle_frame = tk.Frame(right_h, bg=BG_CARD2)
        toggle_frame.pack(side="right", padx=4)
        toggle_frame.config(highlightbackground=BORDER, highlightthickness=1)

        self.btn_demo = tk.Button(
            toggle_frame, text="Demonstração",
            font=("Segoe UI", 10, "bold"),
            bg=PURPLE, fg=TEXT_WHITE,
            activebackground=PURPLE_LT, activeforeground=TEXT_WHITE,
            relief="flat", cursor="hand2", bd=0,
            padx=12, pady=5,
            command=lambda: self._set_modo(True)
        )
        self.btn_demo.pack(side="left")

        self.btn_real = tk.Button(
            toggle_frame, text="Conta Real",
            font=("Segoe UI", 10),
            bg=BG_CARD2, fg=TEXT_GREY,
            activebackground=BG_CARD, activeforeground=TEXT_WHITE,
            relief="flat", cursor="hand2", bd=0,
            padx=12, pady=5,
            command=lambda: self._set_modo(False)
        )
        self.btn_real.pack(side="left")

        # ── Corpo principal ───────────────────────────────────────────────
        body = tk.Frame(self, bg=BG_DARK)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # Coluna esquerda com scroll
        left_outer = tk.Frame(body, bg=BG_DARK, width=362)
        left_outer.pack(side="left", fill="y", padx=(0, 12))
        left_outer.pack_propagate(False)

        left_canvas = tk.Canvas(left_outer, bg=BG_DARK, highlightthickness=0,
                                width=350)
        left_scroll = tk.Scrollbar(left_outer, orient="vertical",
                                   command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left = tk.Frame(left_canvas, bg=BG_DARK)
        left_win = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _on_left_resize(event):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            left_canvas.itemconfig(left_win, width=left_canvas.winfo_width())
        left.bind("<Configure>", _on_left_resize)
        left_canvas.bind("<Configure>",
                         lambda e: left_canvas.itemconfig(left_win, width=e.width))

        # Scroll com roda do mouse — registrado UMA vez, removido ao destruir a tela

        def _on_mousewheel(event):
            # Cálculo de delta com guarda de plataforma explícita.
            # Wayland (XWayland): pode disparar <MouseWheel> com event.num==0
            # e event.delta preenchido — o branch else já cobre esse caso.
            if sys.platform == "linux" and event.num in (4, 5):
                # X11 clássico: Button-4 = scroll up, Button-5 = scroll down
                delta = -1 if event.num == 4 else 1
            elif sys.platform == "darwin":
                # macOS: delta em incrementos unitários (sem ×120)
                delta = -int(event.delta)
            else:
                # Windows + XWayland (event.num==0, event.delta preenchido)
                delta = int(-1 * (event.delta / 120))

            # Rola somente se o widget sob o cursor é descendente do left_canvas
            w = event.widget.winfo_containing(event.x_root, event.y_root)
            while w is not None:
                if w is left_canvas:
                    bbox = left_canvas.bbox("all")
                    if bbox and bbox[3] > left_canvas.winfo_height():
                        left_canvas.yview_scroll(delta, "units")
                    return
                try:
                    w = w.master
                except Exception:
                    break

        # Capturar funcid para remoção cirúrgica (sem afetar outros handlers)
        root = self.winfo_toplevel()
        _mw_id = _b4_id = _b5_id = None
        if sys.platform == "linux":
            # Linux usa Button-4/5; <MouseWheel> não é disparado nesta plataforma
            _b4_id = root.bind("<Button-4>", _on_mousewheel, add="+")
            _b5_id = root.bind("<Button-5>", _on_mousewheel, add="+")
        else:
            _mw_id = root.bind("<MouseWheel>", _on_mousewheel, add="+")

        def _on_destroy(event):
            if event.widget is not self:
                return
            try:
                if _mw_id:
                    root.unbind("<MouseWheel>", _mw_id)
                if _b4_id:
                    root.unbind("<Button-4>", _b4_id)
                if _b5_id:
                    root.unbind("<Button-5>", _b5_id)
            except Exception:
                pass
        self.bind("<Destroy>", _on_destroy)

        right = tk.Frame(body, bg=BG_DARK)
        right.pack(side="left", fill="both", expand=True)

        self._build_estrategia(left)
        self._build_controles(left)
        self._build_stats(right)
        self._build_log(right)

    def _set_modo(self, demo: bool):
        if estado.rodando:
            messagebox.showinfo("Bot ativo", "Pare o bot antes de trocar o modo.")
            return
        self.demo_mode.set(demo)
        estado.demo = demo
        if demo:
            self.btn_demo.config(bg=PURPLE, fg=TEXT_WHITE,
                                 font=("Segoe UI", 10, "bold"))
            self.btn_real.config(bg=BG_CARD2, fg=TEXT_GREY,
                                 font=("Segoe UI", 10))
        else:
            self.btn_real.config(bg=GOLD, fg=BG_DARK,
                                 font=("Segoe UI", 10, "bold"))
            self.btn_demo.config(bg=BG_CARD2, fg=TEXT_GREY,
                                 font=("Segoe UI", 10))
            messagebox.showwarning(
                "⚠ Conta Real",
                "Você está prestes a operar com dinheiro REAL.\n"
                "Opere apenas o que pode perder."
            )

    # ── Seleção de estratégia ─────────────────────────────────────────────
    def _build_estrategia(self, parent):
        lbl_sec = tk.Label(parent, text="ESTRATÉGIA DE RISCO",
                           font=("Segoe UI", 10, "bold"),
                           bg=BG_DARK, fg=TEXT_GREY)
        lbl_sec.pack(anchor="w", pady=(0, 8))

        self.cards_btn = {}
        for key, cfg in ESTRATEGIAS.items():
            card = tk.Frame(parent, bg=BG_CARD, cursor="hand2")
            card.pack(fill="x", pady=3)
            card.config(highlightbackground=BORDER, highlightthickness=1)

            inner = tk.Frame(card, bg=BG_CARD, padx=14, pady=11)
            inner.pack(fill="x")

            # Ícone colorido
            ic_bg = tk.Frame(inner, bg=cfg["cor_sel"], width=44, height=44)
            ic_bg.pack(side="left", padx=(0, 12))
            ic_bg.pack_propagate(False)
            tk.Label(ic_bg, text=cfg["emoji"],
                     font=("Segoe UI", 18),
                     bg=cfg["cor_sel"]).place(relx=0.5, rely=0.5, anchor="center")

            txt_frame = tk.Frame(inner, bg=BG_CARD)
            txt_frame.pack(side="left", fill="x", expand=True)

            lbl_nome = tk.Label(txt_frame, text=cfg["nome"],
                                font=("Segoe UI", 12, "bold"),
                                bg=BG_CARD, fg=TEXT_WHITE)
            lbl_nome.pack(anchor="w")

            lbl_desc = tk.Label(txt_frame, text=cfg["descricao"],
                                font=("Segoe UI", 10),
                                bg=BG_CARD, fg=TEXT_GREY,
                                justify="left")
            lbl_desc.pack(anchor="w")

            det = tk.Label(txt_frame,
                           text=f"Confiança ≥{cfg['confianca']:.0%}  ·  Max {cfg['max_trades']} trade",
                           font=("Segoe UI", 9, "bold"),
                           bg=BG_CARD, fg=cfg["cor"])
            det.pack(anchor="w", pady=(2, 0))

            rb = tk.Radiobutton(inner, variable=self.estrategia_sel,
                                value=key,
                                bg=BG_CARD,
                                activebackground=BG_CARD,
                                selectcolor=cfg["cor"],
                                command=lambda k=key: self._sel_estrategia(k))
            rb.pack(side="right")

            self.cards_btn[key] = card
            for w in [card, inner, ic_bg, txt_frame, lbl_nome, lbl_desc, det]:
                w.bind("<Button-1>", lambda e, k=key: self._sel_estrategia(k))

        self._sel_estrategia("moderada")

    def _sel_estrategia(self, key):
        if estado.rodando:
            return
        self.estrategia_sel.set(key)
        for k, card in self.cards_btn.items():
            cor = ESTRATEGIAS[k]["cor"] if k == key else BORDER
            card.config(highlightbackground=cor, highlightthickness=2 if k == key else 1)

    # ── Controles ─────────────────────────────────────────────────────────
    def _build_controles(self, parent):
        tk.Frame(parent, height=10, bg=BG_DARK).pack()

        # ── Campo de valor por operação ───────────────────────────────────
        valor_card = tk.Frame(parent, bg=BG_CARD)
        valor_card.pack(fill="x", pady=(0, 10))
        valor_card.config(highlightbackground=BORDER, highlightthickness=1)

        inner_val = tk.Frame(valor_card, bg=BG_CARD, padx=14, pady=12)
        inner_val.pack(fill="x")

        tk.Label(inner_val, text="💵  VALOR POR OPERAÇÃO",
                 font=("Segoe UI", 10, "bold"),
                 bg=BG_CARD, fg=TEXT_GREY).pack(anchor="w", pady=(0, 8))

        entry_row = tk.Frame(inner_val, bg=BG_CARD)
        entry_row.pack(fill="x")

        tk.Label(entry_row, text="R$",
                 font=("Segoe UI", 14, "bold"),
                 bg=BG_CARD, fg=TEXT_WHITE).pack(side="left", padx=(0, 6))

        self.valor_var = tk.StringVar(value="10")
        self.valor_entry = tk.Entry(
            entry_row,
            textvariable=self.valor_var,
            font=("Segoe UI", 18, "bold"),
            bg=BG_INPUT, fg=GOLD_LT,
            insertbackground=PURPLE_LT,
            relief="flat", bd=0,
            width=8,
            justify="center"
        )
        self.valor_entry.pack(side="left", ipady=7)
        self.valor_entry.config(highlightbackground=PURPLE, highlightthickness=1)

        # Botões rápidos de valor
        rapidos_row = tk.Frame(inner_val, bg=BG_CARD)
        rapidos_row.pack(fill="x", pady=(10, 0))

        tk.Label(rapidos_row, text="Rápido:",
                 font=("Segoe UI", 9),
                 bg=BG_CARD, fg=TEXT_GREY).pack(side="left", padx=(0, 6))

        for val in [5, 10, 25, 50, 100]:
            btn = tk.Button(
                rapidos_row,
                text=f"R${val}",
                font=("Segoe UI", 9, "bold"),
                bg=BG_CARD2, fg=PURPLE_LT,
                activebackground=BORDER, activeforeground=TEXT_WHITE,
                relief="flat", cursor="hand2", bd=0,
                padx=7, pady=3,
                command=lambda v=val: self.valor_var.set(str(v))
            )
            btn.pack(side="left", padx=2)
            btn.config(highlightbackground=BORDER, highlightthickness=1)

        tk.Label(inner_val,
                 text="⚠  Opere somente o que pode perder",
                 font=("Segoe UI", 9),
                 bg=BG_CARD, fg=TEXT_GREY).pack(anchor="w", pady=(8, 0))

        # Botão INICIAR — roxo com borda brilhante
        btn_ini_wrap = tk.Frame(parent, bg=PURPLE)
        btn_ini_wrap.pack(fill="x", pady=(0, 6))
        self.btn_iniciar = tk.Button(
            btn_ini_wrap,
            text="▶   INICIAR ROBÔ",
            font=("Segoe UI", 12, "bold"),
            bg=PURPLE, fg=TEXT_WHITE,
            activebackground=PURPLE_LT, activeforeground=TEXT_WHITE,
            relief="flat", cursor="hand2", bd=0,
            pady=12,
            command=self._iniciar
        )
        self.btn_iniciar.pack(fill="x")

        # Botão PARAR — vermelho escuro
        self.btn_parar = tk.Button(
            parent,
            text="⏹   PARAR",
            font=("Segoe UI", 12, "bold"),
            bg="#3d0f0f", fg="#ff6b6b",
            activebackground=TEXT_RED, activeforeground=TEXT_WHITE,
            relief="flat", cursor="hand2", bd=0,
            pady=12,
            state="disabled",
            command=self._parar
        )
        self.btn_parar.pack(fill="x")
        self.btn_parar.config(highlightbackground=TEXT_RED, highlightthickness=1)

    # ── Estatísticas ───────────────────────────────────────────────────────
    def _build_stats(self, parent):
        stats_row = tk.Frame(parent, bg=BG_DARK)
        stats_row.pack(fill="x", pady=(0, 8))

        metricas = [
            ("💰  SALDO", "lbl_saldo", TEXT_WHITE),
            ("📈  INVESTIDO", "lbl_investido", PURPLE_LT),
            ("✅  FATURADO", "lbl_faturado", TEXT_GREEN),
            ("🎯  WIN RATE", "lbl_wr", GOLD),
        ]

        for titulo, attr, cor in metricas:
            card = tk.Frame(stats_row, bg=BG_CARD)
            card.pack(side="left", fill="both", expand=True, padx=3)
            card.config(highlightbackground=BORDER, highlightthickness=1)

            tk.Label(card, text=titulo,
                     font=("Segoe UI", 9, "bold"),
                     bg=BG_CARD, fg=TEXT_GREY).pack(pady=(10, 3))

            # linha colorida sob o título
            tk.Frame(card, height=2, bg=cor).pack(fill="x", padx=10)

            lbl = tk.Label(card, text="R$ 0,00",
                           font=("Segoe UI", 15, "bold"),
                           bg=BG_CARD, fg=cor)
            lbl.pack(pady=(6, 10))
            setattr(self, attr, lbl)

        # Linha wins/losses
        wl_row = tk.Frame(parent, bg=BG_CARD)
        wl_row.pack(fill="x", pady=(0, 8))
        wl_row.config(highlightbackground=BORDER, highlightthickness=1)

        self.lbl_trades = tk.Label(
            wl_row,
            text="Trades: 0  |  ✅ Wins: 0  |  ❌ Losses: 0  |  Em curso: 0",
            font=("Segoe UI", 10),
            bg=BG_CARD, fg=TEXT_GREY
        )
        self.lbl_trades.pack(side="left", padx=12, pady=8)

    # ── Log de operações ───────────────────────────────────────────────────
    def _build_log(self, parent):
        tk.Label(parent, text="OPERAÇÕES EM TEMPO REAL",
                 font=("Segoe UI", 10, "bold"),
                 bg=BG_DARK, fg=TEXT_GREY).pack(anchor="w", pady=(0, 5))

        log_frame = tk.Frame(parent, bg=BG_CARD)
        log_frame.pack(fill="both", expand=True)
        log_frame.config(highlightbackground=BORDER, highlightthickness=1)

        self.log_text = tk.Text(
            log_frame,
            bg=BG_CARD, fg=TEXT_WHITE,
            font=("Consolas", 10),
            relief="flat", bd=0,
            state="disabled",
            wrap="word",
            insertbackground=PURPLE_LT
        )
        self.log_text.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        scroll = tk.Scrollbar(log_frame, command=self.log_text.yview,
                              bg=BG_CARD2, troughcolor=BG_DARK,
                              relief="flat", bd=0)
        scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scroll.set)

        self.log_text.tag_config("call",  foreground=TEXT_GREEN)
        self.log_text.tag_config("put",   foreground=TEXT_RED)
        self.log_text.tag_config("win",   foreground=TEXT_GREEN)
        self.log_text.tag_config("loss",  foreground=TEXT_RED)
        self.log_text.tag_config("info",  foreground=PURPLE_LT)
        self.log_text.tag_config("warn",  foreground=GOLD)
        self.log_text.tag_config("grey",  foreground=TEXT_GREY)

    # ── Log helper ─────────────────────────────────────────────────────────
    def log(self, msg, tag="info"):
        hora = datetime.now().strftime("%H:%M:%S")
        linha = f"[{hora}] {msg}\n"
        estado.log_msgs.append((linha, tag))
        self.log_text.config(state="normal")
        self.log_text.insert("end", linha, tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # ── Tick de atualização da UI ──────────────────────────────────────────
    def _tick(self):
        self.lbl_hora.config(text=datetime.now().strftime("%d/%m/%Y  %H:%M:%S"))

        # ── Snapshot atômico e consistente de todo o estado ──────────────────
        s = estado.snapshot()
        wins          = s["wins"]
        losses        = s["losses"]
        saldo_atual   = s["saldo_atual"]
        inv_total     = s["total_investido"]
        lucro         = s["lucro"]
        conectado     = s["conectado"]
        rodando       = s["rodando"]
        demo          = s["demo"]
        em_curso_flag = s["trade_em_curso"]

        # ── Pulse animado no indicador de status ──────────────────────────────
        if not hasattr(self, '_pulse_state'): self._pulse_state = 0
        self._pulse_state = (self._pulse_state + 1) % 6
        bright = self._pulse_state < 3

        if conectado:
            cor = NEON_GREEN if bright else GOLD
            self.lbl_status.config(text="●  Conectado", fg=cor)
        elif rodando:
            cor = PURPLE_LT if bright else PURPLE
            self.lbl_status.config(text="◌  Conectando...", fg=cor)
        else:
            self.lbl_status.config(text="●  Desconectado", fg=TEXT_RED)

        # ── Shimmer animado no botão INICIAR (quando parado) ─────────────────
        if not hasattr(self, '_shimmer_state'): self._shimmer_state = 0
        if not rodando:
            self._shimmer_state = (self._shimmer_state + 1) % 8
            shimmer_colors = [PURPLE, "#4a90e2", "#5ba0f0", PURPLE_LT,
                              "#5ba0f0", "#4a90e2", PURPLE, PURPLE]
            self.btn_iniciar.config(bg=shimmer_colors[self._shimmer_state])

        total = wins + losses
        wr = wins / total * 100 if total else 0

        self.lbl_saldo.config(text=f"R$ {saldo_atual:,.2f}")
        self.lbl_investido.config(text=f"R$ {inv_total:,.2f}")

        self.lbl_faturado.config(
            text=f"R$ {lucro:+,.2f}",
            fg=NEON_GREEN if lucro >= 0 else NEON_RED
        )
        self.lbl_wr.config(
            text=f"{wr:.0f}%",
            fg=NEON_GREEN if wr >= 60 else (GOLD if wr >= 50 else NEON_RED)
        )

        # ── Snapshot thread-safe da IA Suprema ────────────────────────────────
        ia_total = ia_taxa = 0
        if ia_suprema is not None:
            with ia_suprema._lock:
                ia_total = ia_suprema.total_pred
                ia_taxa  = ia_suprema.taxa_acerto

        modo     = "DEMO" if demo else "REAL"
        em_curso = 1 if em_curso_flag else 0
        pf       = estado.gross_profit / max(estado.gross_loss, 0.01)
        dd       = estado.max_drawdown
        wr_pct   = wins / total * 100 if total > 0 else 0
        if ia_total > 0:
            ia_wr  = ia_taxa * 100
            ia_txt = (f"[{modo}]  Trades:{total}  ✅{wins}  ❌{losses}  "
                      f"WR:{wr_pct:.0f}%  PF:{pf:.2f}  DD:R${dd:.2f}  IA:{ia_wr:.0f}%  ▶{em_curso}")
        else:
            ia_txt = (f"[{modo}]  Trades:{total}  ✅{wins}  ❌{losses}  "
                      f"WR:{wr_pct:.0f}%  PF:{pf:.2f}  DD:R${dd:.2f}  ▶{em_curso}")

        self.lbl_trades.config(text=ia_txt)

        self.after(1000, self._tick)

    # ── Iniciar / Parar bot ────────────────────────────────────────────────
    def _iniciar(self):
        # Validar valor inserido pelo usuário
        try:
            valor_usuario = float(self.valor_var.get().replace(",", ".").strip())
            if valor_usuario <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Valor inválido",
                                   "Digite um valor válido por operação.\nExemplo: 10 ou 50,00")
            return

        if valor_usuario < 1:
            messagebox.showwarning("Valor muito baixo",
                                   "O valor mínimo por operação é R$ 1,00.")
            return

        key = self.estrategia_sel.get()
        # Copiar estratégia e sobrescrever valor com o informado pelo usuário
        import copy
        estado.estrategia = copy.deepcopy(ESTRATEGIAS[key])
        estado.estrategia["valor"] = valor_usuario

        # Ajustar stops proporcionalmente ao valor
        estado.estrategia["stop_loss"] = -(valor_usuario * 6)
        estado.estrategia["stop_win"]  =  valor_usuario * 25

        estado.update(
            rodando=True, wins=0, losses=0,
            lucro=0.0, total_investido=0.0,
        )
        estado.trades_abertos = {}
        estado.cooldowns = {}

        self.btn_iniciar.config(state="disabled", text="⏳  Conectando...")
        self.btn_parar.config(state="normal")
        self.valor_entry.config(state="disabled")
        for k in self.cards_btn:
            self.cards_btn[k].config(cursor="")

        estado.demo = self.demo_mode.get()
        modo_str = "DEMO" if estado.demo else "CONTA REAL ⚠"
        self.log(f"Iniciando [{modo_str}] — {estado.estrategia['nome']} {estado.estrategia['emoji']}", "info")
        self.log(f"Valor por operação: R$ {valor_usuario:.2f}  |  Confiança: {estado.estrategia['confianca']:.0%}", "grey")
        self.log(f"Stop Loss: R$ {estado.estrategia['stop_loss']:.2f}  |  Stop Win: R$ {estado.estrategia['stop_win']:.2f}", "grey")

        # Executar bot em thread separada
        estado.thread = threading.Thread(target=self._run_bot_thread, daemon=True)
        estado.thread.start()

    def _parar(self):
        estado.update(rodando=False)
        self.btn_iniciar.config(state="normal", text="▶  INICIAR ROBÔ")
        self.btn_parar.config(state="disabled")
        self.valor_entry.config(state="normal")
        for k in self.cards_btn:
            self.cards_btn[k].config(cursor="hand2")
        self.log("Bot parado pelo usuário.", "warn")

    def _sair(self):
        estado.rodando = False
        self.controller.mostrar("TelaLogin")

    # ── Thread do bot ──────────────────────────────────────────────────────
    def _run_bot_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        estado.loop = loop
        try:
            loop.run_until_complete(self._bot_main())
        except Exception as e:
            self._ui_log(f"Erro fatal: {e}", "loss")
        finally:
            loop.close()
            estado.conectado = False
            self.after(0, lambda: self.btn_iniciar.config(
                state="normal", text="▶  INICIAR ROBÔ"))
            self.after(0, lambda: self.btn_parar.config(state="disabled"))
            self.after(0, lambda: self.valor_entry.config(state="normal"))

    # ══════════════════════════════════════════════════════════════════════
    #  MOTOR DE LEITURA INTRA-VELA — analisa a vela em formação tick a tick
    # ══════════════════════════════════════════════════════════════════════
    def _anatomia_vela(self, o, h, l, c):
        """Retorna métricas anatômicas da vela em formação."""
        corpo      = c - o
        amplitude  = h - l if h > l else 0.0001
        corpo_pct  = abs(corpo) / amplitude          # 0..1  corpo vs amplitude total
        sombra_sup = (h - max(o, c)) / amplitude     # sombra superior
        sombra_inf = (min(o, c) - l) / amplitude     # sombra inferior
        direcao    = "call" if corpo > 0 else ("put" if corpo < 0 else None)
        return {
            "corpo_pct":  corpo_pct,
            "sombra_sup": sombra_sup,
            "sombra_inf": sombra_inf,
            "direcao":    direcao,
            "amplitude":  amplitude,
            "corpo_abs":  abs(corpo),
        }

    def _sinal_intra_vela(self, anat, candles_hist, tempo_restante):
        """
        Decide se deve entrar agora com base na anatomia da vela em formação
        e no histórico. Retorna (direcao_entrada, forca, motivo) ou (None,0,'').

        Estratégias implementadas:
        1. REVERSÃO DE ESTICADA  — vela nasceu muito esticada (corpo > 70%),
           entra na direção OPOSTA esperando exaustão/fechamento contra.
        2. PIN BAR / REJEIÇÃO    — sombra longa (> 55%) de um lado e corpo
           pequeno do outro; entra na direção oposta à sombra.
        3. CONTINUAÇÃO DE IMPULSO — vela saudável (corpo 40-70%), sem sombras
           dominantes, histórico confirma tendência; entra na mesma direção.
        4. DOJI / INDECISÃO       — corpo < 10%, não entra.
        """
        cp   = anat["corpo_pct"]
        ss   = anat["sombra_sup"]
        si   = anat["sombra_inf"]
        dire = anat["direcao"]

        # ── 4. Doji — não entra ──────────────────────────────────────────
        if cp < 0.10:
            return None, 0.0, "Doji/indecisão — sem entrada"

        # ── 1. REVERSÃO DE ESTICADA ──────────────────────────────────────
        # Vela muito esticada nos últimos 30s da vida dela → exaustão iminente
        if cp > 0.72 and tempo_restante <= 30:
            entrada = "put" if dire == "call" else "call"
            forca   = 0.75 + (cp - 0.72) * 0.5   # aumenta com o esticamento
            motivo  = f"Reversão-esticada corpo={cp:.0%} → {entrada.upper()}"
            return entrada, min(forca, 0.95), motivo

        # ── 2. PIN BAR / REJEIÇÃO ────────────────────────────────────────
        # Sombra superior longa + corpo pequeno/baixo → rejeição de alta → PUT
        if ss > 0.55 and cp < 0.35:
            return "put", 0.78, f"PinBar-sup sombra={ss:.0%} → PUT"
        # Sombra inferior longa + corpo pequeno/alto → rejeição de baixa → CALL
        if si > 0.55 and cp < 0.35:
            return "call", 0.78, f"PinBar-inf sombra={si:.0%} → CALL"

        # ── 3. CONTINUAÇÃO DE IMPULSO ────────────────────────────────────
        # Corpo saudável (40-70%), confirmar com últimas 3 velas fechadas
        if 0.40 <= cp <= 0.72 and dire and len(candles_hist) >= 4:
            ult3 = candles_hist[-4:-1]
            mesma_dir = sum(
                1 for c in ult3
                if (dire == "call" and c["close"] > c["open"]) or
                   (dire == "put"  and c["close"] < c["open"])
            )
            if mesma_dir >= 2:
                forca = 0.65 + (mesma_dir / 3) * 0.20
                motivo = f"Impulso corpo={cp:.0%} ({mesma_dir}/3 velas confirmam) → {dire.upper()}"
                return dire, forca, motivo

        return None, 0.0, f"Sem padrão claro (corpo={cp:.0%})"

    async def _monitorar_vela_formando(self, client, ativo, candles_hist, cfg):
        """
        Monitora a vela em formação tick a tick (a cada 3s) e decide o
        melhor momento de entrada dentro da vela atual.
        Retorna (direcao, confianca, motivo) ou (None, 0, motivo).
        """
        import time as _t
        import datetime as _dt

        def _seg_prox_vela():
            agora = _dt.datetime.utcnow()
            seg = agora.second + agora.microsecond / 1e6
            return max(0.5, 60.0 - seg)

        tentativas_sem_sinal = 0
        ultimo_sinal         = None

        while True:
            espera = _seg_prox_vela()
            if espera < 3:
                return None, 0.0, "Tarde demais para entrar nesta vela"

            # Buscar preço atual da vela em formação
            try:
                raw = await asyncio.wait_for(
                    client.get_candles(ativo, _t.time(), 120, 60),
                    timeout=5.0
                )
                if not raw:
                    await asyncio.sleep(2)
                    continue
                c_atual = raw[-1]
                if isinstance(c_atual, dict):
                    o = float(c_atual.get("open",  0) or 0)
                    h = float(c_atual.get("high",  0) or 0)
                    l = float(c_atual.get("low",   0) or 0)
                    c = float(c_atual.get("close", 0) or 0)
                else:
                    o = float(getattr(c_atual, "open",  0) or 0)
                    h = float(getattr(c_atual, "high",  0) or 0)
                    l = float(getattr(c_atual, "low",   0) or 0)
                    c = float(getattr(c_atual, "close", 0) or 0)
            except Exception:
                await asyncio.sleep(2)
                continue

            if o == 0 or h == 0:
                await asyncio.sleep(2)
                continue

            anat = self._anatomia_vela(o, h, l, c)
            tempo_decorrido = 60 - espera   # segundos desde abertura da vela

            direcao_iv, forca_iv, motivo_iv = self._sinal_intra_vela(
                anat, candles_hist, espera
            )

            self._ui_log(
                f"  [intra-vela {ativo}] t={tempo_decorrido:.0f}s restam={espera:.0f}s "
                f"corpo={anat['corpo_pct']:.0%} ss={anat['sombra_sup']:.0%} "
                f"si={anat['sombra_inf']:.0%} → {motivo_iv}",
                "grey"
            )

            if direcao_iv:
                # Confirmar sinal repetido (2 leituras consecutivas)
                if ultimo_sinal == direcao_iv:
                    return direcao_iv, forca_iv, motivo_iv
                ultimo_sinal = direcao_iv
            else:
                ultimo_sinal = None
                tentativas_sem_sinal += 1
                if tentativas_sem_sinal >= 4:
                    return None, 0.0, "Sem padrão claro após múltiplas leituras"

            await asyncio.sleep(3)   # nova leitura a cada 3 segundos

    # ── Obter candles de um ativo ──────────────────────────────────────────
    async def _get_candles(self, client, ativo):
        try:
            import time as _time
            # assinatura: get_candles(asset, end_from_time, offset, period)
            raw = await asyncio.wait_for(
                client.get_candles(ativo, _time.time(), 2500, 60),  # ≈42 velas de 1min
                timeout=10.0
            )
            if not raw:
                return []
            candles = []
            for c in raw:
                if isinstance(c, dict):
                    o = float(c.get('open', 0) or 0)
                    h = float(c.get('high', 0) or 0)
                    l = float(c.get('low',  0) or 0)
                    cl= float(c.get('close',0) or 0)
                else:
                    o  = float(getattr(c, 'open',  0) or 0)
                    h  = float(getattr(c, 'high',  0) or 0)
                    l  = float(getattr(c, 'low',   0) or 0)
                    cl = float(getattr(c, 'close', 0) or 0)
                if cl > 0:
                    candles.append({'open': o, 'high': max(o,h,cl),
                                    'low': min(o,l,cl) if l > 0 else min(o,cl),
                                    'close': cl, 'volume': float(getattr(c,'volume',1) or 1)})
            return candles
        except asyncio.TimeoutError:
            return []
        except Exception:
            return []

    # ── Pontuação ajustada pelo aprendizado ────────────────────────────────
    def _confianca_ajustada(self, ativo, confianca_bruta):
        pen = estado.penalidade.get(ativo, 0.0)
        return confianca_bruta * (1.0 - pen * 0.25)

    # ── Varrer todos os ativos em PARALELO ────────────────────────────────
    async def _varrer_ativos(self, client, cfg, limite_minimo, candles_cache=None):
        """
        Analisa todos os ativos em paralelo.
        Se candles_cache (dict ativo→candles) for fornecido, pula o fetch de rede
        e usa os dados já pré-buscados — análise instantânea.
        """
        agora = time.time()

        # Filtro de horário: evitar 02-04 UTC (mercado absolutamente morto)
        utc_h = datetime.utcnow().hour
        if 2 <= utc_h <= 4:
            self._ui_log(f"  Mercado inativo ({utc_h:02d}:xx UTC). Aguardando abertura.", "grey")
            return None, None, 0.0, []

        ativos_livres = [
            a for a in ATIVOS
            if (agora - estado.cooldowns.get(a, 0)) >= 30
        ]

        if not ativos_livres:
            self._ui_log("  Todos os ativos em cooldown.", "grey")
            return None, None, 0.0, []

        # ── Buscar candles (rede) ou usar cache pré-buscado ──────────────────
        if candles_cache:
            resultados = [candles_cache.get(a, []) for a in ativos_livres]
        else:
            tasks = [self._get_candles(client, a) for a in ativos_livres]
            resultados = await asyncio.gather(*tasks, return_exceptions=True)

        candidatos = []
        linhas_log = []

        for ativo, candles in zip(ativos_livres, resultados):
            if isinstance(candles, Exception) or not candles:
                continue

            # ── IA Suprema: ensemble de 12 modelos (cérebros) ───────────────
            direcao, conf_bruta, votos = ia_suprema.analisar_supremo(candles, confianca_minima=0.20)
            if direcao is None:
                _, conf_bruta, votos = ia_suprema.analisar_supremo(candles, confianca_minima=0.0)
            if conf_bruta is None:
                conf_bruta = 0.0

            conf_aj = self._confianca_ajustada(ativo, conf_bruta)
            pen     = estado.penalidade.get(ativo, 0.0)
            risco   = ia_suprema.calcular_risco(candles)
            risco_str = "🔴" if risco > 0.7 else ("🟡" if risco > 0.4 else "🟢")

            # Log diagnóstico: por que a IA deu None?
            if direcao is None and conf_bruta > 0:
                self._ui_log(f"  [{ativo}] IA indecisa: conf={conf_bruta:.0%} (sem dominância suficiente)", "grey")

            # ── Extrair features para Quality + Confluence + Risk ──────────
            closes = [c['close'] for c in candles]
            r_val      = rsi(closes) or 50
            _, _, hist = macd_calc(closes)
            bu, bm, bl = bollinger(closes)
            adx_v, pdi_v, mdi_v = adx_calc(candles)
            st_v  = stoch(candles) or 50
            wr_v  = williams_r(candles) or -50
            cci_v = cci(candles) or 0
            vols  = [c.get('volume', 1) for c in candles]
            avg_v = sum(vols[:-1]) / max(len(vols)-1, 1)
            atr_v = (sum(
                max(abs(candles[i]['high'] - candles[i]['low']),
                    abs(candles[i]['high'] - candles[i-1]['close']),
                    abs(candles[i]['low']  - candles[i-1]['close']))
                for i in range(-14, 0)
            ) / 14) if len(candles) >= 15 else 0.0

            # Filtro ATR mínimo: 0.00002 aceita praticamente qualquer par com algum movimento.
            if atr_v < 0.00002:
                continue
            closes_5m  = [candles[i]['close'] for i in range(-1, -min(len(candles), 100) - 1, -5)][::-1]
            rsi_5m_v   = rsi(closes_5m) if len(closes_5m) >= 14 else r_val
            sma_5m_now = sum(closes_5m[-20:]) / 20 if len(closes_5m) >= 20 else (closes_5m[-1] if closes_5m else closes[-1])
            sma_5m_prv = sum(closes_5m[-21:-1]) / 20 if len(closes_5m) >= 21 else sma_5m_now
            obv   = sum(vols[i] if candles[i]['close'] > candles[i-1]['close'] else -vols[i] for i in range(1, len(candles)))
            bb_pos = ((closes[-1] - bl) / (bu - bl)) if bu and bl and (bu - bl) > 0 else 0.5
            sma20  = sum(closes[-20:]) / 20 if len(closes) >= 20 else closes[-1]
            sma20p = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else closes[-1]
            feats = {
                "rsi": r_val, "rsi_5m": rsi_5m_v,
                "rsi7": rsi_fast(closes) or 50,
                "macd_hist": hist or 0, "adx": adx_v or 0,
                "pdi": pdi_v or 0, "mdi": mdi_v or 0,
                "stoch_k": st_v, "wr": wr_v, "cci": cci_v,
                "bb_position": bb_pos, "vol_ratio": vols[-1] / avg_v if avg_v > 0 else 1.0,
                "obv_positive": obv > 0, "atr": atr_v,
                "sma_now": sma20, "sma_prev": sma20p,
                "sma_5m_now": sma_5m_now, "sma_5m_prev": sma_5m_prv,
            }
            # MACD rápido (5/13) para confluência
            _mfl, _mfs, _mfh = macd_fast(closes)
            if _mfh is not None:
                feats["macd_fast_hist"] = _mfh
                # histograma anterior (penúltimo)
                if len(closes) >= 15:
                    _mfl2, _mfs2, _mfh2 = macd_fast(closes[:-1])
                    if _mfh2 is not None:
                        feats["macd_fast_hist_prev"] = _mfh2
            # Bollinger Squeeze
            bbs = bollinger_squeeze(closes)
            if bbs:
                feats["bb_squeeze_dir"] = bbs

            qual     = quality_filters.calculate_quality_score(feats)
            conf_res = confluence_analyzer.analyze(feats)
            risk_res = risk_manager_adv.analyze_risk(feats, conf_bruta)

            qual_str  = f"Q:{qual:.0%}"
            conf_n    = conf_res["count"]
            risk_lv   = risk_res["risk_level"][0]   # L/M/H
            conf_ok   = conf_res["ok"]
            qual_ok   = qual >= 0.05   # gate mínimo relaxado: praticamente qualquer sinal passa

            # Log diagnóstico detalhado de confluência
            if direcao and not conf_ok:
                dom_force = conf_res.get("put_forca" if conf_res["dominante"] == "put" else "call_forca", 0)
                self._ui_log(
                    f"  [{ativo}] Confluência bloqueou: dom_force={dom_force:.2f} < {confluence_analyzer.MIN_CONFLUENCE_FORCE:.2f}  tipos={[c['tipo'] for c in conf_res['confluences'][:3]]}",
                    "grey"
                )

            # Filtro de confirmação: candle fortemente contra o sinal reduz confiança (-20%)
            # Não bloqueia mais — é um penalty para não desperdiçar bons candidatos
            if direcao:
                _lc  = candles[-1]
                _rng = _lc['high'] - _lc['low']
                if _rng > 0:
                    _br = (_lc['close'] - _lc['open']) / _rng
                    if (direcao == "call" and _br < -0.50) or (direcao == "put" and _br > 0.50):
                        conf_bruta *= 0.80   # candle muito contrário: desconto de 20%
                        conf_aj    *= 0.80

            barra   = "█" * int(conf_bruta * 10) + "░" * (10 - int(conf_bruta * 10))
            pen_str = f"[pen:{pen:.0%}]" if pen > 0 else ""
            seta    = ("↑CALL" if direcao == "call" else "↓PUT ") if direcao else "─────"
            ok_str  = "✅" if (conf_bruta >= limite_minimo and conf_ok and qual_ok) else "  "
            ia_str  = f"IA:{ia_suprema.taxa_acerto:.0%}" if ia_suprema.total_pred > 0 else "IA:novo"
            linhas_log.append(
                f"  {ok_str} {ativo:<14} [{barra}] {conf_bruta:.0%} {risco_str}R{risk_lv} {qual_str} C:{conf_n} {ia_str} {pen_str} {seta}"
            )

            if direcao:
                candidatos.append((ativo, direcao, conf_bruta, conf_aj, risco, candles,
                                   conf_ok, qual_ok, risk_res["risk_level"], votos))

        self._ui_log("─── IA Suprema v2 | 7 modelos | Scan paralelo ─────", "grey")
        for linha in linhas_log:
            self._ui_log(linha, "grey")

        if not candidatos:
            return None, None, 0.0, []

        # Descartar candidatos abaixo do piso de confiança ANTES de qualquer iteração
        candidatos = [c for c in candidatos if c[2] >= limite_minimo]

        if not candidatos:
            self._ui_log(f"  Nenhum candidato acima do piso {limite_minimo:.0%}.", "grey")
            return None, None, 0.0, []

        # Ordenar por: hot-score (% de wins recentes) × conf_aj — prioriza ativos quentes
        def _hot_score(c):
            hist = list(estado.historico_ativo.get(c[0], []))
            if not hist:
                return c[3] * 0.6   # ativo sem histórico recebe fator neutro
            wr = sum(1 for r in hist if r == 'W') / len(hist)
            return c[3] * (0.5 + wr * 0.7)   # conf × hot_weight (0.5 neutro, 1.2 se 100% wins)

        candidatos.sort(key=_hot_score, reverse=True)

        # Iterar em ordem de confiança e retornar o primeiro que passe todos os gates
        for cand in candidatos:
            ativo_m, direcao_m, conf_bruta_m, _, risco_m, candles_m, conf_ok_m, qual_ok_m, risk_lv_m, votos_m = cand

            if risk_lv_m == "HIGH" and conf_bruta_m < 0.80:
                conf_bruta_m *= 0.85   # risco alto sem confiança alta: desconto extra
                self._ui_log(f"  [{ativo_m}] Risco alto — confiança ajustada para {conf_bruta_m:.0%}.", "grey")
            if not conf_ok_m:
                conf_bruta_m *= 0.85   # confluência fraca: desconto de 15%
                self._ui_log(f"  [{ativo_m}] Confluência fraca — confiança ajustada para {conf_bruta_m:.0%}.", "grey")
                if conf_bruta_m < limite_minimo:
                    continue
            if not qual_ok_m:
                conf_bruta_m *= 0.90   # qualidade baixa: desconto leve, sem bloqueio
                self._ui_log(f"  [{ativo_m}] Qualidade baixa — confiança ajustada para {conf_bruta_m:.0%}.", "grey")

            # Lookback 10 velas: apenas penalidade leve — nunca bloqueia
            lb_ok, lb_score = ia_suprema.confirmar_lookback_10(candles_m, direcao_m)
            if not lb_ok:
                conf_bruta_m *= 0.90
                self._ui_log(f"  [{ativo_m}] Lookback fraco ({lb_score:.0%}) — confiança ajustada para {conf_bruta_m:.0%}.", "grey")

            # Verificar cooldown do monitor contínuo
            pode, motivo = monitor_continuo.pode_operar()
            if not pode:
                self._ui_log(f"  {motivo}", "grey")
                return None, None, 0.0, []

            explicacao = ia_suprema.gerar_explicacao(candles_m, direcao_m, conf_bruta_m)
            self._ui_log(f"  {explicacao}", "grey")
            return ativo_m, direcao_m, conf_bruta_m, votos_m

        self._ui_log("  Nenhum candidato passou todos os filtros.", "grey")
        return None, None, 0.0

    async def _bot_main(self):
        try:
            from pyquotex.stable_api import Quotex
        except ImportError:
            self._ui_log("pyquotex nao encontrado. Instale com: pip install pyquotex", "loss")
            return

        cfg            = estado.estrategia
        KEEPALIVE_INT  = 50    # segundos entre pings keep-alive

        def _segundos_para_proxima_vela():
            """Retorna quantos segundos faltam para o proximo fechamento de vela de 1 min."""
            import datetime as _dt
            agora = _dt.datetime.utcnow()
            seg_passados = agora.second + agora.microsecond / 1e6
            return max(0.5, 60.0 - seg_passados)

        def _log_relogio():
            import datetime as _dt
            t = _dt.datetime.utcnow()
            return t.strftime("%H:%M:%S UTC")

        async def _conectar(client):
            try:
                self._ui_log("  Conectando...", "grey")
                ok, reason = await asyncio.wait_for(client.connect(), timeout=120)
                if ok:
                    modo = "PRACTICE" if estado.demo else "REAL"
                    await client.change_account(modo)
                    return True
                self._ui_log(f"  Falhou: {reason}", "warn")
            except asyncio.TimeoutError:
                self._ui_log("  Timeout de conexao (25s)", "warn")
            except Exception as e:
                self._ui_log(f"  Erro de conexao: {e}", "warn")
            return False

        # ── Setup 2FA: captura codigo UMA VEZ e reutiliza nas tentativas ──────
        import threading as _threading
        import pyquotex.http.login as _login_mod
        import pathlib, configparser

        _codigo_2fa = [None]   # codigo guardado entre tentativas

        async def _awaiting_pin_gui(self_login, data, input_message):
            """Popup 2FA — abre somente na primeira vez, reutiliza o codigo depois."""
            if not _codigo_2fa[0]:
                _evt = _threading.Event()

                def _show_popup():
                    popup = tk.Toplevel()
                    popup.title("Verificacao de Seguranca")
                    popup.geometry("420x210")
                    popup.configure(bg=BG_DARK)
                    popup.resizable(False, False)
                    popup.grab_set()
                    tk.Label(popup, text="Verificacao de Seguranca",
                             font=("Segoe UI", 13, "bold"),
                             bg=BG_DARK, fg=TEXT_WHITE).pack(pady=(18, 2))
                    tk.Label(popup, text=input_message.strip(),
                             font=("Segoe UI", 10), bg=BG_DARK, fg=TEXT_GREY,
                             wraplength=380).pack(pady=(0, 10))
                    var = tk.StringVar()
                    entry = tk.Entry(popup, textvariable=var,
                                     font=("Segoe UI", 18, "bold"),
                                     bg=BG_INPUT, fg=TEXT_WHITE,
                                     insertbackground=TEXT_WHITE,
                                     relief="flat", justify="center", width=10)
                    entry.pack(ipady=8)
                    entry.focus_set()
                    def _confirmar(ev=None):
                        cod = var.get().strip()
                        if not cod.isdigit() or len(cod) < 4:
                            return
                        _codigo_2fa[0] = cod
                        popup.destroy()
                        _evt.set()
                    entry.bind("<Return>", _confirmar)
                    tk.Button(popup, text="Confirmar",
                              font=("Segoe UI", 11, "bold"),
                              bg=ACCENT, fg=TEXT_WHITE,
                              activebackground=PURPLE_LT,
                              relief="flat", cursor="hand2",
                              padx=20, pady=6,
                              command=_confirmar).pack(pady=10)

                try:
                    self.winfo_toplevel().after(0, _show_popup)
                except Exception:
                    _show_popup()

                self._ui_log("  Aguardando codigo 2FA do email...", "warn")
                # Aguarda sem limite de tempo ate o usuario digitar o codigo
                while not _evt.is_set():
                    await asyncio.sleep(0.3)
            else:
                self._ui_log("  Reutilizando codigo 2FA anterior.", "grey")

            codigo = _codigo_2fa[0] or ""
            self_login.headers["Content-Type"] = "application/x-www-form-urlencoded"
            self_login.headers["Referer"] = f"{self_login.full_url}/sign-in/modal"
            data["keep_code"] = 1
            data["code"]      = codigo
            await asyncio.sleep(3)   # aguarda servidor processar o codigo
            self_login.send_request(
                method="POST",
                url=f"{self_login.full_url}/sign-in/modal",
                data=data
            )

        _login_mod.Login.awaiting_pin = _awaiting_pin_gui

        try:
            # Gravar credenciais no config.ini que o pyquotex le
            cfg_path = pathlib.Path("settings/config.ini")
            cfg_path.parent.mkdir(exist_ok=True, parents=True)
            cfg_ini = configparser.ConfigParser(interpolation=None)
            cfg_ini["settings"] = {"email": estado.email, "password": estado.senha}
            with open(cfg_path, "w", encoding="utf-8") as f:
                cfg_ini.write(f)

            client = Quotex(email=estado.email, password=estado.senha, lang="pt")
            estado.client = client
            self._ui_log("Conectando a Quotex...", "grey")

            if not await _conectar(client):
                self._ui_log("Falha na conexao apos 3 tentativas.", "loss")
                return

            saldo = await client.get_balance()
            estado.update(conectado=True, saldo_atual=saldo)
            estado.saldo_inicial = saldo
            tipo = "DEMO" if estado.demo else "REAL"
            self._ui_log(f"Conectado! [{tipo}] Saldo: R$ {saldo:,.2f}", "win")
            self._ui_log(f"Meta: >={estado.conf_floor:.0%} | Velas: 1 min | Keep-alive: {KEEPALIVE_INT}s", "info")

            ultimo_keepalive  = time.time()
            scan_num          = 0
            ultimo_log_espera = -1   # evita spam do countdown
            _candles_prefetch = {}   # cache de pré-fetch por ciclo de vela

            while estado.rodando:
                # ── Stops ──────────────────────────────────────────────────
                if estado.lucro <= cfg["stop_loss"]:
                    self._ui_log(f"STOP LOSS: R$ {estado.lucro:,.2f}", "loss")
                    break
                if estado.lucro >= cfg["stop_win"]:
                    self._ui_log(f"STOP WIN: R$ {estado.lucro:,.2f}", "win")
                    break

                # ── Aguardar trade em curso ─────────────────────────────────
                if estado.trade_em_curso:
                    await asyncio.sleep(1)
                    continue

                # ── Keep-alive: manter sessao sem expirar ──────────────────
                agora = time.time()
                if agora - ultimo_keepalive >= KEEPALIVE_INT:
                    try:
                        saldo = await client.get_balance()
                        estado.update(saldo_atual=saldo)
                        ultimo_keepalive   = agora
                        self._ui_log(f"Sessao ativa | Saldo: R$ {saldo:,.2f}", "grey")
                    except Exception:
                        self._ui_log("Conexao perdida. Reconectando...", "warn")
                        estado.update(conectado=False)
                        if await _conectar(client):
                            estado.update(conectado=True)
                            ultimo_keepalive = time.time()
                            self._ui_log("Reconectado com sucesso.", "win")
                        else:
                            self._ui_log("Falha ao reconectar. Encerrando.", "loss")
                            break

                # ══ SINCRONIZAR COM FECHAMENTO DA VELA DE 1 MINUTO ══════════
                # Analisa nos últimos 15s da vela para ter tempo de varrer todos os ativos
                espera = _segundos_para_proxima_vela()

                if espera > 20:
                    espera_int = int(espera)
                    if abs(espera_int - ultimo_log_espera) >= 8:
                        self._ui_log(f"  Proxima vela em {espera_int}s ({_log_relogio()})", "grey")
                        ultimo_log_espera = espera_int

                    # ── PRÉ-FETCH: busca candles em background quando faltam 35s ──
                    # Assim quando chegar nos 20s, a análise é instantânea
                    if 20 < espera <= 37 and not _candles_prefetch:
                        self._ui_log("  [pré-fetch] baixando candles...", "grey")
                        _tasks_pf = [self._get_candles(client, a) for a in ATIVOS]
                        _res_pf   = await asyncio.gather(*_tasks_pf, return_exceptions=True)
                        _candles_prefetch = {
                            a: c for a, c in zip(ATIVOS, _res_pf)
                            if not isinstance(c, Exception) and c
                        }
                        self._ui_log(f"  [pré-fetch] {len(_candles_prefetch)} ativos prontos.", "grey")

                    aguardar = min(espera - 19.0, 2.0)
                    await asyncio.sleep(max(0.3, aguardar))
                    continue

                # ── Nos últimos 15s da vela: análise com dados pré-buscados ──────
                scan_num += 1
                alvo_conf = max(0.28, estado.conf_floor)
                self._ui_log(
                    f"[ Scan #{scan_num} | vela fecha em {espera:.1f}s | {_log_relogio()} | piso={alvo_conf:.0%} ]",
                    "info"
                )

                # Usa cache do pré-fetch (sem espera de rede)
                ativo, direcao, conf, votos = await self._varrer_ativos(
                    client, cfg, alvo_conf,
                    candles_cache=_candles_prefetch if _candles_prefetch else None
                )
                _candles_prefetch = {}   # limpa cache após uso

                if not ativo or not direcao:
                    self._ui_log("  Mercado indeciso — aguardando próxima vela.", "grey")
                    await asyncio.sleep(max(1.0, _segundos_para_proxima_vela() + 1.0))
                    continue

                qualidade = "OK" if conf >= alvo_conf else "FB"
                self._ui_log(
                    f"[{qualidade}] {ativo} {direcao.upper()} {conf:.0%}",
                    "win" if conf >= alvo_conf else "warn"
                )

                # ══ LEITURA INTRA-VELA: monitora a vela em formação ══════════
                # Aguarda o início da nova vela (+1s de margem) e depois lê tick a tick
                espera_nova_vela = _segundos_para_proxima_vela()
                if espera_nova_vela > 1.5:
                    self._ui_log(
                        f"  Aguardando nova vela em {espera_nova_vela:.1f}s para leitura intra-vela...",
                        "grey"
                    )
                    await asyncio.sleep(espera_nova_vela + 0.8)

                # Buscar histórico atualizado para o monitor intra-vela
                candles_iv = await self._get_candles(client, ativo)

                self._ui_log(f"  [intra-vela] Iniciando leitura tick a tick de {ativo}...", "info")
                dir_iv, conf_iv, motivo_iv = await self._monitorar_vela_formando(
                    client, ativo, candles_iv, cfg
                )

                if dir_iv:
                    # Sinal intra-vela encontrado — usar como direção final
                    self._ui_log(f"  [intra-vela] {motivo_iv}", "win")
                    # Mesclar confiança: 60% intra-vela + 40% ensemble histórico
                    conf   = conf_iv * 0.60 + conf * 0.40
                    direcao = dir_iv
                else:
                    # Sem sinal intra-vela: usar direção do ensemble, entrar agora
                    self._ui_log(
                        f"  [intra-vela] {motivo_iv} — usando sinal do ensemble ({direcao.upper()})",
                        "grey"
                    )

                # ── Executar operacao ───────────────────────────────────────
                tag      = "call" if direcao == "call" else "put"
                seta_str = "CALL" if direcao == "call" else "PUT "

                # Verificar se o ativo esta aberto; trocar para _otc se necessario
                try:
                    ativo_real, asset_info = await client.get_available_asset(ativo, force_open=True)
                    if not asset_info or not asset_info[2]:
                        self._ui_log(f"  Ativo {ativo} fechado/indisponivel. Pulando.", "warn")
                        estado.cooldowns[ativo] = time.time()
                        continue
                    if ativo_real != ativo:
                        self._ui_log(f"  Ativo ajustado: {ativo} -> {ativo_real}", "grey")
                        ativo = ativo_real
                except Exception as e_asset:
                    self._ui_log(f"  Erro ao verificar ativo {ativo}: {e_asset}", "warn")
                    continue

                # ── Log supremo: cérebros que votaram ───────────────────────
                if votos:
                    self._ui_log(
                        f"  🧠 Cérebros: {' | '.join(votos[:5])}{'...' if len(votos) > 5 else ''}",
                        "grey"
                    )

                # ── Kelly Criterion: ajuste de tamanho de posição ───────────
                wr_atual = estado.wins / max(estado.wins + estado.losses, 1)
                kelly_f  = kelly_criterion(wr_atual, payout=0.80)
                # Mapeia Kelly(0..0.25) → fator (0.6..1.4) do valor base
                kelly_fator = 0.6 + (kelly_f / 0.25) * 0.8 if kelly_f > 0 else 0.6
                valor_kelly = round(
                    max(1.0,                       # mínimo absoluto Quotex = R$ 1,00
                        max(cfg["valor"] * 0.5,
                            min(cfg["valor"] * 1.5, cfg["valor"] * kelly_fator))
                    ), 2
                )

                self._ui_log(
                    f">>> {seta_str} {ativo:<14} | R$ {valor_kelly:.2f} "
                    f"[Kelly {kelly_f:.0%}] | {conf:.0%} | 1 min",
                    tag
                )

                try:
                    ok, data = await client.buy(
                        valor_kelly, ativo, direcao, cfg["duracao"], "TIME"
                    )
                except Exception as e:
                    self._ui_log(f"Erro ao abrir trade: {e}", "loss")
                    continue

                if not ok:
                    motivo = str(data) if data else "sem resposta"
                    self._ui_log(f"Trade rejeitado [{ativo}]: {motivo}", "warn")
                    # Cooldown curto para nao repetir imediatamente
                    estado.cooldowns[ativo] = time.time() - 40
                    continue

                tid        = data.get("id", "unknown")
                lucro_pot  = data.get("profit", 0)
                estado.trades_abertos[tid] = {
                    "ativo":     ativo,
                    "direcao":   direcao,
                    "valor":     cfg["valor"],
                    "lucro_pot": lucro_pot,
                    "confianca": conf
                }
                estado.update(total_investido=estado.total_investido + cfg["valor"],
                              trade_em_curso=True)
                estado.cooldowns[ativo] = time.time()
                ultimo_keepalive        = time.time()
                monitor_continuo.registrar_trade(direcao, cfg["valor"])

                asyncio.create_task(
                    self._aguardar_resultado(client, tid, cfg["duracao"])
                )

            try:
                await client.close()
            except Exception:
                pass
            estado.update(conectado=False, trade_em_curso=False)
            self._ui_log("Sessao encerrada.", "info")

            with estado._lock:
                total = estado.wins + estado.losses
                wr    = estado.wins / total * 100 if total else 0
                lucro_final = estado.lucro
                wins_final  = estado.wins
                losses_final= estado.losses
            self._ui_log(
                f"Resultado: R$ {lucro_final:+,.2f} | WR: {wr:.0f}% ({wins_final}W/{losses_final}L)",
                "win" if lucro_final >= 0 else "loss"
            )

        except Exception as e:
            self._ui_log(f"Erro: {e}", "loss")
            estado.update(conectado=False, trade_em_curso=False)


    async def _aguardar_resultado(self, client, tid, duracao):
        await asyncio.sleep(duracao + 6)
        try:
            ganhou = await client.check_win(tid)
            t = estado.trades_abertos.pop(tid, {})
            ativo = t.get('ativo', '')

            if ganhou:
                res = t.get('lucro_pot', 0)
                tag, label = "win", "WIN ✅"
                pen_atual = estado.penalidade.get(ativo, 0.0)
                estado.penalidade[ativo] = max(0.0, pen_atual * 0.70)
                if ativo not in estado.historico_ativo:
                    estado.historico_ativo[ativo] = deque(maxlen=20)
                estado.historico_ativo[ativo].append('W')
                estado.update(wins=estado.wins + 1)
                estado.conf_floor    = max(0.40, estado.conf_floor - 0.02)
                estado.losses_consec = 0
                # Métricas avançadas
                estado.gross_profit += res
                estado.peak_lucro    = max(estado.peak_lucro, estado.lucro + res)
                direcao_trade = t.get('direcao', '')
                ia_suprema.aprender(direcao_trade, direcao_trade, t.get('confianca', 0.5))
                monitor_continuo.registrar_resultado(True, res, t.get('valor', 0))
            else:
                res = -t.get('valor', 0)
                tag, label = "loss", "LOSS ❌"
                pen_atual = estado.penalidade.get(ativo, 0.0)
                estado.penalidade[ativo] = min(0.90, pen_atual + 0.35)
                estado.losses_consec += 1
                estado.conf_floor     = min(0.65, estado.conf_floor + 0.04)
                if ativo not in estado.historico_ativo:
                    estado.historico_ativo[ativo] = deque(maxlen=20)
                estado.historico_ativo[ativo].append('L')
                estado.update(losses=estado.losses + 1)
                # Métricas avançadas
                estado.gross_loss += abs(res)
                drawdown_atual    = estado.peak_lucro - (estado.lucro + res)
                if drawdown_atual > estado.max_drawdown:
                    estado.max_drawdown = drawdown_atual
                dir_pred = t.get('direcao', '')
                dir_real = 'put' if dir_pred == 'call' else 'call'
                ia_suprema.aprender(dir_pred, dir_real, t.get('confianca', 0.5))
                monitor_continuo.registrar_resultado(False, 0, t.get('valor', 0))

                # Lock-out severo: 3+ losses consecutivos neste ativo → bloqueia 5min
                consec_loss = 0
                for r in reversed(list(estado.historico_ativo.get(ativo, []))):
                    if r == 'L': consec_loss += 1
                    else: break
                if consec_loss >= 3:
                    estado.penalidade[ativo] = 0.90
                    estado.cooldowns[ativo]  = time.time() + 300  # bloqueia 5min extra
                    self._ui_log(f"  BLOQUEADO {ativo} por 5min ({consec_loss} losses consecutivos)", "loss")

            estado.update(lucro=estado.lucro + res, trade_em_curso=False)

            total = estado.wins + estado.losses
            wr = estado.wins / total * 100 if total else 0

            pen = estado.penalidade.get(ativo, 0.0)
            pen_str = f" | penalidade {ativo}: {pen:.0%}" if pen > 0 else ""
            self._ui_log(
                f"[{label}] {ativo} {t.get('direcao','').upper()}"
                f" | R$ {res:+.2f} | Sessão: R$ {estado.lucro:+.2f} | WR: {wr:.0f}%{pen_str}",
                tag
            )

            # Avisar se ativo está sendo evitado
            if pen >= 0.40:
                hist = list(estado.historico_ativo.get(ativo, []))
                consec_loss = 0
                for r in reversed(hist):
                    if r == 'L':
                        consec_loss += 1
                    else:
                        break
                self._ui_log(
                    f"⚠️  {ativo} penalizado ({pen:.0%}) — {consec_loss} loss(es) seguidos. Score ajustado.",
                    "warn"
                )

        except Exception as e:
            estado.trades_abertos.pop(tid, None)
            estado.trade_em_curso = False
            self._ui_log(f"Erro resultado {str(tid)[:8]}: {e}", "warn")

    def _ui_log(self, msg, tag="info"):
        self.after(0, lambda m=msg, t=tag: self.log(m, t))


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = App()
    app.mainloop()

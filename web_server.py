"""
Profit Cash — Servidor Web (Deriv API + Multi-User Auth)
"""
import asyncio, json, os, sqlite3, sys, threading, time, uuid, math, hashlib, hmac
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, jsonify, request,
                   session, redirect, url_for)
from flask_sock import Sock
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app  = Flask(__name__, template_folder="templates")

# SECRET_KEY fixo é obrigatório para sessions persistirem entre restarts.
# No Railway, defina SECRET_KEY como variável de ambiente (qualquer string longa).
_raw_key = os.environ.get("SECRET_KEY", "")
if not _raw_key:
    # Fallback: gera e persiste em arquivo local (funciona em dev/Railway com volume)
    _key_file = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(_key_file):
        _raw_key = open(_key_file).read().strip()
    else:
        _raw_key = uuid.uuid4().hex + uuid.uuid4().hex
        try:
            open(_key_file, "w").write(_raw_key)
        except Exception:
            pass
app.secret_key = _raw_key or uuid.uuid4().hex
app.permanent_session_lifetime = timedelta(days=60)  # sessão dura 60 dias sem logout

sock = Sock(app)

# Deriv OAuth — configure DERIV_APP_ID no Railway (env var)
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "")
DERIV_WS     = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID or '1089'}"

# ── Ticto / Assinatura ───────────────────────────────────────────────────────
# TICTO_MODE=open  → todos têm acesso (desenvolvimento / testes)
# TICTO_MODE=strict → só quem comprou na Ticto tem acesso
TICTO_MODE           = os.environ.get("TICTO_MODE", "open")
TICTO_WEBHOOK_SECRET = os.environ.get("TICTO_WEBHOOK_SECRET", "")   # chave de postback da Ticto
TICTO_PRODUCT_ID     = os.environ.get("TICTO_PRODUCT_ID", "")        # filtrar produto específico (opcional)
TICTO_COURSE_URL     = os.environ.get("TICTO_COURSE_URL", "")         # link de compra do curso
TICTO_DAYS           = int("".join(c for c in os.environ.get("TICTO_DAYS", "180") if c.isdigit()) or "180")

# ── MARKET_MODE ──────────────────────────────────────────────────────────────
# "forex"     → pares reais na Deriv (frx*). Preço externo e notícia têm efeito real.
# "synthetic" → Volatility Indices (R_*). RNG da própria Deriv, 24/7, sem correlação
#               com mercado real — rollback instantâneo (restart, sem redeploy de código)
#               se algo falhar em produção no modo forex.
MARKET_MODE = os.environ.get("MARKET_MODE", "forex")

# Tamanho do candle agregado (segundos) usado pelas funções de sinal em modo forex.
# Em sintéticos a taxa de chegada de tick é estável (~1/s), então período fixo em
# nº de ticks sempre representa o mesmo tempo de parede. Em forex real essa taxa
# varia (Ásia calma vs. London/NY overlap), então as 3 funções de sinal passam a
# consumir candle_buf (closes agregados por tempo) em vez de tick_buf cru.
CANDLE_SECONDS = int(os.environ.get("CANDLE_SECONDS", "30"))

ASSETS_BY_MODE = {
    "synthetic": ["R_75", "R_100", "R_50", "R_25", "R_10"],
    "forex":     ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxAUDUSD"],
}
ASSET_NAMES_BY_MODE = {
    "synthetic": {
        "R_75":  "Volatility 75",
        "R_100": "Volatility 100",
        "R_50":  "Volatility 50",
        "R_25":  "Volatility 25",
        "R_10":  "Volatility 10",
    },
    "forex": {
        "frxEURUSD": "EUR/USD",
        "frxGBPUSD": "GBP/USD",
        "frxUSDJPY": "USD/JPY",
        "frxAUDUSD": "AUD/USD",
    },
}
STRATEGIES_BY_MODE = {
    "synthetic": {
        # duracao: em TICKS (duracao_unit: "t")
        # 5 ticks ≈ 5 segundos nos índices sintéticos da Deriv
        # Deriv não aceita duration_unit "s" < 15s — ticks é o menor contrato disponível
        "cautelosa": {"duracao": 5, "duracao_unit": "t", "rsi_upper": 75, "rsi_lower": 25, "conf_min": 0.78, "stop_diario_pct": 0.30},
        "moderada":  {"duracao": 5, "duracao_unit": "t", "rsi_upper": 70, "rsi_lower": 30, "conf_min": 0.70, "stop_diario_pct": 0.30},
        "agressiva": {"duracao": 5, "duracao_unit": "t", "rsi_upper": 65, "rsi_lower": 35, "conf_min": 0.62, "stop_diario_pct": 0.30},
    },
    "forex": {
        # duracao: em SEGUNDOS (duracao_unit: "s") — placeholder conservador.
        # Fase 2 (contracts_for) descobre dinamicamente a duração real disponível
        # por símbolo e sobrescreve isso por-ativo em runtime.
        "cautelosa": {"duracao": 60, "duracao_unit": "s", "rsi_upper": 75, "rsi_lower": 25, "conf_min": 0.78, "stop_diario_pct": 0.30},
        "moderada":  {"duracao": 60, "duracao_unit": "s", "rsi_upper": 70, "rsi_lower": 30, "conf_min": 0.70, "stop_diario_pct": 0.30},
        "agressiva": {"duracao": 60, "duracao_unit": "s", "rsi_upper": 65, "rsi_lower": 35, "conf_min": 0.62, "stop_diario_pct": 0.30},
    },
}

ASSETS      = ASSETS_BY_MODE[MARKET_MODE]
ASSET_NAMES = ASSET_NAMES_BY_MODE[MARKET_MODE]
STRATEGIES  = STRATEGIES_BY_MODE[MARKET_MODE]


# ════════════════════════════════════════════════════════
#  BANCO DE DADOS
# ════════════════════════════════════════════════════════
DB_PATH = os.path.join(BASE_DIR, "users.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active     INTEGER DEFAULT 1,
                is_admin      INTEGER DEFAULT 0,
                deriv_token   TEXT DEFAULT '',
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrações para bancos antigos (novas colunas adicionadas ao longo do tempo)
        for migration in [
            "ALTER TABLE users ADD COLUMN deriv_token         TEXT    DEFAULT ''",
            "ALTER TABLE users ADD COLUMN ticto_authorized    INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN ticto_expires_at    TEXT",
            "ALTER TABLE users ADD COLUMN ticto_transaction_id TEXT",
        ]:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # coluna já existe
        conn.commit()

    # ── Admin principal (fixo) ───────────────────────────────────────────────
    # Credenciais do dono do sistema — sempre garante acesso admin
    _OWNER_EMAIL = "joaoedaltonsudou@gmail.com"
    _OWNER_PASS  = os.environ.get("OWNER_PASSWORD", "@Salugo10!")
    _OWNER_USER  = "joao"
    try:
        with get_db() as conn:
            exists = conn.execute(
                "SELECT id FROM users WHERE email=?", (_OWNER_EMAIL,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO users (username,email,password_hash,is_admin,is_active,"
                    "ticto_authorized) VALUES (?,?,?,1,1,1)",
                    (_OWNER_USER, _OWNER_EMAIL, generate_password_hash(_OWNER_PASS))
                )
            else:
                # Garantir que sempre seja admin e ativo, mesmo se alterado
                conn.execute(
                    "UPDATE users SET is_admin=1, is_active=1, ticto_authorized=1 WHERE email=?",
                    (_OWNER_EMAIL,)
                )
            conn.commit()
    except Exception as e:
        print(f"[INIT] Aviso ao criar owner: {e}", flush=True)

    # ── Admin via variáveis de ambiente (opcional, para outros admins) ────────
    admin_user = os.environ.get("ADMIN_USERNAME", "")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    admin_email= os.environ.get("ADMIN_EMAIL", "")
    if admin_user and admin_pass:
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO users "
                    "(username, email, password_hash, is_admin, ticto_authorized) VALUES (?,?,?,1,1)",
                    (admin_user,
                     admin_email or f"{admin_user}@admin.local",
                     generate_password_hash(admin_pass))
                )
                conn.commit()
        except Exception:
            pass


# ════════════════════════════════════════════════════════
#  DECORATORS DE AUTENTICAÇÃO
# ════════════════════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if not session.get("is_admin"):
            return "Acesso negado", 403
        return f(*args, **kwargs)
    return decorated


# ════════════════════════════════════════════════════════
#  CONTROLE DE ASSINATURA (TICTO)
# ════════════════════════════════════════════════════════
def check_subscription(user_id: int, is_admin: bool = False) -> tuple[bool, str]:
    """
    Retorna (permitido, motivo).
    motivo: 'open' | 'admin' | 'ok' | 'not_authorized' | 'expired'
    """
    if TICTO_MODE != "strict":
        return True, "open"
    if is_admin:
        return True, "admin"
    with get_db() as conn:
        row = conn.execute(
            "SELECT ticto_authorized, ticto_expires_at FROM users WHERE id=?",
            (user_id,)
        ).fetchone()
    if not row or not row["ticto_authorized"]:
        return False, "not_authorized"
    if row["ticto_expires_at"]:
        try:
            expiry = datetime.fromisoformat(row["ticto_expires_at"])
            if expiry < datetime.utcnow():
                return False, "expired"
        except ValueError:
            pass
    return True, "ok"


def subscription_required(f):
    """Decorator: bloqueia rota se usuário não tiver assinatura ativa."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "Login necessário"}), 401
        allowed, reason = check_subscription(
            session["user_id"], session.get("is_admin", False)
        )
        if not allowed:
            return jsonify({
                "ok": False,
                "error": "subscription_required",
                "reason": reason,
                "course_url": TICTO_COURSE_URL,
            }), 403
        return f(*args, **kwargs)
    return decorated


# ════════════════════════════════════════════════════════
#  SESSÕES DO BOT
# ════════════════════════════════════════════════════════
class SessionState:
    def __init__(self, sid):
        self.id          = sid
        self.ws_clients  = []
        self.ws_lock     = threading.Lock()
        self.stop_evt    = threading.Event()
        self.bot_thread  = None
        self.estado_snap = {
            "saldo": 0.0, "lucro": 0.0,
            "wins": 0,    "losses": 0,
            "rodando": False, "conectado": False,
        }

    def broadcast(self, msg: dict):
        data = json.dumps(msg, ensure_ascii=False, default=str)
        with self.ws_lock:
            dead = []
            for ws in self.ws_clients:
                try:    ws.send(data)
                except: dead.append(ws)
            for d in dead:
                self.ws_clients.remove(d)

    def log(self, msg, tag="info"):
        self.broadcast({"type": "log", "text": msg, "tag": tag})
        print(f"[{self.id[:6]}][{tag.upper():4s}] {msg}", flush=True)

    def update_estado(self, **kw):
        self.estado_snap.update(kw)
        self.broadcast({"type": "estado", "data": dict(self.estado_snap)})

    def sinal(self, ativo, action, conf, motivo="", votos=None):
        self.broadcast({"type": "sinal", "ativo": ativo, "action": action,
                        "conf": round(conf, 4), "motivo": motivo,
                        "votos": votos or []})

    def trade(self, tid, ativo, direcao, valor):
        self.broadcast({"type": "trade", "id": str(tid),
                        "ativo": ativo, "direcao": direcao, "valor": valor})

    def resultado(self, tid, resultado, lucro, lucro_brl=0):
        self.broadcast({"type": "resultado", "id": str(tid),
                        "resultado": resultado, "lucro": round(lucro, 2),
                        "lucro_brl": round(lucro_brl, 2)})


_sessions: dict[str, SessionState] = {}
_sessions_lock = threading.Lock()


def get_or_create_session(sid: str) -> SessionState:
    with _sessions_lock:
        if sid not in _sessions:
            _sessions[sid] = SessionState(sid)
        return _sessions[sid]


# ════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS — v3 (Reversão RSI + Stochastic + BB obrigatório)
# ════════════════════════════════════════════════════════
def calc_rsi(prices: list, period: int = 14) -> float:
    """RSI de Wilder — suavização exponencial."""
    if len(prices) < period + 1:
        return 50.0
    changes  = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains    = [max(c, 0.0) for c in changes]
    losses   = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def calc_ema(prices: list, period: int) -> float:
    """EMA — reage mais rápido que SMA."""
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1.0 - k)
    return ema


def calc_bb(prices: list, period: int = 20, num_std: float = 2.0):
    """Bollinger Bands — retorna (mid, upper, lower)."""
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mid    = sum(window) / period
    std    = (sum((p - mid) ** 2 for p in window) / period) ** 0.5
    return mid, mid + num_std * std, mid - num_std * std


def detect_bb_squeeze_signal(prices: list, period: int = 20, dev: float = 2.0):
    """
    Sinal de Bollinger Bands Squeeze + Breakout (lógica do Trade AI).

    Como funciona:
    1. SQUEEZE: compara bandwidth atual (BB de 20 ticks) com bandwidth
       de uma janela 3x maior. Se a banda atual é ≤ 65% da normal → squeeze.
    2. BREAKOUT: preço toca ou cruza uma das bandas após o squeeze.
       - Preço >= upper  → CALL (breakout para cima)
       - Preço <= lower  → PUT  (breakout para baixo)
       - Próximo à banda (dentro de 15% do half-range) + tendência → antecipa

    Retorna: ('CALL' | 'PUT' | None, squeeze_ratio)
      squeeze_ratio: 0.0–1.0, quanto menor mais forte o squeeze
                     (ex: 0.40 = banda atual é 40% da normal)
    """
    needed = period * 3 + 5
    if len(prices) < needed:
        return None, 0.0

    # BB janela atual (20 ticks)
    mid_now, upper_now, lower_now = calc_bb(prices[-period:], period, dev)
    if not mid_now or mid_now == 0:
        return None, 0.0
    bw_now = (upper_now - lower_now) / mid_now   # bandwidth normalizado

    # BB janela histórica (60 ticks) — referência de "volatilidade normal"
    mid_hist, upper_hist, lower_hist = calc_bb(prices[-(period * 3):], period * 3, dev)
    if not mid_hist or mid_hist == 0:
        return None, 0.0
    bw_hist = (upper_hist - lower_hist) / mid_hist

    # Squeeze: banda atual comprimida em relação à normal
    if bw_hist == 0:
        return None, 0.0
    squeeze_ratio = bw_now / bw_hist   # < 0.65 = squeeze

    if squeeze_ratio > 0.65:
        return None, squeeze_ratio     # sem squeeze, não operar

    # Breakout: preço tocou ou cruzou a banda
    price = prices[-1]

    if price >= upper_now:
        return "CALL", squeeze_ratio
    if price <= lower_now:
        return "PUT", squeeze_ratio

    # Iminente: preço dentro de 15% do half-range da banda + tendência confirmando
    half_range = (upper_now - lower_now) / 2.0
    if half_range > 0:
        near_upper = (upper_now - price) / half_range
        near_lower = (price - lower_now) / half_range
        trend_up   = prices[-1] > prices[-6] if len(prices) >= 6 else False

        if near_upper < 0.15 and trend_up:
            return "CALL", squeeze_ratio
        if near_lower < 0.15 and not trend_up:
            return "PUT", squeeze_ratio

    return None, squeeze_ratio


def calc_trend(prices: list, fast: int = 10, slow: int = 30) -> str:
    """
    Determina a tendência dominante comparando EMA rápida vs EMA lenta.

    Retorna:
      'UP'      → tendência de alta  (só operar CALL)
      'DOWN'    → tendência de baixa (só operar PUT)
      'NEUTRAL' → lateral, sem operar
    """
    if len(prices) < slow + 5:
        return "NEUTRAL"
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    diff_pct = (ema_fast - ema_slow) / ema_slow if ema_slow else 0.0
    # Exige diferença mínima de 0.005% para considerar tendência definida
    if diff_pct > 0.00005:
        return "UP"
    if diff_pct < -0.00005:
        return "DOWN"
    return "NEUTRAL"


def check_3candle_confirm(prices: list, direction: str,
                          candle_size: int = 5) -> bool:
    """
    Verifica se as últimas 3 micro-velas confirmam a direção.

    Cada micro-vela = `candle_size` ticks (padrão 5).
    Confirmação:
      CALL → as 3 últimas velas fecharam ACIMA da abertura (bullish)
      PUT  → as 3 últimas velas fecharam ABAIXO da abertura (bearish)

    Retorna True se as 3 velas confirmam, False caso contrário.
    """
    needed = candle_size * 3 + 1
    if len(prices) < needed:
        return False

    confirmed = 0
    for i in range(3):
        # Vela mais recente = i=0, anterior = i=1, etc.
        end   = len(prices) - i * candle_size
        start = end - candle_size
        if start < 0:
            return False
        open_p  = prices[start]
        close_p = prices[end - 1]
        if direction == "CALL" and close_p > open_p:
            confirmed += 1
        elif direction == "PUT" and close_p < open_p:
            confirmed += 1
        else:
            break   # vela contrária → falha imediata

    return confirmed == 3


def calc_stoch(prices: list, k_period: int = 14, d_period: int = 3):
    """
    Stochastic Oscillator (%K e %D).
    %K: posição do preço atual dentro do range high-low dos últimos k_period ticks.
    %D: média móvel de %K (sinal suavizado).
    """
    if len(prices) < k_period + d_period:
        return 50.0, 50.0
    k_series = []
    for offset in range(d_period - 1, -1, -1):
        window = prices[-(k_period + offset): len(prices) - offset if offset > 0 else None]
        lo, hi = min(window), max(window)
        price  = prices[-(offset + 1)] if offset > 0 else prices[-1]
        k_series.append(50.0 if hi == lo else (price - lo) / (hi - lo) * 100.0)
    k_now = k_series[-1]
    d_now = sum(k_series) / len(k_series)
    return k_now, d_now


def calc_williams_r(prices: list, period: int = 14) -> float:
    """Williams %R — overbought > -20, oversold < -80."""
    if len(prices) < period:
        return -50.0
    window = prices[-period:]
    hi, lo = max(window), min(window)
    if hi == lo:
        return -50.0
    return -100.0 * (hi - prices[-1]) / (hi - lo)


def calc_cci(prices: list, period: int = 20) -> float:
    """Commodity Channel Index — overbought > +100, oversold < -100."""
    if len(prices) < period:
        return 0.0
    window   = prices[-period:]
    mean_tp  = sum(window) / period
    mean_dev = sum(abs(p - mean_tp) for p in window) / period
    if mean_dev == 0:
        return 0.0
    return (window[-1] - mean_tp) / (0.015 * mean_dev)


def calc_atr(prices: list, period: int = 14) -> float:
    """ATR simplificado (só close, sem high/low separados) — mede volatilidade relativa."""
    if len(prices) < period + 1:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def detect_rsi_divergence(prices: list, period: int = 14) -> str | None:
    """
    Divergência entre preço e RSI (janela de 15 ticks).
    Retorna 'bullish' (sinal de CALL), 'bearish' (sinal de PUT) ou None.
    """
    if len(prices) < period + 20:
        return None
    rsi_now  = calc_rsi(prices,       period)
    rsi_old  = calc_rsi(prices[:-15], period)
    price_now = prices[-1]
    price_old = prices[-16]
    # Bullish divergence: preço caiu, RSI subiu → impulso de reversão CALL
    if price_now < price_old and rsi_now > rsi_old + 5:
        return "bullish"
    # Bearish divergence: preço subiu, RSI caiu → impulso de reversão PUT
    if price_now > price_old and rsi_now < rsi_old - 5:
        return "bearish"
    return None


# ── Log de operações (trade journal) ────────────────────────────────────────
_LOG_DIR = os.path.join(BASE_DIR, "logs")
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
except Exception:
    pass
_TRADE_LOG = os.path.join(_LOG_DIR, "trades.jsonl")


def _log_trade(entry: dict):
    """Grava registro JSONL em logs/trades.jsonl para auditoria/backtesting."""
    try:
        with open(_TRADE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def get_candle_5m(history: list) -> dict | None:
    """
    Reconstrói a vela de 5 minutos atual a partir do histórico de ticks.

    history: lista de (price, timestamp) — mantida em tick_history[asset]

    Retorna dict com:
      direction  → "CALL" ou "PUT" (direção do corpo da vela)
      strength   → 0.0–1.0  (corpo / range total — 0=doji, 1=vela perfeita)
      open/close/high/low → preços OHLC da vela atual
      ticks      → número de ticks na vela atual
      time_pct   → 0.0–1.0 (quanto do período de 5min já passou)
      momentum   → variação % do close em relação ao open
    """
    if not history:
        return None

    now         = time.time()
    candle_start = now - (now % 300)   # início do período de 5min (floor ao múltiplo de 300s)

    # Ticks pertencentes à vela atual
    candle_prices = [p for p, t in history if t >= candle_start]

    if len(candle_prices) < 8:
        return None   # Vela nova demais — dados insuficientes

    open_p  = candle_prices[0]
    close_p = candle_prices[-1]
    high_p  = max(candle_prices)
    low_p   = min(candle_prices)

    candle_range = high_p - low_p
    body         = abs(close_p - open_p)

    if candle_range == 0:
        return None   # Sem movimento — não opera

    strength  = body / candle_range        # corpo / range (0=doji, 1=vela forte)
    direction = "CALL" if close_p >= open_p else "PUT"
    time_pct  = min(1.0, (now - candle_start) / 300.0)
    momentum  = (close_p - open_p) / open_p * 100 if open_p else 0.0

    return {
        "direction": direction,
        "strength":  strength,
        "open":      open_p,
        "close":     close_p,
        "high":      high_p,
        "low":       low_p,
        "ticks":     len(candle_prices),
        "time_pct":  time_pct,
        "momentum":  momentum,
    }


def build_candles(history: list, candle_seconds: int = 30, min_ticks_per_candle: int = 2) -> list:
    """
    Agrega tick_history[asset] = [(price, ts), ...] em candles de `candle_seconds`
    segundos, alinhados ao relógio de parede (candle_start = ts - ts % candle_seconds).

    Retorna lista de CLOSES (float), em ordem cronológica, contendo só candles
    COMPLETOS (exclui o candle corrente, ainda em formação). Um candle só entra
    no resultado se tiver >= min_ticks_per_candle ticks — evita close artificial
    em gap de liquidez (ex: virada de sessão, mercado calmo).

    Usado pelas 3 funções de sinal reais (detect_bb_squeeze_signal, calc_trend,
    check_3candle_confirm) no lugar do tick_buf cru, para que os períodos fixos
    (20, 65 candles etc.) representem sempre o mesmo tempo de parede — algo que
    "N ticks" não garante em forex (taxa de chegada varia por sessão/par).
    """
    if not history:
        return []

    now           = time.time()
    current_start = now - (now % candle_seconds)

    buckets: dict = {}
    for price, ts in history:
        bucket_start = ts - (ts % candle_seconds)
        if bucket_start >= current_start:
            continue  # candle corrente — ainda em formação, não entra no resultado
        buckets.setdefault(bucket_start, []).append(price)

    closes = []
    for bucket_start in sorted(buckets.keys()):
        prices_in_bucket = buckets[bucket_start]
        if len(prices_in_bucket) < min_ticks_per_candle:
            continue
        closes.append(prices_in_bucket[-1])
    return closes


def calc_signal(prices: list, config: dict):
    """
    Sinal v4 — Multi-timeframe + 8 filtros expandidos:

    OBRIGATÓRIOS (sem eles, não opera):
      F1: RSI Reversão (janela curta 50 ticks)
      F2: BB Band Touch (preço na banda)
      F2b: BB Squeeze blocker (não opera quando bandas estão muito apertadas)
      F3: EMA 50/100 macro trend (veta se fortemente contra a tendência)

    PONTUAÇÃO (acumula confiança):
      RSI Reversão:          +0.22
      BB Touch:              +0.12
      EMA 50/100 a favor:    +0.10
      Williams %R:           +0.12
      CCI:                   +0.10
      Stochastic %K/%D:      +0.12
      RSI Divergência:       +0.12
      EMA 9/21 curto prazo:  +0.08
      Acordo multi-TF:       +0.10

    Total máximo: 1.08 → cap em 1.00
    """
    if len(prices) < 35:
        return None, 0.0, "", []

    # Janelas: curta (últimos 50) e longa (últimos 200 ou todos disponíveis)
    p_short = prices[-50:]
    p_long  = prices[-200:] if len(prices) >= 100 else prices

    # ── Indicadores curto prazo ───────────────────────────────────────────────
    rsi_now  = calc_rsi(p_short,      14)
    rsi_prev = calc_rsi(p_short[:-1], 14)
    rsi_2ago = calc_rsi(p_short[:-2], 14)

    ema_fast          = calc_ema(p_short, 9)
    ema_slow          = calc_ema(p_short, 21)
    _, bb_upper, bb_lower = calc_bb(p_short, 20, 2.0)
    stoch_k, stoch_d  = calc_stoch(p_short, 14, 3)
    wpr               = calc_williams_r(p_short, 14)
    cci               = calc_cci(p_short, 20)
    price_now         = prices[-1]

    # ── Indicadores longo prazo (opcional) ───────────────────────────────────
    ema_50  = calc_ema(p_long, 50)  if len(p_long) >= 50  else None
    ema_100 = calc_ema(p_long, 100) if len(p_long) >= 100 else None

    # RSI na janela longa (para acordo multi-TF)
    rsi_long_now  = calc_rsi(p_long,      14) if len(p_long) >= 15 else rsi_now
    rsi_long_prev = calc_rsi(p_long[:-1], 14) if len(p_long) >= 16 else rsi_prev
    rsi_long_2ago = calc_rsi(p_long[:-2], 14) if len(p_long) >= 17 else rsi_2ago

    direction = None
    conf      = 0.0
    motivo    = ""
    votos     = []

    # ── F1: RSI REVERSÃO (obrigatório) ───────────────────────────────────────
    put_rsi_peak    = (rsi_2ago >= config["rsi_upper"] and
                       rsi_prev  >= config["rsi_upper"] and
                       rsi_now   <  rsi_prev)
    call_rsi_bottom = (rsi_2ago <= config["rsi_lower"] and
                       rsi_prev  <= config["rsi_lower"] and
                       rsi_now   >  rsi_prev)

    if put_rsi_peak:
        direction = "PUT"
        conf     += 0.22
        motivo    = f"RSI reverteu {rsi_prev:.1f}→{rsi_now:.1f} (pico confirmado)"
        votos.append(f"RSI↓ {rsi_now:.0f}")
    elif call_rsi_bottom:
        direction = "CALL"
        conf     += 0.22
        motivo    = f"RSI reverteu {rsi_prev:.1f}→{rsi_now:.1f} (fundo confirmado)"
        votos.append(f"RSI↑ {rsi_now:.0f}")
    else:
        return None, 0.0, "", []

    # ── F2: BOLLINGER BANDS (obrigatório) ────────────────────────────────────
    if bb_upper is None or bb_lower is None:
        return None, 0.0, "", []

    bb_range  = bb_upper - bb_lower
    bb_mid    = (bb_upper + bb_lower) / 2.0
    bb_margin = bb_range * 0.05

    # F2b: BB Squeeze — bandas muito apertadas = sem direcionalidade, não opera
    if bb_mid > 0 and (bb_range / bb_mid) < 0.0008:
        return None, 0.0, "", []

    if direction == "CALL" and price_now <= (bb_lower + bb_margin):
        conf += 0.12; votos.append("BB↓ ✓")
    elif direction == "PUT" and price_now >= (bb_upper - bb_margin):
        conf += 0.12; votos.append("BB↑ ✓")
    else:
        return None, 0.0, "", []

    # ── F3: EMA 50/100 MACRO TREND (veta se fortemente contra) ──────────────
    if ema_50 and ema_100 and ema_100 > 0:
        ema_diff_pct = abs(ema_50 - ema_100) / ema_100 * 100.0
        macro_bull   = ema_50 > ema_100
        macro_bear   = ema_50 < ema_100
        if direction == "CALL" and macro_bear and ema_diff_pct > 0.05:
            return None, 0.0, "", []  # tendência macro baixista — veta CALL
        if direction == "PUT" and macro_bull and ema_diff_pct > 0.05:
            return None, 0.0, "", []  # tendência macro altista — veta PUT
        if direction == "CALL" and macro_bull:
            conf += 0.10; votos.append("EMA50/100↑")
        elif direction == "PUT" and macro_bear:
            conf += 0.10; votos.append("EMA50/100↓")
        else:
            votos.append("EMA50/100✗")

    # ── F4: WILLIAMS %R ──────────────────────────────────────────────────────
    if direction == "CALL" and wpr < -80:
        conf += 0.12; votos.append(f"W%R {wpr:.0f}")
    elif direction == "PUT" and wpr > -20:
        conf += 0.12; votos.append(f"W%R {wpr:.0f}")
    else:
        votos.append(f"W%R✗ {wpr:.0f}")

    # ── F5: CCI ──────────────────────────────────────────────────────────────
    if direction == "CALL" and cci < -100:
        conf += 0.10; votos.append(f"CCI {cci:.0f}")
    elif direction == "PUT" and cci > 100:
        conf += 0.10; votos.append(f"CCI {cci:.0f}")
    else:
        votos.append(f"CCI✗ {cci:.0f}")

    # ── F6: STOCHASTIC ───────────────────────────────────────────────────────
    if direction == "CALL" and stoch_k < 25 and stoch_d < 30:
        conf += 0.12; votos.append(f"STOCH↑ {stoch_k:.0f}")
    elif direction == "PUT" and stoch_k > 75 and stoch_d > 70:
        conf += 0.12; votos.append(f"STOCH↓ {stoch_k:.0f}")
    else:
        votos.append(f"STOCH✗ {stoch_k:.0f}")

    # ── F7: RSI DIVERGÊNCIA ──────────────────────────────────────────────────
    div = detect_rsi_divergence(p_short)
    if direction == "CALL" and div == "bullish":
        conf += 0.12; votos.append("DIV↑")
    elif direction == "PUT" and div == "bearish":
        conf += 0.12; votos.append("DIV↓")
    else:
        votos.append("DIV✗")

    # ── F8: EMA 9/21 curto prazo ─────────────────────────────────────────────
    if direction == "CALL" and ema_fast > ema_slow:
        conf += 0.08; votos.append("EMA9/21↑")
    elif direction == "PUT" and ema_fast < ema_slow:
        conf += 0.08; votos.append("EMA9/21↓")
    else:
        votos.append("EMA9/21✗")

    # ── F9: ACORDO MULTI-TIMEFRAME ────────────────────────────────────────────
    # Verifica se a janela longa também está em reversão
    put_long  = (rsi_long_2ago >= config["rsi_upper"] and
                 rsi_long_prev  >= config["rsi_upper"] and
                 rsi_long_now   <  rsi_long_prev)
    call_long = (rsi_long_2ago <= config["rsi_lower"] and
                 rsi_long_prev  <= config["rsi_lower"] and
                 rsi_long_now   >  rsi_long_prev)
    if direction == "PUT"  and put_long:
        conf += 0.10; votos.append("MTF↓")
    elif direction == "CALL" and call_long:
        conf += 0.10; votos.append("MTF↑")
    else:
        votos.append("MTF✗")

    return direction, max(0.0, min(1.0, conf)), motivo, votos


# ════════════════════════════════════════════════════════
#  BOT DERIV — CORRIGIDO
#  Fix 1: req_id DEVE ser inteiro (Deriv rejeita strings)
#  Fix 2: buy usa ask_price da proposta (não o stake)
#  Fix 3: logging detalhado para diagnóstico
# ════════════════════════════════════════════════════════
async def _deriv_bot_async(ss: SessionState, token: str, valor_brl: float,
                            estrategia: str, want_demo: bool = False):
    try:
        import websockets
    except ImportError:
        ss.log("❌ Biblioteca 'websockets' não instalada.", "loss")
        return

    config      = STRATEGIES.get(estrategia, STRATEGIES["moderada"])
    lucro_total = 0.0
    wins = losses = 0
    tick_buf    = {a: [] for a in ASSETS}
    last_trade  = {a: 0.0 for a in ASSETS}
    active_cx   = {}   # contract_id -> {tid, buy_price, ...}
    pending_p   = {}   # req_id (int) -> {asset, direction, conf, motivo}
    req_ctr     = [0]  # inteiro mutable via lista (closure)
    trade_count = 0
    currency    = "USD"
    rate_brl    = 5.70  # taxa BRL→moeda da conta (atualizada após autenticação)
    last_status = [time.time()]
    pending_buy = {}   # buy_rid -> {direction, asset, expected_payout}
    # ── Memória adaptativa por ativo ──────────────────────────────────────────
    # hist: últimos 20 resultados (1=WIN, 0=LOSS)
    # conf_adj: ajuste dinâmico no threshold de confiança (-0.05 a +0.20)
    # pause_until: timestamp até quando o ativo está pausado por losses seguidos
    # last_result: 1=WIN, 0=LOSS, None=sem histórico (para cooldown adaptativo)
    perf = {a: {"hist": [], "conf_adj": 0.0, "pause_until": 0.0, "last_result": None}
            for a in ASSETS}
    # Histórico global em ordem cronológica (para stop por sequência)
    global_hist = []   # lista de 1/0 na ordem em que as operações fecharam
    # ── Dupla confirmação: sinal precisa aparecer 2x antes de abrir operação ──
    pending_signal = {a: None for a in ASSETS}  # {direction, conf, motivo, votos, ts}
    # ── Tick speed: rastreia velocidade dos ticks (atividade do mercado) ──────
    tick_times = {a: [] for a in ASSETS}
    # ── Histórico para reconstrução de vela de 5 minutos ─────────────────────
    # Cada entrada: (price, timestamp) — mantido em sincronia com tick_buf
    tick_history = {a: [] for a in ASSETS}
    # ── Candles agregados por tempo (CANDLE_SECONDS) — input das 3 funções de
    # sinal reais em modo forex. Recalculado a cada tick via build_candles().
    # tick_buf cru continua sendo a fonte do preço de execução (entry/exit) —
    # nunca trocar isso pelo candle, que só atualiza periodicamente.
    candle_buf = {a: [] for a in ASSETS}
    # ── Ranking dinâmico: histórico com timestamp para calcular melhor ativo ──
    # Cada entrada: {"ts": float, "won": bool}  (mantém só últimas 2h)
    asset_log = {a: [] for a in ASSETS}
    _TWO_HOURS = 7200  # segundos

    # ── MARTINGALE ────────────────────────────────────────────────────────────
    # Base: R$5 convertido para moeda da conta.
    # Após cada LOSS: multiplica por 2.2 (recupera perdas + lucro com payout 92%).
    # Após WIN: reseta para o valor base.
    # Após 5 losses consecutivos (bust): para o bot.
    #
    # Sequência com R$5 base (em reais):
    #   Round 0: R$5.00   Round 1: R$11.00  Round 2: R$24.20
    #   Round 3: R$53.24  Round 4: R$117.13 Round 5: R$257.69
    #
    _MART_BASE_BRL  = valor_brl  # valor base = escolha do usuário (padrão R$2.50)
    _MART_MULT      = 2.2        # multiplicador por round de loss
    _MART_MAX_ROUND = 3          # máximo de rounds antes de parar (bust)
    mart_round      = [0]     # round atual (lista para ser mutável em closures)
    mart_stake_curr = [0.0]   # stake atual na moeda da conta (calculado após auth)
    # Recuperação imediata: após LOSS guarda {direction, asset} para re-entrar
    # na próxima vela sem esperar novo sinal (bypass de todos os filtros)
    recovery_pending = {a: None for a in ASSETS}  # None ou 'CALL'/'PUT'

    def _best_asset():
        """
        Retorna o ativo com maior taxa de acerto nas últimas 2h.
        Critérios de desempate: mais operações ganha (mais dados = mais confiança).
        Retorna None se nenhum ativo tem operações suficientes (mínimo 3).
        Ativos com pausa ativa são excluídos.
        """
        now_ts  = time.time()
        cutoff  = now_ts - _TWO_HOURS
        best    = None
        best_wr = -1.0
        best_n  = 0
        for a in ASSETS:
            if now_ts < perf[a].get("pause_until", 0):
                continue  # ativo pausado por losses seguidos
            recent = [e for e in asset_log[a] if e["ts"] >= cutoff]
            n = len(recent)
            if n < 3:
                continue  # dados insuficientes para decisão
            wr = sum(1 for e in recent if e["won"]) / n
            if wr > best_wr or (wr == best_wr and n > best_n):
                best_wr = wr
                best_n  = n
                best    = a
        return best, best_wr, best_n

    ss.log("Conectando à Deriv…", "info")
    ss.update_estado(rodando=True)

    _max_reconnects = 5
    for _attempt in range(_max_reconnects):
        if ss.stop_evt.is_set():
            break
        if _attempt > 0:
            _wait = min(5 * _attempt, 30)
            ss.log(f"🔄 Reconectando em {_wait}s… (tentativa {_attempt}/{_max_reconnects})", "warn")
            await asyncio.sleep(_wait)
            if ss.stop_evt.is_set():
                break
        try:
            async with websockets.connect(
                DERIV_WS, ping_interval=20, ping_timeout=30, open_timeout=20
            ) as dws:
                ss.log("✅ WebSocket estabelecido.", "info")

                # ── AUTORIZAÇÃO ───────────────────────────────────────────
                await dws.send(json.dumps({"authorize": token}))
                raw  = await asyncio.wait_for(dws.recv(), timeout=20)
                auth = json.loads(raw)

                if "error" in auth:
                    err = auth["error"].get("message", "Token inválido")
                    ss.log(f"❌ Autenticação falhou: {err}", "loss")
                    ss.log("→ Verifique o token em app.deriv.com → Conta → Token API", "warn")
                    ss.log("→ Precisa ter permissão 'Trade' ativada", "warn")
                    break  # token inválido — não tenta reconectar

                acct    = auth.get("authorize", {})
                is_virt = bool(acct.get("is_virtual", False))

                # ── TROCA DE CONTA (Demo ↔ Real) ──────────────────────────
                if want_demo != is_virt:
                    acct_list = acct.get("account_list", [])
                    targets = [a for a in acct_list
                               if bool(a.get("is_virtual", False)) == want_demo
                               and a.get("token")]
                    if targets:
                        new_token = targets[0]["token"]
                        tipo_str  = "Demo 🧪" if want_demo else "Real 💰"
                        ss.log(f"🔄 Alternando para conta {tipo_str}…", "info")
                        await dws.send(json.dumps({"authorize": new_token}))
                        raw2  = await asyncio.wait_for(dws.recv(), timeout=20)
                        auth2 = json.loads(raw2)
                        if "error" not in auth2:
                            acct    = auth2.get("authorize", {})
                            is_virt = bool(acct.get("is_virtual", False))
                        else:
                            ss.log("⚠️ Falha ao trocar conta, continuando com a atual.", "warn")
                    else:
                        tipo_str = "Demo 🧪" if want_demo else "Real 💰"
                        ss.log(f"⚠️ Conta {tipo_str} não encontrada. Usando a atual.", "warn")

                loginid  = acct.get("loginid", "?")
                saldo0   = float(acct.get("balance", 0))
                currency = acct.get("currency", "USD")
                is_virt  = bool(acct.get("is_virtual", False))
                tipo     = "Demo 🧪" if is_virt else "Real 💰"

                ss.log(f"✅ Conta {loginid} ({tipo}) autenticada!", "win")
                ss.log(f"💰 Saldo inicial: {saldo0:.2f} {currency}", "info")
                ss.update_estado(saldo=saldo0, conectado=True)
                _attempt = 0  # reset contador ao conectar com sucesso

                # ── CONVERSÃO BRL → MOEDA DA CONTA ────────────────────────
                # Tabela de referência (atualizada manualmente quando necessário)
                _BRL_PER = {"USD": 5.70, "EUR": 6.20, "GBP": 7.20,
                            "AUD": 3.70, "CAD": 4.10, "CHF": 6.40}
                if currency == "BRL":
                    rate_brl = 1.0
                    stake    = valor_brl
                else:
                    rate_brl = _BRL_PER.get(currency, 5.70)
                    stake    = max(round(valor_brl / rate_brl, 2), 0.35)

                # ── MARTINGALE: calcular stake base na moeda da conta ─────
                def _mart_stake(rnd: int) -> float:
                    """Retorna o stake em moeda da conta para o round N."""
                    brl = _MART_BASE_BRL * (_MART_MULT ** rnd)
                    if currency == "BRL":
                        return round(brl, 2)
                    return max(round(brl / rate_brl, 2), 0.35)

                mart_stake_curr[0] = _mart_stake(0)
                ss.log(f"💱 R${_MART_BASE_BRL:.2f} base → {currency} {mart_stake_curr[0]:.2f} "
                       f"| Martingale x{_MART_MULT} até {_MART_MAX_ROUND} rounds", "info")

                # ── SUBSCRIÇÕES ───────────────────────────────────────────
                await dws.send(json.dumps({"balance": 1, "subscribe": 1}))
                for asset in ASSETS:
                    await dws.send(json.dumps({"ticks": asset, "subscribe": 1}))
                    await asyncio.sleep(0.05)
                ss.log(f"📊 Monitorando {len(ASSETS)} ativos | Estratégia: {estrategia}", "info")
                ss.log(f"⏳ Coletando histórico de ticks (aguarde ~20s)…", "info")

                # ── FUNÇÃO INTERNA: ENVIAR PROPOSTA ───────────────────────
                async def request_proposal(asset, direction, conf, motivo):
                    req_ctr[0] += 1
                    rid = req_ctr[0]  # INTEIRO — Deriv exige inteiro
                    _amount = mart_stake_curr[0]   # stake dinâmico do Martingale
                    pending_p[rid] = {
                        "asset": asset, "direction": direction,
                        "conf": conf, "motivo": motivo,
                        "mart_round": mart_round[0],
                        "amount": _amount,
                    }
                    await dws.send(json.dumps({
                        "proposal":       1,
                        "req_id":         rid,
                        "amount":         _amount,
                        "basis":          "stake",
                        "contract_type":  direction,
                        "currency":       currency,
                        "duration":       config["duracao"],
                        "duration_unit":  config.get("duracao_unit", "s"),
                        "symbol":         asset,
                    }))
                    aname = ASSET_NAMES.get(asset, asset)
                    _brl_now = _MART_BASE_BRL * (_MART_MULT ** mart_round[0])
                    ss.log(
                        f"📡 {direction} {aname} | "
                        f"Round {mart_round[0]} | R${_brl_now:.2f} ({currency} {_amount:.2f})"
                        f" (req#{rid})",
                        "info"
                    )

                # ── LIQUIDAR CONTRATO (função local reutilizável) ────────
                def _liquidar(cid):
                    nonlocal wins, losses, lucro_total
                    info = active_cx.pop(cid, None)
                    if not info:
                        return
                    asset_cx    = info["asset"]
                    direction_cx= info["direction"]
                    entry_price = info["entry_price"]
                    bp          = info["buy_price"]
                    tid         = info["tid"]
                    payout_est  = info.get("expected_payout", bp * 1.85)
                    ticks       = tick_buf.get(asset_cx, [])
                    if not ticks:
                        # Sem ticks: devolve ao dicionário para tentar no próximo tick
                        active_cx[cid] = info
                        return
                    exit_price = ticks[-1]
                    won = (exit_price > entry_price if direction_cx == "CALL"
                           else exit_price < entry_price)
                    if won:
                        profit       = payout_est - bp
                        wins        += 1
                        lucro_total += profit
                        profit_brl   = profit * rate_brl
                        ss.log(f"✅ WIN  +R${profit_brl:.2f} | {direction_cx} {entry_price:.5g}→{exit_price:.5g}", "win")
                        ss.resultado(tid, "W", profit, lucro_brl=profit_brl)
                    else:
                        losses      += 1
                        lucro_total -= bp
                        loss_brl     = bp * rate_brl
                        ss.log(f"❌ LOSS −R${loss_brl:.2f} | {direction_cx} {entry_price:.5g}→{exit_price:.5g}", "loss")
                        ss.resultado(tid, "L", -bp, lucro_brl=-loss_brl)
                    ss.update_estado(wins=wins, losses=losses, lucro=lucro_total)
                    # ── Gravar no trade journal ───────────────────────────
                    _log_trade({
                        "ts":         datetime.utcnow().isoformat(),
                        "event":      "close",
                        "trade_id":   tid,
                        "asset":      asset_cx,
                        "direction":  direction_cx,
                        "entry":      entry_price,
                        "exit":       exit_price,
                        "result":     "WIN" if won else "LOSS",
                        "profit_brl": round((payout_est - bp if won else -bp) * rate_brl, 2),
                    })
                    # ── Aprendizado adaptativo ────────────────────────────
                    # Registra resultado por ativo para ajustar threshold
                    perf[asset_cx]["hist"].append(1 if won else 0)
                    perf[asset_cx]["last_result"] = 1 if won else 0
                    # ── Ranking dinâmico: registra com timestamp ──────────
                    asset_log[asset_cx].append({"ts": time.time(), "won": won})
                    # Descarta entradas com mais de 2h para não acumular
                    _cutoff = time.time() - _TWO_HOURS
                    asset_log[asset_cx] = [e for e in asset_log[asset_cx] if e["ts"] >= _cutoff]
                    if len(perf[asset_cx]["hist"]) > 20:
                        perf[asset_cx]["hist"] = perf[asset_cx]["hist"][-20:]
                    recent_hist = perf[asset_cx]["hist"]
                    if len(recent_hist) >= 4:
                        win_rate = sum(recent_hist) / len(recent_hist)
                        if win_rate >= 0.65:
                            # Ativo performando bem → relaxa um pouco o threshold
                            perf[asset_cx]["conf_adj"] = max(-0.05, perf[asset_cx]["conf_adj"] - 0.01)
                        elif win_rate < 0.40:
                            # Ativo performando mal → exige mais confiança
                            perf[asset_cx]["conf_adj"] = min(0.20, perf[asset_cx]["conf_adj"] + 0.03)
                        else:
                            # Neutro → volta gradualmente ao zero
                            perf[asset_cx]["conf_adj"] *= 0.95
                    # Pausa por ativo após 2 losses seguidos
                    if len(recent_hist) >= 2 and recent_hist[-2:] == [0, 0]:
                        perf[asset_cx]["pause_until"] = time.time() + 600  # pausa 10 min
                        ss.log(f"⏸️ {ASSET_NAMES.get(asset_cx, asset_cx)}: 2 losses seguidos → pausado 10 min", "warn")
                    # ── Histórico global (diagnóstico) ───────────────────
                    global_hist.append(1 if won else 0)
                    if len(global_hist) > 50:
                        global_hist.pop(0)

                    # ── MARTINGALE: ajustar stake para próxima operação ───
                    if won:
                        # WIN → reseta ciclo e limpa recuperação pendente
                        mart_round[0] = 0
                        mart_stake_curr[0] = _mart_stake(0)
                        recovery_pending[asset_cx] = None
                        ss.log(
                            f"♻️  Martingale reset → Round 0 | "
                            f"R${_MART_BASE_BRL:.2f} ({currency} {mart_stake_curr[0]:.2f})",
                            "win"
                        )
                    else:
                        # LOSS → avança round
                        mart_round[0] += 1
                        if mart_round[0] > _MART_MAX_ROUND:
                            _brl_lost = _MART_BASE_BRL * sum(
                                _MART_MULT ** r for r in range(_MART_MAX_ROUND + 1)
                            )
                            ss.log(
                                f"🛑 MARTINGALE BUST após {_MART_MAX_ROUND} losses "
                                f"(~R${_brl_lost:.0f} de exposição). Bot pausado.",
                                "loss"
                            )
                            ss.stop_evt.set()
                        else:
                            mart_stake_curr[0] = _mart_stake(mart_round[0])
                            _brl_next = _MART_BASE_BRL * (_MART_MULT ** mart_round[0])
                            # Ativa recuperação imediata: re-entra na mesma direção
                            # na próxima vela sem esperar novo sinal
                            recovery_pending[asset_cx] = direction_cx
                            ss.log(
                                f"📈 Martingale Round {mart_round[0]}/{_MART_MAX_ROUND} | "
                                f"Próxima: R${_brl_next:.2f} ({currency} {mart_stake_curr[0]:.2f}) | "
                                f"⚡ Re-entrada imediata {direction_cx}",
                                "warn"
                            )

                # ── LOOP PRINCIPAL ────────────────────────────────────────
                while not ss.stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(dws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        if ss.stop_evt.is_set(): break
                        await dws.send(json.dumps({"ping": 1}))
                        now = time.time()
                        # Liquidar expirados (fallback para quando não há ticks)
                        for cid in [c for c, i in list(active_cx.items())
                                    if now >= i.get("expires_at", float("inf"))]:
                            _liquidar(cid)
                        # Status periódico a cada 60s
                        if now - last_status[0] > 60:
                            last_status[0] = now
                            counts = {a: len(tick_buf[a]) for a in ASSETS}
                            ss.log(f"📈 Ticks coletados: {counts}", "info")
                        continue

                    msg   = json.loads(raw)
                    mtype = msg.get("msg_type", "")

                    # ── ERRO GENÉRICO ──────────────────────────────────────
                    if mtype == "error":
                        err_obj = msg.get("error") or {}
                        em   = err_obj.get("message", "?")
                        code = err_obj.get("code", "?")
                        rid  = msg.get("req_id", "?")
                        ss.log(f"⚠️ Deriv [{code}] req#{rid}: {em}", "warn")
                        continue

                    # ── SALDO ──────────────────────────────────────────────
                    if mtype == "balance":
                        b = float((msg.get("balance") or {}).get("balance", 0))
                        ss.update_estado(saldo=b)
                        continue

                    # ── TICK ───────────────────────────────────────────────
                    if mtype == "tick":
                        tick  = msg.get("tick", {})
                        asset = tick.get("symbol", "")
                        price = float(tick.get("quote", 0))
                        if asset not in tick_buf:
                            continue
                        tick_buf[asset].append(price)
                        if len(tick_buf[asset]) > 500:
                            tick_buf[asset] = tick_buf[asset][-500:]
                        # ── Rastrear velocidade dos ticks (últimos 60s) ────
                        _tnow = time.time()
                        tick_times[asset].append(_tnow)
                        tick_times[asset] = [t for t in tick_times[asset] if _tnow - t <= 60]
                        # ── Histórico para candles agregados ────────────────
                        # Prune por JANELA DE TEMPO (2h), não por contagem fixa de
                        # itens — em forex a taxa de chegada de tick varia muito
                        # (Ásia calma vs. London/NY overlap), então um cap fixo de
                        # N ticks pode cobrir só alguns minutos em sessão ativa e
                        # nunca acumular os ~65 candles (32min @ 30s) necessários
                        # para a estratégia rodar. Janela de tempo garante cobertura
                        # consistente independente do tick rate.
                        tick_history[asset].append((price, _tnow))
                        _hist_cutoff = _tnow - 7200  # 2h
                        if tick_history[asset][0][1] < _hist_cutoff:
                            tick_history[asset] = [
                                (p, t) for p, t in tick_history[asset] if t >= _hist_cutoff
                            ]
                        if MARKET_MODE == "forex":
                            candle_buf[asset] = build_candles(
                                tick_history[asset], CANDLE_SECONDS
                            )

                        # ── Liquidar contratos expirados a cada tick ────────
                        now = time.time()
                        for cid in [c for c, i in list(active_cx.items())
                                    if now >= i.get("expires_at", float("inf"))]:
                            _liquidar(cid)

                        n_ticks = len(tick_buf[asset])
                        if n_ticks < 21:
                            continue  # Aguarda histórico suficiente

                        # Status periódico de diagnóstico
                        if now - last_status[0] > 60:
                            last_status[0] = now
                            _buf = tick_buf[asset]
                            rsi_now  = calc_rsi(_buf,      14)
                            rsi_prev = calc_rsi(_buf[:-1], 14) if len(_buf) > 15 else rsi_now
                            stk, _   = calc_stoch(_buf, 14, 3)
                            _, bbu, bbl = calc_bb(_buf, 20, 2.0)
                            bb_info = f"BB [{bbl:.4g}–{bbu:.4g}]" if bbu else "BB n/a"
                            ss.log(
                                f"🔍 {ASSET_NAMES.get(asset,asset)} | "
                                f"RSI {rsi_now:.1f} (prev {rsi_prev:.1f}) | "
                                f"STOCH {stk:.0f} | {bb_info}",
                                "info"
                            )
                            # ── Ranking dinâmico das últimas 2h ──────────────
                            _now_ts  = time.time()
                            _cutoff2 = _now_ts - _TWO_HOURS
                            _ranking = []
                            for _a in ASSETS:
                                _recent = [e for e in asset_log[_a] if e["ts"] >= _cutoff2]
                                _n = len(_recent)
                                if _n == 0: continue
                                _wr2 = sum(1 for e in _recent if e["won"]) / _n
                                _ranking.append((_a, _wr2, _n))
                            _ranking.sort(key=lambda x: (-x[1], -x[2]))
                            _best_now, _best_wr2, _best_n2 = _best_asset()
                            ss.log("📊 RANKING ÚLTIMAS 2H:", "info")
                            for _rank_i, (_ra, _rwr, _rn) in enumerate(_ranking):
                                _star = " 🎯 OPERANDO" if _ra == _best_now else ""
                                _lbl  = "🟢" if _rwr >= 0.55 else ("🟡" if _rwr >= 0.45 else "🔴")
                                ss.log(
                                    f"  {_lbl} {ASSET_NAMES.get(_ra,_ra)}: "
                                    f"{_rwr*100:.0f}% ({_rn} ops){_star}",
                                    "info"
                                )
                            if not _ranking:
                                ss.log("  ⏳ Aquecimento: coletando dados de todos os ativos…", "info")
                            # Log do aprendizado adaptativo por ativo
                            for _a, _p in perf.items():
                                if not _p["hist"]: continue
                                _wr  = sum(_p["hist"]) / len(_p["hist"])
                                _adj = _p["conf_adj"]
                                ss.log(
                                    f"🧠 {ASSET_NAMES.get(_a,_a)}: "
                                    f"threshold adj {_adj:+.2f}",
                                    "info"
                                )

                        # ── Uma operação de cada vez ───────────────────────
                        # Só abre nova trade se não há contrato ativo nem proposta pendente
                        if active_cx or pending_p or pending_buy:
                            # Limpar sinais pendentes expirados enquanto há trade aberta
                            _exp = 20 if config.get("duracao_unit") in ("t", "s") else 120
                            for _a in list(pending_signal):
                                ps = pending_signal[_a]
                                if ps and (now - ps["ts"]) > _exp:
                                    pending_signal[_a] = None
                            continue

                        # ── MARTINGALE RECOVERY: re-entrada imediata ────────
                        # Após LOSS, recovery_pending[asset] guarda a direção.
                        # Bypassa cooldown, pausa, tendência e 3-velas —
                        # entra na próxima vela disponível com o stake dobrado.
                        if recovery_pending.get(asset):
                            _rec_dir = recovery_pending[asset]
                            recovery_pending[asset] = None   # consumir flag
                            _tick_speed_rec = len(tick_times[asset])
                            if _tick_speed_rec < 3:
                                # mercado sem ticks, reagendar para próximo tick
                                recovery_pending[asset] = _rec_dir
                                continue
                            # Stop diário antes de re-entrar
                            _saldo_rec = ss.estado_snap.get("saldo", saldo0)
                            if saldo0 > 0 and (saldo0 - _saldo_rec) / saldo0 >= config["stop_diario_pct"]:
                                ss.stop_evt.set()
                                continue
                            _brl_rec   = _MART_BASE_BRL * (_MART_MULT ** mart_round[0])
                            _conf_rec  = 0.85
                            _motivo_rec = (
                                f"⚡ Martingale Recovery Round {mart_round[0]} | "
                                f"{_rec_dir} | R${_brl_rec:.2f} "
                                f"({currency} {mart_stake_curr[0]:.2f})"
                            )
                            ss.sinal(ASSET_NAMES.get(asset, asset), _rec_dir,
                                     _conf_rec, _motivo_rec,
                                     [f"Recovery Round {mart_round[0]}",
                                      f"{_rec_dir} (mesma direção anterior)",
                                      f"Stake: R${_brl_rec:.2f}"])
                            last_trade[asset] = now
                            await request_proposal(asset, _rec_dir, _conf_rec, _motivo_rec)
                            continue

                        # ── Pausa adaptativa por ativo ──────────────────────
                        if now < perf[asset].get("pause_until", 0):
                            continue

                        # ── Cooldown adaptativo ──────────────────────────────
                        _unit = config.get("duracao_unit", "t")
                        if _unit == "t":
                            base_cooldown = config["duracao"] + 5   # 5 ticks → ~10s base
                        elif _unit == "s":
                            base_cooldown = config["duracao"] + 5
                        else:
                            base_cooldown = config["duracao"] * 60
                        last_res = perf[asset].get("last_result")
                        if last_res == 0:
                            cooldown = base_cooldown       # após LOSS: volta rápido
                        elif last_res == 1:
                            cooldown = base_cooldown + 10  # após WIN: 10s extra de cautela
                        else:
                            cooldown = base_cooldown + 5
                        if now - last_trade[asset] < cooldown:
                            continue

                        # ── Seleção dinâmica: só opera o melhor ativo das últimas 2h ──
                        best_a, best_wr, best_n = _best_asset()
                        if best_a is not None and asset != best_a:
                            # Existe um líder claro — ignorar todos os outros
                            continue
                        # Se best_a is None (dados insuficientes), libera todos os ativos
                        # para as primeiras operações do dia (fase de aquecimento)

                        # ── Filtro de velocidade de tick (mercado ativo) ────
                        tick_speed = len(tick_times[asset])  # ticks nos últimos 60s
                        if tick_speed < 3:
                            continue  # mercado inativo

                        # ── Stop diário ─────────────────────────────────────
                        _saldo_atual = ss.estado_snap.get("saldo", saldo0)
                        if saldo0 > 0:
                            _perda_pct = (saldo0 - _saldo_atual) / saldo0
                            if _perda_pct >= config["stop_diario_pct"]:
                                ss.log(
                                    f"🛑 STOP DIÁRIO: perdeu "
                                    f"{_perda_pct*100:.1f}% do saldo inicial "
                                    f"(limite {config['stop_diario_pct']*100:.0f}%). "
                                    f"Robô pausado.",
                                    "loss"
                                )
                                ss.stop_evt.set()
                                break

                        # ════════════════════════════════════════════════════
                        #  ESTRATÉGIA: BB SQUEEZE + BREAKOUT  (Trade AI)
                        #
                        #  Mesma lógica do robô analisado:
                        #  1. BB Squeeze: banda atual ≤ 65% da banda normal
                        #     (Bollinger Bands tightening)
                        #  2. Breakout direcional: preço toca/cruza a banda
                        #     após o squeeze (Downward/Upward spike)
                        #  3. Operação de 5 ticks com Martingale automático
                        #
                        #  MARTINGALE:
                        #   WIN  → reseta para R$5 (Round 0)
                        #   LOSS → multiplica por 2.2 (até Round 5, depois para)
                        #   R$5 → R$11 → R$24 → R$53 → R$117 → R$258
                        # ════════════════════════════════════════════════════
                        # Em forex, as 3 funções de sinal consomem candle_buf (closes
                        # agregados por CANDLE_SECONDS) em vez do tick cru — período
                        # fixo (65) passa a significar 65 CANDLES, tempo de parede
                        # consistente independente da taxa de chegada de tick.
                        # Em synthetic, comportamento 100% preservado (tick cru).
                        buf = candle_buf[asset] if MARKET_MODE == "forex" else tick_buf[asset]
                        if len(buf) < 65:   # mínimo para calcular BB histórico
                            continue

                        bb_signal, squeeze_ratio = detect_bb_squeeze_signal(buf)

                        if bb_signal is None:
                            continue   # sem squeeze ou sem breakout

                        direction = bb_signal

                        # ── FILTRO 1: Tendência (só operar a favor) ──────────
                        # EMA 10 vs EMA 30 define a direção dominante.
                        # CALL só entra se tendência é UP; PUT só se DOWN.
                        trend = calc_trend(buf, fast=10, slow=30)
                        if trend == "NEUTRAL":
                            continue   # mercado lateral, não operar
                        if direction == "CALL" and trend != "UP":
                            continue   # sinal contra a tendência, ignorar
                        if direction == "PUT"  and trend != "DOWN":
                            continue   # sinal contra a tendência, ignorar

                        # ── FILTRO 2: Confirmação de 3 micro-velas ───────────
                        # Em forex, buf já é candle_buf (cada elemento é 1 candle
                        # completo de CANDLE_SECONDS) → candle_size=1. Em synthetic,
                        # buf é tick cru e cada "vela" continua sendo 5 ticks.
                        _candle_size = 1 if MARKET_MODE == "forex" else 5
                        if not check_3candle_confirm(buf, direction, candle_size=_candle_size):
                            continue   # sem confirmação das 3 velas, não operar

                        # Confiança: quanto mais forte o squeeze, mais confiante
                        # squeeze_ratio: 0.0 = squeeze extremo (conf alta)
                        #                0.65 = limite (conf mínima)
                        conf = min(1.0, 0.95 - squeeze_ratio * 0.50)

                        conf_threshold = config["conf_min"] + perf[asset]["conf_adj"]
                        if conf < conf_threshold:
                            continue

                        aname  = ASSET_NAMES.get(asset, asset)
                        _mart_info = (f"Round {mart_round[0]} | "
                                      f"R${_MART_BASE_BRL * (_MART_MULT ** mart_round[0]):.2f}")
                        motivo = (f"BB Squeeze {'↑ CALL' if direction=='CALL' else '↓ PUT'} | "
                                  f"Tendência {trend} | 3 velas ✓ | "
                                  f"squeeze {squeeze_ratio*100:.0f}% | "
                                  f"conf {conf*100:.0f}% | {_mart_info}")
                        votos = [
                            f"BB Squeeze: banda atual = {squeeze_ratio*100:.0f}% da normal",
                            f"Tendência {trend} ✓ ({direction} a favor)",
                            f"3 micro-velas confirmadas ✓",
                            f"Breakout {'↑' if direction=='CALL' else '↓'} {direction}",
                            f"Martingale {_mart_info}",
                            f"Price: {buf[-1]:.5g}",
                        ]

                        ss.sinal(aname, direction, conf, motivo, votos)
                        _log_trade({
                            "ts":           datetime.utcnow().isoformat(),
                            "event":        "signal",
                            "asset":        asset,
                            "direction":    direction,
                            "conf":         round(conf, 4),
                            "squeeze_ratio":round(squeeze_ratio, 3),
                            "mart_round":   mart_round[0],
                            "mart_stake_brl": round(_MART_BASE_BRL * (_MART_MULT ** mart_round[0]), 2),
                            "tick_speed":   tick_speed,
                            "price":        buf[-1],
                        })
                        last_trade[asset] = now
                        await request_proposal(asset, direction, conf, motivo)
                        continue

                    # ── PROPOSTA RECEBIDA ─────────────────────────────────
                    if mtype == "proposal":
                        rid = msg.get("req_id")
                        if rid is None or rid not in pending_p:
                            continue
                        info = pending_p.pop(rid)

                        if "error" in msg:
                            err_msg = msg["error"].get("message", "?")
                            ss.log(f"⚠️ Proposta recusada (req#{rid}): {err_msg}", "warn")
                            continue

                        prop             = msg.get("proposal") or {}
                        pid              = prop.get("id", "")
                        ask_price        = float(prop.get("ask_price", stake))
                        expected_payout  = float(prop.get("payout", ask_price * 1.85))

                        if not pid:
                            ss.log("⚠️ Proposta sem ID recebida", "warn")
                            continue

                        trade_count += 1
                        req_ctr[0] += 1
                        buy_rid = req_ctr[0]

                        # Guardar contexto para associar ao buy confirmado
                        pending_buy[buy_rid] = {
                            "direction":       info["direction"],
                            "asset":           info["asset"],
                            "expected_payout": expected_payout,
                        }

                        aname = ASSET_NAMES.get(info["asset"], info["asset"])
                        ss.trade(f"T{trade_count}", aname, info["direction"].lower(), valor_brl)
                        ss.log(f"🛒 Comprando contrato: {info['direction']} {aname} "
                               f"R${valor_brl:.2f} (ask: {currency} {ask_price:.2f})", "info")

                        await dws.send(json.dumps({
                            "buy":    pid,
                            "req_id": buy_rid,
                            "price":  ask_price,
                        }))
                        continue

                    # ── COMPRA CONFIRMADA ─────────────────────────────────
                    if mtype == "buy":
                        if "error" in msg:
                            err_msg = msg["error"].get("message", "?")
                            ss.log(f"❌ Compra recusada: {err_msg}", "loss")
                            # Diagnóstico de erros comuns
                            if "balance" in err_msg.lower():
                                ss.log("→ Saldo insuficiente para esta operação", "warn")
                            elif "market" in err_msg.lower():
                                ss.log("→ Mercado fechado ou fora de horário", "warn")
                            continue

                        bd      = msg.get("buy", {})
                        cid     = str(bd.get("contract_id", ""))
                        bp      = float(bd.get("buy_price", stake))
                        buy_rid = msg.get("req_id")
                        binfo   = pending_buy.pop(buy_rid, {})

                        if cid:
                            asset_cx    = binfo.get("asset", "")
                            direction_cx= binfo.get("direction", "CALL")
                            payout_cx   = binfo.get("expected_payout", bp * 1.85)
                            entry_price = tick_buf.get(asset_cx, [0])[-1] if tick_buf.get(asset_cx) else 0
                            _unit = config.get("duracao_unit", "t")
                            if _unit == "t":
                                _dur_secs = config["duracao"] * 1.5   # 5 ticks × ~1s + margem
                            elif _unit == "s":
                                _dur_secs = config["duracao"]
                            else:
                                _dur_secs = config["duracao"] * 60
                            expires_at  = time.time() + _dur_secs + 8  # +8s buffer para liquidação
                            tid = f"T{trade_count}"
                            active_cx[cid] = {
                                "tid": tid, "buy_price": bp,
                                "expires_at": expires_at,
                                "asset": asset_cx, "direction": direction_cx,
                                "entry_price": entry_price,
                                "expected_payout": payout_cx,
                            }
                            ss.log(f"✅ Contrato #{cid} aberto — R${valor_brl:.2f} "
                                   f"({direction_cx} @ {entry_price:.5g})", "win")
                        continue


        except websockets.exceptions.ConnectionClosed as e:
            if ss.stop_evt.is_set():
                break
            ss.update_estado(conectado=False)
            ss.log(f"⚠️ Conexão fechada inesperadamente: {e}", "warn")
            # vai para próxima iteração do for (reconectar)
        except Exception as e:
            if isinstance(e, asyncio.CancelledError):
                break
            import traceback
            ss.log(f"❌ Erro inesperado: {e}", "loss")
            print(f"[BOT ERROR] {traceback.format_exc()}", flush=True)
            break
        else:
            # async with saiu limpo (stop_evt ativado dentro do while)
            break

    ss.update_estado(rodando=False, conectado=False)
    ss.log("Robô desconectado.", "warn")


def start_bot(ss: SessionState, token: str, valor_brl: float,
              estrategia: str, want_demo: bool = False):
    if ss.estado_snap.get("rodando"):
        return
    ss.stop_evt.clear()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _deriv_bot_async(ss, token, valor_brl, estrategia, want_demo)
            )
        except Exception as e:
            ss.log(f"Robô encerrado: {e}", "warn")
        finally:
            ss.update_estado(rodando=False, conectado=False)
            try: loop.close()
            except: pass

    ss.bot_thread = threading.Thread(target=run, daemon=True, name=f"bot-{ss.id[:6]}")
    ss.bot_thread.start()


def stop_bot(ss: SessionState):
    ss.stop_evt.set()
    ss.update_estado(rodando=False, conectado=False)
    ss.log("Parando robô…", "warn")


# ════════════════════════════════════════════════════════
#  ROTAS — AUTENTICAÇÃO
# ════════════════════════════════════════════════════════
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect("/")

    error = None
    tab   = "login"

    if request.method == "POST":
        action = request.form.get("action", "login")

        # ── CADASTRO ──────────────────────────────────────────────────────────
        if action == "register":
            tab      = "register"
            reg_open = os.environ.get("REGISTRATION_OPEN", "true").lower() != "false"
            if not reg_open:
                error = "Cadastro fechado. Contate o administrador."
            else:
                username = request.form.get("username", "").strip()
                email    = request.form.get("email", "").strip()
                password = request.form.get("password", "").strip()
                if not username or not email or not password:
                    error = "Preencha todos os campos."
                elif len(password) < 6:
                    error = "Senha com mínimo de 6 caracteres."
                else:
                    try:
                        with get_db() as conn:
                            conn.execute(
                                "INSERT INTO users (username,email,password_hash) VALUES (?,?,?)",
                                (username, email, generate_password_hash(password))
                            )
                            conn.commit()
                        with get_db() as conn:
                            user = conn.execute(
                                "SELECT * FROM users WHERE email=?", (email,)
                            ).fetchone()
                        session.permanent  = True
                        session["user_id"]  = user["id"]
                        session["username"] = user["username"]
                        session["is_admin"] = bool(user["is_admin"])
                        return redirect("/")
                    except sqlite3.IntegrityError:
                        error = "Email ou usuário já cadastrado."

        # ── LOGIN ─────────────────────────────────────────────────────────────
        else:
            email    = request.form.get("email", "").strip()
            password = request.form.get("password", "").strip()
            with get_db() as conn:
                user = conn.execute(
                    "SELECT * FROM users WHERE email=?", (email,)
                ).fetchone()
            if not user or not check_password_hash(user["password_hash"], password):
                error = "Email ou senha incorretos."
            elif not user["is_active"]:
                error = "Conta desativada. Contate o administrador."
            else:
                session.permanent  = True   # mantém login por 60 dias
                session["user_id"]  = user["id"]
                session["username"] = user["username"]
                session["is_admin"] = bool(user["is_admin"])
                return redirect("/")

    return render_template("login.html", error=error, tab=tab)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ════════════════════════════════════════════════════════
#  ROTAS — TOKEN DERIV (auto-salvo por usuário)
# ════════════════════════════════════════════════════════
@app.route("/api/token", methods=["GET"])
@login_required
def get_saved_token():
    """Retorna o token Deriv salvo para o usuário atual."""
    uid = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT deriv_token FROM users WHERE id=?", (uid,)
        ).fetchone()
    token = (row["deriv_token"] or "") if row else ""
    return jsonify({"ok": True, "token": token})


@app.route("/api/token/save", methods=["POST"])
@login_required
def save_token_manually():
    """Salva token Deriv colado manualmente pelo usuário."""
    data  = request.get_json(force=True) or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Token vazio"}), 400
    uid = session["user_id"]
    with get_db() as conn:
        conn.execute("UPDATE users SET deriv_token=? WHERE id=?", (token, uid))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/token/clear", methods=["POST"])
@login_required
def clear_saved_token():
    """Remove o token salvo do usuário."""
    uid = session["user_id"]
    with get_db() as conn:
        conn.execute("UPDATE users SET deriv_token='' WHERE id=?", (uid,))
        conn.commit()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
#  ROTAS — OAUTH DERIV
# ════════════════════════════════════════════════════════
@app.route("/oauth/deriv")
@login_required
def oauth_deriv_start():
    """Inicia o fluxo OAuth da Deriv."""
    if not DERIV_APP_ID:
        return redirect("/?oauth=no_app_id")
    # Monta redirect_uri dinamicamente com base no host atual
    redirect_uri = request.url_root.rstrip("/") + "/oauth/callback"
    oauth_url = (
        f"https://oauth.deriv.com/oauth2/authorize"
        f"?app_id={DERIV_APP_ID}&l=pt&brand=deriv"
        f"&redirect_uri={redirect_uri}"
    )
    return redirect(oauth_url)


@app.route("/oauth/callback")
@login_required
def oauth_deriv_callback():
    """Recebe o callback OAuth da Deriv, salva os tokens e redireciona."""
    # Deriv envia: ?acct1=CR...&token1=a1-...&cur1=USD&acct2=VRTC...&token2=a1-...&cur2=USD
    real_token  = None
    demo_token  = None
    i = 1
    while request.args.get(f"token{i}"):
        acct  = request.args.get(f"acct{i}", "")
        token = request.args.get(f"token{i}", "")
        # Contas virtuais começam com "VRTC" ou "VR"
        is_virtual = acct.upper().startswith(("VR", "VRTC"))
        if is_virtual and not demo_token:
            demo_token = token
        elif not is_virtual and not real_token:
            real_token = token
        i += 1

    token_to_save = real_token or demo_token
    if not token_to_save:
        return redirect("/?oauth=error")

    uid = session["user_id"]
    with get_db() as conn:
        conn.execute("UPDATE users SET deriv_token=? WHERE id=?", (token_to_save, uid))
        conn.commit()
    return redirect("/?oauth=success")


@app.route("/api/oauth/status")
@login_required
def oauth_status():
    """Informa ao frontend se OAuth Deriv está disponível."""
    return jsonify({"available": bool(DERIV_APP_ID)})


# ════════════════════════════════════════════════════════
#  ROTAS — WEBHOOK TICTO (recebe notificações de compra)
# ════════════════════════════════════════════════════════
@app.route("/webhook/ticto", methods=["POST"])
def ticto_webhook():
    """
    Endpoint que a Ticto chama quando alguém compra o curso.
    Configure no painel da Ticto → Produto → Configurações → Postback URL.
    Coloque a mesma chave em TICTO_WEBHOOK_SECRET no Railway.
    """
    payload = request.get_json(force=True) or request.form.to_dict() or {}

    # ── Validar token de segurança ────────────────────────────────────────────
    if TICTO_WEBHOOK_SECRET:
        token_recv = (payload.get("token") or payload.get("chave") or
                      request.headers.get("X-Ticto-Token", ""))
        if token_recv != TICTO_WEBHOOK_SECRET:
            print(f"[TICTO] Token inválido recebido: {token_recv[:20]}…", flush=True)
            return jsonify({"ok": False, "error": "token_invalido"}), 403

    # ── Extrair dados do pedido (Ticto pode variar o formato) ─────────────────
    tx      = payload.get("transaction") or payload.get("data") or payload
    buyer   = tx.get("buyer") or tx.get("customer") or tx.get("aluno") or {}
    email   = (buyer.get("email") or tx.get("email") or payload.get("email") or "").strip().lower()
    name    = buyer.get("name") or buyer.get("nome") or email.split("@")[0]
    tx_id   = str(tx.get("id") or tx.get("transaction_id") or payload.get("id") or "")
    event   = (payload.get("event") or payload.get("status") or "approved").lower()
    product = (tx.get("product") or tx.get("produto") or {})
    prod_id = str(product.get("id") or tx.get("product_id") or "")

    if not email:
        return jsonify({"ok": False, "error": "email_nao_encontrado"}), 400

    # ── Filtrar por produto (opcional) ───────────────────────────────────────
    if TICTO_PRODUCT_ID and prod_id and prod_id != TICTO_PRODUCT_ID:
        return jsonify({"ok": True, "msg": "produto_ignorado"}), 200

    # ── Classificar tipo de evento ────────────────────────────────────────────
    is_active   = any(s in event for s in ["approv", "ativ", "creat", "paid", "pago", "complet"])
    is_cancelled= any(s in event for s in ["cancel", "refund", "chargeback", "estorno"])

    # ── Data de expiração ─────────────────────────────────────────────────────
    sub_data   = tx.get("subscription") or tx.get("assinatura") or {}
    raw_expiry = sub_data.get("expires_at") or sub_data.get("expira_em") or ""
    if raw_expiry:
        try:
            expires_at = datetime.fromisoformat(raw_expiry.replace(" ", "T"))
        except ValueError:
            expires_at = datetime.utcnow() + timedelta(days=TICTO_DAYS)
    else:
        expires_at = datetime.utcnow() + timedelta(days=TICTO_DAYS)

    # ── Aplicar no banco ──────────────────────────────────────────────────────
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()

        if not user:
            # Criar conta pendente — usuário define senha no primeiro login
            import secrets as _sec
            tmp_hash = generate_password_hash(_sec.token_hex(16))
            uname = (name[:20] if name else email.split("@")[0][:20]).replace(" ", "_")
            conn.execute(
                "INSERT OR IGNORE INTO users (username, email, password_hash) VALUES (?,?,?)",
                (uname, email, tmp_hash)
            )
            conn.commit()
            user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()

        uid = user["id"]
        if is_cancelled:
            conn.execute("UPDATE users SET ticto_authorized=0 WHERE id=?", (uid,))
            print(f"[TICTO] ❌ Acesso revogado: {email}", flush=True)
        elif is_active:
            conn.execute(
                """UPDATE users SET ticto_authorized=1,
                   ticto_expires_at=?, ticto_transaction_id=? WHERE id=?""",
                (expires_at.isoformat(), tx_id, uid)
            )
            print(f"[TICTO] ✅ Acesso liberado: {email} até {expires_at.date()}", flush=True)
        conn.commit()

    return jsonify({"ok": True, "email": email, "event": event}), 200


# ════════════════════════════════════════════════════════
#  ROTAS — STATUS DE ASSINATURA (frontend)
# ════════════════════════════════════════════════════════
@app.route("/api/subscription")
@login_required
def api_subscription():
    uid      = session["user_id"]
    is_admin = session.get("is_admin", False)
    allowed, reason = check_subscription(uid, is_admin)
    with get_db() as conn:
        row = conn.execute(
            "SELECT ticto_authorized, ticto_expires_at FROM users WHERE id=?", (uid,)
        ).fetchone()
    return jsonify({
        "ok":         True,
        "allowed":    allowed,
        "reason":     reason,
        "expires_at": (row["ticto_expires_at"] if row else None),
        "mode":       TICTO_MODE,
        "course_url": TICTO_COURSE_URL,
    })


# ════════════════════════════════════════════════════════
#  ROTAS — ADMIN
# ════════════════════════════════════════════════════════
@app.route("/admin")
@admin_required
def admin_panel():
    with get_db() as conn:
        users = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ).fetchall()
    return render_template("admin.html", users=users,
                           me=session["user_id"],
                           reg_open=os.environ.get("REGISTRATION_OPEN","true").lower()!="false")


@app.route("/admin/toggle/<int:user_id>", methods=["POST"])
@admin_required
def toggle_user(user_id):
    if user_id == session["user_id"]:
        return jsonify({"ok": False, "error": "Não pode desativar a si mesmo"}), 400
    with get_db() as conn:
        conn.execute("UPDATE users SET is_active = 1 - is_active WHERE id=?", (user_id,))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/admin/toggle_admin/<int:user_id>", methods=["POST"])
@admin_required
def toggle_admin(user_id):
    if user_id == session["user_id"]:
        return jsonify({"ok": False, "error": "Não pode alterar seu próprio admin"}), 400
    with get_db() as conn:
        conn.execute("UPDATE users SET is_admin = 1 - is_admin WHERE id=?", (user_id,))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session["user_id"]:
        return jsonify({"ok": False, "error": "Não pode deletar a si mesmo"}), 400
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/admin/authorize/<int:user_id>", methods=["POST"])
@admin_required
def admin_authorize(user_id):
    """Autoriza manualmente (sem precisar da Ticto). Útil para testes ou cortesias."""
    data    = request.get_json(force=True) or {}
    days    = int(data.get("days", TICTO_DAYS))
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET ticto_authorized=1, ticto_expires_at=?, ticto_transaction_id='admin' WHERE id=?",
            (expires, user_id)
        )
        conn.commit()
    return jsonify({"ok": True, "expires_at": expires})


@app.route("/admin/deauthorize/<int:user_id>", methods=["POST"])
@admin_required
def admin_deauthorize(user_id):
    """Revoga acesso manualmente."""
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET ticto_authorized=0, ticto_expires_at=NULL WHERE id=?",
            (user_id,)
        )
        conn.commit()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
#  ROTAS — APLICAÇÃO PRINCIPAL
# ════════════════════════════════════════════════════════
@app.route("/")
@login_required
def index():
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    uid      = session["user_id"]
    is_admin = session.get("is_admin", False)
    allowed, reason = check_subscription(uid, is_admin)
    with get_db() as conn:
        row = conn.execute(
            "SELECT ticto_expires_at FROM users WHERE id=?", (uid,)
        ).fetchone()
    expires_at = (row["ticto_expires_at"] if row else None) or ""
    return render_template("index.html",
                           username=session.get("username", ""),
                           is_admin=is_admin,
                           sub_allowed=allowed,
                           sub_reason=reason,
                           sub_expires=expires_at,
                           course_url=TICTO_COURSE_URL or "#",
                           ticto_mode=TICTO_MODE)


@app.route("/manifest.json")
def manifest():
    return render_template("manifest.json"), 200, {"Content-Type": "application/json"}


@app.route("/api/estado")
@login_required
def api_estado():
    sid = session.get("sid", "")
    ss  = get_or_create_session(sid)
    return jsonify(ss.estado_snap)


@app.route("/api/start", methods=["POST"])
@login_required
@subscription_required
def api_start():
    data = request.get_json(force=True) or {}

    # CRÍTICO: usar o SID que veio do cliente (localStorage)
    # O WebSocket do browser conecta com ESSE SID.
    # Se usarmos session["sid"] (diferente), o bot roda num SessionState
    # diferente do que o WS está escutando → logs nunca aparecem na tela.
    sid = data.get("sid", "").strip() or session.get("sid") or uuid.uuid4().hex
    session["sid"] = sid  # manter sessão sincronizada

    ss   = get_or_create_session(sid)

    token      = data.get("token", "").strip()
    stake      = max(0.5, float(data.get("valor", 2.5)))
    estrategia = data.get("estrategia", "moderada")
    want_demo  = bool(data.get("demo", True))

    if not token:
        return jsonify({"ok": False, "error": "Token API obrigatório"}), 400
    if ss.estado_snap.get("rodando"):
        return jsonify({"ok": False, "error": "Robô já está rodando"}), 400

    # Salva o token para o usuário (auto-preenchimento futuro)
    uid = session["user_id"]
    try:
        with get_db() as conn:
            conn.execute("UPDATE users SET deriv_token=? WHERE id=?", (token, uid))
            conn.commit()
    except Exception:
        pass

    try:
        start_bot(ss, token, stake, estrategia, want_demo)
        return jsonify({"ok": True, "sid": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    data = request.get_json(force=True) or {}
    # Também usa o SID do cliente para encontrar o bot certo
    sid = data.get("sid", "").strip() or session.get("sid", "")
    if sid and sid in _sessions:
        stop_bot(_sessions[sid])
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
#  WEBSOCKET
# ════════════════════════════════════════════════════════
@sock.route("/ws/<sid>")
def ws_handler(ws, sid):
    ss = get_or_create_session(sid)
    ws.send(json.dumps({"type": "estado", "data": dict(ss.estado_snap)}))
    with ss.ws_lock:
        ss.ws_clients.append(ws)
    try:
        while True:
            msg = ws.receive(timeout=30)
            # timeout retorna None — NÃO fechar, apenas continuar (keepalive)
            # Só break se receber None depois de uma exceção (conexão fechada de vez)
            if msg is None:
                continue
    except Exception:
        pass
    finally:
        with ss.ws_lock:
            if ws in ss.ws_clients:
                ss.ws_clients.remove(ws)


# ════════════════════════════════════════════════════════
#  INICIAR SERVIDOR
# ════════════════════════════════════════════════════════
init_db()


def run_server(host="0.0.0.0", port=5000):
    app.run(host=host, port=port, debug=False,
            use_reloader=False, threaded=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    run_server(host="0.0.0.0", port=port)

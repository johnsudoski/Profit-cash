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

ASSETS = ["R_75", "R_100", "R_50", "R_25", "R_10"]
ASSET_NAMES = {
    "R_75":  "Volatility 75",
    "R_100": "Volatility 100",
    "R_50":  "Volatility 50",
    "R_25":  "Volatility 25",
    "R_10":  "Volatility 10",
}
STRATEGIES = {
    # stop_diario_pct: para o robô se a perda acumulada passar X% do saldo inicial
    # rsi_upper/lower mais extremos = menos operações, mais qualidade
    "cautelosa": {"duracao": 5, "rsi_upper": 75, "rsi_lower": 25, "conf_min": 0.78, "stop_diario_pct": 0.15},
    "moderada":  {"duracao": 3, "rsi_upper": 70, "rsi_lower": 30, "conf_min": 0.70, "stop_diario_pct": 0.25},
    "agressiva": {"duracao": 1, "rsi_upper": 65, "rsi_lower": 35, "conf_min": 0.62, "stop_diario_pct": 0.35},
}


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


def calc_signal(prices: list, config: dict):
    """
    Sinal v3 — Reversão confirmada (4 filtros):

      FILTRO 1 — RSI REVERSÃO (obrigatório, +0.40)
        Não basta RSI estar extremo — ele precisa ter PICADO e estar VIRANDO.
        • PUT: RSI estava acima do limite em t-2 e t-1, e agora caiu (peak confirmado)
        • CALL: RSI estava abaixo do limite em t-2 e t-1, e agora subiu (bottom confirmado)

      FILTRO 2 — Bollinger Bands (obrigatório, +0.25)
        Preço DEVE estar tocando a banda. Se não estiver, não há extremo real.

      FILTRO 3 — Stochastic %K/%D (confirmação, +0.20)
        %K e %D acima de 80 (overbought) para PUT, abaixo de 20 para CALL.

      FILTRO 4 — EMA 9/21 direção (+0.15)
        Confirmação de tendência de curto prazo.

    Confiança máxima: 1.00
    Filtros 1 e 2 são OBRIGATÓRIOS — sem eles não opera.
    """
    # Precisa de dados suficientes para RSI em t, t-1, t-2 + Stoch
    if len(prices) < 35:
        return None, 0.0, "", []

    # RSI em 3 momentos para detectar reversão
    rsi_now  = calc_rsi(prices,      14)
    rsi_prev = calc_rsi(prices[:-1], 14)
    rsi_2ago = calc_rsi(prices[:-2], 14)

    ema_fast           = calc_ema(prices, 9)
    ema_slow           = calc_ema(prices, 21)
    _, bb_upper, bb_lower = calc_bb(prices, 20, 2.0)
    stoch_k, stoch_d   = calc_stoch(prices, 14, 3)
    price_now          = prices[-1]

    direction = None
    conf      = 0.0
    motivo    = ""
    votos     = []

    # ── FILTRO 1: RSI REVERSÃO (obrigatório) ──────────────────────────────────
    # PUT: RSI estava acima do limite e agora está caindo
    put_rsi_peak  = (rsi_2ago >= config["rsi_upper"] and
                     rsi_prev  >= config["rsi_upper"] and
                     rsi_now   <  rsi_prev)
    # CALL: RSI estava abaixo do limite e agora está subindo
    call_rsi_bottom = (rsi_2ago <= config["rsi_lower"] and
                       rsi_prev  <= config["rsi_lower"] and
                       rsi_now   >  rsi_prev)

    if put_rsi_peak:
        direction = "PUT"
        conf     += 0.40
        motivo    = f"RSI reverteu de {rsi_prev:.1f}→{rsi_now:.1f} (pico confirmado)"
        votos.append(f"RSI↓ {rsi_now:.0f}")
    elif call_rsi_bottom:
        direction = "CALL"
        conf     += 0.40
        motivo    = f"RSI reverteu de {rsi_prev:.1f}→{rsi_now:.1f} (fundo confirmado)"
        votos.append(f"RSI↑ {rsi_now:.0f}")
    else:
        return None, 0.0, "", []  # sem reversão confirmada → não opera

    # ── FILTRO 2: BOLLINGER BANDS (obrigatório) ───────────────────────────────
    if bb_upper is None or bb_lower is None:
        return None, 0.0, "", []  # dados insuficientes

    bb_range   = bb_upper - bb_lower
    bb_margin  = bb_range * 0.05          # 5% de tolerância na banda

    if direction == "CALL" and price_now <= (bb_lower + bb_margin):
        conf += 0.25; votos.append("BB banda ↓ ✓")
    elif direction == "PUT" and price_now >= (bb_upper - bb_margin):
        conf += 0.25; votos.append("BB banda ↑ ✓")
    else:
        # Preço não está na banda — sinal fraco, não opera
        return None, 0.0, "", []

    # ── FILTRO 3: STOCHASTIC (+0.20) ──────────────────────────────────────────
    if direction == "CALL" and stoch_k < 25 and stoch_d < 30:
        conf += 0.20; votos.append(f"STOCH↑ {stoch_k:.0f}")
    elif direction == "PUT" and stoch_k > 75 and stoch_d > 70:
        conf += 0.20; votos.append(f"STOCH↓ {stoch_k:.0f}")
    else:
        votos.append(f"STOCH✗ {stoch_k:.0f}")

    # ── FILTRO 4: EMA 9/21 (+0.15) ────────────────────────────────────────────
    if direction == "CALL" and ema_fast > ema_slow:
        conf += 0.15; votos.append("EMA ↑")
    elif direction == "PUT" and ema_fast < ema_slow:
        conf += 0.15; votos.append("EMA ↓")
    else:
        votos.append("EMA ✗")

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
    active_cx   = {}   # contract_id -> {tid, buy_price}
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
    perf = {a: {"hist": [], "conf_adj": 0.0, "pause_until": 0.0} for a in ASSETS}

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
                ss.log(f"💱 R${valor_brl:.2f} → {currency} {stake:.2f}", "info")

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
                    pending_p[rid] = {
                        "asset": asset, "direction": direction,
                        "conf": conf, "motivo": motivo
                    }
                    await dws.send(json.dumps({
                        "proposal":       1,
                        "req_id":         rid,          # inteiro
                        "amount":         stake,
                        "basis":          "stake",
                        "contract_type":  direction,    # "CALL" ou "PUT"
                        "currency":       currency,
                        "duration":       config["duracao"],
                        "duration_unit":  "m",
                        "symbol":         asset,
                    }))
                    aname = ASSET_NAMES.get(asset, asset)
                    ss.log(f"📡 Proposta enviada: {direction} {aname} (req#{rid})", "info")

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
                    # ── Aprendizado adaptativo ────────────────────────────
                    # Registra resultado por ativo para ajustar threshold
                    perf[asset_cx]["hist"].append(1 if won else 0)
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
                        if len(tick_buf[asset]) > 200:
                            tick_buf[asset] = tick_buf[asset][-200:]

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
                            # Log do aprendizado adaptativo por ativo
                            for _a, _p in perf.items():
                                if not _p["hist"]: continue
                                _wr  = sum(_p["hist"]) / len(_p["hist"])
                                _adj = _p["conf_adj"]
                                _lbl = "🟢" if _wr >= 0.60 else ("🟡" if _wr >= 0.45 else "🔴")
                                ss.log(
                                    f"🧠 {ASSET_NAMES.get(_a,_a)}: "
                                    f"acerto {_wr*100:.0f}% ({len(_p['hist'])} ops) | "
                                    f"threshold adj {_adj:+.2f}",
                                    "info"
                                )

                        # ── Uma operação de cada vez ───────────────────────
                        # Só abre nova trade se não há contrato ativo nem proposta pendente
                        if active_cx or pending_p or pending_buy:
                            continue

                        # ── Pausa adaptativa por ativo ──────────────────────
                        if now < perf[asset].get("pause_until", 0):
                            continue

                        cooldown = config["duracao"] * 60 + 15
                        if now - last_trade[asset] < cooldown:
                            continue

                        direction, conf, motivo, votos = calc_signal(tick_buf[asset], config)
                        # Threshold adaptativo: base + ajuste aprendido para este ativo
                        conf_threshold = config["conf_min"] + perf[asset]["conf_adj"]
                        if direction and conf >= conf_threshold:
                            aname = ASSET_NAMES.get(asset, asset)
                            ss.sinal(aname, direction, conf, motivo, votos)
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
                            expires_at  = time.time() + config["duracao"] * 60 + 20
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
    stake      = max(1.0, float(data.get("valor", 5.0)))
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

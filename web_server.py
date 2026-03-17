"""
Profit Cash — Servidor Web (Deriv API + Multi-User Auth)
"""
import asyncio, json, os, sqlite3, sys, threading, time, uuid, math
from functools import wraps
from flask import (Flask, render_template, jsonify, request,
                   session, redirect, url_for)
from flask_sock import Sock
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app  = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", uuid.uuid4().hex)
sock = Sock(app)

# Deriv OAuth — configure DERIV_APP_ID no Railway (env var)
# Cadastro gratuito em: https://api.deriv.com/ → Register app
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "")
DERIV_WS     = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID or '1089'}"

ASSETS = ["R_75", "R_100", "R_50", "R_25", "R_10"]
ASSET_NAMES = {
    "R_75":  "Volatility 75",
    "R_100": "Volatility 100",
    "R_50":  "Volatility 50",
    "R_25":  "Volatility 25",
    "R_10":  "Volatility 10",
}
STRATEGIES = {
    "cautelosa": {"duracao": 5, "rsi_upper": 72, "rsi_lower": 28, "conf_min": 0.68},
    "moderada":  {"duracao": 3, "rsi_upper": 67, "rsi_lower": 33, "conf_min": 0.60},
    "agressiva": {"duracao": 1, "rsi_upper": 62, "rsi_lower": 38, "conf_min": 0.52},
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
        # Migração para bancos antigos sem deriv_token
        try:
            conn.execute("ALTER TABLE users ADD COLUMN deriv_token TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Coluna já existe
        conn.commit()

    # Criar admin a partir de variáveis de ambiente
    admin_user = os.environ.get("ADMIN_USERNAME", "")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if admin_user and admin_pass:
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO users "
                    "(username, email, password_hash, is_admin) VALUES (?,?,?,1)",
                    (admin_user, f"{admin_user}@admin.local",
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

    def resultado(self, tid, resultado, lucro):
        self.broadcast({"type": "resultado", "id": str(tid),
                        "resultado": resultado, "lucro": round(lucro, 2)})


_sessions: dict[str, SessionState] = {}
_sessions_lock = threading.Lock()


def get_or_create_session(sid: str) -> SessionState:
    with _sessions_lock:
        if sid not in _sessions:
            _sessions[sid] = SessionState(sid)
        return _sessions[sid]


# ════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS
# ════════════════════════════════════════════════════════
def calc_rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def calc_sma(prices: list, period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    return sum(prices[-period:]) / period


def calc_signal(prices: list, config: dict):
    if len(prices) < 21:
        return None, 0.0, "", []
    rsi      = calc_rsi(prices, 14)
    sma_fast = calc_sma(prices, 5)
    sma_slow = calc_sma(prices, 20)
    direction = None
    conf      = 0.0
    motivo    = ""
    votos     = []
    if rsi > config["rsi_upper"]:
        direction = "PUT";  conf += 0.40
        motivo = f"RSI sobrecomprado ({rsi:.1f})"; votos.append(f"RSI {rsi:.0f}")
    elif rsi < config["rsi_lower"]:
        direction = "CALL"; conf += 0.40
        motivo = f"RSI sobrevendido ({rsi:.1f})";  votos.append(f"RSI {rsi:.0f}")
    else:
        return None, 0.0, "", []
    if direction == "CALL" and sma_fast > sma_slow:
        conf += 0.22; votos.append("SMA ↑")
    elif direction == "PUT" and sma_fast < sma_slow:
        conf += 0.22; votos.append("SMA ↓")
    else:
        conf -= 0.08; votos.append("SMA ✗")
    recent = prices[-4:]
    ups    = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
    downs  = 3 - ups
    if direction == "CALL" and downs >= 2:
        conf += 0.15; votos.append("MOM ↓→↑")
    elif direction == "PUT" and ups >= 2:
        conf += 0.15; votos.append("MOM ↑→↓")
    else:
        votos.append("MOM ✗")
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
    last_status = [time.time()]

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
                if currency == "BRL":
                    stake = valor_brl
                else:
                    # Pede taxa com base em USD (Deriv não aceita BRL como base)
                    req_ctr[0] += 1
                    await dws.send(json.dumps({
                        "exchange_rates": 1,
                        "base_currency": currency,  # ex: "USD"
                        "req_id": req_ctr[0],
                    }))
                    rate_brl = 0.0
                    try:
                        # Procura a mensagem exchange_rates (ignora outras que possam vir)
                        for _ in range(5):
                            raw_er = await asyncio.wait_for(dws.recv(), timeout=10)
                            er_msg = json.loads(raw_er)
                            if er_msg.get("msg_type") == "exchange_rates":
                                rates    = (er_msg.get("exchange_rates") or {}).get("rates", {})
                                rate_brl = float(rates.get("BRL", 0))
                                break
                    except Exception:
                        pass

                    if rate_brl > 0:
                        # rate_brl = quantos BRL vale 1 unidade de currency
                        # ex: 1 USD = 5.70 BRL → stake = 10 / 5.70 = 1.75 USD
                        stake = max(round(valor_brl / rate_brl, 2), 0.35)
                        ss.log(f"💱 R${valor_brl:.2f} → {currency} {stake:.2f} "
                               f"(câmbio: 1 {currency} = R${rate_brl:.2f})", "info")
                    else:
                        # Fallback silencioso com taxa aproximada
                        _fallback = {"USD": 5.70, "EUR": 6.20, "GBP": 7.20}
                        rate_brl  = _fallback.get(currency, 5.70)
                        stake     = max(round(valor_brl / rate_brl, 2), 0.35)
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

                # ── LOOP PRINCIPAL ────────────────────────────────────────
                while not ss.stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(dws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        if ss.stop_evt.is_set(): break
                        await dws.send(json.dumps({"ping": 1}))
                        # Status periódico a cada 60s
                        now = time.time()
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

                        n_ticks = len(tick_buf[asset])
                        if n_ticks < 21:
                            continue  # Aguarda histórico suficiente

                        # Status periódico de diagnóstico
                        now = time.time()
                        if now - last_status[0] > 60:
                            last_status[0] = now
                            rsi_now = calc_rsi(tick_buf[asset], 14)
                            ss.log(f"🔍 RSI {ASSET_NAMES.get(asset,asset)}: {rsi_now:.1f} "
                                   f"(limite: <{config['rsi_lower']} ou >{config['rsi_upper']})", "info")

                        cooldown = config["duracao"] * 60 + 15
                        if now - last_trade[asset] < cooldown:
                            continue

                        direction, conf, motivo, votos = calc_signal(tick_buf[asset], config)
                        if direction and conf >= config["conf_min"]:
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

                        prop      = msg.get("proposal") or {}
                        pid       = prop.get("id", "")
                        ask_price = float(prop.get("ask_price", stake))  # Preço real da Deriv

                        if not pid:
                            ss.log("⚠️ Proposta sem ID recebida", "warn")
                            continue

                        trade_count += 1
                        req_ctr[0] += 1
                        buy_rid = req_ctr[0]  # Inteiro para o buy também

                        aname = ASSET_NAMES.get(info["asset"], info["asset"])
                        ss.trade(f"T{trade_count}", aname, info["direction"].lower(), stake)
                        ss.log(f"🛒 Comprando contrato: {info['direction']} {aname} "
                               f"R${valor_brl:.2f} (ask: {currency} {ask_price:.2f})", "info")

                        # FIX: usa ask_price, não stake (evita rejeição por preço incorreto)
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

                        bd  = msg.get("buy", {})
                        cid = str(bd.get("contract_id", ""))
                        bp  = float(bd.get("buy_price", stake))

                        if cid:
                            tid = f"T{trade_count}"
                            active_cx[cid] = {"tid": tid, "buy_price": bp}
                            ss.log(f"✅ Contrato #{cid} aberto — ${bp:.2f}", "win")
                            # Subscrição individual por contrato (evita "Unrecognised request")
                            req_ctr[0] += 1
                            await dws.send(json.dumps({
                                "proposal_open_contracts": 1,
                                "contract_id": int(cid),
                                "subscribe": 1,
                                "req_id": req_ctr[0],
                            }))
                        continue

                    # ── RESULTADO DO CONTRATO ──────────────────────────────
                    if mtype == "proposal_open_contracts":
                        poc  = msg.get("proposal_open_contracts", {})
                        cid  = str(poc.get("contract_id", ""))
                        stat = poc.get("status", "")

                        if not cid or cid not in active_cx:
                            continue
                        if stat not in ("won", "lost", "sold"):
                            continue  # Contrato ainda aberto

                        info   = active_cx.pop(cid)
                        profit = float(poc.get("profit", 0))
                        bp     = info["buy_price"]
                        tid    = info["tid"]

                        if stat == "won":
                            wins        += 1
                            lucro_total += profit
                            ss.log(f"✅ WIN  +${profit:.2f} | Total: ${lucro_total:+.2f}", "win")
                            ss.resultado(tid, "W", profit)
                        else:
                            losses      += 1
                            lucro_total -= bp
                            ss.log(f"❌ LOSS −${bp:.2f} | Total: ${lucro_total:+.2f}", "loss")
                            ss.resultado(tid, "L", -bp)

                        ss.update_estado(wins=wins, losses=losses, lucro=lucro_total)
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


# ════════════════════════════════════════════════════════
#  ROTAS — APLICAÇÃO PRINCIPAL
# ════════════════════════════════════════════════════════
@app.route("/")
@login_required
def index():
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return render_template("index.html",
                           username=session.get("username", ""),
                           is_admin=session.get("is_admin", False))


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

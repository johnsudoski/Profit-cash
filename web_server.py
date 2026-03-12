"""
Profit Cash — Servidor Web (Deriv API)
Cada cliente tem sua própria sessão independente com a Deriv.
"""
import asyncio, json, os, sys, threading, time, uuid, math
from flask import Flask, render_template, jsonify, request, session
from flask_sock import Sock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app  = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", uuid.uuid4().hex)
sock = Sock(app)

# ── Deriv WebSocket endpoint ────────────────────────────────────────────────
# app_id=1089 is the public Deriv demo app — works for testing.
# For production, register at https://developers.deriv.com
DERIV_WS = "wss://ws.binaryws.com/websockets/v3?app_id=1089"

# Synthetic Indices — 24/7, no market hours restrictions
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
#  SESSÕES
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
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def calc_sma(prices: list, period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    return sum(prices[-period:]) / period


def calc_signal(prices: list, config: dict) -> tuple:
    """Returns (direction, confidence, motivo, votos) or (None, 0, '', [])"""
    if len(prices) < 21:
        return None, 0.0, "", []

    rsi      = calc_rsi(prices, 14)
    sma_fast = calc_sma(prices, 5)
    sma_slow = calc_sma(prices, 20)

    direction = None
    conf      = 0.0
    motivo    = ""
    votos     = []

    # ── RSI signal (primary) ───────────────────────────────────────────────
    if rsi > config["rsi_upper"]:
        direction = "PUT"
        conf     += 0.40
        motivo    = f"RSI sobrecomprado ({rsi:.1f})"
        votos.append(f"RSI {rsi:.0f}")
    elif rsi < config["rsi_lower"]:
        direction = "CALL"
        conf     += 0.40
        motivo    = f"RSI sobrevendido ({rsi:.1f})"
        votos.append(f"RSI {rsi:.0f}")
    else:
        return None, 0.0, "", []

    # ── SMA trend confirmation ─────────────────────────────────────────────
    if direction == "CALL" and sma_fast > sma_slow:
        conf += 0.22
        votos.append("SMA ↑")
    elif direction == "PUT" and sma_fast < sma_slow:
        conf += 0.22
        votos.append("SMA ↓")
    else:
        conf -= 0.08
        votos.append("SMA ✗")

    # ── Momentum (last 4 ticks) ────────────────────────────────────────────
    recent = prices[-4:]
    ups    = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
    downs  = 3 - ups
    if direction == "CALL" and downs >= 2:
        conf += 0.15
        votos.append("MOM ↓→↑")
    elif direction == "PUT" and ups >= 2:
        conf += 0.15
        votos.append("MOM ↑→↓")
    else:
        votos.append("MOM ✗")

    conf = max(0.0, min(1.0, conf))
    return direction, conf, motivo, votos


# ════════════════════════════════════════════════════════
#  BOT DERIV
# ════════════════════════════════════════════════════════
async def _deriv_bot_async(ss: SessionState, token: str, stake: float, estrategia: str):
    try:
        import websockets
    except ImportError:
        ss.log("❌ Biblioteca 'websockets' não instalada.", "loss")
        ss.log("→ Execute: pip install websockets", "warn")
        return

    config = STRATEGIES.get(estrategia, STRATEGIES["moderada"])

    lucro_total = 0.0
    wins        = 0
    losses      = 0
    tick_buf    = {a: [] for a in ASSETS}
    last_trade  = {a: 0.0 for a in ASSETS}         # timestamp of last trade per asset
    active_cx   = {}                                # contract_id → {tid, buy_price}
    pending_p   = {}                                # req_id → info
    trade_count = 0
    currency    = "USD"

    ss.log("Conectando à Deriv…", "info")
    ss.update_estado(rodando=True)

    try:
        async with websockets.connect(
            DERIV_WS,
            ping_interval=20,
            ping_timeout=30,
            open_timeout=20,
        ) as dws:
            ss.log("WebSocket Deriv estabelecido.", "info")

            # ── Autorizar ────────────────────────────────────────────────
            await dws.send(json.dumps({"authorize": token}))
            raw = await asyncio.wait_for(dws.recv(), timeout=20)
            auth = json.loads(raw)

            if "error" in auth:
                err = auth["error"].get("message", "Token inválido")
                ss.log(f"❌ Autenticação falhou: {err}", "loss")
                ss.log("→ Gere um token em app.deriv.com → Configurações → Token API", "warn")
                return

            acct    = auth.get("authorize", {})
            loginid = acct.get("loginid", "?")
            saldo0  = float(acct.get("balance", 0))
            currency = acct.get("currency", "USD")
            is_virt = acct.get("is_virtual", False)

            tipo = "Demo 🧪" if is_virt else "Real 💰"
            ss.log(f"✅ Conta {loginid} ({tipo}) autenticada!", "win")
            ss.log(f"💰 Saldo: {saldo0:.2f} {currency}", "info")
            ss.update_estado(saldo=saldo0, conectado=True)

            # ── Subscribe to balance ──────────────────────────────────
            await dws.send(json.dumps({"balance": 1, "subscribe": 1}))

            # ── Subscribe to ticks ────────────────────────────────────
            for asset in ASSETS:
                await dws.send(json.dumps({"ticks": asset, "subscribe": 1}))
                await asyncio.sleep(0.05)

            # ── Subscribe to open contracts ───────────────────────────
            await dws.send(json.dumps({"proposal_open_contracts": 1, "subscribe": 1}))

            ss.log(f"📊 Monitorando {len(ASSETS)} ativos | Estratégia: {estrategia}", "info")

            # ── Helper: request a proposal ────────────────────────────
            async def request_proposal(asset, direction, conf, motivo):
                nonlocal pending_p
                rid = uuid.uuid4().hex[:10]
                pending_p[rid] = {
                    "asset": asset, "direction": direction,
                    "conf": conf,   "motivo": motivo, "stake": stake,
                }
                await dws.send(json.dumps({
                    "proposal": 1,
                    "req_id": rid,
                    "amount": stake,
                    "basis": "stake",
                    "contract_type": direction,
                    "currency": currency,
                    "duration": config["duracao"],
                    "duration_unit": "m",
                    "symbol": asset,
                }))

            # ── Main loop ─────────────────────────────────────────────
            while not ss.stop_evt.is_set():
                try:
                    raw = await asyncio.wait_for(dws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    if ss.stop_evt.is_set():
                        break
                    await dws.send(json.dumps({"ping": 1}))
                    continue

                msg  = json.loads(raw)
                mtype = msg.get("msg_type", "")

                # ── Error ────────────────────────────────────────────
                if mtype == "error" or "error" in msg:
                    em = (msg.get("error") or {}).get("message", "erro")
                    ss.log(f"⚠️ Deriv: {em}", "warn")
                    continue

                # ── Balance update ────────────────────────────────────
                if mtype == "balance":
                    b = float(msg.get("balance", {}).get("balance", 0))
                    ss.update_estado(saldo=b)
                    continue

                # ── Tick ──────────────────────────────────────────────
                if mtype == "tick":
                    tick  = msg.get("tick", {})
                    asset = tick.get("symbol", "")
                    price = float(tick.get("quote", 0))
                    if asset not in tick_buf:
                        continue

                    tick_buf[asset].append(price)
                    if len(tick_buf[asset]) > 120:
                        tick_buf[asset] = tick_buf[asset][-120:]

                    # ── Analyze signal ───────────────────────────────
                    now      = time.time()
                    cooldown = config["duracao"] * 60 + 30
                    if now - last_trade[asset] < cooldown:
                        continue

                    direction, conf, motivo, votos = calc_signal(tick_buf[asset], config)
                    if direction and conf >= config["conf_min"]:
                        aname = ASSET_NAMES.get(asset, asset)
                        ss.sinal(aname, direction, conf, motivo, votos)
                        last_trade[asset] = now
                        await request_proposal(asset, direction, conf, motivo)
                    continue

                # ── Proposal response ─────────────────────────────────
                if mtype == "proposal":
                    rid = msg.get("req_id", "")
                    if rid not in pending_p:
                        continue
                    info = pending_p.pop(rid)
                    if "error" in msg:
                        ss.log(f"⚠️ Proposta recusada: {msg['error'].get('message','?')}", "warn")
                        continue
                    pid = (msg.get("proposal") or {}).get("id", "")
                    if not pid:
                        continue

                    # Buy it
                    trade_count += 1
                    tid = f"T{trade_count}"
                    aname = ASSET_NAMES.get(info["asset"], info["asset"])
                    ss.trade(tid, aname, info["direction"].lower(), info["stake"])
                    ss.log(
                        f"🔄 {info['direction']} {aname} "
                        f"R${info['stake']:.2f} | {info['motivo']}", "info"
                    )
                    await dws.send(json.dumps({
                        "buy": pid,
                        "req_id": tid,
                        "price": info["stake"],
                    }))
                    continue

                # ── Buy confirmation ──────────────────────────────────
                if mtype == "buy":
                    if "error" in msg:
                        ss.log(f"❌ Compra recusada: {msg['error'].get('message','?')}", "loss")
                        continue
                    buy_data    = msg.get("buy", {})
                    contract_id = str(buy_data.get("contract_id", ""))
                    buy_price   = float(buy_data.get("buy_price", stake))
                    tid         = msg.get("req_id", "?")
                    if contract_id:
                        active_cx[contract_id] = {"tid": tid, "buy_price": buy_price}
                        ss.log(f"✅ Contrato {contract_id} aberto", "win")
                    continue

                # ── Contract result ───────────────────────────────────
                if mtype == "proposal_open_contracts":
                    poc  = msg.get("proposal_open_contracts", {})
                    cid  = str(poc.get("contract_id", ""))
                    stat = poc.get("status", "")
                    if cid not in active_cx or stat not in ("won", "lost"):
                        continue
                    info      = active_cx.pop(cid)
                    profit    = float(poc.get("profit", 0))
                    buy_price = info["buy_price"]
                    tid       = info["tid"]
                    if stat == "won":
                        wins        += 1
                        lucro_total += profit
                        ss.log(f"✅ WIN  +R${profit:.2f}", "win")
                        ss.resultado(tid, "W", profit)
                    else:
                        losses      += 1
                        lucro_total -= buy_price
                        ss.log(f"❌ LOSS −R${buy_price:.2f}", "loss")
                        ss.resultado(tid, "L", -buy_price)
                    ss.update_estado(wins=wins, losses=losses, lucro=lucro_total)
                    continue

                # ── Pong ──────────────────────────────────────────────
                if mtype == "pong":
                    continue

    except Exception as e:
        if "websockets" in sys.modules:
            import websockets as _ws
            if isinstance(e, _ws.exceptions.ConnectionClosed):
                ss.log(f"❌ Conexão com Deriv perdida: {e}", "loss")
                return
        if isinstance(e, asyncio.CancelledError):
            ss.log("Bot encerrado.", "warn")
            return
        import traceback
        ss.log(f"❌ Erro: {e}", "loss")
        print(f"[BOT ERROR] {traceback.format_exc()}", flush=True)
    finally:
        ss.update_estado(rodando=False, conectado=False)
        ss.log("Robô desconectado.", "warn")


def start_bot(ss: SessionState, token: str, stake: float, estrategia: str):
    if ss.estado_snap.get("rodando"):
        ss.log("Robô já está rodando.", "warn")
        return

    ss.stop_evt.clear()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _deriv_bot_async(ss, token, stake, estrategia)
            )
        except Exception as e:
            ss.log(f"Robô encerrado: {e}", "warn")
        finally:
            ss.update_estado(rodando=False, conectado=False)
            try:
                loop.close()
            except Exception:
                pass

    ss.bot_thread = threading.Thread(target=run, daemon=True,
                                      name=f"bot-{ss.id[:6]}")
    ss.bot_thread.start()


def stop_bot(ss: SessionState):
    ss.stop_evt.set()
    ss.update_estado(rodando=False, conectado=False)
    ss.log("Parando robô…", "warn")


# ════════════════════════════════════════════════════════
#  ROTAS HTTP
# ════════════════════════════════════════════════════════
@app.route("/")
def index():
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return render_template("index.html")


@app.route("/manifest.json")
def manifest():
    return render_template("manifest.json"), 200, {"Content-Type": "application/json"}


@app.route("/api/estado")
def api_estado():
    sid = session.get("sid", "")
    ss  = get_or_create_session(sid)
    return jsonify(ss.estado_snap)


@app.route("/api/start", methods=["POST"])
def api_start():
    sid = session.get("sid") or request.json.get("sid", "")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid

    ss   = get_or_create_session(sid)
    data = request.get_json(force=True) or {}

    token      = data.get("token", "").strip()
    stake      = max(1.0, float(data.get("valor", 5.0)))
    estrategia = data.get("estrategia", "moderada")

    if not token:
        return jsonify({"ok": False, "error": "Token API obrigatório"}), 400

    if ss.estado_snap.get("rodando"):
        return jsonify({"ok": False, "error": "Robô já está rodando"}), 400

    try:
        start_bot(ss, token, stake, estrategia)
        return jsonify({"ok": True, "sid": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    sid = session.get("sid", "")
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
            if msg is None:
                break
    except Exception:
        pass
    finally:
        with ss.ws_lock:
            if ws in ss.ws_clients:
                ss.ws_clients.remove(ws)


# ════════════════════════════════════════════════════════
#  INICIAR SERVIDOR
# ════════════════════════════════════════════════════════
def run_server(host="0.0.0.0", port=5000):
    app.run(host=host, port=port, debug=False,
            use_reloader=False, threaded=True)

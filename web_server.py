"""
Profit Cash — Servidor Web
Cada cliente tem sua própria sessão e instância de robô independente.
"""
import asyncio, json, os, sys, threading, time, uuid, types
from flask import Flask, render_template, jsonify, request, session
from flask_sock import Sock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app  = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", uuid.uuid4().hex)
sock = Sock(app)

# ════════════════════════════════════════════════════════
#  SESSÕES
# ════════════════════════════════════════════════════════
class SessionState:
    def __init__(self, sid):
        self.id          = sid
        self.ws_clients  = []
        self.ws_lock     = threading.Lock()
        self.engine_mod  = None
        self.engine_inst = None
        self.engine_thrd = None
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
        self.broadcast({"type":"sinal","ativo":ativo,"action":action,
                        "conf":round(conf,4),"motivo":motivo,"votos":votos or []})

    def trade(self, tid, ativo, direcao, valor):
        self.broadcast({"type":"trade","id":str(tid),
                        "ativo":ativo,"direcao":direcao,"valor":valor})

    def resultado(self, tid, resultado, lucro):
        self.broadcast({"type":"resultado","id":str(tid),
                        "resultado":resultado,"lucro":round(lucro,2)})


_sessions: dict[str, SessionState] = {}
_sessions_lock = threading.Lock()

def get_or_create_session(sid: str) -> SessionState:
    with _sessions_lock:
        if sid not in _sessions:
            _sessions[sid] = SessionState(sid)
        return _sessions[sid]


# ════════════════════════════════════════════════════════
#  STUBS TKINTER (o engine original usa tkinter —
#  aqui rodamos no modo headless/web, então mockamos)
# ════════════════════════════════════════════════════════
def _install_tkinter_stubs():
    if "tkinter" in sys.modules:
        return

    class _W:
        def __init__(self,*a,**kw): pass
        def __call__(self,*a,**kw): return self
        def __getattr__(self,n):    return self
        def pack(self,*a,**kw):    pass
        def grid(self,*a,**kw):    pass
        def config(self,*a,**kw):  pass
        configure = config
        def after(self,ms,fn=None,*a):
            if fn: threading.Timer(ms/1000,fn,args=a).start()
        def mainloop(self):        pass
        def destroy(self):         pass
        def winfo_screenwidth(self): return 1920
        def winfo_screenheight(self):return 1080
        def get(self):             return ""
        def set(self,v):           pass
        def insert(self,*a,**kw):  pass
        def delete(self,*a,**kw):  pass
        def see(self,*a,**kw):     pass
        def tag_config(self,*a,**kw): pass
        def tag_add(self,*a,**kw): pass
        def yview(self,*a,**kw):   pass

    tk = types.ModuleType("tkinter")
    for n in ["Tk","Frame","Label","Button","Entry","Text","Canvas",
              "Scrollbar","StringVar","IntVar","BooleanVar","DoubleVar",
              "Toplevel","Menu","OptionMenu","Checkbutton","Radiobutton",
              "LabelFrame","PanedWindow","Scale","Spinbox","Listbox",
              "PhotoImage","END","BOTH","LEFT","RIGHT","TOP","BOTTOM",
              "X","Y","W","E","N","S","NW","NE","SW","SE","WORD",
              "DISABLED","NORMAL","FLAT","GROOVE","RIDGE","RAISED",
              "SUNKEN","HORIZONTAL","VERTICAL","INSERT","SEL","CURRENT",
              "ANCHOR","LAST","FIRST","ALL","CENTER","NONE"]:
        setattr(tk, n, _W)

    ttk = types.ModuleType("tkinter.ttk")
    for n in ["Notebook","Combobox","Progressbar","Treeview",
              "Separator","Style","Frame","Label","Button","Entry"]:
        setattr(ttk, n, _W)
    tk.ttk = ttk

    msg = types.ModuleType("tkinter.messagebox")
    for n in ["showinfo","showwarning","showerror","askyesno","askokcancel"]:
        setattr(msg, n, lambda *a,**kw: True)
    tk.messagebox = msg

    for name, mod in [
        ("tkinter", tk), ("tkinter.ttk", ttk),
        ("tkinter.messagebox", msg), ("tkinter.filedialog", msg),
        ("tkinter.simpledialog", msg), ("tkinter.font", msg),
        ("tkinter.scrolledtext", msg),
    ]:
        sys.modules[name] = mod

_install_tkinter_stubs()


# ════════════════════════════════════════════════════════
#  LOAD ENGINE (carregado uma vez, compartilhado)
# ════════════════════════════════════════════════════════
_engine_mod_cache = None
_engine_mod_lock  = threading.Lock()

def _load_engine():
    global _engine_mod_cache
    with _engine_mod_lock:
        if _engine_mod_cache is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "trading_engine", os.path.join(BASE_DIR, "app.py")
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _engine_mod_cache = mod
        return _engine_mod_cache


# ════════════════════════════════════════════════════════
#  START / STOP BOT
# ════════════════════════════════════════════════════════
def start_bot(ss: SessionState, email, senha, valor, demo, estrategia):
    mod = _load_engine()

    import copy
    est = copy.copy(mod.estado)
    est._lock    = threading.Lock()
    est.wins     = 0
    est.losses   = 0
    est.lucro    = 0.0
    est.rodando  = True
    est.conectado= False

    estr = dict(mod.ESTRATEGIAS.get(estrategia, mod.ESTRATEGIAS["moderada"]))
    estr["valor"] = max(1.0, float(valor))
    est.estrategia = estr
    est.demo       = demo

    instance = object.__new__(mod.App)
    instance._headless = True
    instance.estado    = est

    def _ui_log(msg, tag="info"):
        ss.log(msg, tag)

    instance._ui_log = _ui_log
    instance.log     = _ui_log
    instance.after   = lambda ms, fn=None, *a: (
        threading.Timer(ms/1000, fn, args=a).start() if fn else None
    )

    _orig = est.update
    def _patched(**kw):
        _orig(**kw)
        ss.update_estado(
            saldo    =est.saldo_atual,
            lucro    =est.lucro,
            wins     =est.wins,
            losses   =est.losses,
            rodando  =est.rodando,
            conectado=est.conectado,
        )
    est.update = _patched

    import types as _t
    orig_bot_main = mod.App._bot_main

    async def _bot_main_session(self):
        os.environ["QUOTEX_EMAIL"] = email
        os.environ["QUOTEX_SENHA"] = senha
        await orig_bot_main(self)

    instance._bot_main = _t.MethodType(_bot_main_session, instance)

    ss.engine_mod  = mod
    ss.engine_inst = instance

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(instance._bot_main())
        except Exception as e:
            ss.log(f"Robô encerrado: {e}", "warn")
        finally:
            est.rodando = False
            ss.update_estado(rodando=False)

    ss.engine_thrd = threading.Thread(target=run, daemon=True,
                                      name=f"engine-{ss.id[:6]}")
    ss.engine_thrd.start()


def stop_bot(ss: SessionState):
    if ss.engine_inst and ss.engine_mod:
        try:
            ss.engine_inst.estado.update(rodando=False)
        except Exception:
            pass
    ss.log("Robô parado.", "warn")


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
    sid  = session.get("sid") or request.json.get("sid", "")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid

    ss   = get_or_create_session(sid)
    data = request.get_json(force=True) or {}

    email      = data.get("email", "").strip()
    senha      = data.get("senha", "").strip()
    valor      = float(data.get("valor", 5.0))
    demo       = data.get("demo", True)
    estrategia = data.get("estrategia", "moderada")

    if not email or not senha:
        return jsonify({"ok": False, "error": "Email e senha obrigatórios"}), 400

    if ss.estado_snap.get("rodando"):
        return jsonify({"ok": False, "error": "Robô já está rodando"}), 400

    try:
        start_bot(ss, email, senha, valor, demo, estrategia)
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

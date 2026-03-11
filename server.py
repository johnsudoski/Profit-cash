"""
Profit Cash — Entry point para produção (Railway/Render)
Não abre browser, usa PORT do ambiente.
"""
import os
from web_server import run_server

PORT = int(os.environ.get("PORT", 5000))
print(f"\n  Profit Cash rodando na porta {PORT}\n", flush=True)
run_server(host="0.0.0.0", port=PORT)

"""
Profit Cash — Launcher
Inicia o servidor e abre o browser automaticamente.
"""
import threading, time, webbrowser, os, sys

PORT = int(os.environ.get("PORT", 5000))


def open_browser():
    time.sleep(1.8)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    from web_server import run_server

    t = threading.Thread(target=open_browser, daemon=True)
    t.start()

    print(f"\n  Profit Cash rodando em http://localhost:{PORT}")
    print("  Pressione Ctrl+C para encerrar.\n")

    run_server(host="0.0.0.0", port=PORT)

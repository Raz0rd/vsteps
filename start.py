#!/usr/bin/env python3
"""Inicia dashboard + worker em paralelo."""
import threading, time, sys, os, subprocess


def install_mitmproxy_cert():
    """Instala cert CA do mitmproxy no trust store do sistema."""
    cert_path = "/mitmproxy-certs/mitmproxy-ca-cert.pem"
    dest = "/usr/local/share/ca-certificates/mitmproxy-ca-cert.crt"
    for attempt in range(30):
        if os.path.isfile(cert_path):
            try:
                import shutil
                shutil.copy2(cert_path, dest)
                subprocess.run(["update-ca-certificates"], check=True, capture_output=True)
                print(f"✅ Cert mitmproxy instalado: {cert_path}")
                return True
            except Exception as e:
                print(f"⚠️ Erro instalando cert: {e}")
                return False
        print(f"⏳ Aguardando cert mitmproxy... ({attempt+1}/30)")
        time.sleep(2)
    print("❌ Cert mitmproxy não encontrado após 60s — proxy SSL pode falhar!")
    return False


def run_dashboard():
    from app import app
    from config import DASHBOARD_PORT
    print(f"🌐 Dashboard: http://0.0.0.0:{DASHBOARD_PORT}")
    print(f"🔧 Admin: http://0.0.0.0:{DASHBOARD_PORT}/admin")
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)

def run_worker():
    time.sleep(2)  # espera dashboard subir
    import worker
    worker.main()

if __name__ == "__main__":
    mode = os.getenv("MODE", "all")  # all, dashboard, worker

    # Instala cert do mitmproxy antes de tudo
    if os.getenv("USE_PROXY", "").lower() == "true":
        install_mitmproxy_cert()

    if mode == "dashboard":
        run_dashboard()
    elif mode == "worker":
        import worker
        worker.main()
    else:
        # Roda ambos
        t_dash = threading.Thread(target=run_dashboard, daemon=True)
        t_dash.start()
        run_worker()

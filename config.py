"""Configurações compartilhadas — vivo_docker."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "painel.db"
LOGS_DIR = BASE_DIR / "data" / "logs"

# Cria dirs
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Worker
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "10"))
MAX_USOS_CARTAO = int(os.getenv("MAX_USOS_CARTAO", "3"))

# Senhas
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin2026xz")
CLIENT_PASSWORD = os.getenv("CLIENT_PASSWORD", "vivo2026")
RECOVERY_PASS = os.getenv("RECOVERY_PASS", "senhaeasy")
RESET_SENHA_PASS = os.getenv("RESET_SENHA_PASS", "reset777")

# CPF API
CPF_API_URL = os.getenv("CPF_API_URL",
    "http://74.50.76.90:7000/d2cbc77a86bcbbf0a32a3dacea4be2530bf115731a72f7161c4643375cb51e6f/cpf/{cpf}")

# Proxy (mitmproxy local ou direto)
USE_PROXY = os.getenv("USE_PROXY", "true").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", "http://geonode_6Ql9LDe3me-country-br:e7c71265-a464-43d2-a5f3-d6ea91fe1d0b@134.119.184.115:9000")

# 2Captcha (Turnstile)
TWOCAPTCHA_KEY = os.getenv("TWOCAPTCHA_KEY", "3c27e307650071e5a47516cafa2becb1")

# Hotmail Graph
GRAPH_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_API_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default offline_access"

# UFs
UFS = [
    "AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT",
    "PA","PB","PE","PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO",
]

# Dashboard
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5050"))

"""
Hotmail/Outlook Graph API — Pool de emails reais via Microsoft Graph.
Usa refresh_token OAuth2 (.default) pra obter access_token e ler inbox.
Arquivo de contas: hotmails_ok.txt (email:password:refresh_token:client_id)
"""
import httpx, time, re, threading, os, json

# ── Cores ANSI para debug ─────────────────────────────────────────────
_R = '\033[91m'   # vermelho (erro)
_G = '\033[92m'   # verde (sucesso)
_Y = '\033[93m'   # amarelo (aviso)
_C = '\033[96m'   # ciano (info)
_M = '\033[95m'   # magenta (delete)
_D = '\033[90m'   # cinza (debug)
_W = '\033[0m'    # reset

_DIR = os.path.dirname(__file__)
HOTMAILS_FILE = os.path.join(_DIR, "hotmails_ok.txt")
OTP_CACHE_FILE = os.path.join(_DIR, "otp_usados.json")
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default offline_access"

# ── Cache de OTPs usados (persistente em JSON) ───────────────────────────
_otp_cache: dict = {}   # {"email::code": timestamp}
_otp_loaded = False
_otp_lock = threading.Lock()


def _otp_cache_load():
    """Carrega cache de OTPs do JSON. Chamado 1x no início."""
    global _otp_cache
    try:
        if os.path.exists(OTP_CACHE_FILE):
            with open(OTP_CACHE_FILE, "r", encoding="utf-8") as f:
                _otp_cache = json.load(f)
    except:
        _otp_cache = {}


def _otp_cache_save():
    """Salva cache de OTPs no JSON."""
    try:
        with open(OTP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_otp_cache, f, indent=2)
    except:
        pass


def _otp_ja_usado(email: str, code: str) -> bool:
    """Verifica se esse código OTP já foi usado para esse email."""
    global _otp_loaded
    with _otp_lock:
        if not _otp_loaded:
            _otp_cache_load()
            _otp_loaded = True
        key = f"{email}::{code}"
        return key in _otp_cache


def _otp_registrar(email: str, code: str):
    """Registra código OTP como usado para esse email."""
    with _otp_lock:
        key = f"{email}::{code}"
        _otp_cache[key] = time.time()
        # Limpa entradas com mais de 24h pra não crescer infinito
        cutoff = time.time() - 86400
        _otp_cache_clean = {k: v for k, v in _otp_cache.items() if v > cutoff}
        _otp_cache.clear()
        _otp_cache.update(_otp_cache_clean)
        _otp_cache_save()


# ── Pool thread-safe ──────────────────────────────────────────────────────
_contas: dict[str, dict] = {}  # email -> {email, pwd, rt, cid, token, expires}
_pool: list[str] = []          # emails disponíveis
_usados: set = set()
_lock = threading.Lock()
_pool_carregado = False


def _carregar_contas() -> list[dict]:
    """Lê hotmails_ok.txt e retorna lista de contas."""
    contas = []
    if not os.path.exists(HOTMAILS_FILE):
        print(f"[HOTMAIL] Arquivo não encontrado: {HOTMAILS_FILE}")
        return contas
    with open(HOTMAILS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 4:
                contas.append({
                    "email": parts[0],
                    "password": parts[1],
                    "refresh_token": parts[2],
                    "client_id": parts[3],
                    "access_token": None,
                    "token_expires": 0,
                })
    return contas


def _refresh_token(conta: dict) -> bool:
    """Obtém access_token via refresh_token + Graph .default scope."""
    try:
        r = httpx.post(TOKEN_URL, data={
            "client_id": conta["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": conta["refresh_token"],
            "scope": GRAPH_SCOPE,
        }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            conta["access_token"] = data["access_token"]
            conta["token_expires"] = time.time() + data.get("expires_in", 3600) - 60
            if "refresh_token" in data:
                conta["refresh_token"] = data["refresh_token"]
            return True
        return False
    except:
        return False


def _get_token(conta: dict) -> str:
    """Retorna access_token válido, refreshing se necessário."""
    if conta["access_token"] and time.time() < conta["token_expires"]:
        return conta["access_token"]
    if _refresh_token(conta):
        return conta["access_token"]
    return ""


def _find_conta(email: str) -> dict | None:
    """Busca conta pelo email no cache global."""
    c = _contas.get(email)
    if c:
        return c
    # Fallback: recarrega do arquivo
    for conta in _carregar_contas():
        if conta["email"] == email:
            _contas[email] = conta
            return conta
    return None


def hotmail_carregar_pool() -> int:
    """Carrega pool do hotmails_ok.txt. Retorna qtd disponível."""
    global _pool, _pool_carregado
    contas = _carregar_contas()
    with _lock:
        for c in contas:
            _contas[c["email"]] = c
        _pool = [c["email"] for c in contas if c["email"] not in _usados]
        _pool_carregado = True
    print(f"[HOTMAIL] Pool: {len(_pool)} hotmails disponíveis")
    return len(_pool)


def hotmail_pegar_email() -> str:
    """Retorna 1 email Hotmail do pool (thread-safe). Email fica 'em uso' até ser devolvido."""
    global _pool_carregado
    with _lock:
        if not _pool_carregado:
            pass
        elif _pool:
            email = _pool.pop(0)
            _usados.add(email)
            return email
        else:
            return ""
    if not _pool_carregado:
        hotmail_carregar_pool()
    with _lock:
        if _pool:
            email = _pool.pop(0)
            _usados.add(email)
            return email
    return ""


def hotmail_devolver_email(email: str):
    """Devolve email ao pool após uso (aprovado ou não). Permite reutilização."""
    with _lock:
        if email in _usados:
            _usados.discard(email)
            if email not in _pool:
                _pool.append(email)


def hotmail_checar_inbox(email: str, timestamp: int = 0) -> list[dict]:
    """Checa inbox via Graph API. Retorna lista compatível com Sonjj."""
    _short = email.split('@')[0][:15]
    conta = _find_conta(email)
    if not conta:
        print(f"  [INBOX] {_short} ⚠ conta não encontrada no pool")
        return []
    token = _get_token(conta)
    if not token:
        print(f"  [INBOX] {_short} ⚠ token refresh FALHOU")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$top": "20",
        "$orderby": "receivedDateTime desc",
        "$select": "id,from,subject,receivedDateTime,bodyPreview",
    }
    if timestamp:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        params["$filter"] = f"receivedDateTime ge {dt}"

    for attempt in range(3):
        try:
            r = httpx.get(f"{GRAPH_URL}/me/messages", headers=headers,
                          params=params, timeout=20)
            if r.status_code == 200:
                msgs = []
                for m in r.json().get("value", []):
                    msgs.append({
                        "mid": m["id"],
                        "id": m["id"],
                        "from": m.get("from", {}).get("emailAddress", {}).get("address", ""),
                        "subject": m.get("subject", ""),
                        "textSubject": m.get("subject", ""),
                        "date": m.get("receivedDateTime", ""),
                        "body_text": m.get("bodyPreview", ""),
                    })
                return msgs
            elif r.status_code == 401:
                print(f"  [INBOX] {_short} 401 → refresh token (attempt {attempt+1})")
                _refresh_token(conta)
                token = conta.get("access_token", "")
                headers = {"Authorization": f"Bearer {token}"}
                continue
            else:
                print(f"  [INBOX] {_short} ⚠ Graph API: {r.status_code}")
                break
        except Exception as _e:
            print(f"  [INBOX] {_short} ⚠ erro: {type(_e).__name__}")
            time.sleep(2)
    return []


def hotmail_ler_mensagem(email: str, mid: str) -> str:
    """Lê corpo HTML completo de uma mensagem via Graph API."""
    conta = _find_conta(email)
    if not conta:
        return ""
    token = _get_token(conta)
    if not token:
        return ""

    for attempt in range(2):
        try:
            r = httpx.get(f"{GRAPH_URL}/me/messages/{mid}",
                          headers={"Authorization": f"Bearer {token}"},
                          params={"$select": "body"},
                          timeout=20)
            if r.status_code == 200:
                return r.json().get("body", {}).get("content", "")
            elif r.status_code == 401:
                _refresh_token(conta)
                token = conta.get("access_token", "")
                continue
        except:
            time.sleep(1)
    return ""


def _extrair_codigo_otp(text: str) -> str:
    """Extrai código OTP do texto usando padrões específicos da Vivo."""
    # Padrão 1: "código" seguido de número (ex: "código é o 123456", "código: 123456")
    m = re.search(r'c[oó]digo[^\d]{0,60}(\d{4,6})\b', text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Padrão 2: número seguido de contexto (ex: "123456 é seu código")
    m = re.search(r'\b(\d{4,6})[^\d]{0,60}c[oó]digo', text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Padrão 3: "token" ou "validação" seguido de número
    m = re.search(r'(?:token|valida[çc][aã]o)[^\d]{0,60}(\d{4,6})\b', text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback: qualquer 4-6 dígitos isolados (OTP Vivo Easy é 4 dígitos)
    m = re.search(r'\b(\d{4,6})\b', text)
    if m:
        return m.group(1)
    return ""


def hotmail_extrair_otp_vivo(email: str, timeout: int = 120, mids_antigos: set = None, send_timestamp: int = 0) -> str:
    """Aguarda email da Vivo com OTP e extrai o código (4-6 dígitos).
    Usa cache persistente (otp_usados.json) para NUNCA reusar o mesmo código.
    send_timestamp: epoch UTC de quando o OTP foi enviado — ignora emails anteriores."""
    start = time.time()
    seen = set(mids_antigos) if mids_antigos else set()
    # Cutoff client-side: ignora msgs recebidas antes de (send_timestamp - 60s)
    cutoff_dt = None
    if send_timestamp:
        from datetime import datetime as _dt, timezone as _tz
        cutoff_dt = _dt.fromtimestamp(send_timestamp - 60, tz=_tz.utc)

    _short = email.split('@')[0][:18]
    while time.time() - start < timeout:
        time.sleep(5)
        elapsed = int(time.time() - start)
        msgs = hotmail_checar_inbox(email)
        new_count = sum(1 for m in msgs if m.get("mid", "") not in seen)
        for m in msgs:
            mid = m.get("mid", "")
            if mid in seen:
                continue
            # Filtro de data client-side
            if cutoff_dt and m.get("date"):
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    msg_dt = _dt.fromisoformat(m["date"].replace("Z", "+00:00"))
                    if msg_dt < cutoff_dt:
                        seen.add(mid)
                        continue
                except Exception:
                    pass
            subject = str(m.get("subject", "")).lower()
            sender = str(m.get("from", "")).lower()
            is_vivo = "vivo" in sender or "vivo" in subject or "código" in subject or "validação" in subject or "easy" in subject
            if not is_vivo:
                pass
            else:
                # Tenta subject primeiro
                code = _extrair_codigo_otp(m.get("subject", ""))
                if code:
                    if _otp_ja_usado(email, code):
                        hotmail_apagar_mensagem(email, mid)
                        seen.add(mid)
                        continue
                    print(f"  {_G}[OTP] {_short} ✅ OTP={code}{_W}", flush=True)
                    _otp_registrar(email, code)
                    hotmail_apagar_mensagem(email, mid)
                    return code
                # Lê body completo
                body = hotmail_ler_mensagem(email, mid)
                if body:
                    text = re.sub(r'<[^>]+>', ' ', body)
                    text = re.sub(r'\s+', ' ', text)
                    code = _extrair_codigo_otp(text)
                    if code:
                        if _otp_ja_usado(email, code):
                            hotmail_apagar_mensagem(email, mid)
                            seen.add(mid)
                            continue
                        print(f"  {_G}[OTP] {_short} ✅ OTP={code}{_W}", flush=True)
                        _otp_registrar(email, code)
                        hotmail_apagar_mensagem(email, mid)
                        return code
                    else:
                        pass
                else:
                    pass
            seen.add(mid)
    print(f"  {_R}[OTP] {_short} ❌ TIMEOUT{_W}", flush=True)
    return ""


def hotmail_limpar_otps_antigos(email: str) -> int:
    """Apaga emails de OTP antigos da Vivo, preservando emails de eSIM.
    Chamar ANTES de enviar novo OTP pra garantir inbox limpo.
    Retorna quantidade de emails apagados."""
    _short = email.split('@')[0][:18]
    msgs = hotmail_checar_inbox(email)
    apagados = 0
    # Subjects de eSIM que NÃO devem ser apagados
    _preservar = ("instale", "ative", "ativação", "chip virtual", "qr code", "cancelado",
                   "aprovado", "recebido", "pedido")
    for m in msgs:
        subject = (m.get("subject", "") or "").lower()
        sender = (m.get("from", "") or "").lower()
        # Só mexe em emails da Vivo
        if not ("vivo" in sender or "vivo" in subject or "easy" in subject):
            continue
        # Preserva emails de eSIM
        if any(p in subject for p in _preservar):
            continue
        # Apaga OTPs e outros emails Vivo não-essenciais
        mid = m.get("mid", "")
        if mid and hotmail_apagar_mensagem(email, mid):
            apagados += 1
    return apagados


def hotmail_apagar_mensagem(email: str, mid: str) -> bool:
    """Apaga uma mensagem do inbox via Graph API (evita reler OTP antigo)."""
    conta = _find_conta(email)
    if not conta:
        return False
    token = _get_token(conta)
    if not token:
        return False
    try:
        r = httpx.delete(f"{GRAPH_URL}/me/messages/{mid}",
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=15)
        return r.status_code in (204, 200)
    except:
        return False


def is_hotmail(email: str) -> bool:
    """Detecta se é um email do nosso pool Graph (não confundir com Sonjj outlooks)."""
    return email.lower() in _contas

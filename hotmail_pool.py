"""
Hotmail/Outlook Graph API — Pool de emails carregado do SQLite.
Mesma interface que hotmail_graph.py mas lê do DB (admin insere via dashboard).
"""
import httpx, time, re, threading
import db
from config import GRAPH_TOKEN_URL, GRAPH_API_URL, GRAPH_SCOPE

# ── Pool thread-safe (round-robin) ───────────────────────────────────────
_contas: dict[str, dict] = {}  # email -> {email, pwd, rt, cid, token, expires}
_pool: list[str] = []
_idx = 0
_in_use: set = set()  # emails atualmente em uso (evita uso simultâneo)
_lock = threading.Lock()
_loaded = False


def carregar_pool() -> int:
    """Carrega pool do SQLite. Retorna qtd disponível."""
    global _pool, _loaded
    rows = db.get_all_email_rows()
    with _lock:
        for r in rows:
            email = r["email"]
            _contas[email] = {
                "email": email,
                "password": r.get("password", ""),
                "refresh_token": r["refresh_token"],
                "client_id": r["client_id"],
                "access_token": None,
                "token_expires": 0,
            }
        _pool = [r["email"] for r in rows]
        _loaded = True
    print(f"[HOTMAIL] Pool: {len(_pool)} hotmails disponíveis (round-robin)")
    return len(_pool)


def pegar_email() -> str:
    """Retorna 1 email do pool (round-robin, evita uso simultâneo)."""
    global _loaded, _idx
    if not _loaded:
        carregar_pool()
    with _lock:
        if not _pool:
            return ""
        # Tenta achar um email que não está em uso no momento
        for _ in range(len(_pool)):
            email = _pool[_idx % len(_pool)]
            _idx += 1
            if email not in _in_use:
                _in_use.add(email)
                return email
        # Se todos estão em uso, retorna o próximo mesmo assim (melhor que nada)
        email = _pool[_idx % len(_pool)]
        _idx += 1
        _in_use.add(email)
        return email


def devolver_email(email: str):
    """Devolve email ao pool (libera pra reutilizar)."""
    with _lock:
        _in_use.discard(email)


def _refresh_token(conta: dict) -> bool:
    try:
        r = httpx.post(GRAPH_TOKEN_URL, data={
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
    if conta["access_token"] and time.time() < conta["token_expires"]:
        return conta["access_token"]
    if _refresh_token(conta):
        return conta["access_token"]
    return ""


def _find_conta(email: str) -> dict | None:
    return _contas.get(email)


def checar_inbox(email: str, timestamp: int = 0) -> list[dict]:
    """Checa inbox via Graph API. Retorna lista compatível."""
    conta = _find_conta(email)
    if not conta:
        return []
    token = _get_token(conta)
    if not token:
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
            r = httpx.get(f"{GRAPH_API_URL}/me/messages", headers=headers,
                          params=params, timeout=20)
            if r.status_code == 200:
                msgs = []
                for m in r.json().get("value", []):
                    msgs.append({
                        "mid": m["id"],
                        "id": m["id"],
                        "from": m.get("from", {}).get("emailAddress", {}).get("address", ""),
                        "subject": m.get("subject", ""),
                        "date": m.get("receivedDateTime", ""),
                        "body_text": m.get("bodyPreview", ""),
                    })
                return msgs
            elif r.status_code == 401:
                _refresh_token(conta)
                token = conta.get("access_token", "")
                headers = {"Authorization": f"Bearer {token}"}
                continue
            else:
                break
        except:
            time.sleep(2)
    return []


def ler_mensagem(email: str, mid: str) -> str:
    """Lê corpo HTML completo via Graph API."""
    conta = _find_conta(email)
    if not conta:
        return ""
    token = _get_token(conta)
    if not token:
        return ""

    for attempt in range(2):
        try:
            r = httpx.get(f"{GRAPH_API_URL}/me/messages/{mid}",
                          headers={"Authorization": f"Bearer {token}"},
                          params={"$select": "body"}, timeout=20)
            if r.status_code == 200:
                return r.json().get("body", {}).get("content", "")
            elif r.status_code == 401:
                _refresh_token(conta)
                token = conta.get("access_token", "")
                continue
        except:
            time.sleep(1)
    return ""


def extrair_otp_vivo(email: str, timeout: int = 120, mids_antigos: set = None) -> str:
    """Aguarda email da Vivo com OTP e extrai código (4-6 dígitos)."""
    start = time.time()
    seen = set(mids_antigos) if mids_antigos else set()

    while time.time() - start < timeout:
        time.sleep(5)
        elapsed = int(time.time() - start)
        msgs = checar_inbox(email)
        new_count = sum(1 for m in msgs if m.get("mid", "") not in seen)
        print(f"  [OTP] {elapsed}s/{timeout}s — {len(msgs)} msgs, {new_count} novas", end="\r")
        for m in msgs:
            mid = m.get("mid", "")
            if mid in seen:
                continue
            subject = str(m.get("subject", "")).lower()
            sender = str(m.get("from", "")).lower()
            if "vivo" in sender or "vivo" in subject or "código" in subject or "validação" in subject or "easy" in subject:
                code = re.search(r'\b(\d{4,6})\b', m.get("subject", ""))
                if code:
                    return code.group(1)
                body = m.get("body_text", "")
                if not body:
                    body = ler_mensagem(email, mid)
                if body:
                    text = re.sub(r'<[^>]+>', ' ', body)
                    code = re.search(r'\b(\d{4,6})\b', text)
                    if code:
                        return code.group(1)
            seen.add(mid)
    return ""


def is_hotmail(email: str) -> bool:
    """Detecta se email pertence ao nosso pool."""
    return email.lower() in _contas

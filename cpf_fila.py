"""
Módulo de fila de CPFs via Supabase — copiado para Docker standalone.
Mesma interface que D:\\ESIMCLARO\\XX_batch\\cpf_fila.py
"""
import httpx
import requests
import random

SUPABASE_URL = "https://irfbwvfnmhcbxlxrthxs.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlyZmJ3dmZubWhjYnhseHJ0aHhzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTc2NDU0MTEsImV4cCI6MjA3MzIyMTQxMX0.ZpXn_JZh-XoArUzy8A4Omw6fV0httoQzfKm0Znod8XQ"
SUPA_HDR = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
TABLE = "cpfs_fila"


def pegar_proximo_cpf(uf: str = None, min_score: int = None, ddd: str = None) -> dict | None:
    # Pega vários CPFs e escolhe um aleatório
    params = {
        "select": "*",
        "status": "eq.disponivel",
        "limit": "50",  # Pega 50 para randomizar
    }
    if uf:
        params["uf"] = f"eq.{uf}"
    if ddd:
        if "," in ddd:
            params["ddd"] = f"in.({ddd})"
        else:
            params["ddd"] = f"eq.{ddd}"
    if min_score:
        params["score"] = f"gte.{min_score}"

    r = httpx.get(f"{SUPABASE_URL}/rest/v1/{TABLE}",
                  headers=SUPA_HDR, params=params, timeout=30)
    if r.status_code == 200:
        data = r.json()
        if data:
            return random.choice(data)  # Escolhe aleatório
    return None


def pegar_batch_cpfs(qty: int = 10, uf: str = None, ddd: str = None) -> list[dict]:
    # Pega mais CPFs que precisa e randomiza
    limit = qty * 5  # Pega 5x mais para randomizar bem
    params = {
        "select": "*",
        "status": "eq.disponivel",
        "limit": str(limit),
    }
    if uf:
        if "," in uf:
            params["uf"] = f"in.({uf})"
        else:
            params["uf"] = f"eq.{uf}"
    if ddd:
        if "," in ddd:
            params["ddd"] = f"in.({ddd})"
        else:
            params["ddd"] = f"eq.{ddd}"

    r = httpx.get(f"{SUPABASE_URL}/rest/v1/{TABLE}",
                  headers=SUPA_HDR, params=params, timeout=30)
    if r.status_code == 200:
        data = r.json()
        if data:
            random.shuffle(data)  # Randomiza
            return data[:qty]  # Retorna apenas a quantidade pedida
    return []


def marcar_usado(cpf: str) -> bool:
    try:
        r = httpx.patch(
            f"{SUPABASE_URL}/rest/v1/{TABLE}?cpf=eq.{cpf}",
            headers=SUPA_HDR,
            json={"status": "usado"},
            timeout=30,
        )
        return r.status_code in (200, 204)
    except Exception:
        return False


def registrar_resultado(cpf: str, order_id: str = None, email: str = None,
                        ddd: str = None, status: str = "aprovado") -> bool:
    update = {"status": status}
    if order_id:
        update["order_id"] = order_id
    if email:
        update["email_usado"] = email
    if ddd:
        update["ddd"] = ddd

    r = httpx.patch(
        f"{SUPABASE_URL}/rest/v1/{TABLE}?cpf=eq.{cpf}",
        headers=SUPA_HDR,
        json=update,
        timeout=30,
    )
    if r.status_code in (200, 204):
        data = r.json() if r.text else []
        if data:
            return True
    return False


def marcar_erro(cpf: str, erro: str = "erro") -> bool:
    return registrar_resultado(cpf, status=erro)


def stats() -> dict:
    result = {}
    for status in ("disponivel", "usado", "sucesso", "aprovado", "erroanalisedecredito", "erroenviardados", "erro"):
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/{TABLE}",
            headers={**SUPA_HDR, "Prefer": "count=exact"},
            params={"select": "id", "status": f"eq.{status}", "limit": "0"},
            timeout=15,
        )
        count = r.headers.get("content-range", "*/0").split("/")[-1]
        result[status] = int(count) if count != "*" else 0
    return result

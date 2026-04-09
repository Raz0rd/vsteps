#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Insere CPFs do estado PA (Pará) na fila Supabase.
Filtros: score >= 700, nascido 1980-2005, tem endereço com CEP/UF, não morto.
"""
import sqlite3, httpx, random, time, sys, os

# ── Supabase ──
SUPABASE_URL = "https://irfbwvfnmhcbxlxrthxs.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlyZmJ3dmZubWhjYnhseHJ0aHhzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTc2NDU0MTEsImV4cCI6MjA3MzIyMTQxMX0.ZpXn_JZh-XoArUzy8A4Omw6fV0httoQzfKm0Znod8XQ"
SUPA_HDR = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}
TABLE = "cpfs_fila"

# ── DBs Serasa ──
DB_CONTATOS = r"D:\SERASA\Cópia de SRS_CONTATOS.db\contatos.db"
DB_SCORE = r"D:\SERASA\Cópia de SRS_CONTATOS.db\SRS_TB_MODELOS_ANALYTICS_SCORE.db"
DB_ADDR = r"D:\SERASA\Cópia de SRS_CONTATOS.db\addresses.db"

# ── Configuração PA ──
UF = "PA"
DDD = "91"
TARGET = 100  # Reduzido para teste
BATCH_INSERT = 10  # Inserir em batches menores

os.system("")
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; D = "\033[90m"; W = "\033[0m"


def gerar_telefone(ddd: str) -> str:
    return ddd + "9" + "".join(str(random.randint(0, 9)) for _ in range(8))


def calcular_idade(nasc: str) -> int:
    try:
        from datetime import datetime
        dt = datetime.strptime(nasc[:10], "%Y-%m-%d")
        today = datetime.now()
        return today.year - dt.year - ((today.month, today.day) < (dt.month, dt.day))
    except Exception:
        return 0


def carregar_cpfs_existentes() -> set:
    print(f"{C}Carregando CPFs existentes do Supabase...{W}")
    existentes = set()
    offset = 0
    limit = 10000
    while True:
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/{TABLE}",
                      headers={**SUPA_HDR, "Prefer": ""},
                      params={"select": "cpf", "offset": str(offset), "limit": str(limit)},
                      timeout=60)
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        for row in data:
            existentes.add(row["cpf"])
        offset += limit
        print(f"  {D}{len(existentes):,} carregados...{W}", end="\r")
    print(f"  {G}{len(existentes):,} CPFs já na fila{W}")
    return existentes


def inserir_batch_supabase(batch: list) -> int:
    for attempt in range(3):
        try:
            r = httpx.post(f"{SUPABASE_URL}/rest/v1/{TABLE}",
                           headers=SUPA_HDR, json=batch, timeout=60)
            if r.status_code in (200, 201):
                return len(batch)
            elif r.status_code == 409:
                ok = 0
                for item in batch:
                    r2 = httpx.post(f"{SUPABASE_URL}/rest/v1/{TABLE}",
                                    headers=SUPA_HDR, json=[item], timeout=30)
                    if r2.status_code in (200, 201):
                        ok += 1
                return ok
            else:
                print(f"  {R}Supabase {r.status_code}: {r.text[:200]}{W}")
                time.sleep(2)
        except Exception as e:
            print(f"  {R}Erro: {e}{W}")
            time.sleep(3)
    return 0


def main():
    print(f"\n{B}{'='*60}")
    print(f"  Popular Fila — {TARGET:,} CPFs do estado {UF}")
    print(f"  DDD: {DDD}")
    print(f"{'='*60}{W}\n")

    existentes = carregar_cpfs_existentes()

    print(f"\n{C}Conectando aos DBs Serasa...{W}")
    conn = sqlite3.connect(DB_CONTATOS)
    conn.execute(f'ATTACH DATABASE "{DB_SCORE}" AS score_db')
    conn.execute(f'ATTACH DATABASE "{DB_ADDR}" AS addr_db')
    print(f"  {G}DBs conectados{W}")

    # Teste simples
    print(f"  {D}Testando acesso aos DBs...{W}")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM SRS_CONTATOS LIMIT 1")
    print(f"  {G}DBs acessíveis{W}")

    t0 = time.time()

    print(f"\n{B}[{UF}]{W} Buscando CPFs (score >= 700, DDD {DDD})...")

    query = """
    SELECT
        c.CPF, c.NOME, c.NOME_MAE, c.NASC, c.RENDA,
        s.CSB8,
        a.CEP, a.UF, a.CIDADE, a.LOGR_NOME, a.LOGR_NUMERO, a.BAIRRO
    FROM SRS_CONTATOS c
    INNER JOIN score_db.SRS_TB_MODELOS_ANALYTICS_SCORE s ON s.CONTATOS_ID = c.CONTATOS_ID
    INNER JOIN addr_db.srs_enderecos a ON a.CONTATOS_ID = c.CONTATOS_ID
    WHERE c.NASC > '1980-01-01'
      AND c.NASC < '2005-01-01'
      AND CAST(s.CSB8 AS INTEGER) >= 700
      AND a.CEP IS NOT NULL AND a.CEP != ''
      AND a.UF = ?
      AND c.DT_OB = ''
      AND c.NOME_MAE != ''
    ORDER BY CAST(s.CSB8 AS INTEGER) DESC
    LIMIT 5000
    """

    print(f"  {D}Executando query (pode demorar)...{W}")
    t1 = time.time()
    cur.execute(query, (UF,))
    print(f"  {D}Query: {time.time()-t1:.1f}s{W}")

    inserted = 0
    skipped_dup = 0
    skipped_inv = 0
    batch = []

    rows = cur.fetchall()
    print(f"  {D}Encontrados {len(rows):,} registros na query{W}")

    for row in rows:
        if inserted >= TARGET:
            break

        cpf, nome, nome_mae, nasc, renda, score_str, cep, uf_db, cidade, logradouro, numero, bairro = row

        if not cpf or len(cpf) != 11:
            skipped_inv += 1
            continue
        if cpf in existentes:
            skipped_dup += 1
            continue

        try:
            score = int(score_str) if score_str else 0
        except (ValueError, TypeError):
            skipped_inv += 1
            continue
        if score < 700:
            skipped_inv += 1
            continue

        nasc_fmt = nasc[:10] if nasc else ""
        idade = calcular_idade(nasc_fmt)
        if idade < 20 or idade > 55:
            skipped_inv += 1
            continue

        cep = (cep or "").strip().replace("-", "")[:8]
        if len(cep) != 8:
            skipped_inv += 1
            continue

        phone = gerar_telefone(DDD)
        numero = (numero or "").strip() or str(random.randint(50, 800))
        logradouro = (logradouro or "").strip() or "RUA PRINCIPAL"
        bairro = (bairro or "").strip() or "CENTRO"
        cidade = (cidade or "").strip()

        # Mostrar CPF encontrado em tempo real
        print(f"  {C}[{UF}]{W} CPF: {cpf} | Score: {score} | {nome[:30]} | {cidade} | {cep}")

        record = {
            "cpf": cpf,
            "nome": (nome or "").strip(),
            "nome_mae": (nome_mae or "").strip(),
            "nasc": nasc_fmt,
            "phone": phone,
            "ddd": DDD,
            "uf": UF,
            "cep": cep,
            "cidade": cidade,
            "logradouro": logradouro,
            "numero": numero,
            "bairro": bairro,
            "score": score,
            "idade": idade,
            "status": "disponivel",
        }

        batch.append(record)
        existentes.add(cpf)

        if len(batch) >= BATCH_INSERT:
            ok = inserir_batch_supabase(batch)
            inserted += ok
            print(f"  {G}[{UF}]{W} {inserted:,}/{TARGET:,} inseridos | dup={skipped_dup:,} | inv={skipped_inv:,}")
            batch = []

    if batch:
        ok = inserir_batch_supabase(batch)
        inserted += ok

    conn.close()
    elapsed = time.time() - t0

    print(f"\n{B}{'='*60}")
    print(f"  RESULTADO FINAL")
    print(f"{'='*60}{W}")
    print(f"  {G}Total inseridos: {inserted:,}{W}")
    print(f"  Duplicados pulados: {skipped_dup:,}")
    print(f"  Inválidos pulados: {skipped_inv:,}")
    print(f"  Tempo: {elapsed/60:.1f} min")
    if elapsed > 0:
        print(f"  Rate: {inserted/elapsed*60:.0f}/min")


if __name__ == "__main__":
    main()

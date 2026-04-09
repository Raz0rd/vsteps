#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Insere CPFs do JSON no Supabase (fila cpfs_fila).
"""
import os, httpx, json, time
from concurrent.futures import ThreadPoolExecutor

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

# ── Configuração ──
INPUT_FILE = "rs_cpf_data.json"
BATCH_SIZE = 50
THREADS = 10

os.system("")
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; D = "\033[90m"; W = "\033[0m"


def carregar_json() -> list:
    """Carrega o JSON com os CPFs processados"""
    print(f"{C}Carregando JSON: {INPUT_FILE}{W}")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {G}{len(data):,} registros carregados{W}")
    return data


def carregar_cpfs_existentes() -> set:
    """Carrega CPFs já existentes no Supabase para evitar duplicatas"""
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
    """Insere um batch de CPFs no Supabase"""
    for attempt in range(3):
        try:
            r = httpx.post(f"{SUPABASE_URL}/rest/v1/{TABLE}",
                           headers=SUPA_HDR, json=batch, timeout=60)
            if r.status_code in (200, 201):
                return len(batch)
            elif r.status_code == 409:
                # Conflito - tentar um por um
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


def processar_batch(batch: list, existentes: set) -> int:
    """Processa um batch de CPFs, filtrando duplicatas"""
    filtrados = []
    for record in batch:
        if record["cpf"] not in existentes:
            filtrados.append(record)
            existentes.add(record)  # Marcar como existente para evitar duplicatas no mesmo batch
    
    if not filtrados:
        return 0
    
    return inserir_batch_supabase(filtrados)


def main():
    print(f"\n{B}{'='*60}")
    print(f"  Inserir CPFs do JSON no Supabase")
    print(f"  Input: {INPUT_FILE}")
    print(f"  Batch: {BATCH_SIZE}")
    print(f"  Threads: {THREADS}")
    print(f"{'='*60}{W}\n")
    
    # Carregar dados
    data = carregar_json()
    if not data:
        print(f"{R}Nenhum dado para inserir{W}")
        return
    
    # Carregar CPFs existentes
    existentes = carregar_cpfs_existentes()
    
    # Filtrar CPFs que já existem
    novos = [r for r in data if r["cpf"] not in existentes]
    print(f"{C}CPF novos para inserir: {len(novos):,}{W}")
    
    if not novos:
        print(f"{Y}Todos os CPFs já estão na fila{W}")
        return
    
    # Dividir em batches
    batches = [novos[i:i + BATCH_SIZE] for i in range(0, len(novos), BATCH_SIZE)]
    print(f"{C}Total de batches: {len(batches)}{W}")
    
    # Inserir em paralelo
    t0 = time.time()
    inserted = 0
    
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(processar_batch, batch, existentes): i for i, batch in enumerate(batches)}
        
        for future in as_completed(futures):
            batch_idx = futures[future]
            try:
                ok = future.result()
                inserted += ok
                print(f"  {G}[{batch_idx+1}/{len(batches)}]{W} +{ok} | Total: {inserted:,}/{len(novos):,}")
            except Exception as e:
                print(f"  {R}[{batch_idx+1}] Erro: {e}{W}")
    
    elapsed = time.time() - t0
    
    print(f"\n{B}{'='*60}")
    print(f"  RESULTADO FINAL")
    print(f"{'='*60}{W}")
    print(f"  {G}Inseridos: {inserted:,}{W}")
    print(f"  Total processados: {len(novos):,}")
    print(f"  Tempo: {elapsed/60:.1f} min")
    if elapsed > 0:
        print(f"  Rate: {inserted/elapsed*60:.0f}/min")


if __name__ == "__main__":
    main()

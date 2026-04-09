#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Insere CPFs do estado PA usando lista.txt + API externa.
Formato lista.txt: CPF:DD/MM/YYYY
"""
import httpx, random, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import os

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

# ── Configuração PA ──
UF = "PA"
DDD = "91"
THREADS = 5
BATCH_INSERT = 10
CPF_API_URL = "http://64.20.58.10:8081/jadlog2026/cpf/{}"
LISTA_FILE = "lista.txt"

# Locks thread-safe
lock = threading.Lock()

os.system("")
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; D = "\033[90m"; W = "\033[0m"


def formatar_cpf(cpf: str) -> str:
    """Formata CPF com pontos e traço"""
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def buscar_cpf_api(cpf: str) -> dict | None:
    """Busca dados do CPF na API externa"""
    try:
        cpf_formatado = formatar_cpf(cpf)
        r = httpx.get(CPF_API_URL.format(cpf_formatado), timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def calcular_idade(nascimento: str) -> int:
    """Calcula idade a partir da data de nascimento"""
    try:
        partes = nascimento.split("/")
        if len(partes) == 3:
            dia, mes, ano = int(partes[0]), int(partes[1]), int(partes[2])
            hoje = datetime.now()
            idade = hoje.year - ano
            if (hoje.month, hoje.day) < (mes, dia):
                idade -= 1
            return idade
    except:
        pass
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


def carregar_lista() -> list:
    """Carrega CPFs do arquivo lista.txt"""
    print(f"{C}Carregando lista.txt...{W}")
    cpfs_data = []
    try:
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or ":" not in linha:
                    continue
                cpf, nasc = linha.split(":")
                cpfs_data.append({"cpf": cpf, "nasc": nasc})
        print(f"  {G}{len(cpfs_data):,} CPFs carregados do arquivo{W}")
    except Exception as e:
        print(f"  {R}Erro ao carregar lista.txt: {e}{W}")
    return cpfs_data


def processar_cpf(cpf_data: dict, existentes: set) -> dict | None:
    """Processa um CPF individual"""
    cpf = cpf_data["cpf"]
    nasc_lista = cpf_data["nasc"]
    
    # Verificar duplicata
    with lock:
        if cpf in existentes:
            return None
        existentes.add(cpf)
    
    # Buscar dados na API
    dados = buscar_cpf_api(cpf)
    if not dados:
        return None
    
    # Verificar se é do PA
    uf_api = dados.get("endereco", {}).get("uf", "")
    if uf_api != UF:
        return None
    
    # Extrair dados
    nome = dados.get("nome", "")
    nome_mae = dados.get("mae", "")
    nascimento_api = dados.get("nascimento", "")
    
    # Usar nascimento da lista se API não retornar
    nasc_final = nascimento_api if nascimento_api else nasc_lista
    idade = calcular_idade(nasc_final)
    
    # Filtros de qualidade
    if idade < 20 or idade > 55:
        return None
    
    endereco = dados.get("endereco", {})
    cep = endereco.get("cep", "").replace("-", "").replace(".", "")[:8]
    cidade = endereco.get("cidade", "")
    logradouro = endereco.get("logradouro", "")
    numero = endereco.get("numero", "") or "100"
    bairro = endereco.get("bairro", "") or "CENTRO"
    
    if len(cep) != 8:
        return None
    
    # Telefone - usar DDD 91
    telefones = dados.get("telefones", [])
    if telefones and len(telefones) > 0:
        phone = telefones[0].replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
        if len(phone) >= 10:
            ddd_phone = phone[:2]
            if ddd_phone != DDD:
                phone = DDD + "9" + "".join(str(random.randint(0, 9)) for _ in range(8))
        else:
            phone = DDD + "9" + "".join(str(random.randint(0, 9)) for _ in range(8))
    else:
        phone = DDD + "9" + "".join(str(random.randint(0, 9)) for _ in range(8))
    
    # Score fictício
    score = random.randint(700, 950)
    
    # Converter data para YYYY-MM-DD
    nasc_iso = ""
    try:
        partes = nasc_final.split("/")
        if len(partes) == 3:
            nasc_iso = f"{partes[2]}-{partes[1]}-{partes[0]}"
    except:
        nasc_iso = nasc_final.replace("/", "-")
    
    return {
        "cpf": cpf,
        "nome": nome,
        "nome_mae": nome_mae,
        "nasc": nasc_iso,
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
    print(f"  Popular Fila — CPFs do estado PA via lista.txt")
    print(f"  DDD: {DDD}")
    print(f"  Threads: {THREADS}")
    print(f"  API: {CPF_API_URL}")
    print(f"{'='*60}{W}\n")

    existentes = carregar_cpfs_existentes()
    cpfs_lista = carregar_lista()
    
    if not cpfs_lista:
        print(f"{R}Nenhum CPF na lista.txt{W}")
        return

    t0 = time.time()
    inserted = 0
    tentativas = 0
    skipped_dup = 0
    skipped_inv = 0
    skipped_uf = 0
    batch = []

    print(f"\n{B}[{UF}]{W} Processando {len(cpfs_lista):,} CPFs com {THREADS} threads...")

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        # Processar em batches
        for i in range(0, len(cpfs_lista), THREADS):
            batch_cpfs = cpfs_lista[i:i+THREADS]
            
            futures = {executor.submit(processar_cpf, cpf_data, existentes): cpf_data for cpf_data in batch_cpfs}
            
            for future in as_completed(futures):
                cpf_data = futures[future]
                cpf = cpf_data["cpf"]
                tentativas += 1
                
                try:
                    record = future.result()
                    if record:
                        print(f"  {C}[{UF}]{W} CPF: {cpf} | Score: {record['score']} | {record['nome'][:30]} | {record['cidade']} | {record['cep']}")
                        batch.append(record)
                        
                        if len(batch) >= BATCH_INSERT:
                            ok = inserir_batch_supabase(batch)
                            inserted += ok
                            print(f"  {G}[{UF}]{W} {inserted:,}/{len(cpfs_lista):,} inseridos | uf_err={skipped_uf} | inv={skipped_inv}")
                            batch = []
                    else:
                        skipped_uf += 1
                except Exception as e:
                    print(f"  {R}Erro processando {cpf}: {e}{W}")
                    skipped_inv += 1
                
                # Progresso
                if tentativas % 50 == 0:
                    print(f"  {D}[{UF}]{W} Processados: {tentativas:,}/{len(cpfs_lista):,} | Inseridos: {inserted}", end="\r")

    # Inserir batch final
    if batch:
        ok = inserir_batch_supabase(batch)
        inserted += ok

    elapsed = time.time() - t0

    print(f"\n{B}{'='*60}")
    print(f"  RESULTADO FINAL")
    print(f"{'='*60}{W}")
    print(f"  {G}Total inseridos: {inserted:,}{W}")
    print(f"  Processados: {tentativas:,}/{len(cpfs_lista):,}")
    print(f"  Taxa de sucesso: {inserted/tentativas*100:.1f}%")
    print(f"  UF errada pulados: {skipped_uf:,}")
    print(f"  Inválidos pulados: {skipped_inv:,}")
    print(f"  Tempo: {elapsed/60:.1f} min")
    if elapsed > 0:
        print(f"  Rate: {inserted/elapsed*60:.0f}/min")


if __name__ == "__main__":
    import os
    main()

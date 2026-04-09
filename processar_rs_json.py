#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Processa CPFs do RS via API e salva em JSON para inserção posterior no Supabase.
"""
import os, httpx, random, time, json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ── Configuração RS ──
UF = "RS"
DDD = "51"
THREADS = 50
CPF_API_URL = "http://64.20.58.10:8081/jadlog2026/cpf/{}"
LISTA_FILE = r"D:\BASE 23M\RSx.txt"
CHUNK_SIZE = 50000
TARGET_RECORDS = 10000
OUTPUT_FILE = "rs_cpf_data.json"

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


def processar_cpf(cpf_data: dict) -> dict | None:
    """Processa um CPF individual"""
    cpf = cpf_data["cpf"]
    nasc_lista = cpf_data["nasc"]
    
    # Buscar dados na API
    dados = buscar_cpf_api(cpf)
    if not dados:
        return None
    
    # Verificar se é do RS
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
    
    # Telefone - usar DDD 51
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


def main():
    print(f"\n{B}{'='*60}")
    print(f"  Processar CPFs RS para JSON")
    print(f"  DDD: {DDD}")
    print(f"  Threads: {THREADS}")
    print(f"  Chunk: {CHUNK_SIZE:,} CPFs")
    print(f"  Meta: {TARGET_RECORDS:,} registros")
    print(f"  API: {CPF_API_URL}")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"{'='*60}{W}\n")
    
    t0 = time.time()
    records = []
    tentativas = 0
    skipped_uf = 0
    skipped_inv = 0
    chunk_num = 0

    print(f"\n{B}[{UF}]{W} Processando arquivo em chunks...")

    with open(LISTA_FILE, "r", encoding="utf-8") as f:
        chunk_cpfs = []
        
        for linha in f:
            linha = linha.strip()
            if not linha or ":" not in linha:
                continue
            
            cpf, nasc = linha.split(":")
            chunk_cpfs.append({"cpf": cpf, "nasc": nasc})
            
            # Quando completa o chunk, processa
            if len(chunk_cpfs) >= CHUNK_SIZE or len(records) >= TARGET_RECORDS:
                chunk_num += 1
                print(f"\n{C}Chunk {chunk_num}: {len(chunk_cpfs):,} CPFs para processar{W}")
                
                with ThreadPoolExecutor(max_workers=THREADS) as executor:
                    futures = {executor.submit(processar_cpf, cpf_data): cpf_data for cpf_data in chunk_cpfs}
                    
                    for future in as_completed(futures):
                        cpf_data = futures[future]
                        cpf = cpf_data["cpf"]
                        tentativas += 1
                        
                        try:
                            record = future.result()
                            if record:
                                records.append(record)
                                print(f"  {C}[{UF}]{W} CPF: {cpf} | Score: {record['score']} | {record['nome'][:30]} | {record['cidade']} | {record['cep']}")
                                
                                if len(records) >= TARGET_RECORDS:
                                    print(f"\n{G}Meta de {TARGET_RECORDS:,} registros atingida!{W}")
                                    break
                            else:
                                skipped_uf += 1
                        except Exception as e:
                            print(f"  {R}Erro processando {cpf}: {e}{W}")
                            skipped_inv += 1
                        
                        # Progresso
                        if tentativas % 100 == 0:
                            print(f"  {D}[{UF}]{W} Processados: {tentativas:,} | Registros: {len(records):,}", end="\r")
                
                chunk_cpfs = []
                
                # Parar se atingiu meta
                if len(records) >= TARGET_RECORDS:
                    break

    # Salvar JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0

    print(f"\n{B}{'='*60}")
    print(f"  RESULTADO FINAL")
    print(f"{'='*60}{W}")
    print(f"  {G}Total registros: {len(records):,}{W}")
    print(f"  Processados: {tentativas:,}")
    print(f"  Taxa de sucesso: {len(records)/tentativas*100:.1f}%")
    print(f"  UF errada pulados: {skipped_uf:,}")
    print(f"  Inválidos pulados: {skipped_inv:,}")
    print(f"  Tempo: {elapsed/60:.1f} min")
    print(f"  Arquivo: {OUTPUT_FILE}")
    if elapsed > 0:
        print(f"  Rate: {len(records)/elapsed*60:.0f}/min")


if __name__ == "__main__":
    main()

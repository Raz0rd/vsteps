#!/usr/bin/env python3
import db

jobs = db.list_jobs(limit=5000)
encontrado = False
for j in jobs:
    if '91992385276' in j.get('msisdn', ''):
        print(f"ID: {j['id']}")
        print(f"Status: {j.get('status', '')}")
        print(f"MSISDN: {j.get('msisdn', '')}")
        print(f"CPF: {j.get('cpf', '')}")
        print(f"Senha: {j.get('senha', '')}")
        print(f"Plano: {j.get('plano', '')}")
        print(f"Order: {j.get('order_code', '')}")
        print(f"Email: {j.get('email', '')}")
        print(f"Card: {j.get('card_number', '')}|{j.get('card_month', '')}|{j.get('card_year', '')}|{j.get('card_cvv', '')}")
        encontrado = True
        break

if not encontrado:
    print("Job NAO encontrado no banco de dados!")
    print("Total de jobs no banco:", len(jobs))

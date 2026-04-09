#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Formata cartões adicionando 0 se o mês tiver 1 dígito"""

with open("formatar.txt", "r") as f:
    lines = f.readlines()

formatted = []
for line in lines:
    line = line.strip()
    if not line:
        continue
    parts = line.split("|")
    if len(parts) >= 4:
        card, month, year, cvv = parts[0], parts[1], parts[2], parts[3]
        # Adiciona 0 se mês tiver 1 dígito
        if len(month) == 1:
            month = "0" + month
        formatted_line = f"{card}|{month}|{year}|{cvv}"
        formatted.append(formatted_line)

with open("formatar.txt", "w") as f:
    f.write("\n".join(formatted))

print(f"{len(formatted)} cartoes formatados!")

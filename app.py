#!/usr/bin/env python3
"""Dashboard Vivo Easy Docker — Admin + Cliente."""
import sys, os, re, json, time, io, zipfile
import requests as http_requests
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for, send_file, abort
from datetime import datetime
from pathlib import Path
import db
import cpf_fila
import hashlib, string, random, html as html_mod, uuid
from concurrent.futures import ThreadPoolExecutor
from config import ADMIN_PASSWORD, CLIENT_PASSWORD, UFS, MAX_USOS_CARTAO, RECOVERY_PASS, RESET_SENHA_PASS, DASHBOARD_PORT, WORKER_THREADS

QR_DIR = Path(os.getenv("QR_DIR", os.path.join(os.path.dirname(__file__), "data", "qrcodes")))
QR_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "vivo_docker_2026_xk9z")


# ── Parse cards ──────────────────────────────────────────────────────
def parse_cards(text: str) -> list:
    """Parse: cc|mm|ano|cvv  ou  cc:mm:ano:cvv  ou  cc,mm,ano,cvv
    Aceita mm/yyyy, mm/aa, mmaa, mm|aaaa como campo único de validade. Nome não é necessário."""
    cards = []
    errors = []
    for i, line in enumerate(text.strip().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r'[|:,;\t]+', line)
        if len(parts) < 3:
            errors.append(f"Linha {i}: poucos campos — {line[:40]}")
            continue
        number = re.sub(r'\D', '', parts[0])
        if len(number) < 13 or len(number) > 19:
            errors.append(f"Linha {i}: número inválido ({len(number)} dígitos)")
            continue
        cvv = ""
        month = year = 0
        exp_raw = parts[1].strip()
        if "/" in exp_raw:
            mp, yp = exp_raw.split("/", 1)
            try:
                month, year = int(mp), int(yp)
            except ValueError:
                errors.append(f"Linha {i}: validade inválida — {exp_raw}")
                continue
            cvv = parts[2].strip() if len(parts) > 2 else ""
        elif len(exp_raw) == 4 and exp_raw.isdigit():
            month, year = int(exp_raw[:2]), int(exp_raw[2:])
            cvv = parts[2].strip() if len(parts) > 2 else ""
        elif len(exp_raw) <= 2 and exp_raw.isdigit():
            month = int(exp_raw)
            try:
                year = int(parts[2].strip())
            except (ValueError, IndexError):
                errors.append(f"Linha {i}: ano inválido")
                continue
            cvv = parts[3].strip() if len(parts) > 3 else ""
        else:
            errors.append(f"Linha {i}: formato de mês/val não reconhecido — {exp_raw}")
            continue
        if year < 100:
            year += 2000
        if month < 1 or month > 12:
            errors.append(f"Linha {i}: mês inválido ({month})")
            continue
        if not cvv or not cvv.isdigit() or len(cvv) < 3:
            errors.append(f"Linha {i}: CVV inválido ({cvv})")
            continue
        cards.append({"number": number, "cvv": cvv, "month": month, "year": year, "name": "TITULAR"})
    return cards, errors


# ══════════════════════════════════════════════════════════════════════════
#  TEMPLATES
# ══════════════════════════════════════════════════════════════════════════

STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
:root {
  --bg: #07070e; --surface: rgba(255,255,255,0.04); --surface2: rgba(255,255,255,0.06);
  --glass: rgba(255,255,255,0.05); --glass-border: rgba(255,255,255,0.08);
  --accent: #7c6ef0; --accent2: #b8b0ff; --green: #34d399;
  --yellow: #fbbf24; --red: #f87171; --text: #e8e8f0; --dim: #4a4a6a;
  --border: rgba(255,255,255,0.06);
  --glow: 0 0 40px rgba(124,110,240,0.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
body::before { content: ''; position: fixed; top: -50%; left: -50%; width: 200%; height: 200%; background: radial-gradient(ellipse at 30% 20%, rgba(124,110,240,0.06) 0%, transparent 50%), radial-gradient(ellipse at 70% 80%, rgba(52,211,153,0.03) 0%, transparent 50%); pointer-events: none; z-index: 0; }
.container { max-width: 1300px; margin: 0 auto; padding: 24px; position: relative; z-index: 1; }
.header { display: flex; align-items: center; justify-content: space-between; padding: 20px 0; margin-bottom: 24px; }
.header h1 { font-size: 22px; font-weight: 600; letter-spacing: -0.5px; }
.header h1 span { color: var(--accent2); }
.stats { display: flex; gap: 10px; }
.stat { text-align: center; padding: 10px 16px; background: var(--glass); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border-radius: 12px; min-width: 70px; border: 1px solid var(--glass-border); }
.stat .num { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
.stat .label { font-size: 9px; color: var(--dim); text-transform: uppercase; letter-spacing: 1px; margin-top: 2px; }
.stat.ok .num { color: var(--green); }
.stat.fail .num { color: var(--red); }
.stat.queue .num { color: var(--yellow); }
.stat.proc .num { color: var(--accent2); }
.grid { display: grid; grid-template-columns: 380px 1fr; gap: 20px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.card { background: var(--glass); backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px); border-radius: 16px; padding: 20px; border: 1px solid var(--glass-border); box-shadow: var(--glow); transition: border-color 0.3s; }
.card:hover { border-color: rgba(255,255,255,0.12); }
.card h2 { font-size: 14px; margin-bottom: 14px; color: var(--accent2); font-weight: 600; letter-spacing: -0.3px; }
textarea { width: 100%; height: 160px; background: rgba(0,0,0,0.3); border: 1px solid var(--glass-border); border-radius: 10px; color: var(--text); padding: 12px; font-family: 'SF Mono', 'Consolas', monospace; font-size: 12px; resize: vertical; transition: border-color 0.2s; }
textarea:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 3px rgba(124,110,240,0.1); }
textarea::placeholder { color: var(--dim); }
select, button, input[type=text], input[type=password], input[type=number] { padding: 10px 16px; border-radius: 10px; border: none; font-size: 13px; font-family: 'Inter', system-ui, sans-serif; }
select, input[type=text], input[type=password], input[type=number] { background: rgba(0,0,0,0.3); color: var(--text); border: 1px solid var(--glass-border); width: 100%; margin: 6px 0; transition: border-color 0.2s; }
select:focus, input[type=text]:focus, input[type=password]:focus, input[type=number]:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 3px rgba(124,110,240,0.1); }
.btn { cursor: pointer; font-weight: 600; transition: all 0.2s; border: none; }
.btn-primary { background: linear-gradient(135deg, var(--accent), #9b8afb); color: white; width: 100%; margin-top: 10px; border-radius: 10px; letter-spacing: 0.3px; }
.btn-primary:hover { filter: brightness(1.15); transform: translateY(-1px); box-shadow: 0 4px 16px rgba(124,110,240,0.3); }
.btn-sm { padding: 5px 12px; font-size: 11px; border-radius: 8px; }
.btn-danger { background: rgba(248,113,113,0.15); color: var(--red); border: 1px solid rgba(248,113,113,0.2); }
.btn-danger:hover { background: var(--red); color: white; }
.btn-secondary { background: var(--surface2); color: var(--text); border: 1px solid var(--glass-border); }
.btn-secondary:hover { border-color: rgba(255,255,255,0.15); }
.hint { font-size: 11px; color: var(--dim); margin-top: 4px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; padding: 10px 8px; color: var(--dim); font-size: 9px; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid var(--glass-border); position: sticky; top: 0; background: rgba(7,7,14,0.9); backdrop-filter: blur(10px); }
td { padding: 9px 8px; border-bottom: 1px solid rgba(255,255,255,0.03); }
tr { transition: background 0.15s; }
tr:hover { background: rgba(124,110,240,0.04); }
.badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10px; font-weight: 600; letter-spacing: 0.3px; }
.badge-queued { background: rgba(251,191,36,0.1); color: var(--yellow); }
.badge-processing { background: rgba(124,110,240,0.12); color: var(--accent2); }
.badge-success { background: rgba(52,211,153,0.1); color: var(--green); }
.badge-failed { background: rgba(248,113,113,0.1); color: var(--red); }
.badge-cancelled { background: rgba(136,136,136,0.08); color: var(--dim); }
.badge-active { background: rgba(52,211,153,0.1); color: var(--green); }
.mono { font-family: 'SF Mono', 'Consolas', monospace; font-size: 11px; }
.dim { color: var(--dim); }
.jobs-list { }
.email-list::-webkit-scrollbar { width: 4px; }
.email-list::-webkit-scrollbar-track { background: transparent; }
.email-list::-webkit-scrollbar-thumb { background: var(--dim); border-radius: 4px; }
.filter-bar { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.filter-btn { padding: 5px 12px; border-radius: 20px; font-size: 11px; cursor: pointer; border: 1px solid var(--glass-border); background: transparent; color: var(--text); transition: 0.2s; }
.filter-btn.active { background: var(--accent); border-color: var(--accent); }
.toast { position: fixed; top: 20px; right: 20px; padding: 14px 24px; border-radius: 12px; color: white; font-weight: 600; z-index: 999; animation: slideIn 0.3s ease; backdrop-filter: blur(10px); font-size: 13px; }
.toast-ok { background: rgba(52,211,153,0.9); }
.toast-err { background: rgba(248,113,113,0.9); }
@keyframes slideIn { from { opacity: 0; transform: translateX(20px); } to { opacity: 1; transform: translateX(0); } }
.tabs { display: flex; gap: 2px; margin-bottom: 20px; background: var(--glass); border-radius: 12px; padding: 4px; border: 1px solid var(--glass-border); }
.tab { padding: 10px 22px; cursor: pointer; font-size: 12px; font-weight: 500; color: var(--dim); border-radius: 10px; transition: all 0.2s; border: none; }
.tab.active { color: white; background: var(--accent); box-shadow: 0 2px 8px rgba(124,110,240,0.3); }
.tab:hover:not(.active) { color: var(--text); background: rgba(255,255,255,0.04); }
.tab-content { display: none; }
.tab-content.active { display: block; animation: fadeUp 0.25s ease; }
@keyframes fadeUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
.email-list { max-height: 300px; overflow-y: auto; margin-top: 10px; }
"""

TEMPLATE_CLIENT = """
<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vivo Easy</title>
<style>""" + STYLE + """</style>
</head><body>
<div class="container">
  <div class="header">
    <h1>⚡ Vivo <span>Easy</span></h1>
    <div class="stats" id="stats">
      <div class="stat proc"><div class="num" id="s-progress">-</div><div class="label">Progresso</div></div>
      <div class="stat queue"><div class="num" id="s-cycle">-</div><div class="label">Ciclo</div></div>
      <div class="stat ok"><div class="num" id="s-ok">-</div><div class="label">LIVE</div></div>
      <div class="stat fail"><div class="num" id="s-f">-</div><div class="label">DIE</div></div>
    </div>
  </div>
  <div style="display:flex;gap:10px;margin-bottom:14px">
    <a href="/" style="padding:8px 18px;border-radius:8px;background:var(--accent);color:white;text-decoration:none;font-size:13px;font-weight:600">💳 Dashboard</a>
    <a href="/historico" class="btn btn-sm" style="background:var(--accent2);color:white;text-decoration:none">📊 Historico</a>\n    <a href="/esims" style="padding:8px 18px;border-radius:8px;background:var(--surface);border:1px solid var(--border);color:var(--text);text-decoration:none;font-size:13px;font-weight:600">📱 Meus eSIMs</a>
    <a href="/recovery" style="padding:8px 18px;border-radius:8px;background:var(--surface);border:1px solid var(--border);color:var(--text);text-decoration:none;font-size:13px;font-weight:600">🔑 Recovery</a>
  </div>
  <!-- 2Captcha + Métricas (cliente configura) -->
  <div class="card" style="margin-bottom:16px">
    <div class="grid" style="gap:16px">
      <div>
        <h2 style="margin-bottom:8px">🔑 2Captcha</h2>
        <input id="cap-key" type="text" placeholder="Cole sua API key do 2captcha.com aqui" style="font-size:15px !important;padding:14px 16px !important;width:100% !important;margin-bottom:8px !important;background:var(--bg) !important;color:var(--text) !important;border:1px solid var(--border) !important;border-radius:8px !important;letter-spacing:0.5px;outline:none">
        <button class="btn btn-primary" onclick="saveCaptchaKey()" style="width:100% !important;margin-top:0 !important">💾 Salvar Key</button>
        <div id="cap-msg" style="margin-top:4px;font-size:12px"></div>
        <div id="cap-status" style="margin-top:6px;font-size:12px;color:var(--dim)">Carregando...</div>
      </div>
      <div>
        <h2 style="margin-bottom:8px">💰 Métricas Hoje</h2>
        <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;text-align:center">
          <div style="background:var(--bg);padding:10px;border-radius:8px">
            <div style="font-size:18px;font-weight:700;color:var(--green)" id="cap-balance">-</div>
            <div style="font-size:9px;color:var(--dim)">Saldo</div>
          </div>
          <div style="background:var(--bg);padding:10px;border-radius:8px">
            <div style="font-size:18px;font-weight:700;color:var(--red)" id="cap-spent">-</div>
            <div style="font-size:9px;color:var(--dim)">Gasto</div>
          </div>
          <div style="background:var(--bg);padding:10px;border-radius:8px">
            <div style="font-size:18px;font-weight:700" id="cap-solves">-</div>
            <div style="font-size:9px;color:var(--dim)">Captchas</div>
          </div>
          <div style="background:var(--bg);padding:10px;border-radius:8px">
            <div style="font-size:18px;font-weight:700;color:var(--accent)" id="met-ccs">-</div>
            <div style="font-size:9px;color:var(--dim)">CCs</div>
          </div>
          <div style="background:var(--bg);padding:10px;border-radius:8px">
            <div style="font-size:18px;font-weight:700;color:var(--green)" id="met-ok">-</div>
            <div style="font-size:9px;color:var(--dim)">Aprovados</div>
          </div>
          <div style="background:var(--bg);padding:10px;border-radius:8px">
            <div style="font-size:18px;font-weight:700;color:var(--yellow)" id="met-proxy">-</div>
            <div style="font-size:9px;color:var(--dim)">Proxy MB</div>
          </div>
        </div>
        <div style="font-size:9px;color:var(--dim);margin-top:4px" id="met-updated">Atualiza a cada 5min</div>
      </div>
    </div>
  </div>

  <div class="grid">
    <div>
      <div class="card">
        <h2>💳 Adicionar Cartões</h2>
        <textarea id="cards" placeholder="5412xxxx|03|2027|123&#10;4111xxxx:03:2027:456&#10;5500xxxx,03,2027,789&#10;&#10;1 cartão por linha"></textarea>
        <div class="hint">Formatos aceitos: <b>cc|mm|ano|cvv</b> — <b>cc:mm:ano:cvv</b> — <b>cc,mm,ano,cvv</b></div>
        <div id="drop-zone" style="margin:8px 0;padding:14px;border:2px dashed var(--border);border-radius:8px;text-align:center;color:var(--dim);font-size:12px;cursor:pointer;transition:0.2s"
             ondragover="event.preventDefault();this.style.borderColor='var(--accent)';this.style.color='var(--accent)'"
             ondragleave="this.style.borderColor='var(--border)';this.style.color='var(--dim)'"
             ondrop="handleDrop(event)"
             onclick="document.getElementById('file-input').click()">
          📁 Arraste um arquivo .txt aqui ou clique pra selecionar
        </div>
        <input type="file" id="file-input" accept=".txt" style="display:none" onchange="handleFile(this.files[0])">
        <div id="validation-box" style="display:none;margin:8px 0;padding:10px;border-radius:8px;background:var(--bg);font-size:12px;max-height:120px;overflow-y:auto"></div>
        <div style="margin:8px 0">
          <div style="font-size:11px;color:var(--dim);margin-bottom:4px">Estados (clique pra selecionar):</div>
          <div id="uf-box" style="display:flex;flex-wrap:wrap;gap:4px">
            <label style="padding:4px 10px;border-radius:6px;background:var(--accent);color:white;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--accent)">
              <input type="checkbox" value="ALL" checked onchange="toggleAllUf(this)" style="display:none"> 🌎 Todos
            </label>
            {% for u in ufs %}<label class="uf-opt" style="padding:4px 10px;border-radius:6px;background:var(--bg);color:var(--dim);font-size:11px;cursor:pointer;border:1px solid var(--border);transition:0.2s">
              <input type="checkbox" value="{{u}}" onchange="ufChanged()" style="display:none"> {{u}}
            </label>{% endfor %}
          </div>
        </div>
        <button class="btn btn-primary" onclick="submitCards()">🚀 Enviar pra Fila</button>
        <div id="msg" style="margin-top:6px"></div>
      </div>
    </div>
    <div>
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <h2 style="margin:0">📋 Jobs</h2>
          <div style="display:flex;gap:6px">
            <button class="btn btn-sm" style="background:var(--red);color:white;padding:4px 10px" onclick="stopWorker()" title="Parar worker">🛑 Parar</button>
            <button class="btn btn-sm" style="background:#22c55e;color:white;padding:4px 10px" onclick="startWorker()" title="Iniciar worker">▶️ Iniciar</button>
            <button class="btn btn-sm" style="background:#b91c1c;color:white;padding:4px 10px" onclick="cancelJobs('all')" title="Cancelar jobs">⛔ Cancelar</button>
            <button class="btn btn-sm" style="background:var(--dim);color:white;padding:4px 10px" onclick="clearVisualList()" title="Limpar lista">🗑️ Limpar</button>
            <button class="btn btn-sm" style="background:#6366f1;color:white;padding:4px 10px" onclick="cancelJobs('reset')" title="Deletar tudo">🔄 Reset</button>
            <button class="btn btn-secondary btn-sm" onclick="loadJobs()">⟳</button>
          </div>
        </div>
        <!-- LIVE PANEL -->
        <div id="live-panel" style="display:none;margin-bottom:12px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <div style="font-size:13px;font-weight:700;color:var(--accent2)">🔴 LIVE — <span id="lv-count">0</span> threads ativas</div>
            <div id="lv-rates" style="font-size:11px;display:flex;gap:12px"></div>
          </div>
          <div id="lv-bar" style="height:6px;border-radius:3px;background:var(--surface2);margin-bottom:8px;overflow:hidden">
            <div id="lv-bar-fill" style="height:100%;border-radius:3px;transition:width 0.5s;background:linear-gradient(90deg,var(--green),var(--accent))"></div>
          </div>
          <div id="lv-threads" style="display:flex;flex-wrap:wrap;gap:6px"></div>
        </div>
        <div class="jobs-list">
          <table><thead><tr>
            <th>Cartão</th><th>Status</th><th>Resultado</th>
          </tr></thead>
          <tbody id="jbody"><tr><td colspan="3" class="dim">Carregando...</td></tr></tbody></table>
        </div>
      </div>
    </div>
  </div>
</div>
<script>
let cF='active';
let BATCH_TS=0;
let BATCH_ID=0;
function toast(m,ok){const d=document.createElement('div');d.className='toast '+(ok?'toast-ok':'toast-err');d.textContent=m;document.body.appendChild(d);setTimeout(()=>d.remove(),3000)}
function toggleAllUf(cb){
  const labels=document.querySelectorAll('.uf-opt');
  labels.forEach(l=>{
    const inp=l.querySelector('input');inp.checked=false;
    l.style.background='var(--bg)';l.style.color='var(--dim)';l.style.borderColor='var(--border)';
  });
  if(cb.checked){
    cb.parentElement.style.background='var(--accent)';cb.parentElement.style.color='white';cb.parentElement.style.borderColor='var(--accent)';
  }
}
function ufChanged(){
  const allCb=document.querySelector('#uf-box input[value=ALL]');
  const labels=document.querySelectorAll('.uf-opt');
  const any=[...labels].some(l=>l.querySelector('input').checked);
  if(any){allCb.checked=false;allCb.parentElement.style.background='var(--bg)';allCb.parentElement.style.color='var(--dim)';allCb.parentElement.style.borderColor='var(--border)'}
  labels.forEach(l=>{
    const inp=l.querySelector('input');
    if(inp.checked){l.style.background='var(--accent)';l.style.color='white';l.style.borderColor='var(--accent)'}
    else{l.style.background='var(--bg)';l.style.color='var(--dim)';l.style.borderColor='var(--border)'}
  });
  if(!any){allCb.checked=true;allCb.parentElement.style.background='var(--accent)';allCb.parentElement.style.color='white';allCb.parentElement.style.borderColor='var(--accent)'}
}
function getSelectedUFs(){
  const allCb=document.querySelector('#uf-box input[value=ALL]');
  if(allCb&&allCb.checked)return 'ALL';
  const checked=[...document.querySelectorAll('.uf-opt input:checked')].map(i=>i.value);
  return checked.length?checked.join(','):'ALL';
}
async function cancelJobs(mode){
  const labels={'all':'TODOS os jobs e limpar CCs','queued':'jobs da fila e limpar CCs','purge':'jobs cancelados + failed (limpar histórico)','reset':'RESET COMPLETO - deletar TODOS os jobs (success, failed, cancelled). Tem certeza?'};
  if(!confirm(`${labels[mode]||mode}?`))return;
  const r=await fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});
  const d=await r.json();
  toast(d.msg,d.ok);loadJobs();loadStats();
}
async function stopWorker(){
  if(!confirm('Parar worker? Isso vai parar o processamento de jobs.'))return;
  const r=await fetch('/api/worker/stop',{method:'POST'});
  const d=await r.json();
  toast(d.msg,d.ok);
}
async function startWorker(){
  if(!confirm('Iniciar worker?'))return;
  const r=await fetch('/api/worker/start',{method:'POST'});
  const d=await r.json();
  toast(d.msg,d.ok);
  loadJobs();
}
function clearVisualList(){
  document.getElementById('jobs-list').innerHTML='';
  toast('Lista visual limpa',true);
}
async function submitCards(){
  const t=document.getElementById('cards').value.trim(),uf=getSelectedUFs();
  if(!t){toast('Cole os cartões',false);return}
  const r=await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cards_text:t,uf})});
  const d=await r.json();
  if(d.ok){
    BATCH_TS=Math.floor(Date.now()/1000)-5;if(d.batch_id)BATCH_ID=d.batch_id;document.getElementById('s-ok').textContent='0';document.getElementById('s-f').textContent='0';document.getElementById('s-progress').textContent='...';toast(d.msg,true);document.getElementById('cards').value='';hideValidation();loadJobs();loadStats();
    if(d.errors&&d.errors.length)showErrors(d.errors);
  }else{
    toast(d.msg||'Erro',false);
    if(d.errors&&d.errors.length)showErrors(d.errors);
  }
}
function showErrors(errs){
  const box=document.getElementById('validation-box');
  box.style.display='block';
  box.innerHTML='<div style="color:var(--red);font-weight:600;margin-bottom:4px">⚠️ Erros encontrados:</div>'+errs.map(e=>`<div style="color:var(--yellow);padding:2px 0">• ${e}</div>`).join('');
}
function hideValidation(){document.getElementById('validation-box').style.display='none'}
function handleDrop(e){
  e.preventDefault();
  e.currentTarget.style.borderColor='var(--border)';e.currentTarget.style.color='var(--dim)';
  const f=e.dataTransfer.files[0];
  if(f)handleFile(f);
}
function handleFile(f){
  if(!f)return;
  if(!f.name.endsWith('.txt')){toast('Só aceita arquivo .txt',false);return}
  const reader=new FileReader();
  reader.onload=async function(e){
    const text=e.target.result.trim();
    if(!text){toast('Arquivo vazio',false);return}
    document.getElementById('cards').value=text;
    // Validar preview
    const r=await fetch('/api/validate_cards',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cards_text:text})});
    const d=await r.json();
    const box=document.getElementById('validation-box');
    box.style.display='block';
    let html=`<div style="color:var(--green);font-weight:600;margin-bottom:4px">✅ ${d.valid} cartões válidos</div>`;
    if(d.preview&&d.preview.length){
      html+='<div style="color:var(--dim);margin-bottom:4px">Preview:</div>';
      html+=d.preview.map(p=>`<div class="mono" style="padding:1px 0">${p}</div>`).join('');
      if(d.valid>20)html+=`<div class="dim">... e mais ${d.valid-20}</div>`;
    }
    if(d.errors&&d.errors.length){
      html+=`<div style="color:var(--red);font-weight:600;margin-top:6px">⚠️ ${d.errors.length} erros:</div>`;
      html+=d.errors.map(e=>`<div style="color:var(--yellow);padding:1px 0">• ${e}</div>`).join('');
    }
    box.innerHTML=html;
    if(!d.valid)toast('Nenhum cartão válido no arquivo',false);
    else toast(`📁 ${f.name}: ${d.valid} cartões carregados`,true);
  };
  reader.readAsText(f);
}
function setF(){}
async function loadStats(){try{const [r,jr]=await Promise.all([fetch('/api/stats'),fetch('/api/jobs?exclude=queued')]);const s=await r.json(),jobs=await jr.json();const q=s.queued||0,p=s.processing||0;const bj=BATCH_TS>0?jobs.filter(j=>j.created_at>=BATCH_TS):[];const allBatch=[...bj];const uniqueCards=new Set(allBatch.map(j=>j.card_number));const doneCards=new Set();allBatch.filter(j=>j.status==='success'||j.status==='failed').forEach(j=>doneCards.add(j.card_number));const procCards=new Set();allBatch.filter(j=>j.status==='processing').forEach(j=>procCards.add(j.card_number));const totalCards=uniqueCards.size+(q>0?q:0);const doneCount=doneCards.size;document.getElementById('s-progress').textContent=totalCards>0?doneCount+'/'+totalCards:(BATCH_TS>0?'idle':'-');let maxCycle=1;allBatch.forEach(j=>{if(j.cycle&&j.cycle>maxCycle)maxCycle=j.cycle});document.getElementById('s-cycle').textContent=(q+p>0||totalCards>0)?maxCycle+'/3':'-';const liveCards=new Set(allBatch.filter(j=>j.status==='success').map(j=>j.card_number)).size;let dieCount=0;doneCards.forEach(cn=>{const cardJobs=allBatch.filter(j=>j.card_number===cn);const hasSuccess=cardJobs.some(j=>j.status==='success');const hasActive=cardJobs.some(j=>j.status==='processing'||j.status==='queued');if(!hasSuccess&&!hasActive)dieCount++;});document.getElementById('s-ok').textContent=liveCards;document.getElementById('s-f').textContent=dieCount;}catch(e){console.error('loadStats error',e)}}
async function loadJobs(){try{
  const [r,sr]=await Promise.all([fetch('/api/jobs?exclude=queued'),fetch('/api/stats')]);
  const jobs=await r.json(),stats=await sr.json(),tb=document.getElementById('jbody');
  // Live panel com processing jobs + stats globais
  updateLivePanel(jobs,stats);
  // Tabela: só resultados (success/failed)
  const results=(BATCH_TS>0?jobs.filter(j=>(j.status==='success'||j.status==='failed')&&j.created_at>=BATCH_TS):jobs.filter(j=>j.status==='success'||j.status==='failed'));
  if(!results.length){tb.innerHTML='<tr><td colspan="3" class="dim">Nenhum resultado ainda</td></tr>';return}
  tb.innerHTML=results.map(j=>{
    const card=j.card_number;
    const cardFull=`${card} ${j.card_month||''}/${j.card_year||''} CVV:${j.card_cvv||''}`;
    const statusLabel=j.status==='success'?'LIVE':'DIE';
    const badge=`<span class="badge badge-${j.status}">${statusLabel}</span>`;
    let res='';
    if(j.status==='success'){
      res=`<div><span style="color:var(--green)">${j.msisdn||''}</span>`;
      if(j.order_code)res+=` <span class="dim">#${j.order_code}</span>`;
      res+=`</div>`;
      if(j.email)res+=`<div class="dim" style="font-size:10px">📧 ${j.email}</div>`;
    }
    else res=`<span class="dim">${j.error_msg||''}</span>`;
    // Botão eSIMs só aparece se não tem QR (aguardando eSIM)
    const btn=(j.status==='success' && !j.qr_url && !j.qr_path)?`<button class="btn btn-sm" style="background:var(--accent);color:white;font-size:10px;padding:2px 8px;margin-left:8px" onclick="window.open('/esims','_blank')">📱 eSIMs</button>`:'';
    return `<tr><td class="mono" style="font-size:10px">${cardFull}</td><td>${badge}</td><td>${res}${btn}</td></tr>`
  }).join('');
}catch(e){console.error(e)}}
function updateLivePanel(jobs,stats){
  const active=jobs.filter(j=>j.status==='processing');
  const panel=document.getElementById('live-panel');
  if(!active.length){panel.style.display='none';if(BATCH_TS>0){const bj=jobs.filter(j=>j.created_at>=BATCH_TS);const ok=bj.filter(j=>j.status==='success').length;const fail=bj.filter(j=>j.status==='failed').length;const done=ok+fail;document.getElementById('s-ok').textContent=ok;document.getElementById('s-f').textContent=fail;document.getElementById('s-progress').textContent=done>0?'done ('+done+')':'-';document.getElementById('lv-rates').innerHTML='<span style="color:var(--green)">✅ '+ok+' LIVE</span> '+'<span style="color:var(--red)">❌ '+fail+' DIE</span> '+'<span class="dim">📊 '+done+' processados</span>';}return}
  panel.style.display='block';
  document.getElementById('lv-count').textContent=active.length;
  // Cores por step
  const sc={'SESSÃO':'#6366f1','OFERTA':'#8b5cf6','CPF':'#a78bfa','EMAIL':'#818cf8','OTP':'#f59e0b','OTP ⏳':'#eab308','OTP OK':'#84cc16','CADASTRO':'#06b6d4','NÚMERO':'#14b8a6','CAPTCHA':'#f97316','CARD LIVE':'#22c55e','CARD DIE':'#ef4444'};
  const stepOrder=['SESSÃO','OFERTA','CPF','EMAIL','OTP','OTP ⏳','OTP OK','CADASTRO','NÚMERO','CAPTCHA'];
  document.getElementById('lv-threads').innerHTML=active.map(j=>{
    const card=j.card_number;
    const step=j.current_step||'SESSÃO';
    const idx=stepOrder.indexOf(step);
    const pct=idx>=0?Math.round((idx+1)/stepOrder.length*100):0;
    const bg=sc[step]||'var(--accent2)';
    const cpf=j.cpf||'?';
    const now=Date.now()/1000;
    const elapsed=j.created_at?Math.round(now-j.created_at)+'s':'';
    return `<div style="background:var(--surface);border:1px solid ${bg}55;border-radius:8px;padding:6px 10px;font-size:11px;min-width:150px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <span style="font-weight:700;color:${bg};font-size:13px">${step}</span>
        <span class="dim" style="font-size:9px">${elapsed}</span>
      </div>
      <div style="height:4px;border-radius:2px;background:var(--surface2);margin-bottom:4px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${bg};border-radius:2px;transition:width 0.5s"></div>
      </div>
      <div class="mono" style="font-size:10px;color:var(--text)">${card} · ${cpf}</div>
    </div>`
  }).join('');
  // Rates — usa stats globais do /api/stats pra não truncar pelo limit=200
  const bj=BATCH_TS>0?jobs.filter(j=>j.created_at>=BATCH_TS):jobs;const ok=bj.filter(j=>j.status==='success').length;
  const fail=bj.filter(j=>j.status==='failed').length;
  const done=ok+fail;
  const pctOk=done?Math.round(ok/done*100):0;
  const pctFail=done?Math.round(fail/done*100):0;
  document.getElementById('lv-rates').innerHTML=
    `<span style="color:var(--green)">✅ ${ok} LIVE (${pctOk}%)</span>`+
    `<span style="color:var(--red)">❌ ${fail} DIE (${pctFail}%)</span>`+
    `<span class="dim">📊 ${done} processados</span>`;
  // Progress bar
  const total=bj.length+(stats?stats.queued:0);
  const pct=total?Math.round(done/total*100):0;
  document.getElementById('lv-bar-fill').style.width=pct+'%';
  document.getElementById('s-ok').textContent=ok;
  document.getElementById('s-f').textContent=fail;
  document.getElementById('s-progress').textContent=total?(done+'/'+total):'-';
}
// ── 2Captcha + Métricas ──
async function saveCaptchaKey(){
  const key=document.getElementById('cap-key').value.trim();
  if(!key){toast('Cole a key',false);return}
  document.getElementById('cap-msg').innerHTML='<span class="dim">Testando...</span>';
  const r=await fetch('/api/captcha_key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
  const d=await r.json();
  document.getElementById('cap-msg').innerHTML=d.ok?`<span style="color:var(--green)">${d.msg}</span>`:`<span style="color:var(--red)">${d.msg}</span>`;
  if(d.ok){document.getElementById('cap-key').value='';loadCaptchaStatus();loadMetrics()}
}
async function loadCaptchaStatus(){try{
  const r=await fetch('/api/captcha_key'),d=await r.json();
  let h=`Key: <b>${d.key_status}</b>`;
  if(d.balance!==null)h+=` — <span style="color:var(--green)">$${d.balance.toFixed(2)}</span>`;
  document.getElementById('cap-status').innerHTML=h
}catch(e){}}
async function loadMetrics(){try{
  const r=await fetch('/api/metrics'),d=await r.json();
  document.getElementById('cap-balance').textContent=d.captcha_balance!==null?'$'+d.captcha_balance.toFixed(2):'—';
  document.getElementById('cap-spent').textContent='$'+d.captcha_spent_today.toFixed(4);
  document.getElementById('cap-solves').textContent=d.captcha_solves_today;
  document.getElementById('met-ccs').textContent=d.unique_ccs_today;
  document.getElementById('met-ok').textContent=d.success_today;
  document.getElementById('met-proxy').textContent=d.proxy_mb_today>=1024?(d.proxy_mb_today/1024).toFixed(1)+'G':d.proxy_mb_today+'M';
  document.getElementById('met-updated').textContent='Atualizado: '+new Date().toLocaleTimeString()
}catch(e){}}
setInterval(()=>{loadStats();loadJobs()},3000);
setInterval(()=>{loadMetrics();loadCaptchaStatus()},300000);
loadStats();loadJobs();loadCaptchaStatus();loadMetrics();
</script></body></html>
"""

TEMPLATE_ADMIN = """
<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin — Vivo Easy</title>
<style>""" + STYLE + """</style>
</head><body>
<div class="container">
  <div class="header">
    <h1>🔧 <span>Admin</span> Painel</h1>
    <div class="stats">
      <div class="stat"><div class="num" id="em-n">-</div><div class="label">Emails</div></div>
      <div class="stat"><div class="num" id="cpf-n">-</div><div class="label">CPFs</div></div>
      <div class="stat ok"><div class="num" id="job-ok">-</div><div class="label">Aprovados</div></div>
      <div class="stat queue"><div class="num" id="job-q">-</div><div class="label">Fila</div></div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="switchTab('emails')">📧 Emails</div>
    <div class="tab" onclick="switchTab('cpfs')">👤 CPFs</div>
    <div class="tab" onclick="switchTab('jobs')">📋 Jobs</div>
    <div class="tab" onclick="switchTab('config')">⚙️ Config</div>
  </div>

  <!-- EMAILS -->
  <div id="tab-emails" class="tab-content active">
    <div class="grid">
      <div>
        <div class="card">
          <h2>Adicionar Hotmails</h2>
          <textarea id="em-input" placeholder="email:password:refresh_token:client_id&#10;1 conta por linha"></textarea>
          <div class="hint">Formato: email:password:refresh_token:client_id</div>
          <button class="btn btn-primary" onclick="addEmails()">➕ Adicionar</button>
          <div id="em-msg" style="margin-top:6px"></div>
        </div>
      </div>
      <div>
        <div class="card">
          <h2>Pool Atual</h2>
          <div class="email-list">
            <table><thead><tr><th>Email</th><th>Status</th><th></th></tr></thead>
            <tbody id="em-body"><tr><td colspan="3" class="dim">Carregando...</td></tr></tbody></table>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- CPFS -->
  <div id="tab-cpfs" class="tab-content">
    <div class="card" style="max-width:500px">
      <h2>👤 CPFs — Supabase (Automático)</h2>
      <p class="dim" style="margin-bottom:12px">CPFs são gerenciados automaticamente via Supabase. Não precisa importar.</p>
      <div id="cpf-stats" style="font-size:14px">Carregando...</div>
    </div>
  </div>

  <!-- JOBS -->
  <div id="tab-jobs" class="tab-content">
    <div class="card">
      <h2>Últimos Jobs</h2>
      <div class="jobs-list">
        <table><thead><tr>
          <th>#</th><th>Cartão</th><th>UF</th><th>CPF</th><th>Email</th><th>Status</th><th>Resultado</th>
        </tr></thead>
        <tbody id="aj-body"><tr><td colspan="7" class="dim">Carregando...</td></tr></tbody></table>
      </div>
    </div>
  </div>

  <!-- CONFIG -->
  <div id="tab-config" class="tab-content">
    <div class="grid">
      <div>
        <div class="card">
          <h2>🔧 Worker Threads</h2>
          <p class="dim" style="margin-bottom:12px;font-size:12px">Quantidade de threads paralelas que o worker usa. Alteração requer reinício do worker.</p>
          <div style="display:flex;gap:8px;align-items:center">
            <input id="cfg-threads" type="number" min="1" max="50" value="10" style="width:80px;text-align:center;font-size:18px;font-weight:700;padding:10px">
            <button class="btn btn-primary" onclick="saveThreads()">💾 Salvar</button>
          </div>
          <div id="cfg-threads-msg" style="margin-top:6px;font-size:12px"></div>
        </div>
      </div>
      <div>
        <div class="card">
          <h2>🌐 Proxy</h2>
          <p class="dim" style="margin-bottom:12px;font-size:12px">Proxy atualmente em uso. Formato: host:port:user:password</p>
          <div style="display:flex;flex-direction:column;gap:8px">
            <input id="cfg-proxy" type="text" placeholder="134.119.184.115:9000:user:password" style="width:100%;font-size:13px;padding:10px;font-family:monospace">
            <button class="btn btn-primary" onclick="saveProxy()">💾 Salvar Proxy</button>
          </div>
          <div id="cfg-proxy-msg" style="margin-top:6px;font-size:12px"></div>
        </div>
      </div>
    </div>
  </div>
</div>
<script>
function toast(m,ok){const d=document.createElement('div');d.className='toast '+(ok?'toast-ok':'toast-err');d.textContent=m;document.body.appendChild(d);setTimeout(()=>d.remove(),3000)}
function switchTab(t){
  document.querySelectorAll('.tab').forEach((el,i)=>{el.classList.remove('active')});
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  document.querySelector(`.tab[onclick*="${t}"]`).classList.add('active');
}
async function addEmails(){
  const t=document.getElementById('em-input').value.trim();
  if(!t){toast('Cole os emails',false);return}
  const r=await fetch('/admin/api/emails',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lines:t})});
  const d=await r.json();toast(d.msg,d.ok);if(d.ok){document.getElementById('em-input').value='';loadEmails();loadAdminStats()}
}
async function loadEmails(){try{
  const r=await fetch('/admin/api/emails'),rows=await r.json(),tb=document.getElementById('em-body');
  if(!rows.length){tb.innerHTML='<tr><td colspan="3" class="dim">Nenhum email</td></tr>';return}
  tb.innerHTML=rows.map(e=>`<tr><td class="mono">${e.email}</td><td><span class="badge badge-${e.status==='active'?'success':'failed'}">${e.status}</span></td><td><button class="btn btn-danger btn-sm" onclick="delEmail(${e.id})">✕</button></td></tr>`).join('')
}catch(e){}}
async function delEmail(id){
  if(!confirm('Remover?'))return;
  await fetch('/admin/api/emails/'+id,{method:'DELETE'});loadEmails();loadAdminStats()
}
async function addCpfs(){
  const t=document.getElementById('cpf-input').value.trim();
  if(!t){toast('Cole os CPFs',false);return}
  const r=await fetch('/admin/api/cpfs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lines:t})});
  const d=await r.json();toast(d.msg,d.ok);if(d.ok){document.getElementById('cpf-input').value='';loadCpfStats()}
}
async function loadCpfStats(){try{
  const r=await fetch('/admin/api/cpfs/stats'),s=await r.json();
  document.getElementById('cpf-stats').innerHTML=`<p>📦 Disponíveis: <b style="color:var(--green)">${s.available}</b></p><p>✅ Usados: <b>${s.used}</b></p><p>📊 Total: <b>${s.total}</b></p>`;
  document.getElementById('cpf-n').textContent=s.available
}catch(e){}}
async function loadAdminStats(){try{
  const[em,st]=await Promise.all([fetch('/admin/api/emails/count').then(r=>r.json()),fetch('/api/stats').then(r=>r.json())]);
  document.getElementById('em-n').textContent=em.active;
  document.getElementById('job-ok').textContent=st.success;
  document.getElementById('job-q').textContent=st.queued
}catch(e){}}
async function loadAdminJobs(){try{
  const r=await fetch('/api/jobs?limit=100'),jobs=await r.json(),tb=document.getElementById('aj-body');
  if(!jobs.length){tb.innerHTML='<tr><td colspan="7" class="dim">Nenhum job</td></tr>';return}
  tb.innerHTML=jobs.map(j=>{
    const card='****'+j.card_number.slice(-4),badge=`<span class="badge badge-${j.status}">${j.status}</span>`;
    let res='';if(j.status==='success')res=j.msisdn||'';else if(j.status==='failed')res=j.error_msg||'';
    return `<tr><td class="dim">${j.id}</td><td class="mono">${card}</td><td>${j.uf}</td><td class="mono">${j.cpf||'-'}</td><td class="mono" style="font-size:10px">${j.email||'-'}</td><td>${badge}</td><td class="dim">${res}</td></tr>`
  }).join('')}catch(e){}}
async function loadThreads(){try{
  const r=await fetch('/admin/api/workers'),d=await r.json();
  document.getElementById('cfg-threads').value=d.threads||10
}catch(e){}}
async function saveThreads(){
  const n=parseInt(document.getElementById('cfg-threads').value);
  if(!n||n<1||n>50){toast('Valor entre 1 e 50',false);return}
  const r=await fetch('/admin/api/workers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({threads:n})});
  const d=await r.json();
  const el=document.getElementById('cfg-threads-msg');
  if(d.ok){toast(d.msg,true);el.innerHTML='<span style="color:var(--green)">'+d.msg+'</span>'}
  else{toast(d.msg||'Erro',false);el.innerHTML='<span style="color:var(--red)">'+(d.msg||'Erro')+'</span>'}
}
async function loadProxy(){try{
  const r=await fetch('/api/config/proxy'),d=await r.json();
  document.getElementById('cfg-proxy').value=d.proxy||''
}catch(e){}}
async function saveProxy(){
  const p=document.getElementById('cfg-proxy').value.trim();
  if(!p){toast('Proxy vazio',false);return}
  const r=await fetch('/api/config/proxy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({proxy:p})});
  const d=await r.json();
  const el=document.getElementById('cfg-proxy-msg');
  if(d.ok){toast(d.msg,true);el.innerHTML='<span style="color:var(--green)">'+d.msg+'</span>'}
  else{toast(d.msg||'Erro',false);el.innerHTML='<span style="color:var(--red)">'+(d.msg||'Erro')+'</span>'}
}
setInterval(()=>{loadAdminStats();loadEmails();loadCpfStats();loadAdminJobs()},8000);
loadAdminStats();loadEmails();loadCpfStats();loadAdminJobs();loadThreads();loadProxy();
</script></body></html>
"""

TEMPLATE_LOGIN = """
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Admin Login</title>
<style>""" + STYLE + """
.login-box{max-width:320px;margin:15vh auto;text-align:center}
</style></head><body>
<div class="login-box">
  <div class="card">
    <h2>🔐 Admin</h2>
    <form method="POST">
      <input type="password" name="password" placeholder="Senha admin" style="text-align:center">
      <button class="btn btn-primary" type="submit">Entrar</button>
    </form>
    {% if error %}<p style="color:var(--red);margin-top:8px;font-size:12px">{{error}}</p>{% endif %}
  </div>
</div>
</body></html>
"""

TEMPLATE_CLIENT_LOGIN = """
<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vivo Easy</title>
<style>""" + STYLE + """
.login-box{max-width:340px;margin:15vh auto;text-align:center}
</style></head><body>
<div class="login-box">
  <div class="card">
    <h1 style="margin-bottom:16px">⚡ Vivo <span style="color:var(--accent)">Easy</span></h1>
    <form method="POST">
      <input type="password" name="password" placeholder="Senha de acesso" style="text-align:center">
      <button class="btn btn-primary" type="submit">Entrar</button>
    </form>
    {% if error %}<p style="color:var(--red);margin-top:8px;font-size:12px">{{error}}</p>{% endif %}
  </div>
</div>
</body></html>
"""

TEMPLATE_ESIMS = """
<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>eSIMs — Vivo Easy</title>
<style>""" + STYLE + """
.esim-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.esim-card { background: var(--surface); border-radius: 12px; padding: 16px; border: 1px solid var(--border); cursor: pointer; transition: 0.2s; }
.esim-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.esim-card .num { font-size: 18px; font-weight: 700; margin-bottom: 6px; color: #fff; }
.esim-card .meta { font-size: 11px; color: #fff; }
.esim-card .qr-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; background: rgba(0,210,106,0.12); color: var(--green); margin-top: 6px; }
.esim-card .no-qr { background: rgba(255,214,10,0.12); color: var(--yellow); }
.esim-card .ext-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; background: rgba(136,136,136,0.15); color: var(--dim); margin-top: 6px; margin-left: 4px; }
.esim-card.extracted { opacity: 0.5; }
.esim-card.selected { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(99,102,241,0.3); }
.esim-check { position: absolute; top: 8px; right: 8px; width: 18px; height: 18px; accent-color: var(--accent); cursor: pointer; z-index: 2; }
.esim-card { position: relative; }
.sel-bar { display:none; background: var(--glass); border: 1px solid var(--accent); border-radius: 10px; padding: 10px 16px; margin-bottom: 12px; align-items: center; gap: 10px; flex-wrap: wrap; }
.sel-bar.active { display: flex; }
.sel-bar .sel-count { font-size: 13px; font-weight: 700; color: var(--accent); }
.esim-card .wa-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; margin-top: 4px; }
.wa-yes { background: rgba(37,211,102,0.15); color: #25d366; }
.wa-no { background: rgba(255,59,59,0.12); color: var(--red); }
.wa-unk { background: rgba(136,136,136,0.12); color: var(--dim); }
.wa-stats { display: flex; gap: 12px; align-items: center; padding: 10px 16px; background: var(--surface); border-radius: 10px; border: 1px solid var(--border); margin-bottom: 12px; flex-wrap: wrap; }
.wa-stats .ws { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 4px; }
.wa-stats .ws .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.wa-stats .dot-g { background: #25d366; } .wa-stats .dot-r { background: var(--red); } .wa-stats .dot-d { background: var(--dim); }
.ddd-ranges { display: grid; grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); gap: 10px; margin-bottom: 16px; }
.ddd-range-card { background: var(--glass); border: 1px solid var(--glass-border); border-radius: 12px; padding: 14px; cursor: pointer; transition: all .15s; text-align: center; }
.ddd-range-card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 4px 16px rgba(124,110,240,.15); }
.ddd-range-card .range-label { font-size: 14px; font-weight: 800; color: var(--accent2); }
.ddd-range-card .range-count { font-size: 28px; font-weight: 900; margin: 2px 0; }
.ddd-range-card .range-sub { font-size: 10px; color: var(--dim); }
.ddd-range-card .range-zap { font-size: 10px; color: #25d366; margin-top: 4px; }
.ddd-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 200; justify-content: center; align-items: center; }
.ddd-overlay.open { display: flex; }
.ddd-modal { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 24px; max-width: 850px; width: 95%; max-height: 85vh; overflow-y: auto; position: relative; }
.ddd-modal h2 { font-size: 16px; color: var(--accent2); margin-bottom: 12px; }
.ddd-modal .close-btn { position: absolute; top: 12px; right: 16px; background: none; border: none; color: var(--dim); font-size: 20px; cursor: pointer; }
.ddd-modal .close-btn:hover { color: var(--text); }
.ddd-modal table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 10px; }
.ddd-modal th { background: var(--bg); color: var(--dim); padding: 8px 10px; text-align: left; font-size: 9px; text-transform: uppercase; letter-spacing: .5px; }
.ddd-modal td { padding: 7px 10px; border-bottom: 1px solid var(--border); font-family: 'SF Mono','Consolas',monospace; font-size: 12px; }
.modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; }
.modal-overlay.open { display: flex; }
.modal { background: var(--surface); border-radius: 16px; padding: 24px; max-width: 480px; width: 90%; border: 1px solid var(--border); max-height: 90vh; overflow-y: auto; }
.modal h2 { margin-bottom: 16px; font-size: 18px; }
.modal .qr-img { display: block; margin: 0 auto 16px; max-width: 200px; border-radius: 8px; }
.modal .info-grid { display: grid; grid-template-columns: 90px 1fr; gap: 6px; font-size: 13px; }
.modal .info-grid .label { color: var(--dim); }
.modal .info-grid .value { word-break: break-all; }
.modal .actions { display: flex; gap: 8px; margin-top: 16px; }
.nav-bar { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; }
.nav-bar a { color: var(--accent2); text-decoration: none; font-size: 13px; }
.export-bar { display: flex; gap: 8px; margin-bottom: 16px; }
</style>
</head><body>
<div class="container">
  <div class="header">
    <h1>📱 <span style="color:var(--accent)">eSIMs</span></h1>
    <div class="stats">
      <div class="stat ok"><div class="num" id="es-ok">-</div><div class="label">Prontos</div></div>
      <div class="stat queue"><div class="num" id="es-wait">-</div><div class="label">Aguardando</div></div>
      <div class="stat"><div class="num" id="es-ext" style="color:var(--dim)">-</div><div class="label">Extraídos</div></div>
    </div>
  </div>
  <div class="nav-bar">
    <a href="/">← Dashboard</a>
    <span class="dim">|</span>
    <a href="/recovery">🔑 Recovery</a>
  </div>
  <div class="export-bar">
    <button class="btn btn-secondary btn-sm" onclick="loadEsims()">⟳ Atualizar</button>
    <button class="btn btn-primary btn-sm" onclick="exportAll()">📦 Exportar Todos (ZIP)</button>
    <button class="btn btn-secondary btn-sm" onclick="copyAll()">📋 Copiar Todos</button>
    <button class="btn btn-sm" style="background:#f59e0b;color:white" onclick="exportCards()">💳 Exportar Cards</button>
    <button class="btn btn-sm" style="background:#25d366;color:white" onclick="enrichWhatsApp()" id="btn-enrich">📞 Verificar WhatsApp</button>
    <button class="btn btn-sm" style="background:var(--dim);color:white" onclick="markAllExtracted()">✅ Marcar Todos Extraídos</button>
    <button class="btn btn-sm" style="background:#ef4444;color:white" onclick="clearHistory()">🗑️ Limpar Histórico</button>
    <a href="/reset-senha" class="btn btn-sm" style="background:#6366f1;color:white;text-decoration:none;display:inline-flex;align-items:center">🔐 Reset Senha</a>
    <span id="enrich-progress" style="font-size:11px;color:var(--dim);display:none"></span>
  </div>
  <div class="wa-stats" id="wa-stats" style="display:none">
    <span class="ws"><span class="dot dot-g"></span> <span id="wa-ok">0</span> com ZAP</span>
    <span class="ws"><span class="dot dot-r"></span> <span id="wa-no">0</span> sem ZAP</span>
    <span class="ws"><span class="dot dot-d"></span> <span id="wa-unk">0</span> não checado</span>
    <button class="btn btn-sm" style="background:#25d366;color:white;margin-left:auto" onclick="copyWaNumbers()">📋 Copiar c/ ZAP</button>
  </div>
  <!-- Selection Bar -->
  <div class="sel-bar" id="sel-bar">
    <span class="sel-count" id="sel-count">0 selecionados</span>
    <button class="btn btn-sm" style="background:var(--accent);color:white" onclick="selectAll()">Selecionar Todos</button>
    <button class="btn btn-sm" style="background:var(--surface);color:var(--text)" onclick="deselectAll()">Limpar</button>
    <button class="btn btn-primary btn-sm" onclick="exportSelected()">ZIP Selecionados</button>
    <button class="btn btn-secondary btn-sm" onclick="copySelected()">Copiar Selecionados</button>
    <button class="btn btn-sm" style="background:var(--dim);color:white" onclick="markSelectedExtracted()">Marcar Selecionados Extraidos</button>
  </div>
  <!-- DDD Range Cards -->
  <div class="ddd-ranges" id="ddd-ranges"></div>

  <div class="esim-grid" id="esim-grid">
    <div class="dim">Carregando...</div>
  </div>
</div>

<!-- DDD Range Modal -->
<div class="ddd-overlay" id="ddd-overlay" onclick="if(event.target===this)closeDddModal()">
  <div class="ddd-modal">
    <button class="close-btn" onclick="closeDddModal()">&times;</button>
    <h2 id="ddd-modal-title">DDD Range</h2>
    <div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-secondary btn-sm" onclick="copyDddNumbers()">📋 Copiar Números</button>
      <button class="btn btn-sm" style="background:#25d366;color:white" onclick="copyDddFull()">📋 Copiar Completo</button>
      <button class="btn btn-primary btn-sm" onclick="downloadDddZip()">📦 Baixar ZIP</button>
    </div>
    <div id="ddd-modal-count" style="font-size:12px;color:var(--dim);margin-bottom:8px"></div>
    <table>
      <thead><tr><th>DDD</th><th>Número</th><th>CPF</th><th>Senha</th><th>📞</th></tr></thead>
      <tbody id="ddd-modal-body"></tbody>
    </table>
  </div>
</div>

<!-- MODAL -->
<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <h2 id="m-title">-</h2>
    <img id="m-qr" class="qr-img" src="" style="display:none">
    <div class="info-grid" id="m-info"></div>
    <div class="actions">
      <button class="btn btn-primary btn-sm" onclick="copyModalData()">📋 Copiar</button>
      <button class="btn btn-secondary btn-sm" onclick="exportOne()">📦 ZIP</button>
      <button class="btn btn-sm" style="background:var(--dim);color:white" onclick="markOneExtracted()">✅ Extraído</button>
      <button class="btn btn-secondary btn-sm" onclick="closeModal()">Fechar</button>
    </div>
  </div>
</div>

<script>
let ESIMS=[], MODAL_JOB=null, WA_CACHE={}, SELECTED=new Set();
function toast(m,ok){const d=document.createElement('div');d.className='toast '+(ok?'toast-ok':'toast-err');d.textContent=m;document.body.appendChild(d);setTimeout(()=>d.remove(),3000)}

async function loadEsims(){
  const r=await fetch('/api/esims');
  ESIMS=(await r.json()).filter(e=>e.created_at>=1773810000);
  ESIMS.forEach(e=>{const ph=(e.msisdn||'').startsWith('55')?(e.msisdn||''):'55'+(e.msisdn||'');if(e.whatsapp===1)WA_CACHE[ph]=true;else if(e.whatsapp===0)WA_CACHE[ph]=false;});
  const grid=document.getElementById('esim-grid');
  let ready=0,waiting=0,ext=0;
  ESIMS.forEach(e=>{if(e.extracted){ext++;ready++}else if(e.qr_url||e.qr_path)ready++;else waiting++});
  document.getElementById('es-ok').textContent=ready;
  document.getElementById('es-wait').textContent=waiting;
  document.getElementById('es-ext').textContent=ext;
  updateWaStats();
  loadDddRanges();
  if(!ESIMS.length){grid.innerHTML='<div class="dim">Nenhum eSIM aprovado ainda</div>';return}
  grid.innerHTML=ESIMS.map(e=>{
    const num=e.msisdn||'?';
    const numFmt=num.length===11?num.replace(/(\\d{2})(\\d{5})(\\d{4})/,'($1) $2-$3'):num;
    const hasQr=e.qr_url||e.qr_path;
    const extClass=e.extracted?'extracted':'';
    const extTag=e.extracted?'<span class="ext-tag">✅ Extraído</span>':'';
    const qrTag=hasQr?'<span class="qr-tag">QR Pronto</span>':'<span class="qr-tag no-qr">Aguardando QR</span>';
    const phone=num.startsWith('55')?num:'55'+num;
    let waTag='';
    if(WA_CACHE[phone]===true)waTag='<span class="wa-badge wa-yes">📱 WhatsApp</span>';
    else if(WA_CACHE[phone]===false)waTag='<span class="wa-badge wa-no">✕ Sem ZAP</span>';
    return `<div class="esim-card ${extClass}" id="card-${e.id}">
      <input type="checkbox" class="esim-check" data-id="${e.id}" onclick="event.stopPropagation();toggleSelect(${e.id})" ${SELECTED.has(e.id)?'checked':''}>
      <div class="num" onclick="openModal(${e.id})" style="cursor:pointer">${numFmt}</div>
      <div style="font-size:10px;color:#fff;margin-top:2px">${_fmtDate(e.created_at)}</div>
      <div class="meta">DDD ${num.substring(0,2)} | ${e.card_number||'?'} ${e.card_month||''}/${e.card_year||''} CVV:${e.card_cvv||''} | ${e.plano||'-'}</div>
      ${qrTag} ${extTag} ${waTag}
    </div>`
  }).join('')
}

function updateWaStats(){
  let ok=0,no=0,unk=0;
  ESIMS.forEach(e=>{
    const num=e.msisdn||'';
    const phone=num.startsWith('55')?num:'55'+num;
    if(WA_CACHE[phone]===true)ok++;
    else if(WA_CACHE[phone]===false)no++;
    else unk++;
  });
  const bar=document.getElementById('wa-stats');
  if(ok||no){bar.style.display='flex'}
  document.getElementById('wa-ok').textContent=ok;
  document.getElementById('wa-no').textContent=no;
  document.getElementById('wa-unk').textContent=unk;
}

let ENRICHING=false;
let BATCH_TS=0;
async function enrichWhatsApp(){
  if(ENRICHING)return;
  const toCheck=ESIMS.filter(e=>{
    const num=e.msisdn||'';
    const phone=num.startsWith('55')?num:'55'+num;
    return WA_CACHE[phone]===undefined;
  });
  if(!toCheck.length){toast('Todos já verificados!',true);return}
  ENRICHING=true;
  const btn=document.getElementById('btn-enrich');
  const prog=document.getElementById('enrich-progress');
  btn.disabled=true;prog.style.display='inline';
  let done=0,ok=0,nok=0;
  for(const e of toCheck){
    const num=e.msisdn||'';
    const phone=num.startsWith('55')?num:'55'+num;
    prog.textContent=`${done+1}/${toCheck.length} — ${phone}...`;
    try{
      const r=await fetch('/api/check_whatsapp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone})});
      const d=await r.json();
      if(d.has_whatsapp===true){WA_CACHE[phone]=true;ok++}
      else if(d.has_whatsapp===false){WA_CACHE[phone]=false;nok++}
      // Atualizar card inline
      const card=document.getElementById('card-'+e.id);
      if(card){
        const old=card.querySelector('.wa-badge');if(old)old.remove();
        const span=document.createElement('span');
        if(d.has_whatsapp===true){span.className='wa-badge wa-yes';span.textContent='📱 WhatsApp'}
        else if(d.has_whatsapp===false){span.className='wa-badge wa-no';span.textContent='✕ Sem ZAP'}
        card.appendChild(span);
      }
    }catch(err){}
    done++;
    updateWaStats();
    await new Promise(r=>setTimeout(r,600));
  }
  prog.textContent=`✅ ${ok} com ZAP | ❌ ${nok} sem | ${done} checados`;
  btn.disabled=false;ENRICHING=false;
}

function _copyText(t){const a=document.createElement('textarea');a.value=t;a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a)}
async function copyWaNumbers(){
  const nums=ESIMS.filter(e=>{const ph=(e.msisdn||'').startsWith('55')?(e.msisdn||''):'55'+(e.msisdn||'');return WA_CACHE[ph]===true}).map(e=>e.msisdn);
  if(!nums.length){toast('Nenhum número com ZAP',false);return}
  _copyText(nums.join('\\n'));
  toast('📋 '+nums.length+' números com ZAP copiados!',true);
}

function openModal(jobId){
  const e=ESIMS.find(x=>x.id===jobId);
  if(!e)return;
  MODAL_JOB=e;
  const num=e.msisdn||'?';
  const numFmt=num.length===11?num.replace(/(\\d{2})(\\d{5})(\\d{4})/,'($1) $2-$3'):num;
  document.getElementById('m-title').textContent=numFmt;
  const img=document.getElementById('m-qr');
  if(e.qr_path){img.src='/qr/'+e.id+'.png?'+Date.now();img.style.display='block'}
  else{img.style.display='none'}
  document.getElementById('m-info').innerHTML=`
    <span class="label">Numero:</span><span class="value">${num}</span>
    <span class="label">CPF:</span><span class="value">${e.cpf||'-'}</span>
    <span class="label">Senha:</span><span class="value">${e.senha||'-'}</span>
    <span class="label">Nome:</span><span class="value">${e.card_name||'-'}</span>
    <span class="label">Plano:</span><span class="value">${e.plano||'-'}</span>
    <span class="label">Order:</span><span class="value">${e.order_code||'-'}</span>
    <span class="label">Email:</span><span class="value">${e.email||'-'}</span>
    <span class="label">Card:</span><span class="value" style="font-weight:700;color:#fff">${e.card_number||'-'} ${e.card_month||''}/${e.card_year||''} CVV:${e.card_cvv||'-'}</span>
    <span class="label">QR URL:</span><span class="value" style="font-size:10px">${e.qr_url||'aguardando...'}</span>
  `;
  document.getElementById('modal-overlay').classList.add('open')
}

async function exportCards(){
  if(!ESIMS.length){toast('Nenhum eSIM',false);return}
  const cards=ESIMS.map(e=>`${e.card_number}|${e.card_month}|${e.card_year}|${e.card_cvv}|${e.card_name||'TITULAR'}`).join('\\n');
  _copyText(cards);
  toast('💳 '+ESIMS.length+' cards copiados!',true);
}

async function clearHistory(){
  if(!confirm('🗑️ LIMPAR HISTÓRICO - Deletar TODOS os eSIMs aprovados? Tem certeza?'))return;
  const r=await fetch('/api/esims/clear',{method:'POST'});
  const d=await r.json();
  toast(d.msg,d.ok);
  if(d.ok){loadEsims()}
}
function closeModal(){document.getElementById('modal-overlay').classList.remove('open');MODAL_JOB=null}

async function copyModalData(){
  if(!MODAL_JOB)return;
  const e=MODAL_JOB;
  const text=`Numero: ${e.msisdn||''}\\nCPF: ${e.cpf||''}\\nSenha: ${e.senha||''}\\nCC: ${e.card_number||''} ${e.card_month||''}/${e.card_year||''} CVV:${e.card_cvv||''}\\nNome: ${e.card_name||''}\\nQR URL: ${e.qr_url||''}\\nOrder: ${e.order_code||''}`;
  _copyText(text);
  toast('📋 Dados copiados!',true)
}

async function exportOne(){
  if(!MODAL_JOB)return;
  const wa_map={};
  const ph=(MODAL_JOB.msisdn||'').startsWith('55')?(MODAL_JOB.msisdn||''):'55'+(MODAL_JOB.msisdn||'');
  if(WA_CACHE[ph]===true)wa_map[String(MODAL_JOB.id)]=true;
  const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job_ids:[MODAL_JOB.id],wa_map:wa_map})});
  if(!r.ok){toast('Erro',false);return}
  const blob=await r.blob();
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='esim_'+MODAL_JOB.id+'.zip';a.click();
  toast('📦 ZIP baixado!',true);closeModal()
}

async function exportAll(){
  const ids=ESIMS.filter(e=>e.qr_url||e.qr_path).map(e=>e.id);
  if(!ids.length){toast('Nenhum eSIM com QR pronto',false);return}
  const wa_map={};
  ESIMS.forEach(e=>{const ph=(e.msisdn||'').startsWith('55')?(e.msisdn||''):'55'+(e.msisdn||'');if(WA_CACHE[ph]===true)wa_map[String(e.id)]=true});
  const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job_ids:ids,wa_map:wa_map})});
  if(!r.ok){toast('Erro',false);return}
  const blob=await r.blob();
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='esim_todos.zip';a.click();
  toast('📦 ZIP exportado!',true)
}

async function copyAll(){
  const lines=ESIMS.filter(e=>e.qr_url||e.qr_path).map(e=>
    `Numero: ${e.msisdn||''}\\nCPF: ${e.cpf||''}\\nSenha: ${e.senha||''}\\nCC: ${e.card_number||''} ${e.card_month||''}/${e.card_year||''} CVV:${e.card_cvv||''}\\nQR URL: ${e.qr_url||''}\\nOrder: ${e.order_code||''}\\n`
  );
  if(!lines.length){toast('Nenhum eSIM pronto',false);return}
  _copyText(lines.join('\\n'));
  toast('📋 '+lines.length+' eSIMs copiados!',true)
}

function toggleSelect(id){
  if(SELECTED.has(id))SELECTED.delete(id);else SELECTED.add(id);
  const card=document.getElementById('card-'+id);
  if(card)card.classList.toggle('selected',SELECTED.has(id));
  updateSelBar();
}
function selectAll(){
  ESIMS.filter(e=>true&&(e.qr_url||e.qr_path)).forEach(e=>{
    SELECTED.add(e.id);
    const card=document.getElementById('card-'+e.id);
    if(card){card.classList.add('selected');const cb=card.querySelector('.esim-check');if(cb)cb.checked=true}
  });
  updateSelBar();
}
function deselectAll(){
  SELECTED.clear();
  document.querySelectorAll('.esim-card.selected').forEach(c=>c.classList.remove('selected'));
  document.querySelectorAll('.esim-check:checked').forEach(cb=>cb.checked=false);
  updateSelBar();
}
function updateSelBar(){
  const bar=document.getElementById('sel-bar');
  const cnt=document.getElementById('sel-count');
  if(SELECTED.size>0){bar.classList.add('active');cnt.textContent=SELECTED.size+' selecionado'+(SELECTED.size>1?'s':'')}else{bar.classList.remove('active')}
}
async function exportSelected(){
  const ids=[...SELECTED];
  if(!ids.length){toast('Selecione eSIMs primeiro',false);return}
  const wa_map={};
  ids.forEach(id=>{const e=ESIMS.find(x=>x.id===id);if(e){const ph=(e.msisdn||'').startsWith('55')?(e.msisdn||''):'55'+(e.msisdn||'');if(WA_CACHE[ph]===true)wa_map[String(id)]=true}});
  const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:ids,wa_map:wa_map})});
  if(!r.ok){toast('Erro',false);return}
  const blob=await r.blob();
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='esim_selecionados.zip';a.click();
  toast(ids.length+' eSIMs exportados!',true)
}
async function copySelected(){
  const ids=[...SELECTED];
  if(!ids.length){toast('Selecione eSIMs primeiro',false);return}
  const lines=ids.map(id=>{const e=ESIMS.find(x=>x.id===id);if(!e)return '';return 'Numero: '+(e.msisdn||'')+'\\nCPF: '+(e.cpf||'')+'\\nSenha: '+(e.senha||'')+'\\nCC: '+(e.card_number||'')+' '+(e.card_month||'')+'/'+(e.card_year||'')+' CVV:'+(e.card_cvv||'')+'\\nQR URL: '+(e.qr_url||'')+'\\nOrder: '+(e.order_code||'')}).filter(Boolean);
  _copyText(lines.join('\\n\\n'));
  toast(lines.length+' eSIMs copiados!',true)
}
async function markSelectedExtracted(){
  const ids=[...SELECTED];
  if(!ids.length){toast('Selecione eSIMs primeiro',false);return}
  if(!confirm('Marcar '+ids.length+' eSIMs como extraidos?'))return;
  const r=await fetch('/api/esim/mark-extracted',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:ids})});
  const d=await r.json();
  if(d.ok){toast(d.count+' marcados como extraidos',true);SELECTED.clear();updateSelBar();loadEsims()}else{toast('Erro',false)}
}
async function markAllExtracted(){
  if(!confirm('Marcar TODOS os eSIMs com QR como extraídos?'))return;
  const r=await fetch('/api/esim/mark-extracted',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all:true})});
  const d=await r.json();
  if(d.ok){toast('✅ '+d.count+' marcados como extraídos',true);loadEsims()}else{toast('Erro',false)}
}

async function markOneExtracted(){
  if(!MODAL_JOB)return;
  const r=await fetch('/api/esim/mark-extracted',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:MODAL_JOB.id})});
  const d=await r.json();
  if(d.ok){toast('✅ Marcado como extraído',true);closeModal();loadEsims()}else{toast('Erro',false)}
}

// ── DDD Range Cards ─────────────────────────────
const DDD_RANGES = [
  {label:'11-19', min:11, max:19, region:'SP'},
  {label:'21-29', min:21, max:29, region:'RJ/ES'},
  {label:'31-39', min:31, max:39, region:'MG'},
  {label:'41-49', min:41, max:49, region:'PR/SC'},
  {label:'51-59', min:51, max:59, region:'RS/MS/MT'},
  {label:'61-69', min:61, max:69, region:'DF/GO/TO/AC/RO'},
  {label:'71-79', min:71, max:79, region:'BA/SE'},
  {label:'81-89', min:81, max:89, region:'PE/AL/PB/RN/CE/PI/MA'},
  {label:'91-99', min:91, max:99, region:'PA/AP/AM/RR'},
];
let CURRENT_DDD_RANGE = null;

function _fmtDate(ts){if(!ts)return '';const d=new Date(ts*1000);const dd=String(d.getDate()).padStart(2,'0');const mm=String(d.getMonth()+1).padStart(2,'0');const hh=String(d.getHours()).padStart(2,'0');const mi=String(d.getMinutes()).padStart(2,'0');return dd+'/'+mm+' '+hh+':'+mi;}
function _dddOf(e){ return parseInt((e.msisdn||'').substring(0,2)) || 0; }

function loadDddRanges(){
  const c=document.getElementById('ddd-ranges'); c.innerHTML='';
  DDD_RANGES.forEach(range=>{
    const items=ESIMS.filter(e=>{ const d=_dddOf(e); return d>=range.min && d<=range.max; });
    const zapCnt=items.filter(e=>{ const ph=(e.msisdn||'').startsWith('55')?(e.msisdn||''):'55'+(e.msisdn||''); return WA_CACHE[ph]===true; }).length;
    const card=document.createElement('div'); card.className='ddd-range-card';
    card.onclick=()=>openDddRangeModal(range);
    card.innerHTML=`<div class="range-label">DDD ${range.label}</div><div class="range-count">${items.length}</div><div class="range-sub">${range.region}</div>${zapCnt>0?`<div class="range-zap">📱 ${zapCnt} com ZAP</div>`:''}`;
    c.appendChild(card);
  });
}

function openDddRangeModal(range){
  CURRENT_DDD_RANGE=range;
  const items=ESIMS.filter(e=>{ const d=_dddOf(e); return d>=range.min && d<=range.max; });
  document.getElementById('ddd-modal-title').textContent=`DDD ${range.label} — ${range.region} (${items.length})`;
  document.getElementById('ddd-modal-count').textContent=`${items.length} números`;
  const tbody=document.getElementById('ddd-modal-body'); tbody.innerHTML='';
  items.forEach(e=>{
    const num=e.msisdn||'';
    const numFmt=num.length===11?num.replace(/(\\d{2})(\\d{5})(\\d{4})/,'($1) $2-$3'):num;
    const cpfFmt=e.cpf?e.cpf.replace(/(\\d{3})(\\d{3})(\\d{3})(\\d{2})/,'$1.$2.$3-$4'):'';
    const ph=num.startsWith('55')?num:'55'+num;
    let waIcon; if(WA_CACHE[ph]===true)waIcon='✅'; else if(WA_CACHE[ph]===false)waIcon='❌'; else waIcon='—';
    tbody.innerHTML+=`<tr><td>${num.substring(0,2)}</td><td>${numFmt}</td><td>${cpfFmt}</td><td>${e.senha||'-'}</td><td style="text-align:center">${waIcon}</td></tr>`;
  });
  document.getElementById('ddd-overlay').classList.add('open');
}

function closeDddModal(){ document.getElementById('ddd-overlay').classList.remove('open'); CURRENT_DDD_RANGE=null; }

function _getDddItems(){
  if(!CURRENT_DDD_RANGE)return [];
  return ESIMS.filter(e=>{ const d=_dddOf(e); return d>=CURRENT_DDD_RANGE.min && d<=CURRENT_DDD_RANGE.max; });
}

function copyDddNumbers(){
  const items=_getDddItems();
  _copyText(items.map(e=>e.msisdn).join('\\n'));
  toast('📋 '+items.length+' números copiados!',true);
}

function copyDddFull(){
  const items=_getDddItems();
  const lines=items.map(e=>`Numero: ${e.msisdn||''}\\nCPF: ${e.cpf||''}\\nSenha: ${e.senha||''}\\nCC: ${e.card_number||''} ${e.card_month||''}/${e.card_year||''} CVV:${e.card_cvv||''}\\nOrder: ${e.order_code||''}`);
  _copyText(lines.join('\\n---\\n'));
  toast('📋 '+items.length+' copiados (completo)!',true);
}

async function downloadDddZip(){
  const items=_getDddItems();
  const ids=items.filter(e=>e.qr_url||e.qr_path).map(e=>e.id);
  if(!ids.length){toast('Nenhum com QR code',false);return}
  const wa_map={};
  items.forEach(e=>{const ph=(e.msisdn||'').startsWith('55')?(e.msisdn||''):'55'+(e.msisdn||'');if(WA_CACHE[ph]===true)wa_map[String(e.id)]=true});
  try{
    const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:ids,wa_map:wa_map})});
    if(!r.ok){toast('Erro',false);return}
    const blob=await r.blob();
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download='esim_ddd_'+CURRENT_DDD_RANGE.label.replace('-','_')+'.zip';a.click();
    toast('📦 ZIP DDD '+CURRENT_DDD_RANGE.label+' baixado!',true);
  }catch(e){toast('Erro: '+e.message,false)}
}

document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();closeDddModal()}});

setInterval(()=>{loadEsims()},10000);loadEsims()
</script></body></html>
"""

TEMPLATE_RECOVERY = """
<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Recovery — Vivo Easy</title>
<style>
:root { --bg: #0a0a0f; --surface: #12121f; --surface2: #1a1a2e; --accent: #6c5ce7; --accent2: #a29bfe;
        --green: #00d26a; --red: #ff3b3b; --yellow: #ffd60a; --text: #eee; --dim: #888; --border: #2a2a3e; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh;
       display: flex; align-items: center; justify-content: center; }
.rec-box { max-width: 400px; width: 95%; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 28px; }
input { width: 100%; background: var(--surface2); border: 1px solid var(--border); color: var(--text);
        padding: 14px; border-radius: 10px; font-size: 16px; outline: none; text-align: center; letter-spacing: 2px;
        font-family: 'Consolas', monospace; }
input:focus { border-color: var(--accent); }
input::placeholder { letter-spacing: 0; font-family: 'Segoe UI', sans-serif; font-size: 13px; color: var(--dim); }
.btn { padding: 12px 24px; border-radius: 10px; border: none; font-size: 14px; font-weight: 600; cursor: pointer;
       transition: 0.2s; width: 100%; margin-top: 10px; }
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { background: #5a4bd6; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
.rec-result { margin-top: 16px; padding: 16px; background: var(--surface); border-radius: 12px; border: 1px solid var(--border); display: none; }
.rec-result.show { display: block; }
.otp-code { font-size: 42px; font-weight: 800; letter-spacing: 8px; text-align: center; color: var(--accent); margin: 16px 0;
            font-family: 'Consolas', monospace; }
.info-row { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.info-row:last-of-type { border: none; }
.label { color: var(--dim); }
.toast { position: fixed; top: 16px; right: 16px; padding: 12px 20px; border-radius: 8px; color: white; font-weight: 600;
         z-index: 999; animation: fadeIn 0.3s; }
.toast-ok { background: var(--green); } .toast-err { background: var(--red); }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; } }
</style>
</head><body>
<div class="rec-box">
  <div class="card">
    <h2 style="text-align:center;margin-bottom:4px">🔑 Recovery</h2>
    <p style="text-align:center;font-size:12px;color:var(--dim);margin-bottom:16px">Digite o número do eSIM para recuperar o código</p>
    <input id="rec-num" type="text" placeholder="DDD + Número" inputmode="numeric">
    <button class="btn btn-primary" onclick="buscarOTP()" id="rec-btn">🔍 Buscar Código</button>
    <div id="rec-msg" style="margin-top:10px;font-size:12px;text-align:center"></div>
  </div>
  <div class="rec-result" id="rec-result">
    <div style="text-align:center;font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:1px">Código de Recuperação</div>
    <div class="otp-code" id="rec-otp">-</div>
    <div class="info-row"><span class="label">Número</span><span id="rec-numero" style="font-family:Consolas,monospace">-</span></div>
    <div class="info-row"><span class="label">Senha eSIM</span><span id="rec-senha" style="font-family:Consolas,monospace">-</span></div>
    <button class="btn btn-secondary" onclick="buscarOTP()" style="margin-top:12px;font-size:13px">🔄 Atualizar OTP</button>
  </div>
</div>
<script>
function toast(m,ok){const d=document.createElement('div');d.className='toast '+(ok?'toast-ok':'toast-err');d.textContent=m;document.body.appendChild(d);setTimeout(()=>d.remove(),3000)}
async function buscarOTP(){
  const num=document.getElementById('rec-num').value.trim();
  if(!num||num.length<8){toast('Digite o número completo',false);return}
  const btn=document.getElementById('rec-btn');
  btn.disabled=true;btn.textContent='⏳ Consultando...';
  document.getElementById('rec-msg').innerHTML='<span style="color:var(--dim)">Buscando no inbox...</span>';
  try{
    const r=await fetch('/api/recovery',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({numero:num})});
    const d=await r.json();
    if(!d.ok){
      document.getElementById('rec-msg').innerHTML=`<span style="color:var(--red)">${d.msg}</span>`;
      document.getElementById('rec-result').classList.remove('show');
    }else{
      document.getElementById('rec-msg').innerHTML='';
      document.getElementById('rec-result').classList.add('show');
      document.getElementById('rec-numero').textContent=d.numero||'-';
      document.getElementById('rec-senha').textContent=d.senha||'-';
      if(d.otp){
        document.getElementById('rec-otp').textContent=d.otp;
        document.getElementById('rec-otp').style.color='var(--accent)';
      }else{
        document.getElementById('rec-otp').textContent='aguardando...';
        document.getElementById('rec-otp').style.color='var(--yellow)';
        document.getElementById('rec-msg').innerHTML='<span style="color:var(--yellow)">OTP não chegou ainda — clique Atualizar em alguns segundos</span>';
      }
    }
  }catch(e){document.getElementById('rec-msg').innerHTML='<span style="color:var(--red)">Erro de conexão</span>'}
  btn.disabled=false;btn.textContent='🔍 Buscar Código';
}
document.getElementById('rec-num').addEventListener('keydown',e=>{if(e.key==='Enter')buscarOTP()});
</script></body></html>
"""


# ══════════════════════════════════════════════════════════════════════════
#  ROTAS — Cliente (protegido por senha)
# ══════════════════════════════════════════════════════════════════════════

def _require_client():
    if not session.get("client") and not session.get("admin"):
        return False
    return True

@app.route("/login", methods=["GET", "POST"])
def client_login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == CLIENT_PASSWORD or pwd == ADMIN_PASSWORD:
            session["client"] = True
            if pwd == ADMIN_PASSWORD:
                session["admin"] = True
            return redirect(url_for("index"))
        return render_template_string(TEMPLATE_CLIENT_LOGIN, error="Senha incorreta")
    return render_template_string(TEMPLATE_CLIENT_LOGIN, error=None)

@app.route("/")
def index():
    if not _require_client():
        return redirect(url_for("client_login"))
    return render_template_string(TEMPLATE_CLIENT, ufs=UFS)

@app.route("/api/submit", methods=["POST"])
def api_submit():
    data = request.json or {}
    cards, errors = parse_cards(data.get("cards_text", ""))
    if not cards and errors:
        return jsonify({"ok": False, "msg": "Nenhum cartão válido", "errors": errors})
    if not cards:
        return jsonify({"ok": False, "msg": "Nenhum cartão válido"})
    uf = data.get("uf", "ALL")
    result = db.add_jobs(cards, uf=uf)
    count, batch_id = result if isinstance(result, tuple) else (result, 0)
    msg = f"✅ {count} cartões na fila"
    if errors:
        msg += f" ({len(errors)} linhas com erro)"
    return jsonify({"ok": True, "msg": msg, "errors": errors, "batch_id": batch_id})

@app.route("/api/validate_cards", methods=["POST"])
def api_validate_cards():
    """Valida cartões sem submeter — pra preview do drop/paste."""
    data = request.json or {}
    cards, errors = parse_cards(data.get("cards_text", ""))
    return jsonify({
        "valid": len(cards),
        "errors": errors,
        "preview": [f"****{c['number'][-4:]} {c['month']:02d}/{c['year']} CVV:{c['cvv']}" for c in cards[:20]],
    })

@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    data = request.json or {}
    mode = data.get("mode", "queued")
    if mode == "all":
        result = db.cancel_all_pending()
        # Limpa os cancelados e queued (remove CCs)
        db.purge_queued()
        db.purge_cancelled()
        return jsonify({"ok": True, "msg": f"🛑 {result['total']} cancelados e CCs removidos"})
    elif mode == "purge":
        q = db.purge_queued()
        c = db.purge_cancelled()
        f = db.purge_failed()
        return jsonify({"ok": True, "msg": f"🗑️ Removidos: {q} fila + {c} cancelados + {f} failed"})
    elif mode == "reset":
        result = db.purge_all()
        return jsonify({"ok": True, "msg": f"🔄 Reset completo: {result['total']:,} jobs deletados ({result['success']} success, {result['failed']} failed, {result['cancelled']} cancelled)"})
    else:
        count = db.cancel_queued()
        db.purge_queued()
        return jsonify({"ok": True, "msg": f"🛑 {count} jobs removidos da fila"})

@app.route("/api/worker/stop", methods=["POST"])
def api_worker_stop():
    """Para o worker setando flag no banco."""
    db.set_setting("worker_stop", "true")
    return jsonify({"ok": True, "msg": "🛑 Worker sinalizado para parar. Reinicie o container para aplicar."})

@app.route("/api/worker/start", methods=["POST"])
def api_worker_start():
    """Remove flag de parada do worker."""
    db.set_setting("worker_stop", "false")
    return jsonify({"ok": True, "msg": "▶️ Worker habilitado. Reinicie o container para aplicar."})

@app.route("/api/worker/status", methods=["GET"])
def api_worker_status():
    """Verifica status do worker."""
    stop_flag = db.get_setting("worker_stop", "false")
    return jsonify({"stopped": stop_flag == "true"})

@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())

@app.route("/api/jobs")
def api_jobs():
    status = request.args.get("status")
    exclude = request.args.get("exclude")
    limit = int(request.args.get("limit", 200))
    return jsonify(db.list_jobs(limit=limit, status_filter=status, exclude_status=exclude))

@app.route("/api/job/<int:job_id>")
def api_job_detail(job_id):
    job = db.get_job(job_id)
    return jsonify(job) if job else (jsonify({"error": "not found"}), 404)


# ── eSIMs ───────────────────────────────────────────────────────────────

@app.route("/esims")
def esims_page():
    if not _require_client():
        return redirect(url_for("client_login"))
    return render_template_string(TEMPLATE_ESIMS)



@app.route("/historico")
def historico_page():
    if not _require_client():
        return redirect(url_for("admin_login"))
    return render_template_string(TEMPLATE_HISTORICO)

@app.route("/api/batches")
def api_batches():
    return jsonify(db.list_batches(limit=50))

@app.route("/api/esims")
def api_esims():
    jobs = db.list_jobs(limit=500, status_filter="success")
    safe = []
    for j in jobs:
        safe.append({
            "id": j["id"], "msisdn": j.get("msisdn", ""),
            "cpf": j.get("cpf", ""), "email": j.get("email", ""),
            "senha": j.get("senha", ""), "plano": j.get("plano", ""),
            "order_code": j.get("order_code", ""),
            "card_number": j.get("card_number", ""),
            "card_name": j.get("card_name", ""), "card_month": j.get("card_month", ""), "card_year": j.get("card_year", ""), "card_cvv": j.get("card_cvv", ""),
            "qr_url": j.get("qr_url", ""), "qr_path": j.get("qr_path", ""),
            "extracted": j.get("extracted", 0),
            "whatsapp": j.get("whatsapp", None),
            "created_at": j["created_at"],
        })
    return jsonify(safe)

@app.route("/api/esims/clear", methods=["POST"])
def api_esims_clear():
    """Deleta todos os jobs success (limpa histórico de eSIMs)."""
    count = db.purge_success()
    return jsonify({"ok": True, "msg": f"🗑️ {count} eSIMs aprovados deletados"})

@app.route("/qr/<int:job_id>.png")
def serve_qr(job_id):
    job = db.get_job(job_id)
    if not job:
        abort(404)
    qr_path = job.get("qr_path", "")
    if qr_path and os.path.isfile(qr_path):
        return send_file(qr_path, mimetype="image/png")
    abort(404)

@app.route("/api/esim/mark-extracted", methods=["POST"])
def api_mark_extracted():
    data = request.json or {}
    job_id = data.get("job_id")
    job_ids = data.get("job_ids", [])
    mark_all = data.get("all", False)
    if mark_all:
        n = db.mark_all_extracted()
        return jsonify({"ok": True, "count": n})
    if job_ids:
        n = db.mark_extracted_bulk(job_ids)
        return jsonify({"ok": True, "count": n})
    if job_id:
        db.mark_extracted(job_id)
        return jsonify({"ok": True, "count": 1})
    return jsonify({"ok": False, "msg": "Informe job_id, job_ids ou all=true"}), 400

@app.route("/api/export", methods=["POST"])
def api_export():
    if not _require_client():
        return jsonify({"error": "unauthorized"}), 401
    data = request.json or {}
    job_ids = data.get("job_ids", [])
    wa_map = data.get("wa_map", {})
    if not job_ids:
        return jsonify({"error": "Nenhum job"}), 400

    buf = io.BytesIO()
    info_lines = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for jid in job_ids:
            job = db.get_job(jid)
            if not job or job["status"] != "success":
                continue
            num = job.get("msisdn", "")
            cpf = job.get("cpf", "")
            senha = job.get("senha", "")
            order = job.get("order_code", "")
            qr_url = job.get("qr_url", "")
            qr_path = job.get("qr_path", "")

            if qr_path and os.path.isfile(qr_path):
                has_zap = wa_map.get(str(jid), False)
                zap_prefix = "zap_" if has_zap else ""
                png_name = f"{zap_prefix}{num}_{senha}.png" if num else os.path.basename(qr_path)
                zf.write(qr_path, f"qrcodes/{png_name}")

            info_lines.append(f"QR url: {qr_url}")
            info_lines.append(f"Numero: {num}")
            info_lines.append(f"CPF: {cpf}")
            info_lines.append(f"Senha: {senha}")
            info_lines.append(f"Plano: {job.get('plano', '')}")
            info_lines.append(f"Order: {order}")
            info_lines.append(f"Email: {job.get('email', '')}")
            info_lines.append("")

        zf.writestr("_dados.txt", "\n".join(info_lines))

    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"esim_vivo_{ts}.zip")


# ── Recovery (público — sem auth) ────────────────────────────────────
@app.route("/recovery", methods=["GET"])
def recovery():
    return render_template_string(TEMPLATE_RECOVERY)

@app.route("/api/recovery", methods=["POST"])
def api_recovery():
    data = request.json or {}
    numero = data.get("numero", "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not numero or len(numero) < 8:
        return jsonify({"ok": False, "msg": "Informe o número completo"})

    # Busca job pelo número (últimos 8 dígitos) - TODOS os status
    jobs = db.list_jobs(limit=5000)
    job = None
    for j in jobs:
        msisdn = j.get("msisdn", "")
        if msisdn and msisdn.endswith(numero[-8:]):
            job = j
            break
    if not job:
        # Fallback: buscar nos eSIMs importados
        imp = db.buscar_esim_importado(numero)
        if imp:
            job = imp
    if not job:
        # Fallback: buscar nos eSIMs importados
        imp = db.buscar_esim_importado(numero)
        if imp:
            job = imp

    # Buscar tokens em TODOS os emails do pool (mesmo sem job)
    import hotmail_pool, re
    emails = db.list_emails()
    otp = None
    email_encontrado = None

    for email_info in emails:
        email = email_info.get("email", "")
        if not email:
            continue
        msgs = hotmail_pool.checar_inbox(email, timestamp=int(time.time()) - 3600)  # Última hora
        for m in msgs:
            subject = str(m.get("subject", "")).lower()
            sender = str(m.get("from", "")).lower()
            # Buscar mensagens da Vivo com código
            if "vivo" in sender or "vivo" in subject or "código" in subject or "validação" in subject or "recovery" in subject or "easy" in subject or "token" in subject:
                # Verificar se o número aparece no corpo
                body = m.get("body_text", "")
                if not body:
                    body = hotmail_pool.ler_mensagem(email, m.get("mid", ""))
                if body and numero[-8:] in body:
                    # Extrair código de 4-6 dígitos
                    text = re.sub(r'<[^>]+>', ' ', body)
                    code = re.search(r'\b(\d{4,6})\b', text)
                    if code:
                        otp = code.group(1)
                        email_encontrado = email
                        break
                # Também tentar no subject
                code = re.search(r'\b(\d{4,6})\b', subject)
                if code:
                    otp = code.group(1)
                    email_encontrado = email
                    break
        if otp:
            break

    if not otp:
        return jsonify({"ok": False, "msg": "Token não encontrado nos emails (última hora)"})

    # Retornar dados
    result = {
        "ok": True,
        "numero": numero,
        "otp": otp,
        "email": email_encontrado,
    }
    if job:
        result["cpf"] = job.get("cpf", "")
        result["senha"] = job.get("senha", "")
        result["plano"] = job.get("plano", "")
        result["order"] = job.get("order_code", "")
    else:
        result["cpf"] = ""
        result["senha"] = ""
        result["plano"] = ""
        result["order"] = ""

    return jsonify(result)


# ── WhatsApp Check (RapidAPI + cache no DB) ──────────────────────────
WA_URL = "https://whatsapp-number-validator3.p.rapidapi.com/WhatsappNumberHasItWithToken"
WA_HEADERS = {
    "x-rapidapi-key": "266997de2dmsh6224e5bdfaa1091p16dc66jsn2329b850a250",
    "x-rapidapi-host": "whatsapp-number-validator3.p.rapidapi.com",
    "Content-Type": "application/json",
}

def _wa_cache_load() -> dict:
    raw = db.get_setting("wa_cache", "{}")
    try:
        return json.loads(raw)
    except:
        return {}

def _wa_cache_save(cache: dict):
    db.set_setting("wa_cache", json.dumps(cache))

def _save_wa_to_jobs(phone: str, has_whatsapp: bool):
    """Save WhatsApp status to jobs table by msisdn."""
    num = re.sub(r"[^\d]", "", phone)
    if num.startswith("55") and len(num) > 11:
        num = num[2:]
    try:
        jobs = db.list_jobs(limit=2000, status_filter="success")
        for j in jobs:
            if j.get("msisdn", "") == num or j.get("msisdn", "").endswith(num[-8:]):
                db.update_job(j["id"], whatsapp=1 if has_whatsapp else 0)
                break
    except Exception:
        pass

def _check_wa_single(phone: str, cache: dict) -> dict:
    if phone in cache:
        return {"phone": phone, "has_whatsapp": cache[phone], "cached": True}
    for _ in range(3):
        try:
            r = http_requests.post(WA_URL, json={"phone_number": phone}, headers=WA_HEADERS, timeout=15)
            data = r.json()
            if "Rate limit" in data.get("response", "") or "retry_after_ms" in data:
                import time as _t
                _t.sleep(data.get("retry_after_ms", 1500) / 1000 + 0.3)
                continue
            has = data.get("has_whatsapp", data.get("numberExists", data.get("exists")))
            if has is True or data.get("status") == "valid":
                cache[phone] = True
                _save_wa_to_jobs(phone, True)
                return {"phone": phone, "has_whatsapp": True, "cached": False}
            elif has is False or data.get("status") == "invalid":
                cache[phone] = False
                _save_wa_to_jobs(phone, False)
                return {"phone": phone, "has_whatsapp": False, "cached": False}
            return {"phone": phone, "has_whatsapp": None, "cached": False}
        except:
            return {"phone": phone, "has_whatsapp": None, "cached": False}
    return {"phone": phone, "has_whatsapp": None, "cached": False}

@app.route("/api/check_whatsapp", methods=["POST"])
def api_check_whatsapp():
    if not _require_client():
        return jsonify({"error": "unauthorized"}), 401
    data = request.json or {}
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify({"error": "phone required"}), 400
    cache = _wa_cache_load()
    result = _check_wa_single(phone, cache)
    _wa_cache_save(cache)
    return jsonify(result)

@app.route("/api/whatsapp_numbers")
def api_whatsapp_numbers():
    """Lista números com WhatsApp confirmado (do cache)."""
    if not _require_client():
        return jsonify({"error": "unauthorized"}), 401
    cache = _wa_cache_load()
    jobs = db.list_jobs(limit=2000, status_filter="success")
    numbers = []
    for j in jobs:
        num = j.get("msisdn", "")
        if not num:
            continue
        phone = f"55{num}" if not num.startswith("55") else num
        if cache.get(phone) is True:
            numbers.append({
                "msisdn": num, "ddd": num[:2] if len(num) >= 2 else "?",
                "cpf": j.get("cpf", ""), "nome": j.get("card_name", ""),
            })
    return jsonify({"count": len(numbers), "numbers": numbers})


# ══════════════════════════════════════════════════════════════════════════
#  ROTAS — Admin
# ══════════════════════════════════════════════════════════════════════════

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_panel"))
        return render_template_string(TEMPLATE_LOGIN, error="Senha incorreta")
    return render_template_string(TEMPLATE_LOGIN, error=None)

@app.route("/admin")
def admin_panel():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template_string(TEMPLATE_ADMIN)

@app.route("/admin/api/emails", methods=["GET", "POST"])
def admin_emails():
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "POST":
        data = request.json or {}
        lines = data.get("lines", "").strip().splitlines()
        inserted, dupes = db.add_emails(lines)
        return jsonify({"ok": True, "msg": f"✅ {inserted} inseridos, {dupes} duplicados"})
    return jsonify(db.list_emails())

@app.route("/admin/api/emails/count")
def admin_emails_count():
    return jsonify(db.count_emails())

@app.route("/admin/api/emails/<int:eid>", methods=["DELETE"])
def admin_email_delete(eid):
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    db.remove_email(eid)
    return jsonify({"ok": True})

@app.route("/admin/api/cpfs/stats")
def admin_cpfs_stats():
    try:
        s = cpf_fila.stats()
        return jsonify({"available": s.get("disponivel", 0), "used": s.get("usado", 0),
                         "total": sum(s.values())})
    except:
        return jsonify({"available": "?", "used": "?", "total": "?"})


# ── Worker Threads (admin) ──────────────────────────────────────────────

@app.route("/admin/api/workers", methods=["GET", "POST"])
def admin_workers():
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "POST":
        data = request.json or {}
        try:
            n = int(data.get("threads", 0))
        except (ValueError, TypeError):
            return jsonify({"ok": False, "msg": "Valor inválido"}), 400
        if n < 1 or n > 50:
            return jsonify({"ok": False, "msg": "Valor deve ser entre 1 e 50"}), 400
        db.set_setting("worker_threads", str(n))
        return jsonify({"ok": True, "msg": f"✅ Worker threads alterado para {n}. Reinicie o worker para aplicar.", "threads": n})
    # GET
    current = db.get_setting("worker_threads", str(WORKER_THREADS))
    return jsonify({"threads": int(current)})


# ── Proxy Config ───────────────────────────────────────────────────────────

@app.route("/api/config/proxy", methods=["GET", "POST"])
def config_proxy():
    proxy_file = os.path.join(os.path.dirname(__file__), "data", "proxy_config.txt")
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if request.method == "POST":
        if not session.get("admin"):
            return jsonify({"error": "unauthorized"}), 401
        data = request.json or {}
        proxy = data.get("proxy", "").strip()
        try:
            # Salvar no arquivo proxy_config.txt
            with open(proxy_file, "w") as f:
                f.write(proxy)
            
            # Atualizar .env
            parts = proxy.split(":")
            if len(parts) >= 4:
                host, port, user, password = parts[0], parts[1], parts[2], parts[3]
                with open(env_file, "w") as f:
                    f.write(f"PROXY_HOST={host}\n")
                    f.write(f"PROXY_PORT={port}\n")
                    f.write(f"PROXY_USER={user}\n")
                    f.write(f"PROXY_PASS={password}\n")
            
            return jsonify({"ok": True, "msg": "✅ Proxy salvo. Reinicie o container para aplicar."})
        except Exception as e:
            return jsonify({"ok": False, "msg": f"Erro: {str(e)}"}), 500
    # GET
    try:
        if os.path.exists(proxy_file):
            with open(proxy_file, "r") as f:
                proxy = f.read().strip()
        else:
            proxy = ""
    except:
        proxy = ""
    return jsonify({"proxy": proxy})


# ── 2Captcha Key ────────────────────────────────────────────────────────

@app.route("/api/captcha_key", methods=["GET", "POST"])
def client_captcha_key():
    if not _require_client():
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "POST":
        data = request.json or {}
        key = data.get("key", "").strip()
        if not key:
            return jsonify({"ok": False, "msg": "Key vazia"}), 400
        # Testa a key consultando saldo
        try:
            import requests as req
            r = req.get("http://2captcha.com/res.php", params={
                "key": key, "action": "getbalance", "json": 1,
            }, timeout=10)
            d = r.json()
            if d.get("status") != 1:
                return jsonify({"ok": False, "msg": f"Key inválida: {d.get('request', '?')}"})
            balance = float(d["request"])
            db.set_setting("2captcha_key", key)
            return jsonify({"ok": True, "msg": f"Key salva! Saldo: ${balance:.2f}", "balance": balance})
        except Exception as e:
            return jsonify({"ok": False, "msg": f"Erro: {str(e)[:100]}"})
    # GET
    key = db.get_setting("2captcha_key", "")
    masked = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else ("configurada" if key else "não configurada")
    balance = None
    if key:
        try:
            import requests as req
            r = req.get("http://2captcha.com/res.php", params={
                "key": key, "action": "getbalance", "json": 1,
            }, timeout=10)
            d = r.json()
            if d.get("status") == 1:
                balance = float(d["request"])
        except:
            pass
    return jsonify({"key_status": masked, "balance": balance})


# ── Métricas ────────────────────────────────────────────────────────────

@app.route("/api/metrics")
def api_metrics():
    metrics = db.get_today_metrics()
    captcha = db.get_today_captcha_spend()
    # Saldo atual
    key = db.get_setting("2captcha_key", "")
    balance = None
    if key:
        try:
            import requests as req
            r = req.get("http://2captcha.com/res.php", params={
                "key": key, "action": "getbalance", "json": 1,
            }, timeout=10)
            d = r.json()
            if d.get("status") == 1:
                balance = float(d["request"])
        except:
            pass
    proxy_bytes = db.get_today_proxy_bytes()
    return jsonify({
        **metrics,
        "captcha_balance": balance,
        "captcha_spent_today": captcha["spent"],
        "captcha_solves_today": captcha["checks"],
        "proxy_bytes_today": proxy_bytes,
        "proxy_mb_today": round(proxy_bytes / (1024*1024), 1),
    })


# ══════════════════════════════════════════════════════════════════════════
#  Reset Senha Batch
# ══════════════════════════════════════════════════════════════════════════

VIVO_BASE = "https://easy.vivo.com.br"
_PBKDF2_SALT = b"a,0,2,b,3,q,1,l,o,4,7,8,c,9,01,02"


def _gerar_senha(length=6):
    """Gera senha sem repetir char (case-insensitive). Garante 1 upper + 1 lower + 1 digit."""
    letters = list("abcdefghijkmnpqrstuvwxyz")  # sem l, o
    digits = list("23456789")  # sem 0, 1
    while True:
        pool = [random.choice([c, c.upper()]) for c in letters] + digits[:]
        random.shuffle(pool)
        senha = "".join(pool[:length])
        if any(c.isupper() for c in senha) and any(c.islower() for c in senha) and any(c.isdigit() for c in senha):
            return senha


def _pbkdf2_hash(plain: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha1", plain.encode("utf-8"), _PBKDF2_SALT, 1000, dklen=64)
    return dk.hex()


def _buscar_email_por_msisdn(msisdn: str) -> dict:
    """Busca job success pelo msisdn (últimos 8 dígitos). Busca em jobs E esim_imported."""
    jobs = db.list_jobs(limit=2000, status_filter="success")
    for j in jobs:
        m = j.get("msisdn", "")
        if m and m.endswith(msisdn[-8:]):
            return j
    # Fallback: buscar nos eSIMs importados do local
    imp = db.buscar_esim_importado(msisdn)
    if imp:
        return imp
    return {}


def _extrair_codigo_reset(text: str) -> str:
    m = re.search(r'c[oó]digo[^\d]{0,20}(\d{4,6})\b', text, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r'\b(\d{4,6})[^\d]{0,20}c[oó]digo', text, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r'(?:token|valida[çc][aã]o|senha)[^\d]{0,20}(\d{4,6})\b', text, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r'\b(\d{6})\b', text)
    if m: return m.group(1)
    return ""


def _aguardar_token_reset(email: str, pre_mids: set, timeout=90) -> str:
    """Poll inbox até achar email novo de reset com código."""
    import hotmail_pool
    start = time.time()
    seen = set(pre_mids)
    while time.time() - start < timeout:
        time.sleep(6)
        try:
            msgs = hotmail_pool.checar_inbox(email)
            for m in msgs:
                mid = m.get("mid", "")
                if mid in seen:
                    continue
                subject = str(m.get("subject", "")).lower()
                sender = str(m.get("from", "")).lower()
                preview = m.get("body_text", "")
                is_vivo = "vivo" in sender or "vivo" in subject or "easy" in subject
                has_kw = any(k in subject for k in ("senha", "password", "código", "codigo",
                             "recuper", "token", "validação", "validacao", "reset", "autenticação"))
                if not (is_vivo or has_kw):
                    seen.add(mid)
                    continue
                code = _extrair_codigo_reset(m.get("subject", ""))
                if code:
                    return code
                code = _extrair_codigo_reset(preview)
                if code:
                    return code
                body = hotmail_pool.ler_mensagem(email, mid)
                if body:
                    text = re.sub(r'<style[^>]*>.*?</style>', ' ', body, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = html_mod.unescape(text)
                    text = re.sub(r'\s+', ' ', text)
                    code = _extrair_codigo_reset(text)
                    if code:
                        return code
                seen.add(mid)
        except Exception:
            pass
    return ""


_RESET_MAX_RETRIES = 3

def _resetar_senha_numero(msisdn: str) -> dict:
    num = re.sub(r"[^\d]", "", msisdn)
    if len(num) < 10:
        return {"ok": False, "msisdn": num, "error": "Número inválido"}
    job = _buscar_email_por_msisdn(num)
    if not job:
        return {"ok": False, "msisdn": num, "error": "Número não encontrado no DB"}
    email = job.get("email", "")
    if not email:
        return {"ok": False, "msisdn": num, "error": "Sem email associado"}

    last_error = ""
    for attempt in range(1, _RESET_MAX_RETRIES + 1):
        try:
            result = _resetar_senha_attempt(num, email)
            if result["ok"]:
                return result
            last_error = result.get("error", "")
            if not any(k in last_error.lower() for k in ("timeout", "proxy", "connection", "cf:", "cloudflare", "reset by peer", "broken pipe")):
                return result
            if attempt < _RESET_MAX_RETRIES:
                time.sleep(3)
        except Exception as e:
            last_error = str(e)
            if attempt < _RESET_MAX_RETRIES:
                time.sleep(3)
    return {"ok": False, "msisdn": num, "error": f"Falhou após {_RESET_MAX_RETRIES} tentativas: {last_error}"}


def _resetar_senha_attempt(num: str, email: str) -> dict:
    import urllib3, hotmail_pool
    urllib3.disable_warnings()
    sess = http_requests.Session()
    sess.verify = False
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "*/*",
        "Origin": VIVO_BASE,
        "x-organization-slug": "vivo",
        "Referer": f"{VIVO_BASE}/alta/ativacao",
        "Content-Type": "application/json",
    })
    try:
        r = sess.get(f"{VIVO_BASE}/alta/ativacao", timeout=20)
        if r.status_code == 403:
            return {"ok": False, "msisdn": num, "error": "Cloudflare bloqueou"}
    except Exception as e:
        return {"ok": False, "msisdn": num, "error": f"CF: {e}"}

    # Inbox ANTES
    msgs = hotmail_pool.checar_inbox(email)
    pre_mids = {m.get("mid", "") for m in msgs}

    # Enviar token
    try:
        r = sess.post(f"{VIVO_BASE}/bff/clients/token/password/v2",
                      json={"msisdn": num, "type": "EMAIL"}, timeout=15)
        if r.status_code not in (200, 201, 204):
            return {"ok": False, "msisdn": num, "error": f"Envio token falhou ({r.status_code}): {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "msisdn": num, "error": f"Envio token: {e}"}

    # Aguardar token no email
    token = _aguardar_token_reset(email, pre_mids, timeout=90)
    if not token:
        return {"ok": False, "msisdn": num, "error": "Token não chegou no email (timeout 90s)"}

    # Gerar nova senha e trocar
    nova_senha = _gerar_senha(6)
    nova_hash = _pbkdf2_hash(nova_senha)
    try:
        r = sess.post(f"{VIVO_BASE}/bff/clients/password/recovery",
                      json={"token": token, "password": nova_hash, "msisdn": num}, timeout=15)
        if r.status_code in (200, 201, 204):
            # Atualizar senha no DB do Docker (tabela jobs)
            job = _buscar_email_por_msisdn(num)
            if job:
                db.update_job(job["id"], senha=nova_senha)
            return {"ok": True, "msisdn": num, "senha": nova_senha}
        else:
            return {"ok": False, "msisdn": num, "error": f"Recovery falhou ({r.status_code}): {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "msisdn": num, "error": f"Recovery: {e}"}


RESET_SENHA_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reset Senha Batch</title>
<style>
  :root { --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a; --text: #e4e4e7;
          --accent: #6366f1; --green: #22c55e; --red: #ef4444; --dim: #71717a; --yellow: #eab308; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text);
         display: flex; justify-content: center; padding: 40px 16px; }
  .container { background: var(--card); border: 1px solid var(--border); border-radius: 16px;
               padding: 32px; max-width: 640px; width: 100%; }
  h1 { font-size: 20px; color: var(--accent); margin-bottom: 6px; }
  .sub { font-size: 13px; color: var(--dim); margin-bottom: 20px; }
  .nav { font-size: 13px; margin-bottom: 16px; }
  .nav a { color: var(--accent); text-decoration: none; }
  textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text);
             padding: 12px; border-radius: 10px; font-size: 14px; font-family: 'Cascadia Code', monospace;
             resize: vertical; min-height: 120px; outline: none; }
  textarea:focus { border-color: var(--accent); }
  .btn { width: 100%; padding: 12px; border-radius: 10px; border: none; font-size: 14px; font-weight: 700;
         cursor: pointer; background: var(--accent); color: white; margin-top: 12px; transition: all .15s; }
  .btn:hover { background: #4f46e5; }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .status { margin-top: 16px; font-size: 13px; color: var(--dim); }
  .results { margin-top: 20px; }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px;
         border-radius: 8px; margin-bottom: 6px; font-size: 13px; font-family: 'Cascadia Code', monospace; }
  .row.ok { background: rgba(34,197,94,.1); border: 1px solid var(--green); }
  .row.err { background: rgba(239,68,68,.08); border: 1px solid rgba(239,68,68,.3); }
  .row .num { color: var(--text); font-weight: 600; }
  .row .senha { color: var(--green); font-weight: 700; font-size: 15px; letter-spacing: 1px; }
  .row .erro { color: var(--red); font-size: 12px; }
  .row.processing { background: rgba(99,102,241,.08); border: 1px solid rgba(99,102,241,.3); }
  .row .waiting { color: var(--yellow); font-size: 12px; }
  .summary { margin-top: 16px; padding: 12px; border-radius: 10px; background: var(--bg);
             border: 1px solid var(--border); font-size: 13px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .copy-all-box { margin-top: 16px; position: relative; }
  .copy-all-box textarea { min-height: 80px; font-size: 13px; color: var(--green); background: var(--bg);
                           border: 1px solid var(--border); }
  .copy-float { position: absolute; top: 8px; right: 8px; background: var(--accent); color: white;
                border: none; padding: 6px 14px; border-radius: 8px; font-size: 12px; font-weight: 700;
                cursor: pointer; z-index: 2; }
  .copy-float:hover { background: #4f46e5; }
  .copy-float.copied { background: var(--green); }
  .login-box { text-align: center; }
  .login-box input { background: var(--bg); border: 1px solid var(--border); color: var(--text);
                     padding: 12px 16px; border-radius: 10px; font-size: 15px; width: 100%;
                     font-family: 'Cascadia Code', monospace; outline: none; text-align: center; }
  .login-box input:focus { border-color: var(--accent); }
  .login-err { color: var(--red); font-size: 12px; margin-top: 8px; display: none; }
  .hidden { display: none; }
</style>
</head>
<body>
<div class="container">
  <div id="login-view" class="login-box">
    <h1>🔐 Reset Senha</h1>
    <div class="sub">Digite a senha de acesso</div>
    <input type="password" id="login-pass" placeholder="Senha" onkeydown="if(event.key==='Enter')doLogin()">
    <button class="btn" onclick="doLogin()">Entrar</button>
    <div class="login-err" id="login-err">Senha incorreta</div>
  </div>
  <div id="main-view" class="hidden">
    <div class="nav"><a href="/esims">← eSIMs</a> <span style="color:var(--dim)">|</span> <a href="/">Dashboard</a></div>
    <h1>🔐 Reset Senha Batch</h1>
    <div class="sub">Cole os numeros (1 por linha). Senha de 6 chars sera gerada. Processa 5 em paralelo.</div>
    <textarea id="nums" placeholder="11987654321&#10;85989783066&#10;33998548797"></textarea>
    <button class="btn" id="btn" onclick="processar()">Processar</button>
    <div class="status" id="status"></div>
    <div class="results" id="results"></div>
    <div class="summary" id="summary" style="display:none"></div>
    <div class="copy-all-box" id="copy-box" style="display:none">
      <button class="copy-float" id="copy-float-btn" onclick="copiarTudo()">Copiar tudo</button>
      <textarea id="copy-area" readonly></textarea>
    </div>
  </div>
</div>
<script>
let _token = '';
async function doLogin() {
  const pass = document.getElementById('login-pass').value;
  const r = await fetch('/api/reset_senha_auth', { method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({password: pass}) });
  const d = await r.json();
  if (d.ok) {
    _token = pass;
    document.getElementById('login-view').classList.add('hidden');
    document.getElementById('main-view').classList.remove('hidden');
  } else {
    document.getElementById('login-err').style.display = 'block';
  }
}
let _ok = 0, _fail = 0, _total = 0, _sucessos = [];
function _updateSummary() {
  const summary = document.getElementById('summary');
  const copyBox = document.getElementById('copy-box');
  summary.style.display = 'flex';
  summary.innerHTML = `<span>✅ ${_ok} sucesso</span><span>❌ ${_fail} falhas</span><span style="color:var(--dim)">${_ok+_fail}/${_total}</span>`;
  if (_sucessos.length) {
    copyBox.style.display = 'block';
    document.getElementById('copy-area').value = _sucessos.join('\n');
  }
}
function _removeFromFila(msisdn) {
  const ta = document.getElementById('nums');
  const lines = ta.value.split('\n').filter(l => l.replace(/\D/g, '') !== msisdn);
  ta.value = lines.join('\n').trim();
}
async function _processOne(msisdn) {
  try {
    const r = await fetch('/api/reset_senha_single', { method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({msisdn, password: _token}) });
    const item = await r.json();
    const row = document.getElementById('row-' + msisdn);
    if (!row) return;
    if (item.ok) {
      _ok++;
      row.className = 'row ok';
      row.innerHTML = `<span class="num">${item.msisdn}</span><span class="senha">${item.senha}</span>`;
      _sucessos.push(`${item.msisdn}: ${item.senha}`);
      _removeFromFila(item.msisdn);
    } else {
      _fail++;
      row.className = 'row err';
      row.innerHTML = `<span class="num">${item.msisdn}</span><span class="erro">${item.error}</span>`;
    }
    _updateSummary();
  } catch(e) {
    _fail++;
    const row = document.getElementById('row-' + msisdn);
    if (row) { row.className = 'row err'; row.innerHTML = `<span class="num">${msisdn}</span><span class="erro">Erro: ${e.message}</span>`; }
    _updateSummary();
  }
}
async function processar() {
  const raw = document.getElementById('nums').value.trim();
  if (!raw) return;
  const numeros = [...new Set(raw.split('\n').map(l => l.replace(/\D/g, '')).filter(n => n.length >= 10))];
  if (!numeros.length) { alert('Nenhum numero valido'); return; }
  const btn = document.getElementById('btn');
  const status = document.getElementById('status');
  const results = document.getElementById('results');
  btn.disabled = true;
  results.innerHTML = '';
  document.getElementById('summary').style.display = 'none';
  document.getElementById('copy-box').style.display = 'none';
  _ok = 0; _fail = 0; _total = numeros.length; _sucessos = [];
  numeros.forEach(n => {
    const row = document.createElement('div');
    row.className = 'row processing';
    row.id = 'row-' + n;
    row.innerHTML = `<span class="num">${n}</span><span class="waiting">⏳ fila...</span>`;
    results.appendChild(row);
  });
  status.textContent = `Processando ${numeros.length} numeros (5 paralelos)...`;
  const queue = [...numeros];
  const CONCURRENT = 5;
  async function worker() {
    while (queue.length) {
      const n = queue.shift();
      const row = document.getElementById('row-' + n);
      if (row) row.querySelector('.waiting').textContent = '⏳ processando...';
      await _processOne(n);
    }
  }
  await Promise.all(Array.from({length: Math.min(CONCURRENT, numeros.length)}, () => worker()));
  status.textContent = `Concluido: ${_ok} sucesso, ${_fail} falhas`;
  btn.disabled = false;
}
function copiarTudo() {
  const area = document.getElementById('copy-area');
  navigator.clipboard.writeText(area.value).then(() => {
    const btn = document.getElementById('copy-float-btn');
    btn.textContent = 'Copiado!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copiar tudo'; btn.classList.remove('copied'); }, 2000);
  });
}
</script>
</body>
</html>"""


@app.route("/reset-senha")
def reset_senha_page():
    return render_template_string(RESET_SENHA_HTML)


@app.route("/api/reset_senha_auth", methods=["POST"])
def api_reset_senha_auth():
    data = request.get_json()
    if data.get("password") == RESET_SENHA_PASS:
        return jsonify({"ok": True})
    return jsonify({"ok": False})


@app.route("/api/reset_senha_single", methods=["POST"])
def api_reset_senha_single():
    data = request.get_json()
    if data.get("password") != RESET_SENHA_PASS:
        return jsonify({"error": "Senha incorreta"}), 403
    msisdn = data.get("msisdn", "")
    if not msisdn:
        return jsonify({"ok": False, "msisdn": "", "error": "Sem número"}), 400
    try:
        result = _resetar_senha_numero(msisdn)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "msisdn": msisdn, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# ATIVAÇÃO eSIM — Fluxo completo via API Vivo
# ══════════════════════════════════════════════════════════════════════════

_SYNC_ID = ""
_SYNC_ID_FILE = os.path.join(os.path.dirname(__file__), "sync_id_easy.txt")
if os.path.exists(_SYNC_ID_FILE):
    _SYNC_ID = open(_SYNC_ID_FILE).read().strip()


def _ativar_esim_flow(msisdn: str, senha_plain: str) -> dict:
    """Fluxo completo de ativação eSIM (7 steps)."""
    import urllib3
    urllib3.disable_warnings()

    num = re.sub(r"[^\d]", "", msisdn)
    if len(num) < 10:
        return {"ok": False, "error": "Número inválido", "steps": []}

    steps_log = []
    nova_senha = None
    senha_hash = _pbkdf2_hash(senha_plain)
    device_id = str(uuid.uuid4())
    wifi_mac = ":".join(f"{random.randint(0,255):02X}" for _ in range(6))

    sess = http_requests.Session()
    sess.verify = False
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Origin": VIVO_BASE,
        "x-organization-slug": "vivo",
        "Referer": f"{VIVO_BASE}/alta/ativacao",
        "Content-Type": "application/json",
        "x-device-uuid": device_id,
        "x-app-os": "WEB",
        "x-device-wifiMacAddress": wifi_mac,
    })

    def _update_token(resp):
        t = resp.headers.get("x-access-token", "")
        if t:
            sess.headers["Authorization"] = f"Bearer {t}"

    def _fail(msg):
        """Retorno de erro limpo."""
        ret = {"ok": False, "error": msg}
        if nova_senha:
            ret["nova_senha"] = nova_senha
        return ret

    # Step 0: CF cookies
    try:
        for attempt in range(5):
            r = sess.get(f"{VIVO_BASE}/alta/ativacao", timeout=25)
            if r.status_code != 403:
                break
            time.sleep(2 + attempt * 2)
        if r.status_code == 403:
            return _fail("Servidor indisponivel, tente novamente")
        sess.get(f"{VIVO_BASE}/_/feature-flags/status", timeout=10)
        sess.get(f"{VIVO_BASE}/_/maintenance", timeout=10)
        sess.get(f"{VIVO_BASE}/_/adherence/device/status", timeout=10)
    except Exception:
        return _fail("Erro de conexao, tente novamente")

    # Step 1: Authenticate (auto-reset se senha errada)
    try:
        r = sess.post(f"{VIVO_BASE}/bff/activation/authenticate",
                      json={"deviceId": device_id, "msisdn": num, "password": senha_hash}, timeout=15)
        _update_token(r)
        if r.status_code != 200:
            reset = _resetar_senha_numero(num)
            if not reset.get("ok"):
                return _fail("Senha incorreta e nao foi possivel resetar")
            nova_senha = reset["senha"]
            senha_hash = _pbkdf2_hash(nova_senha)
            r = sess.post(f"{VIVO_BASE}/bff/activation/authenticate",
                          json={"deviceId": device_id, "msisdn": num, "password": senha_hash}, timeout=15)
            _update_token(r)
            if r.status_code != 200:
                return _fail("Nao foi possivel autenticar mesmo apos reset")
        auth_data = r.json()
        if auth_data.get("status") != "PENDING_ACTIVATE":
            return _fail("ATIVA ANTES NO APARELHO PORRA!!!")
    except Exception:
        return _fail("Erro na autenticacao, tente novamente")

    # Step 2: Validate
    try:
        hdrs = {"x-channel": "DESKTOP_BROWSER"}
        if _SYNC_ID:
            hdrs["x-sync-id"] = _SYNC_ID
        r = sess.post(f"{VIVO_BASE}/bff/activation/validate", json={}, timeout=15, headers=hdrs)
        _update_token(r)
        if r.status_code not in (200, 204):
            return _fail("Erro na validacao")
    except Exception:
        return _fail("Erro na validacao")

    # Step 3: Validate eSIM
    try:
        r = sess.post(f"{VIVO_BASE}/bff/activation/validate/esim", timeout=15)
        _update_token(r)
        if r.status_code not in (200, 204):
            try:
                err_data = r.json()
                title = err_data.get("title", "").strip()
                if title:
                    return _fail(title)
            except Exception:
                pass
            return _fail("Erro na validacao do eSIM")
    except Exception:
        return _fail("Erro na validacao do eSIM")

    # Step 4: Send activation token
    try:
        r = sess.post(f"{VIVO_BASE}/bff/activate/token/send",
                      json={"tokenType": "NEW_MSISDN_ACTIVATION"}, timeout=15)
        _update_token(r)
        if r.status_code not in (200, 201, 204):
            return _fail("Erro ao enviar token de ativacao")
    except Exception:
        return _fail("Erro ao enviar token de ativacao")

    # Step 5: Get purchase info
    purchase_info = {}
    try:
        r = sess.get(f"{VIVO_BASE}/bff/adherence/new-msisdn/purchase", timeout=15)
        _update_token(r)
        if r.status_code == 200:
            purchase_info = r.json()
        else:
            return _fail("Erro ao obter dados da ativacao")
    except Exception:
        return _fail("Erro ao obter dados da ativacao")

    # Step 6: Confirm purchase
    try:
        r = sess.post(f"{VIVO_BASE}/bff/adherence/new-msisdn/purchase/confirm", timeout=15)
        _update_token(r)
        if r.status_code not in (200, 204):
            return _fail("Erro ao confirmar ativacao")
    except Exception:
        return _fail("Erro ao confirmar ativacao")

    # Step 7: Poll status
    final_status = {}
    try:
        for _ in range(10):
            time.sleep(3)
            r = sess.get(f"{VIVO_BASE}/bff/adherence/new-msisdn/status", timeout=15)
            _update_token(r)
            if r.status_code == 200:
                final_status = r.json()
                st = final_status.get("status", "")
                if st == "ACTIVE":
                    ret = {"ok": True, "message": "eSIM Ativado!"}
                    if nova_senha:
                        ret["nova_senha"] = nova_senha
                    return ret
                if st in ("FAILED", "ERROR", "CANCELLED"):
                    return _fail("Ativacao falhou, tente novamente")
    except Exception:
        return _fail("Erro ao verificar status")

    return _fail("Tempo esgotado, tente novamente")


ATIVAR_ESIM_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ativar eSIM</title>
<style>
  :root { --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a; --text: #e4e4e7;
          --accent: #8b5cf6; --green: #22c55e; --red: #ef4444; --dim: #71717a; --yellow: #eab308; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text);
         display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 20px;
          padding: 36px 32px; max-width: 420px; width: 100%; }
  .logo { text-align: center; font-size: 40px; margin-bottom: 8px; }
  h1 { text-align: center; font-size: 22px; color: var(--accent); margin-bottom: 4px; }
  .sub { text-align: center; font-size: 13px; color: var(--dim); margin-bottom: 28px; }
  label { display: block; font-size: 12px; color: var(--dim); margin-bottom: 6px; margin-top: 16px;
          text-transform: uppercase; letter-spacing: .5px; }
  input { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text);
          padding: 14px 16px; border-radius: 12px; font-size: 16px; font-family: 'Cascadia Code', monospace;
          outline: none; text-align: center; letter-spacing: 2px; }
  input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(139,92,246,.15); }
  input::placeholder { color: var(--dim); letter-spacing: 0; font-size: 14px; }
  .btn { width: 100%; padding: 14px; border-radius: 12px; border: none; font-size: 15px; font-weight: 700;
         cursor: pointer; background: linear-gradient(135deg, #8b5cf6, #6d28d9); color: white;
         margin-top: 24px; transition: all .2s; }
  .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(139,92,246,.3); }
  .btn:disabled { opacity: .4; cursor: not-allowed; transform: none; box-shadow: none; }
  .steps { margin-top: 20px; }
  .step { display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin-bottom: 4px;
          border-radius: 8px; font-size: 13px; font-family: 'Cascadia Code', monospace; }
  .step.ok { color: var(--green); }
  .step.err { color: var(--red); }
  .step.wait { color: var(--yellow); }
  .step .icon { width: 20px; text-align: center; flex-shrink: 0; }
  .result { margin-top: 20px; padding: 20px; border-radius: 14px; text-align: center; }
  .result.success { background: rgba(34,197,94,.08); border: 1px solid var(--green); }
  .result.error { background: rgba(239,68,68,.06); border: 1px solid rgba(239,68,68,.3); }
  .result h2 { font-size: 18px; margin-bottom: 6px; }
  .result .msg { font-size: 13px; color: var(--dim); }
  .result.success h2 { color: var(--green); }
  .result.error h2 { color: var(--red); }
  .err-text { color: var(--red); font-size: 12px; text-align: center; margin-top: 10px; display: none; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--dim);
             border-top-color: var(--accent); border-radius: 50%; animation: spin .6s linear infinite; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">📱</div>
  <h1>Ativar eSIM</h1>
  <div class="sub">Insira o numero e senha para ativar</div>
  <div id="form-view">
    <label>Numero (MSISDN)</label>
    <input type="tel" id="inp-msisdn" placeholder="11987654321" maxlength="13"
           onkeydown="if(event.key==='Enter')document.getElementById('inp-senha').focus()">
    <label>Senha</label>
    <input type="text" id="inp-senha" placeholder="Ex: eKb3Xf" maxlength="10"
           onkeydown="if(event.key==='Enter')ativar()">
    <button class="btn" id="btn-ativar" onclick="ativar()">Ativar eSIM</button>
    <div class="err-text" id="err-text"></div>
  </div>
  <div id="result-box"></div>
</div>
<script>
function showErr(msg) {
  const el = document.getElementById('err-text');
  el.textContent = msg;
  el.style.display = 'block';
}
async function ativar() {
  const msisdn = document.getElementById('inp-msisdn').value.replace(/\D/g, '');
  const senha = document.getElementById('inp-senha').value.trim();
  document.getElementById('err-text').style.display = 'none';
  if (!msisdn || msisdn.length < 10) { showErr('Numero invalido'); return; }
  if (!senha || senha.length < 4) { showErr('Senha invalida'); return; }
  const btn = document.getElementById('btn-ativar');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Ativando...';
  document.getElementById('result-box').innerHTML = '';
  try {
    const r = await fetch('/api/ativar_esim', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({msisdn, senha})});
    const data = await r.json();
    if (data.ok) {
      let html = '<div class="result success"><h2>\uD83C\uDF89 '+(data.message||'eSIM Ativado!')+'</h2>';
      html += '<div class="msg">'+msisdn+'</div>';
      if (data.nova_senha) {
        html += '<div style="margin-top:14px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:10px">';
        html += '<div style="font-size:11px;color:var(--dim);margin-bottom:4px">SENHA TROCADA PARA</div>';
        html += '<div style="font-size:22px;font-weight:800;letter-spacing:3px;color:var(--accent)">'+data.nova_senha+'</div>';
        html += '</div>';
      }
      html += '</div>';
      document.getElementById('result-box').innerHTML = html;
    } else {
      let errHtml = '<div class="result error"><h2>'+(data.error||'Erro desconhecido')+'</h2>';
      if (data.nova_senha) {
        errHtml += '<div style="margin-top:14px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:10px">';
        errHtml += '<div style="font-size:11px;color:var(--dim);margin-bottom:4px">SUA NOVA SENHA</div>';
        errHtml += '<div style="font-size:22px;font-weight:800;letter-spacing:3px;color:var(--accent)">'+data.nova_senha+'</div>';
        errHtml += '<div style="font-size:11px;color:var(--dim);margin-top:6px">Use essa senha para tentar novamente</div>';
        errHtml += '</div>';
      }
      errHtml += '</div>';
      document.getElementById('result-box').innerHTML = errHtml;
    }
  } catch(e) {
    document.getElementById('result-box').innerHTML = '<div class="result error"><h2>Erro de conexao</h2></div>';
  }
  btn.disabled = false;
  btn.textContent = 'Ativar eSIM';
}
</script>
</body>
</html>"""


@app.route("/ativar")
def ativar_esim_page():
    return render_template_string(ATIVAR_ESIM_HTML)


@app.route("/api/ativar_esim", methods=["POST"])
def api_ativar_esim():
    data = request.get_json()
    msisdn = re.sub(r"[^\d]", "", data.get("msisdn", ""))
    senha = data.get("senha", "").strip()
    if not msisdn or len(msisdn) < 10:
        return jsonify({"ok": False, "error": "Numero invalido", "steps": []})
    if not senha:
        return jsonify({"ok": False, "error": "Senha obrigatoria", "steps": []})
    # Validar número no DB
    job = _buscar_email_por_msisdn(msisdn)
    if not job:
        return jsonify({"ok": False, "error": "Numero nao encontrado no sistema", "steps": []})
    try:
        result = _ativar_esim_flow(msisdn, senha)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "steps": []})



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT)
    args = parser.parse_args()
    print(f"🌐 Dashboard: http://localhost:{args.port}")
    print(f"🔧 Admin: http://localhost:{args.port}/admin")
    app.run(host="0.0.0.0", port=args.port, debug=False)

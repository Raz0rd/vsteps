# API Documentation - vsteps

Documentação da API para integração com frontend (Lovable).

## Base URL

```
http://localhost:5050
```

## Autenticação

- **Dashboard público**: Sem autenticação
- **Admin**: Password `admin2026xz` (session cookie)

---

## Endpoints de Worker

### 1. Parar Worker

```http
POST /api/worker/stop
```

**Response:**
```json
{
  "ok": true,
  "msg": "🛑 Worker sinalizado para parar. Reinicie o container para aplicar."
}
```

### 2. Iniciar Worker

```http
POST /api/worker/start
```

**Response:**
```json
{
  "ok": true,
  "msg": "▶️ Worker habilitado. Reinicie o container para aplicar."
}
```

### 3. Status do Worker

```http
GET /api/worker/status
```

**Response:**
```json
{
  "stopped": false
}
```

---

## Endpoints de Jobs

### 4. Listar Jobs

```http
GET /api/jobs?exclude=queued&limit=200
```

**Query Params:**
- `exclude` - Status para excluir (ex: `queued`)
- `limit` - Limite de resultados (padrão: 200)
- `status` - Filtrar por status (ex: `success`, `failed`, `processing`)

**Response:**
```json
[
  {
    "id": 12345,
    "card_number": "1234567890123456",
    "card_month": 12,
    "card_year": 2025,
    "card_cvv": "123",
    "card_name": "TITULAR",
    "uf": "RS",
    "status": "success",
    "msisdn": "5551999999999",
    "order_code": "EASY-123456",
    "email": "email@hotmail.com",
    "cpf": "12345678900",
    "senha": "senha123",
    "qr_url": "https://...",
    "current_step": "APPROVED C1",
    "error_msg": "",
    "created_at": 1744190400,
    "updated_at": 1744191000
  }
]
```

**Status possíveis:**
- `queued` - Na fila
- `processing` - Em processamento
- `success` - Aprovado
- `failed` - Falhou
- `cancelled` - Cancelado

### 5. Enviar Cartões para Fila

```http
POST /api/submit
Content-Type: application/json
```

**Body:**
```json
{
  "cards_text": "1234567890123456|12|25|123\n9876543210987654|06|26|456",
  "uf": "RS"
}
```

**Response:**
```json
{
  "ok": true,
  "msg": "✅ 2 cartões adicionados à fila",
  "batch_id": 123,
  "errors": []
}
```

### 6. Cancelar Jobs

```http
POST /api/cancel
Content-Type: application/json
```

**Body:**
```json
{
  "mode": "queued"
}
```

**Modos:**
- `queued` - Cancela jobs na fila e limpa CCs
- `all` - Cancela todos (queued + processing) e limpa CCs
- `purge` - Remove cancelled + failed (limpa histórico)
- `reset` - Deleta TODOS os jobs

**Response:**
```json
{
  "ok": true,
  "msg": "🛑 10 jobs cancelados e CCs removidos"
}
```

### 7. Estatísticas

```http
GET /api/stats
```

**Response:**
```json
{
  "queued": 5,
  "processing": 3,
  "success": 100,
  "failed": 20,
  "cancelled": 2
}
```

---

## Endpoints de Email (Admin)

### 8. Listar Emails

```http
GET /admin/api/emails
```

**Response:**
```json
[
  {
    "id": 1,
    "email": "email1@hotmail.com",
    "status": "available",
    "created_at": 1744190400
  }
]
```

### 9. Contar Emails

```http
GET /admin/api/emails/count
```

**Response:**
```json
{
  "total": 50,
  "available": 45,
  "in_use": 5
}
```

---

## Endpoints de eSIMs

### 10. Listar eSIMs Aprovados

```http
GET /api/esims
```

**Response:**
```json
[
  {
    "id": 12345,
    "msisdn": "5551999999999",
    "cpf": "12345678900",
    "senha": "senha123",
    "card_number": "1234567890123456",
    "card_month": 12,
    "card_year": 2025,
    "card_cvv": "123",
    "card_name": "TITULAR",
    "qr_url": "https://...",
    "qr_path": "/data/qrcodes/...",
    "order_code": "EASY-123456",
    "email": "email@hotmail.com",
    "extracted": 0,
    "whatsapp": null,
    "created_at": 1744190400
  }
]
```

### 11. Limpar Histórico de eSIMs

```http
POST /api/esims/clear
```

**Response:**
```json
{
  "ok": true,
  "msg": "🗑️ 100 eSIMs aprovados deletados"
}
```

### 12. Marcar eSIMs como Extraídos

```http
POST /api/esim/mark-extracted
Content-Type: application/json
```

**Body:**
```json
{
  "job_ids": [12345, 12346],
  "all": false
}
```

**Response:**
```json
{
  "ok": true,
  "count": 2
}
```

---

## Endpoints de CPFs (Supabase)

### 13. Estatísticas de CPFs

```http
GET /admin/api/cpfs/stats
```

**Response:**
```json
{
  "disponivel": 1000,
  "usado": 500,
  "sucesso": 200,
  "aprovado": 150,
  "erroanalisedecredito": 50,
  "erroenviardados": 30,
  "erro": 70
}
```

---

## Endpoints de Configuração (Admin)

### 14. Configurar Threads do Worker

```http
GET /admin/api/workers
```

**Response:**
```json
{
  "threads": 5
}
```

```http
POST /admin/api/workers
Content-Type: application/json
```

**Body:**
```json
{
  "threads": 10
}
```

**Response:**
```json
{
  "ok": true,
  "msg": "✅ Worker threads alterado para 10. Reinicie o worker para aplicar.",
  "threads": 10
}
```

### 15. Configurar Proxy

```http
GET /api/config/proxy
```

**Response:**
```json
{
  "proxy": "http://mitmproxy:8888"
}
```

---

## Monitoramento em Tempo Real

### Live Panel

Para monitorar jobs em processamento em tempo real:

1. Chamar `/api/jobs?exclude=queued` a cada 2-3 segundos
2. Filtrar jobs com `status === "processing"`
3. Exibir `current_step`, `cpf`, `card_number` e tempo decorrido

**Steps do processamento:**
- `SESSÃO` - Inicializando sessão com Vivo
- `OFERTA` - Selecionando oferta
- `CPF` - Preenchendo CPF
- `EMAIL` - Preenchendo email
- `OTP` - Aguardando OTP
- `OTP OK` - OTP validado
- `CADASTRO` - Cadastro completo
- `NÚMERO` - Selecionando número
- `CAPTCHA` - Resolvendo captcha
- `CARD LIVE` - Cartão aprovado
- `CARD DIE` - Cartão rejeitado

### Polling Recomendado

```javascript
// Poll jobs a cada 2 segundos
setInterval(async () => {
  const response = await fetch('/api/jobs?exclude=queued');
  const jobs = await response.json();
  
  const processing = jobs.filter(j => j.status === 'processing');
  const success = jobs.filter(j => j.status === 'success');
  const failed = jobs.filter(j => j.status === 'failed');
  
  updateLivePanel(processing);
  updateStats(success, failed);
}, 2000);
```

---

## Exemplo de Integração

### React Component

```javascript
import { useEffect, useState } from 'react';

function WorkerMonitor() {
  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState({});
  const [workerStopped, setWorkerStopped] = useState(false);

  useEffect(() => {
    loadJobs();
    const interval = setInterval(loadJobs, 2000);
    return () => clearInterval(interval);
  }, []);

  const loadJobs = async () => {
    const [jobsRes, statsRes, workerRes] = await Promise.all([
      fetch('/api/jobs?exclude=queued'),
      fetch('/api/stats'),
      fetch('/api/worker/status')
    ]);
    
    setJobs(await jobsRes.json());
    setStats(await statsRes.json());
    setWorkerStopped((await workerRes.json()).stopped);
  };

  const stopWorker = async () => {
    await fetch('/api/worker/stop', { method: 'POST' });
    loadJobs();
  };

  const startWorker = async () => {
    await fetch('/api/worker/start', { method: 'POST' });
    loadJobs();
  };

  const processing = jobs.filter(j => j.status === 'processing');
  const success = jobs.filter(j => j.status === 'success');

  return (
    <div>
      <div>
        <button onClick={stopWorker} disabled={workerStopped}>
          🛑 Parar
        </button>
        <button onClick={startWorker} disabled={!workerStopped}>
          ▶️ Iniciar
        </button>
      </div>
      
      <div>
        <h3>Stats</h3>
        <p>Success: {stats.success}</p>
        <p>Failed: {stats.failed}</p>
        <p>Processing: {stats.processing}</p>
      </div>

      <div>
        <h3>Processing ({processing.length})</h3>
        {processing.map(job => (
          <div key={job.id}>
            <span>{job.current_step}</span>
            <span>{job.card_number}</span>
            <span>{job.cpf}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

---

## Notas Importantes

1. **Worker Stop/Start**: A flag é salva no banco, mas o worker precisa checar a cada loop. Pode levar até 5 segundos para o efeito.
2. **Thread Safety**: `take_next_job()` usa transação para evitar race conditions entre threads.
3. **CPF Randomização**: CPFs são puxados randomizados da fila, não sempre os primeiros.
4. **CPF Aprovado**: Quando job é `success`, CPF é marcado como "aprovado" no Supabase e não volta para fila.
5. **Rate Limiting**: Polling não deve ser mais frequente que 1-2 segundos para não sobrecarregar o servidor.

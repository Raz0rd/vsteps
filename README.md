# vsteps - Vivo Easy eSIM Automation

Dashboard Docker para automação de eSIMs Vivo Easy com proxy mitmproxy.

## Funcionalidades

- **Dashboard Web** - Interface para gerenciar jobs e eSIMs
- **Worker Multi-thread** - Processamento paralelo de jobs
- **Proxy mitmproxy** - Proxy upstream com autenticação
- **Pool de Emails Hotmail** - Integração com Microsoft Graph API
- **Fila de CPFs** - Integração com Supabase para CPFs randomizados

## Como usar

### 1. Subir os containers

```bash
docker-compose up -d
```

### 2. Acessar o dashboard

- Dashboard: http://localhost:5050
- Admin: http://localhost:5050/admin (senha: admin2026xz)

### 3. Controles do Worker

- **🛑 Parar** - Para o worker
- **▶️ Iniciar** - Inicia o worker
- **⛔ Cancelar** - Cancela jobs
- **🗑️ Limpar** - Limpa lista visual
- **🔄 Reset** - Deleta tudo

## Configuração

### Proxy

O proxy é configurado no `docker-compose.yml`:

```yaml
mitmproxy:
  command: >
    mitmdump -p 8888
    --mode upstream:HOST:PORT
    --upstream-auth USER:PASS
```

### Variáveis de Ambiente

- `WORKER_THREADS` - Número de threads (padrão: 5)
- `MAX_USOS_CARTAO` - Máximo de usos por cartão (padrão: 3)
- `USE_PROXY` - Usar proxy (true/false)
- `PROXY_URL` - URL do proxy (http://mitmproxy:8888)
- `TWOCAPTCHA_KEY` - Chave do 2Captcha

## Estrutura

```
vivo_docker/
├── app.py              - Dashboard Flask
├── worker.py           - Worker processador
├── db.py               - Database SQLite
├── cpf_fila.py         - Integração Supabase CPFs
├── hotmail_pool.py     - Pool de emails Hotmail
├── docker-compose.yml  - Orquestração Docker
└── Dockerfile          - Imagem do container
```

## Endpoints

### API Pública

- `POST /api/submit` - Enviar cartões para fila
- `POST /api/cancel` - Cancelar jobs
- `POST /api/worker/stop` - Parar worker
- `POST /api/worker/start` - Iniciar worker
- `GET /api/worker/status` - Status do worker
- `GET /api/jobs` - Listar jobs
- `GET /api/stats` - Estatísticas

### API Admin

- `GET /admin/api/workers` - Configurar threads
- `GET /admin/api/emails` - Gerenciar emails
- `GET /admin/api/cpfs/stats` - Stats CPFs Supabase

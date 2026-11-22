# FastGov — Automação de participação em licitações

Sistema enxuto (FastAPI + SQLite) para **extrair compras do PNCP**, **detalhar
itens selecionados** (ComprasNet/SERPRO) e **conduzir cada item por um workflow de
participação**. Projetado para rodar leve no Railway.

## Princípio

| Camada | Fonte | Custo | Quando |
|---|---|---|---|
| Compras + itens básicos | PNCP (`/api/search`, `/api/pncp/v1`) — público | barato, em lote | ingestão |
| Detalhamento (valor estimado, intervalo de lances, item/grupo, modo de disputa) | ComprasNet/SERPRO — autenticado | ~1 req por item | **só ao entrar na etapa "proposta"** |

## Workflow do item

Cada item percorre as etapas e pode ser **excluído** (soft-delete) em qualquer uma:

```
selecao  →  proposta (detalhamento)  →  disputa  →  homologacao  →  homologado
```

- Item novo nasce em **`selecao`**.
- Entrar em **`proposta`** dispara o detalhamento SERPRO (via `PATCH /itens/{id}/etapa`
  ou `POST /compras/{id}/detalhar`).
- Itens ficam **agrupados por edital** — ver `GET /compras/{id}/painel`.
- Reprocessar o PNCP **não** reseta a etapa nem a exclusão do item.

## Arquitetura (modular)

```
app/
  config.py      env/configuração
  db.py          SQLite (WAL) + schema + migração
  schemas.py     contratos da API (pydantic)
  repository.py  acesso a dados (toda a SQL)
  workflow.py    etapas do item e regras
  pncp.py        cliente PNCP (throttle/retry contra rate-limit)
  comprasnet.py  cliente SERPRO (detalhamento sob demanda)
  services.py    orquestração
  main.py        API REST
```

## Rodando local

```bash
pip install -r requirements.txt
cp .env.example .env        # ajuste se quiser
uvicorn app.main:app --reload
# Painel (frontend):     http://localhost:8000/        (redireciona p/ /ui/)
# Docs interativas:      http://localhost:8000/docs
```

## Frontend (`web/`)

Painel ERP estático (HTML/CSS/JS, **sem build**), servido pelo próprio FastAPI em
`/ui` (mesma origem da API — sem CORS). Minimalista, analítico e responsivo:

- **Etapa é a navegação superior**: abas `Seleção · Proposta · Disputa ·
  Homologação · Homologado`, com contador de itens ativos por etapa.
- **Master-detail**: a lateral lista os **editais** da etapa (com contagem de
  ativos e inativos); o conteúdo mostra **só os itens do edital selecionado**.
- Item **avança/retrocede por botões** (`← Voltar` / `Avançar →`); entrar em
  *Proposta* dispara o detalhamento.
- **Inativar/Reativar**: inativar não apaga — marca o item como inativo **na
  etapa** (mantém a etapa e grava `inativado_em`), para **histórico**.
- **Filtro principal de situação** (toolbar): `Ativos · Inativos · Todos` —
  aplica à lateral e aos itens de uma vez.
- **Paginação**: a lateral (editais) pagina no servidor e a tabela de itens no
  cliente; ambas com seletor de *itens por página* (lembrado no navegador).
- Busca por texto, UF e modalidade; botão **Ingerir PNCP**.
- **Mobile**: abas rolam na horizontal, a lateral vira faixa no topo e a tabela
  vira cartões empilhados (sem scroll horizontal nem quebra de layout).

Arquivos: `web/index.html`, `web/styles.css`, `web/app.js`.

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `POST`  | `/ingest` | Varre o PNCP e grava editais + itens básicos |
| `GET`   | `/compras` | Lista compras (filtros `uf`, `modalidade`, `uasg`, `q`, paginação) |
| `GET`   | `/compras/{id}` | Compra + itens |
| `GET`   | `/compras/{id}/itens` | Itens da compra (filtros `etapa`, `incluir_excluidos`) |
| `GET`   | `/compras/{id}/painel` | Painel de um edital: itens + contagem por etapa |
| `GET`   | `/etapas` | **Contagem de itens ativos por etapa** (abas superiores); filtros `q`, `uf`, `modalidade` |
| `GET`   | `/etapas/{etapa}/editais` | **Lateral paginada** (`limit`/`offset`→`{total,editais}`); `situacao`=ativos/inativos/todos; `n_itens`/`n_inativos` |
| `GET`   | `/etapas/{etapa}/compras/{id}/itens` | **Itens de um edital na etapa** (`situacao`=ativos/inativos/todos) |
| `POST`  | `/compras/{id}/detalhar` | Detalha os itens em `numeros` (→ etapa `proposta`) |
| `GET`   | `/itens` | Busca itens entre editais (`q`, `etapa`, `uf`, `edital_id`, `material_ou_servico`) |
| `GET`   | `/itens/{id}` | Um item |
| `PATCH` | `/itens/{id}/etapa` | Move de etapa (entrar em `proposta` detalha) |
| `POST`  | `/itens/{id}/inativar` · `/reativar` | Inativa/reativa o item **na etapa** (mantém etapa p/ histórico) |
| `GET`   | `/health` | Healthcheck |

### Exemplos

```bash
# 1) Ingerir compras publicadas a partir de hoje
curl -X POST localhost:8000/ingest -H 'Content-Type: application/json' \
  -d '{"data_publicacao_inicio":"2026-11-13"}'

# 2) Listar compras de SP / painel de um edital
curl 'localhost:8000/compras?uf=SP&limit=20'
curl 'localhost:8000/compras/16052605000152027/painel'

# 3) Selecionar e detalhar itens (vão para a etapa "proposta")
curl -X POST localhost:8000/compras/16052605000152027/detalhar \
  -H 'Content-Type: application/json' -d '{"numeros":[1,5,12]}'

# 4) Avançar / excluir item no workflow
curl -X PATCH localhost:8000/itens/16052605000152027-1/etapa \
  -H 'Content-Type: application/json' -d '{"etapa":"disputa"}'
curl -X POST localhost:8000/itens/16052605000152027-3/excluir

# 5) Buscar itens em disputa que contenham "cabo"
curl 'localhost:8000/itens?etapa=disputa&q=cabo'
```

## Autenticação (token ComprasNet) e modo

O app gerencia o token SERPRO (portado de `oauth.py`) em `app/auth.py`:

- **Modo debug** (ajuste *runtime*, persistido; badge no topo): o token vem do
  servidor remoto `COMPRASNET_TOKEN_URL` (fastgov2). **Modo produção**: o app
  autentica e renova o token (`tokenInicial` / `retoken`), guardando-o em SQLite
  (`token_gov`). O modo inicial deriva de `EM_PRODUCAO`, mas o valor escolhido em
  `/ui/admin.html` prevalece e persiste.
- Token estático `COMPRASNET_TOKEN` (env) tem prioridade sobre tudo.
- `app/comprasnet.py` usa `auth.obter_token()`; o app também **expõe** o token.

| Método | Rota | Descrição |
|---|---|---|
| `GET`  | `/token/?tipo=token` | Emite o token vigente (o app vira provedor, como o fastgov2) |
| `GET`  | `/admin/modo` | Modo atual (debug/produção) + presença de token |
| `POST` | `/admin/modo-debug` | `{debug: bool}` — seleciona o modo (badge atualiza) |
| `GET`  | `/admin/auth` | Testa a sessão (`/comprasnet-usuario/v1/usuario`) |
| `POST` | `/admin/auth/refresh` | Renova via `retoken` |
| `POST` | `/admin/auth/token-inicial` | `{id}` — semeia a sessão do fornecedor |

Tela **Operações** (`/ui/admin.html`): alterna o modo (badge DEBUG/PRODUÇÃO),
testa sessão, renova e semeia token. (Workers/agendador/serviços entram a seguir.)

## Operações (background) — `/ui/admin.html`

Painel para operar o sistema como um ERP. Tudo **in-process** (uvicorn
`--workers 1`), em threads, sem bloquear a API. **Dois modelos de execução:**

- **Tarefas** (trabalho finito, ex.: `ingestao`) → fila SQLite (`execucao`) drenada
  pelo **pool de workers** (`BG_WORKERS`). Assíncrono e observável (backlog/rodando).
- **Serviços** (longa duração, ex.: `ws_comprasnet` — cliente STOMP) → **thread própria**
  (não ocupam worker), com start/stop manual ou por cron.
- **Agendador** (APScheduler): cron → só **enfileira** a tarefa (mantém a fila visível)
  ou liga/desliga serviço. Recuperação de crash: `rodando`→`fila` no startup.

Código: `app/background/` (`registry`, `worker`, `scheduler`, `services_manager`,
`ws_comprasnet`). Config: `BG_WORKERS`, `BG_POLL_INTERVAL`, `SCHED_TZ`, `COMPRASNET_WS_URL`.

| Método | Rota | Descrição |
|---|---|---|
| `GET`  | `/admin/status` | Workers/backlog, serviços, próximos agendamentos, execuções recentes |
| `GET`  | `/admin/tarefas` | Tarefas registráveis |
| `POST` | `/admin/executar` | Enfileira `{tarefa, params}` ad-hoc |
| `GET`  | `/admin/execucoes` | Fila + histórico (`status`, paginação) |
| `POST` | `/admin/execucoes/{id}/cancelar` | Cancela item em `fila` |
| `GET POST PATCH DELETE` | `/admin/agendamentos[...]` | CRUD + `/{id}/executar-agora` |
| `GET`  | `/admin/servicos` · `POST .../{nome}/iniciar\|parar` · `PATCH .../{nome}` | Serviços (auto, crons) |

> O serviço WS conecta e reporta status/erros; afinar as assinaturas de tópicos de
> disputa ao vivo (e persistir eventos) é follow-up.

## Railway

- `Procfile`/`railway.json` já configurados (uvicorn, 1 worker).
- Variáveis: ver `.env.example`.
- **Persistência**: o SQLite é um arquivo; monte um **volume** e aponte
  `DB_PATH=/data/fastgov.db`, senão o banco é perdido a cada deploy.

## Banco

SQLite em modo WAL: zero processo externo, baixa RAM, leituras concorrentes.
Para uma instância na Railway é o ponto ideal entre velocidade e simplicidade.
Trocar por Postgres depois = reimplementar só `db.py`/`repository.py`.

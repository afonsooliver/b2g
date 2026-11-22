"""Camada de banco: SQLite em modo WAL (leve, sem processo externo, rápido).

Uma conexão por operação (SQLite abre em microssegundos). WAL permite leituras
concorrentes com escrita; `synchronous=NORMAL` dá ótima performance com
durabilidade suficiente para este caso de uso.
"""
import os
import sqlite3
from contextlib import contextmanager

from .config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS uasg (
    codigo        TEXT PRIMARY KEY,
    cnpj          TEXT,
    razao_social  TEXT,
    nome          TEXT,
    municipio     TEXT,
    uf            TEXT
);

CREATE TABLE IF NOT EXISTS edital (
    id                      TEXT PRIMARY KEY,          -- chave de 18 dígitos (uasg+mod+numero+ano)
    uasg_codigo             TEXT,
    numero_pncp             TEXT,
    ano_compra              INTEGER,
    sequencial_compra       TEXT,
    numero                  TEXT,
    descricao               TEXT,
    srp                     INTEGER,
    dt_abertura             TEXT,
    modalidade              TEXT,
    modo_disputa            TEXT,                      -- A/F/AF/FA (preenchido no detalhamento)
    modo_disputa_descricao  TEXT,
    url_edital              TEXT,
    status_itens            TEXT DEFAULT 'fila',       -- fila | completo | nao_encontrado
    criado_em               TEXT DEFAULT (datetime('now')),
    atualizado_em           TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (uasg_codigo) REFERENCES uasg(codigo)
);

CREATE TABLE IF NOT EXISTS item (
    id                              TEXT PRIMARY KEY,  -- {edital_id}-{numero}
    edital_id                       TEXT NOT NULL,
    numero                          INTEGER,
    descricao                       TEXT,
    material_ou_servico             TEXT,              -- M/S (catálogo PNCP)
    quantidade                      REAL,
    unidade                         TEXT,
    valor_unitario_estimado_edital  REAL,
    -- Detalhamento SERPRO (preenchido SOB DEMANDA, só para itens selecionados) --
    descricao_detalhada             TEXT,
    item_ou_grupo                   TEXT,              -- ITEM | GRUPO
    tipo_fe                         TEXT,              -- I/S/G (fase-externa)
    valor_estimado_unitario         REAL,
    valor_estimado_total            REAL,
    intervalo_lance_tipo            TEXT,              -- Percentual | Valor
    intervalo_lance_tipo_codigo     TEXT,              -- P | V
    intervalo_lance_valor           REAL,
    detalhado_em                    TEXT,              -- timestamp do detalhamento (NULL = não detalhado)
    -- Workflow de participação --
    etapa                           TEXT NOT NULL DEFAULT 'selecao',  -- selecao|proposta|disputa|homologacao|homologado
    inativo                         INTEGER NOT NULL DEFAULT 0,       -- 0/1: inativo NA ETAPA (mantém etapa p/ histórico)
    etapa_em                        TEXT,
    inativado_em                    TEXT,
    FOREIGN KEY (edital_id) REFERENCES edital(id)
);

CREATE TABLE IF NOT EXISTS token_gov (
    id            INTEGER PRIMARY KEY CHECK (id = 1),   -- linha única
    accesstoken   TEXT,
    refreshtoken  TEXT,
    raw_json      TEXT,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS config_app (
    chave         TEXT PRIMARY KEY,                     -- ex.: 'modo_debug'
    valor         TEXT,
    atualizado_em TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agendamento (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    nome             TEXT,
    tarefa           TEXT NOT NULL,                     -- chave do registry
    params_json      TEXT,
    cron             TEXT NOT NULL,                     -- expressão crontab (5 campos)
    ativo            INTEGER NOT NULL DEFAULT 1,
    proxima_execucao TEXT,
    ultima_execucao  TEXT,
    criado_em        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS execucao (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    agendamento_id INTEGER,
    tarefa         TEXT NOT NULL,
    params_json    TEXT,
    status         TEXT NOT NULL DEFAULT 'fila',        -- fila|rodando|sucesso|erro|cancelado
    origem         TEXT NOT NULL DEFAULT 'manual',      -- agendado|manual
    enfileirado_em TEXT DEFAULT (datetime('now')),
    iniciado_em    TEXT,
    finalizado_em  TEXT,
    duracao_ms     INTEGER,
    resultado_json TEXT,
    erro           TEXT,
    worker         TEXT
);

CREATE TABLE IF NOT EXISTS servico (
    nome           TEXT PRIMARY KEY,                    -- ex.: 'ws_comprasnet'
    estado         TEXT NOT NULL DEFAULT 'parado',      -- parado|rodando|erro
    auto           INTEGER NOT NULL DEFAULT 0,
    cron_inicio    TEXT,
    cron_fim       TEXT,
    config_json    TEXT,
    detalhe        TEXT,
    ultima_mudanca TEXT
);

CREATE TABLE IF NOT EXISTS disputa (
    compra            TEXT NOT NULL,
    numero            INTEGER NOT NULL,
    lance_automatico  INTEGER NOT NULL DEFAULT 0,
    valor_minimo      REAL,                            -- piso: não lançar abaixo
    passo             REAL,                            -- decremento por lance
    situacao          TEXT,                            -- situacaoParticipanteDisputa (P=perdendo…)
    fase              TEXT,
    melhor_fornecedor REAL,
    melhor_geral      REAL,
    atualizado_em     TEXT,
    PRIMARY KEY (compra, numero)
);

CREATE TABLE IF NOT EXISTS catalogo (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nome       TEXT NOT NULL,
    descricao  TEXT,
    criado_em  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS produto (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    nome                   TEXT NOT NULL,
    catalogo_id            INTEGER,
    ncm                    TEXT,
    custo_produto_unitario REAL,
    custo_maximo_unitario  REAL,
    margem_min_lucro       REAL,            -- %
    peso_unitario          REAL,            -- gramas
    altura_cm              REAL,
    largura_cm             REAL,
    comprimento_cm         REAL,
    imagem_path            TEXT,
    descricao              TEXT,
    criado_em              TEXT DEFAULT (datetime('now')),
    atualizado_em          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (catalogo_id) REFERENCES catalogo(id)
);

CREATE TABLE IF NOT EXISTS custo (
    item_id                 TEXT PRIMARY KEY,        -- {compra}-{numero}
    compra                  TEXT,
    numero                  INTEGER,
    produto_id              INTEGER,
    quantidade              REAL,
    custo_produto_unitario  REAL,
    custo_maximo_unitario   REAL,
    custo_produto           REAL,
    margem_minima           REAL,
    frete_unitario          REAL,
    frete_total             REAL,
    transportadora          TEXT,
    custo_total_unitario    REAL,
    custo_total             REAL,
    valor_proposta_minima   REAL,                    -- lance mínimo
    total_proposta_minima   REAL,
    lucro_proposta_minima   REAL,
    valor_proposta_atual    REAL,                    -- proposta inicial
    valor_estimado_edital   REAL,
    incremento              REAL,
    incremento_tipo         TEXT,
    modo_disputa            TEXT,
    melhor_proposta_global  REAL,
    atualizado_em           TEXT
);

CREATE INDEX IF NOT EXISTS idx_execucao_status ON execucao(status);
CREATE INDEX IF NOT EXISTS idx_produto_catalogo ON produto(catalogo_id);
"""

# Índices criados DEPOIS da migração (podem referenciar colunas renomeadas).
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_item_edital    ON item(edital_id);
CREATE INDEX IF NOT EXISTS idx_item_etapa     ON item(etapa);
CREATE INDEX IF NOT EXISTS idx_item_inativo   ON item(inativo);
CREATE INDEX IF NOT EXISTS idx_edital_status  ON edital(status_itens);
CREATE INDEX IF NOT EXISTS idx_edital_uasg    ON edital(uasg_codigo);
"""

# Colunas adicionadas depois da v1: aplicadas via ALTER se faltarem (bancos antigos).
_MIGRACOES = {
    "item": {
        "etapa": "TEXT NOT NULL DEFAULT 'selecao'",
        "inativo": "INTEGER NOT NULL DEFAULT 0",
        "etapa_em": "TEXT",
        "inativado_em": "TEXT",
        "produto_id": "INTEGER",
    },
    "edital": {
        "frete_servico": "TEXT DEFAULT 'mais_barato'",
    },
}

# Renomes preservando dados (bancos criados antes da renomeação excluido->inativo).
_RENOMES = {
    "item": {"excluido": "inativo", "excluido_em": "inativado_em"},
}


def _migrar(conn):
    for tabela, renomes in _RENOMES.items():
        existentes = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})")}
        for antigo, novo in renomes.items():
            if antigo in existentes and novo not in existentes:
                conn.execute(f"ALTER TABLE {tabela} RENAME COLUMN {antigo} TO {novo}")
    for tabela, colunas in _MIGRACOES.items():
        existentes = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})")}
        for coluna, definicao in colunas.items():
            if coluna not in existentes:
                conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")


def _connect():
    destino = os.path.dirname(config.DB_PATH)
    if destino:
        os.makedirs(destino, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_conn():
    """Transação por bloco: commit no sucesso, rollback em exceção."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)   # cria tabelas (fresh) / no-op se já existem
        _migrar(conn)                # renomeia/adiciona colunas (bancos antigos)
        conn.executescript(INDEXES)  # índices já com as colunas finais

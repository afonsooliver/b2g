"""Acesso a dados. Toda SQL mora aqui — serviços e API não tocam no banco direto.

Escritas em lote usam INSERT ... ON CONFLICT DO UPDATE (upsert) para reprocessar
sem SELECT prévio por linha.
"""
import json
from typing import Iterable, Optional

from .db import get_conn

# Campos do detalhamento SERPRO atualizados sob demanda.
_CAMPOS_DETALHE = (
    "descricao_detalhada", "item_ou_grupo", "tipo_fe",
    "valor_estimado_unitario", "valor_estimado_total",
    "intervalo_lance_tipo", "intervalo_lance_tipo_codigo", "intervalo_lance_valor",
)


# ---------------- UASG ----------------
def upsert_uasgs(conn, uasgs: Iterable[dict]):
    linhas = [
        (u["codigo"], u.get("cnpj"), u.get("razao_social"),
         u.get("nome"), u.get("municipio"), u.get("uf"))
        for u in uasgs if u.get("codigo")
    ]
    if not linhas:
        return
    conn.executemany(
        """INSERT INTO uasg (codigo, cnpj, razao_social, nome, municipio, uf)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(codigo) DO UPDATE SET
             cnpj=excluded.cnpj, razao_social=excluded.razao_social,
             nome=excluded.nome, municipio=excluded.municipio, uf=excluded.uf""",
        linhas,
    )


# ---------------- EDITAL ----------------
def ids_editais_existentes(conn, ids: list[str]) -> set:
    if not ids:
        return set()
    marc = ",".join("?" * len(ids))
    cur = conn.execute(f"SELECT id FROM edital WHERE id IN ({marc})", ids)
    return {r["id"] for r in cur.fetchall()}


def upsert_editais(conn, editais: Iterable[dict], reprocessar: bool = False):
    """Insere editais. Sem reprocessar, mantém o existente (não rebaixa status)."""
    linhas = [(
        e["id"], e.get("uasg_codigo"), e.get("numero_pncp"), e.get("ano_compra"),
        e.get("sequencial_compra"), e.get("numero"), e.get("descricao"),
        1 if e.get("srp") else (0 if e.get("srp") is not None else None),
        e.get("dt_abertura"), e.get("modalidade"), e.get("url_edital"),
    ) for e in editais if e.get("id")]
    if not linhas:
        return
    if reprocessar:
        conflito = """ON CONFLICT(id) DO UPDATE SET
            uasg_codigo=excluded.uasg_codigo, numero_pncp=excluded.numero_pncp,
            ano_compra=excluded.ano_compra, sequencial_compra=excluded.sequencial_compra,
            numero=excluded.numero, descricao=excluded.descricao, srp=excluded.srp,
            dt_abertura=excluded.dt_abertura, modalidade=excluded.modalidade,
            url_edital=excluded.url_edital, atualizado_em=datetime('now')"""
    else:
        conflito = "ON CONFLICT(id) DO NOTHING"
    conn.executemany(
        f"""INSERT INTO edital
            (id, uasg_codigo, numero_pncp, ano_compra, sequencial_compra,
             numero, descricao, srp, dt_abertura, modalidade, url_edital)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) {conflito}""",
        linhas,
    )


def set_status_itens(conn, edital_id: str, status: str):
    conn.execute(
        "UPDATE edital SET status_itens=?, atualizado_em=datetime('now') WHERE id=?",
        (status, edital_id),
    )


def set_modo_disputa(conn, edital_id: str, codigo, descricao):
    conn.execute(
        """UPDATE edital SET modo_disputa=?, modo_disputa_descricao=?,
           atualizado_em=datetime('now') WHERE id=?""",
        (codigo, descricao, edital_id),
    )


def get_edital(conn, edital_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM edital WHERE id=?", (edital_id,)).fetchone()
    return dict(r) if r else None


def get_uasg(conn, codigo: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM uasg WHERE codigo=?", (codigo,)).fetchone()
    return dict(r) if r else None


def listar_editais(conn, uf=None, modalidade=None, uasg=None, q=None,
                   limit=50, offset=0) -> list[dict]:
    cond, params = [], []
    if uf:
        cond.append("e.uasg_codigo IN (SELECT codigo FROM uasg WHERE uf=?)")
        params.append(uf)
    if modalidade:
        cond.append("e.modalidade=?")
        params.append(modalidade)
    if uasg:
        cond.append("e.uasg_codigo=?")
        params.append(uasg)
    if q:
        cond.append("e.descricao LIKE ?")
        params.append(f"%{q}%")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    params += [limit, offset]
    cur = conn.execute(
        f"SELECT * FROM edital e {where} ORDER BY dt_abertura DESC LIMIT ? OFFSET ?",
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def editais_pendentes(conn, status="fila", ids: Optional[list] = None) -> list[dict]:
    cond, params = [], []
    if status is not None:
        cond.append("status_itens=?")
        params.append(status)
    if ids:
        cond.append("id IN (%s)" % ",".join("?" * len(ids)))
        params += list(ids)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    cur = conn.execute(f"SELECT * FROM edital {where}", params)
    return [dict(r) for r in cur.fetchall()]


# ---------------- ITEM ----------------
def upsert_itens(conn, edital_id: str, itens: Iterable[dict]):
    """Itens básicos (PNCP). Não mexe nos campos de detalhamento já preenchidos."""
    linhas = [(
        f"{edital_id}-{it['numero']}", edital_id, it.get("numero"),
        it.get("descricao"), it.get("material_ou_servico"), it.get("quantidade"),
        it.get("unidade"), it.get("valor_unitario_estimado_edital"),
    ) for it in itens if it.get("numero") is not None]
    if not linhas:
        return 0
    conn.executemany(
        """INSERT INTO item
            (id, edital_id, numero, descricao, material_ou_servico,
             quantidade, unidade, valor_unitario_estimado_edital)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              descricao=excluded.descricao,
              material_ou_servico=excluded.material_ou_servico,
              quantidade=excluded.quantidade, unidade=excluded.unidade,
              valor_unitario_estimado_edital=excluded.valor_unitario_estimado_edital""",
        linhas,
    )
    return len(linhas)


def update_detalhe(conn, edital_id: str, numero: int, detalhe: dict):
    """Grava o detalhamento de 1 item. Cria linha mínima se o item ainda não existe."""
    item_id = f"{edital_id}-{numero}"
    sets = ", ".join(f"{c}=?" for c in _CAMPOS_DETALHE)
    valores = [detalhe.get(c) for c in _CAMPOS_DETALHE]
    cur = conn.execute(
        f"UPDATE item SET {sets}, detalhado_em=datetime('now') WHERE id=?",
        valores + [item_id],
    )
    if cur.rowcount == 0:
        cols = ", ".join(_CAMPOS_DETALHE)
        marc = ",".join("?" * len(_CAMPOS_DETALHE))
        conn.execute(
            f"""INSERT INTO item (id, edital_id, numero, {cols}, detalhado_em)
                VALUES (?,?,?,{marc}, datetime('now'))""",
            [item_id, edital_id, numero] + valores,
        )


def listar_itens(conn, edital_id: str, numeros: Optional[list] = None,
                 etapa: Optional[str] = None, incluir_inativos: bool = False) -> list[dict]:
    cond = ["edital_id=?"]
    params = [edital_id]
    if numeros:
        cond.append("numero IN (%s)" % ",".join("?" * len(numeros)))
        params += list(numeros)
    if etapa:
        cond.append("etapa=?")
        params.append(etapa)
    if not incluir_inativos:
        cond.append("inativo=0")
    cur = conn.execute(
        f"SELECT * FROM item WHERE {' AND '.join(cond)} ORDER BY numero", params
    )
    return [dict(r) for r in cur.fetchall()]


def buscar_itens(conn, q=None, etapa=None, edital_id=None, uf=None,
                 material_ou_servico=None, incluir_inativos=False,
                 limit=50, offset=0) -> list[dict]:
    """Busca itens entre todos os editais (painel global)."""
    cond, params = [], []
    if q:
        cond.append("(i.descricao LIKE ? OR i.descricao_detalhada LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if etapa:
        cond.append("i.etapa=?")
        params.append(etapa)
    if edital_id:
        cond.append("i.edital_id=?")
        params.append(edital_id)
    if material_ou_servico:
        cond.append("i.material_ou_servico=?")
        params.append(material_ou_servico)
    if uf:
        cond.append("i.edital_id IN (SELECT id FROM edital WHERE uasg_codigo IN "
                    "(SELECT codigo FROM uasg WHERE uf=?))")
        params.append(uf)
    if not incluir_inativos:
        cond.append("i.inativo=0")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    params += [limit, offset]
    cur = conn.execute(
        f"SELECT i.* FROM item i {where} ORDER BY i.edital_id, i.numero LIMIT ? OFFSET ?",
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def mover_etapa(conn, item_id: str, etapa: str) -> int:
    cur = conn.execute(
        "UPDATE item SET etapa=?, etapa_em=datetime('now') WHERE id=?",
        (etapa, item_id),
    )
    return cur.rowcount


def set_inativo(conn, item_id: str, inativo: bool) -> int:
    """Inativa/reativa o item NA ETAPA atual (mantém `etapa` para histórico)."""
    cur = conn.execute(
        "UPDATE item SET inativo=?, inativado_em=CASE WHEN ? THEN datetime('now') ELSE NULL END "
        "WHERE id=?",
        (1 if inativo else 0, 1 if inativo else 0, item_id),
    )
    return cur.rowcount


def painel_edital(conn, edital_id: str) -> dict:
    """Contagens por etapa (ativos) + total de inativos do edital."""
    cur = conn.execute(
        "SELECT etapa, COUNT(*) AS n FROM item WHERE edital_id=? AND inativo=0 GROUP BY etapa",
        (edital_id,),
    )
    por_etapa = {r["etapa"]: r["n"] for r in cur.fetchall()}
    inativos = conn.execute(
        "SELECT COUNT(*) AS n FROM item WHERE edital_id=? AND inativo=1", (edital_id,)
    ).fetchone()["n"]
    return {"por_etapa": por_etapa, "inativos": inativos}


def get_item(conn, item_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
    return dict(r) if r else None


# ---------------- TOKEN SERPRO (store) ----------------
def salvar_token(conn, data: dict):
    acc = data.get("accessToken") or data.get("accesstoken")
    ref = data.get("refreshToken") or data.get("refreshtoken")
    conn.execute(
        """INSERT INTO token_gov (id, accesstoken, refreshtoken, raw_json, atualizado_em)
           VALUES (1, ?, ?, ?, datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
             accesstoken=excluded.accesstoken, refreshtoken=excluded.refreshtoken,
             raw_json=excluded.raw_json, atualizado_em=excluded.atualizado_em""",
        (acc, ref, json.dumps(data, ensure_ascii=False)),
    )


def obter_token_store(conn) -> Optional[dict]:
    r = conn.execute("SELECT * FROM token_gov WHERE id=1").fetchone()
    return dict(r) if r else None


# ---------------- CONFIG runtime (key-value) ----------------
def get_config(conn, chave: str, padrao=None):
    r = conn.execute("SELECT valor FROM config_app WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r else padrao


def set_config(conn, chave: str, valor: str):
    conn.execute(
        """INSERT INTO config_app (chave, valor, atualizado_em)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor, atualizado_em=excluded.atualizado_em""",
        (chave, valor),
    )


# ---------------- FILA DE EXECUÇÕES ----------------
def enfileirar(conn, tarefa: str, params: Optional[dict] = None,
               origem: str = "manual", agendamento_id: Optional[int] = None) -> int:
    cur = conn.execute(
        """INSERT INTO execucao (agendamento_id, tarefa, params_json, status, origem)
           VALUES (?, ?, ?, 'fila', ?)""",
        (agendamento_id, tarefa, json.dumps(params or {}, ensure_ascii=False), origem),
    )
    return cur.lastrowid


def reivindicar_execucao(conn, worker: str) -> Optional[dict]:
    """Reivindica atomicamente a próxima execução da fila (o guard status='fila'
    + lock de escrita do SQLite evita que dois workers peguem a mesma)."""
    row = conn.execute(
        """UPDATE execucao SET status='rodando', iniciado_em=datetime('now'), worker=?
           WHERE id = (SELECT id FROM execucao WHERE status='fila' ORDER BY id LIMIT 1)
           RETURNING *""",
        (worker,),
    ).fetchone()
    return dict(row) if row else None


def marcar_ok(conn, exec_id: int, resultado, duracao_ms: int):
    conn.execute(
        """UPDATE execucao SET status='sucesso', finalizado_em=datetime('now'),
           duracao_ms=?, resultado_json=? WHERE id=?""",
        (duracao_ms, json.dumps(resultado, ensure_ascii=False, default=str), exec_id),
    )


def marcar_erro(conn, exec_id: int, erro: str, duracao_ms: int):
    conn.execute(
        """UPDATE execucao SET status='erro', finalizado_em=datetime('now'),
           duracao_ms=?, erro=? WHERE id=?""",
        (duracao_ms, erro[:4000], exec_id),
    )


def cancelar_execucao(conn, exec_id: int) -> int:
    cur = conn.execute(
        "UPDATE execucao SET status='cancelado', finalizado_em=datetime('now') "
        "WHERE id=? AND status='fila'", (exec_id,),
    )
    return cur.rowcount


def requeue_orfas(conn) -> int:
    """Após crash: execuções presas em 'rodando' voltam para a fila."""
    cur = conn.execute(
        "UPDATE execucao SET status='fila', iniciado_em=NULL, worker=NULL WHERE status='rodando'")
    return cur.rowcount


def contagem_execucoes(conn) -> dict:
    cur = conn.execute("SELECT status, COUNT(*) n FROM execucao GROUP BY status")
    return {r["status"]: r["n"] for r in cur.fetchall()}


def listar_execucoes(conn, status=None, limit=50, offset=0) -> list[dict]:
    cond, params = [], []
    if status:
        cond.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    params += [limit, offset]
    cur = conn.execute(
        f"SELECT * FROM execucao {where} ORDER BY id DESC LIMIT ? OFFSET ?", params)
    return [dict(r) for r in cur.fetchall()]


# ---------------- AGENDAMENTOS ----------------
def criar_agendamento(conn, nome, tarefa, cron, params=None, ativo=True) -> int:
    cur = conn.execute(
        """INSERT INTO agendamento (nome, tarefa, params_json, cron, ativo)
           VALUES (?, ?, ?, ?, ?)""",
        (nome, tarefa, json.dumps(params or {}, ensure_ascii=False), cron, 1 if ativo else 0),
    )
    return cur.lastrowid


def listar_agendamentos(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM agendamento ORDER BY id").fetchall()]


def get_agendamento(conn, ag_id: int) -> Optional[dict]:
    r = conn.execute("SELECT * FROM agendamento WHERE id=?", (ag_id,)).fetchone()
    return dict(r) if r else None


def atualizar_agendamento(conn, ag_id: int, campos: dict):
    permitido = {"nome", "tarefa", "cron", "params_json", "ativo",
                 "proxima_execucao", "ultima_execucao"}
    sets, params = [], []
    for k, v in campos.items():
        if k in permitido:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return
    params.append(ag_id)
    conn.execute(f"UPDATE agendamento SET {', '.join(sets)} WHERE id=?", params)


def remover_agendamento(conn, ag_id: int):
    conn.execute("DELETE FROM agendamento WHERE id=?", (ag_id,))


# ---------------- SERVIÇOS ----------------
def upsert_servico(conn, nome, **campos):
    existe = conn.execute("SELECT 1 FROM servico WHERE nome=?", (nome,)).fetchone()
    if not existe:
        conn.execute("INSERT INTO servico (nome) VALUES (?)", (nome,))
    if campos:
        sets = ", ".join(f"{k}=?" for k in campos)
        conn.execute(f"UPDATE servico SET {sets}, ultima_mudanca=datetime('now') WHERE nome=?",
                     list(campos.values()) + [nome])


def get_servico(conn, nome) -> Optional[dict]:
    r = conn.execute("SELECT * FROM servico WHERE nome=?", (nome,)).fetchone()
    return dict(r) if r else None


def listar_servicos(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM servico ORDER BY nome").fetchall()]


# ---------------- DISPUTA (monitoramento + lances) ----------------
def compras_monitoradas(conn) -> list[str]:
    """Compras a monitorar no WS = com itens ativos em etapa disputa/homologação."""
    cur = conn.execute(
        "SELECT DISTINCT edital_id FROM item "
        "WHERE inativo=0 AND etapa IN ('disputa','homologacao')")
    return [r["edital_id"] for r in cur.fetchall()]


def disputa_upsert(conn, compra: str, numero: int, **campos):
    existe = conn.execute(
        "SELECT 1 FROM disputa WHERE compra=? AND numero=?", (compra, numero)).fetchone()
    if not existe:
        conn.execute("INSERT INTO disputa (compra, numero) VALUES (?, ?)", (compra, numero))
    if campos:
        sets = ", ".join(f"{k}=?" for k in campos)
        conn.execute(
            f"UPDATE disputa SET {sets}, atualizado_em=datetime('now') WHERE compra=? AND numero=?",
            list(campos.values()) + [compra, numero])


def disputa_get(conn, compra: str, numero: int) -> Optional[dict]:
    r = conn.execute("SELECT * FROM disputa WHERE compra=? AND numero=?", (compra, numero)).fetchone()
    return dict(r) if r else None


def disputa_listar(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM disputa ORDER BY compra, numero").fetchall()]


def cancelar_todas_fila(conn) -> int:
    cur = conn.execute(
        "UPDATE execucao SET status='cancelado', finalizado_em=datetime('now') WHERE status='fila'")
    return cur.rowcount


def execucoes_rodando_ids(conn) -> list[int]:
    return [r["id"] for r in conn.execute("SELECT id FROM execucao WHERE status='rodando'").fetchall()]


# ---------------- CATÁLOGOS ----------------
def criar_catalogo(conn, nome, descricao=None) -> int:
    cur = conn.execute("INSERT INTO catalogo (nome, descricao) VALUES (?, ?)", (nome, descricao))
    return cur.lastrowid


def listar_catalogos(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM catalogo ORDER BY nome").fetchall()]


def get_catalogo(conn, cid) -> Optional[dict]:
    r = conn.execute("SELECT * FROM catalogo WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def atualizar_catalogo(conn, cid, campos: dict):
    campos = {k: v for k, v in campos.items() if k in ("nome", "descricao")}
    if not campos:
        return
    sets = ", ".join(f"{k}=?" for k in campos)
    conn.execute(f"UPDATE catalogo SET {sets} WHERE id=?", list(campos.values()) + [cid])


def remover_catalogo(conn, cid):
    conn.execute("DELETE FROM catalogo WHERE id=?", (cid,))


# ---------------- PRODUTOS ----------------
_PRODUTO_COLS = ("nome", "catalogo_id", "ncm", "custo_produto_unitario", "custo_maximo_unitario",
                 "margem_min_lucro", "peso_unitario", "altura_cm", "largura_cm", "comprimento_cm",
                 "descricao", "imagem_path")


def criar_produto(conn, dados: dict) -> int:
    cols = [c for c in _PRODUTO_COLS if c in dados]
    marc = ",".join("?" * len(cols))
    cur = conn.execute(f"INSERT INTO produto ({','.join(cols)}) VALUES ({marc})",
                       [dados[c] for c in cols])
    return cur.lastrowid


def atualizar_produto(conn, pid, dados: dict):
    cols = [c for c in _PRODUTO_COLS if c in dados]
    if not cols:
        return
    sets = ", ".join(f"{c}=?" for c in cols)
    conn.execute(f"UPDATE produto SET {sets}, atualizado_em=datetime('now') WHERE id=?",
                 [dados[c] for c in cols] + [pid])


def get_produto(conn, pid) -> Optional[dict]:
    r = conn.execute("SELECT * FROM produto WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def listar_produtos(conn, catalogo_id=None, q=None, limit=200, offset=0) -> list[dict]:
    cond, params = [], []
    if catalogo_id:
        cond.append("catalogo_id=?")
        params.append(catalogo_id)
    if q:
        cond.append("(nome LIKE ? OR ncm LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    params += [limit, offset]
    cur = conn.execute(f"SELECT * FROM produto {where} ORDER BY nome LIMIT ? OFFSET ?", params)
    return [dict(r) for r in cur.fetchall()]


def remover_produto(conn, pid):
    conn.execute("DELETE FROM produto WHERE id=?", (pid,))


# ---------------- ITEM: produto selecionado ----------------
def set_produto_item(conn, item_id: str, produto_id):
    conn.execute("UPDATE item SET produto_id=? WHERE id=?", (produto_id, item_id))


# ---------------- EDITAL: serviço de frete ----------------
def set_frete_servico(conn, edital_id: str, servico: str):
    conn.execute("UPDATE edital SET frete_servico=? WHERE id=?", (servico, edital_id))


# ---------------- CUSTO (precificação por item) ----------------
def custo_upsert(conn, item_id: str, dados: dict):
    existe = conn.execute("SELECT 1 FROM custo WHERE item_id=?", (item_id,)).fetchone()
    if not existe:
        conn.execute("INSERT INTO custo (item_id) VALUES (?)", (item_id,))
    if dados:
        sets = ", ".join(f"{k}=?" for k in dados)
        conn.execute(f"UPDATE custo SET {sets}, atualizado_em=datetime('now') WHERE item_id=?",
                     list(dados.values()) + [item_id])


def custo_get(conn, item_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM custo WHERE item_id=?", (item_id,)).fetchone()
    return dict(r) if r else None


def sala_disputa(conn) -> list[dict]:
    """Itens em disputa/homologação + custo + estado ao vivo (para a Sala de Disputa)."""
    cur = conn.execute(
        """SELECT i.id AS item_id, i.edital_id AS compra, i.numero, i.descricao,
                  i.etapa, i.quantidade, i.produto_id,
                  c.valor_proposta_minima, c.valor_proposta_atual, c.lucro_proposta_minima,
                  c.custo_total_unitario, c.incremento, c.incremento_tipo,
                  d.situacao, d.fase, d.melhor_fornecedor, d.melhor_geral, d.lance_automatico
           FROM item i
           LEFT JOIN custo c ON c.item_id = i.id
           LEFT JOIN disputa d ON d.compra = i.edital_id AND d.numero = i.numero
           WHERE i.inativo=0 AND i.etapa IN ('disputa','homologacao')
           ORDER BY i.edital_id, i.numero""")
    return [dict(r) for r in cur.fetchall()]


# ---------------- VISÃO POR ETAPA (etapa no topo; editais filtrados dentro dela) ----------------
_BASE_ETAPA = ("FROM item i JOIN edital e ON e.id=i.edital_id "
               "LEFT JOIN uasg u ON u.codigo=e.uasg_codigo")


def _having_situacao(situacao: str) -> str:
    """HAVING para a agregação por edital, conforme o filtro principal de situação."""
    if situacao == "inativos":
        return "SUM(CASE WHEN i.inativo=1 THEN 1 ELSE 0 END) > 0"
    if situacao == "todos":
        return "COUNT(*) > 0"
    return "SUM(CASE WHEN i.inativo=0 THEN 1 ELSE 0 END) > 0"  # ativos (padrão)


def _pred_item_situacao(situacao: str):
    """Predicado de item (coluna inativo) conforme a situação, ou None para 'todos'."""
    if situacao == "inativos":
        return "inativo=1"
    if situacao == "todos":
        return None
    return "inativo=0"  # ativos (padrão)


def _filtros_etapa(q, uf, modalidade):
    cond, params = [], []
    if q:
        cond.append("(i.descricao LIKE ? OR i.descricao_detalhada LIKE ? OR e.descricao LIKE ? OR e.id LIKE ?)")
        params += [f"%{q}%"] * 4
    if modalidade:
        cond.append("e.modalidade=?")
        params.append(modalidade)
    if uf:
        cond.append("u.uf=?")
        params.append(uf)
    return cond, params


def contagem_etapas(conn, q=None, uf=None, modalidade=None) -> dict:
    """Contagem de itens ATIVOS por etapa, honrando os filtros — para as abas."""
    cond, params = _filtros_etapa(q, uf, modalidade)
    where = " AND ".join(["i.inativo=0"] + cond)
    cur = conn.execute(f"SELECT i.etapa, COUNT(*) n {_BASE_ETAPA} WHERE {where} GROUP BY i.etapa", params)
    return {r["etapa"]: r["n"] for r in cur.fetchall()}


def editais_de_etapa(conn, etapa, q=None, uf=None, modalidade=None,
                     situacao="ativos", limit=300, offset=0) -> list[dict]:
    """Editais da etapa (painel lateral) segundo a situação, com contagem de ativos e inativos."""
    cond, params = _filtros_etapa(q, uf, modalidade)
    cond = ["i.etapa=?"] + cond
    params = [etapa] + params
    where = " AND ".join(cond)
    cur = conn.execute(
        f"""SELECT e.*,
                   SUM(CASE WHEN i.inativo=0 THEN 1 ELSE 0 END) AS n_itens,
                   SUM(CASE WHEN i.inativo=1 THEN 1 ELSE 0 END) AS n_inativos
            {_BASE_ETAPA} WHERE {where}
            GROUP BY e.id HAVING {_having_situacao(situacao)}
            ORDER BY e.dt_abertura IS NULL, e.dt_abertura DESC LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    return [dict(r) for r in cur.fetchall()]


def contar_editais_de_etapa(conn, etapa, q=None, uf=None, modalidade=None, situacao="ativos") -> int:
    """Total de editais da etapa conforme a situação (para a paginação da lateral)."""
    cond, params = _filtros_etapa(q, uf, modalidade)
    cond = ["i.etapa=?"] + cond
    params = [etapa] + params
    where = " AND ".join(cond)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM (SELECT e.id {_BASE_ETAPA} WHERE {where} "
        f"GROUP BY e.id HAVING {_having_situacao(situacao)})",
        params,
    ).fetchone()
    return row["n"]


def itens_edital_etapa(conn, edital_id, etapa, situacao="ativos") -> list[dict]:
    """Itens de UM edital naquela etapa (detalhe) + custo (precificação), por situação."""
    cond = ["i.edital_id=?", "i.etapa=?"]
    params = [edital_id, etapa]
    pred = _pred_item_situacao(situacao)
    if pred:
        cond.append("i." + pred)
    cur = conn.execute(
        f"""SELECT i.*, c.valor_proposta_minima, c.valor_proposta_atual,
                   c.lucro_proposta_minima, c.custo_total_unitario, c.transportadora
            FROM item i LEFT JOIN custo c ON c.item_id = i.id
            WHERE {' AND '.join(cond)} ORDER BY i.inativo, i.numero""", params)
    return [dict(r) for r in cur.fetchall()]

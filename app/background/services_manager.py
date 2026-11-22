"""Gerente dos serviços de longa duração (start/stop manual e por cron).

Mantém instâncias em memória e reflete o estado na tabela `servico` (persistente),
para o painel e para retomar serviços após reinício.
"""
from .. import repository as repo
from ..db import get_conn
from .ws_comprasnet import WsComprasnet

# instâncias dos serviços conhecidos (nome -> objeto com iniciar/parar/status)
_SERVICOS = {
    WsComprasnet.nome: WsComprasnet(),
}


def _seed():
    """Garante uma linha em `servico` para cada serviço conhecido."""
    with get_conn() as conn:
        for nome in _SERVICOS:
            if repo.get_servico(conn, nome) is None:
                repo.upsert_servico(conn, nome, estado="parado", auto=0)


def iniciar(nome: str) -> dict:
    svc = _SERVICOS.get(nome)
    if svc is None:
        return {"erro": "servico_desconhecido"}
    try:
        svc.iniciar()
        with get_conn() as conn:
            repo.upsert_servico(conn, nome, estado="rodando", detalhe=None)
    except Exception as e:
        with get_conn() as conn:
            repo.upsert_servico(conn, nome, estado="erro", detalhe=str(e)[:500])
        return {"erro": str(e)}
    return status_de(nome)


def parar(nome: str) -> dict:
    svc = _SERVICOS.get(nome)
    if svc is None:
        return {"erro": "servico_desconhecido"}
    try:
        svc.parar()
    finally:
        with get_conn() as conn:
            repo.upsert_servico(conn, nome, estado="parado")
    return status_de(nome)


def status_de(nome: str) -> dict:
    svc = _SERVICOS.get(nome)
    with get_conn() as conn:
        row = repo.get_servico(conn, nome) or {"nome": nome}
    row["runtime"] = svc.status() if svc else {}
    return row


def listar() -> list[dict]:
    with get_conn() as conn:
        linhas = {r["nome"]: r for r in repo.listar_servicos(conn)}
    out = []
    for nome, svc in _SERVICOS.items():
        row = linhas.get(nome, {"nome": nome, "estado": "parado"})
        row["runtime"] = svc.status()
        out.append(row)
    return out


def iniciar_auto():
    """Retoma serviços marcados como rodando (ex.: após reinício do processo)."""
    _seed()
    with get_conn() as conn:
        linhas = repo.listar_servicos(conn)
    for row in linhas:
        if row.get("estado") == "rodando":
            iniciar(row["nome"])


def parar_todos():
    for nome in list(_SERVICOS):
        try:
            _SERVICOS[nome].parar()
        except Exception:
            pass

"""Orquestração (regras de negócio). A API chama estes serviços; eles coordenam
o cliente PNCP, o cliente ComprasNet e o repositório.
"""
from concurrent.futures import ThreadPoolExecutor

from . import repository as repo
from . import workflow
from .comprasnet import ComprasNet
from .config import config
from .db import get_conn
from .pncp import PncpClient


def ingestao(modalidades=None, data_publicacao_inicio=None, data_publicacao_fim="2099-12-31",
             reprocessar=False, com_itens=True, deve_cancelar=None) -> dict:
    """Varre o PNCP e persiste editais (+uasg) e, opcionalmente, os itens básicos.

    `deve_cancelar`: callable opcional; quando retorna True, interrompe em ponto seguro.
    O detalhamento SERPRO NÃO é feito aqui — só sob demanda (ver `detalhar`).
    """
    modalidades = modalidades or config.PNCP_MODALIDADES
    client = PncpClient(tam_pagina=config.PNCP_TAM_PAGINA)
    cancelar = deve_cancelar or (lambda: False)

    processados = novos = paginas_vistas = 0
    ids_novos = []
    cancelado = False

    for pagina, total_paginas, editais in client.buscar(
            modalidades, data_publicacao_inicio, data_publicacao_fim):
        if cancelar():
            cancelado = True
            break
        paginas_vistas = total_paginas
        if not editais:
            continue
        with get_conn() as conn:
            repo.upsert_uasgs(conn, (e["uasg"] for e in editais))
            ids_pagina = [e["id"] for e in editais]
            existentes = set() if reprocessar else repo.ids_editais_existentes(conn, ids_pagina)
            repo.upsert_editais(conn, editais, reprocessar=reprocessar)
        processados += len(editais)
        for e in editais:
            if e["id"] not in existentes:
                ids_novos.append(e["id"])
        novos = len(ids_novos)

    itens_baixados = 0
    if com_itens and not cancelado:
        alvo = ids_novos if not reprocessar else None
        itens_baixados = _baixar_itens_basicos(client, status="fila", ids=alvo, deve_cancelar=cancelar)

    return {
        "editais_processados": processados,
        "editais_novos": novos,
        "itens_baixados": itens_baixados,
        "paginas": paginas_vistas,
        "cancelado": cancelado,
    }


def _baixar_itens_basicos(client, status="fila", ids=None, deve_cancelar=None) -> int:
    cancelar = deve_cancelar or (lambda: False)
    with get_conn() as conn:
        pendentes = repo.editais_pendentes(conn, status=status, ids=ids)
    total_itens = 0
    for edital in pendentes:
        if cancelar():
            break
        situacao, itens = client.itens_edital(edital)
        with get_conn() as conn:
            if situacao == "ok":
                total_itens += repo.upsert_itens(conn, edital["id"], itens)
                repo.set_status_itens(conn, edital["id"], "completo")
            elif situacao == "nao_encontrado":
                repo.set_status_itens(conn, edital["id"], "nao_encontrado")
            # 'erro' fica em 'fila' para retry no próximo ciclo
    return total_itens


def detalhar(edital_id: str, numeros: list[int]) -> dict:
    """Detalha (SERPRO) apenas os itens selecionados de um edital.

    - Preenche o modo de disputa do edital (1x, se ainda não tiver).
    - Para cada número, busca o detalhamento e grava no item.
    Retorna {edital, itens} atualizados.
    """
    with get_conn() as conn:
        edital = repo.get_edital(conn, edital_id)
    if edital is None:
        return {"edital": None, "itens": []}

    cn = ComprasNet()

    # modo de disputa (nível edital) — só busca se ainda não temos
    if not edital.get("modo_disputa"):
        cod, desc = cn.modo_disputa(edital_id)
        if cod:
            with get_conn() as conn:
                repo.set_modo_disputa(conn, edital_id, cod, desc)
            edital["modo_disputa"], edital["modo_disputa_descricao"] = cod, desc

    # detalhamento dos itens selecionados (paralelo controlado)
    def _trab(numero):
        return numero, cn.detalhar_item(edital_id, numero)

    resultados = []
    with ThreadPoolExecutor(max_workers=config.DETALHE_WORKERS) as ex:
        for numero, detalhe in ex.map(_trab, numeros):
            resultados.append((numero, detalhe))

    with get_conn() as conn:
        for numero, detalhe in resultados:
            if detalhe:
                repo.update_detalhe(conn, edital_id, numero, detalhe)
            # detalhar = entrar na etapa "proposta"
            repo.mover_etapa(conn, f"{edital_id}-{numero}", workflow.ETAPA_DETALHAMENTO)

    # precificação: ao entrar em "proposta", calcula custos dos itens com produto selecionado
    from . import precificacao
    for numero in numeros:
        iid = f"{edital_id}-{numero}"
        with get_conn() as conn:
            it = repo.get_item(conn, iid)
        if it and it.get("produto_id"):
            precificacao.precificar(iid)

    with get_conn() as conn:
        itens = repo.listar_itens(conn, edital_id, numeros=numeros, incluir_inativos=True)
    return {"edital": edital, "itens": itens}


def mover_item(item_id: str, etapa: str) -> dict:
    """Move um item para outra etapa. Entrar em 'proposta' dispara o detalhamento."""
    if not workflow.etapa_valida(etapa):
        return {"erro": "etapa_invalida"}
    with get_conn() as conn:
        item = repo.get_item(conn, item_id)
    if item is None:
        return {"erro": "nao_encontrado"}

    if etapa == workflow.ETAPA_DETALHAMENTO:
        # reaproveita o fluxo de detalhamento (preenche dados SERPRO + seta etapa)
        res = detalhar(item["edital_id"], [item["numero"]])
        alvo = [i for i in res["itens"] if i["id"] == item_id]
        return {"item": alvo[0] if alvo else repo_get(item_id)}

    with get_conn() as conn:
        repo.mover_etapa(conn, item_id, etapa)
        return {"item": repo.get_item(conn, item_id)}


def inativar_item(item_id: str, inativo: bool = True) -> dict:
    """Inativa/reativa o item na etapa atual (mantém a etapa para histórico)."""
    with get_conn() as conn:
        if repo.get_item(conn, item_id) is None:
            return {"erro": "nao_encontrado"}
        repo.set_inativo(conn, item_id, inativo)
        return {"item": repo.get_item(conn, item_id)}


def repo_get(item_id: str):
    with get_conn() as conn:
        return repo.get_item(conn, item_id)

"""Precificação por item (fórmulas de item_atualizar.py do fastgov2).

Encadeia: custos do produto → frete (Frenet) → dados da disputa (já no item) →
custos totais (lance mínimo, proposta inicial, lucro). Dispara ao item entrar na
etapa "proposta" (ver app/services.py). Guarda tudo na tabela `custo`.
"""
from decimal import ROUND_HALF_UP, Decimal

from . import frete as frete_mod
from . import repository as repo
from .config import config
from .db import get_conn


def _D(v) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return Decimal(0)


def _money(v) -> float:
    return float(_D(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def precificar(item_id: str) -> dict:
    """Calcula e persiste o custo do item. Retorna o custo ou {mensagem: erro}."""
    with get_conn() as conn:
        item = repo.get_item(conn, item_id)
        if item is None:
            return {"mensagem": "erro", "erro": "item não encontrado"}
        if not item.get("produto_id"):
            return {"mensagem": "erro", "erro": "item sem produto selecionado"}
        produto = repo.get_produto(conn, item["produto_id"])
        edital = repo.get_edital(conn, item["edital_id"])
        uasg = repo.get_uasg(conn, edital["uasg_codigo"]) if edital else None
    if produto is None:
        return {"mensagem": "erro", "erro": "produto não encontrado"}

    compra = item["edital_id"]
    numero = item["numero"]
    quantidade = _D(item.get("quantidade") or 1)
    if quantidade <= 0:
        quantidade = Decimal(1)

    # 1) custos do produto
    custo_prod_unit = _D(produto.get("custo_produto_unitario"))
    custo_max_unit = _D(produto.get("custo_maximo_unitario"))
    margem = _D(produto.get("margem_min_lucro"))

    # 2) frete (Frenet); se indisponível, segue com 0 e registra transportadora vazia
    cep = frete_mod.resolver_cep_destino(uasg)
    frete_unit = Decimal(0)
    frete_total = Decimal(0)
    transportadora = None
    servico = (edital or {}).get("frete_servico") or "mais_barato"
    f = frete_mod.calcular_frete(produto, int(quantidade), cep, servico,
                                 valor_seguro=float(quantidade * custo_prod_unit))
    if f.get("mensagem") == "sucesso":
        frete_unit = _D(f["frete_unitario"])
        frete_total = _D(f["frete_total"])
        transportadora = f.get("transportadora")

    # 3) dados da disputa (já no item, do detalhamento)
    valor_estimado = _D(item.get("valor_estimado_unitario"))
    incremento = _D(item.get("intervalo_lance_valor"))
    incremento_tipo = item.get("intervalo_lance_tipo_codigo")  # P / V
    modo_disputa = (edital or {}).get("modo_disputa")

    # 4) custos totais (fórmulas do fastgov2)
    custo_total_unit = custo_prod_unit + frete_unit
    custo_total = custo_total_unit * quantidade
    valor_prop_min = custo_total_unit * (1 + margem * Decimal("0.01"))
    total_prop_min = valor_prop_min * quantidade
    lucro_prop_min = total_prop_min - custo_total

    if valor_prop_min < valor_estimado:
        valor_prop_atual = valor_estimado
    else:
        valor_prop_atual = valor_prop_min
    if valor_estimado == 0:
        valor_prop_atual = valor_prop_min * Decimal("2")
    if modo_disputa == "AF":
        valor_prop_atual = valor_prop_min * Decimal("1.1")

    # piso de lucro absoluto (R$ LUCRO_MINIMO_VENDA)
    lucro_minimo = _D(config.LUCRO_MINIMO_VENDA)
    if lucro_prop_min < lucro_minimo:
        lucro_unit = lucro_minimo / quantidade
        lance_min = custo_total_unit + lucro_unit
        valor_prop_min = lance_min
        lucro_prop_min = (lance_min - custo_total_unit) * quantidade
        total_prop_min = valor_prop_min * quantidade
        if valor_prop_atual < lance_min:
            valor_prop_atual = lance_min

    dados = {
        "compra": compra, "numero": numero, "produto_id": item["produto_id"],
        "quantidade": float(quantidade),
        "custo_produto_unitario": _money(custo_prod_unit),
        "custo_maximo_unitario": _money(custo_max_unit),
        "custo_produto": _money(custo_prod_unit * quantidade),
        "margem_minima": float(margem),
        "frete_unitario": _money(frete_unit), "frete_total": _money(frete_total),
        "transportadora": transportadora,
        "custo_total_unitario": _money(custo_total_unit), "custo_total": _money(custo_total),
        "valor_proposta_minima": _money(valor_prop_min),
        "total_proposta_minima": _money(total_prop_min),
        "lucro_proposta_minima": _money(lucro_prop_min),
        "valor_proposta_atual": _money(valor_prop_atual),
        "valor_estimado_edital": _money(valor_estimado),
        "incremento": float(incremento), "incremento_tipo": incremento_tipo,
        "modo_disputa": modo_disputa,
    }
    with get_conn() as conn:
        repo.custo_upsert(conn, item_id, dados)
        # sincroniza o piso do auto-lance
        repo.disputa_upsert(conn, compra, numero, valor_minimo=dados["valor_proposta_minima"])
        custo = repo.custo_get(conn, item_id)
    return {"mensagem": "sucesso", "custo": custo}


def proximo_lance(custo: dict, melhor_geral) -> float | None:
    """Próximo lance = melhor_geral − incremento (V) ou ×(1−inc%) (P); piso = lance mínimo."""
    if melhor_geral is None:
        return None
    melhor = _D(melhor_geral)
    inc = _D(custo.get("incremento"))
    tipo = custo.get("incremento_tipo")
    if tipo == "P":
        prox = melhor * (1 - inc / Decimal("100"))
    else:  # V (valor) ou desconhecido
        prox = melhor - inc
    piso = _D(custo.get("valor_proposta_minima"))
    if piso > 0 and prox < piso:
        return None
    return _money(prox)

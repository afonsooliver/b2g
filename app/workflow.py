"""Workflow de participação em licitações: as etapas por item e suas regras.

Fluxo: selecao -> proposta (detalhamento) -> disputa -> homologacao -> homologado.
Um item pode ser excluído (soft-delete) em qualquer etapa.
"""

# ordem importa (define "avançar"/"voltar" e a ordenação do painel)
ETAPAS = ["selecao", "proposta", "disputa", "homologacao", "homologado"]

ETAPA_LABEL = {
    "selecao": "Seleção",
    "proposta": "Proposta",
    "disputa": "Disputa",
    "homologacao": "Homologação",
    "homologado": "Homologado",
}

# Etapa em que o detalhamento (SERPRO) é disparado.
ETAPA_DETALHAMENTO = "proposta"


def etapa_valida(etapa: str) -> bool:
    return etapa in ETAPAS


def indice(etapa: str) -> int:
    return ETAPAS.index(etapa) if etapa in ETAPAS else -1

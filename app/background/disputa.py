"""Disputa: enviar lance (REST, assíncrono) e reagir às mensagens do WS.

Adaptado de disputa.py (referência): os lances vão por REST
(POST /comprasnet-disputa/v1/.../lances), e a reação vem da mensagem do WS —
se `situacaoParticipanteDisputa == "P"` (perdendo) e o auto-lance estiver ligado
para o item, envia o próximo lance, respeitando o piso (`valor_minimo`).
"""
import threading
from decimal import ROUND_HALF_UP, Decimal

from .. import auth
from .. import repository as repo
from ..config import config
from ..db import get_conn

BASE = config.COMPRASNET_BASE.rstrip("/")


def _2casas(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _val(dados: dict, chave: str):
    o = dados.get(chave) or {}
    return o.get("valorCalculado") if isinstance(o, dict) else None


def _salvar_estado(compra: str, numero: int, dados: dict):
    melhor_geral = _val(dados, "melhorValorGeral")
    with get_conn() as conn:
        repo.disputa_upsert(
            conn, compra, numero,
            situacao=dados.get("situacaoParticipanteDisputa"),
            fase=dados.get("fase"),
            melhor_fornecedor=_val(dados, "melhorValorFornecedor"),
            melhor_geral=melhor_geral,
        )
        if repo.custo_get(conn, f"{compra}-{numero}"):
            repo.custo_upsert(conn, f"{compra}-{numero}",
                              {"melhor_proposta_global": melhor_geral})


# ---------- enviar lance (REST) ----------
def enviar_lance(compra: str, numero: int, valor, fase: str = "LA") -> dict:
    url = f"{BASE}/comprasnet-disputa/v1/compras/{compra}/itens/{numero}/lances"
    payload = {"valorInformado": str(_2casas(valor)), "faseItem": fase}
    try:
        r = auth.request_autenticado(url, method="POST", json=payload)
        dados = r.json()
    except Exception as e:
        return {"mensagem": "erro", "erro": str(e)}
    if isinstance(dados, dict):
        _salvar_estado(compra, numero, dados)
    return {"mensagem": "sucesso", "status": r.status_code, "dados": dados}


def enviar_lance_async(compra: str, numero: int, valor, fase: str = "LA"):
    """Dispara o lance em thread própria (não bloqueia o loop de mensagens do WS)."""
    threading.Thread(target=enviar_lance, args=(compra, numero, valor, fase),
                     daemon=True, name=f"lance-{compra}-{numero}").start()


# ---------- ler mensagem e agir ----------
def _compra_do_destino(destino: str):
    if "fornec/" in destino:                       # /topic/disputa/fornec/{compra}/{fornecedor}
        return destino.split("fornec/")[-1].split("/")[0]
    if "/compra/" in destino:                      # /topic/compra/{compra}
        return destino.rstrip("/").split("/compra/")[-1].split("/")[0]
    return None


def tratar_mensagem(cabecalho: dict, mensagem: dict):
    """Entrada do WS: atualiza o estado e, se perdendo + auto-lance, envia próximo lance."""
    if not isinstance(mensagem, dict):
        return
    numero = mensagem.get("numero")
    if numero is None:
        return
    compra = _compra_do_destino(cabecalho.get("destination", ""))
    if not compra:
        return
    _salvar_estado(compra, numero, mensagem)

    if mensagem.get("situacaoParticipanteDisputa") != "P":   # P = perdendo
        return
    with get_conn() as conn:
        d = repo.disputa_get(conn, compra, numero)
        custo = repo.custo_get(conn, f"{compra}-{numero}")
    if not d or not d.get("lance_automatico") or not custo:
        return
    from .. import precificacao
    proximo = precificacao.proximo_lance(custo, _val(mensagem, "melhorValorGeral"))
    if proximo is not None:
        enviar_lance_async(compra, numero, proximo)

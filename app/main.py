"""API REST (FastAPI). Camada fina: valida entrada, chama serviços/repositório,
devolve os schemas. Sem regra de negócio aqui.
"""
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from . import background
from . import precificacao
from . import repository as repo
from . import services
from . import workflow
from .background import logbuf, scheduler, services_manager
from .background.registry import listar as listar_tarefas
from .config import config
from .db import get_conn, init_db
from .schemas import (AgendamentoIn, AgendamentoPatch, CatalogoIn, DetalharRequest,
                      DisputaConfig, EditaisPagina, EditalComItens, EditalOut,
                      EditalResumoEtapa, EtapaResumo, ExecutarRequest,
                      FreteServicoReq, IngestRequest, IngestResult, ItemOut,
                      ModoDebugRequest, MoverEtapaRequest, PainelOut,
                      ProdutoItemReq, ProdutoIn, ProdutoPatch, ServicoPatch,
                      TokenInicialRequest)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    background.iniciar_tudo()
    try:
        yield
    finally:
        background.parar_tudo()


def _valida_etapa(etapa: str) -> None:
    if not workflow.etapa_valida(etapa):
        raise HTTPException(404, f"Etapa inválida. Use: {workflow.ETAPAS}")


app = FastAPI(
    title="FastGov — Extração PNCP + Detalhamento ComprasNet",
    description="Ingestão pública de compras (PNCP) e detalhamento autenticado "
                "(ComprasNet/SERPRO) sob demanda, apenas para itens selecionados.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def raiz():
    return RedirectResponse("/ui/")


class NoCacheStatic(StaticFiles):
    """StaticFiles que força revalidação (no-cache) — evita o navegador servir
    JS/CSS antigos sem checar o servidor. O ETag/Last-Modified ainda permite 304."""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


# Frontend estático (servido na mesma origem da API — sem CORS, sem build).
_WEB = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_WEB):
    app.mount("/ui", NoCacheStatic(directory=_WEB, html=True), name="ui")

# Imagens de produto (disco/volume).
_UPLOAD = os.path.abspath(config.UPLOAD_DIR)
os.makedirs(_UPLOAD, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_UPLOAD), name="uploads")


# ---- Autenticador / provedor de token ----
@app.get("/token/", tags=["auth"])
@app.get("/token", include_in_schema=False)
def emitir_token(tipo: str = "token"):
    """Expõe o token vigente (o app pode ser provedor, como o fastgov2)."""
    return {"token": auth.obter_token()}


@app.get("/admin/modo", tags=["admin"])
def admin_modo():
    """Modo atual do autenticador (debug/produção) + presença de token."""
    return auth.status()


@app.post("/admin/modo-debug", tags=["admin"])
def admin_set_modo_debug(req: ModoDebugRequest):
    """Seleciona o modo debug (em debug o token vem do servidor remoto fastgov2)."""
    auth.set_modo_debug(req.debug)
    return auth.status()


@app.get("/admin/auth", tags=["admin"])
def admin_auth_status():
    """Checa a sessão no ComprasNet (GET /comprasnet-usuario/v1/usuario)."""
    return auth.info_usuario()


@app.post("/admin/auth/refresh", tags=["admin"])
def admin_auth_refresh():
    return auth.refresh_token()


@app.post("/admin/auth/token-inicial", tags=["admin"])
def admin_token_inicial(req: TokenInicialRequest):
    return auth.token_inicial(req.id)


# ---- Operações: status, tarefas, execuções, agendamentos, serviços ----
@app.get("/admin/status", tags=["admin"])
def admin_status():
    g = background.status_geral()
    with get_conn() as conn:
        execucoes = repo.listar_execucoes(conn, limit=10)
    return {**g, "autenticador": auth.status(), "execucoes_recentes": execucoes}


@app.get("/admin/tarefas", tags=["admin"])
def admin_tarefas():
    return listar_tarefas()


@app.get("/admin/execucoes", tags=["admin"])
def admin_execucoes(status: str | None = None,
                    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    with get_conn() as conn:
        return repo.listar_execucoes(conn, status=status, limit=limit, offset=offset)


@app.post("/admin/executar", tags=["admin"])
def admin_executar(req: ExecutarRequest):
    """Enfileira uma execução ad-hoc de uma tarefa do registry."""
    from .background import registry
    if not registry.existe(req.tarefa):
        raise HTTPException(422, f"Tarefa inválida. Use: {[t['tarefa'] for t in listar_tarefas()]}")
    with get_conn() as conn:
        eid = repo.enfileirar(conn, req.tarefa, req.params, origem="manual")
    return {"id": eid, "status": "fila"}


@app.post("/admin/execucoes/{exec_id}/cancelar", tags=["admin"])
def admin_cancelar_execucao(exec_id: int):
    """Cancela execução em 'fila'; se estiver 'rodando', pede cancelamento cooperativo."""
    with get_conn() as conn:
        n = repo.cancelar_execucao(conn, exec_id)
    if n:
        return {"id": exec_id, "status": "cancelado"}
    background.worker_pool.solicitar_cancelamento(exec_id)
    return {"id": exec_id, "status": "cancelamento_solicitado"}


@app.post("/admin/execucoes/cancelar-tudo", tags=["admin"])
def admin_cancelar_tudo():
    """Cancela toda a fila e pede cancelamento cooperativo de tudo que está rodando."""
    with get_conn() as conn:
        n_fila = repo.cancelar_todas_fila(conn)
        rodando = repo.execucoes_rodando_ids(conn)
    for eid in rodando:
        background.worker_pool.solicitar_cancelamento(eid)
    return {"fila_cancelada": n_fila, "rodando_sinalizado": len(rodando)}


# ---- Logs e controles de operação ----
@app.get("/admin/logs", tags=["admin"])
def admin_logs(origem: str | None = None, nivel: str | None = None,
               limit: int = Query(200, ge=1, le=1000), desde: int = 0):
    return {"origens": logbuf.origens(), "logs": logbuf.listar(origem, nivel, limit, desde)}


@app.post("/admin/workers/reiniciar", tags=["admin"])
def admin_reiniciar_workers():
    background.reiniciar_workers()
    return background.worker_pool.status()


@app.post("/admin/scheduler/reiniciar", tags=["admin"])
def admin_reiniciar_scheduler():
    background.reiniciar_scheduler()
    return {"ok": True, "proximos": scheduler.proximos()}


@app.post("/admin/reiniciar", tags=["admin"])
def admin_reiniciar_tudo():
    background.reiniciar_tudo()
    return {"ok": True}


# ---- Disputa (lances e auto-lance) ----
@app.get("/admin/disputa", tags=["admin"])
def admin_disputa():
    with get_conn() as conn:
        return repo.disputa_listar(conn)


@app.get("/admin/sala-disputa", tags=["admin"])
def admin_sala_disputa():
    """Itens em disputa/homologação + custo + estado ao vivo + status do WS."""
    with get_conn() as conn:
        itens = repo.sala_disputa(conn)
    ws = services_manager.status_de("ws_comprasnet")
    return {"itens": itens, "ws": ws}


@app.patch("/admin/disputa/{compra}/{numero}", tags=["admin"])
def admin_disputa_config(compra: str, numero: int, req: DisputaConfig):
    """Configura o auto-lance do item (ligar/desligar, piso e passo)."""
    campos = {}
    if req.lance_automatico is not None:
        campos["lance_automatico"] = 1 if req.lance_automatico else 0
    if req.valor_minimo is not None:
        campos["valor_minimo"] = req.valor_minimo
    if req.passo is not None:
        campos["passo"] = req.passo
    with get_conn() as conn:
        repo.disputa_upsert(conn, compra, numero, **campos)
        return repo.disputa_get(conn, compra, numero)


# ---- Catálogos ----
@app.get("/catalogos", tags=["catalogos"])
def listar_catalogos():
    with get_conn() as conn:
        return repo.listar_catalogos(conn)


@app.post("/catalogos", tags=["catalogos"])
def criar_catalogo(req: CatalogoIn):
    with get_conn() as conn:
        cid = repo.criar_catalogo(conn, req.nome, req.descricao)
        return repo.get_catalogo(conn, cid)


@app.patch("/catalogos/{cid}", tags=["catalogos"])
def atualizar_catalogo(cid: int, req: CatalogoIn):
    with get_conn() as conn:
        if repo.get_catalogo(conn, cid) is None:
            raise HTTPException(404, "Catálogo não encontrado")
        repo.atualizar_catalogo(conn, cid, req.model_dump(exclude_none=True))
        return repo.get_catalogo(conn, cid)


@app.delete("/catalogos/{cid}", tags=["catalogos"])
def remover_catalogo(cid: int):
    with get_conn() as conn:
        repo.remover_catalogo(conn, cid)
    return {"ok": True}


# ---- Produtos ----
@app.get("/produtos", tags=["produtos"])
def listar_produtos(catalogo_id: int | None = None, q: str | None = None,
                    limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with get_conn() as conn:
        return repo.listar_produtos(conn, catalogo_id=catalogo_id, q=q, limit=limit, offset=offset)


@app.get("/produtos/{pid}", tags=["produtos"])
def obter_produto(pid: int):
    with get_conn() as conn:
        p = repo.get_produto(conn, pid)
        if p is None:
            raise HTTPException(404, "Produto não encontrado")
        return p


@app.post("/produtos", tags=["produtos"])
def criar_produto(req: ProdutoIn):
    with get_conn() as conn:
        pid = repo.criar_produto(conn, req.model_dump(exclude_none=True))
        return repo.get_produto(conn, pid)


@app.patch("/produtos/{pid}", tags=["produtos"])
def atualizar_produto(pid: int, req: ProdutoPatch):
    with get_conn() as conn:
        if repo.get_produto(conn, pid) is None:
            raise HTTPException(404, "Produto não encontrado")
        repo.atualizar_produto(conn, pid, req.model_dump(exclude_none=True))
        return repo.get_produto(conn, pid)


@app.delete("/produtos/{pid}", tags=["produtos"])
def remover_produto(pid: int):
    with get_conn() as conn:
        repo.remover_produto(conn, pid)
    return {"ok": True}


@app.post("/produtos/{pid}/imagem", tags=["produtos"])
async def upload_imagem(pid: int, arquivo: UploadFile = File(...)):
    with get_conn() as conn:
        if repo.get_produto(conn, pid) is None:
            raise HTTPException(404, "Produto não encontrado")
    ext = os.path.splitext(arquivo.filename or "")[1].lower() or ".jpg"
    nome = f"produto_{pid}{ext}"
    with open(os.path.join(_UPLOAD, nome), "wb") as f:
        f.write(await arquivo.read())
    with get_conn() as conn:
        repo.atualizar_produto(conn, pid, {"imagem_path": f"/uploads/{nome}"})
        return repo.get_produto(conn, pid)


# ---- Seleção de produto por item + precificação ----
@app.patch("/itens/{item_id}/produto", response_model=ItemOut, tags=["itens"])
def selecionar_produto(item_id: str, req: ProdutoItemReq):
    with get_conn() as conn:
        item = repo.get_item(conn, item_id)
        if item is None:
            raise HTTPException(404, "Item não encontrado")
        if req.produto_id is not None and repo.get_produto(conn, req.produto_id) is None:
            raise HTTPException(422, "Produto inválido")
        repo.set_produto_item(conn, item_id, req.produto_id)
    # se o item já passou de seleção e tem produto, recalcula o custo
    if req.produto_id and item.get("etapa") in ("proposta", "disputa", "homologacao", "homologado"):
        precificacao.precificar(item_id)
    with get_conn() as conn:
        return repo.get_item(conn, item_id)


@app.get("/itens/{item_id}/precificacao", tags=["itens"])
def obter_precificacao(item_id: str):
    with get_conn() as conn:
        return repo.custo_get(conn, item_id) or {}


@app.post("/itens/{item_id}/precificar", tags=["itens"])
def recalcular_precificacao(item_id: str):
    return precificacao.precificar(item_id)


@app.patch("/compras/{edital_id}/frete-servico", response_model=EditalOut, tags=["compras"])
def set_frete_servico(edital_id: str, req: FreteServicoReq):
    with get_conn() as conn:
        if repo.get_edital(conn, edital_id) is None:
            raise HTTPException(404, "Edital não encontrado")
        repo.set_frete_servico(conn, edital_id, req.frete_servico)
        return repo.get_edital(conn, edital_id)


@app.get("/admin/agendamentos", tags=["admin"])
def admin_listar_agendamentos():
    with get_conn() as conn:
        return repo.listar_agendamentos(conn)


@app.post("/admin/agendamentos", tags=["admin"])
def admin_criar_agendamento(req: AgendamentoIn):
    from .background import registry
    if not registry.existe(req.tarefa):
        raise HTTPException(422, "Tarefa inválida")
    with get_conn() as conn:
        ag_id = repo.criar_agendamento(conn, req.nome, req.tarefa, req.cron, req.params, req.ativo)
    scheduler.recarregar_agendamento(ag_id)
    with get_conn() as conn:
        return repo.get_agendamento(conn, ag_id)


@app.patch("/admin/agendamentos/{ag_id}", tags=["admin"])
def admin_patch_agendamento(ag_id: int, req: AgendamentoPatch):
    campos = {}
    if req.nome is not None:
        campos["nome"] = req.nome
    if req.cron is not None:
        campos["cron"] = req.cron
    if req.params is not None:
        import json
        campos["params_json"] = json.dumps(req.params, ensure_ascii=False)
    if req.ativo is not None:
        campos["ativo"] = 1 if req.ativo else 0
    with get_conn() as conn:
        if repo.get_agendamento(conn, ag_id) is None:
            raise HTTPException(404, "Agendamento não encontrado")
        repo.atualizar_agendamento(conn, ag_id, campos)
    scheduler.recarregar_agendamento(ag_id)
    with get_conn() as conn:
        return repo.get_agendamento(conn, ag_id)


@app.delete("/admin/agendamentos/{ag_id}", tags=["admin"])
def admin_remover_agendamento(ag_id: int):
    with get_conn() as conn:
        repo.remover_agendamento(conn, ag_id)
    scheduler.recarregar_agendamento(ag_id)
    return {"ok": True}


@app.post("/admin/agendamentos/{ag_id}/executar-agora", tags=["admin"])
def admin_executar_agora(ag_id: int):
    import json
    with get_conn() as conn:
        ag = repo.get_agendamento(conn, ag_id)
        if ag is None:
            raise HTTPException(404, "Agendamento não encontrado")
        params = json.loads(ag.get("params_json") or "{}")
        eid = repo.enfileirar(conn, ag["tarefa"], params, origem="manual", agendamento_id=ag_id)
    return {"id": eid, "status": "fila"}


@app.get("/admin/servicos", tags=["admin"])
def admin_servicos():
    return services_manager.listar()


@app.post("/admin/servicos/{nome}/iniciar", tags=["admin"])
def admin_servico_iniciar(nome: str):
    r = services_manager.iniciar(nome)
    if r.get("erro") == "servico_desconhecido":
        raise HTTPException(404, "Serviço desconhecido")
    return r


@app.post("/admin/servicos/{nome}/parar", tags=["admin"])
def admin_servico_parar(nome: str):
    r = services_manager.parar(nome)
    if r.get("erro") == "servico_desconhecido":
        raise HTTPException(404, "Serviço desconhecido")
    return r


@app.patch("/admin/servicos/{nome}", tags=["admin"])
def admin_servico_patch(nome: str, req: ServicoPatch):
    campos = {}
    if req.auto is not None:
        campos["auto"] = 1 if req.auto else 0
    if req.cron_inicio is not None:
        campos["cron_inicio"] = req.cron_inicio
    if req.cron_fim is not None:
        campos["cron_fim"] = req.cron_fim
    with get_conn() as conn:
        if repo.get_servico(conn, nome) is None:
            raise HTTPException(404, "Serviço desconhecido")
        repo.upsert_servico(conn, nome, **campos)
    scheduler.recarregar_servico(nome)
    return services_manager.status_de(nome)


@app.get("/etapas", response_model=EtapaResumo, tags=["etapas"])
def resumo_etapas(q: str | None = None, uf: str | None = None, modalidade: str | None = None):
    """Contagem de itens ativos por etapa (para a navegação superior)."""
    with get_conn() as conn:
        por = repo.contagem_etapas(conn, q=q, uf=uf, modalidade=modalidade)
        return {"por_etapa": {e: por.get(e, 0) for e in workflow.ETAPAS}}


Situacao = Literal["ativos", "inativos", "todos"]


@app.get("/etapas/{etapa}/editais", response_model=EditaisPagina, tags=["etapas"])
def etapa_editais(etapa: str, q: str | None = None, uf: str | None = None,
                  modalidade: str | None = None, situacao: Situacao = "ativos",
                  limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """Painel lateral paginado: editais da etapa conforme a situação (+ contagem ativos/inativos)."""
    _valida_etapa(etapa)
    with get_conn() as conn:
        editais = repo.editais_de_etapa(conn, etapa, q=q, uf=uf, modalidade=modalidade,
                                        situacao=situacao, limit=limit, offset=offset)
        total = repo.contar_editais_de_etapa(conn, etapa, q=q, uf=uf, modalidade=modalidade,
                                             situacao=situacao)
    return {"total": total, "limit": limit, "offset": offset, "editais": editais}


@app.get("/etapas/{etapa}/compras/{edital_id}/itens", response_model=list[ItemOut], tags=["etapas"])
def etapa_edital_itens(etapa: str, edital_id: str, situacao: Situacao = "ativos"):
    """Detalhe: itens de UM edital naquela etapa, conforme a situação (ativos/inativos/todos)."""
    _valida_etapa(etapa)
    with get_conn() as conn:
        if repo.get_edital(conn, edital_id) is None:
            raise HTTPException(404, "Edital não encontrado")
        return repo.itens_edital_etapa(conn, edital_id, etapa, situacao=situacao)


@app.post("/ingest", response_model=IngestResult, tags=["ingestão"])
def ingest(req: IngestRequest):
    """Varre o PNCP e persiste editais + itens básicos (sem detalhamento SERPRO)."""
    resultado = services.ingestao(
        modalidades=req.modalidades,
        data_publicacao_inicio=req.data_publicacao_inicio,
        data_publicacao_fim=req.data_publicacao_fim,
        reprocessar=req.reprocessar,
        com_itens=req.com_itens,
    )
    return resultado


@app.get("/compras", response_model=list[EditalOut], tags=["compras"])
def listar_compras(
    uf: str | None = None,
    modalidade: str | None = None,
    uasg: str | None = None,
    q: str | None = Query(None, description="Busca no texto da descrição"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    with get_conn() as conn:
        return repo.listar_editais(conn, uf=uf, modalidade=modalidade,
                                   uasg=uasg, q=q, limit=limit, offset=offset)


@app.get("/compras/{edital_id}", response_model=EditalComItens, tags=["compras"])
def obter_compra(edital_id: str):
    with get_conn() as conn:
        edital = repo.get_edital(conn, edital_id)
        if edital is None:
            raise HTTPException(404, "Edital não encontrado")
        edital["itens"] = repo.listar_itens(conn, edital_id)
        return edital


@app.get("/compras/{edital_id}/itens", response_model=list[ItemOut], tags=["compras"])
def listar_itens(edital_id: str, etapa: str | None = None, incluir_inativos: bool = False):
    with get_conn() as conn:
        if repo.get_edital(conn, edital_id) is None:
            raise HTTPException(404, "Edital não encontrado")
        return repo.listar_itens(conn, edital_id, etapa=etapa,
                                 incluir_inativos=incluir_inativos)


@app.get("/compras/{edital_id}/painel", response_model=PainelOut, tags=["painel"])
def painel(edital_id: str):
    """Painel do edital: itens agrupados por etapa + contagens."""
    with get_conn() as conn:
        edital = repo.get_edital(conn, edital_id)
        if edital is None:
            raise HTTPException(404, "Edital não encontrado")
        itens = repo.listar_itens(conn, edital_id)
        resumo = repo.painel_edital(conn, edital_id)
    edital["itens"] = itens
    edital["total_itens"] = len(itens)
    edital["inativos"] = resumo["inativos"]
    # garante todas as etapas no dicionário (zeradas), na ordem do fluxo
    edital["por_etapa"] = {e: resumo["por_etapa"].get(e, 0) for e in workflow.ETAPAS}
    return edital


@app.post("/compras/{edital_id}/detalhar", response_model=EditalComItens, tags=["detalhamento"])
def detalhar_itens(edital_id: str, req: DetalharRequest):
    """Detalhamento SERPRO SOB DEMANDA: só os itens em `numeros`."""
    res = services.detalhar(edital_id, req.numeros)
    if res["edital"] is None:
        raise HTTPException(404, "Edital não encontrado")
    edital = dict(res["edital"])
    edital["itens"] = res["itens"]
    return edital


@app.get("/itens", response_model=list[ItemOut], tags=["itens"])
def buscar_itens(
    q: str | None = Query(None, description="Busca em descrição / descrição detalhada"),
    etapa: str | None = None,
    edital_id: str | None = None,
    uf: str | None = None,
    material_ou_servico: str | None = Query(None, description="M | S"),
    incluir_inativos: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Busca itens entre todos os editais (filtros + paginação)."""
    with get_conn() as conn:
        return repo.buscar_itens(conn, q=q, etapa=etapa, edital_id=edital_id, uf=uf,
                                 material_ou_servico=material_ou_servico,
                                 incluir_inativos=incluir_inativos,
                                 limit=limit, offset=offset)


@app.get("/itens/{item_id}", response_model=ItemOut, tags=["itens"])
def obter_item(item_id: str):
    with get_conn() as conn:
        item = repo.get_item(conn, item_id)
        if item is None:
            raise HTTPException(404, "Item não encontrado")
        return item


@app.patch("/itens/{item_id}/etapa", response_model=ItemOut, tags=["workflow"])
def mover_etapa(item_id: str, req: MoverEtapaRequest):
    """Move o item de etapa. Entrar em 'proposta' dispara o detalhamento SERPRO."""
    res = services.mover_item(item_id, req.etapa)
    if res.get("erro") == "etapa_invalida":
        raise HTTPException(422, f"Etapa inválida. Use uma de: {workflow.ETAPAS}")
    if res.get("erro") == "nao_encontrado":
        raise HTTPException(404, "Item não encontrado")
    return res["item"]


@app.post("/itens/{item_id}/inativar", response_model=ItemOut, tags=["workflow"])
def inativar_item(item_id: str):
    """Inativa o item NA ETAPA atual (mantém a etapa — histórico)."""
    res = services.inativar_item(item_id, inativo=True)
    if res.get("erro") == "nao_encontrado":
        raise HTTPException(404, "Item não encontrado")
    return res["item"]


@app.post("/itens/{item_id}/reativar", response_model=ItemOut, tags=["workflow"])
def reativar_item(item_id: str):
    res = services.inativar_item(item_id, inativo=False)
    if res.get("erro") == "nao_encontrado":
        raise HTTPException(404, "Item não encontrado")
    return res["item"]

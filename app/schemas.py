"""Contratos da API (entrada/saída) em pydantic v2."""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


# --------- Entrada ---------
class IngestRequest(BaseModel):
    modalidades: Optional[list[int]] = None  # None => usa PNCP_MODALIDADES do config
    data_publicacao_inicio: str = Field(default_factory=lambda: date.today().isoformat())
    data_publicacao_fim: str = "2099-12-31"
    reprocessar: bool = False
    com_itens: bool = True  # baixa os itens básicos (PNCP) dos editais novos


class DetalharRequest(BaseModel):
    numeros: list[int] = Field(..., min_length=1, description="Números dos itens a detalhar")


class MoverEtapaRequest(BaseModel):
    etapa: str = Field(..., description="selecao | proposta | disputa | homologacao | homologado")


# --------- Saída ---------
class ItemOut(BaseModel):
    id: str
    edital_id: str
    numero: Optional[int] = None
    descricao: Optional[str] = None
    material_ou_servico: Optional[str] = None
    quantidade: Optional[float] = None
    unidade: Optional[str] = None
    valor_unitario_estimado_edital: Optional[float] = None
    # detalhamento (NULL enquanto não detalhado)
    descricao_detalhada: Optional[str] = None
    item_ou_grupo: Optional[str] = None
    tipo_fe: Optional[str] = None
    valor_estimado_unitario: Optional[float] = None
    valor_estimado_total: Optional[float] = None
    intervalo_lance_tipo: Optional[str] = None
    intervalo_lance_tipo_codigo: Optional[str] = None
    intervalo_lance_valor: Optional[float] = None
    detalhado_em: Optional[str] = None
    # workflow
    etapa: Optional[str] = None
    inativo: Optional[int] = None          # inativo NA ETAPA (mantém etapa p/ histórico)
    etapa_em: Optional[str] = None
    inativado_em: Optional[str] = None
    produto_id: Optional[int] = None
    # custo (precificação)
    custo_total_unitario: Optional[float] = None
    valor_proposta_minima: Optional[float] = None
    valor_proposta_atual: Optional[float] = None
    lucro_proposta_minima: Optional[float] = None
    transportadora: Optional[str] = None


class EditalOut(BaseModel):
    id: str
    uasg_codigo: Optional[str] = None
    numero_pncp: Optional[str] = None
    ano_compra: Optional[int] = None
    numero: Optional[str] = None
    descricao: Optional[str] = None
    modalidade: Optional[str] = None
    modo_disputa: Optional[str] = None
    modo_disputa_descricao: Optional[str] = None
    dt_abertura: Optional[str] = None
    url_edital: Optional[str] = None
    status_itens: Optional[str] = None


class EditalComItens(EditalOut):
    itens: list[ItemOut] = []


class PainelOut(EditalOut):
    total_itens: int               # itens ativos
    inativos: int
    por_etapa: dict[str, int]      # {"selecao": n, "proposta": n, ...}
    itens: list[ItemOut] = []      # itens ativos (o front agrupa por etapa)


class EditalResumoEtapa(EditalOut):
    """Edital no painel lateral de uma etapa: contagem de itens ativos e inativos."""
    n_itens: int = 0               # ativos na etapa
    n_inativos: int = 0            # inativos na etapa (histórico)


class EditaisPagina(BaseModel):
    """Página da lista de editais de uma etapa (painel lateral paginado)."""
    total: int
    limit: int
    offset: int
    editais: list[EditalResumoEtapa] = []


class EtapaResumo(BaseModel):
    por_etapa: dict[str, int]      # itens ATIVOS por etapa: {"selecao": n, ...}


class IngestResult(BaseModel):
    editais_processados: int
    editais_novos: int
    itens_baixados: int
    paginas: int


class ModoDebugRequest(BaseModel):
    debug: bool


class TokenInicialRequest(BaseModel):
    id: str = Field(..., description="Id da sessão do fornecedor (semente do tokenInicial)")


# ---- Operações (background) ----
class AgendamentoIn(BaseModel):
    nome: Optional[str] = None
    tarefa: str
    cron: str = Field(..., description="Expressão crontab de 5 campos (min hora dia mês diasem)")
    params: dict = {}
    ativo: bool = True


class AgendamentoPatch(BaseModel):
    nome: Optional[str] = None
    cron: Optional[str] = None
    params: Optional[dict] = None
    ativo: Optional[bool] = None


class ExecutarRequest(BaseModel):
    tarefa: str
    params: dict = {}


class ServicoPatch(BaseModel):
    auto: Optional[bool] = None
    cron_inicio: Optional[str] = None
    cron_fim: Optional[str] = None


class LanceRequest(BaseModel):
    compra: str
    numero: int
    valor: float
    fase: str = "LA"


class DisputaConfig(BaseModel):
    lance_automatico: Optional[bool] = None
    valor_minimo: Optional[float] = None
    passo: Optional[float] = None


# ---- Catálogos & Produtos ----
class CatalogoIn(BaseModel):
    nome: str
    descricao: Optional[str] = None


class ProdutoIn(BaseModel):
    nome: str
    catalogo_id: Optional[int] = None
    ncm: Optional[str] = None
    custo_produto_unitario: Optional[float] = None
    custo_maximo_unitario: Optional[float] = None
    margem_min_lucro: Optional[float] = None          # %
    peso_unitario: Optional[float] = None             # gramas
    altura_cm: Optional[float] = None
    largura_cm: Optional[float] = None
    comprimento_cm: Optional[float] = None
    descricao: Optional[str] = None


class ProdutoPatch(ProdutoIn):
    nome: Optional[str] = None                         # tudo opcional no patch


class ProdutoItemReq(BaseModel):
    produto_id: Optional[int] = None                   # None = desvincular


class FreteServicoReq(BaseModel):
    frete_servico: str = Field(..., description="mais_barato | PAC")

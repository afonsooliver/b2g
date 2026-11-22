"""Cliente do PNCP (busca pública de compras + itens básicos).

Baseado no pncp.py original, porém SEM Django: retorna dicts puros, deixando a
persistência para a camada de repositório. Mantém o throttle/retry compartilhado
que respeita o rate-limit (429) do PNCP — essencial para não ser bloqueado.
"""
import datetime
import threading
import time
import unicodedata
from urllib.parse import urlencode

import requests


def limpar_descricao(texto):
    if not texto:
        return texto
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


class PncpClient:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    BUSCA_URL = "https://pncp.gov.br/api/search/"

    # Throttle/rate-limit compartilhado entre threads (estado de classe).
    _local = threading.local()
    _rate_lock = threading.Lock()
    _proxima_liberacao = 0.0
    INTERVALO_BASE = 1.0
    INTERVALO_MAXIMO = 5.0
    JANELA_RECUPERACAO = 30.0
    BACKOFF_TETO = 30.0
    intervalo_minimo = INTERVALO_BASE
    _ultimo_429 = 0.0

    def __init__(self, tam_pagina=1000):
        self.tam_pagina = tam_pagina

    # ---- infra HTTP ----
    @classmethod
    def _session(cls):
        session = getattr(cls._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(cls.HEADERS)
            adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            cls._local.session = session
        return session

    @classmethod
    def _throttle(cls):
        with cls._rate_lock:
            agora = time.monotonic()
            if (cls.intervalo_minimo > cls.INTERVALO_BASE
                    and (agora - cls._ultimo_429) > cls.JANELA_RECUPERACAO):
                cls.intervalo_minimo = max(cls.INTERVALO_BASE, cls.intervalo_minimo * 0.9)
                cls._ultimo_429 = agora
            espera = cls._proxima_liberacao - agora
            if espera > 0:
                time.sleep(espera)
                agora = time.monotonic()
            cls._proxima_liberacao = agora + cls.intervalo_minimo

    @classmethod
    def request(cls, url, timeout=15, max_tentativas=10, backoff_base=2):
        session = cls._session()
        for tentativa in range(1, max_tentativas + 1):
            cls._throttle()
            try:
                resp = session.get(url, timeout=timeout)
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    try:
                        espera = float(ra) if ra is not None else min(backoff_base ** tentativa, cls.BACKOFF_TETO)
                    except ValueError:
                        espera = min(backoff_base ** tentativa, cls.BACKOFF_TETO)
                    with cls._rate_lock:
                        cls._ultimo_429 = time.monotonic()
                        cls.intervalo_minimo = min(cls.intervalo_minimo * 1.5, cls.INTERVALO_MAXIMO)
                    if tentativa < max_tentativas:
                        time.sleep(espera)
                    continue
                if resp.status_code in (500, 502, 503, 504):
                    pass  # vale retry
                else:
                    return resp  # 404/403/etc: definitivo
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                pass
            if tentativa < max_tentativas:
                time.sleep(min(backoff_base ** tentativa, cls.BACKOFF_TETO))
        return None

    # ---- busca de editais ----
    def _url_busca(self, pagina, modalidades, data_inicio, data_fim):
        params = {
            "ordenacao": "-data",
            "tipos_documento": "edital",
            "pagina": pagina,
            "tam_pagina": self.tam_pagina,
            "status": "recebendo_proposta",
            "modalidades": "|".join(str(m) for m in modalidades),
            "fontes": 3,  # Compras.gov.br
            "data_publicacao_inicio": data_inicio,
            "data_publicacao_fim": data_fim,
            "tipos_item": "M",
        }
        return f"{self.BUSCA_URL}?{urlencode(params)}"

    @staticmethod
    def _normalizar_editais(data):
        """Converte a resposta de /api/search em dicts de edital + uasg aninhado."""
        out = []
        for e in data.get("items", []):
            try:
                dt_abertura = None
                fim = e.get("data_fim_vigencia")
                if fim:
                    dt_abertura = datetime.datetime.fromisoformat(fim[:19]).date().isoformat()

                numero = str(e.get("numero") or "").zfill(5)
                ano = e.get("ano")
                uasg = e.get("unidade_codigo")
                modalidade = "06" if e.get("tipo_id") == "2" else "05"
                edital_id = f"{uasg}{modalidade}{numero}{ano}"
                url_edital = ("https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/"
                              f"public/compras/acompanhamento-compra?compra={edital_id}")
                out.append({
                    "id": edital_id,
                    "uasg_codigo": uasg,
                    "ano_compra": ano,
                    "sequencial_compra": e.get("numero_sequencial"),
                    "numero": numero,
                    "descricao": e.get("description"),
                    "srp": None,
                    "dt_abertura": dt_abertura,
                    "modalidade": modalidade,
                    "numero_pncp": e.get("numero_controle_pncp"),
                    "url_edital": url_edital,
                    "uasg": {
                        "codigo": uasg,
                        "cnpj": e.get("orgao_cnpj"),
                        "razao_social": e.get("orgao_nome"),
                        "nome": e.get("unidade_nome"),
                        "municipio": e.get("municipio_nome"),
                        "uf": e.get("uf"),
                    },
                })
            except Exception:
                continue
        return out

    def buscar(self, modalidades, data_inicio, data_fim):
        """Gera (pagina, total_paginas, editais_normalizados) página a página.

        Streaming por página mantém o uso de memória baixo mesmo em buscas grandes.
        """
        resp = self.request(self._url_busca(1, modalidades, data_inicio, data_fim))
        if resp is None or resp.status_code != 200:
            return
        data = resp.json()
        total = data.get("total", 0)
        paginas = max(1, -(-total // self.tam_pagina))  # ceil
        yield 1, paginas, self._normalizar_editais(data)
        for pagina in range(2, paginas + 1):
            resp = self.request(self._url_busca(pagina, modalidades, data_inicio, data_fim))
            if resp is not None and resp.status_code == 200:
                yield pagina, paginas, self._normalizar_editais(resp.json())
            else:
                yield pagina, paginas, []

    # ---- itens básicos de um edital ----
    def itens_edital(self, edital: dict):
        """Itens básicos (público). Retorna (status, lista_de_itens).

        status: 'ok' | 'nao_encontrado' | 'erro'.
        """
        numero_pncp = edital.get("numero_pncp") or ""
        cnpj = numero_pncp.split("-")[0]
        if not cnpj:
            return "erro", []
        url = (f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/"
               f"{edital['ano_compra']}/{edital['sequencial_compra']}/itens?tamanhoPagina=999")
        resp = self.request(url)
        if resp is None:
            return "erro", []
        if resp.status_code == 404:
            return "nao_encontrado", []
        if resp.status_code != 200:
            return "erro", []
        itens = []
        for it in resp.json():
            itens.append({
                "numero": it.get("numeroItem"),
                "descricao": limpar_descricao(it.get("descricao")),
                "material_ou_servico": it.get("materialOuServico"),
                "quantidade": it.get("quantidade"),
                "unidade": it.get("unidadeMedida"),
                "valor_unitario_estimado_edital": it.get("valorUnitarioEstimado"),
            })
        return "ok", itens

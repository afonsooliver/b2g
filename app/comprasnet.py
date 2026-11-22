"""Cliente ComprasNet Mobile / SERPRO — detalhamento AUTENTICADO, sob demanda.

Só é chamado para os itens que o usuário seleciona (cada item = ~1 requisição).
Fontes descobertas por engenharia reversa do APK (ver Rotas.txt):
  - modo de disputa (nível edital):
      GET /comprasnet-fase-externa/v1/compras/{compra}/participacao -> modoDisputa
  - detalhamento do item (valor estimado + intervalo mínimo entre lances + tipo I/S/G):
      GET /comprasnet-fase-externa/v1/compras/{compra}/itens/{numero}/detalhamento
"""
import requests

from .config import config

MODO_DISPUTA = {"A": "Aberto", "F": "Fechado", "AF": "Aberto/Fechado", "FA": "Fechado/Aberto"}
TIPO_INTERVALO_LANCE = {"P": "Percentual", "V": "Valor"}
# tipo (fase-externa): I = item avulso, S = subitem de grupo, G = grupo
ITEM_OU_GRUPO = {"I": "ITEM", "S": "GRUPO", "G": "GRUPO"}


class ComprasNet:
    def __init__(self, base=None, token=None, token_url=None, token_ttl=240):
        self.base = (base or config.COMPRASNET_BASE).rstrip("/")
        self._token_fixo = token  # override explícito (ex.: testes)
        self._session = requests.Session()

    # ---- token ----
    def token(self):
        if self._token_fixo:
            return self._token_fixo
        from . import auth  # import tardio evita ciclo
        return auth.obter_token()

    def _headers(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def _fe(self, caminho):
        return f"{self.base}/comprasnet-fase-externa/v1{caminho}"

    # ---- modo de disputa (edital) ----
    def modo_disputa(self, compra):
        """Retorna (codigo, descricao) ou (None, None) se indisponível."""
        try:
            r = self._session.get(self._fe(f"/compras/{compra}/participacao"),
                                  headers=self._headers(), timeout=30)
            if r.status_code in (200, 206) and r.text.strip():
                cod = r.json().get("modoDisputa")
                return cod, MODO_DISPUTA.get(cod)
        except requests.RequestException:
            pass
        return None, None

    # ---- detalhamento de um item ----
    def detalhar_item(self, compra, numero):
        """Retorna dict com os campos de detalhamento, ou {} se indisponível."""
        try:
            r = self._session.get(self._fe(f"/compras/{compra}/itens/{numero}/detalhamento"),
                                  headers=self._headers(), timeout=30)
            if r.status_code not in (200, 206) or not r.text.strip():
                return {}
            d = r.json()
        except requests.RequestException:
            return {}
        tipo_fe = d.get("tipo")
        tipo_int = d.get("tipoVariacaoMinimaEntreLances")
        return {
            "descricao_detalhada": d.get("descricaoDetalhada"),
            "tipo_fe": tipo_fe,
            "item_ou_grupo": ITEM_OU_GRUPO.get(tipo_fe),
            "valor_estimado_unitario": d.get("valorEstimadoUnitario"),
            "valor_estimado_total": d.get("valorEstimadoTotal", d.get("valorEstimado")),
            "intervalo_lance_tipo": TIPO_INTERVALO_LANCE.get(tipo_int),
            "intervalo_lance_tipo_codigo": tipo_int,
            "intervalo_lance_valor": d.get("variacaoMinimaEntreLances"),
        }

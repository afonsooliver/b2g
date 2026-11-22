"""Frete via Frenet, com empacotamento por dimensão/peso (porta de frete.py).

Escolhe o serviço conforme a preferência do edital: 'mais_barato' ou 'PAC'.
Destino = CEP da UASG (ou estimado por UF/município via ViaCEP).
"""
import math
from decimal import Decimal

import requests

from .config import config


def resolver_cep_destino(uasg: dict) -> str | None:
    """CEP da UASG; se vazio, estima pelo município/UF (ViaCEP)."""
    cep = (uasg or {}).get("cep")
    if cep and str(cep).strip():
        return str(cep).replace("-", "").strip()
    uf, municipio = (uasg or {}).get("uf"), (uasg or {}).get("municipio")
    if not uf or not municipio:
        return None
    try:
        r = requests.get(f"https://viacep.com.br/ws/{uf}/{municipio}/rua/json/", timeout=5)
        data = r.json()
        if data:
            return (data[0].get("cep") or "").replace("-", "")
    except Exception:
        pass
    return None


def calcular_volumes_agrupados(quantidade, altura_cm, largura_cm, comprimento_cm,
                               peso_unitario_g, lado_max_cm=30, peso_max_kg=30):
    """Agrupa os itens em caixas (<= lado_max e peso_max). Retorna lista de volumes."""
    altura_cm = float(altura_cm or 1)
    largura_cm = float(largura_cm or 1)
    comprimento_cm = float(comprimento_cm or 1)
    peso_kg = float(peso_unitario_g or 0) / 1000.0

    max_a = int(lado_max_cm // altura_cm) or 1
    max_l = int(lado_max_cm // largura_cm) or 1
    max_c = int(lado_max_cm // comprimento_cm) or 1
    max_fisico = max(1, max_a * max_l * max_c)
    max_por_peso = int(peso_max_kg // peso_kg) if peso_kg > 0 else max_fisico
    itens_por_volume = max(1, int(min(max_fisico, max_por_peso)))

    quantidade = int(quantidade or 1)
    volumes_cheios = quantidade // itens_por_volume
    resto = quantidade % itens_por_volume
    volumes = []
    if volumes_cheios > 0:
        volumes.append({
            "itens": itens_por_volume,
            "peso_kg": round(itens_por_volume * peso_kg, 2) or 0.1,
            "altura": round(max_a * altura_cm, 2),
            "largura": round(max_l * largura_cm, 2),
            "comprimento": round(max_c * comprimento_cm, 2),
            "quantidade_volumes": volumes_cheios,
        })
    if resto > 0:
        a = min(max_a, max(1, math.ceil(resto ** (1 / 3))))
        l = min(max_l, max(1, math.ceil(resto / a)))
        c = min(max_c, max(1, math.ceil(resto / (a * l))))
        volumes.append({
            "itens": resto,
            "peso_kg": round(resto * peso_kg, 2) or 0.1,
            "altura": round(a * altura_cm, 2),
            "largura": round(l * largura_cm, 2),
            "comprimento": round(c * comprimento_cm, 2),
            "quantidade_volumes": 1,
        })
    return {"volumes": volumes, "itens_por_volume": itens_por_volume}


def cotar_frenet(cep_destino, itens_array, seguro) -> dict:
    if not config.FRENET_TOKEN:
        return {"mensagem": "erro", "erro": "FRENET_TOKEN não configurado"}
    payload = {
        "SellerCEP": config.FRETE_ORIGEM_CEP,
        "RecipientCEP": str(cep_destino).replace("-", ""),
        "ShipmentInvoiceValue": float(seguro or 0),
        "RecipientCountry": "BR",
        "ShippingItemArray": itens_array,
    }
    headers = {"token": config.FRENET_TOKEN, "Content-Type": "application/json",
               "Accept": "application/json"}
    try:
        data = requests.post(config.FRENET_URL, json=payload, headers=headers, timeout=20).json()
    except Exception as e:
        return {"mensagem": "erro", "erro": str(e)}
    return {"mensagem": "sucesso", "servicos": data.get("ShippingSevicesArray") or []}


def escolher_servico(servicos: list, preferencia: str = "mais_barato"):
    validos = [s for s in servicos if not s.get("Error") and s.get("ShippingPrice") not in (None, "")]
    validos = [s for s in validos if float(s.get("ShippingPrice") or 0) > 0]
    if not validos:
        return None
    if preferencia == "PAC":
        pac = [s for s in validos if "PAC" in (s.get("ServiceDescription") or "").upper()]
        if pac:
            return min(pac, key=lambda s: float(s["ShippingPrice"]))
    return min(validos, key=lambda s: float(s["ShippingPrice"]))


def calcular_frete(produto: dict, quantidade, cep_destino, servico="mais_barato",
                   valor_seguro=None) -> dict:
    """Retorna {frete_total, frete_unitario, transportadora} ou {mensagem: erro}."""
    if not cep_destino:
        return {"mensagem": "erro", "erro": "CEP de destino indisponível"}
    try:
        vol = calcular_volumes_agrupados(
            quantidade, produto.get("altura_cm"), produto.get("largura_cm"),
            produto.get("comprimento_cm"), produto.get("peso_unitario"))
    except Exception as e:
        return {"mensagem": "erro", "erro": f"empacotamento: {e}"}
    itens_array = [{
        "Height": v["altura"], "Length": v["comprimento"], "Width": v["largura"],
        "Weight": v["peso_kg"], "Quantity": v["quantidade_volumes"],
    } for v in vol["volumes"]]
    seguro = valor_seguro if valor_seguro is not None else \
        float(quantidade or 0) * float(produto.get("custo_produto_unitario") or 0)
    cot = cotar_frenet(cep_destino, itens_array, seguro)
    if cot.get("mensagem") == "erro":
        return cot
    escolhido = escolher_servico(cot["servicos"], servico)
    if not escolhido:
        return {"mensagem": "erro", "erro": "nenhum serviço de frete disponível"}
    frete_total = Decimal(str(escolhido["ShippingPrice"]))
    qtd = Decimal(str(quantidade or 1))
    return {
        "mensagem": "sucesso",
        "frete_total": float(frete_total),
        "frete_unitario": float((frete_total / qtd).quantize(Decimal("0.01"))),
        "transportadora": escolhido.get("ServiceDescription") or escolhido.get("Carrier"),
    }

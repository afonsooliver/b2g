"""Autenticador ComprasNet/SERPRO — ciclo de vida do token (portado de oauth.py).

Fonte do token:
  - COMPRASNET_TOKEN (estático) tem prioridade absoluta, se definido.
  - MODO DEBUG (ajuste runtime, persistido em config_app): token vem do servidor
    remoto COMPRASNET_TOKEN_URL (fastgov2) — usado em dev.
  - MODO PRODUÇÃO: autentica de verdade — renova via `retoken` e lê o accessToken
    do store (token_gov). Semente da sessão via `token_inicial(id)`.

O modo inicial deriva de EM_PRODUCAO (debug = not EM_PRODUCAO), mas uma vez
alterado pelo admin o valor persistido prevalece.
"""
import threading

import requests
import urllib3

from . import repository as repo
from .config import config
from .db import get_conn

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BASE = config.COMPRASNET_BASE.rstrip("/")
_VERIFY = config.COMPRASNET_VERIFY_SSL
_lock = threading.Lock()
_cache = {"token": None}  # cache em memória do access token vigente


# ---------- store helpers ----------
def _store_get():
    with get_conn() as conn:
        return repo.obter_token_store(conn)


def _store_save(data: dict):
    with get_conn() as conn:
        repo.salvar_token(conn, data)


# ---------- modo debug (runtime) ----------
def modo_debug() -> bool:
    with get_conn() as conn:
        v = repo.get_config(conn, "modo_debug")
    if v is None:
        return not config.EM_PRODUCAO          # default derivado do env
    return v == "1"


def set_modo_debug(debug: bool) -> bool:
    with get_conn() as conn:
        repo.set_config(conn, "modo_debug", "1" if debug else "0")
    with _lock:
        _cache["token"] = None                 # troca de fonte → invalida cache
    return debug


# ---------- fluxo SERPRO (produção) ----------
def token_inicial(id_value: str) -> dict:
    """Semeia a sessão do fornecedor: GET .../sessao/fornecedor/usuario/token/{id}."""
    url = f"{_BASE}/comprasnet-usuario/v2/sessao/fornecedor/usuario/token/{id_value}"
    try:
        data = requests.get(url, verify=_VERIFY, timeout=10).json()
    except Exception as e:
        return {"mensagem": "erro", "erro": str(e)}
    if data.get("accessToken"):
        _store_save(data)
        with _lock:
            _cache["token"] = data["accessToken"]
        data["mensagem"] = "sucesso"
    else:
        data["mensagem"] = "erro"
    return data


def refresh_token() -> dict:
    """Renova o access token via PUT .../retoken usando o refresh token do store."""
    store = _store_get()
    if not store or not store.get("refreshtoken"):
        return {"mensagem": "erro", "erro": "sem refresh token"}
    url = f"{_BASE}/comprasnet-usuario/v2/sessao/fornecedor/retoken"
    try:
        data = requests.put(
            url, headers={"Authorization": f"Bearer {store['refreshtoken']}"},
            verify=_VERIFY, timeout=10,
        ).json()
    except Exception as e:
        return {"mensagem": "erro", "erro": str(e)}
    if data.get("accessToken"):
        _store_save(data)
        with _lock:
            _cache["token"] = data["accessToken"]
        data["mensagem"] = "sucesso"
    else:
        data["mensagem"] = "erro"
    return data


def _access_token_producao():
    refresh_token()
    store = _store_get()
    return store.get("accesstoken") if store else None


def _token_remoto():
    return requests.get(config.COMPRASNET_TOKEN_URL, timeout=10).json().get("token")


# ---------- API pública ----------
def obter_token(forcar: bool = False):
    """Retorna o access token vigente conforme o modo (debug/produção), com cache."""
    if config.COMPRASNET_TOKEN:
        return config.COMPRASNET_TOKEN
    with _lock:
        if not forcar and _cache["token"]:
            return _cache["token"]
    token = _token_remoto() if modo_debug() else _access_token_producao()
    with _lock:
        if token:
            _cache["token"] = token
    return token


def request_autenticado(url, method="GET", **kwargs):
    """Request com Bearer e 1 retry forçando novo token em 401."""
    timeout = kwargs.pop("timeout", 30)
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = f"Bearer {obter_token()}"
    resp = requests.request(method, url, headers=headers, verify=_VERIFY, timeout=timeout, **kwargs)
    if resp.status_code == 401:
        headers["Authorization"] = f"Bearer {obter_token(forcar=True)}"
        resp = requests.request(method, url, headers=headers, verify=_VERIFY, timeout=timeout, **kwargs)
    return resp


def info_usuario() -> dict:
    """Checa a sessão: GET /comprasnet-usuario/v1/usuario."""
    try:
        r = request_autenticado(f"{_BASE}/comprasnet-usuario/v1/usuario")
    except Exception as e:
        return {"mensagem": "erro", "conectado": False, "erro": str(e)}
    if r.status_code == 401:
        return {"mensagem": "erro", "conectado": False, "erro": "nao autenticado"}
    try:
        data = r.json()
    except Exception:
        return {"mensagem": "erro", "conectado": False}
    return {"mensagem": "sucesso", "conectado": bool(data.get("tipoUsuario")), "usuario": data}


def status() -> dict:
    """Resumo do autenticador para o painel."""
    store = _store_get()
    return {
        "modo": "debug" if modo_debug() else "producao",
        "debug": modo_debug(),
        "token_presente": bool(obter_token_silencioso()),
        "token_store_em": store.get("atualizado_em") if store else None,
    }


def obter_token_silencioso():
    try:
        return obter_token()
    except Exception:
        return None

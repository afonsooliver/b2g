"""Configuração central via variáveis de ambiente (12-factor).

Mantém tudo que muda entre ambientes fora do código. No Railway, defina as
variáveis no painel; localmente, use um arquivo .env (ver .env.example).
"""
import os


def _int(nome, padrao):
    try:
        return int(os.getenv(nome, padrao))
    except (TypeError, ValueError):
        return int(padrao)


def _bool(nome, padrao=False):
    v = os.getenv(nome)
    if v is None:
        return padrao
    return v.strip().lower() in ("1", "true", "yes", "sim", "on")


class Config:
    # --- Banco (SQLite) ---
    # No Railway, aponte para um volume persistente, ex.: /data/fastgov.db
    DB_PATH = os.getenv("DB_PATH", "data/fastgov.db")

    # --- PNCP (busca pública de compras) ---
    PNCP_TAM_PAGINA = _int("PNCP_TAM_PAGINA", 1000)
    PNCP_MODALIDADES = tuple(
        int(x) for x in os.getenv("PNCP_MODALIDADES", "4,8,9,1,6").split(",") if x.strip()
    )

    # --- ComprasNet / SERPRO (detalhamento autenticado, sob demanda) ---
    COMPRASNET_BASE = os.getenv(
        "COMPRASNET_BASE", "https://cnetmobile.estaleiro.serpro.gov.br"
    )
    # Token estático (COMPRASNET_TOKEN) tem prioridade sobre o autenticador.
    COMPRASNET_TOKEN = os.getenv("COMPRASNET_TOKEN")
    # Fora de produção, o token é buscado remotamente nesta URL (instância produtiva / fastgov2).
    COMPRASNET_TOKEN_URL = os.getenv(
        "COMPRASNET_TOKEN_URL", "https://fastgov2.up.railway.app/token/?tipo=token"
    )
    # Paralelismo do detalhamento dos itens selecionados.
    DETALHE_WORKERS = _int("DETALHE_WORKERS", 6)

    # --- Autenticador ComprasNet (ciclo do token SERPRO) ---
    # Em produção o app autentica de verdade (tokenInicial/retoken + keep-alive);
    # fora de produção ele apenas consome COMPRASNET_TOKEN_URL.
    EM_PRODUCAO = _bool("EM_PRODUCAO", False)
    # SERPRO costuma ter cadeia TLS problemática neste host; oauth.py de referência usa verify=False.
    COMPRASNET_VERIFY_SSL = _bool("COMPRASNET_VERIFY_SSL", False)
    # Semente da sessão do fornecedor (id usado em tokenInicial), quando disponível.
    COMPRASNET_SESSAO_ID = os.getenv("COMPRASNET_SESSAO_ID")
    # Intervalos do keep-alive de autenticação (segundos).
    AUTH_INTERVALO_OK = _int("AUTH_INTERVALO_OK", 300)
    AUTH_INTERVALO_FALHA = _int("AUTH_INTERVALO_FALHA", 5)

    # --- Background: workers, agendador e serviços ---
    BG_WORKERS = _int("BG_WORKERS", 2)
    BG_POLL_INTERVAL = float(os.getenv("BG_POLL_INTERVAL", "1.0"))
    SCHED_TZ = os.getenv("SCHED_TZ", "America/Sao_Paulo")
    # Cliente WebSocket/STOMP ComprasNet (serviço de longa duração).
    COMPRASNET_WS_URL = os.getenv(
        "COMPRASNET_WS_URL",
        "wss://cnetmobile.estaleiro.serpro.gov.br/comprasnet-websocket/socket/websocket",
    )
    WS_HEARTBEAT_MS = _int("WS_HEARTBEAT_MS", 20000)
    WS_RECONNECT_MS = _int("WS_RECONNECT_MS", 5000)

    # --- Precificação / frete ---
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "data/produtos")
    FRENET_TOKEN = os.getenv("FRENET_TOKEN")
    FRENET_URL = os.getenv("FRENET_URL", "https://api.frenet.com.br/shipping/quote")
    FRETE_ORIGEM_CEP = os.getenv("FRETE_ORIGEM_CEP", "18682100")
    LUCRO_MINIMO_VENDA = float(os.getenv("LUCRO_MINIMO_VENDA", "30"))


config = Config()

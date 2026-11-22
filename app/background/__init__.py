"""Orquestração do background: singletons + ciclo de vida (lifespan)."""
from . import scheduler, services_manager
from .. import repository as repo
from ..db import get_conn
from .worker import WorkerPool

worker_pool = WorkerPool()


def iniciar_tudo():
    """Chamado no startup (lifespan)."""
    with get_conn() as conn:
        repo.requeue_orfas(conn)        # execuções presas em 'rodando' após crash
    worker_pool.iniciar()
    services_manager.iniciar_auto()     # seed + retoma serviços 'rodando'
    scheduler.iniciar()                 # agendamentos + crons de serviço


def parar_tudo():
    """Chamado no shutdown (lifespan)."""
    scheduler.parar()
    services_manager.parar_todos()
    worker_pool.parar()


def status_geral() -> dict:
    return {
        "workers": worker_pool.status(),
        "servicos": services_manager.listar(),
        "proximos_agendamentos": scheduler.proximos(),
    }


# ---- controles de operação ----
def reiniciar_workers():
    worker_pool.reiniciar()


def reiniciar_scheduler():
    scheduler.parar()
    scheduler.iniciar()


def reiniciar_tudo():
    parar_tudo()
    iniciar_tudo()

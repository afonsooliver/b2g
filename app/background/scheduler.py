"""Agendador (APScheduler): dispara tarefas em cron e liga/desliga serviços.

A ação do job de tarefa SÓ ENFILEIRA uma execução (os workers processam), para
manter backlog/workers visíveis. Jobs de serviço chamam o services_manager.
A fonte de verdade é o banco; os jobs são recarregados do banco ao iniciar.
"""
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import logbuf, services_manager
from .. import repository as repo
from ..config import config
from ..db import get_conn

_sched: BackgroundScheduler | None = None
_tz = pytz.timezone(config.SCHED_TZ)


def iniciar():
    global _sched
    if _sched:
        return
    _sched = BackgroundScheduler(timezone=_tz)
    _sched.start()
    sincronizar()


def parar():
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None


# ---- sincronização com o banco ----
def sincronizar():
    with get_conn() as conn:
        agendamentos = repo.listar_agendamentos(conn)
        servicos = repo.listar_servicos(conn)
    for ag in agendamentos:
        _carregar_agendamento(ag)
    for sv in servicos:
        _carregar_servico(sv)


def _carregar_agendamento(ag: dict):
    jid = f"ag:{ag['id']}"
    _remover_job(jid)
    if not ag.get("ativo"):
        _salvar_proxima(ag["id"], None)
        return
    try:
        trigger = CronTrigger.from_crontab(ag["cron"], timezone=_tz)
    except Exception:
        _salvar_proxima(ag["id"], None)
        return
    job = _sched.add_job(_disparar_agendamento, trigger, args=[ag["id"]], id=jid,
                         replace_existing=True, misfire_grace_time=3600, coalesce=True)
    _salvar_proxima(ag["id"], _fmt(job.next_run_time))


def _carregar_servico(sv: dict):
    for sufixo, cron, acao in (("inicio", sv.get("cron_inicio"), services_manager.iniciar),
                               ("fim", sv.get("cron_fim"), services_manager.parar)):
        jid = f"svc:{sv['nome']}:{sufixo}"
        _remover_job(jid)
        if not sv.get("auto") or not cron:
            continue
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=_tz)
        except Exception:
            continue
        _sched.add_job(acao, trigger, args=[sv["nome"]], id=jid, replace_existing=True)


# ---- ações dos jobs ----
def _disparar_agendamento(ag_id: int):
    with get_conn() as conn:
        ag = repo.get_agendamento(conn, ag_id)
        if not ag or not ag.get("ativo"):
            return
        import json
        params = json.loads(ag.get("params_json") or "{}")
        repo.enfileirar(conn, ag["tarefa"], params, origem="agendado", agendamento_id=ag_id)
        repo.atualizar_agendamento(conn, ag_id, {"ultima_execucao": _agora()})
    logbuf.log("scheduler", f"agendamento {ag_id} ({ag['tarefa']}) disparado → enfileirado")
    job = _sched.get_job(f"ag:{ag_id}") if _sched else None
    _salvar_proxima(ag_id, _fmt(job.next_run_time) if job else None)


# ---- API usada pela camada admin (após alterar o banco) ----
def recarregar_agendamento(ag_id: int):
    with get_conn() as conn:
        ag = repo.get_agendamento(conn, ag_id)
    if ag:
        _carregar_agendamento(ag)
    else:
        _remover_job(f"ag:{ag_id}")


def recarregar_servico(nome: str):
    with get_conn() as conn:
        sv = repo.get_servico(conn, nome)
    if sv:
        _carregar_servico(sv)


def proximos(limit: int = 10) -> list[dict]:
    if not _sched:
        return []
    jobs = [j for j in _sched.get_jobs() if j.id.startswith("ag:")]
    jobs.sort(key=lambda j: j.next_run_time or _longe())
    out = []
    for j in jobs[:limit]:
        out.append({"id": j.id, "proxima": _fmt(j.next_run_time)})
    return out


# ---- helpers ----
def _remover_job(jid: str):
    if _sched and _sched.get_job(jid):
        _sched.remove_job(jid)


def _salvar_proxima(ag_id: int, valor):
    with get_conn() as conn:
        repo.atualizar_agendamento(conn, ag_id, {"proxima_execucao": valor})


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _agora():
    import datetime
    return datetime.datetime.now(_tz).strftime("%Y-%m-%d %H:%M:%S")


def _longe():
    import datetime
    return datetime.datetime.max.replace(tzinfo=pytz.UTC)

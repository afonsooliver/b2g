"""Pool de workers: threads que drenam a fila `execucao` (SQLite).

Cada worker reivindica uma execução atômica, roda a função do registry FORA de
qualquer transação, e grava o resultado. Suporta reinício do pool e cancelamento
COOPERATIVO de execuções em andamento (a tarefa recebe `deve_cancelar()` e decide
parar em um ponto seguro).
"""
import inspect
import json
import threading
import time
import traceback

from . import logbuf, registry
from .. import repository as repo
from ..config import config
from ..db import get_conn


class WorkerPool:
    def __init__(self, n_workers: int = None, poll: float = None):
        self.n = n_workers or config.BG_WORKERS
        self.poll = poll or config.BG_POLL_INTERVAL
        self._stop = threading.Event()     # geração atual (substituído a cada iniciar)
        self._threads: list[threading.Thread] = []
        self._ocupados: set[str] = set()
        self._cancelar: set[int] = set()   # ids de execução a cancelar (cooperativo)
        self._lock = threading.Lock()

    def _log(self, m, nivel="info"):
        logbuf.log("worker", m, nivel)

    # ---- ciclo de vida ----
    def iniciar(self):
        # Event novo por geração: workers antigos (se ainda rodando uma tarefa longa)
        # mantêm o Event antigo, já setado, e encerram ao terminar — sem duplicar o pool.
        self._stop = threading.Event()
        ev = self._stop
        self._threads = [
            threading.Thread(target=self._loop, args=(f"w{i+1}", ev), daemon=True, name=f"worker-{i+1}")
            for i in range(self.n)
        ]
        for t in self._threads:
            t.start()
        self._log(f"pool iniciado com {self.n} worker(s)")

    def parar(self, timeout: float = 5.0):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads = []
        self._log("pool parado")

    def reiniciar(self):
        self._log("reiniciando pool…")
        self.parar()
        self.iniciar()

    # ---- cancelamento ----
    def solicitar_cancelamento(self, exec_id: int) -> bool:
        with self._lock:
            self._cancelar.add(exec_id)
        self._log(f"cancelamento solicitado para execução {exec_id}")
        return True

    def _cancelado(self, exec_id: int) -> bool:
        with self._lock:
            return exec_id in self._cancelar

    # ---- status ----
    def status(self) -> dict:
        with get_conn() as conn:
            c = repo.contagem_execucoes(conn)
        with self._lock:
            ocupados = len(self._ocupados)
        return {
            "workers": self.n, "ocupados": ocupados, "ociosos": self.n - ocupados,
            "backlog": c.get("fila", 0), "rodando": c.get("rodando", 0),
            "sucesso": c.get("sucesso", 0), "erro": c.get("erro", 0),
            "cancelado": c.get("cancelado", 0),
        }

    # ---- loop ----
    def _loop(self, nome: str, stop: threading.Event):
        while not stop.is_set():
            with get_conn() as conn:
                ex = repo.reivindicar_execucao(conn, nome)
            if ex is None:
                stop.wait(self.poll)
                continue
            self._executar(nome, ex)

    def _executar(self, nome: str, ex: dict):
        exec_id = ex["id"]
        with self._lock:
            self._ocupados.add(nome)
        self._log(f"[{nome}] execução {exec_id} ({ex['tarefa']}) iniciada")
        t0 = time.monotonic()
        try:
            func = registry.resolver(ex["tarefa"])
            params = json.loads(ex["params_json"] or "{}")
            # injeta deve_cancelar() se a tarefa suportar cancelamento cooperativo
            suporta_cancel = "deve_cancelar" in inspect.signature(func).parameters
            if suporta_cancel:
                params["deve_cancelar"] = lambda: self._cancelado(exec_id)
            resultado = func(**params)
            dur = int((time.monotonic() - t0) * 1000)
            with get_conn() as conn:
                if suporta_cancel and self._cancelado(exec_id):
                    conn.execute(
                        "UPDATE execucao SET status='cancelado', finalizado_em=datetime('now'), "
                        "duracao_ms=?, resultado_json=? WHERE id=?",
                        (dur, json.dumps(resultado, ensure_ascii=False, default=str), exec_id))
                    self._log(f"[{nome}] execução {exec_id} cancelada")
                else:
                    repo.marcar_ok(conn, exec_id, resultado, dur)
                    self._log(f"[{nome}] execução {exec_id} concluída em {dur}ms")
        except Exception as e:
            dur = int((time.monotonic() - t0) * 1000)
            with get_conn() as conn:
                repo.marcar_erro(conn, exec_id, f"{e}\n{traceback.format_exc()}", dur)
            self._log(f"[{nome}] execução {exec_id} com erro: {e}", "erro")
        finally:
            with self._lock:
                self._ocupados.discard(nome)
                self._cancelar.discard(exec_id)

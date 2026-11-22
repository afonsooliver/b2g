"""Buffer de logs em memória (ring buffer) por origem — para o painel.

Leve e thread-safe. Não persiste (é log operacional ao vivo); o histórico de
execuções fica na tabela `execucao`.
"""
import threading
import time
from collections import deque

_MAX = 1000
_buf: deque = deque(maxlen=_MAX)
_lock = threading.Lock()
_seq = 0


def log(origem: str, mensagem, nivel: str = "info"):
    global _seq
    with _lock:
        _seq += 1
        _buf.append({
            "seq": _seq,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "origem": origem,
            "nivel": nivel,
            "mensagem": str(mensagem)[:1000],
        })


def listar(origem: str = None, nivel: str = None, limit: int = 200, desde: int = 0) -> list[dict]:
    with _lock:
        itens = list(_buf)
    if origem:
        itens = [x for x in itens if x["origem"] == origem]
    if nivel:
        itens = [x for x in itens if x["nivel"] == nivel]
    if desde:
        itens = [x for x in itens if x["seq"] > desde]
    return itens[-limit:]


def origens() -> list[str]:
    with _lock:
        return sorted({x["origem"] for x in _buf})

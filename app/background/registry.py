"""Registry das tarefas que podem ser agendadas/executadas em background.

Desacopla "o que pode rodar" do agendador/worker. Cada entrada aponta para uma
função já existente (reuso de app/services.py).
"""
from .. import services

# nome -> {func, rotulo, params_default}
TAREFAS = {
    "ingestao": {
        "func": services.ingestao,
        "rotulo": "Ingestão PNCP",
        "params_default": {"com_itens": True},
    },
}


def existe(tarefa: str) -> bool:
    return tarefa in TAREFAS


def resolver(tarefa: str):
    if tarefa not in TAREFAS:
        raise KeyError(f"tarefa desconhecida: {tarefa}")
    return TAREFAS[tarefa]["func"]


def listar() -> list[dict]:
    return [{"tarefa": k, "rotulo": v["rotulo"], "params_default": v["params_default"]}
            for k, v in TAREFAS.items()]

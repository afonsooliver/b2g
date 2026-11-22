"""Serviço de longa duração: cliente STOMP sobre WebSocket da ComprasNet.

Conecta no wss do SERPRO (subprotocolo STOMP), autentica no frame CONNECT, assina
os tópicos das compras monitoradas (itens em etapa disputa/homologação) e despacha
as mensagens para o domínio (app/background/disputa.py). Reconecta sozinho e
registra tudo no buffer de logs. Baseado em disputa.py / seção 9 do Rotas.txt.
"""
import json
import ssl
import threading
import time

from websocket import WebSocketApp

from .. import auth
from .. import repository as repo
from ..config import config
from ..db import get_conn
from . import disputa, logbuf

NUL = "\x00"
TOPICOS_BASE = ["/user/queue/dataHoraBrasilia", "/topic/dataHoraBrasilia"]


def _frame(comando: str, headers: dict, corpo: str = "") -> str:
    return "\n".join([comando] + [f"{k}:{v}" for k, v in headers.items()] + ["", corpo]) + NUL


def _parse(msg: str):
    msg = msg.rstrip(NUL)
    cabeca, _, corpo = msg.partition("\n\n")
    linhas = cabeca.split("\n")
    comando = linhas[0] if linhas else ""
    headers = {}
    for ln in linhas[1:]:
        if ":" in ln:
            k, _, v = ln.partition(":")
            headers[k] = v
    return comando, headers, corpo


class WsComprasnet:
    nome = "ws_comprasnet"

    def __init__(self):
        self.url = config.COMPRASNET_WS_URL
        self.heartbeat = config.WS_HEARTBEAT_MS
        self.reconnect = config.WS_RECONNECT_MS / 1000.0
        self._rodando = False
        self._conectado = False
        self._ws = None
        self._thread = None
        self._mensagens = 0
        self._ultima_classe = None
        self._ultimo_erro = None
        self._desde = None
        self._fornecedor = None
        self._subs: dict[str, str] = {}
        self._sub_id = 0
        self._lock = threading.Lock()

    def _log(self, m, nivel="info"):
        logbuf.log(self.nome, m, nivel)

    # ---- ciclo de vida ----
    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._desde = time.strftime("%Y-%m-%d %H:%M:%S")
        self._log("iniciando serviço WS")
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ws-comprasnet")
        self._thread.start()

    def parar(self):
        self._rodando = False
        self._log("parando serviço WS")
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._conectado = False

    def status(self) -> dict:
        return {
            "conectado": self._conectado,
            "mensagens": self._mensagens,
            "ultima_classe": self._ultima_classe,
            "ultimo_erro": self._ultimo_erro,
            "desde": self._desde,
            "url": self.url,
            "fornecedor": self._fornecedor,
            "inscricoes": list(self._subs.values()),
        }

    # ---- loop de conexão/reconexão ----
    def _loop(self):
        while self._rodando:
            try:
                self._ws = WebSocketApp(
                    self.url,
                    header=[
                        "Host: cnetmobile.estaleiro.serpro.gov.br",
                        "Origin: https://cnetmobile.estaleiro.serpro.gov.br",
                        "Pragma: no-cache",
                        "Cache-Control: no-cache",
                        "User-Agent: Mozilla/5.0",
                        "Sec-WebSocket-Protocol: v12.stomp, v11.stomp, v10.stomp",
                    ],
                    on_open=self._on_open, on_message=self._on_message,
                    on_error=self._on_error, on_close=self._on_close,
                )
                self._log(f"conectando em {self.url}")
                sslopt = {"cert_reqs": ssl.CERT_NONE} if not config.COMPRASNET_VERIFY_SSL else None
                self._ws.run_forever(ping_interval=None, ping_timeout=None, sslopt=sslopt)
            except Exception as e:
                self._ultimo_erro = str(e)
                self._log(f"erro na conexão: {e}", "erro")
            self._conectado = False
            with self._lock:
                self._subs.clear()
            if self._rodando:
                self._log(f"reconectando em {self.reconnect:.0f}s")
                time.sleep(self.reconnect)

    # ---- callbacks STOMP ----
    def _on_open(self, ws):
        # Authorization no CONNECT é o token CRU (sem "Bearer") — como no sala_disputa.py.
        ws.send(_frame("CONNECT", {
            "Authorization": auth.obter_token(),
            "accept-version": "1.2,1.1,1.0",
            "heart-beat": f"{self.heartbeat},0",
        }))
        self._log("CONNECT enviado")

    def _on_message(self, ws, message):
        if message == "\n" or not message.strip(NUL):
            return
        comando, headers, corpo = _parse(message)
        if comando == "CONNECTED":
            self._conectado = True
            self._ultimo_erro = None
            self._log("CONNECTED — sessão STOMP autenticada")
            self._fornecedor = self._resolver_fornecedor()
            for destino in TOPICOS_BASE:
                self._subscrever(ws, destino)
            self._subscrever_monitoradas(ws)
            self._iniciar_heartbeat(ws)
            self._iniciar_refresher(ws)
        elif comando == "MESSAGE":
            self._mensagens += 1
            classe = headers.get("classe")
            destino = headers.get("destination")
            self._ultima_classe = classe or destino
            if destino == "/topic/dataHoraBrasilia":
                return
            self._log(f"MESSAGE classe={classe} dest={destino}")
            try:
                corpo_json = json.loads(corpo) if corpo.strip() else None
            except Exception:
                corpo_json = None
            mensagens = corpo_json if isinstance(corpo_json, list) \
                else ([corpo_json] if isinstance(corpo_json, dict) else [])
            if classe == "ItemDisputaRepresentation":
                for m in mensagens:
                    if isinstance(m, dict):
                        threading.Thread(target=self._tratar_disputa, args=(headers, m),
                                         daemon=True).start()
            elif classe == "NotificacaoNovaMensagemChatRepresentation":
                self._log("nova mensagem de chat recebida")
        elif comando == "ERROR":
            self._ultimo_erro = headers.get("message") or corpo[:200]
            self._log(f"ERROR do servidor: {self._ultimo_erro}", "erro")

    def _tratar_disputa(self, headers, mensagem):
        try:
            disputa.tratar_mensagem(headers, mensagem)
        except Exception as e:
            self._log(f"erro ao tratar mensagem de disputa: {e}", "erro")

    def _on_error(self, ws, error):
        self._ultimo_erro = str(error)
        self._log(f"on_error: {error}", "erro")

    def _on_close(self, ws, *args):
        self._conectado = False
        self._log("conexão fechada")

    # ---- inscrições ----
    def _resolver_fornecedor(self):
        try:
            info = auth.info_usuario()
            f = (info.get("usuario") or {}).get("fornecedor") or {}
            ident = f.get("identificacao")
            self._log(f"fornecedor identificado: {ident}")
            return ident
        except Exception as e:
            self._log(f"não foi possível identificar o fornecedor: {e}", "erro")
            return None

    def _subscrever(self, ws, destino):
        with self._lock:
            if destino in self._subs.values():
                return
            self._sub_id += 1
            sid = f"sub-{self._sub_id}"
            self._subs[sid] = destino
        ws.send(_frame("SUBSCRIBE", {"id": sid, "destination": destino, "ack": "auto"}))
        self._log(f"SUBSCRIBE {destino}")

    def _subscrever_monitoradas(self, ws):
        with get_conn() as conn:
            compras = repo.compras_monitoradas(conn)
        for compra in compras:
            self._subscrever(ws, f"/topic/compra/{compra}")
            if self._fornecedor:
                self._subscrever(ws, f"/topic/disputa/fornec/{compra}/{self._fornecedor}")

    def _iniciar_heartbeat(self, ws):
        def _bater():
            while self._rodando and self._conectado:
                try:
                    ws.send("\n")
                except Exception:
                    return
                time.sleep(5)   # como no sala_disputa.py
        threading.Thread(target=_bater, daemon=True, name="ws-heartbeat").start()

    def _iniciar_refresher(self, ws):
        """Reassina periodicamente conforme itens entram em disputa/homologação."""
        def _ref():
            while self._rodando and self._conectado:
                time.sleep(30)
                try:
                    self._subscrever_monitoradas(ws)
                except Exception:
                    pass
        threading.Thread(target=_ref, daemon=True, name="ws-refresh").start()

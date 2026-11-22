"use strict";
// FastGov — Operações (admin): modo/autenticador, workers/fila, serviços, agendamentos, execuções.

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = {
  async get(u) { const r = await fetch(u); if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json(); },
  async send(m, u, b) { const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined }); if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json(); },
};
let toastT;
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 2600); }

// ---------- modo & auth ----------
let debugAtual = true;
function pintarModo(st) {
  debugAtual = !!st.debug;
  const b = $("#modo-badge");
  b.textContent = debugAtual ? "DEBUG" : "PRODUÇÃO";
  b.className = "badge " + (debugAtual ? "badge-debug" : "badge-prod");
  $("#modo-txt").innerHTML = `<b>${debugAtual ? "Debug" : "Produção"}</b>`;
  $("#btn-toggle").textContent = debugAtual ? "→ Produção" : "→ Debug";
  $("#store-em").textContent = st.token_store_em || "—";
}
$("#btn-toggle").onclick = async () => { try { pintarModo(await api.send("POST", "/admin/modo-debug", { debug: !debugAtual })); toast("Modo alterado"); } catch (e) { toast("Erro: " + e.message); } };
$("#btn-sessao").onclick = async () => {
  $("#sessao-txt").textContent = "checando…";
  try { const r = await api.get("/admin/auth"); $("#sessao-txt").textContent = r.conectado ? `✅ conectado${r.usuario?.nome ? " — " + r.usuario.nome : ""}` : `❌ ${r.erro || "não autenticado"}`; }
  catch (e) { $("#sessao-txt").textContent = "erro: " + e.message; }
};
$("#btn-refresh").onclick = async () => { try { const r = await api.send("POST", "/admin/auth/refresh"); toast("Renovar: " + (r.mensagem || "?")); } catch (e) { toast("Erro: " + e.message); } };
$("#btn-inicial").onclick = async () => { const id = $("#inp-id").value.trim(); if (!id) return toast("Informe o id"); try { const r = await api.send("POST", "/admin/auth/token-inicial", { id }); toast("Semear: " + (r.mensagem || "?")); } catch (e) { toast("Erro: " + e.message); } };

// ---------- workers / status ----------
function renderTiles(w) {
  const tiles = [["backlog", "fila"], ["rodando", "rodando"], ["ocupados", "workers ocup."], ["workers", "workers"], ["sucesso", "sucesso"], ["erro", "erro"]];
  const box = $("#tiles"); box.innerHTML = "";
  for (const [k, l] of tiles) box.appendChild(el("div", "tile", `<div class="n">${w[k] ?? 0}</div><div class="l">${l}</div>`));
}

function renderServicos(lista) {
  const box = $("#servicos"); box.innerHTML = "";
  for (const s of lista) {
    const rt = s.runtime || {};
    const badge = s.estado === "rodando" ? "badge-rodando" : s.estado === "erro" ? "badge-erro" : "badge-parado";
    const info = s.nome === "ws_comprasnet"
      ? `conectado: ${rt.conectado ? "sim" : "não"} · msgs: ${rt.mensagens ?? 0}${rt.ultimo_erro ? " · erro: " + esc(rt.ultimo_erro).slice(0, 60) : ""}` : "";
    const row = el("div", "svc");
    row.innerHTML = `<span class="svc-nome">${esc(s.nome)}</span>
      <span class="badge ${badge}">${esc(s.estado)}</span>
      <span class="kv">${info}</span>`;
    const acao = el("button", "btn btn-sm" + (s.estado === "rodando" ? " btn-danger" : " btn-primary"), s.estado === "rodando" ? "Parar" : "Iniciar");
    acao.style.marginLeft = "auto";
    acao.onclick = async () => { try { await api.send("POST", `/admin/servicos/${s.nome}/${s.estado === "rodando" ? "parar" : "iniciar"}`); atualizar(); } catch (e) { toast("Erro: " + e.message); } };
    row.appendChild(acao);
    box.appendChild(row);
  }
  if (!lista.length) box.appendChild(el("div", "kv", "Nenhum serviço."));
}

function renderExecucoes(lista) {
  const t = $("#tab-exec");
  t.innerHTML = `<thead><tr><th>#</th><th>Tarefa</th><th>Origem</th><th>Status</th><th>Início</th><th>Dur.</th><th>Resultado / erro</th><th></th></tr></thead>`;
  const tb = el("tbody");
  for (const e of lista) {
    const res = e.status === "erro" ? esc((e.erro || "").split("\n")[0]).slice(0, 80) : esc(e.resultado_json || "").slice(0, 80);
    const tr = el("tr");
    tr.innerHTML = `<td>${e.id}</td><td>${esc(e.tarefa)}</td><td>${esc(e.origem)}</td>
      <td class="st-${e.status}">${esc(e.status)}</td><td class="kv">${esc(e.iniciado_em || e.enfileirado_em || "")}</td>
      <td class="kv">${e.duracao_ms != null ? e.duracao_ms + "ms" : ""}</td><td class="kv">${res}</td><td></td>`;
    if (e.status === "fila") {
      const b = el("button", "btn btn-sm btn-danger", "Cancelar");
      b.onclick = async () => { try { await api.send("POST", `/admin/execucoes/${e.id}/cancelar`); atualizar(); } catch (err) { toast("Erro: " + err.message); } };
      tr.lastElementChild.appendChild(b);
    }
    tb.appendChild(tr);
  }
  t.appendChild(tb);
}

function renderAgendamentos(lista) {
  const t = $("#tab-ag");
  t.innerHTML = `<thead><tr><th>Nome</th><th>Tarefa</th><th>Cron</th><th>Próxima</th><th>Ativo</th><th>Ações</th></tr></thead>`;
  const tb = el("tbody");
  for (const a of lista) {
    const tr = el("tr");
    tr.innerHTML = `<td>${esc(a.nome || "—")}</td><td>${esc(a.tarefa)}</td><td class="kv">${esc(a.cron)}</td>
      <td class="kv">${esc(a.proxima_execucao || "—")}</td><td>${a.ativo ? "sim" : "não"}</td><td class="acoes"></td>`;
    const td = tr.querySelector(".acoes");
    const run = el("button", "btn btn-sm btn-primary", "Executar"); run.onclick = () => acaoAg("POST", `/admin/agendamentos/${a.id}/executar-agora`);
    const tog = el("button", "btn btn-sm", a.ativo ? "Pausar" : "Retomar"); tog.onclick = () => acaoAg("PATCH", `/admin/agendamentos/${a.id}`, { ativo: !a.ativo });
    const del = el("button", "btn btn-sm btn-danger", "Excluir"); del.onclick = () => acaoAg("DELETE", `/admin/agendamentos/${a.id}`);
    td.append(run, tog, del);
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  if (!lista.length) { const tr = el("tr"); tr.innerHTML = `<td colspan="6" class="kv">Nenhum agendamento.</td>`; tb.appendChild(tr); }
}
async function acaoAg(m, u, b) { try { await api.send(m, u, b); atualizar(); toast("Ok"); } catch (e) { toast("Erro: " + e.message); } }

// ---------- carga / polling ----------
async function carregarTarefas() {
  try {
    const ts = await api.get("/admin/tarefas");
    const sel = $("#ag-tarefa"); sel.innerHTML = "";
    for (const t of ts) { const o = el("option", null, t.rotulo); o.value = t.tarefa; sel.appendChild(o); }
  } catch {}
}
async function atualizar() {
  try {
    const st = await api.get("/admin/status");
    pintarModo(st.autenticador);
    renderTiles(st.workers);
    renderServicos(st.servicos);
    renderExecucoes(st.execucoes_recentes);
    renderAgendamentos(await api.get("/admin/agendamentos"));
    await carregarLogs();
  } catch (e) { /* silencioso no polling */ }
}

// ---------- controles ----------
async function ctrl(url, msg) { try { await api.send("POST", url); toast(msg); atualizar(); } catch (e) { toast("Erro: " + e.message); } }
$("#btn-rw").onclick = () => ctrl("/admin/workers/reiniciar", "Workers reiniciados");
$("#btn-rs").onclick = () => ctrl("/admin/scheduler/reiniciar", "Agendador reiniciado");
$("#btn-rt").onclick = () => { if (confirm("Reiniciar todo o background (workers, agendador, serviços)?")) ctrl("/admin/reiniciar", "Background reiniciado"); };
$("#btn-cancel-tudo").onclick = () => { if (confirm("Cancelar TODA a fila e sinalizar cancelamento dos processos em andamento?")) ctrl("/admin/execucoes/cancelar-tudo", "Cancelamento solicitado"); };

// ---------- logs ----------
let logOrigensCarregadas = false;
async function carregarLogs() {
  const origem = $("#log-origem").value || "";
  const r = await api.get("/admin/logs?limit=200" + (origem ? "&origem=" + encodeURIComponent(origem) : ""));
  if (!logOrigensCarregadas || $("#log-origem").options.length !== r.origens.length + 1) {
    const sel = $("#log-origem"); const atual = sel.value;
    sel.innerHTML = `<option value="">(todas as origens)</option>` + r.origens.map((o) => `<option value="${o}">${o}</option>`).join("");
    sel.value = atual; logOrigensCarregadas = true;
  }
  $("#logbox").textContent = r.logs.map((l) => `${l.ts} [${l.origem}/${l.nivel}] ${l.mensagem}`).join("\n");
}
$("#log-origem").onchange = carregarLogs;

$("#ag-add").onclick = async () => {
  let params = {};
  const raw = $("#ag-params").value.trim();
  if (raw) { try { params = JSON.parse(raw); } catch { return toast("params JSON inválido"); } }
  try {
    await api.send("POST", "/admin/agendamentos", {
      nome: $("#ag-nome").value.trim() || null, tarefa: $("#ag-tarefa").value,
      cron: $("#ag-cron").value.trim(), params,
    });
    $("#ag-nome").value = ""; $("#ag-params").value = "";
    toast("Agendado"); atualizar();
  } catch (e) { toast("Erro: " + e.message); }
};
$("#btn-exec").onclick = async () => {
  const tarefa = $("#ag-tarefa").value;
  let params = {}; const raw = $("#ag-params").value.trim();
  if (raw) { try { params = JSON.parse(raw); } catch { return toast("params JSON inválido"); } }
  try { await api.send("POST", "/admin/executar", { tarefa, params }); toast("Enfileirado"); atualizar(); }
  catch (e) { toast("Erro: " + e.message); }
};

carregarTarefas();
atualizar();
setInterval(atualizar, 4000);   // polling

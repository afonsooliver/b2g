"use strict";
// FastGov — Sala de Disputa (ao vivo). Itens em disputa/homologação + auto-lance + lance manual.

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const brl = (v) => v == null ? "—" : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(v);
const api = {
  async get(u) { const r = await fetch(u); if (!r.ok) throw new Error(r.status); return r.json(); },
  async send(m, u, b) { const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined }); if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json(); },
};
let toastT; function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 2600); }

const SIT = { P: "Perdendo", G: "Ganhando", V: "Vencedor" };

let wsRodando = false;
function pintarWs(ws) {
  const rt = ws.runtime || {}; wsRodando = ws.estado === "rodando";
  const b = $("#ws-badge");
  const conectado = rt.conectado;
  b.textContent = "WS " + (wsRodando ? (conectado ? "conectado" : "ligando…") : "parado");
  b.className = "badge " + (conectado ? "badge-rodando" : wsRodando ? "badge-debug" : "badge-parado");
  $("#ws-toggle").textContent = wsRodando ? "Parar WS" : "Iniciar WS";
}
$("#ws-toggle").onclick = async () => {
  try { await api.send("POST", `/admin/servicos/ws_comprasnet/${wsRodando ? "parar" : "iniciar"}`); atualizar(); }
  catch (e) { toast("Erro: " + e.message); }
};

function render(itens) {
  $("#info").textContent = `${itens.length} item(ns)`;
  const t = $("#tab");
  t.innerHTML = `<thead><tr><th>Compra</th><th>Item</th><th>Descrição</th><th>Etapa</th><th>Situação</th>
    <th class="num">Melhor geral</th><th class="num">Lance mín.</th><th class="num">Proposta</th><th class="num">Lucro</th>
    <th>Auto</th><th>Ações</th></tr></thead>`;
  const tb = el("tbody");
  for (const d of itens) {
    const tr = el("tr");
    const sit = d.situacao ? (SIT[d.situacao] || d.situacao) : "—";
    const sitCls = d.situacao === "P" ? "st-erro" : d.situacao ? "st-sucesso" : "";
    tr.innerHTML = `<td class="kv">${esc(d.compra)}</td><td>${d.numero}</td>
      <td class="col-desc">${esc((d.descricao || "").slice(0, 40))}</td>
      <td>${esc(d.etapa)}</td><td class="${sitCls}">${sit}</td>
      <td class="num">${brl(d.melhor_geral)}</td><td class="num">${brl(d.valor_proposta_minima)}</td>
      <td class="num">${brl(d.valor_proposta_atual)}</td><td class="num">${brl(d.lucro_proposta_minima)}</td>
      <td></td><td class="acoes"></td>`;
    // auto-lance toggle
    const chk = el("input"); chk.type = "checkbox"; chk.checked = !!d.lance_automatico;
    chk.onchange = async () => {
      try { await api.send("PATCH", `/admin/disputa/${d.compra}/${d.numero}`, { lance_automatico: chk.checked }); toast("Auto-lance " + (chk.checked ? "ON" : "OFF")); }
      catch (e) { toast("Erro: " + e.message); chk.checked = !chk.checked; }
    };
    tr.children[9].appendChild(chk);
    // recalcular custo
    const rec = el("button", "btn btn-sm", "Recalcular");
    rec.onclick = async () => { try { await api.send("POST", `/itens/${d.item_id}/precificar`); toast("Recalculado"); atualizar(); } catch (e) { toast("Erro: " + e.message); } };
    tr.querySelector(".acoes").appendChild(rec);
    tb.appendChild(tr);
  }
  if (!itens.length) { const tr = el("tr"); tr.innerHTML = `<td colspan="11" class="kv">Nenhum item em disputa/homologação. Mova itens para "Disputa" na tela de Licitações.</td>`; tb.appendChild(tr); }
  t.appendChild(tb);
}

async function atualizar() {
  try { const d = await api.get("/admin/sala-disputa"); pintarWs(d.ws); render(d.itens); }
  catch (e) { /* silencioso no polling */ }
}
atualizar();
setInterval(atualizar, 4000);

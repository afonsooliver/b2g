"use strict";
// FastGov — etapa no topo; lateral lista editais da etapa; detalhe mostra só os itens do edital selecionado.
// "Inativo" = item inativo NA ETAPA (mantém a etapa para histórico).

/**
 * @typedef {"selecao"|"proposta"|"disputa"|"homologacao"|"homologado"} Etapa
 * @typedef {Object} Item
 * @property {string} id
 * @property {number} numero
 * @property {Etapa} etapa
 * @property {0|1} inativo
 * @property {string|null} inativado_em
 * @property {string|null} descricao
 * @property {string|null} descricao_detalhada
 * @property {"GRUPO"|"ITEM"|null} item_ou_grupo
 * @property {number|null} quantidade
 * @property {string|null} unidade
 * @property {number|null} valor_unitario_estimado_edital
 * @property {number|null} valor_estimado_total
 * @property {"P"|"V"|null} intervalo_lance_tipo_codigo
 * @property {number|null} intervalo_lance_valor
 * @typedef {Object} EditalResumo
 * @property {string} id
 * @property {string} numero
 * @property {number} ano_compra
 * @property {string} uasg_codigo
 * @property {string|null} descricao
 * @property {string|null} modo_disputa
 * @property {string|null} modo_disputa_descricao
 * @property {number} n_itens
 * @property {number} n_inativos
 */

/** @type {{id:Etapa,label:string}[]} */
const ETAPAS = [
  { id: "selecao",     label: "Seleção" },
  { id: "proposta",    label: "Proposta" },
  { id: "disputa",     label: "Disputa" },
  { id: "homologacao", label: "Homologação" },
  { id: "homologado",  label: "Homologado" },
];
const LABEL = Object.fromEntries(ETAPAS.map((e) => [e.id, e.label]));
const idx = (et) => ETAPAS.findIndex((e) => e.id === et);
const proxima = (et) => { const i = idx(et); return i >= 0 && i < ETAPAS.length - 1 ? ETAPAS[i + 1].id : null; };
const anterior = (et) => { const i = idx(et); return i > 0 ? ETAPAS[i - 1].id : null; };

const PAGE_SIZES = [25, 50, 100, 200];
function lsInt(k, def) { try { const v = parseInt(localStorage.getItem(k)); return PAGE_SIZES.includes(v) ? v : def; } catch { return def; } }
function salvarTamanhos() { try { localStorage.setItem("fg_ed_limit", String(state.edLimit)); localStorage.setItem("fg_it_size", String(state.itPorPagina)); } catch {} }

const state = {
  /** @type {string} */ aba: "selecao",
  /** @type {Record<string,number>} */ porEtapa: {},
  /** @type {EditalResumo[]} */ editais: [],
  /** @type {string|null} */ selEditalId: null,
  editaisTotal: 0,
  edLimit: lsInt("fg_ed_limit", 50),   // editais por página (lateral)
  edOffset: 0,
  /** @type {Item[]} */ itens: [],
  /** @type {"ativos"|"inativos"|"todos"} */ situacao: "ativos", // filtro PRINCIPAL
  itPorPagina: lsInt("fg_it_size", 50), // itens por página (detalhe)
  itPagina: 1,
};

// ---------- API ----------
const api = {
  async get(u) { const r = await fetch(u); if (!r.ok) throw await err(r); return r.json(); },
  async send(m, u, b) { const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined }); if (!r.ok) throw await err(r); return r.json(); },
};
async function err(r) { let d = ""; try { d = (await r.json()).detail || ""; } catch {} return new Error(`${r.status} ${d}`); }

// ---------- utils ----------
const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const brl = (v) => v == null ? "" : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(v);
/** @param {Item} it */
function intervalo(it) {
  if (it.intervalo_lance_valor == null) return "";
  return it.intervalo_lance_tipo_codigo === "P"
    ? `${String(it.intervalo_lance_valor).replace(".", ",")}%` : brl(it.intervalo_lance_valor);
}
let toastT;
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 2600); }

/**
 * Rodapé de paginação reutilizável (lateral e tabela de itens).
 * @param {{page:number,pageSize:number,total:number,onPage:(p:number)=>void,onSize:(s:number)=>void}} o
 */
function buildPager({ page, pageSize, total, onPage, onSize }) {
  const nPages = Math.max(1, Math.ceil(total / pageSize));
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(total, page * pageSize);
  const wrap = el("div", "pager");
  const prev = el("button", "btn btn-sm", "‹"); prev.title = "Anterior"; prev.disabled = page <= 1; prev.onclick = () => onPage(page - 1);
  const next = el("button", "btn btn-sm", "›"); next.title = "Próxima"; next.disabled = page >= nPages; next.onclick = () => onPage(page + 1);
  const info = el("span", "pg-info", `${from}–${to} de ${total}`);
  const size = el("label", "pg-size", "por página ");
  const sel = el("select");
  for (const s of PAGE_SIZES) { const o = el("option", null, String(s)); o.value = String(s); if (s === pageSize) o.selected = true; sel.appendChild(o); }
  sel.onchange = () => onSize(parseInt(sel.value));
  size.appendChild(sel);
  wrap.append(prev, next, info, size);
  return wrap;
}

function filtrosQS() {
  const p = new URLSearchParams();
  const q = $("#f-q").value.trim(), uf = $("#f-uf").value.trim().toUpperCase(), mod = $("#f-modalidade").value.trim();
  if (q) p.set("q", q); if (uf) p.set("uf", uf); if (mod) p.set("modalidade", mod);
  return p;
}

// ---------- carga ----------
let PRODUTOS = [];
async function carregar() {
  try {
    if (!PRODUTOS.length) { try { PRODUTOS = await api.get("/produtos?limit=1000"); } catch {} }
    await Promise.all([carregarResumo(), carregarEditais()]);
  } catch (e) { toast("Erro: " + e.message); }
}
async function carregarResumo() {
  const r = await api.get(`/etapas?${filtrosQS()}`);
  state.porEtapa = r.por_etapa; renderTabs();
}
async function carregarEditais() {
  const p = filtrosQS(); p.set("situacao", state.situacao);
  p.set("limit", String(state.edLimit)); p.set("offset", String(state.edOffset));
  const r = await api.get(`/etapas/${state.aba}/editais?${p}`);
  // tolerante: aceita envelope {total, editais} ou lista simples (compat.)
  state.editais = Array.isArray(r) ? r : (Array.isArray(r?.editais) ? r.editais : []);
  state.editaisTotal = Array.isArray(r) ? r.length : (typeof r?.total === "number" ? r.total : state.editais.length);
  if (!state.editais.some((e) => e.id === state.selEditalId))
    state.selEditalId = state.editais.length ? state.editais[0].id : null;
  renderEditais(); renderEdPager();
  await carregarItens();
}
async function carregarItens() {
  if (!state.selEditalId) { state.itens = []; renderDetalhe(); return; }
  state.itens = await api.get(
    `/etapas/${state.aba}/compras/${state.selEditalId}/itens?situacao=${state.situacao}`);
  state.itPagina = 1;
  renderDetalhe();
}

// ---------- abas (etapas) ----------
function renderTabs() {
  const nav = $("#etapa-tabs"); nav.innerHTML = "";
  for (const e of ETAPAS) {
    const b = el("button", "tab" + (e.id === state.aba ? " active" : ""));
    b.style.setProperty("--tab-c", `var(--e-${e.id})`);
    b.innerHTML = `${e.label} <span class="count">${state.porEtapa[e.id] || 0}</span>`;
    b.onclick = () => { if (state.aba !== e.id) { state.aba = e.id; state.edOffset = 0; renderTabs(); carregarEditais(); } };
    nav.appendChild(b);
  }
}

// ---------- lateral (editais da etapa) ----------
function renderEdPager() {
  const box = $("#ed-pager"); box.innerHTML = "";
  if (!state.editaisTotal) return;
  box.appendChild(buildPager({
    page: Math.floor(state.edOffset / state.edLimit) + 1,
    pageSize: state.edLimit, total: state.editaisTotal,
    onPage: (p) => { state.edOffset = (p - 1) * state.edLimit; carregarEditais(); },
    onSize: (s) => { state.edLimit = s; state.edOffset = 0; salvarTamanhos(); carregarEditais(); },
  }));
}

function renderEditais() {
  const box = $("#edital-list"); box.innerHTML = "";
  $("#info").textContent = `${state.editaisTotal} edital(is) em ${LABEL[state.aba]}`;
  if (!state.editais.length) { box.appendChild(el("div", "placeholder muted", "Nenhum edital nesta etapa.")); return; }
  for (const e of state.editais) {
    const card = el("button", "ed-card" + (e.id === state.selEditalId ? " sel" : ""));
    card.innerHTML = `
      <div class="ed-tit">${esc(e.numero)}/${esc(e.ano_compra)} · UASG ${esc(e.uasg_codigo)}</div>
      <div class="ed-desc">${esc((e.descricao || "").slice(0, 80))}</div>
      <div class="ed-meta">
        <span class="badge badge-info">${e.n_itens} item(ns)</span>
        ${e.n_inativos ? `<span class="badge badge-inativo">${e.n_inativos} inativo(s)</span>` : ""}
        ${e.modo_disputa ? `<span class="badge">${esc(e.modo_disputa_descricao || e.modo_disputa)}</span>` : ""}
      </div>`;
    card.onclick = () => { state.selEditalId = e.id; renderEditais(); carregarItens(); };
    box.appendChild(card);
  }
}

// ---------- detalhe (itens do edital selecionado) ----------
function renderDetalhe() {
  const box = $("#detalhe"); box.innerHTML = "";
  const ed = state.editais.find((e) => e.id === state.selEditalId);
  if (!ed) { box.appendChild(el("div", "placeholder muted", "Selecione um edital à esquerda.")); return; }

  const head = el("div", "det-head");
  head.innerHTML = `
    <div>
      <h3>${esc(ed.numero)}/${esc(ed.ano_compra)} · UASG ${esc(ed.uasg_codigo)}</h3>
      <div class="d-sub">${esc(ed.descricao || "")}</div>
    </div>
    <div class="det-tools">
      ${ed.modo_disputa ? `<span class="badge badge-info">Disputa: ${esc(ed.modo_disputa_descricao || ed.modo_disputa)}</span>` : ""}
      <label class="kv">Frete:
        <select id="sel-frete" class="sel-pg">
          <option value="mais_barato">mais barato</option>
          <option value="PAC">PAC</option>
        </select></label>
      ${ed.n_inativos ? `<span class="badge badge-inativo">${ed.n_inativos} inativo(s)</span>` : ""}
    </div>`;
  box.appendChild(head);
  const selFrete = head.querySelector("#sel-frete");
  selFrete.value = ed.frete_servico || "mais_barato";
  selFrete.onchange = async () => {
    try { await api.send("PATCH", `/compras/${ed.id}/frete-servico`, { frete_servico: selFrete.value }); toast("Frete: " + selFrete.value); }
    catch (e) { toast("Erro: " + e.message); }
  };

  if (!state.itens.length) { box.appendChild(el("div", "vazio", "Nenhum item nesta etapa.")); return; }

  const mostrarProduto = ["selecao", "proposta"].includes(state.aba);
  const mostrarCustos = ["proposta", "disputa", "homologacao", "homologado"].includes(state.aba);

  const nPages = Math.max(1, Math.ceil(state.itens.length / state.itPorPagina));
  if (state.itPagina > nPages) state.itPagina = nPages;
  const ini = (state.itPagina - 1) * state.itPorPagina;
  const slice = state.itens.slice(ini, ini + state.itPorPagina);

  const wrap = el("div", "tabela-wrap");
  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>#</th><th>Descrição</th><th class="num">Qtd</th><th>Un.</th>
    ${mostrarProduto ? "<th>Produto</th>" : ""}
    <th class="num">Valor estimado</th>
    ${mostrarCustos ? '<th class="num">Lance mín.</th><th class="num">Proposta</th><th class="num">Lucro</th>' : ""}
    <th class="num">Δ lance</th><th>Tipo</th><th>Ações</th>
  </tr></thead>`;
  const tb = el("tbody");
  for (const it of slice) tb.appendChild(linha(it, mostrarProduto, mostrarCustos));
  table.appendChild(tb);
  wrap.appendChild(table);
  box.appendChild(wrap);

  box.appendChild(buildPager({
    page: state.itPagina, pageSize: state.itPorPagina, total: state.itens.length,
    onPage: (p) => { state.itPagina = p; renderDetalhe(); },
    onSize: (s) => { state.itPorPagina = s; state.itPagina = 1; salvarTamanhos(); renderDetalhe(); },
  }));
}

/** @param {Item} it */
function linha(it, mostrarProduto, mostrarCustos) {
  const inativo = !!it.inativo;
  const tr = el("tr", inativo ? "inativo" : "");
  const tipo = inativo ? `<span class="badge badge-inativo">Inativo</span>`
    : (it.item_ou_grupo ? `<span class="badge ${it.item_ou_grupo === "GRUPO" ? "badge-grupo" : "badge-item"}">${it.item_ou_grupo}</span>` : "—");
  const valEst = it.valor_estimado_total != null ? brl(it.valor_estimado_total)
    : (it.valor_unitario_estimado_edital != null ? `un. ${brl(it.valor_unitario_estimado_edital)}` : "—");
  const custos = mostrarCustos
    ? `<td class="num val" data-label="Lance mín.">${brl(it.valor_proposta_minima)}</td>
       <td class="num" data-label="Proposta">${brl(it.valor_proposta_atual)}</td>
       <td class="num" data-label="Lucro">${brl(it.lucro_proposta_minima)}</td>` : "";

  tr.innerHTML = `
    <td class="col-num" data-label="#">${esc(it.numero)}</td>
    <td class="col-desc" data-label="Descrição"><div class="d1">${esc(it.descricao || "—")}</div>
      ${it.descricao_detalhada ? `<div class="d2">${esc(it.descricao_detalhada)}</div>` : ""}</td>
    <td class="num" data-label="Qtd">${it.quantidade ?? ""}</td>
    <td data-label="Unidade">${esc(it.unidade || "")}</td>
    ${mostrarProduto ? '<td class="td-prod" data-label="Produto"></td>' : ""}
    <td class="num val" data-label="Valor estimado">${valEst}</td>
    ${custos}
    <td class="num" data-label="Δ lance">${esc(intervalo(it) || "—")}</td>
    <td data-label="Tipo">${tipo}</td>
    <td class="acoes" data-label="Ações"></td>`;

  if (mostrarProduto && !inativo) {
    const sel = el("select", "sel-pg");
    sel.innerHTML = `<option value="">(produto…)</option>` +
      PRODUTOS.map((p) => `<option value="${p.id}">${esc(p.nome)}</option>`).join("");
    sel.value = it.produto_id || "";
    sel.onchange = async () => {
      try { await api.send("PATCH", `/itens/${it.id}/produto`, { produto_id: sel.value ? parseInt(sel.value) : null }); toast("Produto atualizado"); carregarItens(); }
      catch (e) { toast("Erro: " + e.message); }
    };
    tr.querySelector(".td-prod").appendChild(sel);
  }

  const td = tr.querySelector(".acoes");
  if (inativo) {
    const b = el("button", "btn btn-sm", "↩ Reativar"); b.onclick = () => reativar(it.id); td.appendChild(b);
  } else {
    const prev = anterior(it.etapa), next = proxima(it.etapa);
    const bVoltar = el("button", "btn btn-sm", "← Voltar");
    bVoltar.disabled = !prev; bVoltar.title = prev ? `Voltar p/ ${LABEL[prev]}` : "Primeira etapa";
    bVoltar.onclick = () => moverItem(it.id, prev);
    const bAvancar = el("button", "btn btn-sm btn-primary", "Avançar →");
    bAvancar.disabled = !next; bAvancar.title = next ? `Avançar p/ ${LABEL[next]}` : "Última etapa";
    bAvancar.onclick = () => moverItem(it.id, next);
    const bInativar = el("button", "btn btn-sm btn-danger", "Inativar"); bInativar.onclick = () => inativar(it.id);
    td.append(bVoltar, bAvancar, bInativar);
  }
  return tr;
}

// ---------- ações ----------
async function moverItem(id, etapa) {
  if (!etapa) return;
  try {
    if (etapa === "proposta") toast("Detalhando item…");
    await api.send("PATCH", `/itens/${id}/etapa`, { etapa });
    await carregar();
    toast(`Movido para ${LABEL[etapa]}`);
  } catch (e) { toast("Erro ao mover: " + e.message); }
}
async function inativar(id) { try { await api.send("POST", `/itens/${id}/inativar`); await carregar(); toast("Item inativado na etapa"); } catch (e) { toast("Erro: " + e.message); } }
async function reativar(id) { try { await api.send("POST", `/itens/${id}/reativar`); await carregar(); toast("Item reativado"); } catch (e) { toast("Erro: " + e.message); } }

// ---------- ingestão ----------
function abrirModal() { $("#ing-data").value = new Date().toISOString().slice(0, 10); $("#modal").classList.remove("hidden"); }
async function confirmarIngest() {
  const body = { data_publicacao_inicio: $("#ing-data").value, com_itens: $("#ing-itens").checked };
  const mod = $("#ing-modalidades").value.trim();
  if (mod) body.modalidades = mod.split(",").map((s) => parseInt(s.trim())).filter((n) => !isNaN(n));
  $("#modal").classList.add("hidden");
  toast("Ingerindo do PNCP… (pode levar um tempo)");
  try { const r = await api.send("POST", "/ingest", body);
    toast(`Ingestão: ${r.editais_novos} novos / ${r.editais_processados} editais · ${r.itens_baixados} itens`);
    await carregar();
  } catch (e) { toast("Erro na ingestão: " + e.message); }
}

// ---------- eventos ----------
function aplicarFiltro() { state.edOffset = 0; state.selEditalId = null; carregar(); }
$("#btn-buscar").onclick = aplicarFiltro;
for (const sel of ["#f-q", "#f-uf", "#f-modalidade"])
  $(sel).addEventListener("keydown", (e) => { if (e.key === "Enter") aplicarFiltro(); });
$("#f-situacao").addEventListener("change", (e) => { state.situacao = e.target.value; aplicarFiltro(); });
$("#btn-ingest").onclick = abrirModal;
$("#ing-cancelar").onclick = () => $("#modal").classList.add("hidden");
$("#ing-confirmar").onclick = confirmarIngest;

// badge de modo (debug/produção) no topbar
(async () => {
  try {
    const st = await api.get("/admin/modo");
    const b = $("#modo-badge");
    if (b) { b.textContent = st.debug ? "DEBUG" : "PRODUÇÃO"; b.className = "badge " + (st.debug ? "badge-debug" : "badge-prod"); }
  } catch {}
})();

carregar();

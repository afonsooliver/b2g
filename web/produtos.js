"use strict";
// FastGov — Catálogos & Produtos (CRUD + upload de imagem).

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = {
  async get(u) { const r = await fetch(u); if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json(); },
  async send(m, u, b) { const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined }); if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json(); },
};
let toastT; function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 2600); }

let catalogos = [], produtos = [], editId = null;

// ---------- catálogos ----------
async function carregarCatalogos() {
  catalogos = await api.get("/catalogos");
  renderCatalogos();
  // popula selects
  const opts = `<option value="">(todos)</option>` + catalogos.map((c) => `<option value="${c.id}">${esc(c.nome)}</option>`).join("");
  $("#f-cat").innerHTML = opts;
  $("#p-cat").innerHTML = `<option value="">(sem catálogo)</option>` + catalogos.map((c) => `<option value="${c.id}">${esc(c.nome)}</option>`).join("");
}
function renderCatalogos() {
  const t = $("#tab-cat");
  t.innerHTML = `<thead><tr><th>Nome</th><th>Descrição</th><th></th></tr></thead>`;
  const tb = el("tbody");
  for (const c of catalogos) {
    const tr = el("tr");
    tr.innerHTML = `<td>${esc(c.nome)}</td><td class="kv">${esc(c.descricao || "")}</td><td class="acoes"></td>`;
    const del = el("button", "btn btn-sm btn-danger", "Excluir");
    del.onclick = async () => { if (confirm("Excluir catálogo?")) { await api.send("DELETE", `/catalogos/${c.id}`); carregarCatalogos(); } };
    tr.querySelector(".acoes").appendChild(del);
    tb.appendChild(tr);
  }
  t.appendChild(tb);
}
$("#c-add").onclick = async () => {
  const nome = $("#c-nome").value.trim(); if (!nome) return toast("Informe o nome");
  await api.send("POST", "/catalogos", { nome, descricao: $("#c-desc").value.trim() || null });
  $("#c-nome").value = ""; $("#c-desc").value = ""; toast("Catálogo criado"); carregarCatalogos();
};

// ---------- produtos ----------
async function carregarProdutos() {
  const p = new URLSearchParams();
  if ($("#f-cat").value) p.set("catalogo_id", $("#f-cat").value);
  if ($("#f-q").value.trim()) p.set("q", $("#f-q").value.trim());
  produtos = await api.get(`/produtos?${p}`);
  renderProdutos();
}
function renderProdutos() {
  const t = $("#tab-prod");
  t.innerHTML = `<thead><tr><th>Img</th><th>Nome</th><th>NCM</th><th class="num">Custo</th><th class="num">Margem%</th><th class="num">Peso(g)</th><th>Dim (A×L×C)</th><th>Ações</th></tr></thead>`;
  const tb = el("tbody");
  for (const p of produtos) {
    const tr = el("tr");
    const img = p.imagem_path ? `<img src="${p.imagem_path}" style="height:32px;border-radius:4px">` : "—";
    tr.innerHTML = `<td>${img}</td><td>${esc(p.nome)}</td><td class="kv">${esc(p.ncm || "")}</td>
      <td class="num">${p.custo_produto_unitario ?? "—"}</td><td class="num">${p.margem_min_lucro ?? "—"}</td>
      <td class="num">${p.peso_unitario ?? "—"}</td>
      <td class="kv">${[p.altura_cm, p.largura_cm, p.comprimento_cm].map((x) => x ?? "?").join("×")}</td>
      <td class="acoes"></td>`;
    const ed = el("button", "btn btn-sm", "Editar"); ed.onclick = () => abrirModal(p);
    const del = el("button", "btn btn-sm btn-danger", "Excluir");
    del.onclick = async () => { if (confirm("Excluir produto?")) { await api.send("DELETE", `/produtos/${p.id}`); carregarProdutos(); } };
    tr.querySelector(".acoes").append(ed, del);
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  if (!produtos.length) { const tr = el("tr"); tr.innerHTML = `<td colspan="8" class="kv">Nenhum produto.</td>`; tb.appendChild(tr); }
}
$("#f-buscar").onclick = carregarProdutos;
$("#f-q").addEventListener("keydown", (e) => { if (e.key === "Enter") carregarProdutos(); });
$("#f-cat").onchange = carregarProdutos;

// ---------- modal ----------
function abrirModal(p) {
  editId = p ? p.id : null;
  $("#m-titulo").textContent = p ? "Editar produto" : "Novo produto";
  $("#p-nome").value = p?.nome || ""; $("#p-cat").value = p?.catalogo_id || "";
  $("#p-ncm").value = p?.ncm || ""; $("#p-custo").value = p?.custo_produto_unitario ?? "";
  $("#p-customax").value = p?.custo_maximo_unitario ?? ""; $("#p-margem").value = p?.margem_min_lucro ?? "";
  $("#p-peso").value = p?.peso_unitario ?? ""; $("#p-alt").value = p?.altura_cm ?? "";
  $("#p-larg").value = p?.largura_cm ?? ""; $("#p-comp").value = p?.comprimento_cm ?? "";
  $("#p-desc").value = p?.descricao || ""; $("#p-img").value = "";
  const prev = $("#p-preview");
  if (p?.imagem_path) { prev.src = p.imagem_path; prev.style.display = "inline"; } else prev.style.display = "none";
  $("#modal").classList.remove("hidden");
}
$("#p-novo").onclick = () => abrirModal(null);
$("#m-cancelar").onclick = () => $("#modal").classList.add("hidden");
$("#p-img").onchange = (e) => { const f = e.target.files[0]; if (f) { const pr = $("#p-preview"); pr.src = URL.createObjectURL(f); pr.style.display = "inline"; } };

$("#m-salvar").onclick = async () => {
  const num = (id) => { const v = $(id).value; return v === "" ? null : parseFloat(v); };
  const body = {
    nome: $("#p-nome").value.trim(),
    catalogo_id: $("#p-cat").value ? parseInt($("#p-cat").value) : null,
    ncm: $("#p-ncm").value.trim() || null,
    custo_produto_unitario: num("#p-custo"), custo_maximo_unitario: num("#p-customax"),
    margem_min_lucro: num("#p-margem"), peso_unitario: num("#p-peso"),
    altura_cm: num("#p-alt"), largura_cm: num("#p-larg"), comprimento_cm: num("#p-comp"),
    descricao: $("#p-desc").value.trim() || null,
  };
  if (!body.nome) return toast("Informe o nome");
  try {
    const prod = editId ? await api.send("PATCH", `/produtos/${editId}`, body) : await api.send("POST", "/produtos", body);
    const file = $("#p-img").files[0];
    if (file) {
      const fd = new FormData(); fd.append("arquivo", file);
      await fetch(`/produtos/${prod.id}/imagem`, { method: "POST", body: fd });
    }
    $("#modal").classList.add("hidden"); toast("Produto salvo"); carregarProdutos();
  } catch (e) { toast("Erro: " + e.message); }
};

carregarCatalogos().then(carregarProdutos);

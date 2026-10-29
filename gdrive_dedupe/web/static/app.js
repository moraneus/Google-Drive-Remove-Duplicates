const $ = (sel) => document.querySelector(sel);
let currentResult = null;

// ---- access token (only set when the server is not bound to loopback) ----
// The CLI prints a URL like http://host:port/?token=…; keep it for this tab
// and strip it from the address bar.
const TOKEN_KEY = "gdrive-dedupe-token";
(function adoptToken() {
  const params = new URLSearchParams(location.search);
  const token = params.get("token");
  if (token) {
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch (_) { /* private mode */ }
    params.delete("token");
    const rest = params.toString();
    history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : ""));
  }
})();
function accessToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = accessToken();
  if (token) headers["X-Dedupe-Token"] = token;
  const resp = await fetch(path, { headers, ...options });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

function show(id, visible) {
  $(id).hidden = !visible;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

const fmt = new Intl.NumberFormat();
const n = (value) => fmt.format(value);
const plural = (count, one, many) => `${n(count)} ${count === 1 ? one : many}`;

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${name}`);
  svg.appendChild(use);
  return svg;
}

function pill(kind, text) {
  const span = document.createElement("span");
  span.className = `pill ${kind}`;
  if (kind === "guard") span.appendChild(icon("i-lock"));
  span.append(text);
  return span;
}

// The side rail mirrors progress through the four stages.
function setRail(current, done) {
  const order = ["connect", "scan", "review", "clean"];
  for (const step of order) {
    const li = $(`#rail-${step}`);
    li.classList.remove("is-current", "is-done", "is-locked");
    const mark = li.querySelector(".mark");
    if (done.includes(step)) {
      li.classList.add("is-done");
      mark.replaceChildren(icon("i-check"));
    } else {
      mark.textContent = order.indexOf(step) + 1;
      if (step === current) li.classList.add("is-current");
      else li.classList.add("is-locked");
    }
  }
  const toolsReady = done.includes("scan");
  $("#rail-tools").hidden = !toolsReady;
  $("#rail-tools-title").hidden = !toolsReady;
}

function setConnection(text, on) {
  $("#conn-text").textContent = text;
  $("#conn").classList.toggle("is-on", on);
}

// ---- connect ----

async function refreshAuth() {
  let status;
  try {
    status = await api("/api/auth/status");
  } catch (err) {
    $("#auth-info").textContent = `Error: ${err.message}`;
    return { connected: false };
  }
  const connected = status.connected;
  if (connected) {
    const mode = status.write_access ? "write access granted" : "read-only";
    $("#auth-info").textContent = `Connected, ${mode}.`;
    setConnection(`Connected · ${mode}`, true);
  } else if (status.has_client_secrets) {
    $("#auth-info").textContent = "Your OAuth client is in place. Connect to continue.";
    setConnection("Not connected", false);
  } else {
    $("#auth-info").textContent = "Not connected yet. A one-time setup comes first.";
    setConnection("Not connected", false);
  }
  if (!currentResult) setRail(connected ? "scan" : "connect", connected ? ["connect"] : []);
  show("#setup-panel", !connected && !status.has_client_secrets);
  $("#btn-connect").hidden = connected;
  $("#btn-connect").disabled = !status.has_client_secrets;
  $("#btn-disconnect").hidden = !connected;
  if (status.auth_url) {
    $("#auth-link-a").href = status.auth_url;
    show("#auth-link", true);
  } else {
    show("#auth-link", false);
  }
  show("#stage-scan", connected);
  return status;
}

async function uploadCredentials(event) {
  const file = event.target.files[0];
  if (!file) return;
  const content = await file.text();
  try {
    await api("/api/auth/credentials", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    $("#upload-result").textContent = "Saved. Now connect below.";
    await refreshAuth();
    show("#setup-panel", true); // keep the success message visible until they connect
  } catch (err) {
    $("#upload-result").textContent = `${err.message}`;
  } finally {
    event.target.value = "";
  }
}

async function connect(writeAccess) {
  await api("/api/auth/connect", {
    method: "POST",
    body: JSON.stringify({ write_access: writeAccess }),
  });
  $("#auth-info").textContent = "A Google consent tab should open. Approve access there…";
  const poll = setInterval(async () => {
    const status = await refreshAuth();
    if (writeAccess ? status.write_access : status.connected) clearInterval(poll);
  }, 1500);
}

// ---- scan ----

// Follows the scan's progress stream. Also used on page load: the server
// answers with the current state, so a running scan is resumed and a finished
// one is shown again without rescanning.
function watchScan() {
  const token = accessToken();
  const url = token ? `/api/scan/events?token=${encodeURIComponent(token)}` : "/api/scan/events";
  const source = new EventSource(url);
  source.onmessage = async (event) => {
    const snap = JSON.parse(event.data);
    if (snap.status === "idle" || snap.status === "connecting") {
      source.close();
    } else if (snap.status === "scanning") {
      const count = snap.progress.files_fetched || 0;
      $("#scan-progress").textContent = `Scanning… ${plural(count, "file", "files")} so far`;
    } else if (snap.status === "done") {
      source.close();
      $("#scan-progress").textContent = "";
      await loadFolders();
      await loadResult();
      await loadBatches();
    } else if (snap.status === "error") {
      source.close();
      $("#scan-progress").textContent = `Scan failed: ${snap.error}`;
      $("#scan-progress").classList.add("is-error");
    }
  };
  source.onerror = () => {
    source.close();
    $("#scan-progress").textContent = "Lost connection to the scan — reload the page.";
  };
}

async function startScan() {
  $("#scan-progress").textContent = "Starting…";
  $("#scan-progress").classList.remove("is-error");
  setRail("scan", ["connect"]);
  try {
    await api("/api/scan", { method: "POST" });
  } catch (err) {
    $("#scan-progress").textContent = `Couldn’t start the scan: ${err.message}`;
    $("#scan-progress").classList.add("is-error");
    return;
  }
  watchScan();
}

// ---- folders, scope & keep rules ----

const folders = { byPath: new Map(), byId: new Map() };
const protectedIds = new Set();

async function loadFolders() {
  const body = await api("/api/folders");
  folders.byPath.clear();
  folders.byId.clear();
  const list = $("#folder-list");
  list.innerHTML = "";
  for (const f of body.folders) {
    folders.byPath.set(f.path, f.id);
    folders.byId.set(f.id, f.path);
    const opt = document.createElement("option");
    opt.value = f.path;
    list.appendChild(opt);
  }
}

// Resolve a typed folder path to an id; "" means "none". Throws on unknown.
function folderIdFromInput(sel, label) {
  const raw = $(sel).value.trim();
  if (!raw) return "";
  const path = "/" + raw.replace(/^\/+|\/+$/g, "");
  const id = folders.byPath.get(path);
  if (!id) throw new Error(`${label}: no folder "${path}" in your Drive`);
  return id;
}

function rules() {
  return {
    folder: folderIdFromInput("#scope-folder", "Look only in"),
    prefer: folderIdFromInput("#prefer-folder", "Preferring copies in"),
    protected: [...protectedIds],
  };
}

function scopeQuery() {
  try {
    const { folder } = rules();
    return folder ? `&folder=${encodeURIComponent(folder)}` : "";
  } catch (_) {
    return "";
  }
}

function renderProtected() {
  const box = $("#protect-list");
  box.innerHTML = "";
  for (const id of protectedIds) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = folders.byId.get(id) || id;
    const x = document.createElement("button");
    x.type = "button";
    x.setAttribute("aria-label", `Stop protecting ${folders.byId.get(id) || id}`);
    x.appendChild(icon("i-x"));
    x.addEventListener("click", () => { protectedIds.delete(id); renderProtected(); loadResult(); });
    chip.appendChild(x);
    box.appendChild(chip);
  }
}

function addProtected() {
  try {
    const id = folderIdFromInput("#protect-folder", "Never trash anything in");
    if (!id) return;
    protectedIds.add(id);
    $("#protect-folder").value = "";
    renderProtected();
    loadResult();
  } catch (err) {
    showRulesError(err.message);
  }
}

function showRulesError(message) {
  const el = $("#rules-error");
  el.textContent = message || "";
  el.hidden = !message;
}

let resultRequest = 0; // rules can change faster than responses arrive; keep only the latest

async function loadResult() {
  let r;
  try {
    r = rules();
  } catch (err) {
    showRulesError(err.message);
    return;
  }
  showRulesError("");
  const strategy = $("#strategy").value;
  let query = `strategy=${strategy}`;
  if (r.folder) query += `&folder=${encodeURIComponent(r.folder)}`;
  if (r.prefer) query += `&prefer=${encodeURIComponent(r.prefer)}`;
  for (const id of r.protected) query += `&protected=${encodeURIComponent(id)}`;
  const seq = ++resultRequest;
  let result;
  try {
    result = await api(`/api/scan/result?${query}`);
  } catch (err) {
    if (seq === resultRequest) showRulesError(err.message);
    return;
  }
  if (seq !== resultRequest) return; // a newer request superseded this one
  currentResult = result;
  renderResult();
  clearTools();
}

const dateFmt = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });
function shortDate(iso) {
  const d = new Date(iso || "");
  return Number.isNaN(d.getTime()) ? "" : dateFmt.format(d);
}

// Folder paths can mix scripts (Hebrew, Arabic…); isolating each segment in
// <bdi> keeps every folder name readable without reordering the slashes.
function pathNode(path) {
  const frag = document.createDocumentFragment();
  const parts = (path || "/").split("/").filter(Boolean);
  if (!parts.length) {
    frag.append("/");
    return frag;
  }
  for (const part of parts) {
    frag.append("/");
    const bdi = document.createElement("bdi");
    bdi.textContent = part;
    frag.appendChild(bdi);
  }
  return frag;
}

// Link to the folder that holds the item, in Google Drive.
function folderLink(path, parentId) {
  const a = document.createElement("a");
  a.href = parentId ? `https://drive.google.com/drive/folders/${encodeURIComponent(parentId)}`
                    : "https://drive.google.com/drive/my-drive";
  a.target = "_blank";
  a.rel = "noopener";
  a.className = "path-link";
  a.title = "Open this folder in Google Drive";
  a.appendChild(pathNode(path));
  return a;
}

function nameNode(name) {
  const bdi = document.createElement("bdi");
  bdi.textContent = name;
  return bdi;
}

function checksumNode(md5) {
  const code = document.createElement("code");
  code.className = "sum";
  code.setAttribute("translate", "no");
  if (md5) {
    code.textContent = `md5 ${md5.slice(0, 12)}…`;
    code.title = `MD5 ${md5}`;
    code.setAttribute("aria-label", `MD5 checksum ${md5}`);
  } else {
    code.textContent = "—";
    code.title = "No checksum (Google Docs format)";
  }
  return code;
}

function openLink(href) {
  const link = document.createElement("a");
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener";
  link.append("Open", icon("i-external"));
  link.setAttribute("aria-label", "Open in Google Drive");
  return link;
}

function skippedNote(count) {
  if (!count) return "no Google Docs-format files to skip";
  return `${plural(count, "Google Docs-format file", "Google Docs-format files")} skipped (they have no checksum)`;
}

function renderResult() {
  const r = currentResult;
  for (const id of ["#stage-review", "#stage-clean", "#stage-folders", "#stage-old", "#stage-largest"]) {
    show(id, true);
  }
  setRail("review", ["connect", "scan"]);
  const lede = $("#summary");
  lede.innerHTML = "";
  const where = r.scope ? ` in <span class="scope">${escapeHtml(r.scope.path)}</span>` : "";
  lede.innerHTML = r.groups.length
    ? `${escapeHtml(r.reclaimable_human)} to reclaim<small>${plural(r.duplicate_groups, "duplicate group", "duplicate groups")}
       across ${plural(r.total_files, "file", "files")}${where}; ${skippedNote(r.skipped_no_md5)}</small>`
    : `No duplicates${where}<small>${plural(r.total_files, "file", "files")} checked; ${skippedNote(r.skipped_no_md5)}</small>`;
  const container = $("#groups");
  container.innerHTML = "";
  if (!r.groups.length) {
    container.innerHTML = `<p class="empty">Nothing to remove here. Try a wider scope, or use the tools for empty folders, old files and large files.</p>`;
    updateCleanSummary();
    return;
  }
  for (const group of r.groups) {
    const details = document.createElement("details");
    details.className = "group";
    details.open = r.groups.length <= 8;
    details.innerHTML = `
      <summary>
        <span class="gname"><bdi>${escapeHtml(group.files[0].name)}</bdi></span>
        <span class="gmeta">${plural(group.files.length, "copy", "copies")} of ${escapeHtml(group.size_human)},
          ${escapeHtml(group.wasted_human)} wasted</span>
        <span class="gsum" title="All copies share this MD5 checksum, so their contents are identical">MD5 <code translate="no">${escapeHtml(group.md5)}</code></span>
      </summary>`;
    const table = document.createElement("table");
    table.innerHTML =
      "<tr><th></th><th>Verdict</th><th>Name</th><th>Folder</th><th>Modified</th><th></th></tr>";
    for (const file of group.files) {
      const row = document.createElement("tr");
      row.className = file.protected ? "guard" : (file.suggested_trash ? "remove" : "keep");

      const checkCell = document.createElement("td");
      checkCell.className = "check";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.id = file.id;
      box.checked = file.suggested_trash;
      box.disabled = !!file.protected;
      box.setAttribute("aria-label", `Remove ${file.name} in ${file.path}`);
      if (file.protected) box.title = "In a protected folder";
      checkCell.appendChild(box);

      const verdictCell = document.createElement("td");
      verdictCell.className = "verdict";
      verdictCell.appendChild(
        file.protected ? pill("guard", "protected") : pill(file.suggested_trash ? "remove" : "keep", file.suggested_trash ? "remove" : "keep")
      );
      const nameCell = document.createElement("td");
      nameCell.className = "name";
      nameCell.appendChild(nameNode(file.name));
      nameCell.appendChild(checksumNode(file.md5));
      const pathCell = document.createElement("td");
      pathCell.className = "path";
      pathCell.appendChild(folderLink(file.path, file.parent));
      const dateCell = document.createElement("td");
      dateCell.className = "num";
      dateCell.textContent = shortDate(file.modified_time);
      const linkCell = document.createElement("td");
      if (file.web_view_link) linkCell.appendChild(openLink(file.web_view_link));
      row.append(checkCell, verdictCell, nameCell, pathCell, dateCell, linkCell);
      table.appendChild(row);
    }
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.appendChild(table);
    details.appendChild(wrap);
    container.appendChild(details);
  }
  updateCleanSummary();
}

function selectedIds() {
  return [...document.querySelectorAll('#groups input[type="checkbox"]:checked:not(:disabled)')]
    .map((el) => el.dataset.id);
}

function updateCleanSummary() {
  document.querySelectorAll("#groups tr").forEach((row) => {
    const box = row.querySelector("input");
    if (!box || box.disabled) return;
    row.className = box.checked ? "remove" : "keep";
    const verdict = row.querySelector("td.verdict");
    if (verdict) verdict.replaceChildren(pill(box.checked ? "remove" : "keep", box.checked ? "remove" : "keep"));
  });
  const count = selectedIds().length;
  $("#clean-summary").textContent = count
    ? `${plural(count, "file", "files")} marked for removal. Change any verdict above before you continue.`
    : "Nothing is marked for removal. Tick the copies you want to remove.";
  $("#btn-clean-text").textContent = count ? `Move ${plural(count, "file", "files")} to trash` : "Move selected to trash";
  $("#btn-clean").disabled = count === 0;
}

// ---- trash confirmation (shared by duplicates and the tools) ----

let pendingTrash = null; // { ids, endpoint }

function openConfirm(ids, names, endpoint) {
  pendingTrash = { ids, endpoint };
  $("#confirm-list").innerHTML = names.slice(0, 15)
    .map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  $("#confirm-count").textContent = names.length > 15
    ? `…and ${plural(names.length - 15, "more item", "more items")}. ${plural(names.length, "item", "items")} in total.`
    : `${plural(names.length, "item", "items")} in total.`;
  $("#confirm-dialog").showModal();
}

function confirmClean() {
  const ids = selectedIds();
  const names = [];
  for (const group of currentResult.groups) {
    for (const file of group.files) {
      if (ids.includes(file.id)) names.push(`${file.name} (${file.path})`);
    }
  }
  openConfirm(ids, names, "/api/clean");
}

async function ensureWriteAccess(messageEl) {
  const status = await api("/api/auth/status");
  if (status.write_access) return true;
  messageEl.textContent =
    "Write access is needed for this. Approve the Google consent tab, then confirm again.";
  await connect(true);
  return false;
}

async function runPendingTrash() {
  $("#confirm-dialog").close();
  if (!pendingTrash) return;
  if (!(await ensureWriteAccess($("#clean-result")))) return;
  try {
    const body = await api(pendingTrash.endpoint, {
      method: "POST",
      body: JSON.stringify({ file_ids: pendingTrash.ids, protected: [...protectedIds] }),
    });
    $("#clean-result").textContent =
      `Moved ${plural(body.trashed, "item", "items")} to trash as batch ${body.batch_id}. ` +
      "Scan again to refresh the lists.";
    $("#btn-undo").hidden = false;
    pendingTrash = null;
    await loadBatches();
  } catch (err) {
    $("#clean-result").textContent = `Couldn’t move to trash: ${err.message}`;
  }
}

async function undo() {
  try {
    const body = await api("/api/undo", { method: "POST" });
    $("#clean-result").textContent = `Restored ${plural(body.restored, "file", "files")} from trash.`;
    $("#btn-undo").hidden = true;
    await loadBatches();
  } catch (err) {
    $("#clean-result").textContent = `Couldn’t undo: ${err.message}`;
  }
}

// ---- batches & permanent deletion ----

let batchesCache = [];

async function loadBatches() {
  let body;
  try {
    body = await api("/api/batches");
  } catch (_) {
    return;
  }
  batchesCache = body.batches;
  const panel = $("#batches-panel");
  panel.hidden = batchesCache.length === 0;
  const container = $("#batches");
  container.innerHTML = "";
  if (!batchesCache.length) return;
  $("#btn-undo").hidden = !batchesCache.some((b) => b.status === "trashed");
  const whenFmt = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
  const table = document.createElement("table");
  table.innerHTML = "<tr><th>When</th><th>Batch</th><th>Files</th><th>Status</th><th></th></tr>";
  for (const b of batchesCache.slice(0, 20)) {
    const row = document.createElement("tr");
    const when = document.createElement("td");
    when.className = "num";
    const t = new Date(b.timestamp || "");
    when.textContent = Number.isNaN(t.getTime()) ? "" : whenFmt.format(t);
    const id = document.createElement("td");
    const code = document.createElement("code");
    code.textContent = b.batch_id;
    code.setAttribute("translate", "no");
    id.appendChild(code);
    const count = document.createElement("td");
    count.className = "num";
    count.textContent = n(b.count);
    const status = document.createElement("td");
    status.className = `status status-${b.status}`;
    const kind = { trashed: "remove", undone: "keep", purged: "guard" }[b.status] || "keep";
    const label = { trashed: "in trash", undone: "restored", purged: "deleted" }[b.status] || b.status;
    const p = pill(kind, label);
    if (b.status === "purged") p.replaceChildren(label);
    status.appendChild(p);
    const action = document.createElement("td");
    if (b.status === "trashed") {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "danger small";
      btn.textContent = "Delete permanently…";
      btn.addEventListener("click", () => openPurge(b));
      action.appendChild(btn);
    }
    row.append(when, id, count, status, action);
    table.appendChild(row);
  }
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.appendChild(table);
  container.appendChild(wrap);
}

let pendingPurge = null;

function openPurge(batch) {
  pendingPurge = batch;
  $("#purge-batch-id").textContent = batch.batch_id;
  $("#purge-list").innerHTML = batch.files.slice(0, 15)
    .map((f) => `<li>${escapeHtml(f.name)}</li>`).join("");
  $("#purge-count").textContent = batch.files.length > 15
    ? `…and ${plural(batch.files.length - 15, "more file", "more files")}. ${plural(batch.files.length, "file", "files")} in total.`
    : `${plural(batch.files.length, "file", "files")} in total.`;
  $("#purge-input").value = "";
  $("#purge-yes").disabled = true;
  $("#purge-dialog").showModal();
  $("#purge-input").focus();
}

async function runPurge() {
  const typed = $("#purge-input").value.trim();
  if (!pendingPurge || typed !== pendingPurge.batch_id) return;
  $("#purge-dialog").close();
  if (!(await ensureWriteAccess($("#clean-result")))) return;
  try {
    const body = await api("/api/purge", {
      method: "POST",
      body: JSON.stringify({ batch_id: pendingPurge.batch_id, confirm: typed }),
    });
    $("#clean-result").textContent =
      `Deleted ${plural(body.deleted, "file", "files")} permanently` +
      (body.skipped ? `. Skipped ${plural(body.skipped, "file", "files")} no longer in the trash.` : ".");
    pendingPurge = null;
    await loadBatches();
  } catch (err) {
    $("#clean-result").textContent = `Couldn’t delete: ${err.message}`;
  }
}

// ---- Extra tools: empty folders, older files, largest files ----

function checkedIds(containerSel) {
  return [...document.querySelectorAll(`${containerSel} input[type="checkbox"]:checked`)]
    .map((el) => el.dataset.id);
}

function colorRows(container) {
  container.querySelectorAll("tr").forEach((row) => {
    const box = row.querySelector("input");
    if (!box) return;
    row.className = box.checked ? "remove" : "";
    const verdict = row.querySelector("td.verdict");
    if (verdict) verdict.replaceChildren(box.checked ? pill("remove", "remove") : "");
  });
}

const toolItems = { folders: [], old: [], largest: [] };

function clearTools() {
  for (const [list, info, btn] of [
    ["#folders-list", "#folders-info", "#btn-trash-folders"],
    ["#old-list", "#old-info", "#btn-trash-old"],
    ["#largest-list", "#largest-info", "#btn-trash-largest"],
  ]) {
    $(list).innerHTML = "";
    $(info).textContent = "";
    $(btn).hidden = true;
  }
}

function renderToolTable(containerSel, items, preChecked) {
  const container = $(containerSel);
  container.innerHTML = "";
  if (!items.length) return;
  const table = document.createElement("table");
  table.innerHTML =
    `<tr><th></th>${preChecked ? "<th>Verdict</th>" : ""}<th>Name</th><th>Folder</th><th>Modified</th><th>Size</th><th></th></tr>`;
  for (const item of items) {
    const row = document.createElement("tr");
    row.className = preChecked ? "remove" : "";

    const checkCell = document.createElement("td");
    checkCell.className = "check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.dataset.id = item.id;
    box.checked = preChecked;
    box.setAttribute("aria-label", `Remove ${item.name} in ${item.path}`);
    checkCell.appendChild(box);

    const cells = [checkCell];
    if (preChecked) {
      const verdictCell = document.createElement("td");
      verdictCell.className = "verdict";
      verdictCell.appendChild(pill("remove", "remove"));
      cells.push(verdictCell);
    }
    const nameCell = document.createElement("td");
    nameCell.className = "name";
    nameCell.appendChild(nameNode(item.name));
    const pathCell = document.createElement("td");
    pathCell.className = "path";
    pathCell.appendChild(folderLink(item.path, item.parent));
    const dateCell = document.createElement("td");
    dateCell.className = "num";
    dateCell.textContent = shortDate(item.modified_time);
    const sizeCell = document.createElement("td");
    sizeCell.className = "num";
    sizeCell.textContent = item.size ? item.size_human : "";
    const linkCell = document.createElement("td");
    if (item.web_view_link) linkCell.appendChild(openLink(item.web_view_link));
    row.append(...cells, nameCell, pathCell, dateCell, sizeCell, linkCell);
    table.appendChild(row);
  }
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.appendChild(table);
  container.appendChild(wrap);
}

async function findFolders() {
  try {
    const body = await api(`/api/empty-folders?${scopeQuery()}`);
    toolItems.folders = body.folders;
    renderToolTable("#folders-list", body.folders, true);
    $("#folders-info").textContent = body.count
      ? `${plural(body.count, "empty folder", "empty folders")} found.` : "No empty folders.";
    $("#btn-trash-folders").hidden = body.count === 0;
  } catch (err) {
    $("#folders-info").textContent = `Couldn’t search: ${err.message}`;
  }
}

async function findOld() {
  const before = $("#before-date").value;
  if (!before) {
    $("#old-info").textContent = "Pick a date first.";
    return;
  }
  try {
    const body = await api(`/api/old-files?before=${before}${scopeQuery()}`);
    toolItems.old = body.files;
    renderToolTable("#old-list", body.files, false);
    $("#old-info").textContent = body.count
      ? `${plural(body.count, "file", "files")} older than ${before}, ${body.total_human} in total.`
      : `No files older than ${before}.`;
    $("#btn-trash-old").hidden = body.count === 0;
  } catch (err) {
    $("#old-info").textContent = `Couldn’t search: ${err.message}`;
  }
}

async function findLargest() {
  const limit = Math.max(1, Math.min(1000, parseInt($("#largest-limit").value, 10) || 50));
  $("#largest-limit").value = limit;
  try {
    const body = await api(`/api/largest-files?limit=${limit}${scopeQuery()}`);
    toolItems.largest = body.files;
    renderToolTable("#largest-list", body.files, false);
    $("#largest-info").textContent = body.count
      ? `Top ${plural(body.count, "file", "files")}, ${body.total_human} in total.`
      : "No files with a size found.";
    $("#btn-trash-largest").hidden = body.count === 0;
  } catch (err) {
    $("#largest-info").textContent = `Couldn’t search: ${err.message}`;
  }
}

function trashToolSelection(listSel, items, kind) {
  const ids = checkedIds(listSel);
  const names = items
    .filter((x) => ids.includes(x.id))
    .map((x) => (kind === "folder"
      ? `${x.path.replace(/\/$/, "")}/${x.name} (folder)`
      : `${x.name} (${x.path})`));
  if (!ids.length) return;
  openConfirm(ids, names, "/api/trash");
}

// ---- wiring ----

$("#credentials-file").addEventListener("change", uploadCredentials);
$("#btn-connect").addEventListener("click", () => connect(false));
$("#btn-disconnect").addEventListener("click", async () => {
  await api("/api/auth/disconnect", { method: "POST" });
  refreshAuth();
});
$("#btn-scan").addEventListener("click", startScan);
$("#strategy").addEventListener("change", loadResult);
$("#scope-folder").addEventListener("change", loadResult);
$("#prefer-folder").addEventListener("change", loadResult);
$("#btn-protect-add").addEventListener("click", addProtected);
$("#protect-folder").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); addProtected(); }
});
$("#groups").addEventListener("change", updateCleanSummary);
$("#btn-clean").addEventListener("click", confirmClean);
$("#confirm-yes").addEventListener("click", runPendingTrash);
$("#confirm-no").addEventListener("click", () => $("#confirm-dialog").close());
$("#btn-undo").addEventListener("click", undo);
$("#purge-input").addEventListener("input", () => {
  $("#purge-yes").disabled =
    !pendingPurge || $("#purge-input").value.trim() !== pendingPurge.batch_id;
});
$("#purge-yes").addEventListener("click", runPurge);
$("#purge-no").addEventListener("click", () => $("#purge-dialog").close());
$("#btn-find-folders").addEventListener("click", findFolders);
$("#btn-find-old").addEventListener("click", findOld);
$("#btn-find-largest").addEventListener("click", findLargest);
$("#btn-trash-folders").addEventListener("click",
  () => trashToolSelection("#folders-list", toolItems.folders, "folder"));
$("#btn-trash-old").addEventListener("click",
  () => trashToolSelection("#old-list", toolItems.old, "file"));
$("#btn-trash-largest").addEventListener("click",
  () => trashToolSelection("#largest-list", toolItems.largest, "file"));
$("#folders-list").addEventListener("change", (e) => colorRows(e.currentTarget));
$("#old-list").addEventListener("change", (e) => colorRows(e.currentTarget));
$("#largest-list").addEventListener("change", (e) => colorRows(e.currentTarget));

refreshAuth().then((status) => {
  if (!status.connected) return;
  loadBatches();
  watchScan();
});

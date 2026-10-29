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
    $("#auth-info").textContent =
      `Connected${status.write_access ? " (write access granted)" : " (read-only)"}`;
  } else if (status.has_client_secrets) {
    $("#auth-info").textContent = "OAuth client configured ✓ — ready to connect.";
  } else {
    $("#auth-info").textContent = "Not connected — one-time setup needed:";
  }
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
    $("#upload-result").textContent = "Saved ✓ — now click Connect below.";
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
  $("#auth-info").textContent = "A Google consent tab should open — approve access there…";
  const poll = setInterval(async () => {
    const status = await refreshAuth();
    if (writeAccess ? status.write_access : status.connected) clearInterval(poll);
  }, 1500);
}

// ---- scan ----

function watchScan() {
  const token = accessToken();
  const url = token ? `/api/scan/events?token=${encodeURIComponent(token)}` : "/api/scan/events";
  const source = new EventSource(url);
  source.onmessage = async (event) => {
    const snap = JSON.parse(event.data);
    if (snap.status === "scanning") {
      const n = snap.progress.files_fetched || 0;
      $("#scan-progress").textContent = `Fetched metadata for ${n} files…`;
    } else if (snap.status === "done") {
      source.close();
      $("#scan-progress").textContent = "";
      await loadFolders();
      await loadResult();
      await loadBatches();
    } else if (snap.status === "error") {
      source.close();
      $("#scan-progress").textContent = `Error: ${snap.error}`;
    }
  };
  source.onerror = () => {
    source.close();
    $("#scan-progress").textContent = "Lost connection to the scan — reload the page.";
  };
}

async function startScan() {
  $("#scan-progress").textContent = "Starting…";
  try {
    await api("/api/scan", { method: "POST" });
  } catch (err) {
    $("#scan-progress").textContent = `Error: ${err.message}`;
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
    x.title = "Remove";
    x.textContent = "✕";
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

function renderResult() {
  const r = currentResult;
  show("#stage-review", true);
  show("#stage-clean", true);
  show("#stage-folders", true);
  show("#stage-old", true);
  show("#stage-largest", true);
  const scope = r.scope
    ? `<div class="scope-note">in <b>${escapeHtml(r.scope.path)}</b></div>` : "";
  $("#summary").innerHTML = `
    <div><b>${r.total_files}</b> files scanned</div>
    <div><b>${r.duplicate_groups}</b> duplicate groups</div>
    <div><b>${r.reclaimable_human}</b> reclaimable</div>
    <div><b>${r.skipped_no_md5}</b> skipped (Google Docs formats)</div>${scope}`;
  const container = $("#groups");
  container.innerHTML = "";
  if (!r.groups.length) {
    container.innerHTML = "<p>No duplicates found 🎉</p>";
    updateCleanSummary();
    return;
  }
  for (const group of r.groups) {
    const details = document.createElement("details");
    details.open = r.groups.length <= 8;
    details.innerHTML = `
      <summary>
        <span class="gname">${escapeHtml(group.files[0].name)}</span>
        <span class="gmeta">${group.files.length} copies · ${group.size_human} each ·
          wastes ${group.wasted_human}</span>
      </summary>`;
    const table = document.createElement("table");
    table.innerHTML = "<tr><th></th><th>Name</th><th>Folder</th><th>Modified</th><th></th></tr>";
    for (const file of group.files) {
      const row = document.createElement("tr");
      row.className = file.protected ? "protected" : (file.suggested_trash ? "trash" : "keep");

      const checkCell = document.createElement("td");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.id = file.id;
      box.checked = file.suggested_trash;
      box.disabled = !!file.protected;
      if (file.protected) box.title = "In a protected folder";
      checkCell.appendChild(box);

      const nameCell = document.createElement("td");
      nameCell.textContent = file.name;
      const pathCell = document.createElement("td");
      pathCell.className = "path";
      pathCell.textContent = file.path;
      const dateCell = document.createElement("td");
      dateCell.textContent = (file.modified_time || "").slice(0, 10);
      const linkCell = document.createElement("td");
      if (file.web_view_link) {
        const link = document.createElement("a");
        link.href = file.web_view_link;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = "view";
        linkCell.appendChild(link);
      }
      row.append(checkCell, nameCell, pathCell, dateCell, linkCell);
      table.appendChild(row);
    }
    details.appendChild(table);
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
    if (box && !box.disabled) row.className = box.checked ? "trash" : "keep";
  });
  const n = selectedIds().length;
  $("#clean-summary").textContent = n
    ? `${n} file(s) selected for trash.`
    : "Nothing selected — tick the copies you want to remove.";
  $("#btn-clean").disabled = n === 0;
}

// ---- trash confirmation (shared by duplicates and the tools) ----

let pendingTrash = null; // { ids, endpoint }

function openConfirm(ids, names, endpoint) {
  pendingTrash = { ids, endpoint };
  $("#confirm-list").innerHTML = names.slice(0, 15)
    .map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  $("#confirm-count").textContent = names.length > 15
    ? `…and ${names.length - 15} more.` : `${names.length} item(s) total.`;
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
    "Requesting write access — approve the Google consent tab, then confirm again.";
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
      `Moved ${body.trashed} item(s) to trash (batch ${body.batch_id}). ` +
      "Re-scan to refresh the lists.";
    $("#btn-undo").hidden = false;
    pendingTrash = null;
    await loadBatches();
  } catch (err) {
    $("#clean-result").textContent = `Error: ${err.message}`;
  }
}

async function undo() {
  try {
    const body = await api("/api/undo", { method: "POST" });
    $("#clean-result").textContent = `Restored ${body.restored} file(s) from trash.`;
    $("#btn-undo").hidden = true;
    await loadBatches();
  } catch (err) {
    $("#clean-result").textContent = `Error: ${err.message}`;
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
  const table = document.createElement("table");
  table.innerHTML = "<tr><th>When</th><th>Batch</th><th>Files</th><th>Status</th><th></th></tr>";
  for (const b of batchesCache.slice(0, 20)) {
    const row = document.createElement("tr");
    const when = document.createElement("td");
    when.textContent = (b.timestamp || "").replace("T", " ").slice(0, 16);
    const id = document.createElement("td");
    id.innerHTML = `<code>${escapeHtml(b.batch_id)}</code>`;
    const count = document.createElement("td");
    count.textContent = b.count;
    const status = document.createElement("td");
    status.className = `status-${b.status}`;
    status.textContent = { trashed: "in trash", undone: "restored", purged: "deleted" }[b.status]
      || b.status;
    const action = document.createElement("td");
    if (b.status === "trashed") {
      const btn = document.createElement("button");
      btn.className = "danger";
      btn.textContent = "Delete permanently…";
      btn.addEventListener("click", () => openPurge(b));
      action.appendChild(btn);
    }
    row.append(when, id, count, status, action);
    table.appendChild(row);
  }
  container.appendChild(table);
}

let pendingPurge = null;

function openPurge(batch) {
  pendingPurge = batch;
  $("#purge-batch-id").textContent = batch.batch_id;
  $("#purge-list").innerHTML = batch.files.slice(0, 15)
    .map((f) => `<li>${escapeHtml(f.name)}</li>`).join("");
  $("#purge-count").textContent = batch.files.length > 15
    ? `…and ${batch.files.length - 15} more.` : `${batch.files.length} file(s) total.`;
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
      `Deleted ${body.deleted} file(s) permanently` +
      (body.skipped ? `; skipped ${body.skipped} no longer in the trash.` : ".");
    pendingPurge = null;
    await loadBatches();
  } catch (err) {
    $("#clean-result").textContent = `Error: ${err.message}`;
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
    if (box) row.className = box.checked ? "trash" : "";
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
    "<tr><th></th><th>Name</th><th>Folder</th><th>Modified</th><th>Size</th><th></th></tr>";
  for (const item of items) {
    const row = document.createElement("tr");
    row.className = preChecked ? "trash" : "";

    const checkCell = document.createElement("td");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.dataset.id = item.id;
    box.checked = preChecked;
    checkCell.appendChild(box);

    const nameCell = document.createElement("td");
    nameCell.textContent = item.name;
    const pathCell = document.createElement("td");
    pathCell.className = "path";
    pathCell.textContent = item.path;
    const dateCell = document.createElement("td");
    dateCell.textContent = (item.modified_time || "").slice(0, 10);
    const sizeCell = document.createElement("td");
    sizeCell.textContent = item.size ? item.size_human : "";
    const linkCell = document.createElement("td");
    if (item.web_view_link) {
      const link = document.createElement("a");
      link.href = item.web_view_link;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "view";
      linkCell.appendChild(link);
    }
    row.append(checkCell, nameCell, pathCell, dateCell, sizeCell, linkCell);
    table.appendChild(row);
  }
  container.appendChild(table);
}

async function findFolders() {
  try {
    const body = await api(`/api/empty-folders?${scopeQuery()}`);
    toolItems.folders = body.folders;
    renderToolTable("#folders-list", body.folders, true);
    $("#folders-info").textContent = body.count
      ? `${body.count} empty folder(s) found.` : "No empty folders 🎉";
    $("#btn-trash-folders").hidden = body.count === 0;
  } catch (err) {
    $("#folders-info").textContent = `Error: ${err.message}`;
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
      ? `${body.count} file(s) older than ${before} — ${body.total_human} total.`
      : `No files older than ${before}.`;
    $("#btn-trash-old").hidden = body.count === 0;
  } catch (err) {
    $("#old-info").textContent = `Error: ${err.message}`;
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
      ? `Top ${body.count} file(s) — ${body.total_human} total.`
      : "No files with a size found.";
    $("#btn-trash-largest").hidden = body.count === 0;
  } catch (err) {
    $("#largest-info").textContent = `Error: ${err.message}`;
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

refreshAuth().then((status) => { if (status.connected) loadBatches(); });

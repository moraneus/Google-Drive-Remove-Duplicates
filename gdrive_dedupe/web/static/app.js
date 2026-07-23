const $ = (sel) => document.querySelector(sel);
let currentResult = null;

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

function show(id, visible) {
  $(id).hidden = !visible;
}

async function refreshAuth() {
  const status = await api("/api/auth/status");
  const connected = status.connected;
  $("#auth-info").textContent = connected
    ? `Connected${status.write_access ? " (write access granted)" : " (read-only)"}`
    : "Not connected.";
  show("#secrets-hint", !connected && !status.has_client_secrets);
  $("#btn-connect").hidden = connected;
  $("#btn-disconnect").hidden = !connected;
  show("#stage-scan", connected);
  return status;
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

function watchScan() {
  const source = new EventSource("/api/scan/events");
  source.onmessage = async (event) => {
    const snap = JSON.parse(event.data);
    if (snap.status === "scanning") {
      const n = snap.progress.files_fetched || 0;
      $("#scan-progress").textContent = `Fetched metadata for ${n} files…`;
    } else if (snap.status === "done") {
      source.close();
      $("#scan-progress").textContent = "";
      await loadResult();
    } else if (snap.status === "error") {
      source.close();
      $("#scan-progress").textContent = `Error: ${snap.error}`;
    }
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

async function loadResult() {
  const strategy = $("#strategy").value;
  currentResult = await api(`/api/scan/result?strategy=${strategy}`);
  renderResult();
}

function renderResult() {
  const r = currentResult;
  show("#stage-review", true);
  show("#stage-clean", r.groups.length > 0);
  $("#summary").innerHTML = `
    <div><b>${r.total_files}</b> files scanned</div>
    <div><b>${r.duplicate_groups}</b> duplicate groups</div>
    <div><b>${r.reclaimable_human}</b> reclaimable</div>
    <div><b>${r.skipped_no_md5}</b> skipped (Google Docs formats)</div>`;
  const container = $("#groups");
  container.innerHTML = "";
  if (!r.groups.length) {
    container.innerHTML = "<p>No duplicates found 🎉</p>";
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
      row.className = file.suggested_trash ? "trash" : "keep";

      const checkCell = document.createElement("td");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.id = file.id;
      box.checked = file.suggested_trash;
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
  return [...document.querySelectorAll('#groups input[type="checkbox"]:checked')]
    .map((el) => el.dataset.id);
}

function updateCleanSummary() {
  document.querySelectorAll("#groups tr").forEach((row) => {
    const box = row.querySelector("input");
    if (box) row.className = box.checked ? "trash" : "keep";
  });
  const n = selectedIds().length;
  $("#clean-summary").textContent = n
    ? `${n} file(s) selected for trash.`
    : "Nothing selected — tick the copies you want to remove.";
  $("#btn-clean").disabled = n === 0;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

async function confirmClean() {
  const ids = selectedIds();
  const names = [];
  for (const group of currentResult.groups) {
    for (const file of group.files) {
      if (ids.includes(file.id)) names.push(`${file.name} (${file.path})`);
    }
  }
  $("#confirm-list").innerHTML = names.slice(0, 15)
    .map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  $("#confirm-count").textContent = names.length > 15
    ? `…and ${names.length - 15} more.` : `${names.length} file(s) total.`;
  $("#confirm-dialog").showModal();
}

async function runClean() {
  $("#confirm-dialog").close();
  const status = await api("/api/auth/status");
  if (!status.write_access) {
    $("#clean-result").textContent =
      "Requesting write access — approve the Google consent tab, then click Clean again.";
    await connect(true);
    return;
  }
  try {
    const body = await api("/api/clean", {
      method: "POST",
      body: JSON.stringify({ file_ids: selectedIds() }),
    });
    $("#clean-result").textContent =
      `Moved ${body.trashed} file(s) to trash (batch ${body.batch_id}).`;
    $("#btn-undo").hidden = false;
  } catch (err) {
    $("#clean-result").textContent = `Error: ${err.message}`;
  }
}

async function undo() {
  try {
    const body = await api("/api/undo", { method: "POST" });
    $("#clean-result").textContent = `Restored ${body.restored} file(s) from trash.`;
    $("#btn-undo").hidden = true;
  } catch (err) {
    $("#clean-result").textContent = `Error: ${err.message}`;
  }
}

$("#btn-connect").addEventListener("click", () => connect(false));
$("#btn-disconnect").addEventListener("click", async () => {
  await api("/api/auth/disconnect", { method: "POST" });
  refreshAuth();
});
$("#btn-scan").addEventListener("click", startScan);
$("#strategy").addEventListener("change", loadResult);
$("#groups").addEventListener("change", updateCleanSummary);
$("#btn-clean").addEventListener("click", confirmClean);
$("#confirm-yes").addEventListener("click", runClean);
$("#confirm-no").addEventListener("click", () => $("#confirm-dialog").close());
$("#btn-undo").addEventListener("click", undo);

refreshAuth();

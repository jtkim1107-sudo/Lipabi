/* Lipabi 칸반보드 프런트엔드 */

let tasks = [];

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401) {
    // 세션 만료 → 로그인 페이지로
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add("hidden"), 3000);
}

/* ── 리포트 패널 ─────────────────────────────── */

async function loadReport() {
  const report = await api("/api/reports/latest");
  const panel = document.getElementById("report-panel");
  if (!report || !report.summary) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  document.getElementById("report-date").textContent = report.run_date;
  document.getElementById("report-summary").textContent = report.summary;
  const ul = document.getElementById("report-insights");
  ul.innerHTML = "";
  for (const insight of report.insights || []) {
    const li = document.createElement("li");
    li.textContent = insight;
    ul.appendChild(li);
  }
  const notes = document.getElementById("report-notes");
  notes.textContent = report.data_notes ? `⚠️ ${report.data_notes}` : "";
}

/* ── 칸반보드 ────────────────────────────────── */

function renderCard(task) {
  const card = document.createElement("div");
  card.className = `card pri-${task.priority}`;
  card.draggable = true;
  card.dataset.id = task.id;

  const top = document.createElement("div");
  top.className = "card-top";
  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = task.title;
  const del = document.createElement("button");
  del.className = "btn-delete";
  del.title = "삭제";
  del.textContent = "✕";
  del.onclick = async () => {
    if (!confirm(`"${task.title}" 업무를 삭제할까요?`)) return;
    await api(`/api/tasks/${task.id}`, { method: "DELETE" });
    await loadTasks();
  };
  top.append(title, del);
  card.appendChild(top);

  if (task.description) {
    const desc = document.createElement("div");
    desc.className = "card-desc";
    desc.textContent = task.description;
    card.appendChild(desc);
  }

  const meta = document.createElement("div");
  meta.className = "card-meta";

  if (task.category) {
    const cat = document.createElement("span");
    cat.className = "meta-tag";
    cat.textContent = task.category;
    meta.appendChild(cat);
  }

  if (task.source === "ai") {
    const ai = document.createElement("span");
    ai.className = "meta-tag ai";
    ai.textContent = "AI";
    meta.appendChild(ai);
  }

  const assignee = document.createElement("input");
  assignee.className = "assignee-input";
  assignee.placeholder = "담당자";
  assignee.value = task.assignee || "";
  assignee.onchange = async () => {
    await api(`/api/tasks/${task.id}`, {
      method: "PATCH",
      body: JSON.stringify({ assignee: assignee.value.trim() }),
    });
    toast("담당자를 지정했습니다");
  };
  meta.appendChild(assignee);

  card.appendChild(meta);

  card.addEventListener("dragstart", () => card.classList.add("dragging"));
  card.addEventListener("dragend", () => card.classList.remove("dragging"));
  return card;
}

function renderBoard() {
  for (const zone of document.querySelectorAll(".cards")) {
    const status = zone.dataset.status;
    zone.innerHTML = "";
    const list = tasks.filter((t) => t.status === status);
    for (const task of list) zone.appendChild(renderCard(task));
    zone.closest(".column").querySelector(".count").textContent = list.length;
  }
}

async function loadTasks() {
  tasks = await api("/api/tasks");
  renderBoard();
}

/* ── 드래그 앤 드롭 ──────────────────────────── */

function setupDragAndDrop() {
  for (const zone of document.querySelectorAll(".cards")) {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("drag-over");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", async (e) => {
      e.preventDefault();
      zone.classList.remove("drag-over");
      const card = document.querySelector(".card.dragging");
      if (!card) return;
      const id = Number(card.dataset.id);
      const newStatus = zone.dataset.status;
      const task = tasks.find((t) => t.id === id);
      if (!task || task.status === newStatus) return;
      task.status = newStatus;
      renderBoard();
      try {
        await api(`/api/tasks/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: newStatus }),
        });
      } catch (err) {
        toast(err.message);
        await loadTasks();
      }
    });
  }
}

/* ── 사용자 / 직원 관리 ─────────────────────── */

async function loadMe() {
  const me = await api("/api/me");
  document.getElementById("user-name").textContent = `${me.name}님`;
  if (me.role === "admin") {
    document.getElementById("manage-users-btn").classList.remove("hidden");
  }
  return me;
}

async function refreshUsersTable() {
  const users = await api("/api/users");
  const tbody = document.getElementById("users-tbody");
  tbody.innerHTML = "";
  for (const u of users) {
    const tr = document.createElement("tr");

    const tdUsername = document.createElement("td");
    tdUsername.textContent = u.username;
    const tdName = document.createElement("td");
    tdName.textContent = u.name;
    const tdRole = document.createElement("td");
    tdRole.textContent = u.role === "admin" ? "관리자" : "직원";

    const tdActions = document.createElement("td");
    tdActions.className = "row-actions";
    const resetBtn = document.createElement("button");
    resetBtn.className = "btn-ghost";
    resetBtn.textContent = "비밀번호 재설정";
    resetBtn.onclick = async () => {
      const pw = prompt(`${u.name}의 새 비밀번호 (8자 이상):`);
      if (!pw) return;
      try {
        await api(`/api/users/${u.id}/password`, {
          method: "POST",
          body: JSON.stringify({ new_password: pw }),
        });
        toast("비밀번호를 재설정했습니다");
      } catch (err) {
        toast(err.message);
      }
    };
    const delBtn = document.createElement("button");
    delBtn.className = "btn-ghost";
    delBtn.textContent = "삭제";
    delBtn.onclick = async () => {
      if (!confirm(`${u.name}(${u.username}) 계정을 삭제할까요?`)) return;
      try {
        await api(`/api/users/${u.id}`, { method: "DELETE" });
        await refreshUsersTable();
      } catch (err) {
        toast(err.message);
      }
    };
    tdActions.append(resetBtn, delBtn);

    tr.append(tdUsername, tdName, tdRole, tdActions);
    tbody.appendChild(tr);
  }
}

function setupUserActions() {
  document.getElementById("logout-btn").onclick = async () => {
    await api("/api/logout", { method: "POST" });
    window.location.href = "/login";
  };

  const modal = document.getElementById("users-modal");
  document.getElementById("manage-users-btn").onclick = async () => {
    await refreshUsersTable();
    modal.classList.remove("hidden");
  };
  document.getElementById("close-users-modal").onclick = () => modal.classList.add("hidden");
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });

  document.getElementById("add-user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify({
          username: document.getElementById("new-username").value.trim(),
          name: document.getElementById("new-name").value.trim(),
          password: document.getElementById("new-password").value,
          role: document.getElementById("new-role").value,
        }),
      });
      e.target.reset();
      await refreshUsersTable();
      toast("직원 계정을 추가했습니다");
    } catch (err) {
      toast(err.message);
    }
  });
}

/* ── 수동 업무 추가 / 분석 실행 ──────────────── */

function runAnalysis() {
  return api("/api/run", { method: "POST" });
}

function setupActions() {
  document.getElementById("add-task-btn").onclick = async () => {
    const title = prompt("새 업무 제목을 입력하세요:");
    if (!title || !title.trim()) return;
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ title: title.trim(), status: "todo" }),
    });
    await loadTasks();
  };

  const runBtn = document.getElementById("run-btn");
  runBtn.onclick = async () => {
    runBtn.disabled = true;
    runBtn.textContent = "분석 중… (수십 초 걸릴 수 있어요)";
    try {
      const result = await runAnalysis();
      toast(`분석 완료! 업무 ${result.tasks_created}건이 추가됐습니다`);
      await Promise.all([loadReport(), loadTasks()]);
    } catch (err) {
      toast(err.message);
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = "지금 분석 실행";
    }
  };

  const toggle = document.getElementById("toggle-report");
  toggle.onclick = () => {
    const body = document.getElementById("report-body");
    const hidden = body.classList.toggle("hidden");
    toggle.textContent = hidden ? "펼치기" : "접기";
  };
}

/* ── 초기화 ─────────────────────────────────── */

(async function init() {
  setupDragAndDrop();
  setupActions();
  setupUserActions();
  await Promise.all([loadMe(), loadReport(), loadTasks()]);
  // 다른 직원의 변경 사항을 주기적으로 반영
  setInterval(loadTasks, 15000);
})();

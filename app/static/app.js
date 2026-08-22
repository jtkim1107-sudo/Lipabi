/* Lipabi 칸반보드 프런트엔드 */

let tasks = [];
let latestReportId = null;

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
  latestReportId = report.id;
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
    let query = "";
    // AI 제안을 거절하는 경우 이유를 물어 학습에 활용
    if (task.source === "ai" && task.status === "suggested") {
      const reason = prompt("삭제 이유 (선택 — 분석가 교육에 활용됩니다):") || "";
      if (reason.trim()) query = `?reason=${encodeURIComponent(reason.trim())}`;
    }
    await api(`/api/tasks/${task.id}${query}`, { method: "DELETE" });
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

  // AI가 추천한 담당자 — 클릭 한 번으로 배정
  if (task.suggested_assignee && !task.assignee && task.status === "suggested") {
    const suggest = document.createElement("span");
    suggest.className = "suggest-badge";
    suggest.textContent = `추천: ${task.suggested_assignee}`;
    suggest.title = "클릭하면 이 담당자로 배정합니다";
    suggest.onclick = async () => {
      await api(`/api/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ assignee: task.suggested_assignee }),
      });
      toast(`${task.suggested_assignee}님에게 배정했습니다`);
      await loadTasks();
    };
    meta.appendChild(suggest);
  }

  // AI에게 업무 맡기기 / 결과물 보기
  if (task.deliverable) {
    const resultBtn = document.createElement("button");
    resultBtn.className = "btn-mini has-result";
    resultBtn.textContent = "📄 결과 보기";
    resultBtn.onclick = () => openDeliverable(task);
    meta.appendChild(resultBtn);
  } else if (task.status !== "done") {
    const aiBtn = document.createElement("button");
    aiBtn.className = "btn-mini";
    aiBtn.textContent = "🤖 AI 수행";
    aiBtn.title = "AI가 이 업무의 결과물 초안을 작성합니다";
    aiBtn.onclick = async () => {
      if (!confirm(`AI가 "${task.title}" 업무를 수행해 결과물 초안을 만듭니다. 진행할까요?`)) return;
      aiBtn.disabled = true;
      aiBtn.textContent = "작업 중…";
      try {
        await api(`/api/tasks/${task.id}/delegate`, { method: "POST" });
        toast("AI가 결과물을 작성했습니다 — 카드의 [결과 보기]에서 확인하세요");
        await loadTasks();
      } catch (err) {
        toast(err.message);
        aiBtn.disabled = false;
        aiBtn.textContent = "🤖 AI 수행";
      }
    };
    meta.appendChild(aiBtn);
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

/* ── AI 결과물 모달 ──────────────────────────── */

let deliverableTaskId = null;

function openDeliverable(task) {
  deliverableTaskId = task.id;
  document.getElementById("deliverable-title").textContent = task.title;
  document.getElementById("deliverable-summary").textContent = task.deliverable_summary || "";
  document.getElementById("deliverable-content").textContent = task.deliverable;
  document.getElementById("deliverable-modal").classList.remove("hidden");
}

function setupDeliverableModal() {
  const modal = document.getElementById("deliverable-modal");
  document.getElementById("close-deliverable-modal").onclick = () => modal.classList.add("hidden");
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });

  document.getElementById("copy-deliverable-btn").onclick = async () => {
    try {
      await navigator.clipboard.writeText(
        document.getElementById("deliverable-content").textContent
      );
      toast("클립보드에 복사했습니다");
    } catch {
      toast("복사에 실패했습니다 — 직접 선택해서 복사해 주세요");
    }
  };

  document.getElementById("complete-deliverable-btn").onclick = async () => {
    if (!deliverableTaskId) return;
    await api(`/api/tasks/${deliverableTaskId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "done" }),
    });
    modal.classList.add("hidden");
    toast("완료로 이동했습니다");
    await loadTasks();
  };
}

/* ── 사용자 / 직원 관리 ─────────────────────── */

async function loadMe() {
  const me = await api("/api/me");
  document.getElementById("user-name").textContent = `${me.name}님`;
  if (me.role === "admin") {
    document.getElementById("manage-users-btn").classList.remove("hidden");
    document.getElementById("agent-btn").classList.remove("hidden");
  }
  return me;
}

/* ── 내 분석가 (교육) ────────────────────────── */

async function openAgentModal() {
  const profile = await api("/api/agent");
  document.getElementById("agent-name").value = profile.name || "";
  document.getElementById("agent-instructions").value = profile.instructions || "";
  document.getElementById("agent-lessons").textContent =
    profile.lessons?.trim() || "(아직 학습된 교훈이 없습니다)";
  document.getElementById("pending-fb").textContent = profile.pending_feedback ?? 0;
  document.getElementById("train-changelog").classList.add("hidden");
  document.getElementById("agent-modal").classList.remove("hidden");
}

function setupAgentModal() {
  const modal = document.getElementById("agent-modal");
  document.getElementById("agent-btn").onclick = openAgentModal;
  document.getElementById("close-agent-modal").onclick = () => modal.classList.add("hidden");
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });

  document.getElementById("save-agent-btn").onclick = async () => {
    try {
      await api("/api/agent", {
        method: "PUT",
        body: JSON.stringify({
          name: document.getElementById("agent-name").value.trim(),
          instructions: document.getElementById("agent-instructions").value,
        }),
      });
      toast("분석가 프로필을 저장했습니다 — 다음 분석부터 반영됩니다");
    } catch (err) {
      toast(err.message);
    }
  };

  const trainBtn = document.getElementById("train-agent-btn");
  trainBtn.onclick = async () => {
    trainBtn.disabled = true;
    trainBtn.textContent = "학습 중…";
    try {
      const result = await api("/api/agent/train", { method: "POST" });
      if (!result.trained) {
        toast(result.message);
      } else {
        document.getElementById("agent-lessons").textContent = result.lessons;
        document.getElementById("pending-fb").textContent = 0;
        const log = document.getElementById("train-changelog");
        log.textContent = `✓ 피드백 ${result.feedback_count}건 반영 — ${result.changelog}`;
        log.classList.remove("hidden");
        toast("학습 완료! 다음 분석부터 반영됩니다");
      }
    } catch (err) {
      toast(err.message);
    } finally {
      trainBtn.disabled = false;
      trainBtn.innerHTML = `피드백 학습하기 (<span id="pending-fb">${document.getElementById("pending-fb")?.textContent ?? 0}</span>건 대기)`;
    }
  };
}

/* ── 내 에이전트 (직원 개인) ─────────────────── */

async function openMyAgentModal() {
  const agent = await api("/api/my-agent");
  document.getElementById("my-agent-name").value = agent.name || "";
  document.getElementById("my-agent-instructions").value = agent.instructions || "";
  document.getElementById("my-agent-lessons").textContent =
    agent.lessons?.trim() || "(아직 학습된 내용이 없습니다)";
  document.getElementById("my-pending-fb").textContent = agent.pending_feedback ?? 0;
  document.getElementById("my-train-changelog").classList.add("hidden");
  document.getElementById("my-agent-modal").classList.remove("hidden");
}

function setupMyAgentModal() {
  const modal = document.getElementById("my-agent-modal");
  document.getElementById("my-agent-btn").onclick = openMyAgentModal;
  document.getElementById("close-my-agent-modal").onclick = () => modal.classList.add("hidden");
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });

  document.getElementById("save-my-agent-btn").onclick = async () => {
    try {
      await api("/api/my-agent", {
        method: "PUT",
        body: JSON.stringify({
          name: document.getElementById("my-agent-name").value.trim(),
          instructions: document.getElementById("my-agent-instructions").value,
        }),
      });
      toast("내 에이전트를 저장했습니다 — 다음 브리핑부터 반영됩니다");
    } catch (err) {
      toast(err.message);
    }
  };

  const trainBtn = document.getElementById("train-my-agent-btn");
  trainBtn.onclick = async () => {
    trainBtn.disabled = true;
    trainBtn.textContent = "학습 중…";
    let pending = 0;
    try {
      const result = await api("/api/my-agent/train", { method: "POST" });
      if (!result.trained) {
        toast(result.message);
        pending = Number(document.getElementById("my-pending-fb")?.textContent) || 0;
      } else {
        document.getElementById("my-agent-lessons").textContent = result.lessons;
        const log = document.getElementById("my-train-changelog");
        log.textContent = `✓ 피드백 ${result.feedback_count}건 반영 — ${result.changelog}`;
        log.classList.remove("hidden");
        toast("학습 완료! 다음 브리핑부터 반영됩니다");
      }
    } catch (err) {
      toast(err.message);
    } finally {
      trainBtn.disabled = false;
      trainBtn.innerHTML = `피드백 학습하기 (<span id="my-pending-fb">${pending}</span>건 대기)`;
    }
  };
}

/* ── 오늘 브리핑 ─────────────────────────────── */

function renderBriefing(b) {
  const body = document.getElementById("briefing-body");
  body.innerHTML = "";

  const headline = document.createElement("p");
  headline.className = "briefing-headline";
  headline.textContent = b.headline;
  body.appendChild(headline);

  const list = document.createElement("ol");
  list.className = "focus-list";
  (b.focus || []).forEach((item, i) => {
    const li = document.createElement("li");
    li.className = "focus-item";
    const num = document.createElement("span");
    num.className = "focus-num";
    num.textContent = i + 1;
    const content = document.createElement("div");
    const title = document.createElement("div");
    title.className = "focus-title";
    title.textContent = item.title;
    if (item.suggested_assignee) {
      const assignee = document.createElement("span");
      assignee.className = "delegate-badge";
      assignee.textContent = `추천 담당: ${item.suggested_assignee}`;
      title.appendChild(assignee);
    }
    const reason = document.createElement("div");
    reason.className = "focus-reason";
    reason.textContent = item.reason;
    content.append(title, reason);
    li.append(num, content);
    list.appendChild(li);
  });
  body.appendChild(list);

  if (b.tip) {
    const tip = document.createElement("p");
    tip.className = "briefing-tip";
    tip.textContent = `💡 ${b.tip}`;
    body.appendChild(tip);
  }
}

function setupBriefing() {
  const modal = document.getElementById("briefing-modal");
  const body = document.getElementById("briefing-body");

  const load = async (refresh) => {
    body.innerHTML = '<p class="briefing-loading">브리핑을 준비하는 중… (수십 초 걸릴 수 있어요)</p>';
    modal.classList.remove("hidden");
    try {
      const b = await api(`/api/briefing${refresh ? "?refresh=true" : ""}`);
      document.getElementById("briefing-title").textContent = `오늘 브리핑 · ${b.run_date || ""}`;
      renderBriefing(b);
    } catch (err) {
      body.innerHTML = "";
      const p = document.createElement("p");
      p.className = "briefing-loading";
      p.textContent = err.message;
      body.appendChild(p);
    }
  };

  document.getElementById("briefing-btn").onclick = () => load(false);
  document.getElementById("refresh-briefing-btn").onclick = () => load(true);
  document.getElementById("close-briefing-modal").onclick = () => modal.classList.add("hidden");
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });
}

function setupReportFeedback() {
  const send = async (signal) => {
    if (!latestReportId) return;
    const comment = prompt(
      signal === "positive"
        ? "어떤 점이 좋았나요? (선택)"
        : "어떤 점이 아쉬웠나요? (선택 — 분석가 교육에 활용됩니다)"
    ) || "";
    try {
      await api("/api/feedback", {
        method: "POST",
        body: JSON.stringify({
          kind: "report",
          ref_id: latestReportId,
          signal,
          comment: comment.trim(),
        }),
      });
      toast("피드백이 기록되었습니다");
    } catch (err) {
      toast(err.message);
    }
  };
  document.getElementById("fb-good").onclick = () => send("positive");
  document.getElementById("fb-bad").onclick = () => send("negative");
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

    const tdTeam = document.createElement("td");
    const teamInput = document.createElement("input");
    teamInput.className = "inline-input";
    teamInput.placeholder = "팀";
    teamInput.value = u.team || "";
    teamInput.onchange = async () => {
      try {
        await api(`/api/users/${u.id}`, {
          method: "PATCH",
          body: JSON.stringify({ team: teamInput.value.trim() }),
        });
        toast("팀을 변경했습니다");
      } catch (err) {
        toast(err.message);
      }
    };
    tdTeam.appendChild(teamInput);

    const tdRole = document.createElement("td");
    const roleSelect = document.createElement("select");
    roleSelect.className = "inline-select";
    for (const [value, label] of [["member", "직원"], ["leader", "팀장"], ["admin", "관리자"]]) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      if (u.role === value) opt.selected = true;
      roleSelect.appendChild(opt);
    }
    roleSelect.onchange = async () => {
      try {
        await api(`/api/users/${u.id}`, {
          method: "PATCH",
          body: JSON.stringify({ role: roleSelect.value }),
        });
        toast("역할을 변경했습니다");
      } catch (err) {
        toast(err.message);
        await refreshUsersTable();
      }
    };
    tdRole.appendChild(roleSelect);

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

    tr.append(tdUsername, tdName, tdTeam, tdRole, tdActions);
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
          team: document.getElementById("new-team").value.trim(),
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
  setupAgentModal();
  setupMyAgentModal();
  setupBriefing();
  setupDeliverableModal();
  setupReportFeedback();
  await Promise.all([loadMe(), loadReport(), loadTasks()]);
  // 다른 직원의 변경 사항을 주기적으로 반영
  setInterval(loadTasks, 15000);
})();

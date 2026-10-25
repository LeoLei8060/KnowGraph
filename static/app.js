/* KnowGraph local UI. All state comes from the local API; no third-party scripts. */

const state = {
  profile: null,
  inboxStatus: "",
  interests: [],
  reviews: [],
  sources: [],
  settings: {},
  llm: null,
  providers: [],
  presets: [],
  activeProviderId: null,
  editingProviderId: null,
  providerModels: [],
  providerError: "",
  qaGroups: { groups: [], ungrouped_count: 0, total: 0 },
  qaEntries: [],
  qaFilter: "all",
  qaTargetGroup: null,
  news: [],
  newsPlatform: "all",
  githubRanking: null,
  githubMode: "trending_weekly",
  githubCategory: "all",
  lastSearch: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" && data ? data.detail || data.error : data;
    throw new Error(detail || `请求失败 (${response.status})`);
  }
  return data;
}

async function apiForm(path, formData) {
  const response = await fetch(path, { method: "POST", body: formData });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "上传失败");
  return data;
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast is-visible${isError ? " is-error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.className = "toast";
  }, 3600);
}

function setBusy(button, busy, busyText = "处理中…") {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
  }
}

function fmtDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options = withTime
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" };
  return new Intl.DateTimeFormat("zh-CN", options).format(date);
}

function tagRow(tags, strong = []) {
  if (!tags || !tags.length) return "";
  const strongSet = new Set(strong.map((item) => String(item).toLowerCase()));
  return `<div class="tag-row">${tags
    .map((tag) => `<span class="tag${strongSet.has(String(tag).toLowerCase()) ? " is-strong" : ""}">${esc(tag)}</span>`)
    .join("")}</div>`;
}

function empty(message) {
  return `<div class="empty">${esc(message)}</div>`;
}

async function openNote(noteId) {
  try {
    const note = await api(`/api/notes/${encodeURIComponent(noteId)}`);
    $("#note-title").textContent = note.title;
    $("#note-path").textContent = note.path;
    $("#note-body").textContent = note.content;
    const ob = $("#note-obsidian");
    if (note.obsidian_uri) {
      ob.href = note.obsidian_uri;
      ob.hidden = false;
    } else {
      ob.hidden = true;
    }
    const dialog = $("#note-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  } catch (error) {
    toast(error.message, true);
  }
}

// Navigation -----------------------------------------------------------------
const loaders = {
  profile: loadProfile,
  inbox: loadInbox,
  news: loadNews,
  search: () => {},
  ask: loadQA,
  reviews: loadReviews,
  interests: loadInterests,
  settings: loadSettings,
};

function showView(name) {
  const view = $(`#view-${name}`) ? name : "profile";
  $$(".view").forEach((node) => node.classList.toggle("is-active", node.id === `view-${view}`));
  $$(".rail-tab").forEach((node) => node.classList.toggle("is-active", node.dataset.view === view));
  if (loaders[view]) {
    Promise.resolve(loaders[view]()).catch((error) => toast(error.message, true));
  }
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

function wireNavigation() {
  $$(".rail-tab").forEach((button) => {
    button.addEventListener("click", () => {
      window.location.hash = button.dataset.view;
    });
  });
  window.addEventListener("hashchange", () => showView(window.location.hash.slice(1) || "profile"));
}

// Status ---------------------------------------------------------------------
async function refreshStatus() {
  try {
    const health = await api("/api/health");
    const llm = health.llm || {};
    const providerName = llm.provider_name ? `${llm.provider_name} · ` : "";
    const modelState = llm.available
      ? llm.verified
        ? `<span class="state-ok">${esc(providerName)}模型已验证</span>`
        : `<span class="state-ok">${esc(providerName)}模型已配置</span>`
      : !llm.enabled
        ? `<span class="state-off">模型已禁用</span>`
        : `<span class="state-off">离线抽取</span>`;
    $("#context-stats").innerHTML = [
      `<span class="stat-item">本地资料 <strong>${esc(health.counts.items)}</strong> 条</span>`,
      `<span class="stat-item">待处理 <strong>${esc(health.counts.inbox)}</strong> 条</span>`,
      `<span class="stat-item stat-secondary">时区 <strong>${esc(health.timezone)}</strong></span>`,
      `<span class="stat-item">${modelState}</span>`,
      `<span class="stat-item stat-secondary" title="${esc(health.vault)}">Vault <strong>${esc(shortPath(health.vault))}</strong></span>`,
    ].join('<span class="dot">/</span>');
    $("#rail-time").textContent = fmtDate(health.time, true);
    if ($("#settings-status") && $("#view-settings").classList.contains("is-active")) {
      renderSettingsStatus(health);
    }
  } catch (error) {
    $("#context-stats").innerHTML = `<span class="state-off">本地服务不可达</span>`;
  }
}

function shortPath(path) {
  if (!path) return "—";
  const parts = String(path).split(/[\\/]/).filter(Boolean);
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : String(path);
}

function renderSettingsStatus(health) {
  const llm = health.llm || {};
  const keySources = { env: "环境变量", store: "本地安全存储", profile: "Anthropic profile", none: "未配置" };
  const modelState = llm.available
    ? llm.verified
      ? "已验证"
      : "已配置，未验证"
    : !llm.enabled
      ? "已禁用"
      : llm.configured
        ? "密钥已保存，连接未验证"
        : "未配置，使用本地抽取";
  const modelLabel = llm.provider_name ? `${llm.provider_name} / ${llm.model}` : llm.model;
  const keyState = llm.configured
    ? `${keySources[llm.key_source] || llm.key_source || "已配置"}${llm.key_hint ? ` ${llm.key_hint}` : ""}`
    : "未配置";
  const rows = [
    ["数据目录", health.home],
    ["Vault", health.vault],
    ["模型", `${modelLabel || "—"}（${modelState}）`],
    ["模型密钥", keyState],
    ["调度器", health.scheduler_running ? "运行中" : "已停止"],
    ["资料", `${health.counts.items} 条`],
    ["笔记索引", `${health.counts.notes} 条`],
    ["来源", `${health.counts.sources} 个`],
    ["回顾", `${health.counts.reviews} 份`],
    ["最近任务", health.last_job ? `${health.last_job.kind} / ${health.last_job.status}` : "—"],
  ];
  $("#settings-status").innerHTML = rows
    .map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value || "—")}</dd></div>`)
    .join("");
}

function providerSourceLabel(source) {
  return { env: "环境变量", store: "本地安全存储", profile: "Anthropic profile", none: "未配置" }[source] || source || "未配置";
}

function providerProtocolLabel(protocol) {
  return protocol === "openai" ? "OpenAI Chat" : "Anthropic Messages";
}

function renderProviderPresets() {
  const select = $("#provider-preset");
  if (!select) return;
  const current = select.value;
  select.innerHTML =
    `<option value="">选择预设…</option>` +
    state.presets
      .map((preset) => `<option value="${esc(preset.key)}">${esc(preset.name)}</option>`)
      .join("");
  if (current && state.presets.some((preset) => preset.key === current)) select.value = current;
}

function providerLinks(provider) {
  const links = [];
  const add = (url, label) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        links.push(`<a href="${esc(parsed.href)}" target="_blank" rel="noreferrer">${label}</a>`);
      }
    } catch (error) {
      // Ignore malformed reference links; they are never used for requests.
    }
  };
  if (provider.website_url) add(provider.website_url, "官网");
  if (provider.docs_url) add(provider.docs_url, "文档");
  if (provider.api_key_url) add(provider.api_key_url, "获取密钥");
  return links.join(" · ");
}

function renderProviders() {
  const container = $("#provider-list");
  const node = $("#llm-key-state");
  if (!container) return;
  if (state.providerError) {
    container.innerHTML = empty(`模型服务接口不可用：${state.providerError}。请确认已重启到最新版本。`);
    if (node) node.textContent = "模型服务接口不可用";
    return;
  }
  if (!state.providers.length) {
    container.innerHTML = empty("还没有模型服务。用下方预设添加一个。");
    if (node) node.textContent = "未配置模型服务";
    return;
  }
  container.innerHTML = state.providers
    .map((provider) => {
      const active = provider.active;
      const keyState = !provider.requires_key
        ? "无需密钥"
        : provider.configured
          ? `密钥 · ${providerSourceLabel(provider.source)} ${provider.hint || ""}`
          : "密钥未配置";
      const verified = active && state.llm && state.llm.verified ? " · 已验证" : "";
      return `
      <div class="provider-row ${active ? "is-active" : ""}" data-provider="${esc(provider.id)}">
        <div class="provider-main">
          <div class="provider-name">${esc(provider.name)}${active ? '<span class="tag is-strong">当前</span>' : ""}</div>
          <div class="provider-meta">${esc(providerProtocolLabel(provider.protocol))} · ${esc(provider.model)} · ${esc(provider.base_url)}</div>
          <div class="provider-key">${esc(keyState)}${esc(verified)}</div>
          ${providerLinks(provider) ? `<div class="provider-key">${providerLinks(provider)}</div>` : ""}
        </div>
        <div class="provider-actions">
          ${active ? "" : `<button class="btn small ghost" data-action="activate-provider" type="button">设为当前</button>`}
          <button class="btn small ghost" data-action="edit-provider" type="button">编辑</button>
          <button class="btn small ghost" data-action="test-provider" type="button" ${provider.requires_key && !provider.configured ? "disabled" : ""}>测试</button>
          ${provider.source === "store" ? `<button class="btn small danger-quiet" data-action="clear-provider-key" type="button">清除密钥</button>` : ""}
          <button class="icon-btn" data-action="delete-provider" type="button" ${state.providers.length <= 1 ? "disabled" : ""}>删除</button>
        </div>
      </div>`;
    })
    .join("");
  const active = state.providers.find((provider) => provider.active);
  if (node) {
    node.textContent = state.llm && !state.llm.enabled
      ? `当前：${active ? active.name : "—"} · 外部模型已禁用`
      : active
        ? `当前：${active.name} · ${providerProtocolLabel(active.protocol)} · ${active.model}`
        : "未选择当前服务";
  }
}

function updateProviderModelOptions(models) {
  state.providerModels = Array.isArray(models) ? models.slice() : [];
  const datalist = $("#provider-models");
  if (datalist) {
    datalist.innerHTML = state.providerModels
      .map((model) => `<option value="${esc(model)}"></option>`)
      .join("");
  }
}

function applyProviderPreset(presetKey) {
  const preset = state.presets.find((item) => item.key === presetKey);
  if (!preset) return;
  const form = $("#provider-form");
  form.provider_id.value = "";
  state.editingProviderId = null;
  form.name.value = preset.name || "";
  form.protocol.value = preset.protocol || "anthropic";
  form.auth_style.value = preset.auth_style || "bearer";
  form.base_url.value = preset.base_url || "";
  form.model.value = preset.model || "";
  form.website_url.value = preset.website_url || "";
  form.docs_url.value = preset.docs_url || "";
  form.requires_key.checked = preset.requires_key !== false;
  form.extra_headers_json.value = "{}";
  form.api_key.value = "";
  form.activate.checked = false;
  updateProviderModelOptions(preset.models || []);
  $("#save-provider").textContent = "添加服务";
  $("#cancel-provider").hidden = true;
  $("#provider-form-note").textContent = preset.note || "";
  if (!preset.model) setTimeout(() => form.model.focus(), 0);
}

function fillProviderForm(provider) {
  const form = $("#provider-form");
  state.editingProviderId = provider.id;
  form.provider_id.value = provider.id;
  $("#provider-preset").value = state.presets.some((item) => item.key === provider.preset_key)
    ? provider.preset_key
    : "custom";
  form.name.value = provider.name || "";
  form.protocol.value = provider.protocol || "anthropic";
  form.auth_style.value = provider.auth_style || "bearer";
  form.base_url.value = provider.base_url || "";
  form.model.value = provider.model || "";
  form.website_url.value = provider.website_url || "";
  form.docs_url.value = provider.docs_url || "";
  form.requires_key.checked = provider.requires_key !== false;
  form.extra_headers_json.value = JSON.stringify(provider.extra_headers || {}, null, 0);
  form.api_key.value = "";
  form.activate.checked = false;
  updateProviderModelOptions(provider.models || []);
  $("#save-provider").textContent = "保存修改";
  $("#cancel-provider").hidden = false;
  $("#provider-form-note").textContent = "编辑服务。API Key 留空会保留现有密钥。";
  $("#provider-form").scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetProviderForm() {
  const form = $("#provider-form");
  form.reset();
  form.provider_id.value = "";
  form.requires_key.checked = true;
  form.extra_headers_json.value = "{}";
  state.editingProviderId = null;
  state.providerModels = [];
  $("#provider-preset").value = "";
  $("#provider-models").innerHTML = "";
  $("#save-provider").textContent = "添加服务";
  $("#cancel-provider").hidden = true;
  $("#provider-form-note").textContent = "";
}

async function saveProvider(event) {
  event.preventDefault();
  const form = $("#provider-form");
  const button = $("#save-provider");
  let headers = {};
  const rawHeaders = form.extra_headers_json.value.trim();
  if (rawHeaders) {
    try {
      headers = JSON.parse(rawHeaders);
    } catch (error) {
      toast("自定义请求头必须是 JSON 对象", true);
      return;
    }
    if (!headers || Array.isArray(headers) || typeof headers !== "object") {
      toast("自定义请求头必须是 JSON 对象", true);
      return;
    }
  }
  const editing = state.editingProviderId;
  const existing = state.providers.find((provider) => provider.id === editing);
  const body = {
    preset_key: form.preset_key.value || null,
    name: form.name.value.trim(),
    protocol: form.protocol.value,
    auth_style: form.auth_style.value,
    base_url: form.base_url.value.trim(),
    model: form.model.value.trim(),
    models: state.providerModels,
    website_url: form.website_url.value.trim(),
    docs_url: form.docs_url.value.trim(),
    extra_headers: headers,
    requires_key: form.requires_key.checked,
    activate: form.activate.checked,
  };
  if (existing) body.api_key_url = existing.api_key_url || "";
  const key = form.api_key.value.trim();
  if (key) body.api_key = key;
  setBusy(button, true, "保存中…");
  try {
    if (editing) {
      await api(`/api/llm/providers/${encodeURIComponent(editing)}`, { method: "PATCH", body });
    } else {
      await api("/api/llm/providers", { method: "POST", body });
    }
    toast(editing ? "模型服务已更新" : "模型服务已添加");
    resetProviderForm();
    await loadSettings();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

async function activateProvider(providerId) {
  try {
    await api(`/api/llm/providers/${encodeURIComponent(providerId)}/activate`, { method: "POST" });
    toast("当前模型服务已切换");
    await loadSettings();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  }
}

async function testProvider(providerId, button) {
  setBusy(button, true, "测试中…");
  try {
    const result = await api(`/api/llm/providers/${encodeURIComponent(providerId)}/test`, { method: "POST" });
    toast(`连接成功：${result.provider_name} / ${result.model}${result.input_tokens != null ? `（${result.input_tokens} tokens）` : ""}`);
    await loadSettings();
    await refreshStatus();
  } catch (error) {
    toast(`连接失败：${error.message}`, true);
  } finally {
    setBusy(button, false);
  }
}

async function deleteProvider(providerId) {
  if (!window.confirm("删除这个模型服务及其本地密钥？")) return;
  try {
    await api(`/api/llm/providers/${encodeURIComponent(providerId)}`, { method: "DELETE" });
    if (state.editingProviderId === providerId) resetProviderForm();
    toast("模型服务已删除");
    await loadSettings();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  }
}

// Profile --------------------------------------------------------------------
async function loadProfile() {
  const profile = await api("/api/profile");
  state.profile = profile;
  $("#profile-form [name=name]").value = profile.name || "";
  $("#profile-form [name=headline]").value = profile.headline || "";
  $("#profile-form [name=location]").value = profile.location || "";
  $("#profile-form [name=github]").value = (profile.links && profile.links.github) || "";
  $("#profile-form [name=website]").value = (profile.links && profile.links.website) || "";
  $("#resume-text").value = profile.resume_text || "";
  $("#profile-completeness").textContent = `${profile.completeness || 0}%`;
  renderSkills(profile.skills);
  renderGoals(profile.goals);
  renderSuggestions(profile.suggestions);
}

const SKILL_CATEGORY_LABELS = {
  language: "语言",
  framework: "框架/库",
  database: "数据库",
  middleware: "中间件",
  platform: "平台",
  devops: "DevOps",
  tool: "工具",
  graphics: "图形",
  protocol: "协议",
  architecture: "架构",
  concept: "概念",
  other: "其他",
};

function renderSkills(skills) {
  const container = $("#skills-list");
  if (!skills || !skills.length) {
    container.innerHTML = empty("还没有技能。可以从简历提取，或手动添加。");
    return;
  }
  const levels = ["了解", "入门", "熟练", "精通", "专家"];
  container.innerHTML = skills
    .map(
      (skill) => `
      <div class="stack-row" data-skill="${esc(skill.id)}">
        <div>
          <div class="stack-name">${esc(skill.name)} ${skill.category ? `<span class="tag">${esc(SKILL_CATEGORY_LABELS[skill.category] || skill.category)}</span>` : ""}</div>
          <div class="stack-level">${esc(levels[skill.level] || "熟练")}${skill.source === "resume" ? " · 简历提取" : ""}</div>
        </div>
        <div class="row-actions">
          <select class="skill-level" aria-label="${esc(skill.name)} 熟练度">
            ${levels.map((label, index) => `<option value="${index}" ${Number(skill.level) === index ? "selected" : ""}>${label}</option>`).join("")}
          </select>
          <button class="icon-btn" data-action="delete-skill" type="button">删除</button>
        </div>
      </div>`
    )
    .join("");
}

function renderGoals(goals) {
  const short = (goals || []).filter((goal) => goal.kind === "short");
  const long = (goals || []).filter((goal) => goal.kind === "long");
  renderGoalList($("#short-goals"), short, "还没有短期目标。");
  renderGoalList($("#long-goals"), long, "还没有长期目标。");
}

function renderGoalList(container, goals, emptyText) {
  if (!goals.length) {
    container.innerHTML = empty(emptyText);
    return;
  }
  const statusLabels = { active: "进行中", done: "已完成", paused: "暂缓", blocked: "受阻" };
  container.innerHTML = goals
    .map(
      (goal) => `
      <div class="goal-row" data-goal="${esc(goal.id)}">
        <div class="goal-main">
          <div class="goal-title">${esc(goal.title)}</div>
          <div class="goal-meta">${goal.due_date ? `截止 ${esc(goal.due_date)} · ` : ""}${esc(goal.description || "")}</div>
        </div>
        <div class="row-actions">
          <select class="goal-status-select" aria-label="目标状态">
            ${Object.entries(statusLabels)
              .map(([value, label]) => `<option value="${value}" ${goal.status === value ? "selected" : ""}>${label}</option>`)
              .join("")}
          </select>
          <button class="icon-btn" data-action="delete-goal" type="button">删除</button>
        </div>
      </div>`
    )
    .join("");
}

function renderSuggestions(suggestions) {
  const container = $("#resume-suggestions");
  const acceptAll = $("#accept-all-suggestions");
  const dismissAll = $("#dismiss-all-suggestions");
  const hasSuggestions = Boolean(suggestions && suggestions.length);
  if (acceptAll) acceptAll.hidden = !hasSuggestions;
  if (dismissAll) dismissAll.hidden = !hasSuggestions;
  if (!suggestions || !suggestions.length) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = suggestions
    .map(
      (item) => `
      <div class="suggestion" data-suggestion="${esc(item.id)}">
        <div>
          <strong>${esc(item.name)}</strong>
          ${item.category ? `<span class="tag">${esc(SKILL_CATEGORY_LABELS[item.category] || item.category)}</span>` : ""}
          <em>${esc(item.evidence || "来自简历")}</em>
        </div>
        <div class="row-actions">
          <button class="btn small" data-action="accept-suggestion" type="button">确认加入</button>
          <button class="icon-btn" data-action="dismiss-suggestion" type="button">忽略</button>
        </div>
      </div>`
    )
    .join("");
}

async function saveProfile() {
  const button = $("#save-profile");
  setBusy(button, true, "保存中…");
  try {
    const links = {};
    const github = $("#profile-form [name=github]").value.trim();
    const website = $("#profile-form [name=website]").value.trim();
    if (github) links.github = github;
    if (website) links.website = website;
    await api("/api/profile", {
      method: "PATCH",
      body: {
        name: $("#profile-form [name=name]").value,
        headline: $("#profile-form [name=headline]").value,
        location: $("#profile-form [name=location]").value,
        links,
      },
    });
    $("#profile-save-state").textContent = "已保存";
    toast("画像已保存");
    await loadProfile();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

async function analyzeResume() {
  const button = $("#analyze-resume");
  const text = $("#resume-text").value.trim();
  if (!text) {
    toast("先粘贴简历文本", true);
    return;
  }
  setBusy(button, true, "提取中…");
  try {
    const result = await api("/api/profile/resume", {
      method: "POST",
      body: { text, filename: "resume.md" },
    });
    await applyResumeResult(result);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

async function applyResumeResult(result) {
  $("#resume-state").textContent = `已保存到 ${shortPath(result.saved_to)}`;
  toast(`简历已保存，生成 ${result.suggestions.length} 条待确认技能`);
  state.profile = result.profile;
  $("#resume-text").value = result.profile.resume_text || $("#resume-text").value;
  renderSuggestions(result.suggestions);
  await loadProfile();
  await refreshStatus();
}

async function uploadResumeFile(file) {
  const data = new FormData();
  data.append("file", file);
  $("#resume-state").textContent = "正在读取文件…";
  try {
    const result = await apiForm("/api/profile/resume-file", data);
    await applyResumeResult(result);
  } catch (error) {
    $("#resume-state").textContent = "";
    toast(error.message, true);
  }
}

function wireProfile() {
  $("#save-profile").addEventListener("click", saveProfile);
  $("#analyze-resume").addEventListener("click", analyzeResume);
  $("#accept-all-suggestions").addEventListener("click", async () => {
    if (!window.confirm("把当前所有待确认技能加入技术栈？")) return;
    try {
      const result = await api("/api/profile/suggestions/accept-all", { method: "POST" });
      toast(`已加入 ${result.accepted} 项技能`);
      await loadProfile();
      await refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#dismiss-all-suggestions").addEventListener("click", async () => {
    if (!window.confirm("忽略当前所有待确认技能建议？")) return;
    try {
      await api("/api/profile/suggestions", { method: "DELETE" });
      toast("技能建议已清空");
      await loadProfile();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#resume-file").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) await uploadResumeFile(file);
    event.target.value = "";
  });

  $("#skill-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/api/skills", {
        method: "POST",
        body: {
          name: form.name.value.trim(),
          category: form.category.value,
          level: Number(form.level.value),
        },
      });
      form.reset();
      await loadProfile();
      await refreshStatus();
      toast("技能已添加");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#short-goal-form").addEventListener("submit", (event) => addGoal(event, "short"));
  $("#long-goal-form").addEventListener("submit", (event) => addGoal(event, "long"));

  $("#skills-list").addEventListener("change", async (event) => {
    if (!event.target.classList.contains("skill-level")) return;
    const row = event.target.closest("[data-skill]");
    try {
      await api(`/api/skills/${row.dataset.skill}`, {
        method: "PATCH",
        body: { level: Number(event.target.value) },
      });
      toast("熟练度已更新");
    } catch (error) {
      toast(error.message, true);
    }
  });

  document.addEventListener("click", async (event) => {
    const action = event.target.closest("[data-action]");
    if (!action) return;
    const kind = action.dataset.action;
    if (kind === "delete-skill") {
      const row = action.closest("[data-skill]");
      await guarded(() => api(`/api/skills/${row.dataset.skill}`, { method: "DELETE" }), "技能已删除", loadProfile);
    }
    if (kind === "delete-goal") {
      const row = action.closest("[data-goal]");
      await guarded(() => api(`/api/goals/${row.dataset.goal}`, { method: "DELETE" }), "目标已删除", loadProfile);
    }
    if (kind === "accept-suggestion") {
      const row = action.closest("[data-suggestion]");
      await guarded(
        () => api(`/api/profile/suggestions/${row.dataset.suggestion}/accept`, { method: "POST" }),
        "技能已加入",
        loadProfile
      );
    }
    if (kind === "dismiss-suggestion") {
      const row = action.closest("[data-suggestion]");
      await guarded(
        () => api(`/api/profile/suggestions/${row.dataset.suggestion}`, { method: "DELETE" }),
        "建议已忽略",
        loadProfile
      );
    }
  });

  document.addEventListener("change", async (event) => {
    if (!event.target.classList.contains("goal-status-select")) return;
    const row = event.target.closest("[data-goal]");
    try {
      await api(`/api/goals/${row.dataset.goal}`, {
        method: "PATCH",
        body: { status: event.target.value },
      });
      toast("目标状态已更新");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

async function addGoal(event, kind) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api("/api/goals", {
      method: "POST",
      body: {
        kind,
        title: form.title.value.trim(),
        description: form.description ? form.description.value.trim() : "",
        due_date: form.due_date && form.due_date.value ? form.due_date.value : null,
      },
    });
    form.reset();
    await loadProfile();
    await refreshStatus();
    toast("目标已添加");
  } catch (error) {
    toast(error.message, true);
  }
}

async function guarded(action, successMessage, reload) {
  try {
    await action();
    if (successMessage) toast(successMessage);
    if (reload) await reload();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  }
}

// Inbox ----------------------------------------------------------------------
async function loadInbox() {
  const data = await api(`/api/inbox${state.inboxStatus ? `?status=${encodeURIComponent(state.inboxStatus)}` : ""}`);
  renderInbox(data.items || []);
}

function renderInbox(items) {
  const container = $("#inbox-list");
  $("#inbox-count").textContent = `当前 ${items.length} 条`;
  if (!items.length) {
    container.innerHTML = empty("这里还没有资料。点击“立即采集”，或在设置中添加来源。");
    return;
  }
  const statusLabels = { inbox: "未读", read: "已读", saved: "已保存", skipped: "略过", not_relevant: "不相关" };
  container.innerHTML = items
    .map((item) => {
      const tags = (item.tags || []).slice(0, 8);
      return `
      <article class="item-card" data-type="${esc(item.source_type)}" data-item="${esc(item.id)}">
        <div class="item-top">
          <div>
            <h3 class="item-title">${esc(item.title)}</h3>
            <div class="item-meta">
              ${esc(item.source_type)}${item.author ? ` · ${esc(item.author)}` : ""}
              ${item.published_at ? ` · ${esc(fmtDate(item.published_at))}` : ""}
              · 采集于 ${esc(fmtDate(item.fetched_at, true))}
            </div>
          </div>
          <span class="item-status ${esc(item.status)}">${esc(statusLabels[item.status] || item.status)}</span>
        </div>
        <p class="item-why">${esc(item.why || "暂无推荐理由")}</p>
        <p class="item-summary">${esc(item.summary || "")}</p>
        ${tagRow(tags)}
        <div class="item-actions">
          <button class="btn small" data-action="open-note" data-id="${esc(item.id)}" type="button">查看本地卡片</button>
          ${item.canonical_url ? `<a class="btn small ghost" href="${esc(item.canonical_url)}" target="_blank" rel="noreferrer">打开原文</a>` : ""}
          <button class="btn small ghost" data-action="feedback" data-id="${esc(item.id)}" data-status="read" type="button">标记已读</button>
          <button class="btn small ghost" data-action="feedback" data-id="${esc(item.id)}" data-status="saved" type="button">保存</button>
          <button class="btn small ghost" data-action="feedback" data-id="${esc(item.id)}" data-status="not_relevant" type="button">不相关</button>
        </div>
      </article>`;
    })
    .join("");
}

async function collectNow() {
  const button = $("#collect-now");
  setBusy(button, true, "采集中…");
  try {
    const result = await api("/api/collect", { method: "POST" });
    const saved = (result.results || []).reduce((sum, item) => sum + item.saved, 0);
    const errors = result.errors || [];
    toast(`采集完成，新增或更新 ${saved} 条${errors.length ? `；${errors.length} 个来源失败` : ""}`, errors.length > 0);
    await loadInbox();
    if (state.news.length || $("#view-news").classList.contains("is-active")) {
      await loadNews();
    }
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function wireInbox() {
  $("#collect-now").addEventListener("click", collectNow);
  $("#inbox-filter").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-status]");
    if (!button) return;
    state.inboxStatus = button.dataset.status;
    $$("#inbox-filter button").forEach((node) => node.classList.toggle("is-active", node === button));
    loadInbox().catch((error) => toast(error.message, true));
  });
  $("#inbox-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    if (button.dataset.action === "open-note") {
      await openNote(button.dataset.id);
      return;
    }
    if (button.dataset.action === "feedback") {
      try {
        await api(`/api/items/${button.dataset.id}/feedback`, {
          method: "POST",
          body: { status: button.dataset.status },
        });
        toast(button.dataset.status === "not_relevant" ? "已标记不相关，并下调相关兴趣" : "状态已更新");
        await loadInbox();
        await refreshStatus();
      } catch (error) {
        toast(error.message, true);
      }
    }
  });
}

// News -----------------------------------------------------------------------
async function loadNews() {
  const data = await api("/api/news?days=7&limit=200");
  state.news = data.items || [];
  renderNewsPlatforms();
  renderNews();
  if (state.newsPlatform === "GitHub") {
    await loadGithubRanking();
  }
}

function platformOf(item) {
  return item.source_platform || "未分类";
}

function renderNewsPlatforms() {
  const container = $("#news-platforms");
  const platforms = ["all"];
  for (const item of state.news) {
    const platform = platformOf(item);
    if (!platforms.includes(platform)) platforms.push(platform);
  }
  if (state.newsPlatform !== "all" && !platforms.includes(state.newsPlatform)) {
    state.newsPlatform = "all";
  }
  container.innerHTML = platforms
    .map((platform) => {
      const count = platform === "all"
        ? state.news.length
        : state.news.filter((item) => platformOf(item) === platform).length;
      const label = platform === "all" ? "全部" : platform;
      return `<button type="button" data-platform="${esc(platform)}" class="${state.newsPlatform === platform ? "is-active" : ""}">${esc(label)} ${count}</button>`;
    })
    .join("");
}

function renderNews() {
  const container = $("#news-list");
  const items = state.newsPlatform === "all"
    ? state.news
    : state.news.filter((item) => platformOf(item) === state.newsPlatform);
  const panel = $("#github-ranking-panel");
  if (panel) panel.hidden = state.newsPlatform !== "GitHub";
  $("#news-count").textContent = `最近 7 天 ${items.length} 条${state.newsPlatform === "all" ? "" : ` · ${state.newsPlatform}`}`;
  if (!items.length) {
    container.innerHTML = empty(
      state.newsPlatform === "GitHub"
        ? "最近 7 天没有 GitHub 来源的资讯。可在下方刷新 GitHub 榜单。"
        : "最近 7 天还没有这类资讯。等待定时采集，或在收件箱点击“立即采集”。"
    );
    return;
  }
  const organizedLabels = { ai: "AI 整理", fallback: "待整理" };
  container.innerHTML = items
    .map((item) => {
      const title = item.title_zh || item.title;
      const original = item.title_zh && item.title_zh !== item.title ? item.title : "";
      return `
      <article class="news-card" data-news="${esc(item.id)}">
        <h3>${esc(title)}</h3>
        ${original ? `<div class="original-title">原标题：${esc(original)}</div>` : ""}
        <div class="news-meta">
          ${esc(item.source_name || item.source_type)}${item.author ? ` · ${esc(item.author)}` : ""}
          ${item.published_at ? ` · ${esc(fmtDate(item.published_at))}` : ""}
          · 获取于 ${esc(fmtDate(item.fetched_at, true))}
          · ${esc(organizedLabels[item.organization] || item.organization)}
        </div>
        <p class="news-summary">${esc(item.summary || "暂无概括")}</p>
        ${tagRow((item.tags || []).slice(0, 8))}
        <div class="item-actions">
          <button class="btn small" data-action="open-news" data-id="${esc(item.id)}" type="button">查看全文</button>
          ${item.canonical_url ? `<a class="btn small ghost" href="${esc(item.canonical_url)}" target="_blank" rel="noreferrer">打开原文</a>` : ""}
          ${item.obsidian_uri ? `<a class="btn small ghost" href="${esc(item.obsidian_uri)}">在 Obsidian 打开</a>` : ""}
        </div>
      </article>`;
    })
    .join("");
}

async function loadGithubRanking(force = false) {
  const list = $("#github-ranking-list");
  const note = $("#github-note");
  if (!list) return;
  const mode = $("#github-mode").value || state.githubMode;
  const category = $("#github-category").value || state.githubCategory;
  state.githubMode = mode;
  state.githubCategory = category;
  const key = `${mode}:${category}`;
  if (!force && state.githubRanking && state.githubRanking.__key === key) {
    renderGithubRanking(state.githubRanking);
    return;
  }
  list.innerHTML = empty("正在读取 GitHub 榜单…");
  note.textContent = "";
  try {
    const data = await api(`/api/github/rankings?mode=${encodeURIComponent(mode)}&category=${encodeURIComponent(category)}&limit=12`);
    data.__key = key;
    state.githubRanking = data;
    renderGithubRanking(data);
  } catch (error) {
    list.innerHTML = empty(`GitHub 榜单加载失败：${error.message}`);
    note.textContent = "";
  }
}

function renderGithubRanking(data) {
  const list = $("#github-ranking-list");
  const note = $("#github-note");
  note.textContent = `${data.mode_label} · ${data.category_label} · ${data.note}`;
  const gainLabels = {
    trending_daily: "今日",
    trending_weekly: "本周",
    trending_monthly: "本月",
  };
  if (!data.repos || !data.repos.length) {
    list.innerHTML = empty("这个榜单暂时没有结果。");
    return;
  }
  list.innerHTML = data.repos
    .map((repo) => {
      const gained = repo.stars_gained != null
        ? `<span class="github-gain">${esc(gainLabels[data.mode] || "")} +${esc(repo.stars_gained)} Star</span>`
        : "";
      const stars = repo.stars != null ? `★ ${esc(repo.stars)}` : "";
      const forks = repo.forks != null ? `⑂ ${esc(repo.forks)}` : "";
      return `
      <div class="github-card">
        <div class="github-main">
          <div class="github-name"><a href="${esc(repo.url)}" target="_blank" rel="noreferrer">${esc(repo.full_name)}</a>
            ${repo.language ? `<span class="tag">${esc(repo.language)}</span>` : ""}
          </div>
          <p class="github-desc">${esc(repo.summary_zh || repo.description || "暂无描述")}</p>
          <div class="github-meta">${[stars, forks, gained].filter(Boolean).join(" · ")}
            ${repo.summary_source === "ai" ? " · AI 概括" : ""}
            ${(repo.topics || []).length ? ` · ${esc((repo.topics || []).slice(0, 5).join(", "))}` : ""}
          </div>
        </div>
        <div class="github-actions"><a class="btn small ghost" href="${esc(repo.url)}" target="_blank" rel="noreferrer">查看仓库</a></div>
      </div>`;
    })
    .join("");
}

async function openNewsDetail(itemId) {
  try {
    const item = await api(`/api/items/${encodeURIComponent(itemId)}`);
    $("#news-detail-title").textContent = item.title_zh || item.title;
    $("#news-detail-meta").textContent = [
      item.source_name || item.source_type,
      item.author,
      item.published_at ? fmtDate(item.published_at) : "",
      `获取于 ${fmtDate(item.fetched_at, true)}`,
      item.content_scope === "full" ? "本地全文" : "本地摘录",
    ].filter(Boolean).join(" · ");
    $("#news-detail-summary").textContent = item.summary || "暂无概括";
    const body = item.content_text || item.excerpt || "本地没有保存正文。请打开原文查看。";
    $("#news-detail-body").textContent =
      (item.content_scope === "full" ? "" : "注意：本地只保存了摘要或摘录，以下不是完整原文。\n\n") + body;
    const source = $("#news-source-link");
    if (item.canonical_url) {
      source.href = item.canonical_url;
      source.hidden = false;
    } else {
      source.hidden = true;
    }
    const obsidian = $("#news-obsidian-link");
    if (item.obsidian_uri) {
      obsidian.href = item.obsidian_uri;
      obsidian.hidden = false;
    } else {
      obsidian.hidden = true;
    }
    const dialog = $("#news-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  } catch (error) {
    toast(error.message, true);
  }
}

function wireNews() {
  $("#refresh-news").addEventListener("click", () => loadNews().catch((error) => toast(error.message, true)));
  $("#news-platforms").addEventListener("click", (event) => {
    const button = event.target.closest("[data-platform]");
    if (!button) return;
    state.newsPlatform = button.dataset.platform;
    renderNewsPlatforms();
    renderNews();
    if (state.newsPlatform === "GitHub") {
      loadGithubRanking().catch((error) => toast(error.message, true));
    }
  });
  $("#github-mode").addEventListener("change", () => loadGithubRanking(true));
  $("#github-category").addEventListener("change", () => loadGithubRanking(true));
  $("#github-refresh").addEventListener("click", () => loadGithubRanking(true));
  $("#news-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action=open-news]");
    if (button) await openNewsDetail(button.dataset.id);
  });
  const dialog = $("#news-dialog");
  $("#close-news").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

// Search ---------------------------------------------------------------------
async function runSearch(event) {
  event.preventDefault();
  const query = $("#search-query").value.trim();
  const tags = $("#search-tags").value.trim();
  if (!query && !tags) {
    toast("输入关键词或标签", true);
    return;
  }
  const button = $("#search-form button[type=submit]");
  setBusy(button, true, "检索中…");
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}&tags=${encodeURIComponent(tags)}&limit=30`);
    state.lastSearch = data;
    $("#search-meta").textContent = `找到 ${data.total} 条结果${data.tags.length ? `；标签：${data.tags.join("、")}` : ""}`;
    renderSearchResults(data.results || []);
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function renderSearchResults(results) {
  const container = $("#search-results");
  if (!results.length) {
    container.innerHTML = empty("没有命中。试试更短的术语，或先在收件箱保存相关资料。");
    return;
  }
  container.innerHTML = results
    .map(
      (result) => `
      <article class="result-card">
        <h3>${esc(result.title)}</h3>
        <div class="result-path">${esc(result.path)}</div>
        <p class="result-snippet">${esc(result.snippet || "")}</p>
        ${tagRow(result.tags || [])}
        <div class="item-actions">
          <button class="btn small" data-action="open-search-note" data-id="${esc(result.note_id)}" type="button">打开本地笔记</button>
          <span class="hint">匹配：${esc((result.matched_by || []).join(" / "))}</span>
        </div>
      </article>`
    )
    .join("");
}

function wireSearch() {
  $("#search-form").addEventListener("submit", runSearch);
  $("#search-results").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action=open-search-note]");
    if (button) await openNote(button.dataset.id);
  });
}

// Ask ------------------------------------------------------------------------
async function askQuestion(event) {
  event.preventDefault();
  const question = $("#ask-question").value.trim();
  if (!question) return;
  const mode = $("input[name=ask-mode]:checked").value;
  const button = $("#ask-form button[type=submit]");
  setBusy(button, true, "思考中…");
  try {
    const result = await api("/api/ask", {
      method: "POST",
      body: { question, mode, top_k: 8, group_id: state.qaTargetGroup },
    });
    renderAnswer(question, result);
    $("#ask-question").value = "";
    await loadQA();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function qaQuery() {
  if (state.qaFilter === "ungrouped") return "?ungrouped=true&limit=100";
  if (state.qaFilter !== "all") return `?group_id=${encodeURIComponent(state.qaFilter)}&limit=100`;
  return "?limit=100";
}

async function loadQA() {
  const [groups, history] = await Promise.all([
    api("/api/qa/groups"),
    api(`/api/qa/history${qaQuery()}`),
  ]);
  state.qaGroups = groups;
  if (state.qaFilter !== "all" && state.qaFilter !== "ungrouped" && !groups.groups.some((group) => group.id === state.qaFilter)) {
    state.qaFilter = "all";
  }
  state.qaTargetGroup = state.qaFilter !== "all" && state.qaFilter !== "ungrouped" ? state.qaFilter : null;  state.qaEntries = history.entries || [];
  renderQAGroups();
  renderQAHistory();
}

function renderQAGroups() {
  const container = $("#qa-group-list");
  const data = state.qaGroups;
  const rows = [
    { id: "all", name: "全部", count: data.total || 0, delete: false },
    { id: "ungrouped", name: "未分组", count: data.ungrouped_count || 0, delete: false },
    ...(data.groups || []).map((group) => ({ id: group.id, name: group.name, count: group.count, delete: true })),
  ];
  container.innerHTML = rows
    .map(
      (row) => `
      <div class="qa-group-row ${state.qaFilter === row.id ? "is-active" : ""}" data-group-row="${esc(row.id)}">
        <button class="qa-group-select" data-group="${esc(row.id)}" type="button">
          <span>${esc(row.name)}</span><span class="qa-group-count">${esc(row.count)}</span>
        </button>
        ${row.delete ? `<button class="icon-btn" data-action="delete-qa-group" data-id="${esc(row.id)}" title="删除分组" aria-label="删除分组" type="button">×</button>` : ""}
      </div>`
    )
    .join("");
  const target = (data.groups || []).find((group) => group.id === state.qaTargetGroup);
  $("#ask-group-hint").textContent = target
    ? `新问题会保存到“${target.name}”。`
    : "新问题会保存到“未分组”。";
}

function renderQAHistory() {
  const container = $("#qa-history-list");
  const filterLabel =
    state.qaFilter === "all"
      ? "全部"
      : state.qaFilter === "ungrouped"
        ? "未分组"
        : (state.qaGroups.groups || []).find((group) => group.id === state.qaFilter)?.name || "分组";
  $("#qa-history-meta").textContent = `${state.qaEntries.length} 条 · ${filterLabel}`;
  if (!state.qaEntries.length) {
    container.innerHTML = empty("这个分组还没有问答记录。提问后会自动保存在这里。");
    return;
  }
  const modeLabels = { grounded: "本地证据", extractive: "本地摘录", partial: "引用未通过", general: "通用知识", insufficient: "证据不足" };
  container.innerHTML = state.qaEntries
    .map((entry) => {
      const selectOptions = [
        `<option value="">未分组</option>`,
        ...(state.qaGroups.groups || []).map(
          (group) =>
            `<option value="${esc(group.id)}" ${group.id === entry.group_id ? "selected" : ""}>${esc(group.name)}</option>`
        ),
      ].join("");
      return `
      <div class="qa-history-row" data-entry="${esc(entry.id)}">
        <div>
          <button class="qa-question" data-action="open-qa" data-id="${esc(entry.id)}" type="button">${esc(entry.question)}</button>
          <div class="qa-history-meta">
            ${esc(fmtDate(entry.created_at, true))} · ${esc(modeLabels[entry.mode] || entry.mode)}
            · ${entry.grounded ? "有本地引用" : "无本地引用"}
            ${entry.group_name ? ` · ${esc(entry.group_name)}` : ""}
          </div>
          <p class="qa-history-preview">${esc(entry.answer_preview || "")}</p>
        </div>
        <div class="qa-history-actions">
          <select class="qa-assign-group" data-entry="${esc(entry.id)}" aria-label="移动到分组">${selectOptions}</select>
          <button class="icon-btn" data-action="delete-qa" data-id="${esc(entry.id)}" aria-label="删除问答记录" type="button">删除</button>
        </div>
      </div>`;
    })
    .join("");
}

function renderAnswer(question, result) {
  const panel = $("#ask-answer");
  panel.hidden = false;
  panel.className = `ask-answer ${result.mode === "general" ? "general" : result.mode === "insufficient" ? "insufficient" : ""}`;
  const modeLabels = {
    grounded: "本地证据支持",
    extractive: "本地证据摘录",
    partial: "引用校验未通过",
    general: "模型通用知识",
    insufficient: "证据不足",
  };
  const citations = (result.citations || [])
    .map((citation) => {
      const stale = Boolean(citation.stale);
      return `
      <div class="citation ${stale ? "is-stale" : ""}">
        <div class="citation-head"><span class="citation-id">${esc(citation.citation_id)}</span><span>${esc(citation.title)}</span></div>
        ${stale ? `<div class="citation-stale">本地文档已移动或删除，引用已失效。</div>` : `<div class="citation-path result-path">${esc(citation.path)}</div>`}
        ${citation.quote ? `<p class="citation-quote">“${esc(citation.quote)}”</p>` : `<p class="citation-quote">${esc(citation.snippet || "")}</p>`}
        ${stale ? "" : `<div class="citation-actions">
          <button class="btn small" data-action="open-citation" data-id="${esc(citation.note_id)}" type="button">查看本地文档</button>
          ${citation.obsidian_uri ? `<a class="btn small ghost" href="${esc(citation.obsidian_uri)}">在 Obsidian 打开</a>` : ""}
        </div>`}
      </div>`;
    })
    .join("");
  panel.innerHTML = `
    <div class="answer-question">${esc(question)}</div>
    <div class="answer-kicker">${result.created_at ? `${esc(fmtDate(result.created_at, true))} · ` : ""}${esc(modeLabels[result.mode] || result.mode)} · 本地相关笔记 ${esc(result.local_note_count ?? (result.citations || []).length)} 条</div>
    <div class="answer-text">${esc(result.answer)}</div>
    ${result.notice ? `<div class="answer-notice">${esc(result.notice)}</div>` : ""}
    ${citations ? `<div class="citations">${citations}</div>` : `<p class="hint">没有可验证的本地引用。${result.mode === "insufficient" ? "可以换一种问法，或先在设置中接入来源。" : ""}</p>`}
    <details class="answer-details"><summary class="hint">查看检索到的本地片段（${(result.evidence || []).length}）</summary>
      ${(result.evidence || [])
        .map(
          (item) => `<div class="citation ${item.stale ? "is-stale" : ""}"><div class="citation-head"><span class="citation-id">${esc(item.citation_id || "—")}</span>${esc(item.title)}</div><p class="citation-quote">${esc(item.snippet || "")}</p>${item.stale ? `<div class="citation-stale">本地文档已失效</div>` : ""}</div>`
        )
        .join("") || empty("没有本地片段")}
    </details>`;
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function openQAEntry(entryId) {
  try {
    const entry = await api(`/api/qa/history/${encodeURIComponent(entryId)}`);
    renderAnswer(entry.question, entry);
    $$("#qa-history-list .qa-history-row").forEach((row) =>
      row.classList.toggle("is-active", row.dataset.entry === entryId)
    );
  } catch (error) {
    toast(error.message, true);
  }
}

function wireAsk() {
  $("#ask-form").addEventListener("submit", askQuestion);
  $("#ask-answer").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action=open-citation]");
    if (button) await openNote(button.dataset.id);
  });
  $("#qa-group-list").addEventListener("click", async (event) => {
    const deleteButton = event.target.closest("[data-action=delete-qa-group]");
    if (deleteButton) {
      if (!window.confirm("删除这个分组？其中的问答会回到“未分组”。")) return;
      try {
        await api(`/api/qa/groups/${encodeURIComponent(deleteButton.dataset.id)}`, { method: "DELETE" });
        if (state.qaFilter === deleteButton.dataset.id) state.qaFilter = "all";
        toast("分组已删除");
        await loadQA();
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    const button = event.target.closest("[data-group]");
    if (!button) return;
    state.qaFilter = button.dataset.group;
    await loadQA().catch((error) => toast(error.message, true));
  });
  $("#qa-group-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const group = await api("/api/qa/groups", {
        method: "POST",
        body: { name: form.name.value.trim() },
      });
      form.reset();
      state.qaFilter = group.id;
      toast("分组已创建");
      await loadQA();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#qa-history-list").addEventListener("click", async (event) => {
    const openButton = event.target.closest("[data-action=open-qa]");
    if (openButton) {
      await openQAEntry(openButton.dataset.id);
      return;
    }
    const deleteButton = event.target.closest("[data-action=delete-qa]");
    if (deleteButton) {
      if (!window.confirm("删除这条问答记录？")) return;
      try {
        await api(`/api/qa/history/${encodeURIComponent(deleteButton.dataset.id)}`, { method: "DELETE" });
        toast("问答记录已删除");
        await loadQA();
      } catch (error) {
        toast(error.message, true);
      }
    }
  });
  $("#qa-history-list").addEventListener("change", async (event) => {
    const select = event.target.closest(".qa-assign-group");
    if (!select) return;
    try {
      await api(`/api/qa/history/${encodeURIComponent(select.dataset.entry)}`, {
        method: "PATCH",
        body: { group_id: select.value || null },
      });
      toast("分组已更新");
      await loadQA();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#clear-qa-history").addEventListener("click", async () => {
    if (!window.confirm("清空所有问答历史？分组会保留。")) return;
    try {
      await api("/api/qa/history/clear", { method: "POST" });
      toast("问答历史已清空");
      await loadQA();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

// Reviews --------------------------------------------------------------------
async function loadReviews() {
  const [reviews, progress] = await Promise.all([api("/api/reviews"), api("/api/progress")]);
  state.reviews = reviews.reviews || [];
  renderReviewList();
  renderProgress(progress.progress || []);
}

function renderReviewList() {
  const container = $("#review-list");
  if (!state.reviews.length) {
    container.innerHTML = empty("还没有回顾。选择周期后点击“生成上一期”。");
    return;
  }
  container.innerHTML = state.reviews
    .map(
      (review) => `
      <div class="compact-row" data-review="${esc(review.id)}">
        <strong>${esc(review.period_type === "week" ? "周回顾" : "月回顾")} · ${esc(review.label || review.period_start_local)}</strong>
        <span>${esc(review.period_start_local)} 至 ${esc(review.period_end_local)} · r${esc(review.revision)} · ${esc(review.status)}</span>
      </div>`
    )
    .join("");
}

async function openReview(reviewId) {
  try {
    const review = await api(`/api/reviews/${encodeURIComponent(reviewId)}`);
    $("#review-detail-title").textContent = `${review.period_type === "week" ? "周回顾" : "月回顾"} · ${review.label || review.period_start_local}`;
    $("#review-detail").textContent = review.body || review.summary || "（空）";
    const link = $("#review-obsidian");
    if (review.obsidian_uri) {
      link.href = review.obsidian_uri;
      link.hidden = false;
    } else {
      link.hidden = true;
    }
    $$("#review-list .compact-row").forEach((row) => row.classList.toggle("is-active", row.dataset.review === reviewId));
  } catch (error) {
    toast(error.message, true);
  }
}

function renderProgress(progress) {
  const container = $("#progress-list");
  if (!progress.length) {
    container.innerHTML = empty("还没有学习记录。回顾不会把阅读自动算作掌握。");
    return;
  }
  container.innerHTML = progress
    .map(
      (row) => `
      <div class="progress-row" data-progress="${esc(row.id)}">
        <div>
          <strong>${esc(row.title)}</strong>
          <div class="goal-meta">${esc(row.skill || "未标注技能")} · ${esc(row.status)} · ${esc(row.minutes)} 分钟 · ${esc(fmtDate(row.occurred_at))}</div>
        </div>
        <button class="icon-btn" data-action="delete-progress" type="button">删除</button>
      </div>`
    )
    .join("");
}

async function generateReview() {
  const button = $("#generate-review");
  const period = $("#review-period").value;
  setBusy(button, true, "生成中…");
  try {
    const review = await api("/api/reviews/generate", {
      method: "POST",
      body: { period_type: period, offset: -1, force: false },
    });
    toast("回顾已生成");
    await loadReviews();
    await openReview(review.id);
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function wireReviews() {
  $("#generate-review").addEventListener("click", generateReview);
  $("#review-list").addEventListener("click", (event) => {
    const row = event.target.closest("[data-review]");
    if (row) openReview(row.dataset.review);
  });
  $("#progress-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/api/progress", {
        method: "POST",
        body: {
          title: form.title.value.trim(),
          skill: form.skill.value.trim(),
          status: form.status.value,
          minutes: Number(form.minutes.value || 0),
        },
      });
      form.reset();
      toast("学习记录已添加");
      await loadReviews();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#progress-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action=delete-progress]");
    if (!button) return;
    const row = button.closest("[data-progress]");
    await guarded(
      () => api(`/api/progress/${row.dataset.progress}`, { method: "DELETE" }),
      "学习记录已删除",
      loadReviews
    );
  });
}

// Interests ------------------------------------------------------------------
async function loadInterests() {
  const data = await api("/api/interests");
  state.interests = data.interests || [];
  renderInterests(data.half_life_days || 14);
}

function renderInterests(halfLife) {
  const container = $("#interest-list");
  if (!state.interests.length) {
    container.innerHTML = empty("还没有近期兴趣。主动检索或提问后，系统会在这里显示可解释、可撤销的信号。");
    return;
  }
  const max = Math.max(...state.interests.map((item) => item.score), 0.001);
  container.innerHTML = state.interests
    .map((interest) => {
      const width = Math.max(3, Math.round((interest.score / max) * 100));
      const status = interest.blocked ? "已屏蔽" : interest.pinned ? "已固定" : `半衰期 ${halfLife} 天`;
      return `
      <article class="interest-card" data-interest="${esc(interest.slug)}">
        <div class="interest-head">
          <span class="interest-name">${esc(interest.label)}</span>
          <span class="interest-score">${esc(interest.score.toFixed(2))} · ${esc(interest.signals)} 次信号</span>
        </div>
        <div class="interest-bar"><span style="width:${width}%"></span></div>
        <div class="interest-meta">${esc(status)} · 最近 ${esc(fmtDate(interest.last_signal_at, true))}${interest.expires_at ? ` · ${esc(fmtDate(interest.expires_at))} 过期` : ""}</div>
        ${(interest.evidence || []).length ? `<ul class="interest-evidence">${interest.evidence.map((item) => `<li>${esc(item.kind)}：${esc(item.query)}</li>`).join("")}</ul>` : ""}
        <div class="interest-meta-row">
          <button class="btn small ghost" data-action="pin-interest" data-pinned="${interest.pinned ? "0" : "1"}" type="button">${interest.pinned ? "取消固定" : "固定"}</button>
          <button class="btn small ghost" data-action="block-interest" data-blocked="${interest.blocked ? "0" : "1"}" type="button">${interest.blocked ? "取消屏蔽" : "屏蔽主题"}</button>
          <button class="btn small danger-quiet" data-action="delete-interest" type="button">删除并清除证据</button>
        </div>
      </article>`;
    })
    .join("");
}

function wireInterests() {
  $("#interest-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const card = button.closest("[data-interest]");
    const slug = card.dataset.interest;
    try {
      if (button.dataset.action === "pin-interest") {
        await api(`/api/interests/${encodeURIComponent(slug)}`, {
          method: "PATCH",
          body: { pinned: button.dataset.pinned === "1" },
        });
        toast("兴趣状态已更新");
      }
      if (button.dataset.action === "block-interest") {
        await api(`/api/interests/${encodeURIComponent(slug)}`, {
          method: "PATCH",
          body: { blocked: button.dataset.blocked === "1" },
        });
        toast("兴趣状态已更新");
      }
      if (button.dataset.action === "delete-interest") {
        await api(`/api/interests/${encodeURIComponent(slug)}`, { method: "DELETE" });
        toast("兴趣及其证据已删除");
      }
      await loadInterests();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#clear-interests").addEventListener("click", async () => {
    if (!window.confirm("清空所有检索与问答行为历史？固定兴趣会保留，自动兴趣将消失。")) return;
    try {
      await api("/api/interests/clear", { method: "POST" });
      toast("行为历史已清空");
      await loadInterests();
      await refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

// Settings -------------------------------------------------------------------
async function loadSettings() {
  const [sources, settingsPayload, tags, health] = await Promise.all([
    api("/api/sources"),
    api("/api/settings"),
    api("/api/tags"),
    api("/api/health"),
  ]);
  let providersPayload = null;
  let presetsPayload = null;
  let providerError = "";
  try {
    [providersPayload, presetsPayload] = await Promise.all([
      api("/api/llm/providers"),
      api("/api/llm/presets"),
    ]);
  } catch (error) {
    providerError = error.message || "模型服务接口不可用";
  }
  state.sources = sources.sources || [];
  state.settings = settingsPayload.settings || {};
  state.providers = (providersPayload && providersPayload.providers) || [];
  state.activeProviderId = providersPayload ? providersPayload.active_id : null;
  state.presets = (presetsPayload && presetsPayload.presets) || [];
  state.providerError = providerError;
  state.llm = (providersPayload && providersPayload.llm) || settingsPayload.llm || health.llm || {};
  renderSources();
  renderSettingsForm();
  renderProviderPresets();
  renderProviders();
  renderTags(tags.tags || []);
  renderSettingsStatus(health);
}

function renderSources() {
  const container = $("#source-list");
  if (!state.sources.length) {
    container.innerHTML = empty("还没有来源。先在下方添加一个 RSS、GitHub 仓库或 arXiv 分类。");
    return;
  }
  container.innerHTML = state.sources
    .map(
      (source) => `
      <div class="source-row" data-source="${esc(source.id)}">
        <div class="source-main">
          <div class="source-name">${esc(source.name)} <span class="tag">${esc(source.kind)}</span></div>
          <div class="source-config">${esc(JSON.stringify(source.config))}</div>
          <div class="source-status ${source.last_status === "error" ? "error" : "ok"}">
            ${source.enabled ? "启用" : "停用"} · 每 ${esc(source.interval_minutes)} 分钟 · 最近 ${esc(fmtDate(source.last_run_at, true))}
            ${source.last_error ? ` · ${esc(source.last_error)}` : ""}
          </div>
        </div>
        <div class="row-actions">
          <button class="btn small ghost" data-action="toggle-source" data-enabled="${source.enabled ? "1" : "0"}" type="button">${source.enabled ? "停用" : "启用"}</button>
          <button class="icon-btn" data-action="delete-source" type="button">删除</button>
        </div>
      </div>`
    )
    .join("");
}

function renderSettingsForm() {
  const form = $("#settings-form");
  form.timezone.value = state.settings.timezone || "Asia/Shanghai";
  form.collection_limit.value = state.settings.collection_limit || "12";
  form.interest_half_life_days.value = state.settings.interest_half_life_days || "14";
  form.track_interactions.checked = state.settings.track_interactions === "1";
  form.qa_history_enabled.checked = state.settings.qa_history_enabled !== "0";
  form.llm_enabled.checked = state.settings.llm_enabled === "1";
  form.allow_private_urls.checked = state.settings.allow_private_urls === "1";
}

function renderTags(tags) {
  const container = $("#tag-list");
  if (!tags.length) {
    container.innerHTML = `<span class="hint">还没有标签。采集资料后会自动登记。</span>`;
    return;
  }
  container.innerHTML = tags
    .slice(0, 60)
    .map((tag) => `<span class="tag-chip">${esc(tag.slug)}${tag.aliases && tag.aliases.length ? ` <em>↔ ${esc(tag.aliases.join(", "))}</em>` : ""}</span>`)
    .join("");
}

async function saveSettings() {
  const button = $("#save-settings");
  setBusy(button, true, "保存中…");
  const form = $("#settings-form");
  try {
    await api("/api/settings", {
      method: "PATCH",
      body: {
        timezone: form.timezone.value.trim(),
        collection_limit: Number(form.collection_limit.value),
        interest_half_life_days: Number(form.interest_half_life_days.value),
        track_interactions: form.track_interactions.checked,
        qa_history_enabled: form.qa_history_enabled.checked,
        llm_enabled: form.llm_enabled.checked,
        allow_private_urls: form.allow_private_urls.checked,
      },
    });
    $("#settings-state").textContent = "已保存";
    toast("设置已保存");
    await loadSettings();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function parseSourceTarget(kind, target) {
  const parts = target.split(",").map((item) => item.trim()).filter(Boolean);
  if (kind === "rss") return { url: target.trim() };
  if (kind === "github") return { repos: parts };
  if (kind === "arxiv") return { categories: parts };
  return { urls: parts };
}

async function addSource(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const kind = form.kind.value;
  try {
    await api("/api/sources", {
      method: "POST",
      body: {
        kind,
        name: form.name.value.trim(),
        platform: form.platform.value.trim(),
        config: parseSourceTarget(kind, form.target.value),
        interval_minutes: Number(form.interval.value || 1440),
      },
    });
    form.reset();
    updateSourceHint();
    toast("来源已添加");
    await loadSettings();
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  }
}

function updateSourceHint() {
  const kind = $("#source-kind").value;
  const hints = {
    rss: ["订阅地址", "RSS：填写完整订阅地址，例如 https://example.com/feed.xml。"],
    github: ["owner/repo，可逗号分隔", "GitHub：填写仓库全名，例如 redis/redis，可填多个。"],
    arxiv: ["分类，可逗号分隔", "arXiv：填写分类，例如 cs.AI, cs.DC 或 cs.LG。"],
    web: ["网页地址，可逗号分隔", "网页：填写完整 URL，可填多个。会遵守 robots.txt。"],
  };
  const [placeholder, hint] = hints[kind];
  $("#source-target").placeholder = placeholder;
  $("#source-target-hint").textContent = hint;
}

function wireSettings() {
  $("#save-settings").addEventListener("click", saveSettings);
  $("#provider-preset").addEventListener("change", (event) => applyProviderPreset(event.target.value));
  $("#provider-form").addEventListener("submit", saveProvider);
  $("#cancel-provider").addEventListener("click", resetProviderForm);
  $("#provider-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const row = button.closest("[data-provider]");
    if (!row) return;
    const id = row.dataset.provider;
    if (button.dataset.action === "activate-provider") await activateProvider(id);
    if (button.dataset.action === "edit-provider") {
      const provider = state.providers.find((item) => item.id === id);
      if (provider) fillProviderForm(provider);
    }
    if (button.dataset.action === "test-provider") await testProvider(id, button);
    if (button.dataset.action === "clear-provider-key") {
      if (!window.confirm("清除这个服务保存的本地密钥？环境变量中的密钥不受影响。")) return;
      try {
        await api(`/api/llm/providers/${encodeURIComponent(id)}/key`, { method: "DELETE" });
        toast("服务密钥已清除");
        await loadSettings();
        await refreshStatus();
      } catch (error) {
        toast(error.message, true);
      }
    }
    if (button.dataset.action === "delete-provider") await deleteProvider(id);
  });
  $("#refresh-status").addEventListener("click", () => refreshStatus().catch((error) => toast(error.message, true)));
  $("#sync-vault").addEventListener("click", async () => {
    const button = $("#sync-vault");
    setBusy(button, true, "索引中…");
    try {
      const result = await api("/api/vault/sync", { method: "POST" });
      toast(`Vault 已索引：${result.indexed} 个文件，更新 ${result.updated} 个`);
      await refreshStatus();
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  });
  $("#source-kind").addEventListener("change", updateSourceHint);
  $("#source-form").addEventListener("submit", addSource);
  $("#source-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const row = button.closest("[data-source]");
    const id = row.dataset.source;
    try {
      if (button.dataset.action === "toggle-source") {
        await api(`/api/sources/${id}`, {
          method: "PATCH",
          body: { enabled: button.dataset.enabled !== "1" },
        });
        toast("来源状态已更新");
      }
      if (button.dataset.action === "delete-source") {
        await api(`/api/sources/${id}`, { method: "DELETE" });
        toast("来源已删除");
      }
      await loadSettings();
      await refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#alias-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api(`/api/tags/${encodeURIComponent(form.slug.value.trim())}/aliases`, {
        method: "POST",
        body: { alias: form.alias.value.trim() },
      });
      form.reset();
      toast("别名已添加");
      await loadSettings();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

// Init -----------------------------------------------------------------------
function wireDialog() {
  const dialog = $("#note-dialog");
  $("#close-note").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

function init() {
  wireNavigation();
  wireProfile();
  wireInbox();
  wireNews();
  wireSearch();
  wireAsk();
  wireReviews();
  wireInterests();
  wireSettings();
  wireDialog();
  updateSourceHint();
  refreshStatus();
  showView(window.location.hash.slice(1) || "profile");
  setInterval(refreshStatus, 60000);
}

document.addEventListener("DOMContentLoaded", init);

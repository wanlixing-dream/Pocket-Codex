const state = {
  sessions: [], selected: null, runId: null, polling: null, clock: null,
  images: [], lastSubmission: null, creatingSession: false,
  newFolder: null, folderParent: null
};
const $ = (id) => document.getElementById(id);
const fragmentToken = new URLSearchParams(location.hash.slice(1)).get('token');
if (fragmentToken) {
  localStorage.setItem('remote_codex_token', fragmentToken);
  history.replaceState(null, '', `${location.pathname}${location.search}`);
}
const accessToken = localStorage.getItem('remote_codex_token') || '';

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Remote-Codex-Token': accessToken,
      ...(options.headers || {})
    }
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function loadSessions(showLoading = true) {
  if (showLoading) $('connection').textContent = '正在读取任务';
  try {
    const data = await api('/api/sessions');
    state.sessions = data.sessions;
    $('session-count').textContent = `${data.sessions.length} 个`;
    $('connection').textContent = '电脑在线';
    renderSessions();
    if (!state.selected && data.sessions.length) selectSession(data.sessions[0].id);
    else updateSelectedState();
  } catch (error) {
    $('connection').textContent = error.message === 'Unauthorized'
      ? '访问密钥无效，请从 ntfy 通知重新打开'
      : `连接失败：${error.message}`;
  }
}

function selectedSession() {
  return state.sessions.find((item) => item.id === state.selected) || null;
}

function isOwnRunActive(run) {
  return Boolean(run && ['queued', 'running', 'cancelling'].includes(run.status));
}

function isExternalDesktopActive(session) {
  return Boolean(session && session.desktop_activity === 'active' && !isOwnRunActive(session.run));
}

function desktopStatusType(session) {
  return session && session.desktop_status && session.desktop_status.type
    ? String(session.desktop_status.type)
    : 'unknown';
}

function desktopActivityDetail(session) {
  if (session && session.desktop_activity_source === 'session_log') return 'session 日志仍在更新';
  return `状态 ${desktopStatusType(session)}`;
}

function renderSessions() {
  $('sessions').innerHTML = state.sessions.map((session) => {
    const externalActive = isExternalDesktopActive(session);
    const classes = ['session', state.selected === session.id ? 'selected' : '', externalActive ? 'desktop-active' : '']
      .filter(Boolean)
      .join(' ');
    const statusLabel = externalActive ? '桌面运行中' : session.updated_label;
    return `
    <button class="${classes}" type="button"
      role="option" aria-selected="${state.selected === session.id}" data-id="${session.id}">
      <span class="session-meta"><span class="session-project">${escapeHtml(session.project)}</span><span>${escapeHtml(statusLabel)}</span></span>
      <div class="session-title">${escapeHtml(session.title)}</div>
      <div class="session-last">${escapeHtml(session.last_prompt)}</div>
    </button>`;
  }).join('') || '<p class="session-last">没有找到 Codex 任务</p>';
  document.querySelectorAll('.session').forEach((button) => {
    button.addEventListener('click', () => selectSession(button.dataset.id));
  });
}

function selectSession(id) {
  state.selected = id;
  const session = selectedSession();
  $('selected-project').textContent = session ? session.project : '未选择';
  updateSendButton();
  renderSessions();
  updateSelectedState();
}

function updateSendButton() {
  const session = selectedSession();
  const hasInput = Boolean($('prompt').value.trim()) || state.images.length > 0;
  const externalActive = isExternalDesktopActive(session);
  $('send').disabled = !state.selected || !hasInput || Boolean(state.runId) || externalActive;
  const running = Boolean(state.runId);
  $('send').hidden = running;
  $('cancel').hidden = !running;
  $('attach').disabled = running;
  $('voice').disabled = running;
}

function elapsedLabel(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  if (value < 60) return `${value} 秒`;
  return `${Math.floor(value / 60)} 分 ${value % 60} 秒`;
}

function updateSelectedState() {
  if (state.creatingSession && state.runId) {
    updateSendButton();
    return;
  }
  const session = selectedSession();
  if (!session) {
    setRunStatus('等待选择任务');
    return;
  }
  if (state.clock) clearInterval(state.clock);
  state.clock = null;
  const run = session.run;

  if (isOwnRunActive(run)) {
    state.runId = run.id;
    const paint = () => setRunStatus(`正在执行 · 已运行 ${elapsedLabel(Date.now() / 1000 - run.started_at)}`);
    paint();
    state.clock = setInterval(paint, 1000);
    if (!state.polling) pollRun();
  } else if (run && run.status === 'completed') {
    state.runId = null;
    const finished = new Date(run.finished_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    setRunStatus(`已完成于 ${finished} · 正在等待你的下一步指令`);
    if (run.output) {
      $('run-output').textContent = run.output;
      $('run-output').hidden = false;
    }
  } else if (run && run.status === 'failed') {
    state.runId = null;
    setRunStatus('上一轮执行失败 · 可以修改指令后重试', true);
  } else if (run && run.status === 'cancelled') {
    state.runId = null;
    restoreLastSubmission();
    setRunStatus('已停止 · 上一条内容已恢复，可以修改后重新发送');
  } else if (isExternalDesktopActive(session)) {
    state.runId = null;
    setRunStatus(`桌面端正在运行 · ${desktopActivityDetail(session)} · 最后同步 ${session.updated_label}`);
    $('run-output').hidden = true;
  } else {
    state.runId = null;
    setRunStatus(`当前空闲 · 最后更新 ${session.updated_label} · 等待你的指令`);
  }

  $('last-response').hidden = !session.last_response;
  $('last-response-text').textContent = session.last_response || '';
  $('full-response-text').textContent = session.last_response || '';
  updateSendButton();
}

function loadBrowserImage(file) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('无法读取这张图片'));
    };
    image.src = url;
  });
}

async function prepareImage(file) {
  const image = await loadBrowserImage(file);
  const maxSide = 2200;
  const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
  const outputType = file.type === 'image/png' ? 'image/png' : 'image/jpeg';
  let data = canvas.toDataURL(outputType, .88);
  if (data.length > 10_500_000) data = canvas.toDataURL('image/jpeg', .72);
  if (data.length > 10_500_000) throw new Error('图片压缩后仍超过 8 MB');
  return { name: file.name || 'photo.jpg', data };
}

function renderAttachments() {
  $('attachments').hidden = state.images.length === 0;
  $('attachments').innerHTML = state.images.map((image, index) => `
    <div class="attachment">
      <img src="${image.data}" alt="待上传图片 ${index + 1}">
      <button type="button" data-index="${index}" aria-label="删除图片">×</button>
    </div>`).join('');
  document.querySelectorAll('.attachment button').forEach((button) => {
    button.addEventListener('click', () => {
      state.images.splice(Number(button.dataset.index), 1);
      renderAttachments();
      updateSendButton();
    });
  });
}

async function addImages(files) {
  const remaining = 4 - state.images.length;
  if (remaining <= 0) return setRunStatus('每次最多上传 4 张图片', true);
  setRunStatus('正在处理图片');
  try {
    for (const file of Array.from(files).slice(0, remaining)) {
      state.images.push(await prepareImage(file));
    }
    renderAttachments();
    updateSendButton();
    setRunStatus(`已添加 ${state.images.length} 张图片 · 可以输入问题后发送`);
  } catch (error) {
    setRunStatus(error.message, true);
  } finally {
    $('image-picker').value = '';
  }
}

async function sendPrompt() {
  const prompt = $('prompt').value.trim();
  if (!state.selected || (!prompt && !state.images.length) || state.runId || isExternalDesktopActive(selectedSession())) return;
  setRunStatus(state.images.length ? '正在上传图片并启动 Codex' : '已发送，Codex 正在执行');
  $('send').disabled = true;
  $('run-output').hidden = true;
  try {
    state.lastSubmission = { prompt, images: state.images.map((image) => ({ ...image })) };
    const run = await api('/api/runs', {
      method: 'POST',
      body: JSON.stringify({
        session_id: state.selected,
        prompt,
        images: state.images.map(({ name, data }) => ({ name, data }))
      })
    });
    state.runId = run.id;
    $('prompt').value = '';
    state.images = [];
    renderAttachments();
    pollRun();
  } catch (error) {
    state.runId = null;
    setRunStatus(error.message, true);
    $('send').disabled = false;
  }
}

function restoreLastSubmission() {
  if (!state.lastSubmission) return;
  if (!$('prompt').value.trim()) $('prompt').value = state.lastSubmission.prompt;
  if (!state.images.length) state.images = state.lastSubmission.images.map((image) => ({ ...image }));
  renderAttachments();
  updateSendButton();
}

async function cancelRun() {
  if (!state.runId) return;
  $('cancel').disabled = true;
  setRunStatus('正在停止 Codex');
  try {
    await api(`/api/runs/${state.runId}/cancel`, { method: 'POST', body: '{}' });
    setRunStatus('停止请求已发送');
  } catch (error) {
    setRunStatus(error.message, true);
  } finally {
    $('cancel').disabled = false;
  }
}

async function pollRun() {
  if (!state.runId) return;
  try {
    const run = await api(`/api/runs/${state.runId}`);
    if (run.status === 'queued' || run.status === 'running') {
      setRunStatus('Codex 正在执行，完成后手机和手表会收到通知');
      state.polling = setTimeout(() => {
        state.polling = null;
        pollRun();
      }, 2500);
      return;
    }
    const ok = run.status === 'completed';
    const cancelled = run.status === 'cancelled';
    const createdSessionId = state.creatingSession && ok ? run.session_id : null;
    setRunStatus(cancelled ? '已停止 · 可以修改后重新发送' : (ok ? '本轮已完成' : '执行失败，请查看输出'), !ok && !cancelled);
    if (run.output) {
      $('run-output').textContent = run.output;
      $('run-output').hidden = false;
    }
    state.runId = null;
    state.polling = null;
    if (cancelled) restoreLastSubmission();
    await loadSessions();
    if (createdSessionId && state.sessions.some((session) => session.id === createdSessionId)) {
      selectSession(createdSessionId);
    }
    state.creatingSession = false;
    updateSendButton();
  } catch (error) {
    setRunStatus(error.message, true);
    state.runId = null;
    state.polling = null;
  }
}

function setRunStatus(message, isError = false) {
  $('run-status').textContent = message;
  $('run-status').hidden = false;
  $('run-status').classList.toggle('error', isError);
}

function startVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $('prompt').focus();
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = 'zh-CN';
  recognition.interimResults = true;
  recognition.continuous = false;
  const original = $('prompt').value;
  $('voice').textContent = '■';
  recognition.onresult = (event) => {
    const transcript = Array.from(event.results).map((result) => result[0].transcript).join('');
    $('prompt').value = `${original}${original ? '\n' : ''}${transcript}`;
    $('prompt').dispatchEvent(new Event('input'));
  };
  recognition.onend = () => { $('voice').textContent = '🎙'; };
  recognition.onerror = () => { $('voice').textContent = '🎙'; $('prompt').focus(); };
  recognition.start();
}

function openFullResponse() {
  $('response-overlay').hidden = false;
  document.body.style.overflow = 'hidden';
  $('full-response-text').scrollTop = 0;
}

function closeFullResponse() {
  $('response-overlay').hidden = true;
  document.body.style.overflow = '';
}

async function loadFolders(path = '') {
  $('folder-list').innerHTML = '<div class="session-last">正在读取文件夹</div>';
  try {
    const data = await api(`/api/folders?path=${encodeURIComponent(path)}`);
    state.newFolder = data.current;
    state.folderParent = data.parent;
    $('folder-current').textContent = data.current || '选择根目录';
    $('selected-folder').textContent = data.current || '尚未选择';
    $('folder-up').disabled = data.current === null;
    $('folder-list').innerHTML = data.folders.map((folder) => `
      <button class="folder-row" type="button" data-path="${escapeHtml(folder.path)}">
        <span class="folder-icon">▣</span>
        <span class="folder-name">${escapeHtml(folder.name)}</span>
        <span class="folder-arrow">›</span>
      </button>`).join('') || '<div class="session-last">这个文件夹没有子文件夹</div>';
    document.querySelectorAll('.folder-row').forEach((button) => {
      button.addEventListener('click', () => loadFolders(button.dataset.path));
    });
    updateCreateButton();
  } catch (error) {
    $('folder-list').innerHTML = `<div class="session-last">${escapeHtml(error.message)}</div>`;
  }
}

function updateCreateButton() {
  $('create-session').disabled = !state.newFolder || !$('new-session-prompt').value.trim() || Boolean(state.runId);
}

function openNewSession() {
  if (state.runId || isExternalDesktopActive(selectedSession())) return setRunStatus('请先等待当前任务结束，再新建或继续任务', true);
  $('new-session-overlay').hidden = false;
  document.body.style.overflow = 'hidden';
  $('new-session-prompt').value = '';
  loadFolders('');
}

function closeNewSession() {
  $('new-session-overlay').hidden = true;
  document.body.style.overflow = '';
}

async function createSession() {
  const prompt = $('new-session-prompt').value.trim();
  if (!state.newFolder || !prompt || state.runId || isExternalDesktopActive(selectedSession())) return;
  $('create-session').disabled = true;
  try {
    const run = await api('/api/sessions/new', {
      method: 'POST',
      body: JSON.stringify({ cwd: state.newFolder, prompt })
    });
    state.lastSubmission = { prompt, images: [] };
    state.creatingSession = true;
    state.runId = run.id;
    closeNewSession();
    setRunStatus(`正在 ${run.project} 中创建新任务`);
    updateSendButton();
    pollRun();
  } catch (error) {
    setRunStatus(error.message, true);
    $('create-session').disabled = false;
  }
}

$('refresh').addEventListener('click', loadSessions);
$('new-session').addEventListener('click', openNewSession);
$('close-new-session').addEventListener('click', closeNewSession);
$('folder-up').addEventListener('click', () => loadFolders(state.folderParent || ''));
$('new-session-prompt').addEventListener('input', updateCreateButton);
$('create-session').addEventListener('click', createSession);
$('send').addEventListener('click', sendPrompt);
$('cancel').addEventListener('click', cancelRun);
$('voice').addEventListener('click', startVoice);
$('open-response').addEventListener('click', openFullResponse);
$('close-response').addEventListener('click', closeFullResponse);
$('attach').addEventListener('click', () => $('image-picker').click());
$('image-picker').addEventListener('change', (event) => addImages(event.target.files));
$('prompt').addEventListener('input', () => {
  updateSendButton();
});
loadSessions();
setInterval(() => loadSessions(false), 5000);

// ==================== 重複ファイルの判定（純粋関数） ====================
// 「同じファイル」= 名前・サイズ・更新日時がすべて同じもの。
// 名前だけ同じで中身が違うものは、アップロードで上書きされ結果が取り違えになるため nameConflicts に分ける。
// <dedupeFiles>
function dedupeFiles(selected, incoming) {
  // Windows のファイル名は大文字小文字を区別しない（Memo.wav と memo.wav は同じ保存先になる）ので小文字化して比べる
  const key = f => f.name.toLowerCase();
  const sameFile = (a, b) =>
    key(a) === key(b) && a.size === b.size && a.lastModified === b.lastModified;
  const known = [...selected];
  const accepted = [];
  const duplicates = [];
  const nameConflicts = [];
  for (const file of incoming) {
    if (known.some(k => sameFile(k, file))) {
      duplicates.push(file);
    } else if (known.some(k => key(k) === key(file))) {
      nameConflicts.push(file);
    } else {
      accepted.push(file);
      known.push(file);
    }
  }
  return { accepted, duplicates, nameConflicts };
}
// </dedupeFiles>

// Markdownの表。判定規則は src_backends/output_formats.py の _parse_table と同じにする
// （見出し行 + 区切り行で始まり、| を含まない行・見出し・リスト・引用・コードフェンスで終わる）。
// lines[start] から表が始まれば { html, next }（next は表の次の行の位置）、表でなければ null を返す
// <markdownTable>
function markdownTable(lines, start) {
  const PIPE = /(?<!\\)\|/;
  const OTHER_BLOCK = [
    /^\s*(\x60{3}|~~~)/, /^\s{0,3}#{1,6}\s+/, /^\s*[-*+\u30fb]\s+/, /^\s*\p{Nd}+[.)]\s+/u, /^\s*>/,
  ];
  const lineAt = (i) => (i < lines.length ? lines[i].replace(/^\ufeff+/, '').trimEnd() : '');
  const isRow = (line) => PIPE.test(line) && !OTHER_BLOCK.some((r) => r.test(line));
  const splitCells = (line) => {
    let s = line.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
    return s.split(PIPE).map((c) => c.trim().replace(/\\\|/g, '|'));
  };
  const isSeparator = (line) => PIPE.test(line) && splitCells(line).every((c) => /^:?-+:?$/.test(c));
  // セルの文字はエスケープしてから、太字・斜体・改行だけを戻す
  const cellHtml = (text) => text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(?!\s)([^*]+?)(?<!\s)\*/g, '<em>$1</em>')
    .replace(/&lt;br\s*\/?&gt;/gi, '<br>');

  const head = lineAt(start);
  const sep = lineAt(start + 1);
  if (!isRow(head) || isSeparator(head) || !isSeparator(sep)) return null;

  const header = splitCells(head);
  const aligns = splitCells(sep).map((c) => {
    if (!c.endsWith(':')) return '';
    return c.startsWith(':') ? 'md-center' : 'md-right';
  });
  const rows = [];
  let next = start + 2;
  while (next < lines.length && isRow(lineAt(next))) {
    const cells = splitCells(lineAt(next));
    // 本文に繰り返された区切り行は捨てる（「-」だけのセルは「該当なし」の意味で使われるため残す）
    if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
    next += 1;
  }

  const columns = Math.max(header.length, ...rows.map((r) => r.length));
  const rowHtml = (cells, tag) => {
    let out = '<tr>';
    for (let c = 0; c < columns; c += 1) {
      const cls = aligns[c] ? ` class="${aligns[c]}"` : '';
      out += `<${tag}${cls}>${cellHtml(cells[c] || '')}</${tag}>`;
    }
    return `${out}</tr>`;
  };
  const body = rows.length ? `<tbody>${rows.map((r) => rowHtml(r, 'td')).join('')}</tbody>` : '';
  return {
    html: `<div class="md-table"><table><thead>${rowHtml(header, 'th')}</thead>${body}</table></div>`,
    next,
  };
}
// </markdownTable>

document.addEventListener('DOMContentLoaded', () => {
  // 画面を配信したサーバーと同じ場所へ接続する（既定のポートが使用中で別ポートへ移った場合にも動く）
  const API_BASE = '';
  let apiConnected = false;

  // ==================== API接続リトライ ====================
  async function apiRequest(url, options = {}, retries = 5, delay = 2000) {
    let busyWaits = 0;
    for (let i = 0; i < retries; i++) {
      try {
        const response = await fetch(`${API_BASE}${url}`, options);
        if (response.ok) {
          apiConnected = true;
          return response;
        }
        if (response.status === 409 && busyWaits < 30) {
          // 直前の処理の後始末中（中止直後など）。試行回数を消費せず、終わるまで待って再試行する
          busyWaits++;
          i--;
          await new Promise(r => setTimeout(r, delay));
          if (options.signal && options.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        }
      } catch (e) {
        if (e.name === 'AbortError') throw e;  // 「処理を中止」による打ち切りは再試行しない
        console.warn(`API接続リトライ ${i + 1}/${retries}: ${e.message}`);
        if (i < retries - 1) {
          await new Promise(r => setTimeout(r, delay));
        }
      }
    }
    apiConnected = false;
    throw new Error(t('app.m001'));
  }

  // vibe-server.exeが見つからずPython版whisperへフォールバック中の警告ダイアログを、
  // アプリ起動後1回だけ表示するためのフラグ
  let vibeFallbackDialogShown = false;

  function maybeShowVibeFallbackDialog(st) {
    // 文字起こしモデルのことで Python 版になった場合（vibe_model_notice あり）は、Vibe の導入案内ダイアログは出さない
    if (!st.vibe_fallback_active || st.vibe_model_notice || vibeFallbackDialogShown) return;
    vibeFallbackDialogShown = true;
    const modal = document.getElementById('vibe-fallback-modal');
    if (modal) modal.style.display = 'flex';
  }

  document.getElementById('btn-vibe-fallback-ok').addEventListener('click', () => {
    document.getElementById('vibe-fallback-modal').style.display = 'none';
  });

  // 「再確認」: Vibe を入れたあとに、アプリを再起動せず vibe-server を使える状態にする（POST /vibe/activate）。
  // 成功したら警告ダイアログとバナーを閉じる
  document.getElementById('btn-vibe-recheck').addEventListener('click', async (e) => {
    const button = e.currentTarget;
    const statusEl = document.getElementById('vibe-recheck-status');
    button.disabled = true;
    statusEl.textContent = t('app.vibe_rechecking');
    try {
      const response = await apiRequest('/vibe/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      }, 1, 0);
      const result = await response.json();
      if (result.status === 'ok') {
        document.getElementById('vibe-fallback-modal').style.display = 'none';
        document.getElementById('status-banner').style.display = 'none';
        statusEl.textContent = '';
        showSuccess(t('app.vibe_recheck_ok'));
      } else if (result.status === 'not_found') {
        statusEl.textContent = t('app.vibe_recheck_not_found');
      } else if (result.status === 'model_downloading' || result.status === 'starting') {
        statusEl.textContent = t('app.vibe_recheck_downloading');
      } else {
        statusEl.textContent = t('app.vibe_recheck_failed');
      }
    } catch (err) {
      statusEl.textContent = t('app.vibe_recheck_failed');
    } finally {
      button.disabled = false;
    }
  });

  // ==================== 処理進捗表示（ファイル処理/テキスト入力/リトライ共通） ====================
  // /status のレスポンス(st)を受け取り、ステップ名・推定時間・LLMストリーミング・音声長の
  // 表示を更新する。3つの処理メニュー(ファイルアップロード/テキスト入力/リトライ)で
  // 表示のされ方が異なっていたため、共通関数に統一している。
  function updateProgressUI(st, els) {
    maybeShowVibeFallbackDialog(st);
    const stepName = st.current_step_name || '';
    if (stepName) {
      els.progressStatus.textContent = stepName;
    }
    if (st.current_output) {
      els.streamingOutput.style.display = 'block';
      els.streamingText.textContent = st.current_output;
      els.streamingText.scrollTop = els.streamingText.scrollHeight;
    } else {
      els.streamingOutput.style.display = 'none';
      els.streamingText.textContent = '';
    }
    if (st.estimated_remaining) {
      els.progressDetail.textContent = st.estimated_remaining;
      els.progressDetail.style.display = 'block';
    } else {
      els.progressDetail.style.display = 'none';
    }
    if (st.audio_duration && st.audio_duration > 0) {
      const dur = st.audio_duration;
      const min = Math.floor(dur / 60);
      const sec = Math.floor(dur % 60);
      els.audioDuration.textContent = t('app.m002', min, sec);
      els.audioDuration.style.display = 'block';
    } else {
      els.audioDuration.style.display = 'none';
    }
  }

  // ==================== バージョン表示更新 ====================
  function updateVersionDisplay(appVersion) {
    if (!appVersion) return;
    const versionEl = document.querySelector('.app-header .version');
    if (versionEl) {
      versionEl.textContent = `v${appVersion}`;
    }
    const aboutVersionEl = document.getElementById('about-version');
    if (aboutVersionEl) aboutVersionEl.textContent = appVersion;
  }

  // 外部リンク（data-external）はアプリの中で開かず、既定のブラウザで開く（バックエンドが https の決まったサイトだけ開く）
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[data-external]');
    if (!link) return;
    e.preventDefault();
    apiRequest('/open-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: link.href }),
    }, 1, 0).catch(() => {});
  });

  // ==================== 画面が表示できたことの合図 ====================
  // /health が初めて成功した時点で1回だけ送る。バックエンドの見張り役は、これが来ないと
  // 「白い画面のまま」と見なして再読み込み・安全モードでの作り直しを行う
  let uiReadySent = false;
  function postUiReady(instanceId, attemptsLeft) {
    fetch(`${API_BASE}/ui-ready`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instance_id: instanceId }),
    }).then((resp) => { if (!resp.ok) throw new Error(String(resp.status)); })
      .catch(() => { if (attemptsLeft > 0) setTimeout(() => postUiReady(instanceId, attemptsLeft - 1), 1000); });
  }
  function signalUiReady(data) {
    window.__v2dBooted = true;
    const early = document.querySelector('[data-early-error]');  // 起動前の一時的なエラーで出た案内を、起動できたら消す
    if (early) early.remove();
    if (uiReadySent || !data || !data.instance_id) return;
    uiReadySent = true;
    postUiReady(data.instance_id, 5);  // 一時的な失敗でも合図が落ちないよう、数回やり直す
  }

  // 描画の安全モード（GPU無効）の注記と解除ボタン
  function updateSafeRenderNotice(on) {
    const group = document.getElementById('safe-render-group');
    if (group) group.style.display = on ? '' : 'none';
  }
  const btnSafeRenderReset = document.getElementById('btn-safe-render-reset');
  if (btnSafeRenderReset) {
    btnSafeRenderReset.addEventListener('click', async () => {
      try {
        const resp = await fetch(`${API_BASE}/safe-render/reset`, { method: 'POST' });
        if (!resp.ok) throw new Error(String(resp.status));
        updateSafeRenderNotice(false);
        alert(t('app.safe_render_reset_done'));
      } catch (e) {
        alert(t('app.safe_render_reset_failed'));
      }
    });
  }

  // ==================== 起動時の接続待機 ====================
  async function waitForApi(maxWait = 960000) {
    const overlay = document.getElementById('startup-overlay');
    const detail = document.getElementById('startup-detail');
    const startTime = Date.now();
    while (Date.now() - startTime < maxWait) {
      try {
        const response = await fetch(`${API_BASE}/health`);
        if (response.ok) {
          const data = await response.json();
          updateVersionDisplay(data.app_version);
          updateSafeRenderNotice(!!data.safe_render);
          signalUiReady(data);
          if (data.startup_complete) {
            apiConnected = true;
            console.log('API接続成功 & 起動完了');
            if (overlay) {
              detail.textContent = t('app.m003');
              setTimeout(() => overlay.classList.add('hidden'), 300);
            }
            return true;
          } else {
            if (detail && data.startup_message) {
              detail.textContent = data.startup_message;
            }
          }
        }
      } catch (e) {
        if (detail) {
          const elapsed = Math.floor((Date.now() - startTime) / 1000);
          detail.textContent = t('app.m004', elapsed);
        }
        console.log('API起動中...');
      }
      await new Promise(r => setTimeout(r, 1000));
    }
    if (detail) detail.textContent = t('app.m005');
    // オーバーレイに張り付いたままにしない（アプリが操作不能になるのを防ぐ）
    if (overlay) overlay.classList.add('hidden');
    return false;
  }

  // ==================== ヘッダーの状態表示 ====================
  // 処理の開始・終了は複数の経路(ファイル/テキスト/リトライ)から切り替わるため、isProcessing を定期的に反映する
  const appStatus = document.getElementById('app-status');
  setInterval(() => {
    const busy = isProcessing;
    appStatus.textContent = busy ? t('app.m006') : t('app.m007');
    appStatus.classList.toggle('busy', busy);
  }, 500);

  // ==================== 処理の中止（ファイル/テキスト/リトライの3経路で共通） ====================
  let currentRun = null;  // 実行中の処理 { cancelled, controller, processSent, cleanup }
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  function beginRun(cleanup) {
    const run = { cancelled: false, processSent: false, controller: new AbortController(), cleanup };
    currentRun = run;
    document.getElementById('btn-cancel-process').disabled = false;
    return run;
  }

  // 完了・エラーで終わるとき。完了表示の間は「処理を中止」を押せなくする（もう取り消せないため）
  function endRun(run) {
    if (currentRun === run) currentRun = null;
    document.getElementById('btn-cancel-process').disabled = true;
  }

  // サーバーが「中止された」と返したとき（完了ではなく中止として画面を戻す）
  function finishCancelled(run) {
    run.cancelled = true;
    endRun(run);
    run.cleanup();
    showSuccess(t('app.m008'));
  }

  // 「処理を中止」ボタン・Escキー・処理中の画面遷移から呼ばれる。画面は即座に開始画面へ戻し、
  // サーバー側の打ち切りと完了の確認は裏で行う
  async function cancelCurrentRun() {
    const run = currentRun;
    if (!run || run.cancelled) return false;
    run.cancelled = true;
    currentRun = null;
    run.controller.abort();  // 送信中のアップロード・/process の待機を打ち切る
    document.getElementById('btn-cancel-process').disabled = true;
    run.cleanup();
    showWarning(t('app.m009'));
    confirmCancelOnServer(run);
    return true;
  }

  async function confirmCancelOnServer(run) {
    // /process が始まった直後は、サーバーがまだ処理中になっていないことがあるため数回リトライする
    for (let i = 0; i < 10; i++) {
      if (currentRun) return;  // 既に次の処理を始めている。その処理を巻き込まない
      let cancelling = false;
      try {
        const response = await fetch(`${API_BASE}/process/cancel`, { method: 'POST' });
        cancelling = !!(await response.json()).cancelling;
      } catch (e) {
        // 接続できなければ次の確認へ
      }
      if (cancelling || !run.processSent) break;
      await sleep(300);
    }
    // 音声認識の打ち切り（vibe-serverの停止）まで終わるのを待ってから完了を知らせる
    for (let i = 0; i < 40; i++) {
      if (currentRun) return;
      try {
        const st = await (await fetch(`${API_BASE}/status`)).json();
        if (!st.is_processing) break;
      } catch (e) {
        break;
      }
      await sleep(500);
    }
    showSuccess(t('app.m008'));
  }

  // 処理中ダイアログの「処理を中止」ボタン
  document.getElementById('btn-cancel-process').addEventListener('click', () => cancelCurrentRun());

  // ==================== メニュー操作 ====================
  const menuItems = document.querySelectorAll('.menu-item');
  const pages = document.querySelectorAll('.page');

  let pendingNavItem = null;

  function navigateToMenu(item, targetPage) {
    menuItems.forEach(mi => mi.classList.remove('active'));
    item.classList.add('active');
    pages.forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${targetPage}`).classList.add('active');

    if (targetPage === 'history') loadHistory();
    if (targetPage === 'settings') loadSettings();
    if (targetPage === 'file-input') {
      // メニュー選択時は常にプロンプト選択カード一覧に戻す
      showPromptSelector();
      refreshCurrentModels();
    }
  }

  menuItems.forEach(item => {
    item.addEventListener('click', async () => {
      const targetPage = item.dataset.page;
      if (isProcessing) {
        pendingNavItem = item;
        document.getElementById('processing-nav-confirm-modal').style.display = 'flex';
        return;
      }
      const onSettings = document.getElementById('page-settings').classList.contains('active');
      if (onSettings && targetPage === 'settings') return;
      if (onSettings && hasDirty()) {
        if (!(await askDiscardSettings())) return;
        clearAllDirty();
      }
      navigateToMenu(item, targetPage);
    });
  });

  document.getElementById('btn-processing-nav-continue').addEventListener('click', () => {
    document.getElementById('processing-nav-confirm-modal').style.display = 'none';
    pendingNavItem = null;
  });

  document.getElementById('btn-processing-nav-abort').addEventListener('click', async () => {
    document.getElementById('processing-nav-confirm-modal').style.display = 'none';
    // 中止の要求が失敗しても画面遷移は続行する（ユーザーの意思を優先）
    await cancelCurrentRun();
    if (pendingNavItem) {
      const target = pendingNavItem.dataset.page;
      navigateToMenu(pendingNavItem, target);
      pendingNavItem = null;
    }
  });

  // ==================== タブ操作 ====================
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTab = btn.dataset.tab;
      tabBtns.forEach(tb => tb.classList.remove('active'));
      btn.classList.add('active');
      tabContents.forEach(tc => tc.classList.remove('active'));
      document.getElementById(`tab-${targetTab}`).classList.add('active');
      updateSettingsFooter();
    });
  });

  // ==================== 設定: 未保存の管理と共通フッター ====================
  // 保存・リセット対象のタブ。環境設定タブ（ダーク/ダウンロード形式）は変更のたびに即時反映するためフッターを隠す
  const SAVABLE_TABS = ['prompt-settings', 'model-settings'];
  const dirtyTabs = { 'prompt-settings': false, 'model-settings': false };

  function currentSettingsTab() {
    const active = document.querySelector('.tab-btn.active');
    return active ? active.dataset.tab : null;
  }

  function setDirty(tab, dirty) {
    if (!(tab in dirtyTabs)) return;
    dirtyTabs[tab] = dirty;
    const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
    if (btn) btn.classList.toggle('dirty', dirty);
    updateSettingsFooter();
  }

  function clearAllDirty() {
    SAVABLE_TABS.forEach(tab => setDirty(tab, false));
  }

  function hasDirty() {
    return SAVABLE_TABS.some(tab => dirtyTabs[tab]);
  }

  function updateSettingsFooter() {
    const footer = document.getElementById('settings-footer');
    if (!footer) return;
    const tab = currentSettingsTab();
    footer.style.display = SAVABLE_TABS.includes(tab) ? 'flex' : 'none';
    const note = document.getElementById('settings-footer-note');
    if (note) note.textContent = hasDirty() ? t('app.m010') : '';
  }

  // 入力・選択の変更を検知して、そのタブを未保存にする（プログラムによる値の設定では発火しない）
  SAVABLE_TABS.forEach(tab => {
    const area = document.getElementById(`tab-${tab}`);
    ['input', 'change'].forEach(type => area.addEventListener(type, () => setDirty(tab, true)));
  });

  // 未保存のまま設定画面から離れる/別のプロンプトを開くときの確認（Promise で結果を返す）
  function askDiscardSettings() {
    return new Promise(resolve => {
      const modal = document.getElementById('settings-leave-modal');
      if (modal.style.display === 'flex') {
        resolve(false);  // 既に確認中なら二重に開かない
        return;
      }
      const cancel = document.getElementById('btn-leave-cancel');
      const discard = document.getElementById('btn-leave-discard');
      const finish = (result) => {
        modal.style.display = 'none';
        cancel.removeEventListener('click', onCancel);
        discard.removeEventListener('click', onDiscard);
        resolve(result);
      };
      const onCancel = () => finish(false);
      const onDiscard = () => finish(true);
      cancel.addEventListener('click', onCancel);
      discard.addEventListener('click', onDiscard);
      modal.style.display = 'flex';
    });
  }

  document.getElementById('btn-footer-save').addEventListener('click', async () => {
    const tab = currentSettingsTab();
    if (tab === 'prompt-settings') await savePromptSettings();
    else if (tab === 'model-settings') await saveModelSettingsFromForm();
  });

  document.getElementById('btn-footer-reset').addEventListener('click', () => {
    const tab = currentSettingsTab();
    if (tab === 'prompt-settings') confirmReset('prompt');
    else if (tab === 'model-settings') confirmReset('model');
  });

  // ==================== プロンプト選択 (メイン画面) ====================
  let savedPrompts = {};
  let activePromptName = '';
  let selectedPromptName = null;
  let builtinPromptNames = [];  // 削除できない初期プロンプトの名前（GET /config の builtin_prompt_names）

  const promptSelector = document.getElementById('prompt-selector');
  const fileInputView = document.getElementById('file-input-view');
  const promptCardList = document.getElementById('prompt-card-list');
  const selectedPromptNameEl = document.getElementById('selected-prompt-name');
  const fileInputDescription = document.getElementById('file-input-description');

  // ダッシュボード（工程カード・一覧）は開始画面（プロンプト選択）でのみ表示する
  function setDashboardVisible(visible) {
    ['dashboard-top', 'dashboard-lists'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = visible ? 'block' : 'none';
    });
  }

  function showPromptSelector() {
    promptSelector.style.display = 'block';
    fileInputView.style.display = 'none';
    setDashboardVisible(true);
    refreshDashboard();
    fileInputDescription.textContent = t('app.m011');
    selectedPromptName = null;
    renderPromptCards();
    // 処理結果・プログレス表示をリセット
    const resultEl = document.getElementById('result-container');
    if (resultEl) resultEl.style.display = 'none';
    const progressEl = document.getElementById('progress-container');
    if (progressEl) progressEl.style.display = 'none';
  }

  function showFileInputView(promptName) {
    selectedPromptName = promptName;
    promptSelector.style.display = 'none';
    setDashboardVisible(false);
    fileInputView.style.display = 'block';
    selectedPromptNameEl.textContent = promptName;
    fileInputDescription.textContent = t('app.m012');
    // ファイルタブをデフォルト表示
    inputModeTabs.forEach(t => t.classList.remove('active'));
    document.querySelector('[data-mode="file"]').classList.add('active');
    inputModeContents.forEach(c => {
      c.classList.remove('active');
      c.style.display = 'none';
    });
    const fileMode = document.getElementById('mode-file');
    if (fileMode) {
      fileMode.classList.add('active');
      fileMode.style.display = 'block';
    }
  }

  // ==================== 開始画面: 工程カード・一覧 ====================
  // 新しいバックエンドAPIは使わず、処理結果(/processの戻り値)・/history・/statusから組み立てる
  const sessionErrors = [];   // 今回の起動中に失敗したファイル

  function recordRunForDashboard(results) {
    results.filter(r => r.status === 'error').forEach(r => {
      sessionErrors.unshift({ file: r.file, message: r.message || t('app.m013'), time: new Date() });
    });
    sessionErrors.splice(50);
  }

  function renderRecentResults(items) {
    const box = document.getElementById('recent-results-list');
    box.innerHTML = '';
    if (!items.length) {
      box.innerHTML = t('app.m014');
      return;
    }
    items.forEach(item => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'list-row';
      const date = new Date(item.created * 1000);
      row.innerHTML = `<span class="list-main">${escapeHtml(item.name)}</span>` +
        `<span class="list-meta">${date.toLocaleDateString(currentLocale())} ${date.toLocaleTimeString(currentLocale())}</span>`;
      row.addEventListener('click', () => showHistoryModal(item));
      box.appendChild(row);
    });
  }

  function renderErrorList() {
    const box = document.getElementById('recent-errors-list');
    box.innerHTML = '';
    if (!sessionErrors.length) {
      box.innerHTML = t('app.m014');
      return;
    }
    sessionErrors.forEach(err => {
      const row = document.createElement('div');
      row.className = 'list-row error';
      row.innerHTML = `<span class="list-main" title="${escapeHtml(err.message)}">${escapeHtml(err.file)}: ${escapeHtml(err.message)}</span>` +
        `<span class="list-meta">${err.time.toLocaleTimeString(currentLocale())}</span>`;
      box.appendChild(row);
    });
  }

  async function refreshDashboard() {
    renderErrorList();
    try {
      const [historyRes, statusRes] = await Promise.all([
        apiRequest('/history', {}, 1, 0),
        apiRequest('/status', {}, 1, 0),
      ]);
      const history = await historyRes.json();
      const status = await statusRes.json();
      renderRecentResults((history.items || []).slice(0, 5));
      const banner = document.getElementById('status-banner');
      if (status.vibe_model_downloading) {
        banner.textContent = t('app.vibe_dl_progress', status.vibe_model_downloading, status.vibe_model_progress || 0);
        banner.style.display = 'block';
      } else if (status.vibe_model_notice) {
        // 設定の文字起こしモデルを使えなかった案内（サーバー側が日英で返す）
        banner.textContent = status.vibe_model_notice;
        banner.style.display = 'block';
      } else if (status.vibe_fallback_active) {
        banner.textContent = t('app.m015');
        banner.style.display = 'block';
      } else {
        banner.style.display = 'none';
      }
    } catch (e) {
      // 接続できない場合は前回の表示のままにする（起動直後などで一時的に失敗しうる）
    }
  }

  // 「このプロンプトを使わない」がチェックされたプロンプトか
  function isPromptHidden(data) {
    return typeof data === 'object' && data !== null && data.hidden === true;
  }

  function renderPromptCards() {
    promptCardList.innerHTML = '';
    const allNames = Object.keys(savedPrompts);
    if (allNames.length === 0) {
      promptCardList.innerHTML = t('app.m016');
      return;
    }
    const promptNames = allNames.filter(name => !isPromptHidden(savedPrompts[name]));
    if (promptNames.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'empty-prompts';
      empty.textContent = t('app.no_visible_prompts');
      promptCardList.appendChild(empty);
      return;
    }
    promptNames.forEach(name => {
      const data = savedPrompts[name];
      const description = (typeof data === 'object' && data !== null) ? (data.description || '') : '';
      const card = document.createElement('div');
      card.className = 'prompt-card' + (name === activePromptName ? ' active' : '');
      card.innerHTML = `
        <div class="prompt-card-name">${escapeHtml(name)}</div>
        ${description ? `<div class="prompt-card-description">${escapeHtml(description)}</div>` : ''}
      `;
      card.addEventListener('click', () => selectPromptCard(name));
      // カードへ直接ファイルをドロップ: そのプロンプトを選び、ファイル入力ビューの「選択済みファイル」に加える
      card.addEventListener('dragover', (e) => {
        if (isProcessing || !isFileDrag(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        card.classList.add('dragover');
      });
      card.addEventListener('dragleave', (e) => {
        if (!card.contains(e.relatedTarget)) card.classList.remove('dragover');
      });
      card.addEventListener('drop', (e) => {
        card.classList.remove('dragover');
        if (isProcessing || !isFileDrag(e)) return;
        e.preventDefault();
        const files = Array.from(e.dataTransfer.files);
        selectPromptCard(name);
        if (files.length) addFiles(files);
      });
      promptCardList.appendChild(card);
    });
  }

  // ドラッグ中のものがファイルか（文字列・リンクのドラッグではカードを反応させない）
  function isFileDrag(e) {
    return !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files'));
  }

  // プロンプトカードを選んでファイル入力ビューへ進む（クリック・ファイルのドロップ共通）
  function selectPromptCard(name) {
    activePromptName = name;
    showFileInputView(name);
    // バックエンドにアクティブプロンプトを保存
    apiRequest('/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_prompt_name: name }),
    }, 2, 500).catch(() => {});
  }

  // プロンプト変更ボタン
  document.getElementById('btn-change-prompt').addEventListener('click', () => {
    showPromptSelector();
  });

  // ==================== ドラッグ&ドロップ ====================
  document.addEventListener('dragover', (e) => {
    e.preventDefault();
  });
  document.addEventListener('drop', (e) => {
    e.preventDefault();
  });

  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const btnSelectFiles = document.getElementById('btn-select-files');
  const fileListContainer = document.getElementById('file-list-container');
  const fileList = document.getElementById('file-list');
  const fileCount = document.getElementById('file-count');
  const btnClearAll = document.getElementById('btn-clear-all');
  const btnStartProcess = document.getElementById('btn-start-process');

  let selectedFiles = [];
  let isProcessing = false;
  let lastFailedFiles = [];
  let historyItems = [];   // 現在の表示候補（検索なし=全件、検索あり=サーバー検索結果）
  let allHistoryItems = []; // 検索なしの全件（日付フィルタのみの再描画時に使う）
  const MAX_FILES = 10;
  const MAX_SIZE_BYTES = 1024 * 1024 * 1024;
  const ALLOWED_EXTENSIONS = ['.wav', '.mp3', '.aac', '.m4a', '.ogg', '.flac', '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.txt', '.md'];

  dropZone.addEventListener('click', () => fileInput.click());
  btnSelectFiles.addEventListener('click', (e) => {
    e.stopPropagation();
    fileInput.click();
  });

  fileInput.addEventListener('change', (e) => {
    addFiles(Array.from(e.target.files));
    fileInput.value = '';
  });

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });

  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
  });

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    addFiles(Array.from(e.dataTransfer.files));
  });

  // ==================== タブ切替 (ファイル/テキスト) ====================
  const inputModeTabs = document.querySelectorAll('.input-mode-tab');
  const inputModeContents = document.querySelectorAll('.input-mode-content');
  const textInputArea = document.getElementById('text-input-area');
  const btnSendText = document.getElementById('btn-send-text');

  inputModeTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const mode = tab.dataset.mode;
      inputModeTabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      inputModeContents.forEach(c => {
        c.classList.remove('active');
        c.style.display = 'none';
      });
      const target = document.getElementById(`mode-${mode}`);
      if (target) {
        target.classList.add('active');
        target.style.display = 'block';
      }
    });
  });

  // テキスト送信
  btnSendText.addEventListener('click', async () => {
    const text = textInputArea.value.trim();
    if (!text) {
      showWarning(t('app.m017'));
      return;
    }
    await startTextProcessing(text);
  });

  async function startTextProcessing(text) {
    isProcessing = true;
    try {
      const statusResp = await fetch(`${API_BASE}/status`);
      if (statusResp.ok) {
        const st = await statusResp.json();
        if (st.is_processing) {
          isProcessing = false;
          showWarning(t('app.m018'));
          return;
        }
      }
    } catch (e) {}

    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const progressStatus = document.getElementById('progress-status');
    const progressDetail = document.getElementById('progress-detail');
    const audioDuration = document.getElementById('audio-duration');
    const streamingOutput = document.getElementById('streaming-output');
    const streamingText = document.getElementById('streaming-text');

    fileInputView.style.display = 'none';
    progressContainer.style.display = 'block';
    progressContainer.classList.add('processing');
    progressBar.classList.add('animating');

    progressStatus.textContent = t('app.m019');
    progressDetail.textContent = '';

    let pollingInterval = null;

    function startPolling() {
      pollingInterval = setInterval(async () => {
        try {
          const resp = await fetch(`${API_BASE}/status`);
          if (!resp.ok) return;
          const st = await resp.json();
          if (!st.is_processing) {
            stopPolling();
            return;
          }
          updateProgressUI(st, { progressStatus, progressDetail, audioDuration, streamingOutput, streamingText });
        } catch (e) {}
      }, 1500);
    }

    function stopPolling() {
      if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
      }
    }

    function resetToInput() {
      progressContainer.style.display = 'none';
      progressBar.style.width = '0%';
      progressBar.classList.remove('animating', 'completed');
      progressText.textContent = '0%';
      progressContainer.classList.remove('processing');
      streamingOutput.style.display = 'none';
      streamingText.textContent = '';
      textInputArea.value = '';
      showPromptSelector();
    }

    const run = beginRun(() => {
      stopPolling();
      isProcessing = false;
      resetToInput();
    });

    startPolling();

    try {
      const processBody = { files: [], text_input: text };
      if (selectedPromptName) {
        processBody.prompt_name = selectedPromptName;
      }

      run.processSent = true;
      const processResponse = await apiRequest('/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(processBody),
        signal: run.controller.signal,
      }, 3, 2000);

      const processResult = await processResponse.json();
      if (run.cancelled) return;
      if (processResult.status === 'cancelled') { finishCancelled(run); return; }

      stopPolling();
      progressBar.classList.remove('animating');
      progressBar.classList.add('completed');
      progressBar.style.width = '100%';
      progressText.textContent = '100%';
      progressStatus.classList.remove('blinking');
      progressContainer.classList.remove('processing');

      progressStatus.innerHTML = t('app.m020');
      isProcessing = false;
      endRun(run);
      sendDesktopNotification(t('app.m021'), t('app.m022'), 'success');

      setTimeout(() => {
        if (processResult && processResult.results) {
          showResults(processResult.results);
        }
        resetToInput();
        // 履歴ページを表示
        menuItems.forEach(mi => mi.classList.remove('active'));
        pages.forEach(p => p.classList.remove('active'));
        document.querySelector('[data-page="history"]').classList.add('active');
        document.getElementById('page-history').classList.add('active');
        loadHistory();
      }, 5000);

    } catch (error) {
      if (run.cancelled) return;  // 中止による打ち切りはエラーとして扱わない
      endRun(run);
      stopPolling();
     showWarning(t('app.m023') + error.message);
      isProcessing = false;
      sendDesktopNotification(t('app.m021'), t('app.m024'), 'error');
      resetToInput();
    }
  }

  function addFiles(files) {
    const validFiles = files.filter(file => {
      const ext = '.' + file.name.split('.').pop().toLowerCase();
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        showWarning(t('app.m025', file.name));
        return false;
      }
      return true;
    });

    // 選択済み・同時に入れたものとの重複を除く（件数・サイズの上限は除いたあとで数える）
    const { accepted, duplicates, nameConflicts } = dedupeFiles(selectedFiles, validFiles);
    const notices = [];
    if (duplicates.length > 0) {
      notices.push(t('app.m082', duplicates.map(f => f.name).join(', ')));
    }
    if (nameConflicts.length > 0) {
      notices.push(t('app.m083', nameConflicts.map(f => f.name).join(', ')));
    }
    if (notices.length > 0) showWarning(notices.join('\n'));
    if (accepted.length === 0) return;

    const newTotal = selectedFiles.length + accepted.length;
    if (newTotal > MAX_FILES) {
      showWarning(t('app.m026', MAX_FILES, selectedFiles.length));
      return;
    }

    const newSize = [...selectedFiles, ...accepted].reduce((sum, f) => sum + f.size, 0);
    if (newSize > MAX_SIZE_BYTES) {
      showWarning(t('app.m027'));
      return;
    }

    selectedFiles = [...selectedFiles, ...accepted];
    updateFileList();
  }

  function updateFileList() {
    fileList.innerHTML = '';
    selectedFiles.forEach((file, index) => {
      const li = document.createElement('li');
      li.className = 'file-item';
      li.innerHTML = `
        <div class="file-info">
          <span class="file-icon">🎵</span>
          <div>
            <div class="file-name">${file.name}</div>
            <div class="file-size">${formatFileSize(file.size)}</div>
          </div>
        </div>
        <button class="file-remove" data-index="${index}">✕</button>
      `;
      fileList.appendChild(li);
    });

    document.querySelectorAll('.file-remove').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const index = parseInt(btn.dataset.index);
        selectedFiles.splice(index, 1);
        updateFileList();
      });
    });

    const totalSize = selectedFiles.reduce((sum, f) => sum + f.size, 0);
    fileCount.textContent = t('app.m028', selectedFiles.length, formatFileSize(totalSize));
    fileListContainer.style.display = selectedFiles.length > 0 ? 'block' : 'none';
  }

  btnClearAll.addEventListener('click', () => {
    selectedFiles = [];
    updateFileList();
  });

  // ==================== 処理開始 ====================
  btnStartProcess.addEventListener('click', async () => {
    if (selectedFiles.length === 0) return;
    await startProcessing();
  });

  async function startProcessing() {
    isProcessing = true;
    try {
      const statusResp = await fetch(`${API_BASE}/status`);
      if (statusResp.ok) {
        const st = await statusResp.json();
        if (st.is_processing) {
          isProcessing = false;
          showWarning(t('app.m018'));
          return;
        }
      }
    } catch (e) {}

    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const progressStatus = document.getElementById('progress-status');
    const progressDetail = document.getElementById('progress-detail');
    const audioDuration = document.getElementById('audio-duration');
    const streamingOutput = document.getElementById('streaming-output');
    const streamingText = document.getElementById('streaming-text');

    fileInputView.style.display = 'none';
    fileListContainer.style.display = 'none';
    progressContainer.style.display = 'block';
    progressContainer.classList.add('processing');
    progressBar.classList.add('animating');

    const fileNames = selectedFiles.map(f => f.name).join(', ');
    progressStatus.textContent = t('app.m029', fileNames);
    progressDetail.textContent = '';

    let pollingInterval = null;

    function startPolling() {
      pollingInterval = setInterval(async () => {
        try {
          const resp = await fetch(`${API_BASE}/status`);
          if (!resp.ok) return;
          const st = await resp.json();
          if (!st.is_processing) {
            stopPolling();
            return;
          }
          updateProgressUI(st, { progressStatus, progressDetail, audioDuration, streamingOutput, streamingText });
        } catch (e) {}
      }, 1500);
    }

    function stopPolling() {
      if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
      }
    }

    function resetToInput() {
      progressContainer.style.display = 'none';
      progressBar.style.width = '0%';
      progressBar.classList.remove('animating', 'completed');
      progressText.textContent = '0%';
      progressContainer.classList.remove('processing');
      streamingOutput.style.display = 'none';
      streamingText.textContent = '';
      fileListContainer.style.display = 'none';
      selectedFiles = [];
      updateFileList();
      textInputArea.value = '';
      // ファイルタブに戻す
      inputModeTabs.forEach(t => t.classList.remove('active'));
      document.querySelector('[data-mode="file"]').classList.add('active');
      inputModeContents.forEach(c => {
        c.classList.remove('active');
        c.style.display = 'none';
      });
      const fileMode = document.getElementById('mode-file');
      if (fileMode) {
        fileMode.classList.add('active');
        fileMode.style.display = 'block';
      }
      showPromptSelector();
    }

    const run = beginRun(() => {
      stopPolling();
      isProcessing = false;
      resetToInput();
    });

    try {
      // 万一 addFiles をすり抜けても、同じファイルは1回しか送らない（最終チェック）
      const uploadFiles = dedupeFiles([], selectedFiles).accepted;
      const formData = new FormData();
      uploadFiles.forEach(file => formData.append('files', file));

      const uploadResponse = await apiRequest('/upload', {
        method: 'POST',
        body: formData,
        signal: run.controller.signal,
      }, 5, 2000);
      const uploadResult = await uploadResponse.json();
      if (run.cancelled) return;  // アップロード中に中止された場合は /process を呼ばない

      if (uploadResult.errors && uploadResult.errors.length > 0) {
        showWarning(uploadResult.errors.map(e => e.error).join('\n'));
      }

      // 処理リクエスト（選択済みプロンプト名を含める）
      const processBody = { files: uploadResult.uploaded };
      if (selectedPromptName) {
        processBody.prompt_name = selectedPromptName;
      }

      // ポーリングを開始（POST /process レスポンス前に開始し、ステップ名を表示）
      startPolling();

      run.processSent = true;
      const processResponse = await apiRequest('/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(processBody),
        signal: run.controller.signal,
      }, 3, 2000);

      const processResult = await processResponse.json();
      if (run.cancelled) return;
      if (processResult.status === 'cancelled') { finishCancelled(run); return; }

      stopPolling();
      progressBar.classList.remove('animating');
      progressBar.classList.add('completed');
      progressBar.style.width = '100%';
      progressText.textContent = '100%';
      progressStatus.classList.remove('blinking');
      progressContainer.classList.remove('processing');

      progressStatus.innerHTML = t('app.m020');
      isProcessing = false;
      endRun(run);
      sendDesktopNotification(t('app.m021'), t('app.m030'), 'success');

      setTimeout(() => {
        if (processResult && processResult.results) {
          showResults(processResult.results);
        }
        resetToInput();
        // 履歴ページを表示
        menuItems.forEach(mi => mi.classList.remove('active'));
        pages.forEach(p => p.classList.remove('active'));
        document.querySelector('[data-page="history"]').classList.add('active');
        document.getElementById('page-history').classList.add('active');
        loadHistory();
      }, 5000);

    } catch (error) {
      if (run.cancelled) return;  // 中止による打ち切りはエラーとして扱わない
      endRun(run);
      stopPolling();
      showWarning(t('app.m023') + error.message);
      isProcessing = false;
      sendDesktopNotification(t('app.m021'), t('app.m024'), 'error');
      resetToInput();
    }
  }

  function renderMarkdown(text) {
    if (!text) return '';

    const inlineFormat = (s) => s
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>');

    // 末尾の空行は表示上の無駄な余白になるため除去してから行単位で処理する
    const lines = text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n');
    const html = [];
    let inList = false;
    const closeList = () => {
      if (inList) {
        html.push('</ul>');
        inList = false;
      }
    };

    let inFence = false;  // コードフェンスの中では表とみなさない
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const isFence = /^\s*(\x60{3}|~~~)/.test(line);
      if (isFence) inFence = !inFence;
      const table = isFence || inFence ? null : markdownTable(lines, i);
      if (table) {
        closeList();
        html.push(table.html);
        i = table.next - 1;
        continue;
      }
      const h3 = line.match(/^### (.*)$/);
      const h2 = line.match(/^## (.*)$/);
      const h1 = line.match(/^# (.*)$/);
      const li = line.match(/^\s*-\s+(.*)$/);

      if (h3) {
        closeList();
        html.push(`<h3>${inlineFormat(h3[1])}</h3>`);
      } else if (h2) {
        closeList();
        html.push(`<h2>${inlineFormat(h2[1])}</h2>`);
      } else if (h1) {
        closeList();
        html.push(`<h1>${inlineFormat(h1[1])}</h1>`);
      } else if (li) {
        // リスト項目同士の間には<br>を挟まない（挟むと項目間に無駄な空行が生じるため）
        if (!inList) {
          html.push('<ul>');
          inList = true;
        }
        html.push(`<li>${inlineFormat(li[1])}</li>`);
      } else {
        closeList();
        html.push(line === '' ? '<br>' : `${inlineFormat(line)}<br>`);
      }
    }
    closeList();
    return html.join('');
  }

  function showResults(results) {
    const resultContainer = document.getElementById('result-container');
    const resultContent = document.getElementById('result-content');
    const resultErrorActions = document.getElementById('result-error-actions');
    const btnNewProcess = document.getElementById('btn-new-process');
    resultContent.innerHTML = '';
    lastFailedFiles = [];

    const hasErrors = results.some(r => r.status === 'error');
    recordRunForDashboard(results);

    results.forEach(r => {
      const div = document.createElement('div');
      div.className = 'result-item';
      if (r.status === 'ok') {
        div.innerHTML = `
          <h3>✅ ${r.file}</h3>
          <div class="result-report markdown-body">${renderMarkdown(r.report)}</div>
        `;
      } else {
        if (!lastFailedFiles.includes(r.file)) lastFailedFiles.push(r.file);
        div.innerHTML = `
          <h3>❌ ${r.file}</h3>
          <p class="error-message">${escapeHtml(r.message)}</p>
        `;
      }
      resultContent.appendChild(div);
    });

    if (hasErrors && lastFailedFiles.length > 0) {
      resultErrorActions.style.display = 'flex';
      btnNewProcess.style.display = 'none';
    } else {
      resultErrorActions.style.display = 'none';
      btnNewProcess.style.display = 'block';
    }

    resultContainer.style.display = 'block';
  }

  document.getElementById('btn-new-process').addEventListener('click', () => {
    document.getElementById('result-container').style.display = 'none';
  });

  document.getElementById('btn-new-process-top').addEventListener('click', () => {
    document.getElementById('result-container').style.display = 'none';
  });

  // ==================== 設定画面 ====================
  let currentReportText = '';
  let currentHistoryFileName = '';
  // 設定画面で選んだダウンロード形式の既定値（loadSettings()で復元）
  let defaultDownloadFormat = 'txt';
  let pendingResetTab = null;
  let _resetOnConfirm = null;
  let _resetOnCancel = null;

  // 「AIモデルの指定」欄の既定表示（GET /config の llm_model_default_label で上書きされる）
  let llmModelDefaultLabel = 'gemma4:e2b-qat(llama.cpp)';
  // 設定から読み込んだ（保存済みの）文字起こしモデル。保存時に、変わったかどうかの判断に使う
  let loadedVibeModel = null;
  // 保存の前の確認で「ダウンロード」を選び、ダウンロードを始めたか（保存後の案内と進捗表示に使う）
  let vibeDownloadStarted = false;
  let savingModelSettings = false;

  // 処理メニューの説明行に、いま選んでいる文字起こしモデルとAIモデルの名前を表示する
  function updateCurrentModels(config) {
    const el = document.getElementById('current-models');
    if (!el || !config) return;
    const vibe = config.vibe_model || 'large-v3-turbo';
    const defaultLabel = config.llm_model_default_label || llmModelDefaultLabel;
    const llm = config.llm_model_path ? String(config.llm_model_path).split(/[\\/]/).pop() : defaultLabel;
    el.textContent = t('app.current_models', vibe, llm);
  }

  async function refreshCurrentModels() {
    try {
      const response = await apiRequest('/config', {}, 1, 0);
      updateCurrentModels(await response.json());
    } catch (e) {
      // 取得できなければ前回の表示のまま
    }
  }

  // 選んだ文字起こしモデルがこの PC に無いとき、保存の前にダウンロードするかを確認する。
  // 続けてよければ true（ダウンロードの開始も済ませる）、キャンセルなら false
  async function confirmVibeModelDownload(model, changed = true) {
    let status;
    try {
      const response = await apiRequest(`/vibe/model/status?model=${encodeURIComponent(model)}`, {}, 1, 0);
      status = await response.json();
    } catch (e) {
      showWarning(t('app.vibe_status_failed') + e.message);
      return false;
    }
    if (status.status === 'ready' || status.app_downloading) return true;

    const modal = document.getElementById('vibe-model-download-modal');
    const detail = document.getElementById('vibe-model-download-detail');
    detail.textContent = status.size_mb
      ? t('app.vibe_dl_detail', status.model, status.size_mb)
      : t('app.vibe_dl_detail_nosize', status.model);
    modal.style.display = 'flex';
    const choice = await new Promise(resolve => {
      const ok = document.getElementById('btn-vibe-dl-download');
      const cancel = document.getElementById('btn-vibe-dl-cancel');
      const done = value => {
        ok.removeEventListener('click', onOk);
        cancel.removeEventListener('click', onCancel);
        modal.style.display = 'none';
        resolve(value);
      };
      const onOk = () => done(true);
      const onCancel = () => done(false);
      ok.addEventListener('click', onOk);
      cancel.addEventListener('click', onCancel);
    });
    if (!choice) return !changed;  // 変えていないモデルのときは、ダウンロードせずに他の設定の保存を続ける
    try {
      const response = await apiRequest('/vibe/model/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: status.model }),
      }, 1, 0);
      await response.json();
    } catch (e) {
      showWarning(t('app.vibe_dl_failed') + e.message);
      return false;
    }
    vibeDownloadStarted = true;
    return true;
  }

  // ダウンロードの進捗を、処理メニューの状態バナーに表示する（終わるまで数秒おきに更新）
  function watchVibeModelDownload() {
    let count = 0;
    const tick = async () => {
      await refreshDashboard();
      try {
        const response = await apiRequest('/status', {}, 1, 0);
        const st = await response.json();
        if (st.vibe_model_downloading && count++ < 600) {
          setTimeout(tick, 3000);
          return;
        }
      } catch (e) {
        // 取得できなければ見守りをやめる
      }
      refreshCurrentModels();
    };
    setTimeout(tick, 1500);
  }

  async function loadSettings() {
    try {
      const response = await apiRequest('/config', {}, 3, 1000);
      const config = await response.json();
      // 読み込んだ値で画面を置き換えるので、未保存の印は解除する
      clearAllDirty();

      document.getElementById('transcription-model').value = config.vibe_model || 'large-v3-turbo';
      loadedVibeModel = config.vibe_model || 'large-v3-turbo';
      updateCurrentModels(config);
      document.getElementById('vibe-server-path').value = config.vibe_server_path || '';
      // AIモデルの指定: 空(=既定モデル)のときは既定ラベルを表示する
      llmModelDefaultLabel = config.llm_model_default_label || llmModelDefaultLabel;
      document.getElementById('llm-model-path').value = config.llm_model_path || llmModelDefaultLabel;
      document.getElementById('llm-device').value = config.llm_device || 'auto';
      refreshLlmDeviceStatus(false);
      document.getElementById('prompt-editor').value = config.prompt;

      savedPrompts = config.saved_prompts || {};
      builtinPromptNames = config.builtin_prompt_names || [];
      applyUiLanguage(config.ui_language);
      activePromptName = config.active_prompt_name || Object.keys(savedPrompts)[0] || '';
      document.getElementById('prompt-name').value = activePromptName;

      // 説明フィールドを読み込み
      const currentPromptData = savedPrompts[activePromptName];
      const description = (typeof currentPromptData === 'object' && currentPromptData !== null)
        ? (currentPromptData.description || '') : '';
      document.getElementById('prompt-description').value = description;

      // include_transcript ラジオボタンを読み込み
      const includeTranscript = (typeof currentPromptData === 'object' && currentPromptData !== null)
        ? (currentPromptData.include_transcript ? 'true' : 'false') : 'false';
      const includeRadio = document.querySelector(`input[name="include-transcript"][value="${includeTranscript}"]`);
      if (includeRadio) includeRadio.checked = true;
      document.getElementById('prompt-hidden').checked = isPromptHidden(currentPromptData);
      // 出力先も読み込む（読み込まないまま保存すると、出力先が空で上書きされていた）
      document.getElementById('prompt-output-dir').value = (typeof currentPromptData === 'object' && currentPromptData !== null)
        ? (currentPromptData.output_dir || '') : '';

      renderPromptList();

      if (config.dark_mode) {
        applyTheme('dark');
      }

      // ダウンロード形式の既定値を復元
      defaultDownloadFormat = config.download_format || 'txt';
      const dlFormatRadio = document.querySelector(`input[name="download_format"][value="${defaultDownloadFormat}"]`);
      if (dlFormatRadio) {
        dlFormatRadio.checked = true;
      }
    } catch (e) {
      console.error('設定の読み込みに失敗:', e);
      showWarning(t('app.m031'));
    }
  }


  function renderPromptList() {
    const list = document.getElementById('prompt-list');
    list.innerHTML = '';
    Object.keys(savedPrompts).forEach(name => {
      const div = document.createElement('div');
      const promptData = savedPrompts[name];
      const hidden = isPromptHidden(promptData);
      div.className = 'prompt-item' + (name === activePromptName ? ' active' : '') + (hidden ? ' is-hidden' : '');
      const description = (typeof promptData === 'object' && promptData !== null) ? (promptData.description || '') : '';
      div.innerHTML = `
        <span class="prompt-item-name">${escapeHtml(name)}</span>
        ${description ? `<span class="prompt-item-description">${escapeHtml(description)}</span>` : ''}
        ${hidden ? `<span class="prompt-item-badge">${escapeHtml(t('app.prompt_hidden_badge'))}</span>` : ''}
      `;
      div.addEventListener('click', async () => {
        if (dirtyTabs['prompt-settings']) {
          if (!(await askDiscardSettings())) return;
        }
        activePromptName = name;
        document.getElementById('prompt-name').value = name;
        if (typeof promptData === 'object' && promptData !== null) {
          document.getElementById('prompt-editor').value = promptData.content || '';
          document.getElementById('prompt-description').value = promptData.description || '';
          document.getElementById('prompt-output-dir').value = promptData.output_dir || '';
          const includeRadio = document.querySelector(`input[name="include-transcript"][value="${promptData.include_transcript ? 'true' : 'false'}"]`);
          if (includeRadio) includeRadio.checked = true;
          document.getElementById('prompt-hidden').checked = hidden;
        } else {
          document.getElementById('prompt-editor').value = promptData || '';
          document.getElementById('prompt-description').value = '';
          document.getElementById('prompt-output-dir').value = '';
          document.querySelector('input[name="include-transcript"][value="false"]').checked = true;
          document.getElementById('prompt-hidden').checked = false;
        }
        setDirty('prompt-settings', false);  // 保存済みの内容を読み込んだので未保存ではない
        renderPromptList();
        try {
          await apiRequest('/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active_prompt_name: name }),
          }, 2, 500);
        } catch (e) {
          console.error('アクティブプロンプト保存エラー:', e);
        }
      });
      list.appendChild(div);
    });
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // HTML属性の値に入れるときは引用符も変換する（escapeHtml は " を変換しない）
  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  document.getElementById('btn-new-prompt').addEventListener('click', () => {
    document.getElementById('prompt-name').value = '';
    document.getElementById('prompt-editor').value = '';
    document.getElementById('prompt-description').value = '';
    document.getElementById('prompt-output-dir').value = '';
    document.querySelector('input[name="include-transcript"][value="false"]').checked = true;
    document.getElementById('prompt-hidden').checked = false;
    document.getElementById('prompt-name').focus();
    setDirty('prompt-settings', true);
  });

  async function savePromptSettings() {
    const name = document.getElementById('prompt-name').value.trim();
    const content = document.getElementById('prompt-editor').value;
    const description = document.getElementById('prompt-description').value.trim();
    const outputDir = document.getElementById('prompt-output-dir').value.trim();
    const includeTranscript = document.querySelector('input[name="include-transcript"]:checked').value === 'true';
    const hidden = document.getElementById('prompt-hidden').checked;
    if (!name) {
      showWarning(t('app.m032'));
      return false;
    }
    try {
      const response = await apiRequest('/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, content, description, output_dir: outputDir, include_transcript: includeTranscript, hidden }),
      }, 3, 1000);
      const result = await response.json();
      if (result.status === 'ok') {
        savedPrompts[name] = { content, description, output_dir: outputDir, include_transcript: includeTranscript, hidden };
        activePromptName = name;
        await apiRequest('/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active_prompt_name: name }),
        }, 3, 1000);
        renderPromptList();
        setDirty('prompt-settings', false);
        showSuccess(t('app.m033', name));
        return true;
      } else {
        showWarning(result.message);
      }
    } catch (e) {
      showWarning(t('app.m034') + e.message);
    }
    return false;
  }

  document.getElementById('btn-delete-prompt').addEventListener('click', async () => {
    const name = document.getElementById('prompt-name').value.trim();
    if (!name) {
      showWarning(t('app.m032'));
      return;
    }
    if (builtinPromptNames.includes(name)) {
      showWarning(t('app.m035'));
      return;
    }
    if (!(await askConfirm({ title: t('app.m036'), message: t('app.m037', name) }))) return;
    try {
      const response = await apiRequest(`/prompts/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      }, 3, 1000);
      const result = await response.json();
      if (result.status === 'ok') {
        delete savedPrompts[name];
        if (activePromptName === name) {
          // アクティブだったプロンプトを消した場合、サーバーが選び直した初期プロンプトを読み込み直す
          setDirty('prompt-settings', false);
          await loadSettings();
        }
        renderPromptList();
        showSuccess(t('app.m038', name));
      } else {
        showWarning(result.message);
      }
    } catch (e) {
      showWarning(t('app.m039') + e.message);
    }
  });

  document.getElementById('btn-copy-prompt').addEventListener('click', async () => {
    const currentName = document.getElementById('prompt-name').value.trim();
    if (!currentName) {
      showWarning(t('app.m040'));
      return;
    }
    const content = document.getElementById('prompt-editor').value;

    // 1. クリップボードへコピー（保存の成否に関わらず常に試みる）
    let clipboardOk = true;
    try {
      await navigator.clipboard.writeText(content);
    } catch (e) {
      try {
        const textarea = document.createElement('textarea');
        textarea.value = content;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      } catch (e2) {
        clipboardOk = false;
      }
    }

    // 2. 「元の名前+コピー」という名前で保存済みプロンプト一覧に新規追加
    const copyName = currentName + t('app.m041');
    const description = document.getElementById('prompt-description').value.trim();
    const outputDir = document.getElementById('prompt-output-dir').value.trim();
    const includeTranscript = document.querySelector('input[name="include-transcript"]:checked').value === 'true';
    try {
      const response = await apiRequest('/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: copyName, content, description, output_dir: outputDir, include_transcript: includeTranscript, hidden: false }),
      }, 3, 1000);
      const result = await response.json();
      if (result.status === 'ok') {
        savedPrompts[copyName] = { content, description, output_dir: outputDir, include_transcript: includeTranscript, hidden: false };
        activePromptName = copyName;
        await apiRequest('/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active_prompt_name: copyName }),
        }, 3, 1000);
        document.getElementById('prompt-name').value = copyName;
        document.getElementById('prompt-hidden').checked = false;
        setDirty('prompt-settings', false);
        renderPromptList();
        showSuccess(clipboardOk
          ? t('app.m042', copyName)
          : t('app.m043', copyName));
      } else {
        showWarning(result.message);
      }
    } catch (e) {
      showWarning(t('app.m044') + e.message);
    }
  });

  // ==================== 保存済みプロンプトのバックアップ / 復元 ====================
  document.getElementById('btn-backup-prompts').addEventListener('click', async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      const response = await apiRequest('/prompts/backup', { method: 'POST' }, 1, 0);
      const result = await response.json();
      if (result.status === 'ok') {
        showSuccess(result.message);
      } else {
        showWarning(result.message);
      }
    } catch (err) {
      showWarning(t('app.backup_failed') + err.message);
    } finally {
      button.disabled = false;
    }
  });

  // 復元方法を選ぶダイアログ。'merge' / 'replace' / null（キャンセル）を返す
  function askRestoreMode(info) {
    return new Promise(resolve => {
      const modal = document.getElementById('restore-prompts-modal');
      if (modal.style.display === 'flex') {
        resolve(null);
        return;
      }
      document.getElementById('restore-prompts-message').textContent = t('app.restore_count', info.count);
      const details = [];
      if (info.conflicts && info.conflicts.length) details.push(t('app.restore_conflicts', info.conflicts.join(', ')));
      if (info.builtin_kept && info.builtin_kept.length) details.push(t('app.restore_builtin_kept', info.builtin_kept.join(', ')));
      document.getElementById('restore-prompts-detail').textContent = details.join('\n');
      const buttons = {
        'btn-restore-merge': 'merge',
        'btn-restore-replace': 'replace',
        'btn-restore-cancel': null,
      };
      const handlers = {};
      const finish = (mode) => {
        modal.style.display = 'none';
        Object.keys(handlers).forEach(id => document.getElementById(id).removeEventListener('click', handlers[id]));
        resolve(mode);
      };
      Object.entries(buttons).forEach(([id, mode]) => {
        handlers[id] = () => finish(mode);
        document.getElementById(id).addEventListener('click', handlers[id]);
      });
      modal.style.display = 'flex';
      document.getElementById('btn-restore-cancel').focus();
    });
  }

  // source は {path} または {content}
  async function restorePromptsFrom(source) {
    try {
      const previewResponse = await apiRequest('/prompts/restore/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(source),
      }, 1, 0);
      const info = await previewResponse.json();
      if (info.status !== 'ok') {
        showWarning(info.message);
        return;
      }
      const mode = await askRestoreMode(info);
      if (!mode) return;
      const response = await apiRequest('/prompts/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...source, mode }),
      }, 1, 0);
      const result = await response.json();
      if (result.status !== 'ok') {
        showWarning(result.message);
        return;
      }
      await loadSettings();
      setDirty('prompt-settings', false);
      await loadPromptsForMain();
      showSuccess(result.message);
    } catch (err) {
      showWarning(t('app.restore_failed') + err.message);
    }
  }

  document.getElementById('btn-restore-prompts').addEventListener('click', async (e) => {
    const button = e.currentTarget;
    // 復元後は設定を読み込み直すので、どのタブの未保存の変更も消える。確認して先に破棄しておく
    if (hasDirty()) {
      if (!(await askDiscardSettings())) return;
      await loadSettings();
      clearAllDirty();
    }
    button.disabled = true;
    try {
      const response = await apiRequest('/dialog/pick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: 'prompts_json' }),
      }, 1, 0);
      const result = await response.json();
      if (result.status === 'ok') {
        await restorePromptsFrom({ path: result.path });
      } else if (result.status === 'error') {
        // アプリのウィンドウが無い（ブラウザで開いた等）ときは、ブラウザのファイル選択で読み込む
        document.getElementById('restore-prompts-file').click();
      }
    } catch (err) {
      document.getElementById('restore-prompts-file').click();
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById('restore-prompts-file').addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';  // 同じファイルをもう一度選んでも change が起きるようにする
    if (!file) return;
    try {
      await restorePromptsFrom({ content: await file.text() });
    } catch (err) {
      showWarning(t('app.restore_failed') + err.message);
    }
  });

  // ファイル/フォルダ選択ダイアログ（アプリ側で開き、選ばれたフルパスを入力欄へ入れる）。
  // kind: "exe"（vibe-server.exe）/ "gguf"（AIモデル）/ "folder"（フォルダ）
  async function pickPath(kind, inputId, button) {
    const input = document.getElementById(inputId);
    const message = t('app.m045');
    if (button) button.disabled = true;
    try {
      const response = await apiRequest('/dialog/pick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, initial: input.value.trim() }),
      }, 1, 0);
      const result = await response.json();
      if (result.status === 'ok') {
        input.value = result.path;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      } else if (result.status === 'error') {
        showWarning(result.message || message);
      }
    } catch (e) {
      showWarning(message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  [
    ['btn-select-output-dir', 'folder', 'prompt-output-dir'],
    ['btn-select-vibe-server', 'exe', 'vibe-server-path'],
    ['btn-select-llm-model', 'gguf', 'llm-model-path'],
  ].forEach(([buttonId, kind, inputId]) => {
    const button = document.getElementById(buttonId);
    button.addEventListener('click', () => pickPath(kind, inputId, button));
  });

  // 「LLM の実行装置」の実際の状態（GET /llm/status）を表示する。poll=true のときは読み込み直しが
  // 終わるまで（最大約30秒）数秒おきに確認する
  async function refreshLlmDeviceStatus(poll, attempt = 0) {
    const el = document.getElementById('llm-device-status');
    if (!el) return;
    try {
      const response = await apiRequest('/llm/status', {}, 1, 500);
      const st = await response.json();
      let text;
      if (st.device === 'gpu') {
        text = t('app.llm_status_gpu', st.device_name || 'GPU');
      } else if (st.device === 'cpu') {
        text = t('app.llm_status_cpu');
        const reasonKeys = {
          cpu_selected: 'app.llm_reason_cpu_selected',
          no_gpu: 'app.llm_reason_no_gpu',
          previous_crash: 'app.llm_reason_previous_crash',
          gpu_load_failed: 'app.llm_reason_gpu_load_failed',
        };
        if (reasonKeys[st.reason]) text += ' — ' + t(reasonKeys[st.reason]);
      } else {
        text = t('app.llm_status_unloaded');
      }
      el.textContent = text;
      // 前回 GPU で強制終了して自動で CPU にしているときは、一度だけ案内する（印が消えたら次回に備えて解除）
      // localStorage が使えなくても、案内以外は動く
      try {
        if (st.gpu_disabled_by_crash && st.configured === 'auto') {
          if (!localStorage.getItem('llm_crash_notice_shown')) {
            localStorage.setItem('llm_crash_notice_shown', '1');
            showWarning(t('app.llm_crash_toast'));
          }
        } else if (!st.gpu_disabled_by_crash) {
          localStorage.removeItem('llm_crash_notice_shown');
        }
      } catch (e) { /* ignore */ }
      if (poll && st.device === null && attempt < 10) {
        setTimeout(() => refreshLlmDeviceStatus(true, attempt + 1), 3000);
      }
    } catch (e) {
      el.textContent = '';
    }
  }

  async function saveModelSettingsFromForm() {
    if (savingModelSettings) return false;  // 確認ダイアログの表示中などに、重ねて保存しない（Ctrl+S の連打）
    savingModelSettings = true;
    try {
      return await saveModelSettingsFromFormInner();
    } finally {
      savingModelSettings = false;
    }
  }

  async function saveModelSettingsFromFormInner() {
    const transcriptionModel = document.getElementById('transcription-model').value;
    if (loadedVibeModel !== null) {
      // モデルは自動ではダウンロードしない。選んだモデルがこの PC に無ければ、ここで利用者に確認する。
      // 変えたモデルでキャンセルなら保存しない。変えていないモデルが未取得のときも確認する（取得し直せるように）
      const changed = transcriptionModel !== loadedVibeModel;
      if (!(await confirmVibeModelDownload(transcriptionModel, changed))) return false;
    }
    const vibeServerPath = document.getElementById('vibe-server-path').value.trim();
    const llmModelPath = document.getElementById('llm-model-path').value.trim();
    const llmDevice = document.getElementById('llm-device').value;
    return saveModelSettings(transcriptionModel, vibeServerPath, llmModelPath, llmDevice);
  }

  async function saveModelSettings(transcriptionModel, vibeServerPath, llmModelPath, llmDevice) {
    try {
      const body = {
        vibe_model: transcriptionModel,
        vibe_server_path: vibeServerPath,
        // 空欄・既定ラベルのままならバックエンドが既定モデル扱いにする
        llm_model_path: llmModelPath,
        llm_device: llmDevice,
      };
      const response = await apiRequest('/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }, 3, 1000);
      const result = await response.json();
      if (result.status === 'ok') {
        setDirty('model-settings', false);
        loadedVibeModel = transcriptionModel;
        refreshCurrentModels();
        if (vibeDownloadStarted) {
          vibeDownloadStarted = false;
          showSuccess(t('app.vibe_dl_started'));
          watchVibeModelDownload();
        } else {
          showSuccess(t('app.m046'));
        }
        // 装置を変えると裏でモデルを読み込み直すので、実際に使っている装置が確定するまで表示を更新する
        refreshLlmDeviceStatus(true);
        return true;
      }
      vibeDownloadStarted = false;
      showWarning(result.message);
    } catch (e) {
      vibeDownloadStarted = false;
      showWarning(t('app.m034') + e.message);
    }
    return false;
  }

  // ==================== 設定リセット ====================
  function confirmReset(tab) {
    pendingResetTab = tab;
    const modal = document.getElementById('reset-confirm-modal');
    const detail = document.getElementById('reset-confirm-detail');

    if (tab === 'prompt') {
      detail.textContent = t('app.m047');
    } else {
      detail.textContent = t('app.m048');
    }

    modal.style.display = 'flex';

    const onConfirm = async () => {
      modal.style.display = 'none';
      cleanupResetListeners();
      // リセット後は設定を読み直すため、リセット対象でないタブの未保存の編集も消える。先に確認する
      const otherTab = tab === 'prompt' ? 'model-settings' : 'prompt-settings';
      if (dirtyTabs[otherTab] && !(await askDiscardSettings())) {
        pendingResetTab = null;
        return;
      }
      await executeReset(pendingResetTab);
    };

    const onCancel = () => {
      modal.style.display = 'none';
      cleanupResetListeners();
      pendingResetTab = null;
    };

    _resetOnConfirm = onConfirm;
    _resetOnCancel = onCancel;

    document.getElementById('btn-reset-confirm').addEventListener('click', onConfirm);
    document.getElementById('btn-reset-cancel').addEventListener('click', onCancel);
  }

  function cleanupResetListeners() {
    if (_resetOnConfirm) {
      document.getElementById('btn-reset-confirm').removeEventListener('click', _resetOnConfirm);
      _resetOnConfirm = null;
    }
    if (_resetOnCancel) {
      document.getElementById('btn-reset-cancel').removeEventListener('click', _resetOnCancel);
      _resetOnCancel = null;
    }
  }

  async function executeReset(tab) {
    try {
      const response = await apiRequest('/config/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tab: tab }),
      }, 3, 1000);
      const result = await response.json();
      if (result.status === 'ok') {
        showSuccess(t('app.m049'));
        // 設定画面を再読み込み
        await loadSettings();
        // メイン画面のプロンプト一覧も更新
        await loadPromptsForMain();
      } else {
        showWarning(result.message);
      }
    } catch (e) {
      showWarning(t('app.m050') + e.message);
    }
    pendingResetTab = null;
  }

  // ==================== ダークモード切替 ====================
  function applyTheme(theme) {
    if (theme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    document.querySelectorAll('.toggle-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.themeValue === theme);
    });
  }

  document.querySelectorAll('.toggle-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const theme = btn.dataset.themeValue;
      applyTheme(theme);
      try {
        await apiRequest('/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dark_mode: theme === 'dark' }),
        }, 3, 1000);
      } catch (e) {
        console.error('ダークモード設定の保存に失敗:', e);
      }
    });
  });

  // ==================== 表示言語 ====================
  function applyUiLanguage(lang) {
    // 言語が変わるときだけ文書全体を再翻訳する（処理中の表示・ファイル数など、動的に書き換えた文言を初期文言へ戻さない）
    if ((lang || 'ja') !== getLanguage()) setLanguage(lang || 'ja');
    const select = document.getElementById('ui-language');
    if (select) select.value = getLanguage();
  }

  document.getElementById('ui-language').addEventListener('change', async (e) => {
    const select = e.target;
    const previous = getLanguage();
    if (hasDirty()) {
      // 未保存の編集（初期プロンプトの名前など）が、入れ替わった名前と食い違って重複を作らないよう、先に保存か破棄を求める
      select.value = previous;
      showWarning(t('app.lang_blocked'));
      return;
    }
    try {
      await apiRequest('/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ui_language: select.value }),
      }, 3, 1000);
      applyUiLanguage(select.value);
      // 編集していない初期プロンプトの名前・内容が入れ替わるため、一覧と編集欄を読み込み直す
      await loadSettings();
      await loadPromptsForMain();
      refreshDashboard();
      if (document.getElementById('page-history').classList.contains('active')) loadHistory();
      showSuccess(t('app.lang_changed'));
    } catch (err) {
      applyUiLanguage(previous);
      showWarning(t('app.lang_change_failed') + err.message);
    }
  });

  // ダウンロード形式の既定値ラジオボタン（設定画面）のイベントハンドラ
  document.querySelectorAll('input[name="download_format"]').forEach(radio => {
    radio.addEventListener('change', async (e) => {
      const format = e.target.value;
      try {
        await apiRequest('/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ download_format: format }),
        }, 3, 1000);
        defaultDownloadFormat = format;
      } catch (err) {
        console.error('ダウンロード形式の保存に失敗:', err);
        showWarning(t('app.m051'));
      }
    });
  });

  // ==================== 処理履歴 ====================
  document.getElementById('btn-open-output-folder').addEventListener('click', async () => {
    try {
      const response = await apiRequest('/open-output-folder', { method: 'POST' }, 3, 1000);
      const result = await response.json();
      if (result.status !== 'ok') {
        showWarning(result.message || t('app.m052'));
      }
    } catch (err) {
      showWarning(t('app.m053') + err.message);
    }
  });

  // performSearch()の多重発火・追い越し対策用トークン。新しい検索（またはクリア）が始まるたびに
  // インクリメントし、awaitから戻った時点で自分のトークンが最新でなければ結果を捨てる
  let historySearchToken = 0;

  async function loadHistory() {
    const historyList = document.getElementById('history-list');
    const historyEmpty = document.getElementById('history-empty');
    const historyActions = document.getElementById('history-actions');
    const historyFilterBar = document.getElementById('history-filter-bar');

    try {
      const response = await apiRequest('/history', {}, 3, 1000);
      const data = await response.json();
      allHistoryItems = data.items || [];
      historySearchToken++; // 進行中のperformSearch()の結果が後から反映されないようにする
      historyItems = allHistoryItems;

      if (allHistoryItems.length === 0) {
        selectedHistoryIds.clear();
        historyList.innerHTML = '';
        historyEmpty.style.display = 'block';
        historyActions.style.display = 'none';
        historyFilterBar.style.display = 'none';
        return;
      }

      historyEmpty.style.display = 'none';
      historyActions.style.display = 'flex';
      historyFilterBar.style.display = 'block';

      // 検索欄に文字列が残っている場合（履歴削除後の再読み込み等）は検索条件を維持する
      const searchText = document.getElementById('history-search').value.trim();
      if (searchText) {
        await performSearch();
      } else {
        renderFilteredHistory();
      }
    } catch (e) {
      console.error('履歴の読み込みに失敗:', e);
      historyEmpty.style.display = 'block';
      historyList.innerHTML = '';
    }
  }

  // 検索欄の入力時に呼ばれる。全文検索（ファイル名・タイトル・本文）はサーバー側で行い
  // 、結果をhistoryItemsに反映してから日付フィルタ込みで再描画する
  async function performSearch() {
    const searchText = document.getElementById('history-search').value.trim();
    const myToken = ++historySearchToken;

    if (!searchText) {
      historyItems = allHistoryItems;
      renderFilteredHistory();
      return;
    }

    let results;
    try {
      const response = await apiRequest(`/history?q=${encodeURIComponent(searchText)}`, {}, 3, 1000);
      const data = await response.json();
      results = data.items || [];
    } catch (e) {
      console.error('履歴検索に失敗:', e);
      if (myToken === historySearchToken) {
        showWarning(t('app.m054') + e.message);
      }
      return;
    }

    // 検索中に別の検索やクリアが割り込んでいたら、古い結果は反映しない
    if (myToken !== historySearchToken) return;
    historyItems = results;
    renderFilteredHistory();
  }

  function renderFilteredHistory() {
    const historyList = document.getElementById('history-list');
    const historyEmpty = document.getElementById('history-empty');

    const dateFrom = document.getElementById('history-date-from').value;
    const dateTo = document.getElementById('history-date-to').value;

    // テキスト検索は既にperformSearch()でサーバー側に適用済みのため、ここでは日付範囲のみ絞り込む
    let filtered = historyItems.filter(item => {
      const itemDate = new Date(item.created * 1000);
      if (dateFrom) {
        const from = new Date(dateFrom + 'T00:00:00');
        if (itemDate < from) return false;
      }
      if (dateTo) {
        const to = new Date(dateTo + 'T23:59:59');
        if (itemDate > to) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      historyList.innerHTML = '';
      historyEmpty.style.display = 'block';
      historyEmpty.querySelector('p').textContent = t('app.m055');
      selectedHistoryIds.clear();
      updateHistorySelectionUI();
      return;
    }

    historyEmpty.style.display = 'none';
    historyEmpty.querySelector('p').textContent = t('app.m056');
    historyList.innerHTML = '';

    // 再描画のたびに選択は解除する（絞り込み・再読み込みで見えなくなった行を誤って操作しないため）
    selectedHistoryIds.clear();

    filtered.forEach(item => {
      const div = document.createElement('div');
      div.className = 'history-item';
      const date = new Date(item.created * 1000);
      const dateStr = date.toLocaleDateString(currentLocale()) + ' ' + date.toLocaleTimeString(currentLocale());
      // 1行目はファイル名（「元ファイル名-H1タイトル」なのでタイトルを別の行に出すと重複する）、2行目は日時とサイズ
      div.innerHTML = `
        <div class="history-info">
          <input type="checkbox" class="history-check" aria-label="${t('app.history_select')}" data-id="${escapeAttr(item.id)}">
          <span class="history-icon">📄</span>
          <div>
            <div class="history-name">${escapeHtml(item.name)}</div>
            <div class="history-meta">${dateStr} - ${formatFileSize(item.size)}</div>
          </div>
        </div>
        <div class="history-actions-inline">
          <button class="btn btn-secondary btn-sm" data-action="view" data-id="${escapeAttr(item.id)}">${t('app.history_view')}</button>
        </div>
      `;
      historyList.appendChild(div);
    });

    historyList.querySelectorAll('.history-check').forEach(box => {
      box.addEventListener('change', () => {
        if (box.checked) selectedHistoryIds.add(box.dataset.id);
        else selectedHistoryIds.delete(box.dataset.id);
        updateHistorySelectionUI();
      });
    });

    historyList.querySelectorAll('[data-action="view"]').forEach(btn => {
      btn.addEventListener('click', () => {
        const item = historyItems.find(i => i.id === btn.dataset.id);
        if (item) showHistoryModal(item);
      });
    });

    updateHistorySelectionUI();
  }

  async function showHistoryModal(item) {
    const modal = document.getElementById('history-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');

    modalTitle.textContent = item.name;
    currentReportText = '';
    currentHistoryFileName = item.name;
    // 開くたびに設定の既定形式で選択状態を初期化する（モーダル内の変更は設定を書き換えない）。
    // 設定画面を開いていなくても最新の既定値を反映するため、ここで設定を取得し直す
    try {
      const cfgResp = await apiRequest('/config', {}, 2, 500);
      const cfg = await cfgResp.json();
      defaultDownloadFormat = cfg.download_format || 'txt';
    } catch (e) {
      console.warn('ダウンロード形式の既定値の取得に失敗:', e);
    }
    const dlRadio = document.querySelector(`input[name="dl-format"][value="${defaultDownloadFormat}"]`)
      || document.querySelector('input[name="dl-format"][value="txt"]');
    if (dlRadio) dlRadio.checked = true;
    modalBody.innerHTML = t('app.m057');
    modal.style.display = 'flex';

    // 一覧レスポンスは帯域節約のため本文を含まないため、
    // 詳細モーダルを開いたタイミングで本文を個別取得する
    try {
      const response = await apiRequest(`/history/${encodeURIComponent(item.name)}`, {}, 3, 1000);
      const detail = await response.json();
      // 取得中に別の項目のモーダルが開かれていたら、古い応答でそちらを上書きしない
      if (currentHistoryFileName !== item.name) return;
      currentReportText = detail.report || '';
      if (detail.report) {
        modalBody.innerHTML = `<div class="report-content markdown-body">${renderMarkdown(detail.report)}</div>`;
      } else {
        modalBody.innerHTML = t('app.m058');
      }
    } catch (e) {
      if (currentHistoryFileName !== item.name) return;
      modalBody.innerHTML = t('app.m059');
    }
  }

  document.getElementById('btn-copy-report').addEventListener('click', async () => {
    if (!currentReportText) {
      showWarning(t('app.m060'));
      return;
    }
    try {
      await navigator.clipboard.writeText(currentReportText);
      showSuccess(t('app.m061'));
    } catch (e) {
      const textarea = document.createElement('textarea');
      textarea.value = currentReportText;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      showSuccess(t('app.m061'));
    }
  });

  const downloadButton = document.getElementById('btn-download-report');
  downloadButton.addEventListener('click', async () => {
    if (!currentHistoryFileName) {
      showWarning(t('app.m062'));
      return;
    }
    // 変換中の二重クリックで同じ形式を2回生成しないよう、完了まで無効化する
    if (downloadButton.disabled) return;
    downloadButton.disabled = true;
    const format = (document.querySelector('input[name="dl-format"]:checked') || {}).value || defaultDownloadFormat;
    try {
      // apiRequest()は失敗時に原因を捨てて再試行するため、変換エラー(フォント欠落等)の
      // メッセージを出せるようfetchを直接使う
      const response = await fetch(
        `${API_BASE}/history/${encodeURIComponent(currentHistoryFileName)}/download?format=${encodeURIComponent(format)}`,
        { method: 'POST' },
      );
      const result = await response.json().catch(() => ({}));
      if (response.ok && result.status === 'ok') {
        const savedName = String(result.downloaded || '').split(/[\\/]/).pop() || currentHistoryFileName;
        showSuccess(t('app.m063', savedName));
      } else {
        showWarning(t('app.m064') + (result.detail ? `: ${result.detail}` : ''));
      }
    } catch (e) {
      showWarning(t('app.m065') + e.message);
    } finally {
      downloadButton.disabled = false;
    }
  });

  document.getElementById('modal-close').addEventListener('click', () => {
    document.getElementById('history-modal').style.display = 'none';
  });

  document.getElementById('history-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
      e.currentTarget.style.display = 'none';
    }
  });

  // ==================== 確認ダイアログ（はい / キャンセル） ====================
  // askDiscardSettings と同じ Promise 方式。二重に開かず、Escでキャンセル扱い
  function askConfirm({ title, message, okLabel = t('app.m066'), danger = true }) {
    return new Promise(resolve => {
      const modal = document.getElementById('confirm-modal');
      if (modal.style.display === 'flex') {
        resolve(false);
        return;
      }
      document.getElementById('confirm-title').textContent = title;
      document.getElementById('confirm-message').textContent = message;
      const ok = document.getElementById('btn-confirm-ok');
      const cancel = document.getElementById('btn-confirm-cancel');
      ok.textContent = okLabel;
      ok.classList.toggle('btn-danger', danger);
      ok.classList.toggle('btn-primary', !danger);
      const finish = (result) => {
        modal.style.display = 'none';
        ok.removeEventListener('click', onOk);
        cancel.removeEventListener('click', onCancel);
        resolve(result);
      };
      const onOk = () => finish(true);
      const onCancel = () => finish(false);
      ok.addEventListener('click', onOk);
      cancel.addEventListener('click', onCancel);
      modal.style.display = 'flex';
      cancel.focus();  // 誤操作を避けるため、既定のフォーカスは「キャンセル」
    });
  }

  document.getElementById('btn-clear-history').addEventListener('click', async () => {
    const ok = await askConfirm({
      title: t('app.m067'),
      message: t('app.m068'),
    });
    if (!ok) return;
    try {
      await apiRequest('/history', { method: 'DELETE' }, 3, 1000);
      loadHistory();
      showSuccess(t('app.m069'));
    } catch (e) {
      showWarning(t('app.m039') + e.message);
    }
  });

  // ==================== 履歴: チェックボックスで選んだ複数件の操作 ====================
  const selectedHistoryIds = new Set();

  function updateHistorySelectionUI() {
    const checks = [...document.querySelectorAll('#history-list .history-check')];
    const n = selectedHistoryIds.size;
    document.getElementById('btn-hide-selected').disabled = n === 0;
    document.getElementById('btn-delete-selected').disabled = n === 0;
    document.getElementById('history-selected-count').textContent = n ? t('app.m070', n) : '';
    const all = document.getElementById('history-select-all');
    all.checked = checks.length > 0 && n === checks.length;
    all.indeterminate = n > 0 && n < checks.length;
  }

  document.getElementById('history-select-all').addEventListener('change', (e) => {
    document.querySelectorAll('#history-list .history-check').forEach(box => {
      box.checked = e.target.checked;
      if (e.target.checked) selectedHistoryIds.add(box.dataset.id);
      else selectedHistoryIds.delete(box.dataset.id);
    });
    updateHistorySelectionUI();
  });

  async function bulkHistoryAction(kind) {
    const ids = [...selectedHistoryIds];
    if (ids.length === 0) return;
    const isHide = kind === 'hide';
    const ok = await askConfirm(isHide
      ? { title: t('app.m071'), message: t('app.m072', ids.length) }
      : { title: t('app.m073'),
          message: t('app.m074', ids.length) });
    if (!ok) return;
    try {
      const response = await apiRequest(isHide ? '/history/hide' : '/history/delete-files', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      }, 2, 500);
      const result = await response.json();
      const count = isHide ? result.hidden_count : result.deleted_count;
      showSuccess(isHide ? t('app.m075', count) : t('app.m076', count));
    } catch (e) {
      showWarning((isHide ? t('app.m071') : t('app.m073')) + t('app.m077') + e.message);
    }
    loadHistory();
  }

  document.getElementById('btn-hide-selected').addEventListener('click', () => bulkHistoryAction('hide'));
  document.getElementById('btn-delete-selected').addEventListener('click', () => bulkHistoryAction('delete'));

  // ==================== ユーティリティ ====================
  function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function showWarning(message) {
    const existing = document.querySelector('.toast-notification');
    if (existing) existing.remove();

    const div = document.createElement('div');
    div.className = 'toast-notification toast-warning';
    div.textContent = message;
    document.body.appendChild(div);
    setTimeout(() => {
      div.classList.add('toast-fade-out');
      div.addEventListener('animationend', () => div.remove());
    }, 5000);
  }

  function showSuccess(message) {
    const existing = document.querySelector('.toast-notification');
    if (existing) existing.remove();

    const div = document.createElement('div');
    div.className = 'toast-notification toast-success';
    div.textContent = message;
    document.body.appendChild(div);
    setTimeout(() => {
      div.classList.add('toast-fade-out');
      div.addEventListener('animationend', () => div.remove());
    }, 3000);
  }

  // ==================== 初期化 ====================
  async function loadPromptsForMain() {
    try {
      const response = await apiRequest('/config', {}, 3, 1000);
      const config = await response.json();
      savedPrompts = config.saved_prompts || {};
      builtinPromptNames = config.builtin_prompt_names || [];
      applyUiLanguage(config.ui_language);
      activePromptName = config.active_prompt_name || Object.keys(savedPrompts)[0] || '';
      renderPromptCards();
    } catch (e) {
      console.error('プロンプト一覧の読み込みに失敗:', e);
    }
  }

  // ==================== キーボードショートカット ====================
  document.addEventListener('keydown', (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    
    if (e.key === 'Escape') {
      e.preventDefault();
      const historyModal = document.getElementById('history-modal');
      if (historyModal && historyModal.style.display === 'flex') {
        historyModal.style.display = 'none';
        return;
      }
      const vibeFallbackModal = document.getElementById('vibe-fallback-modal');
      if (vibeFallbackModal && vibeFallbackModal.style.display === 'flex') {
        vibeFallbackModal.style.display = 'none';
        return;
      }
      const vibeDlModal = document.getElementById('vibe-model-download-modal');
      if (vibeDlModal && vibeDlModal.style.display === 'flex') {
        document.getElementById('btn-vibe-dl-cancel').click();
        return;
      }
      const resetModal = document.getElementById('reset-confirm-modal');
      if (resetModal && resetModal.style.display === 'flex') {
        resetModal.style.display = 'none';
        cleanupResetListeners();
        pendingResetTab = null;
        return;
      }
      const restoreModal = document.getElementById('restore-prompts-modal');
      if (restoreModal && restoreModal.style.display === 'flex') {
        document.getElementById('btn-restore-cancel').click();
        return;
      }
      const confirmModal = document.getElementById('confirm-modal');
      if (confirmModal && confirmModal.style.display === 'flex') {
        document.getElementById('btn-confirm-cancel').click();
        return;
      }
      const leaveModal = document.getElementById('settings-leave-modal');
      if (leaveModal && leaveModal.style.display === 'flex') {
        document.getElementById('btn-leave-cancel').click();
        return;
      }
      const processingNavModal = document.getElementById('processing-nav-confirm-modal');
      if (processingNavModal && processingNavModal.style.display === 'flex') {
        processingNavModal.style.display = 'none';
        pendingNavItem = null;
        return;
      }
      if (isProcessing) {
        cancelCurrentRun();
      }
      return;
    }
    
    if (ctrl && e.key === 'u') {
      e.preventDefault();
      const fileInputPage = document.getElementById('page-file-input');
      if (fileInputPage && fileInputPage.classList.contains('active')) {
        const fileInputView = document.getElementById('file-input-view');
        if (fileInputView && fileInputView.style.display !== 'none') {
          document.getElementById('file-input').click();
        }
      }
      return;
    }
    
    if (ctrl && e.key === 'Enter') {
      e.preventDefault();
      if (isProcessing) return;
      const textMode = document.getElementById('mode-text');
      if (textMode && textMode.classList.contains('active')) {
        const textInputArea = document.getElementById('text-input-area');
        if (textInputArea) {
          const text = textInputArea.value.trim();
          if (text) startTextProcessing(text);
        }
      } else if (selectedFiles && selectedFiles.length > 0) {
        startProcessing();
      }
      return;
    }
    
    if (ctrl && e.key === 's') {
      e.preventDefault();
      const settingsPage = document.getElementById('page-settings');
      if (settingsPage && settingsPage.classList.contains('active')) {
        // 表示中のタブを保存する（環境設定タブは即時反映のためフッターが無く何もしない）
        const footer = document.getElementById('settings-footer');
        if (footer && footer.style.display !== 'none') {
          document.getElementById('btn-footer-save').click();
        }
      }
      return;
    }
  });

  // ==================== デバウンス ====================
  // 履歴検索などの入力の間引きに使う
  const debounceTimers = {};
  function debounce(id, fn, delay = 300) {
    return function (...args) {
      if (debounceTimers[id]) clearTimeout(debounceTimers[id]);
      debounceTimers[id] = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  // ==================== デスクトップ通知 ====================
  function sendDesktopNotification(title, body, type = 'info') {
    if (!('Notification' in window)) return;
    
    if (Notification.permission === 'granted') {
      createNotification(title, body, type);
    } else if (Notification.permission !== 'denied') {
      Notification.requestPermission().then(permission => {
        if (permission === 'granted') {
          createNotification(title, body, type);
        }
      });
    }
  }

  function createNotification(title, body, type) {
    try {
      const notification = new Notification(title, {
        body: body,
        tag: 'voice2doc-' + type,
      });
      setTimeout(() => notification.close(), 5000);
      notification.onclick = () => {
        window.focus();
        notification.close();
      };
    } catch (e) {
      console.warn('通知の表示に失敗:', e);
    }
  }

  // ==================== リトライ ====================
  const btnRetry = document.getElementById('btn-retry');
  const btnNewProcessError = document.getElementById('btn-new-process-error');
  if (btnRetry) {
    btnRetry.addEventListener('click', async () => {
      if (lastFailedFiles.length === 0) return;
      document.getElementById('result-container').style.display = 'none';
      const failedNames = [...lastFailedFiles];
      lastFailedFiles = [];

      // リトライ: ファイル名だけを渡して再処理（ファイルは既に INPUT_DIR にある）
      isProcessing = true;
      const progressContainer = document.getElementById('progress-container');
      const progressBar = document.getElementById('progress-bar');
      const progressText = document.getElementById('progress-text');
      const progressStatus = document.getElementById('progress-status');
      const progressDetail = document.getElementById('progress-detail');
      const audioDuration = document.getElementById('audio-duration');
      const streamingOutput = document.getElementById('streaming-output');
      const streamingText = document.getElementById('streaming-text');
      const fileInputView = document.getElementById('file-input-view');
      const fileListContainer = document.getElementById('file-list-container');

      fileInputView.style.display = 'none';
      fileListContainer.style.display = 'none';
      progressContainer.style.display = 'block';
      progressContainer.classList.add('processing');
      progressBar.classList.add('animating');
      progressStatus.textContent = t('app.m078', failedNames.join(', '));
      progressDetail.textContent = '';

      let pollingInterval = null;
      function startPolling() {
        pollingInterval = setInterval(async () => {
          try {
            const resp = await fetch(`${API_BASE}/status`);
            if (!resp.ok) return;
            const st = await resp.json();
            if (!st.is_processing) { stopPolling(); return; }
            updateProgressUI(st, { progressStatus, progressDetail, audioDuration, streamingOutput, streamingText });
          } catch (e) {}
        }, 1500);
      }
      function stopPolling() {
        if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null; }
      }
      function resetToInput() {
        progressContainer.style.display = 'none';
        progressBar.style.width = '0%';
        progressBar.classList.remove('animating', 'completed');
        progressText.textContent = '0%';
        progressContainer.classList.remove('processing');
        streamingOutput.style.display = 'none';
        streamingText.textContent = '';
        showPromptSelector();
      }

      const run = beginRun(() => {
        stopPolling();
        isProcessing = false;
        resetToInput();
      });

      startPolling();

      try {
        run.processSent = true;
        const processResponse = await apiRequest('/process', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ files: failedNames }),
          signal: run.controller.signal,
        }, 3, 2000);
        const processResult = await processResponse.json();
        if (run.cancelled) return;
        if (processResult.status === 'cancelled') { finishCancelled(run); return; }

        stopPolling();
        progressBar.classList.remove('animating');
        progressBar.classList.add('completed');
        progressBar.style.width = '100%';
        progressText.textContent = '100%';
        progressStatus.classList.remove('blinking');
        progressContainer.classList.remove('processing');
        progressStatus.innerHTML = t('app.m020');
        endRun(run);

        setTimeout(() => {
          if (processResult && processResult.results) {
            showResults(processResult.results);
          }
          isProcessing = false;
          resetToInput();
        }, 3000);
      } catch (error) {
        if (run.cancelled) return;
        endRun(run);
        stopPolling();
        showWarning(t('app.m079') + error.message);
        sendDesktopNotification(t('app.m021'), t('app.m080'), 'error');
        isProcessing = false;
        resetToInput();
      }
    });
  }
  if (btnNewProcessError) {
    btnNewProcessError.addEventListener('click', () => {
      document.getElementById('result-container').style.display = 'none';
      lastFailedFiles = [];
    });
  }

  // ==================== 履歴フィルタ ====================
  const historySearch = document.getElementById('history-search');
  const historyDateFrom = document.getElementById('history-date-from');
  const historyDateTo = document.getElementById('history-date-to');
  const btnClearFilters = document.getElementById('btn-clear-filters');
  
  if (historySearch) historySearch.addEventListener('input', debounce('history-search', performSearch, 300));
  if (historyDateFrom) historyDateFrom.addEventListener('change', renderFilteredHistory);
  if (historyDateTo) historyDateTo.addEventListener('change', renderFilteredHistory);
  if (btnClearFilters) {
    btnClearFilters.addEventListener('click', () => {
      historySearch.value = '';
      historyDateFrom.value = '';
      historyDateTo.value = '';
      historySearchToken++; // 進行中のperformSearch()の結果が後から反映されないようにする
      historyItems = allHistoryItems;
      renderFilteredHistory();
    });
  }

  async function init() {
    console.log('Voice2Doc 起動中...');
    const connected = await waitForApi();
    if (connected) {
      await loadPromptsForMain();
      refreshDashboard();  // 起動直後の開始画面にも最近の結果・警告バナーを出す
      refreshCurrentModels();
      console.log('Voice2Doc 初期化完了');

      // 起動後にダウンロード中かどうかを確認し、オーバーレイを表示
      checkDownloadStatus();
    } else {
      console.error('APIに接続できません');
      showWarning(t('app.m081'));
    }
  }

  // ダウンロード中ステータスを確認し、必要に応じてオーバーレイを表示
  async function checkDownloadStatus() {
    let pollCount = 0;
    const maxPolls = 300; // 10分 (2秒間隔 x 300回)
    try {
      const resp = await fetch(`${API_BASE}/status`);
      if (!resp.ok) return;
      const st = await resp.json();
      maybeShowVibeFallbackDialog(st);
      if (st.is_downloading) {
        showDownloadOverlay(st.message);
        // ポーリングでダウンロード完了を監視
        const pollInterval = setInterval(async () => {
          pollCount++;
          if (pollCount >= maxPolls) {
            clearInterval(pollInterval);
            hideDownloadOverlay();
            return;
          }
          try {
            const pollResp = await fetch(`${API_BASE}/status`);
            if (!pollResp.ok) return;
            const pollSt = await pollResp.json();
            if (!pollSt.is_downloading) {
              clearInterval(pollInterval);
              hideDownloadOverlay();
            }
          } catch (e) {}
        }, 2000);
      }
    } catch (e) {
      console.warn('ダウンロード状態の確認に失敗:', e);
    }
  }

  // ダウンロード中オーバーレイを表示
  function showDownloadOverlay(message) {
    let overlay = document.getElementById('download-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'download-overlay';
      overlay.className = 'startup-overlay';
      overlay.innerHTML = `
        <div class="startup-content">
          <div class="startup-spinner"></div>
          <div class="startup-text">${t('app.download_title')}</div>
          <div class="startup-detail">${escapeHtml(message)}</div>
          <div class="startup-detail" style="margin-top:8px;font-size:0.85em;color:#888;">${t('app.download_note')}</div>
        </div>
      `;
      document.body.appendChild(overlay);
    } else {
      overlay.classList.remove('hidden');
      const detail = overlay.querySelector('.startup-detail');
      if (detail) detail.textContent = message;
    }
  }

  // ダウンロード中オーバーレイを非表示
  function hideDownloadOverlay() {
    const overlay = document.getElementById('download-overlay');
    if (overlay) {
      overlay.classList.add('hidden');
    }
  }

  init();
});

/* Voice2Doc 初回起動ウィザード ロジック */
(function () {
  'use strict';

  // 画面を配信したサーバーと同じ場所へ接続する（既定のポートが使用中で別ポートへ移った場合にも動く）
  const API_BASE = '';
  let currentPage = 1;
  const totalPages = 5;
  // 前提条件（VC++・WebView2・FFmpeg・vibe-server）が揃ったか。揃うまで2ページ目の「次へ」は押せない
  let prereqAllReady = false;
  let skipPrereqCheck = false;  // 「確認せずに進む」を選んだ（上級者向け）
  let activateTimer = null;  // Vibe のモデルのダウンロード待ちの再確認タイマー
  let activateAttempts = 0;
  const ACTIVATE_RETRY_MS = 5000;
  const ACTIVATE_MAX_ATTEMPTS = 360;  // 約30分

  // 選択状態
  const state = {
    uiLanguage: detectOsLanguage(),  // 初回のみOSの言語で決める（日本語以外は英語）
    whisperBackend: 'vibe',
    whisperModel: 'large-v3-turbo',  // vibe-server のモデル名（設定の vibe_model）
    whisperLanguage: 'ja',
  };
  state.whisperLanguage = state.uiLanguage === 'ja' ? 'ja' : 'auto';

  // 初期化
  document.addEventListener('DOMContentLoaded', function () {
    setLanguage(state.uiLanguage);
    var select = document.getElementById('wizard-language');
    select.value = state.uiLanguage;
    select.addEventListener('change', function () {
      state.uiLanguage = select.value;
      state.whisperLanguage = state.uiLanguage === 'ja' ? 'ja' : 'auto';  // 音声認識の言語も既定の組み合わせに合わせる
      setLanguage(state.uiLanguage);
      updateButtons();
      updateSummary();
    });
    updateStepDots();
    setupEventListeners();
    signalUiReady();
  });

  // 画面が表示できたことをバックエンドへ知らせる（白い画面の検知用）。/health に自分のサーバーの識別子がある
  function signalUiReady(attemptsLeft) {
    if (attemptsLeft === undefined) attemptsLeft = 5;
    window.__v2dBooted = true;
    var early = document.querySelector('[data-early-error]');  // 起動前の一時的なエラーで出た案内を、起動できたら消す
    if (early) early.remove();
    fetch(API_BASE + '/health')
      .then(function (res) {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then(function (data) {
        if (!data.instance_id) return;
        return fetch(API_BASE + '/ui-ready', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instance_id: data.instance_id }),
        }).then(function (res) { if (!res.ok) throw new Error(String(res.status)); });
      })
      .catch(function () {
        // 一時的な失敗でも合図が落ちないよう、数回やり直す
        if (attemptsLeft > 0) setTimeout(function () { signalUiReady(attemptsLeft - 1); }, 1000);
      });
  }

  function setupEventListeners() {
    // 前へ / 次へボタン
    document.getElementById('btn-prev').addEventListener('click', goPrev);
    document.getElementById('btn-next').addEventListener('click', goNext);
    document.getElementById('btn-skip').addEventListener('click', skipWizard);
    document.getElementById('btn-start').addEventListener('click', completeWizard);
    document.getElementById('btn-vibe-recheck').addEventListener('click', function () {
      activateAttempts = 0;
      runPrereqChecks(true);
    });
    document.getElementById('btn-vibe-pick').addEventListener('click', pickVibeServer);
    document.getElementById('btn-skip-check').addEventListener('click', function (e) {
      e.preventDefault();
      skipPrereqCheck = true;
      updateButtons();
      goNext();
    });

    // 外部リンク（data-external）は既定のブラウザで開く（バックエンドが https の決まったサイトだけ開く）
    document.addEventListener('click', function (e) {
      var link = e.target.closest('a[data-external]');
      if (!link) return;
      e.preventDefault();
      fetch(API_BASE + '/open-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: link.href }),
      }).catch(function () {});
    });

    // Whisper モデル選択
    document.querySelectorAll('.whisper-model-option').forEach(function (el) {
      el.addEventListener('click', function () {
        selectWhisperModel(el.dataset.value);
      });
    });
  }

  // ====== ページ遷移 ======

  function showPage(page) {
    document.querySelectorAll('.wizard-page').forEach(function (p) {
      p.classList.remove('active');
    });
    var target = document.getElementById('page-' + page);
    if (target) {
      target.classList.add('active');
    }
    currentPage = page;
    if (page !== 2) clearTimeout(activateTimer);
    updateStepDots();
    updateButtons();
    updateSummary();

    // ページ2に遷移した場合は前提条件チェックを実行
    if (page === 2) {
      activateAttempts = 0;
      runPrereqChecks();
    }
  }

  function nextBlocked() {
    return currentPage === 2 && !prereqAllReady && !skipPrereqCheck;
  }

  function goNext() {
    if (nextBlocked()) return;
    if (currentPage === 3) {
      // 選んだモデルがこの PC に無ければ、ダウンロードするかを確認してから進む（キャンセルならこのページに残る）
      confirmModelDownload(state.whisperModel).then(function (ok) {
        if (ok) showPage(currentPage + 1);
      });
      return;
    }
    if (currentPage < totalPages) {
      showPage(currentPage + 1);
    }
  }

  // 未導入のモデルを選んだときのダウンロード確認。進んでよければ true（ダウンロードの開始も済ませる）
  var modelDlBusy = false;
  function confirmModelDownload(model) {
    if (modelDlBusy) return Promise.resolve(false);
    modelDlBusy = true;
    var release = function (v) { modelDlBusy = false; return v; };
    return fetch(API_BASE + '/vibe/model/status?model=' + encodeURIComponent(model))
      .then(function (res) {
        if (!res.ok) throw new Error('status ' + res.status);
        return res.json();
      })
      .then(function (st) {
        if (st.status === 'ready' || st.app_downloading) return release(true);
        var modal = document.getElementById('model-download-modal');
        var detail = document.getElementById('model-download-detail');
        detail.textContent = st.size_mb
          ? t('app.vibe_dl_detail', st.model, st.size_mb)
          : t('app.vibe_dl_detail_nosize', st.model);
        modal.style.display = 'flex';
        return new Promise(function (resolve) {
          var ok = document.getElementById('btn-model-dl-download');
          var cancel = document.getElementById('btn-model-dl-cancel');
          function done(v) {
            ok.removeEventListener('click', onOk);
            cancel.removeEventListener('click', onCancel);
            document.removeEventListener('keydown', onKey);
            modal.style.display = 'none';
            resolve(v);
          }
          function onOk() { done(true); }
          function onCancel() { done(false); }
          function onKey(e) { if (e.key === 'Escape') done(false); }
          ok.addEventListener('click', onOk);
          cancel.addEventListener('click', onCancel);
          document.addEventListener('keydown', onKey);
        }).then(function (choice) {
          if (!choice) return release(false);
          return fetch(API_BASE + '/vibe/model/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: st.model }),
          }).then(function (res) {
            if (!res.ok) throw new Error('download ' + res.status);
            return release(true);
          });
        });
      })
      .catch(function (err) {
        release(false);
        // このページにはエラー表示欄が無いので、ダイアログで知らせる
        window.alert(t('app.vibe_dl_failed') + err.message);
        return false;
      });
  }

  function goPrev() {
    if (currentPage > 1) {
      showPage(currentPage - 1);
    }
  }

  function updateStepDots() {
    document.querySelectorAll('.step-dot').forEach(function (dot, i) {
      dot.classList.remove('active', 'completed');
      if (i + 1 === currentPage) {
        dot.classList.add('active');
      } else if (i + 1 < currentPage) {
        dot.classList.add('completed');
      }
    });
  }

  function updateButtons() {
    var btnPrev = document.getElementById('btn-prev');
    var btnNext = document.getElementById('btn-next');
    var btnSkip = document.getElementById('btn-skip');
    var btnStart = document.getElementById('btn-start');
    var footerRight = document.querySelector('.wizard-footer-right');

    btnPrev.style.display = currentPage > 1 ? 'inline-block' : 'none';
    btnNext.disabled = nextBlocked();
    var hint = document.getElementById('next-blocked-hint');
    if (hint) hint.style.visibility = nextBlocked() ? 'visible' : 'hidden';

    if (currentPage === totalPages) {
      // 完了ページ
      btnNext.style.display = 'none';
      btnSkip.style.display = 'none';
      btnStart.style.display = 'inline-block';
    } else {
      btnNext.style.display = 'inline-block';
      btnSkip.style.display = currentPage === 1 ? 'inline-block' : 'none';
      btnStart.style.display = 'none';
    }
  }

  // ====== Whisper モデル選択 ======

  function selectWhisperModel(value) {
    state.whisperModel = value;
    document.querySelectorAll('.whisper-model-option').forEach(function (el) {
      el.classList.toggle('selected', el.dataset.value === value);
    });
  }

  // ====== 前提条件チェック ======

  // explicit: 「再確認」を押したとき。見つからなくても /vibe/activate を呼び、探した場所の一覧を表示する
  function runPrereqChecks(explicit) {
    // /prereq-check で setup_checker.run_all_checks() の実際の判定結果を取得する
    fetch(API_BASE + '/prereq-check')
      .then(function (res) {
        if (!res.ok) throw new Error('prereq-check failed: ' + res.status);
        return res.json();
      })
      .then(function (data) {
        setCheckResult('check-vc', !!data.vc_runtime, data.vc_runtime ? t('wz.m001') : t('wz.m002'));
        setCheckResult('check-webview', !!data.webview2, data.webview2 ? t('wz.m001') : t('wz.m002'));
        setCheckResult('check-ffmpeg', !!data.ffmpeg, data.ffmpeg ? t('wz.m003') : t('wz.m004'));
        setCheckResult('check-vibe', !!data.vibe, data.vibe ? t('wz.m005') : t('wz.m004'));
        // vibe-server.exe が無ければ Vibe の案内とダウンロードページへのリンクを出す
        document.getElementById('vibe-guide').style.display = data.vibe ? 'none' : 'block';
        prereqAllReady = !!(data.vc_runtime && data.webview2 && data.ffmpeg && data.vibe);
        updateButtons();
        // Vibe が見つかったら、アプリを再起動しなくても使えるようにバックエンドへ知らせる
        if (data.vibe || explicit === true) activateVibe();
      })
      .catch(function () {
        prereqAllReady = false;
        updateButtons();
        // サーバーがまだ起動していない場合は「確認中」のまま次のチェックを待つ
        setCheckResult('check-vc', false, t('wz.m006'));
        setCheckResult('check-webview', false, t('wz.m006'));
        setCheckResult('check-ffmpeg', false, t('wz.m006'));
        setCheckResult('check-vibe', false, t('wz.m006'));
      });
  }

  // ====== Vibe の有効化（再起動なし）======

  function setGuideStatus(text, isError) {
    var el = document.getElementById('vibe-guide-status');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('error', !!isError);
    el.style.display = text ? 'block' : 'none';
  }

  function showSearched(list) {
    var box = document.getElementById('vibe-guide-searched');
    var ul = document.getElementById('vibe-guide-searched-list');
    ul.textContent = '';
    (list || []).forEach(function (item) {
      var li = document.createElement('li');
      li.textContent = item;
      ul.appendChild(li);
    });
    box.style.display = list && list.length ? 'block' : 'none';
  }

  // POST /vibe/activate を呼ぶ。path があれば手動指定の場所として保存される。結果の status を返す
  function activateVibe(path) {
    clearTimeout(activateTimer);
    var body = path ? { path: path } : {};
    return fetch(API_BASE + '/vibe/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('vibe/activate failed: ' + res.status);
        return res.json();
      })
      .then(function (r) {
        showSearched(r.status === 'not_found' ? r.searched : []);
        if (r.status === 'ok') {
          setGuideStatus('', false);
        } else if (r.status === 'not_found') {
          document.getElementById('vibe-guide').style.display = 'block';
          // 手動で指定した場所が違うときだけ理由を出す（それ以外は案内の見出し・手順が出る）
          setGuideStatus(path ? r.message : '', !!path);
        } else if (r.status === 'model_downloading' || r.status === 'starting') {
          // モデルのダウンロードが終わるまで、このページにいる間は自動で確認を続ける（Vibe は見つかっているので導入の案内は出さない）
          setGuideStatus(t(r.status === 'starting' ? 'wiz.vibe_starting' : 'wiz.vibe_downloading'), false);
          activateAttempts += 1;
          if (currentPage === 2 && activateAttempts < ACTIVATE_MAX_ATTEMPTS) {
            activateTimer = setTimeout(function () { activateVibe(); }, ACTIVATE_RETRY_MS);
          }
        } else {
          setGuideStatus(t('wiz.vibe_failed'), true);
        }
        return r.status;
      })
      .catch(function () { return 'error'; });
  }

  // 「vibe-server.exe の場所を指定する」: ファイル選択（無ければ入力）→ 保存して有効化
  function pickVibeServer() {
    var btn = document.getElementById('btn-vibe-pick');
    btn.disabled = true;
    fetch(API_BASE + '/dialog/pick', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'exe' }),
    })
      .then(function (res) { return res.json(); })
      .then(function (r) {
        if (r.status === 'ok') return r.path;
        if (r.status === 'error') return window.prompt(t('wiz.vibe_pick'), '');  // ウィンドウが無いときは手入力
        return null;
      })
      .catch(function () { return window.prompt(t('wiz.vibe_pick'), ''); })
      .then(function (path) {
        if (!path) return;
        setGuideStatus(t('wiz.vibe_checking'), false);
        return activateVibe(path).then(function (status) {
          if (status === 'ok') setGuideStatus(t('wiz.vibe_activated'), false);
          runPrereqChecks();
        });
      })
      .then(function () { btn.disabled = false; }, function () { btn.disabled = false; });
  }

  function setCheckResult(elementId, ok, detail) {
    var icon = document.getElementById(elementId);
    if (icon) {
      icon.textContent = ok ? '✅' : '❌';
      var detailEl = icon.closest('.check-item').querySelector('.check-detail');
      if (detailEl && detail) {
        detailEl.textContent = detail;
      }
    }
  }

  function updateSummary() {
    if (currentPage !== totalPages) return;

    var summary = document.getElementById('summary-content');
    if (!summary) return;

    var whisperLabel = t('wz.m007');

    summary.innerHTML =
      t('wz.m008') +
      t('wz.m009') +
      t('wz.m010') +
      t('wz.m011');
    var values = summary.querySelectorAll('.summary-value');
    values[0].textContent = whisperLabel;
    values[1].textContent = state.whisperModel === 'large-v3-turbo' ? t('wiz.selection_title') : state.whisperModel;
    values[2].textContent = t(state.whisperLanguage === 'auto' ? 'wz.lang_auto' : 'wz.lang_ja');
  }

  // ====== 設定保存 ======

  function completeWizard() {
    var btnStart = document.getElementById('btn-start');
    btnStart.disabled = true;
    btnStart.textContent = t('wz.m012');

    var payload = {
      ui_language: state.uiLanguage,
      whisper_backend: state.whisperBackend,
      vibe_model: state.whisperModel,
      // Python 版（openai-whisper。vibe-server が使えないときの予備）のモデル名。large-v3-turbo は turbo
      whisper_model: state.whisperModel === 'large-v3-turbo' ? 'turbo' : state.whisperModel,
      whisper_language: state.whisperLanguage,
    };

    fetch(API_BASE + '/wizard/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.status === 'ok') {
          // ウィザード完了 - メインアプリを表示するようリロード
          window.location.href = API_BASE + '/';
        } else {
          showError(data.message || t('wz.m013'));
          btnStart.disabled = false;
          btnStart.textContent = t('wz.m014');
        }
      })
      .catch(function (err) {
        showError(t('wz.m015') + err.message);
        btnStart.disabled = false;
        btnStart.textContent = t('wz.m014');
      });
  }

  function skipWizard() {
    // デフォルト設定で保存してスキップ
    completeWizard();
  }

  // ====== エラー表示 ======

  function showError(msg) {
    var el = document.getElementById('prereq-error');
    if (el) {
      el.textContent = msg;
      el.classList.add('visible');
      setTimeout(function () {
        el.classList.remove('visible');
      }, 5000);
    }
  }
})();

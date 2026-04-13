/* mode-upgrade.js — 자동개발 탭 UI
 *
 * 서브탭 2개:
 *   1) 최초개발 (0→1): 새 앱을 처음부터 자율 개발
 *   2) 강화소: 기존 앱 폴더를 업그레이드
 *
 * WebSocket /ws/upgrade 연결 (두 서브탭 공유)
 */
var UpgradeManager = (function () {
  'use strict';

  var _ws = null;
  var _container = null;
  var _running = false;

  // 서브탭 상태
  var _subMode = 'initial';  // 'initial' | 'upgrade'

  // 강화소(업그레이드) 상태
  var _state = 'form';   // 'form' | 'analyzing' | 'questions' | 'developing' | 'done'
  var _folderPath = '';
  var _task = '';
  var _sessionId = '';
  var _backupPath = '';
  var _analysis = null;
  var _lastToolLabel = '';

  // 최초개발 상태
  var _devTask = '';
  var _devSessionId = '';
  var _devQuestions = '';
  var _devPaused = false;

  function _clear(el) {
    while (el && el.firstChild) el.removeChild(el.firstChild);
  }

  function mountInShell(container) {
    _container = container;
    _render();
  }

  function _render() {
    if (!_container) return;
    // 실행 중인 화면 보존 (탭 왕복 시 리셋 방지)
    if (_running && _subMode === 'initial' && document.getElementById('dev-progress')) return;
    if (_running && _subMode === 'upgrade' && _state === 'developing' && document.getElementById('upgrade-progress')) return;
    _clear(_container);

    // 서브탭 스위처
    var tabSwitcher = document.createElement('div');
    tabSwitcher.className = 'ot-tab-switcher';

    var initialBtn = document.createElement('button');
    initialBtn.textContent = '최초개발';
    initialBtn.className = 'ot-tab-btn' + (_subMode === 'initial' ? ' active' : '');
    initialBtn.onclick = function () { _subMode = 'initial'; _render(); };
    tabSwitcher.appendChild(initialBtn);

    var upgradeBtn = document.createElement('button');
    upgradeBtn.textContent = '강화소';
    upgradeBtn.className = 'ot-tab-btn' + (_subMode === 'upgrade' ? ' active' : '');
    upgradeBtn.onclick = function () { _subMode = 'upgrade'; _render(); };
    tabSwitcher.appendChild(upgradeBtn);

    _container.appendChild(tabSwitcher);

    if (_subMode === 'initial') {
      _renderInitialForm();
    } else {
      _renderUpgradeForm();
    }
  }

  // ──────────────────────────────────────────────
  // 강화소 (기존 앱 업그레이드)
  // ──────────────────────────────────────────────
  function _renderUpgradeForm() {

    var form = document.createElement('div');
    form.className = 'ot-form';

    var title = document.createElement('h2');
    title.className = 'ot-title';
    title.textContent = '강화소';
    form.appendChild(title);

    var subtitle = document.createElement('p');
    subtitle.className = 'ot-subtitle';
    subtitle.textContent = '이미 있는 앱을 업그레이드합니다. 폴더와 지시사항을 입력하면, AI가 먼저 앱을 파악한 뒤 필요한 질문을 되묻고 자동으로 개발을 진행합니다.';
    form.appendChild(subtitle);

    // ── 폴더 경로 ──
    var folderLabel = document.createElement('label');
    folderLabel.className = 'ot-label';
    folderLabel.textContent = '대상 앱 폴더';
    form.appendChild(folderLabel);

    var folderHint = document.createElement('div');
    folderHint.className = 'st-cron-help';
    folderHint.textContent = '💡 Finder에서 폴더를 여기로 끌어 놓으면 경로가 자동으로 입력됩니다.';
    form.appendChild(folderHint);

    var folderInput = document.createElement('input');
    folderInput.type = 'text';
    folderInput.className = 'ot-input';
    folderInput.id = 'upgrade-folder-path';
    folderInput.placeholder = '예: /Users/me/projects/my-todo-app';
    folderInput.value = _folderPath;
    form.appendChild(folderInput);

    folderInput.addEventListener('dragover', function (e) {
      e.preventDefault();
      folderInput.classList.add('upgrade-drag-over');
    });
    folderInput.addEventListener('dragleave', function () {
      folderInput.classList.remove('upgrade-drag-over');
    });
    folderInput.addEventListener('drop', function (e) {
      e.preventDefault();
      folderInput.classList.remove('upgrade-drag-over');
      var files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length > 0) {
        var first = files[0];
        var path = first.path || first.webkitRelativePath || first.name;
        if (path) folderInput.value = path;
      }
    });

    // ── 지시사항 ──
    var taskLabel = document.createElement('label');
    taskLabel.className = 'ot-label';
    taskLabel.textContent = '업그레이드 지시사항';
    form.appendChild(taskLabel);

    var taskInput = document.createElement('textarea');
    taskInput.className = 'dev-task-input';
    taskInput.id = 'upgrade-task';
    taskInput.placeholder = '예: 메인 화면에 다크모드 토글 버튼 추가. 설정은 localStorage에 저장해서 다음 실행 시에도 유지되게.';
    taskInput.rows = 5;
    taskInput.value = _task;
    form.appendChild(taskInput);

    // ── 시작 버튼 ──
    var startBtn = document.createElement('button');
    startBtn.className = 'ot-start-btn';
    startBtn.textContent = '분석 시작';
    startBtn.onclick = function () {
      var folder = folderInput.value.trim();
      var task = taskInput.value.trim();
      if (!folder) { alert('대상 앱 폴더 경로를 입력해주세요.'); return; }
      if (!task) { alert('업그레이드 지시사항을 입력해주세요.'); return; }
      _folderPath = folder;
      _task = task;
      _startAnalyze();
    };
    form.appendChild(startBtn);

    _container.appendChild(form);

    var progress = document.createElement('div');
    progress.id = 'upgrade-analyze-panel';
    progress.style.display = 'none';
    _container.appendChild(progress);
  }

  function _startAnalyze() {
    _state = 'analyzing';
    _running = true;
    _connect();

    var startBtn = _container.querySelector('.ot-start-btn');
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = '분석 중...'; }

    var panel = document.getElementById('upgrade-analyze-panel');
    panel.style.display = '';
    _clear(panel);

    var statusText = document.createElement('div');
    statusText.id = 'upgrade-status';
    statusText.className = 'upgrade-status';
    statusText.textContent = '백업 생성 + 앱 분석 중...';
    panel.appendChild(statusText);

    var activityLine = document.createElement('div');
    activityLine.id = 'upgrade-activity';
    activityLine.className = 'upgrade-activity';
    panel.appendChild(activityLine);

    var retries = 0;
    var send = function () {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify({
          type: 'start_upgrade_analyze',
          data: { folder_path: _folderPath, task: _task },
        }));
      } else if (retries < 50) {
        retries++;
        setTimeout(send, 100);
      }
    };
    send();
  }

  function _connect() {
    if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/ws/upgrade';
    _ws = new WebSocket(url);

    _ws.onmessage = function (e) {
      try { _handleMessage(JSON.parse(e.data)); } catch (err) {}
    };
    _ws.onclose = function () { _ws = null; };
  }

  function _handleMessage(msg) {
    var type = msg.type;
    var data = msg.data || {};

    // ── 강화소 흐름 ──
    if (type === 'upgrade_progress') {
      _handleProgress(data);
    } else if (type === 'upgrade_activity') {
      _updateActivity(data);
    } else if (type === 'upgrade_analyze_result') {
      _sessionId = data.session_id || '';
      _backupPath = data.backup_path || '';
      _analysis = data.analysis || {};
      _folderPath = data.folder_path || _folderPath;
      _showAnalysisAndQuestions();
    } else if (type === 'upgrade_dev_started') {
      _addLog('업그레이드 시작');
    } else if (type === 'upgrade_stopped') {
      _running = false;
      _addLog('중단되었습니다.');
    }
    // ── 최초개발 흐름 ──
    else if (type === 'dev_clarify_questions') {
      _showDevQuestions(data.questions, data.session_id);
    } else if (type === 'dev_started') {
      _addDevLog('개발이 시작되었습니다', 'session_start');
    } else if (type === 'dev_progress') {
      _handleDevProgress(data);
    } else if (type === 'overtime_activity') {
      // 최초개발 세션 중일 때만 dev 로그에 도구 상태 반영
      if (document.getElementById('dev-log')) {
        var label = data.label || data.tool || '';
        var count = data.count || 0;
        _updateDevToolStatus(label, count);
      }
    } else if (type === 'overtime_stopped') {
      _running = false;
      _addDevLog('중단되었습니다', 'rate_limited');
    }
    // ── 공통 ──
    else if (type === 'error') {
      _running = false;
      alert('오류: ' + (data.message || '알 수 없는 오류'));
      if (_subMode === 'initial') {
        _devPaused = false;
        _render();
      } else {
        _state = 'form';
        _render();
      }
    }
  }

  function _handleProgress(data) {
    var phase = data.phase;
    var action = data.action;
    var msg = data.message || '';

    if (_state === 'analyzing' || _state === 'questions') {
      // 분석 단계: 기존 간단 패널 갱신 (사용자 요청대로 그대로 유지)
      var statusEl = document.getElementById('upgrade-status');
      if (statusEl && msg) statusEl.textContent = msg;
      return;
    }

    // developing / done: Phase bar + 로그 방식 (야근팀 dev 스타일)
    _updatePhaseBar(phase, action, data);

    if (msg) _addLog(msg, action);

    if (phase === 'report' && action === 'complete') {
      _state = 'done';
      _running = false;
      _showCompletion(data);
    }
  }

  function _updatePhaseBar(phase, action, data) {
    var phaseEl = document.getElementById('upgrade-phase-' + phase);
    if (!phaseEl) return;

    var baseLabel = phaseEl.dataset.baseLabel || phaseEl.textContent;
    phaseEl.dataset.baseLabel = baseLabel;

    if (action === 'complete') {
      phaseEl.classList.add('done');
      phaseEl.classList.remove('active');
      phaseEl.textContent = '✓ ' + baseLabel;
      var conn = document.getElementById('upgrade-conn-' + phase);
      if (conn) conn.classList.add('done');
      return;
    }

    if (action === 'session_start' && data.session_number) {
      phaseEl.classList.add('active');
      phaseEl.textContent = '● ' + baseLabel + ' (세션 #' + data.session_number + ')';
      return;
    }

    if (action === 'handoff') {
      // 세션 전환 — active 유지, Phase bar 텍스트는 session_start 에서 갱신됨
      return;
    }

    // 일반 진행 상태 — active 상태 표시
    phaseEl.classList.add('active');
    if (!phaseEl.textContent.startsWith('●') && !phaseEl.classList.contains('done')) {
      phaseEl.textContent = '● ' + baseLabel;
    }
  }

  function _updateActivity(data) {
    var label = data.label || data.tool || '';
    var count = data.count || 0;

    // 분석 단계: 간단한 한 줄 인디케이터 (기존 그대로)
    if (_state === 'analyzing' || _state === 'questions') {
      var el = document.getElementById('upgrade-activity');
      if (el) el.textContent = '🔧 ' + label + ' × ' + count;
      return;
    }

    // 개발 단계: 야근팀 dev 스타일 — 같은 도구 연속이면 마지막 줄 카운트만 갱신
    if (_state !== 'developing') return;
    var logArea = document.getElementById('upgrade-log');
    if (!logArea) return;

    if (label === _lastToolLabel) {
      var lastEl = document.getElementById('upgrade-tool-last');
      if (lastEl) {
        lastEl.textContent = '🔧 ' + label + ' (도구 사용 ' + count + '회)';
        logArea.scrollTop = logArea.scrollHeight;
        return;
      }
    }

    var prev = document.getElementById('upgrade-tool-last');
    if (prev) prev.removeAttribute('id');

    var newEl = document.createElement('div');
    newEl.id = 'upgrade-tool-last';
    newEl.style.cssText = 'padding:4px 0;font-size:12px;color:var(--blue,#60a5fa);border-bottom:1px solid var(--border,rgba(255,255,255,0.08));';
    newEl.textContent = '🔧 ' + label + ' (도구 사용 ' + count + '회)';
    logArea.appendChild(newEl);
    logArea.scrollTop = logArea.scrollHeight;
    _lastToolLabel = label;
  }

  function _showAnalysisAndQuestions() {
    _state = 'questions';
    _clear(_container);

    var panel = document.createElement('div');
    panel.className = 'ot-form';

    var title = document.createElement('h2');
    title.className = 'ot-title';
    title.textContent = '앱 분석 결과';
    panel.appendChild(title);

    var summaryCard = document.createElement('div');
    summaryCard.className = 'upgrade-summary-card';

    var summary = document.createElement('div');
    summary.className = 'upgrade-summary-text';
    summary.textContent = _analysis.summary || '앱 요약을 파악할 수 없었습니다.';
    summaryCard.appendChild(summary);

    if (_analysis.stack && _analysis.stack.length > 0) {
      var stackRow = document.createElement('div');
      stackRow.className = 'upgrade-chip-row';
      _analysis.stack.forEach(function (s) {
        var chip = document.createElement('span');
        chip.className = 'upgrade-chip';
        chip.textContent = s;
        stackRow.appendChild(chip);
      });
      summaryCard.appendChild(stackRow);
    }

    if (_backupPath) {
      var backupNote = document.createElement('div');
      backupNote.className = 'upgrade-backup-note';
      backupNote.textContent = '🛟 백업 완료: ' + _backupPath;
      summaryCard.appendChild(backupNote);
    }

    panel.appendChild(summaryCard);

    var qTitle = document.createElement('h3');
    qTitle.className = 'upgrade-q-title';
    qTitle.textContent = '몇 가지만 확인할게요';
    panel.appendChild(qTitle);

    var qList = document.createElement('ol');
    qList.className = 'upgrade-q-list';
    (_analysis.questions || []).forEach(function (q) {
      var li = document.createElement('li');
      li.textContent = q;
      qList.appendChild(li);
    });
    panel.appendChild(qList);

    if (_analysis.concerns && _analysis.concerns.length > 0) {
      var concernTitle = document.createElement('div');
      concernTitle.className = 'upgrade-concern-title';
      concernTitle.textContent = '⚠️ 주의할 점';
      panel.appendChild(concernTitle);

      var concernList = document.createElement('ul');
      concernList.className = 'upgrade-concern-list';
      _analysis.concerns.forEach(function (c) {
        var li = document.createElement('li');
        li.textContent = c;
        concernList.appendChild(li);
      });
      panel.appendChild(concernList);
    }

    var ansLabel = document.createElement('label');
    ansLabel.className = 'ot-label';
    ansLabel.textContent = '답변';
    panel.appendChild(ansLabel);

    var ansInput = document.createElement('textarea');
    ansInput.className = 'dev-task-input';
    ansInput.id = 'upgrade-answers';
    ansInput.placeholder = '위 질문에 자유롭게 답변하세요. 모든 질문에 답하지 않아도 됩니다.';
    ansInput.rows = 6;
    panel.appendChild(ansInput);

    var startBtn = document.createElement('button');
    startBtn.className = 'ot-start-btn';
    startBtn.textContent = '업그레이드 시작';
    startBtn.onclick = function () {
      _startDev(ansInput.value.trim());
    };
    panel.appendChild(startBtn);

    var skipBtn = document.createElement('button');
    skipBtn.className = 'ot-skip-btn';
    skipBtn.style.cssText = 'margin-top:8px;background:none;border:1px solid var(--border,rgba(255,255,255,0.08));color:var(--dim,#8b949e);padding:8px 16px;border-radius:8px;cursor:pointer;width:100%;font-size:14px;';
    skipBtn.textContent = '건너뛰고 바로 업그레이드';
    skipBtn.onclick = function () { _startDev(''); };
    panel.appendChild(skipBtn);

    _container.appendChild(panel);
  }

  function _startDev(answers) {
    _state = 'developing';
    _running = true;

    _clear(_container);
    _renderDevProgress();

    var retries = 0;
    var send = function () {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify({
          type: 'start_upgrade_dev',
          data: {
            folder_path: _folderPath,
            task: _task,
            answers: answers,
            backup_path: _backupPath,
            analysis: _analysis,
            session_id: _sessionId,
          },
        }));
      } else if (retries < 50) {
        retries++;
        setTimeout(send, 100);
      }
    };
    send();
  }

  function _renderDevProgress() {
    _lastToolLabel = '';
    var wrap = document.createElement('div');
    wrap.id = 'upgrade-progress';
    wrap.style.padding = '20px';

    // Phase bar — 5단계 (백업/분석/질문 이미 완료, 개발/리포트 남음)
    var phaseBar = document.createElement('div');
    phaseBar.className = 'dev-phase-bar';
    phaseBar.id = 'upgrade-phase-bar';

    var phases = [
      { id: 'backup', label: '백업', done: true },
      { id: 'analyze', label: '분석', done: true },
      { id: 'clarify', label: '질문', done: true },
      { id: 'dev', label: '개발', done: false },
      { id: 'report', label: '리포트', done: false },
    ];

    phases.forEach(function (p, i) {
      if (i > 0) {
        var conn = document.createElement('div');
        conn.className = 'dev-phase-connector';
        conn.id = 'upgrade-conn-' + p.id;
        // 이전 단계가 완료된 상태면 커넥터도 completed
        if (phases[i - 1].done) conn.classList.add('done');
        phaseBar.appendChild(conn);
      }
      var item = document.createElement('div');
      item.className = 'dev-phase-item' + (p.done ? ' done' : '');
      item.id = 'upgrade-phase-' + p.id;
      item.dataset.baseLabel = p.label;
      item.textContent = p.done ? '✓ ' + p.label : p.label;
      phaseBar.appendChild(item);
    });

    wrap.appendChild(phaseBar);

    // 중지 버튼
    var stopBtn = document.createElement('button');
    stopBtn.id = 'upgrade-stop-btn';
    stopBtn.className = 'dev-stop-btn';
    stopBtn.textContent = '■ 중지';
    stopBtn.onclick = function () {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify({ type: 'stop_upgrade' }));
      }
    };
    wrap.appendChild(stopBtn);

    // 로그 영역 (야근팀 dev 스타일)
    var logArea = document.createElement('div');
    logArea.id = 'upgrade-log';
    logArea.style.cssText = 'margin-top:16px;max-height:400px;overflow-y:auto;';
    wrap.appendChild(logArea);

    _container.appendChild(wrap);
  }

  function _addLog(text, type) {
    if (!text) return;
    var log = document.getElementById('upgrade-log');
    if (!log) return;
    var entry = document.createElement('div');
    entry.style.cssText = 'padding:6px 0;font-size:13px;color:var(--dim,#8b949e);border-bottom:1px solid var(--border,rgba(255,255,255,0.08));';

    var time = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    var icon = '📋';
    if (type === 'rate_limited') icon = '⏸️';
    else if (type === 'complete') icon = '✅';
    else if (type === 'error') icon = '❌';
    else if (type === 'session_start') icon = '🚀';
    else if (type === 'handoff') icon = '🔄';
    else if (type === 'generating') icon = '📝';
    else if (type === 'max_sessions') icon = '⚠️';

    entry.textContent = time + ' ' + icon + ' ' + text;
    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
  }

  function _showCompletion(data) {
    // 중지 버튼 숨김 (야근팀 dev 패턴)
    var stopBtn = document.getElementById('upgrade-stop-btn');
    if (stopBtn) stopBtn.style.display = 'none';

    var logArea = document.getElementById('upgrade-log');
    if (!logArea) return;

    var linkWrap = document.createElement('div');
    linkWrap.style.cssText = 'margin-top:16px;display:flex;gap:8px;flex-wrap:wrap;';

    if (data.report_path) {
      var reportBtn = document.createElement('button');
      reportBtn.className = 'ot-start-btn';
      reportBtn.textContent = '📄 업그레이드 리포트 보기';
      reportBtn.onclick = function () { window.open(data.report_path + '/results.html', '_blank'); };
      linkWrap.appendChild(reportBtn);
    }

    if (data.folder_path) {
      var folderBtn = document.createElement('button');
      folderBtn.style.cssText = 'padding:10px 16px;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);cursor:pointer;font-size:14px;';
      folderBtn.textContent = '📁 앱 폴더 열기';
      folderBtn.onclick = function () {
        fetch('/api/open-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: data.folder_path }),
        }).catch(function () {});
      };
      linkWrap.appendChild(folderBtn);
    }

    logArea.appendChild(linkWrap);

    if (data.backup_path) {
      var backupInfo = document.createElement('div');
      backupInfo.style.cssText = 'margin-top:12px;padding:8px 12px;background:rgba(63,185,80,0.06);border-left:3px solid var(--green,#3FB950);font-size:12px;color:var(--dim,#8b949e);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all;';
      backupInfo.textContent = '🛟 복원 지점: ' + data.backup_path;
      logArea.appendChild(backupInfo);
    }

    // 처음으로 돌아가기 버튼 (야근팀 스타일)
    var homeWrap = document.createElement('div');
    homeWrap.style.cssText = 'margin-top:12px;';
    var homeBtn = document.createElement('button');
    homeBtn.style.cssText = 'padding:10px 16px;background:none;border:1px solid var(--border,rgba(255,255,255,0.08));border-radius:8px;color:var(--dim,#8b949e);cursor:pointer;font-size:14px;width:100%;';
    homeBtn.textContent = '← 처음으로';
    homeBtn.onclick = function () {
      _state = 'form';
      _folderPath = '';
      _task = '';
      _sessionId = '';
      _backupPath = '';
      _analysis = null;
      _render();
    };
    homeWrap.appendChild(homeBtn);
    logArea.appendChild(homeWrap);
  }

  // ──────────────────────────────────────────────
  // 최초개발 (0→1 새 앱 생성)
  // ──────────────────────────────────────────────
  function _renderInitialForm() {
    _connect();

    var form = document.createElement('div');
    form.className = 'ot-form';

    var title = document.createElement('h2');
    title.className = 'ot-title';
    title.textContent = '최초개발';
    form.appendChild(title);

    var subtitle = document.createElement('p');
    subtitle.className = 'ot-subtitle';
    subtitle.textContent = '만들고 싶은 앱을 설명하면 AI가 자동으로 개발합니다. 로컬에서 바로 실행 가능한 앱이 만들어집니다.';
    form.appendChild(subtitle);

    var taskLabel = document.createElement('label');
    taskLabel.textContent = '만들고 싶은 앱';
    taskLabel.className = 'ot-label';
    form.appendChild(taskLabel);

    var taskInput = document.createElement('textarea');
    taskInput.className = 'dev-task-input';
    taskInput.placeholder = '예: 할일 관리 앱을 만들어줘. 할일을 추가하고 완료 체크하고, 날짜별로 정리할 수 있었으면 좋겠어.';
    taskInput.rows = 5;
    form.appendChild(taskInput);

    var startBtn = document.createElement('button');
    startBtn.className = 'ot-start-btn';
    startBtn.textContent = '개발 시작';
    startBtn.onclick = function () {
      var task = taskInput.value.trim();
      if (!task) { alert('만들고 싶은 앱을 설명해주세요.'); return; }
      _devTask = task;
      startBtn.disabled = true;
      startBtn.textContent = '질문 생성 중...';
      _connect();
      var retries = 0;
      var sendClarify = function () {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send(JSON.stringify({
            type: 'start_dev_clarify',
            data: { task: task },
          }));
        } else if (retries < 50) {
          retries++;
          setTimeout(sendClarify, 100);
        }
      };
      sendClarify();
    };
    form.appendChild(startBtn);

    _container.appendChild(form);
  }

  function _showDevQuestions(questions, sessionId) {
    _devSessionId = sessionId;
    _devQuestions = questions;

    _clear(_container);

    // 서브탭 스위처 다시 렌더 (clear 후 복구)
    var tabSwitcher = document.createElement('div');
    tabSwitcher.className = 'ot-tab-switcher';
    var initialBtn = document.createElement('button');
    initialBtn.textContent = '최초개발';
    initialBtn.className = 'ot-tab-btn active';
    tabSwitcher.appendChild(initialBtn);
    var upgradeBtn = document.createElement('button');
    upgradeBtn.textContent = '강화소';
    upgradeBtn.className = 'ot-tab-btn';
    upgradeBtn.onclick = function () { _subMode = 'upgrade'; _render(); };
    tabSwitcher.appendChild(upgradeBtn);
    _container.appendChild(tabSwitcher);

    var panel = document.createElement('div');
    panel.className = 'ot-form';

    var title = document.createElement('h2');
    title.className = 'ot-title';
    title.textContent = '몇 가지만 확인할게요';
    panel.appendChild(title);

    // 질문 텍스트 (marked 안전하게 fallback)
    var qText = document.createElement('div');
    qText.className = 'dev-questions-text';
    if (typeof marked !== 'undefined' && marked.parse) {
      try {
        qText.innerHTML = marked.parse(questions); // eslint-disable-line no-unsanitized/property
      } catch (_) {
        qText.textContent = questions;
      }
    } else {
      qText.textContent = questions;
    }
    panel.appendChild(qText);

    var ansLabel = document.createElement('label');
    ansLabel.textContent = '답변';
    ansLabel.className = 'ot-label';
    panel.appendChild(ansLabel);

    var ansInput = document.createElement('textarea');
    ansInput.className = 'dev-task-input';
    ansInput.placeholder = '위 질문에 자유롭게 답변해주세요. 모든 질문에 답하지 않아도 됩니다.';
    ansInput.rows = 6;
    panel.appendChild(ansInput);

    var startBtn = document.createElement('button');
    startBtn.className = 'ot-start-btn';
    startBtn.textContent = '개발 시작';
    startBtn.onclick = function () {
      _startDev(_devTask, ansInput.value.trim(), _devSessionId);
    };
    panel.appendChild(startBtn);

    var skipBtn = document.createElement('button');
    skipBtn.className = 'ot-skip-btn';
    skipBtn.style.cssText = 'margin-top:8px;background:none;border:1px solid var(--border,rgba(255,255,255,0.08));color:var(--dim,#8b949e);padding:8px 16px;border-radius:8px;cursor:pointer;width:100%;font-size:14px;';
    skipBtn.textContent = '건너뛰고 바로 개발 시작';
    skipBtn.onclick = function () { _startDev(_devTask, '', _devSessionId); };
    panel.appendChild(skipBtn);

    _container.appendChild(panel);
  }

  function _startDev(task, answers, sessionId) {
    _running = true;
    _clear(_container);
    _renderDevProgress();

    var retries = 0;
    var sendStart = function () {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify({
          type: 'start_dev',
          data: {
            task: task,
            answers: answers,
            session_id: sessionId,
          },
        }));
      } else if (retries < 50) {
        retries++;
        setTimeout(sendStart, 100);
      }
    };
    sendStart();
  }

  function _renderDevProgress() {
    _lastToolLabel = '';

    // 서브탭 스위처 (유지)
    var tabSwitcher = document.createElement('div');
    tabSwitcher.className = 'ot-tab-switcher';
    var initialBtn = document.createElement('button');
    initialBtn.textContent = '최초개발';
    initialBtn.className = 'ot-tab-btn active';
    tabSwitcher.appendChild(initialBtn);
    var upgradeBtn = document.createElement('button');
    upgradeBtn.textContent = '강화소';
    upgradeBtn.className = 'ot-tab-btn';
    upgradeBtn.onclick = function () { _subMode = 'upgrade'; _render(); };
    tabSwitcher.appendChild(upgradeBtn);
    _container.appendChild(tabSwitcher);

    var wrap = document.createElement('div');
    wrap.id = 'dev-progress';
    wrap.style.padding = '20px';

    // Phase bar
    var phaseBar = document.createElement('div');
    phaseBar.className = 'dev-phase-bar';

    var phases = [
      { id: 'clarify', label: '질문' },
      { id: 'dev', label: '개발' },
      { id: 'report', label: '리포트' },
    ];

    phases.forEach(function (p, i) {
      if (i > 0) {
        var conn = document.createElement('div');
        conn.className = 'dev-phase-connector';
        conn.id = 'dev-conn-' + p.id;
        if (p.id === 'dev') conn.classList.add('done');
        phaseBar.appendChild(conn);
      }
      var item = document.createElement('div');
      item.className = 'dev-phase-item';
      item.id = 'dev-phase-' + p.id;
      item.textContent = p.label;
      if (p.id === 'clarify') {
        item.classList.add('done');
        item.textContent = '✓ ' + p.label;
      }
      phaseBar.appendChild(item);
    });

    wrap.appendChild(phaseBar);

    var stopBtn = document.createElement('button');
    stopBtn.id = 'dev-stop-btn';
    stopBtn.className = 'dev-stop-btn';
    stopBtn.textContent = '■ 중지';
    stopBtn.onclick = function () { _handleDevStop(); };
    wrap.appendChild(stopBtn);

    var logArea = document.createElement('div');
    logArea.id = 'dev-log';
    logArea.style.cssText = 'margin-top:16px;max-height:400px;overflow-y:auto;';
    wrap.appendChild(logArea);

    _container.appendChild(wrap);
  }

  function _handleDevProgress(data) {
    var phase = data.phase;
    var action = data.action;

    var phaseEl = document.getElementById('dev-phase-' + phase);
    if (phaseEl) {
      if (action === 'complete') {
        phaseEl.classList.add('done');
        phaseEl.classList.remove('active');
        var label = phaseEl.textContent.replace(/^[●○✓]\s*/, '');
        phaseEl.textContent = '✓ ' + label;
        var conn = document.getElementById('dev-conn-' + phase);
        if (conn) conn.classList.add('done');
      } else if (action !== 'generating') {
        phaseEl.classList.add('active');
        var label2 = phaseEl.textContent.replace(/^[●○✓]\s*/, '');
        if (!phaseEl.textContent.startsWith('●')) {
          phaseEl.textContent = '● ' + label2;
        }
      }
    }

    if (data.message) _addDevLog(data.message, action);

    if (phase === 'report' && action === 'complete' && data.report_path) {
      _addDevReportLink(data.report_path, data.app_dir);
    }
  }

  function _updateDevToolStatus(label, count) {
    var logArea = document.getElementById('dev-log');
    if (!logArea) return;

    if (label === _lastToolLabel) {
      var lastEl = document.getElementById('dev-tool-last');
      if (lastEl) {
        lastEl.textContent = '🔧 ' + label + ' (도구 사용 ' + count + '회)';
        logArea.scrollTop = logArea.scrollHeight;
        return;
      }
    }

    var prev = document.getElementById('dev-tool-last');
    if (prev) prev.removeAttribute('id');

    var el = document.createElement('div');
    el.id = 'dev-tool-last';
    el.style.cssText = 'padding:4px 0;font-size:12px;color:var(--blue,#60a5fa);border-bottom:1px solid var(--border,rgba(255,255,255,0.08));';
    el.textContent = '🔧 ' + label + ' (도구 사용 ' + count + '회)';
    logArea.appendChild(el);
    logArea.scrollTop = logArea.scrollHeight;
    _lastToolLabel = label;
  }

  function _addDevLog(message, type) {
    var logArea = document.getElementById('dev-log');
    if (!logArea) return;
    var entry = document.createElement('div');
    entry.style.cssText = 'padding:6px 0;font-size:13px;color:var(--dim,#8b949e);border-bottom:1px solid var(--border,rgba(255,255,255,0.08));';

    var time = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    var icon = '📋';
    if (type === 'rate_limited') icon = '⏸️';
    else if (type === 'complete') icon = '✅';
    else if (type === 'error') icon = '❌';
    else if (type === 'session_start') icon = '🚀';
    else if (type === 'handoff') icon = '🔄';
    else if (type === 'generating') icon = '📝';

    entry.textContent = time + ' ' + icon + ' ' + message;
    logArea.appendChild(entry);
    logArea.scrollTop = logArea.scrollHeight;
  }

  function _addDevReportLink(reportPath, appDir) {
    var logArea = document.getElementById('dev-log');
    if (!logArea) return;

    var linkWrap = document.createElement('div');
    linkWrap.style.cssText = 'margin-top:16px;display:flex;gap:8px;';

    var reportBtn = document.createElement('button');
    reportBtn.className = 'ot-start-btn';
    reportBtn.style.width = 'auto';
    reportBtn.textContent = '📄 리포트 + 실행 가이드 보기';
    reportBtn.onclick = function () { window.open(reportPath + '/results.html', '_blank'); };
    linkWrap.appendChild(reportBtn);

    if (appDir) {
      var folderBtn = document.createElement('button');
      folderBtn.style.cssText = 'padding:10px 16px;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);cursor:pointer;font-size:14px;';
      folderBtn.textContent = '📁 앱 폴더 열기';
      folderBtn.onclick = function () {
        fetch('/api/open-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: appDir }),
        }).catch(function () {});
      };
      linkWrap.appendChild(folderBtn);
    }

    logArea.appendChild(linkWrap);
    _running = false;
    var stopBtn = document.getElementById('dev-stop-btn');
    if (stopBtn) stopBtn.style.display = 'none';

    var homeWrap = document.createElement('div');
    homeWrap.style.cssText = 'margin-top:12px;';
    var homeBtn = document.createElement('button');
    homeBtn.style.cssText = 'padding:10px 16px;background:none;border:1px solid var(--border,rgba(255,255,255,0.08));border-radius:8px;color:var(--dim,#8b949e);cursor:pointer;font-size:14px;width:100%;';
    homeBtn.textContent = '← 처음으로';
    homeBtn.onclick = function () {
      _devTask = '';
      _devSessionId = '';
      _render();
    };
    homeWrap.appendChild(homeBtn);
    logArea.appendChild(homeWrap);
  }

  function _handleDevStop() {
    if (_devPaused) return;
    _devPaused = true;
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: 'stop_dev' }));
    }
    _addDevLog('중단 요청됨', 'rate_limited');
    var stopBtn = document.getElementById('dev-stop-btn');
    if (stopBtn) stopBtn.style.display = 'none';
  }

  return {
    mountInShell: mountInShell,
  };
})();

/* card-builder.js — Company builder mode UI logic.
 *
 * Manages:
 *  - WebSocket /ws/company-builder connection
 *  - Action buttons (new team, add agent, load team)
 *  - builder_stream → chat panel token streaming
 *  - builder_team → CardView.layoutTree()
 *  - save/load/delete company operations
 */
var CardBuilder = (function () {
  'use strict';

  var _ws = null;
  var _wsReady = false;
  var _chatPanel = null;
  var _streamBuffer = '';  // accumulates streamed tokens
  var _streamEl = null;    // current streaming message DOM element
  var _companies = [];     // list of saved companies
  var _schedules = [];     // list of saved schedules
  var _strategies = [];    // list of saved strategies
  var _currentCompanyId = null;
  var _currentStrategyId = null;
  var _currentStrategy = null; // loaded strategy data
  var _useStrategyMode = true; // 싱글 세션 모드에서는 항상 전략 설계

  /* ── WebSocket ── */

  function _connect(chatPanel) {
    _chatPanel = chatPanel;
    if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;

    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/ws/company-builder';
    _ws = new WebSocket(url);
    _wsReady = false;

    _ws.onopen = function () { _wsReady = true; };

    _ws.onmessage = function (e) {
      try {
        var msg = JSON.parse(e.data);
        _handleMessage(msg);
      } catch (err) { /* ignore */ }
    };

    _ws.onclose = function () {
      _wsReady = false;
      _ws = null;
      // Only reconnect if builder mode is still active
      if (CardView.getActiveMode() === 'builder') {
        setTimeout(function () { _connect(_chatPanel); }, 3000);
      }
    };

    _ws.onerror = function () { _wsReady = false; };
  }

  function _send(obj) {
    if (_ws && _wsReady) _ws.send(JSON.stringify(obj));
  }

  function disconnect() {
    if (_ws) {
      // Remove onclose to prevent reconnect after intentional disconnect
      _ws.onclose = null;
      _ws.onerror = null;
      try { _ws.close(); } catch (_) {}
      _ws = null;
      _wsReady = false;
    }
    _streamEl = null;
    _streamBuffer = '';
  }

  /* ── Message Handlers ── */

  function _handleMessage(msg) {
    var type = msg.type;
    var data = msg.data || {};

    if (type === 'builder_stream') {
      _handleStream(data);
    } else if (type === 'builder_team') {
      _handleTeam(data);
    } else if (type === 'builder_companies') {
      _companies = data.companies || [];
      _renderSidebarTeamList();
    } else if (type === 'company_saved') {
      _currentCompanyId = data.id || null;
      if (_chatPanel) _chatPanel.addMessage('💾 팀이 저장되었습니다: ' + (data.name || data.id), 'system');
    } else if (type === 'company_loaded') {
      _loadCompanyToCanvas(data);
    } else if (type === 'company_deleted') {
      if (_chatPanel) _chatPanel.addMessage('🗑️ 팀이 삭제되었습니다.', 'system');
    } else if (type === 'schedule_saved') {
      if (_chatPanel) _chatPanel.addMessage('⏰ 스케줄이 저장되었습니다: ' + (data.name || data.id), 'system');
    } else if (type === 'schedule_list') {
      _schedules = data.schedules || [];
    } else if (type === 'schedule_toggled') {
      var state = data.enabled ? '활성화' : '비활성화';
      if (_chatPanel) _chatPanel.addMessage('⏰ 스케줄 ' + state + ': ' + (data.name || data.id), 'system');
    } else if (type === 'schedule_deleted') {
      if (_chatPanel) _chatPanel.addMessage('🗑️ 스케줄이 삭제되었습니다.', 'system');
    } else if (type === 'task_validation') {
      if (data.fit) {
        if (_onTaskValidated) _onTaskValidated(data);
        _onTaskValidated = null;
      } else {
        _onTaskValidated = null;
        if (_chatPanel) {
          _chatPanel.addMessage('⚠️ ' + (data.suggestion || '이 업무는 현재 팀과 맞지 않습니다.'), 'system');
          if (data.matching_team_id) {
            _chatPanel.addMessage('💡 다른 저장된 팀이 더 적합할 수 있습니다. 불러오시겠습니까?', 'system');
          }
        }
      }
    } else if (type === 'builder_strategy') {
      _handleStrategy(data);
    } else if (type === 'builder_strategies') {
      _strategies = data.strategies || [];
      _renderSidebarStrategyList();
    } else if (type === 'strategy_saved') {
      _currentStrategyId = data.id || null;
      if (_chatPanel) _chatPanel.addMessage('💾 전략이 저장되었습니다: ' + (data.name || data.id), 'system');
    } else if (type === 'strategy_loaded') {
      _displayStrategyCards(data);
    } else if (type === 'strategy_deleted') {
      if (_chatPanel) _chatPanel.addMessage('🗑️ 전략이 삭제되었습니다.', 'system');
    } else if (type === 'error') {
      if (_chatPanel) _chatPanel.addMessage('❌ ' + (data.message || '오류'), 'system');
    }
  }

  function _handleStream(data) {
    if (!_chatPanel) return;

    if (!_streamEl) {
      // Start new streaming message — thinking 제거
      _chatPanel.hideThinking();
      _streamBuffer = '';
      _streamEl = document.createElement('div');
      _streamEl.className = 'cc-message cc-message-system';
      _chatPanel.messagesEl.appendChild(_streamEl);
    }

    _streamBuffer += data.token || '';

    // Strip ```team_json ... ``` block from display (raw JSON is not user-facing)
    var displayText = _streamBuffer.replace(/```team_json[\s\S]*?```/g, '').trim();
    // Also strip trailing ``` if team_json block is still being streamed
    displayText = displayText.replace(/```team_json[\s\S]*/g, '').trim();
    _streamEl.textContent = displayText;
    _chatPanel.messagesEl.scrollTop = _chatPanel.messagesEl.scrollHeight;

    if (data.done) {
      // Final cleanup — remove team/strategy JSON blocks
      displayText = _streamBuffer.replace(/```(?:team_json|strategy_json)[\s\S]*?```/g, '').trim();
      _streamEl.textContent = displayText;
      _streamEl = null;
      _streamBuffer = '';
      // thinking indicator 제거 + placeholder 복원
      if (_chatPanel) {
        _chatPanel.hideThinking();
        _chatPanel.setInputPlaceholder('이 전략으로 업무를 지시하세요...');
      }
    }
  }

  function _handleTeam(data) {
    var agents = data.agents || [];
    var edges = data.edges || [];

    if (agents.length === 0) return;

    // Hide empty state
    var emptyEl = document.getElementById('card-empty-state');
    if (emptyEl) emptyEl.style.display = 'none';

    CardView.layoutTree(agents, edges);

    if (_chatPanel) {
      _chatPanel.addMessage('🏗️ 팀 구조가 캔버스에 배치되었습니다. (' + agents.length + '명)', 'system');
      _chatPanel.setInputPlaceholder('이 팀에게 업무를 지시하세요...');
    }
  }

  /* ── Strategy Handling ── */

  function _handleStrategy(data) {
    _currentStrategy = data;
    _displayStrategyCards(data);
    if (_chatPanel) {
      _chatPanel.addMessage('📋 전략이 설계되었습니다: ' + (data.name || '분석 전략'), 'system');
      _chatPanel.addActionButtons([
        {
          label: '이 전략으로 바로 실행',
          icon: '🚀',
          action: function () {
            _chatPanel.setInputPlaceholder('이 전략으로 업무를 지시하세요...');
            _chatPanel.addMessage('업무를 입력하세요. 이 전략의 관점으로 분석합니다.', 'system');
          },
        },
        {
          label: '전략 저장',
          icon: '💾',
          action: function () {
            var name = data.name || '분석 전략';
            _send({
              type: 'save_strategy',
              data: data,
            });
          },
        },
        {
          label: '전략 수정 요청',
          icon: '✏️',
          action: function () {
            _chatPanel.setInputPlaceholder('수정 요청을 입력하세요...');
            _chatPanel.addMessage('어떤 부분을 수정할까요? (예: "경쟁사 분석 관점 추가해줘")', 'system');
          },
        },
      ]);
      _chatPanel.setInputPlaceholder('이 전략으로 업무를 지시하세요...');
    }
  }

  function _displayStrategyCards(strategy) {
    _currentStrategy = strategy;
    _currentStrategyId = strategy.id || null;

    var canvas = document.getElementById('card-canvas');
    if (!canvas) return;

    // 빈 상태 숨기기
    var emptyEl = document.getElementById('card-empty-state');
    if (emptyEl) emptyEl.style.display = 'none';

    // Drawflow 숨기기
    var drawflow = canvas.querySelector('.drawflow');
    if (drawflow) drawflow.style.display = 'none';

    // 기존 전략 뷰 제거
    var old = document.getElementById('strategy-view');
    if (old) old.remove();

    var perspectives = strategy.perspectives || [];
    var depthLabels = { light: '간략', standard: '표준', deep: '심층' };
    var formatLabels = { summary: '요약', executive_report: '보고서', data_table: '데이터', presentation: '발표' };

    // 전략 뷰 생성 (DOM only, no innerHTML with user data)
    var view = document.createElement('div');
    view.id = 'strategy-view';

    // 헤더
    var header = document.createElement('div');
    header.className = 'sv-header';
    var title = document.createElement('div');
    title.className = 'sv-title';
    title.textContent = strategy.name || '분석 전략';
    var desc = document.createElement('div');
    desc.className = 'sv-desc';
    desc.textContent = strategy.description || '';
    header.appendChild(title);
    header.appendChild(desc);
    view.appendChild(header);

    // 메타 태그
    var meta = document.createElement('div');
    meta.className = 'sv-meta';
    var depthTag = document.createElement('span');
    depthTag.className = 'sv-tag';
    depthTag.textContent = '깊이: ' + (depthLabels[strategy.depth] || '표준');
    var fmtTag = document.createElement('span');
    fmtTag.className = 'sv-tag';
    fmtTag.textContent = '형식: ' + (formatLabels[strategy.output_format] || '보고서');
    meta.appendChild(depthTag);
    meta.appendChild(fmtTag);
    view.appendChild(meta);

    // 관점 카드 그리드
    var grid = document.createElement('div');
    grid.className = 'sv-grid';
    for (var i = 0; i < perspectives.length; i++) {
      var p = perspectives[i];
      var card = document.createElement('div');
      card.className = 'sv-card';
      var cardIcon = document.createElement('div');
      cardIcon.className = 'sv-card-icon';
      cardIcon.textContent = p.icon || '📌';
      var cardName = document.createElement('div');
      cardName.className = 'sv-card-name';
      cardName.textContent = p.name || '관점';
      var cardInst = document.createElement('div');
      cardInst.className = 'sv-card-inst';
      cardInst.textContent = p.instruction || '';
      card.appendChild(cardIcon);
      card.appendChild(cardName);
      card.appendChild(cardInst);
      grid.appendChild(card);
    }
    view.appendChild(grid);

    // 특별 지시
    if (strategy.special_instructions) {
      var special = document.createElement('div');
      special.className = 'sv-special';
      special.textContent = '💡 ' + strategy.special_instructions;
      view.appendChild(special);
    }

    canvas.appendChild(view);
  }

  function _renderSidebarStrategyList() {
    // 전략 목록은 기존 팀 목록 아래에 추가 — 간단히 콘솔만 표시
    // (사이드바 UI 확장은 추후)
  }

  function _loadCompanyToCanvas(company) {
    _currentCompanyId = company.id || null;
    var agents = company.agents || [];
    var edges = company.edges || [];

    // Reconstruct from flow if available
    if (company.flow && !agents.length) {
      // flow is Drawflow export — import directly
      var editor = CardView.getEditor();
      if (editor && company.flow.drawflow) {
        editor.import(company.flow);
        return;
      }
    }

    if (agents.length > 0) {
      var emptyEl = document.getElementById('card-empty-state');
      if (emptyEl) emptyEl.style.display = 'none';
      CardView.layoutTree(agents, edges);
    }

    if (_chatPanel) {
      _chatPanel.addMessage('📂 "' + (company.name || '팀') + '" 을 불러왔습니다.', 'system');
      if (agents.length > 0) {
        _chatPanel.setInputPlaceholder('이 팀에게 업무를 지시하세요...');
      }
    }
  }

  /* ── Sidebar Team List ── */

  function _renderSidebarTeamList() {
    var listEl = document.getElementById('cs-team-list');
    if (!listEl) return;
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);

    if (_companies.length === 0) return;

    var sep = document.createElement('div');
    sep.className = 'cs-team-sep';
    listEl.appendChild(sep);

    var header = document.createElement('div');
    header.className = 'cs-team-header';
    header.textContent = '저장된 팀';
    listEl.appendChild(header);

    _companies.forEach(function (co) {
      var item = document.createElement('button');
      item.className = 'cs-team-item';
      item.title = co.name || co.id;
      item.textContent = '📁';

      var nameSpan = document.createElement('span');
      nameSpan.className = 'cs-team-name';
      nameSpan.textContent = co.name || co.id;
      item.appendChild(nameSpan);

      item.addEventListener('click', function () {
        loadCompany(co.id);
      });
      listEl.appendChild(item);
    });
  }

  /* ── Public API ── */

  function sendMessage(text) {
    // 싱글 세션 모드에서는 전략 설계 에이전트로 전달
    var msgType = _useStrategyMode ? 'strategy_message' : 'builder_message';
    _send({ type: msgType, data: { content: text } });
  }

  function saveCurrentTeam(name, description) {
    var editor = CardView.getEditor();
    var flow = editor ? editor.export() : {};

    // Extract agents and edges from Drawflow nodes
    var agents = [];
    var edges = [];
    if (editor && flow.drawflow && flow.drawflow.Home && flow.drawflow.Home.data) {
      var nodes = flow.drawflow.Home.data;
      var nodeKeys = Object.keys(nodes);
      for (var i = 0; i < nodeKeys.length; i++) {
        var node = nodes[nodeKeys[i]];
        var d = node.data || {};
        agents.push({
          id: d.agentId || d.id || ('agent_node_' + nodeKeys[i]),
          name: d.name || 'Agent',
          role: d.role || '',
          tool_category: d.toolCategory || d.tool_category || 'research',
          emoji: d.emoji || '⚙️',
          system_prompt: d.system_prompt || '',
        });
        // Extract edges from output connections
        var outputs = node.outputs || {};
        var outKeys = Object.keys(outputs);
        for (var j = 0; j < outKeys.length; j++) {
          var conns = outputs[outKeys[j]].connections || [];
          for (var k = 0; k < conns.length; k++) {
            var targetNodeId = conns[k].node;
            var targetNode = nodes[targetNodeId];
            if (targetNode && targetNode.data) {
              edges.push({
                from: d.agentId || d.id || ('agent_node_' + nodeKeys[i]),
                to: targetNode.data.agentId || targetNode.data.id || ('agent_node_' + targetNodeId),
              });
            }
          }
        }
      }
    }

    var company = {
      name: name || '새 팀',
      description: description || '',
      agents: agents,
      edges: edges,
      flow: flow,
    };

    if (_currentCompanyId) {
      company.id = _currentCompanyId;
    }

    _send({ type: 'save_company', data: company });
  }

  function loadCompany(companyId) {
    _send({ type: 'load_company', data: { company_id: companyId } });
  }

  function deleteCompany(companyId) {
    _send({ type: 'delete_company', data: { company_id: companyId } });
  }

  function listCompanies() {
    _send({ type: 'list_companies' });
  }

  function getCompanies() {
    return _companies;
  }

  function saveSchedule(companyId, taskDescription, cronExpression, name) {
    _send({
      type: 'save_schedule',
      data: {
        company_id: companyId || _currentCompanyId || '',
        task_description: taskDescription,
        cron_expression: cronExpression,
        name: name || taskDescription.substring(0, 50),
        enabled: true,
      },
    });
  }

  function listSchedules() {
    _send({ type: 'list_schedules' });
  }

  function toggleSchedule(scheduleId, enabled) {
    _send({ type: 'toggle_schedule', data: { schedule_id: scheduleId, enabled: enabled } });
  }

  function deleteSchedule(scheduleId) {
    _send({ type: 'delete_schedule', data: { schedule_id: scheduleId } });
  }

  function getSchedules() {
    return _schedules;
  }

  function isConnected() {
    return _wsReady;
  }

  /* ── Team execution helpers ── */

  var _onTaskValidated = null;

  function getCanvasAgents() {
    var editor = CardView.getEditor();
    if (!editor) return [];
    var flow = editor.export();
    if (!flow.drawflow || !flow.drawflow.Home || !flow.drawflow.Home.data) return [];
    var nodes = flow.drawflow.Home.data;
    var agents = [];
    var nodeKeys = Object.keys(nodes);
    for (var i = 0; i < nodeKeys.length; i++) {
      var d = nodes[nodeKeys[i]].data || {};
      if (d.agentId || d.name) agents.push(d);
    }
    return agents;
  }

  function getCurrentCompanyId() {
    return _currentCompanyId || '';
  }

  function validateTask(task, teamId, callback) {
    _onTaskValidated = callback;
    _send({ type: 'validate_task', data: { task: task, team_id: teamId } });
  }

  return {
    connect: _connect,
    disconnect: disconnect,
    sendMessage: sendMessage,
    saveCurrentTeam: saveCurrentTeam,
    loadCompany: loadCompany,
    deleteCompany: deleteCompany,
    listCompanies: listCompanies,
    getCompanies: getCompanies,
    saveSchedule: saveSchedule,
    listSchedules: listSchedules,
    toggleSchedule: toggleSchedule,
    deleteSchedule: deleteSchedule,
    getSchedules: getSchedules,
    isConnected: isConnected,
    getCanvasAgents: getCanvasAgents,
    getCurrentCompanyId: getCurrentCompanyId,
    getCurrentStrategyId: function () { return _currentStrategyId; },
    getCurrentStrategy: function () { return _currentStrategy; },
    getStrategies: function () { return _strategies; },
    loadAndDisplayStrategy: function (s) { _displayStrategyCards(s); },
    validateTask: validateTask,
  };
})();

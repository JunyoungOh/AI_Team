/* card-editor.js — Agent card detail/edit panel.
 *
 * When a Drawflow node is clicked in builder mode, the right panel
 * switches from chat to an edit form for the selected agent.
 *
 * Usage: CardEditor.init(containerEl)  — call once
 *        CardEditor.open(agentData)    — show editor for agent
 *        CardEditor.close()            — return to chat panel
 */
var CardEditor = (function () {
  'use strict';

  var _el = null;          // editor container element
  var _chatEl = null;      // chat panel element (to toggle)
  var _agent = null;       // currently editing agent data
  var _onSave = null;      // (updatedAgent) => void
  var _onDelete = null;    // (agentId) => void

  var TOOL_CATEGORIES = [
    { value: 'research', label: '🔍 리서치 (웹 검색, 스크래핑)' },
    { value: 'data', label: '📊 데이터 (분석, 시각화)' },
    { value: 'finance', label: '💰 금융 (주가, 환율, 재무)' },
    { value: 'development', label: '💻 개발 (코드, GitHub)' },
    { value: 'security', label: '🔒 보안 (CVE, 취약점)' },
    { value: 'legal', label: '⚖️ 법률 (규제, 컴플라이언스)' },
    { value: 'hr', label: '👥 HR (인사, 채용)' },
  ];

  function init(containerEl, opts) {
    _chatEl = document.getElementById('card-chat');
    _onSave = (opts && opts.onSave) || null;
    _onDelete = (opts && opts.onDelete) || null;

    // Build editor panel inside container using safe DOM methods
    _el = containerEl;
    while (_el.firstChild) _el.removeChild(_el.firstChild);
    _el.className = 'ce-panel';

    // Header
    var header = _c('div', 'ce-header');
    var title = _c('span', 'ce-header-title');
    title.textContent = '에이전트 편집';
    var closeBtn = _c('button', 'ce-close');
    closeBtn.textContent = '\u00d7';
    closeBtn.title = '닫기';
    closeBtn.addEventListener('click', close);
    header.appendChild(title);
    header.appendChild(closeBtn);

    // Form
    var form = _c('div', 'ce-form');

    // Emoji field
    form.appendChild(_field('ce-emoji', '이모지', 'input', 'emoji'));

    // Name field
    form.appendChild(_field('ce-name', '이름', 'input', 'name'));

    // Role field
    form.appendChild(_field('ce-role', '역할', 'textarea', 'role'));

    // Tool category select
    var tcGroup = _c('div', 'ce-field');
    var tcLabel = _c('label', 'ce-label');
    tcLabel.textContent = '도구 카테고리';
    var tcSelect = _c('select', 'ce-select');
    tcSelect.id = 'ce-tool-category';
    TOOL_CATEGORIES.forEach(function (cat) {
      var opt = document.createElement('option');
      opt.value = cat.value;
      opt.textContent = cat.label;
      tcSelect.appendChild(opt);
    });
    tcGroup.appendChild(tcLabel);
    tcGroup.appendChild(tcSelect);
    form.appendChild(tcGroup);

    // System prompt override
    form.appendChild(_field('ce-prompt', '커스텀 프롬프트 (선택)', 'textarea', 'system_prompt_override'));

    // Action buttons
    var actions = _c('div', 'ce-actions');
    var saveBtn = _c('button', 'ce-save-btn');
    saveBtn.textContent = '💾 저장';
    saveBtn.addEventListener('click', _handleSave);
    var deleteBtn = _c('button', 'ce-delete-btn');
    deleteBtn.textContent = '🗑️ 삭제';
    deleteBtn.addEventListener('click', _handleDelete);
    actions.appendChild(saveBtn);
    actions.appendChild(deleteBtn);

    _el.appendChild(header);
    _el.appendChild(form);
    _el.appendChild(actions);

    // Initially hidden
    _el.style.display = 'none';
  }

  function open(agentData) {
    if (!_el) return;
    _agent = agentData || {};

    // Fill fields
    _setVal('ce-emoji', _agent.emoji || '');
    _setVal('ce-name', _agent.name || '');
    _setVal('ce-role', _agent.role || '');
    _setVal('ce-tool-category', _agent.toolCategory || _agent.tool_category || 'research');
    _setVal('ce-prompt', _agent.system_prompt_override || '');

    // Show editor, hide chat
    _el.style.display = 'flex';
    if (_chatEl) _chatEl.style.display = 'none';

    // Ensure right panel is visible
    var app = document.getElementById('card-app');
    if (app) app.classList.add('chat-open');
  }

  function close() {
    if (_el) _el.style.display = 'none';
    if (_chatEl) _chatEl.style.display = '';
    _agent = null;
  }

  function isOpen() {
    return _el && _el.style.display !== 'none';
  }

  function _handleSave() {
    if (!_agent) return;

    var updated = {
      id: _agent.id,
      emoji: _getVal('ce-emoji') || _agent.emoji,
      name: _getVal('ce-name') || _agent.name,
      role: _getVal('ce-role') || _agent.role,
      tool_category: _getVal('ce-tool-category') || 'research',
      system_prompt_override: _getVal('ce-prompt') || '',
    };

    // Update the card on canvas
    var nodeId = null;
    var editor = CardView.getEditor();
    if (editor) {
      var exportData = editor.export();
      var nodes = exportData.drawflow && exportData.drawflow.Home && exportData.drawflow.Home.data;
      if (nodes) {
        Object.keys(nodes).forEach(function (nid) {
          if (nodes[nid].data && nodes[nid].data.agentId === updated.id) {
            nodeId = nid;
          }
        });
      }
    }

    if (nodeId) {
      var cardEl = document.querySelector('#node-' + nodeId + ' .agent-card');
      if (cardEl) {
        var nameEl = cardEl.querySelector('.ac-name');
        if (nameEl) nameEl.textContent = updated.name;
        var roleEl = cardEl.querySelector('.ac-role');
        if (roleEl) roleEl.textContent = updated.role;
        var emojiEl = cardEl.querySelector('.ac-emoji');
        if (emojiEl) emojiEl.textContent = updated.emoji;
        var toolEl = cardEl.querySelector('.ac-tool');
        if (toolEl) toolEl.textContent = updated.tool_category;
      }
    }

    if (_onSave) _onSave(updated);
    close();
  }

  function _handleDelete() {
    if (!_agent) return;
    if (_onDelete) _onDelete(_agent.id);
    close();
  }

  /* ── DOM Helpers ── */

  function _c(tag, cls) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    return el;
  }

  function _field(id, labelText, inputType, key) {
    var group = _c('div', 'ce-field');
    var label = _c('label', 'ce-label');
    label.textContent = labelText;
    label.setAttribute('for', id);
    var input;
    if (inputType === 'textarea') {
      input = _c('textarea', 'ce-textarea');
      input.rows = key === 'system_prompt_override' ? 4 : 2;
    } else {
      input = _c('input', 'ce-input');
      input.type = 'text';
    }
    input.id = id;
    group.appendChild(label);
    group.appendChild(input);
    return group;
  }

  function _setVal(id, val) {
    var el = document.getElementById(id);
    if (el) el.value = val;
  }

  function _getVal(id) {
    var el = document.getElementById(id);
    return el ? el.value.trim() : '';
  }

  return {
    init: init,
    open: open,
    close: close,
    isOpen: isOpen,
  };
})();

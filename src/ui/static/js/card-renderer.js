/* card-renderer.js — Agent card HTML generation & DOM updates */
var CardRenderer = (function () {
  'use strict';

  var STATUS_LABELS = {
    idle: '\uB300\uAE30',
    running: '\uC2E4\uD589 \uC911',
    done: '\uC644\uB8CC',
    error: '\uC5D0\uB7EC',
  };

  /** XSS-safe text escaping */
  function _esc(str) {
    if (str == null) return '';
    var div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
  }

  /**
   * createCard — returns an HTML string for an agent card.
   * @param {Object} agent  { id, name, role, toolCategory, emoji, status, progress }
   */
  function createCard(agent) {
    var status = agent.status || 'idle';
    var progress = agent.progress != null ? agent.progress : 0;
    var label = STATUS_LABELS[status] || status;

    var toolHtml = agent.toolCategory
      ? '<span class="ac-tool">' + _esc(agent.toolCategory) + '</span>'
      : '';

    return (
      '<div class="agent-card status-' + _esc(status) + '" data-agent-id="' + _esc(agent.id) + '">' +
        '<div class="ac-header">' +
          '<span class="ac-emoji">' + _esc(agent.emoji) + '</span>' +
          '<span class="ac-name">' + _esc(agent.name) + '</span>' +
        '</div>' +
        '<div class="ac-role">' + _esc(agent.role) + '</div>' +
        toolHtml +
        '<div class="ac-progress">' +
          '<div class="ac-progress-fill" style="width:' + Number(progress) + '%"></div>' +
        '</div>' +
        '<div class="ac-status">' +
          '<span class="ac-status-dot ' + _esc(status) + '"></span>' +
          '<span class="ac-status-text">' + _esc(label) + '</span>' +
        '</div>' +
      '</div>'
    );
  }

  /**
   * updateCard — mutates an existing card element in the DOM.
   * @param {HTMLElement} cardEl  The .agent-card element
   * @param {Object}      updates  Any subset of { status, progress }
   */
  function updateCard(cardEl, updates) {
    if (!cardEl) return;

    if (updates.status != null) {
      var escaped = _esc(updates.status);
      cardEl.className = 'agent-card status-' + escaped;

      var dot = cardEl.querySelector('.ac-status-dot');
      if (dot) dot.className = 'ac-status-dot ' + escaped;

      var txt = cardEl.querySelector('.ac-status-text');
      if (txt) txt.textContent = STATUS_LABELS[updates.status] || updates.status;
    }

    if (updates.progress != null) {
      var fill = cardEl.querySelector('.ac-progress-fill');
      if (fill) fill.style.width = Number(updates.progress) + '%';
    }

    if (updates.name != null) {
      var nameEl = cardEl.querySelector('.ac-name');
      if (nameEl) nameEl.textContent = updates.name;
    }
  }

  /**
   * getCardElement — locates the .agent-card inside a Drawflow node.
   * @param {Object} editor   Drawflow editor instance (unused but kept for API consistency)
   * @param {string|number} nodeId
   * @returns {HTMLElement|null}
   */
  function getCardElement(editor, nodeId) {
    return document.querySelector('#node-' + nodeId + ' .agent-card');
  }

  return {
    createCard: createCard,
    updateCard: updateCard,
    getCardElement: getCardElement,
  };
})();

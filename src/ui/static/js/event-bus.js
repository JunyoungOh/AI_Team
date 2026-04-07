// src/ui/static/js/event-bus.js
'use strict';
/**
 * EventBus — 모듈 간 통신 싱글턴
 *
 * 공간 관련 이벤트:
 *   'space:enter'  — { space, mode, sub_mode, participants }
 *   'space:exit'   — { from, reason }
 *   'space:ready'  — { space, mode }
 *
 * 패널 이벤트:
 *   'panel:utterance'       — { speaker_id, speaker_name, side?, content, round }
 *   'panel:review_feedback' — { version, score, feedback, reviewer }
 *   'panel:stage_complete'  — { stage, worker_name, output_summary }
 *   'panel:research_finding'— { sub_query, finding, source, depth }
 */
class AppEventBus extends EventTarget {
  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
  on(type, fn) {
    this.addEventListener(type, (e) => fn(e.detail));
  }
  off(type, fn) {
    this.removeEventListener(type, fn);
  }
}

window.AppBus = new AppEventBus();

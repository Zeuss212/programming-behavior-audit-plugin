(() => {
  /* global acquireVsCodeApi, document, window */
  'use strict';

  const vscode = acquireVsCodeApi();
  const routeButtons = [...document.querySelectorAll('[data-route]')];
  const routePanels = [...document.querySelectorAll('[data-route-panel]')];
  const status = document.getElementById('status');
  const notice = document.getElementById('notice');
  const consent = document.getElementById('consent');

  function setRoute(route) {
    for (const button of routeButtons) {
      const selected = button.dataset.route === route;
      button.setAttribute('aria-pressed', String(selected));
    }
    for (const panel of routePanels) {
      panel.hidden = panel.dataset.routePanel !== route;
    }
  }

  for (const button of routeButtons) {
    button.addEventListener('click', () => {
      const route = button.dataset.route;
      if (route === 'teacher' || route === 'student') {
        setRoute(route);
        vscode.postMessage({ type: 'navigate', route });
      }
    });
  }

  for (const button of document.querySelectorAll('[data-command]')) {
    button.addEventListener('click', () => {
      vscode.postMessage({ type: 'command', command: button.dataset.command });
    });
  }

  consent?.addEventListener('change', () => {
    vscode.postMessage({ type: 'setConsent', value: consent.checked });
  });

  window.addEventListener('message', (event) => {
    const message = event.data;
    if (message?.type === 'notice') {
      notice.textContent = typeof message.message === 'string' ? message.message : '';
      return;
    }
    if (message?.type !== 'state' || typeof message.value !== 'object' || message.value === null) {
      return;
    }
    const state = message.value;
    setRoute(state.route === 'student' ? 'student' : 'teacher');
    consent.checked = state.consent === true;
    notice.textContent = typeof state.notice === 'string' ? state.notice : '';
    if (state.session?.status === 'collecting') {
      status.textContent = `正在监控，共记录 ${String(state.session.eventCount)} 个事件。`;
    } else if (state.session?.status === 'interrupted') {
      status.textContent = `会话已中断，已保存 ${String(state.session.eventCount)} 个事件，可继续或结束。`;
    } else if (state.selectedPlan) {
      status.textContent = `已选择方案版本 ${String(state.selectedPlan.version)}，可以开始监控。`;
    } else {
      status.textContent = '尚未选择方案。';
    }
  });

  vscode.postMessage({ type: 'refresh' });
})();

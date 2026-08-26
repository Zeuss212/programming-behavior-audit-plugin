(() => {
  /* global acquireVsCodeApi, document, window */
  'use strict';

  const vscode = acquireVsCodeApi();
  const routeButtons = [...document.querySelectorAll('[data-route]')];
  const routePanels = [...document.querySelectorAll('[data-route-panel]')];
  const status = document.getElementById('status');
  const notice = document.getElementById('notice');
  const consent = document.getElementById('consent');
  const autoAnalyze = document.getElementById('auto-analyze');
  const commandButtons = [...document.querySelectorAll('[data-command]')];

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

  for (const button of commandButtons) {
    button.addEventListener('click', () => {
      vscode.postMessage({ type: 'command', command: button.dataset.command });
    });
  }

  consent?.addEventListener('change', () => {
    vscode.postMessage({ type: 'setConsent', value: consent.checked });
  });

  autoAnalyze?.addEventListener('change', () => {
    vscode.postMessage({ type: 'setAutoAnalyze', value: autoAnalyze.checked });
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
    autoAnalyze.checked = state.autoAnalyze !== false;
    notice.textContent = typeof state.notice === 'string' ? state.notice : '';
    const progressMessage = typeof state.progress?.message === 'string' ? state.progress.message : '';
    for (const button of commandButtons) {
      button.disabled = progressMessage.length > 0;
    }
    if (progressMessage.length > 0) {
      status.textContent = progressMessage;
    } else if (state.session?.status === 'collecting') {
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

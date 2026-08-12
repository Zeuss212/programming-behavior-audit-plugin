(() => {
  /* global acquireVsCodeApi, crypto, document, window */
  'use strict';

  const vscode = acquireVsCodeApi();
  let draft = {
    schemaVersion: 1,
    currentStep: 1,
    problemText: '',
    knowledgePoints: [],
    tests: [],
    updatedAt: new Date().toISOString(),
  };
  let busy = false;
  let published;
  let saveTimer;

  const byId = (id) => document.getElementById(id);
  const problem = byId('problem-text');
  const status = byId('wizard-status');

  function post(type, extra = {}) { vscode.postMessage({ type, ...extra }); }
  function scheduleSave() {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => post('saveDraft', { draft }), 300);
  }
  function setStatus(message, kind = 'info') {
    status.textContent = message || '';
    status.dataset.kind = kind;
  }
  function updateProblem() {
    draft = { ...draft, problemText: problem.value, updatedAt: new Date().toISOString() };
    byId('problem-count').textContent = `${problem.value.length} / 20000`;
    byId('problem-error').textContent = '';
    scheduleSave();
  }
  function makeButton(text, action, disabled = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = text;
    button.disabled = disabled;
    button.addEventListener('click', action);
    return button;
  }
  function updateKnowledge(index, key, value) {
    const points = draft.knowledgePoints.map((item, itemIndex) =>
      itemIndex === index ? { ...item, [key]: value } : item,
    );
    draft = { ...draft, knowledgePoints: points, updatedAt: new Date().toISOString() };
    scheduleSave();
  }
  function moveKnowledge(index, offset) {
    const target = index + offset;
    if (target < 0 || target >= draft.knowledgePoints.length) return;
    const points = [...draft.knowledgePoints];
    [points[index], points[target]] = [points[target], points[index]];
    draft = { ...draft, knowledgePoints: points };
    scheduleSave();
    renderKnowledge();
  }
  function removeKnowledge(index) {
    draft = { ...draft, knowledgePoints: draft.knowledgePoints.filter((_, itemIndex) => itemIndex !== index) };
    scheduleSave();
    renderKnowledge();
  }
  function appendField(card, labelText, value, multiline, onInput) {
    const wrapper = document.createElement('div');
    wrapper.className = 'field';
    const label = document.createElement('label');
    const id = `field-${crypto.randomUUID()}`;
    label.htmlFor = id;
    label.textContent = labelText;
    const input = document.createElement(multiline ? 'textarea' : 'input');
    input.id = id;
    input.value = value;
    input.addEventListener('input', () => onInput(input.value));
    wrapper.append(label, input);
    card.append(wrapper);
  }
  function renderKnowledge() {
    const list = byId('knowledge-list');
    list.replaceChildren();
    byId('knowledge-empty').hidden = draft.knowledgePoints.length > 0;
    draft.knowledgePoints.forEach((item, index) => {
      const card = document.createElement('article');
      card.className = 'knowledge-card';
      const header = document.createElement('div');
      header.className = 'card-header';
      const title = document.createElement('strong');
      title.textContent = `知识点 ${index + 1}`;
      if (item.needsReview) {
        const badge = document.createElement('span');
        badge.className = 'review-badge';
        badge.textContent = '建议复核';
        title.append(' ', badge);
      }
      const actions = document.createElement('div');
      actions.className = 'card-actions';
      actions.append(
        makeButton('上移', () => moveKnowledge(index, -1), index === 0),
        makeButton('下移', () => moveKnowledge(index, 1), index === draft.knowledgePoints.length - 1),
        makeButton('删除', () => removeKnowledge(index)),
      );
      header.append(title, actions);
      card.append(header);
      appendField(card, '名称', item.name, false, (value) => updateKnowledge(index, 'name', value));
      appendField(card, '说明', item.description, true, (value) => updateKnowledge(index, 'description', value));
      appendField(card, '观察依据', item.observationBasis, true, (value) => updateKnowledge(index, 'observationBasis', value));
      list.append(card);
    });
  }
  function validate(targetStep) {
    if (targetStep >= 2 && !draft.problemText.trim()) {
      byId('problem-error').textContent = '请先输入编程题目。';
      problem.focus();
      return false;
    }
    if (targetStep >= 3) {
      if (draft.knowledgePoints.length === 0) {
        byId('knowledge-error').textContent = '请至少添加一个知识点。';
        return false;
      }
      const incomplete = draft.knowledgePoints.some((item) =>
        !item.name.trim() || !item.description.trim() || !item.observationBasis.trim(),
      );
      if (incomplete) {
        byId('knowledge-error').textContent = '请补全每个知识点的名称、说明和观察依据。';
        return false;
      }
    }
    byId('knowledge-error').textContent = '';
    return true;
  }
  function renderReview() {
    byId('review-problem').textContent = draft.problemText;
    const container = byId('review-knowledge');
    container.replaceChildren();
    draft.knowledgePoints.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'review-item';
      const heading = document.createElement('strong');
      heading.textContent = item.name;
      const description = document.createElement('p');
      description.textContent = item.description;
      const basis = document.createElement('p');
      basis.textContent = `观察依据：${item.observationBasis}`;
      row.append(heading, description, basis);
      container.append(row);
    });
  }
  function render() {
    document.querySelectorAll('[data-step]').forEach((node) => {
      node.hidden = Number(node.dataset.step) !== draft.currentStep;
    });
    document.querySelectorAll('[data-step-indicator]').forEach((node) => {
      const active = Number(node.dataset.stepIndicator) === draft.currentStep;
      node.classList.toggle('active', active);
      if (active) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');
    });
    if (problem.value !== draft.problemText) problem.value = draft.problemText;
    byId('problem-count').textContent = `${problem.value.length} / 20000`;
    renderKnowledge();
    if (draft.currentStep === 3) renderReview();
    byId('previous').hidden = draft.currentStep === 1 || Boolean(published);
    byId('next').hidden = draft.currentStep === 3 || Boolean(published);
    byId('publish').hidden = draft.currentStep !== 3 || Boolean(published);
    byId('export').hidden = !published;
    document.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
    const result = byId('published-result');
    result.hidden = !published;
    result.textContent = published ? `已发布方案版本 ${published.version}。` : '';
  }

  problem.addEventListener('input', updateProblem);
  byId('add-kp').addEventListener('click', () => {
    const index = draft.knowledgePoints.length + 1;
    draft = { ...draft, knowledgePoints: [...draft.knowledgePoints, { localId: `kp-${index}`, name: '', description: '', observationBasis: '', needsReview: false }] };
    scheduleSave(); renderKnowledge();
  });
  byId('generate-ai').addEventListener('click', () => {
    if (!validate(2)) return;
    post('requestSuggestion', { problemText: draft.problemText });
  });
  byId('previous').addEventListener('click', () => {
    draft = { ...draft, currentStep: Math.max(1, draft.currentStep - 1) };
    scheduleSave(); render(); byId(`step-${draft.currentStep}-heading`).focus();
  });
  byId('next').addEventListener('click', () => {
    const target = Math.min(3, draft.currentStep + 1);
    if (!validate(target)) return;
    draft = { ...draft, currentStep: target };
    scheduleSave(); render(); byId(`step-${draft.currentStep}-heading`).focus();
  });
  byId('publish').addEventListener('click', () => { if (validate(3)) post('publishDraft', { draft }); });
  byId('export').addEventListener('click', () => post('exportPublishedPlan'));

  window.addEventListener('message', (event) => {
    if (event.data?.type !== 'state' || !event.data.value) return;
    const value = event.data.value;
    if (value.draft) draft = value.draft;
    busy = Boolean(value.busy);
    published = value.published;
    if (value.notice) setStatus(value.notice.message, value.notice.kind);
    render();
  });
  post('ready');
  render();
})();

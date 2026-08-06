import {
  IDimensionTemplate,
  IProfileDraftInput
} from '../models/dimensionProfile';
import { labelledInput, statusBadge } from './domHelpers';
import { CLEAR_DEFINITION, POSSIBLE_DEFINITION } from './guidedProfileForm';

export interface IEditorDimensionFields {
  container: HTMLElement;
  fieldPrefix: string;
  code?: string;
  name: HTMLInputElement;
  question: HTMLInputElement;
  support: HTMLInputElement;
  exclusion: HTMLInputElement;
  noKnownExclusion: HTMLInputElement;
  possibleAction: HTMLInputElement;
  clearAction: HTMLInputElement;
}

export interface IEditorFields {
  title: HTMLInputElement;
  problemId: HTMLInputElement;
  dimensions: IEditorDimensionFields[];
}

type ButtonTone = 'primary' | 'secondary' | 'danger';
const MAX_DIMENSIONS = 10;

export function createEditorFrame(node: HTMLElement): {
  content: HTMLDivElement;
  saveStatus: HTMLDivElement;
} {
  node.className = 'jp-BehaviorAudit-editor';
  node.setAttribute('aria-busy', 'true');
  const header = document.createElement('header');
  header.className = 'jp-BehaviorAudit-editorHeader';
  const title = document.createElement('h1');
  title.textContent = '创建题目考核方案';
  header.append(title, statusBadge('试点', 'warning'));
  const steps = document.createElement('ol');
  steps.className = 'jp-BehaviorAudit-steps';
  for (const text of ['输入题目', '确认知识点', '确认测试并发布']) {
    const step = document.createElement('li');
    step.className = 'jp-BehaviorAudit-step';
    step.dataset.step = String(steps.childElementCount + 1);
    step.textContent = text;
    steps.appendChild(step);
  }
  const saveStatus = document.createElement('div');
  saveStatus.className = 'jp-BehaviorAudit-saveStatus';
  saveStatus.setAttribute('aria-live', 'polite');
  const content = document.createElement('div');
  content.className = 'jp-BehaviorAudit-editorContent';
  node.append(header, steps, saveStatus, content);
  return { content, saveStatus };
}

export function editorButton(
  text: string,
  tone: ButtonTone,
  action: () => void
): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = [
    'jp-BehaviorAudit-button',
    `jp-BehaviorAudit-button-${tone}`
  ].join(' ');
  button.textContent = text;
  button.addEventListener('click', action);
  return button;
}

export function renderLoading(content: HTMLElement): void {
  content.textContent = '';
  const loading = document.createElement('div');
  loading.className = 'jp-BehaviorAudit-state';
  loading.setAttribute('role', 'status');
  loading.textContent = '正在载入模板…';
  content.appendChild(loading);
}

export function renderTemplateError(
  content: HTMLElement,
  onRetry: () => void
): void {
  content.textContent = '';
  const state = document.createElement('div');
  state.className = 'jp-BehaviorAudit-state jp-BehaviorAudit-state-error';
  state.setAttribute('role', 'alert');
  const heading = document.createElement('h2');
  heading.textContent = '模板载入失败';
  const detail = document.createElement('p');
  detail.textContent = '请检查服务器连接后重试。';
  state.append(heading, detail, editorButton('重试', 'secondary', onRetry));
  content.appendChild(state);
}

export function renderTemplateChoices(
  content: HTMLElement,
  templates: IDimensionTemplate[],
  onSelect: (template: IDimensionTemplate | null) => void
): void {
  content.textContent = '';
  const heading = document.createElement('h2');
  heading.textContent = '选择一个起点';
  const introduction = document.createElement('p');
  introduction.textContent =
    '推荐模板提供可直接修改的教学语言；也可以从完全自定义开始。';
  content.append(heading, introduction);

  if (templates.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'jp-BehaviorAudit-state';
    empty.setAttribute('role', 'status');
    empty.textContent = '暂无推荐模板';
    content.appendChild(empty);
  } else {
    const cards = document.createElement('div');
    cards.className = 'jp-BehaviorAudit-templateGrid';
    for (const item of templates) {
      const card = document.createElement('article');
      card.className = 'jp-BehaviorAudit-templateCard';
      const title = document.createElement('h3');
      title.textContent = item.name;
      const question = document.createElement('p');
      question.textContent = item.question;
      card.append(
        title,
        question,
        editorButton('使用此模板', 'secondary', () => {
          onSelect(item);
        })
      );
      cards.appendChild(card);
    }
    content.appendChild(cards);
  }

  const custom = editorButton('完全自定义', 'secondary', () => {
    onSelect(null);
  });
  custom.classList.add('jp-BehaviorAudit-customEntry');
  content.appendChild(custom);
  focusHeading(heading);
}

export function renderGuidedForm(
  content: HTMLElement,
  instanceId: string,
  template: IDimensionTemplate | null,
  templates: IDimensionTemplate[],
  callbacks: {
    onChange: () => void;
    onBack: () => void;
    onConfirm: () => void;
  },
  initialDraft?: IProfileDraftInput
): IEditorFields {
  content.textContent = '';
  const heading = document.createElement('h2');
  heading.textContent = '填写观察标准';
  content.appendChild(heading);
  if (template) {
    content.appendChild(renderExamples(template));
  }

  const form = document.createElement('form');
  form.className = 'jp-BehaviorAudit-form';
  form.addEventListener('submit', event => {
    event.preventDefault();
    callbacks.onConfirm();
  });
  form.addEventListener('keydown', event => {
    const target = event.target;
    if (
      event.key === 'Enter' &&
      target instanceof HTMLInputElement &&
      target.type !== 'checkbox'
    ) {
      event.preventDefault();
      callbacks.onConfirm();
    }
  });
  const profileField = (
    name: string,
    label: string,
    options: { required?: boolean; maxLength?: number }
  ): ReturnType<typeof labelledInput> =>
    labelledInput(`${instanceId}-${name}`, label, options);
  const title = profileField('title', '方案名称', {
    required: true,
    maxLength: 100
  });
  const problemId = profileField('problemId', '题目标识', {
    required: true,
    maxLength: 200
  });
  const fields: IEditorFields = {
    title: title.input,
    problemId: problemId.input,
    dimensions: []
  };
  populateProfileFields(fields, template, initialDraft);
  title.input.addEventListener('input', callbacks.onChange);
  problemId.input.addEventListener('input', callbacks.onChange);

  const dimensions = document.createElement('div');
  dimensions.className = 'jp-BehaviorAudit-dimensions';
  const addSection = document.createElement('section');
  addSection.className = 'jp-BehaviorAudit-addDimension';
  const addHeading = document.createElement('h3');
  addHeading.textContent = '添加分析维度（最多 10 个）';
  const addActions = document.createElement('div');
  addActions.className = 'jp-BehaviorAudit-dimensionAddActions';
  const templateButtons = templates.map(item => {
    const add = editorButton(`添加：${item.name}`, 'secondary', () => {
      appendDimension(item);
    });
    add.dataset.dimensionCode = item.code;
    addActions.appendChild(add);
    return add;
  });
  const addCustom = editorButton('添加自定义维度', 'secondary', () => {
    appendDimension(null);
  });
  addActions.appendChild(addCustom);
  addSection.append(addHeading, addActions);

  let nextDimensionId = 0;

  function syncDimensionControls(): void {
    const atLimit = fields.dimensions.length >= MAX_DIMENSIONS;
    const usedCodes = new Set(
      fields.dimensions
        .map(dimension => dimension.code)
        .filter((code): code is string => Boolean(code))
    );
    fields.dimensions.forEach((dimension, index) => {
      const heading = dimension.container.querySelector('h3');
      if (heading) heading.textContent = `维度 ${index + 1}`;
      const remove = dimension.container.querySelector<HTMLButtonElement>(
        '.jp-BehaviorAudit-removeDimension'
      );
      if (remove) {
        remove.hidden = fields.dimensions.length === 1;
        remove.disabled = fields.dimensions.length === 1;
      }
    });
    for (const add of templateButtons) {
      add.disabled = atLimit || usedCodes.has(add.dataset.dimensionCode ?? '');
    }
    addCustom.disabled = atLimit;
  }

  function appendDimension(
    selected: IDimensionTemplate | null,
    initial?: IProfileDraftInput['dimensions'][number]
  ): void {
    if (fields.dimensions.length >= MAX_DIMENSIONS) return;
    const serial = nextDimensionId++;
    const prefix =
      serial === 0 ? instanceId : `${instanceId}-dimension-${serial + 1}`;
    const card = document.createElement('section');
    card.className = 'jp-BehaviorAudit-dimensionCard';
    const cardHeader = document.createElement('div');
    cardHeader.className = 'jp-BehaviorAudit-dimensionCardHeader';
    const cardHeading = document.createElement('h3');
    const remove = editorButton('删除此维度', 'danger', () => {
      const index = fields.dimensions.indexOf(dimensionFields);
      if (index < 0 || fields.dimensions.length === 1) return;
      fields.dimensions.splice(index, 1);
      card.remove();
      syncDimensionControls();
      callbacks.onChange();
    });
    remove.classList.add('jp-BehaviorAudit-removeDimension');
    cardHeader.append(cardHeading, remove);

    const dimensionField = (
      name: string,
      label: string,
      options: { required?: boolean; maxLength?: number }
    ): ReturnType<typeof labelledInput> =>
      labelledInput(`${prefix}-${name}`, label, options);
    const name = dimensionField('name', '维度名称', {
      required: true,
      maxLength: 50
    });
    const question = dimensionField('question', '教学问题', {
      required: true,
      maxLength: 200
    });
    const support = dimensionField('supportStatements', '符合表现', {
      required: true,
      maxLength: 500
    });
    const exclusion = dimensionField('exclusionStatements', '排除情况', {
      required: true,
      maxLength: 500
    });
    const possibleAction = dimensionField(
      'possibleAction',
      '“可能出现”时的教学建议（可选）',
      { maxLength: 500 }
    );
    const clearAction = dimensionField(
      'clearAction',
      '“明显出现”时的教学建议（可选）',
      { maxLength: 500 }
    );
    const acknowledgement = document.createElement('div');
    acknowledgement.className = 'jp-BehaviorAudit-checkboxField';
    const noKnownExclusion = document.createElement('input');
    noKnownExclusion.type = 'checkbox';
    noKnownExclusion.id = `${prefix}-noKnownExclusion`;
    noKnownExclusion.name = 'noKnownExclusion';
    const acknowledgementLabel = document.createElement('label');
    acknowledgementLabel.htmlFor = noKnownExclusion.id;
    acknowledgementLabel.textContent = '暂无已知排除情况';
    acknowledgement.append(noKnownExclusion, acknowledgementLabel);

    const dimensionFields: IEditorDimensionFields = {
      container: card,
      fieldPrefix: prefix,
      code: initial?.code ?? selected?.code,
      name: name.input,
      question: question.input,
      support: support.input,
      exclusion: exclusion.input,
      noKnownExclusion,
      possibleAction: possibleAction.input,
      clearAction: clearAction.input
    };
    populateDimensionFields(dimensionFields, selected, initial);
    for (const input of [
      name.input,
      question.input,
      support.input,
      exclusion.input,
      possibleAction.input,
      clearAction.input
    ]) {
      input.addEventListener('input', callbacks.onChange);
    }
    noKnownExclusion.addEventListener('change', () => {
      exclusion.input.disabled = noKnownExclusion.checked;
      exclusion.input.required = !noKnownExclusion.checked;
      callbacks.onChange();
    });
    card.append(
      cardHeader,
      name.container,
      question.container,
      support.container,
      exclusion.container,
      acknowledgement,
      possibleAction.container,
      clearAction.container
    );
    fields.dimensions.push(dimensionFields);
    dimensions.appendChild(card);
    syncDimensionControls();
    if (serial > 0 && initial === undefined) callbacks.onChange();
  }

  if (initialDraft) {
    for (const dimension of initialDraft.dimensions) {
      appendDimension(
        templates.find(item => item.code === dimension.code) ?? null,
        dimension
      );
    }
  } else {
    appendDimension(template);
  }

  const actions = document.createElement('div');
  actions.className = 'jp-BehaviorAudit-actions';
  const next = editorButton('下一步：确认发布', 'primary', callbacks.onConfirm);
  actions.append(
    editorButton('返回选择模板', 'secondary', callbacks.onBack),
    next
  );
  form.append(
    title.container,
    problemId.container,
    dimensions,
    addSection,
    actions
  );
  content.appendChild(form);
  focusHeading(heading);
  return fields;
}

function renderExamples(template: IDimensionTemplate): HTMLElement {
  const examples = document.createElement('section');
  examples.className = 'jp-BehaviorAudit-examples';
  const heading = document.createElement('h3');
  heading.textContent = '模板示例';
  examples.appendChild(heading);
  for (const example of template.examples) {
    const row = document.createElement('p');
    const prefix = document.createElement('strong');
    prefix.textContent = example.kind === 'positive' ? '正例：' : '反例：';
    row.append(prefix, example.summary);
    examples.appendChild(row);
  }
  const notice = document.createElement('p');
  notice.className = 'jp-BehaviorAudit-notice';
  notice.textContent = '示例仅帮助理解维度，不会进入正式效度统计。';
  examples.appendChild(notice);
  return examples;
}

function populateProfileFields(
  fields: IEditorFields,
  template: IDimensionTemplate | null,
  initialDraft?: IProfileDraftInput
): void {
  fields.title.value = template ? `${template.name}观察方案` : '';
  if (initialDraft) {
    fields.title.value = initialDraft.title;
    fields.problemId.value = initialDraft.problem_id;
  }
}

function populateDimensionFields(
  fields: IEditorDimensionFields,
  template: IDimensionTemplate | null,
  initial?: IProfileDraftInput['dimensions'][number]
): void {
  fields.name.value = template?.name ?? '';
  fields.question.value = template?.question ?? '';
  fields.support.value =
    template?.evidence_criteria.find(item => item.direction === 'support')
      ?.statement ?? '';
  fields.exclusion.value =
    template?.evidence_criteria.find(item => item.direction === 'exclude')
      ?.statement ?? '';
  fields.possibleAction.value = template?.teaching_actions.possible ?? '';
  fields.clearAction.value = template?.teaching_actions.clear ?? '';
  if (!initial) {
    return;
  }
  fields.name.value = initial.name;
  fields.question.value = initial.question;
  fields.support.value =
    initial.evidence_criteria.find(item => item.direction === 'support')
      ?.statement ?? '';
  fields.exclusion.value =
    initial.evidence_criteria.find(item => item.direction === 'exclude')
      ?.statement ?? '';
  fields.noKnownExclusion.checked = initial.no_known_exclusion === true;
  fields.exclusion.disabled = fields.noKnownExclusion.checked;
  fields.exclusion.required = !fields.noKnownExclusion.checked;
  fields.possibleAction.value = initial.teaching_actions?.possible ?? '';
  fields.clearAction.value = initial.teaching_actions?.clear ?? '';
}

export function renderValidationErrors(
  root: ParentNode,
  instanceId: string,
  errors: Record<string, string>,
  onlyFields?: string[]
): void {
  const map: Record<string, string[]> = {
    title: ['title'],
    problemId: ['problemId'],
    name: ['name'],
    question: ['question'],
    supportStatements: ['supportStatements'],
    exclusionStatements: ['exclusionStatements'],
    teachingActions: ['possibleAction', 'clearAction']
  };
  for (const [errorKey, fieldNames] of Object.entries(map)) {
    if (onlyFields && !onlyFields.includes(errorKey)) continue;
    for (const fieldName of fieldNames) {
      const error = root.querySelector<HTMLDivElement>(
        `#${instanceId}-${fieldName}-error`
      );
      const input = root.querySelector<HTMLInputElement>(
        `#${instanceId}-${fieldName}`
      );
      const message = errors[errorKey] ?? '';
      if (error) {
        error.textContent = message;
      }
      input?.setAttribute('aria-invalid', message ? 'true' : 'false');
    }
  }
}

export function renderConfirmation(
  content: HTMLElement,
  payload: IProfileDraftInput,
  onBack: () => void,
  onPublish: () => Promise<boolean>
): void {
  content.textContent = '';
  const heading = document.createElement('h2');
  heading.textContent = '确认并发布试点';
  const disclaimer = document.createElement('p');
  disclaimer.className = 'jp-BehaviorAudit-pilotDisclaimer';
  disclaimer.textContent =
    '此方案将作为“试点”发布。结果用于辅助教学观察，不用于成绩或处分。';
  const summary = document.createElement('dl');
  summary.className = 'jp-BehaviorAudit-summary';
  appendSummary(summary, '方案名称', payload.title);
  appendSummary(summary, '题目标识', payload.problem_id);
  appendSummary(summary, '维度数量', `共 ${payload.dimensions.length} 个维度`);
  payload.dimensions.forEach((dimension, index) => {
    appendSummary(
      summary,
      `维度 ${index + 1}`,
      `${dimension.name}：${dimension.question}`
    );
  });
  appendSummary(summary, '可能出现', POSSIBLE_DEFINITION);
  appendSummary(summary, '明显出现', CLEAR_DEFINITION);
  const actions = document.createElement('div');
  actions.className = 'jp-BehaviorAudit-actions';
  const publishButton = editorButton('发布试点方案', 'primary', () => {
    if (publishButton.disabled) {
      return;
    }
    publishButton.disabled = true;
    publishButton.setAttribute('aria-busy', 'true');
    void onPublish().then(succeeded => {
      publishButton.setAttribute('aria-busy', 'false');
      publishButton.disabled = succeeded;
    });
  });
  publishButton.setAttribute('aria-busy', 'false');
  actions.append(editorButton('返回修改', 'secondary', onBack), publishButton);
  content.append(heading, disclaimer, summary, actions);
  focusHeading(heading);
}

export function setCurrentStep(root: ParentNode, step: 1 | 2 | 3): void {
  for (const item of Array.from(
    root.querySelectorAll<HTMLElement>('.jp-BehaviorAudit-step')
  )) {
    if (item.dataset.step === String(step)) {
      item.setAttribute('aria-current', 'step');
    } else {
      item.removeAttribute('aria-current');
    }
  }
}

function focusHeading(heading: HTMLHeadingElement): void {
  heading.tabIndex = -1;
  heading.focus();
}

function appendSummary(
  list: HTMLDListElement,
  term: string,
  description: string
): void {
  const termNode = document.createElement('dt');
  termNode.textContent = term;
  const descriptionNode = document.createElement('dd');
  descriptionNode.textContent = description;
  list.append(termNode, descriptionNode);
}

import { IAssessmentTest } from '../models/assessmentPlan';
import {
  canPublishAssessmentPlan,
  IAssessmentPlanState
} from './assessmentPlanForm';
import {
  advancedSettings,
  assistStatus,
  authoringButton,
  focusStepHeading,
  IAssistStatus
} from './advancedSettings';
import { labelledInput, labelledTextarea, statusBadge } from './domHelpers';

export interface ITestConfirmationStepCallbacks {
  onUpdateTest: (id: string, changes: Partial<IAssessmentTest>) => void;
  onRemoveTest: (id: string) => void;
  onMoveTest: (id: string, direction: -1 | 1) => void;
  onAddTest: () => void;
  onGenerateTests: () => void;
  onBack: () => void;
  onConfirmTests: (confirmed: boolean) => void;
  onPublish: () => void;
}

function testCard(
  instanceId: string,
  state: IAssessmentPlanState,
  test: IAssessmentTest,
  index: number,
  callbacks: ITestConfirmationStepCallbacks
): HTMLElement {
  const card = document.createElement('section');
  card.className = 'jp-BehaviorAudit-dimensionCard';
  const header = document.createElement('div');
  header.className = 'jp-BehaviorAudit-dimensionCardHeader';
  const heading = document.createElement('h3');
  heading.textContent = `测试 ${index + 1}`;
  header.append(
    heading,
    statusBadge(
      test.source === 'teacher' ? '教师编辑' : 'AI 建议',
      test.source === 'teacher' ? 'neutral' : 'info'
    )
  );
  const name = labelledInput(
    `${instanceId}-test-${index}-name`,
    `测试 ${index + 1} 名称`,
    { required: true, maxLength: 120 }
  );
  name.input.value = test.name;
  name.input.addEventListener('input', () => {
    callbacks.onUpdateTest(test.id, { name: name.input.value });
  });
  const input = labelledTextarea(
    `${instanceId}-test-${index}-input`,
    `测试 ${index + 1} 输入`,
    { maxLength: 4000, rows: 3 }
  );
  input.textarea.value = test.input;
  input.textarea.addEventListener('input', () => {
    callbacks.onUpdateTest(test.id, { input: input.textarea.value });
  });
  const expected = labelledTextarea(
    `${instanceId}-test-${index}-expected`,
    `测试 ${index + 1} 预期输出`,
    { maxLength: 4000, rows: 3 }
  );
  expected.textarea.value = test.expected;
  expected.textarea.addEventListener('input', () => {
    callbacks.onUpdateTest(test.id, { expected: expected.textarea.value });
  });

  const pointFieldset = document.createElement('fieldset');
  pointFieldset.className = 'jp-BehaviorAudit-testPointLinks';
  const legend = document.createElement('legend');
  legend.textContent = '对应知识点';
  pointFieldset.appendChild(legend);
  for (const point of state.knowledgePoints) {
    const row = document.createElement('label');
    row.className = 'jp-BehaviorAudit-choice';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = test.knowledge_point_ids.includes(point.id);
    checkbox.addEventListener('change', () => {
      const selected = checkbox.checked
        ? [...new Set([...test.knowledge_point_ids, point.id])]
        : test.knowledge_point_ids.filter(id => id !== point.id);
      callbacks.onUpdateTest(test.id, {
        knowledge_point_ids: selected
      });
    });
    row.append(checkbox, point.name);
    pointFieldset.appendChild(row);
  }
  const enabledRow = document.createElement('label');
  enabledRow.className = 'jp-BehaviorAudit-choice';
  const enabled = document.createElement('input');
  enabled.type = 'checkbox';
  enabled.checked = test.enabled;
  enabled.addEventListener('change', () => {
    callbacks.onUpdateTest(test.id, { enabled: enabled.checked });
  });
  enabledRow.append(enabled, '启用此测试');
  const advanced = advancedSettings('高级设置', pointFieldset, enabledRow);

  const actions = document.createElement('div');
  actions.className = 'jp-BehaviorAudit-inlineActions';
  const up = authoringButton('上移', 'secondary', () => {
    callbacks.onMoveTest(test.id, -1);
  });
  up.disabled = index === 0;
  const down = authoringButton('下移', 'secondary', () => {
    callbacks.onMoveTest(test.id, 1);
  });
  down.disabled = index === state.assessmentTests.length - 1;
  actions.append(
    up,
    down,
    authoringButton('删除', 'danger', () => {
      callbacks.onRemoveTest(test.id);
    })
  );
  card.append(
    header,
    name.container,
    input.container,
    expected.container,
    advanced,
    actions
  );
  return card;
}

export function renderTestConfirmationStep(
  content: HTMLElement,
  instanceId: string,
  state: IAssessmentPlanState,
  requestStatus: IAssistStatus,
  callbacks: ITestConfirmationStepCallbacks
): void {
  content.textContent = '';
  const heading = document.createElement('h2');
  heading.textContent = '确认测试并发布';
  const introduction = document.createElement('p');
  introduction.textContent =
    '逐项核对输入和预期输出。AI 测试只是候选，教师修改后的内容才进入方案。';
  const boundary = document.createElement('p');
  boundary.className = 'jp-BehaviorAudit-pilotDisclaimer';
  boundary.textContent =
    '本阶段只保存测试方案，不会执行测试，也不会据此显示“已掌握”或“未掌握”。';
  content.append(heading, introduction, boundary, assistStatus(requestStatus));
  if (
    state.assessmentTests.length > 0 &&
    state.confirmations.tests_hash === null
  ) {
    const recheck = document.createElement('p');
    recheck.className = 'jp-BehaviorAudit-notice';
    recheck.textContent = '这些测试已保留但尚未确认，请重新核对后勾选确认。';
    content.appendChild(recheck);
  }

  const list = document.createElement('div');
  list.className = 'jp-BehaviorAudit-dimensions';
  if (state.assessmentTests.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'jp-BehaviorAudit-state';
    empty.setAttribute('role', 'status');
    empty.textContent = '尚无测试。可以生成 AI 建议，或添加一个手工测试。';
    list.appendChild(empty);
  } else {
    state.assessmentTests.forEach((test, index) => {
      list.appendChild(testCard(instanceId, state, test, index, callbacks));
    });
  }
  content.appendChild(list);

  const testActions = document.createElement('div');
  testActions.className = 'jp-BehaviorAudit-inlineActions';
  const generate = authoringButton(
    state.assessmentTests.length > 0 ? '重新生成测试建议' : '生成测试建议',
    'secondary',
    callbacks.onGenerateTests
  );
  generate.disabled = requestStatus.status === 'loading';
  testActions.append(
    generate,
    authoringButton('添加手工测试', 'secondary', callbacks.onAddTest)
  );
  content.appendChild(testActions);

  const confirmation = document.createElement('div');
  confirmation.className = 'jp-BehaviorAudit-checkboxField';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.id = `${instanceId}-testConfirmation`;
  checkbox.checked = state.confirmations.tests_hash !== null;
  checkbox.disabled = state.assessmentTests.length === 0;
  checkbox.setAttribute(
    'aria-describedby',
    `${instanceId}-testConfirmation-error`
  );
  checkbox.addEventListener('change', () => {
    callbacks.onConfirmTests(checkbox.checked);
  });
  const label = document.createElement('label');
  label.htmlFor = checkbox.id;
  label.textContent = '我已核对这些测试，确认后才可发布';
  const error = document.createElement('div');
  error.id = `${instanceId}-testConfirmation-error`;
  error.className = 'jp-BehaviorAudit-fieldError';
  error.setAttribute('aria-live', 'polite');
  confirmation.append(checkbox, label, error);
  content.appendChild(confirmation);

  const actions = document.createElement('div');
  actions.className = 'jp-BehaviorAudit-actions';
  const publish = authoringButton(
    '发布试点方案',
    'primary',
    callbacks.onPublish
  );
  publish.disabled = !canPublishAssessmentPlan(state);
  actions.append(
    authoringButton('返回修改知识点', 'secondary', callbacks.onBack),
    publish
  );
  content.appendChild(actions);
  focusStepHeading(heading);
}

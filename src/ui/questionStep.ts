import { SubmissionContract } from '../models/assessmentPlan';
import { IAssessmentPlanState } from './assessmentPlanForm';
import {
  advancedSettings,
  authoringButton,
  focusStepHeading
} from './advancedSettings';
import { labelledInput, labelledSelect, labelledTextarea } from './domHelpers';

export interface IQuestionStepCallbacks {
  onChange: (
    patch: Partial<
      Pick<
        IAssessmentPlanState,
        'title' | 'problemId' | 'problemStatement' | 'submissionContract'
      >
    >
  ) => void;
  onContinue: (teacherFocus: string[]) => void;
}

export function renderQuestionStep(
  content: HTMLElement,
  instanceId: string,
  state: IAssessmentPlanState,
  callbacks: IQuestionStepCallbacks
): void {
  content.textContent = '';
  const heading = document.createElement('h2');
  heading.textContent = '输入题目';
  const introduction = document.createElement('p');
  introduction.textContent =
    '普通情况下只需填写题目；知识点可以选填，留空时下一步会提供 AI 建议。';

  const form = document.createElement('form');
  form.className = 'jp-BehaviorAudit-form';
  const title = labelledInput(`${instanceId}-title`, '方案名称', {
    required: true,
    maxLength: 200
  });
  title.input.value = state.title;
  title.input.addEventListener('input', () => {
    callbacks.onChange({ title: title.input.value });
  });
  const problemId = labelledInput(`${instanceId}-problemId`, '题目标识', {
    required: true,
    maxLength: 200
  });
  problemId.input.value = state.problemId;
  problemId.input.addEventListener('input', () => {
    callbacks.onChange({ problemId: problemId.input.value });
  });
  const statement = labelledTextarea(
    `${instanceId}-problemStatement`,
    '完整题目',
    { required: true, maxLength: 10000, rows: 8 }
  );
  statement.textarea.value = state.problemStatement;
  statement.textarea.addEventListener('input', () => {
    callbacks.onChange({ problemStatement: statement.textarea.value });
  });
  const kind = labelledSelect(`${instanceId}-submissionKind`, '答题形式', [
    { value: 'function', label: '实现一个函数' },
    { value: 'stdin_stdout', label: '标准输入与输出' }
  ]);
  kind.select.value = state.submissionContract.kind;
  const entrypoint = labelledInput(`${instanceId}-entrypoint`, '函数名', {
    required: state.submissionContract.kind === 'function',
    maxLength: 100
  });
  entrypoint.input.value =
    state.submissionContract.kind === 'function'
      ? state.submissionContract.entrypoint
      : '';
  entrypoint.container.hidden = state.submissionContract.kind !== 'function';
  kind.select.addEventListener('change', () => {
    const contract: SubmissionContract =
      kind.select.value === 'function'
        ? { kind: 'function', entrypoint: entrypoint.input.value }
        : { kind: 'stdin_stdout' };
    entrypoint.container.hidden = contract.kind !== 'function';
    entrypoint.input.required = contract.kind === 'function';
    callbacks.onChange({ submissionContract: contract });
  });
  entrypoint.input.addEventListener('input', () => {
    callbacks.onChange({
      submissionContract: {
        kind: 'function',
        entrypoint: entrypoint.input.value
      }
    });
  });
  const focus = labelledTextarea(
    `${instanceId}-teacherFocus`,
    '我想考察的知识点（可选，每行一个）',
    { maxLength: 1000, rows: 4 }
  );
  focus.textarea.value = state.teacherFocus.join('\n');

  const note = document.createElement('p');
  note.className = 'jp-BehaviorAudit-notice';
  note.textContent =
    '方案标识和答题形式会自动推断；需要时可在这里覆盖。行为观察规则由系统作为内部证据配置。';
  const advanced = advancedSettings(
    '高级设置',
    title.container,
    problemId.container,
    kind.container,
    entrypoint.container,
    note
  );

  const actions = document.createElement('div');
  actions.className = 'jp-BehaviorAudit-actions';
  actions.append(
    authoringButton('下一步：确认知识点', 'primary', () => {
      const teacherFocus = focus.textarea.value
        .split(/\r?\n/)
        .map(value => value.trim())
        .filter(
          (value, index, values) => value && values.indexOf(value) === index
        )
        .slice(0, 10);
      callbacks.onContinue(teacherFocus);
    })
  );
  form.addEventListener('submit', event => {
    event.preventDefault();
    actions.querySelector<HTMLButtonElement>('button')?.click();
  });
  form.append(statement.container, focus.container, advanced, actions);
  content.append(heading, introduction, form);
  focusStepHeading(heading);
}

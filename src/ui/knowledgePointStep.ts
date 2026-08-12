import { IKnowledgePointSuggestion } from '../models/assessmentPlan';
import {
  IAssessmentKnowledgePointEditor,
  IAssessmentPlanState,
  KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS,
  missingKnowledgePointFields
} from './assessmentPlanForm';
import {
  advancedSettings,
  assistStatus,
  authoringButton,
  focusStepHeading,
  IAssistStatus
} from './advancedSettings';
import { labelledInput, labelledTextarea, statusBadge } from './domHelpers';

export interface IKnowledgePointStepCallbacks {
  onAdoptSuggestion: (suggestion: IKnowledgePointSuggestion) => void;
  onIgnoreSuggestion: (id: string) => void;
  onAddPoint: (input: { name: string; description: string }) => void;
  onUpdatePoint: (
    id: string,
    changes: Partial<IAssessmentKnowledgePointEditor>
  ) => void;
  onRemovePoint: (id: string) => void;
  onMovePoint: (id: string, direction: -1 | 1) => void;
  onRequestSuggestions: () => void;
  onBack: () => void;
  onConfirm: () => void;
}

function showRequiredFieldError(
  field: HTMLInputElement | HTMLTextAreaElement,
  error: HTMLElement,
  label: string,
  isMissing: boolean
): void {
  if (!isMissing) return;
  field.setAttribute('aria-invalid', 'true');
  error.textContent = `请填写${label}`;
}

function pointCard(
  instanceId: string,
  point: IAssessmentKnowledgePointEditor,
  index: number,
  total: number,
  callbacks: IKnowledgePointStepCallbacks
): HTMLElement {
  const missing = new Set(missingKnowledgePointFields(point));
  const card = document.createElement('section');
  card.className = 'jp-BehaviorAudit-dimensionCard';
  const header = document.createElement('div');
  header.className = 'jp-BehaviorAudit-dimensionCardHeader';
  const heading = document.createElement('h3');
  heading.textContent = `知识点 ${index + 1}`;
  const source = statusBadge(
    point.source === 'teacher' ? '教师添加' : 'AI 建议（已采用）',
    point.source === 'teacher' ? 'neutral' : 'info'
  );
  header.append(heading, source);

  const name = labelledInput(
    `${instanceId}-point-${index}-name`,
    `知识点 ${index + 1} 名称`,
    { required: true, maxLength: 80 }
  );
  name.input.value = point.name;
  name.input.addEventListener('input', () => {
    callbacks.onUpdatePoint(point.id, { name: name.input.value });
  });
  const description = labelledTextarea(
    `${instanceId}-point-${index}-description`,
    `知识点 ${index + 1} 说明`,
    { maxLength: 500, rows: 3 }
  );
  description.textarea.value = point.description;
  description.textarea.addEventListener('input', () => {
    callbacks.onUpdatePoint(point.id, {
      description: description.textarea.value
    });
  });

  const evidenceQuestion = labelledTextarea(
    `${instanceId}-point-${index}-evidenceQuestion`,
    '过程观察问题',
    { required: true, maxLength: 200, rows: 2 }
  );
  evidenceQuestion.textarea.value = point.evidenceQuestion;
  evidenceQuestion.textarea.addEventListener('input', () => {
    callbacks.onUpdatePoint(point.id, {
      evidenceQuestion: evidenceQuestion.textarea.value
    });
  });
  const support = labelledTextarea(
    `${instanceId}-point-${index}-support`,
    '支持表现',
    { required: true, maxLength: 500, rows: 2 }
  );
  support.textarea.value = point.supportStatement;
  support.textarea.addEventListener('input', () => {
    callbacks.onUpdatePoint(point.id, {
      supportStatement: support.textarea.value
    });
  });
  const exclusion = labelledTextarea(
    `${instanceId}-point-${index}-exclusion`,
    '排除情况',
    { required: true, maxLength: 500, rows: 2 }
  );
  exclusion.textarea.value = point.exclusionStatement;
  exclusion.textarea.addEventListener('input', () => {
    callbacks.onUpdatePoint(point.id, {
      exclusionStatement: exclusion.textarea.value
    });
  });
  showRequiredFieldError(
    name.input,
    name.error,
    KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.name,
    missing.has('name')
  );
  showRequiredFieldError(
    evidenceQuestion.textarea,
    evidenceQuestion.error,
    KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.evidenceQuestion,
    missing.has('evidenceQuestion')
  );
  showRequiredFieldError(
    support.textarea,
    support.error,
    KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.supportStatement,
    missing.has('supportStatement')
  );
  showRequiredFieldError(
    exclusion.textarea,
    exclusion.error,
    KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.exclusionStatement,
    missing.has('exclusionStatement')
  );
  const advanced = advancedSettings(
    '高级观察设置',
    evidenceQuestion.container,
    support.container,
    exclusion.container
  );
  advanced.open =
    missing.has('evidenceQuestion') ||
    missing.has('supportStatement') ||
    missing.has('exclusionStatement');

  const actions = document.createElement('div');
  actions.className = 'jp-BehaviorAudit-inlineActions';
  const up = authoringButton('上移', 'secondary', () => {
    callbacks.onMovePoint(point.id, -1);
  });
  up.disabled = index === 0;
  const down = authoringButton('下移', 'secondary', () => {
    callbacks.onMovePoint(point.id, 1);
  });
  down.disabled = index === total - 1;
  actions.append(
    up,
    down,
    authoringButton('删除', 'danger', () => {
      callbacks.onRemovePoint(point.id);
    })
  );
  card.append(header, name.container, description.container, advanced, actions);
  return card;
}

export function renderKnowledgePointStep(
  content: HTMLElement,
  instanceId: string,
  state: IAssessmentPlanState,
  suggestions: IKnowledgePointSuggestion[],
  requestStatus: IAssistStatus,
  callbacks: IKnowledgePointStepCallbacks
): void {
  content.textContent = '';
  const heading = document.createElement('h2');
  heading.textContent = '确认本题知识点';
  const introduction = document.createElement('p');
  introduction.textContent =
    'AI 内容只是建议。请采用需要的项目，并直接修改为本节课实际考察的知识点。';
  content.append(heading, introduction, assistStatus(requestStatus));

  if (suggestions.length > 0) {
    const candidateSection = document.createElement('section');
    candidateSection.className = 'jp-BehaviorAudit-candidateSection';
    const candidateHeading = document.createElement('h3');
    candidateHeading.textContent = 'AI 建议';
    candidateSection.appendChild(candidateHeading);
    for (const suggestion of suggestions) {
      const card = document.createElement('article');
      card.className = 'jp-BehaviorAudit-suggestionCard';
      const title = document.createElement('strong');
      title.textContent = suggestion.name;
      const description = document.createElement('p');
      description.textContent = suggestion.description;
      const actions = document.createElement('div');
      actions.className = 'jp-BehaviorAudit-inlineActions';
      actions.append(
        authoringButton('采用', 'primary', () => {
          callbacks.onAdoptSuggestion(suggestion);
        }),
        authoringButton('忽略', 'secondary', () => {
          callbacks.onIgnoreSuggestion(suggestion.id);
        })
      );
      card.append(title, description, actions);
      candidateSection.appendChild(card);
    }
    content.appendChild(candidateSection);
  }

  const confirmed = document.createElement('section');
  confirmed.className = 'jp-BehaviorAudit-confirmedSection';
  const confirmedHeading = document.createElement('h3');
  confirmedHeading.textContent = '最终知识点';
  confirmed.appendChild(confirmedHeading);
  if (state.knowledgePoints.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'jp-BehaviorAudit-notice';
    empty.textContent = '尚未添加知识点。可以采用建议或手工添加。';
    confirmed.appendChild(empty);
  } else {
    const list = document.createElement('div');
    list.className = 'jp-BehaviorAudit-dimensions';
    state.knowledgePoints.forEach((point, index) => {
      list.appendChild(
        pointCard(
          instanceId,
          point,
          index,
          state.knowledgePoints.length,
          callbacks
        )
      );
    });
    confirmed.appendChild(list);
  }

  const custom = document.createElement('section');
  custom.className = 'jp-BehaviorAudit-addDimension';
  const customHeading = document.createElement('h3');
  customHeading.textContent = '添加自定义知识点';
  const name = labelledInput(`${instanceId}-newPointName`, '新知识点名称', {
    maxLength: 80
  });
  const description = labelledTextarea(
    `${instanceId}-newPointDescription`,
    '新知识点说明（可选）',
    { maxLength: 500, rows: 2 }
  );
  custom.append(
    customHeading,
    name.container,
    description.container,
    authoringButton('添加自定义知识点', 'secondary', () => {
      callbacks.onAddPoint({
        name: name.input.value,
        description: description.textarea.value
      });
    })
  );
  content.append(confirmed, custom);

  const actions = document.createElement('div');
  actions.className =
    'jp-BehaviorAudit-actions jp-BehaviorAudit-actions-spread';
  const suggest = authoringButton(
    suggestions.length > 0 ? '重新获取 AI 建议' : '获取 AI 建议',
    'secondary',
    callbacks.onRequestSuggestions
  );
  suggest.disabled = requestStatus.status === 'loading';
  const confirm = authoringButton(
    '我已确认以上知识点',
    'primary',
    callbacks.onConfirm
  );
  confirm.disabled =
    state.knowledgePoints.length === 0 || requestStatus.status === 'loading';
  actions.append(
    authoringButton('返回修改题目', 'secondary', callbacks.onBack),
    suggest,
    confirm
  );
  content.appendChild(actions);
  focusStepHeading(heading);
}

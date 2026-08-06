import {
  EvidenceStatus,
  IAnalysisResult,
  IDimensionResult,
  IEvidenceClaim,
  IReviewPayload
} from '../models/analysisResult';
import { IDimensionProfileVersion } from '../models/dimensionProfile';

export { IAnalysisResult } from '../models/analysisResult';

type ReviewChoice =
  | 'confirm'
  | 'not_observed'
  | 'possible'
  | 'clear'
  | 'uncertain';

const RESULT_LABELS: Record<EvidenceStatus, string> = {
  observed: '可能出现',
  not_observed: '未发现明显证据',
  insufficient_evidence: '数据不足',
  not_computable: '当前记录无法分析'
};

function element<K extends keyof HTMLElementTagNameMap>(
  name: K,
  className?: string
): HTMLElementTagNameMap[K] {
  const node = document.createElement(name);
  if (className) {
    node.className = className;
  }
  return node;
}

function appendText(
  parent: HTMLElement,
  text: string,
  className?: string
): void {
  const node = element('p', className);
  node.textContent = safeText(text);
  parent.appendChild(node);
}

function safeText(value: string): string {
  return value
    .replace(/(?:[A-Za-z]:)?\/(?:[^\s/]+\/)+[^\s]*/g, '[已隐藏路径]')
    .replace(/\\(?:[^\s\\]+\\)+[^\s]*/g, '[已隐藏路径]');
}

function conclusion(result: IDimensionResult): string {
  if (result.decision.status === 'needs_review') {
    return '需要教师复核';
  }
  if (result.decision.status === 'partial') {
    return '部分结果，建议结合课堂观察';
  }
  if (result.decision.status === 'failed') {
    return '当前分析未完成';
  }
  const status = result.decision.final_evidence_status;
  if (status === 'observed') {
    return result.decision.final_level_code === 'clear'
      ? '明显出现'
      : '可能出现';
  }
  return status === null ? '需要教师复核' : RESULT_LABELS[status];
}

function explanation(result: IDimensionResult): string {
  const aiExplanation = result.ai_result?.explanation?.trim();
  if (aiExplanation) {
    return aiExplanation;
  }
  const reason = result.data_quality?.reason?.trim();
  if (reason) {
    return reason;
  }
  return '当前记录仅反映已采集到的编程行为，建议结合学生说明进一步了解。';
}

function teachingAction(
  result: IDimensionResult,
  profile: IDimensionProfileVersion
): string | null {
  const dimension = profile.dimensions.find(
    value => value.code === result.dimension_code
  );
  const actions = dimension?.teaching_actions;
  if (!actions || result.decision.status !== 'resolved') {
    return null;
  }
  if (result.decision.final_evidence_status === 'not_observed') {
    return actions.not_observed ?? null;
  }
  if (result.decision.final_evidence_status !== 'observed') return null;
  return result.decision.final_level_code === 'clear'
    ? actions.clear
    : actions.possible;
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  cell_execution_scheduled: '开始运行',
  cell_execution_success: '运行完成',
  cell_execution_error: '运行报错',
  code_input_completed: '完成代码编辑',
  idle: '暂停操作',
  page_hidden: '离开当前页面',
  page_visible: '返回当前页面'
};

function eventTypeLabel(eventType?: string): string | null {
  if (!eventType) return null;
  return EVENT_TYPE_LABELS[eventType] ?? '编程行为记录';
}

function evidenceDetails(
  dimensionCode: string,
  claims: IEvidenceClaim[]
): HTMLDetailsElement {
  const details = element('details', 'jp-BehaviorAudit-evidenceDetails');
  details.dataset.stateKey = `evidence:${dimensionCode}`;
  const summary = element('summary');
  summary.textContent = `查看 ${claims.length} 条证据`;
  details.appendChild(summary);
  const list = element('ul', 'jp-BehaviorAudit-evidenceList');
  for (const claim of claims) {
    const item = element('li');
    const metadata = [claim.occurred_at, eventTypeLabel(claim.event_type)]
      .filter((value): value is string => Boolean(value))
      .join(' · ');
    if (metadata) {
      const detail = element('div', 'jp-BehaviorAudit-evidenceMeta');
      detail.textContent = safeText(metadata);
      item.appendChild(detail);
    }
    const statement = element('div');
    statement.textContent = safeText(claim.claim);
    item.appendChild(statement);
    list.appendChild(item);
  }
  details.appendChild(list);
  return details;
}

function reviewPayload(
  choice: ReviewChoice,
  result: IDimensionResult,
  comment: string
): IReviewPayload {
  const claims = result.ai_result?.evidence_claims ?? [];
  const visibleIds = [...new Set(claims.map(claim => claim.event_id))];
  const revision = result.review?.revision ?? 0;
  if (choice === 'confirm') {
    const resolved =
      result.decision.status === 'resolved' &&
      result.decision.final_evidence_status !== null;
    return {
      revision,
      decision_status: resolved ? 'resolved' : 'needs_review',
      evidence_status: resolved ? result.decision.final_evidence_status : null,
      level_code: resolved ? result.decision.final_level_code : null,
      evidence_event_ids: visibleIds,
      reason_code: resolved ? 'teacher_confirmed' : 'uncertain',
      comment
    };
  }
  if (choice === 'uncertain') {
    return {
      revision,
      decision_status: 'needs_review',
      evidence_status: null,
      level_code: null,
      evidence_event_ids: visibleIds,
      reason_code: 'uncertain',
      comment
    };
  }
  return {
    revision,
    decision_status: 'resolved',
    evidence_status: choice === 'not_observed' ? 'not_observed' : 'observed',
    level_code: choice === 'not_observed' ? null : choice,
    evidence_event_ids: visibleIds,
    reason_code: 'teacher_correction',
    comment
  };
}

function reviewForm(
  result: IDimensionResult,
  sessionId: string,
  onReview: (
    dimensionCode: string,
    correction: IReviewPayload
  ) => void | Promise<void>,
  isCurrent: () => boolean
): HTMLDetailsElement {
  const details = element('details', 'jp-BehaviorAudit-reviewDetails');
  const revision = result.review?.revision ?? 0;
  details.dataset.stateKey = `review:${sessionId}:${result.dimension_code}:${revision}`;
  const summary = element('summary');
  summary.textContent = '教师复核';
  details.appendChild(summary);
  const form = element('form', 'jp-BehaviorAudit-reviewForm');
  form.dataset.reviewDimension = result.dimension_code;
  form.dataset.reviewRevision = String(revision);
  const fieldset = element('fieldset');
  const legend = element('legend');
  legend.textContent = '复核结论';
  fieldset.appendChild(legend);
  const choices: Array<[ReviewChoice, string]> = [
    ['confirm', '确认当前结论'],
    ['not_observed', '修正为未发现明显证据'],
    ['possible', '修正为可能出现'],
    ['clear', '修正为明显出现'],
    ['uncertain', '仍不确定']
  ];
  const groupName = `review-${result.dimension_code}`;
  for (const [value, label] of choices) {
    const row = element('label', 'jp-BehaviorAudit-choice');
    const input = element('input') as HTMLInputElement;
    input.type = 'radio';
    input.name = groupName;
    input.value = value;
    input.checked = value === 'confirm';
    row.append(input, document.createTextNode(label));
    fieldset.appendChild(row);
  }
  const commentLabel = element('label', 'jp-BehaviorAudit-label');
  const commentId = `review-comment-${result.dimension_code}`;
  commentLabel.htmlFor = commentId;
  commentLabel.textContent = '复核说明';
  const comment = element(
    'textarea',
    'jp-BehaviorAudit-input'
  ) as HTMLTextAreaElement;
  comment.id = commentId;
  comment.required = true;
  const status = element('div', 'jp-BehaviorAudit-fieldError');
  status.setAttribute('aria-live', 'polite');
  const submit = element(
    'button',
    'jp-BehaviorAudit-button'
  ) as HTMLButtonElement;
  submit.type = 'submit';
  submit.textContent = '提交复核';
  let pending = false;
  form.append(fieldset, commentLabel, comment, status, submit);
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (pending) {
      return;
    }
    if (!comment.value.trim()) {
      status.textContent = '请填写复核说明';
      comment.setAttribute('aria-invalid', 'true');
      comment.focus();
      return;
    }
    const selected = form.querySelector<HTMLInputElement>(
      `input[name="${groupName}"]:checked`
    );
    const choice = (selected?.value ?? 'confirm') as ReviewChoice;
    comment.removeAttribute('aria-invalid');
    pending = true;
    submit.disabled = true;
    submit.setAttribute('aria-busy', 'true');
    status.textContent = '正在提交复核…';
    Promise.resolve(
      onReview(
        result.dimension_code,
        reviewPayload(choice, result, comment.value.trim())
      )
    ).then(
      () => {
        if (!isCurrent()) return;
        status.textContent = '复核已提交，正在更新结果。';
      },
      () => {
        if (!isCurrent()) return;
        pending = false;
        status.textContent = '复核提交失败，请重试。';
        submit.disabled = false;
        submit.removeAttribute('aria-busy');
      }
    );
  });
  details.appendChild(form);
  return details;
}

function dimensionCard(
  result: IDimensionResult,
  sessionId: string,
  profile: IDimensionProfileVersion,
  onReview: (
    dimensionCode: string,
    correction: IReviewPayload
  ) => void | Promise<void>,
  isCurrent: () => boolean
): HTMLElement {
  const card = element('section', 'jp-BehaviorAudit-resultCard');
  const title = element('h2');
  title.textContent =
    profile.dimensions.find(value => value.code === result.dimension_code)
      ?.name ?? '未命名观察维度';
  const label = element('p', 'jp-BehaviorAudit-resultConclusion');
  label.textContent = conclusion(result);
  card.append(title, label);
  appendText(card, explanation(result), 'jp-BehaviorAudit-resultExplanation');
  const claims = result.ai_result?.evidence_claims ?? [];
  if (claims.length > 0) {
    card.append(evidenceDetails(result.dimension_code, claims));
  }
  const action = teachingAction(result, profile);
  if (action) {
    appendText(
      card,
      `下一步教学建议：${action}`,
      'jp-BehaviorAudit-teachingAction'
    );
  }
  if (result.data_quality?.reason) {
    appendText(
      card,
      `数据质量：${result.data_quality.reason}`,
      'jp-BehaviorAudit-dataQuality'
    );
  }
  card.appendChild(reviewForm(result, sessionId, onReview, isCurrent));
  return card;
}

const SOURCE_LABELS: Record<IDimensionResult['decision']['source'], string> = {
  llm_evidence: 'AI 证据分析',
  coverage: '数据覆盖分析'
};

function analysisDetails(
  result: IAnalysisResult,
  profile: IDimensionProfileVersion
): HTMLDetailsElement {
  const details = element('details', 'jp-BehaviorAudit-analysisDetails');
  details.dataset.stateKey = 'analysis-details';
  const summary = element('summary');
  summary.textContent = '分析详情';
  details.appendChild(summary);
  appendText(
    details,
    `方案版本：v${result.profile_version}；结果状态：${
      result.status === 'partial' ? '部分完成' : '已完成'
    }`
  );
  appendText(
    details,
    `模型：${result.provenance.model_name || '未提供'}；模型版本：${
      result.provenance.model_version || '未提供'
    }`
  );
  appendText(
    details,
    `提示词版本：${result.provenance.prompt_version || '未提供'}；信号字典版本：${
      result.provenance.signal_dictionary_version || '未提供'
    }；分析流程版本：${result.provenance.analysis_pipeline_version || '未提供'}`
  );
  for (const dimension of result.dimension_results) {
    const name =
      profile.dimensions.find(value => value.code === dimension.dimension_code)
        ?.name ?? '未命名观察维度';
    const source = SOURCE_LABELS[dimension.decision.source];
    const confidence =
      dimension.ai_result === null || dimension.ai_result === undefined
        ? ''
        : `；模型自评，不代表正确概率：${dimension.ai_result.confidence}`;
    appendText(details, `${name}：${source}${confidence}`);
  }
  return details;
}

export function renderAnalysisResult(
  result: IAnalysisResult,
  profile: IDimensionProfileVersion,
  onReview: (
    dimensionCode: string,
    correction: IReviewPayload
  ) => void | Promise<void>,
  isCurrent: () => boolean = () => true
): HTMLElement {
  const root = element('section', 'jp-BehaviorAudit-results');
  root.dataset.sessionId = result.session_id;
  const heading = element('h2');
  heading.textContent = '本次会话结果';
  if (result.error_code === 'ai_not_configured') {
    heading.textContent = '本次会话数据';
    const state = element('div', 'jp-BehaviorAudit-resultEmpty');
    state.setAttribute('role', 'status');
    state.textContent =
      '数据采集完成，尚未进行 AI 分析。请配置 AI 服务后重试分析。';
    root.append(heading, state);
    return root;
  }
  const dimensions = result.dimension_results ?? [];
  const hasUsableDimensionResult = dimensions.some(
    value =>
      value.decision.status === 'resolved' ||
      (value.ai_result !== null && value.ai_result !== undefined)
  );
  if (result.error_code === 'ai_analysis_failed' && !hasUsableDimensionResult) {
    heading.textContent = '本次会话数据';
    const state = element('div', 'jp-BehaviorAudit-resultEmpty');
    state.setAttribute('role', 'status');
    const qualityReasons = [
      ...new Set(
        dimensions
          .map(value => value.data_quality?.reason?.trim())
          .filter((value): value is string => Boolean(value))
      )
    ];
    state.textContent = `行为采集已完成，AI 分析未完成，可重试分析。${
      qualityReasons.length > 0
        ? ` 数据质量：${qualityReasons.join('；')}。`
        : ''
    }`;
    root.append(heading, state);
    return root;
  }
  const completed = dimensions.filter(
    value =>
      value.decision.status === 'resolved' &&
      !['insufficient_evidence', 'not_computable'].includes(
        value.decision.final_evidence_status ?? ''
      )
  ).length;
  const pending = dimensions.filter(
    value =>
      value.decision.status === 'needs_review' ||
      value.decision.status === 'partial'
  ).length;
  const unavailable = dimensions.filter(value =>
    ['insufficient_evidence', 'not_computable'].includes(
      value.decision.final_evidence_status ?? ''
    )
  ).length;
  const failed = dimensions.filter(
    value => value.decision.status === 'failed'
  ).length;
  const summary = element('p', 'jp-BehaviorAudit-resultSummary');
  summary.textContent = `完成维度 ${completed}；待复核 ${pending}；数据不足或无法分析 ${unavailable}；失败 ${failed}；方案版本 v${result.profile_version}`;
  root.append(heading, summary);
  for (const dimension of dimensions) {
    root.appendChild(
      dimensionCard(dimension, result.session_id, profile, onReview, isCurrent)
    );
  }
  root.appendChild(analysisDetails(result, profile));
  return root;
}

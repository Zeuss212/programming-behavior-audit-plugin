import { ServerConnection } from '@jupyterlab/services';
import { Message } from '@lumino/messaging';
import { Widget } from '@lumino/widgets';

import { ApiError } from '../models/apiError';
import {
  IAssessmentProfileDraft,
  IKnowledgePointSuggestion
} from '../models/assessmentPlan';
import { IDimensionProfileVersion } from '../models/dimensionProfile';
import {
  generateAssessmentTests,
  recommendKnowledgePoints
} from '../services/assessmentPlanApi';
import {
  addTeacherAssessmentTest,
  addTeacherKnowledgePoint,
  assessmentProblemContext,
  buildAssessmentProfileDraft,
  confirmAssessmentTests,
  confirmKnowledgePoints,
  createAssessmentPlanState,
  IAssessmentPlanState,
  invalidateAssessmentTestConfirmation,
  mergeAssessmentTestSuggestions,
  mergeKnowledgeSuggestions,
  moveAssessmentTest,
  moveKnowledgePoint,
  removeAssessmentTest,
  removeKnowledgePoint,
  updateAssessmentPlanContext,
  updateAssessmentTest,
  updateKnowledgePoint,
  validateAssessmentPlanState
} from './assessmentPlanForm';
import { IAssistStatus } from './advancedSettings';
import { GuidedProfileAutosave } from './guidedProfileAutosave';
import { createEditorFrame, setCurrentStep } from './guidedProfileSteps';
import { renderKnowledgePointStep } from './knowledgePointStep';
import { renderQuestionStep } from './questionStep';
import { renderTestConfirmationStep } from './testConfirmationStep';

export {
  IGuidedDimensionForm,
  validateGuidedDimension
} from './guidedProfileForm';

let editorCount = 0;

type Step = 1 | 2 | 3;

function inferredEntrypoint(statement: string): string | null {
  const patterns = [
    /(?:\bdef|\bfunction|\bimplement|\bwrite|函数|实现|编写)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/i,
    /([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?:函数|function)/i
  ];
  for (const pattern of patterns) {
    const match = pattern.exec(statement);
    if (match) {
      return match[1];
    }
  }
  return null;
}

function inferredTitle(statement: string): string {
  const firstLine =
    statement
      .split(/\r?\n/)
      .map(line => line.trim())
      .find(Boolean) ?? '未命名题目';
  return `${firstLine.slice(0, 180)} · 考核方案`;
}

export class GuidedProfileEditor extends Widget {
  private readonly serverSettings: ServerConnection.ISettings;
  private readonly onPublished: (profile: IDimensionProfileVersion) => void;
  private readonly contentNode: HTMLDivElement;
  private readonly saveStatusNode: HTMLDivElement;
  private readonly instanceId: string;
  private readonly defaultProblemId: string;
  private readonly autosave: GuidedProfileAutosave;
  private readonly subtle: SubtleCrypto;
  private state = createAssessmentPlanState();
  private suggestions: IKnowledgePointSuggestion[] = [];
  private knowledgeStatus: IAssistStatus = { status: 'idle' };
  private testStatus: IAssistStatus = { status: 'idle' };
  private assistGeneration = 0;
  private confirmationGeneration = 0;
  private authoringRevision = 0;
  private publishPending = false;
  private submissionContractCustomized = false;

  constructor(options: {
    serverSettings: ServerConnection.ISettings;
    onPublished: (profile: IDimensionProfileVersion) => void;
    subtle?: SubtleCrypto;
  }) {
    const node = document.createElement('section');
    super({ node });
    this.serverSettings = options.serverSettings;
    this.onPublished = options.onPublished;
    this.subtle = options.subtle ?? globalThis.crypto.subtle;
    this.instanceId = `jp-BehaviorAudit-editor-${++editorCount}`;
    this.defaultProblemId = `question-${Date.now().toString(36)}-${editorCount}`;
    this.id = 'myextension-guided-profile-editor';
    this.title.label = '分析方案';
    this.title.caption = '创建题目考核方案';
    this.title.closable = true;

    const frame = createEditorFrame(node);
    this.saveStatusNode = frame.saveStatus;
    this.contentNode = frame.content;
    this.autosave = new GuidedProfileAutosave(
      this.serverSettings,
      status => {
        this.saveStatusNode.textContent = status;
      },
      saved => {
        this.applySavedDimensionCodes(saved);
      }
    );
    this.autosave.beginDraft();
    this.node.setAttribute('aria-busy', 'false');
    this.showQuestion();
  }

  override dispose(): void {
    this.assistGeneration += 1;
    this.confirmationGeneration += 1;
    this.autosave.dispose();
    super.dispose();
  }

  protected override onBeforeHide(msg: Message): void {
    this.node.hidden = true;
    this.node.style.pointerEvents = 'none';
    super.onBeforeHide(msg);
  }

  protected override onBeforeShow(msg: Message): void {
    this.node.hidden = false;
    this.node.style.removeProperty('pointer-events');
    super.onBeforeShow(msg);
  }

  private showQuestion(): void {
    this.setStep(1);
    renderQuestionStep(this.contentNode, this.instanceId, this.state, {
      onChange: patch => {
        if (patch.submissionContract !== undefined) {
          this.submissionContractCustomized = true;
        }
        const contextChanged =
          patch.problemStatement !== undefined ||
          patch.submissionContract !== undefined;
        const next = updateAssessmentPlanContext(this.state, patch);
        if (next === this.state) {
          return;
        }
        this.state = next;
        if (contextChanged) {
          this.assistGeneration += 1;
          this.suggestions = [];
          this.knowledgeStatus = { status: 'idle' };
          this.testStatus = { status: 'idle' };
        }
        this.markDraftChanged();
      },
      onContinue: teacherFocus => {
        this.continueFromQuestion(teacherFocus);
      }
    });
  }

  private continueFromQuestion(teacherFocus: string[]): void {
    this.state = this.withQuestionDefaults(this.state);
    const errors = validateAssessmentPlanState(this.state);
    this.renderQuestionErrors(errors);
    if (
      errors.title ||
      errors.problemId ||
      errors.problemStatement ||
      errors.entrypoint
    ) {
      this.saveStatusNode.textContent = '请先完成题目信息';
      return;
    }

    this.state = {
      ...this.state,
      teacherFocus: [...teacherFocus]
    };
    for (const name of teacherFocus) {
      this.state = addTeacherKnowledgePoint(this.state, {
        name,
        description: ''
      });
    }
    this.knowledgeStatus = { status: 'idle' };
    this.showKnowledgePoints();
    if (this.state.knowledgePoints.length === 0) {
      void this.requestKnowledgeSuggestions();
    } else {
      this.markDraftChanged();
    }
  }

  private showKnowledgePoints(): void {
    this.setStep(2);
    renderKnowledgePointStep(
      this.contentNode,
      this.instanceId,
      this.state,
      this.suggestions,
      this.knowledgeStatus,
      {
        onAdoptSuggestion: suggestion => {
          const next = mergeKnowledgeSuggestions(this.state, [suggestion]);
          if (next !== this.state) {
            this.state = next;
            this.suggestions = this.suggestions.filter(
              item => item.id !== suggestion.id
            );
            this.markDraftChanged();
          }
          this.showKnowledgePoints();
        },
        onIgnoreSuggestion: id => {
          this.suggestions = this.suggestions.filter(item => item.id !== id);
          this.showKnowledgePoints();
        },
        onAddPoint: input => {
          const next = addTeacherKnowledgePoint(this.state, input);
          if (next === this.state) {
            this.knowledgeStatus = {
              status: 'error',
              message: input.name.trim()
                ? '该知识点已存在或已达到数量上限。'
                : '请输入知识点名称。'
            };
          } else {
            this.state = next;
            this.knowledgeStatus = { status: 'idle' };
            this.markDraftChanged();
          }
          this.showKnowledgePoints();
        },
        onUpdatePoint: (id, changes) => {
          const next = updateKnowledgePoint(this.state, id, changes);
          if (next !== this.state) {
            this.state = next;
            this.markDraftChanged();
          }
        },
        onRemovePoint: id => {
          this.state = removeKnowledgePoint(this.state, id);
          this.markDraftChanged();
          this.showKnowledgePoints();
        },
        onMovePoint: (id, direction) => {
          this.state = moveKnowledgePoint(this.state, id, direction);
          this.markDraftChanged();
          this.showKnowledgePoints();
        },
        onRequestSuggestions: () => {
          void this.requestKnowledgeSuggestions();
        },
        onBack: () => {
          this.cancelAssistRequests();
          this.showQuestion();
        },
        onConfirm: () => {
          void this.confirmKnowledgeAndContinue();
        }
      }
    );
  }

  private async requestKnowledgeSuggestions(): Promise<void> {
    const generation = ++this.assistGeneration;
    this.knowledgeStatus = {
      status: 'loading',
      message: '正在根据题目生成知识点建议…'
    };
    this.showKnowledgePoints();
    try {
      const response = await recommendKnowledgePoints(
        this.serverSettings,
        assessmentProblemContext(this.state),
        this.state.teacherFocus
      );
      if (!this.isCurrentAssist(generation)) {
        return;
      }
      this.suggestions = response.knowledge_points;
      this.knowledgeStatus = {
        status: 'success',
        message: `已生成 ${response.knowledge_points.length} 条建议，请按需采用。`
      };
      this.showKnowledgePoints();
    } catch (error) {
      if (!this.isCurrentAssist(generation)) {
        return;
      }
      this.knowledgeStatus = {
        status: 'error',
        message: this.assistFailureMessage(error, 'knowledge')
      };
      this.showKnowledgePoints();
    }
  }

  private async confirmKnowledgeAndContinue(): Promise<void> {
    if (this.knowledgeStatus.status === 'loading') {
      return;
    }
    this.knowledgeStatus = {
      status: 'loading',
      message: '正在确认知识点…'
    };
    this.showKnowledgePoints();
    const generation = ++this.confirmationGeneration;
    const authoringRevision = this.authoringRevision;
    const snapshot = this.state;
    try {
      const confirmed = await confirmKnowledgePoints(snapshot, this.subtle);
      if (!this.isCurrentConfirmation(generation)) {
        return;
      }
      if (this.authoringRevision !== authoringRevision) {
        this.knowledgeStatus = {
          status: 'error',
          message: '内容已修改，未采用旧确认结果，请重新确认。'
        };
        this.showKnowledgePoints();
        return;
      }
      this.state = confirmed;
      this.markDraftChanged();
      this.testStatus = { status: 'idle' };
      this.showTests();
      if (this.state.assessmentTests.length === 0) {
        void this.requestTestSuggestions();
      }
    } catch {
      if (!this.isCurrentConfirmation(generation)) {
        return;
      }
      this.knowledgeStatus = {
        status: 'error',
        message: '请补全每个知识点的名称和观察依据后再确认。'
      };
      this.showKnowledgePoints();
    }
  }

  private showTests(): void {
    this.setStep(3);
    renderTestConfirmationStep(
      this.contentNode,
      this.instanceId,
      this.state,
      this.testStatus,
      {
        onUpdateTest: (id, changes) => {
          const next = updateAssessmentTest(this.state, id, changes);
          if (next === this.state) {
            return;
          }
          this.state = next;
          this.markDraftChanged();
          if (
            changes.knowledge_point_ids !== undefined ||
            changes.enabled !== undefined
          ) {
            this.showTests();
          } else {
            this.syncTestConfirmationControls();
          }
        },
        onRemoveTest: id => {
          this.state = removeAssessmentTest(this.state, id);
          this.markDraftChanged();
          this.showTests();
        },
        onMoveTest: (id, direction) => {
          this.state = moveAssessmentTest(this.state, id, direction);
          this.markDraftChanged();
          this.showTests();
        },
        onAddTest: () => {
          this.addManualTest();
        },
        onGenerateTests: () => {
          void this.requestTestSuggestions();
        },
        onBack: () => {
          this.cancelAssistRequests();
          this.showKnowledgePoints();
        },
        onConfirmTests: confirmed => {
          if (confirmed) {
            void this.confirmTests();
          } else {
            this.state = invalidateAssessmentTestConfirmation(this.state);
            this.markDraftChanged();
            this.showTests();
          }
        },
        onPublish: () => {
          void this.publish();
        }
      }
    );
  }

  private addManualTest(): void {
    const covered = new Set(
      this.state.assessmentTests
        .filter(test => test.enabled)
        .flatMap(test => test.knowledge_point_ids)
    );
    const target =
      this.state.knowledgePoints.find(point => !covered.has(point.id)) ??
      this.state.knowledgePoints[0];
    if (!target) {
      this.testStatus = {
        status: 'error',
        message: '请先返回并确认至少一个知识点。'
      };
      this.showTests();
      return;
    }
    const kind =
      this.state.submissionContract.kind === 'function'
        ? 'function_call'
        : 'stdin_stdout';
    this.state = addTeacherAssessmentTest(this.state, {
      name: `手工测试 ${this.state.assessmentTests.length + 1}`,
      knowledge_point_ids: [target.id],
      kind,
      input: '',
      expected: '',
      enabled: true
    });
    this.testStatus = { status: 'idle' };
    this.markDraftChanged();
    this.showTests();
  }

  private async requestTestSuggestions(): Promise<void> {
    const generation = ++this.assistGeneration;
    this.testStatus = {
      status: 'loading',
      message: '正在生成可编辑的测试建议…'
    };
    this.showTests();
    try {
      const response = await generateAssessmentTests(
        this.serverSettings,
        assessmentProblemContext(this.state),
        this.state.knowledgePoints
      );
      if (!this.isCurrentAssist(generation)) {
        return;
      }
      this.state = mergeAssessmentTestSuggestions(
        this.state,
        response.assessment_tests
      );
      this.testStatus = {
        status: 'success',
        message: '测试建议已生成。教师已编辑的测试会保留，请逐项核对。'
      };
      this.markDraftChanged();
      this.showTests();
    } catch (error) {
      if (!this.isCurrentAssist(generation)) {
        return;
      }
      this.testStatus = {
        status: 'error',
        message: this.assistFailureMessage(error, 'tests')
      };
      this.showTests();
    }
  }

  private async confirmTests(): Promise<void> {
    if (this.testStatus.status === 'loading') {
      return;
    }
    this.testStatus = {
      status: 'loading',
      message: '正在确认测试覆盖…'
    };
    this.showTests();
    const generation = ++this.confirmationGeneration;
    const authoringRevision = this.authoringRevision;
    const snapshot = this.state;
    try {
      const confirmed = await confirmAssessmentTests(snapshot, this.subtle);
      if (!this.isCurrentConfirmation(generation)) {
        return;
      }
      if (this.authoringRevision !== authoringRevision) {
        this.testStatus = {
          status: 'error',
          message: '内容已修改，未采用旧确认结果，请重新确认。'
        };
        this.showTests();
        return;
      }
      this.state = confirmed;
      this.testStatus = {
        status: 'success',
        message: '测试已确认，可以发布试点方案。'
      };
      this.markDraftChanged();
      this.showTests();
    } catch (error) {
      if (!this.isCurrentConfirmation(generation)) {
        return;
      }
      this.testStatus = {
        status: 'error',
        message:
          error instanceof Error
            ? error.message
            : '测试尚未满足发布要求，请检查后重试。'
      };
      this.showTests();
    }
  }

  private async publish(): Promise<void> {
    if (this.publishPending) {
      return;
    }
    const button = this.publishButton();
    this.publishPending = true;
    if (button) {
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
    }
    const profile = await this.autosave.publish();
    this.publishPending = false;
    if (profile && !this.isDisposed) {
      this.onPublished(profile);
      return;
    }
    if (!this.isDisposed) {
      this.testStatus = {
        status: 'error',
        message: '发布失败，请检查草稿保存状态后重试。'
      };
      this.showTests();
    }
  }

  private markDraftChanged(): void {
    this.authoringRevision += 1;
    this.autosave.markChanged(() => this.buildDraftOrNull());
  }

  private buildDraftOrNull() {
    const state = this.withQuestionDefaults(this.state);
    const { knowledgePoints: _knowledgePoints, ...blockingErrors } =
      validateAssessmentPlanState(state);
    if (Object.keys(blockingErrors).length > 0) {
      return null;
    }
    return buildAssessmentProfileDraft(state);
  }

  private withQuestionDefaults(
    state: IAssessmentPlanState
  ): IAssessmentPlanState {
    const defaults: Parameters<typeof updateAssessmentPlanContext>[1] = {};
    if (!state.title.trim()) {
      defaults.title = inferredTitle(state.problemStatement);
    }
    if (!state.problemId.trim()) {
      defaults.problemId = this.defaultProblemId;
    }
    if (
      !this.submissionContractCustomized &&
      state.submissionContract.kind === 'function' &&
      !state.submissionContract.entrypoint.trim()
    ) {
      const entrypoint = inferredEntrypoint(state.problemStatement);
      defaults.submissionContract = entrypoint
        ? { kind: 'function', entrypoint }
        : { kind: 'stdin_stdout' };
    }
    return updateAssessmentPlanContext(state, defaults);
  }

  private applySavedDimensionCodes(
    saved: Parameters<
      NonNullable<ConstructorParameters<typeof GuidedProfileAutosave>[2]>
    >[0]
  ): void {
    if (saved.schema_version !== 2) {
      return;
    }
    const codes = new Map(
      (saved as IAssessmentProfileDraft).dimensions.map(dimension => [
        dimension.knowledge_point_id,
        dimension.code
      ])
    );
    this.state = {
      ...this.state,
      knowledgePoints: this.state.knowledgePoints.map(point => ({
        ...point,
        dimensionCode: codes.get(point.id) ?? point.dimensionCode
      }))
    };
  }

  private renderQuestionErrors(
    errors: ReturnType<typeof validateAssessmentPlanState>
  ): void {
    const fields: Array<
      ['title' | 'problemId' | 'problemStatement' | 'entrypoint', string]
    > = [
      ['title', 'title'],
      ['problemId', 'problemId'],
      ['problemStatement', 'problemStatement'],
      ['entrypoint', 'entrypoint']
    ];
    for (const [errorKey, fieldName] of fields) {
      const message = errors[errorKey] ?? '';
      const input = this.node.querySelector<HTMLElement>(
        `#${this.instanceId}-${fieldName}`
      );
      const error = this.node.querySelector<HTMLElement>(
        `#${this.instanceId}-${fieldName}-error`
      );
      input?.setAttribute('aria-invalid', message ? 'true' : 'false');
      if (error) {
        error.textContent = message;
      }
    }
    if (errors.title || errors.problemId || errors.entrypoint) {
      const advanced = this.node.querySelector<HTMLDetailsElement>(
        '.jp-BehaviorAudit-advancedSettings'
      );
      if (advanced) {
        advanced.open = true;
      }
    }
  }

  private syncTestConfirmationControls(): void {
    const confirmation = this.node.querySelector<HTMLInputElement>(
      `#${this.instanceId}-testConfirmation`
    );
    if (confirmation) {
      confirmation.checked = false;
    }
    const publish = this.publishButton();
    if (publish) {
      publish.disabled = true;
    }
  }

  private publishButton(): HTMLButtonElement | undefined {
    return Array.from(
      this.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(button => button.textContent === '发布试点方案');
  }

  private assistFailureMessage(
    error: unknown,
    kind: 'knowledge' | 'tests'
  ): string {
    if (error instanceof ApiError && error.code === 'ai_not_configured') {
      return kind === 'knowledge'
        ? 'AI 服务尚未配置，可先手工添加知识点，或在左侧完成 AI 配置后重试。'
        : 'AI 服务尚未配置，可添加手工测试，或在左侧完成 AI 配置后重试。';
    }
    if (error instanceof ApiError) {
      const subject = kind === 'knowledge' ? '知识点建议' : '测试建议';
      const messages: Readonly<Record<string, string>> = {
        ai_provider_timeout: `${subject}生成超时，当前草稿已保留，可重试或手工继续。`,
        ai_provider_network_error:
          '无法连接 AI 服务，请检查网络、DNS、TLS 或代理；当前草稿已保留。',
        ai_provider_auth_failed:
          'AI 鉴权失败，请检查 API Key 和模型权限；也可先手工继续。',
        ai_provider_rate_limited:
          'AI 请求受限，请稍后重试，并检查额度或并发限制。',
        ai_provider_request_rejected:
          'AI 拒绝了请求，请检查 Base URL、模型和参数兼容性。',
        ai_provider_unavailable:
          'AI 服务暂时不可用，请稍后重试；当前草稿已保留。',
        ai_response_truncated:
          'AI 输出被截断，请减少知识点数量或描述长度后重试。',
        ai_response_invalid:
          'AI 返回格式无效，请检查模型是否支持结构化 JSON 输出。'
      };
      const mapped = messages[error.code];
      if (mapped) {
        return mapped;
      }
    }
    return kind === 'knowledge'
      ? 'AI 暂时不可用，可继续手工添加知识点。'
      : 'AI 暂时不可用，可继续添加和编辑手工测试。';
  }

  private cancelAssistRequests(): void {
    this.assistGeneration += 1;
    this.confirmationGeneration += 1;
    this.knowledgeStatus = { status: 'idle' };
    this.testStatus = { status: 'idle' };
  }

  private isCurrentAssist(generation: number): boolean {
    return !this.isDisposed && generation === this.assistGeneration;
  }

  private isCurrentConfirmation(generation: number): boolean {
    return !this.isDisposed && generation === this.confirmationGeneration;
  }

  private setStep(step: Step): void {
    setCurrentStep(this.node, step);
  }
}

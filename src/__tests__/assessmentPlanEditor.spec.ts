import { webcrypto } from 'node:crypto';

import { ServerConnection } from '@jupyterlab/services';
import { Widget } from '@lumino/widgets';

import {
  IAssessmentProfileDraft,
  IAssessmentProfileDraftInput,
  IAssessmentProfileVersion,
  IKnowledgePointSuggestion
} from '../models/assessmentPlan';
import { ApiError } from '../models/apiError';
import { IDimensionProfileVersion } from '../models/dimensionProfile';
import * as requestModule from '../request';
import {
  createAssessmentPlanState,
  IAssessmentPlanState
} from '../ui/assessmentPlanForm';
import { GuidedProfileEditor } from '../ui/guidedProfileEditor';

const settings = {} as ServerConnection.ISettings;
const profileId = '12345678-1234-1234-1234-123456789abc';

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 16; index += 1) {
    await Promise.resolve();
  }
  await new Promise(resolve => setTimeout(resolve, 0));
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 16; index += 1) {
    await Promise.resolve();
  }
}

async function waitFor(
  predicate: () => boolean,
  message: string
): Promise<void> {
  for (let index = 0; index < 50; index += 1) {
    if (predicate()) {
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  throw new Error(message);
}

function fieldByLabel(
  root: ParentNode,
  label: string
): HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement {
  const labelNode = Array.from(root.querySelectorAll('label')).find(
    value => value.textContent === label
  );
  if (!labelNode?.htmlFor) {
    throw new Error(`Missing labelled field: ${label}`);
  }
  const field = root.querySelector(`#${labelNode.htmlFor}`);
  if (
    !(
      field instanceof HTMLInputElement ||
      field instanceof HTMLTextAreaElement ||
      field instanceof HTMLSelectElement
    )
  ) {
    throw new Error(`Label does not reference a field: ${label}`);
  }
  return field;
}

function setField(
  field: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
  value: string
): void {
  field.value = value;
  field.dispatchEvent(new Event('input', { bubbles: true }));
  field.dispatchEvent(new Event('change', { bubbles: true }));
}

function clickButton(editor: GuidedProfileEditor, text: string): void {
  const target = Array.from(editor.node.querySelectorAll('button')).find(
    button => button.textContent === text
  );
  if (!target) {
    throw new Error(`Missing button: ${text}`);
  }
  target.click();
}

function completeQuestion(
  editor: GuidedProfileEditor,
  options: { statement?: string; teacherFocus?: string } = {}
): void {
  setField(fieldByLabel(editor.node, '方案名称'), '平均分知识点分析');
  setField(fieldByLabel(editor.node, '题目标识'), 'average-debug');
  setField(
    fieldByLabel(editor.node, '完整题目'),
    options.statement ??
      '编写 calculate_average(numbers)，返回数字列表的平均值。'
  );
  setField(fieldByLabel(editor.node, '函数名'), 'calculate_average');
  if (options.teacherFocus !== undefined) {
    setField(
      fieldByLabel(editor.node, '我想考察的知识点（可选，每行一个）'),
      options.teacherFocus
    );
  }
}

function suggestion(id: string, name: string): IKnowledgePointSuggestion {
  return {
    id,
    name,
    description: `正确应用${name}。`,
    evidence_question: `是否正确应用${name}？`,
    support_statement: `代码和验证过程显示正确应用${name}。`,
    exclusion_statement: `偶然输出不计入${name}。`,
    source: 'ai_suggestion',
    order: 0
  };
}

function createEditor(
  onPublished: (profile: IDimensionProfileVersion) => void = jest.fn(),
  subtle: SubtleCrypto = webcrypto.subtle as SubtleCrypto
): GuidedProfileEditor {
  return new GuidedProfileEditor({
    serverSettings: settings,
    onPublished,
    subtle
  });
}

function controlledSubtle(): {
  subtle: SubtleCrypto;
  delayNext: () => void;
  release: () => void;
} {
  let shouldDelay = false;
  let releasePending: (() => void) | null = null;
  const digest = jest.fn(
    (
      algorithm: AlgorithmIdentifier,
      data: BufferSource
    ): Promise<ArrayBuffer> => {
      const compute = () =>
        webcrypto.subtle.digest(
          algorithm as Parameters<typeof webcrypto.subtle.digest>[0],
          data as Parameters<typeof webcrypto.subtle.digest>[1]
        ) as Promise<ArrayBuffer>;
      if (!shouldDelay) {
        return compute();
      }
      return new Promise<ArrayBuffer>((resolve, reject) => {
        releasePending = () => {
          shouldDelay = false;
          void compute().then(resolve, reject);
        };
      });
    }
  );
  return {
    subtle: { digest } as unknown as SubtleCrypto,
    delayNext: () => {
      shouldDelay = true;
    },
    release: () => {
      const pending = releasePending;
      releasePending = null;
      if (!pending) {
        throw new Error('No digest is waiting to be released');
      }
      pending();
    }
  };
}

describe('teacher-first GuidedProfileEditor', () => {
  let request: jest.SpyInstance;

  beforeEach(() => {
    request = jest.spyOn(requestModule, 'requestAPI');
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('opens directly on the question and never requests the legacy template list', () => {
    const editor = createEditor();

    expect(editor.node.querySelector('h2')?.textContent).toBe('输入题目');
    expect(
      Array.from(editor.node.querySelectorAll('.jp-BehaviorAudit-step')).map(
        step => step.textContent
      )
    ).toEqual(['输入题目', '确认知识点', '确认测试并发布']);
    expect(request).not.toHaveBeenCalled();

    Widget.attach(editor, document.body);
    editor.hide();
    expect(editor.node.hidden).toBe(true);
    expect(editor.node.style.pointerEvents).toBe('none');
    editor.show();
    expect(editor.node.hidden).toBe(false);
    Widget.detach(editor);
    editor.dispose();
  });

  it('turns teacher-entered lines into final knowledge points without requiring AI', () => {
    const editor = createEditor();
    completeQuestion(editor, {
      teacherFocus: '循环边界\n平均值计算\n循环边界'
    });

    clickButton(editor, '下一步：确认知识点');

    expect(editor.node.querySelector('h2')?.textContent).toBe('确认本题知识点');
    const cards = editor.node.querySelectorAll<HTMLElement>(
      '.jp-BehaviorAudit-dimensionCard'
    );
    expect(cards).toHaveLength(2);
    expect(cards[0].querySelector<HTMLInputElement>('input')?.value).toBe(
      '循环边界'
    );
    expect(cards[1].querySelector<HTMLInputElement>('input')?.value).toBe(
      '平均值计算'
    );
    expect(request).not.toHaveBeenCalled();

    clickButton(editor, '返回修改题目');
    expect(
      fieldByLabel(editor.node, '我想考察的知识点（可选，每行一个）').value
    ).toBe('循环边界\n平均值计算');
  });

  it('autofills legacy observation fields before rendering knowledge cards', () => {
    const editor = createEditor();
    const internal = editor as unknown as {
      state: IAssessmentPlanState;
      showKnowledgePoints: () => void;
    };
    internal.state = {
      ...createAssessmentPlanState(),
      title: '默认参数分析',
      problemId: 'default-parameter',
      problemStatement: '实现带默认参数的函数。',
      submissionContract: {
        kind: 'function',
        entrypoint: 'calculate'
      },
      knowledgePoints: [
        {
          id: 'KP_A1B2C3D4',
          name: '函数默认参数',
          description: '为可选参数设置默认值。',
          source: 'ai_suggestion',
          order: 0,
          evidenceQuestion: undefined,
          supportStatement: null,
          exclusionStatement: 7
        }
      ] as unknown as IAssessmentPlanState['knowledgePoints']
    };

    internal.showKnowledgePoints();

    expect(fieldByLabel(editor.node, '过程观察问题').value).toBe(
      '学生是否通过代码、运行和修改过程正确应用“函数默认参数”？'
    );
    expect(fieldByLabel(editor.node, '支持表现').value).toBe(
      '代码与验证过程显示学生正确应用了“函数默认参数”。'
    );
    expect(fieldByLabel(editor.node, '排除情况').value).toBe(
      '只出现一次偶然正确输出，或缺少与“函数默认参数”相关的验证，不计入。'
    );
  });

  it('shows a manual fallback when default AI recommendation is unavailable', async () => {
    request.mockRejectedValueOnce(new Error('offline'));
    const editor = createEditor();
    completeQuestion(editor);

    clickButton(editor, '下一步：确认知识点');
    await flushPromises();

    expect(request.mock.calls[0][0]).toBe('assessment-assist/knowledge-points');
    expect(editor.node.textContent).toContain(
      'AI 暂时不可用，可继续手工添加知识点'
    );
    expect(editor.node.textContent).toContain('添加自定义知识点');
  });

  it.each([
    ['ai_provider_timeout', '生成超时，当前草稿已保留'],
    ['ai_provider_network_error', '检查网络、DNS、TLS 或代理'],
    ['ai_provider_auth_failed', '检查 API Key 和模型权限'],
    ['ai_provider_rate_limited', '稍后重试，并检查额度或并发限制'],
    ['ai_provider_request_rejected', '检查 Base URL、模型和参数兼容性'],
    ['ai_provider_unavailable', 'AI 服务暂时不可用，请稍后重试'],
    ['ai_response_truncated', '减少知识点数量或描述长度后重试'],
    ['ai_response_invalid', '检查模型是否支持结构化 JSON 输出']
  ])(
    'shows actionable knowledge guidance for %s',
    async (code, expectedMessage) => {
      request.mockRejectedValueOnce(new ApiError(502, code, 'safe', true));
      const editor = createEditor();
      completeQuestion(editor);

      clickButton(editor, '下一步：确认知识点');
      await flushPromises();

      expect(editor.node.textContent).toContain(expectedMessage);
      expect(editor.node.textContent).toContain('添加自定义知识点');
      editor.dispose();
    }
  );

  it('keeps manual tests when AI test regeneration times out', async () => {
    request.mockRejectedValue(
      new ApiError(502, 'ai_provider_timeout', 'safe', true)
    );
    const editor = createEditor();
    completeQuestion(editor, { teacherFocus: '循环边界' });
    clickButton(editor, '下一步：确认知识点');
    clickButton(editor, '我已确认以上知识点');
    await waitFor(
      () =>
        Array.from(editor.node.querySelectorAll('button')).some(
          button => button.textContent === '添加手工测试'
        ),
      'Test confirmation step did not render'
    );

    clickButton(editor, '添加手工测试');
    setField(fieldByLabel(editor.node, '测试 1 名称'), '教师保留的边界测试');
    clickButton(editor, '重新生成测试建议');
    await flushPromises();

    expect(editor.node.textContent).toContain(
      '测试建议生成超时，当前草稿已保留'
    );
    expect(fieldByLabel(editor.node, '测试 1 名称').value).toBe(
      '教师保留的边界测试'
    );
    expect(editor.node.textContent).toContain('添加手工测试');
    editor.dispose();
  });

  it('lets an ordinary teacher continue with only the question text', async () => {
    request.mockResolvedValueOnce({ knowledge_points: [] });
    const editor = createEditor();
    setField(
      fieldByLabel(editor.node, '完整题目'),
      '输入若干整数，输出它们的平均值。'
    );

    clickButton(editor, '下一步：确认知识点');
    await flushPromises();

    expect(editor.node.querySelector('h2')?.textContent).toBe('确认本题知识点');
    const requestBody = JSON.parse(
      String((request.mock.calls[0][2] as RequestInit).body)
    );
    expect(requestBody.problem_context).toMatchObject({
      statement: '输入若干整数，输出它们的平均值。',
      submission_contract: { kind: 'stdin_stdout' }
    });
  });

  it('autosaves a question-only Profile v2 draft before knowledge points exist', async () => {
    jest.useFakeTimers();
    request.mockImplementation(
      async (path: string, _settings: unknown, init?: RequestInit) => {
        if (path !== 'dimension-profiles') {
          throw new Error(`Unexpected path: ${path}`);
        }
        const input = JSON.parse(
          String(init?.body)
        ) as IAssessmentProfileDraftInput;
        return {
          ...input,
          profile_id: profileId,
          revision: 1
        };
      }
    );
    const editor = createEditor();
    setField(
      fieldByLabel(editor.node, '完整题目'),
      '输入若干整数，输出它们的平均值。'
    );

    jest.advanceTimersByTime(500);
    await flushMicrotasks();

    expect(request).toHaveBeenCalledTimes(1);
    const draft = JSON.parse(
      String((request.mock.calls[0][2] as RequestInit).body)
    ) as IAssessmentProfileDraftInput;
    expect(draft).toMatchObject({
      schema_version: 2,
      problem_context: {
        statement: '输入若干整数，输出它们的平均值。',
        submission_contract: { kind: 'stdin_stdout' }
      },
      knowledge_points: [],
      assessment_tests: [],
      dimensions: []
    });
    expect(draft.problem_id).toMatch(/^question-/);
    expect(draft.title).toContain('输入若干整数，输出它们的平均值。');
    editor.dispose();
  });

  it('ignores an old AI response after the teacher changes the question', async () => {
    let resolveOld!: (value: {
      knowledge_points: IKnowledgePointSuggestion[];
    }) => void;
    const oldResponse = new Promise<{
      knowledge_points: IKnowledgePointSuggestion[];
    }>(resolve => {
      resolveOld = resolve;
    });
    request.mockReturnValueOnce(oldResponse).mockResolvedValueOnce({
      knowledge_points: [suggestion('KP_B1C2D3E4', '题目 B 的知识点')]
    });
    const editor = createEditor();
    completeQuestion(editor, { statement: '题目 A' });
    clickButton(editor, '下一步：确认知识点');

    clickButton(editor, '返回修改题目');
    setField(fieldByLabel(editor.node, '完整题目'), '题目 B');
    clickButton(editor, '下一步：确认知识点');
    await flushPromises();
    resolveOld({
      knowledge_points: [suggestion('KP_A1B2C3D4', '题目 A 的旧知识点')]
    });
    await flushPromises();

    expect(editor.node.textContent).toContain('题目 B 的知识点');
    expect(editor.node.textContent).not.toContain('题目 A 的旧知识点');
  });

  it('keeps server-generated dimension codes bound by knowledge-point id after reordering', async () => {
    jest.useFakeTimers();
    let revision = 0;
    request.mockImplementation(
      async (path: string, _settings: unknown, init?: RequestInit) => {
        if (path === 'dimension-profiles' || path.includes('/draft')) {
          const envelope = JSON.parse(String(init?.body)) as
            | IAssessmentProfileDraftInput
            | {
                revision: number;
                draft: IAssessmentProfileDraftInput;
              };
          const input = 'draft' in envelope ? envelope.draft : envelope;
          revision += 1;
          return {
            ...input,
            profile_id: profileId,
            revision,
            dimensions: input.dimensions.map(dimension => ({
              ...dimension,
              code:
                input.knowledge_points.find(
                  point => point.id === dimension.knowledge_point_id
                )?.name === '循环边界'
                  ? 'CUSTOM_A1B2C3D4'
                  : 'CUSTOM_B1C2D3E4'
            }))
          };
        }
        throw new Error(`Unexpected path: ${path}`);
      }
    );
    const editor = createEditor();
    completeQuestion(editor, {
      teacherFocus: '循环边界\n平均值计算'
    });
    clickButton(editor, '下一步：确认知识点');
    jest.advanceTimersByTime(500);
    await flushMicrotasks();

    const firstCard = editor.node.querySelector<HTMLElement>(
      '.jp-BehaviorAudit-dimensionCard'
    );
    const moveDown = Array.from(
      firstCard?.querySelectorAll<HTMLButtonElement>('button') ?? []
    ).find(button => button.textContent === '下移');
    moveDown?.click();
    jest.advanceTimersByTime(500);
    await flushMicrotasks();

    const updateCall = request.mock.calls.find(call =>
      String(call[0]).includes('/draft')
    );
    expect(updateCall).toBeDefined();
    const updateBody = JSON.parse(
      String((updateCall?.[2] as RequestInit).body)
    );
    expect(
      updateBody.draft.dimensions.map(
        (dimension: { knowledge_point_id: string; code: string }) => [
          dimension.knowledge_point_id,
          dimension.code
        ]
      )
    ).toEqual(
      updateBody.draft.knowledge_points.map(
        (point: { id: string; name: string }) => [
          point.id,
          point.name === '循环边界' ? 'CUSTOM_A1B2C3D4' : 'CUSTOM_B1C2D3E4'
        ]
      )
    );
    editor.dispose();
  });

  it('does not overwrite knowledge edits made while confirmation hashing is pending', async () => {
    const crypto = controlledSubtle();
    crypto.delayNext();
    const editor = createEditor(jest.fn(), crypto.subtle);
    completeQuestion(editor, { teacherFocus: '循环边界' });
    clickButton(editor, '下一步：确认知识点');

    clickButton(editor, '我已确认以上知识点');
    setField(fieldByLabel(editor.node, '知识点 1 名称'), '列表遍历边界');
    crypto.release();
    await waitFor(
      () =>
        editor.node.textContent?.includes(
          '内容已修改，未采用旧确认结果，请重新确认'
        ) === true,
      'Stale knowledge confirmation was not rejected'
    );

    expect(editor.node.querySelector('h2')?.textContent).toBe('确认本题知识点');
    expect(fieldByLabel(editor.node, '知识点 1 名称').value).toBe(
      '列表遍历边界'
    );
    expect(editor.node.textContent).toContain(
      '内容已修改，未采用旧确认结果，请重新确认'
    );
    editor.dispose();
  });

  it('does not overwrite test edits made while confirmation hashing is pending', async () => {
    const crypto = controlledSubtle();
    request.mockImplementation(
      async (path: string, _settings: unknown, init?: RequestInit) => {
        if (path === 'assessment-assist/tests') {
          const requestBody = JSON.parse(String(init?.body)) as {
            knowledge_points: Array<{ id: string }>;
          };
          return {
            assessment_tests: [
              {
                id: 'TEST_A1B2C3D4',
                name: '普通整数列表',
                knowledge_point_ids: [requestBody.knowledge_points[0].id],
                kind: 'function_call',
                input: '[[78, 85, 92, 66, 88]]',
                expected: '81.8',
                enabled: true,
                source: 'ai_suggestion',
                order: 0
              }
            ]
          };
        }
        throw new Error(`Unexpected path: ${path}`);
      }
    );
    const editor = createEditor(jest.fn(), crypto.subtle);
    completeQuestion(editor, { teacherFocus: '循环边界' });
    clickButton(editor, '下一步：确认知识点');
    clickButton(editor, '我已确认以上知识点');
    await waitFor(
      () =>
        Array.from(editor.node.querySelectorAll('label')).some(
          label => label.textContent === '测试 1 预期输出'
        ),
      'Generated test did not render'
    );

    crypto.delayNext();
    const confirmation = fieldByLabel(
      editor.node,
      '我已核对这些测试，确认后才可发布'
    ) as HTMLInputElement;
    confirmation.checked = true;
    confirmation.dispatchEvent(new Event('change', { bubbles: true }));
    setField(fieldByLabel(editor.node, '测试 1 预期输出'), '82.0');
    crypto.release();
    await waitFor(
      () =>
        editor.node.textContent?.includes(
          '内容已修改，未采用旧确认结果，请重新确认'
        ) === true,
      'Stale test confirmation was not rejected'
    );

    expect(fieldByLabel(editor.node, '测试 1 预期输出').value).toBe('82.0');
    expect(editor.node.textContent).toContain(
      '内容已修改，未采用旧确认结果，请重新确认'
    );
    editor.dispose();
  });

  it('confirms points and tests, saves Profile v2, then publishes once', async () => {
    const saved: { draft?: IAssessmentProfileDraft } = {};
    const onPublished = jest.fn();
    request.mockImplementation(
      async (path: string, _settings: unknown, init?: RequestInit) => {
        if (path === 'assessment-assist/tests') {
          const requestBody = JSON.parse(String(init?.body)) as {
            knowledge_points: Array<{ id: string }>;
          };
          return {
            assessment_tests: [
              {
                id: 'TEST_A1B2C3D4',
                name: '普通整数列表',
                knowledge_point_ids: [requestBody.knowledge_points[0].id],
                kind: 'function_call',
                input: '[[78, 85, 92, 66, 88]]',
                expected: '81.8',
                enabled: true,
                source: 'ai_suggestion',
                order: 0
              }
            ]
          };
        }
        if (path === 'dimension-profiles') {
          const input = JSON.parse(
            String(init?.body)
          ) as IAssessmentProfileDraftInput;
          saved.draft = {
            ...input,
            profile_id: profileId,
            revision: 1,
            dimensions: input.dimensions.map((dimension, index) => ({
              ...dimension,
              code: `CUSTOM_CODE_${index + 1}`
            }))
          };
          return saved.draft;
        }
        if (path === `dimension-profiles/${profileId}/publish`) {
          if (!saved.draft) {
            throw new Error('Draft was not saved');
          }
          const { revision: _revision, ...draftWithoutRevision } = saved.draft;
          const published: IAssessmentProfileVersion = {
            ...draftWithoutRevision,
            version: 1,
            content_hash: 'a'.repeat(64),
            deployment_status: 'pilot',
            preview_status: 'pending_real_samples'
          };
          return published;
        }
        throw new Error(`Unexpected path: ${path}`);
      }
    );
    const editor = createEditor(onPublished);
    completeQuestion(editor, { teacherFocus: '循环边界' });
    clickButton(editor, '下一步：确认知识点');

    clickButton(editor, '我已确认以上知识点');
    await waitFor(
      () =>
        request.mock.calls.some(call => call[0] === 'assessment-assist/tests'),
      'Test suggestion request was not sent'
    );
    expect(request.mock.calls[0][0]).toBe('assessment-assist/tests');
    expect(fieldByLabel(editor.node, '测试 1 名称').value).toBe('普通整数列表');

    const confirmation = fieldByLabel(
      editor.node,
      '我已核对这些测试，确认后才可发布'
    ) as HTMLInputElement;
    confirmation.checked = true;
    confirmation.dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(
      () =>
        Array.from(
          editor.node.querySelectorAll<HTMLButtonElement>('button')
        ).some(
          button =>
            button.textContent === '发布试点方案' && button.disabled === false
        ),
      'Publish button did not become enabled'
    );

    const publish = Array.from(
      editor.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(button => button.textContent === '发布试点方案');
    expect(publish?.disabled).toBe(false);
    publish?.click();
    publish?.click();
    await waitFor(
      () => onPublished.mock.calls.length === 1,
      'Published profile callback was not invoked'
    );

    expect(saved.draft?.schema_version).toBe(2);
    expect(saved.draft?.knowledge_points).toHaveLength(1);
    expect(saved.draft?.assessment_tests).toHaveLength(1);
    expect(saved.draft?.confirmations.knowledge_points_hash).toMatch(
      /^[0-9a-f]{64}$/
    );
    expect(saved.draft?.confirmations.tests_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(
      request.mock.calls.filter(
        call => call[0] === `dimension-profiles/${profileId}/publish`
      )
    ).toHaveLength(1);
    expect(onPublished).toHaveBeenCalledTimes(1);
  });
});

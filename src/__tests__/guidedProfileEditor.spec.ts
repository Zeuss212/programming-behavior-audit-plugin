import { JupyterFrontEnd } from '@jupyterlab/application';
import { ICommandPalette } from '@jupyterlab/apputils';
import { ServerConnection } from '@jupyterlab/services';

import { ApiError } from '../models/apiError';
import {
  IDimensionProfileDraft,
  IDimensionProfileVersion,
  IDimensionTemplate,
  IProfileDraftInput
} from '../models/dimensionProfile';
import { requestAPI } from '../request';
import * as requestModule from '../request';
import {
  createProfile,
  getProfileVersion,
  listProfiles,
  publishProfile,
  updateProfileDraft
} from '../services/profileApi';
import { listTemplates } from '../services/templateApi';
import { labelledInput, statusBadge } from '../ui/domHelpers';
import { FirstRunView } from '../ui/firstRunView';
import { GuidedProfileAutosave } from '../ui/guidedProfileAutosave';
import { registerGuidedProfileEditorCommand } from '../ui/guidedProfileCommand';
import {
  GuidedProfileEditor,
  IGuidedDimensionForm,
  validateGuidedDimension
} from '../ui/guidedProfileEditor';
import { buildGuidedDimension } from '../ui/guidedProfileForm';

const settings = {} as ServerConnection.ISettings;

const template: IDimensionTemplate = {
  template_id: 'debug-chain',
  version: 1,
  deployment_status: 'pilot',
  code: 'DEBUG_CHAIN',
  name: '失败后的修改验证链',
  question: '学生运行失败后，是否修改相关代码并再次验证？',
  evidence_criteria: [
    {
      id: 'support-1',
      direction: 'support',
      statement: '失败运行后修改相关代码并再次运行'
    },
    {
      id: 'exclude-1',
      direction: 'exclude',
      statement: '只修改注释或运行无关 Cell 不计入'
    }
  ],
  levels: [
    {
      code: 'possible',
      name: '可能出现',
      definition: '存在相关行为证据，但范围或持续性有限'
    },
    {
      code: 'clear',
      name: '明显出现',
      definition: '在多个有效阶段持续出现相关行为'
    }
  ],
  teaching_actions: {
    possible: '结合失败与再次运行的证据询问学生调试思路',
    clear: '安排一次短练习，要求记录失败、修改与验证的对应关系'
  },
  analysis_config: {
    mode: 'llm_evidence',
    minimum_observation: { edit_event_count: 1, run_count: 1 }
  },
  examples: [
    {
      kind: 'positive',
      summary: '一次失败运行后，学生修改相关代码并再次运行。'
    },
    {
      kind: 'negative',
      summary: '学生只修改注释，随后运行了另一个无关 Cell。'
    }
  ]
};

function validForm(): IGuidedDimensionForm {
  return {
    name: '失败后是否继续验证',
    question: '学生运行失败后，是否修改相关代码并再次运行？',
    supportStatements: ['失败后修改相关代码并再次运行'],
    exclusionStatements: ['只修改注释不计入'],
    noKnownExclusion: false,
    possibleDefinition: '存在相关行为证据，但范围或持续性有限',
    clearDefinition: '在多个有效阶段持续出现相关行为',
    possibleAction: '结合证据询问学生的调试思路',
    clearAction: '安排一次修改后立即验证的短练习'
  };
}

function draft(
  revision: number,
  code = 'CUSTOM_A1B2C3D4',
  question = template.question
): IDimensionProfileDraft {
  return {
    schema_version: 1,
    profile_id: '12345678-1234-1234-1234-123456789abc',
    problem_id: 'average-debug',
    title: '平均分调试题',
    revision,
    dimensions: [
      {
        code,
        name: template.name,
        question,
        evidence_criteria: template.evidence_criteria,
        levels: [
          {
            code: 'possible',
            name: '可能出现',
            definition: '存在相关行为证据，但范围或持续性有限'
          },
          {
            code: 'clear',
            name: '明显出现',
            definition: '在多个有效阶段持续出现相关行为'
          }
        ],
        teaching_actions: template.teaching_actions,
        analysis_config: {
          mode: 'llm_evidence',
          minimum_observation: {
            valid_observation_duration_ms: 30000,
            edit_event_count: 1
          }
        }
      }
    ]
  };
}

function draftInput(
  question = template.question,
  code?: string
): IProfileDraftInput {
  const dimension = {
    ...draft(1, code ?? 'CUSTOM_A1B2C3D4', question).dimensions[0]
  };
  if (code === undefined) {
    delete (dimension as Partial<typeof dimension>).code;
  }
  return {
    schema_version: 1,
    problem_id: 'average-debug',
    title: '平均分调试题',
    dimensions: [dimension]
  };
}

function published(): IDimensionProfileVersion {
  const { revision: _revision, ...value } = draft(2);
  return {
    ...value,
    version: 1,
    content_hash: 'a'.repeat(64),
    deployment_status: 'pilot',
    preview_status: 'pending_real_samples'
  };
}

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 10; index += 1) {
    await Promise.resolve();
  }
}

describe('guided dimension v1 compatibility', () => {
  it('accepts a complete teaching-language form', () => {
    expect(validateGuidedDimension(validForm())).toEqual({});
  });

  it('uses the exact teaching-question validation message', () => {
    expect(validateGuidedDimension({ ...validForm(), question: '' })).toEqual({
      question: '请输入希望观察的教学问题'
    });
  });

  it('requires an exclusion or an explicit acknowledgement', () => {
    expect(
      validateGuidedDimension({
        ...validForm(),
        exclusionStatements: [],
        noKnownExclusion: false
      })
    ).toEqual({
      exclusionStatements: '请选择排除情况，或确认暂无已知排除情况'
    });
  });

  it('requires both teaching actions or neither', () => {
    expect(
      validateGuidedDimension({
        ...validForm(),
        possibleAction: '询问调试思路',
        clearAction: ''
      })
    ).toEqual({
      teachingActions: '教学建议请同时填写，或全部留空'
    });
  });

  it('emits only a closed complete teaching-action pair', () => {
    const withoutActions = buildGuidedDimension({
      ...validForm(),
      possibleAction: '',
      clearAction: ''
    });
    const withActions = buildGuidedDimension(validForm());

    expect(withoutActions).not.toHaveProperty('teaching_actions');
    expect(withActions.teaching_actions).toEqual({
      possible: '结合证据询问学生的调试思路',
      clear: '安排一次修改后立即验证的短练习'
    });
  });

  it('keeps the fixed behavior-evidence levels', () => {
    expect(buildGuidedDimension(validForm()).levels).toEqual([
      {
        code: 'possible',
        name: '可能出现',
        definition: '存在相关行为证据，但范围或持续性有限'
      },
      {
        code: 'clear',
        name: '明显出现',
        definition: '在多个有效阶段持续出现相关行为'
      }
    ]);
  });
});

describe('accessible DOM helpers', () => {
  it('connects a real label, error description and native constraints', () => {
    const field = labelledInput('question', '教学问题', {
      required: true,
      maxLength: 200
    });

    const label = field.container.querySelector('label');
    expect(label?.htmlFor).toBe('question');
    expect(field.input.required).toBe(true);
    expect(field.input.maxLength).toBe(200);
    expect(field.input.getAttribute('aria-describedby')).toBe('question-error');
    expect(field.error.id).toBe('question-error');
  });

  it('creates a text-bearing status badge', () => {
    const badge = statusBadge('试点', 'warning');

    expect(badge.textContent).toBe('试点');
    expect(badge.className).toContain('jp-BehaviorAudit-statusBadge-warning');
  });
});

describe('requestAPI errors', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('parses a frozen JSON error without logging raw bodies', async () => {
    jest.spyOn(console, 'info').mockImplementation();
    jest.spyOn(ServerConnection, 'makeRequest').mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'draft_revision_conflict',
          message: '草稿已被其他请求更新。',
          retryable: false,
          details: { field: 'revision' }
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/json' }
        }
      )
    );

    await expect(requestAPI('dimension-profiles', settings)).rejects.toEqual(
      new ApiError(
        409,
        'draft_revision_conflict',
        '草稿已被其他请求更新。',
        false,
        { field: 'revision' }
      )
    );
    expect(console.info).not.toHaveBeenCalled();
  });

  it('uses a safe message for non-JSON error responses', async () => {
    jest.spyOn(console, 'info').mockImplementation();
    jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockResolvedValue(
        new Response('private upstream details', { status: 500 })
      );

    await expect(
      requestAPI('dimension-profiles', settings)
    ).rejects.toMatchObject({
      status: 500,
      code: 'http_error',
      message: '服务器暂时无法处理请求。'
    });
    expect(console.info).not.toHaveBeenCalled();
  });

  it('preserves Jupyter network errors', async () => {
    const networkError = new ServerConnection.NetworkError(
      new TypeError('offline')
    );
    jest.spyOn(ServerConnection, 'makeRequest').mockRejectedValue(networkError);

    await expect(requestAPI('dimension-templates', settings)).rejects.toBe(
      networkError
    );
  });
});

describe('profile endpoint services', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('assembles and unwraps every profile endpoint path', async () => {
    const request = jest
      .spyOn(requestModule, 'requestAPI')
      .mockResolvedValueOnce({ templates: [template] })
      .mockResolvedValueOnce({ profiles: [published()] })
      .mockResolvedValueOnce(draft(1))
      .mockResolvedValueOnce(draft(2))
      .mockResolvedValueOnce(published())
      .mockResolvedValueOnce(published());
    const payload = {
      schema_version: 1 as const,
      problem_id: 'average-debug',
      title: '平均分调试题',
      dimensions: draft(1).dimensions
    };

    await expect(listTemplates(settings)).resolves.toEqual([template]);
    await expect(listProfiles(settings, '平均 分')).resolves.toHaveLength(1);
    await createProfile(settings, payload);
    await updateProfileDraft(
      settings,
      '12345678-1234-1234-1234-123456789abc',
      1,
      payload
    );
    await publishProfile(settings, '12345678-1234-1234-1234-123456789abc');
    await getProfileVersion(
      settings,
      '12345678-1234-1234-1234-123456789abc',
      1
    );

    expect(request.mock.calls.map(call => call[0])).toEqual([
      'dimension-templates',
      'dimension-profiles?problem_id=%E5%B9%B3%E5%9D%87%20%E5%88%86',
      'dimension-profiles',
      'dimension-profiles/12345678-1234-1234-1234-123456789abc/draft',
      'dimension-profiles/12345678-1234-1234-1234-123456789abc/publish',
      'dimension-profiles/12345678-1234-1234-1234-123456789abc/versions/1'
    ]);
    expect(request.mock.calls[3][2]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify({ revision: 1, draft: payload })
    });
  });
});

describe('FirstRunView', () => {
  it('exposes the question-first authoring entry', () => {
    const onCreate = jest.fn();
    const view = new FirstRunView({ onCreateProfile: onCreate });

    expect(view.node.textContent).toContain('这个工具能回答什么');
    expect(view.node.textContent).toContain('会采集什么');
    expect(view.node.textContent).toContain('数据是否发送给外部模型');
    const button = Array.from(view.node.querySelectorAll('button')).find(
      value => value.textContent === '创建题目考核方案'
    );
    button?.click();
    expect(onCreate).toHaveBeenCalledTimes(1);
  });
});

describe('guided editor command', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('registers one palette command and reuses the main-area editor', () => {
    const handlers = new Map<string, { execute: () => void }>();
    const add = jest.fn();
    const activateById = jest.fn();
    const addItem = jest.fn();
    const app = {
      commands: {
        addCommand: (id: string, options: { execute: () => void }): void => {
          handlers.set(id, options);
        }
      },
      shell: { add, activateById },
      serviceManager: { serverSettings: settings }
    } as unknown as JupyterFrontEnd;
    const palette = { addItem } as unknown as ICommandPalette;

    registerGuidedProfileEditorCommand(app, palette);
    const command = handlers.get('myextension:manage-dimension-profiles');
    command?.execute();
    command?.execute();

    expect(addItem).toHaveBeenCalledWith({
      command: 'myextension:manage-dimension-profiles',
      category: 'Behavior Audit'
    });
    expect(add).toHaveBeenCalledTimes(1);
    expect(add).toHaveBeenCalledWith(expect.any(GuidedProfileEditor), 'main');
    expect(activateById).toHaveBeenCalledTimes(2);
  });

  it('forwards a published profile so the sidebar can refresh immediately', () => {
    const handlers = new Map<string, { execute: () => void }>();
    let editor: GuidedProfileEditor | null = null;
    const app = {
      commands: {
        addCommand: (id: string, options: { execute: () => void }): void => {
          handlers.set(id, options);
        }
      },
      shell: {
        add: (widget: GuidedProfileEditor): void => {
          editor = widget;
        },
        activateById: jest.fn()
      },
      serviceManager: { serverSettings: settings }
    } as unknown as JupyterFrontEnd;
    const onPublished = jest.fn();

    registerGuidedProfileEditorCommand(app, null, onPublished);
    handlers.get('myextension:manage-dimension-profiles')?.execute();
    (
      editor as unknown as {
        onPublished: (profile: IDimensionProfileVersion) => void;
      }
    ).onPublished(published());

    expect(onPublished).toHaveBeenCalledWith(published());
  });
});

describe('GuidedProfileAutosave queue', () => {
  let request: jest.SpyInstance;

  beforeEach(() => {
    jest.useFakeTimers();
    request = jest.spyOn(requestModule, 'requestAPI');
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('serializes updates and sends the latest after the first commits', async () => {
    let resolveFirstUpdate!: (value: IDimensionProfileDraft) => void;
    const firstUpdate = new Promise<IDimensionProfileDraft>(resolve => {
      resolveFirstUpdate = resolve;
    });
    request
      .mockResolvedValueOnce(draft(1, 'CUSTOM_A1B2C3D4'))
      .mockReturnValueOnce(firstUpdate)
      .mockResolvedValueOnce(draft(3, 'CUSTOM_A1B2C3D4', '第二个更新'));
    const autosave = new GuidedProfileAutosave(settings, jest.fn());
    autosave.beginDraft();
    autosave.markChanged(code => draftInput('初始问题', code));
    jest.advanceTimersByTime(500);
    await flushPromises();

    autosave.markChanged(code => draftInput('第一个更新', code));
    jest.advanceTimersByTime(500);
    await flushPromises();
    autosave.markChanged(code => draftInput('第二个更新', code));
    jest.advanceTimersByTime(500);
    await flushPromises();
    expect(request).toHaveBeenCalledTimes(2);

    resolveFirstUpdate(draft(2, 'CUSTOM_A1B2C3D4', '第一个更新'));
    await flushPromises();
    expect(request).toHaveBeenCalledTimes(3);
    const secondUpdate = JSON.parse(
      String((request.mock.calls[2][2] as RequestInit).body)
    );
    expect(secondUpdate.revision).toBe(2);
    expect(secondUpdate.draft.dimensions[0].question).toBe('第二个更新');
  });

  it('retries latest dirty data from the committed revision', async () => {
    const statuses: string[] = [];
    request
      .mockResolvedValueOnce(draft(1, 'CUSTOM_A1B2C3D4'))
      .mockRejectedValueOnce(
        new ServerConnection.NetworkError(new TypeError('offline'))
      )
      .mockResolvedValueOnce(draft(2, 'CUSTOM_A1B2C3D4', '重试后的最新内容'));
    const autosave = new GuidedProfileAutosave(settings, status => {
      statuses.push(status);
    });
    autosave.beginDraft();
    autosave.markChanged(code => draftInput('初始问题', code));
    jest.advanceTimersByTime(500);
    await flushPromises();

    autosave.markChanged(code => draftInput('失败但不能丢失', code));
    jest.advanceTimersByTime(500);
    await flushPromises();
    expect(statuses).toContain('草稿保存失败，请稍后重试');

    autosave.markChanged(code => draftInput('重试后的最新内容', code));
    jest.advanceTimersByTime(500);
    await flushPromises();
    const retryBody = JSON.parse(
      String((request.mock.calls[2][2] as RequestInit).body)
    );
    expect(retryBody.revision).toBe(1);
    expect(retryBody.draft.dimensions[0].question).toBe('重试后的最新内容');
  });

  it('flushes before one single-flight publish and prevents republish', async () => {
    let resolveCreate!: (value: IDimensionProfileDraft) => void;
    const create = new Promise<IDimensionProfileDraft>(resolve => {
      resolveCreate = resolve;
    });
    request.mockReturnValueOnce(create).mockResolvedValueOnce(published());
    const autosave = new GuidedProfileAutosave(settings, jest.fn());
    autosave.beginDraft();
    autosave.markChanged(code => draftInput('待发布内容', code));
    jest.advanceTimersByTime(500);
    await flushPromises();

    const firstPublish = autosave.publish();
    const secondPublish = autosave.publish();
    expect(secondPublish).toBe(firstPublish);
    expect(request).toHaveBeenCalledTimes(1);

    resolveCreate(draft(1, 'CUSTOM_A1B2C3D4', '待发布内容'));
    await expect(firstPublish).resolves.toEqual(published());
    expect(request.mock.calls.map(call => call[0])).toEqual([
      'dimension-profiles',
      'dimension-profiles/12345678-1234-1234-1234-123456789abc/publish'
    ]);
    await expect(autosave.publish()).resolves.toEqual(published());
    expect(request).toHaveBeenCalledTimes(2);
  });

  it.each([
    [
      '409 conflict',
      new ApiError(409, 'draft_revision_conflict', 'conflict', false)
    ],
    [
      '422 validation error',
      new ApiError(422, 'profile_validation_failed', 'invalid', false)
    ],
    [
      '500 server error',
      new ApiError(500, 'internal_server_error', 'server error', true)
    ],
    [
      'network error',
      new ServerConnection.NetworkError(new TypeError('offline'))
    ]
  ])(
    'never publishes when the latest save fails with %s',
    async (_label, error) => {
      request.mockRejectedValueOnce(error);
      const autosave = new GuidedProfileAutosave(settings, jest.fn());
      autosave.beginDraft();
      autosave.markChanged(code => draftInput('不能发布', code));

      await expect(autosave.publish()).resolves.toBeNull();
      expect(request).toHaveBeenCalledTimes(1);
      expect(request.mock.calls[0][0]).toBe('dimension-profiles');
    }
  );
});

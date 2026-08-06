import { IKnowledgePointSuggestion } from '../models/assessmentPlan';
import {
  addTeacherKnowledgePoint,
  createAssessmentPlanState,
  replaceAssessmentTests,
  updateKnowledgePoint
} from '../ui/assessmentPlanForm';
import { renderKnowledgePointStep } from '../ui/knowledgePointStep';
import { renderQuestionStep } from '../ui/questionStep';
import { renderTestConfirmationStep } from '../ui/testConfirmationStep';

function baseState() {
  return {
    ...createAssessmentPlanState(),
    title: '平均分知识点分析',
    problemId: 'average-debug',
    problemStatement: '编写 calculate_average(numbers)，返回数字列表的平均值。',
    submissionContract: {
      kind: 'function' as const,
      entrypoint: 'calculate_average'
    }
  };
}

function withPoint() {
  return addTeacherKnowledgePoint(
    baseState(),
    { name: '循环边界', description: '正确遍历列表。' },
    () => 'KP_A1B2C3D4'
  );
}

function inputByLabel(
  root: ParentNode,
  label: string
): HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement {
  const labelNode = Array.from(root.querySelectorAll('label')).find(
    item => item.textContent === label
  );
  if (!labelNode?.htmlFor) throw new Error(`Missing label: ${label}`);
  const field = root.querySelector(`#${labelNode.htmlFor}`);
  if (
    !(
      field instanceof HTMLInputElement ||
      field instanceof HTMLTextAreaElement ||
      field instanceof HTMLSelectElement
    )
  ) {
    throw new Error(`Invalid field for label: ${label}`);
  }
  return field;
}

describe('teacher-first assessment steps', () => {
  it('renders question as the first task and keeps advanced settings collapsed', () => {
    const content = document.createElement('div');
    const callbacks = {
      onChange: jest.fn(),
      onContinue: jest.fn()
    };

    renderQuestionStep(content, 'synthetic-question', baseState(), callbacks);

    expect(content.querySelector('h2')?.textContent).toBe('输入题目');
    for (const label of [
      '方案名称',
      '题目标识',
      '完整题目',
      '答题形式',
      '函数名',
      '我想考察的知识点（可选，每行一个）'
    ]) {
      const field = inputByLabel(content, label);
      expect(field.getAttribute('aria-describedby')).toBeTruthy();
    }
    const advanced = content.querySelector('details');
    expect(advanced?.querySelector('summary')?.textContent).toBe('高级设置');
    expect(advanced?.open).toBe(false);
    for (const label of ['方案名称', '题目标识', '答题形式', '函数名']) {
      expect(advanced?.contains(inputByLabel(content, label))).toBe(true);
    }
    expect(advanced?.contains(inputByLabel(content, '完整题目'))).toBe(false);
    expect(content.textContent).not.toMatch(
      /minimum_observation|llm_evidence|threshold|signal/i
    );
  });

  it('restores teacher-entered knowledge point lines when returning to the question', () => {
    const content = document.createElement('div');
    const state = {
      ...baseState(),
      teacherFocus: ['循环边界', '平均值计算']
    };

    renderQuestionStep(content, 'synthetic-return', state, {
      onChange: jest.fn(),
      onContinue: jest.fn()
    });

    expect(
      inputByLabel(content, '我想考察的知识点（可选，每行一个）').value
    ).toBe('循环边界\n平均值计算');
  });

  it('shows AI candidates separately and never treats them as confirmed points', () => {
    const content = document.createElement('div');
    const suggestions: IKnowledgePointSuggestion[] = [
      {
        id: 'KP_B1C2D3E4',
        name: '平均值计算',
        description: '使用总和除以元素数量。',
        evidence_question: '是否正确完成平均值计算？',
        support_statement: '使用多个样例验证平均值。',
        exclusion_statement: '固定输出不计入。',
        source: 'ai_suggestion',
        order: 0
      }
    ];
    const callbacks = {
      onAdoptSuggestion: jest.fn(),
      onIgnoreSuggestion: jest.fn(),
      onAddPoint: jest.fn(),
      onUpdatePoint: jest.fn(),
      onRemovePoint: jest.fn(),
      onMovePoint: jest.fn(),
      onRequestSuggestions: jest.fn(),
      onBack: jest.fn(),
      onConfirm: jest.fn()
    };

    renderKnowledgePointStep(
      content,
      'synthetic-knowledge',
      withPoint(),
      suggestions,
      { status: 'idle' },
      callbacks
    );

    expect(content.querySelector('h2')?.textContent).toBe('确认本题知识点');
    expect(content.textContent).toContain('AI 建议');
    expect(content.textContent).toContain('教师添加');
    expect(content.textContent).toContain('平均值计算');
    expect(
      Array.from(content.querySelectorAll('button')).map(
        button => button.textContent
      )
    ).toEqual(
      expect.arrayContaining([
        '采用',
        '忽略',
        '添加自定义知识点',
        '我已确认以上知识点'
      ])
    );
    expect(callbacks.onAdoptSuggestion).not.toHaveBeenCalled();
    expect(
      content.querySelector<HTMLDetailsElement>(
        'details.jp-BehaviorAudit-advancedSettings'
      )?.open
    ).toBe(false);
  });

  it('opens advanced settings for missing observation fields', () => {
    const content = document.createElement('div');
    const state = updateKnowledgePoint(withPoint(), 'KP_A1B2C3D4', {
      supportStatement: '',
      exclusionStatement: '   '
    });

    renderKnowledgePointStep(
      content,
      'synthetic-invalid-knowledge',
      state,
      [],
      { status: 'idle' },
      {
        onAdoptSuggestion: jest.fn(),
        onIgnoreSuggestion: jest.fn(),
        onAddPoint: jest.fn(),
        onUpdatePoint: jest.fn(),
        onRemovePoint: jest.fn(),
        onMovePoint: jest.fn(),
        onRequestSuggestions: jest.fn(),
        onBack: jest.fn(),
        onConfirm: jest.fn()
      }
    );

    expect(
      content.querySelector<HTMLDetailsElement>(
        'details.jp-BehaviorAudit-advancedSettings'
      )?.open
    ).toBe(true);
    expect(inputByLabel(content, '支持表现').getAttribute('aria-invalid')).toBe(
      'true'
    );
    expect(inputByLabel(content, '排除情况').getAttribute('aria-invalid')).toBe(
      'true'
    );
    expect(
      inputByLabel(content, '过程观察问题').hasAttribute('aria-invalid')
    ).toBe(false);
    expect(content.textContent).toContain('请填写支持表现');
    expect(content.textContent).toContain('请填写排除情况');
  });

  it('keeps tests editable and disables publish until the teacher confirms', () => {
    const content = document.createElement('div');
    const state = replaceAssessmentTests(withPoint(), [
      {
        id: 'TEST_A1B2C3D4',
        name: '普通整数列表',
        knowledge_point_ids: ['KP_A1B2C3D4'],
        kind: 'function_call',
        input: '[[78, 85, 92, 66, 88]]',
        expected: '81.8',
        enabled: true,
        source: 'ai_suggestion',
        order: 0
      }
    ]);
    const callbacks = {
      onUpdateTest: jest.fn(),
      onRemoveTest: jest.fn(),
      onMoveTest: jest.fn(),
      onAddTest: jest.fn(),
      onGenerateTests: jest.fn(),
      onBack: jest.fn(),
      onConfirmTests: jest.fn(),
      onPublish: jest.fn()
    };

    renderTestConfirmationStep(
      content,
      'synthetic-tests',
      state,
      { status: 'idle' },
      callbacks
    );

    expect(content.querySelector('h2')?.textContent).toBe('确认测试并发布');
    expect(inputByLabel(content, '测试 1 名称')).toBeInstanceOf(
      HTMLInputElement
    );
    expect(inputByLabel(content, '测试 1 输入')).toBeInstanceOf(
      HTMLTextAreaElement
    );
    expect(inputByLabel(content, '测试 1 预期输出')).toBeInstanceOf(
      HTMLTextAreaElement
    );
    const confirmation = inputByLabel(
      content,
      '我已核对这些测试，确认后才可发布'
    ) as HTMLInputElement;
    expect(confirmation.type).toBe('checkbox');
    const publish = Array.from(content.querySelectorAll('button')).find(
      button => button.textContent === '发布试点方案'
    );
    expect(publish?.disabled).toBe(true);
    expect(content.textContent).toContain('本阶段只保存测试方案，不会执行测试');
    expect(content.textContent).toContain(
      '这些测试已保留但尚未确认，请重新核对后勾选确认'
    );
  });

  it('notifies the editor when a teacher removes test confirmation', () => {
    const content = document.createElement('div');
    const withTests = replaceAssessmentTests(withPoint(), [
      {
        id: 'TEST_A1B2C3D4',
        name: '普通整数列表',
        knowledge_point_ids: ['KP_A1B2C3D4'],
        kind: 'function_call',
        input: '[[78, 85, 92, 66, 88]]',
        expected: '81.8',
        enabled: true,
        source: 'teacher',
        order: 0
      }
    ]);
    const state = {
      ...withTests,
      confirmations: {
        knowledge_points_hash: 'a'.repeat(64),
        tests_hash: 'b'.repeat(64)
      }
    };
    const onConfirmTests = jest.fn();

    renderTestConfirmationStep(
      content,
      'synthetic-unconfirm',
      state,
      { status: 'idle' },
      {
        onUpdateTest: jest.fn(),
        onRemoveTest: jest.fn(),
        onMoveTest: jest.fn(),
        onAddTest: jest.fn(),
        onGenerateTests: jest.fn(),
        onBack: jest.fn(),
        onConfirmTests,
        onPublish: jest.fn()
      }
    );

    const confirmation = inputByLabel(
      content,
      '我已核对这些测试，确认后才可发布'
    ) as HTMLInputElement;
    expect(confirmation.checked).toBe(true);
    expect(confirmation.disabled).toBe(false);
    confirmation.checked = false;
    confirmation.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onConfirmTests).toHaveBeenCalledWith(false);
  });

  it('uses live status text for loading and recoverable AI errors', () => {
    const content = document.createElement('div');
    renderKnowledgePointStep(
      content,
      'synthetic-loading',
      withPoint(),
      [],
      {
        status: 'error',
        message: 'AI 暂时不可用，可继续手工添加知识点。'
      },
      {
        onAdoptSuggestion: jest.fn(),
        onIgnoreSuggestion: jest.fn(),
        onAddPoint: jest.fn(),
        onUpdatePoint: jest.fn(),
        onRemovePoint: jest.fn(),
        onMovePoint: jest.fn(),
        onRequestSuggestions: jest.fn(),
        onBack: jest.fn(),
        onConfirm: jest.fn()
      }
    );

    const status = content.querySelector('[role="status"]');
    expect(status?.getAttribute('aria-live')).toBe('polite');
    expect(status?.textContent).toContain('可继续手工添加');
    expect(content.textContent).toContain('添加自定义知识点');
  });
});

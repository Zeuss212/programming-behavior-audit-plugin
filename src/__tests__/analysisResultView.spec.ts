import { IDimensionProfileVersion } from '../models/dimensionProfile';
import {
  IAnalysisResult,
  renderAnalysisResult
} from '../ui/analysisResultView';

const profile: IDimensionProfileVersion = {
  schema_version: 1,
  profile_id: '123e4567-e89b-42d3-a456-426614174000',
  problem_id: 'synthetic-debug-problem',
  title: '合成调试题',
  version: 2,
  content_hash: 'a'.repeat(64),
  deployment_status: 'pilot',
  preview_status: 'pending_real_samples',
  dimensions: [
    {
      code: 'DEBUG_CHAIN',
      name: '调试验证链',
      question: '是否修改后再次验证？',
      evidence_criteria: [],
      levels: [],
      teaching_actions: {
        possible: '结合证据询问学生的调试思路',
        clear: '请学生说明修改与验证的关系'
      },
      analysis_config: { mode: 'llm_evidence', minimum_observation: {} }
    }
  ]
};

function result(overrides: Partial<IAnalysisResult> = {}): IAnalysisResult {
  return {
    schema_version: 1,
    request_id: 'synthetic-request',
    analysis_id: '223e4567-e89b-42d3-a456-426614174000',
    job_id: '323e4567-e89b-42d3-a456-426614174000',
    attempt_id: '423e4567-e89b-42d3-a456-426614174000',
    session_id: '523e4567-e89b-42d3-a456-426614174000',
    profile_id: profile.profile_id,
    profile_version: 2,
    profile_content_hash: 'b'.repeat(64),
    status: 'ready',
    error_code: null,
    dimension_results: [
      {
        schema_version: 1,
        request_id: 'synthetic-dimension-request',
        dimension_code: 'DEBUG_CHAIN',
        decision: {
          status: 'resolved',
          final_evidence_status: 'observed',
          final_level_code: 'possible',
          display_label: 'server internal label',
          source: 'llm_evidence'
        },
        data_quality: {
          missing_required_signals: [],
          observation_opportunities: 2,
          reason_code: null,
          reason: null
        },
        ai_result: {
          confidence: 0.91,
          explanation: '修改后进行了再次运行。',
          evidence_claims: [
            {
              event_id: 'event-1',
              criterion_id: 'support-1',
              direction: 'support',
              claim: '修改后再次运行',
              occurred_at: '2026-07-28T10:00:00Z',
              event_type: 'cell_execution_success'
            },
            {
              event_id: 'event-2',
              criterion_id: 'support-1',
              direction: 'support',
              claim: '再次检查输出',
              occurred_at: '2026-07-28T10:01:00Z',
              event_type: 'cell_execution_success'
            }
          ]
        },
        review: { revision: 0, status: 'unreviewed' }
      }
    ],
    provenance: {
      analysis_pipeline_version: 'pilot-v1',
      feature_extractor_version: 'pilot-v1',
      signal_dictionary_version: 'pilot-v1',
      signal_dictionary_hash: 'f'.repeat(64),
      model_name: 'synthetic-model',
      model_version: '1',
      model_parameters: { temperature: 0 },
      prompt_version: 'pilot-v1',
      provider_request_id: 'synthetic-provider-request',
      raw_response_hash: 'c'.repeat(64),
      input_snapshot_hash: 'd'.repeat(64),
      prompt_content_hash: 'e'.repeat(64)
    },
    ...overrides
  };
}

describe('renderAnalysisResult', () => {
  it('renders teaching-language evidence without internal hashes or paths', () => {
    const node = renderAnalysisResult(result(), profile, () => undefined);

    expect(node.textContent).toContain('可能出现');
    expect(node.textContent).toContain('结合证据询问学生的调试思路');
    expect(node.textContent).toContain('查看 2 条证据');
    expect(
      Array.from(node.querySelectorAll('details > summary')).some(summary =>
        summary.textContent?.includes('分析详情')
      )
    ).toBe(true);
    expect(node.textContent).not.toContain('prompt_content_hash');
    expect(node.textContent).not.toContain('aaaaaaaaaaaaaaaa');
    expect(node.textContent).not.toContain('/Users/');
  });

  it('shows safe provenance, teaching-language source, and qualified confidence only inside collapsed details', () => {
    const rendered = renderAnalysisResult(result(), profile, () => undefined);
    const details = Array.from(
      rendered.querySelectorAll<HTMLDetailsElement>('details')
    ).find(
      value => value.querySelector('summary')?.textContent === '分析详情'
    )!;

    expect(details.open).toBe(false);
    expect(details.textContent).toContain('synthetic-model');
    expect(details.textContent).toContain('模型版本：1');
    expect(details.textContent).toContain('提示词版本：pilot-v1');
    expect(details.textContent).toContain('信号字典版本：pilot-v1');
    expect(details.textContent).toContain('分析流程版本：pilot-v1');
    expect(details.textContent).toContain('AI 证据分析');
    expect(details.textContent).toContain('模型自评，不代表正确概率：0.91');
    expect(details.textContent).not.toContain('synthetic-provider-request');
    expect(details.textContent).not.toContain('c'.repeat(64));
    expect(details.textContent).not.toContain('d'.repeat(64));
    expect(details.textContent).not.toContain('e'.repeat(64));
    expect(details.textContent).not.toContain('f'.repeat(64));
    expect(details.textContent).not.toContain('/Users/');
    expect(details.textContent).not.toContain('cell_execution_success');
  });

  it.each([
    ['not_observed', '未发现明显证据'],
    ['insufficient_evidence', '数据不足'],
    ['not_computable', '当前记录无法分析']
  ] as const)('uses a stable label for %s', (status, label) => {
    const value = result();
    value.dimension_results[0].decision.final_evidence_status = status;
    value.dimension_results[0].decision.final_level_code = null;
    value.dimension_results[0].ai_result = null;
    const node = renderAnalysisResult(value, profile, () => undefined);

    expect(node.textContent).toContain(label);
  });

  it('degrades safely when review is needed and profile guidance is missing', () => {
    const value = result();
    value.dimension_results[0].decision = {
      status: 'needs_review',
      final_evidence_status: null,
      final_level_code: null,
      display_label: 'needs review',
      source: 'coverage'
    };
    value.dimension_results[0].ai_result = null;
    const missingGuidance = {
      ...profile,
      dimensions: []
    };

    expect(() =>
      renderAnalysisResult(value, missingGuidance, () => undefined)
    ).not.toThrow();
    expect(
      renderAnalysisResult(value, missingGuidance, () => undefined).textContent
    ).toContain('需要教师复核');
  });

  it('keeps evidence event metadata while redacting path-like claim text', () => {
    const value = result();
    value.dimension_results[0].ai_result!.evidence_claims[0].claim =
      '在 /Users/synthetic/private.py 修改后再次运行';
    const node = renderAnalysisResult(value, profile, () => undefined);
    const evidence = Array.from(node.querySelectorAll('details')).find(value =>
      value.textContent?.includes('查看 2 条证据')
    );

    expect(evidence?.querySelector('summary')).toBeTruthy();
    expect(evidence?.textContent).toContain('2026-07-28T10:00:00Z');
    expect(evidence?.textContent).toContain('运行完成');
    expect(evidence?.textContent).not.toContain('cell_execution_success');
    expect(evidence?.textContent).not.toContain('/Users/synthetic');
  });

  it('uses native review controls and preserves input after an async failure', async () => {
    const onReview = jest.fn(async () => {
      throw new Error('synthetic failure');
    });
    const node = renderAnalysisResult(result(), profile, onReview);
    const details = Array.from(node.querySelectorAll('details')).find(value =>
      value.textContent?.includes('教师复核')
    );
    const summary = details?.querySelector('summary');
    const comment = details?.querySelector<HTMLTextAreaElement>('textarea');
    const form = details?.querySelector('form');

    expect(summary).toBeTruthy();
    expect(details?.querySelector('button')).toBeTruthy();
    expect(details?.querySelector('input[type="radio"]')).toBeTruthy();
    comment!.value = '合成复核说明';
    form!.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    await Promise.resolve();
    await Promise.resolve();

    expect(onReview).toHaveBeenCalledWith(
      'DEBUG_CHAIN',
      expect.objectContaining({ comment: '合成复核说明' })
    );
    expect(comment?.value).toBe('合成复核说明');
    expect(details?.textContent).toContain('复核提交失败，请重试。');
  });

  it.each(['needs_review', 'partial', 'failed'] as const)(
    'submits a backend-valid unresolved confirmation for %s',
    async status => {
      const value = result();
      value.dimension_results[0].decision = {
        status,
        final_evidence_status: null,
        final_level_code: null,
        display_label: 'synthetic unresolved',
        source: 'coverage'
      };
      value.dimension_results[0].ai_result = null;
      const onReview = jest.fn(async () => undefined);
      const rendered = renderAnalysisResult(value, profile, onReview);
      const form = rendered.querySelector<HTMLFormElement>(
        '.jp-BehaviorAudit-reviewForm'
      )!;
      form.querySelector<HTMLTextAreaElement>('textarea')!.value =
        '合成确认说明';
      form.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true })
      );
      await Promise.resolve();

      expect(onReview).toHaveBeenCalledWith(
        'DEBUG_CHAIN',
        expect.objectContaining({
          decision_status: 'needs_review',
          evidence_status: null,
          level_code: null,
          reason_code: 'uncertain'
        })
      );
    }
  );

  it('renders clear as obvious and uses mutually meaningful summary counts', () => {
    const value = result();
    const clear = value.dimension_results[0];
    clear.decision.final_level_code = 'clear';
    const unresolved = {
      ...clear,
      dimension_code: 'UNRESOLVED',
      decision: {
        ...clear.decision,
        status: 'needs_review' as const,
        final_evidence_status: null,
        final_level_code: null
      }
    };
    const unavailable = {
      ...clear,
      dimension_code: 'UNAVAILABLE',
      decision: {
        ...clear.decision,
        final_evidence_status: 'insufficient_evidence' as const,
        final_level_code: null
      }
    };
    const failed = {
      ...clear,
      dimension_code: 'FAILED',
      decision: {
        ...clear.decision,
        status: 'failed' as const,
        final_evidence_status: null,
        final_level_code: null
      }
    };
    value.dimension_results = [clear, unresolved, unavailable, failed];
    const rendered = renderAnalysisResult(value, profile, () => undefined);

    expect(rendered.textContent).toContain('明显出现');
    expect(rendered.textContent).toContain(
      '完成维度 1；待复核 1；数据不足或无法分析 1；失败 1'
    );
  });

  it('uses a safe generic event label for unknown internal codes', () => {
    const value = result();
    value.dimension_results[0].ai_result!.evidence_claims[0].event_type =
      'synthetic_private_signal_code';
    const rendered = renderAnalysisResult(value, profile, () => undefined);

    expect(rendered.textContent).toContain('编程行为记录');
    expect(rendered.textContent).not.toContain('synthetic_private_signal_code');
  });

  it('uses only the bound not-observed teaching action when configured', () => {
    const value = result();
    value.dimension_results[0].decision.final_evidence_status = 'not_observed';
    value.dimension_results[0].decision.final_level_code = null;
    value.dimension_results[0].ai_result = null;
    const configured = {
      ...profile,
      dimensions: profile.dimensions.map(dimension => ({
        ...dimension,
        teaching_actions: {
          ...dimension.teaching_actions!,
          not_observed: '继续常规观察，无需额外干预'
        }
      }))
    };

    const withAction = renderAnalysisResult(value, configured, () => undefined);
    expect(withAction.textContent).toContain(
      '下一步教学建议：继续常规观察，无需额外干预'
    );

    const withoutAction = renderAnalysisResult(value, profile, () => undefined);
    expect(withoutAction.textContent).not.toContain('下一步教学建议');
    expect(withoutAction.textContent).not.toContain('无需额外干预');
  });

  it('separates captured data from a missing AI conclusion', () => {
    const value = result({
      status: 'partial',
      error_code: 'ai_not_configured'
    });
    const onReview = jest.fn();

    const rendered = renderAnalysisResult(value, profile, onReview);

    expect(rendered.textContent).toContain('数据采集完成，尚未进行 AI 分析');
    expect(rendered.textContent).toContain('配置 AI 服务后重试分析');
    expect(rendered.textContent).not.toContain('部分结果');
    expect(rendered.textContent).not.toContain('待复核');
    expect(rendered.querySelector('.jp-BehaviorAudit-resultCard')).toBeNull();
    expect(rendered.querySelector('form')).toBeNull();
  });

  it('separates completed collection from AI failure without zero-evidence cards', () => {
    const value = result({
      status: 'partial',
      error_code: 'ai_analysis_failed'
    });
    value.dimension_results[0].decision = {
      status: 'partial',
      final_evidence_status: null,
      final_level_code: null,
      display_label: 'synthetic unresolved',
      source: 'llm_evidence'
    };
    value.dimension_results[0].ai_result = null;
    value.dimension_results[0].data_quality = {
      missing_required_signals: [],
      observation_opportunities: 0,
      reason_code: 'minimum_observation_met',
      reason: '已达到最低观察要求'
    };

    const rendered = renderAnalysisResult(value, profile, () => undefined);

    expect(rendered.textContent).toContain('行为采集已完成');
    expect(rendered.textContent).toContain('AI 分析未完成，可重试分析');
    expect(rendered.textContent).toContain('已达到最低观察要求');
    expect(rendered.textContent).not.toContain('部分结果');
    expect(rendered.textContent).not.toContain('查看 0 条证据');
    expect(rendered.querySelector('.jp-BehaviorAudit-resultCard')).toBeNull();
    expect(rendered.querySelector('form')).toBeNull();
  });

  it('keeps ordinary partial analysis available for teacher review', () => {
    const value = result({ status: 'partial' });
    value.dimension_results[0].decision = {
      status: 'partial',
      final_evidence_status: null,
      final_level_code: null,
      display_label: 'synthetic unresolved',
      source: 'coverage'
    };
    value.dimension_results[0].ai_result = null;

    const rendered = renderAnalysisResult(value, profile, () => undefined);

    expect(rendered.textContent).toContain('部分结果');
    expect(
      rendered.querySelector('.jp-BehaviorAudit-resultCard')
    ).not.toBeNull();
    expect(rendered.querySelector('form')).not.toBeNull();
    expect(rendered.textContent).not.toContain('查看 0 条证据');
    expect(
      rendered.querySelector('.jp-BehaviorAudit-evidenceDetails')
    ).toBeNull();
  });

  it('keeps a valid not-observed conclusion when another analysis error is present', () => {
    const value = result({
      status: 'partial',
      error_code: 'ai_analysis_failed'
    });
    value.dimension_results[0].decision = {
      status: 'resolved',
      final_evidence_status: 'not_observed',
      final_level_code: null,
      display_label: 'synthetic not observed',
      source: 'llm_evidence'
    };
    value.dimension_results[0].ai_result = {
      confidence: 0.7,
      evidence_claims: [],
      explanation: '达到观察要求但未发现相应行为。'
    };

    const rendered = renderAnalysisResult(value, profile, () => undefined);

    expect(rendered.textContent).toContain('未发现明显证据');
    expect(
      rendered.querySelector('.jp-BehaviorAudit-resultCard')
    ).not.toBeNull();
    expect(rendered.textContent).not.toContain('AI 分析未完成，可重试分析');
    expect(rendered.textContent).not.toContain('查看 0 条证据');
  });
});

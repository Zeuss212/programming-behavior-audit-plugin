import { describe, expect, it, vi } from 'vitest';

import type { AnalysisLog, ExportManifest } from '../domain/types';
import type { SessionAnalysisService } from '../reports/analysisService';
import type { ExportDestination, SessionExporter } from '../reports/exporter';
import { analyzeAndExport } from '../workflows/finishAnalyzeExport';

const sessionId = 'session-finish-export-001';
const destination: ExportDestination = { fsPath: '/safe/export-folder' };

function failedAnalysis(): AnalysisLog {
  return {
    schema_version: 1,
    session_id: sessionId,
    generated_at: '2026-08-14T12:00:00.000Z',
    status: 'failed',
    reason: {
      code: 'ai_provider_timeout',
      message: 'AI 服务响应超时，已保留本地课堂简报。',
    },
  };
}

function manifest(): ExportManifest {
  return {
    schema_version: 1,
    extension_version: '0.1.1',
    session_id: sessionId,
    exported_at: '2026-08-14T12:00:01.000Z',
    files: [],
  };
}

describe('analyzeAndExport', () => {
  it('opens the export-folder dialog before waiting for the optional AI analysis', async () => {
    const materialize = vi.fn(() => Promise.resolve(failedAnalysis()));
    const analysisService: SessionAnalysisService = { materialize };
    const exportSession = vi.fn(() => Promise.resolve(manifest()));
    const exporter: SessionExporter = { exportSession };
    const chooseDestination = vi.fn(() => Promise.resolve(destination));
    const onProgress = vi.fn();

    await analyzeAndExport({
      sessionId,
      workspaceRoot: '/Users/student/workspace',
      autoAnalyze: true,
      analysisService,
      exporter,
      chooseDestination,
      onProgress,
    });

    expect(chooseDestination.mock.invocationCallOrder[0]).toBeLessThan(
      materialize.mock.invocationCallOrder[0]!,
    );
    expect(onProgress).toHaveBeenNthCalledWith(1, 'choosing_destination');
    expect(onProgress).toHaveBeenNthCalledWith(2, 'analyzing');
    expect(onProgress).toHaveBeenNthCalledWith(3, 'exporting');
  });

  it('exports the local package after a non-blocking AI failure', async () => {
    const materialize = vi.fn(() => Promise.resolve(failedAnalysis()));
    const analysisService: SessionAnalysisService = {
      materialize,
    };
    const exportSession = vi.fn(() => Promise.resolve(manifest()));
    const exporter: SessionExporter = { exportSession };
    const chooseDestination = vi.fn(() => Promise.resolve(destination));

    const result = await analyzeAndExport({
      sessionId,
      workspaceRoot: '/Users/student/workspace',
      autoAnalyze: true,
      analysisService,
      exporter,
      chooseDestination,
    });

    expect(materialize).toHaveBeenCalledWith(sessionId, {
      enabled: true,
      workspaceRoot: '/Users/student/workspace',
    });
    expect(exportSession).toHaveBeenCalledWith(sessionId, destination);
    expect(result).toEqual({ kind: 'exported', analysis: failedAnalysis(), manifest: manifest() });
  });

  it('keeps completed local artifacts when the student cancels the export-folder dialog', async () => {
    const materialize = vi.fn(() => Promise.resolve(failedAnalysis()));
    const analysisService: SessionAnalysisService = {
      materialize,
    };
    const exportSession = vi.fn(() => Promise.resolve(manifest()));
    const exporter: SessionExporter = { exportSession };

    const result = await analyzeAndExport({
      sessionId,
      workspaceRoot: '',
      autoAnalyze: false,
      analysisService,
      exporter,
      chooseDestination: () => Promise.resolve(undefined),
    });

    expect(materialize).not.toHaveBeenCalled();
    expect(exportSession).not.toHaveBeenCalled();
    expect(result).toEqual({ kind: 'export_cancelled' });
  });
});

import type { AnalysisLog, ExportManifest } from '../domain/types';
import type { SessionAnalysisService } from '../reports/analysisService';
import type { ExportDestination, SessionExporter } from '../reports/exporter';

export type FinishAnalyzeExportResult =
  | Readonly<{ kind: 'export_cancelled' }>
  | Readonly<{ kind: 'exported'; analysis: AnalysisLog; manifest: ExportManifest }>;

export type FinishAnalyzeExportProgress = 'choosing_destination' | 'analyzing' | 'exporting';

export interface FinishAnalyzeExportInput {
  readonly sessionId: string;
  readonly workspaceRoot: string;
  readonly autoAnalyze: boolean;
  readonly analysisService: SessionAnalysisService;
  readonly exporter: SessionExporter;
  readonly chooseDestination: () => Promise<ExportDestination | undefined>;
  readonly onProgress?: (progress: FinishAnalyzeExportProgress) => Promise<void> | void;
}

export async function analyzeAndExport(
  input: FinishAnalyzeExportInput,
): Promise<FinishAnalyzeExportResult> {
  await input.onProgress?.('choosing_destination');
  const destination = await input.chooseDestination();
  if (destination === undefined) {
    return { kind: 'export_cancelled' };
  }
  await input.onProgress?.('analyzing');
  const analysis = await input.analysisService.materialize(input.sessionId, {
    enabled: input.autoAnalyze,
    workspaceRoot: input.workspaceRoot,
  });
  await input.onProgress?.('exporting');
  const manifest = await input.exporter.exportSession(input.sessionId, destination);
  return { kind: 'exported', analysis, manifest };
}

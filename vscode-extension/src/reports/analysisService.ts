import type { AiClient } from '../ai/aiClient';
import type { JsonObject } from '../domain/types';
import type { SessionArtifactKind } from '../storage/sessionRepository';
import {
  createCompletedAnalysisLog,
  createFailedAnalysisLog,
  createSkippedAnalysisLog,
  serializeAnalysisLog,
} from './analysisLog';
import type { AnalysisLog } from '../domain/types';

const decoder = new TextDecoder();

export interface AnalysisArtifactRepository {
  readArtifact(
    sessionId: string,
    kind: Extract<SessionArtifactKind, 'classroom_brief'>,
  ): Promise<Uint8Array | undefined>;
  writeArtifact(
    sessionId: string,
    kind: Extract<SessionArtifactKind, 'ai_analysis'>,
    bytes: Uint8Array,
  ): Promise<void>;
}

export interface SessionAnalysisService {
  materialize(
    sessionId: string,
    options: Readonly<{ enabled: boolean; workspaceRoot: string }>,
  ): Promise<AnalysisLog>;
}

function isJsonObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function parseBrief(bytes: Uint8Array | undefined): JsonObject {
  if (bytes === undefined) {
    throw new Error('Classroom brief is missing.');
  }
  const value = JSON.parse(decoder.decode(bytes)) as unknown;
  if (!isJsonObject(value)) {
    throw new Error('Classroom brief is invalid.');
  }
  return value;
}

export class FileSessionAnalysisService implements SessionAnalysisService {
  public constructor(
    private readonly repository: AnalysisArtifactRepository,
    private readonly aiClient: Pick<AiClient, 'analyzeSession'>,
    private readonly now: () => Date,
  ) {}

  public async materialize(
    sessionId: string,
    options: Readonly<{ enabled: boolean; workspaceRoot: string }>,
  ): Promise<AnalysisLog> {
    const generatedAt = this.now().toISOString();
    let result: AnalysisLog;
    try {
      const brief = parseBrief(await this.repository.readArtifact(sessionId, 'classroom_brief'));
      if (!options.enabled) {
        result = createSkippedAnalysisLog(sessionId, generatedAt, 'disabled_by_student');
      } else {
        try {
          const analysis = await this.aiClient.analyzeSession({
            sessionId,
            workspaceRoot: options.workspaceRoot,
            brief,
            evidence: [],
            codeFragments: [],
          });
          result = createCompletedAnalysisLog(sessionId, generatedAt, analysis);
        } catch (error) {
          result = createFailedAnalysisLog(sessionId, generatedAt, error);
        }
      }
    } catch (error) {
      result = createFailedAnalysisLog(sessionId, generatedAt, error);
    }
    await this.repository.writeArtifact(sessionId, 'ai_analysis', serializeAnalysisLog(result));
    return result;
  }
}

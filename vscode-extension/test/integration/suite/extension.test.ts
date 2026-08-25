import { strict as assert } from 'node:assert';
import { join } from 'node:path';

import * as vscode from 'vscode';

interface TestAuditApi {
  readonly storageRoot: string;
  startSession(): Promise<string>;
  finishSession(): Promise<string>;
  finishSessionWithAnalysis(): Promise<string>;
  flush(): Promise<void>;
  readBrief(sessionId: string): Promise<unknown>;
  readAnalysisLog(sessionId: string): Promise<unknown>;
  recoverPersistedSession(): Promise<Readonly<{
    session_id: string;
    status: string;
    last_persisted_seq: number;
  }> | undefined>;
}

const expectedCommands = [
  'behaviorAudit.openTeacher',
  'behaviorAudit.openStudent',
  'behaviorAudit.publishPlan',
  'behaviorAudit.suggestPlan',
  'behaviorAudit.importPlan',
  'behaviorAudit.exportPlan',
  'behaviorAudit.startCapture',
  'behaviorAudit.resumeCapture',
  'behaviorAudit.finishCapture',
  'behaviorAudit.finishAnalyzeExport',
  'behaviorAudit.abandonCapture',
  'behaviorAudit.runPython',
  'behaviorAudit.analyzeSession',
  'behaviorAudit.exportSession',
  'behaviorAudit.openDataLocation',
  'behaviorAudit.configureAiKey',
  'behaviorAudit.clearAiKey',
  'behaviorAudit.pasteAndRecord',
] as const;

suite('Behavior Audit extension host', () => {
  test('persists edit/save evidence, materializes five brief categories, and detects interruption', async function () {
    this.timeout(30_000);
    const extension = vscode.extensions.getExtension<TestAuditApi>(
      'bluedot-ai.behavior-audit-vscode',
    );
    assert.ok(extension, 'extension must be installed in the development host');
    const api = await extension.activate();
    assert.ok(api, 'test mode must expose the narrow audit API');
    assert.ok(api.storageRoot.length > 0);

    const registered = await vscode.commands.getCommands(true);
    for (const command of expectedCommands) {
      assert.ok(registered.includes(command), `missing command: ${command}`);
    }

    const workspace = vscode.workspace.workspaceFolders?.[0];
    assert.ok(workspace, 'fixture workspace must be open');
    const uri = vscode.Uri.file(join(workspace.uri.fsPath, 'analyze_scores.py'));
    const document = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(document);

    const completedSessionId = await api.startSession();
    const edit = new vscode.WorkspaceEdit();
    edit.insert(uri, new vscode.Position(0, 0), '# integration edit\n');
    assert.equal(await vscode.workspace.applyEdit(edit), true);
    assert.equal(await document.save(), true);
    await new Promise((resolveWait) => setTimeout(resolveWait, 1200));
    const finishedSessionId = await api.finishSession();
    assert.equal(finishedSessionId, completedSessionId);

    const brief = await api.readBrief(finishedSessionId);
    assert.ok(brief !== null && typeof brief === 'object' && !Array.isArray(brief));
    const semanticKeys = Object.keys(brief as Record<string, unknown>).filter(
      (key) => !['schema_version', 'session_id', 'generated_at'].includes(key),
    );
    assert.deepEqual(
      new Set(semanticKeys),
      new Set([
        'session_result',
        'effective_observation',
        'run_statistics',
        'evidence_summary',
        'attention_point',
      ]),
    );

    const analyzedSessionId = await api.startSession();
    assert.equal(await api.finishSessionWithAnalysis(), analyzedSessionId);
    const analysisLog = await api.readAnalysisLog(analyzedSessionId);
    assert.ok(analysisLog !== null && typeof analysisLog === 'object' && !Array.isArray(analysisLog));
    assert.equal((analysisLog as { readonly session_id?: unknown }).session_id, analyzedSessionId);
    assert.equal((analysisLog as { readonly status?: unknown }).status, 'skipped');
    assert.equal(
      (analysisLog as { readonly reason?: { readonly code?: unknown } }).reason?.code,
      'disabled_by_student',
    );

    const interruptedSessionId = await api.startSession();
    const secondEdit = new vscode.WorkspaceEdit();
    secondEdit.insert(uri, new vscode.Position(0, 0), '# unfinished edit\n');
    assert.equal(await vscode.workspace.applyEdit(secondEdit), true);
    assert.equal(await document.save(), true);
    await new Promise((resolveWait) => setTimeout(resolveWait, 1200));
    await api.flush();

    const recovered = await api.recoverPersistedSession();
    assert.equal(recovered?.session_id, interruptedSessionId);
    assert.equal(recovered?.status, 'interrupted');
    assert.ok((recovered?.last_persisted_seq ?? 0) >= 2);
  });
});

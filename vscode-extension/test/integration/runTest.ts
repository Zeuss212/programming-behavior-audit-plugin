import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';

import { runTests } from '@vscode/test-electron';

async function main(): Promise<void> {
  const extensionDevelopmentPath = resolve(__dirname, '../../..');
  const extensionTestsPath = resolve(__dirname, 'suite');
  const fixturePath = resolve(extensionDevelopmentPath, 'test/fixtures');
  const userDataDirectory = await mkdtemp(`${tmpdir()}/behavior-audit-vscode-test-`);
  try {
    await runTests({
      version: '1.125.0',
      extensionDevelopmentPath,
      extensionTestsPath,
      extensionTestsEnv: {
        BEHAVIOR_AUDIT_TEST_MODE: '1',
      },
      launchArgs: [
        fixturePath,
        `--user-data-dir=${userDataDirectory}`,
        '--disable-workspace-trust',
        '--skip-welcome',
        '--skip-release-notes',
      ],
    });
  } finally {
    await rm(userDataDirectory, { recursive: true, force: true });
  }
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});

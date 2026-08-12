import { cp, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

import { runTests } from '@vscode/test-electron';

async function main(): Promise<void> {
  const extensionDevelopmentPath = resolve(__dirname, '../../..');
  const extensionTestsPath = resolve(__dirname, 'suite');
  const fixtureSourcePath = resolve(extensionDevelopmentPath, 'test/fixtures');
  const testRoot = await mkdtemp(`${tmpdir()}/ba-vsc-`);
  const fixturePath = join(testRoot, 'w');
  const userDataDirectory = join(testRoot, 'u');
  const localExecutablePath = process.env.VSCODE_EXECUTABLE_PATH?.trim();
  try {
    await cp(fixtureSourcePath, fixturePath, { recursive: true });
    await mkdir(userDataDirectory);
    await runTests({
      ...(localExecutablePath
        ? { vscodeExecutablePath: localExecutablePath }
        : { version: '1.125.0' }),
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
    await rm(testRoot, { recursive: true, force: true });
  }
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});

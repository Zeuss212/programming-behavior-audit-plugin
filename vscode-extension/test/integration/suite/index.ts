import { resolve } from 'node:path';

import Mocha from 'mocha';

export async function run(): Promise<void> {
  const mocha = new Mocha({ ui: 'tdd', color: true, timeout: 30_000 });
  mocha.addFile(resolve(__dirname, 'extension.test.js'));
  await new Promise<void>((resolveRun, reject) => {
    mocha.run((failures) => {
      if (failures === 0) {
        resolveRun();
      } else {
        reject(new Error(`${String(failures)} extension-host test(s) failed.`));
      }
    });
  });
}

import { describe, expect, it } from 'vitest';

import { postNonBlockingInformationMessage } from '../ui/nonBlockingNotice';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe('postNonBlockingInformationMessage', () => {
  it('returns control before a notification is dismissed', async () => {
    const pendingNotification = deferred<void>();
    const displayed: string[] = [];

    const result = postNonBlockingInformationMessage((message) => {
      displayed.push(message);
      return pendingNotification.promise;
    }, '课堂简报已导出。');

    expect(result).toBeUndefined();
    expect(displayed).toEqual(['课堂简报已导出。']);

    pendingNotification.resolve();
    await pendingNotification.promise;
  });
});

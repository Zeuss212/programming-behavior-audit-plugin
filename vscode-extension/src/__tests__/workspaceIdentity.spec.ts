import { describe, expect, it } from 'vitest';

import { workspaceIdentity } from '../capture/workspaceIdentity';

describe('workspaceIdentity', () => {
  it('returns a deterministic path-free SHA-256 workspace identifier', () => {
    const identity = workspaceIdentity(['file:///private/course/']);

    expect(identity).toBe('c6ed02c5c42ad7a9a3b2eed5670d71329d6b982d37f1a7746fa61eac64c8b1b6');
    expect(identity).toMatch(/^[a-f0-9]{64}$/);
    expect(identity).not.toContain('private');
    expect(identity).not.toContain('course');
  });

  it('normalizes order and trailing slashes for multi-root workspaces', () => {
    expect(workspaceIdentity(['file:///b/', 'file:///a'])).toBe(
      workspaceIdentity(['file:///a/', 'file:///b']),
    );
  });
});

# VS Code Main Uncommitted Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the cohesive VS Code 0.1.5 finish/analyze/export changes currently stranded in the dirty `main` worktree on an isolated, verified, pushed branch without merging or losing the original files.

**Architecture:** Treat the dirty `main` worktree as a read-only source until the isolated branch is verified and pushed. Transfer only `vscode-extension/**`, prove the transferred tracked diff and two untracked files are byte-equivalent, run the extension's full quality and package gates, then remove only the exact source copies from `main` while retaining a recoverable patch and file backup under `/private/tmp`.

**Tech Stack:** Git worktrees, TypeScript, Vitest, ESLint, esbuild, VSCE, SHA-256

## Global Constraints

- Source worktree: `/Users/sxh/编程行为监控分析插件_交付版_20260727` on `main` at `2920f9331693e2798f8d92fc066dd89fb4c6e7d0`.
- Destination worktree: `/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/vscode-finish-analyze-export` on `codex/vscode-finish-analyze-export`.
- Transfer only `vscode-extension/**`; do not stage or modify any BAMS, classroom, release, or handover file.
- Do not merge, force-push, delete a branch, or change `main` history.
- Do not clean the source copies until the remote branch SHA exactly matches the verified local commit.
- Keep generated `.vsix`, `node_modules`, coverage, and build output out of Git.

---

### Task 1: Capture and transfer the VS Code change set

**Files:**
- Modify in destination: the 16 tracked `vscode-extension/**` files reported by `git diff --stat` in the source worktree.
- Create in destination: `vscode-extension/src/ui/nonBlockingNotice.ts`
- Create in destination: `vscode-extension/src/__tests__/nonBlockingNotice.spec.ts`
- Create backup: `/private/tmp/codex-main-split-20260826/vscode-extension.patch`
- Create backup copies under: `/private/tmp/codex-main-split-20260826/untracked/vscode-extension/`

**Interfaces:**
- Consumes: the exact dirty `main` VS Code diff and two untracked source files.
- Produces: a destination worktree whose VS Code change set is byte-equivalent to the source.

- [ ] **Step 1: Record the source boundary and create recoverable backups**

Run from the source worktree:

```bash
test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = 2920f9331693e2798f8d92fc066dd89fb4c6e7d0
mkdir -p /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/ui
mkdir -p /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/__tests__
git diff --binary -- vscode-extension > /private/tmp/codex-main-split-20260826/vscode-extension.patch
cp vscode-extension/src/ui/nonBlockingNotice.ts /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/ui/
cp vscode-extension/src/__tests__/nonBlockingNotice.spec.ts /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/__tests__/
sha256sum /private/tmp/codex-main-split-20260826/vscode-extension.patch \
  /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/ui/nonBlockingNotice.ts \
  /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/__tests__/nonBlockingNotice.spec.ts
```

Expected: the source branch and SHA match, the patch is non-empty, and all three backup artifacts have SHA-256 values.

- [ ] **Step 2: Apply only the captured VS Code changes to the destination**

Run from the destination worktree:

```bash
test "$(git branch --show-current)" = codex/vscode-finish-analyze-export
git apply /private/tmp/codex-main-split-20260826/vscode-extension.patch
cp /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/ui/nonBlockingNotice.ts vscode-extension/src/ui/
cp /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/__tests__/nonBlockingNotice.spec.ts vscode-extension/src/__tests__/
git status --short
```

Expected: only the 16 tracked VS Code files and the two new VS Code files are changed, in addition to this plan document.

- [ ] **Step 3: Prove source and destination equivalence**

Run from the source worktree:

```bash
{
  git diff --name-only -- vscode-extension
  printf '%s\n' \
    vscode-extension/src/ui/nonBlockingNotice.ts \
    vscode-extension/src/__tests__/nonBlockingNotice.spec.ts
} | sort -u > /private/tmp/codex-main-split-20260826/vscode-files.txt
while IFS= read -r file; do sha256sum "$file"; done \
  < /private/tmp/codex-main-split-20260826/vscode-files.txt \
  > /private/tmp/codex-main-split-20260826/vscode-source.sha256
```

Run from the destination worktree:

```bash
while IFS= read -r file; do sha256sum "$file"; done \
  < /private/tmp/codex-main-split-20260826/vscode-files.txt \
  > /private/tmp/codex-main-split-20260826/vscode-destination.sha256
git diff --binary -- vscode-extension \
  > /private/tmp/codex-main-split-20260826/vscode-destination.patch
diff -u \
  /private/tmp/codex-main-split-20260826/vscode-source.sha256 \
  /private/tmp/codex-main-split-20260826/vscode-destination.sha256
cmp \
  /private/tmp/codex-main-split-20260826/vscode-extension.patch \
  /private/tmp/codex-main-split-20260826/vscode-destination.patch
```

Expected: both `diff` and `cmp` produce no output and exit successfully.

- [ ] **Step 4: Commit the migration plan separately**

```bash
git add docs/superpowers/plans/2026-08-26-vscode-main-uncommitted-migration.md
git commit -m "docs: plan VS Code main change migration"
```

Expected: one documentation-only commit; VS Code changes remain unstaged.

### Task 2: Verify and commit the VS Code feature

**Files:**
- Modify/Create: only `vscode-extension/**` files transferred in Task 1.

**Interfaces:**
- Consumes: the byte-equivalent destination change set.
- Produces: a verified commit implementing non-blocking finish/analyze/export progress for extension version 0.1.5.

- [ ] **Step 1: Install the locked dependencies**

```bash
cd vscode-extension
npm ci
```

Expected: install succeeds using `package-lock.json`; no tracked file changes.

- [ ] **Step 2: Run the full quality gate**

```bash
npm run verify
```

Expected: ESLint, TypeScript, all Vitest unit tests, and the production build pass.

- [ ] **Step 3: Build and validate the VSIX**

```bash
npm run package
```

Expected: `behavior-audit-vscode-0.1.5.vsix` is created and `scripts/verify-vsix.mjs` accepts it; the VSIX remains ignored/untracked.

- [ ] **Step 4: Confirm scope and commit**

```bash
git diff --check
git status --short
git add vscode-extension
git diff --cached --check
git commit -m "feat: make finish analyze export progress non-blocking"
```

Expected: the commit contains only the 18 intended VS Code files.

### Task 3: Push without merging and clean only the verified source copies

**Files:**
- No destination source changes.
- Restore in the source worktree: only the tracked `vscode-extension/**` files captured in the patch.
- Move out of the source worktree: only the two untracked VS Code files already backed up in Task 1.

**Interfaces:**
- Consumes: the verified local destination commit and backups.
- Produces: a remote `codex/vscode-finish-analyze-export` SHA plus a `main` worktree with the VS Code group removed and all non-VS-Code groups untouched.

- [ ] **Step 1: Push the destination branch without force**

```bash
git push origin HEAD:refs/heads/codex/vscode-finish-analyze-export
git ls-remote origin refs/heads/codex/vscode-finish-analyze-export
```

Expected: the remote SHA exactly equals `git rev-parse HEAD`.

- [ ] **Step 2: Reconfirm the source has not changed since capture**

Run from the source worktree:

```bash
git diff --binary -- vscode-extension \
  > /private/tmp/codex-main-split-20260826/vscode-source-before-clean.patch
cmp \
  /private/tmp/codex-main-split-20260826/vscode-extension.patch \
  /private/tmp/codex-main-split-20260826/vscode-source-before-clean.patch
cmp \
  vscode-extension/src/ui/nonBlockingNotice.ts \
  /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/ui/nonBlockingNotice.ts
cmp \
  vscode-extension/src/__tests__/nonBlockingNotice.spec.ts \
  /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/__tests__/nonBlockingNotice.spec.ts
```

Expected: all three `cmp` commands produce no output and exit successfully. Stop if any comparison differs.

- [ ] **Step 3: Remove only the safely preserved VS Code copies from `main`**

Run from the source worktree only after Step 1 and Step 2 pass:

```bash
git restore --worktree -- vscode-extension
mv vscode-extension/src/ui/nonBlockingNotice.ts /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/ui/nonBlockingNotice.source-copy.ts
mv vscode-extension/src/__tests__/nonBlockingNotice.spec.ts /private/tmp/codex-main-split-20260826/untracked/vscode-extension/src/__tests__/nonBlockingNotice.source-copy.spec.ts
git status --short
```

Expected: no `vscode-extension/**` changes remain in `main`; every BAMS, classroom, release, and handover file remains exactly as before.

- [ ] **Step 4: Record completion evidence**

Record the local and remote branch SHA, `npm run verify` result, VSIX verification result, backup paths, and the remaining `main` status. Do not merge or delete either branch/worktree.

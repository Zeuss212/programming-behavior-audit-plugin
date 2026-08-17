# Classroom CI Quality Gates Design

**Status:** Draft — awaiting user review

## Goal

Add repeatable GitHub Actions quality gates for the two repositories that make up the local classroom platform, without merging branches, pushing changes, deploying artifacts, or accessing local AI credentials.

## Context

The main repository already has a VS Code extension workflow, but the trusted classroom-sync service has no dedicated CI workflow. The Vue classroom interface lives in the independent `lab-platform-frontend` repository, so it requires its own workflow and commit; a main-repository workflow cannot validate it after checkout.

## Options considered

1. Add only a main-repository workflow. This leaves Vue classroom changes without a remote gate.
2. Add one workflow in each repository. This keeps checkout, dependency installation, and required checks aligned with the code that each repository actually contains. **Recommended.**
3. Create a cross-repository orchestrator. This would require repository-dispatch credentials and branch/ref coordination, adding a secret-dependent control plane before the individual gates exist.

## Scope

### Main repository: classroom-sync

Create `.github/workflows/classroom-sync.yml` on `codex/classroom-main-integration`.

- Trigger on pushes and pull requests that touch `services/classroom-sync/**`, `contracts/classroom/**`, `deploy/classroom/local-demo/**`, or the workflow itself.
- Use a read-only `contents` permission, `actions/checkout@v4`, Python 3.12, and `astral-sh/setup-uv@v5` with the classroom-sync project configuration as the cache dependency.
- Run exactly the already-established commands from the service directory:
  - `uv run --directory services/classroom-sync --extra dev pytest -q`
  - `uv run --directory services/classroom-sync --extra dev ruff check src tests`
  - `uv run --directory services/classroom-sync --extra dev mypy src`
- Do not run Docker Compose, migrations against a persistent database, deployment scripts, or any command that reads `deploy/classroom/local-demo/.env.ai`.

### Frontend repository: lab-platform-frontend

Create `.github/workflows/classroom-ui.yml` on `codex/classroom-ui` in `/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/lab-platform-frontend-classroom-ui`.

- Trigger on pushes and pull requests that touch `src/**`, `public/**`, `package.json`, `package-lock.json`, Vite/TypeScript configuration, or the workflow itself.
- Use a read-only `contents` permission, `actions/checkout@v4`, `actions/setup-node@v4`, Node 22, and npm cache keyed by `package-lock.json`.
- Install reproducibly with `npm ci`, then run only the documented non-mutating checks:
  - `npm test -- --run`
  - `npm run type-check`
  - `npm run build`
  - `npx --no-install oxlint .`
  - `npx --no-install eslint .`
- Do not run any `--fix` lint script, candidate-image build, registry push, browser test, Docker command, or command using a local environment file.

## Behavior and failure handling

- Each workflow uses a concurrency group scoped to workflow name and ref, cancelling an older in-progress run for the same branch or pull request.
- Dependency, type, lint, test, or build failures fail only their own job and expose standard GitHub Actions logs; no secrets are configured or printed.
- These workflow files create checks but cannot configure repository branch-protection policy. Enforcing them as required checks remains a repository-admin action after the workflows are pushed and observed once.

## Verification plan

1. Verify both worktrees are clean before changes and remain isolated afterward.
2. Review each workflow for its exact trigger paths, read-only permissions, lockfile-based installation, and absence of credential, Docker, image-push, and `--fix` commands.
3. Re-run the same backend and frontend local quality commands used by the workflows.
4. Commit the main-repository and frontend workflow changes separately. Do not push; GitHub-hosted execution remains pending until the user authorizes a push or pull request.

## Non-goals

- No merge, push, PR creation, branch-protection change, deployment, Docker reset, or registry activity.
- No AI configuration change, provider call, environment-file read, or credential migration.
- No cross-repository status aggregation, because it would introduce a secret-bearing dispatch dependency.

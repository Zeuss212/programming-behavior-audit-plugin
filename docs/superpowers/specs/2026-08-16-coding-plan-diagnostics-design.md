# Coding Plan Classroom Diagnostics Design

## Goal

Keep classroom AI analysis on GLM Coding Plan while making a local validation run bounded to one provider attempt and recording only a safe, teacher-supportable failure category.

## Scope

- Keep the configured Coding Plan OpenAI-compatible base URL and model unchanged.
- Add an optional server-only `CLASSROOM_AI_MAX_ATTEMPTS` setting. Its valid range is 1 through 3 and the default remains 3. The ignored local demo environment sets it to 1 for a controlled validation run.
- Preserve safe provider failure categories in `classroom_brief_analysis_jobs.failure_code`: network, timeout, rate-limited, provider-server, authorization-or-policy, invalid-request, invalid-provider-response, and invalid-analysis-response.
- Retry only retryable categories. Authorization/policy, request, and response-shape errors reach `unavailable` immediately.
- Do not persist upstream response bodies, request headers, API keys, raw evidence, or student analysis prompts.

## Non-goals

- Do not bypass Coding Plan eligibility restrictions, imitate an official client, or alter the configured provider endpoint.
- Do not expose provider failure categories in the student UI.
- Do not merge, push, reset local classroom data, or replace the user-managed API key.

## Data flow

`OpenAiCompletionClient` maps transport and HTTP outcomes to safe `UpstreamUnavailableError.code` values. `OpenAiBriefAnalysisService` preserves provider codes and maps only its own JSON/schema failures. `BriefAnalysisJobService` stores that safe code and retries only when `error.retryable` is true, using the configured maximum attempt count. The teacher continues to see the allowlisted terminal AI status; local diagnostics read the stored safe code directly from the job table.

## Verification

1. Unit tests prove HTTP and payload failures map to distinct safe codes without exposing bodies or keys.
2. Integration tests prove a non-retryable failure completes after one attempt, while a retryable failure obeys the configured maximum.
3. Full backend tests, ruff, and mypy pass.
4. The local compose worker runs with `CLASSROOM_AI_MAX_ATTEMPTS=1` and a Computer Use teacher/student flow creates exactly one provider attempt. The result reports only an allowlisted status and safe failure code.

## Self-review

- No credentials, API bodies, or raw evidence are included.
- The fallback default preserves the existing three-attempt production behavior.
- Local single-attempt behavior is opt-in through the ignored demo environment file.

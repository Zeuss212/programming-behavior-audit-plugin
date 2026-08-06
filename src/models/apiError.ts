export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly details?: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

import { JupyterFrontEnd } from '@jupyterlab/application';

import { BehaviorEventLogger, IBehaviorContext } from './events';

interface IActiveFileInterval {
  mode: 'typing' | 'deleting';
  startedAt: number;
  context: IBehaviorContext;
  insertedCharCount: number;
  deletedCharCount: number;
  latestSource: string;
}

type SignalLike<TArgs> = {
  connect: (slot: (sender: unknown, args: TArgs) => void) => void;
};

type FileContextLike = {
  path?: unknown;
  pathChanged?: SignalLike<unknown>;
};

const IDLE_TIMEOUT_MS = 5_000;

export class PythonFileMonitor {
  private readonly boundModels = new WeakSet<object>();
  private readonly sourceByModel = new WeakMap<object, string>();
  private activeInterval: IActiveFileInterval | null = null;
  private idleTimer: number | undefined;

  constructor(
    private readonly app: JupyterFrontEnd,
    private readonly logger: BehaviorEventLogger
  ) {}

  start(): void {
    this.app.shell.currentChanged?.connect(() => {
      this.bindCurrentWidget();
    });
    this.bindCurrentWidget();
  }

  getCurrentContext(): IBehaviorContext | null {
    const path = currentPythonPath(this.app);
    if (!path) {
      return null;
    }
    return contextForPath(path);
  }

  getCurrentSource(): string {
    return sourceFromWidget(this.app.shell.currentWidget);
  }

  close(): void {
    this.endActiveInterval(Date.now());
  }

  private bindCurrentWidget(): void {
    const widget = this.app.shell.currentWidget;
    const context = contextFromWidget(widget);
    const model = modelFromWidget(widget);
    const path = pythonPathFromContext(context);
    if (!path || !model || !context || this.boundModels.has(model)) {
      return;
    }

    this.boundModels.add(model);
    this.sourceByModel.set(model, sourceFromModel(model));

    const signal = (model as { contentChanged?: SignalLike<unknown> })
      .contentChanged;
    signal?.connect(() => {
      this.handleSourceChanged(model, context);
    });

    const sharedSignal = (
      model as { sharedModel?: { changed?: SignalLike<unknown> } }
    ).sharedModel?.changed;
    sharedSignal?.connect(() => {
      this.handleSourceChanged(model, context);
    });

    context.pathChanged?.connect(() => {
      this.syncActiveIntervalContext(context);
    });
  }

  private handleSourceChanged(
    model: object,
    fileContext: FileContextLike
  ): void {
    const path = pythonPathFromContext(fileContext);
    if (!path) {
      return;
    }

    const previousSource = this.sourceByModel.get(model) ?? '';
    const nextSource = sourceFromModel(model);
    this.sourceByModel.set(model, nextSource);

    const change = countTextDiff(previousSource, nextSource);
    if (change.inserted <= 0 && change.deleted <= 0) {
      return;
    }

    const mode = change.deleted > change.inserted ? 'deleting' : 'typing';
    const now = Date.now();
    const context = contextForPath(path);

    if (this.activeInterval && this.activeInterval.mode !== mode) {
      this.endActiveInterval(now);
    }

    if (!this.activeInterval) {
      this.activeInterval = {
        mode,
        startedAt: now,
        context,
        insertedCharCount: 0,
        deletedCharCount: 0,
        latestSource: nextSource
      };
      this.logger.emit(
        mode === 'typing' ? 'typing_start' : 'deleting_start',
        context
      );
    }

    this.activeInterval.context = context;
    this.activeInterval.insertedCharCount += change.inserted;
    this.activeInterval.deletedCharCount += change.deleted;
    this.activeInterval.latestSource = nextSource;
    this.scheduleIdleTimer();
  }

  private syncActiveIntervalContext(fileContext: FileContextLike): void {
    if (!this.activeInterval) {
      return;
    }

    const path = pythonPathFromContext(fileContext);
    if (path) {
      this.activeInterval.context = contextForPath(path);
    }
  }

  private scheduleIdleTimer(): void {
    if (this.idleTimer !== undefined) {
      window.clearTimeout(this.idleTimer);
    }

    this.idleTimer = window.setTimeout(() => {
      const context = this.activeInterval?.context ?? this.getCurrentContext();
      this.endActiveInterval(Date.now());
      if (context) {
        this.logger.emit('idle', context);
      }
    }, IDLE_TIMEOUT_MS);
  }

  private endActiveInterval(endedAt: number): void {
    if (!this.activeInterval) {
      return;
    }

    const interval = this.activeInterval;
    this.activeInterval = null;
    if (this.idleTimer !== undefined) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = undefined;
    }

    const durationMs = Math.max(0, endedAt - interval.startedAt);
    if (interval.mode === 'typing') {
      this.logger.emit('typing_end', interval.context, {
        duration_ms: durationMs,
        inserted_char_count: interval.insertedCharCount
      });
      this.logger.emit('code_input_completed', interval.context, {
        input_started_at: new Date(interval.startedAt).toISOString(),
        input_ended_at: new Date(endedAt).toISOString(),
        duration_ms: durationMs,
        cell_source: interval.latestSource
      });
      return;
    }

    this.logger.emit('deleting_end', interval.context, {
      duration_ms: durationMs,
      deleted_char_count: interval.deletedCharCount
    });
  }
}

export function currentPythonPath(app: JupyterFrontEnd): string | null {
  return pythonPathFromContext(contextFromWidget(app.shell.currentWidget));
}

function contextFromWidget(widget: unknown): FileContextLike | null {
  return (widget as { context?: FileContextLike } | null)?.context ?? null;
}

function pythonPathFromContext(context: FileContextLike | null): string | null {
  const path = context?.path;
  if (typeof path === 'string' && path.toLowerCase().endsWith('.py')) {
    return path;
  }
  return null;
}

export function sourceFromWidget(widget: unknown): string {
  const model = modelFromWidget(widget);
  return model ? sourceFromModel(model) : '';
}

function modelFromWidget(widget: unknown): object | null {
  const context = (widget as { context?: { model?: unknown } } | null)?.context;
  return context?.model && typeof context.model === 'object'
    ? context.model
    : null;
}

function sourceFromModel(model: object): string {
  const sharedModel = (model as { sharedModel?: unknown }).sharedModel;
  const getSource = (sharedModel as { getSource?: () => unknown } | undefined)
    ?.getSource;
  if (typeof getSource === 'function') {
    const source = getSource.call(sharedModel);
    return typeof source === 'string' ? source : '';
  }

  const toString = (model as { toString?: () => unknown }).toString;
  if (typeof toString === 'function') {
    const source = toString.call(model);
    return typeof source === 'string' ? source : '';
  }
  return '';
}

function contextForPath(path: string): IBehaviorContext {
  return {
    document_type: 'python_file',
    file_path: path,
    file_name: path.split('/').pop() ?? path
  };
}

function countTextDiff(
  previousSource: string,
  nextSource: string
): { inserted: number; deleted: number } {
  let prefixLength = 0;
  const minLength = Math.min(previousSource.length, nextSource.length);
  while (
    prefixLength < minLength &&
    previousSource[prefixLength] === nextSource[prefixLength]
  ) {
    prefixLength += 1;
  }

  let suffixLength = 0;
  const previousRest = previousSource.length - prefixLength;
  const nextRest = nextSource.length - prefixLength;
  while (
    suffixLength < previousRest &&
    suffixLength < nextRest &&
    previousSource[previousSource.length - 1 - suffixLength] ===
      nextSource[nextSource.length - 1 - suffixLength]
  ) {
    suffixLength += 1;
  }

  return {
    inserted: Math.max(0, nextSource.length - prefixLength - suffixLength),
    deleted: Math.max(0, previousSource.length - prefixLength - suffixLength)
  };
}

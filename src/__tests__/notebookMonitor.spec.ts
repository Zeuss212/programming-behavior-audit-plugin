jest.mock('@jupyterlab/notebook', () => {
  const createSignal = () => {
    const slots: Array<(sender: unknown, args: unknown) => void> = [];
    return {
      connect(slot: (sender: unknown, args: unknown) => void): void {
        slots.push(slot);
      },
      emit(args: unknown): void {
        for (const slot of slots) slot(undefined, args);
      },
      reset(): void {
        slots.length = 0;
      }
    };
  };
  return {
    NotebookActions: {
      executionScheduled: createSignal(),
      executionStarted: createSignal(),
      executed: createSignal()
    }
  };
});

import { INotebookTracker } from '@jupyterlab/notebook';

import { EditStateMachine } from '../editState';
import { BehaviorEventLogger } from '../events';
import { NotebookBehaviorMonitor } from '../notebookMonitor';

type Slot<T> = (sender: unknown, args: T) => void;

class TestSignal<T> {
  readonly slots: Array<Slot<T>> = [];

  connect(slot: Slot<T>): void {
    this.slots.push(slot);
  }

  emit(args: T): void {
    for (const slot of this.slots) slot(this, args);
  }
}

type ActionSignal = {
  emit(args: unknown): void;
  reset(): void;
};

const notebookActions = (
  jest.requireMock('@jupyterlab/notebook') as {
    NotebookActions: {
      executionScheduled: ActionSignal;
      executionStarted: ActionSignal;
      executed: ActionSignal;
    };
  }
).NotebookActions;

function cellFixture(
  id: string,
  initialSource: string,
  type = 'code',
  outputs: unknown[] = []
) {
  let source = initialSource;
  const changed = new TestSignal<unknown>();
  const pasteListeners: Array<() => void> = [];
  const cell = {
    model: {
      id,
      type,
      sharedModel: {
        changed,
        getSource: () => source
      },
      outputs: {
        length: outputs.length,
        get: (index: number) => outputs[index]
      }
    },
    editor: {
      host: {
        addEventListener: (event: string, listener: () => void) => {
          if (event === 'paste') pasteListeners.push(listener);
        }
      }
    }
  };
  return {
    cell,
    changed,
    update(nextSource: string, change: unknown): void {
      source = nextSource;
      changed.emit(change);
    },
    paste(): void {
      for (const listener of pasteListeners) listener();
    }
  };
}

function panelFixture(
  id: string,
  path: string,
  notebookId: string,
  cells: Array<ReturnType<typeof cellFixture>['cell']>
) {
  const pathChanged = new TestSignal<unknown>();
  const statusChanged = new TestSignal<unknown>();
  const panel = {
    id,
    content: {
      widgets: cells,
      activeCell: cells[0] ?? null
    },
    context: {
      path,
      pathChanged,
      model: {
        sharedModel: {
          getMetadata: (key: string) => (key === 'id' ? notebookId : undefined)
        }
      }
    },
    sessionContext: {
      statusChanged
    }
  };
  return {
    panel,
    pathChanged,
    statusChanged,
    rename(nextPath: string): void {
      panel.context.path = nextPath;
      pathChanged.emit(undefined);
    }
  };
}

function trackerFixture(
  panels: Array<ReturnType<typeof panelFixture>['panel']>
) {
  const widgetAdded = new TestSignal<unknown>();
  const currentChanged = new TestSignal<unknown>();
  const activeCellChanged = new TestSignal<unknown>();
  const tracker = {
    widgetAdded,
    currentChanged,
    activeCellChanged,
    restored: Promise.resolve(),
    currentWidget: panels[0] ?? null,
    activeCell: panels[0]?.content.activeCell ?? null,
    forEach: (
      callback: (panel: ReturnType<typeof panelFixture>['panel']) => void
    ) => panels.forEach(callback),
    find: (
      predicate: (panel: ReturnType<typeof panelFixture>['panel']) => boolean
    ) => panels.find(predicate) ?? null
  };
  return {
    tracker,
    widgetAdded,
    currentChanged,
    activeCellChanged,
    activate(panel: ReturnType<typeof panelFixture>['panel']): void {
      tracker.currentWidget = panel;
      tracker.activeCell = panel.content.activeCell;
      currentChanged.emit(panel);
      activeCellChanged.emit(panel.content.activeCell);
    }
  };
}

function monitorFixture() {
  const firstCell = cellFixture('cell-1', 'value = 1\n');
  const firstPanel = panelFixture('panel-1', 'lesson-one.ipynb', 'notebook-1', [
    firstCell.cell
  ]);
  const secondCell = cellFixture('cell-2', 'print(2)\n');
  const secondPanel = panelFixture(
    'panel-2',
    'lesson-two.ipynb',
    'notebook-2',
    [secondCell.cell]
  );
  const tracker = trackerFixture([firstPanel.panel, secondPanel.panel]);
  const logger = {
    emit: jest.fn()
  };
  const editState = {
    close: jest.fn(),
    markPaste: jest.fn(),
    handleTextChange: jest.fn()
  };
  const monitor = new NotebookBehaviorMonitor(
    tracker.tracker as unknown as INotebookTracker,
    logger as unknown as BehaviorEventLogger,
    editState as unknown as EditStateMachine
  );
  return {
    monitor,
    logger,
    editState,
    tracker,
    firstCell,
    firstPanel,
    secondCell,
    secondPanel
  };
}

async function flushRestored(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe('NotebookBehaviorMonitor', () => {
  beforeEach(() => {
    notebookActions.executionScheduled.reset();
    notebookActions.executionStarted.reset();
    notebookActions.executed.reset();
  });

  it('restores the active Notebook and cell context', async () => {
    const fixture = monitorFixture();

    fixture.monitor.start();
    await flushRestored();

    expect(fixture.monitor.getCurrentContext()).toEqual({
      document_type: 'notebook_cell',
      notebook_path: 'lesson-one.ipynb',
      notebook_id: 'notebook-1',
      cell_id: 'cell-1',
      cell_index: 0,
      cell_type: 'code'
    });
  });

  it('converts code edits and paste into edit-state behavior', async () => {
    const fixture = monitorFixture();
    fixture.monitor.start();
    await flushRestored();

    fixture.firstCell.update('value = 12345678901234567\n', {
      sourceChange: [{ insert: '2345678901234567' }]
    });
    fixture.firstCell.paste();

    expect(fixture.editState.handleTextChange).toHaveBeenCalledWith(
      expect.objectContaining({
        inserted: 16,
        deleted: 0
      }),
      expect.objectContaining({
        notebook_path: 'lesson-one.ipynb',
        cell_id: 'cell-1'
      }),
      'value = 12345678901234567\n'
    );
    expect(fixture.editState.markPaste).toHaveBeenCalledTimes(2);
  });

  it('captures a full-line deletion without exposing unrelated source', async () => {
    const lineCell = cellFixture('cell-line', 'a\nremove\nb\n');
    const panel = panelFixture(
      'panel-line',
      'deletion.ipynb',
      'notebook-line',
      [lineCell.cell]
    );
    const tracker = trackerFixture([panel.panel]);
    const logger = { emit: jest.fn() };
    const editState = {
      close: jest.fn(),
      markPaste: jest.fn(),
      handleTextChange: jest.fn()
    };
    const monitor = new NotebookBehaviorMonitor(
      tracker.tracker as unknown as INotebookTracker,
      logger as unknown as BehaviorEventLogger,
      editState as unknown as EditStateMachine
    );
    monitor.start();
    await flushRestored();

    lineCell.update('a\n\nb\n', {
      sourceChange: [{ retain: 2 }, { delete: 6 }]
    });

    expect(editState.handleTextChange).toHaveBeenCalledWith(
      expect.objectContaining({
        inserted: 0,
        deleted: 6,
        deletedContent: 'remove',
        deletedIsFullLine: true
      }),
      expect.any(Object),
      undefined
    );
  });

  it('emits scheduled, started, success, and output-backed error events', async () => {
    const errorCell = cellFixture('cell-error', '1 / 0\n', 'code', [
      {
        output_type: 'error',
        ename: 'ZeroDivisionError',
        evalue: 'division by zero'
      }
    ]);
    const panel = panelFixture(
      'panel-error',
      'errors.ipynb',
      'notebook-error',
      [errorCell.cell]
    );
    const tracker = trackerFixture([panel.panel]);
    const logger = { emit: jest.fn() };
    const editState = {
      close: jest.fn(),
      markPaste: jest.fn(),
      handleTextChange: jest.fn()
    };
    const monitor = new NotebookBehaviorMonitor(
      tracker.tracker as unknown as INotebookTracker,
      logger as unknown as BehaviorEventLogger,
      editState as unknown as EditStateMachine
    );
    monitor.start();
    await flushRestored();
    const args = { cell: errorCell.cell, notebook: panel.panel.content };

    notebookActions.executionScheduled.emit(args);
    notebookActions.executionStarted.emit(args);
    notebookActions.executed.emit({ ...args, success: true });
    notebookActions.executed.emit({ ...args, success: false, error: {} });

    expect(editState.close).toHaveBeenCalledWith('execution');
    expect(logger.emit).toHaveBeenCalledWith(
      'cell_execution_scheduled',
      expect.objectContaining({ cell_id: 'cell-error' })
    );
    expect(logger.emit).toHaveBeenCalledWith(
      'cell_execution_started',
      expect.objectContaining({ notebook_id: 'notebook-error' })
    );
    expect(logger.emit).toHaveBeenCalledWith(
      'cell_execution_success',
      expect.any(Object),
      expect.objectContaining({ cell_source: '1 / 0\n' })
    );
    expect(logger.emit).toHaveBeenCalledWith(
      'cell_execution_error',
      expect.any(Object),
      expect.objectContaining({
        error_type: 'ZeroDivisionError',
        error_message: 'division by zero'
      })
    );
  });

  it('updates Notebook, cell, path, and kernel status context', async () => {
    const fixture = monitorFixture();
    fixture.monitor.start();
    await flushRestored();
    fixture.logger.emit.mockClear();

    fixture.tracker.activate(fixture.secondPanel.panel);
    fixture.secondPanel.rename('renamed.ipynb');
    fixture.secondPanel.statusChanged.emit('busy');

    expect(fixture.monitor.getCurrentContext()).toEqual(
      expect.objectContaining({
        notebook_path: 'renamed.ipynb',
        notebook_id: 'notebook-2',
        cell_id: 'cell-2'
      })
    );
    expect(fixture.logger.emit).toHaveBeenCalledWith(
      'notebook_changed',
      expect.objectContaining({ notebook_path: 'lesson-two.ipynb' }),
      expect.objectContaining({
        previous_notebook_id: 'notebook-1',
        next_notebook_id: 'notebook-2'
      })
    );
    expect(fixture.logger.emit).not.toHaveBeenCalledWith(
      'cell_changed',
      expect.anything(),
      expect.anything()
    );
    expect(fixture.logger.emit).toHaveBeenCalledWith(
      'kernel_busy',
      expect.objectContaining({ notebook_path: 'renamed.ipynb' }),
      { kernel_status: 'busy' }
    );
  });

  it('does not bind the same Panel or Cell twice', async () => {
    const fixture = monitorFixture();
    fixture.monitor.start();
    await flushRestored();
    const initialCellSlots = fixture.firstCell.changed.slots.length;
    const initialStatusSlots = fixture.firstPanel.statusChanged.slots.length;

    fixture.tracker.widgetAdded.emit(fixture.firstPanel.panel);

    expect(fixture.firstCell.changed.slots).toHaveLength(initialCellSlots);
    expect(fixture.firstPanel.statusChanged.slots).toHaveLength(
      initialStatusSlots
    );
  });

  it('emits a completed input event for the matching code cell', async () => {
    const fixture = monitorFixture();
    fixture.monitor.start();
    await flushRestored();
    fixture.logger.emit.mockClear();

    fixture.monitor.emitCodeInputCompleted({
      inputStartedAt: Date.parse('2026-07-29T03:00:00Z'),
      inputEndedAt: Date.parse('2026-07-29T03:00:02Z'),
      durationMs: 2_000,
      context: {
        notebook_path: 'lesson-one.ipynb',
        cell_id: 'cell-1'
      }
    });

    expect(fixture.logger.emit).toHaveBeenCalledWith(
      'code_input_completed',
      expect.objectContaining({
        notebook_path: 'lesson-one.ipynb',
        cell_id: 'cell-1'
      }),
      expect.objectContaining({
        input_started_at: '2026-07-29T03:00:00.000Z',
        input_ended_at: '2026-07-29T03:00:02.000Z',
        duration_ms: 2_000,
        cell_source: 'value = 1\n'
      })
    );
  });
});

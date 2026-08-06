import { JupyterFrontEnd } from '@jupyterlab/application';

import { BehaviorEventLogger } from '../events';
import {
  currentPythonPath,
  PythonFileMonitor,
  sourceFromWidget
} from '../pythonFileMonitor';

type Slot<T> = (sender: unknown, args: T) => void;

class TestSignal<T> {
  readonly slots: Array<Slot<T>> = [];

  connect(slot: Slot<T>): void {
    this.slots.push(slot);
  }

  emit(args: T): void {
    for (const slot of this.slots) {
      slot(this, args);
    }
  }
}

function fileFixture(path = 'lesson/student.py', initial = 'value = 1\n') {
  let source = initial;
  const contentChanged = new TestSignal<void>();
  const sharedChanged = new TestSignal<unknown>();
  const pathChanged = new TestSignal<unknown>();
  const model = {
    contentChanged,
    sharedModel: {
      changed: sharedChanged,
      getSource: () => source
    }
  };
  const context = {
    path,
    model,
    pathChanged
  };
  const widget = { context };
  return {
    widget,
    context,
    contentChanged,
    sharedChanged,
    pathChanged,
    update(next: string): void {
      source = next;
      contentChanged.emit(undefined);
    },
    rename(next: string): void {
      context.path = next;
      pathChanged.emit(undefined);
    }
  };
}

function appFixture(widget: unknown) {
  const currentChanged = new TestSignal<unknown>();
  const shell = {
    currentWidget: widget,
    currentChanged
  };
  return {
    app: { shell } as unknown as JupyterFrontEnd,
    shell,
    show(next: unknown): void {
      shell.currentWidget = next;
      currentChanged.emit(undefined);
    }
  };
}

function loggerFixture(): jest.Mocked<Pick<BehaviorEventLogger, 'emit'>> {
  return {
    emit: jest.fn()
  };
}

describe('PythonFileMonitor', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-07-29T03:00:00Z'));
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('reports the active Python file context and source', () => {
    const file = fileFixture();
    const { app } = appFixture(file.widget);
    const logger = loggerFixture();
    const monitor = new PythonFileMonitor(
      app,
      logger as unknown as BehaviorEventLogger
    );

    monitor.start();

    expect(currentPythonPath(app)).toBe('lesson/student.py');
    expect(sourceFromWidget(file.widget)).toBe('value = 1\n');
    expect(monitor.getCurrentContext()).toEqual({
      document_type: 'python_file',
      file_path: 'lesson/student.py',
      file_name: 'student.py'
    });
    expect(monitor.getCurrentSource()).toBe('value = 1\n');
  });

  it('turns a source insertion into a completed typing interval and idle event', () => {
    const file = fileFixture();
    const { app } = appFixture(file.widget);
    const logger = loggerFixture();
    const monitor = new PythonFileMonitor(
      app,
      logger as unknown as BehaviorEventLogger
    );
    monitor.start();

    file.update('value = 12\n');

    expect(logger.emit).toHaveBeenCalledWith(
      'typing_start',
      expect.objectContaining({ file_path: 'lesson/student.py' })
    );

    jest.advanceTimersByTime(5_000);

    expect(logger.emit).toHaveBeenCalledWith(
      'typing_end',
      expect.objectContaining({ file_path: 'lesson/student.py' }),
      expect.objectContaining({
        duration_ms: 5_000,
        inserted_char_count: 1
      })
    );
    expect(logger.emit).toHaveBeenCalledWith(
      'code_input_completed',
      expect.objectContaining({ file_name: 'student.py' }),
      expect.objectContaining({
        duration_ms: 5_000,
        cell_source: 'value = 12\n'
      })
    );
    expect(logger.emit).toHaveBeenCalledWith(
      'idle',
      expect.objectContaining({ file_path: 'lesson/student.py' })
    );
  });

  it('uses the renamed path when an active interval finishes', () => {
    const file = fileFixture();
    const { app } = appFixture(file.widget);
    const logger = loggerFixture();
    const monitor = new PythonFileMonitor(
      app,
      logger as unknown as BehaviorEventLogger
    );
    monitor.start();

    file.update('value = 12\n');
    file.rename('lesson/renamed.py');
    jest.advanceTimersByTime(5_000);

    expect(logger.emit).toHaveBeenCalledWith(
      'typing_end',
      expect.objectContaining({
        file_path: 'lesson/renamed.py',
        file_name: 'renamed.py'
      }),
      expect.any(Object)
    );
  });

  it('ignores non-Python widgets and binds a Python widget after a shell change', () => {
    const textFile = fileFixture('notes.txt', 'plain text');
    const pythonFile = fileFixture('student.py', 'print(1)\n');
    const { app, show } = appFixture(textFile.widget);
    const logger = loggerFixture();
    const monitor = new PythonFileMonitor(
      app,
      logger as unknown as BehaviorEventLogger
    );
    monitor.start();

    expect(monitor.getCurrentContext()).toBeNull();
    textFile.update('changed text');
    expect(logger.emit).not.toHaveBeenCalled();

    show(pythonFile.widget);
    pythonFile.update('print(12)\n');

    expect(logger.emit).toHaveBeenCalledWith(
      'typing_start',
      expect.objectContaining({ file_path: 'student.py' })
    );
  });

  it('flushes on close, cancels the old idle timer, and keeps monitoring', () => {
    const file = fileFixture();
    const { app } = appFixture(file.widget);
    const logger = loggerFixture();
    const monitor = new PythonFileMonitor(
      app,
      logger as unknown as BehaviorEventLogger
    );
    monitor.start();

    file.update('value = 12\n');
    monitor.close();

    expect(logger.emit).toHaveBeenCalledWith(
      'typing_end',
      expect.any(Object),
      expect.objectContaining({ duration_ms: 0 })
    );
    logger.emit.mockClear();
    jest.advanceTimersByTime(5_000);
    expect(logger.emit).not.toHaveBeenCalled();

    file.update('value = 123\n');
    expect(logger.emit).toHaveBeenCalledWith(
      'typing_start',
      expect.any(Object)
    );
  });

  it('ends typing before starting a deletion interval', () => {
    const file = fileFixture('student.py', 'value = 1\n');
    const { app } = appFixture(file.widget);
    const logger = loggerFixture();
    const monitor = new PythonFileMonitor(
      app,
      logger as unknown as BehaviorEventLogger
    );
    monitor.start();

    file.update('value = 12\n');
    file.update('value = \n');
    jest.advanceTimersByTime(5_000);

    const eventTypes = logger.emit.mock.calls.map(call => call[0]);
    expect(eventTypes).toEqual(
      expect.arrayContaining([
        'typing_start',
        'typing_end',
        'deleting_start',
        'deleting_end',
        'idle'
      ])
    );
    expect(logger.emit).toHaveBeenCalledWith(
      'deleting_end',
      expect.any(Object),
      expect.objectContaining({ deleted_char_count: 2 })
    );
  });
});

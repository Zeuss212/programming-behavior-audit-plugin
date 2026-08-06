import { BehaviorEventLogger } from '../events';

describe('BehaviorEventLogger privacy boundary', () => {
  it('never sends a full event or synthetic sensitive fields to any console method', () => {
    const spies = [
      jest.spyOn(console, 'debug').mockImplementation(() => undefined),
      jest.spyOn(console, 'info').mockImplementation(() => undefined),
      jest.spyOn(console, 'log').mockImplementation(() => undefined),
      jest.spyOn(console, 'warn').mockImplementation(() => undefined),
      jest.spyOn(console, 'error').mockImplementation(() => undefined)
    ];
    const enqueue = jest.fn();
    const logger = new BehaviorEventLogger({ enqueue });

    const event = logger.emit(
      'cell_execution_error',
      {
        document_type: 'notebook_cell',
        file_path: '/synthetic/private/source.py',
        notebook_path: '/synthetic/private/notebook.ipynb',
        cell_id: 'synthetic-cell'
      },
      {
        cell_source: 'SYNTHETIC_PRIVATE_SOURCE = 1',
        error_message: 'SYNTHETIC_PRIVATE_ERROR',
        deleted_content: 'SYNTHETIC_PRIVATE_DELETED'
      }
    );

    expect(enqueue).toHaveBeenCalledWith(event);
    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled();
      const serializedCalls = JSON.stringify(spy.mock.calls);
      expect(serializedCalls).not.toContain('SYNTHETIC_PRIVATE');
      expect(serializedCalls).not.toContain('/synthetic/private');
    }
  });
});

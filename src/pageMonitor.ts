import { EditStateMachine } from './editState';
import { BehaviorEventLogger } from './events';
import { NotebookBehaviorMonitor } from './notebookMonitor';

export class PageStateMonitor {
  constructor(
    private readonly logger: BehaviorEventLogger,
    private readonly editState: EditStateMachine,
    private readonly notebookMonitor: NotebookBehaviorMonitor
  ) {}

  start(): void {
    window.addEventListener('blur', () => {
      const context = this.notebookMonitor.getCurrentContext();
      this.editState.close('context_change');
      this.logger.emit('page_blur', context);
    });

    window.addEventListener('focus', () => {
      this.logger.emit('page_focus', this.notebookMonitor.getCurrentContext());
    });

    document.addEventListener('visibilitychange', () => {
      const context = this.notebookMonitor.getCurrentContext();
      if (document.visibilityState === 'hidden') {
        this.editState.close('context_change');
        this.logger.emit('page_hidden', context);
        return;
      }

      if (document.visibilityState === 'visible') {
        this.logger.emit('page_visible', context);
      }
    });
  }
}

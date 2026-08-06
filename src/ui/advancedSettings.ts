export type AuthoringButtonTone = 'primary' | 'secondary' | 'danger';

export function authoringButton(
  text: string,
  tone: AuthoringButtonTone,
  action: () => void
): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = [
    'jp-BehaviorAudit-button',
    `jp-BehaviorAudit-button-${tone}`
  ].join(' ');
  button.textContent = text;
  button.addEventListener('click', action);
  return button;
}

export function advancedSettings(
  label: string,
  ...children: Node[]
): HTMLDetailsElement {
  const details = document.createElement('details');
  details.className = 'jp-BehaviorAudit-advancedSettings';
  const summary = document.createElement('summary');
  summary.textContent = label;
  const body = document.createElement('div');
  body.className = 'jp-BehaviorAudit-advancedSettingsBody';
  body.append(...children);
  details.append(summary, body);
  return details;
}

export function focusStepHeading(heading: HTMLHeadingElement): void {
  heading.tabIndex = -1;
  heading.focus();
}

export interface IAssistStatus {
  status: 'idle' | 'loading' | 'error' | 'success';
  message?: string;
}

export function assistStatus(status: IAssistStatus): HTMLDivElement {
  const node = document.createElement('div');
  node.className = 'jp-BehaviorAudit-assistStatus';
  node.setAttribute('role', 'status');
  node.setAttribute('aria-live', 'polite');
  node.setAttribute(
    'aria-busy',
    status.status === 'loading' ? 'true' : 'false'
  );
  node.textContent = status.message ?? '';
  if (status.status === 'error') {
    node.classList.add('jp-BehaviorAudit-assistStatus-error');
  }
  return node;
}

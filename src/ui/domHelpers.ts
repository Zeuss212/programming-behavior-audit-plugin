export interface ILabelledInput {
  container: HTMLDivElement;
  input: HTMLInputElement;
  error: HTMLDivElement;
}

export interface ILabelledTextarea {
  container: HTMLDivElement;
  textarea: HTMLTextAreaElement;
  error: HTMLDivElement;
}

export interface ILabelledSelect {
  container: HTMLDivElement;
  select: HTMLSelectElement;
  error: HTMLDivElement;
}

function fieldShell(
  id: string,
  label: string
): {
  container: HTMLDivElement;
  labelNode: HTMLLabelElement;
  error: HTMLDivElement;
} {
  const container = document.createElement('div');
  container.className = 'jp-BehaviorAudit-field';
  const labelNode = document.createElement('label');
  labelNode.htmlFor = id;
  labelNode.textContent = label;
  labelNode.className = 'jp-BehaviorAudit-label';
  const error = document.createElement('div');
  error.id = `${id}-error`;
  error.className = 'jp-BehaviorAudit-fieldError';
  error.setAttribute('aria-live', 'polite');
  return { container, labelNode, error };
}

export function labelledInput(
  id: string,
  label: string,
  options: { required?: boolean; maxLength?: number }
): ILabelledInput {
  const { container, labelNode, error } = fieldShell(id, label);

  const input = document.createElement('input');
  input.id = id;
  input.name = id;
  input.className = 'jp-BehaviorAudit-input';
  input.required = options.required ?? false;
  if (options.maxLength !== undefined) {
    input.maxLength = options.maxLength;
  }

  input.setAttribute('aria-describedby', error.id);

  container.append(labelNode, input, error);
  return { container, input, error };
}

export function labelledTextarea(
  id: string,
  label: string,
  options: { required?: boolean; maxLength?: number; rows?: number } = {}
): ILabelledTextarea {
  const { container, labelNode, error } = fieldShell(id, label);
  const textarea = document.createElement('textarea');
  textarea.id = id;
  textarea.name = id;
  textarea.className = 'jp-BehaviorAudit-input';
  textarea.required = options.required ?? false;
  textarea.rows = options.rows ?? 4;
  if (options.maxLength !== undefined) {
    textarea.maxLength = options.maxLength;
  }
  textarea.setAttribute('aria-describedby', error.id);
  container.append(labelNode, textarea, error);
  return { container, textarea, error };
}

export function labelledSelect(
  id: string,
  label: string,
  options: Array<{ value: string; label: string }>
): ILabelledSelect {
  const { container, labelNode, error } = fieldShell(id, label);
  const select = document.createElement('select');
  select.id = id;
  select.name = id;
  select.className = 'jp-BehaviorAudit-input';
  select.setAttribute('aria-describedby', error.id);
  for (const option of options) {
    const node = document.createElement('option');
    node.value = option.value;
    node.textContent = option.label;
    select.appendChild(node);
  }
  container.append(labelNode, select, error);
  return { container, select, error };
}

export function statusBadge(
  text: string,
  tone: 'neutral' | 'info' | 'warning' | 'success' | 'danger'
): HTMLSpanElement {
  const badge = document.createElement('span');
  badge.className = [
    'jp-BehaviorAudit-statusBadge',
    `jp-BehaviorAudit-statusBadge-${tone}`
  ].join(' ');
  badge.textContent = text;
  return badge;
}

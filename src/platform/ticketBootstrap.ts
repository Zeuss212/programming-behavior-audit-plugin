export interface FragmentLocation {
  hash: string;
  pathname: string;
  search: string;
}

export interface HistoryReplacement {
  state: unknown;
  replaceState(state: unknown, title: string, url?: string | null): void;
}

export type ClassroomTicketRegistrar = (
  ticket: string,
  pluginInstanceId: string
) => Promise<unknown>;

export function hasClassroomTicket(location: FragmentLocation): boolean {
  const fragment = location.hash.startsWith('#')
    ? location.hash.slice(1)
    : location.hash;
  return new URLSearchParams(fragment).has('behavior_ticket');
}

export function consumeClassroomTicket(
  location: FragmentLocation,
  history: HistoryReplacement
): string | null {
  const fragment = location.hash.startsWith('#')
    ? location.hash.slice(1)
    : location.hash;
  const parameters = new URLSearchParams(fragment);
  if (!parameters.has('behavior_ticket')) {
    return null;
  }

  const ticket = parameters.get('behavior_ticket')?.trim() ?? '';
  parameters.delete('behavior_ticket');
  const nextFragment = parameters.toString();
  const nextUrl = `${location.pathname}${location.search}${
    nextFragment ? `#${nextFragment}` : ''
  }`;
  history.replaceState(history.state, '', nextUrl);
  return ticket || null;
}

export async function bootstrapClassroomTicket(
  location: FragmentLocation,
  history: HistoryReplacement,
  register: ClassroomTicketRegistrar,
  pluginInstanceId: string
): Promise<boolean> {
  const ticket = consumeClassroomTicket(location, history);
  if (ticket === null) {
    return false;
  }
  await register(ticket, pluginInstanceId);
  return true;
}

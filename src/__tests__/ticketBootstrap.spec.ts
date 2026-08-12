import {
  bootstrapClassroomTicket,
  consumeClassroomTicket
} from '../platform/ticketBootstrap';

describe('consumeClassroomTicket', () => {
  it('reads the ticket only from the fragment and removes only that fragment parameter', () => {
    const replaceState = jest.fn();
    const location = {
      hash: '#view=lab&behavior_ticket=temporary-ticket&keep=1',
      pathname: '/notebook/classroom/lab',
      search: '?token=jupyter'
    };
    const history = { state: { existing: true }, replaceState };

    const ticket = consumeClassroomTicket(location, history);

    expect(ticket).toBe('temporary-ticket');
    expect(replaceState).toHaveBeenCalledWith(
      { existing: true },
      '',
      '/notebook/classroom/lab?token=jupyter#view=lab&keep=1'
    );
  });

  it('does not accept a ticket from the query string or leave it behind in history', () => {
    const replaceState = jest.fn();
    const location = {
      hash: '#view=lab',
      pathname: '/notebook/classroom/lab',
      search: '?behavior_ticket=unsafe-query-ticket'
    };
    const history = { state: null, replaceState };

    expect(consumeClassroomTicket(location, history)).toBeNull();
    expect(replaceState).not.toHaveBeenCalled();
  });

  it('clears an empty fragment ticket before reporting it as unusable', () => {
    const replaceState = jest.fn();
    const location = {
      hash: '#behavior_ticket=',
      pathname: '/lab',
      search: ''
    };
    const history = { state: null, replaceState };

    expect(consumeClassroomTicket(location, history)).toBeNull();
    expect(replaceState).toHaveBeenCalledWith(null, '', '/lab');
  });

  it('clears the fragment before handing the ticket to the local registration API', async () => {
    const replaceState = jest.fn();
    const register = jest.fn(async () => undefined);
    const location = {
      hash: '#behavior_ticket=temporary-ticket',
      pathname: '/lab',
      search: ''
    };
    const history = { state: null, replaceState };

    await expect(
      bootstrapClassroomTicket(location, history, register, 'plugin-instance-a')
    ).resolves.toBe(true);

    expect(register).toHaveBeenCalledWith(
      'temporary-ticket',
      'plugin-instance-a'
    );
    expect(replaceState.mock.invocationCallOrder[0]).toBeLessThan(
      register.mock.invocationCallOrder[0]
    );
  });
});

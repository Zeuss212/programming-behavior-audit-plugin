import {
  createUnavailableStudentPlatformContext,
  IPlatformContext
} from './contextApi';

export interface IClassroomUiBootstrapOptions {
  classroomTicketObserved: boolean;
  getContext: () => Promise<IPlatformContext>;
  initialize: (context: IPlatformContext) => void;
  reportUnavailable: () => void;
}

export async function initializeClassroomUi(
  options: IClassroomUiBootstrapOptions
): Promise<'context' | 'student-unavailable' | 'unavailable'> {
  try {
    options.initialize(await options.getContext());
    return 'context';
  } catch {
    if (options.classroomTicketObserved) {
      options.initialize(createUnavailableStudentPlatformContext());
      return 'student-unavailable';
    }
    options.reportUnavailable();
    return 'unavailable';
  }
}

import {
  createUnavailableStudentPlatformContext,
  IPlatformContext,
  isValidStudentPlatformContext
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
    const context = await options.getContext();
    if (
      options.classroomTicketObserved &&
      !isValidStudentPlatformContext(context)
    ) {
      options.initialize(createUnavailableStudentPlatformContext());
      return 'student-unavailable';
    }
    options.initialize(context);
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

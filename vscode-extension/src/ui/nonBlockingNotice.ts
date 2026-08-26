export type InformationMessagePresenter = (message: string) => PromiseLike<unknown>;

export function postNonBlockingInformationMessage(
  showInformationMessage: InformationMessagePresenter,
  message: string,
): void {
  void Promise.resolve(showInformationMessage(message));
}

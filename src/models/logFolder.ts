export interface ILogFolderOpenResponse {
  schema_version: 1;
  request_id: string;
  opened: true;
  platform: 'macos' | 'windows';
}

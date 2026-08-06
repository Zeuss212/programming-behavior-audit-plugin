import { ServerConnection } from '@jupyterlab/services';

import { IDimensionTemplate } from '../models/dimensionProfile';
import { requestAPI } from '../request';

interface ITemplateListResponse {
  templates: IDimensionTemplate[];
}

export async function listTemplates(
  settings: ServerConnection.ISettings
): Promise<IDimensionTemplate[]> {
  const response = await requestAPI<ITemplateListResponse>(
    'dimension-templates',
    settings
  );
  return response.templates;
}

import type { JupyterFrontEnd } from '@jupyterlab/application';
import type { ICommandPalette } from '@jupyterlab/apputils';

import type { IDimensionProfileVersion } from '../models/dimensionProfile';
import { GuidedProfileEditor } from './guidedProfileEditor';

export const MANAGE_DIMENSION_PROFILES_COMMAND =
  'myextension:manage-dimension-profiles';

export function registerGuidedProfileEditorCommand(
  app: JupyterFrontEnd,
  palette: ICommandPalette | null,
  onPublished?: (profile: IDimensionProfileVersion) => void
): void {
  let editor: GuidedProfileEditor | null = null;
  app.commands.addCommand(MANAGE_DIMENSION_PROFILES_COMMAND, {
    label: '创建题目考核方案',
    caption: '输入题目，确认知识点和测试，并发布试点方案。',
    execute: () => {
      if (editor === null || editor.isDisposed) {
        editor = new GuidedProfileEditor({
          serverSettings: app.serviceManager.serverSettings,
          onPublished: profile => onPublished?.(profile)
        });
        app.shell.add(editor, 'main');
      }
      app.shell.activateById(editor.id);
    }
  });

  palette?.addItem({
    command: MANAGE_DIMENSION_PROFILES_COMMAND,
    category: 'Behavior Audit'
  });
}

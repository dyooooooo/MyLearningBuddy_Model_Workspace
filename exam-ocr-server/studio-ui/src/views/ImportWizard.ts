/**
 * ImportWizard — now just a bridge that opens the inline ImportPipelinePanel.
 *
 * The old modal dialog has been replaced by a full-page dashboard in the center
 * content area (see ImportPipelinePanel.ts). This file only handles the
 * "studio:import-wizard" custom event and the ribbon button click.
 */
import { store } from "../state/store";

export function mountImportWizard(): void {
  document.addEventListener("studio:import-wizard", () => {
    store.enterImportMode();
  });
}

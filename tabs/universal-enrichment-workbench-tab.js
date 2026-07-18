import { renderUniversalEnrichmentWorkbench } from './universal-enrichment-workbench/workbench-controller.js';

const STYLE_ID = 'uew-stylesheet';

function ensureStylesheet(documentRef) {
  const existing = documentRef.getElementById(STYLE_ID);
  if (existing) return { element: existing, created: false };
  const link = documentRef.createElement('link');
  link.id = STYLE_ID;
  link.rel = 'stylesheet';
  link.href = new URL('./universal-enrichment-workbench/styles.css', import.meta.url).href;
  documentRef.head.appendChild(link);
  return { element: link, created: true };
}

export function renderUniversalEnrichmentWorkbenchTab(container, options = {}) {
  const documentRef = options.document || globalThis.document;
  const stylesheet = ensureStylesheet(documentRef);
  const cleanupWorkbench = renderUniversalEnrichmentWorkbench(container, options);
  return () => {
    cleanupWorkbench();
    if (stylesheet.created) stylesheet.element.remove();
  };
}

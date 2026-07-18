const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { createStaticEsmFixture } = require('./xml-cii-standalone-esm-fixture-helper');

const root = path.resolve(__dirname, '..');

function prepareEsmFixture() {
  const fixture = createStaticEsmFixture(root, {
    prefix: 'xml-cii-support-mapper-test-',
    entryFiles: [
      'tabs/xml-cii-2019-standalone/xml-cii-support-type-mapper.js',
      'tabs/xml-cii-2019-standalone/ui-adapted/xml-cii-adapted-support-type-mapper.js',
      'tabs/xml-cii-2019-standalone/ui-adapted/xml-cii-adapted-state.js',
    ],
  });
  for (const required of [
    'converters/xml-cii2019-core/support-mapping-config.js',
    'support/SupportKindResolver.js',
    'tabs/model-converters/converters/xmltocii2019_helper/support-mapping-table.js',
  ]) assert(fixture.files.includes(required), `ESM fixture dependency closure missing ${required}`);
  return fixture.tempRoot;
}

const { installMiniDom, MiniElement: TestElement } = require('./helpers/mini-dom.js');
installMiniDom();

function hasPreview(result, status, kind) {
  return result.previewRows.some((row) => row.status === status && (!kind || row.supportKind.includes(kind)));
}

(async () => {
  const tempRoot = prepareEsmFixture();
  const helper = await import(pathToFileURL(path.join(tempRoot, 'tabs/xml-cii-2019-standalone/xml-cii-support-type-mapper.js')).href);
  const ui = await import(pathToFileURL(path.join(tempRoot, 'tabs/xml-cii-2019-standalone/ui-adapted/xml-cii-adapted-support-type-mapper.js')).href);
  const stateApi = await import(pathToFileURL(path.join(tempRoot, 'tabs/xml-cii-2019-standalone/ui-adapted/xml-cii-adapted-state.js')).href);

  const xmlResult = helper.runStandaloneSupportTypeMapper({ sourceKind: 'xml', testInput: 'Pipe Rest XRT01\nGuide PG\nUnknown clamp', supportConfigJson: '{}' });
  assert.strictEqual(xmlResult.mapperRows.length, 4, 'four mapper categories expected');
  assert(hasPreview(xmlResult, 'matched', 'REST'), 'REST text should match');
  assert(hasPreview(xmlResult, 'matched', 'GUIDE'), 'GUIDE text should match');
  assert(hasPreview(xmlResult, 'unmatched'), 'unmatched text should produce diagnostics');
  assert(xmlResult.diagnostics.some((row) => row.type === 'unmatched-support-text'), 'unmatched diagnostics expected');
  assert(xmlResult.supportConfigJson.includes('supportTypeMapper'), 'mapper config must write back');

  const inputXmlResult = helper.runStandaloneSupportTypeMapper({
    sourceKind: 'inputxml',
    elementSideLoadText: 'ELEMENT 30-40\nDTXR_POS=Directional Anchor On Shoe\nDTXR_PS=Fixed Anchor',
    supportConfigJson: '{}',
  });
  assert(hasPreview(inputXmlResult, 'ambiguous', 'LINESTOP'), 'ambiguous InputXML DTXR text expected');
  assert(hasPreview(inputXmlResult, 'matched', 'ANCHOR'), 'InputXML fixed anchor text should match ANCHOR');
  assert(inputXmlResult.diagnostics.some((row) => row.type === 'ambiguous-support-kind'), 'ambiguous diagnostics expected');

  const rows = helper.createDefaultSupportTypeMapperConfig();
  rows.find((row) => row.kind === 'REST').aliases.push('Saddle');
  const edited = helper.runStandaloneSupportTypeMapper({ mapperRows: rows, testInput: 'Saddle support', supportConfigJson: '{}' });
  assert(hasPreview(edited, 'matched', 'REST'), 'operator alias edit should affect classification');
  assert(edited.supportConfigJson.includes('Saddle'), 'operator alias edit must serialize to supportConfigJson');

  let state = stateApi.createXmlCiiAdaptedWorkflowState();
  state = stateApi.updateSupportTypeMapperTestInput(state, 'Line Stop LS');
  state = stateApi.updateSupportTypeMapperConfig(state, 'support-mapper-aliases-LINESTOP', 'Line Stop\nLS\nHard Stop');
  const saved = stateApi.applyStandaloneSupportTypeMapperResult(state, helper.runStandaloneSupportTypeMapper({ ...state, mapperRows: state.supportTypeMapperConfig, testInput: state.supportTypeMapperTestInput }), true);
  assert(saved.supportConfigJson.includes('Hard Stop'), 'state save must write edited mapper rows');
  assert(saved.supportTypeMapperStatus.includes('saved'), 'state save status expected');

  const card = new TestElement('section');
  ui.renderStandaloneSupportTypeMapperPanel(card, saved);
  for (const label of ['REST mapper section', 'GUIDE mapper section', 'LINESTOP mapper section', 'ANCHOR mapper section', 'Test Input / Save to Config', 'CII Support Kind Preview', 'Mapper Diagnostics', 'Raw JSON advanced/debug']) {
    assert(card.textContent.includes(label), `Support Type Mapper panel missing ${label}`);
  }
  assert(card.textContent.includes('Test support mapper'), 'test action expected');
  assert(card.textContent.includes('Save to Config'), 'save action expected');
  assert(card.querySelector('[data-field="support-mapper-aliases-REST"]'), 'REST alias field must be wired for events.js data-field selectors');
  assert(card.querySelector('[data-field="support-mapper-test-input"]'), 'test input field must be wired for events.js data-field selectors');
  assert(card.querySelector('[data-action="build-support-mapper"]'), 'build action must be wired for events.js data-action selectors');
  assert(card.querySelector('[data-action="save-support-mapper"]'), 'save action must be wired for events.js data-action selectors');

  const coreSource = fs.readFileSync(path.join(root, 'tabs/xml-cii-2019-standalone/xml-cii-support-type-mapper.js'), 'utf8');
  for (const token of ['document', 'querySelector', 'createElement', 'window']) assert(!coreSource.includes(token), `core helper must remain DOM-free: ${token}`);
  const panelsSource = fs.readFileSync(path.join(root, 'tabs/xml-cii-2019-standalone/ui-adapted/xml-cii-adapted-phase-panels.js'), 'utf8');
  assert(panelsSource.includes('renderStandaloneSupportTypeMapperPanel'), 'phase panel must render real mapper UI');
  const eventsSource = fs.readFileSync(path.join(root, 'tabs/xml-cii-2019-standalone/ui-adapted/xml-cii-adapted-events.js'), 'utf8');
  assert(eventsSource.includes('runStandaloneSupportTypeMapper'), 'events must call mapper helper');
  assert(eventsSource.includes('save-support-mapper'), 'events must expose save action');
  const apiSource = fs.readFileSync(path.join(root, 'tabs/xml-cii-2019-standalone/xml-cii-workflow-api.js'), 'utf8');
  assert(apiSource.includes('export async function runXmlCii2019Workflow'), 'public workflow API boundary must remain exported');
  const workflowSource = fs.readFileSync(path.join(root, '.github/workflows/xml-cii-standalone-mission01.yml'), 'utf8');
  assert(workflowSource.includes('xml-cii-standalone-support-type-mapper.test.js'), 'mission workflow must run support mapper checks');

  console.log('XML CII standalone Support Type Mapper checks passed.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

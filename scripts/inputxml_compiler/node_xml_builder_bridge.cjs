#!/usr/bin/env node
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');

const repositoryRoot = path.resolve(__dirname, '..', '..');

class SimpleElement {
  constructor(name) {
    this.nodeName = name;
    this.localName = name;
    this.attributes = new Map();
    this.children = [];
    this.parentNode = null;
    this._textContent = '';
  }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }
  get textContent() {
    return this.children.length ? this.children.map((child) => child.textContent).join('') : this._textContent;
  }
  set textContent(value) { this.children = []; this._textContent = String(value ?? ''); }
}

class SimpleDocument {
  constructor(rootElement) {
    this.documentElement = rootElement;
    this.children = rootElement ? [rootElement] : [];
  }
  createElement(name) { return new SimpleElement(name); }
  querySelector(selector) { return selector === 'parsererror' ? null : null; }
  getElementsByTagName(name) {
    const wanted = String(name).toUpperCase();
    const out = [];
    const visit = (node) => {
      if (String(node.localName || node.nodeName).toUpperCase() === wanted) out.push(node);
      for (const child of node.children || []) visit(child);
    };
    if (this.documentElement) visit(this.documentElement);
    return out;
  }
}

function decodeEntities(value) {
  return String(value)
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&gt;/g, '>')
    .replace(/&lt;/g, '<')
    .replace(/&amp;/g, '&');
}

function parseAttributes(attrText, element) {
  const pattern = /([A-Za-z_][\w:.-]*)\s*=\s*"([^"]*)"/g;
  let match;
  while ((match = pattern.exec(attrText))) element.setAttribute(match[1], decodeEntities(match[2]));
}

function parseXml(xmlText) {
  const source = String(xmlText).replace(/^\s*<\?xml[^>]*>\s*/i, '');
  const tokenPattern = /<[^>]+>|[^<]+/g;
  const stack = [];
  let rootElement = null;
  let token;
  while ((token = tokenPattern.exec(source))) {
    const part = token[0];
    if (!part.trim()) continue;
    if (part.startsWith('<!--') || part.startsWith('<?')) continue;
    if (part.startsWith('</')) { stack.pop(); continue; }
    if (part.startsWith('<')) {
      const selfClosing = /\/>\s*$/.test(part);
      const body = part.slice(1, selfClosing ? -2 : -1).trim();
      const nameMatch = body.match(/^([^\s/>]+)/);
      if (!nameMatch) continue;
      const element = new SimpleElement(nameMatch[1]);
      parseAttributes(body.slice(nameMatch[1].length), element);
      if (!rootElement) rootElement = element;
      if (stack.length) stack[stack.length - 1].appendChild(element);
      if (!selfClosing) stack.push(element);
    } else if (stack.length) {
      stack[stack.length - 1]._textContent += decodeEntities(part);
    }
  }
  if (!rootElement) throw new Error('No root element parsed.');
  return new SimpleDocument(rootElement);
}

function escapeText(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(value) { return escapeText(value).replace(/"/g, '&quot;'); }
function serializeNode(node) {
  const attrs = Array.from(node.attributes.entries()).map(([name, value]) => ` ${name}="${escapeAttr(value)}"`).join('');
  if (!node.children.length && !node._textContent) return `<${node.nodeName}${attrs}/>`;
  const body = `${node._textContent ? escapeText(node._textContent) : ''}${node.children.map(serializeNode).join('')}`;
  return `<${node.nodeName}${attrs}>${body}</${node.nodeName}>`;
}

globalThis.DOMParser = class { parseFromString(text) { return parseXml(text); } };
globalThis.XMLSerializer = class {
  serializeToString(node) { return node instanceof SimpleDocument ? serializeNode(node.documentElement) : serializeNode(node); }
};

function prepareEsmFixture() {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'inputxml-canonical-xml-builder-'));
  fs.writeFileSync(path.join(tempRoot, 'package.json'), '{"type":"module"}', 'utf8');
  const files = [
    'tabs/model-converters/xml-cii-node-to-inputxml-core.js',
    'tabs/model-converters/xml-cii-node-to-inputxml-audit.js',
    'tabs/model-converters/xml-cii-node-to-inputxml-topology-normalizer.js',
    'converters/xml-cii2019-core/custom-input-diagnostics.js',
    'converters/xml-cii2019-core/restraint-type-mutation.js',
    'converters/xml-cii2019-core/restraint-type-codes.js',
    'converters/xml-cii2019-core/custom-input-model.js',
    'converters/xml-cii2019-core/custom-input-xml-builder.js',
    'tabs/xml-cii-2019-standalone/xml-cii-inputxml-enrichment.js',
    'tabs/xml-cii-2019-standalone/xml-cii-workflow-types.js',
  ];
  for (const relativePath of files) {
    const sourcePath = path.join(repositoryRoot, relativePath);
    const targetPath = path.join(tempRoot, relativePath);
    if (!fs.existsSync(sourcePath)) throw new Error(`Required topology dependency is missing: ${relativePath}`);
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fs.copyFileSync(sourcePath, targetPath);
  }
  return tempRoot;
}

(async () => {
  const source = fs.readFileSync(0, 'utf8');
  const context = JSON.parse(process.env.INPUTXML_COMPILER_BRIDGE_CONTEXT || '{}');
  const tempRoot = prepareEsmFixture();
  try {
    const moduleUrl = pathToFileURL(path.join(tempRoot, 'tabs/model-converters/xml-cii-node-to-inputxml-core.js')).href;
    const api = await import(moduleUrl);
    const result = api.buildInputXmlFromNodeXml(source, {
      applyEnrichment: false,
      buildProfile: 'inputxml-topo',
      jobName: context.jobName || 'XML_BUILDER_CANONICAL_COMPILER',
      enableDuplicateCoordinateCoalescing: false,
      enableShortFillers: false,
      enableRayFillers: false,
      enableSecondPassFillers: false,
      defaultTeeSifType: Number(context.defaultTeeSifType ?? 5),
    });
    process.stdout.write(JSON.stringify({
      ok: result?.ok === true,
      coreInputXmlText: result?.coreInputXmlText || '',
      elementSideLoadText: result?.elementSideLoadText || '',
      diagnostics: result?.diagnostics || {},
      warnings: result?.warnings || [],
      error: result?.error || null,
    }));
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
})().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});

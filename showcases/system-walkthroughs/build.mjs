#!/usr/bin/env node
// SPDX-License-Identifier: MIT
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from '../../skills/system-walkthrough/scripts/walkthrough.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const check = process.argv.includes('--check');
const temporary = check ? await mkdtemp(resolve(tmpdir(), 'walkthrough-showcase-')) : null;
try { for (const name of ['valkey', 'sqlite']) {
  const input = resolve(here, '../../skills/system-walkthrough/examples', `${name}.json`);
  const model = JSON.parse(await readFile(input, 'utf8'));
  const destination = resolve(temporary || here, name);
  const result = await build(model, destination);
  for (const warning of result.warnings) console.warn(`${name}: ${warning}`);
  if (check && !(await readFile(resolve(destination,'index.html'))).equals(await readFile(resolve(here,name,'index.html')))) throw new Error(`${name}/index.html is stale; regenerate with build.mjs`);
  console.log(`${name}/index.html: ${result.bytes} bytes${check ? ' (current)' : ''}`);
} } finally { if (temporary) await rm(temporary,{recursive:true,force:true}); }

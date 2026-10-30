import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {validate, build} from '../scripts/walkthrough.mjs';

const fixture = JSON.parse(await readFile(new URL('../examples/starter.json',import.meta.url)));
const copy = () => structuredClone(fixture);
test('valid source-grounded example and independent figure subsets', () => {
  assert.deepEqual(validate(fixture).errors, []);
  const m = copy(); delete m.map; delete m.walkthrough;
  assert.deepEqual(validate(m).errors, []);
  const n = copy(); delete n.sequence; delete n.walkthrough;
  assert.deepEqual(validate(n).errors, []);
});
test('rejects dangling map/event/evidence references and duplicate IDs before build', () => {
  const m=copy(); m.map.nodes[1].id=m.map.nodes[0].id;
  m.sequence.scenarios[0].events[1].to='missing';
  m.claims[0].sources=['nonexistent'];
  const errors=validate(m).errors.join('\n');
  assert.match(errors,/duplicate ID/); assert.match(errors,/unknown node/);
  assert.match(errors,/unknown lane/); assert.match(errors,/unknown ID nonexistent/);
});
test('requires explicit post-event state and immutable source or live-doc disclosure', () => {
  const m=copy(); m.walkthrough.steps[1].state=[];
  m.sources[0].url='https://example.org/unversioned';
  m.project.revision='main';
  const errors=validate(m).errors.join('\n');
  assert.match(errors,/state: need at least/); assert.match(errors,/unpinned documentation/);
  assert.match(errors,/full 40-character/);
  m.sources[0].note='Live documentation, checked against pinned source.';
  assert.doesNotMatch(validate(m).errors.join('\n'),/unpinned documentation/);
});
test('catches geometry and unsafe URLs with field-specific diagnostics', () => {
  const m=copy(); m.map.nodes[0].x=m.map.width;
  m.sources[0].url='javascript:alert(1)';
  m.navigation=[{label:'unsafe',url:'//example.org'}];
  const errors=validate(m).errors.join('\n');
  assert.match(errors,/node exceeds map bounds/); assert.match(errors,/public HTTPS/);
  assert.match(errors,/navigation\[0\].url/);
});
test('missing reverse exchange warns rather than fabricating a response', () => {
  const m=copy(); m.sequence.scenarios[0].events.pop();
  assert.equal(validate(m).errors.length,0);
  assert.equal(validate(m).warnings.length,1);
});
test('unopened or self-loop returns prompt an editorial correction', () => {
  const m=copy(); m.sequence.scenarios[0].events[1].kind='return';
  assert.equal(validate(m).errors.length,0);
  assert.match(validate(m).warnings.join('\n'),/return has no preceding opposite request/);
});
test('portable build embeds assets/model, escapes hostile data and keeps text without JS', async () => {
  const output=await mkdtemp(join(tmpdir(),'walkthrough-test-'));
  try {
    const m=copy(); m.title='</script><script>alert("x")</script>';
    m.sequence.scenarios[0].events[0].body='<img src=x onerror=alert(1)>';
    const result=await build(m,output), html=await readFile(join(output,'index.html'),'utf8');
    assert.ok(result.bytes<500000);
    assert.ok(html.includes('\\u003c/script>'));
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
    assert.ok(html.includes('<noscript>'));
    assert.ok(html.includes('Content-Security-Policy'));
    assert.doesNotMatch(html,/<script[^>]+src=|<link[^>]+stylesheet/);
    assert.ok(html.includes('SystemWalkthrough.mount'));
  } finally {await rm(output,{recursive:true,force:true});}
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {build} from '../scripts/walkthrough.mjs';
import {verify} from '../scripts/browser-check.mjs';

// Opt in on machines with Chrome: RUN_BROWSER_TESTS=1 node --test tests/*.test.mjs
test('browser verifier detects lost arrows, hidden exchanges and scrolled-away lane headers', {skip:process.env.RUN_BROWSER_TESTS!=='1',timeout:120000}, async()=>{
  const directory=await mkdtemp(join(tmpdir(),'walkthrough-browser-test-'));
  try {
    const model=JSON.parse(await readFile(new URL('../examples/starter.json',import.meta.url)));
    const events=model.sequence.scenarios[0].events;
    for(let i=0;i<10;i++)events.push({...structuredClone(events[1]),id:`later-lane-fixture-${i}`,from:'origin',to:'origin',label:'Local fixture'});
    await build(model,directory);
    const file=join(directory,'index.html');
    const html=await readFile(file,'utf8');
    // Preserve correct endpoint metadata, but remove arrowheads and disable the
    // horizontal reveal and sticky-header behavior. The checker must inspect what
    // a reader can see after selecting a late event, not just endpoint metadata.
    await writeFile(file,html.replace('</body>',`<style>[data-sequence-header],:has(>[data-sequence-header]){position:static!important}</style><script>document.querySelectorAll('.sw-map-edge polyline').forEach(path=>path.removeAttribute('marker-end'));Object.defineProperty(Element.prototype,'scrollLeft',{get(){return 0},set(){}});</script></body>`));
    const report=await verify(file,join(directory,'evidence'));
    assert.ok(report.summary.failed>0);
    for(const view of report.viewports)assert.ok(view.results.some(r=>!r.passed && r.name.includes('directional arrowhead')));
    assert.ok(report.viewports.find(v=>v.width===390).results.some(r=>!r.passed && r.name.includes('selected destination visible')));
    assert.ok(report.viewports.find(v=>v.width===390).results.some(r=>!r.passed && r.name.includes('lane header visible')));
  } finally {await rm(directory,{recursive:true,force:true});}
});

test('browser verifier rejects stacked reading content even when model selections and state still match', {skip:process.env.RUN_BROWSER_TESTS!=='1',timeout:120000}, async()=>{
  const directory=await mkdtemp(join(tmpdir(),'walkthrough-reader-test-'));
  try {
    const model=JSON.parse(await readFile(new URL('../examples/starter.json',import.meta.url)));
    // A tall canvas reproduces the published SQLite failure; the starter's small
    // 340px canvas can legitimately fit a stacked layout on a desktop viewport.
    model.map.height=900;
    await build(model,directory);
    const file=join(directory,'index.html'),html=await readFile(file,'utf8');
    const before=await verify(file,join(directory,'before'));
    assert.equal(before.summary.failed,0,'the same tall canvas must pass before restoring the stacked layout');
    // Recreate the original usability failure: an unbounded diagram comes first,
    // followed by navigation and the explanation. Preserve authored data,
    // selection handlers, highlights, endpoint metadata, and complete snapshots.
    await writeFile(file,html.replace('</body>',`<style>
      [data-reader-stage],.sw-stage-content{display:block!important;height:auto!important;max-height:none!important}
      .sw-reader-canvas,.sw-diagram-shell,.sw-viewport,[data-reader]{height:auto!important;max-height:none!important}
      [data-reader]{overflow:visible!important}
      [data-stage-navigation]{margin-top:24px!important}
    </style><script>
      document.querySelectorAll('[data-reader-stage]').forEach(stage=>{
        const navigation=stage.querySelector('[data-stage-navigation]'),reader=stage.querySelector('[data-reader]');
        stage.append(navigation,reader);
      });
    </script></body>`));
    const report=await verify(file,join(directory,'evidence'));
    assert.ok(report.summary.failed>0);
    for(const view of report.viewports){
      assert.ok(view.results.some(r=>!r.passed&&r.name.includes('heading, body and Previous/Next co-visible')),`${view.width}x${view.height} should detect displaced reader/navigation`);
      assert.ok(view.results.filter(r=>r.name.includes('full state snapshot')).every(r=>r.passed),'state metadata must remain correct in this visual regression');
      assert.ok(view.results.filter(r=>/^(Walk .+: selection|Scenario .+: resets first event|Sequence .+: active event)$/.test(r.name)).every(r=>r.passed),'selection metadata must remain correct in this visual regression');
    }
  } finally {await rm(directory,{recursive:true,force:true});}
});

#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// Node 22+ and a local Chrome/Chromium executable. No npm dependencies.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {access, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {basename, join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const delay = ms => new Promise(r => setTimeout(r, ms));
class CDP {
  constructor(socket) {
    this.socket = socket; this.serial = 0; this.pending = new Map(); this.events = [];
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const item = this.pending.get(message.id);
        if (!item) return;
        this.pending.delete(message.id); clearTimeout(item.timer);
        message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result);
      } else this.events.push(message);
    });
    socket.addEventListener('close', () => {
      for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error('Browser connection closed')); }
      this.pending.clear();
    });
  }
  send(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = ++this.serial;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
      this.pending.set(id, {resolve, reject, timer});
      this.socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }
}

async function chrome() {
  const candidates = [process.env.BROWSER_BIN, '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);
  for (const candidate of candidates) { try { await access(candidate); return candidate; } catch {} }
  throw new Error('Chrome/Chromium not found. Set BROWSER_BIN to its executable path.');
}

// Runs inside the generated page. Assertions compare rendered state to authored data,
// while geometry checks measure actual browser glyphs and path endpoints.
function exercise() {
  const model = JSON.parse(document.getElementById('walkthrough-model').textContent);
  const results = [], check = (name, passed, detail) => results.push({name, passed:!!passed, ...(passed ? {} : {detail})});
  const q = (s, root = document) => root.querySelector(s);
  const qa = (s, root = document) => [...root.querySelectorAll(s)];
  const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
  const bounds = r => ({x:r.x,y:r.y,width:r.width,height:r.height});
  const state = block => qa('.sw-inspector-host .sw-state > div', block).map(x => ({owner:q('dt',x).textContent, detail:q('dd',x).textContent}));
  const viewportOK = () => document.documentElement.scrollWidth <= innerWidth + 1;
  const marker = path => {
    const value = path.getAttribute('marker-end');
    const id = value?.match(/#([^)]*)/i)?.[1], target = id && document.getElementById(id);
    return target?.tagName.toLowerCase() === 'marker' && target.getAttribute('orient') === 'auto';
  };
  function fitMap(block, selection, label) {
    const activeNodes = new Set(selection.nodes);
    for (const edge of model.map.edges) if (selection.edges.includes(edge.id)) {activeNodes.add(edge.from); activeNodes.add(edge.to);}
    check(`${label}: highlighted edges`, equal(qa('.sw-map-edge[data-active="true"]',block).map(n=>n.dataset.edge).sort(), [...selection.edges].sort()));
    check(`${label}: highlighted nodes`, equal(qa('.sw-map-node[data-active="true"]',block).map(n=>n.dataset.node).sort(), [...activeNodes].sort()));
    check(`${label}: inspector`, q('.sw-inspector h3',block)?.textContent === (selection.title || selection.label));
    const evidenceURLs = new Set(selection.claims.flatMap(id=>model.claims.find(c=>c.id===id).sources).map(id=>model.sources.find(s=>s.id===id).url));
    const renderedURLs = new Set(qa('.sw-inspector-host .sw-source-links a',block).map(n=>n.getAttribute('href')));
    check(`${label}: evidence`, equal([...renderedURLs].sort(), [...evidenceURLs].sort()));
  }
  function mapGeometry(block, label) {
    for (const node of qa('.sw-map-node',block)) {
      const box = q('rect',node).getBBox();
      for (const text of qa('text',node)) {
        const b=text.getBBox(), style=getComputedStyle(text), scale=text.getScreenCTM().a;
        check(`${label}: ${node.dataset.node} text fits`, b.x>=box.x+3 && b.x+b.width<=box.x+box.width-3 && b.y>=box.y+2 && b.y+b.height<=box.y+box.height-2, {text:text.textContent,bounds:bounds(b),box:bounds(box)});
        check(`${label}: ${node.dataset.node} readable text`, parseFloat(style.fontSize)*scale>=13.9);
      }
    }
    for (const edge of model.map.edges) {
      const group = q(`[data-edge="${edge.id}"]`,block), path=q('polyline',group), points=path.points;
      const first=points.getItem(0),last=points.getItem(points.numberOfItems-1);
      const from=model.map.nodes.find(n=>n.id===edge.from),to=model.map.nodes.find(n=>n.id===edge.to);
      const boundary = (p,n) => p.x>=n.x-.2 && p.x<=n.x+n.w+.2 && p.y>=n.y-.2 && p.y<=n.y+n.h+.2 && Math.min(Math.abs(p.x-n.x),Math.abs(p.x-n.x-n.w),Math.abs(p.y-n.y),Math.abs(p.y-n.y-n.h))<.2;
      check(`${label}: ${edge.id} sender and receiver`, group.dataset.from===edge.from && group.dataset.to===edge.to && boundary(first,from) && boundary(last,to), {from:group.dataset.from,to:group.dataset.to});
      check(`${label}: ${edge.id} directional arrowhead`, marker(path));
    }
  }
  check('No document horizontal overflow',viewportOK(),{viewport:innerWidth,document:document.documentElement.scrollWidth});
  const ids=qa('[id]').map(n=>n.id); check('No duplicate IDs',ids.length===new Set(ids).size);
  check('Every button has an accessible name',qa('button').every(n=>(n.getAttribute('aria-label')||n.textContent).trim()));
  check('Every SVG has title and description',qa('svg').every(n=>q('title',n)?.textContent && q('desc',n)?.textContent));
  check('Text alternatives exist for all figures',qa('.sw-text-alternative').length >= Number(!!model.map)+Number(!!model.walkthrough)+Number(!!model.sequence));
  const catalogURLs = qa('.sw-source-catalog a').map(n=>n.getAttribute('href'));
  check('Primary source catalog matches model',equal(catalogURLs,model.sources.map(s=>s.url)));
  for (const a of qa('a[href^="#"]')) check(`Anchor ${a.getAttribute('href')}`,!!document.getElementById(decodeURIComponent(a.hash.slice(1))));
  if (model.map) {
    const block=q('[data-view="map"]');
    for (const [kind,items] of [['lens',model.map.lenses],['intent',model.map.intents]]) for (const item of items) {
      const control=q(`[data-${kind}="${item.id}"]`,block); control.click();
      check(`Map ${item.id}: selection`,block.dataset.selection===item.id && control.getAttribute('aria-pressed')==='true');
      fitMap(block,item,`Map ${item.id}`);
      check(`Map ${item.id}: overflow`,viewportOK());
    }
    mapGeometry(block,'Map');
  }
  if (model.walkthrough) {
    const block=q('[data-view="walkthrough"]'), steps=model.walkthrough.steps;
    // Reverse and forward traversal catches state carried accidentally from the prior selection.
    for (const step of [...steps].reverse().concat(steps)) {
      q(`[data-step="${step.id}"]`,block).click();
      check(`Walk ${step.id}: selection`,block.dataset.selection===step.id);
      fitMap(block,step,`Walk ${step.id}`);
      check(`Walk ${step.id}: full state snapshot`,equal(state(block),step.state),state(block));
    }
    const nav=q('.sw-step-navigation',block), navButtons=qa('button',nav);
    q('[data-step]',block).click(); check('Walk: previous disabled at start',navButtons[0].disabled);
    for(let i=1;i<steps.length;i++) navButtons[1].click();
    check('Walk: next reaches last and disables',block.dataset.selection===steps.at(-1).id && navButtons[1].disabled);
    for(let i=1;i<steps.length;i++) navButtons[0].click();
    check('Walk: previous reaches first',block.dataset.selection===steps[0].id);
    mapGeometry(block,'Walk');
  }
  if (model.sequence) {
    const block=q('[data-view="sequence"]');
    const testScenario = scenario => {
      q(`[data-scenario="${scenario.id}"]`,block).click();
      check(`Scenario ${scenario.id}: resets first event`,block.dataset.scenario===scenario.id && block.dataset.selection===scenario.events[0].id && equal(state(block),scenario.events[0].state));
      for (const event of [...scenario.events].reverse().concat(scenario.events)) {
        const control=q(`[data-event-select="${event.id}"]`,block); control.click();
        const group=q(`g[data-event="${event.id}"]`,block),path=q('.sw-message',group);
        check(`Sequence ${scenario.id}/${event.id}: active event`,block.dataset.selection===event.id && group.dataset.active==='true' && qa('g[data-event][data-active="true"]',block).length===1);
        check(`Sequence ${scenario.id}/${event.id}: full state snapshot`,equal(state(block),event.state),state(block));
        const from=model.sequence.lanes.findIndex(l=>l.id===event.from),to=model.sequence.lanes.findIndex(l=>l.id===event.to);
        const start=path.getPointAtLength(0),end=path.getPointAtLength(path.getTotalLength());
        check(`Sequence ${scenario.id}/${event.id}: sender → receiver`,group.dataset.from===event.from && group.dataset.to===event.to && (from===to ? Math.abs(start.x-end.x)<.1 && end.y>start.y : Math.sign(end.x-start.x)===Math.sign(to-from)));
        check(`Sequence ${scenario.id}/${event.id}: arrowhead`,marker(path));
        const panel=q('.sw-sequence-viewport',block),panelBox=panel.getBoundingClientRect(),pathBox=path.getBoundingClientRect();
        const left=panelBox.left+panel.clientLeft,right=left+panel.clientWidth;
        const destination=new DOMPoint(end.x,end.y).matrixTransform(path.getScreenCTM());
        check(`Sequence ${scenario.id}/${event.id}: selected destination visible`,destination.x>=left-1 && destination.x<=right+1,{destinationX:destination.x,panelLeft:left,panelRight:right});
        // A long exchange may need horizontal exploration, but a short one should
        // be wholly visible when its control is selected, including a local loop.
        if(pathBox.width+4<=panel.clientWidth)check(`Sequence ${scenario.id}/${event.id}: selected path visible when it fits`,pathBox.left>=left-1 && pathBox.right<=right+1,{pathLeft:pathBox.left,pathRight:pathBox.right,panelLeft:left,panelRight:right});
        const header=q('[data-sequence-header]',panel);
        const sender=new DOMPoint(start.x,start.y).matrixTransform(path.getScreenCTM());
        for(const id of new Set([event.from,event.to])) {
          const laneIndex=model.sequence.lanes.findIndex(l=>l.id===id),lane=header&&q(`[data-lane="${id}"]`,header);
          // The fallback also evaluates older exports with static SVG headers.
          const label=lane?(q('text',lane)||lane):qa('.sw-lane-label',panel)[laneIndex];
          const box=lane?(q('.sw-lane-box',lane)||q('rect',lane)||lane):qa('.sw-lane-box',panel)[laneIndex];
          const labelBox=label?.getBoundingClientRect(),laneBox=box?.getBoundingClientRect();
          // Compare vertical position to the internal panel, independent of where
          // that panel sits in the outer document. Offscreen lanes may stay offscreen.
          check(`Sequence ${scenario.id}/${event.id}: ${id} lane header visible`,!!labelBox && label.textContent.replace(/\s/g,'')===model.sequence.lanes[laneIndex].label.replace(/\s/g,'') && labelBox.height>0 && labelBox.top>=panelBox.top-1 && labelBox.bottom<=panelBox.top+panel.clientHeight+1,{headerBounds:labelBox&&bounds(labelBox),panelTop:panelBox.top,panelBottom:panelBox.top+panel.clientHeight});
          const endpoint=id===event.from?sender:destination,center=laneBox&&(laneBox.left+laneBox.right)/2;
          check(`Sequence ${scenario.id}/${event.id}: ${id} header aligns with exchange`,!!laneBox && Math.abs(center-endpoint.x)<=1.5,{headerCenterX:center,endpointX:endpoint.x});
        }
        check(`Sequence ${scenario.id}/${event.id}: overflow`,viewportOK());
      }
      for(const text of qa('svg text',block)) {
        const box=text.getBBox(),svg=text.ownerSVGElement,scale=text.getScreenCTM().a;
        check(`Sequence ${scenario.id}: label inside canvas`,box.x>=0 && box.x+box.width<=svg.viewBox.baseVal.width && box.y>=0 && box.y+box.height<=svg.viewBox.baseVal.height,{text:text.textContent,bounds:bounds(box)});
        if(!text.classList.contains('sw-row-number'))check(`Sequence ${scenario.id}: readable label`,parseFloat(getComputedStyle(text).fontSize)*scale>=13.9);
      }
      const nav=qa('.sw-step-navigation button',block);
      check(`Sequence ${scenario.id}: next disabled at end`,nav[1].disabled);
      for(let i=1;i<scenario.events.length;i++) nav[0].click();
      check(`Sequence ${scenario.id}: previous reaches first`,block.dataset.selection===scenario.events[0].id && nav[0].disabled);
    };
    for(const scenario of model.sequence.scenarios) testScenario(scenario);
    // Revisit after another scenario's last event; the old state must not leak.
    testScenario(model.sequence.scenarios[0]);
  }
  check('Document still fits after all interactions',viewportOK());
  return {title:model.title,revision:model.project.revision,results};
}

// Unlike the model checks above, these measurements use the physical browser
// viewport and every clipping ancestor. An element inside an offscreen panel is
// not readable merely because it fits that panel's own coordinate system.
function readerGeometry(view) {
  const block=document.querySelector(`[data-view="${view}"]`);
  const q=selector=>block?.querySelector(selector);
  const stage=q('[data-reader-stage]')||q('.sw-diagram-shell');
  const reader=q('[data-reader]')||q('.sw-inspector-host');
  const panel=q('.sw-viewport'),heading=q('.sw-inspector h3');
  const body=heading?.nextElementSibling;
  const rectangle=r=>({left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height});
  function visible(element,firstLine=false) {
    if(!element)return {visible:false,hit:false};
    let rect=element.getBoundingClientRect();
    if(firstLine) {
      const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT),text=walker.nextNode();
      if(text?.length){const range=document.createRange();range.setStart(text,0);range.setEnd(text,Math.min(text.length,40));rect=range.getClientRects()[0]||rect;}
    }
    let clip={left:0,top:0,right:innerWidth,bottom:innerHeight};
    for(let parent=element.parentElement;parent;parent=parent.parentElement) {
      const style=getComputedStyle(parent),box=parent.getBoundingClientRect();
      if(style.overflowX!=='visible'){clip.left=Math.max(clip.left,box.left+parent.clientLeft);clip.right=Math.min(clip.right,box.left+parent.clientLeft+parent.clientWidth);}
      if(style.overflowY!=='visible'){clip.top=Math.max(clip.top,box.top+parent.clientTop);clip.bottom=Math.min(clip.bottom,box.top+parent.clientTop+parent.clientHeight);}
    }
    const style=getComputedStyle(element),displayed=style.visibility==='visible'&&style.display!=='none'&&Number(style.opacity)>0;
    const full=displayed&&rect.width>0&&rect.height>0&&rect.left>=clip.left-1&&rect.right<=clip.right+1&&rect.top>=clip.top-1&&rect.bottom<=clip.bottom+1;
    const center={x:(rect.left+rect.right)/2,y:(rect.top+rect.bottom)/2},hit=document.elementFromPoint(center.x,center.y);
    return {visible:full,hit:!!hit&&(element===hit||element.contains(hit)),bounds:rectangle(rect),clip,center};
  }
  const previous=q('[data-action="previous"]')||q('.sw-step-navigation button:first-child');
  const next=q('[data-action="next"]')||q('.sw-step-navigation button:last-child');
  const diagram=visible(panel),title=visible(heading),start=visible(body,true),prev=visible(previous),forward=visible(next);
  const choosers=Object.fromEntries([...block.querySelectorAll('select[data-chooser]')].map(select=>[select.dataset.chooser,visible(select)]));
  const mapControl=choosers.map||visible(q('.sw-controls button[aria-pressed="true"]'));
  const active=[];
  let diagramTop=diagram.bounds?.top;
  if(view==='sequence') {
    const path=q('.sw-sequence-event[data-active="true"] .sw-message');
    if(path){const point=path.getPointAtLength(path.getTotalLength()),screen=new DOMPoint(point.x,point.y).matrixTransform(path.getScreenCTM());active.push({x:screen.x,y:screen.y});}
    diagramTop=Math.max(diagramTop,q('[data-sequence-header]')?.getBoundingClientRect().bottom||diagramTop);
  } else {
    const model=JSON.parse(document.getElementById('walkthrough-model').textContent);
    const item=(view==='map'?[...model.map.lenses,...(model.map.intents||[])]:model.walkthrough.steps).find(item=>item.id===block.dataset.selection);
    // A clipped overview must reveal a receiver from the selected exchange (or
    // an explicitly selected node), not merely an unrelated highlighted origin.
    const receivers=model.map.edges.filter(edge=>item.edges.includes(edge.id)).map(edge=>edge.to);
    const targets=receivers.length?receivers:item.nodes;
    for(const id of targets){const info=visible(q(`.sw-map-node[data-node="${id}"] .sw-node-label`));if(info.visible)active.push(info.center);}
  }
  const activeVisible=active.some(point=>diagram.visible&&point.x>=diagram.bounds.left+1&&point.x<=diagram.bounds.right-1&&point.y>=diagramTop+1&&point.y<=diagram.bounds.bottom-1);
  const diagramReadable=diagram.visible&&diagram.hit&&diagram.bounds.height>=(innerWidth<600?140:180)&&diagram.bounds.width>=220&&title.visible&&title.hit&&start.visible&&start.hit;
  return {stage:visible(stage),reader:visible(reader),diagram,title,start,previous:prev,next:forward,choosers,activeVisible,selection:block.dataset.selection,scenario:block.dataset.scenario,scrollY,readerScroll:reader?.scrollTop,
    readable:!!heading&&!!body&&parseFloat(getComputedStyle(heading).fontSize)>=20&&parseFloat(getComputedStyle(body).fontSize)>=15,
    navigationSize:!!previous&&!!next&&[previous,next].every(button=>button.getBoundingClientRect().height>=42&&parseFloat(getComputedStyle(button).fontSize)>=14),
    coVisible:diagramReadable&&(view==='map'?mapControl.visible&&mapControl.hit:prev.visible&&prev.hit&&forward.visible&&forward.hit)};
}

export async function verify(input, output) {
  input=resolve(input); output=resolve(output);
  await access(input); await mkdir(output,{recursive:true});
  const profile=await mkdtemp(join(tmpdir(),'system-walkthrough-browser-'));
  let processHandle,cdp,session;
  const report={input:basename(input),sha256:createHash('sha256').update(await readFile(input)).digest('hex'),checkedAt:new Date().toISOString(),browser:'Chrome/Chromium',viewports:[],environment:[],limits:['Checks local exported HTML in desktop Chromium emulation; not physical mobile/Safari or a factual source audit.','Source hrefs and rendered evidence are checked locally; remote target availability is separate.']};
  try {
    processHandle=spawn(await chrome(),['--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-background-networking','--disable-component-update','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
    const endpoint=await new Promise((resolve,reject)=>{
      let stderr=''; const timer=setTimeout(()=>reject(new Error('Chrome did not expose a debugging endpoint')),10000);
      processHandle.once('error',e=>{clearTimeout(timer);reject(e);});
      processHandle.stderr.on('data',chunk=>{stderr+=chunk;const found=stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(found){clearTimeout(timer);resolve(found[1]);}});
      processHandle.once('exit',code=>{clearTimeout(timer);reject(new Error(`Chrome exited before connection (${code})`));});
    });
    const socket=new WebSocket(endpoint);
    await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',()=>reject(new Error('Cannot connect to Chrome')),{once:true});});
    cdp=new CDP(socket);
    report.browser=(await cdp.send('Browser.getVersion')).product;
    const target=await cdp.send('Target.createTarget',{url:'about:blank'});
    session=(await cdp.send('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
    const call=(method,params={})=>cdp.send(method,params,session);
    await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable'); await call('Log.enable');
    const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.text+': '+(r.exceptionDetails.exception?.description||''));return r.result.value;};
    const navigate=async()=>{
      await call('Page.navigate',{url:pathToFileURL(input).href});
      for(let i=0;i<100;i++){if(await evaluate(`!!document.querySelector('#walkthrough-model') && !!document.querySelector('.sw-hero')`))return;await delay(50);}
      throw new Error('Generated walkthrough did not mount');
    };
    const key=async(value)=>{await call('Input.dispatchKeyEvent',{type:'keyDown',key:value,code:value});await call('Input.dispatchKeyEvent',{type:'keyUp',key:value,code:value});};
    const settle=()=>evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const pointer=async point=>{await call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});await settle();};
    const checkKey=async(selector,view,kind,field='selection')=>{
      if(!await evaluate(`!!document.querySelector(${JSON.stringify(selector)})`))return [];
      await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);
      const results=[];
      for(const [value,which] of [['End','last'],['Home','first']]){
        await key(value);
        const result=await evaluate(`(()=>{const b=document.querySelector('[data-view="${view}"]');const a=[...b.querySelectorAll('${selector}')];const want=${which==='last'?'a.at(-1)':'a[0]'};return {selected:b.dataset['${field}'],expected:want.dataset['${kind}'],focus:document.activeElement===want};})()`);
        results.push({name:`${view}: keyboard ${value} selects and focuses ${which}`,passed:result.selected===result.expected&&result.focus,...(result.selected===result.expected&&result.focus?{}:{detail:result})});
      }
      return results;
    };
    const checkChooser=async(view,kind,field='selection')=>{
      const selector=`[data-view="${view}"] select[data-chooser="${kind}"]`;
      if(!await evaluate(`!!document.querySelector(${JSON.stringify(selector)})`))return null;
      await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus({preventScroll:true})`);
      const results=[];
      for(const [value,which] of [['End','last'],['Home','first']]){
        await key(value);await settle();
        const detail=await evaluate(`(()=>{const select=document.querySelector(${JSON.stringify(selector)}),block=select.closest('[data-view]'),want=${which==='last'?'select.options[select.options.length-1]':'select.options[0]'};return {selected:block.dataset[${JSON.stringify(field)}],expected:want.value,focus:document.activeElement===select};})()`);
        const passed=detail.selected===detail.expected&&detail.focus;
        results.push({name:`${view}: native ${kind} chooser keyboard ${value} selects ${which}`,passed,...(passed?{}:{detail})});
      }
      return results;
    };
    const readerTasks=async viewport=>{
      const results=viewport.results,record=(name,passed,detail)=>results.push({name,passed:!!passed,...(passed?{}:{detail})});
      const geometry=view=>evaluate(`(${readerGeometry.toString()})(${JSON.stringify(view)})`);
      const anchor=async view=>{await evaluate(`(()=>{const block=document.querySelector('[data-view="${view}"]');(block.querySelector('[data-reader-stage]')||block.querySelector('.sw-diagram-shell')).scrollIntoView({block:'start',behavior:'instant'});})()`);await settle();};
      const choose=async(view,kind,index,fallback,expected,field='selection')=>{
        const selector=`[data-view="${view}"] select[data-chooser="${kind}"]`;
        const available=await evaluate(`!!document.querySelector(${JSON.stringify(selector)})`);
        const before=await geometry(view);
        if(available){
          // Native selection receives real browser key events. Assigning value or
          // calling a hidden button's click() would not prove reader usability.
          await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus({preventScroll:true})`);
          await key('Home');for(let i=0;i<index;i++)await key('ArrowDown');await settle();
          record(`${view}: ${kind} chooser is visible and reachable`,before.choosers[kind]?.visible&&before.choosers[kind]?.hit,before.choosers[kind]);
        } else await evaluate(`document.querySelector(${JSON.stringify(fallback)}).click()`);
        const after=await geometry(view);
        record(`${view}: direct ${kind} choice ${expected}`,after[field]===expected,{selected:after[field],expected});
        if(available)record(`${view}: direct ${kind} choice keeps page position`,Math.abs(after.scrollY-before.scrollY)<=1,{before:before.scrollY,after:after.scrollY});
      };
      const visible=async(view,label)=>{
        const detail=await geometry(view);
        record(`${label}: diagram, heading, body and Previous/Next co-visible`,detail.coVisible,detail);
        record(`${label}: narrative and navigation retain readable type and targets`,detail.readable&&detail.navigationSize,{readable:detail.readable,navigationSize:detail.navigationSize});
        record(`${label}: active diagram context revealed in physical viewport`,detail.activeVisible,{diagram:detail.diagram,selection:detail.selection});
        return detail;
      };
      const navigateReader=async(view,direction,expected,label)=>{
        const before=await geometry(view),button=before[direction];
        if(button.visible&&button.hit)await pointer(button.center);
        const after=await geometry(view);
        record(`${label}: pointer ${direction} changes selected state`,after.selection===expected,{selected:after.selection,expected,button});
        record(`${label}: ${direction} leaves outer page still`,Math.abs(after.scrollY-before.scrollY)<=1,{before:before.scrollY,after:after.scrollY});
        record(`${label}: ${direction} resets reader to heading`,after.readerScroll<=1&&after.title.visible&&after.start.visible,{readerScroll:after.readerScroll,title:after.title,start:after.start});
        return after;
      };
      const scrollReader=async(view,label)=>{
        const selector=`[data-view="${view}"] .sw-inspector-host`;
        const initial=await geometry(view);
        if(!initial.reader.visible||!initial.reader.hit){
          record(`${label}: state and evidence scroll independently of diagram/navigation`,false,{reason:'Reader is clipped or outside the physical viewport',reader:initial.reader});return;
        }
        for(const disclosure of ['[data-reader-state]','.sw-evidence']){
          const point=await evaluate(`(()=>{const reader=document.querySelector(${JSON.stringify(selector)}),details=reader.querySelector(${JSON.stringify(disclosure)});if(!details||details.open)return null;const summary=details.querySelector('summary');reader.scrollTop+=summary.getBoundingClientRect().top-reader.getBoundingClientRect().top-12;const box=summary.getBoundingClientRect();return {x:(box.left+box.right)/2,y:(box.top+box.bottom)/2};})()`);
          if(point)await pointer(point);
        }
        const before=await geometry(view);
        const point=await evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}),b=r.getBoundingClientRect();return {x:b.left+b.width/2,y:b.top+b.height/2};})()`);
        await call('Input.dispatchMouseEvent',{type:'mouseWheel',...point,deltaX:0,deltaY:1200});await settle();
        const after=await geometry(view);
        const content=await evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}),state=r.querySelector('[data-reader-state]'),evidence=r.querySelector('.sw-evidence'),links=[...r.querySelectorAll('.sw-evidence a')],box=r.getBoundingClientRect();return {scrollable:r.scrollHeight>r.clientHeight+1,stateOpen:!state||state.open,evidenceOpen:!evidence||evidence.open,evidenceVisible:!links.length||links.some(a=>{const b=a.getBoundingClientRect();return b.top>=box.top&&b.bottom<=box.bottom;})};})()`);
        record(`${label}: state and evidence scroll independently of diagram/navigation`,after.diagram.visible&&after.previous.visible&&after.previous.hit&&after.next.visible&&after.next.hit&&Math.abs(after.scrollY-before.scrollY)<=1&&(!content.scrollable||after.readerScroll>0)&&content.stateOpen&&content.evidenceOpen&&content.evidenceVisible,{beforePage:before.scrollY,afterPage:after.scrollY,readerScroll:after.readerScroll,content,diagram:after.diagram,previous:after.previous,next:after.next});
      };
      const model=await evaluate(`JSON.parse(document.getElementById('walkthrough-model').textContent)`);
      if(model.map){
        const view='map',items=[...model.map.lenses,...(model.map.intents||[])];await anchor(view);
        for(const index of [...new Set([0,Math.floor(items.length/2),items.length-1])]){
          const item=items[index],kind=model.map.lenses.some(lens=>lens.id===item.id)?'lens':'intent';
          await choose(view,'map',index,`[data-view="map"] [data-${kind}="${item.id}"]`,item.id);
          const detail=await geometry(view),label=`Map reader ${item.id}`;
          record(`${label}: diagram, heading, body and chooser co-visible`,detail.coVisible,detail);
          record(`${label}: active diagram context revealed in physical viewport`,detail.activeVisible,{diagram:detail.diagram,selection:detail.selection});
        }
      }
      if(model.walkthrough){
        const view='walkthrough',items=model.walkthrough.steps;await anchor(view);
        for(const index of [...new Set([0,Math.floor(items.length/2),items.length-1])]){
          await choose(view,'step',index,`[data-view="${view}"] [data-step="${items[index].id}"]`,items[index].id);
          const label=`Walk reader ${items[index].id}`;await visible(view,label);await scrollReader(view,label);
          if(index<items.length-1){await navigateReader(view,'next',items[index+1].id,label);await navigateReader(view,'previous',items[index].id,label);}
          else if(index>0){await navigateReader(view,'previous',items[index-1].id,label);await navigateReader(view,'next',items[index].id,label);}
        }
      }
      if(model.sequence){
        const view='sequence';await anchor(view);
        for(let scenarioIndex=0;scenarioIndex<model.sequence.scenarios.length;scenarioIndex++){
          const scenario=model.sequence.scenarios[scenarioIndex];
          await choose(view,'scenario',scenarioIndex,`[data-view="sequence"] [data-scenario="${scenario.id}"]`,scenario.id,'scenario');
          record(`Sequence reader ${scenario.id}: scenario starts at first event`,(await geometry(view)).selection===scenario.events[0].id);
          for(const index of [...new Set([0,Math.floor(scenario.events.length/2),scenario.events.length-1])]){
            const event=scenario.events[index],label=`Sequence reader ${scenario.id}/${event.id}`;
            await choose(view,'event',index,`[data-view="sequence"] [data-event-select="${event.id}"]`,event.id);
            await visible(view,label);await scrollReader(view,label);
            if(index<scenario.events.length-1){await navigateReader(view,'next',scenario.events[index+1].id,label);await navigateReader(view,'previous',event.id,label);}
            else if(index>0){await navigateReader(view,'previous',scenario.events[index-1].id,label);await navigateReader(view,'next',event.id,label);}
          }
        }
      }
      // Capture the actual bounded reading stages, including the walkthrough.
      for(const view of ['map','walkthrough','sequence'])if(await evaluate(`!!document.querySelector('[data-view="${view}"]')`)){
        await anchor(view);
        const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
        const name=`${view}-${viewport.width}x${viewport.height}.png`;await writeFile(join(output,name),Buffer.from(shot.data,'base64'));
        (viewport.screenshots??=[]).push(name);
      }
    };
    for(const {width,height} of [{width:1440,height:800},{width:900,height:700},{width:390,height:844}]){
      await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
      await call('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});
      const start=cdp.events.length; await navigate();
      const viewport=await evaluate(`(${exercise.toString()})()`);viewport.width=width;viewport.height=height;
      const mapKeyboard=await checkChooser('map','map');
      viewport.results.push(...(mapKeyboard||[...await checkKey('button[data-lens]','map','lens'),...await checkKey('button[data-intent]','map','intent')]));
      viewport.results.push(...(await checkChooser('walkthrough','step')||await checkKey('button[data-step]','walkthrough','step')));
      viewport.results.push(...(await checkChooser('sequence','event')||await checkKey('button[data-event-select]','sequence','eventSelect')));
      viewport.results.push(...(await checkChooser('sequence','scenario','scenario')||await checkKey('button[data-scenario]','sequence','scenario','scenario')));
      await readerTasks(viewport);
      const events=cdp.events.slice(start).filter(e=>e.sessionId===session);
      const errors=events.filter(e=>e.method==='Runtime.exceptionThrown'||(e.method==='Log.entryAdded'&&['error','warning'].includes(e.params.entry.level))||(e.method==='Runtime.consoleAPICalled'&&['error','warning'].includes(e.params.type)));
      const external=events.filter(e=>e.method==='Network.requestWillBeSent'&&!/^(file:|data:|about:)/.test(e.params.request.url));
      viewport.results.push({name:'No runtime/console/CSP errors',passed:errors.length===0,...(errors.length?{detail:errors.map(e=>e.params)}:{})});
      viewport.results.push({name:'Offline output requests no remote assets',passed:external.length===0,...(external.length?{detail:external.map(e=>e.params.request.url)}:{})});
      report.viewports.push(viewport);
    }
    await evaluate(`document.querySelector('.sw-theme').click()`);
    const theme=await evaluate(`document.documentElement.dataset.swTheme`);await navigate();
    report.environment.push({name:'Theme survives file reload',passed:await evaluate(`document.documentElement.dataset.swTheme`)===theme});
    await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    report.environment.push({name:'Reduced-motion path and no automatic animations',passed:await evaluate(`matchMedia('(prefers-reduced-motion:reduce)').matches && document.getAnimations().length===0`)});
    const denied=await call('Page.addScriptToEvaluateOnNewDocument',{source:`Object.defineProperty(window,'localStorage',{get(){throw new DOMException('denied','SecurityError')}})`});
    await navigate();await evaluate(`document.querySelector('.sw-theme').click()`);
    report.environment.push({name:'Denied storage still permits reading and theme switching',passed:await evaluate(`!!document.querySelector('.sw-hero') && document.documentElement.dataset.swTheme==='light'`)});
    await call('Page.removeScriptToEvaluateOnNewDocument',{identifier:denied.identifier});
  } finally {
    if(cdp){await cdp.send('Browser.close').catch(()=>{});cdp.socket.close();}
    const alive=()=>processHandle&&processHandle.exitCode===null&&processHandle.signalCode===null;
    if(alive()){processHandle.kill('SIGTERM');for(let i=0;i<20&&alive();i++)await delay(100);if(alive())processHandle.kill('SIGKILL');}
    await rm(profile,{recursive:true,force:true});
  }
  const checks=[...report.viewports.flatMap(v=>v.results),...report.environment];
  report.summary={checks:checks.length,passed:checks.filter(r=>r.passed).length,failed:checks.filter(r=>!r.passed).length};
  await writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
  return report;
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const args=process.argv.slice(2),outIndex=args.indexOf('--out');
  if(!args[0]||outIndex<0||!args[outIndex+1]){console.error('Usage: node browser-check.mjs path/to/index.html --out evidence-directory');process.exitCode=1;}
  else try {
    const report=await verify(args[0],args[outIndex+1]);
    console.log(JSON.stringify(report.summary));
    for(const view of report.viewports)for(const failure of view.results.filter(r=>!r.passed))console.error(`${view.width}px: ${failure.name}${failure.detail?' '+JSON.stringify(failure.detail):''}`);
    for(const failure of report.environment.filter(r=>!r.passed))console.error(failure.name);
    if(report.summary.failed)process.exitCode=1;
  }catch(e){console.error(e.stack||e.message);process.exitCode=1;}
}

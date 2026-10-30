/* System Walkthrough — dependency-free, source-neutral components. MIT licensed. */
(function (global) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  let serial = 0;
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value !== undefined && value !== null) node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child !== undefined && child !== null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  function svgEl(tag, attrs, ...children) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
    for (const child of children.flat(Infinity)) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  }
  const para = (text, className) => el('p', { class: className }, text);
  const button = (label, attrs) => el('button', { type: 'button', ...attrs }, label);
  const link = (label, url) => {
    let allowed = typeof url === 'string' && url.startsWith('#');
    try {
      const parsed = new URL(url);
      allowed ||= parsed.protocol === 'https:' && !parsed.username && !parsed.password;
    } catch (_) { /* A fragment does not need to be an absolute URL. */ }
    return allowed ? el('a', { href: url, rel: 'noreferrer' }, label) : el('span', {}, label);
  };
  const indexById = items => new Map((items || []).map(item => [item.id, item]));

  function words(text, max) {
    const lines = [''];
    for (const word of String(text).split(/\s+/)) {
      const last = lines.length - 1;
      if (lines[last] && lines[last].length + word.length + 1 > max) lines.push(word);
      else lines[last] += (lines[last] ? ' ' : '') + word;
    }
    return lines;
  }
  function svgText(text, x, y, attrs, max = 24, lineHeight = 18) {
    const lines = words(text, max);
    const node = svgEl('text', { x, y, ...attrs });
    lines.forEach((line, i) => node.append(svgEl('tspan', { x, dy: i ? lineHeight : 0 }, line)));
    return node;
  }
  function arrowDefs(svg, prefix) {
    const defs = svgEl('defs');
    for (const color of ['teal', 'blue', 'amber', 'violet', 'rose', 'slate']) {
      const marker = svgEl('marker', { id: `${prefix}-${color}`, viewBox: '0 0 12 12', refX: 10, refY: 6, markerWidth: 9, markerHeight: 9, orient: 'auto', markerUnits: 'userSpaceOnUse' },
        svgEl('path', { d: 'M1,1 L11,6 L1,11 Z', fill: `var(--sw-${color})` }));
      defs.append(marker);
    }
    svg.append(defs);
  }
  function makeSvg(prefix, title, description, width, height) {
    if (![width, height].every(value => Number.isFinite(value) && value > 0)) {
      throw new TypeError('SVG width and height must be finite positive numbers.');
    }
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-labelledby': `${prefix}-title ${prefix}-desc`, class: 'sw-diagram' });
    svg.style.setProperty('--sw-diagram-width', `${width}px`);
    svg.append(svgEl('title', { id: `${prefix}-title` }, title), svgEl('desc', { id: `${prefix}-desc` }, description));
    arrowDefs(svg, prefix);
    return svg;
  }
  function viewport(svg, label) {
    return el('div', { class: 'sw-diagram-shell' },
      para('Wide diagram · scroll inside this panel on a narrow screen. A full text version follows.', 'sw-scroll-hint'),
      el('div', { class: 'sw-viewport', tabindex: '0', role: 'region', 'aria-label': label }, svg));
  }
  function statePanel(items) {
    const list = el('dl', { class: 'sw-state' });
    for (const item of items || []) list.append(el('div', {}, el('dt', {}, item.owner), el('dd', {}, item.detail)));
    return el('div', { class: 'sw-state-panel' }, el('h4', {}, 'State after this step'), list);
  }
  function keyboardGroup(group, buttons, select, wrap = false) {
    group.addEventListener('keydown', event => {
      const current = buttons.indexOf(event.target.closest('button'));
      if (current < 0 || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let next = current;
      if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = buttons.length - 1;
      else next += ['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1;
      next = wrap ? (next + buttons.length) % buttons.length : Math.max(0, Math.min(buttons.length - 1, next));
      select(next);
      buttons[next].focus({ preventScroll: true });
    });
  }
  function section(id, number, title, description) {
    return el('section', { id, class: 'sw-section', 'aria-labelledby': `${id}-heading` },
      el('div', { class: 'sw-section-heading' }, para(number, 'sw-kicker'), el('h2', { id: `${id}-heading` }, title), para(description, 'sw-section-intro')));
  }
  function mount(root, model) {
    if (!root) throw new Error('SystemWalkthrough.mount needs a root element.');
    const uid = `sw-${++serial}`;
    const sources = indexById(model.sources), claims = indexById(model.claims), roles = indexById(model.roles);
    root.replaceChildren();
    root.classList.add('sw');
    root.dataset.version = '1';
    const sourceLinks = ids => el('span', { class: 'sw-source-links' }, (ids || []).map(id => {
      const source = sources.get(id);
      return source ? link(source.label, source.url) : el('span', {}, `Unknown source: ${id}`);
    }));
    function evidence(ids) {
      const list = el('ul', { class: 'sw-evidence-list' });
      for (const id of ids || []) {
        const claim = claims.get(id);
        if (claim) list.append(el('li', {}, el('span', { class: 'sw-claim-kind' }, claim.kind === 'inference' ? 'Inference' : 'Source-backed'), para(claim.text), sourceLinks(claim.sources)));
      }
      return el('details', { class: 'sw-evidence' }, el('summary', {}, `Evidence (${(ids || []).length})`), list);
    }
    function inspector(item, ordinal) {
      const box = el('div', { class: 'sw-inspector', 'aria-live': 'polite', 'aria-atomic': 'true' });
      if (ordinal) box.append(para(ordinal, 'sw-kicker'));
      box.append(el('h3', {}, item.title || item.label), para(item.body));
      if (item.why) box.append(el('h4', {}, 'Why it matters'), para(item.why));
      if (item.boundary) box.append(el('p', { class: 'sw-boundary' }, el('strong', {}, 'Boundary · '), item.boundary));
      if (item.state) box.append(statePanel(item.state));
      box.append(evidence(item.claims));
      return box;
    }
    function textAlternative(title, children) {
      return el('details', { class: 'sw-text-alternative' }, el('summary', {}, title), el('div', { class: 'sw-text-body' }, children));
    }
    const theme = button(document.documentElement.dataset.swTheme === 'light' ? 'Dark mode' : 'Light mode', { class: 'sw-theme', 'aria-label': 'Switch color theme' });
    theme.addEventListener('click', () => {
      const next = document.documentElement.dataset.swTheme === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.swTheme = next;
      try { localStorage.setItem('system-walkthrough-theme', next); } catch (_) { /* Reading remains available. */ }
      theme.textContent = next === 'light' ? 'Dark mode' : 'Light mode';
    });
    const navigation = el('nav', { class: 'sw-nav', 'aria-label': 'Walkthrough sections' });
    if (model.map) navigation.append(link('System map', `#${uid}-map`));
    if (model.walkthrough) navigation.append(link('Walkthrough', `#${uid}-walk`));
    if (model.sequence) navigation.append(link('Scenarios', `#${uid}-sequence`));
    navigation.append(link('Sources & limits', `#${uid}-sources`));
    root.append(el('header', { class: 'sw-hero' },
      el('div', { class: 'sw-topline' }, para('SYSTEM WALKTHROUGH · SOURCE-DERIVED', 'sw-kicker'), theme),
      el('h1', {}, model.title), para(model.subtitle, 'sw-subtitle'),
      para(model.question, 'sw-question'),
      el('div', { class: 'sw-snapshot' }, link(`${model.project.name} · ${model.project.version}`, `${model.project.repository}/tree/${model.project.revision}`), el('code', {}, model.project.revision.slice(0, 12))),
      el('p', { class: 'sw-scope' }, el('strong', {}, 'Scope · '), model.scope), navigation));
    if (model.takeaways?.length) {
      root.append(el('ol', { class: 'sw-takeaways' }, model.takeaways.map(item => el('li', {}, para(item.text), evidence(item.claims)))));
    }
    function legend() {
      return el('ul', { class: 'sw-legend', 'aria-label': 'Semantic roles' }, model.roles.map(role => el('li', { 'data-color': role.color }, el('span', { class: 'sw-dot', 'aria-hidden': 'true' }), role.label)));
    }
    function mapDiagram(prefix) {
      const map = model.map;
      const nodeIndex = indexById(map.nodes);
      const svg = makeSvg(prefix, map.title, `${map.description} Arrowheads show the destination. Inactive context remains visible.`, map.width, map.height);
      const edgeElements = new Map(), nodeElements = new Map();
      function clip(node, toward) {
        const cx = node.x + node.w / 2, cy = node.y + node.h / 2;
        const dx = toward.x - cx, dy = toward.y - cy;
        const scale = Math.min(dx ? node.w / 2 / Math.abs(dx) : Infinity, dy ? node.h / 2 / Math.abs(dy) : Infinity);
        return { x: cx + dx * scale, y: cy + dy * scale };
      }
      for (const edge of map.edges) {
        const from = nodeIndex.get(edge.from), to = nodeIndex.get(edge.to);
        const center = node => ({ x: node.x + node.w / 2, y: node.y + node.h / 2 });
        const via = edge.via || [];
        let points;
        if (from.id === to.id && !via.length) {
          points = [{ x: from.x + from.w, y: from.y + from.h * .35 }, { x: from.x + from.w + 32, y: from.y + from.h * .35 }, { x: from.x + from.w + 32, y: from.y + from.h * .8 }, { x: from.x + from.w, y: from.y + from.h * .8 }];
        } else points = [clip(from, via[0] || center(to)), ...via, clip(to, via.at(-1) || center(from))];
        const color = roles.get(from.role).color;
        const group = svgEl('g', { class: 'sw-map-edge', 'data-edge': edge.id, 'data-from': edge.from, 'data-to': edge.to, 'data-kind': edge.kind, 'data-color': color });
        group.append(svgEl('title', {}, `${from.label} → ${to.label}: ${edge.label}`), svgEl('polyline', { points: points.map(p => `${p.x},${p.y}`).join(' '), 'marker-end': `url(#${prefix}-${color})` }));
        const a = points[Math.floor((points.length - 1) / 2)], b = points[Math.ceil((points.length - 1) / 2)];
        const edgeLines = words(edge.label, 24).length;
        group.append(svgText(edge.label, (a.x + b.x) / 2, (a.y + b.y) / 2 - 10 - (edgeLines - 1) * 16, { class: 'sw-edge-label', 'text-anchor': 'middle' }, 24, 16));
        svg.append(group); edgeElements.set(edge.id, group);
      }
      for (const node of map.nodes) {
        const group = svgEl('g', { class: 'sw-map-node', 'data-node': node.id, 'data-color': roles.get(node.role).color });
        const labelLines = words(node.label, Math.max(12, Math.floor((node.w - 28) / 8)));
        group.append(svgEl('title', {}, `${node.label}. ${node.detail}`), svgEl('rect', { x: node.x, y: node.y, width: node.w, height: node.h, rx: 9 }),
          svgText(node.label, node.x + 16, node.y + 27, { class: 'sw-node-label' }, Math.max(12, Math.floor((node.w - 28) / 8)), 19),
          svgText(node.detail, node.x + 16, node.y + 28 + labelLines.length * 20, { class: 'sw-node-detail' }, Math.max(12, Math.floor((node.w - 28) / 7)), 17));
        svg.append(group); nodeElements.set(node.id, group);
      }
      function highlight(selection) {
        const nodes = new Set(selection.nodes || []), edges = new Set(selection.edges || []);
        for (const edge of map.edges) if (edges.has(edge.id)) { nodes.add(edge.from); nodes.add(edge.to); }
        for (const [id, node] of nodeElements) node.dataset.active = String(nodes.has(id));
        for (const [id, edge] of edgeElements) edge.dataset.active = String(edges.has(id));
      }
      return { view: viewport(svg, `${map.title}, horizontally scrollable`), highlight };
    }
    if (model.map) {
      const map = model.map;
      const block = section(`${uid}-map`, '01 · ONE MODEL, SEVERAL QUESTIONS', map.title, map.description);
      block.dataset.view = 'map';
      const controls = el('div', { class: 'sw-controls', role: 'group', 'aria-label': 'Map lenses' });
      const diagram = mapDiagram(`${uid}-map-svg`), host = el('div', { class: 'sw-inspector-host' });
      const allButtons = [];
      function select(item, active) {
        for (const control of allButtons) control.setAttribute('aria-pressed', String(control === active));
        diagram.highlight(item); host.replaceChildren(inspector(item));
        block.dataset.selection = item.id;
      }
      const lenses = map.lenses.map(item => {
        const control = button(item.label, { 'data-lens': item.id, 'aria-pressed': 'false' });
        control.addEventListener('click', () => select(item, control)); controls.append(control); allButtons.push(control); return control;
      });
      keyboardGroup(controls, lenses, i => select(map.lenses[i], lenses[i]), true);
      block.append(controls);
      if (map.intents?.length) {
        const intentControls = el('div', { class: 'sw-controls sw-intents', role: 'group', 'aria-label': 'Feature intents' }, el('span', { class: 'sw-control-label' }, 'Follow an action'));
        const intents = map.intents.map(item => {
          const control = button(item.label, { 'data-intent': item.id, 'aria-pressed': 'false' });
          control.addEventListener('click', () => select(item, control)); intentControls.append(control); allButtons.push(control); return control;
        });
        keyboardGroup(intentControls, intents, i => select(map.intents[i], intents[i]), true);
        block.append(intentControls);
      }
      const layout = el('div', { class: 'sw-map-layout' }, diagram.view, host);
      layout.style.setProperty('--sw-map-width', `${map.width + 2}px`);
      block.append(legend(), layout, textAlternative('Read the map as text', [
        el('h3', {}, 'Components'), el('dl', { class: 'sw-state' }, map.nodes.map(node => el('div', {}, el('dt', {}, node.label), el('dd', {}, node.detail)))),
        el('h3', {}, 'Directed relationships'), el('ul', {}, map.edges.map(edge => el('li', {}, `${map.nodes.find(n => n.id === edge.from).label} → ${map.nodes.find(n => n.id === edge.to).label}: ${edge.label} (${edge.kind})`))),
        ...[...map.lenses, ...(map.intents || [])].map(item => el('article', {}, el('h3', {}, item.title), para(item.body), evidence(item.claims)))
      ]));
      root.append(block); select(map.lenses[0], lenses[0]);
    }
    if (model.walkthrough) {
      const walk = model.walkthrough;
      const block = section(`${uid}-walk`, '02 · FOLLOW ONE CONCRETE PATH', walk.title, walk.intro);
      block.dataset.view = 'walkthrough';
      const diagram = mapDiagram(`${uid}-walk-svg`);
      const list = el('ol', { class: 'sw-step-list', role: 'list', 'aria-label': 'Walkthrough steps' });
      const host = el('div', { class: 'sw-inspector-host' });
      const previous = button('← Previous'), next = button('Next →');
      const position = el('span', { class: 'sw-position', 'aria-live': 'off' });
      let current = 0;
      const buttons = walk.steps.map((step, i) => {
        const control = button([String(i + 1).padStart(2, '0'), ' · ', step.title].join(''), { 'data-step': step.id });
        control.addEventListener('click', () => select(i)); list.append(el('li', {}, control)); return control;
      });
      function select(i) {
        current = Math.max(0, Math.min(walk.steps.length - 1, i));
        const step = walk.steps[current];
        buttons.forEach((control, index) => { control.setAttribute('aria-current', index === current ? 'step' : 'false'); control.dataset.active = String(index === current); });
        previous.disabled = current === 0; next.disabled = current === walk.steps.length - 1;
        position.textContent = `${current + 1} / ${walk.steps.length}`;
        diagram.highlight(step); host.replaceChildren(inspector(step, `STEP ${current + 1}`)); block.dataset.selection = step.id;
      }
      previous.addEventListener('click', () => select(current - 1)); next.addEventListener('click', () => select(current + 1));
      keyboardGroup(list, buttons, select);
      block.append(legend(), diagram.view, el('div', { class: 'sw-step-navigation', role: 'group', 'aria-label': 'Walkthrough navigation' }, previous, position, next),
        el('div', { class: 'sw-walk-layout' }, list, host),
        textAlternative('Read every walkthrough step', walk.steps.map((step, i) => el('article', {}, el('h3', {}, `${i + 1}. ${step.title}`), para(step.body), para(`Why: ${step.why}`), para(`Boundary: ${step.boundary}`), statePanel(step.state), evidence(step.claims)))));
      root.append(block); select(0);
    }
    if (model.sequence) {
      const sequence = model.sequence;
      const lanes = indexById(sequence.lanes);
      const block = section(`${uid}-sequence`, '03 · CHANGE THE CONDITIONS', sequence.title, sequence.intro);
      block.dataset.view = 'sequence';
      const scenarioControls = el('div', { class: 'sw-controls', role: 'group', 'aria-label': 'Sequence scenarios' });
      const summary = el('div', { class: 'sw-scenario-summary' });
      const diagramHost = el('div');
      const eventList = el('ol', { class: 'sw-step-list', 'aria-label': 'Sequence events' });
      const host = el('div', { class: 'sw-inspector-host' });
      const previous = button('← Previous'), next = button('Next →'), position = el('span', { class: 'sw-position' });
      let scenarioIndex = 0, eventIndex = 0, eventButtons = [], eventGroups = [], scrollPanel, laneHeader;
      const scenarioButtons = sequence.scenarios.map((scenario, i) => {
        const control = button(scenario.label, { 'data-scenario': scenario.id, 'aria-pressed': 'false' });
        control.addEventListener('click', () => selectScenario(i)); scenarioControls.append(control); return control;
      });
      const laneX = new Map(sequence.lanes.map((lane, i) => [lane.id, 125 + i * 195]));
      function drawScenario(scenario) {
        const width = Math.max(480, 180 + sequence.lanes.length * 195), height = 105 + scenario.events.length * 86;
        const prefix = `${uid}-sequence-${scenario.id}`;
        const svg = makeSvg(prefix, `${sequence.title}: ${scenario.label}`, `${scenario.summary} Time runs downward. Arrowheads point to receivers. Numbered rows correspond to the event controls below.`, width, height);
        laneHeader = makeSvg(`${prefix}-lanes`, 'Sequence lane owners', 'Lane labels stay aligned with the sequence while its content scrolls.', width, 64);
        laneHeader.dataset.sequenceHeader = '';
        sequence.lanes.forEach(lane => {
          const x = laneX.get(lane.id), color = roles.get(lane.role).color;
          svg.append(svgEl('line', { x1: x, y1: 64, x2: x, y2: height - 22, class: 'sw-lifeline' }));
          laneHeader.append(svgEl('g', { 'data-lane': lane.id, 'data-color': color },
            svgEl('rect', { x: x - 88, y: 9, width: 176, height: 49, rx: 8, class: 'sw-lane-box', 'data-color': color }),
            svgText(lane.label, x, 30, { class: 'sw-lane-label', 'text-anchor': 'middle' }, 20, 17)));
        });
        eventGroups = scenario.events.map((event, i) => {
          const from = laneX.get(event.from), to = laneX.get(event.to), y = 105 + i * 86;
          const color = roles.get(lanes.get(event.from).role).color;
          const group = svgEl('g', { class: 'sw-sequence-event', 'data-event': event.id, 'data-edge': event.id, 'data-from': event.from, 'data-to': event.to, 'data-kind': event.kind, 'data-color': color });
          group.append(svgEl('title', {}, `${i + 1}. ${lanes.get(event.from).label} → ${lanes.get(event.to).label}: ${event.label}`),
            svgEl('rect', { class: 'sw-sequence-row', x: 10, y: y - 34, width: width - 20, height: 75, rx: 7 }),
            svgEl('text', { x: 25, y: y + 5, class: 'sw-row-number' }, String(i + 1).padStart(2, '0')));
          const path = from === to ? `M${from},${y} h68 v26 h-68` : `M${from},${y} H${to}`;
          const lineLimit = from === to ? 20 : Math.max(18, Math.floor(Math.abs(to - from) / 8));
          const labelLines = words(event.label, lineLimit).length;
          group.append(svgEl('path', { d: path, class: 'sw-message', 'marker-end': `url(#${prefix}-${color})` }),
            svgText(event.label, from === to ? from + 85 : (from + to) / 2, y - 12 - (labelLines - 1) * 17, { class: 'sw-edge-label', 'text-anchor': from === to ? 'start' : 'middle' }, lineLimit, 17));
          svg.append(group); return group;
        });
        const view = viewport(svg, `${scenario.label} sequence, scrollable`);
        view.querySelector('.sw-viewport').classList.add('sw-sequence-viewport');
        scrollPanel = view.querySelector('.sw-viewport');
        scrollPanel.prepend(el('div', { class: 'sw-sequence-sticky' }, laneHeader));
        diagramHost.replaceChildren(view);
      }
      function selectEvent(i, scroll = false) {
        const scenario = sequence.scenarios[scenarioIndex];
        eventIndex = Math.max(0, Math.min(scenario.events.length - 1, i));
        const event = scenario.events[eventIndex];
        eventGroups.forEach((group, index) => { group.dataset.active = String(index === eventIndex); group.dataset.past = String(index < eventIndex); });
        eventButtons.forEach((control, index) => { control.setAttribute('aria-current', index === eventIndex ? 'step' : 'false'); control.dataset.active = String(index === eventIndex); });
        previous.disabled = eventIndex === 0; next.disabled = eventIndex === scenario.events.length - 1;
        position.textContent = `${eventIndex + 1} / ${scenario.events.length}`;
        host.replaceChildren(inspector({ ...event, title: event.label }, `EVENT ${eventIndex + 1} · ${lanes.get(event.from).label} → ${lanes.get(event.to).label}`));
        block.dataset.selection = event.id;
        if (scroll) {
          const row = eventGroups[eventIndex].getBoundingClientRect();
          const panel = scrollPanel.getBoundingClientRect();
          const headerHeight = laneHeader.getBoundingClientRect().height;
          if (row.top < panel.top + headerHeight + 12 || row.bottom > panel.bottom) scrollPanel.scrollTop += row.top - panel.top - headerHeight - 18;
          const path = eventGroups[eventIndex].querySelector('.sw-message');
          const message = path.getBoundingClientRect();
          const label = eventGroups[eventIndex].querySelector('.sw-edge-label').getBoundingClientRect();
          let left = Math.min(message.left, label.left), right = Math.max(message.right, label.right);
          const margin = 24, visibleRight = panel.left + scrollPanel.clientWidth;
          const owners = [...laneHeader.querySelectorAll('[data-lane]')]
            .filter(lane => lane.dataset.lane === event.from || lane.dataset.lane === event.to)
            .map(lane => lane.querySelector('text').getBoundingClientRect());
          const ownerLeft = Math.min(left, ...owners.map(owner => owner.left));
          const ownerRight = Math.max(right, ...owners.map(owner => owner.right));
          if (ownerRight - ownerLeft <= scrollPanel.clientWidth - margin * 2) {
            left = ownerLeft; right = ownerRight;
          }
          if (right - left <= scrollPanel.clientWidth - margin * 2) {
            if (left < panel.left + margin) scrollPanel.scrollLeft += left - panel.left - margin;
            else if (right > visibleRight - margin) scrollPanel.scrollLeft += right - visibleRight + margin;
          } else {
            const end = path.getPointAtLength(path.getTotalLength());
            const destination = new DOMPoint(end.x, end.y).matrixTransform(path.getScreenCTM());
            if (destination.x < panel.left + margin) scrollPanel.scrollLeft += destination.x - panel.left - margin;
            else if (destination.x > visibleRight - margin) scrollPanel.scrollLeft += destination.x - visibleRight + margin;
          }
        }
      }
      function selectScenario(i) {
        scenarioIndex = i;
        const scenario = sequence.scenarios[i];
        scenarioButtons.forEach((control, index) => control.setAttribute('aria-pressed', String(index === i)));
        summary.replaceChildren(para('Source-derived illustrative scenario · no measured timing', 'sw-kicker'), para(scenario.summary), el('ul', { class: 'sw-assumptions' }, scenario.assumptions.map(item => el('li', {}, item))));
        drawScenario(scenario); eventList.replaceChildren();
        eventButtons = scenario.events.map((event, index) => {
          const control = button(`${String(index + 1).padStart(2, '0')} · ${event.label}`, { 'data-event-select': event.id });
          control.addEventListener('click', () => selectEvent(index, true)); eventList.append(el('li', {}, control)); return control;
        });
        block.dataset.scenario = scenario.id; selectEvent(0);
      }
      previous.addEventListener('click', () => selectEvent(eventIndex - 1, true)); next.addEventListener('click', () => selectEvent(eventIndex + 1, true));
      eventList.addEventListener('keydown', event => {
        const current = eventButtons.indexOf(event.target.closest('button'));
        if (current < 0 || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? eventButtons.length - 1 : Math.max(0, Math.min(eventButtons.length - 1, current + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1)));
        selectEvent(nextIndex, true); eventButtons[nextIndex].focus({ preventScroll: true });
      });
      keyboardGroup(scenarioControls, scenarioButtons, selectScenario, true);
      block.append(scenarioControls, summary, legend(), diagramHost,
        el('div', { class: 'sw-step-navigation', role: 'group', 'aria-label': 'Sequence navigation' }, previous, position, next),
        el('div', { class: 'sw-walk-layout' }, eventList, host),
        textAlternative('Read all scenarios and directed exchanges', sequence.scenarios.map(scenario => el('article', {}, el('h3', {}, scenario.label), para(scenario.summary), el('ul', {}, scenario.assumptions.map(item => el('li', {}, item))), el('ol', {}, scenario.events.map(event => el('li', {}, el('h4', {}, `${lanes.get(event.from).label} → ${lanes.get(event.to).label}: ${event.label}`), para(event.body), statePanel(event.state), evidence(event.claims))))))));
      root.append(block); selectScenario(0);
    }
    const sourceSection = section(`${uid}-sources`, 'EVIDENCE & BOUNDARIES', 'Sources and limits', 'This is a source-derived explanation. Scenarios illustrate supported behavior; they are not recorded execution traces.');
    sourceSection.append(el('h3', {}, 'Pinned project'), para(`${model.project.name} ${model.project.version} · ${model.project.license}`), link(model.project.revision, `${model.project.repository}/tree/${model.project.revision}`),
      el('h3', {}, 'Primary evidence'), el('ol', { class: 'sw-source-catalog' }, model.sources.map(source => el('li', { id: `${uid}-source-${source.id}` }, link(source.label, source.url), source.note ? para(source.note) : null))),
      el('h3', {}, 'What this model leaves out'), el('ul', {}, model.limits.map(item => el('li', {}, item))),
      textAlternative('Inspect every claim and its evidence', model.claims.map(claim => el('article', {}, el('h4', {}, claim.text), para(claim.kind === 'inference' ? 'Inference from the sources below.' : 'Source-backed assertion.'), sourceLinks(claim.sources)))));
    root.append(sourceSection, el('footer', { class: 'sw-footer' }, para('Created with AI assistance and checked against the linked primary sources. Interpret the model within its stated scope and assumptions.'), link('System Walkthrough skill & reusable components', 'https://github.com/EvanOman/claude-skills-public/tree/main/skills/system-walkthrough')));
    let printClosed = [];
    function beforePrint() {
      printClosed = [...root.querySelectorAll('details:not([open])')];
      printClosed.forEach(item => { item.open = true; });
    }
    function afterPrint() { printClosed.forEach(item => { item.open = false; }); printClosed = []; }
    global.addEventListener('beforeprint', beforePrint);
    global.addEventListener('afterprint', afterPrint);
    return { destroy() {
      global.removeEventListener('beforeprint', beforePrint);
      global.removeEventListener('afterprint', afterPrint);
      root.replaceChildren(); root.classList.remove('sw');
    } };
  }
  global.SystemWalkthrough = Object.freeze({ mount });
})(window);

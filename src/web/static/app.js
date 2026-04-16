'use strict';

const MODEL_PALETTE = {
  'gpt-5.4': { title: '#315c72', body: '#101c24', badge: '#6bc7f0' },
  'gpt-5.4-mini': { title: '#44613d', body: '#162114', badge: '#97d67d' },
  'gpt-5.3-codex': { title: '#6b4b2a', body: '#24180f', badge: '#f0b36b' },
  'gpt-5.2': { title: '#5b4068', body: '#1d1323', badge: '#c592e3' },
};
const MODEL_PALETTE_DEFAULT = { title: '#465063', body: '#141923', badge: '#9bb0d1' };

function modelColors(modelName) {
  return MODEL_PALETTE[modelName] || MODEL_PALETTE_DEFAULT;
}

function shortModelName(modelName) {
  if (!modelName) return 'tool';
  return modelName.replace(/^gpt-/, '');
}

const S = {
  graph: null,
  canvas: null,
  graphData: null,
  linkEdgeMap: {},
  lnodeToDbId: {},
  _prevLinkIds: new Set(),
  addEdgeMode: false,
  isHydrating: false,
  nodeDrafts: {},
  selectedNodeId: null,
};

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}
const GET = path => api('GET', path);
const PUT = (path, body) => api('PUT', path, body);
const POST = (path, body) => api('POST', path, body);
const DEL = path => api('DELETE', path);

let toastTimer = null;
function toast(msg, kind = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.className = '';
  }, 2400);
}

function registerNodeType() {
  function WorkflowNodeView() {
    this.addInput('in', 'workflow');
    this.addOutput('out', 'workflow');
    this.size = [250, 90];
    this.properties = {
      node_id: '',
      name: '',
      model_name: '',
      enabled: true,
      start_node: false,
      hooks: {},
    };
    this.resizable = false;
  }

  WorkflowNodeView.title = 'Node';

  WorkflowNodeView.prototype._applyColors = function _applyColors() {
    const pal = this.properties.model_name
      ? modelColors(this.properties.model_name)
      : MODEL_PALETTE_DEFAULT;
    this.color = this.properties.enabled ? pal.title : '#2a2a2a';
    this.bgcolor = this.properties.enabled ? pal.body : '#1a1a1a';
  };

  WorkflowNodeView.prototype.onConfigure = function onConfigure() {
    this._applyColors();
  };

  WorkflowNodeView.prototype.onDrawForeground = function onDrawForeground(ctx) {
    if (this.flags && this.flags.collapsed) return;

    const titleHeight = LiteGraph.NODE_TITLE_HEIGHT || 30;
    const width = this.size[0];
    const badgeY = titleHeight + 6;
    let badgeX = 8;

    function drawBadge(text, color) {
      const w = 12 + (text.length * 7);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(badgeX, badgeY, w, 16, 3);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 10px monospace';
      ctx.fillText(text, badgeX + 6, badgeY + 11);
      badgeX += w + 6;
    }

    drawBadge(shortModelName(this.properties.model_name), modelColors(this.properties.model_name).badge);
    if (this.properties.start_node) drawBadge('START', '#BF616A');

    const hooks = this.properties.hooks || {};
    const lifecycle = [
      hooks.has_pre_hook ? 'PRE' : null,
      hooks.has_run ? 'RUN' : null,
      hooks.has_post_hook ? 'POST' : null,
    ].filter(Boolean).join(' · ');

    ctx.fillStyle = this.properties.enabled ? '#aab0cc' : '#666';
    ctx.font = '11px monospace';
    ctx.fillText(this.properties.node_id, 8, titleHeight + 40);

    ctx.fillStyle = '#d8dee9';
    ctx.font = '12px sans-serif';
    ctx.fillText(lifecycle || 'no lifecycle files', 8, titleHeight + 60);

    if (!this.properties.enabled) {
      ctx.fillStyle = 'rgba(0,0,0,0.45)';
      ctx.fillRect(0, titleHeight, width, this.size[1]);
      ctx.fillStyle = '#d95c5c';
      ctx.font = 'bold 9px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText('DISABLED', width - 6, titleHeight + this.size[1] - 6);
      ctx.textAlign = 'left';
    }
  };

  LiteGraph.registerNodeType('workflow/node', WorkflowNodeView);
}

function initLiteGraph() {
  registerNodeType();
  S.graph = new LGraph();
  S.canvas = new LGraphCanvas(document.getElementById('lg-canvas'), S.graph);
  S.canvas.background_color = '#0d1018';
  S.canvas.render_canvas_border = false;
  S.canvas.render_connections_shadows = false;
  S.canvas.show_info = false;
  S.canvas.allow_searchbox = false;
  S.canvas.allow_dragnodes = true;
  S.canvas.getMenuOptions = () => [];
  S.canvas.getCanvasMenuOptions = () => [];
  S.canvas.getNodeMenuOptions = () => [];
  S.canvas.getGroupMenuOptions = () => [];
  S.canvas.getLinkMenuOptions = () => [];
  S.canvas.onShowSearchBox = () => false;
  S.graph.onDblClick = () => false;

  const canvasEl = document.getElementById('lg-canvas');
  canvasEl.addEventListener('contextmenu', event => {
    const edge = S.canvas.over_link_center || S.canvas.over_link;
    if (!edge) event.preventDefault();
  });
  canvasEl.addEventListener('dblclick', event => {
    event.preventDefault();
    event.stopPropagation();
  });

  S.canvas.onNodeSelected = lnode => {
    if (lnode) renderNodeEditor(lnode);
  };
  S.canvas.onNodeDeselected = () => showHint();
  S.graph.onConnectionChange = onConnectionChange;

  fitCanvas();
  window.addEventListener('resize', fitCanvas);
}

function fitCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  const el = document.getElementById('lg-canvas');
  el.width = wrap.clientWidth;
  el.height = wrap.clientHeight;
  if (S.canvas) S.canvas.resize(el.width, el.height);
}

async function loadGraph() {
  S.isHydrating = true;
  try {
    const data = await GET('/api/workflow');
    S.graphData = data;
    S.graph.clear();
    S.linkEdgeMap = {};
    S.lnodeToDbId = {};
    S._prevLinkIds = new Set();

    const colWidth = 340;
    const rowHeight = 150;
    const margin = 60;
    const nodeMap = {};
    const sortedNodes = layoutSortedNodes(data.nodes);
    const layout = computeNodeLayout(data.nodes, data.edges);

    sortedNodes.forEach(node => {
      const lnode = LiteGraph.createNode('workflow/node');
      lnode.title = node.name || node.id;
      lnode.properties = {
        node_id: node.id,
        name: node.name,
        model_name: node.model_name || '',
        enabled: node.enabled,
        start_node: node.start_node,
        hooks: node.hooks || {},
      };
      lnode._applyColors();
      const pos = layout.get(node.id) || { depth: 0, row: 0 };
      lnode.pos = [margin + (pos.depth * colWidth), margin + (pos.row * rowHeight)];
      S.graph.add(lnode);
      nodeMap[node.id] = lnode;
      S.lnodeToDbId[lnode.id] = node.id;
    });

    data.edges.forEach(edge => {
      const from = nodeMap[edge.from_node_id];
      const to = nodeMap[edge.to_node_id];
      if (!from || !to) return;
      const link = from.connect(0, to, 0);
      if (!link) return;
      S.linkEdgeMap[link.id] = {
        dbId: edge.id,
        from_node_id: edge.from_node_id,
        to_node_id: edge.to_node_id,
      };
      S._prevLinkIds.add(link.id);
    });

    updateBadge();
    showHint();
  } finally {
    S.isHydrating = false;
  }
}

function layoutSortedNodes(nodes) {
  return [...nodes].sort((a, b) => {
    if (!!a.start_node !== !!b.start_node) {
      return a.start_node ? -1 : 1;
    }
    if ((a.model_name || '') !== (b.model_name || '')) {
      return (a.model_name || '').localeCompare(b.model_name || '');
    }
    return (a.id || '').localeCompare(b.id || '');
  });
}

function updateBadge() {
  const llmNodes = S.graphData.nodes.filter(n => n.model_name);
  const models = new Set(llmNodes.map(n => n.model_name));
  const toolCount = S.graphData.nodes.length - llmNodes.length;
  const parts = [
    `${S.graphData.nodes.length} nodes`,
    `${S.graphData.edges.length} edges`,
    `${models.size} model${models.size !== 1 ? 's' : ''}`,
  ];
  if (toolCount > 0) parts.push(`${toolCount} tool`);
  document.getElementById('graph-badge').textContent = parts.join(' · ');
}

function computeNodeLayout(nodes, edges) {
  const outgoing = new Map();
  const incoming = new Map();
  nodes.forEach(node => {
    outgoing.set(node.id, []);
    incoming.set(node.id, []);
  });
  edges.forEach(edge => {
    if (outgoing.has(edge.from_node_id)) outgoing.get(edge.from_node_id).push(edge.to_node_id);
    if (incoming.has(edge.to_node_id)) incoming.get(edge.to_node_id).push(edge.from_node_id);
  });

  const startNodes = nodes.filter(node => node.start_node);
  const queue = startNodes.map(node => ({ id: node.id, depth: 0 }));
  const depths = new Map();
  while (queue.length) {
    const current = queue.shift();
    if (!current) break;
    const prevDepth = depths.get(current.id);
    if (prevDepth !== undefined && prevDepth <= current.depth) continue;
    depths.set(current.id, current.depth);
    (outgoing.get(current.id) || []).forEach(nextId => {
      queue.push({ id: nextId, depth: current.depth + 1 });
    });
  }

  let fallbackDepth = Math.max(0, ...depths.values(), 0) + 1;
  nodes.forEach(node => {
    if (!depths.has(node.id)) {
      depths.set(node.id, node.start_node ? 0 : fallbackDepth);
      fallbackDepth += 1;
    }
  });

  const grouped = new Map();
  nodes.forEach(node => {
    const depth = depths.get(node.id) || 0;
    if (!grouped.has(depth)) grouped.set(depth, []);
    grouped.get(depth).push(node);
  });

  const layout = new Map();
  [...grouped.entries()]
    .sort((a, b) => a[0] - b[0])
    .forEach(([depth, groupedNodes]) => {
      groupedNodes
        .sort((a, b) => (a.id || '').localeCompare(b.id || ''))
        .forEach((node, row) => {
          layout.set(node.id, { depth, row });
        });
    });
  return layout;
}

function onConnectionChange() {
  if (S.isHydrating) return;
  const links = S.graph.links || {};
  const currentIds = new Set(Object.values(links).map(link => link.id));

  currentIds.forEach(id => {
    if (!S._prevLinkIds.has(id)) handleLinkAdded(links[id]);
  });
  S._prevLinkIds.forEach(id => {
    if (!currentIds.has(id)) handleLinkRemoved(id);
  });
  S._prevLinkIds = currentIds;
}

async function handleLinkAdded(link) {
  if (!link || S.linkEdgeMap[link.id]) return;
  const fromNode = S.graph.getNodeById(link.origin_id);
  const toNode = S.graph.getNodeById(link.target_id);
  if (!fromNode || !toNode) return;

  try {
    const result = await POST('/api/workflow/edges', {
      from_node_id: fromNode.properties.node_id,
      to_node_id: toNode.properties.node_id,
    });
    S.linkEdgeMap[link.id] = {
      dbId: result.id,
      from_node_id: fromNode.properties.node_id,
      to_node_id: toNode.properties.node_id,
    };
    S.graphData.edges.push({
      id: result.id,
      from_node_id: fromNode.properties.node_id,
      to_node_id: toNode.properties.node_id,
    });
    updateBadge();
    toast('Edge added');
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function handleLinkRemoved(linkId) {
  const edgeData = S.linkEdgeMap[linkId];
  if (!edgeData) return;
  try {
    await DEL(`/api/workflow/edges/${edgeData.dbId}`);
    delete S.linkEdgeMap[linkId];
    S.graphData.edges = S.graphData.edges.filter(edge => edge.id !== edgeData.dbId);
    updateBadge();
    toast('Edge removed');
  } catch (err) {
    toast(err.message, 'err');
  }
}

document.addEventListener('keydown', async event => {
  if (event.key !== 'Delete' && event.key !== 'Backspace') return;
  const link = S.canvas && (S.canvas.selected_link || S.canvas.over_link || S.canvas.over_link_center);
  if (!link) return;
  const edgeData = S.linkEdgeMap[link.id];
  if (!edgeData) return;
  event.preventDefault();
  try {
    await DEL(`/api/workflow/edges/${edgeData.dbId}`);
    S.graph.removeLink(link.id);
    await loadGraph();
    toast('Edge deleted');
  } catch (err) {
    toast(err.message, 'err');
  }
});

function showHint() {
  S.selectedNodeId = null;
  document.getElementById('editor-hint').classList.remove('hidden');
  document.getElementById('node-editor').classList.add('hidden');
}

function showNodeEditor() {
  document.getElementById('editor-hint').classList.add('hidden');
  document.getElementById('node-editor').classList.remove('hidden');
}

function renderNodeEditor(lnode) {
  showNodeEditor();
  const baseNode = S.graphData.nodes.find(item => item.id === lnode.properties.node_id);
  const node = mergeNodeDraft(baseNode);
  if (!node) return;
  S.selectedNodeId = node.id;

  const hooks = node.hooks || {};
  setValue('ne-node-id', node.id, true);
  setValue('ne-name', node.name || '');
  setValue('ne-description', node.description || '');
  const usesLlm = !!node.model_name;
  document.getElementById('ne-model-row').classList.toggle('hidden', !usesLlm);
  document.getElementById('ne-tool-row').classList.toggle('hidden', usesLlm);
  if (usesLlm) setValue('ne-model-name', node.model_name);
  document.getElementById('ne-enabled').checked = !!node.enabled;
  document.getElementById('ne-start-node').checked = !!node.start_node;
  document.getElementById('ne-use-prev-output').checked = !!node.use_prev_output;
  setValue('ne-executor-path', node.executor_path || '');
  setValue('ne-pre-hook-path', node.pre_hook_path || '');
  setValue('ne-post-hook-path', node.post_hook_path || '');
  setValue('ne-node-prompt-path', node.node_prompt_path || '');
  setValue('ne-timeout-seconds', String(node.timeout_seconds || 600));
  setValue('ne-hook-pre', hooks.effective_pre_hook_path || '(none)', true);
  setValue('ne-hook-run', hooks.effective_executor_path || '(none)', true);
  setValue('ne-hook-post', hooks.effective_post_hook_path || '(none)', true);
  bindDraftInputs();

  document.getElementById('ne-details').onclick = async () => {
    await renderNodeDetails(collectNodeForm(node.id), node.hooks || {});
  };

  document.getElementById('ne-delete').onclick = async () => {
    if (!confirm(`Delete node "${node.id}"? Its connections will also be removed.`)) return;
    try {
      await DEL(`/api/nodes/${node.id}`);
      delete S.nodeDrafts[node.id];
      updateSaveState();
      await loadGraph();
      toast('Node deleted');
    } catch (err) {
      toast(err.message, 'err');
    }
  };
}

function mergeNodeDraft(node) {
  if (!node) return null;
  const draft = S.nodeDrafts[node.id];
  if (!draft) return { ...node };
  return { ...node, ...draft };
}

async function renderNodeDetails(node, hooks = {}) {
  const detail = await POST('/api/node-details-preview', node);

  setText('dm-name', node.name || '(empty)');
  setText('dm-model-name', node.model_name || '— (tool node)');
  setPre('dm-description', node.description || '(empty)');
  setText('dm-timeout', `${node.timeout_seconds ?? 0}s`);
  setPre('dm-node-prompt-path', detail.node_prompt_path || '(none)');
  setCode('dm-allowed-tools', (detail.resolved_tools || []).length ? detail.resolved_tools.join('\n') : '(none)', 'text');
  setPre('dm-run-output-preview', detail.run_output_preview || '{RUN_OUTPUT}');
  setMarkdown('dm-node-prompt', detail.node_prompt || '(empty)');
  setPre('dm-preview-prompt', detail.preview_prompt || '(empty)');
  setCode('dm-pre-hook-code', detail.execution_code.pre_hook || '(none)');
  setCode('dm-run-code', detail.execution_code.run || '(none)');
  setCode('dm-post-hook-code', detail.execution_code.post_hook || '(none)');
  document.getElementById('dm-execution-details').open = false;

  document.getElementById('details-modal').classList.remove('hidden');
}

function collectNodeForm(nodeId) {
  const modelRow = document.getElementById('ne-model-row');
  const usesLlm = !modelRow.classList.contains('hidden');
  return {
    id: nodeId,
    name: document.getElementById('ne-name').value.trim(),
    description: document.getElementById('ne-description').value.trim(),
    model_name: usesLlm ? document.getElementById('ne-model-name').value : null,
    enabled: document.getElementById('ne-enabled').checked,
    start_node: document.getElementById('ne-start-node').checked,
    use_prev_output: document.getElementById('ne-use-prev-output').checked,
    executor_path: document.getElementById('ne-executor-path').value.trim(),
    pre_hook_path: blankToNull(document.getElementById('ne-pre-hook-path').value),
    post_hook_path: blankToNull(document.getElementById('ne-post-hook-path').value),
    node_prompt_path: blankToNull(document.getElementById('ne-node-prompt-path').value),
    timeout_seconds: parseInt(document.getElementById('ne-timeout-seconds').value, 10),
  };
}

function bindDraftInputs() {
  [
    'ne-name',
    'ne-description',
    'ne-model-name',
    'ne-enabled',
    'ne-start-node',
    'ne-use-prev-output',
    'ne-executor-path',
    'ne-pre-hook-path',
    'ne-post-hook-path',
    'ne-node-prompt-path',
    'ne-timeout-seconds',
  ].forEach(id => {
    const el = document.getElementById(id);
    if (!el || el.dataset.draftBound === 'true') return;
    const handler = () => {
      if (S.selectedNodeId) markNodeDraft(S.selectedNodeId);
    };
    el.addEventListener('input', handler);
    el.addEventListener('change', handler);
    el.dataset.draftBound = 'true';
  });
}

function markNodeDraft(nodeId) {
  try {
    S.nodeDrafts[nodeId] = collectNodeForm(nodeId);
    applyDraftToCanvas(nodeId, S.nodeDrafts[nodeId]);
    updateSaveState();
  } catch (_err) {
    S.nodeDrafts[nodeId] = collectNodeFormLoose(nodeId);
    applyDraftToCanvas(nodeId, S.nodeDrafts[nodeId]);
    updateSaveState();
  }
}

function collectNodeFormLoose(nodeId) {
  const modelRow = document.getElementById('ne-model-row');
  const usesLlm = !modelRow.classList.contains('hidden');
  return {
    id: nodeId,
    name: document.getElementById('ne-name').value.trim(),
    description: document.getElementById('ne-description').value.trim(),
    model_name: usesLlm ? document.getElementById('ne-model-name').value : null,
    enabled: document.getElementById('ne-enabled').checked,
    start_node: document.getElementById('ne-start-node').checked,
    use_prev_output: document.getElementById('ne-use-prev-output').checked,
    executor_path: document.getElementById('ne-executor-path').value.trim(),
    pre_hook_path: blankToNull(document.getElementById('ne-pre-hook-path').value),
    post_hook_path: blankToNull(document.getElementById('ne-post-hook-path').value),
    node_prompt_path: blankToNull(document.getElementById('ne-node-prompt-path').value),
    timeout_seconds: parseInt(document.getElementById('ne-timeout-seconds').value || '600', 10),
  };
}

function blankToNull(value) {
  const text = value.trim();
  return text || null;
}

function setValue(id, value, useText = false) {
  const el = document.getElementById(id);
  if (useText) {
    el.textContent = value;
  } else {
    el.value = value;
  }
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function setPre(id, value) {
  document.getElementById(id).textContent = value;
}

function setMarkdown(id, value) {
  document.getElementById(id).innerHTML = renderMarkdown(value);
}

function setCode(id, value, forcedLanguage = '') {
  const el = document.getElementById(id);
  const detected = forcedLanguage || detectCodeLanguage(value);
  el.innerHTML = renderHighlightedCode(value, detected);
}

function detectCodeLanguage(source) {
  const text = source || '';
  if (!text.trim() || text.trim() === '(none)') return 'text';
  if (/^\s*[{[]/.test(text) || /:\s*[{[]/.test(text)) return 'json';
  if (/\b(def|class|import|from|async|await|elif|except|lambda)\b/.test(text) || /__name__\s*==/.test(text)) {
    return 'python';
  }
  if (/\b(function|const|let|var|=>|async function|document\.|window\.)\b/.test(text)) {
    return 'javascript';
  }
  if (/\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|\bCREATE TABLE\b/i.test(text)) {
    return 'sql';
  }
  if (/^\s*#\s|^\s*-\s/m.test(text)) return 'markdown';
  return 'text';
}

function renderHighlightedCode(source, language) {
  const escaped = escapeHtml(source || '');
  if (language === 'text') return withLangBadge(escaped, language);
  if (language === 'python') return withLangBadge(highlightPython(escaped), language);
  if (language === 'javascript') return withLangBadge(highlightJavaScript(escaped), language);
  if (language === 'json') return withLangBadge(highlightJson(escaped), language);
  if (language === 'sql') return withLangBadge(highlightSql(escaped), language);
  return withLangBadge(escaped, language);
}

function withLangBadge(html, language) {
  return `<span class="tok-lang">${language}</span>\n${html}`;
}

function escapeHtml(text) {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function escapeAttr(text) {
  return escapeHtml(String(text || ''))
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function highlightPython(text) {
  let html = text;
  html = html.replace(/(#.*)$/gm, '<span class="tok-comment">$1</span>');
  html = html.replace(/("""[\s\S]*?"""|'''[\s\S]*?'''|"[^"\n]*"|'[^'\n]*')/g, '<span class="tok-string">$1</span>');
  html = html.replace(/\b(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)/g, '<span class="tok-keyword">$1</span> <span class="tok-function">$2</span>');
  html = html.replace(/\b(import|from|as|return|if|elif|else|for|while|try|except|finally|with|async|await|pass|break|continue|raise|yield|in|is|not|and|or|lambda|None|True|False)\b/g, '<span class="tok-keyword">$1</span>');
  html = html.replace(/\b(print|len|str|int|float|dict|list|set|tuple|open|range|enumerate|zip|json)\b/g, '<span class="tok-builtin">$1</span>');
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-number">$1</span>');
  return html;
}

function highlightJavaScript(text) {
  let html = text;
  html = html.replace(/(\/\/.*)$/gm, '<span class="tok-comment">$1</span>');
  html = html.replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)/g, '<span class="tok-string">$1</span>');
  html = html.replace(/\b(function)\s+([A-Za-z_][A-Za-z0-9_]*)/g, '<span class="tok-keyword">$1</span> <span class="tok-function">$2</span>');
  html = html.replace(/\b(const|let|var|return|if|else|for|while|switch|case|break|continue|try|catch|finally|async|await|import|from|export|new|throw|null|true|false)\b/g, '<span class="tok-keyword">$1</span>');
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-number">$1</span>');
  return html;
}

function highlightJson(text) {
  let html = text;
  html = html.replace(/("(?:[^"\\]|\\.)*")(\s*:)/g, '<span class="tok-function">$1</span>$2');
  html = html.replace(/:\s*("(?:[^"\\]|\\.)*")/g, ': <span class="tok-string">$1</span>');
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-number">$1</span>');
  html = html.replace(/\b(true|false|null)\b/g, '<span class="tok-keyword">$1</span>');
  return html;
}

function highlightSql(text) {
  let html = text;
  html = html.replace(/\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ORDER BY|GROUP BY|LIMIT|CREATE|TABLE|INDEX|VALUES|SET|INTO|ON|AND|OR|NOT|NULL)\b/gi, '<span class="tok-keyword">$1</span>');
  html = html.replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, '<span class="tok-string">$1</span>');
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-number">$1</span>');
  return html;
}

function renderMarkdown(source) {
  const text = source || '';
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const parts = [];
  let inList = false;
  let inQuote = false;
  let inCode = false;
  let codeLang = '';
  let codeBuffer = [];

  function closeList() {
    if (inList) {
      parts.push('</ul>');
      inList = false;
    }
  }

  function closeQuote() {
    if (inQuote) {
      parts.push('</blockquote>');
      inQuote = false;
    }
  }

  function closeCode() {
    if (!inCode) return;
    const codeText = codeBuffer.join('\n');
    parts.push(`<pre class="md-code code-viewer"><code>${renderHighlightedCode(codeText, codeLang || detectCodeLanguage(codeText))}</code></pre>`);
    inCode = false;
    codeLang = '';
    codeBuffer = [];
  }

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith('```')) {
      if (inCode) {
        closeCode();
      } else {
        closeList();
        closeQuote();
        inCode = true;
        codeLang = trimmed.slice(3).trim().toLowerCase();
      }
      continue;
    }

    if (inCode) {
      codeBuffer.push(line);
      continue;
    }

    if (!trimmed) {
      closeList();
      closeQuote();
      parts.push('<div class="md-spacer"></div>');
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      closeQuote();
      const level = Math.min(6, heading[1].length);
      parts.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    if (trimmed.startsWith('>')) {
      closeList();
      if (!inQuote) {
        parts.push('<blockquote>');
        inQuote = true;
      }
      parts.push(`<p>${renderInlineMarkdown(trimmed.replace(/^>\s?/, ''))}</p>`);
      continue;
    }

    const listItem = trimmed.match(/^[-*]\s+(.*)$/);
    if (listItem) {
      closeQuote();
      if (!inList) {
        parts.push('<ul>');
        inList = true;
      }
      parts.push(`<li>${renderInlineMarkdown(listItem[1])}</li>`);
      continue;
    }

    closeList();
    closeQuote();
    parts.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
  }

  closeCode();
  closeList();
  closeQuote();

  return parts.join('') || '<p>(empty)</p>';
}

function renderInlineMarkdown(text) {
  let html = escapeHtml(text || '');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  return html;
}

function formatFlags(node) {
  const flags = [];
  if (node.enabled) flags.push('enabled');
  if (node.start_node) flags.push('start_node');
  if (node.use_prev_output) flags.push('use_prev_output');
  return flags.length ? flags.join(' | ') : '(none)';
}

function findLiteNodeByDbId(nodeId) {
  return S.graph._nodes.find(node => node.properties.node_id === nodeId) || null;
}

function updateSaveState() {
  const dirtyCount = Object.keys(S.nodeDrafts).length;
  const btn = document.getElementById('btn-save-all');
  const badge = document.getElementById('save-state-badge');
  btn.textContent = dirtyCount > 0 ? `Save (${dirtyCount})` : 'Save';
  btn.classList.toggle('dirty', dirtyCount > 0);
  badge.classList.toggle('hidden', dirtyCount === 0);
}

function applyDraftToCanvas(nodeId, draft) {
  const lnode = findLiteNodeByDbId(nodeId);
  if (!lnode || !draft) return;

  lnode.title = draft.name || nodeId;
  lnode.properties = {
    ...lnode.properties,
    node_id: nodeId,
    name: draft.name || nodeId,
    model_name: draft.model_name || '',
    enabled: !!draft.enabled,
    start_node: !!draft.start_node,
    hooks: lnode.properties.hooks || {},
  };
  lnode._applyColors();
  S.canvas.setDirty(true, true);
}

function openModal() {
  document.getElementById('modal-node-id').value = '';
  document.getElementById('modal-name').value = '';
  document.getElementById('modal').classList.remove('hidden');
}

document.getElementById('modal-confirm').addEventListener('click', async () => {
  const nodeId = document.getElementById('modal-node-id').value.trim();
  if (!nodeId) {
    toast('Node ID is required', 'err');
    return;
  }

  try {
    await PUT(`/api/nodes/${nodeId}`, {
      name: document.getElementById('modal-name').value.trim() || nodeId,
      description: '',
      model_name: null,
      enabled: true,
      start_node: false,
      use_prev_output: true,
      executor_path: '',
      pre_hook_path: null,
      post_hook_path: null,
      node_prompt_path: null,
      timeout_seconds: 600,
    });
    document.getElementById('modal').classList.add('hidden');
    await loadGraph();
    toast('Node added');
  } catch (err) {
    toast(err.message, 'err');
  }
});

document.getElementById('modal-cancel').addEventListener('click', () => {
  document.getElementById('modal').classList.add('hidden');
});

document.getElementById('modal').addEventListener('click', event => {
  if (event.target === event.currentTarget) {
    document.getElementById('modal').classList.add('hidden');
  }
});

document.getElementById('btn-add-node').addEventListener('click', openModal);

document.getElementById('btn-save-all').addEventListener('click', async () => {
  const entries = Object.entries(S.nodeDrafts);
  if (!entries.length) {
    toast('No unsaved changes');
    return;
  }

  try {
    const selectedNodeIdBeforeSave = S.selectedNodeId;
    for (const [nodeId, draft] of entries) {
      const payload = nodeId === S.selectedNodeId ? collectNodeForm(nodeId) : normalizeDraftForSave(draft);
      await PUT(`/api/nodes/${nodeId}`, payload);
    }
    S.nodeDrafts = {};
    await loadGraph();
    if (selectedNodeIdBeforeSave) {
      const refreshed = findLiteNodeByDbId(selectedNodeIdBeforeSave);
      if (refreshed) renderNodeEditor(refreshed);
    }
    updateSaveState();
    toast('Saved');
  } catch (err) {
    toast(err.message, 'err');
  }
});

function normalizeDraftForSave(draft) {
  return { ...draft };
}

document.getElementById('btn-add-edge').addEventListener('click', () => {
  S.addEdgeMode = !S.addEdgeMode;
  const btn = document.getElementById('btn-add-edge');
  if (S.addEdgeMode) {
    S.canvas.connecting_node = null;
    LiteGraph.connect_ports_with_link_color = true;
    S.graph.config.allow_add_edge = true;
    S.canvas.startRendering();
    btn.classList.add('active');
    btn.textContent = '✕ Cancel';
    toast('Drag from a node output to another node input');
  } else {
    btn.classList.remove('active');
    btn.textContent = '↗ Edge';
  }
});

document.getElementById('btn-reload').addEventListener('click', async () => {
  S.nodeDrafts = {};
  updateSaveState();
  await loadGraph();
  toast('Reloaded');
});

document.getElementById('btn-engine-prompt').addEventListener('click', async () => {
  try {
    const detail = await GET('/api/engine-prompt');
    setPre('engine-prompt-path', detail.path || '(none)');
    setMarkdown('engine-prompt-content', detail.content || '(empty)');
    document.getElementById('engine-prompt-modal').classList.remove('hidden');
  } catch (err) {
    toast(err.message, 'err');
  }
});

document.getElementById('details-close').addEventListener('click', () => {
  document.getElementById('details-modal').classList.add('hidden');
});

document.getElementById('details-close-top').addEventListener('click', () => {
  document.getElementById('details-modal').classList.add('hidden');
});

document.getElementById('details-modal').addEventListener('click', event => {
  if (event.target === event.currentTarget) {
    document.getElementById('details-modal').classList.add('hidden');
  }
});

document.getElementById('engine-prompt-close').addEventListener('click', () => {
  document.getElementById('engine-prompt-modal').classList.add('hidden');
});

document.getElementById('engine-prompt-close-top').addEventListener('click', () => {
  document.getElementById('engine-prompt-modal').classList.add('hidden');
});

document.getElementById('engine-prompt-modal').addEventListener('click', event => {
  if (event.target === event.currentTarget) {
    document.getElementById('engine-prompt-modal').classList.add('hidden');
  }
});

// ── Nav ──────────────────────────────────────────────────────────────────────

const PAGES = ['dag', 'cron', 'trace', 'debug'];

function switchPage(pageId) {
  PAGES.forEach(id => {
    document.getElementById(`page-${id}`).classList.toggle('hidden', id !== pageId);
    document.querySelector(`.nav-item[data-page="${id}"]`).classList.toggle('active', id === pageId);
  });

  if (pageId === 'cron') loadCronJobs();
  if (pageId === 'trace') loadTraceRuns();
  if (pageId === 'dag') fitCanvas();
}

document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => switchPage(btn.dataset.page));
});

document.getElementById('nav-toggle').addEventListener('click', () => {
  const nav = document.getElementById('side-nav');
  const collapsed = nav.classList.toggle('collapsed');
  document.getElementById('nav-toggle').textContent = collapsed ? '›' : '‹';
  fitCanvas();
});

// ── Cron Jobs ─────────────────────────────────────────────────────────────────

let cronEditingId = null;
let cronRunBusy = false;

async function loadCronJobs() {
  try {
    const jobs = await GET('/api/schedule/jobs');
    renderCronTable(jobs);
  } catch (err) {
    toast(`Failed to load jobs: ${err.message}`, 'err');
  }
}

function renderCronTable(jobs) {
  const tbody = document.getElementById('cron-table-body');
  const empty = document.getElementById('cron-empty');

  if (!jobs.length) {
    tbody.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  tbody.innerHTML = jobs.map(job => {
    const statusClass = job.last_status === 'ok' ? 'status-ok' : job.last_status ? 'status-err' : '';
    const enabledBadge = job.enabled
      ? '<span class="status-ok">●</span>'
      : '<span class="status-err">●</span>';
    const toggleLabel = job.enabled ? 'Disable' : 'Enable';
    const toggleClass = job.enabled ? 'btn-secondary' : 'btn-primary';
    const inputPreview = formatCronInputPreview(job.input || {});
    return `<tr>
      <td class="dim">${job.id}</td>
      <td><span class="cron-truncate" title="${escapeAttr(job.name)}">${escapeHtml(job.name)}</span></td>
      <td class="mono">${escapeHtml(job.cron_expr)}</td>
      <td class="mono">${escapeHtml(job.start_node_id || '—')}</td>
      <td><span class="cron-truncate" title="${escapeAttr(inputPreview)}">${escapeHtml(inputPreview)}</span></td>
      <td class="mono">${escapeHtml(job.channel_id || '—')}</td>
      <td>${enabledBadge}</td>
      <td>${job.run_once ? '<span class="status-ok">●</span>' : '<span class="dim">—</span>'}</td>
      <td class="dim">${escapeHtml(job.last_run_at || '—')}</td>
      <td><span class="${statusClass}">${escapeHtml(job.last_status || '—')}</span></td>
      <td>
        <div class="btn-row" style="margin:0;gap:4px">
          <button class="btn btn-primary btn-sm" onclick="runCronJobNow(${job.id})">Run now</button>
          <button class="btn ${toggleClass} btn-sm" onclick="toggleCronJob(${job.id}, ${job.enabled ? 'false' : 'true'})">${toggleLabel}</button>
          <button class="btn btn-secondary btn-sm" onclick="openCronModal(${job.id})">Edit</button>
          <button class="btn btn-danger btn-sm" onclick="deleteCronJob(${job.id})">Delete</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function formatCronInputPreview(input) {
  const message = String(input.message || '').trim();
  const args = input.args && typeof input.args === 'object' ? input.args : {};
  const argsText = Object.keys(args).length ? JSON.stringify(args) : '{}';
  const text = `${message || '(empty)'} | ${argsText}`;
  return text.length > 72 ? `${text.slice(0, 69)}...` : text;
}

async function openCronModal(jobId = null) {
  cronEditingId = jobId;
  const title = document.getElementById('cron-modal-title');
  await populateCronStartNodes();

  if (jobId !== null) {
    const jobs = await GET('/api/schedule/jobs');
    const job = jobs.find(j => j.id === jobId);
    if (!job) return;
    title.textContent = 'Edit Job';
    document.getElementById('cj-name').value = job.name;
    document.getElementById('cj-cron-expr').value = job.cron_expr;
    document.getElementById('cj-start-node').value = job.start_node_id || '';
    const input = job.input || {};
    document.getElementById('cj-message').value = input.message || '';
    document.getElementById('cj-args-json').value = JSON.stringify(input.args || {}, null, 2);
    document.getElementById('cj-channel-id').value = job.channel_id || '';
    document.getElementById('cj-enabled').checked = job.enabled;
    document.getElementById('cj-run-once').checked = job.run_once || false;
    document.getElementById('cj-notify-before-run').checked = job.notify_before_run !== false;
    document.getElementById('cron-modal').classList.remove('hidden');
  } else {
    title.textContent = 'Add Job';
    document.getElementById('cj-name').value = '';
    document.getElementById('cj-cron-expr').value = '';
    document.getElementById('cj-message').value = '';
    document.getElementById('cj-args-json').value = '{}';
    document.getElementById('cj-channel-id').value = '';
    document.getElementById('cj-enabled').checked = true;
    document.getElementById('cj-run-once').checked = false;
    document.getElementById('cj-notify-before-run').checked = true;
    document.getElementById('cron-modal').classList.remove('hidden');
  }
}

async function populateCronStartNodes() {
  const select = document.getElementById('cj-start-node');
  const nodes = await GET('/api/nodes');
  const enabledNodes = nodes.filter(node => node.enabled);
  if (!enabledNodes.length) {
    select.innerHTML = '<option value="">No enabled nodes</option>';
    return;
  }
  select.innerHTML = enabledNodes.map(node => (
    `<option value="${escapeHtml(node.id)}">${escapeHtml(node.id)} — ${escapeHtml(node.name || node.id)}</option>`
  )).join('');
}

async function deleteCronJob(jobId) {
  if (!confirm('Delete this scheduled job?')) return;
  try {
    await DEL(`/api/schedule/jobs/${jobId}`);
    await loadCronJobs();
    toast('Job deleted');
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function runCronJobNow(jobId) {
  if (cronRunBusy) {
    toast('Another cron job is running', 'err');
    return;
  }
  cronRunBusy = true;
  try {
    await POST(`/api/schedule/jobs/${jobId}/run`, {});
    await loadCronJobs();
    toast('Job finished');
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    cronRunBusy = false;
  }
}

async function toggleCronJob(jobId, enabled) {
  try {
    await PUT(`/api/schedule/jobs/${jobId}`, { enabled });
    await loadCronJobs();
    toast(enabled ? 'Job enabled' : 'Job disabled');
  } catch (err) {
    toast(err.message, 'err');
  }
}

document.getElementById('btn-add-job').addEventListener('click', () => openCronModal(null));

document.getElementById('btn-reload-cron').addEventListener('click', async () => {
  await loadCronJobs();
  toast('Reloaded');
});

document.getElementById('cj-confirm').addEventListener('click', async () => {
  const name = document.getElementById('cj-name').value.trim();
  const cronExpr = document.getElementById('cj-cron-expr').value.trim();
  const startNodeId = document.getElementById('cj-start-node').value.trim();
  if (!name || !cronExpr || !startNodeId) {
    toast('Name, Cron Expression, and Start Node are required', 'err');
    return;
  }
  let args = {};
  try {
    args = JSON.parse(document.getElementById('cj-args-json').value.trim() || '{}');
    if (!args || Array.isArray(args) || typeof args !== 'object') {
      throw new Error('Args JSON must be an object');
    }
  } catch (err) {
    toast(`Invalid Args JSON: ${err.message}`, 'err');
    return;
  }
  const body = {
    name,
    cron_expr: cronExpr,
    start_node_id: startNodeId,
    input_json: {
      message: document.getElementById('cj-message').value.trim(),
      args,
      metadata: {},
    },
    channel_id: document.getElementById('cj-channel-id').value.trim(),
    enabled: document.getElementById('cj-enabled').checked,
    run_once: document.getElementById('cj-run-once').checked,
    notify_before_run: document.getElementById('cj-notify-before-run').checked,
  };
  try {
    if (cronEditingId !== null) {
      await PUT(`/api/schedule/jobs/${cronEditingId}`, body);
      toast('Job updated');
    } else {
      await POST('/api/schedule/jobs', body);
      toast('Job created');
    }
    document.getElementById('cron-modal').classList.add('hidden');
    await loadCronJobs();
  } catch (err) {
    toast(err.message, 'err');
  }
});

document.getElementById('cj-cancel').addEventListener('click', () => {
  document.getElementById('cron-modal').classList.add('hidden');
});

document.getElementById('cron-modal').addEventListener('click', event => {
  if (event.target === event.currentTarget) {
    document.getElementById('cron-modal').classList.add('hidden');
  }
});

// ── Workflow Trace ───────────────────────────────────────────────────────────

let selectedTraceRunId = null;

async function loadTraceRuns() {
  try {
    const runs = await GET('/api/traces/runs?limit=100');
    renderTraceRuns(runs);
  } catch (err) {
    toast(`Failed to load traces: ${err.message}`, 'err');
  }
}

function renderTraceRuns(runs) {
  const list = document.getElementById('trace-run-list');
  const empty = document.getElementById('trace-empty');
  if (!runs.length) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  list.innerHTML = runs.map(run => {
    const statusClass = run.status === 'ok' ? 'status-ok' : run.status === 'error' ? 'status-err' : '';
    const active = run.id === selectedTraceRunId ? ' active' : '';
    const message = run.message || '(empty)';
    return `<button class="trace-run-item${active}" onclick="openTraceRun(${run.id})">
      <div class="trace-run-title">
        <span class="mono">#${run.id}</span>
        <span class="${statusClass}">${escapeHtml(run.status)}</span>
        <span>${escapeHtml(run.start_node_id || '—')}</span>
      </div>
      <div class="trace-run-meta">${escapeHtml(run.started_at || '—')} · ${escapeHtml(run.trigger || 'workflow')} · ${run.node_count} nodes</div>
      <div class="trace-run-message">${escapeHtml(message)}</div>
    </button>`;
  }).join('');
}

async function openTraceRun(runId) {
  selectedTraceRunId = runId;
  try {
    const data = await GET(`/api/traces/runs/${runId}`);
    renderTraceRunDetail(data.run, data.nodes || []);
    await loadTraceRuns();
  } catch (err) {
    toast(`Failed to load trace: ${err.message}`, 'err');
  }
}

function renderTraceRunDetail(run, nodes) {
  document.getElementById('trace-detail-empty').classList.add('hidden');
  document.getElementById('trace-detail-content').classList.remove('hidden');
  document.getElementById('trace-run-summary').innerHTML = `
    <div class="trace-summary-grid">
      <div><span class="muted">Run</span><br><code>#${run.id}</code></div>
      <div><span class="muted">Status</span><br><span class="${run.status === 'ok' ? 'status-ok' : run.status === 'error' ? 'status-err' : ''}">${escapeHtml(run.status)}</span></div>
      <div><span class="muted">Start Node</span><br><code>${escapeHtml(run.start_node_id || '—')}</code></div>
      <div><span class="muted">Trigger</span><br>${escapeHtml(run.trigger || 'workflow')}</div>
      <div><span class="muted">Started</span><br>${escapeHtml(run.started_at || '—')}</div>
      <div><span class="muted">Finished</span><br>${escapeHtml(run.finished_at || '—')}</div>
      <div><span class="muted">Channel</span><br><code>${escapeHtml(run.channel_id || '—')}</code></div>
      <div><span class="muted">Nodes</span><br>${nodes.length}</div>
    </div>
    ${run.error ? `<pre class="trace-error">${escapeHtml(run.error)}</pre>` : ''}
  `;
  document.getElementById('trace-node-list').innerHTML = nodes.map(node => renderTraceNode(node)).join('');
}

function renderTraceNode(node) {
  const statusClass = node.status === 'ok' ? 'status-ok' : node.status === 'error' ? 'status-err' : '';
  return `<section class="trace-node">
    <h4><span class="dim">#${node.seq}</span> <code>${escapeHtml(node.node_id)}</code> <span class="${statusClass}">${escapeHtml(node.status)}</span></h4>
    ${node.error ? `<pre class="trace-error">${escapeHtml(node.error)}</pre>` : ''}
    <div class="trace-json-grid">
      <div class="trace-json-box">
        <label>Input JSON</label>
        <pre>${escapeHtml(formatJson(node.input))}</pre>
      </div>
      <div class="trace-json-box">
        <label>Output JSON</label>
        <pre>${escapeHtml(formatJson(node.output))}</pre>
      </div>
    </div>
  </section>`;
}

function formatJson(value) {
  if (typeof value === 'string') return value;
  return JSON.stringify(value ?? {}, null, 2);
}

document.getElementById('btn-reload-traces').addEventListener('click', async () => {
  await loadTraceRuns();
  toast('Reloaded');
});

// ── Init ──────────────────────────────────────────────────────────────────────

(async () => {
  try {
    initLiteGraph();
    await loadGraph();
    updateSaveState();
    S.canvas.centerOnGraph();
  } catch (err) {
    console.error(err);
    toast(`Load failed: ${err.message}`, 'err');
  }
})();

// ── Debug Chat ─────────────────────────────────────────────────────────────────

let debugBusy = false;

function appendDebugMsg(role, text, extra = '') {
  const feed = document.getElementById('debug-messages');
  const wrap = document.createElement('div');
  wrap.className = `debug-msg ${role}${extra ? ' ' + extra : ''}`;

  const label = document.createElement('div');
  label.className = 'debug-msg-label';
  label.textContent = role === 'user' ? 'You' : 'Bot';

  const bubble = document.createElement('div');
  bubble.className = 'debug-msg-bubble';
  bubble.textContent = text;

  wrap.appendChild(label);
  wrap.appendChild(bubble);
  feed.appendChild(wrap);
  feed.scrollTop = feed.scrollHeight;
  return wrap;
}

async function sendDebugMessage() {
  if (debugBusy) return;
  const input = document.getElementById('debug-input');
  const message = input.value.trim();
  if (!message) return;

  input.value = '';
  input.style.height = 'auto';
  appendDebugMsg('user', message);

  const thinkingEl = appendDebugMsg('bot', 'Thinking…', 'thinking');
  debugBusy = true;
  document.getElementById('debug-send').disabled = true;

  try {
    const data = await POST('/api/debug/chat', { message });
    thinkingEl.remove();
    const wrap = appendDebugMsg('bot', data.reply);
    if (data.node_trace && data.node_trace.length) {
      const trace = document.createElement('div');
      trace.className = 'debug-node-trace';
      trace.textContent = data.node_trace.join(' → ');
      wrap.insertBefore(trace, wrap.querySelector('.debug-msg-bubble'));
    }
  } catch (err) {
    thinkingEl.remove();
    appendDebugMsg('bot', `Error: ${err.message}`, 'thinking');
  } finally {
    debugBusy = false;
    document.getElementById('debug-send').disabled = false;
    input.focus();
  }
}

document.getElementById('debug-send').addEventListener('click', sendDebugMessage);

document.getElementById('debug-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendDebugMessage();
  }
});

// auto-grow textarea
document.getElementById('debug-input').addEventListener('input', function () {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 140) + 'px';
});

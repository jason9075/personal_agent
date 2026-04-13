'use strict';

const PASS_PALETTE = {
  1: { title: '#3B4F6E', body: '#2E3440', badge: '#81A1C1' },
  2: { title: '#3D5248', body: '#2E3440', badge: '#A3BE8C' },
  3: { title: '#5A3F35', body: '#2E3440', badge: '#D08770' },
};
const PASS_PALETTE_DEFAULT = { title: '#4A3D56', body: '#2E3440', badge: '#B48EAD' };

function passColors(idx) {
  return PASS_PALETTE[idx] || PASS_PALETTE_DEFAULT;
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
      node_type: 'agent',
      pass_index: 1,
      enabled: true,
      start_node: false,
      send_response: false,
      hooks: {},
    };
    this.resizable = false;
  }

  WorkflowNodeView.title = 'Node';

  WorkflowNodeView.prototype._applyColors = function _applyColors() {
    const pal = passColors(this.properties.pass_index);
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

    drawBadge(`P${this.properties.pass_index}`, passColors(this.properties.pass_index).badge);
    drawBadge(this.properties.node_type.toUpperCase(), '#5E81AC');
    if (this.properties.start_node) drawBadge('START', '#BF616A');
    if (this.properties.send_response) drawBadge('RESP', '#A3BE8C');

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

  S.canvas.onNodeSelected = lnode => {
    if (lnode) renderNodeEditor(lnode);
  };
  S.canvas.onNodeDeselected = () => showHint();
  S.canvas.onShowLinkMenu = link => {
    const edgeData = S.linkEdgeMap[link.id];
    if (edgeData) renderEdgeEditor(link.id, edgeData);
    return false;
  };
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

    const nodeMap = {};
    const colWidth = 320;
    const rowHeight = 150;
    const margin = 60;
    const sortedNodes = layoutSortedNodes(data.nodes);
    const rowIndexByPass = new Map();

    sortedNodes.forEach(node => {
      const lnode = LiteGraph.createNode('workflow/node');
      lnode.title = node.name || node.id;
      lnode.properties = {
        node_id: node.id,
        name: node.name,
        node_type: node.node_type,
        pass_index: node.pass_index,
        enabled: node.enabled,
        start_node: node.start_node,
        send_response: node.send_response,
        hooks: node.hooks || {},
      };
      lnode._applyColors();

      const passColumn = Math.max(0, (node.pass_index || 1) - 1);
      const rowIndex = rowIndexByPass.get(node.pass_index) || 0;
      lnode.pos = [margin + (passColumn * colWidth), margin + (rowIndex * rowHeight)];
      rowIndexByPass.set(node.pass_index, rowIndex + 1);
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
        condition_type: edge.condition_type,
        condition_value: edge.condition_value,
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
    if ((a.pass_index || 0) !== (b.pass_index || 0)) {
      return (a.pass_index || 0) - (b.pass_index || 0);
    }
    if ((a.node_type || '') !== (b.node_type || '')) {
      if (a.node_type === 'router') return -1;
      if (b.node_type === 'router') return 1;
    }
    return (a.id || '').localeCompare(b.id || '');
  });
}

function updateBadge() {
  const maxPass = S.graphData.nodes.reduce((acc, node) => Math.max(acc, node.pass_index), 0);
  document.getElementById('graph-badge').textContent =
    `${S.graphData.nodes.length} nodes · ${S.graphData.edges.length} edges · ${maxPass} passes`;
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
      condition_type: 'always',
      condition_value: '',
    });
    S.linkEdgeMap[link.id] = {
      dbId: result.id,
      from_node_id: fromNode.properties.node_id,
      to_node_id: toNode.properties.node_id,
      condition_type: 'always',
      condition_value: '',
    };
    S.graphData.edges.push({
      id: result.id,
      from_node_id: fromNode.properties.node_id,
      to_node_id: toNode.properties.node_id,
      condition_type: 'always',
      condition_value: '',
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

function showHint() {
  document.getElementById('editor-hint').classList.remove('hidden');
  document.getElementById('node-editor').classList.add('hidden');
  document.getElementById('edge-editor').classList.add('hidden');
}

function showNodeEditor() {
  document.getElementById('editor-hint').classList.add('hidden');
  document.getElementById('node-editor').classList.remove('hidden');
  document.getElementById('edge-editor').classList.add('hidden');
}

function showEdgeEditor() {
  document.getElementById('editor-hint').classList.add('hidden');
  document.getElementById('node-editor').classList.add('hidden');
  document.getElementById('edge-editor').classList.remove('hidden');
}

function renderNodeEditor(lnode) {
  showNodeEditor();
  const node = S.graphData.nodes.find(item => item.id === lnode.properties.node_id);
  if (!node) return;

  const hooks = node.hooks || {};
  setValue('ne-node-id', node.id, true);
  setValue('ne-name', node.name || '');
  setValue('ne-description', node.description || '');
  setValue('ne-node-type', node.node_type || 'agent');
  setValue('ne-pass-index', String(node.pass_index || 1));
  document.getElementById('ne-enabled').checked = !!node.enabled;
  document.getElementById('ne-start-node').checked = !!node.start_node;
  document.getElementById('ne-send-response').checked = !!node.send_response;
  document.getElementById('ne-use-prev-output').checked = !!node.use_prev_output;
  setValue('ne-executor-path', node.executor_path || '');
  setValue('ne-pre-hook-path', node.pre_hook_path || '');
  setValue('ne-post-hook-path', node.post_hook_path || '');
  setValue('ne-system-prompt-path', node.system_prompt_path || '');
  setValue('ne-prompt-template-path', node.prompt_template_path || '');
  setValue('ne-timeout-seconds', String(node.timeout_seconds || 600));
  setValue('ne-max-llm-calls', String(node.max_llm_calls || 0));
  setValue('ne-hook-pre', hooks.effective_pre_hook_path || '(none)', true);
  setValue('ne-hook-run', hooks.effective_executor_path || '(none)', true);
  setValue('ne-hook-post', hooks.effective_post_hook_path || '(none)', true);
  setValue('ne-route-label', node.route_label || '');
  setValue('ne-route-description', node.route_description || '');
  setValue('ne-router-mode', node.router_mode || 'llm');
  setValue('ne-router-patterns', (node.router_patterns || []).join('\n'));
  setValue('ne-allowed-tools', (node.allowed_tools || []).join('\n'));
  setValue('ne-input-schema', stringifyJson(node.input_schema));
  setValue('ne-output-schema', stringifyJson(node.output_schema));
  setValue('ne-metadata', stringifyJson(node.metadata || {}));
  syncRouterPatternVisibility();

  document.getElementById('ne-router-mode').onchange = syncRouterPatternVisibility;

  document.getElementById('ne-save').onclick = async () => {
    try {
      await PUT(`/api/nodes/${node.id}`, collectNodeForm(node.id));
      await loadGraph();
      const refreshed = findLiteNodeByDbId(node.id);
      if (refreshed) renderNodeEditor(refreshed);
      toast('Node saved');
    } catch (err) {
      toast(err.message, 'err');
    }
  };

  document.getElementById('ne-details').onclick = async () => {
    await renderNodeDetails(collectNodeForm(node.id), node.hooks || {});
  };

  document.getElementById('ne-delete').onclick = async () => {
    if (!confirm(`Delete node "${node.id}"? Its connections will also be removed.`)) return;
    try {
      await DEL(`/api/nodes/${node.id}`);
      await loadGraph();
      toast('Node deleted');
    } catch (err) {
      toast(err.message, 'err');
    }
  };
}

async function renderNodeDetails(node, hooks = {}) {
  const [systemPromptPreview, promptTemplatePreview] = await Promise.all([
    previewPrompt(node.system_prompt_path),
    previewPrompt(node.prompt_template_path),
  ]);

  setText('dm-node-id', node.id || '(unsaved)');
  setText('dm-name', node.name || '(empty)');
  setText('dm-node-type', node.node_type || '(empty)');
  setText('dm-pass-index', `Pass ${node.pass_index ?? '-'}`);
  setText('dm-flags', formatFlags(node));

  setText('dm-pre-hook', hooks.effective_pre_hook_path || node.pre_hook_path || '(none)');
  setText('dm-executor', hooks.effective_executor_path || node.executor_path || '(none)');
  setText('dm-post-hook', hooks.effective_post_hook_path || node.post_hook_path || '(none)');
  setText('dm-timeout', `${node.timeout_seconds ?? 0}s`);
  setText('dm-max-llm', String(node.max_llm_calls ?? 0));

  setPre('dm-route-label', node.route_label || '(empty)');
  setPre('dm-route-description', node.route_description || '(empty)');
  setPre('dm-router-patterns', (node.router_patterns || []).length ? node.router_patterns.join('\n') : '(none)');
  setPre('dm-system-prompt-path', node.system_prompt_path || '(none)');
  setPre('dm-system-prompt', systemPromptPreview || '(empty)');
  setPre('dm-prompt-template-path', node.prompt_template_path || '(none)');
  setPre('dm-prompt-template', promptTemplatePreview || '(empty)');
  setPre('dm-input-schema', stringifyJson(node.input_schema) || '(empty)');
  setPre('dm-output-schema', stringifyJson(node.output_schema) || '(empty)');
  setPre('dm-allowed-tools', (node.allowed_tools || []).length ? node.allowed_tools.join('\n') : '(none)');
  setPre('dm-metadata', stringifyJson(node.metadata || {}) || '{}');

  document.getElementById('details-modal').classList.remove('hidden');
}

async function previewPrompt(path) {
  if (!path) return '';
  const result = await POST('/api/prompt-preview', { path });
  return result.content || '';
}

function renderEdgeEditor(linkId, edgeData) {
  showEdgeEditor();
  setValue('ee-from', edgeData.from_node_id, true);
  setValue('ee-to', edgeData.to_node_id, true);
  setValue('ee-cond-type', edgeData.condition_type || 'always');
  setValue('ee-cond-value', edgeData.condition_value || '');
  syncEdgeValueVisibility();
  document.getElementById('ee-cond-type').onchange = syncEdgeValueVisibility;

  document.getElementById('ee-save').onclick = async () => {
    try {
      await DEL(`/api/workflow/edges/${edgeData.dbId}`);
      await POST('/api/workflow/edges', {
        from_node_id: edgeData.from_node_id,
        to_node_id: edgeData.to_node_id,
        condition_type: document.getElementById('ee-cond-type').value,
        condition_value: document.getElementById('ee-cond-value').value.trim(),
      });
      await loadGraph();
      toast('Edge saved');
      showHint();
    } catch (err) {
      toast(err.message, 'err');
    }
  };

  document.getElementById('ee-delete').onclick = async () => {
    try {
      await DEL(`/api/workflow/edges/${edgeData.dbId}`);
      S.graph.removeLink(linkId);
      await loadGraph();
      toast('Edge deleted');
      showHint();
    } catch (err) {
      toast(err.message, 'err');
    }
  };
}

function collectNodeForm(nodeId) {
  return {
    id: nodeId,
    name: document.getElementById('ne-name').value.trim(),
    description: document.getElementById('ne-description').value.trim(),
    node_type: document.getElementById('ne-node-type').value,
    pass_index: parseInt(document.getElementById('ne-pass-index').value, 10),
    enabled: document.getElementById('ne-enabled').checked,
    start_node: document.getElementById('ne-start-node').checked,
    send_response: document.getElementById('ne-send-response').checked,
    use_prev_output: document.getElementById('ne-use-prev-output').checked,
    executor_path: document.getElementById('ne-executor-path').value.trim(),
    pre_hook_path: blankToNull(document.getElementById('ne-pre-hook-path').value),
    post_hook_path: blankToNull(document.getElementById('ne-post-hook-path').value),
    system_prompt_path: blankToNull(document.getElementById('ne-system-prompt-path').value),
    prompt_template_path: blankToNull(document.getElementById('ne-prompt-template-path').value),
    timeout_seconds: parseInt(document.getElementById('ne-timeout-seconds').value, 10),
    max_llm_calls: parseInt(document.getElementById('ne-max-llm-calls').value, 10),
    route_label: blankToNull(document.getElementById('ne-route-label').value),
    route_description: blankToNull(document.getElementById('ne-route-description').value),
    router_mode: document.getElementById('ne-router-mode').value,
    router_patterns: splitLines(document.getElementById('ne-router-patterns').value),
    allowed_tools: splitLines(document.getElementById('ne-allowed-tools').value),
    input_schema: parseJsonOrNull(document.getElementById('ne-input-schema').value),
    output_schema: parseJsonOrNull(document.getElementById('ne-output-schema').value),
    metadata: parseJsonOrObject(document.getElementById('ne-metadata').value),
  };
}

function stringifyJson(value) {
  if (!value || (typeof value === 'object' && Object.keys(value).length === 0)) return '';
  return JSON.stringify(value, null, 2);
}

function parseJsonOrNull(value) {
  const text = value.trim();
  if (!text) return null;
  return JSON.parse(text);
}

function parseJsonOrObject(value) {
  const text = value.trim();
  if (!text) return {};
  return JSON.parse(text);
}

function splitLines(value) {
  return value.split('\n').map(line => line.trim()).filter(Boolean);
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

function formatFlags(node) {
  const flags = [];
  if (node.enabled) flags.push('enabled');
  if (node.start_node) flags.push('start_node');
  if (node.send_response) flags.push('send_response');
  if (node.use_prev_output) flags.push('use_prev_output');
  return flags.length ? flags.join(' | ') : '(none)';
}

function syncRouterPatternVisibility() {
  const show = document.getElementById('ne-router-mode').value === 'direct_regex';
  document.getElementById('ne-patterns-wrap').style.display = show ? '' : 'none';
}

function syncEdgeValueVisibility() {
  const show = document.getElementById('ee-cond-type').value !== 'always';
  document.getElementById('ee-value-wrap').style.display = show ? '' : 'none';
}

function findLiteNodeByDbId(nodeId) {
  return S.graph._nodes.find(node => node.properties.node_id === nodeId) || null;
}

function openModal() {
  document.getElementById('modal-node-id').value = '';
  document.getElementById('modal-name').value = '';
  document.getElementById('modal-node-type').value = 'agent';
  document.getElementById('modal-pass').value = '2';
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
      node_type: document.getElementById('modal-node-type').value,
      pass_index: parseInt(document.getElementById('modal-pass').value, 10),
      enabled: true,
      start_node: false,
      send_response: true,
      use_prev_output: true,
      executor_path: '',
      pre_hook_path: null,
      post_hook_path: null,
      system_prompt_path: null,
      prompt_template_path: null,
      timeout_seconds: 600,
      max_llm_calls: 0,
      route_label: nodeId,
      route_description: '',
      router_mode: 'llm',
      router_patterns: [],
      allowed_tools: [],
      input_schema: null,
      output_schema: null,
      metadata: {},
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

document.getElementById('btn-add-node').addEventListener('click', openModal);

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
  await loadGraph();
  toast('Reloaded');
});

document.getElementById('details-close').addEventListener('click', () => {
  document.getElementById('details-modal').classList.add('hidden');
});

(async () => {
  try {
    initLiteGraph();
    await loadGraph();
    S.canvas.centerOnGraph();
  } catch (err) {
    console.error(err);
    toast(`Load failed: ${err.message}`, 'err');
  }
})();

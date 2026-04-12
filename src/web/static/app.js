/**
 * personal_agent — Workflow Manager (LiteGraph.js edition)
 *
 * Architecture:
 *  - Custom LiteGraph node type "workflow/skill" renders each WorkflowNode
 *  - graph.onConnectionChange diffs link IDs to detect add/remove and syncs DB
 *  - Node selection → side panel skill editor (real-time API save)
 *  - Right-click on a link → edge condition editor in side panel
 */

'use strict';

// ── Pass colours — Nord palette ───────────────────────────────────────────────
// Frost:  #5E81AC (nord10) #81A1C1 (nord9) #88C0D0 (nord8)
// Aurora: #A3BE8C (nord14) #D08770 (nord12) #B48EAD (nord15)
const PASS_PALETTE = {
  1: { title: '#3B4F6E', body: '#2E3440', badge: '#81A1C1' },  // frost blue
  2: { title: '#3D5248', body: '#2E3440', badge: '#A3BE8C' },  // aurora green
  3: { title: '#5A3F35', body: '#2E3440', badge: '#D08770' },  // aurora orange
};
const PASS_PALETTE_DEFAULT = { title: '#4A3D56', body: '#2E3440', badge: '#B48EAD' }; // aurora purple

function passColors(idx) { return PASS_PALETTE[idx] || PASS_PALETTE_DEFAULT; }

// ── App state ─────────────────────────────────────────────────────────────────
const S = {
  graph:        null,   // LGraph
  canvas:       null,   // LGraphCanvas
  graphData:    null,   // {nodes, edges, skills}
  allSkills:    [],
  // LiteGraph link_id → {dbId, from_node_id, to_node_id, condition_type, condition_value}
  linkEdgeMap:  {},
  // LiteGraph node.id → WorkflowNode DB id string (e.g. "p1:echo")
  lnodeToDbId:  {},
  // track previous link set for diff
  _prevLinkIds: new Set(),
  addEdgeMode:  false,
};

// ── API helpers ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}
const GET  = p      => api('GET',    p);
const PUT  = (p, b) => api('PUT',    p, b);
const POST = (p, b) => api('POST',   p, b);
const DEL  = p      => api('DELETE', p);

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg, kind = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${kind}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = ''; }, 2400);
}

// ── Register custom LiteGraph node type ──────────────────────────────────────
function registerSkillNodeType() {
  function SkillNode() {
    this.addInput('in',  'workflow');
    this.addOutput('out', 'workflow');
    this.size       = [220, 74];
    this.properties = { node_id: '', skill_id: '', pass_index: 1, enabled: true };
    this.resizable  = false;
  }

  SkillNode.title = 'Skill';

  SkillNode.prototype.onDrawForeground = function(ctx) {
    if (this.flags && this.flags.collapsed) return;
    const pal  = passColors(this.properties.pass_index);
    const TH   = LiteGraph.NODE_TITLE_HEIGHT || 30;
    const W    = this.size[0];

    // Pass badge strip (top of body)
    ctx.fillStyle = pal.body + 'cc';
    ctx.fillRect(0, TH, W, this.size[1]);

    ctx.fillStyle = pal.badge;
    ctx.beginPath();
    ctx.roundRect(6, TH + 6, 54, 16, 3);
    ctx.fill();

    ctx.fillStyle = '#fff';
    ctx.font = 'bold 10px monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`Pass ${this.properties.pass_index}`, 10, TH + 17);

    // Skill ID
    ctx.fillStyle = this.properties.enabled ? '#aab0cc' : '#666';
    ctx.font = '11px monospace';
    ctx.fillText(this.properties.skill_id, 6, TH + 40);

    if (!this.properties.enabled) {
      ctx.fillStyle = 'rgba(0,0,0,0.45)';
      ctx.fillRect(0, TH, W, this.size[1]);
      ctx.fillStyle = '#d95c5c';
      ctx.font = 'bold 9px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText('DISABLED', W - 6, TH + this.size[1] - 6);
      ctx.textAlign = 'left';
    }
  };

  // Apply pass colours on title when node is created/updated
  SkillNode.prototype._applyColors = function() {
    const pal = passColors(this.properties.pass_index);
    this.color   = this.properties.enabled ? pal.title : '#2a2a2a';
    this.bgcolor = this.properties.enabled ? pal.body   : '#1a1a1a';
  };

  SkillNode.prototype.onConfigure = function() {
    this._applyColors();
  };

  LiteGraph.registerNodeType('workflow/skill', SkillNode);
}

// ── LiteGraph initialisation ─────────────────────────────────────────────────
function initLiteGraph() {
  registerSkillNodeType();

  S.graph  = new LGraph();
  const canvasEl = document.getElementById('lg-canvas');
  S.canvas = new LGraphCanvas(canvasEl, S.graph);

  // Dark background
  S.canvas.background_color = '#0d1018';
  S.canvas.render_canvas_border = false;
  S.canvas.render_connections_shadows = false;
  S.canvas.show_info = false;
  S.canvas.node_panel = null;

  // Node selection
  S.canvas.onNodeSelected = (lnode) => {
    if (!lnode) return;
    showEditorTab();
    renderNodeEditor(lnode);
  };
  S.canvas.onNodeDeselected = () => {
    if (!S.canvas.selected_nodes || !Object.keys(S.canvas.selected_nodes).length) {
      showHint();
    }
  };

  // Right-click on a link → edge editor
  S.canvas.onShowLinkMenu = (link /*, e*/) => {
    const edgeData = S.linkEdgeMap[link.id];
    if (edgeData) {
      showEditorTab();
      renderEdgeEditor(link.id, edgeData);
    }
    return false; // suppress default menu
  };

  // Connection changes (add / remove)
  S.graph.onConnectionChange = onConnectionChange;

  // Resize canvas to fill its container
  fitCanvas();
  window.addEventListener('resize', fitCanvas);
}

function fitCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  const el   = document.getElementById('lg-canvas');
  el.width   = wrap.clientWidth;
  el.height  = wrap.clientHeight;
  if (S.canvas) S.canvas.resize(el.width, el.height);
}

// ── Load graph from API ───────────────────────────────────────────────────────
async function loadGraph() {
  const [gd, skills] = await Promise.all([GET('/api/workflow'), GET('/api/skills')]);
  S.graphData = gd;
  S.allSkills = skills;

  S.graph.clear();
  S.linkEdgeMap  = {};
  S.lnodeToDbId  = {};
  S._prevLinkIds = new Set();

  // Group nodes by pass so we can space them vertically
  const byPass = {};
  for (const n of gd.nodes) {
    (byPass[n.pass_index] = byPass[n.pass_index] || []).push(n);
  }

  const nodeMap = {};   // DB node id → LiteGraph node
  const COL_W   = 280;
  const ROW_H   = 120;
  const MARGIN  = 60;

  for (const n of gd.nodes) {
    const lnode = LiteGraph.createNode('workflow/skill');
    const skill = gd.skills[n.skill_id] || {};
    lnode.title = skill.display_name || n.skill_id;
    lnode.properties = {
      node_id:    n.id,
      skill_id:   n.skill_id,
      pass_index: n.pass_index,
      enabled:    n.enabled,
    };
    lnode._applyColors();

    // Position: x by pass, y by index within pass
    const siblings = byPass[n.pass_index];
    const yIdx     = siblings.indexOf(n);
    lnode.pos = [
      MARGIN + (n.pass_index - 1) * COL_W,
      MARGIN + yIdx * ROW_H,
    ];

    S.graph.add(lnode);
    nodeMap[n.id]          = lnode;
    S.lnodeToDbId[lnode.id] = n.id;
  }

  // Add edges (LiteGraph links)
  for (const e of gd.edges) {
    const from = nodeMap[e.from_node_id];
    const to   = nodeMap[e.to_node_id];
    if (from && to) {
      const link = from.connect(0, to, 0);
      if (link) {
        S.linkEdgeMap[link.id] = {
          dbId:           e.id,
          from_node_id:   e.from_node_id,
          to_node_id:     e.to_node_id,
          condition_type:  e.condition_type,
          condition_value: e.condition_value,
        };
        S._prevLinkIds.add(link.id);
      }
    }
  }

  updateBadge();
  renderSkillsTab();
}

function updateBadge() {
  const maxPass = S.graphData.nodes.reduce((m, n) => Math.max(m, n.pass_index), 0);
  document.getElementById('graph-badge').textContent =
    `${S.graphData.nodes.length} nodes · ${S.graphData.edges.length} edges · ${maxPass} passes`;
}

// ── Connection change handler ─────────────────────────────────────────────────
function onConnectionChange(/*changedNode*/) {
  const links = S.graph.links || {};
  const currentIds = new Set(Object.values(links).map(l => l.id));

  // Added links
  for (const id of currentIds) {
    if (!S._prevLinkIds.has(id)) handleLinkAdded(links[id]);
  }
  // Removed links
  for (const id of S._prevLinkIds) {
    if (!currentIds.has(id)) handleLinkRemoved(id);
  }

  S._prevLinkIds = currentIds;
}

async function handleLinkAdded(link) {
  // Skip links that were pre-loaded from DB (already in linkEdgeMap)
  if (!link || S.linkEdgeMap[link.id]) return;

  const fromLNode = S.graph.getNodeById(link.origin_id);
  const toLNode   = S.graph.getNodeById(link.target_id);
  if (!fromLNode || !toLNode) return;

  const fromDbId = fromLNode.properties.node_id;
  const toDbId   = toLNode.properties.node_id;
  if (!fromDbId || !toDbId) return;

  try {
    const result = await POST('/api/workflow/edges', {
      from_node_id:    fromDbId,
      to_node_id:      toDbId,
      condition_type:  'always',
      condition_value: '',
    });
    S.linkEdgeMap[link.id] = {
      dbId: result.id, from_node_id: fromDbId, to_node_id: toDbId,
      condition_type: 'always', condition_value: '',
    };
    toast('Edge added');
    // Refresh edge count in badge
    S.graphData.edges.push({ id: result.id, from_node_id: fromDbId, to_node_id: toDbId, condition_type: 'always', condition_value: '' });
    updateBadge();
  } catch (e) {
    toast(e.message, 'err');
  }
}

async function handleLinkRemoved(linkId) {
  const ed = S.linkEdgeMap[linkId];
  if (!ed) return;
  try {
    await DEL(`/api/workflow/edges/${ed.dbId}`);
    delete S.linkEdgeMap[linkId];
    S.graphData.edges = S.graphData.edges.filter(e => e.id !== ed.dbId);
    updateBadge();
    toast('Edge removed');
  } catch (e) {
    toast(e.message, 'err');
  }
}

// ── Panel helpers ─────────────────────────────────────────────────────────────
function showEditorTab() {
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === 'editor'));
  document.getElementById('tab-editor').classList.remove('hidden');
  document.getElementById('tab-skills').classList.add('hidden');
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

// ── Node editor ───────────────────────────────────────────────────────────────
function renderNodeEditor(lnode) {
  showNodeEditor();
  const { node_id, skill_id, pass_index, enabled } = lnode.properties;

  document.getElementById('ne-node-id').textContent = node_id || '(unsaved)';
  document.getElementById('ne-pass').textContent    = `Pass ${pass_index}`;
  document.getElementById('ne-skill-id').textContent = skill_id;
  document.getElementById('ne-enabled').checked     = enabled;

  const skill = S.allSkills.find(s => s.id === skill_id) || null;
  if (skill) populateSkillForm(skill);

  // Save node
  document.getElementById('ne-save').onclick = async () => {
    const nowEnabled = document.getElementById('ne-enabled').checked;
    try {
      await PUT(`/api/workflow/nodes/${node_id}`, {
        pass_index,
        skill_id,
        enabled: nowEnabled,
      });
      lnode.properties.enabled = nowEnabled;
      lnode._applyColors();
      S.canvas.setDirty(true, true);
      toast('Node saved');
    } catch (e) { toast(e.message, 'err'); }
  };

  // Delete node
  document.getElementById('ne-delete').onclick = async () => {
    if (!confirm(`Delete node "${node_id}"? Its connections will also be removed.`)) return;
    try {
      await DEL(`/api/workflow/nodes/${node_id}`);
      S.graph.remove(lnode);
      showHint();
      toast('Node deleted');
      await refreshGraphData();
    } catch (e) { toast(e.message, 'err'); }
  };

  // Save skill
  document.getElementById('se-save').onclick = () => saveSkillForm(skill_id);
}

function populateSkillForm(skill) {
  document.getElementById('se-id-label').textContent     = skill.id;
  document.getElementById('se-display-name').value       = skill.display_name || '';
  document.getElementById('se-description').value        = skill.description  || '';
  document.getElementById('se-router-mode').value        = skill.router_mode  || 'llm';
  document.getElementById('se-patterns').value           = (skill.router_patterns || []).join('\n');
  document.getElementById('se-pass2-mode').value         = skill.pass2_mode   || 'never';
  document.getElementById('se-script-path').value        = skill.script_path  || '';
  document.getElementById('se-system-prompt').value      = skill.system_prompt || '';

  syncPatternsVisibility();
  document.getElementById('se-router-mode').onchange = syncPatternsVisibility;
}

function syncPatternsVisibility() {
  const show = document.getElementById('se-router-mode').value === 'direct_regex';
  document.getElementById('se-patterns-wrap').style.display = show ? '' : 'none';
}

async function saveSkillForm(skillId) {
  const patterns = document.getElementById('se-patterns').value
    .split('\n').map(l => l.trim()).filter(Boolean);
  try {
    await PUT(`/api/skills/${skillId}`, {
      display_name:    document.getElementById('se-display-name').value.trim(),
      description:     document.getElementById('se-description').value.trim(),
      router_mode:     document.getElementById('se-router-mode').value,
      router_patterns: patterns,
      pass2_mode:      document.getElementById('se-pass2-mode').value,
      script_path:     document.getElementById('se-script-path').value.trim(),
      system_prompt:   document.getElementById('se-system-prompt').value.trim(),
    });
    toast('Skill saved');
    await refreshGraphData();
    // Update node title in canvas if display_name changed
    S.canvas.setDirty(true, true);
  } catch (e) { toast(e.message, 'err'); }
}

// ── Edge editor ───────────────────────────────────────────────────────────────
function renderEdgeEditor(linkId, edgeData) {
  showEdgeEditor();
  document.getElementById('ee-from').textContent = edgeData.from_node_id;
  document.getElementById('ee-to').textContent   = edgeData.to_node_id;
  document.getElementById('ee-cond-type').value  = edgeData.condition_type;
  document.getElementById('ee-cond-value').value = edgeData.condition_value;

  syncEdgeValueVisibility();
  document.getElementById('ee-cond-type').onchange = syncEdgeValueVisibility;

  document.getElementById('ee-save').onclick = async () => {
    const condType  = document.getElementById('ee-cond-type').value;
    const condValue = document.getElementById('ee-cond-value').value.trim();
    try {
      // Replace: delete old + create new
      await DEL(`/api/workflow/edges/${edgeData.dbId}`);
      const result = await POST('/api/workflow/edges', {
        from_node_id:    edgeData.from_node_id,
        to_node_id:      edgeData.to_node_id,
        condition_type:  condType,
        condition_value: condValue,
      });
      // Update local map
      S.linkEdgeMap[linkId] = { ...edgeData, dbId: result.id, condition_type: condType, condition_value: condValue };
      toast('Edge saved');
      await refreshGraphData();
      showHint();
    } catch (e) { toast(e.message, 'err'); }
  };

  document.getElementById('ee-delete').onclick = async () => {
    try {
      await DEL(`/api/workflow/edges/${edgeData.dbId}`);
      // Remove the LiteGraph link
      S.graph.removeLink(linkId);
      delete S.linkEdgeMap[linkId];
      S._prevLinkIds.delete(linkId);
      toast('Edge deleted');
      await refreshGraphData();
      showHint();
    } catch (e) { toast(e.message, 'err'); }
  };
}

function syncEdgeValueVisibility() {
  const show = document.getElementById('ee-cond-type').value !== 'always';
  document.getElementById('ee-value-wrap').style.display = show ? '' : 'none';
}

// ── Skills tab ────────────────────────────────────────────────────────────────
function renderSkillsTab() {
  const list = document.getElementById('skills-list');
  list.innerHTML = '';
  S.allSkills.forEach(skill => {
    const card = document.createElement('div');
    card.className = `skill-card${skill.enabled ? '' : ' disabled'}`;
    card.innerHTML = `
      <div class="skill-card-name">${skill.display_name}
        <code style="font-size:10px;margin-left:4px">${skill.id}</code>
      </div>
      <div class="skill-card-meta">${skill.router_mode} · pass2=${skill.pass2_mode}</div>
    `;
    card.onclick = () => {
      showEditorTab();
      showNodeEditor();
      document.getElementById('ne-node-id').textContent  = '(select a node)';
      document.getElementById('ne-pass').textContent     = '—';
      document.getElementById('ne-skill-id').textContent = skill.id;
      document.getElementById('ne-enabled').checked      = skill.enabled;
      document.getElementById('ne-save').style.display   = 'none';
      document.getElementById('ne-delete').style.display = 'none';
      populateSkillForm(skill);
      document.getElementById('se-save').onclick = () => saveSkillForm(skill.id);
    };
    list.appendChild(card);
  });
}

// ── Refresh graph data (after mutations) ──────────────────────────────────────
async function refreshGraphData() {
  const [gd, skills] = await Promise.all([GET('/api/workflow'), GET('/api/skills')]);
  S.graphData = gd;
  S.allSkills = skills;
  updateBadge();
  renderSkillsTab();

  // Update node titles in canvas without full reload
  for (const [lid, dbId] of Object.entries(S.lnodeToDbId)) {
    const lnode = S.graph.getNodeById(Number(lid));
    if (!lnode) continue;
    const dbNode = gd.nodes.find(n => n.id === dbId);
    if (!dbNode) continue;
    const skill = gd.skills[dbNode.skill_id];
    if (skill) {
      lnode.title = skill.display_name || dbNode.skill_id;
      lnode.properties.enabled = dbNode.enabled;
      lnode._applyColors();
    }
  }
  S.canvas.setDirty(true, true);
}

// ── Add-node modal ────────────────────────────────────────────────────────────
function openModal() {
  const sel = document.getElementById('modal-skill');
  sel.innerHTML = S.allSkills.map(s =>
    `<option value="${s.id}">${s.display_name} (${s.id})</option>`
  ).join('');
  autoNodeId();
  document.getElementById('modal').classList.remove('hidden');
}

function autoNodeId() {
  const pass    = document.getElementById('modal-pass').value;
  const skillId = document.getElementById('modal-skill').value;
  document.getElementById('modal-node-id').value = `p${pass}:${skillId}`;
}

document.getElementById('modal-pass').addEventListener('input',  autoNodeId);
document.getElementById('modal-skill').addEventListener('change', autoNodeId);

document.getElementById('modal-confirm').addEventListener('click', async () => {
  const skillId = document.getElementById('modal-skill').value;
  const pass    = parseInt(document.getElementById('modal-pass').value, 10);
  const nodeId  = document.getElementById('modal-node-id').value.trim();
  if (!nodeId || !skillId || isNaN(pass)) { toast('Fill in all fields', 'err'); return; }

  try {
    await PUT(`/api/workflow/nodes/${nodeId}`, { pass_index: pass, skill_id: skillId, enabled: true });
    document.getElementById('modal').classList.add('hidden');
    toast('Node added');
    // Add to LiteGraph without full reload
    const skill  = S.allSkills.find(s => s.id === skillId) || {};
    const lnode  = LiteGraph.createNode('workflow/skill');
    lnode.title  = skill.display_name || skillId;
    lnode.properties = { node_id: nodeId, skill_id: skillId, pass_index: pass, enabled: true };
    lnode._applyColors();
    lnode.pos = [60 + (pass - 1) * 280, 60 + S.graph.nodes.length * 30];
    S.graph.add(lnode);
    S.lnodeToDbId[lnode.id] = nodeId;
    await refreshGraphData();
  } catch (e) { toast(e.message, 'err'); }
});

document.getElementById('modal-cancel').addEventListener('click', () => {
  document.getElementById('modal').classList.add('hidden');
});

// ── Tab switching ─────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-editor').classList.toggle('hidden', btn.dataset.tab !== 'editor');
    document.getElementById('tab-skills').classList.toggle('hidden', btn.dataset.tab !== 'skills');
  });
});

// ── Toolbar ───────────────────────────────────────────────────────────────────
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
    toast('Drag from a node\'s output slot to another\'s input', 'ok');
  } else {
    btn.classList.remove('active');
    btn.textContent = '↗ Edge';
  }
});

document.getElementById('btn-reload').addEventListener('click', async () => {
  await loadGraph();
  toast('Reloaded');
});

// ── Bootstrap ─────────────────────────────────────────────────────────────────
(async () => {
  try {
    initLiteGraph();
    await loadGraph();
    S.canvas.centerOnGraph();
  } catch (err) {
    console.error(err);
    toast('Load failed: ' + err.message, 'err');
  }
})();

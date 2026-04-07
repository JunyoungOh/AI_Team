'use strict';
const CardLayout = (() => {
  const CARD_W = 210, CARD_H = 140, GAP_X = 32, GAP_Y = 80;

  function computePositions(agents, edges) {
    const positions = {};

    // 1. Build adjacency from edges (filter invalid refs)
    const agentIds = new Set(agents.map(a => a.id));
    const children = {};
    const parentCount = {};
    for (const agent of agents) {
      children[agent.id] = [];
      parentCount[agent.id] = 0;
    }
    for (const edge of edges) {
      if (!agentIds.has(edge.from) || !agentIds.has(edge.to)) continue; // skip invalid
      if (edge.from === edge.to) continue; // skip self-loops
      if (!children[edge.from]) children[edge.from] = [];
      children[edge.from].push(edge.to);
      parentCount[edge.to] = (parentCount[edge.to] || 0) + 1;
    }

    // 2. Topological sort (Kahn's algorithm) to assign layers
    //    Nodes with 0 incoming edges = layer 0, etc.
    const layers = {};
    const queue = [];
    const inDegree = {};
    for (const agent of agents) {
      inDegree[agent.id] = parentCount[agent.id] || 0;
      if (inDegree[agent.id] === 0) {
        queue.push(agent.id);
        layers[agent.id] = 0;
      }
    }

    while (queue.length > 0) {
      const nodeId = queue.shift();
      const kids = children[nodeId] || [];
      for (const kid of kids) {
        // Child layer = max of current assignment and parent+1
        layers[kid] = Math.max(layers[kid] || 0, layers[nodeId] + 1);
        inDegree[kid]--;
        if (inDegree[kid] === 0) {
          queue.push(kid);
        }
      }
    }

    // 2b. Handle cycle orphans — nodes stuck in cycle get no layer assigned
    for (const agent of agents) {
      if (layers[agent.id] == null) {
        layers[agent.id] = 0; // fallback: place at top if cycle detected
      }
    }

    // 3. Group agents by layer
    const layerGroups = {};
    let maxLayer = 0;
    for (const agent of agents) {
      const layer = layers[agent.id] || 0;
      if (!layerGroups[layer]) layerGroups[layer] = [];
      layerGroups[layer].push(agent.id);
      if (layer > maxLayer) maxLayer = layer;
    }

    // 4. Position each layer
    const startY = 80;
    for (let layer = 0; layer <= maxLayer; layer++) {
      const group = layerGroups[layer] || [];
      const totalWidth = group.length * CARD_W + (group.length - 1) * GAP_X;
      let startX = Math.max(80, (900 - totalWidth) / 2); // center horizontally

      for (let i = 0; i < group.length; i++) {
        positions[group[i]] = {
          x: startX + i * (CARD_W + GAP_X),
          y: startY + layer * (CARD_H + GAP_Y),
        };
      }
    }

    return positions;
  }

  return { computePositions, CARD_W, CARD_H, GAP_X, GAP_Y };
})();

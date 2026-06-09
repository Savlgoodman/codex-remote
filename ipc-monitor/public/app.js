const state = {
  status: null,
  packets: [],
  filterText: "",
};

const elements = {
  statusStrip: document.querySelector("#status-strip"),
  statusDetails: document.querySelector("#status-details"),
  packets: document.querySelector("#packets"),
  packetCount: document.querySelector("#packet-count"),
  startBtn: document.querySelector("#start-btn"),
  pauseBtn: document.querySelector("#pause-btn"),
  exportBtn: document.querySelector("#export-btn"),
  clearBtn: document.querySelector("#clear-btn"),
  searchInput: document.querySelector("#search-input"),
  legend: document.querySelector("#legend"),
  packetTemplate: document.querySelector("#packet-template"),
};

const TYPE_LABELS = {
  request: "Request",
  response: "Response",
  broadcast: "Broadcast",
  "thread.summary": "Thread Summary",
  "client-discovery-request": "Discovery Req",
  "client-discovery-response": "Discovery Res",
  "invalid-json": "Invalid JSON",
  unknown: "Other",
};

function typeClass(type) {
  return `type-${String(type ?? "unknown").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatTime(isoString) {
  if (!isoString) {
    return "-";
  }

  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return isoString;
  }

  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDateTime(isoString) {
  if (!isoString) {
    return "-";
  }
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return isoString;
  }
  return date.toLocaleString("zh-CN", {
    hour12: false,
  });
}

function formatJson(value) {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function packetSearchText(packet) {
  return [
    packet.ipcType,
    packet.method,
    packet.requestId,
    packet.conversationId,
    packet.summary,
    packet.rawText,
    JSON.stringify(packet.parsed),
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

function visiblePackets() {
  const search = state.filterText.trim().toLowerCase();
  if (!search) {
    return state.packets;
  }
  return state.packets.filter((packet) => packetSearchText(packet).includes(search));
}

function renderLegend() {
  const items = [
    ["thread.summary", "Thread Summary"],
    ["broadcast", "Broadcast"],
    ["request", "Request"],
    ["response", "Response"],
    ["client-discovery-request", "Discovery"],
    ["invalid-json", "Invalid"],
  ];

  elements.legend.innerHTML = items
    .map(
      ([type, label]) =>
        `<span class="legend-item ${typeClass(type)}"><span class="legend-dot"></span>${escapeHtml(label)}</span>`,
    )
    .join("");
}

function renderStatusStrip() {
  const status = state.status;
  if (!status) {
    elements.statusStrip.innerHTML = "";
    return;
  }

  const items = [
    {
      label: "IPC",
      value: status.ipcConnected ? "Connected" : "Disconnected",
      tone: status.ipcConnected ? "ok" : "warn",
    },
    {
      label: "Capture",
      value: status.captureEnabled ? "Running" : "Paused",
      tone: status.captureEnabled ? "active" : "neutral",
    },
    {
      label: "Buffer",
      value: `${status.bufferedPackets}`,
      tone: "neutral",
    },
    {
      label: "Dropped",
      value: `${status.droppedCount}`,
      tone: status.droppedCount > 0 ? "warn" : "neutral",
    },
  ];

  elements.statusStrip.innerHTML = items
    .map(
      (item) =>
        `<div class="status-pill tone-${item.tone}"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(
          item.value,
        )}</strong></div>`,
    )
    .join("");

  elements.startBtn.disabled = status.captureEnabled;
  elements.pauseBtn.disabled = !status.captureEnabled;
  elements.exportBtn.disabled = status.bufferedPackets === 0;
}

function renderStatusDetails() {
  const status = state.status;
  if (!status) {
    elements.statusDetails.innerHTML = "";
    return;
  }

  const entries = [
    ["IPC endpoint", status.ipcEndpoint],
    ["Monitor client", status.monitorClientId],
    ["Negotiated client", status.negotiatedClientId],
    ["Last connected", formatDateTime(status.lastConnectedAt)],
    ["Last seen", formatDateTime(status.lastSeenAt)],
    ["Last packet", formatDateTime(status.lastPacketAt)],
    ["Total packets", status.packetCount],
    ["Buffered packets", status.bufferedPackets],
    ["Last error", status.lastError ? `${status.lastError.at} ${status.lastError.message}` : "-"],
  ];

  elements.statusDetails.innerHTML = entries
    .map(
      ([label, value]) =>
        `<div class="status-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(formatValue(value))}</dd></div>`,
    )
    .join("");
}

function renderPackets() {
  const packets = visiblePackets();
  elements.packetCount.textContent = `${packets.length} shown`;

  if (packets.length === 0) {
    elements.packets.innerHTML = '<div class="empty-state">No packets yet. Start capture and wait for IPC traffic.</div>';
    return;
  }

  const fragment = document.createDocumentFragment();

  for (const packet of packets) {
    const node = elements.packetTemplate.content.firstElementChild.cloneNode(true);
    const packetType = packet.ipcType ?? "unknown";

    node.classList.add(typeClass(packetType));
    node.querySelector(".type-chip").textContent = TYPE_LABELS[packetType] ?? packetType;
    node.querySelector(".direction-chip").textContent = packet.direction === "out" ? "OUT" : "IN";
    node.querySelector(".method-chip").textContent = packet.method ?? "-";
    node.querySelector(".packet-title").textContent = packet.summary ?? packetType;

    const meta = [
      packet.conversationId ? `conversation ${packet.conversationId}` : null,
      packet.requestId ? `request ${packet.requestId}` : null,
    ]
      .filter(Boolean)
      .join(" | ");

    node.querySelector(".packet-meta").textContent = meta || "No conversation or request metadata";
    node.querySelector(".packet-size").textContent = `${packet.size} B`;
    node.querySelector(".packet-time").textContent = formatTime(packet.observedAt);
    node.querySelector(".parsed-json").textContent = formatJson(packet.parsed);
    node.querySelector(".raw-json").textContent = packet.rawText ?? formatJson(packet.raw);

    fragment.appendChild(node);
  }

  elements.packets.replaceChildren(fragment);
}

function renderAll() {
  renderStatusStrip();
  renderStatusDetails();
  renderPackets();
}

async function postJson(url) {
  const response = await fetch(url, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function filenameFromContentDisposition(value) {
  const match = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(value ?? "");
  if (!match) {
    return null;
  }
  try {
    return decodeURIComponent(match[1].replace(/^"|"$/g, ""));
  } catch {
    return match[1].replace(/^"|"$/g, "");
  }
}

async function downloadJsonlExport() {
  const response = await fetch("/api/packets.jsonl", {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Export failed: ${response.status}`);
  }

  const filename =
    filenameFromContentDisposition(response.headers.get("content-disposition")) ??
    `codex-ipc-${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function mergePacket(packet) {
  const existingIndex = state.packets.findIndex((item) => item.id === packet.id);
  if (existingIndex >= 0) {
    state.packets.splice(existingIndex, 1, packet);
  } else {
    state.packets.unshift(packet);
  }
}

async function loadInitialData() {
  const [statusResponse, packetsResponse] = await Promise.all([
    fetch("/api/status"),
    fetch("/api/packets"),
  ]);

  state.status = await statusResponse.json();
  const packetPayload = await packetsResponse.json();
  state.packets = packetPayload.packets ?? [];
  renderAll();
}

function wireControls() {
  elements.startBtn.addEventListener("click", async () => {
    state.status = await postJson("/api/capture/start");
    renderAll();
  });

  elements.pauseBtn.addEventListener("click", async () => {
    state.status = await postJson("/api/capture/pause");
    renderAll();
  });

  elements.exportBtn.addEventListener("click", async () => {
    try {
      elements.exportBtn.disabled = true;
      elements.exportBtn.textContent = "Exporting...";
      await downloadJsonlExport();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Export failed");
    } finally {
      elements.exportBtn.textContent = "Export JSONL";
      renderStatusStrip();
    }
  });

  elements.clearBtn.addEventListener("click", async () => {
    state.status = await postJson("/api/capture/clear");
    state.packets = [];
    renderAll();
  });

  elements.searchInput.addEventListener("input", (event) => {
    state.filterText = event.target.value ?? "";
    renderPackets();
  });
}

function wireEvents() {
  const eventSource = new EventSource("/events");

  eventSource.addEventListener("snapshot", (event) => {
    const data = JSON.parse(event.data);
    state.status = data.status;
    state.packets = data.packets ?? [];
    renderAll();
  });

  eventSource.addEventListener("status", (event) => {
    state.status = JSON.parse(event.data);
    renderStatusStrip();
    renderStatusDetails();
  });

  eventSource.addEventListener("packet", (event) => {
    const packet = JSON.parse(event.data);
    mergePacket(packet);
    renderAll();
  });

  eventSource.addEventListener("packets-cleared", () => {
    state.packets = [];
    renderPackets();
  });
}

renderLegend();
wireControls();
loadInitialData()
  .then(() => {
    wireEvents();
  })
  .catch((error) => {
    elements.packets.innerHTML = `<div class="empty-state">Failed to load monitor: ${escapeHtml(error.message)}</div>`;
  });

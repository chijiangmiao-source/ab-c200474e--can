/* 星载 CAN 审查回放前端 */
"use strict";

const ERR_KINDS = [
  { v: "none", t: "无错误" },
  { v: "ack", t: "ACK 错误" },
  { v: "crc", t: "CRC 错误" },
  { v: "bit", t: "指定数据位错误" },
];
const STATUS_TEXT = {
  ok: "正常应答",
  bit_error: "位错误",
  ack_error: "ACK 错误",
  crc_error: "CRC 错误",
};
const STATE_TEXT = {
  active: "error-active 主动错误",
  passive: "error-passive 被动错误",
  busoff: "bus-off",
};

let nodeRows = [
  { name: "SENSOR_A", tec: 0 },
  { name: "SENSOR_B", tec: 0 },
  { name: "SENSOR_C", tec: 0 },
  { name: "", tec: 0 },
];
let reqRows = [];
let lastResult = null;

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, String(v));
  }
  for (const k of kids) {
    if (k == null) continue;
    e.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
  }
  return e;
}

// ---------------------------------------------------------------- 录入区
function renderNodeEditor() {
  const box = $("#nodes");
  box.innerHTML = "";
  nodeRows.forEach((nr, i) => {
    const fld = el("div", { class: "node-fld" });
    const name = el("input", { type: "text", value: nr.name, placeholder: `N${i + 1}` });
    const tec = el("input", { type: "number", min: "0", max: "255", value: String(nr.tec) });
    name.addEventListener("input", () => { nr.name = name.value.trim(); renderReqTable(); });
    tec.addEventListener("input", () => { nr.tec = tec.value === "" ? "" : Number(tec.value); });
    fld.append(
      el("label", {}, `节点 ${i + 1}`), name,
      el("label", {}, "初始 TEC"), tec,
      el("span", { style: "color:#889;font-size:12px" },
        "0–127 主动 · 128–255 被动 · 256 即 bus-off"),
    );
    box.appendChild(fld);
  });
}

function renderReqTable() {
  const tb = $("#req-table tbody");
  tb.innerHTML = "";
  const names = nodeRows.map(n => n.name).filter(Boolean);
  reqRows.forEach((r, i) => {
    if (r.node && !names.includes(r.node)) r.node = "";
    const nodeSel = el("select", {});
    names.forEach(n => nodeSel.appendChild(
      el("option", { value: n, ...(r.node === n ? { selected: "selected" } : {}) }, n)));
    nodeSel.value = r.node || names[0] || "";
    r.node = nodeSel.value;
    nodeSel.addEventListener("input", () => { r.node = nodeSel.value; clearRowFeedback(i); });

    const timeIn = el("input", { type: "number", min: "0", value: String(r.time), style: "width:80px" });
    timeIn.addEventListener("input", () => { r.time = timeIn.value; clearRowFeedback(i); });

    const idIn = el("input", { type: "text", value: r.id, style: "width:90px", placeholder: "0x123" });
    idIn.addEventListener("input", () => { r.id = idIn.value; clearRowFeedback(i); });

    const dlcIn = el("input", { type: "number", min: "0", max: "8", value: String(r.dlc), style: "width:58px" });
    dlcIn.addEventListener("input", () => { r.dlc = dlcIn.value; clearRowFeedback(i); });

    const dataIn = el("input", { type: "text", value: r.data, placeholder: "如 11 22 3F", style: "width:150px" });
    dataIn.addEventListener("input", () => { r.data = dataIn.value; clearRowFeedback(i); });

    const errSel = el("select", {});
    ERR_KINDS.forEach(({ v, t }) => {
      const o = el("option", { value: v }, t);
      if (r.error === v) o.setAttribute("selected", "selected");
      errSel.appendChild(o);
    });
    const posIn = el("input", { type: "number", min: "35", value: r.position || "",
      placeholder: "35 起", style: "width:82px",
      class: r.error === "bit" ? "" : "hidden" });
    errSel.addEventListener("input", () => {
      r.error = errSel.value;
      posIn.classList.toggle("hidden", r.error !== "bit");
      clearRowFeedback(i);
    });
    posIn.addEventListener("input", () => { r.position = posIn.value; clearRowFeedback(i); });

    const feedback = el("div", { class: "invalid-feedback" });
    const tr = el("tr", {},
      el("td", {}, String(i + 1)),
      el("td", {}, timeIn),
      el("td", {}, nodeSel),
      el("td", {}, idIn),
      el("td", {}, dlcIn),
      el("td", {}, dataIn),
      el("td", {}, errSel),
      el("td", {}, posIn),
      el("td", {}, (() => {
        const b = el("button", { type: "button", text: "删除" });
        b.addEventListener("click", () => { reqRows.splice(i, 1); renderReqTable(); });
        return b;
      })()),
      el("td", { class: "fb" }, feedback),
    );
    tb.appendChild(tr);
    if (r._errors) showRowFeedback(i, r._errors);
  });
}

function clearRowFeedback(i) {
  const tr = $(`#req-table tbody tr:nth-child(${i + 1})`);
  if (!tr) return;
  const fb = $(".invalid-feedback", tr);
  fb.textContent = "";
  fb.classList.remove("show");
  $$("input,select", tr).forEach(x => x.classList.remove("invalid"));
  $("#global-error").textContent = "";
}

function clearAllFeedback() {
  $$("#req-table .invalid-feedback").forEach(d => { d.textContent = ""; d.classList.remove("show"); });
  $$("#req-table input,#req-table select").forEach(x => x.classList.remove("invalid"));
  $$("#nodes input").forEach(x => x.classList.remove("invalid"));
  $("#global-error").textContent = "";
}

function showRowFeedback(i, msgs) {
  const tr = $(`#req-table tbody tr:nth-child(${i + 1})`);
  if (!tr) return;
  const fb = $(".invalid-feedback", tr);
  msgs.forEach(m => fb.appendChild(el("div", {}, m)));
  fb.classList.add("show");
  $$("input,select", tr).forEach(x => x.classList.add("invalid"));
}

function parseData(str) {
  return String(str || "").split(/[\s,]+/).filter(Boolean)
    .map(t => parseInt(t, 16));
}

function collectPayload() {
  const nodes = [];
  const initialTec = {};
  nodeRows.forEach(nr => {
    if (nr.name) {
      nodes.push(nr.name);
      initialTec[nr.name] = Number(nr.tec) || 0;
    }
  });
  const requests = reqRows.map(r => {
    const idRaw = String(r.id).trim();
    const ident = /^0x[0-9a-f]+$/i.test(idRaw) ? parseInt(idRaw, 16)
      : /^[0-9]+$/.test(idRaw) ? parseInt(idRaw, 10) : idRaw;
    const req = {
      time: Number(r.time),
      node: r.node,
      id: ident,
      dlc: Number(r.dlc),
      data: parseData(r.data),
      error: null,
    };
    if (r.error === "bit") req.error = { kind: "bit", position: Number(r.position) };
    else if (r.error && r.error !== "none") req.error = { kind: r.error };
    return req;
  });
  return { nodes, initial_tec: initialTec, requests };
}

// ---------------------------------------------------------------- 结果
function bitText(v) {
  return el("span", { class: v === 0 ? "bit-d" : "bit-r" },
    v === 0 ? "0（显性）" : "1（隐性）");
}

function stateClass(s) {
  return { active: "state-active", passive: "state-passive", busoff: "state-busoff" }[s] || "";
}

function renderResults(result) {
  lastResult = result;
  $("#results").classList.remove("hidden");

  const totalErr = result.frames.filter(f => f.status !== "ok").length;
  const sum = $("#summary");
  sum.innerHTML = "";
  [
    ["发送帧数", result.frames.length, ""],
    ["错误帧", totalErr, "color:var(--err)"],
    ["被拒请求", result.rejections.length, "color:var(--err)"],
    ["bus-off 恢复", result.recoveries.length, "color:var(--ok)"],
  ].forEach(([label, num, style]) => {
    sum.appendChild(el("div", { class: "stat" },
      el("div", {}, label), el("div", { class: "num", style }, String(num))));
  });

  const ccard = el("div", { class: "card" }, el("h3", {}, "节点错误计数终态"));
  const ct = el("table", { class: "counter-table" });
  ct.innerHTML = "<thead><tr><th>节点</th><th>TEC</th><th>REC</th><th>状态</th><th>bus-off 恢复进度</th></tr></thead>";
  const ctb = el("tbody");
  result.nodes.forEach(n => {
    ctb.appendChild(el("tr", {},
      el("td", {}, n.name),
      el("td", {}, String(n.tec)),
      el("td", {}, String(n.rec)),
      el("td", { class: stateClass(n.state) }, STATE_TEXT[n.state]),
      el("td", {}, n.state === "busoff" ? `${n.recovery_count}/128` : "—"),
    ));
  });
  ct.appendChild(ctb);
  ccard.appendChild(ct);
  sum.appendChild(ccard);

  const rj = $("#rejections");
  rj.innerHTML = "";
  if (result.rejections.length) {
    rj.appendChild(el("h3", {}, "bus-off 节点被拒绝的请求"));
    result.rejections.forEach(r =>
      rj.appendChild(el("div", { class: "reject-item" }, r.message)));
  }

  const rv = $("#recoveries");
  rv.innerHTML = "";
  if (result.recoveries.length) {
    rv.appendChild(el("h3", {}, "bus-off 恢复事件（连续 128 × 11 隐性位）"));
    result.recoveries.forEach(r =>
      rv.appendChild(el("div", { class: "recovery-item" }, `[t=${r.time}] ${r.message}`)));
  }

  const box = $("#frames");
  box.innerHTML = "";
  result.frames.forEach((f, fi) => box.appendChild(renderFrame(f, fi)));
}

function renderFrame(f, fi) {
  const tpl = $("#frame-tpl").content.cloneNode(true);
  const card = $(".frame-card", tpl);
  $(".frame-title", tpl).textContent =
    `帧 ${fi + 1} · t=${f.time} · 获胜节点 ${f.winner} · ` +
    `ID=0x${f.winner_id.toString(16).padStart(3, "0").toUpperCase()} · DLC=${f.dlc}` +
    (f.injected ? ` · 标注：${({ack: "ACK", crc: "CRC", bit: "数据位"})[f.injected]}故障` : "");
  const badge = $(".badge", tpl);
  badge.className = "badge " + f.status;
  badge.textContent = STATUS_TEXT[f.status];
  if (f.error) {
    const st = f.error.state_after;
    if (st === "passive")
      $(".frame-head", tpl).appendChild(el("span", { class: "badge passive-badge" }, "发送方进入 error-passive"));
    if (st === "busoff")
      $(".frame-head", tpl).appendChild(el("span",
        { class: "badge", style: "background:#fdecea;color:var(--err)" }, "发送方进入 bus-off"));
  }

  const body = $(".frame-body", tpl);
  const before = f.counters_before[f.winner];
  const after = f.counters_after[f.winner];
  const kv = el("div", { class: "kv" });
  kv.append(
    el("div", {}, "获胜节点：", el("b", {}, f.winner)),
    el("div", {}, "错误来源：", el("b", {}, f.error ? f.error.source : "无（正常帧）")),
    el("div", {}, `TEC：`, el("b", {},
      `${before.tec} → ${after.tec}` + (f.error
        ? `（${f.error.tec_delta >= 0 ? "+" : ""}${f.error.tec_delta}）` : "（成功发送 −1）"))),
    el("div", {}, "发送后状态：", el("b", { class: stateClass(after.state) }, STATE_TEXT[after.state])),
  );
  body.appendChild(kv);

  if (f.error) {
    body.appendChild(el("div", { class: "evidence red" },
      el("b", {}, "首个违规证据："), " " + f.error.first_violation.message));
    const fk = f.error.flag;
    const flagText = fk.kind === "active" ? "主动错误标志（6 显性）"
      : fk.kind === "passive" ? "被动错误标志（6 隐性，另叠加 8 位挂起发送）"
      : "bus-off 边界标志";
    body.appendChild(el("div", {},
      "错误界定：" + flagText + `；总线驱动：${fk.drivers.join("、") || "（无，隐性）"}` +
      (fk.receiver_active_flags && fk.receiver_active_flags.length
        ? `；主动接收方：${fk.receiver_active_flags.join("、")}` : "") +
      "；随后 8 位错误界定符 + 3 位帧间隔（共 11 隐性位）"));
  }

  if (f.losers.length) {
    const ls = el("div");
    ls.appendChild(el("h3", {}, "仲裁失利定位"));
    f.losers.forEach(l => ls.appendChild(el("div", { class: "loser-item" }, l.message)));
    body.appendChild(ls);
  }

  const allc = el("div");
  allc.appendChild(el("h3", {}, "各节点错误计数变化"));
  const ct = el("table");
  ct.innerHTML = "<thead><tr><th>节点</th><th>TEC 前→后</th><th>REC 前→后</th><th>状态后</th></tr></thead>";
  const ctb = el("tbody");
  Object.keys(f.counters_before).forEach(nm => {
    const b = f.counters_before[nm], a = f.counters_after[nm];
    ctb.appendChild(el("tr", {},
      el("td", {}, nm),
      el("td", {}, `${b.tec} → ${a.tec}`),
      el("td", {}, `${b.rec} → ${a.rec}`),
      el("td", { class: stateClass(a.state) }, STATE_TEXT[a.state]),
    ));
  });
  ct.appendChild(ctb);
  allc.appendChild(ct);
  body.appendChild(allc);

  // 位序轨迹
  const traceBox = $(".trace", tpl);
  const wave = $(".wave", tpl);
  const ttb = $(".trace-table tbody", tpl);
  const width = Math.max(600, f.trace.length * 14);
  wave.style.width = width + "px";
  f.trace.forEach((b, bi) => {
    const cls = "bitcell " + (b.bus === 0 ? "dom" : "rec") + (b.stuffed ? " stuff" : "");
    const cell = el("div", {
      class: cls,
      style: `left:${bi * 14}px;width:14px;`,
      title: `第${b.index}位 t=${b.time} ${b.field}${b.stuffed ? "(填充)" : ""}[${b.field_index}] 总线=${b.bus}`,
    }, String(b.bus));
    wave.appendChild(cell);
    const arbText = b.arbiters
      ? "仲裁驱动 " + Object.entries(b.arbiters).map(([n, v]) => `${n}:${v}`).join(" ")
      : null;
    ttb.appendChild(el("tr", {},
      el("td", {}, String(b.index)),
      el("td", {}, String(b.time)),
      el("td", {}, b.field + (b.stuffed ? "（填充位）" : "")),
      el("td", {}, String(b.field_index)),
      el("td", {}, b.stuffed ? "是" : ""),
      el("td", {}, arbText ? el("span", {}, arbText) : bitText(b.sent)),
      el("td", {}, bitText(b.bus)),
      el("td", {}, (b.drivers || []).join("、")),
      el("td", {}, b.note),
    ));
  });
  if (f.error) {
    const wi = f.error.first_violation.wire_index - 1;
    const cell = $$(".bitcell", wave)[wi];
    if (cell) cell.classList.add("violation");
  }
  wirePlayer(card, f);

  $(".frame-head", tpl).addEventListener("click", (ev) => {
    if (ev.target.tagName === "BUTTON") return;
    traceBox.classList.toggle("hidden");
    const btn = $(".toggle-trace", tpl);
    btn.textContent = traceBox.classList.contains("hidden") ? "展开位序轨迹" : "收起位序轨迹";
  });
  $(".toggle-trace", tpl).addEventListener("click", () => {
    traceBox.classList.toggle("hidden");
    $(".toggle-trace", tpl).textContent =
      traceBox.classList.contains("hidden") ? "展开位序轨迹" : "收起位序轨迹";
  });
  return tpl;
}

function wirePlayer(card, f) {
  const wave = $(".wave", card);
  const ttb = $(".trace-table tbody", card);
  const cursor = $(".cursor", card);
  const cells = $$(".bitcell", wave);
  const trs = $$("tr", ttb);
  const total = f.trace.length;
  let pos = -1;
  let timer = null;

  function render() {
    cells.forEach((c, i) => c.classList.toggle("current", i === pos));
    trs.forEach((r, i) => r.classList.toggle("current", i === pos));
    cursor.textContent = pos < 0 ? `共 ${total} 位（未开始）`
      : `第 ${pos + 1} / ${total} 位 · t=${f.trace[pos].time} · ${f.trace[pos].field}`;
    if (pos >= 0) {
      cells[pos].scrollIntoView({ block: "nearest", inline: "nearest" });
      trs[pos].scrollIntoView({ block: "nearest" });
    }
  }
  $(".play", card).addEventListener("click", (e) => {
    e.stopPropagation();
    if (timer) {
      clearInterval(timer); timer = null; e.target.textContent = "⏵ 逐位播放"; return;
    }
    e.target.textContent = "⏸ 暂停";
    timer = setInterval(() => {
      pos = Math.min(pos + 1, total - 1);
      if (pos === total - 1) {
        clearInterval(timer); timer = null;
        $(".play", card).textContent = "⏵ 逐位播放";
      }
      render();
    }, 90);
  });
  $(".step-back", card).addEventListener("click", (e) => {
    e.stopPropagation(); pos = Math.max(-1, pos - 1); render();
  });
  $(".step-fwd", card).addEventListener("click", (e) => {
    e.stopPropagation(); pos = Math.min(total - 1, pos + 1); render();
  });
  render();
}

// ---------------------------------------------------------------- 提交
async function runSim() {
  clearAllFeedback();
  $("#results").classList.add("hidden");
  const payload = collectPayload();
  let resp;
  try {
    resp = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    $("#global-error").textContent = "请求失败：" + e;
    return;
  }
  const data = await resp.json();
  if (!resp.ok) {
    lastResult = null;  // 清除旧结论
    applyServerErrors(data.errors || []);
    return;
  }
  renderResults(data.result);
}

function applyServerErrors(errors) {
  errors.forEach(e => {
    let m;
    if ((m = e.field.match(/^requests\[(\d+)\]/))) {
      const i = Number(m[1]);
      if (reqRows[i]) reqRows[i]._errors = [e.message];
      showRowFeedback(i, [e.message]);
    } else if ((m = e.field.match(/^nodes\[(\d+)\]/))) {
      const inp = $(`#nodes .node-fld:nth-child(${Number(m[1]) + 1}) input`);
      if (inp) inp.classList.add("invalid");
      $("#global-error").textContent =
        ($("#global-error").textContent ? $("#global-error").textContent + "；" : "") + e.message;
    } else if ((m = e.field.match(/^initial_tec\.(.+)$/))) {
      $$("#nodes .node-fld").forEach(fld => {
        if ($("input[type=text]", fld).value.trim() === m[1])
          $("input[type=number]", fld).classList.add("invalid");
      });
      $("#global-error").textContent =
        ($("#global-error").textContent ? $("#global-error").textContent + "；" : "") + e.message;
    } else {
      $("#global-error").textContent =
        ($("#global-error").textContent ? $("#global-error").textContent + "；" : "") + e.message;
    }
  });
}

// ---------------------------------------------------------------- 示例与初始化
function loadSample() {
  nodeRows = [
    { name: "SENSOR_A", tec: 0 },
    { name: "SENSOR_B", tec: 0 },
    { name: "SENSOR_C", tec: 127 },
    { name: "", tec: 0 },
  ];
  reqRows = [
    { time: "0", node: "SENSOR_A", id: "0x123", dlc: "2", data: "11 22", error: "none", position: "" },
    { time: "0", node: "SENSOR_B", id: "0x124", dlc: "1", data: "AA", error: "none", position: "" },
    { time: "0", node: "SENSOR_C", id: "0x100", dlc: "1", data: "55", error: "ack", position: "" },
    { time: "55", node: "SENSOR_B", id: "0x200", dlc: "1", data: "FF", error: "bit", position: "35" },
  ];
  renderNodeEditor();
  renderReqTable();
  clearAllFeedback();
  $("#results").classList.add("hidden");
}

$("#add-row").addEventListener("click", () => {
  if (reqRows.length >= 24) {
    $("#global-error").textContent = "至多录入 24 条帧请求";
    return;
  }
  const names = nodeRows.map(n => n.name).filter(Boolean);
  reqRows.push({
    time: "0", node: names[0] || "", id: "0x100",
    dlc: "1", data: "00", error: "none", position: "",
  });
  renderReqTable();
});
$("#run").addEventListener("click", runSim);
$("#load-sample").addEventListener("click", loadSample);

loadSample();

/* Paper Radar dashboard — read-only client. All text enters the DOM as text
   nodes: paper titles, venues and query strings are third-party data. */
"use strict";

// ---------------------------------------------------------------- utilities

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") throw new Error("innerHTML is not allowed here");
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "style") Object.assign(node.style, value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs, children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    node.setAttribute(key, value);
  }
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

function compact(value) {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) >= 1000) return (value / 1000).toFixed(value % 1000 === 0 ? 0 : 1) + "K";
  return String(Math.round(value * 10) / 10);
}

/* Fixed-width local timestamps: locale formats wrap badly in narrow columns. */
function dateLabel(iso, withTime) {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return String(iso).slice(0, 10);
  const pad = (value) => String(value).padStart(2, "0");
  const date = `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`;
  if (!withTime) return date;
  return `${date} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function shortDate(ymd) {
  return ymd ? ymd.slice(5).replace("-", "/") : "";
}

async function api(path, params) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== "" && value !== null && value !== undefined) url.searchParams.set(key, value);
  }
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({ error: "응답을 해석할 수 없습니다." }));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function apiPost(path, body) {
  const response = await fetch(new URL(path, window.location.origin), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({ error: "응답을 해석할 수 없습니다." }));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function errorBox(message) {
  return el("p", { class: "empty", text: "불러오지 못했습니다: " + message });
}

/* Feedback verdicts wear the reserved status hues, always with icon + label. */
const VERDICTS = [
  { value: "keep", label: "Keep", icon: "★" },
  { value: "maybe", label: "Maybe", icon: "◐" },
  { value: "reject", label: "Reject", icon: "✕" },
  { value: "read", label: "Read", icon: "✓" },
];
const VERDICT_BY_VALUE = new Map(VERDICTS.map((verdict) => [verdict.value, verdict]));
const VERDICT_TONE = { keep: "good", maybe: "warning", reject: "critical", read: "muted" };

function verdictPill(value) {
  const verdict = VERDICT_BY_VALUE.get(value);
  if (!verdict) return null;
  return el("span", { class: "pill status", title: "피드백" }, [
    el("span", { class: "pill-dot " + (VERDICT_TONE[value] || "muted") }),
    verdict.icon + " " + verdict.label,
  ]);
}

// ------------------------------------------------------------------ tooltip

const tooltip = {
  node: document.getElementById("tooltip"),
  show(event, build) {
    clear(this.node);
    this.node.append(build());
    this.node.hidden = false;
    this.move(event);
  },
  move(event) {
    const box = this.node.getBoundingClientRect();
    const x = Math.min(event.clientX + 14, window.innerWidth - box.width - 8);
    const y = Math.max(8, event.clientY - box.height - 12);
    this.node.style.left = x + "px";
    this.node.style.top = y + "px";
  },
  hide() {
    this.node.hidden = true;
  },
};

function tipContent(value, label, extra) {
  return el("div", {}, [
    el("div", { class: "tip-value", text: value }),
    el("div", { class: "tip-label", text: label }),
    extra ? el("div", { class: "tip-label", text: extra }) : null,
  ]);
}

function attachTip(node, build) {
  node.addEventListener("pointerenter", (event) => tooltip.show(event, build));
  node.addEventListener("pointermove", (event) => tooltip.move(event));
  node.addEventListener("pointerleave", () => tooltip.hide());
  node.tabIndex = 0;
  node.addEventListener("focus", (event) => {
    const box = node.getBoundingClientRect();
    tooltip.show({ clientX: box.left, clientY: box.bottom + 24 }, build);
  });
  node.addEventListener("blur", () => tooltip.hide());
}

// ------------------------------------------------------------------- charts

/* Horizontal bars: length carries magnitude, one hue, value direct-labelled. */
function barChart(container, items, options) {
  const config = options || {};
  clear(container);
  if (!items.length) {
    container.append(el("p", { class: "empty", text: config.empty || "데이터가 없습니다." }));
    return;
  }
  const max = Math.max(...items.map((item) => item.value), 1);
  const wrap = el("div", { class: "bars" });
  for (const item of items) {
    const fill = el("div", { class: "bar-fill", style: { width: (item.value / max) * 100 + "%" } });
    const row = el("div", { class: "bar-row" }, [
      el("div", { class: "bar-label", text: item.label, title: item.label }),
      el("div", { class: "bar-track" }, [fill]),
      el("div", { class: "bar-value", text: config.format ? config.format(item.value) : compact(item.value) }),
    ]);
    attachTip(row, () => tipContent(
      (config.format ? config.format(item.value) : compact(item.value)) + (config.unit || ""),
      item.label,
      item.note
    ));
    wrap.append(row);
  }
  container.append(wrap);
}

/* Columns for an ordered scale (score buckets, run sequence). */
function columnChart(container, items, options) {
  const config = options || {};
  clear(container);
  if (!items.length) {
    container.append(el("p", { class: "empty", text: config.empty || "데이터가 없습니다." }));
    return;
  }
  const max = Math.max(...items.map((item) => item.value), 1);
  const bars = el("div", { class: "columns" });
  const axis = el("div", { class: "column-axis" });
  for (const item of items) {
    const bar = el("div", { class: "column-bar", style: { height: Math.max((item.value / max) * 100, item.value > 0 ? 3 : 0.8) + "%" } });
    const slot = el("div", { class: "column-slot" }, [
      el("div", { class: "column-value", text: compact(item.value) }),
      bar,
    ]);
    attachTip(slot, () => tipContent(compact(item.value) + (config.unit || ""), item.label, item.note));
    bars.append(slot);
    axis.append(el("div", { class: "column-tick", text: item.tick ?? item.label, title: item.label }));
  }
  container.append(bars, axis);
}

/* Multi-series line with a snapping crosshair; ≤3 series, legend + end labels. */
function lineChart(container, spec) {
  const draw = () => {
    clear(container);
    const width = Math.max(container.clientWidth || 640, 320);
    const height = spec.height || 210;
    const pad = { top: 14, right: spec.endLabels === false ? 16 : 74, bottom: 26, left: 38 };
    const dates = spec.dates;
    const series = spec.series;
    if (!dates.length || !series.length) {
      container.append(el("p", { class: "empty", text: "데이터가 없습니다." }));
      return;
    }
    const max = Math.max(1, ...series.flatMap((item) => item.points));
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const xAt = (index) => pad.left + (dates.length === 1 ? plotWidth / 2 : (index / (dates.length - 1)) * plotWidth);
    const yAt = (value) => pad.top + plotHeight - (value / max) * plotHeight;

    const root = svg("svg", { class: "linechart", width, height, viewBox: `0 0 ${width} ${height}`, role: "img" });
    const ticks = max <= 4 ? Array.from({ length: max + 1 }, (_, i) => i) : [0, Math.round(max / 2), max];
    for (const tick of ticks) {
      root.append(svg("line", { class: "grid-line", x1: pad.left, x2: width - pad.right, y1: yAt(tick), y2: yAt(tick) }));
      root.append(svg("text", { class: "tick-text", x: pad.left - 8, y: yAt(tick) + 4, "text-anchor": "end" }, [String(tick)]));
    }
    root.append(svg("line", { class: "axis-line", x1: pad.left, x2: width - pad.right, y1: yAt(0), y2: yAt(0) }));

    // Tick every `step` days plus the final date, dropping any tick that would
    // collide with it.
    const step = Math.max(1, Math.ceil(dates.length / 8));
    const last = dates.length - 1;
    dates.forEach((date, index) => {
      const isLast = index === last;
      if (!isLast && (index % step !== 0 || last - index < step * 0.6)) return;
      root.append(svg("text", {
        class: "tick-text", x: xAt(index), y: height - 8,
        "text-anchor": isLast && spec.endLabels === false ? "end" : "middle",
      }, [shortDate(date)]));
    });

    series.forEach((item, order) => {
      const color = `var(--series-${order + 1})`;
      const path = item.points.map((value, index) => `${index === 0 ? "M" : "L"}${xAt(index)},${yAt(value)}`).join(" ");
      if (spec.area && series.length === 1) {
        const area = `${path} L${xAt(item.points.length - 1)},${yAt(0)} L${xAt(0)},${yAt(0)} Z`;
        root.append(svg("path", { d: area, fill: color, "fill-opacity": 0.1, stroke: "none" }));
      }
      root.append(svg("path", { class: "series-line", d: path, stroke: color }));
      const lastIndex = item.points.length - 1;
      root.append(svg("circle", { class: "series-dot", cx: xAt(lastIndex), cy: yAt(item.points[lastIndex]), r: 4, fill: color }));
      if (spec.endLabels !== false) {
        root.append(svg("text", { class: "end-label", x: xAt(lastIndex) + 9, y: yAt(item.points[lastIndex]) + 4 }, [item.label]));
      }
      if (series.length === 1) {
        const peak = item.points.indexOf(Math.max(...item.points));
        if (peak !== lastIndex && item.points[peak] > 0) {
          root.append(svg("circle", { class: "series-dot", cx: xAt(peak), cy: yAt(item.points[peak]), r: 4, fill: color }));
          root.append(svg("text", { class: "end-label", x: xAt(peak), y: yAt(item.points[peak]) - 9, "text-anchor": "middle" }, [String(item.points[peak])]));
        }
      }
    });

    const crosshair = svg("line", { class: "crosshair", y1: pad.top, y2: pad.top + plotHeight, x1: 0, x2: 0, opacity: 0 });
    root.append(crosshair);
    const overlay = svg("rect", { x: pad.left, y: pad.top, width: plotWidth, height: plotHeight, fill: "transparent" });
    root.append(overlay);
    overlay.addEventListener("pointermove", (event) => {
      const box = root.getBoundingClientRect();
      const ratio = (event.clientX - box.left - pad.left) / (plotWidth || 1);
      const index = Math.max(0, Math.min(dates.length - 1, Math.round(ratio * (dates.length - 1))));
      crosshair.setAttribute("x1", xAt(index));
      crosshair.setAttribute("x2", xAt(index));
      crosshair.setAttribute("opacity", 1);
      tooltip.show(event, () => el("div", {}, [
        el("div", { class: "tip-value", text: dates[index] }),
        ...series.map((item, order) => el("div", { class: "tip-row" }, [
          el("span", { class: "tip-key", style: { background: `var(--series-${order + 1})` } }),
          el("span", { class: "tip-value", text: String(item.points[index]) }),
          el("span", { class: "tip-label", text: item.label }),
        ])),
      ]));
    });
    overlay.addEventListener("pointerleave", () => {
      crosshair.setAttribute("opacity", 0);
      tooltip.hide();
    });
    container.append(root);

    if (series.length >= 2) {
      container.append(el("div", { class: "legend" }, series.map((item, order) =>
        el("div", { class: "legend-item" }, [
          el("span", { class: "legend-key", style: { background: `var(--series-${order + 1})` } }),
          item.label,
        ])
      )));
    }
  };
  draw();
  if (container._observer) container._observer.disconnect();
  let width = container.clientWidth;
  container._observer = new ResizeObserver(() => {
    if (Math.abs(container.clientWidth - width) > 24) {
      width = container.clientWidth;
      draw();
    }
  });
  container._observer.observe(container);
}

/* Sequential heatmap: one hue, 7 steps, near-zero recedes to the surface. */
function heatmap(container, matrix) {
  clear(container);
  if (!matrix.rows.length || !matrix.columns.length) {
    container.append(el("p", { class: "empty", text: "이 기간에는 축이 교차한 논문이 없습니다." }));
    return;
  }
  const cells = new Map(matrix.cells.map((cell) => [cell.row + " " + cell.column, cell.papers]));
  const head = el("tr", {}, [el("th", { text: "" })]);
  for (const column of matrix.columns) {
    head.append(el("th", { class: "col", text: column.label, title: `${column.label} (${column.axis})` }));
  }
  const body = el("tbody");
  for (const row of matrix.rows) {
    const tr = el("tr", {}, [el("th", { scope: "row", text: row })]);
    for (const column of matrix.columns) {
      const value = cells.get(row + " " + column.label) || 0;
      const step = value === 0 ? 0 : Math.max(1, Math.ceil((value / matrix.max) * 7));
      const cell = el("td", {
        class: value ? "filled" : "",
        style: step ? { background: `var(--seq-${step})`, color: `var(--seq-ink-${step})` } : {},
        text: value ? String(value) : "·",
      });
      attachTip(cell, () => tipContent(
        value + "편",
        `${row} × ${column.label}`,
        `${column.axis} 축`
      ));
      tr.append(cell);
    }
    body.append(tr);
  }
  const table = el("table", { class: "heatmap" }, [el("thead", {}, [head]), body]);
  container.append(el("div", { class: "heatmap-scroll" }, [table]));
  container.append(el("p", {
    class: "heat-axis-note",
    text: `세로: 도메인 · 가로: 방법론/과업 · 색이 진할수록 많음 (최대 ${matrix.max}편)`,
  }));
}

function statusPill(status) {
  const tone = { success: "good", partial: "warning", failed: "critical", running: "muted" }[status] || "muted";
  const icon = { success: "✓", partial: "!", failed: "✕", running: "…" }[status] || "·";
  return el("span", { class: "pill status" }, [
    el("span", { class: "pill-dot " + tone }),
    icon + " " + status,
  ]);
}

function tagPill(tag, axis) {
  const mark = { domains: "◆", methods: "▲", tasks: "■" }[axis] || "•";
  return el("span", { class: "pill", title: axis || "" }, [mark + " " + tag]);
}

// ------------------------------------------------------------------ 개요 뷰

async function renderOverview() {
  const kpi = document.getElementById("kpi-row");
  let data;
  try {
    data = await api("/api/overview", { days: 30 });
  } catch (error) {
    clear(kpi).append(errorBox(error.message));
    return;
  }
  document.getElementById("brand-meta").textContent = `${data.db_path} · 논문 ${data.counts.papers}건`;

  clear(kpi).append(
    tile("저장된 논문", data.counts.papers, `버전 레코드 ${data.counts.paper_versions}건`, true),
    tile("최근 30일 신규", data.recent_papers, "first_seen_at 기준"),
    tile("평균 점수", data.mean_score, `최고 ${data.best_score}`),
    tile("파이프라인 실행", data.counts.pipeline_runs, `소스 ${data.sources.length}종`),
    tile("분류한 논문", data.judged_papers, `LLM 요약 ${data.counts.summaries}건`)
  );

  document.getElementById("timeline-sub").textContent =
    `최근 ${data.days}일 · 하루에 처음 수집된 논문 수 (합계 ${data.recent_papers}편)`;
  lineChart(document.getElementById("chart-intake"), {
    dates: data.timeline.map((point) => point.date),
    series: [{ label: "신규", points: data.timeline.map((point) => point.papers) }],
    area: true,
    endLabels: false,
  });

  columnChart(document.getElementById("chart-histogram"),
    data.histogram.map((bucket) => ({ label: `${bucket.label}점`, tick: bucket.label, value: bucket.papers })),
    { unit: "편" });

  barChart(document.getElementById("chart-sources"),
    data.sources.map((source) => ({
      label: source.source,
      value: source.papers,
      note: `버전 레코드 ${source.versions}건`,
    })), { unit: "편" });

  const runs = data.run_stats.slice(-12);
  columnChart(document.getElementById("chart-runs"),
    runs.map((run) => ({
      label: `#${run.id} ${run.kind} · ${run.date || ""}`,
      tick: "#" + run.id,
      value: run.collected,
      note: `선별 ${run.relevant} · 신규 ${run.new} · 오류 ${run.source_errors}`,
    })), { unit: "건 수집" });

  document.getElementById("feedback-sub").textContent =
    `논문 탭에서 기록한 판정 · ${data.judged_papers}/${data.counts.papers}편 분류됨`;
  barChart(document.getElementById("chart-feedback"),
    data.feedback.map((entry) => {
      const verdict = VERDICT_BY_VALUE.get(entry.value);
      return {
        label: verdict ? `${verdict.icon} ${verdict.label}` : "미기록",
        value: entry.papers,
      };
    }), { unit: "편" });

  const table = el("table", { class: "data" }, [
    el("thead", {}, [el("tr", {}, [
      el("th", { text: "#" }), el("th", { text: "종류" }), el("th", { text: "상태" }),
      el("th", { text: "시작" }), el("th", { text: "수집" }), el("th", { text: "선별" }),
      el("th", { text: "신규" }), el("th", { text: "소스오류" }), el("th", { text: "메시지" }),
    ])]),
    el("tbody", {}, data.runs.map((run) => {
      const message = (run.error || "").split("\n")[0];
      return el("tr", {}, [
        el("td", { class: "num", text: run.id }),
        el("td", { text: run.kind }),
        el("td", {}, [statusPill(run.status)]),
        el("td", { class: "nowrap", text: dateLabel(run.started_at, true) }),
        el("td", { class: "num", text: run.stats.collected ?? "—" }),
        el("td", { class: "num", text: run.stats.relevant ?? "—" }),
        el("td", { class: "num", text: run.stats.new ?? "—" }),
        el("td", { class: "num", text: run.stats.source_errors ?? "—" }),
        el("td", { class: "clip", title: run.error || "", text: message || "—" }),
      ]);
    })),
  ]);
  clear(document.getElementById("table-runs")).append(table);
}

function tile(label, value, note, hero) {
  return el("div", { class: "tile" }, [
    el("div", { class: "tile-label", text: label }),
    el("div", { class: "tile-value" + (hero ? " hero" : ""), text: compact(value) }),
    el("div", { class: "tile-note", text: note }),
  ]);
}

// ------------------------------------------------------------------ 논문 뷰

const papersState = { offset: 0, limit: 50, selected: null, loaded: false, pending: null };

function paperQuery() {
  return {
    q: document.getElementById("f-search").value.trim(),
    source: document.getElementById("f-source").value,
    tag: document.getElementById("f-tag").value,
    days: document.getElementById("f-days").value,
    min_score: document.getElementById("f-score").value || 0,
    sort: document.getElementById("f-sort").value,
    feedback: document.getElementById("f-feedback").value,
    limit: papersState.limit,
    offset: papersState.offset,
  };
}

async function renderPapers() {
  if (!papersState.loaded) {
    papersState.loaded = true;
    try {
      const filters = await api("/api/filters");
      const sourceSelect = document.getElementById("f-source");
      for (const source of filters.sources) sourceSelect.append(el("option", { value: source, text: source }));
      const tagSelect = document.getElementById("f-tag");
      for (const tag of filters.tags) {
        tagSelect.append(el("option", { value: tag.tag, text: `${tag.tag} (${tag.papers})` }));
      }
    } catch (error) {
      console.warn("필터를 불러오지 못했습니다", error);
    }
    for (const id of ["f-search", "f-score"]) {
      document.getElementById(id).addEventListener("input", debounce(() => {
        papersState.offset = 0;
        loadPaperList();
      }, 250));
    }
    for (const id of ["f-source", "f-tag", "f-days", "f-sort", "f-feedback"]) {
      document.getElementById(id).addEventListener("change", () => {
        papersState.offset = 0;
        loadPaperList();
      });
    }
  }
  await loadPaperList();
  const pending = papersState.pending || papersState.selected;
  papersState.pending = null;
  if (pending) await selectPaper(pending);
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

async function loadPaperList() {
  const list = document.getElementById("paper-list");
  const count = document.getElementById("papers-count");
  let data;
  try {
    data = await api("/api/papers", paperQuery());
  } catch (error) {
    clear(list).append(errorBox(error.message));
    return;
  }
  const from = data.total === 0 ? 0 : data.offset + 1;
  count.textContent = `${data.total}편 중 ${from}–${data.offset + data.items.length}`;
  clear(list);
  if (!data.items.length) {
    list.append(el("p", { class: "empty", text: "조건에 맞는 논문이 없습니다." }));
  }
  for (const paper of data.items) {
    const item = el("button", {
      class: "paper-item",
      type: "button",
      dataset: { paperId: String(paper.id) },
      "aria-current": String(papersState.selected === paper.id),
      onclick: () => selectPaper(paper.id),
    }, [
      el("h3", { text: paper.title }),
      el("div", { class: "paper-item-meta" }, [
        el("span", { class: "score-chip", text: paper.score.toFixed(1) }),
        ...paper.sources.map((source) => el("span", { class: "pill", text: source })),
        verdictPill(paper.feedback),
        paper.code_url
          ? el("span", { class: "pill attr code", title: "코드 공개 (점수 +보너스)" }, ["⌘ 코드"])
          : null,
        paper.is_survey
          ? el("span", { class: "pill attr survey", title: "제목에 survey/review 포함 (점수 +보너스)" }, ["❋ survey"])
          : null,
        ...["domains", "methods", "tasks"].flatMap((axis) =>
          paper.tags[axis].map((tag) => tagPill(tag, axis))),
        paper.has_summary ? el("span", { class: "pill", text: "요약" }) : null,
        el("span", {
          class: "pill",
          title: "발행일 · 수집일 " + dateLabel(paper.first_seen_at, true),
          text: paper.published_at ? dateLabel(paper.published_at) : "발행일 미상",
        }),
      ]),
    ]);
    list.append(item);
  }

  const pager = clear(document.getElementById("paper-pager"));
  const previous = el("button", {
    class: "ghost", type: "button", disabled: data.offset === 0,
    onclick: () => { papersState.offset = Math.max(0, data.offset - data.limit); loadPaperList(); },
  }, ["← 이전"]);
  const next = el("button", {
    class: "ghost", type: "button", disabled: data.offset + data.limit >= data.total,
    onclick: () => { papersState.offset = data.offset + data.limit; loadPaperList(); },
  }, ["다음 →"]);
  pager.append(previous, next);
}

async function selectPaper(paperId) {
  papersState.selected = paperId;
  history.replaceState(null, "", "#papers/" + paperId);
  for (const item of document.querySelectorAll("#paper-list .paper-item")) {
    item.setAttribute("aria-current", "false");
  }
  const panel = document.getElementById("paper-detail");
  clear(panel).append(el("p", { class: "empty", text: "불러오는 중…" }));
  let paper;
  try {
    paper = await api(`/api/papers/${paperId}`);
  } catch (error) {
    clear(panel).append(errorBox(error.message));
    return;
  }
  clear(panel).append(renderPaperDetail(paper));
  const listed = document.querySelector(`#paper-list .paper-item[data-paper-id="${paperId}"]`);
  if (listed) listed.setAttribute("aria-current", "true");
  panel.scrollTop = 0;
}

/* The one place the dashboard writes. Verdicts append to `feedback`; nothing
   else in the database is touched. */
function feedbackSection(paper) {
  const section = el("section", { class: "detail-section" }, [el("h3", { text: "피드백" })]);
  const note = el("p", { class: "verdict-note" });
  const row = el("div", { class: "verdicts" });

  const paint = (current, history) => {
    for (const button of row.querySelectorAll(".verdict[data-value]")) {
      button.setAttribute("aria-pressed", String(button.dataset.value === current));
    }
    clearButton.hidden = !current;
    note.classList.remove("error");
    note.textContent = history && history.length
      ? `최근 기록 ${dateLabel(history[0].created_at, true)} · 총 ${history.length}회`
      : "아직 기록하지 않았습니다.";
  };

  const send = async (value) => {
    const buttons = [...row.querySelectorAll("button")];
    for (const button of buttons) button.disabled = true;
    note.classList.remove("error");
    note.textContent = "저장 중…";
    try {
      const result = await apiPost(`/api/papers/${paper.id}/feedback`, { value });
      paper.feedback = result.feedback;
      paper.feedback_history = result.feedback_history;
      paint(result.feedback, result.feedback_history);
      const listed = document.querySelector(`#paper-list .paper-item[data-paper-id="${paper.id}"]`);
      if (listed) refreshListPill(listed, result.feedback);
    } catch (error) {
      note.classList.add("error");
      note.textContent = "저장하지 못했습니다: " + error.message;
    } finally {
      for (const button of buttons) button.disabled = false;
    }
  };

  for (const verdict of VERDICTS) {
    row.append(el("button", {
      class: "verdict " + verdict.value,
      type: "button",
      dataset: { value: verdict.value },
      "aria-pressed": "false",
      onclick: () => send(verdict.value),
    }, [el("span", { class: "pill-dot" }), verdict.icon + " " + verdict.label]));
  }
  const clearButton = el("button", {
    class: "verdict clear", type: "button", hidden: true,
    onclick: () => send(null),
  }, ["기록 지우기"]);
  row.append(clearButton);

  section.append(row, note);
  paint(paper.feedback, paper.feedback_history);
  return section;
}

function refreshListPill(item, value) {
  const meta = item.querySelector(".paper-item-meta");
  const existing = meta.querySelector(".pill.status");
  if (existing) existing.remove();
  const pill = verdictPill(value);
  if (pill) meta.insertBefore(pill, meta.children[1] || null);
}

function renderPaperDetail(paper) {
  const breakdown = paper.breakdown;
  const fragment = document.createDocumentFragment();

  fragment.append(el("h2", { class: "detail-title", text: paper.title }));
  fragment.append(el("div", { class: "detail-meta" }, [
    el("span", { text: paper.authors.slice(0, 4).join(", ") + (paper.authors.length > 4 ? " 외" : "") || "저자 미상" }),
    paper.venue ? el("span", { text: paper.venue }) : null,
    el("span", { text: "발행 " + dateLabel(paper.published_at) }),
    el("span", { text: "수집 " + dateLabel(paper.first_seen_at, true) }),
    paper.citation_count !== null ? el("span", { text: `인용 ${paper.citation_count}` }) : null,
  ]));
  const links = el("div", { class: "links" }, [
    paper.url ? el("a", { href: paper.url, target: "_blank", rel: "noreferrer", text: "원문 ↗" }) : null,
    paper.pdf_url ? el("a", { href: paper.pdf_url, target: "_blank", rel: "noreferrer", text: "PDF ↗" }) : null,
    paper.code_url ? el("a", { href: paper.code_url, target: "_blank", rel: "noreferrer", text: "코드 ↗" }) : null,
    paper.doi ? el("span", {}, [el("code", { text: "doi:" + paper.doi })]) : null,
    paper.arxiv_id ? el("span", {}, [el("code", { text: "arXiv:" + paper.arxiv_id })]) : null,
  ]);
  fragment.append(links);
  fragment.append(feedbackSection(paper));

  // 점수 책정 과정
  const ledgerRows = breakdown.matches.map((match) => el("tr", {}, [
    el("td", {}, [
      el("div", {}, [tagPill(match.tag, match.axis)]),
      el("div", { class: "terms-cell" }, match.terms.map((term) =>
        el("span", { class: "term" + (match.title_terms.includes(term) ? " in-title" : ""), text: term }))),
    ]),
    el("td", { class: "num", text: match.weight.toFixed(1) }),
    el("td", { class: "num", text: "×" + match.multiplier.toFixed(2) }),
    el("td", { class: "num", text: "+" + match.contribution.toFixed(1) }),
  ]));
  for (const bonus of breakdown.bonuses) {
    ledgerRows.push(el("tr", { class: "bonus" }, [
      el("td", { text: bonus.name }),
      el("td", { class: "num", text: "—" }),
      el("td", { class: "num", text: "—" }),
      el("td", { class: "num", text: "+" + bonus.amount.toFixed(1) }),
    ]));
  }
  ledgerRows.push(el("tr", { class: "total" }, [
    el("td", { text: breakdown.capped ? `합계 (${breakdown.raw_score.toFixed(1)} → 상한 ${breakdown.max_score})` : "합계" }),
    el("td", { class: "num", text: "" }),
    el("td", { class: "num", text: "" }),
    el("td", { class: "num", text: breakdown.score.toFixed(1) }),
  ]));

  const scoreSection = el("section", { class: "detail-section" }, [
    el("h3", { text: "점수 책정 과정" }),
    el("table", { class: "ledger" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { text: "매칭된 태그 · 용어" }),
        el("th", { text: "가중치" }),
        el("th", { text: "제목 배수" }),
        el("th", { text: "기여" }),
      ])]),
      el("tbody", {}, ledgerRows),
    ]),
    el("div", { class: "meter" }, [
      el("div", { class: "meter-fill", style: { width: Math.min(100, (breakdown.score / breakdown.max_score) * 100) + "%" } }),
    ]),
    el("p", {
      class: "note",
      text: `선별 기준 ${breakdown.minimum_relevant}점 · 상한 ${breakdown.max_score}점 · `
        + "강조된 용어는 제목에서도 발견되어 제목 배수가 곱해진 항목입니다.",
    }),
  ]);
  if (!breakdown.matches_stored) {
    scoreSection.append(el("p", {
      class: "note warn",
      text: `DB에 저장된 점수는 ${breakdown.stored_score.toFixed(1)}점입니다. 현재 keywords.yml로 다시 계산하면 ${breakdown.score.toFixed(1)}점 — 설정이 바뀐 뒤 아직 재수집되지 않았습니다.`,
    }));
  }
  fragment.append(scoreSection);

  if (paper.summary) {
    const fields = paper.summary.fields;
    fragment.append(el("section", { class: "detail-section" }, [
      el("h3", { text: `LLM 요약 · ${paper.summary.model}` }),
      ...Object.entries({
        paper: "논문", problem: "문제", method: "방법",
        benchmark: "벤치마크", why_it_matters: "의의", can_i_use_it: "활용",
      }).map(([key, label]) => el("p", { class: "detail-abstract" }, [
        el("strong", { text: label + ": " }), fields[key] || "—",
      ])),
    ]));
  }

  if (paper.abstract) {
    fragment.append(el("section", { class: "detail-section" }, [
      el("h3", { text: "초록" }),
      el("p", { class: "detail-abstract", text: paper.abstract }),
    ]));
  }

  fragment.append(el("section", { class: "detail-section" }, [
    el("h3", { text: "이 논문을 찾은 쿼리" }),
    paper.queries.length
      ? el("ul", {}, paper.queries.map((query) => el("li", { class: "detail-abstract" }, [
          el("code", { text: query.text }), ` — ${query.runs}회 실행에서 매칭`,
        ])))
      : el("p", { class: "note", text: "쿼리 기록이 없습니다." }),
  ]));

  fragment.append(el("section", { class: "detail-section" }, [
    el("h3", { text: "소스 버전" }),
    el("table", { class: "data" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { text: "소스" }), el("th", { text: "소스 ID" }), el("th", { text: "최근 확인" }),
      ])]),
      el("tbody", {}, paper.versions.map((version) => el("tr", {}, [
        el("td", {}, [el("a", { href: version.url, target: "_blank", rel: "noreferrer", text: version.source })]),
        el("td", {}, [el("code", { text: version.source_id })]),
        el("td", { class: "nowrap", text: dateLabel(version.seen_at, true) }),
      ]))),
    ]),
  ]));

  fragment.append(el("section", { class: "detail-section" }, [
    el("h3", { text: "수집 실행 기록" }),
    el("table", { class: "data" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { text: "실행" }), el("th", { text: "종류" }), el("th", { text: "상태" }), el("th", { text: "시각" }),
      ])]),
      el("tbody", {}, paper.runs.map((run) => el("tr", {}, [
        el("td", { class: "num", text: "#" + run.id }),
        el("td", { text: run.kind }),
        el("td", {}, [statusPill(run.status)]),
        el("td", { class: "nowrap", text: dateLabel(run.started_at, true) }),
      ]))),
    ]),
  ]));

  return fragment;
}

// ------------------------------------------------------------------ 쿼리 뷰

const QUERY_GROUPS = ["daily", "weekly"];
const queriesState = { data: null, draft: null, token: "", bound: false };

async function renderQueries() {
  if (!queriesState.bound) {
    queriesState.bound = true;
    document.getElementById("q-save").addEventListener("click", saveQueries);
    document.getElementById("q-revert").addEventListener("click", () => {
      queriesState.draft = structuredClone(queriesState.data.editable);
      paintEditor();
    });
  }
  let data;
  try {
    data = await api("/api/queries");
  } catch (error) {
    clear(document.getElementById("chart-queries")).append(errorBox(error.message));
    return;
  }
  queriesState.data = data;
  queriesState.token = data.token;
  queriesState.draft = structuredClone(data.editable);
  paintEditor();
  paintQueryStats(data);
}

/* Yield counts come from the database, so a config save repaints these without
   touching the editor's draft or its status line. */
function paintQueryStats(data) {
  document.getElementById("queries-sub").textContent =
    `${data.config_path} · 쿼리 ${data.items.length}개 · 그룹 ${data.groups.join(", ")}`;

  barChart(document.getElementById("chart-queries"),
    data.items.map((item) => ({
      label: item.text,
      value: item.papers,
      note: `${item.group} · 평균 ${item.mean_score}점 · ${item.runs}회 실행`,
    })), { unit: "편" });

}

/* ---- 쿼리 편집: the draft lives in memory until 저장 writes keywords.yml ---- */

function draftIsDirty() {
  return JSON.stringify(queriesState.draft) !== JSON.stringify(queriesState.data.editable);
}

function paintEditor() {
  const container = clear(document.getElementById("query-editor"));
  for (const group of QUERY_GROUPS) {
    const entries = queriesState.draft[group] || [];
    const rows = el("div", {});
    entries.forEach((text, index) => {
      const input = el("input", {
        type: "text", value: text, spellcheck: "false",
        "aria-label": `${group} 쿼리 ${index + 1}`,
        oninput: (event) => {
          queriesState.draft[group][index] = event.target.value;
          updateEditorState();
        },
      });
      rows.append(el("div", { class: "query-row" }, [
        input,
        el("button", {
          class: "icon-button", type: "button", title: "이 쿼리 삭제",
          onclick: () => {
            queriesState.draft[group].splice(index, 1);
            paintEditor();
          },
        }, ["✕"]),
      ]));
    });
    if (!entries.length) {
      rows.append(el("p", { class: "note", text: "이 그룹에는 쿼리가 없습니다." }));
    }
    container.append(el("div", { class: "query-group" }, [
      el("h3", { text: `${group} (${entries.length})` }),
      rows,
      el("button", {
        class: "ghost query-add", type: "button",
        onclick: () => {
          queriesState.draft[group].push("");
          paintEditor();
          const inputs = container.querySelectorAll(".query-group input");
          if (inputs.length) inputs[inputs.length - 1].focus();
        },
      }, ["+ 쿼리 추가"]),
    ]));
  }
  updateEditorState();
}

function updateEditorState() {
  const dirty = draftIsDirty();
  document.getElementById("q-save").disabled = !dirty;
  document.getElementById("q-revert").disabled = !dirty;
  const note = document.getElementById("editor-note");
  note.classList.remove("error");
  note.textContent = dirty
    ? "저장하지 않은 변경이 있습니다. 저장하면 keywords.yml의 queries 블록만 다시 씁니다."
    : "keywords.yml과 동일합니다.";
  document.getElementById("editor-sub").textContent =
    `${queriesState.data.config_path} · daily ${queriesState.draft.daily.length}개 · `
    + `weekly ${queriesState.draft.weekly.length}개`;
}

async function saveQueries() {
  const note = document.getElementById("editor-note");
  const buttons = [document.getElementById("q-save"), document.getElementById("q-revert")];
  for (const button of buttons) button.disabled = true;
  note.classList.remove("error");
  note.textContent = "저장 중…";
  try {
    const result = await apiPost("/api/queries", {
      token: queriesState.token,
      queries: queriesState.draft,
    });
    queriesState.data = result;
    queriesState.token = result.token;
    queriesState.draft = structuredClone(result.editable);
    paintEditor();
    note.textContent = "keywords.yml에 저장했습니다. 다음 수집부터 적용됩니다.";
    paintQueryStats(result);
  } catch (error) {
    note.classList.add("error");
    note.textContent = "저장하지 못했습니다: " + error.message;
    for (const button of buttons) button.disabled = false;
  }
}

// -------------------------------------------------------------------- 태그 뷰

const AXES = [
  { axis: "methods", label: "방법론 (methods)", glyph: "▲" },
  { axis: "domains", label: "도메인 (domains)", glyph: "◆" },
  { axis: "tasks", label: "과업 (tasks)", glyph: "■" },
];
const tagsState = { data: null, draft: null, token: "", bound: false };

async function renderTags() {
  if (!tagsState.bound) {
    tagsState.bound = true;
    document.getElementById("t-save").addEventListener("click", saveTags);
    document.getElementById("t-revert").addEventListener("click", () => {
      tagsState.draft = structuredClone(tagsState.data.draft);
      paintTagEditor();
    });
  }
  let data;
  try {
    data = await api("/api/scoring");
  } catch (error) {
    clear(document.getElementById("tag-editor")).append(errorBox(error.message));
    return;
  }
  // `draft` is the editable shape; `axes` also carries the read-only DB counts.
  data.draft = Object.fromEntries(
    data.axes.map((entry) => [
      entry.axis,
      entry.editable.map((tag) => ({
        tag: tag.tag, weight: tag.weight, terms: [...tag.terms],
      })),
    ])
  );
  data.papersByTag = Object.fromEntries(
    data.axes.flatMap((entry) => entry.editable.map((tag) => [tag.tag, tag.papers]))
  );
  tagsState.data = data;
  tagsState.token = data.token;
  tagsState.draft = structuredClone(data.draft);
  paintScoringRules(data);
  paintTagEditor();
}

function paintScoringRules(data) {
  const labels = {
    minimum_relevant: "선별 최소 점수", title_multiplier: "제목 매칭 배수",
    cross_axis_bonus: "축 교차 보너스", code_bonus: "코드 공개 보너스",
    survey_bonus: "서베이/리뷰 보너스", max_score: "점수 상한",
  };
  clear(document.getElementById("scoring-formula")).append(
    ...Object.entries(data.params).map(([key, value]) => el("div", { class: "formula-item" }, [
      el("b", { text: String(value) }),
      el("span", { text: labels[key] || key }),
    ]))
  );
}

function tagsAreDirty() {
  return JSON.stringify(tagsState.draft) !== JSON.stringify(tagsState.data.draft);
}

function paintTagEditor() {
  const container = clear(document.getElementById("tag-editor"));
  for (const { axis, label, glyph } of AXES) {
    const tags = tagsState.draft[axis] || [];
    const list = el("div", {});
    tags.forEach((tag, index) => {
      list.append(tagRow(axis, tag, index));
    });
    if (!tags.length) {
      list.append(el("p", { class: "note", text: "이 축에는 태그가 없습니다." }));
    }
    container.append(el("div", { class: "tag-axis" }, [
      el("h3", {}, [`${glyph} ${label}`, el("span", { class: "axis-count", text: ` ${tags.length}개` })]),
      list,
      el("button", {
        class: "ghost query-add", type: "button",
        onclick: () => {
          tagsState.draft[axis].push({ tag: "", weight: 10, terms: [] });
          paintTagEditor();
          const inputs = container.querySelectorAll(`[data-axis="${axis}"] .tag-name`);
          if (inputs.length) inputs[inputs.length - 1].focus();
        },
      }, ["+ 태그 추가"]),
    ]));
  }
  updateTagEditorState();
}

function tagRow(axis, tag, index) {
  const papers = tagsState.data.papersByTag[tag.tag];
  const terms = el("div", { class: "term-chips" });
  tag.terms.forEach((term, termIndex) => {
    terms.append(el("span", { class: "term-chip" }, [
      el("span", { text: term }),
      el("button", {
        class: "chip-remove", type: "button", title: `${term} 삭제`,
        onclick: () => {
          tagsState.draft[axis][index].terms.splice(termIndex, 1);
          paintTagEditor();
        },
      }, ["×"]),
    ]));
  });

  const addTerm = (event) => {
    const input = event.target;
    const value = input.value.trim();
    if (!value) return;
    const existing = tagsState.draft[axis][index].terms;
    if (existing.some((term) => term.toLowerCase() === value.toLowerCase())) {
      input.value = "";
      return;
    }
    existing.push(value);
    input.value = "";
    paintTagEditor();
    const focus = document.querySelector(
      `[data-axis="${axis}"][data-index="${index}"] .term-input`);
    if (focus) focus.focus();
  };
  terms.append(el("input", {
    class: "term-input", type: "text", placeholder: "+ 용어 추가", spellcheck: "false",
    "aria-label": `${tag.tag || "새 태그"} 용어 추가`,
    onkeydown: (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      addTerm(event);
    },
    onblur: addTerm,
  }));

  return el("div", { class: "tag-row", dataset: { axis, index: String(index) } }, [
    el("div", { class: "tag-row-head" }, [
      el("input", {
        class: "tag-name", type: "text", value: tag.tag, placeholder: "tag_name",
        spellcheck: "false", "aria-label": "태그 이름",
        oninput: (event) => {
          tagsState.draft[axis][index].tag = event.target.value;
          updateTagEditorState();
        },
      }),
      el("label", { class: "weight-field" }, [
        el("span", { text: "가중치" }),
        el("input", {
          type: "number", value: tag.weight, min: "0", max: "100", step: "1",
          "aria-label": "가중치",
          oninput: (event) => {
            tagsState.draft[axis][index].weight = Number(event.target.value);
            updateTagEditorState();
          },
        }),
      ]),
      papers ? el("span", { class: "pill", text: `논문 ${papers}편` }) : null,
      el("button", {
        class: "icon-button", type: "button", title: "이 태그 삭제",
        onclick: () => {
          tagsState.draft[axis].splice(index, 1);
          paintTagEditor();
        },
      }, ["✕"]),
    ]),
    terms,
  ]);
}

function updateTagEditorState() {
  const dirty = tagsAreDirty();
  document.getElementById("t-save").disabled = !dirty;
  document.getElementById("t-revert").disabled = !dirty;
  const note = document.getElementById("tag-editor-note");
  note.classList.remove("error");
  note.textContent = dirty
    ? "저장하지 않은 변경이 있습니다. 저장하면 keywords.yml의 methods·domains·tasks 중 바뀐 블록만 다시 씁니다."
    : "keywords.yml과 동일합니다.";
  const total = AXES.reduce((sum, { axis }) => sum + (tagsState.draft[axis] || []).length, 0);
  document.getElementById("tag-editor-sub").textContent =
    `${tagsState.data.config_path} · 태그 ${total}개`;
}

async function saveTags() {
  const note = document.getElementById("tag-editor-note");
  const buttons = [document.getElementById("t-save"), document.getElementById("t-revert")];
  for (const button of buttons) button.disabled = true;
  note.classList.remove("error");
  note.textContent = "저장 중…";
  try {
    const result = await apiPost("/api/tags", {
      token: tagsState.token,
      axes: tagsState.draft,
    });
    result.draft = Object.fromEntries(
      result.axes.map((entry) => [
        entry.axis,
        entry.editable.map((tag) => ({
          tag: tag.tag, weight: tag.weight, terms: [...tag.terms],
        })),
      ])
    );
    result.papersByTag = Object.fromEntries(
      result.axes.flatMap((entry) => entry.editable.map((tag) => [tag.tag, tag.papers]))
    );
    tagsState.data = result;
    tagsState.token = result.token;
    tagsState.draft = structuredClone(result.draft);
    paintScoringRules(result);
    paintTagEditor();
    note.textContent =
      "keywords.yml에 저장했습니다. 이미 저장된 논문의 점수는 다음 수집 때 다시 계산됩니다.";
  } catch (error) {
    note.classList.add("error");
    note.textContent = "저장하지 못했습니다: " + error.message;
    for (const button of buttons) button.disabled = false;
  }
}

// ------------------------------------------------------------------ 트렌드 뷰

let trendsBound = false;

async function renderTrends() {
  if (!trendsBound) {
    trendsBound = true;
    document.getElementById("t-days").addEventListener("change", renderTrends);
  }
  const days = Number(document.getElementById("t-days").value);
  let data;
  try {
    data = await api("/api/trends", { days });
  } catch (error) {
    clear(document.getElementById("trend-matrix")).append(errorBox(error.message));
    return;
  }
  document.getElementById("trend-note").textContent =
    `최근 ${data.days}일 · 논문 ${data.papers}편 (수집일 기준)`;

  heatmap(document.getElementById("trend-matrix"), data.matrix);

  barChart(document.getElementById("chart-tags"),
    data.tags.map((tag) => ({ label: tag.tag, value: tag.papers, note: tag.axis })), { unit: "편" });

  barChart(document.getElementById("chart-pairs"),
    data.pairs.map((pair) => ({ label: pair.pair, value: pair.papers })),
    { unit: "편", empty: "반복된 조합이 아직 없습니다." });

  const series = data.timeline.series;
  document.getElementById("trend-series-sub").textContent = series.length
    ? `가장 많이 등장한 도메인 ${series.length}개의 일자별 신규 논문 수`
    : "도메인 태그가 붙은 논문이 아직 없습니다.";
  lineChart(document.getElementById("chart-trend-series"), {
    dates: data.timeline.dates,
    series,
  });

  clear(document.getElementById("table-trend-papers")).append(
    el("table", { class: "data" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { text: "점수" }), el("th", { text: "제목" }), el("th", { text: "" }),
      ])]),
      el("tbody", {}, data.top_papers.map((paper) => el("tr", {}, [
        el("td", { class: "num", text: paper.score.toFixed(1) }),
        el("td", { class: "wrap", text: paper.title }),
        el("td", {}, [el("a", { href: paper.url, target: "_blank", rel: "noreferrer", text: "열기 ↗" })]),
      ]))),
    ])
  );
}

// ------------------------------------------------------------------ 리포트 뷰

/* The report list is small and fully fetched, so the period toggle filters the
   cached items rather than making another round trip. */
const reportsState = { data: null, bound: false };

async function renderReports() {
  const list = document.getElementById("report-list");
  if (!reportsState.bound) {
    reportsState.bound = true;
    document.getElementById("r-period").addEventListener("change", paintReports);
  }
  try {
    reportsState.data = await api("/api/reports");
  } catch (error) {
    clear(list).append(errorBox(error.message));
    return;
  }
  await paintReports();
}

async function paintReports() {
  const data = reportsState.data;
  if (!data) return;
  const list = clear(document.getElementById("report-list"));
  const period = document.getElementById("r-period").value;
  const items = period ? data.items.filter((report) => report.period === period) : data.items;

  const counts = new Map(data.periods.map((entry) => [entry.period, entry.reports]));
  document.getElementById("reports-sub").textContent =
    `${data.output_dir} · ${items.length}개`;
  document.getElementById("reports-note").textContent =
    ["daily", "weekly", "monthly"]
      .map((name) => `${name} ${counts.get(name) || 0}`)
      .join(" · ") + (counts.get("other") ? ` · 기타 ${counts.get("other")}` : "");

  if (!items.length) {
    list.append(el("p", {
      class: "empty",
      text: period ? `${period} 리포트가 아직 없습니다.` : "생성된 리포트가 없습니다.",
    }));
    clear(document.getElementById("report-detail")).append(
      el("p", { class: "empty", text: "표시할 리포트가 없습니다." })
    );
    return;
  }

  items.forEach((report, order) => {
    list.append(el("button", {
      class: "paper-item", type: "button",
      "aria-current": String(order === 0),
      onclick: (event) => {
        for (const node of document.querySelectorAll("#report-list .paper-item")) {
          node.setAttribute("aria-current", "false");
        }
        event.currentTarget.setAttribute("aria-current", "true");
        loadReport(report.kind, report.name);
      },
    }, [
      el("h3", { text: `${report.kind} / ${report.name}` }),
      el("div", { class: "paper-item-meta" }, [
        el("span", { class: "pill", text: report.period }),
        el("span", { class: "pill", text: (report.size / 1024).toFixed(1) + " KB" }),
        el("span", { class: "pill", text: dateLabel(report.modified_at, true) }),
      ]),
    ]));
  });
  const newest = items[0];
  await loadReport(newest.kind, newest.name);
}

async function loadReport(kind, name) {
  const panel = document.getElementById("report-detail");
  clear(panel).append(el("p", { class: "empty", text: "불러오는 중…" }));
  try {
    const data = await api(`/api/reports/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`);
    clear(panel).append(renderMarkdown(data.content));
  } catch (error) {
    clear(panel).append(errorBox(error.message));
  }
}

/* Minimal renderer for the pipeline's own Markdown: headings, bullets, links,
   bold. Every fragment is inserted as a text node. */
function renderMarkdown(text) {
  const root = el("div", { class: "md" });
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trimEnd();
    if (!line) continue;
    if (line.startsWith("> ")) root.append(el("div", { class: "md-h" }, inline(line.slice(2))));
    else if (line.startsWith("- ")) root.append(el("div", { class: "md-li" }, inline(line.slice(2))));
    else root.append(el("p", {}, inline(line)));
  }
  return root;
}

function inline(text) {
  const nodes = [];
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|\*\*([^*]+)\*\*/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    if (match[1]) {
      nodes.push(el("a", { href: match[2], target: "_blank", rel: "noreferrer", text: match[1] }));
    } else {
      nodes.push(el("strong", { text: match[3] }));
    }
    cursor = pattern.lastIndex;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

// ------------------------------------------------------------------- 라우팅

const VIEWS = {
  overview: renderOverview,
  papers: renderPapers,
  queries: renderQueries,
  tags: renderTags,
  trends: renderTrends,
  reports: renderReports,
};

let currentView = "overview";

/* Hash routes are `#view` or `#papers/<id>` so a paper stays bookmarkable. */
function parseHash() {
  const [name, arg] = window.location.hash.slice(1).split("/");
  // The scoring tab became 태그; keep old links working.
  return { name: name === "scoring" ? "tags" : name || "overview", arg };
}

function showView(name, arg) {
  if (!VIEWS[name]) name = "overview";
  currentView = name;
  if (name === "papers" && arg) papersState.pending = Number(arg);
  for (const tab of document.querySelectorAll(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.view === name));
  }
  for (const view of document.querySelectorAll(".view")) {
    view.hidden = view.id !== "view-" + name;
  }
  if (parseHash().name !== name) window.location.hash = name;
  VIEWS[name]().catch((error) => console.error(error));
}

document.getElementById("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (tab) showView(tab.dataset.view);
});
window.addEventListener("hashchange", () => {
  const route = parseHash();
  showView(route.name, route.arg);
});
document.getElementById("refresh").addEventListener("click", () => showView(currentView));

const themeToggle = document.getElementById("theme-toggle");
const savedTheme = localStorage.getItem("radar-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
themeToggle.addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("radar-theme", next);
  showView(currentView);
});

const bootRoute = parseHash();
showView(bootRoute.name, bootRoute.arg);

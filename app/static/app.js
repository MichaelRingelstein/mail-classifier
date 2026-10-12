const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const fmtS = (s) => (s < 10 ? s.toFixed(1) : s.toFixed(0)) + " s";
const IMP_ORDER = { high: 0, normal: 1, low: 2 };

const S = {
  cats: {}, review: "to_review", rows: new Map(), running: false,
  filter: "all", sort: { key: "date", dir: -1 }, fresh: new Set(), dirty: false,
  p1: null, p2: null, lat: [], tokens: 0, agreement: null,
  tab: "mails", stale: false, senders: [],
};

const catLabel = (k) => (k === S.review ? "To review" : S.cats[k] || k);

// ---- table -----------------------------------------------------------------
function viewOf(r) { return r.classified ? r.view : "pending"; }

function filtered() {
  const f = S.filter;
  let rows = [...S.rows.values()];
  if (f === "pending") rows = rows.filter((r) => !r.classified);
  else if (f !== "all") rows = rows.filter((r) => r.view === f);
  const { key, dir } = S.sort;
  const val = (r) => key === "from" ? r.from.toLowerCase() : key === "category" ? (r.view || "~") : key === "importance" ? (IMP_ORDER[r.importance] ?? 9) : r[key] ?? -1;
  return rows.sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
}

function pc(v, low) {
  if (v == null) return el("span", "wait", "…");
  const s = el("span", "pc" + (low ? " low" : ""));
  s.append(el("span", "", Math.round(v * 100) + "%"));
  const bar = el("i"); bar.style.setProperty("--h", Math.round(v * 100) + "%"); s.append(bar);
  return s;
}

function rowNode(r) {
  const tr = el("tr", S.fresh.has(r.id) ? "fresh" : "");
  tr.onclick = () => openDetail(r.id);
  const m = el("td", "mail");
  m.append(el("span", "dot", "•"), el("span", "from", r.from), document.createTextNode(r.subject));
  m.title = `${r.from} — ${r.subject}`;
  tr.append(m);

  const c = el("td");
  if (r.error) c.append(el("span", "cat discard", "Error"), el("span", "flag", "⚠"));
  else if (!r.classified) c.append(el("span", "wait" + (S.running ? " spin" : ""), S.running ? "in progress…" : "—"));
  else {
    c.append(el("span", "cat " + r.view, catLabel(r.view)));
    if (r.needs_review && !r.corrected) { const f = el("span", "flag review", "⚠"); f.title = "Low confidence or instructions detected"; c.append(f); }
    if (r.corrected) { const f = el("span", "flag fix", "✎"); f.title = "Corrected by you"; c.append(f); }
    if (r.rule && !r.corrected) { const f = el("span", "flag rule", r.rule.startsWith("expired") ? "expired" : r.rule.replace("sender: ", "")); f.title = r.rule; c.append(f); }
  }
  tr.append(c);

  const i = el("td");
  i.append(r.importance ? el("span", "imp " + r.importance, r.importance[0].toUpperCase() + r.importance.slice(1)) : el("span", "wait", "…"));
  tr.append(i);
  const sp = el("td", "num"); sp.append(pc(r.spam, true)); tr.append(sp);
  const re = el("td", "num"); re.append(pc(r.action_required, false)); tr.append(re);
  tr.append(el("td", "num", r.latency_ms != null ? Math.round(r.latency_ms) + " ms" : "…"));
  return tr;
}

function render() {
  S.dirty = false;
  const rows = filtered();
  $("rows").replaceChildren(...rows.map(rowNode));
  $("empty").hidden = S.rows.size > 0;
  document.querySelectorAll("#mails th").forEach((th) => {
    const on = th.dataset.sort === S.sort.key;
    th.classList.toggle("sorted", on); th.classList.toggle("asc", on && S.sort.dir === 1);
  });
  renderFilters();
}
function schedule() { if (!S.dirty) { S.dirty = true; requestAnimationFrame(render); } }

function renderFilters() {
  const counts = { all: S.rows.size, pending: 0 };
  for (const k of [...Object.keys(S.cats), S.review]) counts[k] = 0;
  for (const r of S.rows.values()) { if (!r.classified) counts.pending++; else counts[r.view] = (counts[r.view] || 0) + 1; }
  const items = [["all", "All"], ...Object.keys(S.cats).map((k) => [k, S.cats[k]]), [S.review, "To review"], ["pending", "Unclassified"]];
  $("filters").replaceChildren(...items.filter(([k]) => k !== "pending" || counts.pending).map(([k, label]) => {
    const b = el("button", "pill" + (S.filter === k ? " on" : ""), label);
    b.append(el("span", "n", counts[k] ?? 0));
    b.onclick = () => { S.filter = k; schedule(); };
    return b;
  }));
}

document.querySelectorAll("#mails th").forEach((th) => th.onclick = () => {
  const k = th.dataset.sort;
  S.sort = S.sort.key === k ? { key: k, dir: -S.sort.dir } : { key: k, dir: 1 };
  schedule();
});

function upsert(c, fresh) {
  S.rows.set(c.id, c);
  if (fresh) { S.fresh.add(c.id); setTimeout(() => S.fresh.delete(c.id), 1300); }
  schedule();
}

async function refreshApply() {
  try {
    const p = await (await fetch("/api/apply/preview")).json();
    $("apply").textContent = `Apply (${p.count})`;
    $("apply").disabled = p.count === 0 || S.running;
    $("apply").title = S.running ? "Available when the current classification ends" : p.count === 0 ? "Nothing to apply" : "";
  } catch {}
}

// ---- live run --------------------------------------------------------
// Fetch and classify are separate jobs: each one only resets its own step line.
function resetRun(job) {
  if (job === "fetch") { S.p1 = null; $("t1").textContent = "–"; $("i1").textContent = "Waiting"; $("error").hidden = true; $("state").textContent = "Running…"; return; }
  S.p2 = null; S.lat = []; S.tokens = 0;
  $("t2").textContent = "–"; $("i2").textContent = "Waiting";
  $("b2").style.width = "0"; $("done").textContent = "0"; $("of").textContent = "of 0"; $("pct").textContent = "0%"; $("remain").textContent = " ";
  $("error").hidden = true; $("state").textContent = "Running…";
  for (const k of ["mean", "p50", "p95", "rate", "tok", "cost"]) $("k-" + k).textContent = "–";
  $("spark").replaceChildren();
}

const pctile = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.round(p * (s.length - 1)))]; };

function liveKpis(elapsed) {
  if (!S.lat.length) return;
  $("k-mean").textContent = Math.round(S.lat.reduce((a, b) => a + b, 0) / S.lat.length) + " ms";
  $("k-p50").textContent = Math.round(pctile(S.lat, 0.5)) + " ms";
  $("k-p95").textContent = Math.round(pctile(S.lat, 0.95)) + " ms";
  if (elapsed > 0) $("k-rate").textContent = (S.lat.length / elapsed).toFixed(1);
  $("k-tok").textContent = S.tokens.toLocaleString();
  $("k-cost").textContent = "$" + (S.tokens / 1e6 * 0.042).toFixed(5);
}

function spark() {
  const a = S.lat.slice(-120); if (a.length < 2) return;
  const max = Math.max(...a), step = 300 / (a.length - 1);
  const d = a.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(54 - (v / max) * 50).toFixed(1)}`).join("");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", d);
  $("spark").replaceChildren(p);
}

setInterval(() => {
  const now = Date.now() / 1000;
  if (S.p1 && !S.p1.end) $("t1").textContent = fmtS(now - S.p1.start);
  if (S.p2 && !S.p2.end) {
    const e = now - S.p2.start;
    $("t2").textContent = fmtS(e);
    $("i2").textContent = `${S.p2.done} / ${S.p2.total} classified · ${(S.p2.done / Math.max(e, 0.01)).toFixed(1)} mails/s` + (S.p2.errors ? ` · ${S.p2.errors} error(s)` : "");
    liveKpis(e);
    if (S.p2.total) $("remain").textContent = `${S.p2.total - S.p2.done} remaining`;
  }
}, 100);

function onEvent(ev) {
  switch (ev.type) {
    case "run_start":
      if (ev.job === "fetch" || ev.job === "classify") resetRun(ev.job);
      setBusy(true); schedule(); break;
    case "sync_start":
      S.p1 = { start: ev.t }; $("i1").textContent = "Reading the unread list…"; break;
    case "sync_listed":
      $("i1").textContent = `${ev.unread} unread · ${ev.new} new to fetch (list in ${fmtS(ev.list_s)})`; break;
    case "sync_progress":
      $("i1").textContent = `${ev.done} / ${ev.total} mails fetched`; break;
    case "sync_done":
      S.p1.end = ev.t; $("t1").textContent = fmtS(ev.fetch_s);
      $("i1").textContent = `${ev.fetched} fetched in ${fmtS(ev.fetch_s)}` + (ev.failed ? ` · ${ev.failed} failed` : "") + (ev.read_elsewhere ? ` · ${ev.read_elsewhere} read in the meantime` : "");
      setPending(ev.pending); break;
    case "board":
      S.rows = new Map(ev.cards.map((c) => [c.id, c])); schedule(); break;
    case "rows":
      for (const c of ev.cards) S.rows.set(c.id, c);  // no result yet: Jev has not answered
      schedule(); break;
    case "classify_start":
      S.p2 = { start: ev.t, total: ev.total, done: 0, errors: 0 };
      $("of").textContent = "of " + ev.total;
      $("i2").textContent = ev.total ? `${ev.total} mails to classify (${ev.concurrency} in parallel)` : "Nothing to classify: all mails are already classified";
      if (!ev.total) { S.p2.end = ev.t; $("t2").textContent = "0 s"; $("b2").style.width = "100%"; $("pct").textContent = "100%"; }
      break;
    case "email":
      upsert(ev.card, Date.now() / 1000 - ev.t < 2);  // no flash for a replayed history
      if (ev.card.latency_ms != null) { S.lat.push(ev.card.latency_ms); S.tokens += ev.card.input_tokens || 0; spark(); }
      break;
    case "classify_progress":
      if (S.p2) {
        Object.assign(S.p2, { done: ev.done, errors: ev.errors });
        $("done").textContent = ev.done; $("b2").style.width = (100 * ev.done / ev.total) + "%"; $("pct").textContent = Math.round(100 * ev.done / ev.total) + "%";
      }
      break;
    case "classify_done": {
      const m = ev.metrics;
      if (S.p2 && m.ok != null) {
        S.p2.end = ev.t; $("t2").textContent = fmtS(m.wall_s); $("b2").style.width = "100%"; $("pct").textContent = "100%"; $("remain").textContent = "0 remaining";
        $("i2").textContent = `${m.ok} classified in ${fmtS(m.wall_s)} · ${m.throughput_per_s} mails/s` + (m.errors ? ` · ${m.errors} error(s)` : "");
        $("k-mean").textContent = m.latency_ms.mean + " ms"; $("k-p50").textContent = m.latency_ms.p50 + " ms"; $("k-p95").textContent = m.latency_ms.p95 + " ms";
        $("k-rate").textContent = m.throughput_per_s; $("k-tok").textContent = m.input_tokens.toLocaleString(); $("k-cost").textContent = "$" + m.cost_usd.toFixed(5);
      }
      S.agreement = ev.agreement; break;
    }
    case "apply_start": {
      $("state").textContent = ev.kind === "undo" ? "Restoring…" : "Applying…";
      const c = ev.counts;
      $("of").textContent = "of " + ev.total;
      $("i2").textContent = c ? `${c.flag} flags · ${c.newsletters} to Newsletter · ${c.trash} to trash` : `${ev.total} mails to restore`;
      S.mv = { start: ev.t }; break;
    }
    case "apply_progress": {
      const step = { flag: "Flags", newsletters: "Newsletter folder", trash: "Trash", undo: "Restore" }[ev.kind] || "";
      $("done").textContent = ev.done; $("b2").style.width = (100 * ev.done / ev.total) + "%"; $("pct").textContent = Math.round(100 * ev.done / ev.total) + "%";
      $("remain").textContent = `${ev.total - ev.done} remaining`;
      $("i2").textContent = `${step} · ${ev.done} / ${ev.total} · ${(ev.done / Math.max(ev.t - S.mv.start, 0.01)).toFixed(1)} mails/s`; break;
    }
    case "apply_done": {
      const names = { flag: "flagged", newsletters: "to Newsletter", trash: "to trash", undo: "restored" };
      const parts = Object.entries(ev.results || {}).map(([k, r]) => `${r.ok} ${names[k]}` + (r.failed ? ` (${r.failed} failed)` : ""));
      $("i2").textContent = `${parts.join(" · ")} in ${fmtS(ev.seconds)}`;
      const err = Object.values(ev.results || {}).find((r) => r.error);
      if (err) showErr(err.error);
      if (ev.skipped_newsletters) showErr(`${ev.skipped_newsletters} newsletters not moved: Newsletter folder not found in Outlook.`);
      $("b2").style.width = "100%"; $("pct").textContent = "100%"; init(); break;
    }
    case "run_error":
      $("error").hidden = false; $("error").textContent = ev.message; $("state").textContent = "Error"; break;
    case "run_end":
      setBusy(false); if ($("state").textContent !== "Error") $("state").textContent = "Done";
      refreshApply(); loadRuns(); schedule(); break;
  }
}

// ---- detail / correction -----------------------------------------------------------
async function openDetail(id) {
  const d = await (await fetch("/api/emails/" + encodeURIComponent(id))).json();
  $("d-subject").textContent = d.subject; $("d-from").textContent = d.from;
  $("d-body").textContent = d.body || "(empty body)";
  const meta = $("d-meta"); meta.replaceChildren();
  const chip = (t) => meta.append(el("span", "chip", t));
  if (d.confidence != null) chip(`confidence ${Math.round(d.confidence * 100)}%`);
  if (d.latency_ms != null) chip(`analyzed in ${Math.round(d.latency_ms)} ms`);
  if (d.rule) chip("rule: " + d.rule);
  if (d.injection != null && d.injection > 0.5) chip("⚠ instructions detected in the mail");
  for (const [k, p] of Object.entries(d.probabilities || {}).sort((a, b) => b[1] - a[1])) if (p > 0.01) chip(`${S.cats[k] || k} ${Math.round(p * 100)}%`);
  const cats = $("d-cats"); cats.replaceChildren();
  for (const [k, label] of Object.entries(S.cats)) {
    const b = el("button", d.corrected && d.view === k ? "on" : "", label);
    b.onclick = async () => {
      const r = await (await fetch("/api/emails/" + encodeURIComponent(id), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ category: k }) })).json();
      upsert(r.card, true); S.agreement = r.agreement; renderAgreement(); refreshApply(); $("detail").close();
    };
    cats.append(b);
  }
  $("detail").showModal();
}

// ---- history ----------------------------------------------------------------------
function renderAgreement() {
  const a = S.agreement;
  $("agreement").textContent = a && a.reviewed ? `Jev / you agreement: ${a.agree} of ${a.reviewed} reviewed mails (${Math.round(a.rate * 100)}%)` : "Correct some mails to measure agreement between Jev and you.";
}
function renderRuns(runs) {
  const tb = document.querySelector("#runs tbody"); tb.replaceChildren();
  for (const r of runs) {
    const m = r.metrics || {}; if (m.error || r.kind !== "classify" || m.wall_s == null) continue;
    const tr = el("tr");
    [r.id, m.mails ?? "–", fmtS(m.wall_s), m.concurrency ?? "–", m.throughput_per_s ?? "–", m.latency_ms?.mean != null ? m.latency_ms.mean + " ms" : "–"]
      .forEach((v) => tr.append(el("td", "", v)));
    tb.append(tr);
  }
  renderAgreement();
}
async function loadRuns() { const s = await (await fetch("/api/state")).json(); S.agreement = s.agreement; setPending(s.pending); renderRuns(s.runs); }

// ---- actions ---------------------------------------------------------------------------
function setBusy(busy) {
  S.running = busy;
  for (const id of ["fetch", "run"]) $(id).disabled = busy;
  if (busy) $("apply").disabled = true;
}
function setPending(n) {
  $("pending-hint").textContent = n ? `${n} mail${n > 1 ? "s" : ""} waiting to be classified` : "Nothing waiting to be classified";
}
const showErr = (m) => { $("error").hidden = false; $("error").textContent = m; };

$("fetch").onclick = async () => {
  const r = await fetch("/api/fetch", { method: "POST" });
  if (!r.ok) showErr((await r.json()).detail);
};
$("run").onclick = async () => {
  const r = await fetch("/api/classify", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reclassify: $("mode").value === "all", concurrency: +$("conc").value }) });
  if (!r.ok) showErr((await r.json()).detail);
};
$("apply").onclick = async () => {
  const p = await (await fetch("/api/apply/preview")).json();
  $("c-title").textContent = "Apply the changes to Outlook?";
  const sum = $("c-summary"); sum.replaceChildren();
  const line = (label, n) => { const li = el("li"); li.append(el("span", "", label), el("b", "", n)); sum.append(li); };
  if (p.flag) line(`Flag “awaiting reply”${p.flag_uncertain ? ` (${p.flag_uncertain} uncertain)` : ""}`, p.flag);
  if (p.newsletters && p.folder_ok) line(`To the “${p.folder}” folder`, p.newsletters);
  if (p.trash) line("To trash", p.trash);
  const warn = $("c-warn");
  warn.hidden = !(p.newsletters && !p.folder_ok);
  if (!warn.hidden) warn.textContent = `Folder “${p.folder}” not found in Outlook: the ${p.newsletters} newsletters will not be moved. Create it (at the root or under the inbox) then reopen this dialog.`;
  const ul = $("c-list"); ul.replaceChildren();
  p.subjects.forEach((s) => ul.append(el("li", "", s)));
  if (p.trash > p.subjects.length) ul.append(el("li", "", `… and ${p.trash - p.subjects.length} more`));
  $("c-details").hidden = !p.trash;
  $("confirm").showModal();
};
$("c-no").onclick = () => $("confirm").close();
$("c-yes").onclick = async () => {
  $("confirm").close();
  const r = await fetch("/api/apply", { method: "POST" });
  if (!r.ok) return showErr((await r.json()).detail);
  setBusy(true);
};
$("undo").onclick = async () => {
  const r = await fetch("/api/apply/undo", { method: "POST" });
  if (!r.ok) return showErr((await r.json()).detail);
  setBusy(true);
};

// ---- "By sender" view -------------------------------------------------------------
function setTab(tab) {
  S.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $("filters").hidden = tab !== "mails"; $("mails-panel").hidden = tab !== "mails"; $("senders-panel").hidden = tab !== "senders";
  if (tab === "senders") loadSenders();
  else if (S.stale) { S.stale = false; init(); }
}
document.querySelectorAll(".tab").forEach((b) => b.onclick = () => setTab(b.dataset.tab));
$("undecided").onchange = renderSenders;

async function loadSenders() {
  const d = await (await fetch("/api/senders")).json();
  S.senders = d.senders; S.maxAge = d.max_age_days; renderSenders();
}

function renderSenders() {
  const only = $("undecided").checked;
  const list = S.senders.filter((x) => !only || !x.decision);
  $("senders-info").textContent = `${list.length} sender${list.length === 1 ? "" : "s"} · a decision applies to all their mails, past and future · after ${S.maxAge} days, a newsletter goes to “discard”`;
  $("senders-rows").replaceChildren(...list.map((x) => {
    const tr = el("tr");
    const who = el("td", "who"); who.append(document.createTextNode(x.name), el("small", "", `${x.sender} · ${x.subject}`)); who.title = x.sender;
    tr.append(who, el("td", "num", x.total), el("td", "num", x.recent), el("td", "num", x.spam == null ? "–" : Math.round(x.spam * 100) + "%"));
    const d = el("td"), box = el("span", "dec");
    for (const [k, label] of [["keep", "Keep"], ["discard", "Discard"]]) {
      const b = el("button", k + (x.decision === k ? " on" : "") + (!x.decision && x.suggestion === k ? " sugg" : ""), label);
      if (!x.decision && x.suggestion === k) b.title = "Jev suggestion (based on the spam score)";
      b.onclick = () => decide(x, x.decision === k ? null : k);
      box.append(b);
    }
    d.append(box); tr.append(d);
    return tr;
  }));
}

async function decide(x, decision) {
  const r = await fetch("/api/senders/decision", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sender: x.sender, decision }) });
  if (!r.ok) return showErr((await r.json()).detail);
  x.decision = decision; S.stale = true; renderSenders(); refreshApply();
}

// ---- startup ---------------------------------------------------------------------------
async function init() {
  const s = await (await fetch("/api/state")).json();
  S.cats = s.categories; S.review = s.review; S.running = s.running; S.agreement = s.agreement;
  S.rows = new Map(s.emails.map((c) => [c.id, c]));
  $("conc").value = String(s.concurrency); if ($("conc").value !== String(s.concurrency)) $("conc").add(new Option(s.concurrency, s.concurrency, true, true));
  setBusy(s.running); setPending(s.pending);
  renderRuns(s.runs); render(); refreshApply();
}

init().then(() => {
  if (location.hash === "#senders") setTab("senders");
  const es = new EventSource("/api/events");
  es.onmessage = (e) => onEvent(JSON.parse(e.data));
});

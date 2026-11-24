// Web interface of the CMT (NFR-04). It only talks to /api/... on this same server (the interface server),
// which passes everything on to the orchestrator. All checks (login, roles, deadlines) happen in the backend.
//
// Security:
// - Text from the server is always inserted with textContent, never as HTML, so a paper title like
//   "<script>..." is shown as text and never runs (SEC-09, XSS).
// - The login token is in an HttpOnly cookie that JavaScript cannot read (set by the interface server).
// - Every request that changes something sends the CSRF token from the csrf_token cookie (SEC-12).

// What we loaded from the server last time (used by several pages)
const state = { user: null, conferences: [], papers: [], assignments: [], invitations: [] };

// ---------- small helpers ----------

// Make an element with text. Never uses HTML strings.
function el(tag, text, attributes = {}) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

// An element with children: box("div", {class: "x"}, child1, child2, ...)
function box(tag, attributes, ...children) {
  const node = el(tag, null, attributes);
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

function button(label, onClick, className = "") {
  const b = el("button", label, { type: "button", class: className });
  b.addEventListener("click", onClick);
  return b;
}

const $ = (id) => document.getElementById(id);

// A date from the server (ISO format) in a readable form, e.g. "1 Mar 2028, 12:00"
function niceDate(iso) {
  const date = new Date(iso);
  return isNaN(date) ? iso : date.toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// A coloured label for a status
function pill(text) {
  const colours = { accepted: "ok", accept: "ok", rejected: "bad", reject: "bad", "under review": "wait",
    pending: "wait", "needs reviewers": "warn", declined: "bad", released: "ok", recorded: "wait" };
  return el("span", text, { class: `pill ${colours[String(text).toLowerCase()] || ""}` });
}

// Show a short message (green = ok, red = error) on the page that is visible now.
function showMessage(text, isError = false) {
  const target = document.querySelector($("app-view").hidden ? ".login-message" : ".app-message");
  target.textContent = text;
  target.classList.toggle("error", isError);
  target.classList.toggle("ok", !isError);
}

// The CSRF token the interface server put in the csrf_token cookie.
function csrfToken() {
  const found = document.cookie.split("; ").find((c) => c.startsWith("csrf_token="));
  return found ? decodeURIComponent(found.split("=")[1]) : "";
}

// Call the API. body can be an object (sent as JSON) or FormData (for a PDF upload).
async function api(method, path, body) {
  const options = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
  if (method !== "GET") options.headers["X-CSRF-Token"] = csrfToken();
  if (body instanceof FormData) {
    options.body = body;                                 // the browser sets the right Content-Type
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch (e) { /* not JSON */ }
  if (response.status === 401 && !path.endsWith("/auth/login")) {
    showLoginView();                                     // login expired: back to the login page
    throw new Error("Please log in (again)");
  }
  if (!response.ok) throw new Error((data && (data.error || data.message)) || `Error ${response.status}`);
  return data;
}

// Run an action and show its error message instead of crashing.
async function attempt(action) {
  try { await action(); } catch (e) { showMessage(e.message, true); }
}

// Form fields as an object, without empty values.
function formValues(form) {
  const values = {};
  for (const [name, value] of new FormData(form).entries()) {
    if (typeof value === "string" && value.trim() !== "") values[name] = value.trim();
  }
  return values;
}

function emptyItem(text) {
  return el("li", text, { class: "empty" });
}

// ---------- pages and menu ----------

const PAGES = ["overview", "assistant", "conferences", "papers", "reviews", "invitations"];

function showPage(name) {
  if (!PAGES.includes(name)) name = "overview";
  for (const page of PAGES) $(`page-${page}`).hidden = page !== name;
  for (const link of document.querySelectorAll("#menu a")) link.classList.toggle("active", link.dataset.page === name);
  document.querySelector(".app-message").textContent = "";
}

function showLoginView() {
  $("login-view").hidden = false;
  $("app-view").hidden = true;
}

async function showAppView(user) {
  state.user = user;
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  $("user-name").textContent = user.name;
  $("user-email").textContent = user.email;
  $("user-initials").textContent = user.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  $("welcome").textContent = `Welcome back, ${user.name.split(/\s+/)[0]}. Here is what needs your attention.`;
  showPage(location.hash.slice(1));
  await loadEverything();
}

// Load all lists at once and draw every page again.
async function loadEverything() {
  await attempt(async () => {
    [state.conferences, state.papers, state.assignments, state.invitations] = await Promise.all([
      api("GET", "/api/cmt/conferences"), api("GET", "/api/cmt/submissions"),
      api("GET", "/api/cmt/assignments"), api("GET", "/api/cmt/invitations")]);
    drawOverview();
    drawConferences();
    drawPapers();
    drawReviews();
    drawInvitations();
  });
}

function setBadge(id, count) {
  $(id).textContent = count;
  $(id).hidden = count === 0;
}

// ---------- login, register, logout (UR-01) ----------

function switchLoginForm(event) {
  for (const b of document.querySelectorAll(".switch-button")) b.classList.toggle("active", b === event.target);
  $("login-form").hidden = event.target.dataset.show !== "login-form";
  $("register-form").hidden = event.target.dataset.show !== "register-form";
  document.querySelector(".login-message").textContent = "";
}

async function login(event) {
  event.preventDefault();
  await attempt(async () => {
    const answer = await api("POST", "/api/cmt/auth/login", formValues(event.target));
    event.target.reset();
    await showAppView(answer.user);
  });
}

async function register(event) {
  event.preventDefault();
  await attempt(async () => {
    const email = formValues(event.target).email;
    await api("POST", "/api/cmt/auth/register", formValues(event.target));
    event.target.reset();
    document.querySelector('.switch-button[data-show="login-form"]').click();
    $("login-form").elements.email.value = email;
    showMessage("Account created. You can log in now.");
  });
}

async function logout() {
  await attempt(async () => {
    await api("POST", "/api/cmt/auth/logout");
    showLoginView();
    showMessage("Logged out.");
  });
}

// ---------- overview ----------

function drawOverview() {
  const chairOf = state.conferences.filter((c) => c.my_roles.includes("chair")).length;
  const accepted = state.papers.filter((p) => p.status === "accepted").length;
  const toDo = state.assignments.filter((a) => !a.has_review);
  const pending = state.invitations.filter((i) => i.status === "pending").length;

  $("stat-conferences").textContent = state.conferences.length;
  $("stat-conferences-note").textContent = `chair of ${chairOf}`;
  $("stat-papers").textContent = state.papers.length;
  $("stat-papers-note").textContent = `${accepted} accepted`;
  $("stat-reviews").textContent = toDo.length;
  $("stat-reviews-note").textContent = toDo.length ? `next deadline ${niceDate(toDo[0].review_deadline)}` : "all done";
  $("stat-invitations").textContent = pending;
  setBadge("badge-reviews", toDo.length);
  setBadge("badge-invitations", pending);

  const papers = $("overview-papers");
  papers.replaceChildren();
  if (!state.papers.length) papers.append(emptyItem("You have not submitted a paper yet."));
  for (const p of state.papers.slice(0, 5)) {
    papers.append(box("li", {}, box("div", {}, el("b", p.title), el("small", `Conference ${p.conference_id} · paper ${p.id}`)), pill(p.status)));
  }

  const reviews = $("overview-reviews");
  reviews.replaceChildren();
  if (!state.assignments.length) reviews.append(emptyItem("Nothing to review."));
  for (const a of state.assignments.slice(0, 5)) {
    reviews.append(box("li", {}, box("div", {}, el("b", a.title), el("small", `Deadline ${niceDate(a.review_deadline)}`)),
      pill(a.has_review ? "Done" : "To do")));
  }
}

// ---------- conferences (UR-03, UR-04, UR-05) ----------

function drawConferences() {
  const list = $("conference-list");
  const select = $("paper-conference");
  list.replaceChildren();
  select.replaceChildren();
  if (!state.conferences.length) list.append(box("section", { class: "card" }, el("p", "No conferences yet. Create the first one.", { class: "meta" })));

  for (const c of state.conferences) {
    select.append(el("option", `${c.name} (conference ${c.id})`, { value: c.id }));
    const roles = box("div", { class: "row-buttons" }, ...c.my_roles.map((r) => pill(r)));
    const card = box("section", { class: "card" },
      box("div", { class: "card-head" }, box("div", {}, el("h2", c.name), el("div", `Conference ${c.id}`, { class: "meta" })), roles),
      c.description ? el("p", c.description, { class: "abstract" }) : null,
      el("div", `Submissions until ${niceDate(c.submission_deadline)} · reviews until ${niceDate(c.review_deadline)} · ` +
        `notification ${niceDate(c.notification_deadline)}`, { class: "meta" }));

    if (c.my_roles.includes("chair")) {
      // Chair tools. Assigning reviewers and decisions are done through the AI assistant.
      card.append(box("div", { class: "row-buttons" },
        button("Review progress", () => attempt(() => showReviewProgress(c))),
        button("Results and decisions", () => attempt(() => showResults(c)))));
      const email = el("input", null, { type: "email", placeholder: "Email of a reviewer to invite", required: "" });
      const invite = box("form", { class: "inline-form" }, email, el("button", "Invite reviewer", { type: "submit", class: "primary" }));
      invite.addEventListener("submit", (event) => {
        event.preventDefault();
        attempt(async () => {
          await api("POST", `/api/cmt/conferences/${c.id}/invitations`, { email: email.value.trim() });
          showMessage(`Invitation sent to ${email.value.trim()}.`);
          invite.reset();
        });
      });
      card.append(invite);
    }
    list.append(card);
  }
}

function showChairDetails(title, ...content) {
  $("chair-details-title").textContent = title;
  $("chair-details-body").replaceChildren(...content);
  $("chair-details").hidden = false;
  $("chair-details").scrollIntoView({ behavior: "smooth" });
}

async function showReviewProgress(conference) {
  const progress = await api("GET", `/api/cmt/conferences/${conference.id}/review-progress`);
  const list = el("ul", null, { class: "list" });
  if (!progress.papers.length) list.append(emptyItem("No papers yet."));
  for (const p of progress.papers) {
    const missing = p.missing_reviews.map((m) => m.reviewer.name).join(", ");
    const info = box("div", {}, el("b", `Paper ${p.submission_id}: ${p.title}`),
      el("small", `${p.completed} of ${p.assigned} reviews${missing ? ` · waiting for ${missing}` : ""}`));
    if (p.needs_reviewers) {
      list.append(box("li", {}, info, pill("Needs reviewers")));
    } else {
      const fill = el("i");
      fill.style.width = `${p.assigned ? Math.round((100 * p.completed) / p.assigned) : 0}%`;
      list.append(box("li", {}, info, box("div", { class: "bar" }, fill)));
    }
  }
  const note = el("p", `Review deadline ${niceDate(progress.review_deadline)}${progress.deadline_passed ? " (passed)" : ""}`, { class: "meta" });
  showChairDetails(`Review progress · ${conference.name}`, note, list);
}

async function showResults(conference) {
  const papers = await api("GET", `/api/cmt/conferences/${conference.id}/results`);
  const table = box("table", {}, box("tr", {}, ...["Paper", "Reviews", "Avg. score", "Avg. confidence", "Decision"].map((h) => el("th", h))));
  for (const p of papers) {
    const decision = p.chair_is_author ? el("small", "You are an author: hidden")
      : p.decision_state === "none" ? el("small", "none yet") : pill(`${p.outcome} (${p.decision_state})`);
    table.append(box("tr", {}, el("td", `${p.submission_id}. ${p.title}`), el("td", p.reviews_completed ?? "–"),
      el("td", p.average_overall_score ?? "–"), el("td", p.average_confidence ?? "–"), box("td", {}, decision)));
  }
  const tip = el("p", 'Record or release a decision through the AI assistant, e.g. "accept paper 1 in conference 1".', { class: "meta" });
  showChairDetails(`Results · ${conference.name}`, table, tip);
}

async function createConference(event) {
  event.preventDefault();
  await attempt(async () => {
    const conference = await api("POST", "/api/cmt/conferences", formValues(event.target));
    event.target.reset();
    $("conference-form-card").hidden = true;
    showMessage(`Conference "${conference.name}" created. You are its chair.`);
    await loadEverything();
  });
}

// ---------- invitations (UR-07) ----------

function drawInvitations() {
  const list = $("invitation-list");
  list.replaceChildren();
  if (!state.invitations.length) list.append(emptyItem("No invitations."));
  for (const inv of state.invitations) {
    const right = inv.status === "pending"
      ? box("div", { class: "row-buttons" },
        button("Accept", () => answerInvitation(inv, "accept"), "primary sm"),
        button("Decline", () => answerInvitation(inv, "decline"), "sm"))
      : pill(inv.status);
    list.append(box("li", {}, box("div", {}, el("b", inv.conference_name), el("small", "Invitation to review")), right));
  }
}

async function answerInvitation(invitation, answer) {
  await attempt(async () => {
    await api("POST", `/api/cmt/invitations/${invitation.id}/${answer}`);
    showMessage(`Invitation ${answer === "accept" ? "accepted" : "declined"}.`);
    await loadEverything();
  });
}

// ---------- my papers (UR-10, UR-11) ----------

function drawPapers() {
  const list = $("paper-list");
  list.replaceChildren();
  if (!state.papers.length) list.append(emptyItem("No papers yet."));
  for (const p of state.papers) {
    const authors = p.authors.map((a) => a.name).join(", ");
    list.append(box("li", {},
      box("div", {}, el("b", p.title), el("small", `Conference ${p.conference_id} · paper ${p.id} · ${authors}`)),
      box("div", { class: "row-buttons" }, pill(p.status), button("Decision and reviews", () => attempt(() => showPaperResult(p)), "sm"))));
  }
}

async function showPaperResult(paper) {
  const result = await api("GET", `/api/cmt/submissions/${paper.id}/result`);
  const body = [];
  if (!result.decision_released) {
    body.push(el("p", `Status: ${result.status}. The decision has not been released yet.`, { class: "meta" }));
  } else {
    body.push(box("p", {}, el("span", "Decision: "), pill(result.decision)));
    result.reviews.forEach((r, i) => body.push(box("div", { class: "review-box" },
      el("b", `Review ${i + 1} · score ${r.overall_score}/5 · confidence ${r.confidence}/5`), el("p", r.comments))));
  }
  $("paper-details-title").textContent = `Result · ${paper.title}`;
  $("paper-details-body").replaceChildren(...body);
  $("paper-details").hidden = false;
  $("paper-details").scrollIntoView({ behavior: "smooth" });
}

async function submitPaper(event) {
  event.preventDefault();
  const form = event.target;
  await attempt(async () => {
    const data = new FormData(form);                     // title, abstract, coauthors and the PDF file
    const conferenceId = data.get("conference_id");
    data.delete("conference_id");
    const paper = await api("POST", `/api/cmt/conferences/${conferenceId}/submissions`, data);
    form.reset();
    $("paper-form-card").hidden = true;
    showMessage(`Paper "${paper.title}" submitted.`);
    await loadEverything();
  });
}

// ---------- reviews (UR-08, UR-09) ----------

function scoreSelect(name) {
  const select = el("select", null, { name, required: "" });
  for (let i = 1; i <= 5; i++) select.append(el("option", i, { value: i }));
  return select;
}

function drawReviews() {
  const list = $("review-list");
  list.replaceChildren();
  if (!state.assignments.length) list.append(box("section", { class: "card" }, el("p", "Nothing to review.", { class: "meta" })));

  for (const a of state.assignments) {
    const overall = scoreSelect("overall_score");
    const confidence = scoreSelect("confidence");
    const comments = el("textarea", null, { name: "comments", required: "", maxlength: "10000", placeholder: "Your comments for the authors" });
    const form = box("form", { class: "review-form" },
      box("label", {}, el("span", "Overall score (1-5)"), overall),
      box("label", {}, el("span", "Confidence (1-5)"), confidence),
      el("span"),
      box("label", { class: "comments" }, el("span", "Comments"), comments),
      box("div", { class: "form-buttons" }, el("button", a.has_review ? "Update review" : "Save review", { type: "submit", class: "primary" })));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      attempt(async () => {
        await api("PUT", `/api/cmt/assignments/${a.assignment_id}/review`, {
          overall_score: Number(overall.value), confidence: Number(confidence.value), comments: comments.value,
        });
        showMessage(`Review for paper ${a.submission_id} saved.`);
        await loadEverything();
      });
    });
    if (a.has_review) {   // fill in the saved review so it can be edited
      attempt(async () => {
        const saved = await api("GET", `/api/cmt/assignments/${a.assignment_id}/review`);
        overall.value = saved.overall_score;
        confidence.value = saved.confidence;
        comments.value = saved.comments;
      });
    }
    // The PDF comes without author metadata; the login cookie is sent along automatically
    const download = el("a", "Download PDF", { href: `/api/cmt/assignments/${a.assignment_id}/pdf`, class: "button-link" });
    list.append(box("section", { class: "card" },
      box("div", { class: "card-head" }, box("div", {}, el("h2", a.title),          // never author names (SEC-05)
        el("div", `Paper ${a.submission_id} · conference ${a.conference_id} · review deadline ${niceDate(a.review_deadline)}`, { class: "meta" })),
      pill(a.has_review ? "Done" : "To do")),
      el("p", a.abstract, { class: "abstract" }),
      box("p", {}, download),
      form));
  }
}

// ---------- AI assistant (UR-12, FR-16, FR-17) ----------

function chatBubble(text, fromMe) {
  const log = $("chat-log");
  log.querySelector(".hint")?.remove();
  const bubble = el("div", text, { class: `msg ${fromMe ? "me" : "ai"}` });
  log.append(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

// Data from the system is only shown, as text: a table for a list, otherwise the plain values.
function showAssistantData(title, data) {
  // A list, or an answer that contains one list (e.g. {"papers": [...]} or {"items": [...]})
  const lists = data && typeof data === "object" && !Array.isArray(data) ? Object.values(data).filter((v) => Array.isArray(v) && v.length && typeof v[0] === "object") : [];
  const items = Array.isArray(data) ? data : lists.length === 1 ? lists[0] : null;
  let content;
  if (items && items.length && typeof items[0] === "object") {
    const columns = Object.keys(items[0]).filter((k) => typeof items[0][k] !== "object").slice(0, 6);
    content = box("table", {}, box("tr", {}, ...columns.map((c) => el("th", c.replaceAll("_", " ")))),
      ...items.map((item) => box("tr", {}, ...columns.map((c) => el("td", item[c])))));
  } else {
    content = el("pre", JSON.stringify(data, null, 2));
  }
  $("assistant-details-title").textContent = title;
  $("assistant-details-body").replaceChildren(content);
  $("assistant-details").hidden = false;
}

function showAssistantAnswer(answer) {
  const bubble = chatBubble(answer.message, false);
  if (answer.type === "result" && answer.data !== undefined) showAssistantData(answer.message, answer.data);
  if (answer.type === "confirmation_required") {
    // Important actions wait for a "yes" (FR-17)
    const yes = button("Yes, do it", () => answerConfirmation({ confirm: answer.action_id }, yes, no), "primary sm");
    const no = button("Cancel", () => answerConfirmation({ cancel: answer.action_id }, yes, no), "sm");
    bubble.append(box("div", { class: "actions" }, yes, no));
  }
}

async function answerConfirmation(body, yes, no) {
  yes.disabled = no.disabled = true;
  try {
    showAssistantAnswer(await api("POST", "/api/ai/chat", body));
    await loadEverything();
  } catch (e) {
    chatBubble(e.message, false);
  }
}

async function sendChat(event) {
  event.preventDefault();
  const input = event.target.elements.message;
  const message = input.value.trim();
  if (!message) return;
  chatBubble(message, true);
  input.value = "";
  try {
    showAssistantAnswer(await api("POST", "/api/ai/chat", { message }));
    await loadEverything();
  } catch (e) {
    chatBubble(e.message, false);
  }
}

// ---------- start ----------

document.addEventListener("DOMContentLoaded", async () => {
  for (const b of document.querySelectorAll(".switch-button")) b.addEventListener("click", switchLoginForm);
  $("login-form").addEventListener("submit", login);
  $("register-form").addEventListener("submit", register);
  $("logout-button").addEventListener("click", logout);
  $("conference-form").addEventListener("submit", createConference);
  $("paper-form").addEventListener("submit", submitPaper);
  $("chat-form").addEventListener("submit", sendChat);
  $("new-conference-button").addEventListener("click", () => { $("conference-form-card").hidden = !$("conference-form-card").hidden; });
  $("new-paper-button").addEventListener("click", () => { $("paper-form-card").hidden = !$("paper-form-card").hidden; });
  window.addEventListener("hashchange", () => showPage(location.hash.slice(1)));
  $("chat-log").append(el("p", "Ask me something, or say \"help\".", { class: "hint" }));
  try {
    await showAppView(await api("GET", "/api/cmt/auth/me"));   // still logged in (cookie)?
  } catch (e) {
    showLoginView();
  }
});

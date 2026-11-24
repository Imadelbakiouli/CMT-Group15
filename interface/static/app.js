// Web interface of the CMT (NFR-04). It only talks to /api/... on this same server (the interface server),
// which passes everything on to the orchestrator. All checks (login, roles, deadlines) happen in the backend.
//
// Security:
// - Text from the server is always inserted with textContent, never as HTML, so a paper title like
//   "<script>..." is shown as text and never runs (SEC-09, XSS).
// - The login token is in an HttpOnly cookie that JavaScript cannot read (set by the interface server).
// - Every request that changes something sends the CSRF token from the csrf_token cookie (SEC-12).

// ---------- small helpers ----------

// Make an element with text. Never uses innerHTML.
function el(tag, text, attributes = {}) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

// A button that runs a function when clicked.
function button(label, onClick) {
  const b = el("button", label, { type: "button" });
  b.addEventListener("click", onClick);
  return b;
}

// Show a short message at the top of the page (green = ok, red = error).
function showMessage(text, isError = false) {
  const box = document.getElementById("message");
  box.textContent = text;
  box.className = isError ? "error" : "ok";
}

// Show details (an answer from the server) in the "Details" box.
function showOutput(title, data) {
  document.getElementById("output").textContent = title + "\n\n" + JSON.stringify(data, null, 2);
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

// ---------- switching between login page and dashboard ----------

function showLoginView() {
  document.getElementById("login-view").hidden = false;
  document.getElementById("app-view").hidden = true;
  document.getElementById("user-bar").hidden = true;
}

async function showAppView(user) {
  document.getElementById("login-view").hidden = true;
  document.getElementById("app-view").hidden = false;
  document.getElementById("user-bar").hidden = false;
  document.getElementById("user-name").textContent = `${user.name} (${user.email})`;
  await loadEverything();
}

async function loadEverything() {
  await attempt(loadConferences);
  await attempt(loadInvitations);
  await attempt(loadPapers);
  await attempt(loadReviews);
}

// ---------- login, register, logout (UR-01) ----------

async function login(event) {
  event.preventDefault();
  await attempt(async () => {
    const answer = await api("POST", "/api/cmt/auth/login", formValues(event.target));
    event.target.reset();
    showMessage("Logged in.");
    await showAppView(answer.user);
  });
}

async function register(event) {
  event.preventDefault();
  await attempt(async () => {
    await api("POST", "/api/cmt/auth/register", formValues(event.target));
    event.target.reset();
    showMessage("Account created. You can log in now.");
  });
}

async function logout() {
  await attempt(async () => {
    await api("POST", "/api/cmt/auth/logout");
    showMessage("Logged out.");
    showLoginView();
  });
}

// ---------- conferences (UR-03, UR-04, UR-05) ----------

async function loadConferences() {
  const conferences = await api("GET", "/api/cmt/conferences");
  const list = document.getElementById("conference-list");
  const select = document.getElementById("paper-conference");
  list.replaceChildren();
  select.replaceChildren();
  if (conferences.length === 0) list.append(el("li", "No conferences yet."));

  for (const c of conferences) {
    select.append(el("option", `${c.id}: ${c.name}`, { value: c.id }));
    const item = el("li");
    item.append(el("strong", `${c.id}. ${c.name}`));
    item.append(el("div", `Submission deadline: ${c.submission_deadline}`, { class: "small" }));
    item.append(el("div", `My roles: ${c.my_roles.join(", ") || "none"}`, { class: "small" }));

    if (c.my_roles.includes("chair")) {
      // Chair tools: overviews and inviting reviewers. Other chair actions work through the AI assistant.
      item.append(button("Review progress", () => attempt(async () =>
        showOutput(`Review progress of conference ${c.id}`, await api("GET", `/api/cmt/conferences/${c.id}/review-progress`)))));
      item.append(button("Results", () => attempt(async () =>
        showOutput(`Results of conference ${c.id}`, await api("GET", `/api/cmt/conferences/${c.id}/results`)))));
      const invite = el("form");
      const email = el("input", null, { type: "email", placeholder: "Reviewer email to invite", required: "" });
      invite.append(email, el("button", "Invite reviewer", { type: "submit" }));
      invite.addEventListener("submit", (event) => {
        event.preventDefault();
        attempt(async () => {
          await api("POST", `/api/cmt/conferences/${c.id}/invitations`, { email: email.value.trim() });
          showMessage(`Invitation sent to ${email.value.trim()}.`);
          invite.reset();
        });
      });
      item.append(invite);
    }
    list.append(item);
  }
}

async function createConference(event) {
  event.preventDefault();
  await attempt(async () => {
    const conference = await api("POST", "/api/cmt/conferences", formValues(event.target));
    event.target.reset();
    showMessage(`Conference "${conference.name}" created. You are its chair.`);
    await loadConferences();
  });
}

// ---------- invitations (UR-07) ----------

async function loadInvitations() {
  const invitations = await api("GET", "/api/cmt/invitations");
  const list = document.getElementById("invitation-list");
  list.replaceChildren();
  if (invitations.length === 0) list.append(el("li", "No invitations."));

  for (const inv of invitations) {
    const item = el("li", `${inv.conference_name} – ${inv.status} `);
    if (inv.status === "pending") {
      for (const answer of ["accept", "decline"]) {
        item.append(button(answer === "accept" ? "Accept" : "Decline", () => attempt(async () => {
          await api("POST", `/api/cmt/invitations/${inv.id}/${answer}`);
          showMessage(`Invitation ${answer === "accept" ? "accepted" : "declined"}.`);
          await loadEverything();
        })));
      }
    }
    list.append(item);
  }
}

// ---------- my papers (UR-10, UR-11) ----------

async function loadPapers() {
  const papers = await api("GET", "/api/cmt/submissions");
  const list = document.getElementById("paper-list");
  list.replaceChildren();
  if (papers.length === 0) list.append(el("li", "No papers yet."));

  for (const p of papers) {
    const item = el("li");
    item.append(el("strong", `${p.id}. ${p.title}`));
    item.append(el("div", `Conference ${p.conference_id} – status: ${p.status}`, { class: "small" }));
    item.append(button("Decision and reviews", () => attempt(async () =>
      showOutput(`Result of paper ${p.id}`, await api("GET", `/api/cmt/submissions/${p.id}/result`)))));
    list.append(item);
  }
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
    showMessage(`Paper "${paper.title}" submitted.`);
    await loadEverything();
  });
}

// ---------- papers to review (UR-08, UR-09) ----------

function scoreSelect(name) {
  const select = el("select", null, { name, required: "" });
  for (let i = 1; i <= 5; i++) select.append(el("option", i, { value: i }));
  return select;
}

async function loadReviews() {
  const assignments = await api("GET", "/api/cmt/assignments");
  const list = document.getElementById("review-list");
  list.replaceChildren();
  if (assignments.length === 0) list.append(el("li", "Nothing to review."));

  for (const a of assignments) {
    const item = el("li");
    item.append(el("strong", `Paper ${a.submission_id}: ${a.title}`));    // never author names (SEC-05)
    item.append(el("div", a.abstract, { class: "small" }));
    item.append(el("div", `Review deadline: ${a.review_deadline}${a.has_review ? " – review saved" : ""}`, { class: "small" }));
    // The PDF comes without metadata; the login cookie is sent along automatically
    item.append(el("a", "Download PDF (anonymous)", { href: `/api/cmt/assignments/${a.assignment_id}/pdf` }));

    const form = el("form");
    const overall = scoreSelect("overall_score");
    const confidence = scoreSelect("confidence");
    const comments = el("textarea", null, { name: "comments", required: "", maxlength: "10000", placeholder: "Comments" });
    form.append(el("label", "Overall score (1-5)"), overall, el("label", "Confidence (1-5)"), confidence,
      comments, el("button", a.has_review ? "Update review" : "Save review", { type: "submit" }));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      attempt(async () => {
        await api("PUT", `/api/cmt/assignments/${a.assignment_id}/review`, {
          overall_score: Number(overall.value), confidence: Number(confidence.value), comments: comments.value,
        });
        showMessage(`Review for paper ${a.submission_id} saved.`);
        await loadReviews();
      });
    });
    item.append(form);
    list.append(item);
  }
}

// ---------- AI assistant (UR-12, FR-16, FR-17) ----------

function chatLine(text, fromMe = false) {
  const log = document.getElementById("chat-log");
  log.append(el("p", text, fromMe ? { class: "me" } : {}));
  log.scrollTop = log.scrollHeight;
}

// Show what the assistant answered. Data from the system is only shown, as text.
function showAssistantAnswer(answer) {
  chatLine("Assistant: " + answer.message);
  if (answer.type === "result" && answer.data !== undefined) showOutput(answer.message, answer.data);
  if (answer.type === "confirmation_required") {
    // Important actions wait for a "yes" (FR-17)
    const log = document.getElementById("chat-log");
    const yes = button("Yes, do it", () => answerConfirmation({ confirm: answer.action_id }, yes, no));
    const no = button("No, cancel", () => answerConfirmation({ cancel: answer.action_id }, yes, no));
    const row = el("p");
    row.append(yes, no);
    log.append(row);
  }
}

async function answerConfirmation(body, yes, no) {
  yes.disabled = no.disabled = true;
  await attempt(async () => {
    showAssistantAnswer(await api("POST", "/api/ai/chat", body));
    await loadEverything();
  });
}

async function sendChat(event) {
  event.preventDefault();
  const input = event.target.elements.message;
  const message = input.value.trim();
  if (!message) return;
  chatLine("You: " + message, true);
  input.value = "";
  try {
    showAssistantAnswer(await api("POST", "/api/ai/chat", { message }));
    await loadEverything();
  } catch (e) {
    chatLine("Assistant: " + e.message);
  }
}

// ---------- start ----------

document.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("login-form").addEventListener("submit", login);
  document.getElementById("register-form").addEventListener("submit", register);
  document.getElementById("logout-button").addEventListener("click", logout);
  document.getElementById("conference-form").addEventListener("submit", createConference);
  document.getElementById("paper-form").addEventListener("submit", submitPaper);
  document.getElementById("chat-form").addEventListener("submit", sendChat);
  try {
    await showAppView(await api("GET", "/api/cmt/auth/me"));   // still logged in (cookie)?
  } catch (e) {
    showLoginView();
  }
});

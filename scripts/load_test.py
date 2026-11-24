#!/usr/bin/env python3
"""Load test for NFR-01 (95% of normal requests within 1 second with 50 users) and NFR-03 (1000 submissions, 300 reviewers).

It fills a conference with users and papers, then lets 50 virtual users (chair, reviewers, authors) use it at the same time
and measures how long every request takes.

    python scripts/load_test.py                           small data set, against the CMT service on localhost:5002
    python scripts/load_test.py --full                    1000 submissions and 300 reviewers (takes a while)
    python scripts/load_test.py --base-url http://localhost:5001 --prefix /api/cmt     through the orchestrator
    python scripts/load_test.py --base-url https://localhost --prefix /api/cmt --insecure   through the interface and HTTPS

Needs a running system (docker compose up) and `pip install requests pypdf`. Run it against a test system, it creates
many accounts. Every run uses new email addresses, so it can be repeated.
"""
import argparse
import concurrent.futures
import io
import json
import random
import statistics
import sys
import threading
import time

import requests

PASSWORD = "load-test-password-1"
LIMIT_SECONDS = 1.0          # NFR-01
MAX_ERROR_RATE = 0.01


class Client:
    """One user: a requests session that also sends the CSRF header when the server hands out the cookie."""

    def __init__(self, args):
        self.args, self.session, self.token = args, requests.Session(), None
        self.session.verify = not args.insecure
        self.csrf_loaded = False

    def call(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if method != "GET" and not self.csrf_loaded:
            self.session.get(self.args.base_url + "/", timeout=30)            # the interface sets the CSRF cookie
            self.csrf_loaded = True
        cookie = self.session.cookies.get("csrf_token")
        if cookie and method != "GET":
            headers["X-CSRF-Token"] = cookie
        started = time.perf_counter()
        response = self.session.request(method, self.args.base_url + self.args.prefix + path, headers=headers,
                                        timeout=60, **kwargs)
        return response, time.perf_counter() - started

    def register_and_login(self, email, name):
        self.call("POST", "/auth/register", json={"name": name, "email": email, "password": PASSWORD, "affiliation": "Load"})
        response, seconds = self.call("POST", "/auth/login", json={"email": email, "password": PASSWORD})
        response.raise_for_status()
        self.token = response.json()["token"]
        return seconds


def tiny_pdf(number):
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": f"Paper {number}"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def seed(args, run):
    """Chair, conference, reviewers, authors, papers and assignments. Returns everything the load phase needs."""
    chair = Client(args)
    chair.register_and_login(f"load-chair-{run}@example.com", "Load Chair")
    deadlines = {"submission_deadline": "2099-01-01T00:00:00Z", "review_deadline": "2099-02-01T00:00:00Z",
                 "notification_deadline": "2099-03-01T00:00:00Z"}
    conference = chair.call("POST", "/conferences", json={"name": f"Load test {run}", **deadlines})[0].json()["id"]

    def make_user(kind_index):
        kind, i = kind_index
        client = Client(args)
        client.register_and_login(f"load-{kind}-{run}-{i}@example.com", f"Load {kind} {i}")
        return kind, client

    print(f"seeding: {args.reviewers} reviewers, {args.authors} authors, {args.submissions} papers ...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        made = list(pool.map(make_user, [("rev", i) for i in range(args.reviewers)] + [("auth", i) for i in range(args.authors)]))
    reviewers = [c for k, c in made if k == "rev"]
    authors = [c for k, c in made if k == "auth"]

    def invite(reviewer):
        email = f"load-rev-{run}-{reviewers.index(reviewer)}@example.com"
        invitation = chair.call("POST", f"/conferences/{conference}/invitations", json={"email": email})[0].json()["id"]
        reviewer.call("POST", f"/invitations/{invitation}/accept")
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        list(pool.map(invite, reviewers))

    pdf = tiny_pdf(0)

    def submit(number):
        author = authors[number % len(authors)]
        response, _ = author.call("POST", f"/conferences/{conference}/submissions", data={"title": f"Paper {number}", "abstract": "x" * 300},
                                  files={"pdf": ("paper.pdf", pdf, "application/pdf")})
        return response.json()["id"] if response.status_code == 201 else None
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        submissions = [s for s in pool.map(submit, range(args.submissions)) if s]

    def assign(submission):
        reviewer = reviewers[submission % len(reviewers)]
        email = f"load-rev-{run}-{reviewers.index(reviewer)}@example.com"
        chair.call("POST", f"/conferences/{conference}/submissions/{submission}/assignments", json={"reviewer_email": email})
    if reviewers:
        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            list(pool.map(assign, submissions))
    print(f"seeded: {len(submissions)} papers, conference {conference}", flush=True)
    return {"chair": chair, "reviewers": reviewers, "authors": authors, "conference": conference,
            "submissions": submissions}


def pdf_download_path(client, seed_data):
    response, _ = client.call("GET", "/assignments")
    items = response.json() if response.status_code == 200 else []
    return f"/assignments/{items[0]['assignment_id']}/pdf" if items else None


def user_actions(role, client, data):
    """The normal things this kind of user does. Each is (label, method, path)."""
    conf = data["conference"]
    common = [("conferences list", "GET", "/conferences"), ("conference details", "GET", f"/conferences/{conf}"),
              ("my profile", "GET", "/auth/me"), ("my invitations", "GET", "/invitations")]
    if role == "chair":
        return common + [("all submissions (chair)", "GET", f"/conferences/{conf}/submissions"),
                         ("review progress", "GET", f"/conferences/{conf}/review-progress"),
                         ("committee", "GET", f"/conferences/{conf}/committee"),
                         ("results overview", "GET", f"/conferences/{conf}/results")]
    if role == "reviewer":
        pdf = pdf_download_path(client, data)
        return common + [("my assigned papers", "GET", "/assignments")] + ([("anonymous pdf download", "GET", pdf)] if pdf else [])
    return common + [("my submissions", "GET", "/submissions")]


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


def load_phase(args, data):
    """50 virtual users use the system for --duration seconds. Returns the measurements."""
    users = [("chair", data["chair"])]
    others = [("reviewer", r) for r in data["reviewers"]] + [("author", a) for a in data["authors"]]
    random.shuffle(others)
    users += others[: args.users - 1]
    results, lock = [], threading.Lock()
    stop_at = time.perf_counter() + args.duration
    ready = threading.Barrier(len(users))

    def run(role_client):
        role, client = role_client
        actions = user_actions(role, client, data)
        ready.wait()                                          # everybody starts at the same moment
        while time.perf_counter() < stop_at:
            label, method, path = random.choice(actions)
            try:
                response, seconds = client.call(method, path)
                outcome = (label, response.status_code, seconds)
            except requests.RequestException:
                outcome = (label, 0, 30.0)
            with lock:
                results.append(outcome)
            time.sleep(random.uniform(0, args.think))

    with concurrent.futures.ThreadPoolExecutor(len(users)) as pool:
        list(pool.map(run, users))
    return results


def summarize(results, args):
    rows = {}
    for label, status, seconds in results:
        rows.setdefault(label, []).append((status, seconds))
    lines = [f"{'request':28} {'count':>6} {'errors':>6} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7}"]
    for label, values in sorted(rows.items()):
        times = [s for _, s in values]
        errors = sum(1 for st, _ in values if st == 0 or st >= 500)
        lines.append(f"{label:28} {len(values):>6} {errors:>6} {percentile(times, 50):>7.3f} {percentile(times, 95):>7.3f} "
                     f"{percentile(times, 99):>7.3f} {max(times):>7.3f}")
    times = [s for _, _, s in results]
    errors = sum(1 for _, st, _ in results if st == 0 or st >= 500)
    p95 = percentile(times, 95)
    rate = errors / len(results)
    lines.append("-" * 70)
    lines.append(f"{'ALL':28} {len(results):>6} {errors:>6} {percentile(times, 50):>7.3f} {p95:>7.3f} {percentile(times, 99):>7.3f} {max(times):>7.3f}")
    ok = p95 <= LIMIT_SECONDS and rate <= MAX_ERROR_RATE
    lines += ["", f"requests per second: {len(results) / args.duration:.1f}   error rate: {rate:.2%}",
              f"NFR-01 (95% within {LIMIT_SECONDS:.0f} s with {args.users} users): {'MET' if p95 <= LIMIT_SECONDS else 'NOT MET'} (p95 = {p95:.3f} s)"]
    return "\n".join(lines), ok, {"p95": p95, "error_rate": rate, "requests": len(results)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:5002", help="server to test (default: CMT service)")
    ap.add_argument("--prefix", default="", help="path before every API call, /api/cmt for orchestrator or interface")
    ap.add_argument("--users", type=int, default=50, help="users at the same time (NFR-01: 50)")
    ap.add_argument("--duration", type=int, default=30, help="seconds of load")
    ap.add_argument("--think", type=float, default=0.2, help="longest pause between requests of one user")
    ap.add_argument("--submissions", type=int, default=100)
    ap.add_argument("--reviewers", type=int, default=50)
    ap.add_argument("--authors", type=int, default=30)
    ap.add_argument("--full", action="store_true", help="NFR-03 size: 1000 submissions, 300 reviewers")
    ap.add_argument("--insecure", action="store_true", help="accept a self-signed HTTPS certificate")
    ap.add_argument("--json", help="also write the numbers to this file")
    args = ap.parse_args()
    if args.full:
        args.submissions, args.reviewers = 1000, 300
    args.users = min(args.users, args.reviewers + args.authors + 1)

    run = int(time.time())
    started = time.perf_counter()
    data = seed(args, run)
    print(f"seeding took {time.perf_counter() - started:.0f} s. Load: {args.users} users for {args.duration} s ...", flush=True)
    results = load_phase(args, data)
    text, ok, numbers = summarize(results, args)
    print("\n" + text)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"papers": len(data["submissions"]), "reviewers": len(data["reviewers"]), "users": args.users,
                       "duration": args.duration, **numbers}, f, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

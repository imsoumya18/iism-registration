#!/usr/bin/env python3
"""
IISM Gulmarg registration bot.

Opens Chrome, fills the IISM admission form the fast way, attaches the documents and submits.
Every value (site URL, form field IDs, target course/batches, personal details, file paths,
timings) comes from config.json. Nothing is hard-coded here. See README.md.

    python iism_register.py                    practice: fill everything, stop before Submit
    python iism_register.py --submit           fill and submit
    python iism_register.py --submit --at      wait for target.booking_opens, then fill and submit
    python iism_register.py --submit --watch   reload until the course appears, then go

Dropdown labels may differ from the patterns in config.target. The script tries to match them
timing.match_attempts times (reloading in between). If it still can't, it fills every other
field and hands the dropdowns to you: pick Course, Level, Schedule and State in the browser,
and once they've settled it sets the DOB, attaches the files, checks everything and (with
--submit) submits. Files and DOB are done AFTER your picks because every dropdown change
reloads the page, which wipes attached files and clears the DOB.

Site behaviour this relies on (ASP.NET WebForms, observed Oct 2026):
  * Course change -> page reload, fills the Level and Schedule lists.
  * Schedule change -> page reload, clears DOB. Level, Schedule and State are sent together in it.
  * DOB change -> page reload, calculates the fee. Must be the last reload.
  * Typed text survives reloads; attached files do NOT -> files are attached last.
  * Submitting a full batch -> page reloads with site.seat_full_regex, nothing is created.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Error as PWError

T0 = time.perf_counter()


def log(msg):
    print(f"[{time.perf_counter() - T0:6.2f}s] {msg}", flush=True)


# ----------------------------------------------------------------------------- config
class Config:
    def __init__(self, path: Path, url_override: str | None = None):
        if not path.exists():
            sys.exit(f"{path} not found. Copy config.example.json to {path.name} and fill it in.")
        c = json.loads(path.read_text(encoding="utf-8"))
        base = path.resolve().parent

        site, target, app = c["site"], c["target"], c["applicant"]
        self.ids = site["ids"]
        self.placeholder = site["placeholder_option_value"]
        self.validation_group = site["validation_group"]
        self.seat_full_re = re.compile(site["seat_full_regex"], re.I)

        self.booking_opens = target["booking_opens"]
        self.course_re = re.compile(target["course_regex"], re.I)
        self.level_re = re.compile(target["level_regex"], re.I)
        self.batches = [(b["label"], re.compile(b["regex"], re.I)) for b in target["batch_preferences"]]
        self.state_value = target["state_value"]
        self.state_label = target["state_label"]

        self.text = app["text_fields"]
        self.selects = app["select_by_text"]
        self.dob = app["dob"]
        self.tick_declaration = app["tick_declaration"]
        self.email = self.text[app["email_field"]]
        mobile = self.text[app["mobile_field"]]
        self.url = (url_override or site["form_url"]).format(mobile=mobile)

        self.files = {fid: self._resolve(base, p) for fid, p in c["files"].items()}

        t = c["timing"]
        self.nav_timeout_ms = t["nav_timeout_ms"]
        self.poll_s = t["poll_interval_s"]
        self.retry_minutes = t["retry_minutes"]
        self.session_refresh_s = t["session_refresh_s"]
        self.match_attempts = t["match_attempts"]
        self.manual_wait_s = t["manual_wait_minutes"] * 60
        self.manual_settle_s = t["manual_settle_s"]

        self.browser_channel = c["browser"]["channel"]
        self.screenshot = self._resolve(base, c["output"]["screenshot"])

    @staticmethod
    def _resolve(base: Path, p: str) -> Path:
        p = Path(p).expanduser()
        return p if p.is_absolute() else base / p

    def problems(self):
        """Empty values / missing files, reported by key only (never prints personal values)."""
        out = [f"empty applicant.text_fields.{k}" for k, v in self.text.items() if not str(v).strip()]
        out += [f"empty applicant.select_by_text.{k}" for k, v in self.selects.items() if not str(v).strip()]
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", self.dob):
            out.append("applicant.dob must be DD/MM/YYYY")
        out += [f"files.{k}: not found ({p})" for k, p in self.files.items() if not p.exists()]
        return out


# ----------------------------------------------------------------------------- page helpers
def options(page, cfg, sel_id):
    """[(value, text), ...] of a <select>, without the placeholder option."""
    return page.eval_on_selector(
        f"#{sel_id}",
        "(s, ph) => [...s.options].filter(o => o.value !== ph).map(o => [o.value, o.text.trim()])",
        cfg.placeholder,
    )


def postback(page, cfg, js, arg=None):
    """Run JS that triggers an ASP.NET postback and wait for the reloaded page."""
    with page.expect_navigation(wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms):
        page.evaluate(js, arg)


SET_AND_CHANGE = """([id, v]) => { const e = document.getElementById(id); e.value = v;
    e.dispatchEvent(new Event('change', {bubbles: true})); }"""


def open_form(page, cfg):
    page.goto(cfg.url, wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms)


def find_course(page, cfg):
    return next((o for o in options(page, cfg, cfg.ids["course"]) if cfg.course_re.search(o[1])), None)


class MatchError(RuntimeError):
    """A dropdown label didn't match the patterns in config.target."""


def select_dropdowns(page, cfg, batch_re, any_option):
    """Pick course, then level + schedule + state (2 reloads). Raises MatchError if a label doesn't match."""
    ids = cfg.ids

    # reload 1: course (skipped if the right course is already selected)
    course = find_course(page, cfg)
    if not course:
        if not any_option:
            raise MatchError(f"no course matching /{cfg.course_re.pattern}/ in "
                             f"{[t for _, t in options(page, cfg, ids['course'])]}")
        course = options(page, cfg, ids["course"])[0]
        log(f"--any: using course {course[1]}")
    if page.eval_on_selector(f"#{ids['course']}", "s => s.value") != course[0]:
        postback(page, cfg, SET_AND_CHANGE, [ids["course"], course[0]])
    log(f"Course: {course[1]}")

    # reload 2: level + schedule + state together
    levels = options(page, cfg, ids["level"])
    level = next((o for o in levels if cfg.level_re.search(o[1])), None)
    if not level:
        if not (any_option and levels):
            raise MatchError(f"no level matching /{cfg.level_re.pattern}/ in {[t for _, t in levels]}")
        level = levels[0]
    scheds = options(page, cfg, ids["schedule"])
    sched = next((o for o in scheds if batch_re.search(o[1])), None)
    if not sched:
        if not (any_option and scheds):
            raise MatchError(f"no batch matching /{batch_re.pattern}/ in {[t for _, t in scheds]}")
        sched = scheds[-1]
    states = [v for v, _ in options(page, cfg, ids["state"])]
    if cfg.state_value not in states:
        raise MatchError(f"state value {cfg.state_value!r} not in the State list")
    postback(
        page, cfg,
        """([ids, level, state, sched]) => { const g = id => document.getElementById(id);
            g(ids.level).value = level; g(ids.state).value = state;
            const s = g(ids.schedule); s.value = sched;
            s.dispatchEvent(new Event('change', {bubbles: true})); }""",
        [ids, level[0], cfg.state_value, sched[0]],
    )
    log(f"Level: {level[1]} | Batch: {sched[1]} | State: {cfg.state_label}")


def fill_fields(page, cfg):
    """Text fields, non-reloading dropdowns and the declaration. Survives reloads."""
    page.evaluate(
        """([text, sel, declId, tick]) => { const g = id => document.getElementById(id);
            for (const [id, v] of Object.entries(text)) { const e = g(id); if (e && !e.readOnly) e.value = v; }
            for (const [id, t] of Object.entries(sel)) {
                const s = g(id); const o = s && [...s.options].find(o => o.text.trim() === t);
                if (o) s.value = o.value; }
            if (tick) g(declId).checked = true; }""",
        [cfg.text, cfg.selects, cfg.ids["declaration"], cfg.tick_declaration],
    )


def finish_form(page, cfg):
    """After the dropdowns are set: fields, DOB (last reload), files, then a status dict."""
    ids = cfg.ids
    fill_fields(page, cfg)
    postback(page, cfg, SET_AND_CHANGE, [ids["dob"], cfg.dob])
    log("All fields filled, DOB set")

    # attach files last: every reload wipes them
    for input_id, path in cfg.files.items():
        page.set_input_files(f"#{input_id}", str(path))
    log(f"{len(cfg.files)} files attached")

    return page.evaluate(
        """([ids, fileIds, group]) => { const g = id => document.getElementById(id);
            return {
                valid: typeof Page_ClientValidate === 'function' ? Page_ClientValidate(group) : null,
                missingFiles: fileIds.filter(id => !g(id) || !g(id).files.length),
                seats: g(ids.seats).value, fee: g(ids.fee).value,
                course: g(ids.course).selectedOptions[0].text,
                level: g(ids.level).selectedOptions[0].text,
                batch: g(ids.schedule).selectedOptions[0].text,
                state: g(ids.state).selectedOptions[0].text,
                dobSet: !!g(ids.dob).value }; }""",
        [ids, list(cfg.files), cfg.validation_group],
    )


def read_dropdowns(page, cfg):
    """Current values of the 4 reloading dropdowns, or None while the page is mid-reload."""
    try:
        return page.evaluate(
            """(ids) => [ids.course, ids.level, ids.schedule, ids.state]
                   .map(id => document.getElementById(id)?.value ?? null)""",
            cfg.ids,
        )
    except PWError:
        return None


def manual_dropdowns(page, cfg):
    """Fill everything else, then wait for the human to pick the dropdowns. Returns the status dict or None."""
    try:
        fill_fields(page, cfg)
    except PWError:
        pass
    print("\n" + "=" * 70 +
          "\n  >>> PICK THE DROPDOWNS IN THE BROWSER: Course, Level, Schedule, State <<<"
          "\n  Text fields are filled. Don't attach files or set DOB: the script does that"
          "\n  after your picks (each dropdown change reloads the page and wipes them).\n" +
          "=" * 70, flush=True)
    deadline = time.time() + cfg.manual_wait_s
    last, stable_since = None, None
    while time.time() < deadline:
        vals = read_dropdowns(page, cfg)
        done = vals is not None and all(v not in (None, "", cfg.placeholder) for v in vals)
        if done and vals == last:
            stable_since = stable_since or time.time()
            if time.time() - stable_since >= cfg.manual_settle_s:
                break
        else:
            stable_since = None
        last = vals
        time.sleep(0.3)
    else:
        log(f"No dropdown selection within {cfg.manual_wait_s // 60} min.")
        return None
    try:
        page.wait_for_load_state("domcontentloaded")
    except PWError:
        pass
    log("Dropdowns picked; finishing the form")
    return finish_form(page, cfg)


def submit(page, cfg):
    with page.expect_navigation(wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms):
        page.click(f"#{cfg.ids['submit']}")
    body = page.inner_text("body")
    return ("FULL" if cfg.seat_full_re.search(body) else "SENT"), body


def check_and_submit(page, cfg, r, practice):
    """Guards, then submit. Returns 'DONE', 'FULL' or 'SKIP'."""
    log(f"READY: {r}")
    problems = []
    if r["valid"] is False:
        problems.append("site validation failed")
    if r["missingFiles"]:
        problems.append(f"missing files {r['missingFiles']}")
    if not r["dobSet"]:
        problems.append("DOB not set")
    if not practice and r["seats"] in ("", "0"):
        problems.append("0 seats")
    if not practice and r["fee"] in ("", "0"):
        problems.append("fee is 0")
    if problems:
        log(f"Not submitting: {', '.join(problems)}")
        return "SKIP"
    if practice:
        log("PRACTICE MODE: stopping before Submit. Run with --submit to send.")
        return "DONE"
    try:
        status, body = submit(page, cfg)
    except PWTimeout:
        # It MAY have gone through; a retry could create a duplicate application.
        log("Submit timed out. NOT retrying (could duplicate). Check the browser window and your email.")
        return "DONE"
    page.screenshot(path=str(cfg.screenshot), full_page=True)
    log(f"Submit result: {status}")
    if status == "SENT":
        log(f"Submitted! Check {cfg.email} for the payment link. Screenshot: {cfg.screenshot}")
        print(body[:1500])
        return "DONE"
    log("Batch full")
    return "FULL"


def labels_match(page, cfg):
    """Probe: does the course match, and after selecting it, do the level and at least one batch match?"""
    course = find_course(page, cfg)
    if not course:
        return False
    postback(page, cfg, SET_AND_CHANGE, [cfg.ids["course"], course[0]])
    levels = options(page, cfg, cfg.ids["level"])
    scheds = options(page, cfg, cfg.ids["schedule"])
    return (any(cfg.level_re.search(t) for _, t in levels)
            and any(rx.search(t) for _, rx in cfg.batches for _, t in scheds))


def try_batches(page, cfg, practice, any_option):
    """One round. True when finished (submitted, practice READY, or unsafe to retry)."""
    global T0
    T0 = time.perf_counter()

    # Up to match_attempts tries to recognise the dropdown labels; else hand them to the human.
    auto = any_option
    for attempt in range(1, cfg.match_attempts + 1):
        if auto:
            break
        try:
            auto = labels_match(page, cfg)
        except (PWTimeout, PWError) as e:
            log(f"Match attempt {attempt}: {e}")
        if not auto:
            log(f"Match attempt {attempt}/{cfg.match_attempts}: dropdown labels don't match config.target")
            if attempt < cfg.match_attempts:
                try:
                    open_form(page, cfg)
                except PWTimeout:
                    pass

    if not auto:
        while True:   # manual mode: the human picks; repeat if their pick turns out to be full
            r = manual_dropdowns(page, cfg)
            if r is None:
                return False
            res = check_and_submit(page, cfg, r, practice)
            if res == "DONE":
                return True
            log("Pick another batch (or fix the problem above) in the browser.")

    for label, batch_re in cfg.batches:
        T0 = time.perf_counter()
        log(f"Trying {label}")
        try:
            select_dropdowns(page, cfg, batch_re, any_option)
            r = finish_form(page, cfg)
        except MatchError as e:
            log(f"Skipped: {e}")
            open_form(page, cfg)
            continue
        except (PWTimeout, PWError) as e:
            log(f"Skipped: {e}")
            try:
                open_form(page, cfg)
            except PWTimeout:
                pass
            continue
        res = check_and_submit(page, cfg, r, practice)
        if res == "DONE":
            return True
        open_form(page, cfg)
    log("Ran out of batches this round.")
    return False


def wait_until(page, cfg, when):
    start = datetime.strptime(when, "%Y-%m-%d %H:%M")
    last_refresh = time.time()
    while (left := (start - datetime.now()).total_seconds()) > 0:
        print(f"\rWaiting for {when} ... {int(left)//3600:02d}:{int(left)%3600//60:02d}:{int(left)%60:02d} left ",
              end="", flush=True)
        if time.time() - last_refresh > cfg.session_refresh_s:   # keep the session warm
            try:
                open_form(page, cfg)
            except PWTimeout:
                pass
            last_refresh = time.time()
        time.sleep(min(left, 0.2 if left < 5 else 1 if left < 120 else 30))
    print("\nGO", flush=True)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="IISM registration bot. All settings live in the config file.")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.json"),
                    help="path to the config file (default: config.json next to this script)")
    ap.add_argument("--submit", action="store_true", help="actually click Submit")
    ap.add_argument("--at", nargs="?", const="", metavar="'YYYY-MM-DD HH:MM'",
                    help="wait until this local time first (no value = target.booking_opens)")
    ap.add_argument("--watch", action="store_true", help="reload until the course appears")
    ap.add_argument("--check", action="store_true", help="only validate the config and files, then exit")
    ap.add_argument("--any", action="store_true",
                    help="practice only: if labels don't match, pick the first available options instead of handing off")
    ap.add_argument("--url", help="override site.form_url (testing against tests/mock_iism.py)")
    ap.add_argument("--headless", action="store_true", help="no visible window")
    ap.add_argument("--no-pause", action="store_true", help="close the browser at the end")
    ap.add_argument("--chromium", action="store_true", help="use Playwright's Chromium instead of browser.channel")
    args = ap.parse_args()

    cfg = Config(Path(args.config), args.url)
    issues = cfg.problems()
    if issues:
        sys.exit("Config problems:\n  " + "\n  ".join(issues))
    if args.check:
        print(f"Config OK: {len(cfg.text)} text fields, {len(cfg.selects)} dropdowns, "
              f"{len(cfg.files)} files, {len(cfg.batches)} batches, opens {cfg.booking_opens}")
        return
    practice = not args.submit
    if args.any and not practice:
        sys.exit("--any is for practice only; it is refused with --submit.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=None if args.chromium else cfg.browser_channel,
                                     headless=args.headless)
        page = browser.new_page()
        page.set_default_timeout(cfg.nav_timeout_ms)
        page.on("dialog", lambda d: (log(f"Dialog: {d.message}"), d.accept()))

        open_form(page, cfg)
        log("Form loaded (browser warmed up)")

        if args.at is not None:
            wait_until(page, cfg, args.at or cfg.booking_opens)
            open_form(page, cfg)
            log("Form reloaded at opening time")

        if args.watch:
            while not find_course(page, cfg):
                log("Course not listed yet; reloading...")
                time.sleep(cfg.poll_s)
                try:
                    open_form(page, cfg)
                except PWTimeout:
                    log("Reload timed out; retrying")
            log("Course is LIVE")

        deadline = time.time() + cfg.retry_minutes * 60
        done = try_batches(page, cfg, practice, args.any)
        rounds = 1
        while not done and not practice and time.time() < deadline:
            rounds += 1
            log(f"Round {rounds}: nothing went through yet; retrying")
            time.sleep(cfg.poll_s)
            try:
                open_form(page, cfg)
            except PWTimeout:
                continue
            done = try_batches(page, cfg, practice, args.any)
        if not done and not practice:
            log(f"Gave up after {cfg.retry_minutes} min. Open the form manually and check the batches.")

        if not args.no_pause:
            input("\nBrowser left open. Press Enter to close... ")
        browser.close()


if __name__ == "__main__":
    main()

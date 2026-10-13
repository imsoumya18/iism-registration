# IISM Gulmarg Skiing Registration Bot

Automates the online admission form of the **Indian Institute of Skiing & Mountaineering (IISM), Gulmarg** so that Soumyadeep Biswas can grab a seat in the **Basic Snow Skiing course** the moment registration opens. Seats are first-come, first-served and fill within minutes.

## Key facts (from IISM's published FY 2026-27 schedule)

- **Booking opens: Thursday 12 November 2026 at 10:00 IST**
- Target batches (Gulmarg, 14 days), tried in this order:
  1. **Batch 3: 17 Jan – 30 Jan 2027**, Basic SSC 50 seats
  2. **Batch 4: 31 Jan – 13 Feb 2027**, Basic SSC 40 seats
- Fee: ₹10,000 (age up to 25) / ₹20,000 (age above 25). The site calculates it from the DOB. At 25 the applicant should fall in the ₹10,000 band, but confirm the fee shown before paying. Age eligibility 10–40.
- **The day-of command:**
  ```bash
  python iism_register.py --submit --at
  ```
  Start it any time before 10:00. Chrome opens and loads the form, counts down to 10:00:00, then fills and submits. If batch 3 is full it moves to batch 4. If neither has opened yet, it keeps retrying every second for 15 minutes.

The script opens Google Chrome, fills the form, attaches documents, checks everything, and submits. Most runs take 2–3 seconds.

---

## Instructions for Claude Code

Please do the following in this folder, in order, and report the results:

1. **Check the files.**
   - `files/` must contain the four JPGs listed below, each **under 500 KB** (the site's limit). Report each one's size.
   - `config.json` must exist. Run `python iism_register.py --check`, which validates every value and file path and reports problems by key only, never printing personal values.
   - Both are git-ignored on purpose because they contain personal documents and data. **Never commit them, and never remove them from `.gitignore`.**
2. **Set up the environment.**
   - Create a virtualenv in `.venv`.
   - `pip install -r requirements.txt`
   - `playwright install chromium`
   - Check whether Google Chrome is installed. If not, run `playwright install chrome` or tell the user.
3. **Run the offline tests.** They never touch the real site. Each test needs a fresh mock server (stop the previous one first, e.g. `kill %1`):
   ```bash
   # A. labels match: automatic
   python tests/mock_iism.py &
   python iism_register.py --url "http://127.0.0.1:8765/?m={mobile}" --headless --no-pause --chromium                 # practice: READY, valid True, stops before Submit
   python iism_register.py --submit --url "http://127.0.0.1:8765/?m={mobile}" --headless --no-pause --chromium        # batch 3 FULL -> batch 4 -> "Submit result: SENT", files=5
   kill %1

   # B. labels DON'T match: 2 attempts, then hand-off; the mock's --fake-human picks the dropdowns
   python tests/mock_iism.py --odd-labels --fake-human &
   python iism_register.py --submit --url "http://127.0.0.1:8765/?m={mobile}" --headless --no-pause --chromium        # "Match attempt 1/2", "2/2", PICK THE DROPDOWNS banner,
                                              # "Dropdowns picked", then SENT with files=5
   kill %1

   # C. 0 seats everywhere: must never submit (stop it with Ctrl-C after a few rounds)
   python tests/mock_iism.py --seats 0 &
   python iism_register.py --submit --url "http://127.0.0.1:8765/?m={mobile}" --headless --no-pause --chromium        # "Not submitting: 0 seats", and NO "Submit result" line
   kill %1
   ```
   - Optional timed test: add `--at "<a minute from now, YYYY-MM-DD HH:MM>"` to test A's `--submit` run. It should count down, then submit within about 1 s of the minute.
4. **Run a live practice against the real site, in practice mode only.**
   ```bash
   python iism_register.py --any
   ```
   - Until skiing registration opens, the only course listed is "Adventure Courses". `--any` (practice only) picks the first available options so the run can rehearse, and it stops before Submit.
   - Confirm it reaches `READY` with `valid: True` and no missing files.
   - Without `--any`, practice mode hands the dropdowns to a human (see "Dropdown hand-off") and waits for them.
   - **Do not pass `--submit` against the real site** unless the user explicitly asks. A submission on a batch with seats creates a real application, and IISM allows one course per person per year.
5. Report the timings printed on each log line, and any errors.

---

## Project layout

```
iism-registration/
├── README.md              ← this file
├── iism_register.py       ← the bot
├── requirements.txt       ← playwright
├── config.json            ← ALL settings + personal details + file paths (git-ignored)
├── config.example.json    ← template for config.json (committed, no personal data)
├── .gitignore
├── tests/
│   └── mock_iism.py       ← offline fake of the IISM form (--seats N, --odd-labels, --fake-human)
└── files/                 ← documents uploaded with the form (all JPG, < 500 KB)
    ├── dob_certificate.jpg            → "Date of birth" proof (birth certificate)
    ├── qualification_certificate.jpg  → "Education qualification" proof (B.Tech degree)
    ├── photo.jpg                      → "Photo of Applicant"
    └── signature.jpg                  → "Signature of Applicant" AND "Parent/Guardian's Signature"
```

---

## Usage

| Command | What it does |
|---|---|
| `python iism_register.py` | **Practice.** Fills everything, stops before Submit. |
| `python iism_register.py --submit` | **Real.** Fills and submits. Tries the Jan batch, then Feb if Jan is full. |
| `python iism_register.py --submit --at` | **Registration day.** Waits until `target.booking_opens` (12 Nov 2026 10:00) with Chrome already open, then fills and submits. Retries every `poll_interval_s` for up to `retry_minutes` if no batch is open yet. |
| `python iism_register.py --submit --at "2026-11-12 09:59"` | Same, with a custom start time (local machine time). |
| `python iism_register.py --submit --watch` | Reloads every second until a skiing course appears in the list, then goes. |

Extra flags: `--check` (validate the config and files, then exit), `--any` (practice only: pick the first available dropdown options instead of handing off), `--config PATH`, `--url URL` (override `site.form_url`, for the mock), `--headless`, `--no-pause`, `--chromium` (use Playwright's Chromium instead of `browser.channel`).

**On a submit timeout the script stops instead of retrying,** because the application may already have gone through and a retry could create a duplicate. Check the browser window and Gmail.

After a real submit the browser stays open and a screenshot is saved to `iism_after_submit.png`. The **payment link is emailed to the address in `config.json`**. Pay promptly.

---

## Dropdown hand-off (when the labels don't match)

On the day, IISM's dropdown labels may differ from the patterns in `config.json` (`target.course_regex`, `level_regex`, `batch_preferences`). Here's what happens:

1. The script tries to match the labels `timing.match_attempts` (2) times, reloading the form between tries.
2. If they still don't match, it **fills every other field** (text, Education, Food, declaration) and prints a banner: **PICK THE DROPDOWNS IN THE BROWSER**.
3. **You pick Course, Level, Schedule and State** in the Chrome window. Each pick reloads the page, and that's fine because the text survives.
4. Once all four are set and unchanged for `timing.manual_settle_s` (1.5 s), the script **sets the DOB, attaches the 5 files, runs the checks, and submits** (with `--submit`).
5. If your batch is full, it waits for you to pick another one.
6. It gives up after `timing.manual_wait_minutes` (10).

**Don't set the DOB or attach files yourself.** Every dropdown change reloads the page, which clears the DOB and wipes attached files. That's why the script does them after your picks.

Note: `--watch` waits for a course matching `target.course_regex`. If the course name might differ, use `--at` instead.

## How the IISM site works

These findings come from manual testing on 7 Oct 2026. The script depends on them.

- **Direct form URL works.** `https://admission.iismgulmarg.in/admission_form.aspx?mobile=<10-digit mobile>` skips the instructions page, the T&C page and the mobile step. No OTP is enforced.
- **The site is ASP.NET WebForms.** Some fields cause a full-page postback (a reload) when changed:
  - `ddlCourse`: reloads and fills the Level and Schedule lists.
  - `ddlScheduleCourse`: reloads, and **clears the date of birth**.
  - `ddlState`: reloads.
  - `txtDateOfBirth`: reloads and **calculates the course fee**, so it must be the last reload.
- **Fastest correct order (3 reloads):**
  1. Set Course and trigger its reload.
  2. Set Level, Schedule and State together, then trigger the Schedule reload. ASP.NET posts every field value, so State goes through in the same reload.
  3. Fill all text fields and dropdowns, tick the declaration, set DOB, then trigger the DOB reload.
  4. Attach the files.
- **Typed text, dropdowns and the declaration tick survive reloads. Attached files do not.** Every reload wipes them, so files go last.
- **"State" means the course location**, so it's set to `JAMMU AND KASHMIR` (value `1`). It didn't change the fee.
- **Client validation** (`Page_ClientValidate('sv')`) requires a **Parent/Guardian's Signature** even for adults. The applicant's own signature is uploaded there.
- **The server checks seats on Submit.** If the batch is full, the page reloads with **"Seat Not Available"** and no application is created.
- Each reload took about 0.6 s on a normal day. On launch day expect it to be slower. The script waits for each reload to finish (up to 60 s) rather than using fixed sleeps.

### Field IDs

| Section | IDs |
|---|---|
| Course | `ddlCourse`, `ddlCourseLevel`, `ddlScheduleCourse`, `ddlState`, `txtAvailableSeat` (read-only), `txtCourseFee` (read-only) |
| Basic | `txtName`, `txtFatherName`, `txtNationality`, `txtDateOfBirth` (dd/MM/yyyy), `FupBirthCertificate`, `txtDomicile`, `FupDomicileAttachProof` (optional) |
| Address | `txtHouseNoApartment`, `txtStreetVillageColony`, `txtDistrictCity`, `txtPincode`, `txtMobileNo`, `txtEmailId` |
| Academic | `ddlEduationQualification`, `FupEducationQualification`, `FupAnyAdventureCoursePhoto` (optional), `FupCurrentEmployment` (optional) |
| Identification | `ddlVegetarianOrNon`, `txtBloodGroup`, `txtMedicalCategory`, `txtHeight`, `txtWeight`, `txtWaist`, `txtShoeSize` |
| Photo/Signature | `FupPhototOfApplicant`, `FupSignatureOfAppicant` |
| Indemnity | `txtEmergencyContactName`, `txtEmergencyContactPhone`, `FupParentGuardianSignature` (required) |
| Declaration | `chkIAgree`, then `btnSubmit` |

(The misspellings `Eduation`, `Phototo` and `Appicant` are the site's own.)

---

## Configuration (`config.json`)

**The script has no hard-coded values.** Everything it uses is read from `config.json`, which is git-ignored. `config.example.json` is the committed template, with the personal fields left blank.

| Section | What's in it |
|---|---|
| `site` | `form_url` (`{mobile}` is filled from the applicant's mobile), the form's field `ids`, `placeholder_option_value`, `validation_group`, `seat_full_regex` |
| `target` | `booking_opens` (`YYYY-MM-DD HH:MM`, local time), `course_regex`, `level_regex`, `batch_preferences` (ordered list of `{label, regex}`), `state_value` / `state_label` |
| `applicant` | `text_fields` and `select_by_text` keyed by form field ID, `dob` (`DD/MM/YYYY`), `mobile_field`, `email_field`, `tick_declaration` |
| `files` | form file-input ID → path. Relative paths resolve from the config file's folder; absolute and `~` paths also work |
| `timing` | `nav_timeout_ms`, `poll_interval_s`, `retry_minutes`, `session_refresh_s`, `match_attempts` (label-match tries before hand-off), `manual_wait_minutes`, `manual_settle_s` |
| `browser` | `channel` (`chrome`) |
| `output` | `screenshot` path |

To set it up on a new machine: `cp config.example.json config.json`, fill in `applicant`, put the documents where `files` points, then run `python iism_register.py --check`.
Use `--config other.json` to run with a different config.

## Before the real day

- [x] Batch matching set to the published dates in `config.json`: `17 Jan` (batch 3), then `31 Jan` (batch 4).
- [ ] **Check the course and level names** once the dropdown is live (it may appear before 10:00 on the day). `target.course_regex` (`ski`) and `target.level_regex` (`basic`) in `config.json` assume something like "Snow Skiing" / "Basic". "Snow Boarding" doesn't match `ski`, which is correct. If the batch text isn't "17 Jan" / "31 Jan" in some form, fix `target.batch_preferences`.
- [ ] Run the practice mode once on the real site from this machine.
- [ ] Make sure the Mac's clock is correct (System Settings → General → Date & Time → set automatically).
- [ ] **On 12 Nov, before 10:00:** plug in the laptop, stop it sleeping (`caffeinate -dims` in another terminal), close heavy apps, and run `python iism_register.py --submit --at`. Watch Gmail for the payment link and pay promptly.

## Safety guards (the script refuses to submit if any of these fail)

- The site's own validation (`Page_ClientValidate`) must pass.
- All 5 file inputs must have a file attached.
- Seats must be above 0 and the fee must be above 0 (`--submit` only).
- In `--submit` mode the course must match `target.course_regex`, the level `target.level_regex`, and the batch one of `target.batch_preferences` (17 Jan / 31 Jan). If no batch matches after `match_attempts` tries, the dropdowns are handed to you rather than guessed. `--any` (pick whatever exists) only works in practice mode and is refused with `--submit`.

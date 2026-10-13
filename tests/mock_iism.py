"""Local mock of IISM admission_form.aspx for testing iism_register.py. Never touches the real site.

Mimics: full-page ASP.NET postbacks, schedule change clearing DOB, DOB postback setting the fee,
files wiped on every postback, 'Seat Not Available' on submit for a full batch.
Batch 3 (17 Jan, value 81) is always full, so --submit must fall back to batch 4 (31 Jan).

Usage:
  python tests/mock_iism.py [--seats N] [--odd-labels] [--fake-human]   -> http://127.0.0.1:8765/

  --odd-labels  dropdown labels that DON'T match config.target, to test the hand-off
  --fake-human  simulates a person picking the dropdowns once the script hands off
                (waits until the text fields are filled, then picks batch 4 + J&K)
"""
import argparse
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, HTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--seats", type=int, default=5)
ap.add_argument("--odd-labels", action="store_true")
ap.add_argument("--fake-human", action="store_true")
ap.add_argument("--port", type=int, default=8765)
ARGS = ap.parse_args()

if ARGS.odd_labels:
    COURSE, LEVELS = "Snow Programmes 2026-27", [("11", "Beginner (SSC)"), ("12", "Middle (SSC)")]
    BATCHES = [("81", "Batch-III (2027)"), ("82", "Batch-IV (2027)")]
else:
    COURSE, LEVELS = "Skiing Courses", [("11", "Basic"), ("12", "Intermediate")]
    BATCHES = [("81", "Course from 17 Jan to 30 Jan 2027"), ("82", "Course from 31 Jan to 13 Feb 2027")]

TEXT_IDS = ["txtName", "txtFatherName", "txtNationality", "txtDomicile", "txtHouseNoApartment",
            "txtStreetVillageColony", "txtDistrictCity", "txtPincode", "txtMobileNo", "txtEmailId",
            "txtBloodGroup", "txtMedicalCategory", "txtHeight", "txtWeight", "txtWaist", "txtShoeSize",
            "txtEmergencyContactName", "txtEmergencyContactPhone"]
FILE_IDS = ["FupBirthCertificate", "FupEducationQualification", "FupPhototOfApplicant",
            "FupSignatureOfAppicant", "FupParentGuardianSignature"]

FAKE_HUMAN_JS = """
setInterval(() => {
  const g = id => document.getElementById(id);
  if (!g('txtName').value || window.__busy) return;        // wait for the script's hand-off fill
  if (g('ddlCourse').value === '0') { window.__busy = 1; g('ddlCourse').value = '3'; __doPostBack('ddlCourse',''); }
  else if (g('ddlScheduleCourse').value === '0') {
    window.__busy = 1; g('ddlCourseLevel').value = '11'; g('ddlState').value = '1';
    g('ddlScheduleCourse').value = '82'; __doPostBack('ddlScheduleCourse','');
  }
}, 700);
"""


def sel(id, opts, cur, pb=True):
    oc = f"onchange=\"javascript:setTimeout('__doPostBack(\\'{id}\\',\\'\\')', 0)\"" if pb else ""
    o = "".join(f"<option value='{v}' {'selected' if v == cur else ''}>{t}</option>" for v, t in opts)
    return f"<select id='{id}' name='{id}' {oc}>{o}</select>"


def page(st, msg=""):
    c, l = st.get("ddlCourse", "0"), st.get("ddlCourseLevel", "0")
    s, stt = st.get("ddlScheduleCourse", "0"), st.get("ddlState", "0")
    courses = [("0", "Select Course"), ("9", "Adventure Courses"), ("3", COURSE)]
    # "Adventure Courses" mirrors the real site in Oct 2026: level "None", one past September batch
    levels = [("0", "Select Course Level")] + (LEVELS if c == "3" else [("16", "None")] if c == "9" else [])
    scheds = [("0", "Select Schedule Course")] + (
        BATCHES if c == "3" else [("70", "Course from 13 Sep to 26 Sep 2026")] if c == "9" else [])
    states = [("0", "Select State"), ("1", "JAMMU AND KASHMIR"), ("19", "WEST BENGAL")]
    dob = st.get("txtDateOfBirth", "") if st.get("__EVENTTARGET") != "ddlScheduleCourse" else ""
    fee = "8000" if dob else "0"
    seats = str(ARGS.seats) if s != "0" else "0"
    txt = "".join(f"<input type='text' id='{k}' name='{k}' value='{st.get(k, '')}'>" for k in TEXT_IDS)
    files = "".join(f"<input type='file' id='{k}' name='{k}'>" for k in FILE_IDS)
    human = f"<script>{FAKE_HUMAN_JS}</script>" if ARGS.fake_human else ""
    return f"""<html><body><div>{msg}</div><form id='form1' method='post' enctype='multipart/form-data'>
<input type=hidden name=__EVENTTARGET id=__EVENTTARGET>
<script>function __doPostBack(t,a){{document.getElementById('__EVENTTARGET').value=t;document.getElementById('form1').submit();}}
function Page_ClientValidate(){{return [...document.querySelectorAll('input[type=file]')].every(f=>f.files.length);}}</script>
{sel('ddlCourse', courses, c)}{sel('ddlCourseLevel', levels, l, False)}{sel('ddlScheduleCourse', scheds, s)}{sel('ddlState', states, stt)}
<input id=txtAvailableSeat readonly value='{seats}'><input id=txtCourseFee readonly value='{fee}'>
<input type=text id=txtDateOfBirth name=txtDateOfBirth value='{dob}' onchange="javascript:setTimeout('__doPostBack(\\'txtDateOfBirth\\',\\'\\')', 0)">
{txt}{sel('ddlEduationQualification', [('0', 'Select'), ('3', 'Graduation')], st.get('ddlEduationQualification', '0'), False)}
{sel('ddlVegetarianOrNon', [('0', 'Select'), ('2', 'Non-Vegetarian')], st.get('ddlVegetarianOrNon', '0'), False)}
{files}<input type=checkbox id=chkIAgree name=chkIAgree {'checked' if st.get('chkIAgree') else ''}>
<input type=submit id=btnSubmit name=btnSubmit value=Submit></form>{human}</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        self.send(page({}))

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        st = {}
        for part in msg.iter_parts():
            name, fn = part.get_param("name", header="content-disposition"), part.get_filename()
            content = part.get_content()
            st[name] = fn if fn is not None else (content.strip() if isinstance(content, str) else "")
        if "btnSubmit" in st:
            nfiles = sum(1 for k in FILE_IDS if st.get(k))
            if ARGS.seats == 0 or st.get("ddlScheduleCourse") == "81":
                return self.send(page(st, "Seat Not Available"))
            return self.send(
                f"<html><body>Application submitted successfully. files={nfiles} "
                f"batch={st.get('ddlScheduleCourse')} dob={st.get('txtDateOfBirth')} "
                f"state={st.get('ddlState')} name_set={bool(st.get('txtName'))}</body></html>")
        self.send(page(st))


HTTPServer(("127.0.0.1", ARGS.port), H).serve_forever()

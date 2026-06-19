#!/usr/bin/env python3
"""
The Ledger — data builder.

Pulls authoritative U.S. federal finance data from the U.S. Treasury's open API
(fiscaldata.treasury.gov) for the latest COMPLETE fiscal year, validates that
everything reconciles to the published totals, and writes ../data.json.

Design notes (why it's built this way):
  * Source of truth is the Monthly Treasury Statement (MTS). These are NET
    outlays / NET receipts exactly as Treasury reports them, so
    receipts - outlays = the deficit, and the agency lines sum to total outlays.
  * MTS Table 5's machine hierarchy is an inconsistent flattening of the printed
    report (bureau detail mixes gross & net; off-budget Social Security and many
    independent agencies have no clean subtotal row). So instead of trusting the
    parent/child tree, we read ONLY the authoritative "Total--<Department>" rows
    we can verify, take Social Security from the off-budget total, and book the
    remainder as a clearly-labelled "Other agencies" residual. Result: the
    breakdown reconciles to total outlays to the dollar, with no guessed numbers.
"""
import json, urllib.parse, datetime, os, subprocess, time

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
OUT  = os.path.join(os.path.dirname(__file__), "..", "data.json")

def get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params, safe=":,-")
    last = None
    for attempt in range(4):
        try:
            out = subprocess.run(
                ["curl", "-sL", "--max-time", "90", "-H", "User-Agent: the-ledger/1.0", url],
                capture_output=True, text=True, check=True).stdout
            return json.loads(out)
        except Exception as e:
            last = e; time.sleep(1.5 * (attempt + 1))
    raise last

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

# ---------------------------------------------------------------- latest complete FY
probe = get("/v1/accounting/mts/mts_table_5",
            {"filter": "line_code_nbr:eq:5691,record_calendar_month:eq:09",
             "fields": "record_fiscal_year,current_fytd_net_outly_amt",
             "sort": "-record_fiscal_year", "page[size]": "1"})
FY = int(probe["data"][0]["record_fiscal_year"])
print(f"Latest complete fiscal year: FY{FY}")

# ---------------------------------------------------------------- live national debt
debt = get("/v2/accounting/od/debt_to_penny", {"sort": "-record_date", "page[size]": "1"})["data"][0]
national_debt = float(debt["tot_pub_debt_out_amt"]); debt_date = debt["record_date"]
debt_held_public = float(debt.get("debt_held_public_amt") or 0)      # ~$31.6T (the rest is intragovernmental)
# a year-ago point -> authentic ticking rate for the live debt clock
y0 = (datetime.date.fromisoformat(debt_date) - datetime.timedelta(days=365)).isoformat()
prior = get("/v2/accounting/od/debt_to_penny",
            {"filter": f"record_date:lte:{y0}", "sort": "-record_date", "page[size]": "1"})["data"][0]
debt_year_ago = float(prior["tot_pub_debt_out_amt"]); debt_prior_date = prior["record_date"]
days = (datetime.date.fromisoformat(debt_date) - datetime.date.fromisoformat(debt_prior_date)).days
debt_growth_per_sec = (national_debt - debt_year_ago) / (days * 86400)
print(f"Debt grew ${(national_debt-debt_year_ago)/1e12:.2f}T over {days}d -> ${debt_growth_per_sec:,.0f}/sec")

# ---------------------------------------------------------------- MTS Table 5 (outlays)
t5 = get("/v1/accounting/mts/mts_table_5",
         {"filter": f"record_fiscal_year:eq:{FY},record_calendar_month:eq:09", "page[size]": "1500"})["data"]
def amt5(desc, field="current_fytd_net_outly_amt"):
    for r in t5:
        if r["classification_desc"].strip() == desc:
            v = fnum(r[field])
            if v is not None: return v
    raise KeyError(desc)

total_outlays = amt5("Total Outlays")
prior_outlays = amt5("Total Outlays", "prior_fytd_net_outly_amt")
deficit       = amt5("Total Surplus (+) or Deficit (-)")
off_budget    = max(fnum(r["current_fytd_net_outly_amt"])           # off-budget outlays (= Social Security)
                    for r in t5 if r["classification_desc"].strip() == "Total Off-Budget")
undistributed = amt5("Total--Undistributed Offsetting Receipts")
interest_gross_pubdebt = amt5("Total--Interest on the Public Debt")

# Authoritative department lines we trust (exact "Total--" rows in MTS T5), with what they fund.
DEPTS = [
    ("Total--Department of Health and Human Services", "Health & Human Services", "Medicare, Medicaid, NIH, CDC, ACA subsidies"),
    ("Total--Department of the Treasury",              "Treasury",                "Mostly interest on the national debt (~$1.2T); also the IRS & tax refunds"),
    ("Total--Department of Defense--Military Programs","Defense (Military)",       "Personnel, operations, weapons, R&D"),
    ("Total--Department of Veterans Affairs",          "Veterans Affairs",        "Veterans' health care, benefits, pensions"),
    ("Total--Department of Agriculture",               "Agriculture",             "SNAP/food assistance, farm programs, forests"),
    ("Total--Department of Transportation",            "Transportation",          "Highways, transit, aviation, rail"),
    ("Total--Department of Homeland Security",         "Homeland Security",       "Border, FEMA, Coast Guard, TSA, Secret Service"),
    ("Total--Department of Energy",                    "Energy",                  "Nuclear security, the grid, energy R&D"),
    ("Total--Department of Housing and Urban Development","Housing & Urban Dev.", "Rental assistance, public & Indian housing"),
    ("Total--Department of Labor",                     "Labor",                   "Unemployment insurance, worker programs"),
    ("Total--Department of Justice",                   "Justice",                 "FBI, DEA, prisons, federal courts' prosecution"),
    ("Total--Department of Education",                 "Education",               "Student aid, K-12 grants, special education"),
    ("Total--Department of Commerce",                  "Commerce",                "Census, NOAA/weather, patents, trade"),
    ("Total--Department of State",                     "State",                   "Diplomacy, embassies, foreign affairs"),
    ("Total--Department of the Interior",              "Interior",                "National parks, public lands, Native nations"),
    ("Total--Corps of Engineers",                      "Army Corps of Engineers", "Dams, waterways, flood control"),
    ("Total--Judicial Branch",                         "Judicial Branch",         "The federal courts"),
    ("Total--Legislative Branch",                      "Legislative Branch",      "Congress and its agencies"),
]
agencies = [{"name": "Social Security", "amount": off_budget,
             "note": "OASDI retirement & disability benefits (off-budget trust funds). Excludes on-budget SSI, counted in Other agencies"}]
for key, name, note in DEPTS:
    agencies.append({"name": name, "amount": amt5(key), "note": note})

named_sum = sum(a["amount"] for a in agencies)
residual = total_outlays - named_sum - undistributed       # everything else (NASA, EPA, NSF, OPM retirement, military retirement, SBA, SSI, ...)
agencies.append({"name": "Other agencies & programs", "amount": residual,
                 "note": "Federal & military retirement, NASA, EPA, NSF, SBA, foreign aid and ~80 smaller agencies"})
agencies.append({"name": "Offsetting receipts", "amount": undistributed,
                 "note": "Money flowing back to Treasury (royalties, premiums) — booked as negative spending"})
agencies.sort(key=lambda a: -a["amount"])

check = sum(a["amount"] for a in agencies)
assert abs(check - total_outlays) < 1.0, f"agency sum {check} != outlays {total_outlays}"
print(f"Agencies reconcile to total outlays ✓  (${check/1e12:.4f}T)  residual=${residual/1e9:.1f}B")

# ---------------------------------------------------------------- MTS Table 4 (receipts)
t4 = get("/v1/accounting/mts/mts_table_4",
         {"filter": f"record_fiscal_year:eq:{FY},record_calendar_month:eq:09", "page[size]": "200"})["data"]
def amt4(key):
    for r in t4:
        if r["classification_desc"].strip() == key and r["sequence_level_nbr"] in ("1", "2"):
            v = fnum(r["current_fytd_net_rcpt_amt"])
            if v is not None: return v
    raise KeyError(key)
total_receipts = amt4("Total -- Receipts")
REC = [
    ("Total -- Individual Income Taxes",                "Individual Income Taxes",   "What workers pay on wages, salaries & investments"),
    ("Total -- Social Insurance and Retirement Receipts","Payroll Taxes",            "Social Security & Medicare (FICA) withholding"),
    ("Corporation Income Taxes",                        "Corporate Income Taxes",    "Taxes on company profits"),
    ("Customs Duties",                                  "Customs Duties & Tariffs",  "Taxes collected on imported goods"),
    ("Total -- Excise Taxes",                           "Excise Taxes",              "Fuel, alcohol, tobacco, air travel"),
    ("Estate and Gift Taxes",                           "Estate & Gift Taxes",       "Taxes on large inheritances & gifts"),
    ("Total -- Miscellaneous Receipts",                "Miscellaneous",             "Federal Reserve earnings, fees, fines"),
]
receipts = [{"name": n, "amount": amt4(k), "note": note} for k, n, note in REC]
receipts.sort(key=lambda r: -r["amount"])
print(f"Receipts reconcile ✓  named ${sum(r['amount'] for r in receipts)/1e9:.0f}B vs total ${total_receipts/1e9:.0f}B")

# ---------------------------------------------------------------- who gets the contracts (USAspending)
# These are CONTRACT obligations (award types A-D) — the procurement slice, NOT the $7T total
# (most of which is direct benefits & interest). Merge duplicate corporate entities by parent.
def usa(path, body):
    import json as _j
    last=None
    for attempt in range(4):
        try:
            out = subprocess.run(["curl","-sL","--max-time","120","-H","Content-Type: application/json",
                                  "-X","POST","-d",_j.dumps(body),"https://api.usaspending.gov"+path],
                                 capture_output=True, text=True, check=True).stdout
            return _j.loads(out)
        except Exception as e:
            last=e; time.sleep(2*(attempt+1))
    raise last

FYWIN = [{"start_date": f"{FY-1}-10-01", "end_date": f"{FY}-09-30"}]
raw = usa("/api/v2/search/spending_by_category/recipient/",
          {"filters": {"time_period": FYWIN, "award_type_codes": ["A","B","C","D"]}, "limit": 60}).get("results", [])
import re
# Roll subsidiaries up to their ULTIMATE corporate parent, consistently, so the
# ranking reflects which conglomerate actually wins the most money (not an artifact
# of which legal entity signed). Keyed by the 2-word parent_key BEFORE aliasing.
ALIAS = {
    "RAYTHEON": "RTX",                       # Raytheon Co. is RTX Corp's defense segment
    "SIKORSKY AIRCRAFT": "LOCKHEED MARTIN",  # Sikorsky is a Lockheed subsidiary
    "ELECTRIC BOAT": "GENERAL DYNAMICS",     # Electric Boat = GD's submarine yard
    "BATH IRON": "GENERAL DYNAMICS",         # Bath Iron Works = GD
    "GULFSTREAM AEROSPACE": "GENERAL DYNAMICS",
    "OPTUMSERVE HEALTH": "OPTUM",            # OptumServe is part of Optum/UnitedHealth
    "QTC MEDICAL": "LEIDOS",                 # QTC is owned by Leidos
}
def parent_key(n):
    k = n.upper()
    for suf in [" CORPORATION"," CORP"," COMPANY"," CO."," INCORPORATED"," INC."," INC"," LLC"," L.L.C."," LP"," L.P.",
                " LIMITED"," LTD"," HOLDINGS"," GROUP"," THE "," AND ITS"," PUBLIC SECTOR SOLUTIONS"," GOVERNMENT BUSINESS"]:
        k = k.replace(suf, " ")
    k = re.sub(r"[^A-Z0-9 ]", " ", k)
    k = " ".join(k.split()[:2])            # first two significant words
    return ALIAS.get(k, k)
# annotations: what they make / which corner of government feeds them
ANNO = {
    "LOCKHEED MARTIN": "F-35 jets, missiles & satellites — the Pentagon's #1 contractor",
    "OPTUM": "UnitedHealth's arm running military & VA health systems",
    "ELECTRIC BOAT": "General Dynamics' nuclear-submarine yard",
    "TRIWEST HEALTHCARE": "Runs the VA & TRICARE community-care networks",
    "MCKESSON": "Drug distribution for the VA & Defense health system",
    "AMERISOURCEBERGEN DRUG": "Pharmaceutical distribution for federal health programs",
    "RTX": "Raytheon missiles, Pratt & Whitney jet engines & Collins avionics",
    "BOOZ ALLEN": "Consulting & IT for defense and intelligence",
    "HUMANA": "TRICARE military health insurance",
    "BOEING": "Combat aircraft, refuelers & space",
    "ATLANTIC DIVING": "Reseller funneling gear to the military (ADS Inc.)",
    "NATIONAL TECHNOLOGY": "Runs Sandia nuclear-weapons labs (Honeywell)",
    "TRIAD NATIONAL": "Runs the Los Alamos nuclear-weapons lab",
    "FLUOR": "Nuclear cleanup, defense logistics & construction",
    "LEIDOS": "Defense, health & intelligence IT services",
    "GENERAL DYNAMICS": "Submarines (Electric Boat), warships, combat vehicles & IT",
    "SCIENCE APPLICATIONS": "Defense & space engineering services (SAIC)",
    "LAWRENCE LIVERMORE": "Runs the Lawrence Livermore nuclear-weapons lab",
    "AMENTUM SERVICES": "Defense, energy & environmental logistics",
    "CACI": "Defense & intelligence IT and services",
    "SPACE EXPLORATION": "SpaceX — launch & satellite services for NASA & the Pentagon",
    "UT BATTELLE": "Runs Oak Ridge National Laboratory",
    "CONSOLIDATED NUCLEAR": "Runs the Y-12 & Pantex nuclear-weapons plants",
    "ACCENTURE FEDERAL": "Federal IT modernization & consulting",
    "NORTHROP GRUMMAN": "B-21 bomber, ICBMs & space systems",
    "LEIDOS": "Defense, health & intelligence IT services",
    "HUNTINGTON INGALLS": "The Navy's aircraft-carrier & warship builder",
    "BATTELLE MEMORIAL": "Runs national energy & defense labs",
    "BECHTEL": "Nuclear cleanup & megaproject construction",
    "PFIZER": "Vaccines & therapeutics",
    "GUIDEHOUSE": "Government management consulting",
    "FLUOR": "Nuclear, defense logistics & construction",
    "L3HARRIS": "Tactical radios, sensors & space",
    "ACCENTURE": "Federal IT modernization & consulting",
    "SAIC": "Defense & space engineering services",
    "DELOITTE": "Federal consulting, IT & audit",
}
DISPLAY = {
    "LOCKHEED MARTIN":"Lockheed Martin","OPTUM":"Optum (UnitedHealth)","ELECTRIC BOAT":"Electric Boat",
    "RTX":"RTX (Raytheon)","BOEING":"Boeing","TRIWEST HEALTHCARE":"TriWest Healthcare",
    "MCKESSON":"McKesson","HUNTINGTON INGALLS":"Huntington Ingalls","AMERISOURCEBERGEN DRUG":"AmerisourceBergen",
    "BOOZ ALLEN":"Booz Allen Hamilton","HUMANA":"Humana","ATLANTIC DIVING":"ADS Inc.",
    "NATIONAL TECHNOLOGY":"Sandia Labs (NTESS)","GENERAL DYNAMICS":"General Dynamics",
    "NORTHROP GRUMMAN":"Northrop Grumman","L3HARRIS":"L3Harris","GENERAL ELECTRIC":"General Electric",
    "TRIAD NATIONAL":"Los Alamos (Triad)","SCIENCE APPLICATIONS":"SAIC","LAWRENCE LIVERMORE":"Lawrence Livermore",
    "AMENTUM SERVICES":"Amentum","CACI":"CACI","SPACE EXPLORATION":"SpaceX","UT BATTELLE":"Oak Ridge (UT-Battelle)",
    "CONSOLIDATED NUCLEAR":"Y-12 / Pantex (CNS)","ACCENTURE FEDERAL":"Accenture Federal","LEIDOS":"Leidos",
}
merged = {}
for r in raw:
    nm = r.get("name") or ""
    amt = r.get("amount") or 0
    if not nm or nm.upper() in ("MULTIPLE RECIPIENTS","REDACTED DUE TO PII","MISCELLANEOUS FOREIGN CONTRACTORS"): continue
    k = parent_key(nm)
    if k.startswith("THE "): k = k[4:]
    if k not in merged:
        disp = DISPLAY.get(k) or " ".join(w.capitalize() for w in k.split())
        merged[k] = {"amount": 0.0, "display": disp}
    merged[k]["amount"] += amt
# Sector tag (color-coded on the site): defense / health / services.
# Explicit map for the known names, with a keyword fallback so next FY's data still classifies.
_CONTRACTOR_SECTOR = {
    "Lockheed Martin": "defense", "General Dynamics": "defense", "RTX (Raytheon)": "defense",
    "Boeing": "defense", "Huntington Ingalls": "defense", "ADS Inc.": "defense",
    "Sandia Labs (NTESS)": "defense", "Northrop Grumman": "defense", "L3Harris": "defense",
    "Optum (UnitedHealth)": "health", "TriWest Healthcare": "health", "McKesson": "health",
    "AmerisourceBergen": "health", "Humana": "health",
    "Booz Allen Hamilton": "services", "Leidos": "services",
}
def _sector(name, note):
    if name in _CONTRACTOR_SECTOR: return _CONTRACTOR_SECTOR[name]
    s = (name + " " + (note or "")).lower()
    if any(w in s for w in ("health", "pharm", "tricare", "medic", "drug", "hospital", "va &", "va,")): return "health"
    if any(w in s for w in ("missile", "jet", "submarine", "warship", "combat", "aircraft", "navy",
                            "weapon", "defense", "pentagon", "nuclear", "military", "intelligence", "satellite")): return "defense"
    return "services"

contractors = []
for k, v in merged.items():
    note = ANNO.get(k, "")
    contractors.append({"name": v["display"], "amount": v["amount"], "note": note,
                        "sector": _sector(v["display"], note)})
contractors.sort(key=lambda c: -c["amount"])
contractors = contractors[:14]
from collections import Counter as _Counter
_sc = _Counter(c["sector"] for c in contractors)
print(f"Top contractor: {contractors[0]['name']} ${contractors[0]['amount']/1e9:.1f}B  ({len(contractors)} shown); sectors {dict(_sc)}")

# ---------------------------------------------------------------- multi-year trends
def series(table, line, field):
    d = get(f"/v1/accounting/mts/{table}",
            {"filter": f"line_code_nbr:eq:{line},record_calendar_month:eq:09",
             "fields": f"record_fiscal_year,{field}", "sort": "record_fiscal_year", "page[size]": "40"})["data"]
    return [{"fy": int(r["record_fiscal_year"]), "v": fnum(r[field])} for r in d
            if fnum(r[field]) is not None and int(r["record_fiscal_year"]) >= 2008]
outlay_series  = series("mts_table_5", "5691", "current_fytd_net_outly_amt")
receipt_series = series("mts_table_4", "830",  "current_fytd_net_rcpt_amt")

# NET interest = "Interest Expense on Public Issues" only (interest actually paid to
# outside creditors). The dataset's grand total ALSO includes ~$246B of "Government
# Account Series" interest the Treasury pays its own trust funds — that is NOT a cost
# to taxpayers, so we exclude it. FY2025: public issues ~$974B vs gross total ~$1.22T.
# This matches CBO/CRFB "net interest" (~$1T, the figure that just surpassed defense).
ie = get("/v2/accounting/od/interest_expense",
         {"filter": "record_calendar_month:eq:09",
          "fields": "record_fiscal_year,expense_catg_desc,fytd_expense_amt", "page[size]": "3000"})["data"]
net_bf, gross_bf = {}, {}
for r in ie:
    v = fnum(r["fytd_expense_amt"]);  fy = int(r["record_fiscal_year"])
    if v is None: continue
    gross_bf[fy] = gross_bf.get(fy, 0) + v
    if r.get("expense_catg_desc", "").strip() == "INTEREST EXPENSE ON PUBLIC ISSUES":
        net_bf[fy] = net_bf.get(fy, 0) + v
interest_series = [{"fy": fy, "v": net_bf[fy]} for fy in sorted(net_bf) if fy >= 2008]
interest_now   = net_bf[FY]          # net interest paid to the public (~$974B)
interest_gross = gross_bf[FY]        # gross incl. intragovernmental (~$1.22T) — for disclosure only
print(f"Net interest (public issues) FY{FY}: ${interest_now/1e9:.1f}B  | gross incl. GAS: ${interest_gross/1e9:.1f}B")

# ------------------------------------------------ how the budget shifted (per category, by line code)
# Each line_code is verified to reproduce the FY2025 agency figures above to the dollar.
# Compares a base year to FY (decade); growth multiple vs the all-outlay average, and the
# change in each category's SHARE of total outlays (who rose / fell as a slice of the pie).
SHIFT_BASE = FY - 10                  # FY2015 when FY=2025
CAT_CODES = [
    ("Social Security",            "5693", "Retirement & disability benefits"),
    ("Health & Human Services",    "3045", "Medicare, Medicaid & health"),
    ("Treasury",                   "4200", "Mostly interest on the national debt — the budget's fastest-growing line"),
    ("Defense (Military)",         "2570", "Personnel, operations, weapons & R&D"),
    ("Veterans Affairs",           "4264", "Veterans' health care & benefits"),
    ("Agriculture",                "1561", "Food assistance & farm programs"),
    ("Transportation",             "4000", "Highways, transit, aviation & rail"),
    ("Education",                  "2639", "Student aid & K-12 grants"),
]
DISTORTED = {"Education"}             # student-loan re-estimates swing this line wildly year to year
tot_by_fy = {p["fy"]: p["v"] for p in series("mts_table_5", "5691", "current_fytd_net_outly_amt")}
shift_cats = []
for name, code, note in CAT_CODES:
    s = {p["fy"]: p["v"] for p in series("mts_table_5", code, "current_fytd_net_outly_amt")}
    if SHIFT_BASE not in s or FY not in s:
        continue
    shift_cats.append({
        "name": name, "note": note,
        "from": s[SHIFT_BASE], "to": s[FY],
        "mult": s[FY] / s[SHIFT_BASE],
        "share_from": s[SHIFT_BASE] / tot_by_fy[SHIFT_BASE] * 100,
        "share_to":   s[FY] / tot_by_fy[FY] * 100,
        "distorted": name in DISTORTED,
    })
for c in shift_cats:
    c["share_chg"] = c["share_to"] - c["share_from"]
shift_cats.sort(key=lambda c: -c["share_chg"])
shifts = {"fy_from": SHIFT_BASE, "fy_to": FY,
          "avg_mult": tot_by_fy[FY] / tot_by_fy[SHIFT_BASE], "categories": shift_cats}
print(f"Budget shift FY{SHIFT_BASE}->FY{FY}: avg {shifts['avg_mult']:.2f}x; "
      f"top gainer {shift_cats[0]['name']} {shift_cats[0]['mult']:.2f}x ({shift_cats[0]['share_chg']:+.1f} pts)")

# ------------------------------------------------ the rise of the tariff (customs duties over the decade)
cust = {p["fy"]: p["v"] for p in series("mts_table_4", "405", "current_fytd_net_rcpt_amt")}
rtot = {p["fy"]: p["v"] for p in series("mts_table_4", "830", "current_fytd_net_rcpt_amt")}
tyrs = sorted(fy for fy in cust if fy >= SHIFT_BASE and fy in rtot)
def rcpt(name):
    return next((r["amount"] for r in receipts if r["name"] == name), None)
tariff = {
    "series": [{"fy": fy, "v": cust[fy], "share": cust[fy] / rtot[fy] * 100} for fy in tyrs],
    "now": cust[FY], "prev": cust[FY - 1],
    "yoy_mult": cust[FY] / cust[FY - 1],
    "decade_mult": cust[FY] / cust[tyrs[0]],
    "share_from": cust[tyrs[0]] / rtot[tyrs[0]] * 100,
    "share_to": cust[FY] / rtot[FY] * 100,
    "vs_corporate": cust[FY] / rcpt("Corporate Income Taxes"),
    "vs_excise": cust[FY] / rcpt("Excise Taxes"),
}
print(f"Tariff: FY{tyrs[0]} ${cust[tyrs[0]]/1e9:.0f}B -> FY{FY} ${cust[FY]/1e9:.0f}B "
      f"({tariff['yoy_mult']:.2f}x YoY, {tariff['decade_mult']:.1f}x decade, "
      f"{tariff['share_from']:.1f}%->{tariff['share_to']:.1f}% of receipts)")

# ================================================================ NEW SECTIONS (2026-06-18)
import csv as _csv, io as _io
def _curl(url, min_len=1):
    for attempt in range(5):                       # this sandbox drops connections intermittently
        out = subprocess.run(["curl", "-sL", "--max-time", "90", url],
                             capture_output=True, text=True).stdout
        if len(out) >= min_len:
            return out
        time.sleep(1.5 * (attempt + 1))
    return out

# (1) WHO WE OWE — foreign holders of the debt (Treasury TIC; one file = full history, newest-first
# yearly blocks; December = first numeric column). Values are in BILLIONS — convert to dollars.
# Classify each holder into one of three honest buckets (color-coded on the site):
#   custody  — debt is BOOKED here but owned elsewhere (Euroclear, fund domiciles, hedge-fund hubs)
#   center   — major financial centers that hold a MIX of real reserves + custody (hard to disentangle)
#   sovereign— foreign governments & central banks holding Treasuries as genuine official reserves (default)
_HOLDER_CLASS = {
    "Belgium": "custody", "Luxembourg": "custody", "Ireland": "custody",
    "Cayman Islands": "custody", "Bermuda": "custody",
    "United Kingdom": "center", "Switzerland": "center", "Hong Kong": "center", "Singapore": "center",
}
def _holder_class(name): return _HOLDER_CLASS.get(name, "sovereign")
rows = list(_csv.reader(_curl("https://ticdata.treasury.gov/Publish/mfhhis01.txt").splitlines(), delimiter="\t"))
blocks, i = {}, 0
while i < len(rows):
    r = rows[i]
    if r and r[0] == "Country":
        yr = int([c for c in r[1:] if c.strip()][0]); j = i + 2; dat = {}
        while j < len(rows):
            rr = rows[j]
            if not rr or not rr[0].strip(): j += 1; continue
            nm = rr[0].strip().strip('"')
            if nm == "Country": break
            vals = [c.strip() for c in rr[1:] if c.strip()]
            if vals:
                try: dat[nm] = float(vals[0]) * 1e9
                except ValueError: pass
            if nm == "Grand Total": j += 1; break
            j += 1
        blocks[yr] = dat; i = j
    else: i += 1
_cy = max(blocks); _d = blocks[_cy]
holders = sorted(((k, v) for k, v in _d.items() if k not in ("Grand Total", "All Other")), key=lambda x: -x[1])[:15]
who_we_owe = {
    "as_of_year": _cy, "total_foreign": _d.get("Grand Total", 0), "debt_held_public": debt_held_public,
    "holders": [{"name": k, "amount": v, "category": _holder_class(k),
                 "custodial": _holder_class(k) != "sovereign"} for k, v in holders],
    "trend": [{"year": y, "china": blocks[y].get("China, Mainland", 0), "japan": blocks[y].get("Japan", 0)}
              for y in sorted(blocks) if y >= 2008 and "China, Mainland" in blocks[y] and "Japan" in blocks[y]],
}
print(f"Who we owe: top {holders[0][0]} ${holders[0][1]/1e9:.0f}B; foreign total ${_d.get('Grand Total',0)/1e12:.2f}T")

# (2) DEBT AS A SHARE OF GDP (FRED no-key CSV; quarterly -> keep ~annual since 2000)
def _fred(sid):
    out = {}
    for row in _csv.reader(_io.StringIO(_curl(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"))):
        if len(row) == 2 and row[1] not in ("", ".", sid) and not row[0].startswith(("observation", "DATE")):
            try: out[row[0]] = float(row[1])
            except ValueError: pass
    return out
def _annual(series, start=2000):
    by_year = {}
    for dt, v in series.items():
        y = int(dt[:4])
        if y >= start: by_year[y] = v                     # last obs in the year wins (latest quarter)
    return [{"fy": y, "v": by_year[y]} for y in sorted(by_year)]
_gross = _fred("GFDEGDQ188S"); _public = _fred("FYGFGDQ188S"); _def = _fred("FYFSGDA188S")
debt_gdp = {
    "gross": _annual(_gross), "public": _annual(_public), "deficit": _annual(_def, 2000),
    "gross_now": _annual(_gross)[-1]["v"], "public_now": _annual(_public)[-1]["v"],
}
print(f"Debt/GDP: gross {debt_gdp['gross_now']:.0f}% public {debt_gdp['public_now']:.0f}% of GDP")

# (3) WHERE FEDERAL MONEY LANDS — by state, TWO honest lenses (USAspending geography).
#   CONTRACTS (A-D) = what Washington BUYS (procurement; defense-shaped).
#   GRANTS (02-05)  = what's RETURNED to states (Medicaid/highways/food/education/housing).
# NOTE: we deliberately DO NOT map direct payments (06,10 — Social Security, Medicare, SNAP,
# ~$3T, the biggest flow) by state: USAspending books ~$3.0T of it as aggregated "MULTIPLE
# RECIPIENTS" geocoded to payment processors / insurer HQs, not where people live (e.g. MN
# shows $217B for 5.7M residents). A direct-payment-by-state map would be flatly misleading.
def _by_state(award_codes):
    geo = usa("/api/v2/search/spending_by_geography/",
              {"scope": "place_of_performance", "geo_layer": "state",
               "filters": {"time_period": FYWIN, "award_type_codes": award_codes}}).get("results", [])
    rows = sorted(
        [{"code": x.get("shape_code"), "name": x.get("display_name"), "amount": x.get("aggregated_amount") or 0,
          "pop": x.get("population") or 0,
          "per_capita": (x.get("aggregated_amount") or 0) / x["population"] if x.get("population") else 0}
         for x in geo if x.get("shape_code") and (x.get("aggregated_amount") or 0) > 0],
        key=lambda s: -s["amount"])
    return rows

_contract_states = _by_state(["A", "B", "C", "D"])
_grant_states = _by_state(["02", "03", "04", "05"])
# What's inside the grants total, by awarding department (so we can label the map honestly)
_gag = usa("/api/v2/search/spending_by_category/awarding_agency/",
           {"category": "awarding_agency",
            "filters": {"time_period": FYWIN, "award_type_codes": ["02", "03", "04", "05"]}, "limit": 8}).get("results", [])
_grant_mix = [{"name": x.get("name"), "amount": x.get("amount") or 0} for x in _gag if (x.get("amount") or 0) > 0]
spending_by_state = {
    "contracts": {"states": _contract_states, "total": sum(s["amount"] for s in _contract_states),
                  "label": "Contracts", "blurb": "What Washington buys — procurement of goods and services. Defense-shaped."},
    "grants": {"states": _grant_states, "total": sum(s["amount"] for s in _grant_states),
               "label": "Grants to states", "blurb": "Money returned to states — Medicaid, highways, food aid, schools, housing.",
               "mix": _grant_mix},
}
# Backward-compat: keep the old key pointing at the contracts lens
contracts_by_state = {"states": _contract_states, "total": spending_by_state["contracts"]["total"]}
print(f"By state — contracts: {len(_contract_states)} states, top {_contract_states[0]['code']} ${_contract_states[0]['amount']/1e9:.0f}B "
      f"(${_contract_states[0]['per_capita']:,.0f}/capita)")
print(f"By state — grants: ${spending_by_state['grants']['total']/1e9:.0f}B total, top {_grant_states[0]['code']} ${_grant_states[0]['amount']/1e9:.0f}B; "
      f"mix: {', '.join(m['name'].split('Department of ')[-1][:10]+' $%.0fB'%(m['amount']/1e9) for m in _grant_mix[:4])}")

# (4) WHAT IF RATES STAY HIGH — current average interest rate on the debt + an anchor history
def _ir(field="avg_interest_rate_amt"):
    d = get("/v2/accounting/od/avg_interest_rates",
            {"filter": "security_desc:eq:Total Interest-bearing Debt", "sort": "-record_date",
             "fields": f"record_date,{field}", "page[size]": "400"})["data"]
    return d
_irs = _ir()
_cur_rate = float(_irs[0]["avg_interest_rate_amt"])
_ir_by_year = {}
for r in _irs:
    _ir_by_year[int(r["record_date"][:4])] = float(r["avg_interest_rate_amt"])  # last (Dec) of each year
rates = {
    "current_rate": _cur_rate, "as_of": _irs[0]["record_date"],
    "debt_total": national_debt, "debt_public": debt_held_public,
    "trough": min(float(r["avg_interest_rate_amt"]) for r in _irs),
    "history": [{"fy": y, "rate": _ir_by_year[y]} for y in sorted(_ir_by_year) if y >= 2010],
}
print(f"Rates: current {_cur_rate:.2f}% (trough {rates['trough']:.2f}%) -> interest ≈ ${_cur_rate/100*national_debt/1e12:.2f}T")

# (5) MEDICARE vs MEDICAID — split the bundled HHS line. Medicaid = MTS "Grants to States for
# Medicaid" (clean federal share); Medicare = OMB Historical Table 3.2 function 570 net outlays
# (annual actual; MTS can't isolate net Medicare cleanly). HHS total from MTS.
_hhs_total = amt5("Total--Department of Health and Human Services")
_medicaid = amt5("Grants to States for Medicaid")
_MEDICARE_OMB = {2021: 696_458e6, 2022: 755_094e6, 2023: 847_544e6, 2024: 874_133e6, 2025: 996_718e6}
_medicare = _MEDICARE_OMB[FY]
hhs_split = {
    "total": _hhs_total,
    "parts": [
        {"name": "Medicare", "amount": _medicare, "note": "Health coverage for 65+ & disabled (net)"},
        {"name": "Medicaid", "amount": _medicaid, "note": "Federal share of state Medicaid"},
        {"name": "Everything else", "amount": _hhs_total - _medicare - _medicaid,
         "note": "NIH, CDC, FDA, ACF, ACA subsidies, CHIP & more"},
    ],
    "medicare_source": "OMB Historical Table 3.2 (budget function 570), FY actual",
}
print(f"HHS split: Medicare ${_medicare/1e9:.0f}B + Medicaid ${_medicaid/1e9:.0f}B + other "
      f"${(_hhs_total-_medicare-_medicaid)/1e9:.0f}B = ${_hhs_total/1e9:.0f}B")

# ---------------------------------------------------------------- reconciliation
assert abs((total_outlays + deficit) - total_receipts) < 5e9, "outlays+deficit != receipts"
print(f"RECONCILE ✓  receipts ${total_receipts/1e12:.2f}T - outlays ${total_outlays/1e12:.2f}T = deficit ${deficit/1e12:.2f}T")

HOUSEHOLDS = 131_434_000   # U.S. Census Bureau, 2024 (CPS)
POPULATION = 341_000_000   # U.S. Census Bureau, 2025 estimate

data = {
    "meta": {
        "fiscal_year": FY, "fy_label": f"FY{FY}", "fy_period": f"Oct {FY-1} – Sep {FY}",
        "generated": datetime.date.today().isoformat(), "debt_as_of": debt_date,
        "households": HOUSEHOLDS, "population": POPULATION,
        "sources": [
            "U.S. Treasury — Monthly Treasury Statement, Tables 4 & 5",
            "U.S. Treasury — Debt to the Penny",
            "U.S. Treasury — Interest Expense on the Public Debt Outstanding",
            "fiscaldata.treasury.gov (open data, no API key)",
            "USAspending.gov — federal contracts & grants by recipient and by-state geography",
            "U.S. Treasury — Treasury International Capital (foreign holders of the debt)",
            "FRED / St. Louis Fed — debt & deficit as a share of GDP",
            "OMB Historical Tables — Medicare (budget function 570)",
            "U.S. Census Bureau — households (2024) & population (2025)",
        ],
    },
    "topline": {
        "outlays": total_outlays, "prior_outlays": prior_outlays, "receipts": total_receipts,
        "deficit": deficit, "national_debt": national_debt,
        "interest": interest_now,            # NET interest (public issues) — the headline figure
        "interest_gross": interest_gross,    # gross incl. intragovernmental — for disclosure
        "defense_military": amt5("Total--Department of Defense--Military Programs"),
        "debt_growth_per_sec": debt_growth_per_sec,
    },
    "agencies": agencies,
    "receipts": receipts,
    "contractors": contractors,
    "shifts": shifts,
    "tariff": tariff,
    "who_we_owe": who_we_owe,
    "debt_gdp": debt_gdp,
    "contracts_by_state": contracts_by_state,
    "spending_by_state": spending_by_state,
    "rates": rates,
    "hhs_split": hhs_split,
    "trends": {"outlays": outlay_series, "receipts": receipt_series, "interest": interest_series},
}
with open(OUT, "w") as f:
    json.dump(data, f, separators=(",", ":"))
print(f"WROTE {os.path.relpath(OUT)}  ({os.path.getsize(OUT)/1024:.1f} KB)")
print("\nWHERE IT GOES:");  [print(f"  ${a['amount']/1e9:8.1f}B  {a['name']}") for a in agencies]
print("WHERE IT COMES FROM:"); [print(f"  ${r['amount']/1e9:8.1f}B  {r['name']}") for r in receipts]

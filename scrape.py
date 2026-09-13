"""
eRaktKosh blood-stock snapshot collector.

WHAT THIS SCRIPT DOES, IN PLAIN WORDS:
  1. For each state we care about, ask eRaktKosh for the full list of blood
     banks and their current stock (one request per state, covers every
     blood group at once — see NOTE below).
  2. eRaktKosh's answer is messy: names/addresses are jammed together with
     "<br/>" instead of separate fields, and the stock info is a sentence
     like "Available, O-Ve:1, A+Ve:9" instead of clean numbers. This script
     cleans that up into a simple table before saving it.
  3. Saves BOTH the original messy answer (so nothing is ever lost) and the
     cleaned-up version (so it's easy to use later) into one JSON file per
     run, named after the time it ran.

NOTE ON "bloodGroup=all": we confirmed (from a real sample) that searching
with bloodGroup=all still tells us the exact count for each individual
blood group inside the text, e.g. "Available, O-Ve:1, A+Ve:9, B-Ve:1".
So we do NOT need to search each blood group separately — one search per
state already gives us everything.

Run manually with: python scrape.py
Scheduled automatically by .github/workflows/eraktkosh-snapshot.yml
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://eraktkosh.mohfw.gov.in/BLDAHIMS/bloodbank/nearbyBB.cnt"

# Being a polite, identifiable caller is good practice for a government
# citizen-service page you plan to call for months.
HEADERS = {
    "User-Agent": "eraktkosh-shortage-research/0.1 (student project; contact: <your-email-here>)"
}

REQUEST_DELAY_SECONDS = 1  # be polite; only a light burst was tested so far

# --- Config: start scoped to one state, expand later -----------------------

STATE_CODES = {
    "Gujarat": 24,
    # Add more states here later. Full state code list is in the scoping doc.
}

COMPONENT_CODES = {
    "Whole Blood": 11,
    "Packed RBC": 12,
    # More exist (FFP=13, SDP=14, etc.) — add if you decide you need them.
}


# --- Step 1: talk to eRaktKosh ----------------------------------------------

def fetch_stock(state_code: int, component_code: int) -> dict:
    """Ask eRaktKosh for every blood bank + every blood group in one state,
    for one component type (e.g. Whole Blood)."""
    params = {
        "hmode": "GETNEARBYSTOCKDETAILS",
        "stateCode": state_code,
        "districtCode": -1,  # -1 means "whole state", not one district
        "bloodGroup": "all",  # confirmed: "all" still returns each group's own count
        "bloodComponent": component_code,
        "lang": 0,
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


# --- Step 2: clean up the messy text ----------------------------------------

TAG_RE = re.compile(r"<[^>]+>")  # matches things like <p class='...'> or <b>
# Matches blood-group mentions in both styles seen in real data:
#   "O-Ve:1"   -> letters="O",  sign="-", count="1"
#   "AB +ve"   -> letters="AB", sign="+", count=None (no number given)
GROUP_RE = re.compile(r"([A-Za-z]{1,2})\s*([+-])\s*[Vv]e(?::\s*(\d+))?")


def strip_html(text: str) -> str:
    """Remove HTML tags like <p class='text-danger'> so we're left with
    plain readable text."""
    return TAG_RE.sub(" ", text).strip()


def parse_availability(raw_html: str) -> dict:
    """
    Turns something like:
        "<p class='text-danger'><b>Whole Blood</b>Not Available ..."
    or:
        "Available, O-Ve:1, A+Ve:9, B-Ve:1, O+Ve:3"
    or:
        "Available, O +ve, AB +ve, O -ve"   (no numbers given)

    into a clean dict like:
        {"status": "available", "groups": {"O-": 1, "A+": 9, "B-": 1, "O+": 3}}
    or:
        {"status": "available", "groups": {"O+": None, "AB+": None, "O-": None}}
    or:
        {"status": "not_available", "groups": {}}
    """
    text = strip_html(raw_html)
    lower = text.lower()

    if "not available" in lower:
        return {"status": "not_available", "groups": {}, "raw_text": text}

    if "available" not in lower:
        # Something we didn't expect — keep the text so we can look at it later,
        # instead of silently guessing.
        return {"status": "unknown", "groups": {}, "raw_text": text}

    groups = {}
    for letters, sign, count in GROUP_RE.findall(text):
        group_name = letters.upper() + sign
        groups[group_name] = int(count) if count else None

    return {"status": "available", "groups": groups, "raw_text": text}


def parse_bank_block(raw_html: str) -> dict:
    """
    Turns something like:
        "Some Hospital<br/>123 Main Road, City, District, State<br/>Phone: 999, Fax: -, Email: a@b.com"
    into:
        {"name": "Some Hospital", "address": "123 Main Road, City, District, State",
         "phone": "999", "fax": "-", "email": "a@b.com"}

    Real data is inconsistent, so every field falls back to "" if it's missing
    rather than crashing the whole script.
    """
    parts = raw_html.split("<br/>")
    name = strip_html(parts[0]) if len(parts) > 0 else ""
    address = strip_html(parts[1]) if len(parts) > 1 else ""
    contact = strip_html(parts[2]) if len(parts) > 2 else ""

    def extract(label: str) -> str:
        match = re.search(label + r"\s*:?\s*([^,]*)", contact, flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""

    return {
        "name": name,
        "address": address,
        "phone": extract("Phone"),
        "fax": extract("Fax"),
        "email": extract("Email"),
    }


def parse_last_updated(raw_value: str, fetched_at: str) -> dict:
    """
    The 'last updated' field is sometimes a real timestamp, and sometimes
    the literal word "LIVE" (meaning "right now"). This turns both into one
    consistent shape so later code doesn't have to special-case it.
    """
    if not raw_value or raw_value.strip().upper() == "LIVE":
        return {"last_updated": fetched_at, "is_live": True}

    value = raw_value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            parsed = datetime.strptime(value, fmt)
            return {"last_updated": parsed.isoformat(), "is_live": False}
        except ValueError:
            continue

    # Didn't match a format we expected — keep the raw text rather than
    # dropping the information.
    return {"last_updated": value, "is_live": False}


def parse_row(row: list, fetched_at: str) -> dict:
    """One row from eRaktKosh looks like:
       [s_no, name/address/contact_html, category, availability_html, last_updated, type]
       This turns it into one clean dict.
    """
    s_no, bank_html, category, availability_html, last_updated_raw, bank_type = (
        row + [""] * (6 - len(row))  # pad in case a row is shorter than expected
    )[:6]

    bank = parse_bank_block(bank_html)
    availability = parse_availability(availability_html)
    updated = parse_last_updated(last_updated_raw, fetched_at)

    return {
        "s_no": s_no,
        "name": bank["name"],
        "address": bank["address"],
        "phone": bank["phone"],
        "fax": bank["fax"],
        "email": bank["email"],
        "category": category,
        "type": bank_type,
        "status": availability["status"],
        "groups": availability["groups"],
        "last_updated": updated["last_updated"],
        "is_live": updated["is_live"],
    }


# --- Step 3: run everything and save ----------------------------------------

def main():
    fetched_at = datetime.now(timezone.utc).isoformat()
    output_groups = []

    for state_name, state_code in STATE_CODES.items():
        for comp_name, comp_code in COMPONENT_CODES.items():
            try:
                payload = fetch_stock(state_code, comp_code)
            except Exception as e:
                print(f"FAILED: {state_name}/{comp_name}: {e}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            raw_rows = payload.get("data", [])
            parsed_rows = [parse_row(row, fetched_at) for row in raw_rows]

            available_count = sum(1 for r in parsed_rows if r["status"] == "available")
            print(
                f"OK: {state_name}/{comp_name} -> {len(parsed_rows)} banks, "
                f"{available_count} reporting stock"
            )

            output_groups.append(
                {
                    "state": state_name,
                    "state_code": state_code,
                    "component": comp_name,
                    "component_code": comp_code,
                    "banks": parsed_rows,   # the clean version — use this
                    "raw": payload,         # the original answer — kept as a safety net
                }
            )
            time.sleep(REQUEST_DELAY_SECONDS)

    out_dir = os.path.join("data", "raw")
    os.makedirs(out_dir, exist_ok=True)

    fname = fetched_at.replace(":", "-") + ".json"
    out_path = os.path.join(out_dir, fname)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": fetched_at, "groups": output_groups}, f, ensure_ascii=False, indent=2)

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

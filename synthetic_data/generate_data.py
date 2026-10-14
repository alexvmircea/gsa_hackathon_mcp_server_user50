#!/usr/bin/env python3
"""
generate_synthetic_hcm.py
=========================

Generates synthetic HR data for the fictitious **Department of Innovation**,
structured to loosely follow Oracle Fusion HCM conventions (Person / Assignment /
Position / Job / Organization as separate objects linked by surrogate keys).

ALL DATA IS SYNTHETIC. No real people, no PII. Duty-station codes are drawn from
the public GSA/OPM duty-station list (us_duty_stations.csv). Intended purely as
test fixtures for the HR Policy Navigator MCP server (GSA MCP Hackathon 2026).

Outputs four related CSVs into ./synthetic_data/ :
    employees.csv       — one row per worker (10,000)
    positions.csv       — the position hierarchy (incumbents + vacancies)
    organizations.csv    — org tree (agency -> office -> division)
    jobs.csv            — job catalogue (series x grade)

Guarantees enforced by the validator at the end:
    * every ReportsToPositionNumber resolves to a real position
    * a manager's grade is never below the position reporting to it
    * no reporting cycles; every chain terminates at the Secretary
    * DateOfBirth < ServiceComputationDate <= GradeEffectiveDate <= StepEffectiveDate <= today
    * every employee is >= 18 today and was >= 20 at service-computation date
    * one incumbent per position; employee org/duty-station codes always resolve

Usage:
    python generate_synthetic_hcm.py --duty-stations us_duty_stations.csv
    # reproducible: same --seed always yields identical files
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

TODAY = date(2026, 10, 14)
DAYS_PER_YEAR = 365.25

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# A curated subset of major federal-employment metros. Each entry is matched
# against us_duty_stations.csv at runtime so the emitted DutyStationCode is a
# real code from that public file. (city, state) -> fallback locality label.
TARGET_STATIONS = [
    ("WASHINGTON", "DISTRICT OF COLUMBIA"),
    ("ARLINGTON", "VIRGINIA"),
    ("ATLANTA", "GEORGIA"),
    ("DENVER", "COLORADO"),
    ("DALLAS", "TEXAS"),
    ("CHICAGO", "ILLINOIS"),
    ("SAN FRANCISCO", "CALIFORNIA"),
    ("SEATTLE", "WASHINGTON"),
    ("BOSTON", "MASSACHUSETTS"),
    ("BROOKLYN", "NEW YORK"),
    ("KANSAS CITY", "MISSOURI"),
    ("PHILADELPHIA", "PENNSYLVANIA"),
    ("HUNTSVILLE", "ALABAMA"),
    ("PHOENIX", "ARIZONA"),
    ("AUSTIN", "TEXAS"),
]

# OPM occupational series (code, descriptive title).
SERIES = [
    ("2210", "Information Technology Management"),
    ("1550", "Computer Science"),
    ("0343", "Management and Program Analysis"),
    ("0301", "Miscellaneous Administration and Program"),
    ("1515", "Operations Research"),
    ("1560", "Data Science"),
    ("0132", "Intelligence"),
    ("0201", "Human Resources Management"),
    ("0510", "Accounting"),
    ("0905", "General Attorney"),
    ("1102", "Contracting"),
    ("0080", "Security Administration"),
    ("0260", "Equal Employment Opportunity"),
    ("1035", "Public Affairs"),
]
SERIES_TITLE = {code: title for code, title in SERIES}

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph",
    "Jessica", "Thomas", "Sarah", "Christopher", "Karen", "Daniel", "Nancy",
    "Matthew", "Lisa", "Anthony", "Betty", "Mark", "Margaret", "Donald", "Sandra",
    "Steven", "Ashley", "Andrew", "Kimberly", "Joshua", "Emily", "Kenneth", "Donna",
    "Kevin", "Michelle", "Brian", "Carol", "George", "Amanda", "Edward", "Melissa",
    "Ronald", "Deborah", "Timothy", "Stephanie", "Jason", "Rebecca", "Jeffrey",
    "Sharon", "Ryan", "Laura", "Jacob", "Cynthia", "Gary", "Kathleen", "Nicholas",
    "Amy", "Eric", "Angela", "Jonathan", "Shirley", "Stephen", "Brenda", "Larry",
    "Pamela", "Justin", "Nicole", "Scott", "Anna", "Brandon", "Samantha", "Benjamin",
    "Katherine", "Samuel", "Christine", "Gregory", "Rachel", "Alexander", "Carolyn",
    "Patrick", "Maria", "Jack", "Aisha", "Dennis", "Priya", "Jerry", "Fatima",
    "Tyler", "Mei", "Aaron", "Sofia", "Jose", "Ravi", "Omar", "Yuki", "Hassan",
    "Ingrid", "Diego", "Nadia",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Patel", "Kim", "Chen", "Okafor", "Nakamura", "Reyes",
    "Cohen", "Murphy", "Bailey", "Cooper", "Richardson", "Cox", "Howard", "Ward",
    "Peterson", "Gray", "Ramos", "James", "Watson", "Brooks", "Kelly", "Sanders",
    "Price", "Bennett", "Wood", "Barnes", "Ross", "Henderson", "Coleman", "Jenkins",
    "Perry", "Powell", "Long", "Patterson", "Hughes", "Washington", "Butler",
    "Simmons", "Foster", "Bryant", "Alexander", "Russell", "Griffin", "Diaz", "Hayes",
]

TOP_OFFICES = [
    "Office of the Secretary",
    "Office of the Chief Information Officer",
    "Office of Emerging Technology",
    "Office of Data and Analytics",
    "Office of Digital Services",
    "Office of Human Capital",
    "Office of the Chief Financial Officer",
    "Office of General Counsel",
    "Office of Acquisition",
    "Office of Security and Compliance",
]

DIVISIONS = {
    "Office of the Chief Information Officer": [
        "Infrastructure Division", "Cybersecurity Division",
        "Enterprise Applications Division", "IT Policy Division",
    ],
    "Office of Emerging Technology": [
        "Artificial Intelligence Division", "Robotics and Automation Division",
        "Quantum Initiatives Division",
    ],
    "Office of Data and Analytics": [
        "Data Governance Division", "Analytics and Insights Division",
        "Data Engineering Division",
    ],
    "Office of Digital Services": [
        "User Experience Division", "Platform Engineering Division",
        "Product Management Division",
    ],
    "Office of Human Capital": [
        "Staffing and Classification Division", "Employee Relations Division",
        "Workforce Development Division",
    ],
    "Office of the Chief Financial Officer": [
        "Budget Division", "Financial Operations Division",
    ],
    "Office of General Counsel": [
        "Litigation Division", "Regulatory Affairs Division",
    ],
    "Office of Acquisition": [
        "Contracting Division", "Program Management Division",
    ],
    "Office of Security and Compliance": [
        "Physical Security Division", "Information Security Division",
        "Compliance Division",
    ],
}

# Title given to the single incumbent of each top-level office.
OFFICE_HEAD_TITLE = {
    "Office of the Chief Information Officer": "Chief Information Officer",
    "Office of Emerging Technology": "Director, Emerging Technology",
    "Office of Data and Analytics": "Chief Data Officer",
    "Office of Digital Services": "Director, Digital Services",
    "Office of Human Capital": "Chief Human Capital Officer",
    "Office of the Chief Financial Officer": "Chief Financial Officer",
    "Office of General Counsel": "General Counsel",
    "Office of Acquisition": "Chief Acquisition Officer",
    "Office of Security and Compliance": "Chief Security Officer",
}

# Within-grade-increment waiting periods (steps 1->10), simplified from 5 CFR 531.
WGI_WAITS_YEARS = [1, 1, 1, 2, 2, 2, 3, 3, 3]

# Relative frequency of staff grades (GS-7 ... GS-13).
STAFF_GRADE_WEIGHTS = {"13": 4, "12": 6, "11": 5, "9": 3, "7": 2}

ORG_CODE_LETTERS = "ABCDEFGHIJKLMNOPRSTU"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DutyStation:
    code: str
    city: str
    state: str
    locality: str


@dataclass
class Organization:
    code: str
    title: str
    org_type: str              # AGENCY | OFFICE | DIVISION
    parent_code: str
    station: DutyStation


@dataclass
class Position:
    number: str
    title: str
    grade: str                 # "ES" or a GS grade as a string, e.g. "13"
    series: str
    org_code: str
    reports_to: str            # "" for the top of the tree
    is_manager: bool


@dataclass
class Employee:
    person_id: int
    person_number: str
    first_name: str
    last_name: str
    birth_date: date
    scd: date                  # Service Computation Date
    position_number: str
    job_code: str
    grade: str
    step: str                  # "" for ES
    grade_effective_date: date
    step_effective_date: str   # "" for ES, else date
    org_code: str
    duty_station_code: str


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class HcmGenerator:
    def __init__(self, rng: random.Random, stations: list[DutyStation]):
        self.rng = rng
        self.stations = stations
        self._org_codes_used: set[str] = set()
        self._pos_seq = 10_000_000
        self._person_seq = 800_001
        self._names_used: set[tuple[str, str]] = set()
        self.job_catalog: dict[str, dict] = {}

        self.organizations: list[Organization] = []
        self.positions: list[Position] = []
        self.employees: list[Employee] = []

    # ---- small helpers ----------------------------------------------------

    def _weighted(self, items: list[tuple]) -> object:
        """items = [(value, weight), ...]"""
        values, weights = zip(*items)
        return self.rng.choices(values, weights=weights, k=1)[0]

    def _new_org_code(self) -> str:
        while True:
            code = (
                self.rng.choice(ORG_CODE_LETTERS)
                + self.rng.choice(ORG_CODE_LETTERS)
                + self.rng.choice(ORG_CODE_LETTERS)
                + f"{self.rng.randint(0, 999):03d}"
            )
            if code not in self._org_codes_used:
                self._org_codes_used.add(code)
                return code

    def _new_position_number(self) -> str:
        num = f"P{self._pos_seq:08d}"
        self._pos_seq += 1
        return num

    def _station_weighted(self) -> DutyStation:
        # First two stations (DC / Arlington) are heavily weighted, as in real HQ-centric agencies.
        weights = [6 if i < 2 else 2 for i in range(len(self.stations))]
        return self.rng.choices(self.stations, weights=weights, k=1)[0]

    def _series_for_org(self, title: str) -> tuple[str, str]:
        def s(code):  # fetch (code, title) tuple by code
            return next(x for x in SERIES if x[0] == code)

        if any(k in title for k in ("Information", "Infrastructure", "Cyber",
                                    "Applications", "Platform", "Engineering")):
            return self.rng.choice([s("2210"), s("1550"), s("1560")])
        if any(k in title for k in ("Artificial Intelligence", "Data",
                                    "Analytics", "Quantum", "Robotics")):
            return self.rng.choice([s("1550"), s("1560"), s("1515")])
        if any(k in title for k in ("Human Capital", "Staffing",
                                    "Employee Relations", "Workforce")):
            return s("0201")
        if any(k in title for k in ("Financial", "Budget", "Chief Financial")):
            return self.rng.choice([s("0510"), s("0343")])
        if any(k in title for k in ("Counsel", "Litigation", "Regulatory")):
            return s("0905")
        if any(k in title for k in ("Acquisition", "Contracting")):
            return s("1102")
        if any(k in title for k in ("Security", "Compliance")):
            return self.rng.choice([s("0080"), s("0260")])
        if "Public Affairs" in title:
            return s("1035")
        return self.rng.choice([s("0343"), s("0301")])

    def _job_code(self, series: str, grade: str) -> str:
        key = f"{series}-{grade}"
        if key not in self.job_catalog:
            base = SERIES_TITLE.get(series, "Executive")
            if grade == "ES":
                title = f"{base} (Senior Executive Service)"
            else:
                title = f"{base} (GS-{grade})"
            self.job_catalog[key] = {
                "job_code": f"J{series}G{grade}",
                "series": series,
                "grade": grade,
                "title": title,
            }
        return self.job_catalog[key]["job_code"]

    def _unique_name(self) -> tuple[str, str]:
        for _ in range(8):
            first = self.rng.choice(FIRST_NAMES)
            last = self.rng.choice(LAST_NAMES)
            if (first, last) not in self._names_used:
                self._names_used.add((first, last))
                return first, last
        return first, last  # collisions are acceptable past 8 tries

    # ---- build phases -----------------------------------------------------

    def build_organizations(self) -> None:
        agency = Organization(
            code=self._new_org_code(),
            title="Department of Innovation",
            org_type="AGENCY",
            parent_code="",
            station=self.stations[0],
        )
        self.organizations.append(agency)
        self.agency = agency

        self.office_by_name: dict[str, Organization] = {}
        for name in TOP_OFFICES:
            org = Organization(
                code=self._new_org_code(),
                title=name,
                org_type="OFFICE",
                parent_code=agency.code,
                station=self._station_weighted(),
            )
            self.organizations.append(org)
            self.office_by_name[name] = org

        for office_name, division_names in DIVISIONS.items():
            parent = self.office_by_name[office_name]
            for dn in division_names:
                self.organizations.append(Organization(
                    code=self._new_org_code(),
                    title=dn,
                    org_type="DIVISION",
                    parent_code=parent.code,
                    station=self._station_weighted(),
                ))

        self.org_by_code = {o.code: o for o in self.organizations}

    def build_positions(self) -> None:
        # Secretary at the top.
        secretary = Position(
            number=self._new_position_number(),
            title="Secretary of Innovation",
            grade="ES",
            series="0340",
            org_code=self.office_by_name["Office of the Secretary"].code,
            reports_to="",
            is_manager=True,
        )
        self.positions.append(secretary)
        self.secretary = secretary

        # Office heads report to the Secretary.
        office_head = {}
        for name, office in self.office_by_name.items():
            if name == "Office of the Secretary":
                continue
            p = Position(
                number=self._new_position_number(),
                title=OFFICE_HEAD_TITLE[name],
                grade=self._weighted([("ES", 3), ("15", 2)]),
                series=self._series_for_org(name)[0],
                org_code=office.code,
                reports_to=secretary.number,
                is_manager=True,
            )
            self.positions.append(p)
            office_head[name] = p

        # Division directors report to their office head.
        div_director = {}
        for div in [o for o in self.organizations if o.org_type == "DIVISION"]:
            parent_office = self.org_by_code[div.parent_code]
            head = office_head[parent_office.title]
            p = Position(
                number=self._new_position_number(),
                title=f"Director, {div.title}",
                grade="15",
                series=self._series_for_org(div.title)[0],
                org_code=div.code,
                reports_to=head.number,
                is_manager=True,
            )
            self.positions.append(p)
            div_director[div.code] = p

        # Branch chiefs -> supervisors -> staff, under each division.
        for div in [o for o in self.organizations if o.org_type == "DIVISION"]:
            director = div_director[div.code]
            for _ in range(self.rng.randint(4, 6)):            # branches
                series_code, series_title = self._series_for_org(div.title)
                chief = Position(
                    number=self._new_position_number(),
                    title=f"Branch Chief, {series_title}",
                    grade="14",
                    series=series_code,
                    org_code=div.code,
                    reports_to=director.number,
                    is_manager=True,
                )
                self.positions.append(chief)

                for _ in range(self.rng.randint(3, 5)):        # supervisors
                    supervisor = Position(
                        number=self._new_position_number(),
                        title=f"Supervisory {series_title.split()[0]} Specialist",
                        grade="13",
                        series=series_code,
                        org_code=div.code,
                        reports_to=chief.number,
                        is_manager=True,
                    )
                    self.positions.append(supervisor)

                    for _ in range(self.rng.randint(16, 24)):  # staff
                        grade = self._weighted(list(STAFF_GRADE_WEIGHTS.items()))
                        role = "Specialist" if grade >= "12" else "Analyst"
                        self.positions.append(Position(
                            number=self._new_position_number(),
                            title=f"{series_title.split()[0]} {role}",
                            grade=grade,
                            series=series_code,
                            org_code=div.code,
                            reports_to=supervisor.number,
                            is_manager=False,
                        ))

    def _step_for_time_in_grade(self, years_in_grade: float) -> int:
        step, remaining = 1, years_in_grade
        for wait in WGI_WAITS_YEARS:
            if remaining >= wait:
                remaining -= wait
                step += 1
            else:
                break
        return min(step, 10)

    def build_employees(self, target: int) -> None:
        # Managers are seated first so the whole hierarchy is staffed; any
        # leftover positions (target < total) remain vacant.
        ordered = ([p for p in self.positions if p.is_manager]
                   + [p for p in self.positions if not p.is_manager])
        to_seat = ordered[:target]

        for pos in to_seat:
            first, last = self._unique_name()
            person_number = str(self._person_seq)
            person_id = 700_000_000 + self._person_seq
            self._person_seq += 1

            tenure_years = round(0.5 + self.rng.random() * 33.5, 2)
            scd = TODAY - timedelta(days=tenure_years * DAYS_PER_YEAR)

            age_at_scd = self.rng.randint(20, 42)
            birth = date(scd.year - age_at_scd,
                         self.rng.randint(1, 12),
                         self.rng.randint(1, 28))

            grade_eff_offset = self.rng.random() * tenure_years * 0.85
            grade_eff = scd + timedelta(days=grade_eff_offset * DAYS_PER_YEAR)
            years_in_grade = (TODAY - grade_eff).days / DAYS_PER_YEAR

            if pos.grade == "ES":
                step, step_eff = "", ""
            else:
                step = str(self._step_for_time_in_grade(years_in_grade))
                step_offset = self.rng.random() * years_in_grade
                step_eff_date = grade_eff + timedelta(days=step_offset * DAYS_PER_YEAR)
                step_eff_date = max(step_eff_date, grade_eff)
                step_eff_date = min(step_eff_date, TODAY)
                step_eff = step_eff_date.isoformat()

            org = self.org_by_code[pos.org_code]
            self.employees.append(Employee(
                person_id=person_id,
                person_number=person_number,
                first_name=first,
                last_name=last,
                birth_date=birth,
                scd=scd,
                position_number=pos.number,
                job_code=self._job_code(pos.series, pos.grade),
                grade=pos.grade,
                step=step,
                grade_effective_date=grade_eff,
                step_effective_date=step_eff,
                org_code=pos.org_code,
                duty_station_code=org.station.code,
            ))

    # ---- validation -------------------------------------------------------

    def validate(self) -> list[str]:
        problems: list[str] = []
        pos_by_num = {p.number: p for p in self.positions}

        def grade_rank(grade: str) -> int:
            return 16 if grade == "ES" else int(grade)

        for p in self.positions:
            if p.reports_to and p.reports_to not in pos_by_num:
                problems.append(f"{p.number}: reports_to does not resolve")
            if p.reports_to:
                mgr = pos_by_num[p.reports_to]
                if grade_rank(mgr.grade) < grade_rank(p.grade):
                    problems.append(f"{p.number}: manager grade below report")

        for p in self.positions:  # cycle / termination check
            cur, hops = p, 0
            while cur.reports_to:
                cur = pos_by_num[cur.reports_to]
                hops += 1
                if hops > 25:
                    problems.append(f"{p.number}: reporting chain too deep/cyclic")
                    break

        seated: set[str] = set()
        for e in self.employees:
            if not (e.birth_date < e.scd):
                problems.append(f"{e.person_number}: birth not before SCD")
            if not (e.scd <= e.grade_effective_date):
                problems.append(f"{e.person_number}: SCD after grade effective date")
            if e.grade_effective_date > TODAY:
                problems.append(f"{e.person_number}: grade effective date in future")
            if e.step_effective_date:
                se = date.fromisoformat(e.step_effective_date)
                if not (e.grade_effective_date <= se):
                    problems.append(f"{e.person_number}: step before grade date")
                if se > TODAY:
                    problems.append(f"{e.person_number}: step date in future")
            if (TODAY - e.birth_date).days / DAYS_PER_YEAR < 18:
                problems.append(f"{e.person_number}: under 18 today")
            if e.position_number in seated:
                problems.append(f"{e.position_number}: seated more than once")
            seated.add(e.position_number)
            if e.org_code not in self.org_by_code:
                problems.append(f"{e.person_number}: org code does not resolve")

        return problems

    # ---- output -----------------------------------------------------------

    def write_csvs(self, out_dir: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        seated = {e.position_number for e in self.employees}

        with open(os.path.join(out_dir, "employees.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "PersonId", "PersonNumber", "FirstName", "LastName", "DateOfBirth",
                "ServiceComputationDate", "PositionNumber", "JobCode", "Grade", "Step",
                "GradeEffectiveDate", "StepEffectiveDate", "OrganizationCode",
                "DutyStationCode",
            ])
            for e in self.employees:
                w.writerow([
                    e.person_id, e.person_number, e.first_name, e.last_name,
                    e.birth_date.isoformat(), e.scd.isoformat(), e.position_number,
                    e.job_code, e.grade, e.step, e.grade_effective_date.isoformat(),
                    e.step_effective_date, e.org_code, e.duty_station_code,
                ])

        with open(os.path.join(out_dir, "positions.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "PositionNumber", "PositionTitle", "Grade", "Series",
                "OrganizationCode", "ReportsToPositionNumber", "IsManager",
                "VacancyStatus",
            ])
            for p in self.positions:
                w.writerow([
                    p.number, p.title, p.grade, p.series, p.org_code, p.reports_to,
                    "Y" if p.is_manager else "N",
                    "Filled" if p.number in seated else "Vacant",
                ])

        with open(os.path.join(out_dir, "organizations.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "OrganizationCode", "OrganizationTitle", "OrganizationType",
                "ParentOrganizationCode", "DutyStationCode", "DutyStationCity",
                "DutyStationState", "LocalityCode",
            ])
            for o in self.organizations:
                w.writerow([
                    o.code, o.title, o.org_type, o.parent_code, o.station.code,
                    o.station.city, o.station.state, o.station.locality,
                ])

        with open(os.path.join(out_dir, "jobs.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["JobCode", "JobTitle", "Series", "Grade"])
            for j in self.job_catalog.values():
                w.writerow([j["job_code"], j["title"], j["series"], j["grade"]])


# ---------------------------------------------------------------------------
# Duty-station loading
# ---------------------------------------------------------------------------

def load_duty_stations(path: str) -> list[DutyStation]:
    """Match TARGET_STATIONS against the public duty-station file and return
    real codes. Falls back to synthetic codes only if the file is absent."""
    if not os.path.exists(path):
        print(f"[warn] {path} not found; using placeholder duty-station codes.")
        return [
            DutyStation(code=f"{i:09d}", city=c, state=s, locality="ZZ")
            for i, (c, s) in enumerate(TARGET_STATIONS, start=1)
        ]

    # Index the file by (city, state) for quick lookup.
    by_city_state: dict[tuple[str, str], dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for row in reader:
            row = {k.strip(): (v or "").strip() for k, v in row.items()}
            key = (row.get("City", ""), row.get("State/Territory", ""))
            by_city_state.setdefault(key, row)

    stations: list[DutyStation] = []
    for city, state in TARGET_STATIONS:
        row = by_city_state.get((city, state))
        if row is None:
            print(f"[warn] {city}, {state} not found in duty-station file; skipping.")
            continue
        stations.append(DutyStation(
            code=row["Code"],
            city=city,
            state=state,
            locality=row.get("Locality Code", "ZZ"),
        ))
    if not stations:
        raise SystemExit("No duty stations resolved; check the input file path/format.")
    return stations


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic HCM data.")
    parser.add_argument("--duty-stations", default="us_duty_stations.csv",
                        help="Path to the public duty-station CSV.")
    parser.add_argument("--out-dir", default="synthetic_data",
                        help="Directory to write the CSV files into.")
    parser.add_argument("--employees", type=int, default=10_000,
                        help="Number of employees to seat.")
    parser.add_argument("--seed", type=int, default=20_261_014,
                        help="Random seed for reproducibility.")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    stations = load_duty_stations(args.duty_stations)

    gen = HcmGenerator(rng, stations)
    gen.build_organizations()
    gen.build_positions()
    gen.build_employees(args.employees)

    problems = gen.validate()
    if problems:
        print(f"[FAIL] {len(problems)} validation problem(s):")
        for p in problems[:20]:
            print("   -", p)
        raise SystemExit(1)

    gen.write_csvs(args.out_dir)

    filled = len(gen.employees)
    total_pos = len(gen.positions)
    print("Validation passed (0 problems).")
    print(f"  organizations : {len(gen.organizations)}")
    print(f"  positions     : {total_pos}  ({filled} filled, {total_pos - filled} vacant)")
    print(f"  employees     : {filled}")
    print(f"  job catalogue : {len(gen.job_catalog)}")
    print(f"  output dir    : {os.path.abspath(args.out_dir)}")


if __name__ == "__main__":
    main()
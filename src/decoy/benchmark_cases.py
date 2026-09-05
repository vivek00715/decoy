"""Phase 10 benchmark test cases: ~36 cases spanning free-text-only,
DB-record-only, and mixed scenarios, covering the PII types masker.py's
regex layer and record_masker.py's deny-by-default rules actually catch.

IMPORTANT ASYMMETRY, deliberate and documented here once rather than
per-case: masker.py's free-text layer has NO name/address NER detector by
default (ner.py's NoOpNERBackend is a no-op placeholder -- Presidio
wiring is a later phase per the original spec). So a bare person NAME
typed directly into a question is NOT currently masked by this project at
all, and cases below never plant a bare name as `planted_pii` in
free text for that reason -- doing so would make the benchmark report a
"privacy leak" for a gap that's honestly a known, documented scope limit,
not a regression to chase. record_masker.py's DB path is different: it
masks EVERY non-shape-safe column by content regardless of what's inside
it (deny-by-default), so a name value IN A RECORD is reliably masked
whether or not a NAME regex/NER detector exists -- cases below plant
names only in `records`, never in free text, for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BenchmarkCase:
    id: str
    description: str
    question: str
    records: list[dict] = field(default_factory=list)
    planted_pii: list[str] = field(default_factory=list)
    expected_relevant_fields: Optional[set[str]] = None


CASES: list[BenchmarkCase] = []


def _case(**kwargs) -> None:
    CASES.append(BenchmarkCase(**kwargs))


# -- Free-text-only cases (PII typed directly into the question) -----------
# Every planted value here is regex-detectable by masker.py (verified
# directly against EMAIL_RE/PHONE_RE/SSN_RE/CREDIT_CARD_RE/DEFAULT_PNR_RE
# before writing these).

_case(
    id="ft-email-1",
    description="a single email address typed directly into the question",
    question="Can you follow up with jane.doe@example.com about the invoice?",
    planted_pii=["jane.doe@example.com"],
)
_case(
    id="ft-email-2",
    description="two distinct emails in one question",
    question="Loop in both mchen@acme-corp.example.com and r.patel@acme-corp.example.com on this thread.",
    planted_pii=["mchen@acme-corp.example.com", "r.patel@acme-corp.example.com"],
)
_case(
    id="ft-phone-1",
    description="a US-format phone number in the question",
    question="Please call the customer back at 415-555-2671 tomorrow morning.",
    planted_pii=["415-555-2671"],
)
_case(
    id="ft-phone-2",
    description="a parenthesized phone number",
    question="Their office line is (312) 555-0199, try that first.",
    planted_pii=["(312) 555-0199"],
)
_case(
    id="ft-ssn-1",
    description="an SSN typed into a support question",
    question="The applicant's SSN is 123-45-6789, can you verify it against the file?",
    planted_pii=["123-45-6789"],
)
_case(
    id="ft-ssn-2",
    description="an SSN embedded mid-sentence",
    question="I need to correct SSN 987-65-4321 in the record, it was entered wrong.",
    planted_pii=["987-65-4321"],
)
_case(
    id="ft-credit-card-1",
    description="a credit card number in a billing question",
    question="The charge on card 4111-1111-1111-1111 failed, can you check why?",
    planted_pii=["4111-1111-1111-1111"],
)
_case(
    id="ft-credit-card-2",
    description="a credit card number without separators",
    question="Card number 5500005555555559 was declined at checkout.",
    planted_pii=["5500005555555559"],
)
_case(
    id="ft-pnr-1",
    description="a booking reference typed into a travel question",
    question="Can you look up booking AB12CD and confirm the seat assignment?",
    planted_pii=["AB12CD"],
)
_case(
    id="ft-pnr-2",
    description="a second booking reference format",
    question="Reservation XY7788 needs to be moved to the next available flight.",
    planted_pii=["XY7788"],
)

# -- DB-record-only cases (PII in fetched records, question is generic) ----

_case(
    id="db-employee-contact-1",
    description="a single employee record with name+email in text columns",
    question="What is this employee's current status?",
    records=[
        {"employee_id": 4471, "name": "Priya Nair", "email": "priya.nair@example.com", "is_active": True, "status": "active"},
    ],
    planted_pii=["Priya Nair", "priya.nair@example.com"],
)
_case(
    id="db-customer-support-ticket-1",
    description="a support ticket record with a customer's SSN in a free-text notes field",
    question="Summarize the open support tickets.",
    records=[
        {"ticket_id": 9001, "notes": "Caller verified with SSN 555-12-9876, issue resolved.", "status": "closed"},
    ],
    planted_pii=["555-12-9876"],
)
_case(
    id="db-multi-row-emails-1",
    description="5 rows of distinct customer emails, testing masking applies per-row not just per-column-once",
    question="How many customers are in this list?",
    records=[
        {"id": i, "email": f"customer{i}@example.com", "active": True}
        for i in range(1, 6)
    ],
    planted_pii=[f"customer{i}@example.com" for i in range(1, 6)],
)
_case(
    id="db-phone-column-1",
    description="a phone number stored in a dedicated DB column",
    question="List the contact numbers on file.",
    records=[
        {"id": 1, "contact_phone": "212-555-0134", "region": "east"},
        {"id": 2, "contact_phone": "312-555-0199", "region": "central"},
    ],
    planted_pii=["212-555-0134", "312-555-0199"],
)
_case(
    id="db-mixed-shapes-1",
    description="a record mixing shape-safe columns (boolean/status) with a sensitive name column, verifying only the sensitive column's VALUE is the planted target",
    question="What statuses exist for these accounts?",
    records=[
        {"account_holder": "Derek Owusu", "verified": True, "status": "open"},
        {"account_holder": "Lin Zhao", "verified": False, "status": "closed"},
        {"account_holder": "Sam Okafor", "verified": True, "status": "open"},
        {"account_holder": "Priya Rao", "verified": True, "status": "open"},
        {"account_holder": "Ben Tucker", "verified": False, "status": "closed"},
    ],
    planted_pii=["Derek Owusu", "Lin Zhao", "Sam Okafor", "Priya Rao", "Ben Tucker"],
)

# -- Mixed cases (same real value in both the question AND a record) -------
# These also exercise the shared-vault "same value, same fake everywhere"
# guarantee, which is functionally verified elsewhere (test_unified_pipeline_phase4.py);
# here they contribute to the aggregate Privacy Score like any other case.

_case(
    id="mixed-email-both-1",
    description="the same email appears in the question and in a fetched record",
    question="Has alice.chen@example.com filed any support tickets recently?",
    records=[
        {"ticket_id": 501, "customer_email": "alice.chen@example.com", "notes": "reported a login bug"},
        {"ticket_id": 502, "customer_email": "other.user@example.com", "notes": "asked about pricing"},
    ],
    planted_pii=["alice.chen@example.com", "other.user@example.com"],
)
_case(
    id="mixed-phone-both-1",
    description="a phone number quoted in the question and present in a record",
    question="Is 415-555-2671 the correct number on file for this account?",
    records=[
        {"account_id": 77, "phone": "415-555-2671", "plan": "premium"},
    ],
    planted_pii=["415-555-2671"],
)
_case(
    id="mixed-pnr-and-email-1",
    description="a PNR in the question plus a different customer's email in records",
    question="Can you check booking AB12CD for any pending changes?",
    records=[
        {"booking_ref": "AB12CD", "contact_email": "traveler@example.com", "seat": "14C"},
    ],
    planted_pii=["AB12CD", "traveler@example.com"],
)
_case(
    id="mixed-ssn-and-name-1",
    description="an SSN in the question, a different person's name in a record",
    question="Please confirm SSN 111-22-3333 matches our records for this applicant.",
    records=[
        {"applicant_name": "Morgan Reyes", "application_id": 8821, "decision": "pending"},
    ],
    planted_pii=["111-22-3333", "Morgan Reyes"],
)
_case(
    id="mixed-multi-type-1",
    description="an email and a credit card in the question, an SSN and a name in records",
    question="Charge card 4111-1111-1111-1111 and email the receipt to finance@example.com.",
    records=[
        {"employee_name": "Wei Zhang", "ssn": "444-55-6666", "department": "finance"},
    ],
    planted_pii=["4111-1111-1111-1111", "finance@example.com", "Wei Zhang", "444-55-6666"],
)

# -- Relevance-variation cases (query_aware.py's keyword classifier) -------
# These test that HOW a masked-by-default field is masked varies with
# relevance (realistic fake if relevant, redacted if not) -- never
# WHETHER it's masked. Privacy Score should still read ~1.0 for detected
# PII in every one of these; the relevance behavior itself is checked
# separately in tests/test_benchmark_suite.py via expected_relevant_fields.

_case(
    id="relevance-salary-1",
    description="a salary/department question -- those columns should be classified relevant; name/email should not",
    question="What is the average salary by department?",
    records=[
        {"name": "Alex Kim", "email": "alex.kim@example.com", "salary": 95000, "department": "engineering"},
        {"name": "Jordan Lee", "email": "jordan.lee@example.com", "salary": 88000, "department": "sales"},
        {"name": "Sam Patel", "email": "sam.patel@example.com", "salary": 102000, "department": "engineering"},
        {"name": "Casey Wu", "email": "casey.wu@example.com", "salary": 79000, "department": "sales"},
        {"name": "Robin Diaz", "email": "robin.diaz@example.com", "salary": 91000, "department": "engineering"},
    ],
    planted_pii=[
        "Alex Kim", "alex.kim@example.com", "Jordan Lee", "jordan.lee@example.com",
        "Sam Patel", "sam.patel@example.com", "Casey Wu", "casey.wu@example.com",
        "Robin Diaz", "robin.diaz@example.com",
    ],
    expected_relevant_fields={"salary", "department"},
)
_case(
    id="relevance-email-irrelevant-1",
    description=(
        "a question that (via the keyword 'departments') keeps `department` relevant "
        "but has no keyword overlap with name/ssn/email -- those three should be "
        "redacted, not just faked, since they're irrelevant to the question asked"
    ),
    question="How many departments are represented here?",
    records=[
        {"name": "Nina Torres", "ssn": "222-33-4444", "email": "nina.t@example.com", "department": "hr"},
        {"name": "Omar Haddad", "ssn": "333-44-5555", "email": "omar.h@example.com", "department": "legal"},
    ],
    planted_pii=["Nina Torres", "222-33-4444", "nina.t@example.com", "Omar Haddad", "333-44-5555", "omar.h@example.com"],
    # "department" matches via the question's own word "departments" (keyword
    # overlap) -- this is classify_relevant_columns' correct, intended
    # behavior, not a gap; name/ssn/email correctly have zero overlap.
    expected_relevant_fields={"department"},
)
_case(
    id="relevance-phone-relevant-1",
    description="a question specifically about phone numbers -- phone should be classified relevant",
    question="What phone numbers are on file for these customers?",
    records=[
        {"name": "Iris Chen", "phone": "617-555-0142", "ssn": "555-66-7777"},
        {"name": "Marcus Lowe", "phone": "512-555-0198", "ssn": "666-77-8888"},
    ],
    planted_pii=["Iris Chen", "617-555-0142", "555-66-7777", "Marcus Lowe", "512-555-0198", "666-77-8888"],
    expected_relevant_fields={"phone"},
)
_case(
    id="relevance-address-relevant-1",
    description="a question about customer location/address -- address should be classified relevant",
    question="What is the customer's address on file?",
    records=[
        {"name": "Grace Kim", "address": "142 Birch St, Springfield", "ssn": "777-88-9999"},
    ],
    planted_pii=["Grace Kim", "142 Birch St, Springfield", "777-88-9999"],
    expected_relevant_fields={"address"},
)
_case(
    id="relevance-status-relevant-1",
    description="a question about ticket status -- status should be classified relevant (though status is also likely enum_kept by shape)",
    question="What is the status of these tickets?",
    records=[
        {"reporter_email": "user1@example.com", "status": "open"},
        {"reporter_email": "user2@example.com", "status": "closed"},
        {"reporter_email": "user3@example.com", "status": "open"},
        {"reporter_email": "user4@example.com", "status": "open"},
        {"reporter_email": "user5@example.com", "status": "closed"},
    ],
    planted_pii=[f"user{i}@example.com" for i in range(1, 6)],
    expected_relevant_fields={"status"},
)
_case(
    id="relevance-date-relevant-1",
    description="a question about timeline -- date column should be classified relevant",
    question="When were these accounts created?",
    records=[
        {"owner_email": "founder@example.com", "created_date": "2021-03-14"},
    ],
    planted_pii=["founder@example.com"],
    expected_relevant_fields={"created_date"},
)

# -- Additional free-text coverage (padding toward ~36 total, varied phrasing) --

_case(
    id="ft-email-in-signoff-1",
    description="email appearing at the end of a message rather than mid-sentence",
    question="Thanks for looking into this. Reply to me directly at ops-lead@example.com.",
    planted_pii=["ops-lead@example.com"],
)
_case(
    id="ft-multiple-types-1",
    description="an email and a phone number in the same free-text question",
    question="Reach the client at either lchen@example.com or 646-555-0173.",
    planted_pii=["lchen@example.com", "646-555-0173"],
)
_case(
    id="ft-pnr-lowercase-context-1",
    description="a PNR-shaped code in a lowercase sentence (the code itself stays uppercase, as real PNRs are)",
    question="my confirmation code is QR4092 and i haven't received a ticket yet",
    planted_pii=["QR4092"],
)
_case(
    id="ft-pnr-lowercase-code-1",
    description=(
        "the PNR CODE ITSELF is lowercase, not just the surrounding sentence -- a confirmed gap: "
        "mask_text('my pnr fghty6') returned detections=[] before DEFAULT_PNR_RE gained "
        "re.IGNORECASE (see masker.py's top-level comment above DEFAULT_PNR_RE). Distinct from "
        "ft-pnr-lowercase-context-1 above, whose planted code stays uppercase."
    ),
    question="my pnr fghty6",
    planted_pii=["fghty6"],
)
_case(
    id="ft-pnr-context-aware-1",
    description=(
        "a booking-reference code that does NOT fit DEFAULT_PNR_RE's strict shape (no digit at "
        "all) but sits right next to the keyword naming it -- only caught by the new "
        "context-aware fallback (masker.py's PNR_CONTEXT_KEYWORD_RE), a deliberate "
        "false-positive-tolerant tradeoff documented in WHAT_THIS_PROTECTS_AGAINST.md"
    ),
    question="your booking reference is ABCDEF, keep it safe",
    planted_pii=["ABCDEF"],
)
_case(
    id="ft-ssn-with-dashes-varied-1",
    description="an SSN with a different digit pattern to catch any off-by-one in the regex boundary",
    question="Please cross-check SSN 000-11-2222 against the applicant database.",
    planted_pii=["000-11-2222"],
)
_case(
    id="db-notes-field-pii-1",
    description="PII embedded inside a free-text notes column (not its own dedicated column) in a record -- record_masker masks the whole cell regardless",
    question="What do the case notes say?",
    records=[
        {"case_id": 33, "notes": "Contacted via 908-555-0166, confirmed identity.", "resolved": True},
    ],
    planted_pii=["908-555-0166"],
)
_case(
    id="db-empty-records-question-only-1",
    description="a question with planted PII but zero fetched records -- exercises the free-text-only path within the unified pipeline machinery, not just standalone Masker",
    question="Please don't share this internally: contact is r.singh@example.com.",
    records=[],
    planted_pii=["r.singh@example.com"],
)
_case(
    id="db-large-batch-1",
    description="a larger batch (8 rows) of distinct SSNs to check masking doesn't degrade or skip rows as batch size grows",
    question="How many applicants are pending review?",
    records=[
        {"applicant_id": i, "ssn": f"1{i:02d}-{i:02d}-{1000 + i:04d}"[:11], "review_status": "pending" if i % 2 == 0 else "in_progress"}
        for i in range(10, 18)
    ],
    planted_pii=[f"1{i:02d}-{i:02d}-{1000 + i:04d}"[:11] for i in range(10, 18)],
)

_case(
    id="db-credit-card-column-1",
    description="a credit card number stored in a dedicated payment-method column",
    question="List the payment methods on file.",
    records=[
        {"customer_id": 501, "card_number": "4111-1111-1111-1111", "active": True},
        {"customer_id": 502, "card_number": "5500005555555559", "active": False},
    ],
    planted_pii=["4111-1111-1111-1111", "5500005555555559"],
)
_case(
    id="mixed-name-and-pnr-1",
    description="a name in a record and a PNR in the question, no overlap between them",
    question="Can you check the status of booking ZR3391?",
    records=[
        {"passenger_name": "Elena Vasquez", "loyalty_tier": "gold", "flights_this_year": 12},
    ],
    planted_pii=["ZR3391", "Elena Vasquez"],
)
_case(
    id="relevance-multiple-relevant-1",
    description="a compound question naming two relevant categories (salary and phone) so both should be classified relevant while name/ssn are not",
    question="What is the salary and phone number for each person?",
    records=[
        {"name": "Talia Brooks", "ssn": "888-99-0000", "salary": 84000, "phone": "203-555-0110"},
        {"name": "Devon Ashe", "ssn": "999-00-1111", "salary": 76000, "phone": "203-555-0122"},
    ],
    planted_pii=["Talia Brooks", "888-99-0000", "Devon Ashe", "999-00-1111", "203-555-0110", "203-555-0122"],
    expected_relevant_fields={"salary", "phone"},
)

# -- SPIA-style residual-context probe case ---------------------------------
# NOT scored via planted_pii/Privacy Score in the usual way -- this case's
# ground truth (Alice Chen's name/email) IS reliably masked (verified
# below), but the point of this case is the fields that are NOT masked
# (title, office -- both low-cardinality "business logic" columns kept
# real by Phase 2's shape rules) combining into a unique quasi-identifier.
# See tests/test_benchmark_suite.py::test_spia_residual_context_probe for
# the actual finding, stated plainly. 10 rows, exactly 2 distinct values
# each for `title` and `office` (ratio 0.2, at the low-cardinality
# threshold) so both get kept as real labels. Deliberately constructed so
# NEITHER field alone is unique: two people share the "VP of Engineering"
# title (Alice Chen and Employee1), and five people share "Austin" -- but
# only Alice combines BOTH "VP of Engineering" AND "Austin" (Employee1,
# the other VP, is in "Remote"), so it's genuinely the COMBINATION that
# creates the residual re-identification risk, not either field in
# isolation. (Employee1's row is a legitimate second case of the same
# risk for a different reason -- Employee1 is the only "VP of
# Engineering"+"Remote" row -- the quasi-identifier check below is
# expected to flag both rows, not just Alice's.)
SPIA_PROBE_CASE = BenchmarkCase(
    id="spia-quasi-identifier-probe",
    description=(
        "Alice Chen is the unique VP of Engineering based in Austin among 10 "
        "employee records; her name/email are masked, but title+office "
        "(individually low-cardinality, so kept real by Phase 2's shape "
        "rules) combine into a quasi-identifier that could still re-identify "
        "her by inference -- the SPIA-style residual-context risk. Neither "
        "field alone is unique (two VPs, five Austin-office rows); only the "
        "combination is."
    ),
    question="List the employees and their roles.",
    records=(
        [
            {
                "employee_name": "Alice Chen",
                "email": "alice.chen@acme-corp.example.com",
                "title": "VP of Engineering",
                "office": "Austin",
                "department": "Engineering",
            },
            {
                "employee_name": "Employee1",
                "email": "employee1@acme-corp.example.com",
                "title": "VP of Engineering",
                "office": "Remote",
                "department": "Engineering",
            },
        ]
        + [
            {
                "employee_name": f"Employee{i}",
                "email": f"employee{i}@acme-corp.example.com",
                "title": "Senior Engineer",
                "office": "Austin" if i < 6 else "Remote",
                "department": "Engineering",
            }
            for i in range(2, 10)
        ]
    ),
    planted_pii=["Alice Chen", "alice.chen@acme-corp.example.com"],
)

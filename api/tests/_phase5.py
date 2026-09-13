"""Shared scaffolding for the Phase 5 tests.

Extracted because five test modules need the same world — a department, a
doctor, a unit head, an admin, an auditor, a patient, an encounter, an order
and a case. Copying that into each file is how the fixtures drift apart and
two tests start meaning different things by "a flagged case".
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth import AuthenticatedUser, hash_password

# The password every seeded user gets. Long enough to satisfy the policy so a
# test that is not about the policy does not have to think about it.
TEST_PASSWORD = "Correct-Horse-Battery-9"


async def build_world(
    session: AsyncSession,
    *,
    severity: str = "critical",
    state: str = "flagged",
    flagged: bool = True,
) -> dict[str, str]:
    """A department with one of each role, a patient, and one case."""
    tag = uuid.uuid4().hex[:8]
    ids = {
        k: str(uuid.uuid4())
        for k in (
            "dept",
            "other_dept",
            "doctor",
            "other_doctor",
            "head",
            "admin",
            "auditor",
            "labtech",
            "pat",
            "enc",
            "order",
            "contract",
            "case",
        )
    }
    ids["tag"] = tag

    for key, role, code in (
        ("doctor", "doctor", "DOC"),
        ("other_doctor", "doctor", "ODC"),
        ("head", "unit_head", "HED"),
        ("admin", "admin", "ADM"),
        ("auditor", "auditor", "AUD"),
        ("labtech", "lab_tech", "LAB"),
    ):
        await session.execute(
            text(
                "INSERT INTO users (id, employee_code, full_name, role, is_active, "
                " password_hash, must_change_password, email) "
                "VALUES (:i, :c, :n, :r, true, :h, false, :em)"
            ),
            {
                "i": ids[key],
                "c": f"{code}{tag}",
                "n": f"{code} User",
                "r": role,
                "h": hash_password(TEST_PASSWORD),
                "em": f"{code.lower()}{tag}@example.test",
            },
        )

    for dept_key, name in (("dept", "Medicine"), ("other_dept", "Surgery")):
        await session.execute(
            text(
                "INSERT INTO departments (id, code, name, unit_head_user_id, active) "
                "VALUES (:i, :c, :n, :h, true)"
            ),
            {
                "i": ids[dept_key],
                "c": f"{dept_key[:3].upper()}{tag}",
                "n": name,
                "h": ids["head"] if dept_key == "dept" else None,
            },
        )

    await session.execute(
        text(
            "UPDATE users SET department_id = :d "
            " WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"d": ids["dept"], "ids": [ids["doctor"], ids["head"], ids["labtech"]]},
    )
    await session.execute(
        text("UPDATE users SET department_id = :d WHERE id = :u"),
        {"d": ids["other_dept"], "u": ids["other_doctor"]},
    )

    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, phone_primary_e164, "
            " phone_verified_at, preferred_language) "
            "VALUES (:i, :m, 'Sunita Rao', '+915550000099', now(), 'en')"
        ),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            " discharged_at, status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now() - interval '3 days', "
            "        now() - interval '1 day', 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"E{tag}", "d": ids["dept"]},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            " category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'URC', 'Urine Culture', 'micro', "
            "        now() - interval '2 days', 'in_lab')"
        ),
        {"i": ids["order"], "e": ids["enc"], "p": ids["pat"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["contract"], "e": ids["enc"], "o": ids["order"], "u": ids["doctor"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id, "
            " state, severity, opened_at, flagged_at, result_received_at) "
            "VALUES (:i, :o, :e, :p, :c, :u, :st, :sev, now() - interval '6 hours', "
            "        :flag, now() - interval '7 hours')"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["contract"],
            "u": ids["doctor"],
            "st": state,
            "sev": severity,
            "flag": (
                dt.datetime.now(dt.UTC) - dt.timedelta(hours=5) if flagged else None
            ),
        },
    )
    await session.commit()
    return ids


async def extra_case(
    session: AsyncSession,
    ids: dict[str, str],
    *,
    severity: str,
    hours_old: int,
    owner: str | None = None,
    department_id: str | None = None,
) -> str:
    """Another case in the same world, for sorting and pagination tests."""
    tag = uuid.uuid4().hex[:8]
    order_id, case_id = str(uuid.uuid4()), str(uuid.uuid4())
    encounter_id = ids["enc"]

    if department_id is not None and department_id != ids["dept"]:
        encounter_id = str(uuid.uuid4())
        await session.execute(
            text(
                "INSERT INTO encounters (id, patient_id, encounter_no, type, "
                " admitted_at, discharged_at, status, department_id) "
                "VALUES (:i, :p, :n, 'ipd', now(), now(), 'discharged', :d)"
            ),
            {
                "i": encounter_id,
                "p": ids["pat"],
                "n": f"E{tag}",
                "d": department_id,
            },
        )

    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            " category, ordered_at, status) "
            "VALUES (:i, :e, :p, :tc, :tn, 'lab', now(), 'in_lab')"
        ),
        {
            "i": order_id,
            "e": encounter_id,
            "p": ids["pat"],
            "tc": f"T{tag[:4]}",
            "tn": f"Test {tag[:4]}",
        },
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, current_owner_id, state, "
            " severity, opened_at, flagged_at) "
            "VALUES (:i, :o, :e, :p, :u, 'flagged', :sev, "
            "        now() - make_interval(hours => :h), "
            "        now() - make_interval(hours => :h))"
        ),
        {
            "i": case_id,
            "o": order_id,
            "e": encounter_id,
            "p": ids["pat"],
            "u": owner or ids["doctor"],
            "sev": severity,
            "h": hours_old,
        },
    )
    await session.commit()
    return case_id


async def login(
    client: httpx.AsyncClient, employee_code: str, password: str = TEST_PASSWORD
) -> dict[str, Any]:
    response = await client.post(
        "/api/auth/login",
        json={"employee_code": employee_code, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def bearer(
    client: httpx.AsyncClient, ids: dict[str, str], role_key: str
) -> dict[str, str]:
    """Log in as one of the seeded users and return the Authorization header."""
    code_prefix = {
        "doctor": "DOC",
        "other_doctor": "ODC",
        "head": "HED",
        "admin": "ADM",
        "auditor": "AUD",
        "labtech": "LAB",
    }[role_key]
    tokens = await login(client, f"{code_prefix}{ids['tag']}")
    return auth_header(tokens["access_token"])


# ── the Phase 5.1 retro-fit ────────────────────────────────────────────

STUB_ADMIN = AuthenticatedUser(
    id=uuid.UUID("00000000-0000-7000-8000-00000000ad00"),
    employee_code="TEST-ADMIN",
    full_name="Test Admin",
    role="admin",
    department_id=None,
    must_change_password=False,
)


def authenticate_as(app: Any, user: AuthenticatedUser = STUB_ADMIN) -> None:
    """Sign a test app in, without going through a login.

    Phase 5.1 put every endpoint behind RBAC. The Phases 1-4 test suites test
    *their own* behaviour, not authentication, and threading a real login
    through several hundred assertions would obscure what each one is actually
    checking — so they override the dependency and run as an admin.

    **The authentication itself is tested for real**, against a live Postgres
    and a real Argon2 hash, in `test_phase_5_auth.py`, and the fact that every
    endpoint carries the dependency at all is asserted black-box in
    `test_phase_5_rbac_coverage.py`. This override cannot hide a missing
    guard, because that test does not use it.
    """
    from app.security import current_user

    app.dependency_overrides[current_user] = lambda: user

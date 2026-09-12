"""Phase 3.7 — manual result entry and the severity preview.

The UI is a lab tech typing a report. The two things that must hold:

**Preview writes nothing.** A tech looking at what a value would mean must not
move a case by looking. Asserted by counting rows before and after, not by
reading the handler.

**Preview agrees with save.** A preview that predicted one severity and a save
that recorded another would be worse than no preview at all — it would teach
the ward to distrust the number. Asserted by previewing a payload, saving that
same payload, classifying it, and comparing.

Everything else is validation at the door: a transposed reference range, an
analyte with no value, an interpretation that is not S/I/R.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.rules import SEVERITY_CRITICAL, SEVERITY_FOLLOW_UP, SEVERITY_NORMAL
from app.rules.orchestrator import classify_result
from scripts.seed_rules_dev import _seed as seed_rules

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        conn = await engine.connect()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    trans = await conn.begin()
    maker = async_sessionmaker(
        bind=conn,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with maker() as s:
            await seed_rules(s)
            yield s
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _case(
    session: AsyncSession,
    *,
    category: str = "micro",
    discharge_antibiotics: tuple[str, ...] = (),
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:10]
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "pat", "enc", "order", "contract", "case")
    }
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:u, :uc, 'Dr Owner', 'doctor')"
        ),
        {"u": ids["user"], "uc": f"E{tag}"},
    )
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'D')"),
        {"i": ids["dept"], "c": f"D{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, sex, dob) "
            "VALUES (:i, :m, 'P', 'female', DATE '1979-04-02')"
        ),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"ENC{tag}", "d": ids["dept"]},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'URC', 'Urine Culture', :cat, now(), 'in_lab')"
        ),
        {"i": ids["order"], "e": ids["enc"], "p": ids["pat"], "cat": category},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["contract"], "e": ids["enc"], "o": ids["order"], "u": ids["user"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id) "
            "VALUES (:i, :o, :e, :p, :c, :u)"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["contract"],
            "u": ids["user"],
        },
    )
    for drug in discharge_antibiotics:
        await session.execute(
            text(
                "INSERT INTO discharge_medications "
                "(id, encounter_id, drug_name, is_antibiotic) "
                "VALUES (:i, :e, :d, true)"
            ),
            {"i": str(uuid.uuid4()), "e": ids["enc"], "d": drug},
        )
    await session.commit()
    return ids


RESISTANT_CULTURE = {
    "organisms": [
        {
            "organism_name": "Escherichia coli",
            "colony_count": ">100,000 CFU/mL",
            "specimen_type": "urine",
            "sensitivities": [
                {"antibiotic_name": "Ceftriaxone", "interpretation": "R"},
                {"antibiotic_name": "Nitrofurantoin", "interpretation": "S"},
            ],
        }
    ]
}


async def _count(session: AsyncSession, table: str) -> int:
    row = (await session.execute(text(f"SELECT count(*) AS n FROM {table}"))).one()
    return int(row.n)


# ── preview ───────────────────────────────────────────────────────────


async def test_preview_predicts_critical_for_a_resistant_culture(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The tech sees the problem while the report is still being typed."""
    ids = await _case(session, discharge_antibiotics=("Monocef",))

    response = await client.post(
        f"/api/orders/{ids['order']}/results/preview", json=RESISTANT_CULTURE
    )

    assert response.status_code == 200
    body = response.json()
    assert body["severity"] == SEVERITY_CRITICAL
    assert body["would_auto_close"] is False
    assert body["discharge_antibiotics"] == ["Monocef"]
    assert body["engine_version"]

    (row,) = body["rules"]
    assert row["rule_id"] == "B_culture"
    assert row["reason_code"] == "CULT_RESISTANT_TO_DISCHARGE_DRUG"
    assert row["offending_drug"] == "Monocef"
    assert row["subject"] == "Escherichia coli"
    # What to switch to, not just that it is wrong.
    assert row["alternatives_available"] == ["nitrofurantoin"]


async def test_preview_writes_nothing(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Counted, not assumed. Looking at a prediction must not move a case,
    record a classification, set a timer or queue a notification."""
    ids = await _case(session, discharge_antibiotics=("Monocef",))
    before = {
        table: await _count(session, table)
        for table in (
            "results",
            "classifications",
            "result_organisms",
            "result_analytes",
            "result_narratives",
            "sla_timers",
            "case_events",
            "pgmq.q_notifications",
            "pgmq.q_classify",
        )
    }

    response = await client.post(
        f"/api/orders/{ids['order']}/results/preview", json=RESISTANT_CULTURE
    )
    assert response.status_code == 200

    after = {table: await _count(session, table) for table in before}
    assert after == before

    case = (
        await session.execute(
            text("SELECT state, severity FROM pending_cases WHERE id = :i"),
            {"i": ids["case"]},
        )
    ).one()
    assert case.state == "awaiting_result"
    assert case.severity is None


async def test_preview_matches_what_saving_actually_records(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The property that makes the preview worth showing. A prediction that
    disagreed with the decision would teach the ward to ignore both."""
    ids = await _case(session, discharge_antibiotics=("Monocef",))

    predicted = (
        await client.post(
            f"/api/orders/{ids['order']}/results/preview", json=RESISTANT_CULTURE
        )
    ).json()

    saved = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={"report_status": "final", **RESISTANT_CULTURE},
    )
    assert saved.status_code == 201
    decided = await classify_result(session, uuid.UUID(saved.json()["result_id"]))

    assert predicted["severity"] == decided.severity
    assert predicted["engine_version"] == decided.engine_version
    assert [r["reason_code"] for r in predicted["rules"]] == [
        o.reason_code for o in decided.rule_outputs
    ]


@pytest.mark.parametrize(
    ("payload", "severity"),
    [
        (
            {
                "analytes": [
                    {
                        "test_name": "POTASSIUM",
                        "value_numeric": "7.4",
                        "unit": "mmol/L",
                        "ref_low": "3.5",
                        "ref_high": "5.1",
                    }
                ]
            },
            SEVERITY_CRITICAL,
        ),
        (
            {
                "analytes": [
                    {
                        "test_name": "POTASSIUM",
                        "value_numeric": "4.2",
                        "ref_low": "3.5",
                        "ref_high": "5.1",
                    }
                ]
            },
            SEVERITY_NORMAL,
        ),
        (
            {
                "narratives": [
                    {"section": "impression", "text": "No evidence of malignancy."}
                ]
            },
            SEVERITY_FOLLOW_UP,
        ),
        (
            {
                "narratives": [
                    {
                        "section": "impression",
                        "text": "Findings consistent with malignancy.",
                    }
                ]
            },
            SEVERITY_CRITICAL,
        ),
    ],
)
async def test_preview_grades_each_form(
    session: AsyncSession,
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    severity: str,
) -> None:
    """One case per form in the UI: numeric panel, and narrative."""
    ids = await _case(session, category="radiology")
    response = await client.post(
        f"/api/orders/{ids['order']}/results/preview", json=payload
    )
    assert response.status_code == 200
    assert response.json()["severity"] == severity


async def test_an_empty_preview_is_follow_up_not_normal(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """An empty form does not mean a clean report."""
    ids = await _case(session)
    response = await client.post(f"/api/orders/{ids['order']}/results/preview", json={})
    assert response.status_code == 200
    assert response.json()["severity"] == SEVERITY_FOLLOW_UP


async def test_preview_for_an_unknown_order_is_404(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        f"/api/orders/{uuid.uuid4()}/results/preview", json=RESISTANT_CULTURE
    )
    assert response.status_code == 404


async def test_a_narrative_always_blocks_auto_close_in_the_preview(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The tech is told before saving that this one will stay open."""
    ids = await _case(session, discharge_antibiotics=("Monocef",))
    response = await client.post(
        f"/api/orders/{ids['order']}/results/preview",
        json={
            "organisms": [
                {
                    "organism_name": "Escherichia coli",
                    "colony_count": ">100,000 CFU/mL",
                    "specimen_type": "urine",
                    "sensitivities": [
                        {"antibiotic_name": "Ceftriaxone", "interpretation": "S"}
                    ],
                }
            ],
            "narratives": [
                {"section": "microscopy", "text": "Plenty of pus cells seen."}
            ],
        },
    )
    body = response.json()
    assert body["severity"] == SEVERITY_NORMAL
    assert body["would_auto_close"] is False


# ── saving structured content ─────────────────────────────────────────


async def test_saving_a_culture_stores_the_organism_and_its_panel(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session, discharge_antibiotics=("Monocef",))

    response = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={"report_status": "final", "source_ref": "ACC-1", **RESISTANT_CULTURE},
    )
    assert response.status_code == 201
    result_id = response.json()["result_id"]

    organism = (
        await session.execute(
            text(
                "SELECT id, organism_name, colony_count, specimen_type "
                "  FROM result_organisms WHERE result_id = :r"
            ),
            {"r": result_id},
        )
    ).one()
    assert organism.organism_name == "Escherichia coli"
    assert organism.colony_count == ">100,000 CFU/mL"

    panel = (
        await session.execute(
            text(
                "SELECT antibiotic_name, interpretation FROM result_sensitivities "
                " WHERE organism_id = :o ORDER BY antibiotic_name"
            ),
            {"o": str(organism.id)},
        )
    ).all()
    assert [(p.antibiotic_name, p.interpretation) for p in panel] == [
        ("Ceftriaxone", "R"),
        ("Nitrofurantoin", "S"),
    ]


async def test_saving_a_numeric_panel_keeps_the_row_order(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """``seq`` is what makes the stored panel look like the printed one."""
    ids = await _case(session, category="lab")
    response = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={
            "report_status": "final",
            "analytes": [
                {"test_name": "SODIUM", "value_numeric": "138", "unit": "mmol/L"},
                {"test_name": "POTASSIUM", "value_numeric": "4.1", "unit": "mmol/L"},
                {"test_name": "CREATININE", "value_raw": "<0.2", "unit": "mg/dL"},
            ],
        },
    )
    assert response.status_code == 201

    rows = (
        await session.execute(
            text(
                "SELECT seq, test_name_raw, value_numeric, value_raw, unit_normalized "
                "  FROM result_analytes WHERE result_id = :r ORDER BY seq"
            ),
            {"r": response.json()["result_id"]},
        )
    ).all()
    assert [r.test_name_raw for r in rows] == ["SODIUM", "POTASSIUM", "CREATININE"]
    assert [r.seq for r in rows] == [1, 2, 3]
    # A censored value survives intake as text rather than being dropped.
    assert rows[2].value_raw == "<0.2"
    assert rows[2].value_numeric is None


async def test_saving_a_narrative_stores_each_section(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session, category="radiology")
    response = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={
            "report_status": "final",
            "narratives": [
                {"section": "findings", "text": "A 1.5 cm nodule in the RUL."},
                {"section": "impression", "text": "No evidence of malignancy."},
            ],
        },
    )
    assert response.status_code == 201

    rows = (
        await session.execute(
            text(
                "SELECT section, text FROM result_narratives "
                " WHERE result_id = :r ORDER BY section"
            ),
            {"r": response.json()["result_id"]},
        )
    ).all()
    assert [r.section for r in rows] == ["findings", "impression"]


async def test_a_phase_2_payload_with_no_content_still_works(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """THE ONE RULE. A caller written against Phase 2 sends no structured
    content and must behave exactly as it did — the endpoint is the permanent
    intake path and Phase 6/7 callers were built against it."""
    ids = await _case(session)
    response = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={"report_status": "final", "raw_payload": {"anything": "at all"}},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["case_state"] == "result_received"
    assert body["classification_enqueued"] is True

    # Nothing structured was invented on the caller's behalf.
    assert (
        await _count(session, "result_analytes")
        + await _count(session, "result_organisms")
        + await _count(session, "result_narratives")
    ) == 0


async def test_saved_content_is_what_the_worker_then_classifies(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Intake to flagged case, through the real endpoint and the real
    consumer — the path a lab tech's Save button actually takes."""
    from worker.consumers.classify import handle_classify

    ids = await _case(session, discharge_antibiotics=("Monocef",))
    saved = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={"report_status": "final", **RESISTANT_CULTURE},
    )
    await handle_classify(session, {"result_id": saved.json()["result_id"]})

    case = (
        await session.execute(
            text("SELECT state, severity FROM pending_cases WHERE id = :i"),
            {"i": ids["case"]},
        )
    ).one()
    assert case.state == "flagged"
    assert case.severity == SEVERITY_CRITICAL


# ── validation at the door ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("content", "why"),
    [
        (
            {"analytes": [{"test_name": "POTASSIUM"}]},
            "an analyte with neither a number nor raw text says nothing",
        ),
        (
            {
                "analytes": [
                    {
                        "test_name": "POTASSIUM",
                        "value_numeric": "4",
                        "ref_low": "10",
                        "ref_high": "1",
                    }
                ]
            },
            "a transposed range silently inverts every comparison Rule A makes",
        ),
        (
            {
                "organisms": [
                    {
                        "organism_name": "E. coli",
                        "sensitivities": [
                            {"antibiotic_name": "Ceftriaxone", "interpretation": "X"}
                        ],
                    }
                ]
            },
            "S/I/R is the whole vocabulary; anything else is a typo",
        ),
        (
            {"narratives": [{"section": "summary", "text": "x"}]},
            "an unknown section would be stored and never read",
        ),
        (
            {"narratives": [{"section": "impression", "text": ""}]},
            "an empty section is not a finding",
        ),
    ],
)
async def test_bad_content_is_refused_before_it_reaches_a_rule(
    session: AsyncSession,
    client: httpx.AsyncClient,
    content: dict[str, Any],
    why: str,
) -> None:
    ids = await _case(session)
    saved = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={"report_status": "final", **content},
    )
    assert saved.status_code == 422, why
    previewed = await client.post(
        f"/api/orders/{ids['order']}/results/preview", json=content
    )
    assert previewed.status_code == 422, why

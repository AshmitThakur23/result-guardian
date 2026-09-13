"""``/api/admin`` — user management and the configuration editors. Phase 5.4.

    Admin: user management, roster, panic thresholds editor, escalation chain
    editor, keyword editor, notification provider health, **NODE B status and
    kill switch (``LLM_ENABLED``)**
    Overrides report — every discharge override with reason and who approved

The roster editor is not here: Phase 4.1 already built it at
``/api/roster``, and ADR 0004 gives it to the **unit head**, not the admin.
Duplicating it under ``/admin`` would create two write paths into one table.

**Every write in this module produces an audit row in the same transaction.**
A configuration change that nobody can attribute is how a panic threshold
quietly becomes wrong.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models.organisation import USER_ROLES
from app.db.models.ownership import ESCALATION_TARGETS, NOTIFICATION_CHANNELS
from app.db.models.rules_config import KEYWORD_CATEGORIES, KEYWORD_SEVERITIES
from app.db.session import get_session
from app.db.types import uuid7
from app.schemas.admin import (
    EscalationRungOut,
    EscalationRungUpsert,
    KeywordCreate,
    KeywordOut,
    KeywordUpdate,
    KillSwitchRequest,
    NodeBStatusOut,
    OverrideReportRow,
    PanicThresholdCreate,
    PanicThresholdOut,
    PasswordResetResult,
    ProviderHealthOut,
    UserCreate,
    UserCreated,
    UserOut,
    UserUpdate,
)
from app.security import client_ip, require_role
from app.services import audit as audit_service
from app.services import settings_store
from app.services.auth import AuthenticatedUser, hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_ONLY = ("admin",)
# The read-only config views are useful to an auditor too: "what was the
# threshold on the day of the incident" is an audit question.
CONFIG_READERS = ("admin", "auditor")


def _temporary_password() -> str:
    """A generated first password.

    URL-safe base64 of 12 random bytes: 96 bits, long enough that the window
    between creation and first login is not an attack surface, short enough to
    read down a phone line.
    """
    return secrets.token_urlsafe(12)


def _validate(value: str, allowed: tuple[str, ...], field: str) -> None:
    if value not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown {field}: '{value}'. Expected one of: "
            f"{', '.join(allowed)}.",
        )


# ── users ──────────────────────────────────────────────────────────────


@router.get("/users", response_model=list[UserOut], summary="List users")
async def list_users(
    include_inactive: bool = False,
    role: str | None = Query(default=None, max_length=20),
    department_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(require_role(*CONFIG_READERS)),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    where = ["u.deleted_at IS NULL"]
    params: dict[str, Any] = {}
    if not include_inactive:
        where.append("u.is_active")
    if role:
        _validate(role, USER_ROLES, "role")
        where.append("u.role = :role")
        params["role"] = role
    if department_id is not None:
        where.append("u.department_id = :dept")
        params["dept"] = str(department_id)

    rows = (
        await session.execute(
            text(
                "SELECT u.id, u.employee_code, u.full_name, u.email, u.phone_e164, "
                "       u.role, u.department_id, d.name AS department_name, "
                "       u.is_active, u.must_change_password, u.last_login_at, "
                "       u.locked_until, u.failed_login_count "
                "  FROM users u LEFT JOIN departments d ON d.id = u.department_id "
                f" WHERE {' AND '.join(where)} "
                " ORDER BY u.full_name"
            ),
            params,
        )
    ).all()
    return [UserOut(**dict(r._mapping)) for r in rows]


@router.post(
    "/users",
    response_model=UserCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user — first password must be changed on login",
)
async def create_user(
    request: Request,
    payload: UserCreate,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> UserCreated:
    """The temporary password is returned **once** and never stored readably.

    ``must_change_password`` is set, so the account cannot do anything except
    change it — see :func:`app.security.require_role`.
    """
    _validate(payload.role, USER_ROLES, "role")

    existing = (
        await session.execute(
            text("SELECT id FROM users WHERE employee_code = :c"),
            {"c": payload.employee_code},
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Employee code '{payload.employee_code}' is already in use.",
        )

    password = payload.initial_password or _temporary_password()
    new_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, email, phone_e164, "
            "                   role, department_id, password_hash, "
            "                   must_change_password, created_by, updated_by) "
            "VALUES (:id, :code, :name, :email, :phone, :role, :dept, :hash, "
            "        true, :actor, :actor)"
        ),
        {
            "id": str(new_id),
            "code": payload.employee_code,
            "name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone_e164,
            "role": payload.role,
            "dept": str(payload.department_id) if payload.department_id else None,
            "hash": hash_password(password),
            "actor": str(user.id),
        },
    )
    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="user",
        entity_id=new_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        after={
            "created": True,
            "employee_code": payload.employee_code,
            "role": payload.role,
            "department_id": payload.department_id,
        },
    )
    await session.commit()
    return UserCreated(
        id=new_id,
        employee_code=payload.employee_code,
        # Only surfaced when we generated it. An admin who chose the password
        # already knows it, and echoing it back puts it in one more log.
        temporary_password=None if payload.initial_password else password,
    )


@router.patch("/users/{user_id}", response_model=UserOut, summary="Update a user")
async def update_user(
    request: Request,
    user_id: uuid.UUID,
    payload: UserUpdate,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    before = (
        await session.execute(
            text(
                "SELECT role, department_id, is_active, full_name, email, "
                "       phone_e164, locked_until "
                "  FROM users WHERE id = :id AND deleted_at IS NULL FOR UPDATE"
            ),
            {"id": str(user_id)},
        )
    ).first()
    if before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such user"
        )

    if payload.role is not None:
        _validate(payload.role, USER_ROLES, "role")

    sets: list[str] = []
    params: dict[str, Any] = {"id": str(user_id), "actor": str(user.id)}
    for field in ("full_name", "email", "phone_e164", "role", "is_active"):
        value = getattr(payload, field)
        if value is not None:
            sets.append(f"{field} = :{field}")
            params[field] = value
    if "department_id" in payload.model_fields_set:
        sets.append("department_id = :department_id")
        params["department_id"] = (
            str(payload.department_id) if payload.department_id else None
        )
    if payload.unlock:
        sets.append("locked_until = NULL")
        sets.append("failed_login_count = 0")

    if not sets:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update.",
        )

    sets.append("updated_at = now()")
    sets.append("updated_by = :actor")
    await session.execute(
        text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id"), params
    )

    # Deactivating or demoting must take effect now, not at token expiry.
    if payload.is_active is False or payload.role is not None:
        await session.execute(
            text(
                "UPDATE sessions SET revoked_at = now(), "
                "       revoked_reason = 'role_changed', updated_at = now() "
                " WHERE user_id = :id AND revoked_at IS NULL"
            ),
            {"id": str(user_id)},
        )

    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="user",
        entity_id=user_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before=dict(before._mapping),
        after=payload.model_dump(exclude_unset=True),
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT u.id, u.employee_code, u.full_name, u.email, u.phone_e164, "
                "       u.role, u.department_id, d.name AS department_name, "
                "       u.is_active, u.must_change_password, u.last_login_at, "
                "       u.locked_until, u.failed_login_count "
                "  FROM users u LEFT JOIN departments d ON d.id = u.department_id "
                " WHERE u.id = :id"
            ),
            {"id": str(user_id)},
        )
    ).one()
    return UserOut(**dict(row._mapping))


@router.post(
    "/users/{user_id}/reset-password",
    response_model=PasswordResetResult,
    summary="Issue a new temporary password",
)
async def reset_password(
    request: Request,
    user_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> PasswordResetResult:
    """Revokes every session the user holds.

    A password reset that leaves the old sessions live does not lock anybody
    out, which defeats the purpose when the reset is a response to a
    compromise.
    """
    exists = (
        await session.execute(
            text("SELECT id FROM users WHERE id = :id AND deleted_at IS NULL"),
            {"id": str(user_id)},
        )
    ).first()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such user"
        )

    password = _temporary_password()
    await session.execute(
        text(
            "UPDATE users SET password_hash = :hash, must_change_password = true, "
            "       password_changed_at = now(), failed_login_count = 0, "
            "       locked_until = NULL, updated_at = now(), updated_by = :actor "
            " WHERE id = :id"
        ),
        {"hash": hash_password(password), "id": str(user_id), "actor": str(user.id)},
    )
    await session.execute(
        text(
            "UPDATE sessions SET revoked_at = now(), "
            "       revoked_reason = 'password_reset', updated_at = now() "
            " WHERE user_id = :id AND revoked_at IS NULL"
        ),
        {"id": str(user_id)},
    )
    await audit_service.append(
        session,
        action=audit_service.ACTION_PASSWORD_CHANGED,
        entity_type="user",
        entity_id=user_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        after={"reset_by_admin": True, "sessions_revoked": True},
    )
    await session.commit()
    return PasswordResetResult(user_id=user_id, temporary_password=password)


# ── panic thresholds ───────────────────────────────────────────────────


@router.get(
    "/panic-thresholds",
    response_model=list[PanicThresholdOut],
    summary="Panic threshold editor — Rule A step 5",
)
async def list_panic_thresholds(
    test_code: str | None = Query(default=None, max_length=64),
    include_expired: bool = False,
    user: AuthenticatedUser = Depends(require_role(*CONFIG_READERS)),
    session: AsyncSession = Depends(get_session),
) -> list[PanicThresholdOut]:
    where = ["deleted_at IS NULL"]
    params: dict[str, Any] = {}
    if not include_expired:
        where.append("(effective_to IS NULL OR effective_to > now())")
    if test_code:
        where.append("test_code = :tc")
        params["tc"] = test_code

    rows = (
        await session.execute(
            text(
                "SELECT id, test_code, loinc_code, sex, age_min_years, age_max_years, "
                "       critical_low, critical_high, follow_up_low_multiplier, "
                "       follow_up_high_multiplier, unit, source, effective_from, "
                "       effective_to FROM panic_thresholds "
                f" WHERE {' AND '.join(where)} ORDER BY test_code, sex, effective_from"
            ),
            params,
        )
    ).all()
    return [PanicThresholdOut(**dict(r._mapping)) for r in rows]


@router.post(
    "/panic-thresholds",
    response_model=PanicThresholdOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a threshold — supersedes rather than edits",
)
async def create_panic_threshold(
    request: Request,
    payload: PanicThresholdCreate,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> PanicThresholdOut:
    """**Editing is closing the old row and opening a new one.**

    ``effective_from``/``effective_to`` exist so that a classification made
    last March can still be explained with the threshold that was in force
    last March. Mutating the row in place would silently rewrite the reasoning
    behind every past decision, which is the one thing the rule engine's
    audit story cannot survive.
    """
    if payload.critical_low is None and payload.critical_high is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A threshold must set critical_low, critical_high, or both.",
        )

    effective_from = payload.effective_from or dt.datetime.now(dt.UTC)

    # Close the currently-live row for the same (test, sex) at the moment the
    # new one starts, so the two never overlap.
    superseded = (
        (
            await session.execute(
                text(
                    "UPDATE panic_thresholds SET effective_to = :from_ts, "
                    "       updated_at = now(), updated_by = :actor "
                    " WHERE test_code = :tc AND sex = :sex AND deleted_at IS NULL "
                    "   AND effective_from < :from_ts "
                    "   AND (effective_to IS NULL OR effective_to > :from_ts) "
                    " RETURNING id"
                ),
                {
                    "tc": payload.test_code,
                    "sex": payload.sex,
                    "from_ts": effective_from,
                    "actor": str(user.id),
                },
            )
        )
        .scalars()
        .all()
    )

    new_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO panic_thresholds "
            "(id, test_code, loinc_code, sex, age_min_years, age_max_years, "
            " critical_low, critical_high, follow_up_low_multiplier, "
            " follow_up_high_multiplier, unit, source, effective_from, "
            " created_by, updated_by) "
            "VALUES (:id, :tc, :loinc, :sex, :amin, :amax, :clow, :chigh, "
            "        :flow, :fhigh, :unit, :source, :from_ts, :actor, :actor)"
        ),
        {
            "id": str(new_id),
            "tc": payload.test_code,
            "loinc": payload.loinc_code,
            "sex": payload.sex,
            "amin": payload.age_min_years,
            "amax": payload.age_max_years,
            "clow": payload.critical_low,
            "chigh": payload.critical_high,
            "flow": payload.follow_up_low_multiplier,
            "fhigh": payload.follow_up_high_multiplier,
            "unit": payload.unit,
            "source": payload.source,
            "from_ts": effective_from,
            "actor": str(user.id),
        },
    )
    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="panic_threshold",
        entity_id=new_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        after={
            **payload.model_dump(mode="json"),
            "effective_from": effective_from,
            "superseded_ids": [str(s) for s in superseded],
        },
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT id, test_code, loinc_code, sex, age_min_years, age_max_years, "
                "       critical_low, critical_high, follow_up_low_multiplier, "
                "       follow_up_high_multiplier, unit, source, effective_from, "
                "       effective_to FROM panic_thresholds WHERE id = :id"
            ),
            {"id": str(new_id)},
        )
    ).one()
    return PanicThresholdOut(**dict(row._mapping))


# ── clinical keywords ──────────────────────────────────────────────────


@router.get("/keywords", response_model=list[KeywordOut], summary="Keyword editor")
async def list_keywords(
    include_inactive: bool = False,
    user: AuthenticatedUser = Depends(require_role(*CONFIG_READERS)),
    session: AsyncSession = Depends(get_session),
) -> list[KeywordOut]:
    where = "deleted_at IS NULL" + ("" if include_inactive else " AND active")
    rows = (
        await session.execute(
            text(
                "SELECT id, term, category, severity, requires_negation_check, active "
                f"  FROM clinical_keywords WHERE {where} ORDER BY category, term"
            )
        )
    ).all()
    return [KeywordOut(**dict(r._mapping)) for r in rows]


@router.post(
    "/keywords",
    response_model=KeywordOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a clinical keyword",
)
async def create_keyword(
    request: Request,
    payload: KeywordCreate,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> KeywordOut:
    _validate(payload.category, KEYWORD_CATEGORIES, "category")
    _validate(payload.severity, KEYWORD_SEVERITIES, "severity")

    new_id = uuid7()
    inserted = (
        await session.execute(
            text(
                "INSERT INTO clinical_keywords "
                "(id, term, category, severity, requires_negation_check, active, "
                " created_by, updated_by) "
                "VALUES (:id, :term, :cat, :sev, :neg, :active, :actor, :actor) "
                "ON CONFLICT (term) DO NOTHING RETURNING id"
            ),
            {
                "id": str(new_id),
                "term": payload.term,
                "cat": payload.category,
                "sev": payload.severity,
                "neg": payload.requires_negation_check,
                "active": payload.active,
                "actor": str(user.id),
            },
        )
    ).first()
    if inserted is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The term '{payload.term}' already exists.",
        )

    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="clinical_keyword",
        entity_id=new_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        after=payload.model_dump(mode="json"),
    )
    await session.commit()
    return KeywordOut(id=new_id, **payload.model_dump())


@router.patch(
    "/keywords/{keyword_id}", response_model=KeywordOut, summary="Edit a keyword"
)
async def update_keyword(
    request: Request,
    keyword_id: uuid.UUID,
    payload: KeywordUpdate,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> KeywordOut:
    before = (
        await session.execute(
            text(
                "SELECT term, category, severity, requires_negation_check, active "
                "  FROM clinical_keywords WHERE id = :id AND deleted_at IS NULL "
                "   FOR UPDATE"
            ),
            {"id": str(keyword_id)},
        )
    ).first()
    if before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such keyword"
        )
    if payload.category is not None:
        _validate(payload.category, KEYWORD_CATEGORIES, "category")
    if payload.severity is not None:
        _validate(payload.severity, KEYWORD_SEVERITIES, "severity")

    sets: list[str] = []
    params: dict[str, Any] = {"id": str(keyword_id), "actor": str(user.id)}
    for field in ("category", "severity", "requires_negation_check", "active"):
        value = getattr(payload, field)
        if value is not None:
            sets.append(f"{field} = :{field}")
            params[field] = value
    if not sets:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update.",
        )
    sets += ["updated_at = now()", "updated_by = :actor"]

    await session.execute(
        text(f"UPDATE clinical_keywords SET {', '.join(sets)} WHERE id = :id"), params
    )
    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="clinical_keyword",
        entity_id=keyword_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before=dict(before._mapping),
        after=payload.model_dump(exclude_unset=True),
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT id, term, category, severity, requires_negation_check, active "
                "  FROM clinical_keywords WHERE id = :id"
            ),
            {"id": str(keyword_id)},
        )
    ).one()
    return KeywordOut(**dict(row._mapping))


# ── escalation chain ───────────────────────────────────────────────────


@router.get(
    "/escalation-chain",
    response_model=list[EscalationRungOut],
    summary="Escalation chain editor — rows with a null department are the "
    "global default",
)
async def list_escalation_chain(
    department_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(require_role(*CONFIG_READERS)),
    session: AsyncSession = Depends(get_session),
) -> list[EscalationRungOut]:
    where = ["c.deleted_at IS NULL"]
    params: dict[str, Any] = {}
    if department_id is not None:
        # Include the global default rows: a department inherits the rungs it
        # has not overridden, and an editor that hides them would let an admin
        # "delete" a rung that is still live.
        where.append("(c.department_id = :dept OR c.department_id IS NULL)")
        params["dept"] = str(department_id)

    rows = (
        await session.execute(
            text(
                "SELECT c.id, c.department_id, d.name AS department_name, c.level, "
                "       c.target_type, c.delay_minutes, c.channels, c.severity, "
                "       c.active "
                "  FROM escalation_chain c "
                "  LEFT JOIN departments d ON d.id = c.department_id "
                f" WHERE {' AND '.join(where)} "
                " ORDER BY d.name NULLS FIRST, c.severity, c.level"
            ),
            params,
        )
    ).all()
    return [EscalationRungOut(**dict(r._mapping)) for r in rows]


@router.put(
    "/escalation-chain",
    response_model=EscalationRungOut,
    summary="Create or replace one rung",
)
async def upsert_escalation_rung(
    request: Request,
    payload: EscalationRungUpsert,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
) -> EscalationRungOut:
    """Keyed on ``(department_id, severity, level)``.

    **Changing the ladder does not touch timers that are already scheduled.**
    Phase 4 computes each rung's ``fire_at`` when the case is flagged, so an
    edit applies to cases flagged afterwards. That is deliberate: rewriting
    live timers would let an admin push every pending escalation out of reach
    with one form submission.
    """
    _validate(payload.target_type, ESCALATION_TARGETS, "target_type")
    unknown = sorted(set(payload.channels) - set(NOTIFICATION_CHANNELS))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown channel(s): {', '.join(unknown)}. Expected from: "
            f"{', '.join(NOTIFICATION_CHANNELS)}.",
        )

    before = (
        await session.execute(
            text(
                "SELECT id, target_type, delay_minutes, channels, active "
                "  FROM escalation_chain "
                " WHERE department_id IS NOT DISTINCT FROM :dept "
                "   AND severity = :sev AND level = :lvl AND deleted_at IS NULL"
            ),
            {
                "dept": str(payload.department_id) if payload.department_id else None,
                "sev": payload.severity,
                "lvl": payload.level,
            },
        )
    ).first()

    import json as _json

    row = (
        await session.execute(
            text(
                "INSERT INTO escalation_chain "
                "(id, department_id, level, target_type, delay_minutes, channels, "
                " severity, active, created_by, updated_by) "
                "VALUES (CAST(:new_id AS uuid), :dept, :lvl, :target, :delay, "
                "        CAST(:channels AS jsonb), :sev, :active, :actor, :actor) "
                "ON CONFLICT (department_id, severity, level) DO UPDATE "
                "   SET target_type = EXCLUDED.target_type, "
                "       delay_minutes = EXCLUDED.delay_minutes, "
                "       channels = EXCLUDED.channels, "
                "       active = EXCLUDED.active, "
                "       updated_at = now(), updated_by = EXCLUDED.updated_by "
                "RETURNING id"
            ),
            {
                # UUIDv7 in Python -- this column has no server-side default.
                "new_id": str(uuid7()),
                "dept": str(payload.department_id) if payload.department_id else None,
                "lvl": payload.level,
                "target": payload.target_type,
                "delay": payload.delay_minutes,
                "channels": _json.dumps(payload.channels),
                "sev": payload.severity,
                "active": payload.active,
                "actor": str(user.id),
            },
        )
    ).one()

    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="escalation_chain",
        entity_id=row.id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before=dict(before._mapping) if before else None,
        after=payload.model_dump(mode="json"),
    )
    await session.commit()

    out = (
        await session.execute(
            text(
                "SELECT c.id, c.department_id, d.name AS department_name, c.level, "
                "       c.target_type, c.delay_minutes, c.channels, c.severity, "
                "       c.active "
                "  FROM escalation_chain c "
                "  LEFT JOIN departments d ON d.id = c.department_id "
                " WHERE c.id = :id"
            ),
            {"id": str(row.id)},
        )
    ).one()
    return EscalationRungOut(**dict(out._mapping))


# ── notification provider health ───────────────────────────────────────


@router.get(
    "/provider-health",
    response_model=list[ProviderHealthOut],
    summary="Notification provider health over the last 24 hours",
)
async def provider_health(
    user: AuthenticatedUser = Depends(require_role(*CONFIG_READERS)),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderHealthOut]:
    rows = (
        await session.execute(
            text(
                "SELECT channel, "
                "       count(*) FILTER (WHERE status = 'sent') AS sent, "
                "       count(*) FILTER (WHERE status = 'failed') AS failed, "
                "       count(*) FILTER (WHERE status = 'suppressed') AS suppressed, "
                "       max(updated_at) FILTER (WHERE status = 'failed') "
                "         AS last_failure_at, "
                "       (array_agg(error ORDER BY updated_at DESC) "
                "         FILTER (WHERE status = 'failed' AND error IS NOT NULL)"
                "       )[1] AS last_error "
                "  FROM notifications "
                " WHERE deleted_at IS NULL "
                "   AND created_at > now() - interval '24 hours' "
                " GROUP BY channel ORDER BY channel"
            )
        )
    ).all()

    from app.services.notifications import registry

    adapters = registry()
    out: list[ProviderHealthOut] = []
    for r in rows:
        attempted = int(r.sent) + int(r.failed)
        # `for_channel` also reports the fallback, which matters here: a
        # channel showing "InAppAdapter" is one with no provider configured,
        # and that is exactly what this screen exists to surface.
        adapter, delivered_on = adapters.for_channel(r.channel)
        out.append(
            ProviderHealthOut(
                channel=r.channel,
                adapter=type(adapter).__name__
                + (
                    ""
                    if delivered_on == r.channel
                    else f" (falls back to {delivered_on})"
                ),
                sent_24h=int(r.sent),
                failed_24h=int(r.failed),
                suppressed_24h=int(r.suppressed),
                failure_rate=round(int(r.failed) / attempted, 4) if attempted else 0.0,
                last_failure_at=r.last_failure_at,
                last_error=(r.last_error or "")[:300] or None,
            )
        )
    return out


# ── NODE B status and the kill switch ──────────────────────────────────


@router.get(
    "/node-b",
    response_model=NodeBStatusOut,
    summary="NODE B status and the LLM_ENABLED kill switch",
)
async def node_b_status(
    request: Request,
    user: AuthenticatedUser = Depends(require_role(*CONFIG_READERS)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> NodeBStatusOut:
    override = await settings_store.get(session, settings_store.KEY_LLM_ENABLED)
    enabled = settings.llm_enabled if override is None else bool(override)
    reason = await settings_store.get(
        session, settings_store.KEY_LLM_KILL_REASON, default=""
    )
    probe = await request.app.state.llm_probe.status()

    return NodeBStatusOut(
        llm_enabled=enabled,
        llm_enabled_source="database" if override is not None else "environment",
        kill_switch_reason=(reason or None) if not enabled else None,
        reachable=bool(probe.reachable),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        probe_error=getattr(probe, "error", None),
    )


@router.post(
    "/node-b/kill-switch",
    response_model=NodeBStatusOut,
    summary="Turn NODE B inference on or off — takes effect within 10 seconds",
)
async def set_kill_switch(
    request: Request,
    payload: KillSwitchRequest,
    user: AuthenticatedUser = Depends(require_role(*ADMIN_ONLY)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> NodeBStatusOut:
    """RULE 1 made operational.

    Turning this off costs the hospital summarisation and search quality. It
    costs **nothing** in tracking, timers, escalation or notification — those
    are Phases 0–5 and run entirely on NODE A. An admin must be able to make
    that trade in one click at 3am without reading a runbook.
    """
    await settings_store.set_value(
        session,
        settings_store.KEY_LLM_ENABLED,
        payload.llm_enabled,
        actor_user_id=user.id,
    )
    await settings_store.set_value(
        session,
        settings_store.KEY_LLM_KILL_REASON,
        payload.reason,
        actor_user_id=user.id,
    )
    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before={"llm_enabled": not payload.llm_enabled},
        after={
            "llm_enabled": payload.llm_enabled,
            "reason": payload.reason,
            "setting": settings_store.KEY_LLM_ENABLED,
        },
    )
    await session.commit()
    settings_store.invalidate()

    probe = await request.app.state.llm_probe.status()
    return NodeBStatusOut(
        llm_enabled=payload.llm_enabled,
        llm_enabled_source="database",
        kill_switch_reason=payload.reason if not payload.llm_enabled else None,
        reachable=bool(probe.reachable),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        probe_error=getattr(probe, "error", None),
    )


# ── overrides report ───────────────────────────────────────────────────


@router.get(
    "/overrides",
    response_model=list[OverrideReportRow],
    summary="Every discharge override, with reason and who approved",
)
async def overrides_report(
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    department_id: uuid.UUID | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    user: AuthenticatedUser = Depends(require_role("admin", "auditor", "unit_head")),
    session: AsyncSession = Depends(get_session),
) -> list[OverrideReportRow]:
    where = ["o.deleted_at IS NULL"]
    params: dict[str, Any] = {"lim": limit}
    if from_ is not None:
        where.append("o.created_at >= :from_ts")
        params["from_ts"] = from_
    if to is not None:
        where.append("o.created_at <= :to_ts")
        params["to_ts"] = to

    scoped_department = (
        user.department_id if user.role == "unit_head" else department_id
    )
    if scoped_department is not None:
        where.append("e.department_id = :dept")
        params["dept"] = str(scoped_department)

    rows = (
        await session.execute(
            text(
                "SELECT o.id, o.encounter_id, o.order_id, o.reason_code, "
                "       o.reason_text, o.created_at, "
                "       p.name AS patient_name, p.mrn, "
                "       ob.full_name AS overridden_by_name, "
                "       ap.full_name AS approved_by_name, "
                "       d.name AS department_name "
                "  FROM discharge_overrides o "
                "  JOIN encounters e ON e.id = o.encounter_id "
                "  LEFT JOIN patients p ON p.id = e.patient_id "
                "  LEFT JOIN users ob ON ob.id = o.overridden_by "
                "  LEFT JOIN users ap ON ap.id = o.approved_by "
                "  LEFT JOIN departments d ON d.id = e.department_id "
                f" WHERE {' AND '.join(where)} "
                " ORDER BY o.created_at DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    return [OverrideReportRow(**dict(r._mapping)) for r in rows]

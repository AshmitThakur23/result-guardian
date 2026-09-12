"""Investigation orders. Phase 1.5 adds the manual creation path.

*"Manual order creation (until HIS integration exists)"* -- the build plan's
own framing. This is a stand-in for the Phase 9 HIS feed, not a clinical
ordering system, so it validates the schema's constraints and nothing more. It
invents no clinical rules: which test is appropriate for which patient is the
doctor's decision, and stays the doctor's decision.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.db.models.orders import ORDER_CATEGORIES, ORDER_STATUSES

# A manually created order cannot start in a terminal state. Creating an order
# that is already `final` would mean recording a result, which is Phase 6/7 --
# and creating one already `cancelled` or `rejected` is just noise that never
# blocks anything.
MANUAL_ORDER_STATUSES = ("ordered", "collected", "in_lab")

# The schema allows NUMERIC(6,2); this is the sane clinical ceiling. Thirty
# days of turnaround is already far beyond anything a ward orders.
MAX_TAT_HOURS = Decimal("720")


class OrderCreate(BaseModel):
    """One manually entered investigation.

    ``ordered_at`` is an aware datetime by requirement, for the same reason
    ``expected_by`` is on a contract: the gate derives the suggested deadline
    from ``ordered_at + expected_tat_hours``, and a naive value read against
    the server's locale is a deadline that fires at the wrong hour.
    """

    model_config = ConfigDict(extra="forbid")

    test_code: str = Field(min_length=1, max_length=64)
    test_name: str = Field(min_length=1, max_length=300)
    category: str = Field(description=f"One of {', '.join(ORDER_CATEGORIES)}")
    status: str = Field(
        default="ordered",
        description=f"One of {', '.join(MANUAL_ORDER_STATUSES)}",
    )
    ordered_at: AwareDatetime | None = Field(
        default=None,
        description="Defaults to now. Never in the future.",
    )
    expected_tat_hours: Decimal | None = Field(
        default=None,
        description=(
            "Turnaround time in hours. The gate pre-fills its expected-by from "
            "ordered_at + this; leaving it null means the doctor must choose a "
            "deadline themselves rather than be handed a fabricated one."
        ),
    )
    external_order_id: str | None = Field(default=None, max_length=128)
    ordered_by_user_id: uuid.UUID | None = None

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in ORDER_CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(ORDER_CATEGORIES)}")
        return value

    @field_validator("status")
    @classmethod
    def _creatable_status(cls, value: str) -> str:
        if value not in MANUAL_ORDER_STATUSES:
            # Named separately from ORDER_STATUSES so the message says what a
            # caller may actually do, not what the column happens to allow.
            allowed = ", ".join(MANUAL_ORDER_STATUSES)
            terminal = sorted(set(ORDER_STATUSES) - set(MANUAL_ORDER_STATUSES))
            raise ValueError(
                f"status must be one of {allowed}. "
                f"{', '.join(terminal)} are reached by resulting an order, "
                "not by creating one."
            )
        return value

    @field_validator("expected_tat_hours")
    @classmethod
    def _sane_tat(cls, value: Decimal | None) -> Decimal | None:
        # Mirrors ck_orders_expected_tat_hours_positive, plus a ceiling the
        # database does not express. A 0 here would otherwise reach Postgres
        # and come back as an IntegrityError instead of a field error.
        if value is None:
            return value
        if value <= 0:
            raise ValueError("expected_tat_hours must be greater than 0")
        if value > MAX_TAT_HOURS:
            raise ValueError(f"expected_tat_hours must be at most {MAX_TAT_HOURS}")
        return value

    @field_validator("test_code", "test_name", "external_order_id")
    @classmethod
    def _no_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be blank")
        return trimmed


class OrderRow(BaseModel):
    """An order as the encounter detail screen shows it.

    ``is_outstanding`` and the contract fields are derived, not stored: the
    detail screen needs to show *why* an encounter is or is not dischargeable,
    and re-deriving that in the browser would put a second copy of the gate's
    rule somewhere it could drift.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    test_code: str
    test_name: str
    category: str
    status: str
    ordered_at: dt.datetime
    sample_collected_at: dt.datetime | None = None
    expected_tat_hours: Decimal | None = None
    external_order_id: str | None = None

    is_outstanding: bool = Field(
        description="Status is not final, cancelled or rejected."
    )
    contract_id: uuid.UUID | None = None
    responsible_doctor_id: uuid.UUID | None = None
    responsible_doctor_name: str | None = None
    expected_by: dt.datetime | None = None


class OrderCreated(BaseModel):
    """What the caller gets back, including the gate's new answer.

    ``encounter_can_discharge`` is returned so the UI does not have to guess
    whether the order it just added changed the gate. It is re-derived from the
    database inside the same transaction, never asserted by the client.
    """

    order: OrderRow
    encounter_id: uuid.UUID
    encounter_can_discharge: bool
    blocking_order_count: int

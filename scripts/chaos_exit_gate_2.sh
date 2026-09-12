#!/usr/bin/env bash
# Exit Gate 2 — the chaos test, run from the HOST.
#
#   Create 50 cases with deadlines 2 minutes out, `docker compose restart`
#   TWICE during the window, then verify EXACTLY 50 flags — no duplicates,
#   no misses.
#
# It lives here rather than in pytest because it has to restart the containers
# the suite runs inside. Everything it touches goes through the real product
# surface: real discharges over HTTP through Caddy, the real worker, the real
# pg_cron sweep. Nothing is stubbed and nothing is counted from the queue --
# the assertion reads lab_flags out of PostgreSQL, because that is the
# clinical effect, and a duplicate queue message is not a second event.
#
#   Usage:  bash scripts/chaos_exit_gate_2.sh [case_count] [deadline_seconds]
#
# Defaults are the build plan's: 50 cases, 120 seconds.
set -euo pipefail

CASES="${1:-50}"
DEADLINE_S="${2:-120}"
TAG="CHAOS$(date +%s)"
API="http://localhost/api"

psql() { docker compose exec -T postgres psql -U rg_app -d result_guardian "$@"; }
# -q suppresses the "INSERT 0 1" command tag, which otherwise comes back
# glued to the RETURNING value and is then used as a uuid.
q()    { psql -q -t -A -c "$1"; }

echo "══ Exit Gate 2 chaos test ══"
echo "   cases=$CASES  deadline=${DEADLINE_S}s  tag=$TAG"

# ── 1. build the fixtures in one statement ────────────────────────────
# generate_series rather than 50 round trips: the point of the test is what
# happens during the restart window, not how fast rows are inserted.
DOC=$(q "INSERT INTO users (id, employee_code, full_name, role, is_active)
         VALUES (gen_random_uuid(), '${TAG}-DOC', 'Chaos Doctor ${TAG}', 'doctor', true)
         RETURNING id;")
PAT=$(q "INSERT INTO patients (id, mrn, name)
         VALUES (gen_random_uuid(), '${TAG}-MRN', 'Chaos Patient ${TAG}')
         RETURNING id;")

psql -q -c "
INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, status)
SELECT gen_random_uuid(), '${PAT}', '${TAG}-ENC-' || g, 'ipd', now(), 'active'
  FROM generate_series(1, ${CASES}) g;

INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name,
                    category, ordered_at, status)
SELECT gen_random_uuid(), e.id, '${PAT}', 'URC', 'Urine Culture',
       'micro', now() - interval '6 hours', 'in_lab'
  FROM encounters e WHERE e.encounter_no LIKE '${TAG}-ENC-%';
"
echo "   ✓ ${CASES} encounters + orders created"

# ── 2. contract and discharge each, through the real API ──────────────
DEADLINE=$(q "SELECT to_char((now() + interval '${DEADLINE_S} seconds') AT TIME ZONE 'UTC',
                             'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"');")

mapfile -t ENCOUNTERS < <(q "SELECT id FROM encounters
                              WHERE encounter_no LIKE '${TAG}-ENC-%' ORDER BY encounter_no;")

for ENC in "${ENCOUNTERS[@]}"; do
  ORDER=$(q "SELECT id FROM orders WHERE encounter_id = '${ENC}' LIMIT 1;")
  curl -s -o /dev/null -X POST "${API}/encounters/${ENC}/discharge-contracts" \
    -H 'Content-Type: application/json' \
    -d "{\"contracts\":[{\"order_id\":\"${ORDER}\",\"responsible_doctor_id\":\"${DOC}\",\"expected_by\":\"${DEADLINE}\"}]}"
  curl -s -o /dev/null -X POST "${API}/encounters/${ENC}/discharge"
done
echo "   ✓ ${CASES} discharges completed, deadlines ${DEADLINE_S}s out"

OPENED=$(q "SELECT count(*) FROM pending_cases pc JOIN encounters e ON e.id = pc.encounter_id
             WHERE e.encounter_no LIKE '${TAG}-ENC-%';")
TIMERS=$(q "SELECT count(*) FROM sla_timers s JOIN pending_cases pc ON pc.id = s.case_id
             JOIN encounters e ON e.id = pc.encounter_id
            WHERE e.encounter_no LIKE '${TAG}-ENC-%';")
echo "   cases=${OPENED}  timers=${TIMERS}"
[ "$OPENED" = "$CASES" ] || { echo "FAIL: expected ${CASES} cases, got ${OPENED}"; exit 1; }
[ "$TIMERS" = "$CASES" ] || { echo "FAIL: expected ${CASES} timers, got ${TIMERS}"; exit 1; }

# ── 3. chaos: restart twice inside the window ─────────────────────────
echo "   restarting (1/2) at T+$((DEADLINE_S / 4))s ..."
sleep $((DEADLINE_S / 4))
docker compose restart api worker >/dev/null 2>&1

echo "   restarting (2/2) at T+$((DEADLINE_S / 2))s ..."
sleep $((DEADLINE_S / 4))
docker compose restart api worker postgres >/dev/null 2>&1

# ── 4. wait past the deadline, then give the worker time ──────────────
echo "   waiting for deadlines to pass and the worker to drain ..."
sleep $((DEADLINE_S / 2 + 45))

# The sweep normally runs every 5 minutes; nudge it so the test does not have
# to wait that long. This is the same function pg_cron calls -- it is the
# recovery path being exercised, not a shortcut around it.
psql -q -c "SELECT rg_sweep_overdue_sla_timers();" >/dev/null
sleep 20

# ── 5. the assertion: EXACTLY ${CASES} flags ──────────────────────────
FLAGS=$(q "SELECT count(*) FROM lab_flags lf JOIN pending_cases pc ON pc.id = lf.case_id
            JOIN encounters e ON e.id = pc.encounter_id
           WHERE e.encounter_no LIKE '${TAG}-ENC-%';")
FIRED=$(q "SELECT count(*) FROM sla_timers s JOIN pending_cases pc ON pc.id = s.case_id
            JOIN encounters e ON e.id = pc.encounter_id
           WHERE e.encounter_no LIKE '${TAG}-ENC-%' AND s.timer_type = 'result_due'
             AND s.status = 'fired';")
EVENTS=$(q "SELECT count(*) FROM case_events ce JOIN pending_cases pc ON pc.id = ce.case_id
             JOIN encounters e ON e.id = pc.encounter_id
            WHERE e.encounter_no LIKE '${TAG}-ENC-%'
              AND ce.event_type = 'result_due_fired';")
DUPES=$(q "SELECT coalesce(max(n), 0) FROM (
             SELECT count(*) AS n FROM lab_flags lf
               JOIN pending_cases pc ON pc.id = lf.case_id
               JOIN encounters e ON e.id = pc.encounter_id
              WHERE e.encounter_no LIKE '${TAG}-ENC-%'
              GROUP BY lf.case_id) x;")

echo
echo "── RESULT ─────────────────────────────────────────"
echo "   lab flags raised        : ${FLAGS}   (expected ${CASES})"
echo "   result_due timers fired : ${FIRED}   (expected ${CASES})"
echo "   result_due_fired events : ${EVENTS}   (expected ${CASES})"
echo "   max flags on any 1 case : ${DUPES}   (expected 1)"
echo "───────────────────────────────────────────────────"

STATUS=0
[ "$FLAGS"  = "$CASES" ] || { echo "FAIL: flags ${FLAGS} != ${CASES}";   STATUS=1; }
[ "$FIRED"  = "$CASES" ] || { echo "FAIL: fired ${FIRED} != ${CASES}";   STATUS=1; }
[ "$EVENTS" = "$CASES" ] || { echo "FAIL: events ${EVENTS} != ${CASES}"; STATUS=1; }
[ "$DUPES"  = "1" ]      || { echo "FAIL: a case got ${DUPES} flags";    STATUS=1; }

if [ "$STATUS" = "0" ]; then
  echo "✅ EXIT GATE 2 CHAOS TEST PASSED — exactly ${CASES} flags, no duplicates, no misses"
fi

# ── 6. clean up, whatever happened ────────────────────────────────────
# session_replication_role, never DISABLE TRIGGER: it is scoped to this
# connection, so a crash here cannot leave the append-only guard off.
psql -q -c "
SET session_replication_role = replica;
DELETE FROM case_events WHERE case_id IN (
  SELECT pc.id FROM pending_cases pc JOIN encounters e ON e.id = pc.encounter_id
   WHERE e.encounter_no LIKE '${TAG}-ENC-%');
DELETE FROM lab_flags WHERE case_id IN (
  SELECT pc.id FROM pending_cases pc JOIN encounters e ON e.id = pc.encounter_id
   WHERE e.encounter_no LIKE '${TAG}-ENC-%');
DELETE FROM sla_timers WHERE case_id IN (
  SELECT pc.id FROM pending_cases pc JOIN encounters e ON e.id = pc.encounter_id
   WHERE e.encounter_no LIKE '${TAG}-ENC-%');
DELETE FROM pending_cases WHERE encounter_id IN (
  SELECT id FROM encounters WHERE encounter_no LIKE '${TAG}-ENC-%');
DELETE FROM discharge_contracts WHERE encounter_id IN (
  SELECT id FROM encounters WHERE encounter_no LIKE '${TAG}-ENC-%');
DELETE FROM orders WHERE encounter_id IN (
  SELECT id FROM encounters WHERE encounter_no LIKE '${TAG}-ENC-%');
DELETE FROM encounters WHERE encounter_no LIKE '${TAG}-ENC-%';
DELETE FROM patients WHERE mrn = '${TAG}-MRN';
DELETE FROM users WHERE employee_code = '${TAG}-DOC';
DELETE FROM pgmq.q_sla_timers WHERE message->>'case_id' NOT IN
  (SELECT id::text FROM pending_cases);
DELETE FROM pgmq.q_notifications WHERE message->>'case_id' NOT IN
  (SELECT id::text FROM pending_cases);
SET session_replication_role = origin;
" >/dev/null
echo "   (test data cleaned up)"

exit $STATUS

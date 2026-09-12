/**
 * Seed one gated encounter directly into NODE A's database.
 *
 * These tests run against the real stack -- Caddy serving `web/dist`, the real
 * FastAPI app, the real Postgres with its real CHECK constraints -- because
 * that is the only configuration that proves the gate works. A mocked E2E
 * would re-test what the vitest suite already covers.
 *
 * Every run creates a *new* encounter with a unique MRN rather than reusing or
 * truncating anything. Clinical rows are never hard-deleted in this project,
 * and a test that empties tables is one bad `--project` flag away from doing it
 * somewhere it matters.
 */

import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

// The repo root, where docker-compose.yml lives. Derived from this module so
// the tests do not depend on the directory Playwright happened to start in.
const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));

/** UUIDv7, matching the project's PK convention (time-sortable). */
export function uuidv7() {
  const bytes = randomBytes(16);
  const ms = BigInt(Date.now());
  for (let i = 0; i < 6; i += 1) {
    bytes[i] = Number((ms >> BigInt(8 * (5 - i))) & 0xffn);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x70; // version 7
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(
    16,
    20,
  )}-${hex.slice(20)}`;
}

function psql(sql) {
  return execFileSync(
    "docker",
    [
      "compose",
      "exec",
      "-T",
      "postgres",
      "psql",
      "-U",
      "rg_app",
      "-d",
      "result_guardian",
      "-v",
      "ON_ERROR_STOP=1",
      "-t",
      "-A",
      "-c",
      sql,
    ],
    { cwd: REPO_ROOT, encoding: "utf8" },
  ).trim();
}

/**
 * @returns {{encounterId: string, doctorId: string, unitHeadId: string,
 *            attendingName: string, otherName: string, patientName: string,
 *            mrn: string, orderA: string, orderB: string}}
 */
export function seedGatedEncounter() {
  const tag = randomBytes(4).toString("hex");
  const ids = {
    department: uuidv7(),
    unitHead: uuidv7(),
    doctor: uuidv7(),
    other: uuidv7(),
    patient: uuidv7(),
    encounter: uuidv7(),
    orderA: uuidv7(),
    orderB: uuidv7(),
    orderC: uuidv7(),
  };

  const attendingName = `Asha Menon ${tag}`;
  const otherName = `Ravi Kulkarni ${tag}`;
  const unitHeadName = `Meera Iyer ${tag}`;
  const patientName = `Sunita Rao ${tag}`;
  const mrn = `E2E-${tag}`;

  // One statement, one transaction: a half-seeded encounter would fail the
  // test for the wrong reason.
  psql(`
    INSERT INTO users (id, employee_code, full_name, role, is_active) VALUES
      ('${ids.unitHead}', 'E2EH-${tag}', '${unitHeadName}', 'unit_head', true),
      ('${ids.doctor}',   'E2ED-${tag}', '${attendingName}', 'doctor', true),
      ('${ids.other}',    'E2EO-${tag}', '${otherName}', 'doctor', true);

    INSERT INTO departments (id, code, name, unit_head_user_id)
    VALUES ('${ids.department}', 'E2E-${tag}', 'E2E Medicine ${tag}', '${ids.unitHead}');

    UPDATE users SET department_id = '${ids.department}'
     WHERE id IN ('${ids.unitHead}', '${ids.doctor}', '${ids.other}');

    INSERT INTO patients (id, mrn, name)
    VALUES ('${ids.patient}', '${mrn}', '${patientName}');

    INSERT INTO encounters
      (id, patient_id, encounter_no, type, status, admitted_at,
       attending_doctor_id, department_id, ward, bed)
    VALUES
      ('${ids.encounter}', '${ids.patient}', 'E2E-ENC-${tag}', 'ipd', 'active',
       now() - interval '3 days', '${ids.doctor}', '${ids.department}',
       'Ward 3', 'B12');

    INSERT INTO orders
      (id, encounter_id, patient_id, test_code, test_name, category, status,
       ordered_at, expected_tat_hours, ordered_by_user_id)
    VALUES
      ('${ids.orderA}', '${ids.encounter}', '${ids.patient}', 'URC',
       'Urine Culture', 'micro', 'in_lab',
       now() - interval '6 hours', 48, '${ids.doctor}'),
      ('${ids.orderB}', '${ids.encounter}', '${ids.patient}', 'HBA1C',
       'HbA1c', 'lab', 'ordered',
       now() - interval '30 hours', 24, '${ids.doctor}'),
      -- Exit Gate 1 is specific: 3 tests, 1 resulted, 2 pending. This is the
      -- resulted one. It must NOT block the discharge and must NOT open a
      -- tracking case: final is in ORDER_STATUSES_NOT_BLOCKING, and a gate
      -- that blocked on it would be blocking on nothing.
      ('${ids.orderC}', '${ids.encounter}', '${ids.patient}', 'CXR',
       'Chest X-Ray', 'radiology', 'final',
       now() - interval '20 hours', 2, '${ids.doctor}');
  `);

  return {
    encounterId: ids.encounter,
    doctorId: ids.doctor,
    unitHeadId: ids.unitHead,
    attendingName,
    otherName,
    unitHeadName,
    patientName,
    mrn,
    orderA: ids.orderA,
    orderB: ids.orderB,
    orderC: ids.orderC,
  };
}

/** Read a row back, to assert against the database rather than the screen. */
export function query(sql) {
  return psql(sql);
}

/**
 * Remove every row this seeder has ever created.
 *
 * Run before and after the suite. Without it each run leaves another
 * "Ravi Kulkarni <tag>" behind, and by the eighteenth the doctor search that
 * the keyboard test types into is ambiguous -- which is exactly how the
 * Phase 1.8 run first failed. A suite that is not repeatable is not a suite.
 *
 * `session_replication_role = replica` bypasses the case_events append-only
 * trigger for this connection only. That is the same mechanism the Python
 * integration tests already use, and deliberately NOT
 * `ALTER TABLE ... DISABLE TRIGGER`, which would persist if this crashed and
 * leave a production guarantee switched off for everyone.
 */
export function cleanupE2EData() {
  psql(`
    SET session_replication_role = replica;

    DELETE FROM discharge_overrides WHERE encounter_id IN
      (SELECT id FROM encounters WHERE encounter_no LIKE 'E2E-ENC-%');
    DELETE FROM case_events WHERE case_id IN
      (SELECT id FROM pending_cases WHERE encounter_id IN
        (SELECT id FROM encounters WHERE encounter_no LIKE 'E2E-ENC-%'));
    DELETE FROM pending_cases WHERE encounter_id IN
      (SELECT id FROM encounters WHERE encounter_no LIKE 'E2E-ENC-%');
    DELETE FROM discharge_contracts WHERE encounter_id IN
      (SELECT id FROM encounters WHERE encounter_no LIKE 'E2E-ENC-%');
    DELETE FROM discharge_medications WHERE encounter_id IN
      (SELECT id FROM encounters WHERE encounter_no LIKE 'E2E-ENC-%');
    DELETE FROM orders WHERE encounter_id IN
      (SELECT id FROM encounters WHERE encounter_no LIKE 'E2E-ENC-%');
    DELETE FROM encounters WHERE encounter_no LIKE 'E2E-ENC-%';
    DELETE FROM patients WHERE mrn LIKE 'E2E-%';
    UPDATE users SET department_id = NULL WHERE employee_code LIKE 'E2E%';
    DELETE FROM departments WHERE code LIKE 'E2E-%';
    DELETE FROM users WHERE employee_code LIKE 'E2E%';

    -- The SLA timers this suite queued. Their cases are gone; leaving the
    -- messages would hand the worker wake-ups for encounters that no longer
    -- exist.
    DELETE FROM pgmq.q_sla_timers
     WHERE message->>'encounter_id' NOT IN (SELECT id::text FROM encounters);

    SET session_replication_role = origin;
  `);
}

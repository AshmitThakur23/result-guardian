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
       now() - interval '30 hours', 24, '${ids.doctor}');
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
  };
}

/** Read a row back, to assert against the database rather than the screen. */
export function query(sql) {
  return psql(sql);
}

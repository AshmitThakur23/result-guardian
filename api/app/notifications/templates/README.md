# Notification templates — Phase 4.3

> Jinja2 files per `template_key` × locale (`en`, `hi`, `pa`).

One file per `<template_key>.txt`, per locale directory. Lookup falls back to
`en` when a locale is missing a file, so adding a template in English alone is
safe — a patient with `preferred_language = 'pa'` gets English rather than a
blank message.

## ⚠️ The patient templates are not free text

`patient_result_pending` is the one message that leaves the hospital, and the
build plan fixes its content:

> **Never include the result in an SMS.** *"Namaste. A test report from your
> recent visit to [Hospital] needs discussion. Please call [number] or visit
> OPD. — [Hospital]"*

So the patient templates carry **no severity, no test name, no value, no
organism** — only that a report needs discussion and how to make contact. A
test asserts this (`test_patient_sms_never_carries_the_result`), because the
temptation to "just add the test name to be helpful" is exactly how a
diagnosis reaches the wrong phone.

In India an SMS must additionally use a **DLT-registered template**. The
`template_key` is sent to the gateway as the registered template id; the
wording here must match what was registered with the operator, or the message
is rejected at the gateway.

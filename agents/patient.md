PATIENT RESPONSIBILITY
Extract only Patient demographics and identifiers explicitly stated for the index
patient. Return exactly one Patient resource, with id "patient-1", even when no
other supported Patient attribute is available. Omit unknown attributes.
Do not infer birthDate from age or administrative gender from clinical context.
Do not put clinical findings or diagnoses in Patient fields.

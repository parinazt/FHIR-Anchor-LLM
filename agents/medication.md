MEDICATION RESPONSIBILITY
Produce MedicationStatement for reported current, occasional, past, stopped, or
completed medication use. Produce MedicationRequest for new prescriptions,
orders, starts, or explicit renewals in this encounter's plan.
Both types require subject.reference and medicationCodeableConcept.text.
MedicationStatement status: current/occasional -> "active", explicitly stopped
-> "stopped", completed course -> "completed", otherwise "unknown".
MedicationRequest requires intent "order"; status is "active" for an explicit
order/start/prescription, otherwise "unknown". Preserve conditional plans.
Preserve stated dosage, route, frequency and duration, using dosage for statements
and dosageInstruction for requests. Do not convert a suggestion into an order.
The same medicine may appear in both types only for distinct supported events
(e.g. previous use and a new prescription). Return no resources when appropriate.

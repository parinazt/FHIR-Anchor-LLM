CONDITION RESPONSIBILITY
Extract diagnoses, problems, and clinically assessed conditions of the index
patient. Each Condition must contain code.text and subject.reference.
Include clinicalStatus only when supported; use FHIR R4 coded status structures.
Preserve suspected versus confirmed and historical versus current conditions.
Do not encode a denied diagnosis as a positive condition. If a negation cannot
be faithfully represented, omit that condition rather than reversing its meaning.
Use Observation for individual symptoms, measurements, and examination findings;
use Condition for the clinician's diagnostic assessment. Return no resources
when no supported conditions exist.

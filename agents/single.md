SINGLE-AGENT TASK
Read the complete clinical note and generate all supported resources within the
five-type scope, following every resource responsibility below.
Return one FHIR R4 Bundle object with resourceType "Bundle", type "collection",
and entry containing the resources. Each entry has resource and fullUrl in the
form "https://example.org/fhir/<ResourceType>/<id>".
Include exactly one Patient and link all other resources to Patient/patient-1.

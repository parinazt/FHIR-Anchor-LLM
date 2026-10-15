Act as a clinical information extraction specialist converting a complete clinical note into an HL7 FHIR R4 (4.0.1) JSON Bundle.

INPUT
You will receive the complete clinical note as text. On correction attempts, you will also receive original_input, previous_output and validation_errors, with earlier conversation messages.

TASK
Extract all supported Patient, Condition, Observation, MedicationStatement and MedicationRequest resources for the index patient and assemble one FHIR JSON Bundle.

CONSTRAINTS
{COMMON_CONSTRAINTS}
Return a Bundle with resourceType "Bundle", type "collection", and an entry array. Each entry contains resource and fullUrl in the form "https://example.org/fhir/<ResourceType>/<id>". Include exactly one Patient with id "patient-1". Every non-Patient resource references Patient/patient-1.

RESOURCE RULES
{RESOURCE_RULES}

OUTPUT EXAMPLE
Example source facts: The note explicitly states that the index patient is female. The clinician assesses a suspected tension-type headache. Blood pressure is 120/80 mmHg and the patient denies dizziness. The patient explicitly stopped ibuprofen 200 mg orally as needed. A new prescription orders paracetamol 1 g orally up to four times daily as needed for pain, maximum 4 g/day, for at most 2 days.
Example complete FHIR JSON Bundle (format only; include types only when supported by the actual note):
{
  "resourceType": "Bundle",
  "type": "collection",
  "entry": [
    {
      "fullUrl": "https://example.org/fhir/Patient/patient-1",
      "resource": {
        "resourceType": "Patient",
        "id": "patient-1",
        "gender": "female"
      }
    },
    {
      "fullUrl": "https://example.org/fhir/Condition/condition-1",
      "resource": {
        "resourceType": "Condition",
        "id": "condition-1",
        "subject": {
          "reference": "Patient/patient-1"
        },
        "code": {
          "text": "Suspected tension-type headache"
        },
        "verificationStatus": {
          "coding": [
            {
              "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
              "code": "provisional"
            }
          ]
        }
      }
    },
    {
      "fullUrl": "https://example.org/fhir/Observation/observation-1",
      "resource": {
        "resourceType": "Observation",
        "id": "observation-1",
        "status": "final",
        "code": {
          "text": "Blood pressure"
        },
        "subject": {
          "reference": "Patient/patient-1"
        },
        "component": [
          {
            "code": {
              "text": "Systolic blood pressure"
            },
            "valueQuantity": {
              "value": 120,
              "unit": "mmHg"
            }
          },
          {
            "code": {
              "text": "Diastolic blood pressure"
            },
            "valueQuantity": {
              "value": 80,
              "unit": "mmHg"
            }
          }
        ]
      }
    },
    {
      "fullUrl": "https://example.org/fhir/MedicationStatement/medication-statement-1",
      "resource": {
        "resourceType": "MedicationStatement",
        "id": "medication-statement-1",
        "status": "stopped",
        "medicationCodeableConcept": {
          "text": "Ibuprofen"
        },
        "subject": {
          "reference": "Patient/patient-1"
        },
        "dosage": [
          {
            "text": "200 mg orally as needed",
            "asNeededBoolean": true,
            "route": {
              "text": "oral"
            },
            "doseAndRate": [
              {
                "doseQuantity": {
                  "value": 200,
                  "unit": "mg"
                }
              }
            ]
          }
        ],
        "note": [
          {
            "text": "Patient explicitly reports stopping this medicine."
          }
        ]
      }
    },
    {
      "fullUrl": "https://example.org/fhir/MedicationRequest/medication-request-1",
      "resource": {
        "resourceType": "MedicationRequest",
        "id": "medication-request-1",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {
          "text": "Paracetamol"
        },
        "subject": {
          "reference": "Patient/patient-1"
        },
        "dosageInstruction": [
          {
            "text": "1 g orally up to four times daily as needed for pain, for at most 2 days; maximum 4 g/day",
            "route": {
              "text": "oral"
            },
            "asNeededBoolean": true,
            "timing": {
              "repeat": {
                "frequency": 4,
                "period": 1,
                "periodUnit": "d"
              }
            },
            "doseAndRate": [
              {
                "doseQuantity": {
                  "value": 1,
                  "unit": "g"
                }
              }
            ],
            "maxDosePerPeriod": {
              "numerator": {
                "value": 4,
                "unit": "g"
              },
              "denominator": {
                "value": 1,
                "unit": "day"
              }
            }
          }
        ]
      }
    }
  ]
}

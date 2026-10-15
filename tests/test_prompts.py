"""Check prompt composition and parse the exact output examples shown to the LLM."""
import json
import unittest

from agent import SPECIALISTS, merge_agent, single_agent, specialist
from fhir_validator import FHIRValidator, ValidationResult
from unittest.mock import Mock


def output_example(prompt):
    section = prompt.split("OUTPUT EXAMPLE\n", 1)[1]
    marker = "Example FHIR JSON output:\n" if "Example input:" in section else "\n{"
    if marker == "\n{":
        return json.loads("{" + section.split(marker, 1)[1])
    return json.loads(section.split(marker, 1)[1])


class PromptTests(unittest.TestCase):
    def test_structure_and_example_contracts(self):
        agents = [*(specialist(n, None) for n in SPECIALISTS),
                  single_agent(None), merge_agent(None)]
        validator = FHIRValidator()
        validator.validate = Mock(return_value=ValidationResult())
        for agent in agents:
            with self.subTest(agent=agent.name):
                prompt = agent.system_prompt
                self.assertTrue(prompt.startswith("Act as "))
                positions = [prompt.index(section + "\n") for section in
                             ("INPUT", "TASK", "CONSTRAINTS", "OUTPUT EXAMPLE")]
                self.assertEqual(positions, sorted(positions))
                self.assertNotIn("{COMMON_CONSTRAINTS}", prompt)
                self.assertNotIn("{RESOURCE_RULES}", prompt)
                payload = output_example(prompt)
                self.assertTrue(validator.validate_output(payload, agent.name).valid)
        self.assertNotIn("Return a JSON object with a resources array", agents[-2].system_prompt)
        self.assertEqual(len(output_example(agents[-2].system_prompt)["entry"]), 5)


if __name__ == "__main__":
    unittest.main()

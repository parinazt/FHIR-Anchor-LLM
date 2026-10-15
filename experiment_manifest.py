"""Credential-free experiment identity for reproducibility and safe resumption."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import platform

from agent import SPECIALISTS, merge_agent, single_agent, specialist


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_manifest(settings, llm, notes):
    agents = ([single_agent(llm)] if settings.mode == 'single-agent' else
              [*(specialist(name, llm) for name in SPECIALISTS), merge_agent(llm)])
    sources = [{'file': source.name, 'sha256': digest(note)} for source, note in notes]
    identity = {
        'mode': settings.mode, 'num_of_execution': settings.num_of_execution,
        'max_attempts': settings.max_attempts, 'development': settings.development,
        'llm': llm.configuration(), 'fhir_version': '4.0.1',
        'terminology_validation': False,
        'implementation': {name: digest((Path(__file__).parent / name).read_text(encoding='utf-8'))
                           for name in ('agent.py', 'llm.py', 'main.py', 'fhir_validator.py',
                                        'workflow_multiagent.py', 'workflow_singleagent.py',
                                        'experiment_manifest.py')},
        'prompts': {a.name: {'text': a.system_prompt, 'sha256': digest(a.system_prompt)}
                    for a in agents},
        'dataset': {'count': len(sources), 'files': sources,
                    'sha256': digest(json.dumps(sources, sort_keys=True))},
    }
    return {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
            'python_version': platform.python_version(), 'identity': identity}


def check_resume_manifest(path, current, *, allow_extension=False):
    if not path.is_file():
        raise ValueError('Cannot resume: experiment.json is missing. Start a new experiment without --resume.')
    previous = json.loads(path.read_text(encoding='utf-8'))
    previous_identity = dict(previous.get('identity', {}))
    current_identity = dict(current['identity'])
    if allow_extension:
        previous_identity.pop('num_of_execution', None)
        current_identity.pop('num_of_execution', None)
        # Scheduling/reporting fixes may change between independent runs. Keep
        # extraction, transport, validation and prompts identical, and record the
        # exact implementation for each new invocation in continuations.
        for identity in (previous_identity, current_identity):
            implementation = dict(identity.get('implementation', {}))
            for name in ('main.py', 'experiment_manifest.py'):
                implementation.pop(name, None)
            identity['implementation'] = implementation
    if previous.get('schema_version') != current['schema_version'] or previous_identity != current_identity:
        raise ValueError('Cannot resume: configuration, prompts or dataset differ from experiment.json.')
    return previous

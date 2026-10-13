"""Per-note provider-reported token accounting, including failed attempts."""
from contextvars import ContextVar

TOKEN_FIELDS = ('input-tokens', 'output-tokens', 'total-tokens')
CURRENT_USAGE = ContextVar('current_token_usage', default=None)


class TokenUsage:
    def __init__(self):
        self.calls = []

    def start(self, agent):
        call = {'agent': agent, 'usage': None}
        self.calls.append(call)
        return call

    def columns(self, agents):
        result = {}
        for agent in (None, *agents):
            calls = [c for c in self.calls if agent is None or c['agent'] == agent]
            prefix = f'{agent}-' if agent else ''
            for field, api_key in zip(TOKEN_FIELDS, ('prompt_tokens', 'completion_tokens', 'total_tokens')):
                values = [(c['usage'] or {}).get(api_key) for c in calls]
                complete = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)
                result[prefix + field] = sum(values) if complete else ''
        return result


def usage_fields(agents):
    return [*TOKEN_FIELDS, *(f'{agent}-{field}' for agent in agents for field in TOKEN_FIELDS)]

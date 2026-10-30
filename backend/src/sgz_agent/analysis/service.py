"""Stateless calculation dispatcher; never opens a client or a database."""
from .combat_assessment import assess_combat


class AnalysisService:
    def dispatch(self, method, params):
        if method != 'assess_combat':
            raise ValueError('unknown_method')
        return assess_combat(**params)

    def request_stop(self):
        pass

    def close(self):
        pass

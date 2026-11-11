"""Fifth-route execution controls, separate from semantic reading decisions."""
from dataclasses import InitVar, dataclass

VERSION = 'agent-v0.6'


@dataclass(frozen=True)
class AgentSettings:
    max_calls: int | None = None
    timeout_seconds: int | None = None
    page_edge: int = 2200
    reasoning_effort: str = 'max'
    record_model_inputs: InitVar[bool] = False
    task_instruction: str = ''

    def __post_init__(self, record_model_inputs):
        for key, lo, hi in [('max_calls', 1, None), ('timeout_seconds', 1, None), ('page_edge', 1000, 4500)]:
            value = getattr(self, key)
            if value is None and key != 'page_edge': continue
            if type(value) is not int or value < lo or (hi is not None and value > hi):
                raise ValueError('Invalid Agent setting: ' + key)
        if not isinstance(self.task_instruction,str) or len(self.task_instruction)>20000:
            raise ValueError('Agent task instruction must be text up to 20000 characters')
        if self.reasoning_effort not in {'low', 'high', 'max'}:
            raise ValueError('Agent reasoning_effort must be low, high or max')
        # Older callers and saved runs can still supply this retired setting.
        if type(record_model_inputs) is not bool:
            raise ValueError('Invalid Agent setting: record_model_inputs')

"""TC-06 / TC-07: the framework security gates must not be bypassable.

Every node in this template extends FunctionNode, which runs a default input
scan before execute() and a default output scan after it. A domain node may add
checks through the _extra_security_gate_input() / _extra_security_gate_output()
hooks, but replacing the gates themselves is refused at class definition time.
These tests pin that enforcement, because a subclass that quietly replaced a gate
would drop the default scans for every request it handled.
"""

import pytest

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel


class TestFunctionNodeFinalSecurityGates:
    """TC-06 / TC-07: FunctionNode security gates are non-bypassable."""

    def test_tc06_security_gate_input_cannot_be_overridden(self):
        """Overriding the default input gate raises at class definition."""
        with pytest.raises(TypeError, match="_security_gate_input"):

            class _InvalidInputGateOverride(FunctionNode):
                required_trust_level = TrustLevel.ANONYMOUS

                def _security_gate_input(self, state):
                    return state

                def execute(self, state):
                    return {"status": AgentStatus.SUCCESS.value}

    def test_tc07_security_gate_output_cannot_be_overridden(self):
        """Overriding the default output gate raises at class definition."""
        with pytest.raises(TypeError, match="_security_gate_output"):

            class _InvalidOutputGateOverride(FunctionNode):
                required_trust_level = TrustLevel.ANONYMOUS

                def _security_gate_output(self, result):
                    return result

                def execute(self, state):
                    return {"status": AgentStatus.SUCCESS.value}

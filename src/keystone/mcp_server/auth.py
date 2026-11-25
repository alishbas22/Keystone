"""Who is calling the MCP server, and which business functions they may see.

KAN-27 placeholder: every caller sees all business functions. KAN-29 replaces this with agent
keys tied to business functions.
"""

from dataclasses import dataclass

from keystone.ontology import Ontology


@dataclass(frozen=True)
class AgentIdentity:
    name: str
    scope: list[str]  # business functions this agent may see


def current_agent(ontology: Ontology) -> AgentIdentity:
    return AgentIdentity(name="local", scope=list(ontology.business_functions))

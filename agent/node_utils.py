import functools
from langchain_core.messages import AIMessage


def wrap_agent_node(agent_name: str):
    """Decorator der last_agent_result und last_agent_name automatisch setzt.

    Überschreibt keine explizit gesetzten Werte – Agents mit Custom-Logik
    können last_agent_result weiterhin manuell setzen.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(state):
            result = await func(state)
            if not isinstance(result, dict):
                return result

            if "last_agent_name" not in result:
                result = {**result, "last_agent_name": agent_name}

            if "last_agent_result" not in result:
                messages = result.get("messages", [])
                ai_messages = [m for m in messages if isinstance(m, AIMessage)]
                content = ai_messages[-1].content if ai_messages else None
                if isinstance(content, list):
                    content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
                result = {**result, "last_agent_result": content}

            return result

        return wrapper

    return decorator

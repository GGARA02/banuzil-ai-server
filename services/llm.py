from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from config.settings import MODEL_NAME, TEMPERATURE, MAX_OUTPUT_TOKENS


def _make_llm(model: str = None, max_tokens: int = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model or MODEL_NAME,
        temperature=TEMPERATURE,
        max_tokens=max_tokens or MAX_OUTPUT_TOKENS,
    )


async def call_llm(
    system_prompt: str,
    user_message: str,
    model: str = None,
    max_tokens: int = None,
) -> str:
    llm = _make_llm(model, max_tokens)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
    response = await llm.ainvoke(messages)
    return response.content


async def call_llm_with_history(
    system_prompt: str,
    history: list[dict],
    user_message: str,
    model: str = None,
    max_tokens: int = None,
) -> str:
    llm = _make_llm(model, max_tokens)
    messages = [SystemMessage(content=system_prompt)]
    for h in history:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        else:
            messages.append(AIMessage(content=h["content"]))
    messages.append(HumanMessage(content=user_message))
    response = await llm.ainvoke(messages)
    return response.content

import datetime
import uuid
from dataclasses import fields, replace
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    Message,
    ResultMessage,
    SystemMessage,
    TextBlock,
    UserMessage,
)


def generate_agent_id(agent_name:str):
    return agent_name+"_"+str(uuid.uuid4())+"_"+str(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))

def display_message(msg):
    """Standardized message display function.

    - UserMessage: "User: <content>"
    - AssistantMessage: "Claude: <content>"
    - SystemMessage: ignored
    - ResultMessage: "Result ended" + cost if available
    """
    GREEN = "\033[32m"
    RESET = "\033[0m"

    if isinstance(msg, UserMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"👨‍💻: {block.text}",flush=True)

    elif isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"{GREEN}●{RESET} {block.text}",flush=True)
    elif isinstance(msg, SystemMessage):
        # Ignore system messages
        pass
    elif isinstance(msg, ResultMessage):
        message = f"""
        ==================================
        {GREEN}●{RESET}
        result: {msg.result}
        duration: {msg.duration_ms}
        duration_api: {msg.duration_api_ms}
        num_turns: {msg.num_turns}
        session_id: {msg.session_id}
        cost: {msg.total_cost_usd}
        usage: {msg.usage}
        """
        print(message,flush=True)
        print("==================================")
        print("Result ended",flush=True)
    else:
        print(msg,flush=True)




def get_session_id(message:Message)->str:
    if hasattr(message, 'subtype') and message.subtype == 'init':
        session_id = message.data.get('session_id')
        return session_id
    return None


def _dedup_concat(a: list[Any], b: list[Any]) -> list[Any]:
    seen = set()
    out = []
    for x in a + b:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def merge_options(base: ClaudeAgentOptions, patch: ClaudeAgentOptions) -> ClaudeAgentOptions:
    """
    baseとpatchをマージして新しいClaudeAgentOptionsを返す。
    優先順位: patch > base
    - list: 連結して重複除去（順序保持）
    - dict: 浅いマージ（patch優先）
    - その他: patchで上書き
    - patchがNone: 無視（上書きしない）
    deepcopyはしないので、ファイルライクやコールバックも安全。
    """
    # shallow copy of base (keeps file handles/callbacks intact)
    result = replace(base)

    for f in fields(ClaudeAgentOptions):
        name = f.name
        pv = getattr(patch, name)
        if pv is None:
            continue

        bv = getattr(result, name)

        # list同士は結合+重複除去
        if isinstance(bv, list) and isinstance(pv, list):
            setattr(result, name, _dedup_concat(bv, pv))
            continue

        # dict同士は浅いマージ（patch優先）
        if isinstance(bv, dict) and isinstance(pv, dict):
            merged = {**bv, **pv}
            setattr(result, name, merged)
            continue

        # 片方がdictでない等は素直に上書き
        setattr(result, name, pv)

    return result

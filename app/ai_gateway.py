"""Gazellio G.AIOS 智能体运行时适配器。

浏览器只调用本系统的 /api/ai/chat；本模块负责服务端请求智能体、解析
application/x-ndjson 响应并维持会话标识。管理员账号和后台管理 Token
不会进入业务系统或前端代码。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .db import connect


DEFAULT_AI_BASE_URL = "https://adk.gazellio.com"
DEFAULT_AI_AGENT_ID = "default"


class AIServiceError(RuntimeError):
    """可安全展示给业务用户的智能体调用错误。"""


@dataclass(frozen=True)
class AIRuntimeConfig:
    enabled: bool
    mode: str
    base_url: str
    agent_id: str
    connect_timeout: float
    read_timeout: float


def get_ai_runtime_config() -> AIRuntimeConfig:
    """读取数据库集成配置，并允许部署环境变量覆盖非敏感运行参数。"""
    row: Optional[dict[str, Any]] = None
    try:
        with connect() as conn:
            value = conn.execute(
                "SELECT * FROM integration_configs WHERE code='ai'"
            ).fetchone()
            row = dict(value) if value else None
    except Exception:
        # 数据库尚未初始化时仍可从环境变量构建配置，便于启动与测试。
        row = None

    base_url = (
        os.getenv("TRM_AI_BASE_URL")
        or (row or {}).get("base_url")
        or DEFAULT_AI_BASE_URL
    ).strip().rstrip("/")
    agent_id = (
        os.getenv("TRM_AI_AGENT_ID")
        or (row or {}).get("agent_id")
        or DEFAULT_AI_AGENT_ID
    ).strip()
    mode = str((row or {}).get("mode") or os.getenv("TRM_AI_MODE") or "live").lower()
    enabled = bool((row or {}).get("enabled", True))

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AIServiceError("AI服务地址无效，请在集成配置中填写完整的 HTTP(S) 地址")
    if not agent_id:
        raise AIServiceError("未配置智能体标识 TRM_AI_AGENT_ID")

    return AIRuntimeConfig(
        enabled=enabled,
        mode=mode,
        base_url=base_url,
        agent_id=agent_id,
        connect_timeout=float(os.getenv("TRM_AI_CONNECT_TIMEOUT", "8")),
        read_timeout=float(os.getenv("TRM_AI_READ_TIMEOUT", "120")),
    )


def public_ai_config() -> dict[str, Any]:
    """返回可供前端展示的非敏感配置。"""
    cfg = get_ai_runtime_config()
    return {
        "provider": "Gazellio G.AIOS",
        "enabled": cfg.enabled,
        "mode": cfg.mode,
        "base_url": cfg.base_url,
        "agent_id": cfg.agent_id,
    }


def _event_text(event: dict[str, Any]) -> str:
    """兼容 G.AIOS/ADK 常见的内容事件结构。"""
    content = event.get("content")
    if isinstance(content, dict):
        parts = content.get("parts") or []
        # G.AIOS会把模型思考过程标记为 thought=true；该内容不得展示给最终用户。
        texts = [
            str(part.get("text"))
            for part in parts
            if isinstance(part, dict) and part.get("text") and part.get("thought") is not True
        ]
        if texts:
            return "".join(texts)

    for key in ("text", "delta", "output_text"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


async def run_agent_message(
    *,
    question: str,
    user_id: str,
    session_id: str = "",
    context: str = "",
    source: str = "assistant",
) -> dict[str, Any]:
    """调用 /adk/run_stream 并将 NDJSON 事件汇总为一次业务响应。"""
    cfg = get_ai_runtime_config()
    if not cfg.enabled:
        raise AIServiceError("AI集成当前已停用")
    if cfg.mode != "live":
        raise AIServiceError("AI集成当前为 Mock 模式，请在系统管理中切换为 live")

    prompt = question
    if context:
        prompt = (
            "你是TRM科技资源管理系统的AI助手。查询类问题仅依据下方系统事实数据和已授权的TRM MCP只读工具回答；"
            "数据不足时明确说明，不要编造编号、金额、审批结论或日期。"
            "用户要求创建项目或需求时，必须调用TRM MCP工具：先查询预算/关联数据，再调用 trm_prepare_create_* 并向用户展示预览；"
            "只有用户在预览后明确确认，才可调用 trm_create_* 幂等写入。不得跳过确认，也不得仅生成文字草稿来假装已创建。"
            "必须遵守事实上下文中的 effective_ai_capabilities 和 supported_writes；这些能力直接继承当前角色的业务权限，未列出的能力明确告知用户无权操作。"
            "调用每个 trm_* 工具时必须原样传入 mcp_authorization.delegation_token，不得在答案中显示、引用或解释该令牌。"
            "如果当前会话没有这些工具，明确说明‘TRM MCP尚未绑定到当前智能体’。\n\n"
            f"【交互入口】{source}\n"
            f"【用户问题】{question}\n\n"
            f"【系统事实数据】\n{context}"
        )

    payload = {
        "agent_id": cfg.agent_id,
        "user_id": f"trm:{user_id}"[:128],
        "session_id": (session_id or "")[:200],
        "message": prompt,
        "images": [],
        "audios": [],
        "_meta": {"source": source, "system": "TRM"},
    }
    timeout = httpx.Timeout(
        connect=cfg.connect_timeout,
        read=cfg.read_timeout,
        write=20.0,
        pool=cfg.connect_timeout,
    )

    answer_parts: list[str] = []
    returned_session = session_id or ""
    remote_error = ""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                f"{cfg.base_url}/adk/run_stream",
                json=payload,
                headers={"Accept": "application/x-ndjson"},
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")[:300]
                    raise AIServiceError(f"智能体服务返回 HTTP {response.status_code}：{body or '无错误详情'}")
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("session_id"):
                        returned_session = str(event["session_id"])
                    text = _event_text(event)
                    if text:
                        # 流末尾通常会再发一次带 usageMetadata/finishReason 的完整答案，
                        # 用它替换增量片段，避免最终文本重复。
                        if event.get("usageMetadata") is not None or event.get("finishReason") is not None:
                            answer_parts = [text]
                        else:
                            answer_parts.append(text)
                    if event.get("error"):
                        error_value = event["error"]
                        remote_error = error_value if isinstance(error_value, str) else json.dumps(error_value, ensure_ascii=False)
    except AIServiceError:
        raise
    except httpx.TimeoutException as exc:
        raise AIServiceError("智能体响应超时，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise AIServiceError(f"无法连接智能体服务：{exc}") from exc

    answer = "".join(answer_parts).strip()
    if not answer:
        raise AIServiceError(remote_error or "智能体未返回可展示的内容")
    return {
        "answer": answer,
        "session_id": returned_session,
        "provider": "Gazellio G.AIOS",
        "agent_id": cfg.agent_id,
    }

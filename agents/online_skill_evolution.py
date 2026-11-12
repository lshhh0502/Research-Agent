from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable


SideQuery = Callable[[str, str], Awaitable[str]]
ConfirmWrite = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class OnlineSkillCandidate:
    name: str
    description: str
    when_to_use: str = ""
    instructions: str = ""
    evidence: str = ""
    tags: list[str] = field(default_factory=list)


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty model response")
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start : end + 1])
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    raise ValueError("model response must contain a JSON object")


def _normalize_identity(text: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", raw).strip()


def _candidate_search_text(candidate: OnlineSkillCandidate) -> str:
    return "\n".join(
        [
            candidate.name,
            candidate.description,
            candidate.when_to_use,
            candidate.instructions,
            " ".join(candidate.tags),
        ]
    )


def _coerce_candidate(obj: dict[str, Any]) -> OnlineSkillCandidate | None:
    name = str(obj.get("name") or "").strip()
    description = str(obj.get("description") or "").strip()
    instructions = str(obj.get("instructions") or obj.get("prompt") or "").strip()
    if not name or not description or not instructions:
        return None
    tags_raw = obj.get("tags") or []
    if isinstance(tags_raw, str):
        tags = [part.strip() for part in re.split(r"[,，]", tags_raw) if part.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(part).strip() for part in tags_raw if str(part).strip()]
    else:
        tags = []
    return OnlineSkillCandidate(
        name=name,
        description=description,
        when_to_use=str(obj.get("when_to_use") or obj.get("when-to-use") or "").strip(),
        instructions=instructions,
        evidence=str(obj.get("evidence") or "").strip(),
        tags=tags[:8],
    )


async def extract_online_skill_candidate(
    *,
    messages: list[dict[str, Any]],
    side_query: SideQuery,
    retrieved_reference: dict[str, Any] | None = None,
    hint: str = "",
) -> OnlineSkillCandidate | None:
    system = """你是 RA 的在线 Skill 提取器。从当前会话提取至多一个可复用的方法候选。
仅输出严格 JSON：{"skills": []} 或 {"skills": [{...}]}。
候选字段为 name、description、when_to_use、instructions、evidence、tags。

规则：
- 用户发言是主要证据，助手发言仅作背景。后续用户反馈可确认、否定或细化已有做法。
- 仅提取可复用的流程、输出偏好、明确纠正或重复出现的约束。研究主题、具体结论和证据属于 Memory。
- 排除助手猜测、含糊确认、一次性参数、密钥、具体日期、链接、账号和项目事实。用占位符代替任务中的实体。
- retrieved_reference 仅用于识别已有 Skill，不得作为新的用户证据。
- 证据不足或建议无复用价值时返回 {"skills": []}。
"""
    payload = {
        "messages": messages,
        "hint": hint,
        "retrieved_reference": retrieved_reference or None,
    }
    parsed = _parse_json_object(await side_query(system, json.dumps(payload, ensure_ascii=False)))
    skills = parsed.get("skills")
    if not isinstance(skills, list) or len(skills) > 1:
        raise ValueError("extractor must return a skills array with at most one candidate")
    if not skills:
        return None
    first = skills[0]
    candidate = _coerce_candidate(first) if isinstance(first, dict) else None
    if candidate is None:
        raise ValueError("invalid extracted Skill candidate")
    return candidate


def _exact_identity_match(candidate: OnlineSkillCandidate, skills: list[Any]) -> str:
    candidate_ids = {
        _normalize_identity(candidate.name),
        _normalize_identity(candidate.description),
        _normalize_identity(candidate.when_to_use),
    }
    candidate_ids.discard("")
    for skill in skills:
        skill_ids = {
            _normalize_identity(getattr(skill, "name", "")),
            _normalize_identity(getattr(skill, "description", "")),
            _normalize_identity(getattr(skill, "when_to_use", "") or ""),
        }
        skill_ids.discard("")
        if candidate_ids & skill_ids:
            return getattr(skill, "name", "")
    return ""


async def maintain_online_skill_candidate(
    *,
    candidate: OnlineSkillCandidate,
    side_query: SideQuery,
    retrieved_reference: dict[str, Any] | None = None,
    confirm_write: ConfirmWrite | None = None,
    target: str = "project",
    stage_only: bool = False,
) -> dict[str, Any]:
    from .skills import create_skill, discover_skills, evolve_skill, retrieve_relevant_skills

    skills = discover_skills()
    exact_target = _exact_identity_match(candidate, skills)
    similar_hits = retrieve_relevant_skills(_candidate_search_text(candidate), limit=8, min_score=0.03)
    top_reference_name = str((retrieved_reference or {}).get("name") or "").strip()

    system = """你是 RA 的在线 Skill 管理器。判断候选方法应新增、合并或丢弃。仅输出严格 JSON。
格式：{"action":"add|merge|discard","target_skill":"合并目标名称","reason":"简短原因",
"merged_description":"可选","merged_when_to_use":"可选","merged_instructions":"可选的完整合并正文"}。
已有同类方法时优先合并。候选与共享或项目 Skill 重复且没有长期改进时丢弃。
合并时保留已有有效规则，仅加入可复用的新流程和偏好。具体证据、研究结论、密钥和一次性参数不得写入 Skill。"""
    payload = {
        "candidate": asdict(candidate),
        "exact_identity_target": exact_target,
        "retrieved_reference": retrieved_reference or None,
        "similar_skills": similar_hits,
        "existing_skills": [
            {
                "name": getattr(skill, "name", ""),
                "description": getattr(skill, "description", ""),
                "when_to_use": getattr(skill, "when_to_use", "") or "",
                "source": getattr(skill, "source", ""),
                "context": getattr(skill, "context", ""),
                "instructions": (getattr(skill, "prompt_template", "") or "")[:6000],
            }
            for skill in skills[:80]
        ],
    }

    decision = _parse_json_object(await side_query(system, json.dumps(payload, ensure_ascii=False)))
    action = str(decision.get("action") or "").strip().lower()
    target_skill = str(decision.get("target_skill") or "").strip()

    if exact_target:
        action = "merge"
        target_skill = exact_target
    elif action == "merge" and not target_skill:
        target_skill = top_reference_name

    if action not in {"add", "merge", "discard"}:
        raise ValueError("invalid Skill maintenance action")

    if action == "discard":
        return {"ok": True, "action": "discard", "skill": "", "decision": decision}

    if action == "merge":
        target_skill = target_skill or top_reference_name
        if not target_skill:
            return {"ok": False, "action": "merge", "error": "missing target_skill", "decision": decision}
        if not isinstance(decision.get("merged_instructions"), str) or not decision["merged_instructions"].strip():
            return {"ok": False, "action": "merge", "error": "merge requires complete merged_instructions", "decision": decision}
        tool_name = "skill_evolve"
        write_input = dict(
            skill_name=target_skill,
            lesson=candidate.evidence or candidate.description,
            rationale=str(decision.get("reason") or "Online maintainer merge"),
            target="active",
            instructions=str(decision.get("merged_instructions") or candidate.instructions),
            description=str(decision.get("merged_description") or ""),
            when_to_use=str(decision.get("merged_when_to_use") or candidate.when_to_use),
            tags=candidate.tags,
            preserve_existing=True,
        )
    else:
        tool_name = "skill_create"
        write_input = dict(
            name=candidate.name, description=candidate.description, instructions=candidate.instructions,
            when_to_use=candidate.when_to_use, target=target, context="inline", user_invocable=False,
            evidence=candidate.evidence, actor="online", tags=candidate.tags,
        )

    if confirm_write is None or not await confirm_write(tool_name, write_input):
        return {
            "ok": False,
            "action": f"{action}_denied",
            "skill": target_skill or candidate.name,
            "error": "permission denied",
            "decision": decision,
        }

    if stage_only:
        from .skill_candidates import stage_candidate
        return stage_candidate(action, write_input)

    if action == "merge":
        result = evolve_skill(**write_input)
        return {"action": "merge", "candidate": asdict(candidate), "decision": decision, **result}

    result = create_skill(**write_input)
    return {"action": "add", "candidate": asdict(candidate), "decision": decision, **result}


async def online_ingest(
    *,
    messages: list[dict[str, Any]],
    side_query: SideQuery,
    retrieved_reference: dict[str, Any] | None = None,
    hint: str = "",
    confirm_write: ConfirmWrite | None = None,
    target: str = "project",
    replay_context: dict[str, Any] | None = None,
    stage_only: bool = True,
) -> dict[str, Any]:
    from .skills import record_online_provenance

    try:
        candidate = await extract_online_skill_candidate(
            messages=messages,
            side_query=side_query,
            retrieved_reference=retrieved_reference,
            hint=hint,
        )
    except Exception as exc:
        result = {"ok": False, "action": "failed", "error": str(exc)}
        record_online_provenance(
            action="failed",
            replay_context=replay_context,
            result=result,
            messages=messages,
            retrieved_reference=retrieved_reference,
            error=str(exc),
        )
        return result

    if candidate is None:
        result = {"ok": True, "action": "none"}
        record_online_provenance(
            action="none",
            replay_context=replay_context,
            result=result,
            messages=messages,
            retrieved_reference=retrieved_reference,
        )
        return result

    try:
        result = await maintain_online_skill_candidate(
            candidate=candidate,
            side_query=side_query,
            retrieved_reference=retrieved_reference,
            confirm_write=confirm_write,
            target=target,
            stage_only=stage_only,
        )
    except Exception as exc:
        result = {"ok": False, "action": "failed", "skill": candidate.name, "error": str(exc)}

    record_online_provenance(
        action=str(result.get("action") or "none"),
        replay_context=replay_context,
        skill_name=str(result.get("skill") or candidate.name),
        result=result,
        messages=messages,
        retrieved_reference=retrieved_reference,
        decision=result.get("decision") if isinstance(result.get("decision"), dict) else None,
        error="" if result.get("ok") else str(result.get("error") or ""),
    )
    return result


async def judge_retrieved_skill_usage(
    *,
    hits: list[dict[str, Any]],
    user_message: str,
    assistant_text: str,
    side_query: SideQuery | None = None,
) -> list[dict[str, Any]]:
    if not hits:
        return []
    if side_query is None:
        # No judge means no evidence. Mentioning a Skill name does not prove usage.
        return []

    system = """判断召回的 Skill 是否与用户请求相关，以及回复是否实际遵循其独有流程或规则。
仅输出严格 JSON：{"judgments":[{"name":"名称","relevant":true|false,"used":true|false,"reason":"简短理由"}]}。
仅召回或提及 Skill 名称不算使用。"""
    payload = {"user_message": user_message, "assistant_reply": assistant_text, "retrieved_skills": hits}
    parsed = _parse_json_object(await side_query(system, json.dumps(payload, ensure_ascii=False)))
    raw_judgments = parsed.get("judgments") if isinstance(parsed, dict) else None
    names = {str(hit.get("name") or "") for hit in hits}
    if (not isinstance(raw_judgments, list) or len(raw_judgments) != len(names)
            or any(not isinstance(item, dict) or item.get("name") not in names
                   or type(item.get("relevant")) is not bool or type(item.get("used")) is not bool
                   or (item["used"] and not item["relevant"]) for item in raw_judgments)
            or len({item["name"] for item in raw_judgments}) != len(names)):
        raise ValueError("Invalid Skill usage judgments; statistics were not updated")
    by_name = {item["name"]: item for item in raw_judgments}
    judgments: list[dict[str, Any]] = []
    for hit in hits:
        name = str(hit.get("name") or "")
        raw = by_name.get(name, {})
        judgments.append(
            {
                "name": name,
                "source": hit.get("source", ""),
                "skill_dir": hit.get("skill_dir", ""),
                "retrieved": True,
                "relevant": bool(raw.get("relevant")),
                "used": bool(raw.get("used")),
                "score": float(hit.get("score", 0.0)),
                "reason": str(raw.get("reason") or ""),
            }
        )
    return judgments

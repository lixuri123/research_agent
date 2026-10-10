"""Pure, deterministic routing over small owner-scoped metadata indexes.

Reference authorization belongs to the source adapter/API. This module never
reads task bodies or promotes a text reference into execution permission.
"""
import re
import unicodedata

from .policies import COMPLETED_TURN_STATUSES, ROUTER_VERSION
from .schemas import ContextIndex, ContextRequest, RouteDecision


def _normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text)).casefold()).strip()


def _routing_text(text):
    # Examples and quotations are data, so cannot themselves activate routes.
    text = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`]*`", " ", text)
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|\'[^\'\n]*\'', " ", text)
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    return _normalize(text)


def _identifier(row):
    return str(row.get("task_id") or row.get("id") or "")


def _in_scope(row, request, *, turn=False):
    if "owner_id" in row and row["owner_id"] != request.owner_id:
        return False
    if "workspace_id" in row and (row["workspace_id"] or None) != request.workspace_id:
        return False
    if turn and "session_id" in row and row["session_id"] != request.session_id:
        return False
    if row.get("run_id") == request.run_id:
        return False
    if row.get("created_at") is not None and row["created_at"] >= request.submitted_at:
        return False
    return True


def _decision(route, rule, reason, *, tasks=(), turns=(), clarification=None):
    return RouteDecision(route=route, rule_id=f"{ROUTER_VERSION}/{rule}",
                         task_ids=tuple(dict.fromkeys(tasks)), turn_ids=tuple(dict.fromkeys(turns)),
                         reason_codes=(reason,), needs_clarification=clarification is not None,
                         clarification=clarification)


def _clarify(route, rule, reason, message="请明确要接着讨论的对象或提供任务 ID。"):
    return _decision(route, rule, reason, clarification=message)


def _enumerates(text):
    return bool(re.search(r"(?:我)?正在做的所有任务|(?:这个|当前)(?:工作)?空间的(?:所有|进行中|正在进行的)任务|所有正在进行的任务", text))


def _dependency(text):
    return bool(re.search(r"这个方案|刚才那个|你(?:上面|刚才|之前)(?:说|提到)|换一种解释|详细一点|再详细|^(?:请)?(?:再)?(?:接着说|继续说|继续那个|(?:继续|接着)(?:上一个|那个|这个)任务)[。？！!?\s]*$", text))


def _full_title_in_text(title, text):
    if title.isascii():
        return bool(re.search(r"(?<!\w)" + re.escape(title) + r"(?!\w)", text))
    # Chinese has no word separators. Admit only a small set of reference
    # templates, not substrings inside a longer task title (论文实验设计).
    for match in re.finditer(re.escape(title), text):
        before, after = text[:match.start()], text[match.end():]
        left = (not before or not before[-1].isalnum() or
                re.search(r"(?:继续|推进|回顾|汇报|比较|对比|安排|协调|和|与|以及)(?:一下|我的|这个|这项任务)?$", before))
        right = (not after or not after[0].isalnum() or
                 re.match(r"(?:的|和|与|以及|这项任务|这个任务|任务|优先级|有什么|是否)", after))
        if left and right:
            return True
    return False


def _references(text, tasks):
    """Exact IDs or full titles, never fuzzy aliases or generic keywords."""
    ids, titles = [], {}
    for row in tasks:
        identifier = _identifier(row)
        if identifier:
            escaped = re.escape(_normalize(identifier))
            labelled = re.search(r"(?:任务\s*(?:id)?|task\s*(?:id)?)\s*[:：#=]?\s*" + escaped + r"(?![\w-])", text)
            bare = len(identifier) >= 8 and re.search(r"(?<![\w-])" + escaped + r"(?![\w-])", text)
            if labelled or bare:
                ids.append(identifier)
        title = _normalize(row.get("title") or row.get("topic") or "")
        if title and len(title) > 1 and _full_title_in_text(title, text):
            titles.setdefault(title, []).append(identifier)
    return tuple(dict.fromkeys(ids)), titles


def decide_route(request: ContextRequest, index: ContextIndex) -> RouteDecision:
    options = request.context
    text = _routing_text(request.question + "\n" + request.requirements)
    tasks = [row for row in index.tasks if _in_scope(row, request)]
    turns = [row for row in index.turns if _in_scope(row, request, turn=True)
             and row.get("status") in COMPLETED_TURN_STATUSES and str(row.get("assistant") or "").strip()]
    turns = sorted(turns, key=lambda row: (row.get("created_at", 0), str(row.get("run_id") or _identifier(row))), reverse=True)[:6]
    if options.turn_ids:
        turns = [row for row in turns if _identifier(row) in options.turn_ids]

    if options.mode == "independent":
        return _decision("independent", "R01", "explicit_independent")

    if options.mode != "auto":
        if options.mode == "followup":
            if not turns:
                return _clarify("followup", "R02", "missing_anchor")
            selected = options.turn_ids or [_identifier(turns[0])]
            return _decision("followup", "R02", "explicit_followup", turns=selected)
        if options.task_ids:
            return _decision(options.mode, "R02", "explicit_mode", tasks=options.task_ids)
        ids, titles = _references(text, tasks)
        matches = tuple(dict.fromkeys(ids + tuple(value[0] for value in titles.values() if len(value) == 1)))
        if any(len(value) > 1 for value in titles.values()):
            return _clarify(options.mode, "R02", "ambiguous_title", "有多个同名任务，请提供任务 ID。")
        if options.mode == "multi_task" and _enumerates(text):
            return _active(request, index, "R02")
        if (options.mode == "task" and len(matches) == 1) or (options.mode == "multi_task" and 2 <= len(matches) <= 10):
            return _decision(options.mode, "R02", "explicit_mode_reference", tasks=matches)
        return _clarify(options.mode, "R02", "missing_target")

    if options.task_ids:
        return _decision("task" if len(options.task_ids) == 1 else "multi_task", "R03", "explicit_task_ids", tasks=options.task_ids)

    ids, titles = _references(text, tasks)
    action = bool(re.search(r"继续|推进|回顾|汇报", text))
    if ids or (action and titles):
        if not ids and any(len(value) > 1 for value in titles.values()):
            return _clarify("task", "R04", "ambiguous_title", "有多个同名任务，请提供任务 ID。")
        selected = ids or tuple(value[0] for value in titles.values())
        if len(selected) > 10:
            return _clarify("multi_task", "R04", "target_overflow", "请将任务范围缩小至最多 10 个。")
        return _decision("task" if len(selected) == 1 else "multi_task", "R04", "text_task_reference", tasks=selected)

    if _enumerates(text):
        return _active(request, index, "R05")

    if re.search(r"比较|对比|优先级|协调|安排.*(?:和|与)|(?:和|与).*安排", text) and len(titles) >= 2:
        if any(len(value) != 1 for value in titles.values()):
            return _clarify("multi_task", "R06", "ambiguous_title", "请明确比较对象的任务 ID。")
        selected = tuple(dict.fromkeys(value[0] for value in titles.values()))
        if len(selected) > 10:
            return _clarify("multi_task", "R06", "target_overflow", "请将任务范围缩小至最多 10 个。")
        return _decision("multi_task", "R06", "named_comparison", tasks=selected)

    if _dependency(text):
        if not turns:
            return _clarify("followup", "R08", "missing_anchor")
        anchor = turns[0]
        entity = re.search(r"(?:提到|说的)\s*([^,，。？！?\s]+)", text)
        if entity:
            matching = [row for row in turns if entity.group(1) in _normalize(str(row.get("user", "")) + " " + str(row.get("assistant", "")))]
            if not matching:
                return _clarify("followup", "R08", "missing_named_anchor")
            anchor = matching[0]
        if re.search(r"(?:上一个|那个|这个)任务", text):
            associated = anchor.get("associated_task_ids") or []
            if len(associated) != 1:
                return _clarify("task", "R08", "ambiguous_task_anchor")
            return _decision("task", "R07", "previous_task_anchor", tasks=associated, turns=[_identifier(anchor)])
        if text.strip("。？！!? ") == "继续那个" and len(anchor.get("associated_task_ids") or []) > 1:
            return _clarify("followup", "R08", "ambiguous_anchor")
        return _decision("followup", "R07", "conversation_dependency", turns=[_identifier(anchor)])

    return _decision("independent", "R09", "self_contained")


def _active(request, index, rule):
    active = [row for row in index.active_tasks if _in_scope(row, request)
              and ("status" not in row or row["status"] in {"queued", "running", "waiting_approval"})]
    if index.active_overflow or len(active) > 10:
        return _clarify("multi_task", rule, "active_overflow", "请将进行中任务的范围缩小至最多 10 个。")
    return _decision("multi_task", rule, "active_enumeration" if active else "no_active_tasks", tasks=[_identifier(row) for row in active])


from __future__ import annotations

import re as _re

from app.config.settings import get_settings
from app.rag.retriever import HybridRetriever
from app.rag.reranker import rerank
from app.workflows.state import AgentState

_RETRIEVER: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = HybridRetriever()
    return _RETRIEVER


def reset_retriever() -> None:
    global _RETRIEVER
    _RETRIEVER = None


# 从 query 中提取可能的"实体词"（型号、错误码、品牌）
ENTITY_PATTERNS = [
    _re.compile(r"\b[A-Z]{1,4}\d{2,5}\b"),    # E102 / XY200 / ABC123
    _re.compile(r"\b[A-Za-z]{3,}\b"),           # 英文词
]


def _extract_entities(text: str) -> list:
    entities = []
    for pat in ENTITY_PATTERNS:
        for m in pat.finditer(text):
            e = m.group(0)
            if len(e) >= 2:
                entities.append(e.lower())
    return entities


def _entity_hit(query: str, chunks: list) -> bool:
    """query 里的实体词是否出现在 Top 结果里。"""
    entities = _extract_entities(query)
    if not entities:
        return False
    combined = " ".join((c.get("text", "") or "").lower() for c in chunks[:3])
    for e in entities:
        if e in combined:
            return True
    return False


SYNONYMS = {
    "报警": ["报警", "告警", "异常", "故障"],
    "排查": ["排查", "检查", "处理", "怎么修"],
    "重启": ["重启", "复位", "重新启动"],
}


def rewrite_query(q: str) -> str:
    """第一次改写：**做加法** —— 同义词扩展。

    目的是让 BM25 这一路能召回不同说法的文档。
    """
    tokens = [q]
    for key, syns in SYNONYMS.items():
        if key in q:
            tokens.extend([s for s in syns if s != key])
    return " ".join(tokens)


# ============================================================
# 反思与重试：第二次换一个**角度**再试
# ------------------------------------------------------------
# 检索不到往往不是因为"说法不同"，而是因为**问句太长** ——
# 「XY200 报 E102 故障码该怎么处理啊」这种句子，BM25 会把
# 「怎么/该/处理/啊」也算进词频，把真正的实体词 XY200、E102 稀释掉。
#
# ❌ 试过但放弃的方案：去停用词。
#    中文停用词表**永远补不全** —— jieba 会把「怎么办」切成一个词、
#    把「还是不行」切成一段，漏一个就产出噪声查询，比重试前更差。
#    实测：「请问报警了怎么办」→「报警怎么办」、
#          「设备重启后还是不行是什么原因」→「设备重启后还是不行是原因」。
#
# ✅ 采用的方案：**只保留实体词**（故障码 / 型号）。
#    这类词能被正则**可靠识别**，不依赖词表；而且恰恰是检索失败时
#    最该聚焦的东西。没有实体词就说明没有可聚焦的点，不重试。
# ============================================================


def _extract_entities_raw(text: str) -> list:
    """抽实体词，**保留原大小写** —— 故障码与型号是大小写敏感的。"""
    out = []
    for pat in ENTITY_PATTERNS:
        for m in pat.finditer(text):
            e = m.group(0)
            if len(e) >= 2:
                out.append(e)
    return out


def build_retry_query(q: str) -> str:
    """为重试构造聚焦查询：只留实体词，去重保序。

    返回空串表示「没有可聚焦的点，不值得重试」。
    """
    original = (q or "").strip()
    if not original:
        return ""
    ents = _extract_entities_raw(original)
    uniq = list(dict.fromkeys(e for e in ents if len(e) >= 2))
    if not uniq:
        return ""
    focused = " ".join(uniq)
    # 原句本身就是这几个实体（用户直接发了个故障码）-> 重试没意义
    if focused.lower() == original.lower():
        return ""
    return focused


def _retrieve_once(retriever: HybridRetriever, query: str, s) -> tuple:
    """跑一轮完整召回（混合召回 → 父块扩展 → 重排），返回 (reranked, top_score)。"""
    hits = retriever.search_hybrid(query, k=s.top_k_retrieve)
    child_ids = [cid for cid, _ in hits]
    candidates = retriever.fetch_chunks(child_ids)
    parents = retriever.expand_to_parents(child_ids)
    seen = {c["chunk_id"] for c in candidates}
    pool = candidates + [p for p in parents if p["chunk_id"] not in seen]
    reranked = rerank(query, pool, top_k=max(15, s.top_k_rerank))
    top_score = float(reranked[0].get("rerank_score", 0.0)) if reranked else 0.0
    return reranked, top_score


def _is_relevant(query: str, reranked: list, top_score: float, threshold: float) -> bool:
    """相关性判定：分数过阈值 **或** 实体词被覆盖。

    两道都不满足就拒答 —— 宁可说"没找到"，也不让模型硬编。
    """
    if not reranked:
        return False
    return (top_score >= threshold) or _entity_hit(query, reranked)


def rag_search_node(state: AgentState) -> AgentState:
    s = get_settings()
    trace = state.get("trace_id", "?")[:8]
    query = state.get("user_input", "")
    retriever = get_retriever()

    # 候选查询：第一次同义词扩展，第二次做减法。重试次数由配置控制。
    first = rewrite_query(query)
    candidates = [first]
    max_retry = max(0, int(getattr(s, "rag_retry_max", 1) or 0))
    if max_retry > 0:
        focused = build_retry_query(query)
        # 聚焦后没变化（或压根没有实体词）-> 没有重试价值，别白跑一轮
        if focused and focused != first:
            candidates.append(focused)

    attempts: list = []
    chosen_query = first
    chosen_reranked: list = []
    chosen_score = 0.0
    passed = False

    for i, q in enumerate(candidates, start=1):
        reranked, top_score = _retrieve_once(retriever, q, s)
        ok = _is_relevant(q, reranked, top_score, s.min_relevance_score)
        attempts.append({
            "attempt": i,
            "query": q,
            "hits": len(reranked),
            "top_score": round(top_score, 4),
            "passed": ok,
        })
        print(f"[RAG:{trace}] attempt={i} q={q!r} hits={len(reranked)} "
              f"top={top_score:.4f} passed={ok}")

        # 保留目前最好的一轮，即便最终没通过也用于展示置信度
        if not chosen_reranked or top_score > chosen_score:
            chosen_query, chosen_reranked, chosen_score = q, reranked, top_score

        if ok:
            chosen_query, chosen_reranked, chosen_score = q, reranked, top_score
            passed = True
            break

    if not passed:
        if attempts:
            top_all = max(a["top_score"] for a in attempts)
            print(f"[RAG:{trace}] 全部尝试均未通过阈值 -> 拒答"
                  f"（尝试 {len(attempts)} 次，最高分 {top_all:.4f}）")
        else:
            print(f"[RAG:{trace}] 无可用查询 -> 拒答")
        return {
            **state,
            "query_rewritten": chosen_query,
            "retrieved": [],
            "confidence": max(0.0, min(1.0, chosen_score)),
            "rag_attempts": attempts,
            "flow_status": "rejected",
        }

    if len(attempts) > 1:
        print(f"[RAG:{trace}] ★ 第 {attempts[-1]['attempt']} 次改写后命中："
              f"{candidates[0]!r} -> {chosen_query!r}")

    # 只保留超过阈值的结果
    kept = [c for c in chosen_reranked
            if float(c.get("rerank_score", 0.0)) >= s.min_relevance_score * 0.8]

    return {
        **state,
        "query_rewritten": chosen_query,
        "retrieved": kept,
        "confidence": max(0.0, min(1.0, chosen_score)),
        "rag_attempts": attempts,
    }

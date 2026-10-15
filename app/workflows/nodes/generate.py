"""生成节点：LLM 调用 + 多层输出后处理 + 问题类型过滤。"""
from __future__ import annotations
import re
from typing import Dict, List

from app.config.settings import get_settings
from app.workflows.state import AgentState

_LLM_CACHE = None

THINK_BLOCK = re.compile(r"<think[^>]*>.*?</think[^>]*>", re.DOTALL | re.IGNORECASE)
THINK_TAG = re.compile(r"</?think[^>]*>", re.IGNORECASE)

# 剥 heading：直接匹配 [xxx] 开头（含或不含 >）
HEADING_BRACKET_RE = re.compile(r"^\s*\[[^\]]{1,150}\]\s*")
HEADING_BARE_RE = re.compile(r"^[^\[\]\n>]{1,60}\s*>\s*[^\[\]\n>]{1,60}\s*")

TITLE_WORDS = [
    "适用范围", "排查步骤", "故障现象", "解决方法", "注意事项",
    "操作步骤", "问题描述", "原因分析", "处理建议", "参考来源",
    "处理边界", "通用规则", "原因说明", "AI 与人工边界", "AI与人工边界",
]

STOP_MARKERS = [
    "根据知识片段", "根据上述分析", "根据以上分析", "根据提供的",
    "用户问题：", "用户问题:", "用户提问：", "用户提问:",
    "\n问题：", "\n问题:", "\n答案：", "\n答案:",
    "参考来源：", "参考来源:",
    "**用户问题", "**答案",
    "\n备注：", "\n备注:", "\n注：", "\n注:",
    "回答：", "回答:", "总结：", "总结:",
]

REPEAT_CIT = re.compile(r"(\[\d+\]\s*){2,}")
LEADING_NO_INFO = re.compile(r"^\s*暂[无未][^。\n]*建议转人工[。\.]?\s*")
BRACKET_ACTION = re.compile(r"\[(检查|重启|更换|确认|排查|处理)[^\]]{0,30}\]")
NO_INFO_KEYWORDS = ["暂无相关依据", "无法回答", "知识片段中没", "没有相关"]

TERM_MAP = {
    "temperature": "温度", "sensor": "传感器",
    "device": "设备", "module": "模块", "board": "主板",
    "battery": "电池", "fan": "风扇", "power": "电源",
    "warning": "报警", "alert": "告警", "alarm": "报警",
    "error": "故障", "fault": "故障", "failure": "故障",
    "code": "代码", "issue": "问题", "problem": "问题",
    "abnormal": "异常", "normal": "正常", "broken": "损坏",
    "still": "仍然", "again": "再次",
    "check": "检查", "restart": "重启", "reboot": "重启",
    "replace": "更换", "reset": "复位",
    "step": "步骤", "solution": "解决方案", "cause": "原因",
}

TRAD_TO_SIMPLE = {
    "啟": "启", "動": "动", "後": "后", "體": "体", "溫": "温",
    "傳": "传", "檢": "检", "線": "线", "設": "设", "備": "备",
    "換": "换", "錯": "错", "誤": "误", "報": "报", "題": "题",
    "問": "问", "決": "决", "處": "处", "確": "确", "認": "认",
    "試": "试", "開": "开", "關": "关", "調": "调", "節": "节",
    "電": "电", "連": "连", "軸": "轴", "壞": "坏", "養": "养",
    "護": "护", "產": "产", "業": "业", "務": "务", "區": "区",
    "網": "网", "號": "号", "碼": "码", "馬": "马", "驅": "驱",
    "統": "统", "計": "计", "點": "点", "無": "无",
}

_SPACE_IN_CJK = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])")
_CJK_EN_BOUNDARY = re.compile(r"([\u4e00-\u9fff])\s+([A-Za-z])")
_EN_CJK_BOUNDARY = re.compile(r"([A-Za-z])\s+([\u4e00-\u9fff])")
_TRAD_RE = re.compile("[" + "".join(TRAD_TO_SIMPLE.keys()) + "]")


def _strip_think(text: str) -> str:
    return THINK_TAG.sub("", THINK_BLOCK.sub("", text))


def _strip_one_heading(text: str) -> str:
    """剥离一层 heading。"""
    if not text:
        return text
    text = text.strip()
    # 优先剥 [xxx]（含 [xxx > yyy]）
    new = HEADING_BRACKET_RE.sub("", text).strip()
    if new != text:
        return new
    # 再剥裸的 xxx > yyy
    new = HEADING_BARE_RE.sub("", text).strip()
    return new


def _strip_all_headings(text: str) -> str:
    for _ in range(5):
        new = _strip_one_heading(text)
        if new == text:
            break
        text = new
    return text


def _truncate_at_marker(text: str) -> str:
    earliest = len(text)
    for marker in STOP_MARKERS:
        idx = text.find(marker)
        if 0 < idx < earliest:
            earliest = idx
    return text[:earliest].rstrip()


def _to_simplified(text: str) -> str:
    return _TRAD_RE.sub(lambda m: TRAD_TO_SIMPLE.get(m.group(), m.group()), text)


def _normalize_text(text: str) -> str:
    if not text:
        return text
    for en, zh in TERM_MAP.items():
        text = re.sub(rf"(?<![A-Za-z]){en}(s|es)?(?![A-Za-z])", zh, text, flags=re.IGNORECASE)
    for _ in range(3):
        new = _SPACE_IN_CJK.sub("", text)
        if new == text:
            break
        text = new
    text = _CJK_EN_BOUNDARY.sub(r"\1\2", text)
    text = _EN_CJK_BOUNDARY.sub(r"\1\2", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _dedup_citations(text: str) -> str:
    if not text:
        return text
    seen = set()
    def _repl(m):
        num = m.group(1)
        if num in seen:
            return ""
        seen.add(num)
        return m.group(0)
    text = re.sub(r"\[(\d+)\]", _repl, text)
    text = re.sub(r"\s+([。，；：！？、])", r"\1", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _merge_orphan_citations(text: str) -> str:
    if not text:
        return text
    lines = text.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        m = re.match(r"^\s*(?:\d+\.\s*)?\[\s*(\d+)\s*\]\s*$", stripped)
        if m:
            cit = f"[{m.group(1)}]"
            if out:
                prev = out[-1].rstrip()
                prev = re.sub(r"[。.]\s*$", "", prev)
                out[-1] = f"{prev} {cit}。"
            continue
        out.append(line)
    return "\n".join(out)


def _format_as_numbered_list(text: str) -> str:
    if not text:
        return text
    text = text.strip()
    if "\n" not in text:
        matches = list(re.finditer(r"(?<![\d])(\d+)\.\s", text))
        if len(matches) >= 2:
            out = text
            for m in reversed(matches[1:]):
                out = out[:m.start()] + "\n" + out[m.start():]
            text = out
    if re.match(r"^[0-9]+[\.、\)]\s", text):
        lines = []
        for line in text.splitlines():
            line = line.rstrip()
            line = re.sub(r"^(\s*)([0-9]+)[、\)]\s*", r"\1\2. ", line)
            lines.append(line)
        return "\n".join(lines)
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip().rstrip("。.") for p in re.split(r"[；;]+", text) if p.strip()]
    if len(parts) <= 1:
        parts = []
        for p in re.split(r"。(?!\d)", text):
            p = p.strip().rstrip("。.")
            if p:
                parts.append(p)
    if len(parts) <= 1:
        return text
    out = []
    seen = set()
    for p in parts[:8]:
        p = re.sub(r"^\s*[0-9]+[\.、\)]\s*", "", p).strip()
        if not p:
            continue
        # 去重：前 20 字相同视为重复
        key = p[:20]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)

    if len(out) <= 1:
        return text
    return "\n".join(f"{i}. {p}" for i, p in enumerate(out, 1))


def clean_answer(text: str) -> str:
    if not text:
        return text
    text = _strip_think(text)
    text = _strip_all_headings(text)
    text = _truncate_at_marker(text)
    text = LEADING_NO_INFO.sub("", text)
    text = BRACKET_ACTION.sub("", text)
    text = REPEAT_CIT.sub(" ", text)
    text = re.sub(r"^\[\d+\][.、:：\s]*", "", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = text.strip()
    text = _dedup_citations(text)
    text = _merge_orphan_citations(text)
    text = _to_simplified(text)
    text = _normalize_text(text)
    text = _format_as_numbered_list(text)
    text = _merge_orphan_citations(text)
    if len(text) > 500:
        text = text[:500].rstrip()
    return text



# ============================================================
# 引用替换：[1] -> [doc_id]
# ============================================================
def _replace_citation_numbers(answer: str, chunks: List[Dict]) -> str:
    if not answer or not chunks:
        return answer
    mapping = {}
    for i, c in enumerate(chunks[:5], 1):
        meta = c.get("metadata", {}) or {}
        label = meta.get("doc_id") or meta.get("source") or str(i)
        mapping[str(i)] = label
    return re.sub(r"\[(\d+)\]", lambda m: f"[{mapping.get(m.group(1), m.group(1))}]", answer)


def _is_refusal(text: str) -> bool:
    if not text or len(text.strip()) < 3:
        return True
    for kw in NO_INFO_KEYWORDS:
        if kw in text:
            return True
    return False


def _is_ollama_base(base_url: str) -> bool:
    if not base_url:
        return True
    return "11434" in base_url or "ollama" in base_url.lower()


def get_llm():
    global _LLM_CACHE
    if _LLM_CACHE is not None:
        return _LLM_CACHE
    s = get_settings()
    base_url = s.llm_base_url or s.ollama_base_url
    model = s.llm_model or s.ollama_llm_model
    if _is_ollama_base(base_url):
        from langchain_ollama import ChatOllama
        _LLM_CACHE = ChatOllama(
            model=model, base_url=base_url,
            temperature=0.1, num_predict=600, num_ctx=2048,
            repeat_penalty=1.4, top_p=0.85, top_k=30,
        )
    else:
        from langchain_openai import ChatOpenAI
        _LLM_CACHE = ChatOpenAI(
            model=model, api_key=s.llm_api_key or "sk-dummy",
            base_url=base_url, temperature=0.1, timeout=120, max_tokens=2500,
        )
    return _LLM_CACHE


def reset_llm_cache() -> None:
    global _LLM_CACHE
    _LLM_CACHE = None


# ============================================================
# 问题类型识别 + chunk 过滤
# ============================================================
STEP_KW = ["排查", "怎么处理", "怎么办", "怎么修", "维修", "操作步骤", "怎么解决", "如何处理", "处理流程", "处理步骤", "流程"]
CAUSE_KW = ["原因", "为什么", "怎么回事", "为啥", "什么导致", "成因"]
PHENOMENON_KW = ["现象", "表现", "会怎样", "会怎么样", "会有什么", "什么后果", "后果", "征兆", "症状", "什么反应"]
BOUNDARY_KW = ["边界", "能做什么", "不能做什么", "可做", "不可做", "权限"]
NOTICE_KW = ["注意事项", "注意什么", "注意哪些", "注意点", "要注意"]
SCOPE_KW = ["适用范围", "哪些批次", "适用哪些", "哪些设备"]


def _classify_question(question: str) -> str:
    q = question or ""
    if any(k in q for k in CAUSE_KW):
        return "cause"
    if any(k in q for k in PHENOMENON_KW):
        return "phenomenon"
    if any(k in q for k in BOUNDARY_KW):
        return "boundary"
    if any(k in q for k in NOTICE_KW):
        return "notice"
    if any(k in q for k in SCOPE_KW):
        return "scope"
    if any(k in q for k in STEP_KW):
        return "step"
    return "generic"


# 问题类型 -> 目标 heading 关键词
TYPE_TO_HEADING = {
    "step":       ["排查步骤", "步骤", "解决", "处理步骤"],
    "cause":      ["原因", "分析", "成因"],
    "phenomenon": ["故障现象", "现象", "表现", "后果"],
    "boundary":   ["处理边界", "边界", "AI 与人工", "AI与人工"],
    "notice":     ["注意事项", "注意", "通用规则"],
    "scope":      ["适用范围", "范围"],
}


def _filter_chunks(chunks: List[Dict], qtype: str) -> List[Dict]:
    """按问题类型过滤 chunks。没匹配返回空列表。"""
    if qtype == "generic":
        return list(chunks)

    want = TYPE_TO_HEADING.get(qtype, [])
    if not want:
        return list(chunks)

    out = []
    for c in chunks:
        meta = c.get("metadata", {}) or {}
        heading = meta.get("heading_path") or ""
        for w in want:
            if w in heading:
                out.append(c)
                break

    return out


# ============================================================
# Prompt 模板
# ============================================================
PROMPT_STEP = """你是企业业务助手。用户问的是【怎么处理/排查/怎么办】。
**只从知识片段中提取【排查步骤/解决方法/操作步骤】的内容回答。**

【知识片段】
{context}

【用户问题】
{question}

【输出格式】
1. 第1步
2. 第2步
3. 第3步

【硬性约束】
- 每步一行，数字+英文句点开头
- 只输出操作步骤，不要复述故障现象
- 禁止输出章节标题
- 引用 [1] 紧贴句号前
- 中文简体，120 字内

- **只要知识片段里有相关内容，就必须回答，不要拒答**

**直接输出最终答案，不要复述问题，不要分析过程。**

【回答】"""

PROMPT_CAUSE = """你是企业业务助手。用户问的是【什么原因/为什么/怎么回事】。
**只从知识片段中提取【原因说明/原因分析/成因】的内容回答。如果片段里没有"原因"相关内容，直接回复：暂无相关依据。**

【知识片段】
{context}

【用户问题】
{question}

【输出格式】
1. 第1条原因
2. 第2条原因

【硬性约束】
- 只讲原因，禁止给操作步骤
- 禁止输出章节标题
- 引用 [1] 紧贴句号前
- 中文简体，120 字内

- **只要知识片段里有相关内容，就必须回答，不要拒答**

**直接输出最终答案，不要复述问题，不要分析过程。**

【回答】"""

PROMPT_PHENOMENON = """你是企业业务助手。用户问的是【什么现象/会怎样/会有什么表现】。
从知识片段中提取描述故障现象、后果、表现的内容，直接复述给用户。

【知识片段】
{context}

【用户问题】
{question}

【输出格式】
1. 第1条现象
2. 第2条现象

【硬性约束】
- **只要知识片段里有任何现象描述，就必须输出，禁止回复"暂无相关依据"**
- 只描述现象/后果，不要给操作步骤
- 禁止输出章节标题
- 引用 [1] 紧贴句号前
- 中文简体，150 字内

- **只要知识片段里有相关内容，就必须回答，不要拒答**

**直接输出最终答案，不要复述问题，不要分析过程。**

【回答】"""

PROMPT_BOUNDARY = """你是企业业务助手。用户问的是【AI 能做什么/不能做什么/权限边界】。
从知识片段中提取【处理边界/AI 与人工边界】相关内容回答。

【知识片段】
{context}

【用户问题】
{question}

【输出格式】
1. AI 可做：...
2. AI 不可做：...

【硬性约束】
- 只讲边界，禁止给操作步骤
- 禁止输出章节标题
- 引用 [1] 紧贴句号前
- 中文简体，120 字内

- **只要知识片段里有相关内容，就必须回答，不要拒答**

**直接输出最终答案，不要复述问题，不要分析过程。**

【回答】"""

PROMPT_NOTICE = """你是企业业务助手。用户问的是【注意事项/安全】。
从知识片段中提取【注意事项/通用规则】相关内容回答。

【知识片段】
{context}

【用户问题】
{question}

【输出格式】
1. 第1条
2. 第2条

【硬性约束】
- 只讲注意事项，禁止给完整排查步骤
- 禁止输出章节标题
- 引用 [1] 紧贴句号前
- 中文简体，120 字内

- **只要知识片段里有相关内容，就必须回答，不要拒答**

**直接输出最终答案，不要复述问题，不要分析过程。**

【回答】"""

PROMPT_SCOPE = """你是企业业务助手。用户问的是【适用范围/哪些批次/哪些设备】。
从知识片段中提取【适用范围】相关内容回答。

【知识片段】
{context}

【用户问题】
{question}

【输出格式】
1. 适用：...

【硬性约束】
- 只讲范围，不要给步骤
- 禁止输出章节标题
- 引用 [1] 紧贴句号前
- 中文简体，100 字内

- **只要知识片段里有相关内容，就必须回答，不要拒答**

**直接输出最终答案，不要复述问题，不要分析过程。**

【回答】"""

PROMPT_GENERIC = """你是企业业务助手。根据知识片段回答用户问题。

【知识片段】
{context}

【用户问题】
{question}

【输出格式】
1. 第1条
2. 第2条
3. 第3条

【硬性约束】
- 每行数字+英文句点开头
- 禁止输出章节标题
- 引用 [1] 紧贴句号前
- 中文简体，120 字内

- **只要知识片段里有相关内容，就必须回答，不要拒答**

**直接输出最终答案，不要复述问题，不要分析过程。**

【回答】"""


_PROMPT_MAP = {
    "step":       PROMPT_STEP,
    "cause":      PROMPT_CAUSE,
    "phenomenon": PROMPT_PHENOMENON,
    "boundary":   PROMPT_BOUNDARY,
    "notice":     PROMPT_NOTICE,
    "scope":      PROMPT_SCOPE,
    "generic":    PROMPT_GENERIC,
}


def format_context(chunks: List[Dict], max_chunks: int = 5) -> str:
    lines = []
    seen_keys = set()
    for c in chunks:
        if len(lines) >= max_chunks:
            break
        text = (c.get("text") or "").strip().replace("\n", " ")
        text = _strip_all_headings(text)
        # 去重键：用前 100 字（原 60 太短，不同 chunk 会被误判重复）
        key = text[:100]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        i = len(lines) + 1
        lines.append(f"[{i}] {text[:280]}")
    return "\n".join(lines)


def _fallback_from_chunks(chunks: List[Dict]) -> str:
    """兜底：优先找步骤，其次拼前几条不重复的。"""
    if not chunks:
        return "暂无相关依据，建议转人工。"

    # 优先选含步骤关键词的
    for c in chunks:
        text = (c.get("text") or "").strip().replace("\n", " ")
        text = _strip_all_headings(text)
        if any(kw in text for kw in ("排查", "步骤", "检查", "操作")):
            return f"{text[:200].rstrip()} [1]"

    # 否则：拼接前 2 条不重复的内容
    seen = set()
    parts = []
    for c in chunks:
        text = (c.get("text") or "").strip().replace("\n", " ")
        text = _strip_all_headings(text)
        key = text[:40]
        if key in seen:
            continue
        seen.add(key)
        if text:
            parts.append(text[:150].rstrip())
        if len(parts) >= 2:
            break

    if parts:
        return "  ".join(parts) + " [1]"
    return "暂无相关依据，建议转人工。"


def generate_node(state: AgentState) -> AgentState:
    chunks = state.get("retrieved", []) or []
    question = state.get("user_input", "")
    trace = state.get("trace_id", "?")[:8]

    if not chunks or state.get("flow_status") == "rejected":
        return {
            **state,
            "answer": "抱歉，知识库中没有找到与您问题相关的内容。请补充更多信息，或回复「转人工」联系客服。",
            "citations": [],
            "flow_status": "rejected",
        }

    # 分类 + 过滤
    qtype = _classify_question(question)
    filtered = _filter_chunks(chunks, qtype)

    # ⚠️ 章节标题过滤是**精度增强，不是硬门槛**。
    #
    # 它按小标题匹配问题类型（原因/现象/步骤…），只在文档**恰好**有这类
    # 小标题时有效。一旦文档没有（例如一份操作手册根本不会写「步骤」二字），
    # 过滤就必然为空 —— 于是**检索明明命中了也照样拒答**。
    #
    # 真实故障：retrieval 打出 `top=0.7185 passed=True`（15 条命中），
    # 用户收到的却是「暂无相关依据。您问的这类内容知识库里没有记录。」
    #
    # 改成：过滤为空时**回退用全部片段**，把"该不该答"交给 prompt 判断 ——
    # prompt 里本来就写了「如果片段里没有相关内容，直接回复：暂无相关依据」，
    # 模型有能力再拒一次。这里不该抢在它前面一刀切。
    if not filtered and qtype not in ("generic",):
        print(f"[GEN:{trace}] 标题过滤为空 (qtype={qtype})，回退使用全部片段 "
              f"chunks={len(chunks)}")
        filtered = chunks

    # qtype=generic 或 filter 成功
    if not filtered:
        print(f"[GEN:{trace}] qtype=generic, chunks={len(chunks)}")
        filtered = list(chunks)
        actual_type = "generic"
    else:
        actual_type = qtype

    print(f"[GEN:{trace}] qtype={qtype}, actual_type={actual_type}, filtered={len(filtered)}")
    context = format_context(filtered, max_chunks=5)
    prompt = _PROMPT_MAP.get(actual_type, PROMPT_GENERIC).format(
        context=context, question=question
    )

    answer = ""
    try:
        resp = get_llm().invoke(prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        answer = clean_answer(raw)
    except Exception as e:
        answer = f"生成失败：{e}"

    if _is_refusal(answer):
        print(f"[GEN:{trace}] refusal detected, raw={answer[:120]!r}")
        fallback = _fallback_from_chunks(filtered)
        print(f"[GEN:{trace}] fallback result={fallback[:120]!r}")
        answer = clean_answer(fallback)

    answer = _replace_citation_numbers(answer, filtered)

    citations = [
        {"index": i + 1, "chunk_id": c["chunk_id"], "source": c["metadata"].get("source", "")}
        for i, c in enumerate(filtered[:3])
    ]

    messages = state.get("messages", []) or []
    messages = messages + [{"role": "assistant", "content": answer}]

    return {
        **state,
        "answer": answer,
        "citations": citations,
        "messages": messages,
        "flow_status": "succeeded",
    }

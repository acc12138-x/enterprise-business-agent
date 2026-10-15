"""generate 节点的**标题过滤回退**测试。

真实故障
用户问「XY200 报 E102 故障码到底该怎么处理啊请问」，结果是：

    检索日志：[RAG:eead853e] attempt=1 ... hits=15 top=0.7185 passed=True
    用户收到：暂无相关依据。您问的这类内容知识库里没有记录。

**检索明明通过了（0.7185 远超 0.55 阈值、命中 15 条），却仍然拒答。**

根因
generate 节点在检索之后又设了一道门槛 `_filter_chunks`：
它按问题类型（原因 / 现象 / 步骤 …）去匹配 chunk 的
`metadata.heading_path`，只留下小标题里含对应关键词的片段。
而绝大多数文档**不会**恰好写上「步骤」「原因」这种小标题 ——
于是过滤结果恒为空，代码就当成"库里没有这类内容"直接拒答。

本质是**双重门槛**：检索层已经用「相似度阈值 + 实体词覆盖」严格判过一次，
下游不该再用一把更严的尺子抢在模型前面一刀切。
prompt 里本来就写了「如果片段里没有相关内容，直接回复：暂无相关依据」，
模型有能力再拒一次。

修复
标题过滤为空时**回退使用全部片段**（打日志说明），把"该不该答"交回 prompt。
"""
import pytest


@pytest.fixture(scope="module")
def gen():
    from app.workflows.nodes import generate
    return generate


CHUNKS_NO_MATCHING_HEADING = [
    {
        "chunk_id": "c1",
        "text": "XY200 报 E102 表示电源模块欠压，检查输入电压是否低于 180V。",
        "metadata": {"source": "操作手册.md", "heading_path": "5.2 怎么操作一个工单"},
        "rerank_score": 0.72,
    },
    {
        "chunk_id": "c2",
        "text": "E102 的排查顺序：先测输入电压，再测电源板输出。",
        "metadata": {"source": "操作手册.md", "heading_path": "11.2 创建工单后工程师没收到通知"},
        "rerank_score": 0.66,
    },
]


class TestFilterChunksTrigger:
    """先证明"触发条件确实存在" —— 这是那次故障的引信。"""

    def test_filter_returns_empty_when_heading_lacks_keyword(self, gen):
        """小标题里没有「原因/步骤」这类词时，过滤结果为空。"""
        out = gen._filter_chunks(CHUNKS_NO_MATCHING_HEADING, "cause")
        assert out == [], "这个前置条件不成立的话，下面的回归测试就没有意义"

    def test_filter_returns_all_for_generic(self, gen):
        out = gen._filter_chunks(CHUNKS_NO_MATCHING_HEADING, "generic")
        assert len(out) == len(CHUNKS_NO_MATCHING_HEADING)

    def test_filter_keeps_matching_chunk(self, gen):
        """小标题命中时应该保留 —— 过滤本身不是没用的，只是不该当硬门槛。"""
        chunks = [{
            "chunk_id": "c9", "text": "E102 的原因：输入欠压。",
            "metadata": {"heading_path": "故障原因说明"},
            "rerank_score": 0.8,
        }]
        assert len(gen._filter_chunks(chunks, "cause")) == 1


class _FakeLLM:
    """替身：记录收到的 prompt，并返回固定答案（不真的调模型）。"""

    def __init__(self):
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)

        class _R:
            content = "E102 表示电源模块欠压，请先检查输入电压。"
        return _R()


class TestGenerateFallsBackInsteadOfRejecting:
    """★ 回归重点：不能再出现"检索通过但被标题过滤拒答"。"""

    def _state(self, qtype_question):
        return {
            "thread_id": "pytest-gen-fallback",
            "trace_id": "pytest01",
            "user_input": qtype_question,
            "retrieved": CHUNKS_NO_MATCHING_HEADING,
            "confidence": 0.7185,
            "flow_status": "succeeded",
        }

    def test_does_not_reject_when_heading_filter_is_empty(self, gen, monkeypatch):
        """构造「检索命中但标题不匹配」的场景，断言不再拒答。"""
        fake = _FakeLLM()
        monkeypatch.setattr(gen, "get_llm", lambda: fake)

        # 强制分类到一个非 generic 的类型，复现原来的触发路径
        monkeypatch.setattr(gen, "_classify_question", lambda q: "cause")

        out = gen.generate_node(self._state("XY200 报 E102 故障码该怎么处理"))

        answer = out.get("answer") or ""
        assert "知识库里没有记录" not in answer, f"仍然被标题过滤拒答：{answer}"
        assert out.get("flow_status") != "rejected", "不该标成 rejected"
        # 必须真的把片段喂给了模型，而不是在门口拦掉
        assert fake.prompts, "没有调用模型 —— 说明还是被提前拦住了"
        assert "E102" in fake.prompts[0], "检索到的片段没进 prompt"

    def test_still_rejects_when_no_chunks_at_all(self, gen, monkeypatch):
        """真正的"没检索到"仍然要拒答 —— 回退不等于取消防线。"""
        monkeypatch.setattr(gen, "get_llm", lambda: _FakeLLM())
        st = self._state("任何问题")
        st["retrieved"] = []
        out = gen.generate_node(st)
        assert out.get("flow_status") == "rejected"
        assert "没有找到" in (out.get("answer") or "")

    def test_still_rejects_when_flow_already_rejected(self, gen, monkeypatch):
        """检索层判过 rejected 的，不该被下游"救活"。"""
        monkeypatch.setattr(gen, "get_llm", lambda: _FakeLLM())
        st = self._state("任何问题")
        st["flow_status"] = "rejected"
        out = gen.generate_node(st)
        assert out.get("flow_status") == "rejected"


class TestCitationLabel:
    """引用标注要显示**文件名**，不是入库时生成的内部编号。

    真实反馈：答案是「...检查是否伴随其他联动报警[doc-ac8hmk]」——
    用户既看不懂 doc-ac8hmk 是什么，也没法拿它去核对出处。
    """

    def test_prefers_source_over_doc_id(self, gen):
        meta = {"doc_id": "doc-ac8hmk", "source": "操作手册.md"}
        assert gen._citation_label(meta, "1") == "操作手册.md"

    def test_strips_directory(self, gen):
        meta = {"doc_id": "doc-x", "source": "docs/15-操作手册.md"}
        assert gen._citation_label(meta, "1") == "15-操作手册.md"

    def test_falls_back_to_doc_id_when_no_source(self, gen):
        assert gen._citation_label({"doc_id": "doc-ac8hmk"}, "1") == "doc-ac8hmk"

    def test_falls_back_to_index_when_nothing(self, gen):
        assert gen._citation_label({"source": "", "doc_id": ""}, "7") == "7"

    def test_end_to_end_replacement(self, gen):
        chunks = [
            {"chunk_id": "a", "metadata": {"doc_id": "doc-1", "source": "操作手册.md"}},
            {"chunk_id": "b", "metadata": {"doc_id": "doc-2", "source": "故障码手册.pdf"}},
        ]
        out = gen._replace_citation_numbers("先检查电压[1]，再复位[2]。", chunks)
        assert out == "先检查电压[操作手册.md]，再复位[故障码手册.pdf]。"
        assert "doc-1" not in out and "doc-2" not in out

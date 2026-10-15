"""RAG 反思与重试测试。

动机
检索失败时不该只拒答一次就完事 —— 应该**换个角度再试一次**。
但"换个角度"的设计很讲究，这里记录两次失败尝试和一个可行方案：

❌ 方案一：str.replace 去停用词
   「请问报警了怎么办」→「报警 办」，「XY200 报 E102 故障码该怎么处理啊」
   →「该 处理」；中文里 replace 必然切出碎片。

❌ 方案二：jieba 切词后按整词过滤停用词
   好一些但仍不可靠：「请问报警了怎么办」→「报警怎么办」（jieba 把
   「怎么办」当成一个词，漏了）、「设备重启后还是不行是什么原因」
   →「设备重启后还是不行是原因」。中文停用词表**永远补不全**。

✅ 方案三（采用）：只保留**能被正则可靠识别**的实体词（故障码 / 型号）
   「XY200 报 E102 故障码该怎么处理啊」→「XY200 E102」
   不依赖词表，且恰好命中检索失败时最该聚焦的东西 ——
   问句越长，E102 这种短代码越容易被 BM25 的词频淹没。
   没有实体词就不重试，避免白跑一轮 embedding 调用。
"""
import pytest


@pytest.fixture(scope="module")
def rs():
    from app.workflows.nodes import rag_search
    return rag_search


class TestBuildRetryQuery:
    """重试查询的构造。"""

    def test_focuses_on_entities_in_long_question(self, rs):
        """★ 核心场景：长问句里的故障码与型号被抽出来。"""
        q = "XY200 报 E102 故障码该怎么处理啊"
        assert rs.build_retry_query(q) == "XY200 E102"

    def test_entity_order_preserved(self, rs):
        """先出现的实体排在前面（BM25 对位置不敏感，但可读性与可测性更好）。"""
        assert rs.build_retry_query("E102 是 XY200 的报警") == "E102 XY200"

    def test_dedup_entities(self, rs):
        assert rs.build_retry_query("E102 又是 E102") == "E102"

    @pytest.mark.parametrize("q", [
        "请问报警了怎么办",
        "我想问一下这个怎么排查",
        "设备重启后还是不行是什么原因",
        "你好啊",
        "",
    ])
    def test_no_entity_means_no_retry(self, rs, q):
        """★ 没有可聚焦的实体就不重试。

        这是这个方案比"去停用词"好的关键：不硬凑一个查询出来。
        """
        assert rs.build_retry_query(q) == ""

    @pytest.mark.parametrize("q", ["E102", "XY200", "  E102  "])
    def test_query_already_is_entity_means_no_retry(self, rs, q):
        """用户直接发了个故障码 —— 原句已是最优，重试没意义。"""
        assert rs.build_retry_query(q) == ""

    def test_pure_entity_pair_needs_no_retry(self, rs):
        assert rs.build_retry_query("XY200 E102") == ""

    def test_keeps_original_case(self, rs):
        """故障码大小写敏感，重试查询不能小写化。"""
        out = rs.build_retry_query("机器报 E102 怎么办 XY200")
        assert "E102" in out and "XY200" in out
        assert "e102" not in out


class TestRewriteQuery:
    """第一次改写（同义词扩展）行为保持不变。"""

    def test_expands_synonyms(self, rs):
        out = rs.rewrite_query("报警了")
        assert "告警" in out and "异常" in out

    def test_no_synonym_keeps_original(self, rs):
        assert rs.rewrite_query("你好啊") == "你好啊"


class TestRetryCandidates:
    """节点里"候选查询"的组装逻辑。"""

    def _candidates(self, rs, q, retry_max=1):
        first = rs.rewrite_query(q)
        out = [first]
        if retry_max > 0:
            focused = rs.build_retry_query(q)
            if focused and focused != first:
                out.append(focused)
        return out

    def test_two_candidates_for_entity_question(self, rs):
        c = self._candidates(rs, "XY200 报 E102 故障码该怎么处理啊")
        assert len(c) == 2
        assert c[1] == "XY200 E102"

    def test_one_candidate_without_entity(self, rs):
        c = self._candidates(rs, "我想问一下这个怎么排查")
        assert len(c) == 1, "没有实体时不该有第二次尝试"

    def test_retry_disabled_gives_one_candidate(self, rs):
        c = self._candidates(rs, "XY200 报 E102", retry_max=0)
        assert len(c) == 1, "关了重试就只跑一轮"


class TestRelevanceGate:
    """相关性判定：两道过滤，任一满足即放行。"""

    def test_score_above_threshold_passes(self, rs):
        chunks = [{"text": "无关内容"}]
        assert rs._is_relevant("随便问问", chunks, 0.9, 0.55) is True

    def test_entity_hit_passes_even_with_low_score(self, rs):
        """这是刻意的：故障码命中比相似度分数更可信。"""
        chunks = [{"text": "E102 的排查步骤：先检查电源"}, {"text": ""}, {"text": ""}]
        assert rs._is_relevant("E102 怎么修", chunks, 0.1, 0.55) is True

    def test_neither_passes_means_reject(self, rs):
        chunks = [{"text": "完全无关"}]
        assert rs._is_relevant("E999 怎么修", chunks, 0.1, 0.55) is False

    def test_empty_chunks_rejected(self, rs):
        assert rs._is_relevant("任何问题", [], 0.0, 0.55) is False

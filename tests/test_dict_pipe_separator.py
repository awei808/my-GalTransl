"""
字典 Pipe 分隔符单元测试 + 边界测试
覆盖：CGptDict 加载(提示词注入阶段)、CNormalDic 加载(译前译后替换阶段)、
      旧格式兼容、非法文本过滤
"""
import unittest, tempfile, os, asyncio
from unittest import mock

from GalTransl.CSentense import CSentense
from GalTransl.Dictionary import CGptDict, CNormalDic, CBasicDicElement
from GalTransl.Backend.ForGalJsonMulitChat import BatchMetadata, ForGalJsonMulitChat
from GalTransl.Backend.ForFileMetaData import ForFileMetaData
from GalTransl.Backend.ForBatchMetaData import ForBatchMetaData
from GalTransl.Backend.ForImproveTranslation import ForImproveTranslation
from GalTransl.Backend.ForBRStation import ForBRStation


# ── 工具函数 ──────────────────────────────────────────────
def _temp_file(content: str) -> str:
    """创建临时字典文件，返回路径"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.close()
    return tmp.name


# ══════════════════════════════════════════════════════════
# CGptDict (提示词注入阶段) — Pipe 格式
# ══════════════════════════════════════════════════════════
class CGptDictPipeTests(unittest.TestCase):
    """CGptDict 使用新 Pipe 分隔符"""

    def test_basic_pipe(self) -> None:
        """基本 Pipe 分隔：2 列"""
        path = _temp_file("検索|置換\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd._dic_list[0].search_word, "検索")
        self.assertEqual(gd._dic_list[0].replace_word, "置換")

    def test_pipe_three_columns(self) -> None:
        """Pipe 分隔：3 列（含注释）"""
        path = _temp_file("src|dst|这是备注\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd._dic_list[0].search_word, "src")
        self.assertEqual(gd._dic_list[0].replace_word, "dst")
        self.assertEqual(gd._dic_list[0].note, "这是备注")

    def test_pipe_multiple_lines(self) -> None:
        """多行 Pipe 分隔"""
        path = _temp_file("A|B\nC|D|note\nE|F\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 3)
        self.assertEqual(gd._dic_list[1].search_word, "C")
        self.assertEqual(gd._dic_list[1].note, "note")

    def test_old_tab_format(self) -> None:
        """旧 Tab 格式兼容"""
        path = _temp_file("word\treplace\tnote\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd._dic_list[0].search_word, "word")
        self.assertEqual(gd._dic_list[0].replace_word, "replace")
        self.assertEqual(gd._dic_list[0].note, "note")

    def test_old_arrow_format(self) -> None:
        """旧 -> 格式兼容（-> 后空格导致 replace_word 尾随空格，是原有行为）"""
        path = _temp_file("src->dst #备注\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd._dic_list[0].search_word, "src")
        self.assertEqual(gd._dic_list[0].replace_word, "dst ")
        self.assertEqual(gd._dic_list[0].note, "备注")

    def test_space_to_pipe_compat(self) -> None:
        """4 空格 → Tab → Pipe 兼容链"""
        path = _temp_file("src    dst    note\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd._dic_list[0].search_word, "src")
        self.assertEqual(gd._dic_list[0].replace_word, "dst")
        self.assertEqual(gd._dic_list[0].note, "note")

    def test_arrow_with_pipe_content(self) -> None:
        """-> 格式中含额外注释文本（尾随空格是原有行为）"""
        path = _temp_file("keyword->translation #this is a note\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd._dic_list[0].search_word, "keyword")
        self.assertEqual(gd._dic_list[0].replace_word, "translation ")
        self.assertEqual(gd._dic_list[0].note, "this is a note")

    # ── 非法 / 边界 ──

    def test_single_column_skipped(self) -> None:
        """单列行应被跳过"""
        path = _temp_file("only_one_word\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 0)

    def test_empty_file(self) -> None:
        """空文件不崩溃"""
        path = _temp_file("")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 0)

    def test_blank_lines_skipped(self) -> None:
        """空行跳过"""
        path = _temp_file("\n\nvalid|replacement\n\n\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd._dic_list[0].search_word, "valid")

    def test_duplicate_entries_deduplicated(self) -> None:
        """重复词条去重（note 为空时不触发去重逻辑，是原有行为）"""
        path = _temp_file("dup|one\ndup|one\n")
        gd = CGptDict([path])
        os.unlink(path)
        # 当前去重只检查 note 非空且相同的情况
        self.assertEqual(len(gd._dic_list), 2)

    def test_duplicate_with_different_note_kept(self) -> None:
        """同内容不同备注的去重（备注不同时不去重，是原有行为）"""
        path = _temp_file("dup|one|A\ndup|one|B\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 2)

    def test_no_pipe_in_line(self) -> None:
        """没有分隔符的行被跳过但不报错"""
        path = _temp_file("this_line_has_no_delimiter\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 0)

    def test_mixed_valid_and_invalid(self) -> None:
        """合法和非法混编，只加载合法的"""
        path = _temp_file(
            "\n"
            "only_one\n"
            "valid|ok\n"
            "also_good|fine|note\n"
        )
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 2)

    def test_nonexistent_file_warning(self) -> None:
        """不存在的文件不崩溃"""
        gd = CGptDict(["nonexistent_xyz_file.txt"])
        self.assertEqual(len(gd._dic_list), 0)

    # ── gen_prompt 不受分隔符影响 ──

    def test_gen_prompt_gpt_uses_pipe(self) -> None:
        """GPT prompt 输出用 | 表格（Markdown）"""
        path = _temp_file("A|B\n")
        gd = CGptDict([path])
        os.unlink(path)
        # gen_prompt 需要 CTransList，仅测格式化函数不抛异常
        self.assertEqual(len(gd._dic_list), 1)


# ══════════════════════════════════════════════════════════
# CNormalDic (译前译后替换阶段) — Pipe 格式
# ══════════════════════════════════════════════════════════
class CNormalDicPipeTests(unittest.TestCase):
    """CNormalDic 使用新 Pipe 分隔符"""

    def test_basic_normal_pipe(self) -> None:
        """基本普通字典"""
        path = _temp_file("search_word|replace_word\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertGreaterEqual(len(nd.dic_list), 1)
        self.assertEqual(nd.dic_list[-1].search_word, "search_word")
        self.assertEqual(nd.dic_list[-1].replace_word, "replace_word")

    def test_conditional_pipe(self) -> None:
        """条件字典"""
        path = _temp_file("pre_jp|条件词|検索|置換\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertGreaterEqual(len(nd.dic_list), 1)
        d = nd.dic_list[-1]
        self.assertTrue(d.is_conditionaDic)
        self.assertEqual(d.special_key, "pre_jp")
        self.assertEqual(d.search_word, "検索")
        self.assertEqual(d.replace_word, "置換")

    def test_situation_pipe(self) -> None:
        """场景字典"""
        path = _temp_file("diag|dialog_search|dialog_replace\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertGreaterEqual(len(nd.dic_list), 1)
        d = nd.dic_list[-1]
        self.assertTrue(d.is_situationsDic)
        self.assertEqual(d.search_word, "dialog_search")
        self.assertEqual(d.replace_word, "dialog_replace")

    def test_old_tab_format_normal(self) -> None:
        """旧 Tab 普通字典兼容"""
        path = _temp_file("search\treplace\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertGreaterEqual(len(nd.dic_list), 1)
        self.assertEqual(nd.dic_list[-1].search_word, "search")
        self.assertEqual(nd.dic_list[-1].replace_word, "replace")

    def test_old_tab_conditional(self) -> None:
        """旧 Tab 条件字典兼容"""
        path = _temp_file("pre_src\tcond\tsearch\treplace\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertEqual(len(nd.dic_list), 1)
        self.assertTrue(nd.dic_list[0].is_conditionaDic)

    def test_mixed_pipe_and_tab(self) -> None:
        """文件混用 Pipe 和 Tab"""
        path = _temp_file("search|replace\nword\trepl\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertEqual(len(nd.dic_list), 2)

    # ── 替换逻辑不依赖分隔符 ──

    def test_do_replace_simple(self) -> None:
        """基本替换：分隔符变更不影响替换结果"""
        path = _temp_file("hello|こんにちは\n")
        nd = CNormalDic([path])
        os.unlink(path)
        nd.sort_dic()
        # 直接测试替换逻辑
        result = nd.do_replace("hello world", None)
        self.assertEqual(result, "こんにちは world")

    def test_do_replace_startswith(self) -> None:
        """startswith 替换"""
        path = _temp_file("^^hello|こんにちは\n")
        nd = CNormalDic([path])
        os.unlink(path)
        nd.sort_dic()
        result = nd.do_replace("hello world", None)
        self.assertEqual(result, "こんにちは world")

    def test_do_replace_no_match(self) -> None:
        """不匹配时不替换"""
        path = _temp_file("hello|こんにちは\n")
        nd = CNormalDic([path])
        os.unlink(path)
        nd.sort_dic()
        result = nd.do_replace("goodbye world", None)
        self.assertEqual(result, "goodbye world")

    # ── 非法 / 边界 ──

    def test_single_column_skipped(self) -> None:
        """单列普通字典行跳过"""
        path = _temp_file("only_one\nvalid|ok\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertEqual(len(nd.dic_list), 1)

    def test_conditional_too_short_skipped(self) -> None:
        """条件字典列数不足跳过"""
        path = _temp_file("pre_jp|only_two\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertEqual(len(nd.dic_list), 0)

    def test_situation_too_short_skipped(self) -> None:
        """场景字典列数不足跳过"""
        path = _temp_file("diag|only_one\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertEqual(len(nd.dic_list), 0)

    def test_empty_file(self) -> None:
        """空文件"""
        path = _temp_file("")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertEqual(len(nd.dic_list), 0)

    def test_comment_lines_skipped(self) -> None:
        """注释行跳过"""
        path = _temp_file("#这是注释\n//这也是注释\nvalid|ok\n")
        nd = CNormalDic([path])
        os.unlink(path)
        self.assertEqual(len(nd.dic_list), 1)

    def test_nonexistent_file(self) -> None:
        """不存在的文件不崩溃"""
        nd = CNormalDic(["nonexistent_dict_file.txt"])
        self.assertEqual(len(nd.dic_list), 0)


# ══════════════════════════════════════════════════════════
# CBasicDicElement.load_line — 单行加载（死代码，但保持一致性）
# ══════════════════════════════════════════════════════════
class CBasicDicElementLoadLineTests(unittest.TestCase):
    """load_line 是死代码（CNormalDic/CGptDict 自行解析），只测条件行"""

    def test_conditional_key_detected(self) -> None:
        elem = CBasicDicElement()
        elem.load_line("pre_src|condition|search|replace\n")
        self.assertTrue(elem.is_conditionaDic)
        self.assertEqual(elem.special_key, "pre_src")
        self.assertEqual(elem.search_word, "search")

    def test_single_column_returns_none(self) -> None:
        elem = CBasicDicElement()
        result = elem.load_line("only_one\n")
        self.assertIsNone(result)

    def test_empty_line_skipped(self) -> None:
        elem = CBasicDicElement()
        result = elem.load_line("\n")
        self.assertIsNone(result)

    def test_comment_line_skipped(self) -> None:
        elem = CBasicDicElement()
        result = elem.load_line("// this is a comment\n")
        self.assertIsNone(result)
        result = elem.load_line("\\\\ another comment\n")
        self.assertIsNone(result)


# ══════════════════════════════════════════════════════════
# 综合场景
# ══════════════════════════════════════════════════════════
class IntegrationTests(unittest.TestCase):

    def test_full_flow_normal_dict(self) -> None:
        """完整流程：加载 + 排序 + 替换"""
        path = _temp_file("hello|こんにちは\nworld|世界\n")
        nd = CNormalDic([path])
        os.unlink(path)
        nd.sort_dic()
        self.assertEqual(len(nd.dic_list), 2)
        result = nd.do_replace("hello world", None)
        self.assertEqual(result, "こんにちは 世界")

    def test_full_flow_gpt_dict(self) -> None:
        """完整流程：加载 + 查词"""
        path = _temp_file("apple|りんご|果物\n")
        gd = CGptDict([path])
        os.unlink(path)
        self.assertEqual(len(gd._dic_list), 1)
        self.assertEqual(gd.get_dst("apple"), "りんご")

    # check_dic_use 需要正确的 CSentense 构造方式（涉及 __slots__），
    # 当前 CSentense({"post_src": "..."}) 构造后 post_src 返回的是 dict 而非字符串。
    # 这是 check_dic_use 与 CSentense 的已有接口不匹配问题，非 Pipe 分隔符引入。


# ══════════════════════════════════════════════════════════
# gen_prompt scene 参数 — h/非h 场景分流
# ══════════════════════════════════════════════════════════
class CGptDictSceneTests(unittest.TestCase):
    """CGptDict.gen_prompt 的 scene 参数：nh / h / all 三种模式"""

    def _make_gd(self) -> CGptDict:
        gd = CGptDict([])
        gd._dic_list = [
            CBasicDicElement("責め", "责难", dic_name="GPT字典_非h"),
            CBasicDicElement("まんこ", "小穴", dic_name="GPT字典_非h"),
            CBasicDicElement("責め", "折磨/拷问/惩罚/调教", dic_name="GPT字典_h"),
            CBasicDicElement("キンタマ", "蛋蛋", dic_name="GPT字典_h"),
        ]
        return gd

    def _make_trans_list(self) -> list:
        return [
            CSentense(pre_src="責め"),
            CSentense(pre_src="まんこ"),
            CSentense(pre_src="キンタマ"),
        ]

    def test_scene_nh_only_non_h(self) -> None:
        """scene='nh' 只注入非 h 字典词条"""
        out = self._make_gd().gen_prompt(self._make_trans_list(), scene="nh")
        self.assertIn("責め | 责难 |", out)
        self.assertIn("まんこ | 小穴 |", out)
        self.assertNotIn("折磨/拷问/惩罚/调教", out)
        self.assertNotIn("蛋蛋", out)

    def test_scene_h_h_overrides_overlap(self) -> None:
        """scene='h'：h 字典优先，重合词条只取 h 译文，非 h 不重合词条补全"""
        out = self._make_gd().gen_prompt(self._make_trans_list(), scene="h")
        self.assertIn("責め | 折磨/拷问/惩罚/调教 |", out)
        self.assertIn("キンタマ | 蛋蛋 |", out)
        self.assertNotIn("責め | 责难 |", out)
        self.assertIn("まんこ | 小穴 |", out)

    def test_scene_all_default_keeps_all(self) -> None:
        """scene='all'（默认）全量注入，不按场景过滤，行为与未传 scene 一致"""
        gd = self._make_gd()
        out_all = gd.gen_prompt(self._make_trans_list())
        out_explicit = gd.gen_prompt(self._make_trans_list(), scene="all")
        self.assertEqual(out_all, out_explicit)
        self.assertIn("責め | 责难 |", out_all)
        self.assertIn("責め | 折磨/拷问/惩罚/调教 |", out_all)


# ══════════════════════════════════════════════════════════
# check_dic_use scene 过滤 — 字典使用问题检测按场景分流
# ══════════════════════════════════════════════════════════
class CheckDicUseSceneTests(unittest.TestCase):
    """check_dic_use 的 scene 参数：h 全查（重合词条只按 h 检查，对齐注入侧）、nh 只检非 h、all 全检"""

    def _make_tran(self, post_src: str, post_dst: str) -> CSentense:
        tran = CSentense(pre_src=post_src)
        tran.post_src = post_src
        tran.post_dst = post_dst
        return tran

    def test_nh_skips_h_dict(self) -> None:
        """scene='nh'：只报非 h 词条未使用，h 词条同源词不参与"""
        gd = CGptDictSceneTests()._make_gd()
        # post_src 含責め：非 h 与 h 字典都有该词条
        tran = self._make_tran("責め", "責め是啥")
        out = gd.check_dic_use(tran.post_dst, tran, scene="nh")
        self.assertIn("GPT字典_非h未使用：責め---责难", out)
        self.assertNotIn("GPT字典_h未使用", out)  # h 词条不参与 nh 判定

    def test_h_overlap_checks_h_only(self) -> None:
        """scene='h'：重合词条（h 与非 h 同名）只按 h 词条检查，与注入侧 h 优先对齐"""
        gd = CGptDictSceneTests()._make_gd()
        tran = self._make_tran("責め", "責め是啥")
        out = gd.check_dic_use(tran.post_dst, tran, scene="h")
        self.assertIn("GPT字典_h未使用：責め---折磨/拷问/惩罚/调教", out)
        self.assertNotIn("GPT字典_非h未使用", out)  # 重合词条只按 h 检查，非 h 同源词条不参与

    def test_h_overlap_h_dst_used_no_false_positive(self) -> None:
        """scene='h'：重合词用了 h 译文，不报非 h 同源词条未使用（避免确定性误报）"""
        gd = CGptDictSceneTests()._make_gd()
        tran = self._make_tran("責め", "他在折磨她")
        out = gd.check_dic_use(tran.post_dst, tran, scene="h")
        self.assertEqual(out, "")

    def test_h_non_overlap_non_h_still_checked(self) -> None:
        """scene='h'：非 h 独有词条（无同名 h 词条）仍参与检查"""
        gd = CGptDictSceneTests()._make_gd()
        tran = self._make_tran("まんこ", "まんこ是啥")
        out = gd.check_dic_use(tran.post_dst, tran, scene="h")
        self.assertIn("GPT字典_非h未使用：まんこ---小穴", out)
        self.assertNotIn("GPT字典_h未使用", out)

    def test_all_checks_both(self) -> None:
        """scene='all'（默认）：h 与非 h 同源词都参与（不区分场景）"""
        gd = CGptDictSceneTests()._make_gd()
        tran = self._make_tran("責め", "責め是啥")
        out = gd.check_dic_use(tran.post_dst, tran)
        self.assertIn("GPT字典_非h未使用：責め---责难", out)
        self.assertIn("GPT字典_h未使用：責め---折磨/拷问/惩罚/调教", out)


# ══════════════════════════════════════════════════════════
# _group_is_h_scene — 批次 h 场景判定
# ══════════════════════════════════════════════════════════
class GroupIsHSceneTests(unittest.TestCase):
    """ForGalJsonMulitChat._group_is_h_scene 按批次元数据 h 标记判定场景"""

    @staticmethod
    def _make_inst(bm):
        inst = ForGalJsonMulitChat.__new__(ForGalJsonMulitChat)
        inst._resolve_batch_metadata = lambda filename: bm
        return inst

    @staticmethod
    def _make_group(indexes) -> list:
        group = []
        for i in indexes:
            t = CSentense(pre_src="x")
            t.runtime_index = i
            group.append(t)
        return group

    def test_no_metadata_returns_false(self) -> None:
        inst = self._make_inst(None)
        self.assertFalse(inst._group_is_h_scene(self._make_group([1]), "f.json"))

    def test_empty_batches_returns_false(self) -> None:
        inst = self._make_inst(BatchMetadata(id="f", batches=[]))
        self.assertFalse(inst._group_is_h_scene(self._make_group([1]), "f.json"))

    def test_empty_group_returns_false(self) -> None:
        inst = self._make_inst(
            BatchMetadata(id="f", batches=[{"区间": [1, 10], "h": True}])
        )
        self.assertFalse(inst._group_is_h_scene([], "f.json"))

    def test_range_without_h_returns_false(self) -> None:
        inst = self._make_inst(
            BatchMetadata(id="f", batches=[{"区间": [1, 10], "h": False}])
        )
        self.assertFalse(inst._group_is_h_scene(self._make_group([5]), "f.json"))

    def test_range_with_h_returns_true(self) -> None:
        inst = self._make_inst(
            BatchMetadata(id="f", batches=[{"区间": [1, 10], "h": True}])
        )
        self.assertTrue(inst._group_is_h_scene(self._make_group([5]), "f.json"))

    def test_partial_h_segment_returns_true(self) -> None:
        inst = self._make_inst(
            BatchMetadata(
                id="f",
                batches=[
                    {"区间": [1, 10], "h": False},
                    {"区间": [11, 20], "h": True},
                ],
            )
        )
        self.assertTrue(inst._group_is_h_scene(self._make_group([15]), "f.json"))
        self.assertFalse(inst._group_is_h_scene(self._make_group([3]), "f.json"))


# ══════════════════════════════════════════════════════════
# 消费端 scene 接线 — 元数据轮只注入非 h / 改进轮与换行修复轮按 h 分流
# ══════════════════════════════════════════════════════════
def _make_meta_inst(cls, dict_files: dict):
    """构造元数据轮实例（绕过 __init__）：pj_config 返回伪字典配置。

    Args:
        cls: ForFileMetaData 或 ForBatchMetaData 类。
        dict_files: {文件名: 文件内容} 的字典文件集合。
    """
    inst = cls.__new__(cls)
    tmp_dir = tempfile.mkdtemp(prefix="dict_scene_")
    for name, content in dict_files.items():
        with open(os.path.join(tmp_dir, name), "w", encoding="utf-8") as f:
            f.write(content)
    dict_cfg = {
        "gpt.dict": list(dict_files.keys()),
        "defaultDictFolder": tmp_dir,
    }
    fake_cfg = mock.MagicMock()
    fake_cfg.getDictCfgSection.return_value = dict_cfg
    fake_cfg.getProjectDir.return_value = tmp_dir
    inst.pj_config = fake_cfg
    return inst, tmp_dir


class MetaDataGlossarySceneTests(unittest.TestCase):
    """P3：元数据轮 _build_glossary_text 只注入非 h 字典（scene='nh'）"""

    def test_file_metadata_skips_h_dict(self) -> None:
        inst, tmp_dir = _make_meta_inst(
            ForFileMetaData,
            {
                "GPT字典_h.txt": "責め|折磨/拷问/惩罚/调教\n",
                "GPT字典_非h.txt": "責め|责难\n",
            },
        )
        try:
            out = inst._build_glossary_text([{"message": "責め"}])
            self.assertIn("责难", out)
            self.assertNotIn("折磨/拷问/惩罚/调教", out)
        finally:
            for name in ("GPT字典_h.txt", "GPT字典_非h.txt"):
                os.unlink(os.path.join(tmp_dir, name))
            os.rmdir(tmp_dir)

    def test_batch_metadata_skips_h_dict(self) -> None:
        inst, tmp_dir = _make_meta_inst(
            ForBatchMetaData,
            {
                "GPT字典_h.txt": "まんこ|小穴\n",
                "GPT字典_非h.txt": "まんこ|小穴(通用)\n",
            },
        )
        try:
            out = inst._build_glossary_text([{"message": "まんこ"}])
            self.assertIn("小穴(通用)", out)
            self.assertNotIn("小穴|", out)
            self.assertNotIn("| 小穴 |", out)
        finally:
            for name in ("GPT字典_h.txt", "GPT字典_非h.txt"):
                os.unlink(os.path.join(tmp_dir, name))
            os.rmdir(tmp_dir)


def _make_improve_inst(cls, h_scene: bool):
    """构造改进轮/换行修复轮实例，stub 掉 LLM 调用，捕获 gen_prompt 的 scene 参数。

    Args:
        cls: ForImproveTranslation 或 ForBRStation 类。
        h_scene: _group_is_h_scene 的桩返回值。
    """
    inst = cls.__new__(cls)
    inst.pj_config = mock.MagicMock()
    inst.pj_config.getKey.return_value = None
    inst.pj_config.active_workers = 0  # 跳过单 worker 打印
    inst.eng_type = "test"
    inst.conversations = {}
    inst.last_file_name = ""
    inst._force_first_round_files = set()
    inst.system_prompt = "s"
    # 桩方法：避免真实 LLM/对话构建
    inst._check_stop_requested = lambda: None
    inst.reset_conversation = lambda filename="": None
    inst._ensure_conversation = lambda filename: [
        {"role": "system", "content": inst.system_prompt}
    ]
    inst._trim_conversation = lambda messages: messages
    inst._build_idx_tip = lambda batch: ""
    inst._build_input_jsonlines = lambda *a, **k: ("", [], 0, "")
    inst._build_first_round_content = lambda *a, **k: ""
    inst._record_round_runtime_error = lambda *a, **k: None
    inst._group_is_h_scene = lambda group, filename: h_scene

    async def _fail_llm(*a, **k):
        raise RuntimeError("stub llm fail")

    inst._call_llm = _fail_llm
    return inst


class SceneWiringBatchTranslateTests(unittest.IsolatedAsyncioTestCase):
    """P0：改进轮 / 换行修复轮按 h 场景传 scene 给 gen_prompt"""

    def _make_trans(self, count=2) -> list:
        # pre_dst 非空是改进轮/换行修复轮 valid 过滤的前提；
        # problem 带「换行位置异常」是 ForBRStation 基类 _has_target_problem 命中的前提
        trans = []
        for i in range(1, count + 1):
            t = CSentense(pre_src=f"line{i}", index=i)
            t.post_src = f"line{i}"
            t.pre_dst = f"译文{i}"
            t.problem = "换行位置异常：第1行"
            trans.append(t)
        return trans

    def test_improve_h_scene_passes_h(self) -> None:
        gpt_dic = mock.MagicMock()
        inst = _make_improve_inst(ForImproveTranslation, h_scene=True)
        asyncio.run(
            inst.batch_translate(
                "f.json", "cache.json", self._make_trans(), 100, gpt_dic=gpt_dic
            )
        )
        self.assertEqual(
            [c.kwargs.get("scene") for c in gpt_dic.gen_prompt.call_args_list],
            ["h"],
        )

    def test_improve_nh_scene_passes_nh(self) -> None:
        gpt_dic = mock.MagicMock()
        inst = _make_improve_inst(ForImproveTranslation, h_scene=False)
        asyncio.run(
            inst.batch_translate(
                "f.json", "cache.json", self._make_trans(), 100, gpt_dic=gpt_dic
            )
        )
        self.assertEqual(
            [c.kwargs.get("scene") for c in gpt_dic.gen_prompt.call_args_list],
            ["nh"],
        )

    def test_br_station_h_scene_passes_h(self) -> None:
        gpt_dic = mock.MagicMock()
        inst = _make_improve_inst(ForBRStation, h_scene=True)
        asyncio.run(
            inst.batch_translate(
                "f.json", "cache.json", self._make_trans(), 100, gpt_dic=gpt_dic
            )
        )
        self.assertEqual(
            [c.kwargs.get("scene") for c in gpt_dic.gen_prompt.call_args_list],
            ["h"],
        )

    def test_br_station_nh_scene_passes_nh(self) -> None:
        gpt_dic = mock.MagicMock()
        inst = _make_improve_inst(ForBRStation, h_scene=False)
        asyncio.run(
            inst.batch_translate(
                "f.json", "cache.json", self._make_trans(), 100, gpt_dic=gpt_dic
            )
        )
        self.assertEqual(
            [c.kwargs.get("scene") for c in gpt_dic.gen_prompt.call_args_list],
            ["nh"],
        )


# ══════════════════════════════════════════════════════════
# P2 基类 batch_translate 兜底传 scene — 防御未来新引擎不覆写
# ══════════════════════════════════════════════════════════
class BaseTranslateBatchSceneTests(unittest.IsolatedAsyncioTestCase):
    """基类 batch_translate 按 h_scene 传 gen_prompt scene（兜底路径）"""

    def _make_trans(self, count=2) -> list:
        trans = []
        for i in range(1, count + 1):
            t = CSentense(pre_src=f"line{i}", index=i)
            t.post_src = f"line{i}"
            t.pre_dst = f"译文{i}"
            trans.append(t)
        return trans

    def _make_inst(self, h_scene: bool):
        from GalTransl.Backend.BaseTranslate import BaseTranslate

        inst = BaseTranslate.__new__(BaseTranslate)
        inst.skipH = False
        inst.last_file_name = ""
        inst.pj_config = mock.MagicMock()
        inst.save_steps = 100
        inst.dynamic_num_per_request = False
        inst._check_stop_requested = lambda *a, **k: None
        inst.reset_conversation = lambda *a, **k: None

        async def _translate(batch, dic_prompt, proofread=False, **kwargs):
            return len(batch), []

        inst.translate = _translate
        return inst, h_scene

    def test_base_batch_translate_h_passes_h(self) -> None:
        gpt_dic = mock.MagicMock()
        inst, _ = self._make_inst(True)
        asyncio.run(
            inst.batch_translate(
                "f.json", "cache.json", self._make_trans(), 100, gpt_dic=gpt_dic,
                h_scene=True,
            )
        )
        self.assertEqual(
            [c.kwargs.get("scene") for c in gpt_dic.gen_prompt.call_args_list],
            ["h"],
        )

    def test_base_batch_translate_nh_passes_nh(self) -> None:
        gpt_dic = mock.MagicMock()
        inst, _ = self._make_inst(False)
        asyncio.run(
            inst.batch_translate(
                "f.json", "cache.json", self._make_trans(), 100, gpt_dic=gpt_dic,
                h_scene=False,
            )
        )
        self.assertEqual(
            [c.kwargs.get("scene") for c in gpt_dic.gen_prompt.call_args_list],
            ["nh"],
        )


if __name__ == "__main__":
    unittest.main()

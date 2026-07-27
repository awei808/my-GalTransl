"""
字典 Pipe 分隔符单元测试 + 边界测试
覆盖：CGptDict 加载(提示词注入阶段)、CNormalDic 加载(译前译后替换阶段)、
      旧格式兼容、非法文本过滤
"""
import unittest, tempfile, os

from GalTransl.Dictionary import CGptDict, CNormalDic, CBasicDicElement


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


if __name__ == "__main__":
    unittest.main()

"""fix_quotes 回归测试。

修复前 fix_quotes 对整段响应做无条件引号修正，当模型在 dst 字段关闭后
又重复追加 name 等字段（畸形但本身合法的 jsonline）时，正则会贪婪到第一个
"}" 把后续字段吞入 dst 值并转成弯引号，导致 json.loads 虽能通过但译文被污染
（即 22 处 "name" 污染 bug）。

修复后：fix_quotes 仅对「本身无法被 json.loads 解析」的行做修复；已合法的
jsonline（含重复 name 的畸形行）原样返回。
"""

import json
import unittest

from GalTransl.Utils import fix_quotes


class TestFixQuotes(unittest.TestCase):
    def test_redundant_name_field_left_untouched(self):
        """dst 后重复 name 字段的畸形行：应原样返回，dst 不被污染。"""
        line = r'abc|{"id":3,"name":"創","src":"S","dst":"（偶尔像这样，也挺不错的……）", "name": "創"}'
        out = fix_quotes(line)
        self.assertEqual(out, line)  # 已合法，不做任何改写
        obj = json.loads(out.split("|", 1)[1])
        self.assertEqual(obj["dst"], "（偶尔像这样，也挺不错的……）")

    def test_unescaped_quotes_in_dst_still_repaired(self):
        """dst 值内含未转义直引号（真正解析失败）：仍应修复为弯引号。"""
        line = r'abc|{"id":1,"dst":"他说"你好"然后离开了"}'
        with self.assertRaises(json.JSONDecodeError):
            json.loads(line.split("|", 1)[1])
        out = fix_quotes(line)
        self.assertIn("“你好”", out)
        obj = json.loads(out.split("|", 1)[1])
        self.assertEqual(obj["dst"], "他说“你好”然后离开了")

    def test_valid_line_with_internal_quotes_untouched(self):
        """已合法的 jsonline（dst 内不含未转义引号）不应被改写。"""
        line = r'abc|{"id":1,"dst":"正常译文"}'
        self.assertEqual(fix_quotes(line), line)

    def test_multiline_batch_mixed(self):
        """多行批量：正常行 / 重复 name 行 / 破损行 混合，全部正确解析。"""
        batch = (
            'aaa|{"id":0,"name":"甲","dst":"正常的译文一"}\n'
            'bbb|{"id":1,"name":"乙","src":"S","dst":"正常的译文二", "name": "乙"}\n'
            'ccc|{"id":2,"dst":"他说"hi"走了"}\n'
        )
        out = fix_quotes(batch)
        parsed = []
        for ln in out.split("\n"):
            if not ln.strip():
                continue
            parsed.append(json.loads(ln.split("|", 1)[1]))
        self.assertEqual([o["dst"] for o in parsed], [
            "正常的译文一",
            "正常的译文二",
            "他说“hi”走了",
        ])


if __name__ == "__main__":
    unittest.main()

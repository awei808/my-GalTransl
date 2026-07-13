import unittest

from GalTransl.CSentense import CSentense
from GalTransl.Problem import find_problems


class FakeProblemConfig:
    target_lang = "zh-cn"

    def __init__(self, problem_list=None, threshold=17):
        self._problem_list = problem_list or ["长句丢失换行"]
        self._threshold = threshold

    def getProblemAnalyzeArinashiDict(self):
        return {}

    def getProblemAnalyzeConfig(self, key):
        if key == "problemList":
            from GalTransl.Problem import CProblemType
            return [CProblemType[name] for name in self._problem_list]
        return []

    def getlbSymbol(self):
        return "auto"

    def getAvgSentenceLengthThreshold(self):
        return self._threshold


def rebuild_entries(entries, config):
    trans_list = []
    for e in entries:
        speaker = e.get("name", "")
        if isinstance(speaker, list):
            speaker = "/".join(speaker)
        pre_src = e.get("pre_src", "") or e.get("pre_jp", "")
        post_src = e.get("post_src", "") or e.get("post_jp", "")
        pre_dst = e.get("pre_dst", "") or e.get("pre_zh", "")
        proofread_dst = e.get("proofread_dst", "") or e.get("proofread_zh", "")
        if post_src == "":
            continue
        s = CSentense(pre_src, speaker if speaker else "", e.get("index", 0))
        s.post_src = pre_src
        s.pre_dst = pre_dst
        s.proofread_zh = proofread_dst
        s.post_dst = proofread_dst if proofread_dst else pre_dst
        s.trans_by = e.get("trans_by", "")
        s.proofread_by = e.get("proofread_by", "")
        s.trans_conf = e.get("trans_conf", 0)
        s.doub_content = e.get("doub_content", "")
        s.unknown_proper_noun = e.get("unknown_proper_noun", "")
        s.skip_check = bool(e.get("skip_check", False))
        trans_list.append(s)

    for i, s in enumerate(trans_list):
        if i > 0:
            s.prev_tran = trans_list[i - 1]
        if i < len(trans_list) - 1:
            s.next_tran = trans_list[i + 1]

    if trans_list:
        find_problems(trans_list, config, None)

    idx = 0
    for e in entries:
        post_src_val = e.get("post_src", "") or e.get("post_jp", "")
        if post_src_val == "":
            continue
        if idx < len(trans_list):
            tran = trans_list[idx]
            if tran.problem:
                e["problem"] = tran.problem
            elif "problem" in e:
                del e["problem"]
            e["post_dst_preview"] = tran.post_dst
            idx += 1

    return entries


SRC_WITH_BREAK = "日本語の長い文章です。\nもっと続きますよ。"
DST_LONG_NO_BREAK = "这是一句非常长的中文翻译完全没有换行符来分割整句话。"


class TestCacheSaveRespectsSkipCheck(unittest.TestCase):
    def _make_entry(self, skip_check, problem=""):
        return {
            "index": 0,
            "name": "",
            "pre_src": SRC_WITH_BREAK,
            "post_src": SRC_WITH_BREAK,
            "pre_dst": DST_LONG_NO_BREAK,
            "proofread_dst": DST_LONG_NO_BREAK,
            "skip_check": skip_check,
            "problem": problem,
        }

    def test_skip_check_prevents_rebuild_flagging(self) -> None:
        entry = self._make_entry(skip_check=True, problem="")
        entries = rebuild_entries([entry], FakeProblemConfig())
        self.assertNotIn("problem", entries[0])
        self.assertTrue(entries[0]["skip_check"])

    def test_no_skip_check_still_flagged(self) -> None:
        entry = self._make_entry(skip_check=False, problem="")
        entries = rebuild_entries([entry], FakeProblemConfig())
        self.assertIn("problem", entries[0])
        self.assertIn("长句丢失换行", entries[0]["problem"])

    def test_skip_check_clears_existing_problem(self) -> None:
        entry = self._make_entry(skip_check=True, problem="长句丢失换行")
        entries = rebuild_entries([entry], FakeProblemConfig())
        self.assertNotIn("problem", entries[0])


if __name__ == "__main__":
    unittest.main()

"""回归测试：用户在缓存里改好译文后再启动翻译，`problem` 字段应被清空。

场景：文件翻译完成后，用户在 .json 缓存里手动把一句有问题的 pre_dst 改好，
然后重新启动翻译希望刷新缓存。预期：这次运行结束时快照中该句的 `problem`
字段应消失。
"""

import asyncio
import os
import tempfile
import unittest

import orjson

from GalTransl.Cache import (
    get_transCache_from_json,
    save_transCache_to_json,
)
from GalTransl.ConfigHelper import CProblemType
from GalTransl.CSentense import CSentense


class FakeProblemConfig:
    """最小化的 projectConfig，用于驱动 find_problems。"""

    target_lang = "zh-cn"

    def getProblemAnalyzeArinashiDict(self):
        return {}

    def getProblemAnalyzeConfig(self, key):
        # 注意：find_problems 内部用 `CProblemType.xxx in find_type` 判定，
        # 因此这里必须返回枚举成员，而非裸字符串。
        if key == "problemList":
            return [CProblemType.残留日文]
        return []

    def getlbSymbol(self):
        return "auto"


class CacheProblemRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_problem_cleared_when_user_fixes_pre_dst(self) -> None:
        from GalTransl.Problem import find_problems

        with tempfile.TemporaryDirectory() as cache_dir:
            cache_file_path = os.path.join(cache_dir, "demo.json")

            pre_src = "おはよう"
            post_src = pre_src
            # 用户已经在缓存里把译文改好（无残留日文），但 problem 字段还在
            snapshot = [
                {
                    "index": 0,
                    "name": "",
                    "pre_src": pre_src,
                    "post_src": post_src,
                    "pre_dst": "早上好",
                    "proofread_dst": "",
                    "problem": "残留日文：おはよう",
                    "trans_by": "model",
                    "proofread_by": "",
                    "post_dst_preview": "早上好",
                }
            ]
            with open(cache_file_path, "wb") as f:
                f.write(orjson.dumps(snapshot, option=orjson.OPT_INDENT_2))

            # 构造一个与缓存 key 匹配的 CSentense
            tran = CSentense(pre_src, speaker="", index=0)
            tran.post_src = post_src
            trans_list = [tran]

            hit, unhit = await get_transCache_from_json(
                trans_list,
                cache_file_path,
            )
            self.assertEqual(len(hit), 1)
            self.assertEqual(len(unhit), 0)
            # 缓存命中后 pre_dst 应为用户修好的译文
            self.assertEqual(tran.pre_dst, "早上好")

            # 模拟 postprocess_results 的核心步骤
            config = FakeProblemConfig()
            find_problems(trans_list, config, None)

            # 修好之后 find_problems 应不再产生任何 problem
            self.assertEqual(tran.problem, "")

            await save_transCache_to_json(
                trans_list, cache_file_path, post_save=True
            )

            with open(cache_file_path, "rb") as f:
                refreshed = orjson.loads(f.read())

            # 缓存数组不再包含字段说明项，直接是翻译条目
            self.assertEqual(len(refreshed), 1)
            # 预期：problem 字段应被刷新/移除
            self.assertNotIn(
                "problem",
                refreshed[0],
                msg=f"problem 字段未被刷新: {refreshed[0]}",
            )


    async def test_old_problem_not_accumulated_on_recheck(self) -> None:
        from GalTransl.Problem import find_problems

        # 模拟真实重检链路：从缓存读出带旧 problem 的 tran 对象后再次 find_problems。
        # 旧 problem 来自上一次写回（如"残留日文：おはよう"），若 find_problems 使用
        # += 追加，则会累积；正确行为应以其本次检测结果覆盖，旧值被丢弃。
        pre_src = "おはよう"
        post_src = pre_src
        tran = CSentense(pre_src, speaker="", index=0)
        tran.post_src = post_src
        # pre_dst 仍含日文 -> 本次检测应命中"残留日文"；同时没有其它问题
        tran.pre_dst = "おはよう 早上好"
        tran.proofread_zh = "おはよう 早上好"
        # 关键：先塞入一个"旧缓存 problem"，模拟上一次重检遗留
        tran.problem = "旧缓存残留日文, 丢失换行"
        # 残留日文判定要求 pre_dst 与 post_dst 同时含日文，故这里补齐 post_dst
        tran.post_dst = "おはよう 早上好"

        trans_list = [tran]
        config = FakeProblemConfig()
        find_problems(trans_list, config, None)

        # 旧 problem 不得残留 / 不得与本次结果拼接
        self.assertNotIn("旧缓存残留日文", tran.problem)
        self.assertNotIn("丢失换行", tran.problem)
        # 本次检测出的问题应完整写入（以"残留日文："前缀标识，且整体为单次覆盖结果）
        self.assertTrue(tran.problem.startswith("残留日文："))
        self.assertNotIn(", ", tran.problem)  # 不应出现旧值+新值的拼接痕迹

    async def test_empty_problem_clears_old_value(self) -> None:
        from GalTransl.Problem import find_problems

        # 无问题时也应以 "" 覆盖旧 problem，而非保留
        pre_src = "こんにちは"
        post_src = pre_src
        tran = CSentense(pre_src, speaker="", index=0)
        tran.post_src = post_src
        tran.pre_dst = "你好"  # 无问题
        tran.proofread_zh = "你好"
        tran.problem = "旧缓存残留日文"

        find_problems([tran], FakeProblemConfig(), None)
        self.assertEqual(tran.problem, "")


if __name__ == "__main__":
    unittest.main()

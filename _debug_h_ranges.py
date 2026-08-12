"""临时调试脚本：验证真实翻译流程命名下 _resolve_cache_h_ranges 的行为。用完即删。"""
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

from GalTransl.server import _resolve_cache_h_ranges

tmp = tempfile.mkdtemp()
cache_dir = os.path.join(tmp, "transl_cache")
pass3 = os.path.join(cache_dir, "pass3_cache")
pass2 = os.path.join(cache_dir, "pass2_cache")
os.makedirs(pass3)
os.makedirs(pass2)
with open(os.path.join(pass3, "h.txt.json"), "w", encoding="utf-8") as f:
    json.dump([], f)

# 场景1：真实流程命名 batch = h.txt.batch.json（输入文件 h.txt + .batch.json）
with open(os.path.join(pass2, "h.txt.batch.json"), "w", encoding="utf-8") as f:
    json.dump({"批次": [{"区间": [1, 3], "h": True}]}, f, ensure_ascii=False)
print("真实命名(h.txt.batch.json):", _resolve_cache_h_ranges(tmp, "pass3_cache/h.txt.json"))

# 场景2：测试里用的命名 batch = h.txt.json.batch.json
with open(os.path.join(pass2, "h.txt.json.batch.json"), "w", encoding="utf-8") as f:
    json.dump({"批次": [{"区间": [1, 3], "h": True}]}, f, ensure_ascii=False)
print("测试命名(h.txt.json.batch.json):", _resolve_cache_h_ranges(tmp, "pass3_cache/h.txt.json"))

本文件存储
# 存在但不急着修复的bug

- **多轮对话历史裁剪后破坏 user/assistant 交替结构（连续两条 user）**
  - 位置：`GalTransl/Backend/ForGalJsonMulitChat.py` 的 `_trim_conversation`（1511-1535 行）
  - 产生原因：裁剪逻辑为 `head = messages[:2]`（只留 `[system, u1]`）+ `tail = messages[2:]`（从首轮回复 a1 开始，长度恒为奇数 `2K-1`）+ `tail[-keep:]`（`keep = max_turns*2`，偶数）。`tail[-2m:]` 的起始索引 `(2K-1)-2m` 恒为奇数，而 tail 中奇数索引全是 user → 裁剪结果必以 user 开头，拼回 `[system, u1]` 后形成 u1 紧接另一个 user（如 `s u u a u a`）。同时 a1（首轮回复 = 首批译文）被 `head[:2]` 丢弃，"保留首轮上下文"的意图落空。破坏一旦发生便持续存在（历史变偶数长度后仍从 user 开头截取）。
  - 触发条件：
    1. 配置 `gpt.multiRoundMaxHistory`（后端 `multi_round_max_history`）> 0；**默认 0 不裁剪，且该配置无前端入口，只能手动改项目 config.yaml**；
    2. 同一文件多轮对话完成轮数 K ≥ m+1（m = 保留轮数），即第 m+1 轮完成后开始裁剪（`2K-1 > 2m`）；
    3. 仅影响实验性后端 `ForGalJsonMulitChat`（需手动注册接入，普通用户不触发）。
  - 影响：连续 user 破坏交替结构。OpenAI 兼容 API 通常容忍（不报错）但语义错乱——u1 是含完整提示词的"首轮输入"，其后紧跟无对应回复的 user，模型会误把后续批次当第一轮，翻译质量隐性下降；严格交替的 API（如 Anthropic）会直接报错导致整批失败；首轮译文 a1 丢失。
  - 修复方向（未实施）：`head = messages[:3]` 保留完整首轮 `[system, u1, a1]`，`tail = messages[3:]` 从 u2 开始（偶数长度，截取后从 user 开头，交替结构完整）；并同步调整 `len(messages) <= 3` 的提前返回边界。

# 存在且上线前必须修复的bug
- 任意路径读取、读写文件



# 需修复的技术债务
- 字典界面：rowsToText / rowToText / rowsToStructuredText 逻辑相近但行为不同（normal 走 join、conditional 走重建），未来易不一致
建议：统一收敛为单一序列化入口
---
name: douyin-copywriter
description: >
  根据抖音口播视频的文案结构，为指定产品批量生成不同角度的口播文案。
  当用户提供产品名+卖点+抖音参考链接时触发。
---

# 抖音口播文案批量生成器

## 这是什么？

用户提供：产品名 + 核心卖点 + 抖音参考视频链接 → 输出 3 条不同角度的口播文案。

流程如下：

1. 下载参考视频并提取音频（16kHz 单声道 WAV）
2. 用 Whisper small 本地模型识别音频，输出完整口播文案
3. Codex 分析文案结构：钩子开头、痛点切入、产品引入、卖点展开、信任背书、行动号召
4. Codex 读取 `knowledge_base.json` 获取已有产品信息，注入生成 prompt
5. Codex 生成 3 条不同切入角度的口播文案，每条标注角度标签
6. Codex 更新 `knowledge_base.json`：新产品追加，已有产品合并新卖点

## 快速开始

```bash
pip install openai-whisper
python scripts/generate_scripts.py "https://v.douyin.com/xxx/" --cookies cookies.txt
```

依赖：Python 3.8+ / yt-dlp / FFmpeg / openai-whisper

## Whisper 模型

首次运行自动下载 Whisper small 模型到 `scripts/_whisper_model/`（约 500MB）。后续运行直接使用缓存。

## 转录脚本

```bash
# 单个链接
python scripts/generate_scripts.py "https://v.douyin.com/xxx/"

# 批量链接
python scripts/generate_scripts.py "链接1" "链接2" --cookies cookies.txt

# 输出 JSON 到文件
python scripts/generate_scripts.py "链接1" -o result.json
```

脚本输出 JSON 格式：
```json
[
  {
    "url": "https://v.douyin.com/...",
    "title": "视频标题",
    "transcript": "识别的完整文案...",
    "duration_seconds": 45.2
  }
]
```

## Cookie

自动复用 `douyin-downloader` 的 `_cookies_auto.txt`。如果没有，可通过 `--cookies` 手动指定。

## 知识库

`scripts/knowledge_base.json`：
```json
{
  "products": {
    "产品A": {
      "features": ["卖点1", "卖点2"],
      "updated": "2026-07-25"
    }
  }
}
```

**更新规则**：
- 新产品 → 追加记录
- 已有产品 → 新卖点合并进去（语义去重：相似表述用新覆盖旧）
- 每次更新记录 `updated` 时间戳

**注入规则**：
- 生成时自动读取知识库，将已有产品信息注入 prompt
- 帮助模型理解产品语境，提升生成质量

## Codex 工作流程（重要）

当用户提供产品名 + 卖点 + 抖音链接后，按以下步骤执行：

### Step 1：提取参考文案

```bash
python scripts/generate_scripts.py "链接1" "链接2" ...
```

如果失败，检查 Cookie 是否有效，必要时先运行 douyin-downloader 的 `extract_cookies.js`。

### Step 2：分析文案结构

读取转录文本，分析并输出结构摘要：
- **钩子**（前 5 秒怎么抓注意力）
- **痛点/场景**（怎么引入需求）
- **产品引入**（自然过渡到产品）
- **卖点展开**（如何一一说明卖点）
- **信任背书**（数据、证言、效果展示）
- **行动号召**（结尾怎么促转化）
- **节奏特征**（段落长度、语速感、是否用反问/感叹）

### Step 3：读取知识库

读取 `scripts/knowledge_base.json`，提取已有产品的卖点信息。

### Step 4：批量生成

生成 3 条口播文案，每条指定不同切入角度（如：痛点共鸣型、场景带入型、功效数据型）。

每条文案需要：
- 角度标签
- 目标字数范围（用户指定，默认与参考视频相近）
- 沿用参考视频的结构骨架和节奏
- 融入产品的具体卖点
- 自然的抖音口播语气

### Step 5：更新知识库

追加/合并产品信息到 `knowledge_base.json`，写入磁盘。

### Step 6：展示结果

输出 3 条完整文案，标注角度、字数。如果用户要求调整，回到 Step 4 重新生成。

## 注意事项

- 首次运行 Whisper 需要下载模型（~500MB），耐心等待
- 转录脚本可能需要 1-2 分钟处理每个视频
- 如果音频质量差（背景音乐过大），部分词可能识别不准，但不影响结构分析
- 生成时优先参考最近更新的知识库数据

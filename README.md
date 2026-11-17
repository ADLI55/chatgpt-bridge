# chatgpt-bridge

让 **Claude Code** 能搜索、阅读你以前的 **ChatGPT 聊天记录**，还能直接和 GPT 对话（包括接着某段旧对话继续聊）。

*An MCP server that lets Claude Code search your ChatGPT history and talk to GPT.*

## 能做什么

装好之后，在 Claude Code 里直接这样说就行：

- 「我之前问过 ChatGPT 怎么处理 pandas 的缺失值，帮我找出来，按那个思路改这段代码」
- 「去年 3 月我和 ChatGPT 聊过简历修改，总结一下当时的结论」
- 「把这个方案发给 GPT，听听它的意见，然后你们讨论出一个结论」
- 「接着我在 ChatGPT 里那段关于京都旅行的对话继续聊」

Claude Code 会自动调用下面这些工具：

| 工具 | 作用 | 会联网吗 |
| --- | --- | --- |
| `search_chatgpt_history` | 按关键词搜索历史对话（中英文都行），可按日期、说话人筛选 | 否 |
| `list_chatgpt_conversations` | 按时间浏览对话列表，或按标题筛选 | 否 |
| `read_chatgpt_conversation` | 读取一段完整对话（长对话自动分页）。也接受 `chatgpt.com/c/...` 链接 | 否 |
| `ask_chatgpt` | 通过 OpenAI API 和 GPT 对话；可把一段旧对话作为上下文接着聊，并用 `thread_id` 多轮对话 | **是**，发送到 OpenAI |

## 原理

ChatGPT 没有开放读取聊天记录的 API，所以这里用的是 ChatGPT 官方的**导出数据**功能：
导出的 zip 里有 `conversations.json`，本工具把它解析后建成本地 SQLite 全文索引，
Claude Code 通过 MCP 协议来查询。

```
ChatGPT 导出 zip ──import──▶ ~/.chatgpt-bridge/history.db ◀──MCP── Claude Code
                                                                  │
                                              ask_chatgpt ────────┴──▶ OpenAI API
```

## 安装

需要 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)。

**1. 导出 ChatGPT 数据**

ChatGPT → 头像 → 设置 → 数据控制 → 导出数据。OpenAI 会发邮件给你一个下载链接，下载得到一个 zip。

**2. 安装本工具**

```bash
uv tool install git+https://github.com/ADLI55/chatgpt-bridge
```

**3. 导入聊天记录**

```bash
chatgpt-bridge import ~/Downloads/你的导出文件.zip
```

zip、解压后的文件夹、单个 `conversations.json` 都可以。

**4. 添加到 Claude Code**

只用搜索/阅读功能（不需要 OpenAI key）：

```bash
claude mcp add chatgpt --scope user -- chatgpt-bridge serve
```

还要和 GPT 对话的话，带上 OpenAI API key（在 https://platform.openai.com/api-keys 创建；注意 API 是单独按量计费的，和 ChatGPT Plus 订阅不通用）：

```bash
claude mcp add chatgpt --scope user --env OPENAI_API_KEY=sk-你的key -- chatgpt-bridge serve
```

`--scope user` 表示在你所有项目里都能用。然后在 Claude Code 里输入 `/mcp`，应该能看到 `chatgpt` 已连接。

如果 Claude Code 提示找不到 `chatgpt-bridge` 命令，把它换成完整路径（用 `which chatgpt-bridge` 查看，通常是 `~/.local/bin/chatgpt-bridge`）。

**5. 以后更新聊天记录**

重新导出一次、再 `chatgpt-bridge import` 一次即可。服务器不用重启，下一次查询就会用新数据。

## 配置（环境变量）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `OPENAI_API_KEY` | `ask_chatgpt` 需要 | — |
| `OPENAI_MODEL` | `ask_chatgpt` 默认使用的模型。OpenAI 模型更新很快，建议设成你想用的最新模型 | `gpt-5` |
| `OPENAI_BASE_URL` | 使用代理或兼容 OpenAI 接口的服务时设置 | 官方地址 |
| `CHATGPT_BRIDGE_DB` | 索引文件位置 | `~/.chatgpt-bridge/history.db` |
| `CHATGPT_BRIDGE_CONTEXT_CHARS` | 把旧对话交给 GPT 时最多带多少字符（超出时保留最新的部分） | `60000` |

用 `--env NAME=值` 加到上面的 `claude mcp add` 命令里即可。

## 命令行

```bash
chatgpt-bridge import <导出文件>   # 导入/更新
chatgpt-bridge stats               # 看导入了多少对话
chatgpt-bridge search 缺失值 pandas  # 在终端里直接搜
chatgpt-bridge show <对话id或前缀>    # 打印一整段对话
chatgpt-bridge serve               # 启动 MCP 服务器（Claude Code 会自动调用）
```

## 搜索规则

- 多个关键词之间是「且」：每个词都要出现在同一段对话里（标题或任意消息中，不必在同一条消息里）。
- 用英文双引号搜短语：`"classic temples"`。
- 中文没有空格分词，所以用的是 SQLite FTS5 的 trigram（三字）索引做子串匹配；两个字的词（如「清洗」）会退回逐条扫描，速度依然很快。
- 只索引你看得到的聊天内容。模型的思考过程、自定义指令、系统提示不会被搜索；代码执行和联网搜索的结果默认不显示，读取对话时可用 `include_tools` 打开。
- 被你编辑掉的旧分支不会出现，只保留你在 ChatGPT 界面上最后看到的那条分支。

参考性能：3000 段对话、12 万条消息，导入约 10 秒，每次搜索 0.05–0.3 秒。

## 隐私

- 聊天记录只保存在你本地的 `~/.chatgpt-bridge/history.db`，本工具不会上传它。
- 但 Claude Code 搜到、读到的内容会进入对话上下文，也就是会发送给 Claude 模型处理。
- `ask_chatgpt` 会把你的消息（以及作为上下文的旧对话）发送给 OpenAI。为了支持多轮对话，请求使用 `store=true`，OpenAI 会在其服务器上保存这些回复，保存期限以 OpenAI 的数据政策为准。
- `.gitignore` 已经排除 `*.zip`、`conversations*.json`、`*.db`，避免把自己的记录提交进仓库。

## 局限

- 不是实时同步：ChatGPT 里的新对话要重新导出才能搜到。
- 导出格式由 OpenAI 决定，可能变化。目前支持单个 `conversations.json` 和分片的 `conversations-NNN.json`。
- 图片、文件附件的内容不会被索引，只显示 `[image]` 之类的占位符。
- 索引文件大约是聊天文本大小的 2–3 倍（trigram 索引比较占空间）。

## 开发

```bash
uv sync
uv run pytest
```

测试用的是一个按真实导出结构构造的样例（包含编辑分支、隐藏的思考过程、工具调用、图片、中文内容），不含任何真实聊天记录。

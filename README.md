# chatgpt-bridge

让 **Claude Code** 能搜索、阅读你以前的 **ChatGPT 聊天记录**，还能直接和 GPT 对话（包括接着某段旧对话继续聊）。

*An MCP server that lets Claude Code search your ChatGPT history and talk to GPT.*

## 一键安装

**macOS / Linux**：打开「终端」，粘贴这一行：

```bash
curl -LsSf https://raw.githubusercontent.com/ADLI55/chatgpt-bridge/main/install.sh | sh
```

**Windows**：打开 PowerShell，粘贴这一行：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/ADLI55/chatgpt-bridge/main/install.ps1 | iex"
```

这一行会自动完成：

1. 安装 [uv](https://docs.astral.sh/uv/)（如果还没有）和本工具。不需要 git，也不用自己装 Python。
2. 在「下载」「桌面」「文稿」文件夹里找最新的 ChatGPT 导出文件并导入。找不到的话，会在浏览器里打开 ChatGPT 的导出页面。
3. 问你要不要填 OpenAI API key（用于和 GPT 对话，可以直接回车跳过）。
4. 注册到 Claude Code，所有项目都能用。

需要提前装好 [Claude Code](https://claude.com/claude-code)。装完以后新开一个 Claude Code 会话就能用。

### 唯一需要你手动做的一步：导出 ChatGPT 数据

打开 https://chatgpt.com/#settings/DataControls → 点「导出数据」→ 确认。
OpenAI 会发邮件给你，点邮件里的链接，把 zip 下载到「下载」文件夹。

**下载完就行了，不需要任何命令**：下次 Claude Code 用到聊天记录时，会自动发现并导入新的导出文件。
以后想同步新的聊天，也是一样：重新导出一次、下载，就会自动更新。

## 能做什么

在 Claude Code 里直接这样说：

- 「我之前问过 ChatGPT 怎么处理 pandas 的缺失值，帮我找出来，按那个思路改这段代码」
- 「去年 3 月我和 ChatGPT 聊过简历修改，总结一下当时的结论」
- 「把这个方案发给 GPT，听听它的意见，然后你们讨论出一个结论」
- 「接着我在 ChatGPT 里那段关于京都旅行的对话继续聊」
- 「我把导出文件放在了 D:\备份\chatgpt.zip，导入一下」

Claude Code 会自动调用下面这些工具：

| 工具 | 作用 | 会联网吗 |
| --- | --- | --- |
| `search_chatgpt_history` | 按关键词搜索历史对话（中英文都行），可按日期、说话人筛选 | 否 |
| `list_chatgpt_conversations` | 按时间浏览对话列表，或按标题筛选 | 否 |
| `read_chatgpt_conversation` | 读取一段完整对话（长对话自动分页）。也接受 `chatgpt.com/c/...` 链接 | 否 |
| `import_chatgpt_export` | 导入或更新聊天记录（放在别的位置的导出文件也行） | 否 |
| `ask_chatgpt` | 通过 OpenAI API 和 GPT 对话；可把一段旧对话作为上下文接着聊，并用 `thread_id` 多轮对话 | **是**，发送到 OpenAI |

## 原理

ChatGPT 没有开放读取聊天记录的 API，所以这里用的是 ChatGPT 官方的**导出数据**功能：
导出的 zip 里有 `conversations.json`，本工具把它解析后建成本地 SQLite 全文索引，
Claude Code 通过 MCP 协议来查询。

```
ChatGPT「导出数据」→ 邮件 → zip 下载到「下载」文件夹
                                  │ 自动发现、导入
                                  ▼
                   ~/.chatgpt-bridge/history.db ◀──MCP── Claude Code
                                                           │
                                       ask_chatgpt ────────┴──▶ OpenAI API
```

服务器在每次查询前检查一次（最多每分钟一次）监视的文件夹：如果有比上次导入更新的导出文件
（zip，或被 Safari 自动解压出来的文件夹），就先导入再回答。

## 之后想改设置

重新运行 `setup` 就行，它会覆盖之前的注册：

```bash
chatgpt-bridge setup --api-key sk-你的key --model 你想用的模型
```

| 环境变量 / 参数 | 说明 | 默认 |
| --- | --- | --- |
| `OPENAI_API_KEY` / `--api-key` | `ask_chatgpt` 需要。在 https://platform.openai.com/api-keys 创建；API 按量计费，和 ChatGPT Plus 订阅不通用 | — |
| `OPENAI_MODEL` / `--model` | `ask_chatgpt` 默认使用的模型。OpenAI 模型更新很快，建议设成你想用的最新模型 | `gpt-5` |
| `OPENAI_BASE_URL` | 使用代理或兼容 OpenAI 接口的服务时设置 | 官方地址 |
| `CHATGPT_BRIDGE_WATCH` | 自动导入时监视的文件夹，多个用 `:`（Windows 用 `;`）分隔 | 下载、桌面、文稿 |
| `CHATGPT_BRIDGE_AUTO_IMPORT` | 设成 `0` 关闭自动导入 | 开 |
| `CHATGPT_BRIDGE_DB` / `--db` | 索引文件位置 | `~/.chatgpt-bridge/history.db` |
| `CHATGPT_BRIDGE_CONTEXT_CHARS` | 把旧对话交给 GPT 时最多带多少字符（超出时保留最新的部分） | `60000` |

## 命令行

```bash
chatgpt-bridge setup              # 一步完成：导入最新导出 + 注册到 Claude Code
chatgpt-bridge import [导出文件]   # 手动导入；不写路径就用「下载」等文件夹里最新的
chatgpt-bridge stats              # 看导入了多少对话
chatgpt-bridge search 缺失值 pandas # 在终端里直接搜
chatgpt-bridge show <对话id或前缀>   # 打印一整段对话
chatgpt-bridge serve              # MCP 服务器本体（Claude Code 会自动启动它）
```

## 手动安装（不想用一键脚本的话）

```bash
uv tool install https://github.com/ADLI55/chatgpt-bridge/archive/refs/heads/main.zip
chatgpt-bridge setup
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
- OpenAI API key 由 Claude Code 保存在它自己的配置文件（`~/.claude.json`）里。
- `.gitignore` 已经排除 `*.zip`、`conversations*.json`、`*.db`，避免把自己的记录提交进仓库。

## 局限

- ChatGPT 没有实时同步接口，新的聊天要重新导出才能搜到（下载后自动导入）。
- 导出格式由 OpenAI 决定，可能变化。目前支持单个 `conversations.json` 和分片的 `conversations-NNN.json`。
- 图片、文件附件的内容不会被索引，只显示 `[image]` 之类的占位符。
- 索引文件大约是聊天文本大小的 2–3 倍（trigram 索引比较占空间）。

## 开发

```bash
uv sync
uv run pytest
```

测试用的是一个按真实导出结构构造的样例（包含编辑分支、隐藏的思考过程、工具调用、图片、中文内容），不含任何真实聊天记录。

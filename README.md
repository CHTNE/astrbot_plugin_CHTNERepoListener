# CHTNE 仓库监听器

AstrBot 插件 — 监听 GitHub 仓库的新 commit 并推送到指定群聊。

## 功能

- 启动后监听 `localhost:7770`，接收来自 GitHub Actions 的 POST 请求
- 将新 commit 信息格式化后推送到配置的所有群聊
- 通过 `/recentcommit` 指令查询各仓库最近一条 commit
- commit 记录持久化到本地 `latest_commits.json`，重启不丢失

## 配置

在 AstrBot 插件配置中设置 `groups` 列表，填入需要推送的群 Session ID：

```json
{
  "groups": ["group_session_id_1", "group_session_id_2"]
}
```

## GitHub Actions POST Schema

向 `http://<服务器IP>:7770/` 发送 POST：

```json
{
  "repository": "owner/repo-name",
  "commits": [
    {
      "author": {
        "name": "张三",
        "email": "zhangsan@example.com"
      },
      "timestamp": "2026-06-08T14:30:00+08:00",
      "message": "fix: 修复登录页面的bug",
      "stats": {
        "files_changed": 5,
        "insertions": 120,
        "deletions": 30
      }
    }
  ]
}
```

### GitHub Actions Workflow 示例

```yaml
- name: Notify Bot
  run: |
    curl -X POST http://<your-server>:7770/ \
      -H "Content-Type: application/json" \
      -d '{
        "repository": "${{ github.repository }}",
        "commits": [...]
      }'
```

## 指令

| 指令 | 说明 |
|------|------|
| `/recentcommit` | 查看所有已监听仓库的最近一条 commit |

## 依赖

- `aiohttp`

```bash
pip install aiohttp
```

## 推送消息示例

```
监听到来自仓库 owner/repo-name 的新commit：

提交人：张三（zhangsan@example.com）
提交时间：2026-06-08T14:30:00+08:00
提交信息：fix: 修复登录页面的bug
5 files changed, 120 insertions(+), 30 deletions(-)
```

# Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)

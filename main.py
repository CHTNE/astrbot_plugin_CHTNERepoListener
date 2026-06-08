import asyncio
import json
import os
from aiohttp import web

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

@register("astrbot_plugin_CHTNERepoListener", "CHTNE Chem Club", "CHTNE仓库监听器", "1.0.0")
class ConfigPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._latest_pushes: dict[str, dict] = {}  # repo_name -> latest push

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._data_file = os.path.join(plugin_dir, "latest_pushes.json")

    async def initialize(self):
        """初始化 HTTP 服务器，监听 localhost:7770，等待 GitHub Actions 的 POST 请求。"""
        self._load_pushes()

        app = web.Application()
        app.router.add_post("/", self._handle_webhook)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "localhost", 7770)
        await self._site.start()
        logger.info("GitHub Webhook 监听器已启动: http://localhost:7770")

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """处理来自 GitHub Actions 的 POST 请求（一次 push）。"""
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"无法解析 JSON 请求体: {e}")
            return web.json_response({"error": "Invalid JSON"}, status=400)

        repository = data.get("repository", "unknown")
        commits = data.get("commits", [])

        if not commits:
            return web.json_response({"error": "No commits provided"}, status=400)

        push_info = {
            "repository": repository,
            "pusher": data.get("pusher", {}),
            "timestamp": data.get("timestamp", ""),
            "commits": commits,
        }
        self._latest_pushes[repository] = push_info
        self._save_pushes()

        message_text = self._format_push_message(push_info)
        message_chain = MessageChain().message(message_text)
        groups = self.config.get("groups", [])
        for group_id in groups:
            try:
                await self.context.send_message(group_id, message_chain)
            except Exception as e:
                logger.error(f"向群 {group_id} 发送消息失败: {e}")

        logger.info(f"已处理来自 {repository} 的 push，共 {len(commits)} 条 commit")
        return web.json_response({"status": "ok", "processed": len(commits)})

    def _format_push_message(self, push: dict) -> str:
        """将一次 push 格式化为推送消息。"""
        repository = push.get("repository", "unknown")
        pusher = push.get("pusher", {})
        pusher_name = pusher.get("name", "Unknown")
        pusher_email = pusher.get("email", "")
        timestamp = push.get("timestamp", "")
        commits = push.get("commits", [])

        lines = [
            f"监听到来自仓库 {repository} 的新代码推送：",
            "",
            f"提交人：{pusher_name}（{pusher_email}）",
            f"提交时间：{timestamp}",
            "提交信息：",
        ]
        for c in commits:
            sha = c.get("sha", "???????")[:7]
            msg = c.get("message", "")
            stats = c.get("stats", {})
            fc = stats.get("files_changed", 0)
            ins = stats.get("insertions", 0)
            dels = stats.get("deletions", 0)
            lines.append(f"- {sha}：{msg}（{fc} files changed, {ins} insertions(+), {dels} deletions(-)）")
        return "\n".join(lines)

    def _load_pushes(self):
        """从本地文件加载持久化的 push 记录。"""
        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                self._latest_pushes = json.load(f)
            logger.info(f"已加载 {len(self._latest_pushes)} 条持久化 push 记录")
        except (FileNotFoundError, json.JSONDecodeError):
            self._latest_pushes = {}

    def _save_pushes(self):
        """将 push 记录持久化到本地文件。"""
        try:
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(self._latest_pushes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 push 记录失败: {e}")

    @filter.command("recentpush")
    async def recentpush(self, event: AstrMessageEvent):
        """查看已监听仓库的最近一次 push"""
        if not self._latest_pushes:
            yield event.plain_result("暂无任何仓库的推送记录。")
            return

        for repo, push in self._latest_pushes.items():
            message = self._format_push_message(push)
            yield event.plain_result(message)

    async def terminate(self):
        """关闭 HTTP 服务器。"""
        if self._runner:
            await self._runner.cleanup()
            logger.info("GitHub Webhook 监听器已关闭")

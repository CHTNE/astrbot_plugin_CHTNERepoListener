import asyncio
import json
import os
from aiohttp import web

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
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
        self._latest_commits: dict[str, dict] = {}  # repo_name -> latest commit

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._data_file = os.path.join(plugin_dir, "latest_commits.json")

    async def initialize(self):
        """初始化 HTTP 服务器，监听 localhost:7770，等待 GitHub Actions 的 POST 请求。"""
        self._load_commits()

        app = web.Application()
        app.router.add_post("/", self._handle_webhook)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "localhost", 7770)
        await self._site.start()
        logger.info("GitHub Webhook 监听器已启动: http://localhost:7770")

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """处理来自 GitHub Actions 的 POST 请求。"""
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"无法解析 JSON 请求体: {e}")
            return web.json_response({"error": "Invalid JSON"}, status=400)

        repository = data.get("repository", "unknown")
        commits = data.get("commits", [])

        if not commits:
            return web.json_response({"error": "No commits provided"}, status=400)

        for commit in commits:
            self._latest_commits[repository] = commit
            self._save_commits()
            message = self._format_commit_message(repository, commit)
            groups = self.config.get("groups", [])
            for group_id in groups:
                try:
                    await self.context.send_message(group_id, message)
                except Exception as e:
                    logger.error(f"向群 {group_id} 发送消息失败: {e}")

        logger.info(f"已处理来自 {repository} 的 webhook，共 {len(commits)} 条 commit")
        return web.json_response({"status": "ok", "processed": len(commits)})

    def _format_commit_message(self, repository: str, commit: dict) -> str:
        """将单个 commit 格式化为推送消息。"""
        author = commit.get("author", {})
        author_name = author.get("name", "Unknown")
        author_email = author.get("email", "")
        timestamp = commit.get("timestamp", "")
        commit_msg = commit.get("message", "")
        stats = commit.get("stats", {})
        files_changed = stats.get("files_changed", 0)
        insertions = stats.get("insertions", 0)
        deletions = stats.get("deletions", 0)

        lines = [
            f"监听到来自仓库 {repository} 的新commit：",
            "",
            f"提交人：{author_name}（{author_email}）",
            f"提交时间：{timestamp}",
            f"提交信息：{commit_msg}",
            f"{files_changed} files changed, {insertions} insertions(+), {deletions} deletions(-)",
        ]
        return "\n".join(lines)

    def _load_commits(self):
        """从本地文件加载持久化的 commit 记录。"""
        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                self._latest_commits = json.load(f)
            logger.info(f"已加载 {len(self._latest_commits)} 条持久化 commit 记录")
        except (FileNotFoundError, json.JSONDecodeError):
            self._latest_commits = {}

    def _save_commits(self):
        """将 commit 记录持久化到本地文件。"""
        try:
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(self._latest_commits, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 commit 记录失败: {e}")

    @filter.command("recentcommit")
    async def recentcommit(self, event: AstrMessageEvent):
        """查看已监听仓库的最近一条 commit"""
        if not self._latest_commits:
            yield event.plain_result("暂无任何仓库的 commit 记录。")
            return

        for repo, commit in self._latest_commits.items():
            message = self._format_commit_message(repo, commit)
            yield event.plain_result(message)

    async def terminate(self):
        """关闭 HTTP 服务器。"""
        if self._runner:
            await self._runner.cleanup()
            logger.info("GitHub Webhook 监听器已关闭")

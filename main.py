import asyncio
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

    async def initialize(self):
        """初始化 HTTP 服务器，监听 localhost:7770，等待 GitHub Actions 的 POST 请求。"""
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

    # 注册指令的装饰器。指令名为 helloworld。注册成功后，发送 `/helloworld` 就会触发这个指令，并回复 `你好, {user_name}!`
    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        """这是一个 hello world 指令"""
        user_name = event.get_sender_name()
        message_str = event.message_str
        message_chain = event.get_messages()
        logger.info(message_chain)
        yield event.plain_result(f"Hello, {user_name}, 你发了 {message_str}!")

    async def terminate(self):
        """关闭 HTTP 服务器。"""
        if self._runner:
            await self._runner.cleanup()
            logger.info("GitHub Webhook 监听器已关闭")

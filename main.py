import asyncio
import json
import os
import astrbot.api.message_components as Comp
from datetime import datetime
from collections import defaultdict
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
        self._pushes: list[dict] = []  # 所有 push 记录（按时间排序）
        self._github_bindings: dict[str, str] = {}  # github_username -> sender_id

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._data_file = os.path.join(plugin_dir, "latest_pushes.json")
        self._bindings_file = os.path.join(plugin_dir, "github_bindings.json")

    async def initialize(self):
        """初始化 HTTP 服务器，监听 localhost:7770，等待 GitHub Actions 的 POST 请求。"""
        self._load_pushes()
        self._load_bindings()

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
        self._pushes.append(push_info)
        self._save_pushes()

        message_text = self._format_push_message(push_info)
        groups = self.config.get("groups", [])
        for group_id in groups:
            try:
                await self.context.send_message(group_id, MessageChain(message_text))
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

        # 查找是否有绑定的用户用于 At
        at_str = self._get_at_str_for_github_user(pusher_name)
        pusher_display = f"{pusher_name}（{pusher_email}"

        lines = [
            Comp.Plain(f"监听到来自仓库 {repository} 的新代码推送：\n"),
            Comp.Plain(""),
            Comp.Plain(f"提交人：{pusher_display}"),
            Comp.At(qq=at_str) if at_str else Comp.Plain(""),
            Comp.Plain(f"\n提交时间：{timestamp}"),
            Comp.Plain(f"\n提交信息："),
        ]
        for c in commits:
            sha = c.get("sha", "???????")[:7]
            msg = c.get("message", "")
            stats = c.get("stats", {})
            fc = stats.get("files_changed", 0)
            ins = stats.get("insertions", 0)
            dels = stats.get("deletions", 0)
            lines.append(Comp.Plain(f"\n- {sha}：{msg}（{fc} files changed, {ins} insertions(+), {dels} deletions(-)）"))
        return lines

    def _get_at_str_for_github_user(self, github_username: str) -> str:
        """根据 GitHub 用户名获取 At 字符串。"""
        sender_id = self._github_bindings.get(github_username)
        if sender_id:
            return sender_id
        return ""

    def _load_pushes(self):
        """从本地文件加载持久化的 push 记录。"""
        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                self._pushes = json.load(f)
            logger.info(f"已加载 {len(self._pushes)} 条持久化 push 记录")
        except (FileNotFoundError, json.JSONDecodeError):
            self._pushes = []

    def _save_pushes(self):
        """将 push 记录持久化到本地文件。"""
        try:
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(self._pushes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 push 记录失败: {e}")

    def _load_bindings(self):
        """从本地文件加载 GitHub 用户名绑定。"""
        try:
            with open(self._bindings_file, "r", encoding="utf-8") as f:
                self._github_bindings = json.load(f)
            logger.info(f"已加载 {len(self._github_bindings)} 条 GitHub 绑定")
        except (FileNotFoundError, json.JSONDecodeError):
            self._github_bindings = {}

    def _save_bindings(self):
        """将 GitHub 用户名绑定持久化到本地文件。"""
        try:
            with open(self._bindings_file, "w", encoding="utf-8") as f:
                json.dump(self._github_bindings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 GitHub 绑定失败: {e}")

    @filter.command("recentpush")
    async def recentpush(self, event: AstrMessageEvent):
        """查看已监听仓库的最近一次 push"""
        if not self._pushes:
            yield event.plain_result("暂无任何仓库的推送记录。")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [Comp.Plain(f"最近推送 - 截止{now}\n")]

        # 每个仓库取最新一条 push
        latest_per_repo: dict[str, dict] = {}
        for push in self._pushes:
            repo = push.get("repository", "unknown")
            # 因为是按接收顺序追加的，后出现的覆盖先出现的，即保留最新
            latest_per_repo[repo] = push

        for repo, push in latest_per_repo.items():
            pusher = push.get("pusher", {})
            pusher_name = pusher.get("name", "Unknown")
            at_str = self._get_at_str_for_github_user(pusher_name)
            timestamp = push.get("timestamp", "未知")
            lines.append(Comp.Plain(f"{repo} - 最近推送时间{timestamp} - 提交人：{pusher_name} "))
            lines.append(Comp.At(qq=at_str) if at_str else Comp.Plain(""))
            commits = push.get("commits", [])
            for c in commits:
                sha = c.get("sha", "???????")[:7]
                msg = c.get("message", "")
                stats = c.get("stats", {})
                fc = stats.get("files_changed", 0)
                ins = stats.get("insertions", 0)
                dels = stats.get("deletions", 0)
                lines.append(Comp.Plain(f"\n- {sha}：{msg}（{fc} files changed, {ins} insertions(+), {dels} deletions(-)）"))
            lines.append(Comp.Plain("\n"))

        yield event.chain_result(lines)

    @filter.command("commitnum")
    async def commitnum(self, event: AstrMessageEvent):
        """统计每个用户的总 commit 数排行榜"""
        if not self._pushes:
            yield event.plain_result("暂无任何推送记录。")
            return

        # 按 GitHub 用户名聚合（用户名+邮箱作为唯一标识）
        user_stats: dict[str, dict] = {}  # key=(name, email) -> stats

        for push in self._pushes:
            pusher = push.get("pusher", {})
            name = pusher.get("name", "Unknown")
            email = pusher.get("email", "")
            repo = push.get("repository", "unknown")
            key = (name, email)

            if key not in user_stats:
                user_stats[key] = {
                    "total_commits": 0,
                    "total_insertions": 0,
                    "total_deletions": 0,
                    "repos": defaultdict(lambda: {"commits": 0, "insertions": 0, "deletions": 0}),
                }

            stats = user_stats[key]
            for c in push.get("commits", []):
                c_stats = c.get("stats", {})
                ins = c_stats.get("insertions", 0)
                dels = c_stats.get("deletions", 0)
                stats["total_commits"] += 1
                stats["total_insertions"] += ins
                stats["total_deletions"] += dels
                stats["repos"][repo]["commits"] += 1
                stats["repos"][repo]["insertions"] += ins
                stats["repos"][repo]["deletions"] += dels

        # 按总 commit 数降序排序
        ranked = sorted(user_stats.items(), key=lambda x: x[1]["total_commits"], reverse=True)

        lines = [Comp.Plain("⬆️ commit 排行榜"), Comp.Plain("\n")]
        for rank, ((name, email), stats) in enumerate(ranked, 1):
            at_str = self._get_at_str_for_github_user(name)
            lines.append(Comp.Plain(f"\n{rank}️⃣ {name}（{email}"))
            lines.append(Comp.At(qq=at_str) if at_str else Comp.Plain(""))
            lines.append(Comp.Plain(f"\ncommit总数：{stats['total_commits']}"))
            lines.append(Comp.Plain(f"\n更改行数：{stats['total_insertions']}+ {stats['total_deletions']}-"))

            # 按仓库列出
            for repo, r_stats in stats["repos"].items():
                lines.append(
                    Comp.Plain(f"\n{repo}：{r_stats['commits']} commits，{r_stats['insertions']}+，{r_stats['deletions']}-")
                )
            lines.append(Comp.Plain("\n"))

        yield event.chain_result(lines)

    @filter.command("绑定github")
    async def bind_github(self, event: AstrMessageEvent):
        """绑定 GitHub 用户名：/绑定github XCM42-Orion"""
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("用法：/绑定github <GitHub用户名>\n例如：/绑定github XCM42-Orion")
            return

        github_username = args[1]
        sender_id = event.message_obj.sender.user_id if event.message_obj.sender else event.get_sender_id()

        self._github_bindings[github_username] = sender_id
        self._save_bindings()
        yield event.plain_result(f"✅ 已绑定 GitHub 用户 {github_username} → {sender_id}")

    @filter.command("解绑github")
    async def unbind_github(self, event: AstrMessageEvent):
        """解除 GitHub 用户名绑定：/解绑github XCM42-Orion"""
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("用法：/解绑github <GitHub用户名>\n例如：/解绑github XCM42-Orion")
            return

        github_username = args[1]
        if github_username in self._github_bindings:
            del self._github_bindings[github_username]
            self._save_bindings()
            yield event.plain_result(f"✅ 已解除 GitHub 用户 {github_username} 的绑定")
        else:
            yield event.plain_result(f"⚠️ 未找到 GitHub 用户 {github_username} 的绑定记录")

    async def terminate(self):
        """关闭 HTTP 服务器。"""
        if self._runner:
            await self._runner.cleanup()
            logger.info("GitHub Webhook 监听器已关闭")

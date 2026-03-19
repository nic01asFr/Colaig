import logging
import re
from typing import Optional

from app.bot_msg import AlbertMsg

logger = logging.getLogger(__name__)


class TchapIam:
    """
    Identity and Access Management for Tchap users.

    Primary check: domain-based (user_allowed_domains from config).
    No external dependency required.

    Legacy Grist integration is kept as an optional add-on but never
    blocks startup or basic authorization.
    """

    def __init__(self, config):
        self.config = config
        self._grist_client = None
        self._users_allowed: dict = {}
        self._users_not_allowed: dict = {}

        # Optional Grist integration (legacy)
        grist_server = getattr(config, "grist_api_server", "")
        grist_key = getattr(config, "grist_api_key", "")
        grist_doc = getattr(config, "grist_users_table_id", "")
        grist_table = getattr(config, "grist_users_table_name", "")

        if grist_server and grist_key and grist_doc and grist_table:
            try:
                from app._grist_legacy import AsyncGristDocAPI

                self._grist_client = AsyncGristDocAPI(grist_doc, server=grist_server, api_key=grist_key)
                self._grist_table_name = grist_table
                logger.info("Grist IAM integration enabled (legacy)")
            except ImportError:
                logger.info("Grist legacy module not found, domain-only IAM active")
        else:
            logger.info("Grist not configured, domain-only IAM active")

    @staticmethod
    def domain_from_sender(sender: str) -> Optional[str]:
        """
        Extract email domain from a Tchap Matrix sender ID.

        Sender IDs look like: @john.doe-ministere.gouv.fr1:agent.tchap.gouv.fr
        We extract the domain part between the last '-' and ':'.
        """
        match = re.search(r"(?<=\-)[^\-\:]+[0-9]*(?=\:)", sender)
        if match:
            return match.group(0)
        logger.warning(f"Could not extract domain from sender: {sender}")
        return None

    async def is_user_allowed(self, config, username: str, refresh: bool = False) -> tuple[bool, str]:
        """
        Check if a user is allowed to interact with the bot.

        1. Domain check (always active, from config.user_allowed_domains)
        2. Grist user table check (optional legacy, if configured)

        Returns:
            (is_allowed, error_message)
        """
        allowed_domains = getattr(config, "user_allowed_domains", ["*"])

        # 1. Domain check
        if "*" not in allowed_domains:
            domain = self.domain_from_sender(username)
            if not domain or domain not in allowed_domains:
                return False, AlbertMsg.domain_not_allowed

        # 2. Grist check (optional)
        if self._grist_client:
            if refresh:
                await self._refresh_grist()
            if self._users_allowed and username not in self._users_allowed:
                return False, AlbertMsg.user_not_allowed

        return True, ""

    async def _refresh_grist(self):
        """Refresh user lists from Grist (legacy, optional)."""
        if not self._grist_client:
            return
        try:
            from app._grist_legacy import to_record

            users_table = await self._grist_client.fetch_table(
                self._grist_table_name, filters={"status": ["allowed"]}
            )
            self._users_allowed.clear()
            for record in users_table:
                self._users_allowed[record.tchap_user] = record

            users_table = await self._grist_client.fetch_table(
                self._grist_table_name, filters={"status": ["pending", "forbidden"]}
            )
            self._users_not_allowed.clear()
            for record in users_table:
                self._users_not_allowed[record.tchap_user] = record

            logger.info("Grist user table refreshed")
        except Exception as e:
            logger.warning(f"Grist refresh failed (non-blocking): {e}")

    async def add_pending_user(self, config, username: str) -> bool:
        """Add a pending user (Grist only, no-op if Grist not configured)."""
        if not self._grist_client:
            return False
        try:
            if username in list(self._users_allowed) + list(self._users_not_allowed):
                return False
            record = {
                "tchap_user": username,
                "status": "pending",
                "domain": self.domain_from_sender(username),
                "n_questions": 0,
            }
            await self._grist_client.add_records(self._grist_table_name, [record])
            return True
        except Exception as e:
            logger.warning(f"Failed to add pending user to Grist: {e}")
            return False

    async def increment_user_question(self, username: str, n: int = 1, update_last_activity: bool = True):
        """Increment question counter (Grist only, no-op if not configured)."""
        if not self._grist_client:
            return
        try:
            record = self._users_allowed.get(username)
            if not record:
                return
            from datetime import datetime, timedelta, timezone

            updates = {"n_questions": record.n_questions + n}
            if update_last_activity:
                tz = timezone(timedelta(hours=2))
                updates["last_activity"] = str(datetime.now(tz))
            await self._grist_client.update_records(
                self._grist_table_name, [{"id": record.id, **updates}]
            )
            self._users_allowed[username] = record._replace(**updates)
        except Exception as e:
            logger.warning(f"Failed to increment user question in Grist: {e}")

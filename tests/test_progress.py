"""Tests — messaging/progress.py (Phase 6)"""

import pytest
from unittest.mock import AsyncMock

from colaig.messaging.progress import ProgressReporter, resolve_channel
from colaig.models import ChannelFormat, ConversationType


# =============================================================================
# resolve_channel
# =============================================================================

class TestResolveChannel:
    def test_matrix(self):
        fmt = resolve_channel("matrix")
        assert fmt.supports_markdown is True
        assert fmt.supports_streaming is True
        assert fmt.supports_html is True

    def test_tchap(self):
        fmt = resolve_channel("tchap")
        assert fmt.supports_streaming is True

    def test_telegram(self):
        fmt = resolve_channel("telegram")
        assert fmt.supports_streaming is False
        assert fmt.max_length == 4096
        assert fmt.supports_html is False

    def test_mcp(self):
        fmt = resolve_channel("mcp")
        assert fmt.supports_streaming is True
        assert fmt.reply_style == "json"

    def test_slack(self):
        fmt = resolve_channel("slack")
        assert fmt.supports_streaming is False
        assert fmt.max_length == 4000

    def test_unknown_platform(self):
        fmt = resolve_channel("unknown_platform_xyz")
        # Défaut conservateur
        assert fmt.supports_streaming is False
        assert isinstance(fmt, ChannelFormat)

    def test_case_insensitive(self):
        fmt1 = resolve_channel("Matrix")
        fmt2 = resolve_channel("MATRIX")
        assert fmt1.supports_streaming == fmt2.supports_streaming


# =============================================================================
# ProgressReporter
# =============================================================================

class TestProgressReporter:
    @pytest.fixture
    def mock_messaging(self):
        m = AsyncMock()
        m.send = AsyncMock()
        return m

    @pytest.fixture
    def markdown_format(self):
        return ChannelFormat(supports_markdown=True)

    @pytest.fixture
    def plain_format(self):
        return ChannelFormat(supports_markdown=False)

    @pytest.mark.asyncio
    async def test_report_sends_message(self, mock_messaging, markdown_format):
        reporter = ProgressReporter(mock_messaging, "conv-1", markdown_format)
        await reporter.report("thinking")
        mock_messaging.send.assert_awaited_once()
        sent_text = mock_messaging.send.call_args[0][1]
        assert len(sent_text) > 0

    @pytest.mark.asyncio
    async def test_report_custom_message(self, mock_messaging, markdown_format):
        reporter = ProgressReporter(mock_messaging, "conv-1", markdown_format)
        await reporter.report("thinking", "Message personnalisé")
        sent_text = mock_messaging.send.call_args[0][1]
        assert "Message personnalisé" in sent_text

    @pytest.mark.asyncio
    async def test_report_strips_markdown_for_plain(self, mock_messaging, plain_format):
        reporter = ProgressReporter(mock_messaging, "conv-1", plain_format)
        await reporter.report("thinking", "*Analyse...*")
        sent_text = mock_messaging.send.call_args[0][1]
        assert "*" not in sent_text
        assert "Analyse..." in sent_text

    @pytest.mark.asyncio
    async def test_report_no_messaging_silent(self, markdown_format):
        reporter = ProgressReporter(None, "conv-1", markdown_format)
        # Ne doit pas lever d'exception
        await reporter.report("thinking")
        await reporter.report_tool_use("search")

    @pytest.mark.asyncio
    async def test_report_tool_use_with_detail(self, mock_messaging, markdown_format):
        reporter = ProgressReporter(mock_messaging, "conv-1", markdown_format)
        await reporter.report_tool_use("search", "guide-rh.pdf")
        sent_text = mock_messaging.send.call_args[0][1]
        assert "guide-rh.pdf" in sent_text

    @pytest.mark.asyncio
    async def test_report_tool_use_no_detail(self, mock_messaging, markdown_format):
        reporter = ProgressReporter(mock_messaging, "conv-1", markdown_format)
        await reporter.report_tool_use("search_documents")
        sent_text = mock_messaging.send.call_args[0][1]
        assert "search_documents" in sent_text

    @pytest.mark.asyncio
    async def test_report_max_length_truncated(self, mock_messaging):
        fmt = ChannelFormat(supports_markdown=True, max_length=20)
        reporter = ProgressReporter(mock_messaging, "conv-1", fmt)
        await reporter.report("thinking", "A" * 100)
        sent_text = mock_messaging.send.call_args[0][1]
        assert len(sent_text) <= 20

    @pytest.mark.asyncio
    async def test_report_send_failure_ignored(self, markdown_format):
        messaging = AsyncMock()
        messaging.send = AsyncMock(side_effect=Exception("connexion perdue"))
        reporter = ProgressReporter(messaging, "conv-1", markdown_format)
        # Ne doit pas propager l'exception
        await reporter.report("thinking")

    @pytest.mark.asyncio
    async def test_unknown_phase_uses_default(self, mock_messaging, markdown_format):
        reporter = ProgressReporter(mock_messaging, "conv-1", markdown_format)
        await reporter.report("unknown_phase_xyz")
        mock_messaging.send.assert_awaited_once()

"""
Tests für Phase 82 – /wa_contact Command + Kontakt-Management.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# add_whatsapp_contact Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAddWhatsappContact:

    async def test_add_new_contact(self):
        profile = {}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True) as mock_write,
        ):
            from bot.whatsapp import add_whatsapp_contact
            success, msg = await add_whatsapp_contact("Steffi", "Steffi 🌞")
        assert success is True
        assert "Steffi" in msg
        saved = mock_write.call_args[0][0]
        assert saved["whatsapp_contacts"] == [{"name": "Steffi", "whatsapp_name": "Steffi 🌞"}]

    async def test_add_second_contact(self):
        profile = {"whatsapp_contacts": [{"name": "Steffi", "whatsapp_name": "Steffi 🌞"}]}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True) as mock_write,
        ):
            from bot.whatsapp import add_whatsapp_contact
            success, msg = await add_whatsapp_contact("Amalia", "Amalia")
        assert success is True
        saved = mock_write.call_args[0][0]
        assert len(saved["whatsapp_contacts"]) == 2

    async def test_update_existing_contact(self):
        """Gleicher Name → whatsapp_name wird aktualisiert."""
        profile = {"whatsapp_contacts": [{"name": "Steffi", "whatsapp_name": "Steffi"}]}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True) as mock_write,
        ):
            from bot.whatsapp import add_whatsapp_contact
            success, msg = await add_whatsapp_contact("Steffi", "Steffi 🌞")
        assert success is True
        assert "aktualisiert" in msg.lower()
        saved = mock_write.call_args[0][0]
        assert len(saved["whatsapp_contacts"]) == 1
        assert saved["whatsapp_contacts"][0]["whatsapp_name"] == "Steffi 🌞"

    async def test_case_insensitive_duplicate(self):
        """'steffi' und 'Steffi' sind derselbe Kontakt."""
        profile = {"whatsapp_contacts": [{"name": "Steffi", "whatsapp_name": "Steffi 🌞"}]}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True),
        ):
            from bot.whatsapp import add_whatsapp_contact
            success, msg = await add_whatsapp_contact("steffi", "Steffi Neu")
        assert success is True
        assert "aktualisiert" in msg.lower()

    async def test_empty_name_rejected(self):
        from bot.whatsapp import add_whatsapp_contact
        success, msg = await add_whatsapp_contact("", "Steffi 🌞")
        assert success is False

    async def test_empty_whatsapp_name_rejected(self):
        from bot.whatsapp import add_whatsapp_contact
        success, msg = await add_whatsapp_contact("Steffi", "")
        assert success is False

    async def test_whatsapp_name_with_spaces(self):
        """'Fabio Morena (du)' muss korrekt gespeichert werden."""
        profile = {}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True) as mock_write,
        ):
            from bot.whatsapp import add_whatsapp_contact
            success, _ = await add_whatsapp_contact("Fabio", "Fabio Morena (du)")
        assert success is True
        saved = mock_write.call_args[0][0]
        assert saved["whatsapp_contacts"][0]["whatsapp_name"] == "Fabio Morena (du)"

    async def test_write_error_returns_false(self):
        profile = {}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, side_effect=Exception("disk full")),
        ):
            from bot.whatsapp import add_whatsapp_contact
            success, msg = await add_whatsapp_contact("Steffi", "Steffi 🌞")
        assert success is False
        assert "fehler" in msg.lower()


# ---------------------------------------------------------------------------
# remove_whatsapp_contact Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRemoveWhatsappContact:

    async def test_remove_existing(self):
        profile = {
            "whatsapp_contacts": [
                {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
                {"name": "Amalia", "whatsapp_name": "Amalia"},
            ]
        }
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True) as mock_write,
        ):
            from bot.whatsapp import remove_whatsapp_contact
            success, msg = await remove_whatsapp_contact("Steffi")
        assert success is True
        assert "entfernt" in msg.lower()
        saved = mock_write.call_args[0][0]
        assert len(saved["whatsapp_contacts"]) == 1
        assert saved["whatsapp_contacts"][0]["name"] == "Amalia"

    async def test_remove_case_insensitive(self):
        profile = {"whatsapp_contacts": [{"name": "Steffi", "whatsapp_name": "Steffi 🌞"}]}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True),
        ):
            from bot.whatsapp import remove_whatsapp_contact
            success, _ = await remove_whatsapp_contact("steffi")
        assert success is True

    async def test_remove_not_found(self):
        profile = {"whatsapp_contacts": [{"name": "Amalia", "whatsapp_name": "Amalia"}]}
        with (
            patch("agent.profile.load_profile", return_value=profile),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=True),
        ):
            from bot.whatsapp import remove_whatsapp_contact
            success, msg = await remove_whatsapp_contact("Jonas")
        assert success is False
        assert "nicht gefunden" in msg.lower()

    async def test_remove_empty_name(self):
        from bot.whatsapp import remove_whatsapp_contact
        success, msg = await remove_whatsapp_contact("")
        assert success is False

    async def test_remove_from_empty_list(self):
        profile = {}
        with patch("agent.profile.load_profile", return_value=profile):
            from bot.whatsapp import remove_whatsapp_contact
            success, msg = await remove_whatsapp_contact("Steffi")
        assert success is False


# ---------------------------------------------------------------------------
# list_whatsapp_contacts_formatted Tests
# ---------------------------------------------------------------------------

class TestListWhatsappContactsFormatted:

    def test_empty_list(self):
        with patch("agent.profile.load_profile", return_value={}):
            from bot.whatsapp import list_whatsapp_contacts_formatted
            result = list_whatsapp_contacts_formatted()
        assert "wa_contact" in result.lower() or "keine" in result.lower()

    def test_lists_contacts(self):
        profile = {
            "whatsapp_contacts": [
                {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
                {"name": "Amalia", "whatsapp_name": "Amalia"},
            ]
        }
        with patch("agent.profile.load_profile", return_value=profile):
            from bot.whatsapp import list_whatsapp_contacts_formatted
            result = list_whatsapp_contacts_formatted()
        assert "Steffi" in result
        assert "Amalia" in result
        assert "Steffi 🌞" in result

    def test_count_shown(self):
        profile = {
            "whatsapp_contacts": [
                {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
                {"name": "Amalia", "whatsapp_name": "Amalia"},
                {"name": "Fabio",  "whatsapp_name": "Fabio Morena (du)"},
            ]
        }
        with patch("agent.profile.load_profile", return_value=profile):
            from bot.whatsapp import list_whatsapp_contacts_formatted
            result = list_whatsapp_contacts_formatted()
        assert "3" in result

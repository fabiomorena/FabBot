"""
Tests für git_remote_guard.py – blockiert Tokens in Git-Remote-URLs.

Hintergrund: Am 11.08.2026 lag ein GitHub-PAT im Klartext in der Remote-URL
(.git/config). Da .git/config nicht getrackt ist, greift dort kein
dateibasierter Hook – der Check muss den Repo-Zustand prüfen.
"""

from git_remote_guard import finde_unsichere_remotes


class TestFindeUnsichereRemotes:
    def test_saubere_https_url_ist_ok(self):
        text = "origin\thttps://github.com/fabiomorena/FabBot.git (fetch)"
        assert finde_unsichere_remotes(text) == []

    def test_ssh_scp_syntax_ist_ok(self):
        text = "origin\tgit@github.com:fabiomorena/FabBot.git (fetch)"
        assert finde_unsichere_remotes(text) == []

    def test_ssh_url_mit_git_user_ist_ok(self):
        """ssh://git@host hat keinen Passwort-Teil – kein Fund."""
        text = "origin\tssh://git@github.com/fabiomorena/FabBot.git (fetch)"
        assert finde_unsichere_remotes(text) == []

    def test_user_und_passwort_wird_erkannt(self):
        text = "origin\thttps://fabiomorena:ghp_geheim123@github.com/f/FabBot.git (fetch)"
        treffer = finde_unsichere_remotes(text)
        assert len(treffer) == 1
        assert treffer[0][0] == "origin"

    def test_token_als_username_wird_erkannt(self):
        """https://ghp_xxx@host – gängige Form ohne Doppelpunkt."""
        text = "origin\thttps://ghp_geheim123@github.com/f/FabBot.git (fetch)"
        assert len(finde_unsichere_remotes(text)) == 1

    def test_fine_grained_pat_wird_erkannt(self):
        text = "origin\thttps://github_pat_ABC123@github.com/f/FabBot.git (push)"
        assert len(finde_unsichere_remotes(text)) == 1

    def test_geheimnis_steht_nicht_in_der_ausgabe(self):
        """Der Hook darf das Token nicht selbst ins Terminal schreiben."""
        text = "origin\thttps://user:ghp_streng_geheim@github.com/f/FabBot.git (fetch)"
        treffer = finde_unsichere_remotes(text)
        assert "ghp_streng_geheim" not in treffer[0][1]

    def test_mehrere_remotes_werden_einzeln_gemeldet(self):
        text = (
            "origin\thttps://u:ghp_a@github.com/f/FabBot.git (fetch)\n"
            "upstream\thttps://github.com/anderer/FabBot.git (fetch)\n"
            "backup\thttps://u:ghp_b@gitlab.com/f/FabBot.git (push)"
        )
        namen = [name for name, _ in finde_unsichere_remotes(text)]
        assert namen == ["origin", "backup"]

    def test_leere_ausgabe_ist_ok(self):
        """Repo ohne Remotes – kein Fund, kein Absturz."""
        assert finde_unsichere_remotes("") == []

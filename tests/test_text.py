from freeplane_tmux.text import sanitize_details_text, split_shell_commands


def test_details_html_is_cleaned_through_one_path() -> None:
    raw = "<html><body><p>echo one</p><p>echo &lt;two&gt;<br>echo three</p></body></html>"
    cleaned = sanitize_details_text(raw)

    assert cleaned == "echo one\necho <two>\necho three"
    assert "</p>" not in cleaned
    assert "</body>" not in cleaned
    assert "</html>" not in cleaned
    assert split_shell_commands(raw) == ["echo one", "echo <two>", "echo three"]


def test_plain_shell_redirection_is_not_treated_as_html() -> None:
    command = "cat <<'EOF'\nhello\nEOF"
    assert sanitize_details_text(command) == command

class ConnectorError(Exception):
    """A connector call failed. `code` is a short, safe identifier (never a provider message, a URL, a token or an
    address): invalid_token, auth_failed, unreachable, no_chat, webhook_active, chat_not_found, rate_limited,
    recipient_refused, attachment_too_large, failed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

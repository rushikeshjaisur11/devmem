def slug(text):
    """Lowercase, dash-separated slug."""
    return text.strip().lower().replace(" ", "-")


def _private():
    return 1

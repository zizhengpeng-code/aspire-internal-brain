"""Load a local ``.env`` into the process environment.

The repo reads all configuration from the environment (see ``.env.example``). Copy
``.env.example`` to ``.env``, fill in the keys, and the entry points pick it up. Real
environment variables always win, so ``LLM_API_KEY=... python evaluate.py`` overrides the
file. ``python-dotenv`` is used when installed, with a small built-in parser as a fallback.

    import env
    env.load()          # call once, before reading os.environ

``.env`` is git-ignored; never commit real keys.
"""
import os

DEFAULT = ".env"


def _parse(path):
    """Minimal .env parser: ``KEY=VALUE``, ``#`` comments, optional surrounding quotes."""
    pairs = {}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if key:
                pairs[key] = value
    return pairs


def load(path=None, override=False):
    """Load ``path`` (default ``./.env``, or ``$ENV_FILE``) into ``os.environ``.

    Returns the list of keys it set. Missing files are a no-op, so the repo runs on
    the fake model / local authz backend with no ``.env`` at all.
    """
    path = path or os.environ.get("ENV_FILE") or DEFAULT
    if not os.path.isfile(path):
        return []
    try:
        from dotenv import dotenv_values
        pairs = {k: (v if v is not None else "") for k, v in dotenv_values(path).items()}
    except ImportError:
        pairs = _parse(path)
    set_keys = []
    for key, value in pairs.items():
        if override or key not in os.environ:
            os.environ[key] = value
            set_keys.append(key)
    return set_keys

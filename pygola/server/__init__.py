try:
    from .app import app, create_app, ServerConfig
    __all__ = ["app", "create_app", "ServerConfig"]
except ImportError as exc:
    raise ImportError(
        "pygola server dependencies are not installed.\n"
        "Install them with:  pip install 'pygola[server]'"
    ) from exc

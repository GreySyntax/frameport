"""The desktop app (Flet). The display language is loaded here, before any view module builds its texts, so
module-level texts (navigation, status names) are translated too; changing it takes effect after a restart."""


def _load_language() -> None:
    try:
        from ..core import library
        from ..i18n import set_language

        set_language(library.setting("ui.language"))
    except Exception:  # noqa: BLE001 - English if anything is off
        pass


_load_language()

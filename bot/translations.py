import os
import i18n
import logging

translation_dir = 'i18n'
i18n.load_path.append(translation_dir)
i18n.set('filename_format', '{locale}.{format}')
locales = [os.path.splitext(filename)[0] for filename in os.listdir(translation_dir)]
i18n.set('available_locales', locales)
i18n.set('error_on_missing_translation', True)

# Set default locale:
# i18n.set('locale', 'ru')


def localized_text(key: str, lang: str = None, **kwargs) -> str:
    """
    Return translated text for a key in specified bot_language.
    Keys and translations can be found in the i18n directory.
    """
    if lang is None:
        lang = i18n.get('locale')
    try:
        return i18n.t(key, locale=lang, **kwargs)
    except KeyError:
        logging.warning(f"No english definition found for key '{key}' in locales")
        return key

# Third-party components

FocusLedger's source is MIT licensed. Dependencies retain their own licenses and
are installed separately; this source release does not bundle runtime dependency
code or model weights. The bundled typefaces are listed below. See requirements-lock.txt for exact Python versions and the license files
inside each installed distribution for full notices.

| Component | License / source |
| --- | --- |
| PyObjC frameworks and bridge | [MIT](https://github.com/ronaldoussoren/pyobjc) |
| mss | [MIT](https://github.com/BoboTiG/python-mss) |
| Requests | [Apache 2.0](https://github.com/psf/requests) |
| Jinja2 and MarkupSafe | [BSD 3-Clause](https://github.com/pallets/jinja) |
| urllib3 | [MIT](https://github.com/urllib3/urllib3) |
| charset-normalizer | [MIT](https://github.com/jawah/charset_normalizer) |
| idna | [BSD 3-Clause](https://github.com/kjd/idna) |
| certifi | [MPL 2.0](https://github.com/certifi/python-certifi) |
| Ruff (development only) | [MIT](https://github.com/astral-sh/ruff) |
| Ollama (separately installed) | [MIT](https://github.com/ollama/ollama/blob/main/LICENSE) |
| Gemma 3 models (separate downloads) | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) |

Apple frameworks are supplied by macOS. Microsoft HVE Core inspired the repository
workflow; no HVE framework is installed or vendored by this project. Do not assume
FocusLedger's MIT license applies to downloaded model weights.

## Bundled typefaces

Geist and Instrument Serif are bundled in `assets/fonts` under the SIL Open Font
License 1.1. Their full copyright notices and licenses are included alongside the
font files and embedded in exported reports. Source: the Google Fonts `ofl/geist`
and `ofl/instrumentserif` directories. Native registration is process-local.

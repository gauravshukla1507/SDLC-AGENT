Python conventions: Python 3.11+, `src/<package>/` layout with `tests/` alongside, type hints everywhere, standard
library first, `decimal.Decimal` for money, `pytest` with plain asserts and parametrize for boundaries, no global
state, `ruff`-clean code. Dependencies go in `pyproject.toml` (or `requirements.txt` if the repo already uses it).

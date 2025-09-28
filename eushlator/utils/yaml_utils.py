from pathlib import Path

import yaml


class LiteralDumper(yaml.SafeDumper):
    """Custom dumper:
    - Inherits from SafeDumper (no arbitrary Python objects).
    - Used to force strings to be emitted in literal block style `|` when desired,
      so newlines are preserved exactly.
    """
    pass


def str_presenter(dumper, data: str):
    """
    Custom representer for Python `str` → YAML scalar emission.

    Behaviour:
    - If the string contains real newlines *or* the two-character sequence "\\n",
      we normalize "\\n" → real newlines, trim whitespace at both ends of each line,
      and emit the value as a YAML literal block (`|`) to preserve line breaks.
    - Otherwise (single-line strings), we still emit using literal style `|`.
      (Note: Using `style="|"` for single-line strings is intentional here to keep
      output formatting consistent and avoid quoting/escaping surprises.)
    """
    # if the string has real newlines *or* the literal "\n" sequence
    if "\n" in data or "\\n" in data:
        # normalise any \n escape sequences to real newlines
        literal = data.replace("\\n", "\n")
        # strip whitespace around each physical line
        split_literal = literal.split("\n")
        for i in range(len(split_literal)):
            split_literal[i] = split_literal[i].strip()
        literal = "\n".join(split_literal)
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str",
            literal,
            style="|",  # literal block (preserve newlines verbatim)
        )
    # single-line => emit as a literal block as well for consistency
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


# Register the representer for our custom dumper class ...
yaml.add_representer(str, str_presenter, Dumper=LiteralDumper)
# ... and also for PyYAML's internal SafeRepresenter to cover other safe dump paths.
yaml.representer.SafeRepresenter.add_representer(str, str_presenter)


def load_yaml(path: Path) -> dict:
    """
    Load YAML from `path` and return a dict (empty dict if the file is empty).

    Notes:
    - Uses `yaml.CLoader` for speed (requires libyaml). If unavailable in the
      runtime environment, consider using `yaml.SafeLoader` as a fallback.
    - `errors` are not suppressed; callers should handle exceptions if needed.
    """
    with path.open("r", encoding="utf-8") as f:
        script = yaml.load(f, Loader=yaml.CLoader) or {}
    return script


def save_yaml(path: Path, data: dict, allow_unicode=True, sort_keys=False, width=10_000) -> None:
    """
    Dump `data` to YAML at `path` using the LiteralDumper and custom string representer.

    Parameters:
    - allow_unicode: ensure non-ASCII chars are emitted directly (not escaped).
    - sort_keys: keep insertion order by default (False) for stable, human-friendly diffs.
    - width: a large line width to avoid PyYAML folding/wrapping long lines.
    """
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            data,
            fh,
            allow_unicode=allow_unicode,
            sort_keys=sort_keys,
            width=width,
            Dumper=LiteralDumper,  # use our dumper so strings go through `str_presenter`
        )

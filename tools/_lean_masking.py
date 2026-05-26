from __future__ import annotations


def mask_lean_comments_and_strings(
    text: str,
    *,
    mask_strings: bool = True,
) -> str:
    """Mask Lean comments/strings with spaces while preserving line structure."""
    chars = list(text)
    i = 0
    n = len(chars)
    block_depth = 0
    in_string = False

    while i < n:
      if in_string:
        if chars[i] == "\\" and i + 1 < n:
          if chars[i] != "\n":
            chars[i] = " "
          if chars[i + 1] != "\n":
            chars[i + 1] = " "
          i += 2
          continue
        if chars[i] == '"':
          chars[i] = " "
          in_string = False
        elif chars[i] != "\n":
          chars[i] = " "
        i += 1
        continue

      if block_depth > 0:
        if i + 1 < n and text[i : i + 2] == "/-":
          chars[i] = " "
          chars[i + 1] = " "
          block_depth += 1
          i += 2
          continue
        if i + 1 < n and text[i : i + 2] == "-/":
          chars[i] = " "
          chars[i + 1] = " "
          block_depth -= 1
          i += 2
          continue
        if chars[i] != "\n":
          chars[i] = " "
        i += 1
        continue

      if i + 1 < n and text[i : i + 2] == "--":
        chars[i] = " "
        chars[i + 1] = " "
        i += 2
        while i < n and chars[i] != "\n":
          chars[i] = " "
          i += 1
        continue

      if i + 1 < n and text[i : i + 2] == "/-":
        chars[i] = " "
        chars[i + 1] = " "
        block_depth = 1
        i += 2
        continue

      if mask_strings and chars[i] == '"':
        chars[i] = " "
        in_string = True
        i += 1
        continue

      i += 1

    return "".join(chars)

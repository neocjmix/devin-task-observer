#!/usr/bin/env python3
"""Check inline local links in README.md and docs/**/*.md without dependencies.

Fenced code, inline code, external URLs and absolute paths are excluded. Local
fragments resolve to Markdown headings (including Unicode and duplicate slugs).
This checks repository navigation, not remote URLs or reference-style links.
"""
import argparse
import html
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import unquote, urlsplit


FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
LINK = re.compile(
    r'(?<!\\)\[(?P<label>[^\]\n]*)\]\(\s*'
    r'(?P<target><[^>\n]*>|(?:\\.|[^\s()\\]|\([^()\n]*\))+)'
    r'(?:\s+(?:"[^"\n]*"|\x27[^\x27\n]*\x27))?\s*\)'
)
INLINE_CODE = re.compile(r'(`+)(?!`)([^\n]*?)(?<!`)\1(?!`)')


def without_fences(text):
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        match = FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)
        elif fence is not None:
            if (match and match.group(1)[0] == fence[0]
                    and len(match.group(1)) >= len(fence)
                    and not match.group(2).strip()):
                fence = None
        else:
            lines.append(line)
            continue
        # Keep offsets/line numbers intact for diagnostics.
        lines.append(''.join('\n' if char == '\n' else ' ' for char in line))
    return ''.join(lines)


def heading_slug(text):
    text = LINK.sub(lambda match: match.group('label'), text)
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', text)
    text = html.unescape(text).strip().lower()
    return ''.join(
        '-' if char.isspace() else char
        for char in text
        if char.isspace() or char in '-_'
        or unicodedata.category(char)[0] in 'LNM'
    )


def heading_anchors(text):
    lines, used, counts = without_fences(text).splitlines(), set(), {}
    for index, line in enumerate(lines):
        match = re.match(r'^ {0,3}#{1,6}(?:\s+|$)(.*)$', line)
        if match:
            title = re.sub(r'\s+#+\s*$', '', match.group(1))
        elif (index + 1 < len(lines) and line.strip()
              and re.match(r'^ {0,3}(?:=+|-+)\s*$', lines[index + 1])):
            title = line.strip()
        else:
            continue
        base = heading_slug(title)
        slug = base
        while slug in used:
            counts[base] = counts.get(base, 0) + 1
            slug = f'{base}-{counts[base]}'
        used.add(slug)
    return used


def check(root):
    files = [root / 'README.md', *sorted((root / 'docs').rglob('*.md'))]
    anchors, failures, checked = {}, [], 0
    for source in files:
        label = source.relative_to(root)
        try:
            text = without_fences(source.read_text(encoding='utf-8'))
        except (OSError, UnicodeError) as error:
            failures.append(f'{label}:1: cannot read document: {error}')
            continue
        # Do not mistake example Markdown within inline code for a real link.
        visible = INLINE_CODE.sub(lambda match: ' ' * len(match.group()), text)
        for match in LINK.finditer(visible):
            destination = html.unescape(match.group('target').strip('<>'))
            destination = re.sub(r'\\([\\()\[\] <>])', r'\1', destination)
            # Editor-style file.py:123 suffixes are not part of the filename.
            destination = re.sub(r':\d+(?=#|$)', '', destination)
            try:
                parts = urlsplit(destination)
            except ValueError:
                continue  # Malformed external URLs are outside this check.
            if parts.scheme or parts.netloc or parts.path.startswith('/'):
                continue
            checked += 1
            line = text.count('\n', 0, match.start()) + 1
            target = (source.parent / unquote(parts.path)).resolve() if parts.path else source
            problem = None
            if not target.exists():
                problem = 'target does not exist'
            elif parts.fragment and not re.fullmatch(r'L\d+(?:-L?\d+)?', parts.fragment):
                if target.suffix.lower() != '.md' or not target.is_file():
                    problem = 'heading anchor target is not a Markdown file'
                else:
                    try:
                        if target not in anchors:
                            anchors[target] = heading_anchors(target.read_text(encoding='utf-8'))
                        if unquote(parts.fragment) not in anchors[target]:
                            problem = 'heading anchor does not exist'
                    except (OSError, UnicodeError) as error:
                        problem = f'cannot read anchor target: {error}'
            if problem:
                failures.append(f'{label}:{line}: {destination}: {problem}')
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f'Document links: {len(files)} documents, {checked} local links, {len(failures)} errors')
    return bool(failures)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    raise SystemExit(check(parser.parse_args().root.resolve()))

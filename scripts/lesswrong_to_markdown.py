#!/usr/bin/env python3
"""
LessWrong Post to Markdown Converter

Downloads a LessWrong post (including figures and comments) via the GraphQL API
and converts it to a well-formatted Markdown file.

Usage:
    python lesswrong_to_markdown.py <lesswrong_url> [--output-dir OUTPUT_DIR] [--no-comments] [--no-images]

Examples:
    python lesswrong_to_markdown.py https://www.lesswrong.com/posts/ABC123/my-post-title
    python lesswrong_to_markdown.py https://www.lesswrong.com/posts/ABC123/my-post-title --output-dir ./downloads
    python lesswrong_to_markdown.py ABC123  # just the post ID works too
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from textwrap import dedent
from urllib.parse import urlparse, urljoin


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

GRAPHQL_URL = "https://www.lesswrong.com/graphql"

def _gql(query: str, variables: dict | None = None) -> dict:
    """Send a GraphQL request to LessWrong and return the JSON response."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_post(slug: str | None = None, post_id: str | None = None) -> dict:
    """Fetch a post by slug or ID."""
    # LessWrong's GraphQL uses documentId as the selector field
    if post_id:
        selector = f'documentId: "{post_id}"'
    elif slug:
        selector = f'slug: "{slug}"'
    else:
        raise ValueError("Need slug or post_id")

    query = """
    {
      post(input: {selector: {%s}}) {
        result {
          _id
          title
          slug
          htmlBody
          postedAt
          modifiedAt
          baseScore
          voteCount
          commentCount
          wordCount
          url
          user {
            displayName
            slug
          }
          coauthors {
            displayName
            slug
          }
          pageUrl
        }
      }
    }
    """ % selector

    data = _gql(query)
    return data["data"]["post"]["result"]


def fetch_comments(post_id: str, limit: int = 500) -> list[dict]:
    """Fetch all comments for a post."""
    query = """
    query CommentsQuery($postId: String!, $limit: Int!) {
      comments(input: {terms: {view: "postCommentsTop", postId: $postId, limit: $limit}}) {
        results {
          _id
          parentCommentId
          htmlBody
          postedAt
          baseScore
          voteCount
          user {
            displayName
            slug
          }
        }
      }
    }
    """
    data = _gql(query, {"postId": post_id, "limit": limit})
    return data["data"]["comments"]["results"]


# ---------------------------------------------------------------------------
# HTML -> Markdown converter
# ---------------------------------------------------------------------------

class HTMLToMarkdown(HTMLParser):
    """
    A purpose-built HTML-to-Markdown converter for LessWrong post bodies.
    Handles headings, paragraphs, lists, links, images, code blocks,
    blockquotes, bold/italic, tables, footnotes, and more.
    """

    BLOCK_TAGS = {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "pre", "ul", "ol", "li", "table",
        "thead", "tbody", "tr", "th", "td", "hr", "br",
        "figure", "figcaption", "section", "details", "summary",
    }

    def __init__(self, image_dir: str | None = None, download_images: bool = True):
        super().__init__()
        self.image_dir = image_dir
        self.download_images = download_images
        self._result: list[str] = []
        self._tag_stack: list[str] = []
        self._list_stack: list[tuple[str, int]] = []  # (type, counter)
        self._in_pre = False
        self._in_code = False
        self._in_blockquote = 0
        self._href: str | None = None
        self._link_text_parts: list[str] = []
        self._in_link = False
        self._suppress_text = False
        self._td_cells: list[str] = []
        self._in_td = False
        self._current_cell: list[str] = []
        self._table_rows: list[list[str]] = []
        self._in_table = False
        self._footnotes: list[str] = []
        self._images_downloaded: dict[str, str] = {}

    # -- helpers --

    def _write(self, text: str):
        if not self._suppress_text:
            if self._in_td:
                self._current_cell.append(text)
            else:
                self._result.append(text)

    def _ensure_blank_line(self):
        joined = "".join(self._result)
        if joined and not joined.endswith("\n\n"):
            if joined.endswith("\n"):
                self._result.append("\n")
            else:
                self._result.append("\n\n")

    def _download_image(self, src: str) -> str:
        """Download image and return local relative path, or original URL."""
        if not self.download_images or not self.image_dir:
            return src
        if src in self._images_downloaded:
            return self._images_downloaded[src]

        os.makedirs(self.image_dir, exist_ok=True)

        # Derive filename
        parsed = urlparse(src)
        basename = os.path.basename(parsed.path) or "image"
        if not os.path.splitext(basename)[1]:
            basename += ".png"
        # Deduplicate
        dest = os.path.join(self.image_dir, basename)
        counter = 1
        while os.path.exists(dest):
            name, ext = os.path.splitext(basename)
            dest = os.path.join(self.image_dir, f"{name}_{counter}{ext}")
            counter += 1

        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(dest, "wb") as f:
                    f.write(resp.read())
            rel = os.path.relpath(dest, os.path.dirname(self.image_dir))
            self._images_downloaded[src] = rel
            return rel
        except Exception as e:
            print(f"  Warning: failed to download {src}: {e}", file=sys.stderr)
            self._images_downloaded[src] = src
            return src

    # -- parser callbacks --

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_dict = dict(attrs)
        self._tag_stack.append(tag)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._ensure_blank_line()
            self._write("#" * level + " ")

        elif tag == "p":
            self._ensure_blank_line()

        elif tag == "br":
            self._write("  \n")

        elif tag == "hr":
            self._ensure_blank_line()
            self._write("---\n\n")

        elif tag == "blockquote":
            self._in_blockquote += 1
            self._ensure_blank_line()

        elif tag == "pre":
            self._in_pre = True
            self._ensure_blank_line()
            self._write("```\n")

        elif tag == "code":
            if not self._in_pre:
                self._write("`")
            self._in_code = True

        elif tag in ("strong", "b"):
            self._write("**")

        elif tag in ("em", "i"):
            self._write("*")

        elif tag in ("del", "s", "strike"):
            self._write("~~")

        elif tag == "a":
            href = attrs_dict.get("href", "")
            self._href = href
            self._in_link = True
            self._link_text_parts = []

        elif tag == "img":
            src = attrs_dict.get("src", "")
            alt = attrs_dict.get("alt", "")
            if src:
                local = self._download_image(src)
                self._ensure_blank_line()
                self._write(f"![{alt}]({local})\n\n")

        elif tag == "ul":
            if self._list_stack:
                pass  # nested
            else:
                self._ensure_blank_line()
            self._list_stack.append(("ul", 0))

        elif tag == "ol":
            start = int(attrs_dict.get("start", "1"))
            if not self._list_stack:
                self._ensure_blank_line()
            self._list_stack.append(("ol", start - 1))

        elif tag == "li":
            indent = "  " * max(0, len(self._list_stack) - 1)
            if self._list_stack:
                ltype, count = self._list_stack[-1]
                if ltype == "ul":
                    self._write(f"{indent}- ")
                else:
                    count += 1
                    self._list_stack[-1] = (ltype, count)
                    self._write(f"{indent}{count}. ")

        elif tag == "table":
            self._in_table = True
            self._table_rows = []
            self._ensure_blank_line()

        elif tag in ("td", "th"):
            self._in_td = True
            self._current_cell = []

        elif tag == "tr":
            self._td_cells = []

        elif tag == "sup":
            # Footnote reference
            if "footnote-ref" in attrs_dict.get("class", ""):
                pass  # let the link inside handle it

        elif tag == "figure":
            self._ensure_blank_line()

    def handle_endtag(self, tag: str):
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._write("\n\n")

        elif tag == "p":
            if self._in_blockquote:
                self._write("\n")
            else:
                self._write("\n\n")

        elif tag == "blockquote":
            self._in_blockquote = max(0, self._in_blockquote - 1)

        elif tag == "pre":
            self._in_pre = False
            self._write("```\n\n")

        elif tag == "code":
            if not self._in_pre:
                self._write("`")
            self._in_code = False

        elif tag in ("strong", "b"):
            self._write("**")

        elif tag in ("em", "i"):
            self._write("*")

        elif tag in ("del", "s", "strike"):
            self._write("~~")

        elif tag == "a":
            link_text = "".join(self._link_text_parts)
            if self._href and self._href.startswith("#"):
                # footnote or anchor
                self._write(f"[{link_text}]({self._href})")
            elif self._href:
                self._write(f"[{link_text}]({self._href})")
            else:
                self._write(link_text)
            self._in_link = False
            self._href = None

        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            if not self._list_stack:
                self._write("\n")

        elif tag == "li":
            text = "".join(self._result)
            if not text.endswith("\n"):
                self._write("\n")

        elif tag in ("td", "th"):
            self._in_td = False
            self._td_cells.append("".join(self._current_cell).strip())
            self._current_cell = []

        elif tag == "tr":
            if self._td_cells:
                self._table_rows.append(self._td_cells)
            self._td_cells = []

        elif tag == "table":
            self._in_table = False
            if self._table_rows:
                self._render_table()

    def _render_table(self):
        rows = self._table_rows
        if not rows:
            return
        # Compute column widths
        ncols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < ncols:
                r.append("")
        widths = [max(len(r[i]) for r in rows) for i in range(ncols)]
        widths = [max(w, 3) for w in widths]

        def fmt_row(row):
            cells = [cell.ljust(w) for cell, w in zip(row, widths)]
            return "| " + " | ".join(cells) + " |"

        self._write(fmt_row(rows[0]) + "\n")
        self._write("| " + " | ".join("-" * w for w in widths) + " |\n")
        for row in rows[1:]:
            self._write(fmt_row(row) + "\n")
        self._write("\n")
        self._table_rows = []

    def handle_data(self, data: str):
        if self._in_link:
            self._link_text_parts.append(data)
            return

        if self._in_pre:
            self._write(data)
            return

        if self._in_blockquote:
            prefix = "> " * self._in_blockquote
            lines = data.split("\n")
            processed = []
            for line in lines:
                processed.append(line)
            self._write("".join(processed))
            return

        # Collapse whitespace for inline text
        if not self._in_pre:
            data = re.sub(r'\s+', ' ', data)
            # Don't write leading space at start of block
            joined = "".join(self._result)
            if data == " " and (not joined or joined.endswith("\n")):
                return

        self._write(data)

    def handle_entityref(self, name: str):
        import html
        char = html.unescape(f"&{name};")
        self.handle_data(char)

    def handle_charref(self, name: str):
        import html
        char = html.unescape(f"&#{name};")
        self.handle_data(char)

    def get_markdown(self) -> str:
        text = "".join(self._result)
        # Clean up excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Add blockquote prefixes to lines inside blockquotes
        # (handled inline above, but clean up)
        return text.strip() + "\n"


def html_to_markdown(html: str, image_dir: str | None = None, download_images: bool = True) -> str:
    converter = HTMLToMarkdown(image_dir=image_dir, download_images=download_images)
    converter.feed(html)
    return converter.get_markdown()


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def parse_lw_url(url_or_id: str) -> tuple[str | None, str | None]:
    """Extract (slug, post_id) from a LessWrong URL or raw post ID."""
    # Raw post ID
    if re.match(r'^[A-Za-z0-9]{10,20}$', url_or_id):
        return None, url_or_id

    parsed = urlparse(url_or_id)
    # e.g. /posts/HhF5kESdtPHku7kim/some-slug
    m = re.search(r'/posts/([A-Za-z0-9]+)/([^/?#]+)', parsed.path)
    if m:
        return m.group(2), m.group(1)

    m = re.search(r'/posts/([A-Za-z0-9]+)', parsed.path)
    if m:
        return None, m.group(1)

    raise ValueError(f"Could not parse LessWrong URL: {url_or_id}")


# ---------------------------------------------------------------------------
# Comment tree builder
# ---------------------------------------------------------------------------

def build_comment_tree(comments: list[dict]) -> str:
    """Build a threaded comment tree as markdown."""
    if not comments:
        return ""

    by_parent: dict[str | None, list[dict]] = {}
    for c in comments:
        pid = c.get("parentCommentId")
        by_parent.setdefault(pid, []).append(c)

    lines: list[str] = []

    def render(parent_id: str | None, depth: int):
        children = by_parent.get(parent_id, [])
        # Sort by score descending
        children.sort(key=lambda c: c.get("baseScore", 0), reverse=True)
        for c in children:
            user = c.get("user", {})
            name = user.get("displayName", "Anonymous") if user else "Anonymous"
            score = c.get("baseScore", 0)
            date = c.get("postedAt", "")[:10]
            indent = ">" * depth if depth > 0 else ""
            prefix = f"{indent} " if indent else ""

            lines.append(f"{prefix}### {name} (score: {score}) - {date}\n")

            body_html = c.get("htmlBody", "")
            if body_html:
                body_md = html_to_markdown(body_html, download_images=False)
                for line in body_md.split("\n"):
                    lines.append(f"{prefix}{line}\n")

            lines.append("\n")
            render(c["_id"], depth + 1)

    render(None, 0)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def download_post(
    url_or_id: str,
    output_dir: str = ".",
    include_comments: bool = True,
    download_images: bool = True,
) -> str:
    """Download a LessWrong post and save as markdown. Returns output path."""

    slug, post_id = parse_lw_url(url_or_id)

    print(f"Fetching post (slug={slug}, id={post_id})...")
    post = fetch_post(slug=slug, post_id=post_id)

    title = post.get("title", "Untitled")
    author = post.get("user", {})
    author_name = author.get("displayName", "Unknown") if author else "Unknown"
    coauthors = post.get("coauthors") or []
    coauthor_names = [c.get("displayName", "") for c in coauthors if c]
    posted_at = post.get("postedAt", "")[:10]
    modified_at = post.get("modifiedAt", "")[:10]
    score = post.get("baseScore", 0)
    vote_count = post.get("voteCount", 0)
    comment_count = post.get("commentCount", 0)
    word_count = post.get("wordCount", 0)
    lw_url = post.get("url", "")
    post_id = post["_id"]

    # Sanitize title for filename
    safe_title = re.sub(r'[^\w\s-]', '', title)
    safe_title = re.sub(r'\s+', '_', safe_title).strip('_')[:80]

    os.makedirs(output_dir, exist_ok=True)
    image_dir = os.path.join(output_dir, f"{safe_title}_images")

    # Convert body
    print("Converting HTML to Markdown...")
    html_body = post.get("htmlBody", "")
    body_md = html_to_markdown(
        html_body,
        image_dir=image_dir if download_images else None,
        download_images=download_images,
    )

    # Build markdown document
    parts: list[str] = []

    # Frontmatter
    all_authors = [author_name] + coauthor_names
    parts.append(f"# {title}\n\n")
    parts.append(f"**Author(s):** {', '.join(all_authors)}  \n")
    parts.append(f"**Posted:** {posted_at}  \n")
    if modified_at and modified_at != posted_at:
        parts.append(f"**Modified:** {modified_at}  \n")
    parts.append(f"**Score:** {score} ({vote_count} votes)  \n")
    parts.append(f"**Comments:** {comment_count}  \n")
    parts.append(f"**Word count:** {word_count}  \n")
    if lw_url:
        parts.append(f"**URL:** {lw_url}  \n")
    parts.append("\n---\n\n")

    # Body
    parts.append(body_md)

    # Comments
    if include_comments and comment_count > 0:
        print(f"Fetching {comment_count} comments...")
        comments = fetch_comments(post_id)
        if comments:
            parts.append("\n\n---\n\n")
            parts.append(f"# Comments ({len(comments)})\n\n")
            parts.append(build_comment_tree(comments))

    output_path = os.path.join(output_dir, f"{safe_title}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))

    print(f"\nSaved to: {output_path}")
    if download_images and os.path.isdir(image_dir):
        n_images = len(os.listdir(image_dir))
        print(f"Downloaded {n_images} images to: {image_dir}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Download a LessWrong post as Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples:
              %(prog)s https://www.lesswrong.com/posts/ABC123/my-post
              %(prog)s ABC123 --output-dir ./downloads
              %(prog)s https://www.lesswrong.com/posts/ABC123/my-post --no-comments
        """),
    )
    parser.add_argument("url", help="LessWrong post URL or post ID")
    parser.add_argument(
        "--output-dir", "-o", default=".",
        help="Directory to save output (default: current directory)",
    )
    parser.add_argument(
        "--no-comments", action="store_true",
        help="Skip downloading comments",
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Skip downloading images",
    )
    args = parser.parse_args()

    download_post(
        args.url,
        output_dir=args.output_dir,
        include_comments=not args.no_comments,
        download_images=not args.no_images,
    )


if __name__ == "__main__":
    main()

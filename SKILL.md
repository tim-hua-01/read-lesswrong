---
name: read-lesswrong
description: Use this skill when asked to read a LessWrong post given a LessWrong URL. Trigger when the user shares a lesswrong.com link or asks you to read/summarize/review a LessWrong post.
argument-hint: <lesswrong-url>
allowed-tools: Bash Read Glob Grep
---
The user wants you to read a LessWrong post. The URL is: $ARGUMENTS

Example URL format:
https://www.lesswrong.com/posts/HhF5kESdtPHku7kim/reproducing-steering-against-evaluation-awareness-in-a-large-1

### Part 1: Determine the output directory

Use `$TMPDIR/lesswrong/` as the output directory. Create it if it doesn't exist.

### Part 2: Download the post

Run the converter script bundled with this skill:

```
python "${CLAUDE_SKILL_DIR}/scripts/lesswrong_to_markdown.py" "$ARGUMENTS" --output-dir "$TMPDIR/lesswrong/"
```

This script uses the LessWrong GraphQL API to fetch:
- The full post body (HTML), converted to Markdown
- All figures/images, saved locally
- All comments with threading
- Post metadata (author, date, score, word count, etc.)

If the download fails, try passing just the post ID (the alphanumeric string after `/posts/` in the URL) instead of the full URL.

### Part 3: Read the full post body

Read the downloaded Markdown file **in its entirety up to and including any "Discussion", "Conclusion", or "Future work" sections**. You may skip "Appendix" sections and footnotes unless the user specifically asks for them.

**Read every single line.** Do not skip or skim any section of the main body. If the file is too long to read in one chunk, read it in multiple sequential chunks with the `offset` and `limit` parameters until you have covered the entire main body.

**After reading, mentally enumerate the sections you covered and verify none were skipped.** If any main body sections are missing, go back and read them before proceeding.

### Part 4: Inspect all figures

Inspect **every** figure referenced in the main body of the post. The images are saved in a `*_images/` subdirectory next to the Markdown file.

Use the Read tool to view each image file directly — do not skip this step. Figures often contain the most important results (plots, tables, diagrams) and are essential to understanding the post.

### Part 5: Read comments

After reading the post body and figures, read the comments section of the Markdown file. Read at least the top-level comments and any highly-scored replies. You may skim deeply nested low-score threads unless they seem substantive.

### Part 6: Respond to the user

**Begin your response with an explicit enumeration of which main body sections you read**, formatted as a short list (e.g., "Sections read: Introduction, Background, Main Argument, Results, Conclusion. Comments: top-level read, replies skimmed.").

**Be honest.** If you cut corners and did not actually read every section in full — for example, you skimmed a section, skipped figures, or only read top-level comments — say so plainly in this opening enumeration. List which sections were read in full, which were only partially read or skimmed, and which were skipped entirely. Do not claim a full read you did not perform.

After the enumeration, address the user's request:

If the user asked a specific question or requested a specific task about the post, answer that directly.

Otherwise, provide a brief summary (3-5 sentences) covering the post's main contribution, method, and key results, then let the user know you're ready for questions.

**DO NOT RESPOND TO THE USER BEFORE READING EVERYTHING REQUIRED.** If you failed to read the full main body or could not download the post, STOP and tell the user you have failed instead of providing a partial answer.

### Overrides

If the user gives specific instructions that conflict with the above (e.g. "just read the comments", "skip the figures", "only summarize the intro"), follow the user's instructions instead.

# Vendored editor plugins

These two files are the owner's ChatGPT editor plugins, copied verbatim and
embedded in the news prompts. They are the specification for a draft and a
digest; do not edit them here. To update one, copy the plugin's `SKILL.md` over
the file and update this table and the matching hash in
`tests/test_skills.py` in the same change.

| File | Plugin | Version | Source archive | SHA-256 |
|---|---|---|---|---|
| `news_drafting.md` | `news-drafter`（新闻稿整理） | 0.1.4 | `~/Downloads/plugin.zip` → `skills/news-drafting/SKILL.md` | `2ea5f89666baa19d24435dee830c4fd89ce3dc9cd63c08db76c068a2a914e02c` |
| `hot_news.md` | `hot-news`（热点新闻汇总） | 0.1.0 | `~/Downloads/plugin (1).zip` → `skills/hot-news/SKILL.md` | `8e61adb03a2b213905d7c2cdaac27a5913072a9b135287cf1c49045c5251fecf` |

Both files are byte-identical to the extracted `SKILL.md`, including the YAML
frontmatter. `winnie_news.skills.quoted_skills()` wraps each file in a
`<skill name="...">` envelope; nothing inside the envelope is rewritten.

Why the whole file, frontmatter included: trimming it to "the rules" is already
an editorial decision about which lines matter. Quoting the file leaves that
decision with the owner.

The pipeline adds only things the plugins cannot state — the JSON envelope, the
field names the pipeline parses, the pipeline's own settings (language, a
position for the batch, commentary) and the mechanical checks in
`review/checks.py`. Each of those is labelled as a pipeline rule in
`docs/news.md`; none of them is presented to the model as part of a plugin.

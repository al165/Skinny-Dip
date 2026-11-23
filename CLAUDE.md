# dip-args

Argument-level corpus memory: documents → attributed mentions → holders and arguments (with LLM
summaries) → issues/themes → typed relations → an editable wiki, an API and a canvas UI. Local
models only (Ollama qwen3:14b + embedding server). Package `dipa`. Full docs: `README.md`.
Design: the proposal doc https://claude.ai/artifact/HeKPRjW7yLSiNyiTFQtt7k ("As built" section).

## Commands

Python env: the `dip` pyenv virtualenv. Run from this directory.

```bash
python -m dipa.ingest.run <files|dirs|URLs|.urls>    # stages 1-4 per source, 5-10 over the corpus
python -m dipa.ingest.run --stages 5,6,7,8,9         # corpus stages only
python -m dipa.ingest.run --stages 10                # image library
python -m dipa.wiki sync | write | apply             # wiki from the shell
python -m dipa.report                                # <data>/reports/
DIP_DATA_DIR=data-full uvicorn dipa.api:app --port 8001
cd skinny-dip && DIP_API_TARGET=http://localhost:8001 npx vite --port 5174
```

The canvas's Ask panel calls `POST /question` (RAG over the card, issues/themes, arguments and
passages; answers cite short labels, mapped back to ids). Docs: README "Question".

Builds: `data-full/` is current (14 SD documents, list in `data-full/sources.txt`). `data-test3/`,
`-test4/` and `-test5/` are the three-document test builds (gold labels in
`data-test3/reports/gold_sample.csv`).

## Things that bit us (don't repeat them)

- **Cache stability:** every LLM call is cached by prompt + model, which is what makes reruns
  cheap.
  - Anything that changes a prompt invalidates its cache.
  - Prompts number passages by `ord`, never by row id: ids change on re-parse.
  - Renaming a holder changes its summaries, so stage 8 re-judges their pairs.
- **Long runs:** start them detached (`setsid nohup … &`), with the PID in a file.
  - `pkill -f <pattern>` also kills the shell running it if the pattern is in its own command
    line; kill by PID.
  - A watcher that greps the log must only read lines written after it started: the log keeps
    earlier runs.
- **Ollama:** `/etc/ollama.conf` (`EnvironmentFile=`) overrides systemd `Environment=` lines.
  Check what the process really got: `/proc/<pid>/environ`.
- **The 14B model's yes/no checks are unreliable on attribution.** Checks flag, they don't drop.
  Anything that could erase a named holder (people, organisations on the cast list) is guarded
  in code. Test any new filter against the minority-view seeds (`experiments/seeds.json`)
  before trusting it.
- **Wiki state:** `wiki_written` records what each page listed. Sync compares a page against
  that record, not against the database, so edges added later aren't read as deletions.
- **Image pages** use plain `key: value` front matter (titles may contain colons). Parse them
  with `images.parse_page`, not YAML.

## User decisions

- Holders stay separate (same view, different holders → SHARED_WITH). Holder edits live only on
  holder pages in the wiki.
- The LLM only appends: no rewriting of issue/theme/argument text after the first draft
  (`--recluster` / `--resummarise` only on request).
- Passages may appear in the UI but are always marked verbatim; junk (contents, credits,
  references) is hidden.
- Diversity over tidiness: one-clause minority views must survive.

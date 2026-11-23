# dip-args

The argument-level version of dip-memory. Instead of passages, users search and walk short,
attributed summaries of the arguments in the corpus ("Emily M. Bender argues…", "the AI industry
claims…"). Every summary links to the verbatim quotes it came from and to other arguments by
CONTRADICTS, SUPPORTS, SHARED_WITH, EXAMPLE_OF and ELABORATES. Arguments sit in a tree of issues
(questions) and themes, which people curate in an editable wiki, and any argument, issue or theme
can ask for an illustrative image. Everything runs on local models (Ollama + the embedding server).

Proposal: the "dip-args: an argument-level corpus database" doc
(<https://claude.ai/artifact/HeKPRjW7yLSiNyiTFQtt7k>). Its "As built" section lists where the
build differs from the plan.

**Status (2026-11-12): working prototype.** Stages 1–10, API, wiki editor and UI. The current
build is `data-full/`: 14 documents of the SD corpus, 777 passages, 1,080 mentions, 239 holders,
1,042 arguments, 39 issues, 6 themes, 14 images.

## Quick start

```bash
# on the GPU machine (arran-desktop): Ollama with qwen3:14b, and the embedding server
#   cd ../dip-memory/embed_server && CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 \
#     uvicorn server:app --host 0.0.0.0 --port 11500

# To run the API server:
DIP_DATA_DIR=data-full DIP_LLM_MODEL=qwen3:4b OLLAMA_URL=http://<host>:11434 \
    uvicorn dipa.api:app --port 8001          # API (docs at /docs)

# Then to run front-end:
cd skinny-dip && DIP_API_TARGET=http://localhost:8001 npx vite --port 5174   # UI
```

- UI: <http://localhost:5174> (seed tabs: Themes, Issues, Arguments, Holders, Images; the "wiki ↗"
  link opens the editor)
- Wiki editor: <http://localhost:8001/ui/editor.html>
- `skinny-dip/node_modules` is a symlink to dip-memory's copy.

## Deploying the demo

Two containers, as for dip-memory: `api` (`Dockerfile`, `requirements-prod.txt`) and `site`
(`skinny-dip/Dockerfile`: the UI built with Vite, served by nginx, which forwards `/api/` to the
API). Only the site is published: `127.0.0.1:3001` by default (dip-memory's demo uses 3000); put
your reverse proxy in front of it. The wiki editor is at `/api/ui/editor.html`, and the UI's
"wiki ↗" link points there.

```bash
cp -r data-full data-demo                       # serve a copy, not the build you ingest into
DIP_DATA_DIR=data-demo python -m dipa.warm      # once, with the models reachable (~15 min)
cp example.env deploy.env                       # set DIP_EDIT_TOKEN, check the paths
docker compose --env-file deploy.env up -d --build   # run it on this machine, or:
./ship.sh user@vps --data                       # build here, run on a small server (first deploy)
./ship.sh user@vps                              # later: new code only; the server's data is kept
```

`ship.sh` builds both images here for the server's CPU architecture (it asks the server), streams
them over SSH (`docker save | gzip | ssh docker load`), copies `compose.yaml` and `deploy.env`
(with the server's paths), syncs the image files, and starts the stack with `--no-build`.

- `--data` uploads the database (a consistent SQLite backup) and the wiki the first time.
- If the server already has data, it is kept: it holds collaborators' edits.
- `--replace-data` overwrites it, after saving `data-backup-<time>.tar.gz` on the server.
- Building arm64 images on an x86 machine needs QEMU (the script prints the one-line setup).

- **No models on the server (`DIP_OFFLINE=1`, the default).** `dipa.warm` pre-computes what the
  moves need for every argument, issue, theme and passage: the embeddings, plus the Images
  move's concepts and their query embeddings. Every move, the wiki and the images then work from
  the database's caches. Free-text search falls back to keyword matching. A wiki edit that needs
  new LLM work (a holder merge that creates new candidate pairs) treats unjudged pairs as
  different.
- **Live models (`DIP_OFFLINE=0`).** Only if the server can reach the GPU machine, e.g. on the
  tailnet (`OLLAMA_URL`, `DIP_EMBED_URL`).
- **Data:**
  - The database and wiki (`DIPA_DATA`) are mounted read-write, because collaborators' wiki
    edits save pages and update the database. Set `DIP_EDIT_TOKEN`, or anyone with the link can
    edit.
  - The image files (`DIPA_IMAGES`) are mounted read-only. Paths stored by a build made on
    another machine are found again by file name.
  - The API runs as uid 1000, so the mounted folders must be writable by that uid.
- **Model names:** the image sets `DIP_LLM_MODEL` and `DIP_EMBED_MODEL` to the build's models,
  because the caches are keyed by model name.
- **Bringing edits back:** collaborators' edits land in the served copy (`data-demo/wiki/`).
  Copy that `wiki/` folder over the main build's and run `python -m dipa.wiki apply`.

## Setup

Python: the `dip` pyenv virtualenv (as dip-memory), plus `rapidfuzz` and `pyyaml`.
Settings live in `dipa/config.py`; every one can be overridden by an env var or `.env`.

| Setting                               | Here                                       | Notes                                                                     |
| ------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| `OLLAMA_URL`                          | `http://arran-desktop:11434`               | the GPU machine over Tailscale (was 192.168.0.15 on the LAN)              |
| `DIP_LLM_MODEL`                       | `qwen3:14b`                                | all text tasks; `DIP_VLM_MODEL` (qwen2.5vl:3b) drafts image pages         |
| `DIP_EMBED_BACKEND` / `DIP_EMBED_URL` | `remote` / `http://arran-desktop:11500`    | embeddinggemma-2 (text and pictures in one space)                         |
| `DIP_LLM_PARALLEL`                    | `4`                                        | concurrent LLM requests; needs `OLLAMA_NUM_PARALLEL` ≥ this on the server |
| `DIP_DATA_DIR`                        | per build                                  | database, wiki, reports, logs                                             |
| `DIPA_IMAGE_DIR`                      | `00_Data/SD-Corpus-v1-small/ingest/images` | the image library                                                         |
| `DIP_EDIT_TOKEN`                      | unset                                      | if set, saving wiki pages needs it                                        |

Ollama server: set `OLLAMA_NUM_PARALLEL=4`, `OLLAMA_FLASH_ATTENTION=1` and
`OLLAMA_KV_CACHE_TYPE=q8_0` in `/etc/ollama.conf`. That file's `EnvironmentFile=` beats
`Environment=` lines in a systemd override, which is why an override's `NUM_PARALLEL=4` was
silently ignored. With 4 slots, total throughput went from 43 to ~142 tok/s, and extraction took
about half as long.

## Building a database

```bash
python -m dipa.ingest.run <files...>               # all stages: 1-4 per file, then 5-10 over the corpus
python -m dipa.ingest.run https://... list.urls dir/  # web pages, .urls lists (one URL per line), folders
python -m dipa.ingest.run <files...> --stages 3,4  # some stages (LLM answers are cached)
python -m dipa.ingest.run --stages 5,6,7,8,9       # corpus stages only (no files needed)
python -m dipa.ingest.run --stages 10              # the image library
python -m dipa.ingest.run --force <files...>       # re-parse unchanged files (after changing stage-1 rules)
python -m dipa.ingest.run --recluster ...          # stage 7: rebuild the issue/theme tree (drops manual placements)
python -m dipa.ingest.run --resummarise ...        # stage 6: rewrite every argument summary
python -m dipa.ingest.run --reset <files...>       # delete the database (and its LLM cache)
python -m dipa.report                              # review pages and labelling sheets in <data>/reports/
python -m dipa.wiki sync | write | apply           # the wiki from the shell (see Editable wiki)
sqlite3 data-full/args.sqlite                      # schema in dipa/db.py
```

The full build: `data-full/sources.txt` lists the 14 documents. The Esoteric AI chapter is left
out (bad OCR). The one URL in `reading-list.urls` isn't in the build yet.

Web pages: stage 1 fetches the page and extracts the article text (trafilatura, as in dip-memory);
`items.path` and `items.source` hold the URL. A page is re-fetched on every run but only re-parsed
when its extracted text changes. A page that can't be fetched (paywall, JS-only, blocked) is
logged and skipped; save it and ingest the file instead (add a `<file>.source.txt` with the URL).
In the wiki and holder ids a web page is named by a slug of its host and path
(`example-com-blog-post`), in place of a file name.

```bash
DIP_DATA_DIR=data-full bash -c 'mapfile -t F < data-full/sources.txt; python -m dipa.ingest.run "${F[@]}"'
```

Time: a full extraction takes about an hour with 4 parallel slots, and stages 5–8 about
50 minutes the first time. Reruns are cheap because every LLM answer is cached by prompt +
model, including answers that overflowed the output cap. Prompts number passages by their
position in the document, so the cache survives a re-parse. Things that do cost new LLM calls:

- changing a prompt
- renaming or merging a holder: its summaries change, so stage 8 re-judges their pairs (about
  5 min for AIxDESIGN's 112 arguments)

### Adding documents to an existing build

Pass only the new documents; the rest of the corpus is not re-ingested:

```bash
DIP_DATA_DIR=data-full python -m dipa.ingest.run path/to/new.pdf https://example.com/article
```

- **Stages 1–4** run on the documents passed and nothing else. A file that is already in the
  build and unchanged is skipped at stage 1.
- **Stages 5–10** run over the whole corpus, but mostly from the cache. Wiki edits are read in
  first, so the rebuild keeps them.
  - Stage 5 rebuilds holders and arguments; only the new mentions cost LLM calls.
  - Stage 6 summarises only arguments without a summary.
  - Stage 7 keeps the issue/theme tree. A new argument joins the nearest issue if it fits about as
    well as that issue's members, otherwise it goes to `wiki/unsorted.md`: check it when a
    document brings a new topic. `--recluster` rebuilds the tree, but drops manual placements and
    edited issue texts.
  - Stage 8 judges only pairs with a new or changed argument.
- An argument's id is a hash of its mentions. When a new document makes the same point as an
  existing argument from the same holder, the merged argument gets a new id and is summarised and
  placed again, as a new one.
- Add the new paths to `data-full/sources.txt`. Run several documents detached (`setsid nohup …`,
  PID in `data-full/run.pid`).
- To try a document first, copy the build (`cp -r data-full data-trial`) and run with
  `DIP_DATA_DIR=data-trial`.

## Pipeline

Stages 1–4 run per document; 5–10 over the whole corpus.

1. **Parse + filter** (`s1_parse.py`). dip-memory's loaders and chunker give verbatim passages
   (300–800 characters). Short boilerplate lines are dropped before chunking. Passages are hidden
   (kept in the table with a reason, never extracted from or shown) by:
   - rules on the _visible_ text (Markdown links read as their anchor text, URLs ignored): contents
     lists, reference lists, fragments, repeated headers, link lists with no real sentence
   - an LLM label from a fixed list of meta kinds (credits, bios, captions, navigation…). A label
     only hides a passage that is meta in full; captions and headers are only hidden when the
     passage has no sentence of 12+ words (the prose guard, for slide decks).
2. **Cast list** (`s2_cast.py`). Per document: the author (only as the text names them, else
   "the author of this article on <site>"), a summary, the main line, and every other voice the
   text names. Map over 14k-character blocks, voices merged in code.
3. **Extract** (`s3_extract.py`). Two passes per window of ~4,500 characters, all windows in
   parallel. The prompt includes the document summary, the main line, the cast list and the end
   of the previous window.
   - _Author pass_: only what the author puts forward in their own voice (argued, conceded). It
     must skip quotes, cited papers, contributors' projects, captions.
   - _Voices pass_: every view the text attributes to someone else (reported, endorsed,
     rejected), including cited papers, featured projects and narratives others promote, even
     in one clause. It must name the attribution `cue` and the `author_response` before the
     voice. A cue that isn't in the text drops the item.
   - Checks:
     - _Attribution check_ (batched LLM): is this really the holder's view, or the author's?
       Flagged items are not dropped (dropping lost real minority views); `attribution_check` =
       `author`, or `author_echo` when the cue is just the claim.
     - _Who-or-what check_: a holder that is an idea, narrative or image becomes the who the text
       names, or `unspecified`, but never replaces a named person or organisation. Narrative,
       discourse and "-ism" holders become `unspecified` by rule. The original is kept in
       `holder_orig`.
     - _Author items that name a voice_ from the cast list get a second check that can move them
       to that holder (`moved`).
     - _Same sentence in both passes_: reported/endorsed views keep the named holder; rejected
       views keep both (the holder's view and the author's counter-claim).
4. **Verify** (`s4_verify.py`). Each quote is fuzzy-matched against the source (claimed passage,
   neighbours, whole document); stores score, character offsets and `quote_ok`.
5. **Merge** (`s5_merge.py`). Holders and arguments.
   - Holders: named holders merge across documents by name (fuzzy ratio ≥ 92), unless a holder
     page in the wiki says otherwise. Placeholder authors and `unspecified` stay per document.
     Each holder has keys (`name:…`, `author-of:<file>`, `unspecified-in:<file>`) that holder
     pages claim.
   - Arguments: same-holder pairs with a shared quote or embedding cosine ≥ 0.85 go to a strict
     pairwise LLM judge. (Asking the LLM to group 30 statements at once made topic clusters.)
   - An argument's id is a hash of its mentions (document, pass, quote, statement), so it
     survives rebuilds and renames. Summaries of surviving arguments are kept.
6. **Summarise** (`s6_summarise.py`). Only arguments without a summary (`--resummarise` for all).
   The output is a title, a 1–3 sentence summary and an abstraction level (1–5), 4 per call.
   - The summariser sees the statements, quotes and voice labels, plus the source passage for
     argued/reported/endorsed views. Rejected views get no passage, because there the passage
     is mostly the author's rebuttal, which leaked into the holder's view.
   - Summaries are stored with a `{holder}` placeholder and rendered from the holders table
     (plural verbs for plural holders), so a rename needs no LLM.
   - How each document treats a view ("rejected in X") is built from the mention voices, not
     written by the LLM.
7. **Tree** (`s7_tree.py`). The first build clusters holder-free argument embeddings (k-means)
   into ~1.2·√n issues, named as questions with a state-of-the-debate summary, then into
   ~√issues themes.
   - After that it is append-only: new arguments join the issue whose centroid is nearest if
     they are as close as its members usually are (5th percentile), else they go to unsorted.
     No LLM calls.
   - Placements are kept in `placements`, keyed by argument id. `--recluster` rebuilds.
8. **Relations** (`s8_relations.py`). Candidates: each argument's 6 nearest neighbours, plus
   cross-holder pairs in the same issue (counters worded differently are rarely neighbours).
   One graded LLM call per pair, with digit probabilities from the logprobs:

   | Judge's answer               | Edge                                                                                                                                      |
   | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
   | opposed, qualifies           | CONTRADICTS (both directions) when P(opposed) + ½·P(qualifies) ≥ 0.5. "Qualifies" counts half: on its own it was mostly compatible views. |
   | supports                     | SUPPORTS                                                                                                                                  |
   | same view, different holders | SHARED_WITH                                                                                                                               |
   | specific case                | EXAMPLE_OF or ELABORATES                                                                                                                  |

   Manual and rejected edges from the wiki are kept; rejected pairs are never re-added.

9. **Wiki** (`dipa/wiki.py`). Writes the editable wiki (below). Before every corpus run,
   `wiki.sync` reads people's edits back.
10. **Images** (`dipa/images.py`). The image library (below).

Voices: `argued`, `conceded` (author pass); `reported`, `endorsed`, `rejected` (voices pass) =
how the document's author treats the view. Holder types: author, person, org, government,
industry, field, public, work (a project, paper or artwork whose creator isn't named),
unspecified. Kinds: position, fact, example, recommendation, definition, question.

## Data model (`dipa/db.py`)

| Table                                                     | Holds                                                                                                                                                        |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `items`                                                   | documents: path, parsed title, URL (`source`), author, summary, main line; `display_*` from source pages                                                     |
| `passages`                                                | verbatim text, `hidden` + `hidden_reason`                                                                                                                    |
| `cast`                                                    | the voices each document names (stage 2)                                                                                                                     |
| `mentions`                                                | one extracted view: pass, holder, voice, kind, statement, quote (+ offsets, `quote_ok`), cue, author response, checks, `holder_id`                           |
| `holders`                                                 | who holds views, with their keys and wiki page                                                                                                               |
| `nodes`                                                   | arguments (level 1), issues (2), themes (3): title, `summary_tpl`, rendered `summary`, abstraction, `parent_id`, origin (`extracted`/`synthesised`/`manual`) |
| `argument_mentions`                                       | which mentions make up an argument                                                                                                                           |
| `edges`                                                   | typed relations; `method` graded / manual / rejected; the judge's probabilities                                                                              |
| `overrides`, `placements`, `holder_rules`, `wiki_written` | the wiki's state (see Editable wiki)                                                                                                                         |
| `images`, `image_metaphors`, `image_visual`               | the image library                                                                                                                                            |
| `llm_cache`, `embed_cache`                                | every LLM answer and embedding, by input                                                                                                                     |

## API (`dipa/api.py`)

Same endpoint names and result shape as dip-memory, so skinny-dip works with small changes.
Node ids are strings: `a…` argument, `i…` issue, `t…` theme, `p<n>` passage, `h:<slug>` holder.
Every result carries `provenance`: `llm` (a summary the model wrote), `manual` (edited in the
wiki) or `verbatim` (a passage). Arguments also carry `holder`, `reception` (how each document
treats the view, with URLs) and `sources` (document, URL, quote, passage, verified).

| Move (`GET /{move}?node_id=…&k=…`) | On                              | Returns                                                                                |
| ---------------------------------- | ------------------------------- | -------------------------------------------------------------------------------------- |
| `counter`                          | argument                        | outgoing CONTRADICTS, other documents first                                            |
| `evidence`                         | argument                        | incoming SUPPORTS and EXAMPLE_OF                                                       |
| `neighbours`                       | argument, issue, theme          | similar meaning, spread across holders and documents (MMR)                             |
| `zoom-in`                          | argument, issue, theme, holder  | ELABORATES/EXAMPLE_OF; issue → its arguments, one per holder first; theme → its issues |
| `zoom-out`                         | argument, issue                 | the arguments it elaborates, then its issue, then its theme                            |
| `shared-with`                      | argument                        | the same view held by other holders                                                    |
| `sources`                          | argument                        | the verbatim passages, with the quote highlighted                                      |
| `arguments-here`                   | passage                         | the arguments extracted from it                                                        |
| `holder`                           | argument, holder                | the holder's other arguments                                                           |
| `images`                           | argument, issue, theme, passage | illustrative pictures (subject and metaphor)                                           |

Other endpoints:

- `GET /search?q=&kind=argument,issue,theme`: meaning + keywords, at most 2 results per holder
  before the rest
- `GET /nodes?kind=…`, `GET /nodes/{id}`, `GET /items`, `GET /health`
- `GET /holders`, `GET /holders/{slug}`
- `GET /image-library`, `GET /image-file/{slug}`, `GET /image-passages/{slug}` (arguments a
  picture fits)
- `GET/PUT /wiki-files/…`
- `POST /question {question, node_id?, selection?, k?}`: see below

### Question (`POST /question`)

An LLM answer (qwen3:14b) built from the database, cited by label. The material, in prompt order,
within about 14,000 characters:

1. **the card asked about** (`node_id`), and what it links to: for an argument its counters,
   evidence, shared views, issue and source passages; for a passage the arguments extracted from
   it; for an issue or theme its members; for a holder their arguments. `selection` (text the
   reader selected in the card) is quoted in the prompt and added to the search query.
2. **issues and themes** matching the question (the wiki tree, with manual edits)
3. **arguments**, from the hybrid search (at most 2 per holder first)
4. **passages**: the source passage of the top 5 arguments, then the 6 passages nearest the
   question by embedding (passage vectors go into `embed_cache` on the first question)

Each item gets a short label (`[A2]`, `[P1]`, `[I1]`, `[T1]`, `[H1]`), never a row id. The prompt
asks for the holder of every view to be named and quotes to come only from passages.
Labels the model invents are removed. The response has `answer`, `found` (false when the model
says the material doesn't answer, or nothing was cited), `cited` (node ids), `labels` (label →
node id) and `sources` (every item, as a node result with `label` and `cited`, cited first).
Answers are cached by prompt + model (task `question` in `llm_cache`); a fresh answer took 5–12 s
on the test build.

## UI (`skinny-dip/`)

A copy of dip-memory's skinny-dip, changed for arguments (see `skinny-dip/README.md`).

- **Argument nodes** show a holder chip, a provenance label ("Summary · written by qwen3:14b",
  "Summary · edited"), how each document treats the view (linked to its URL) and the quotes.
- **Passages** are styled as source text ("Passage · verbatim from …"), with the quote
  highlighted.
- **Image nodes** always show the credit.
- **Each node offers only the moves that fit its kind.**
- **Ask:** a question to the LLM about a card (its Ask button), about text selected in a card
  ("Ask about this"), or about the whole corpus (the "ask" button, top right). See
  `skinny-dip/README.md`.

## Editable wiki

`<data>/wiki/` is where people curate the debate map. Edit the files directly or in the editor
(`/ui/editor.html`). Saving in the editor runs `wiki.apply`: sync the edits, rebuild what depends
on them without new LLM text (holders and arguments if a holder page changed, the tree
placements, the pages), and rewrite the pages.

| Page                                                                          | Path                |
| ----------------------------------------------------------------------------- | ------------------- |
| Debate map (generated)                                                        | `index.md`          |
| Theme                                                                         | `themes/<slug>.md`  |
| Issue: question, state of the debate, arguments, tensions                     | `issues/<slug>.md`  |
| Holder: name, type, keys, description; arguments and opponents (generated)    | `holders/<slug>.md` |
| Source: display title, URL, author; summary and voices (generated)            | `sources/<file>.md` |
| Image                                                                         | `images/<slug>.md`  |
| Arguments that fit no issue yet                                               | `unsorted.md`       |
| Things to check (generated): unverified quotes, issues that may be duplicates | `lint.md`           |

| Edit                                                   | Effect                                                                                            |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| issue/theme title or summary                           | stored in `overrides`, shown as "Summary · edited"                                                |
| `## Notes` on any page                                 | kept as written                                                                                   |
| move an `[[arg:…]]` line to another issue page         | the argument moves there (manual placement)                                                       |
| delete an `[[arg:…]]` line (and it's on no other page) | the argument becomes unsorted                                                                     |
| delete a tension line                                  | the edge is rejected; stage 8 never re-adds it                                                    |
| add `- [[arg:A]] CONTRADICTS [[arg:B]]` under Tensions | a manual counter                                                                                  |
| add an `[[issue:…]]` line to a theme page              | the issue moves to that theme                                                                     |
| holder page: name, type, description                   | renamed/retyped everywhere (summaries re-render, no LLM)                                          |
| holder page: add another page's `keys:`                | that holder merges into this one; the other page goes                                             |
| source page: title, url, author                        | how the document is named and linked everywhere (extraction keeps the parsed title, so no re-run) |
| image page: description, subjects, metaphors           | the library is re-embedded                                                                        |
| `locked: true`                                         | stage 9 never rewrites the page                                                                   |

Two rules decided with the user:

- **Holder edits live only on holder pages.**
- **The LLM only appends.** Issue/theme texts and argument summaries are drafted once; later
  runs add new arguments to the nearest issue or to unsorted and never rewrite text. A full
  redraft happens only with `--recluster` / `--resummarise`.

Persistence:

- `wiki_written` records what each page listed when it was written. That lets sync tell a
  deleted line from one that is simply newer than the page, so a counter added later by stage 8
  is not mistaken for a deletion.
- A key that appears on two holder pages goes to the page it was added to.

`dipa/edits.py` (`edits.yaml`) is the older correction mechanism, applied during extraction. It
is still the place to set a document's author _before_ stage 3 (so the extraction prompts use it)
and to fix a single mention. Holder renames and merges belong on holder pages.

## Images

Image files live in `DIPA_IMAGE_DIR` (14 SD images; GIFs are skipped). Each has a page in
`wiki/images/` with credit front matter (source, creator, license, credit), a Description,
Subjects, and Metaphors (`- keyword: gloss`).

- **Drafting:** a missing page is drafted by the vision model plus the LLM, with the credit taken
  from `<file>.source.txt`. A garbled vision draft is refused and the page is drafted from the
  `.source.txt` description instead.
- **Seeding:** the SD pages were copied from dip-memory's `data-sd-corpus-v1-small/wiki/images/`.
  Its Novum Organum page was vision-model garbage and was redrafted.
- **Embedding:** each image is embedded as its description, its picture (embedding server
  `/embed-images`) and each metaphor line.

The Images move, as in dip-memory:

- **Subject route:** the node's vector against description and picture vectors, rank-fused.
- **Metaphor route:** the node's abstract concepts (LLM, cached) against the metaphor lines; an
  image scores its best pair.
- The routes alternate. Every score is hubness-corrected against the argument centroid, and
  near-duplicate pictures are skipped.
- Results carry the credit, the licence and why each picture was picked.

The reverse move finds arguments a picture fits.

### Adding images from the shared sheet

Collaborators propose images in a Google Sheet. Columns are matched by name, and case and
punctuation don't matter:

| Column             | Notes                                                                                                          |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| **Image file URL** | a direct link to the picture (`.jpg`/`.png`/`.webp`); Google Drive share links are converted; GIFs are refused |
| Source page URL    | where it is credited and licensed (the UI links here)                                                          |
| **Title**          |                                                                                                                |
| Creator            |                                                                                                                |
| License            |                                                                                                                |
| Credit line        | the exact attribution; empty → "<Title> by <Creator>, <License>, via <site>"                                   |
| Description        | what the picture shows; empty → the vision model drafts one                                                    |
| Subjects           | comma-separated; empty → drafted from the description                                                          |
| Metaphors          | one per line, `keyword: gloss`; empty → the LLM drafts them                                                    |
| Added by           |                                                                                                                |
| Ready              | only ticked rows are imported (no column → every row)                                                          |

Bold columns are required. To import and publish:

```bash
python -m dipa.imagelib import "<sheet link>" [--dry-run]   # DIP_DATA_DIR=data-full; needs the models
./ship.sh user@vps --images                                 # pictures + pages + vectors to the demo
```

- **The sheet link:** the sheet must be shared as "anyone with the link can view", or published
  as CSV (File → Share → Publish to web).
- **What the import does:** it downloads each ticked row's picture into `DIPA_IMAGE_DIR` (scaled
  down past 2,000 px), writes `<file>.source.txt` and the wiki page, then runs stage 10.
- **Re-running:** an unchanged row is skipped. A changed row rewrites its page, unless the page is
  locked in the wiki. `.sheet-imports.json` in the image folder records what was imported.
- **Shipping:** `ship.sh --images` exports the library's vectors (`python -m dipa.imagelib
export`), sends the files and pages (a page edited more recently on the server is kept), and
  loads the vectors in the running API (`python -m dipa.imagelib load`). The offline server
  needs no model, and the rest of its data is untouched.
- **Image pages edited on the offline server** can't be re-embedded there. Those images drop out
  of the Images move until the next `ship.sh --images`.

## Evaluation

Files:

- `experiments/seeds.json`: 37 one-clause minority views in the three test documents, written
  before looking at any output, for recall. `experiments/seed_candidates.py` lists the closest
  mentions for each seed.
- `data-test3/reports/gold_sample.csv`: 60 mentions hand-labelled by the user (holder, voice,
  statement, worth keeping). `experiments/compare_gold.py` scores a later build against it by
  quote.
- `<data>/reports/`: `arguments.md` (every holder and argument),
  `summary_sample.csv` (summaries to label for attribution and faithfulness), one page per
  document.

Team labelling pack (`dipa/labelpack.py`, built for 4 people in `data-full/labelling/`):

```bash
DIP_DATA_DIR=data-full python -m dipa.labelpack make --people 4   # labelling-A..D.xlsx, GUIDE.md, key.json
DIP_DATA_DIR=data-full python -m dipa.labelpack score             # → data-full/labelling/report.md
```

- **The workbooks:** one per person, each with a Guide tab and three task tabs with dropdowns.
  - Summaries (120): attribution, faithfulness, worth keeping.
  - Counter pairs (160): B's relation to A, blind to the model's verdict, sampled evenly over the
    judge's score range.
  - Mentions (100): holder, voice, statement, worth keeping.

  20% of each task's items go to two people, so 36 + 48 + 30 rows per person. Upload the
  workbooks to Google Drive (they open as Sheets with tabs and dropdowns) and download them back
  as `labelling-<letter>.xlsx` into the folder.

- **The report:**
  - summary rates and mention accuracy per pass
  - the counter judge's AUC, precision/recall at the current threshold and the best threshold
  - how often the judge's top answer matches the team's
  - agreement (percent and Cohen's kappa) on the shared rows

Findings so far (three test documents, then the full corpus):

|                                                             | First run       | Latest                       |
| ----------------------------------------------------------- | --------------- | ---------------------------- |
| Quotes verified                                             | 97%             | 93–100% per document         |
| Someone else's view also credited to the author (gold rows) | 26              | 8                            |
| Voices rows: holder right (strict)                          | 11/30           | 16/30                        |
| Seed recall (other voices)                                  | 33/37           | 30–32/37 (varies run to run) |
| Holders that are an idea or narrative                       | 19              | 3                            |
| Merge precision (sample)                                    | ~50% (grouping) | 6/6 (pairwise)               |
| Counter precision (spot checks)                             | ~4/10           | ~5/7                         |

What we learned:

- **The hardest problem is attribution.** A 14B model credits the author with content it
  quotes or cites, and its own yes/no checks are unreliable on views an author sets up to
  reject. Checks therefore flag rather than drop, and anything that would erase a named holder
  is guarded in code.
- **Run-to-run variation is large.** Each re-extraction of the voices pass misses different
  one-clause views. Running the voices pass twice and keeping the union is the obvious lever for
  recall.
- **Summaries can invert a rejected view** when the extracted statement already mixes the view
  with its critique, and can add detail; the source passage helps for non-rejected views. Use
  `summary_sample.csv` to measure this.
- **Bugs found on the full corpus:**
  - inline Markdown links hid half of a live blog (rules now read visible text)
  - passage ids in prompts defeated the cache on re-parse (now positions)
  - argument id collisions (now suffixed)

## Known issues and next steps

- **Duplicate issues:** e.g. "who decides about data" appears more than once. `lint.md` lists
  them; merge by moving arguments.
- **Counters are not calibrated on labels yet.** Adapt dip-memory's counter test set to
  arguments.
- **The summary sheet hasn't been labelled yet** (`data-full/reports/summary_sample.csv`).
- **The Esoteric AI chapter** needs a better OCR before it can be added.
- **Image search ranks the subject route by description score** (picture scores only fuse in);
  with 14 images the same few pictures recur, and a bigger library would spread them.
